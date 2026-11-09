#!/usr/bin/env bash
# check.sh — safe, NON-PRINTING verification of the native Canon G2010 path on <server>.
# Confirms: sink listening, queue present, existing queues untouched, and that a
# raw 9100 connection is received in DRY-RUN (writes to a file only, never lp0).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
QUEUE="G2010-native"
DRYDIR="/var/tmp/canon-g2010-dryrun"
PORT=9101
ok=0; fail=0
chk() { if eval "$2"; then echo "PASS: $1"; ok=$((ok+1)); else echo "FAIL: $1"; fail=$((fail+1)); fi; }

echo "== 1. sink service =="
if systemctl list-unit-files 2>/dev/null | grep -q '^canon-g2010-sink\.service'; then
  chk "sink service active" "systemctl is-active --quiet canon-g2010-sink"
  chk "port 9100 listening" "ss -ltn | grep -q ':9100'"
else
  echo "NOTE: sink service not installed (root needed); run-by-hand mode -- :9100"
  echo "      is expected to be down until 'sudo deploy/install.sh' is run."
fi
chk "cupsd on 631" "ss -ltn | grep -q ':631'"

echo "== 2. CUPS queue =="
chk "queue $QUEUE exists" "lpstat -v $QUEUE >/dev/null 2>&1"
chk "queue points at sink" "lpstat -v $QUEUE 2>/dev/null | grep -q '127.0.0.1:9100'"
chk "existing queue untouched" "lpstat -v G2010-series >/dev/null 2>&1"

echo "== 3. dry-run 9100 receive (no device write) =="
rm -rf "$DRYDIR"
python3 "$HERE/sink/canon-g2010-sink.py" --listen 127.0.0.1 --port "$PORT" \
  --dry-run --dry-run-dir "$DRYDIR" >/tmp/sink-dry.log 2>&1 &
SINKPID=$!
sleep 1
printf 'DRYRUN-HELLO' | timeout 3 bash -c "cat > /dev/tcp/127.0.0.1/$PORT"
sleep 2
kill "$SINKPID" 2>/dev/null; wait "$SINKPID" 2>/dev/null
if ls "$DRYDIR"/job-*.prn >/dev/null 2>&1; then
  echo "PASS: dry-run sink received a job -> $DRYDIR"; ok=$((ok+1))
else
  echo "FAIL: dry-run sink received nothing"; fail=$((fail+1)); sed 's/^/   /' /tmp/sink-dry.log
fi
rm -rf "$DRYDIR"

echo
echo "-------------------------------------------"
echo "checks passed: $ok   failed: $fail"
echo "NOTE: this script NEVER prints. Physical print requires separate approval."
exit $([ "$fail" = 0 ] && echo 0 || echo 1)
