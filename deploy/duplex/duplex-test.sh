#!/usr/bin/env bash
# duplex-test.sh — offline tests for server-side manual duplex. NO printing.
# Covers: page split, both passes, HTTP confirm, bad token, idempotency,
# timeout expiry, sink delivery (to a dry sink), and 1-sided default.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TREE="$(cd "$HERE/../.." && pwd)"
FILTER="$HERE/canon-g2010-duplex-filter.py"
DAEMON="$HERE/canon-g2010-duplexd.py"
SINK="$TREE/deploy/sink/canon-g2010-sink.py"
VERIFY="$TREE/build/out/bjlverify"

WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"; kill $(jobs -p) 2>/dev/null' EXIT
export CANON_G2010_DUPLEX_STATE="$WORK/state"
ok=0; fail=0
pass(){ echo "PASS: $1"; ok=$((ok+1)); }
bad(){ echo "FAIL: $1"; fail=$((fail+1)); }
jok(){ "$VERIFY" "$1" 2>/dev/null | grep -q '"ok":true'; }

printf '%%!PS\n/Helvetica findfont 18 scalefont setfont 72 700 moveto (P1) show showpage\n72 700 moveto (P2) show showpage\n72 700 moveto (P3) show showpage\n' > "$WORK/3.ps"

echo "== duplex filter: 1-sided default (no pause) =="
CANON_G2010_DUPLEX_STATE="$CANON_G2010_DUPLEX_STATE" python3 "$FILTER" 11 u t 1 "" "$WORK/3.ps" > "$WORK/one.prn" 2>/dev/null
jok "$WORK/one.prn" && pass "1-sided verified" || bad "1-sided output invalid"
[ -z "$(ls -A "$CANON_G2010_DUPLEX_STATE" 2>/dev/null)" ] && pass "1-sided left no duplex state" || bad "1-sided created state"

echo "== duplex filter: 2-sided split =="
CANON_G2010_DUPLEX_STATE="$CANON_G2010_DUPLEX_STATE" python3 "$FILTER" 22 u t 1 "sides=two-sided-long-edge" "$WORK/3.ps" > "$WORK/fronts.prn" 2>/dev/null
jok "$WORK/fronts.prn" && pass "fronts pass verified" || bad "fronts invalid"
jok "$CANON_G2010_DUPLEX_STATE/22.prn" && pass "backs pass verified" || bad "backs invalid"
python3 - "$CANON_G2010_DUPLEX_STATE/22.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
assert d["fronts_pages"]==[1,3], d
assert d["backs_pages"]==[2,4], d   # padded to even
assert d["state"]=="awaiting_confirm"
PY
[ $? = 0 ] && pass "state: fronts [1,3], backs [2,4], awaiting" || bad "state wrong"

echo "== HTTP confirm =="
"$DAEMON" --listen 127.0.0.1 --port 18099 --dry-run --dry-run-dir "$WORK/dry" > "$WORK/d.log" 2>&1 &
sleep 1.2
curl -s http://127.0.0.1:18099/healthz | grep -q ok && pass "healthz" || bad "healthz"
curl -s http://127.0.0.1:18099/status | grep -q '"jobid": "22"' && pass "status lists job" || bad "status missing job"
TOK=$(python3 -c "import json;print(json.load(open('$CANON_G2010_DUPLEX_STATE/22.json'))['token'])")
[ "$(curl -s -o /dev/null -w '%{http_code}' -X POST -d 'token=BAD' http://127.0.0.1:18099/confirm/22)" = 403 ] && pass "bad token -> 403" || bad "bad token not rejected"
[ "$(curl -s -o /dev/null -w '%{http_code}' -X POST -d "token=$TOK" http://127.0.0.1:18099/confirm/22)" = 200 ] && pass "confirm -> 200" || bad "confirm failed"
sleep 0.6
ls "$WORK"/dry/backs-22-*.prn >/dev/null 2>&1 && jok "$WORK"/dry/backs-22-*.prn && pass "backs delivered (dry-run) verified" || bad "backs not delivered"
[ "$(curl -s -o /dev/null -w '%{http_code}' -X POST -d "token=$TOK" http://127.0.0.1:18099/confirm/22)" = 200 ] && pass "confirm idempotent (2nd -> 200)" || bad "idempotency"
python3 -c "import json;d=json.load(open('$CANON_G2010_DUPLEX_STATE/22.json'));assert d['state']=='done'" && pass "state done" || bad "state not done"
kill %1 2>/dev/null; wait %1 2>/dev/null

echo "== timeout expiry =="
rm -rf "$CANON_G2010_DUPLEX_STATE"
CANON_G2010_DUPLEX_STATE="$CANON_G2010_DUPLEX_STATE" python3 "$FILTER" 33 u t 1 "sides=two-sided-long-edge" "$WORK/3.ps" >/dev/null 2>&1
CANON_G2010_DUPLEX_STATE="$CANON_G2010_DUPLEX_STATE" CANON_G2010_DUPLEX_TIMEOUT=1 \
  "$DAEMON" --listen 127.0.0.1 --port 18098 --dry-run --dry-run-dir "$WORK/dry2" > "$WORK/d2.log" 2>&1 &
sleep 2.3
TOK=$(python3 -c "import json;print(json.load(open('$CANON_G2010_DUPLEX_STATE/33.json'))['token'])")
[ "$(curl -s -o /dev/null -w '%{http_code}' -X POST -d "token=$TOK" http://127.0.0.1:18098/confirm/33)" = 410 ] && pass "expired confirm -> 410" || bad "expiry not enforced"
kill %1 2>/dev/null; wait %1 2>/dev/null

echo "== sink delivery (dry sink, no device) =="
rm -rf "$CANON_G2010_DUPLEX_STATE" "$WORK/sinkout"
"$SINK" --listen 127.0.0.1 --port 9110 --dry-run --dry-run-dir "$WORK/sinkout" > "$WORK/s.log" 2>&1 &
sleep 1
CANON_G2010_DUPLEX_STATE="$CANON_G2010_DUPLEX_STATE" python3 "$FILTER" 44 u t 1 "sides=two-sided-long-edge" "$WORK/3.ps" >/dev/null 2>&1
python3 - "$CANON_G2010_DUPLEX_STATE/44.json" <<'PY'
import json,sys
p=sys.argv[1]; d=json.load(open(p)); d["sink_port"]=9110; json.dump(d,open(p,"w"),indent=2)
PY
"$DAEMON" --listen 127.0.0.1 --port 18097 > "$WORK/d3.log" 2>&1 &
sleep 1
TOK=$(python3 -c "import json;print(json.load(open('$CANON_G2010_DUPLEX_STATE/44.json'))['token'])")
curl -s -o /dev/null -w '' -X POST -d "token=$TOK" http://127.0.0.1:18097/confirm/44
sleep 0.8
ls "$WORK"/sinkout/*.prn >/dev/null 2>&1 && jok "$WORK"/sinkout/*.prn && pass "backs reached sink + verified" || bad "sink delivery failed"
kill %1 %2 2>/dev/null; wait 2>/dev/null

echo "-------------------------------------------"
echo "checks passed: $ok   failed: $fail"
exit $([ "$fail" = 0 ] && echo 0 || echo 1)
