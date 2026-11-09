#!/usr/bin/env bash
# smart-test.sh — offline tests for canon-g2010-smart.py across all input types.
# No printing, no device, no root. Exit non-zero on any failure.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TREE="$(cd "$HERE/../.." && pwd)"
GEN="$HERE/canon-g2010-smart.py"
VERIFY="$TREE/build/out/bjlverify"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
ok=0; fail=0
say() { echo "$1"; }
pass() { say "PASS: $1"; ok=$((ok+1)); }
bad()  { say "FAIL: $1"; fail=$((fail+1)); }

echo "== smart filter tests =="

# --- 1. ready Canon raw must pass through byte-for-byte ---
# generate a ready-raw stream with our own generator from a tiny PS
printf '%%!PS\n/Helvetica findfont 18 scalefont setfont 72 700 moveto (raw case) show showpage\n' > "$TMP/a.ps"
"$GEN" 1 u t 1 "" "$TMP/a.ps" > "$TMP/ready.prn" 2>/dev/null
"$GEN" 1 u t 1 "" "$TMP/ready.prn" > "$TMP/pass.prn" 2>/dev/null
if cmp -s "$TMP/ready.prn" "$TMP/pass.prn"; then pass "ready Canon raw passthrough identical ($(stat -c%s "$TMP/ready.prn") B)"
else bad "ready Canon raw passthrough differs"; fi

# --- 2. PostScript -> valid inline BJRaster3 ---
"$GEN" 1 u t 1 "" "$TMP/a.ps" > "$TMP/ps.prn" 2>/dev/null
if "$VERIFY" "$TMP/ps.prn" | grep -q '"ok":true'; then pass "PostScript -> inline BJRaster3 verified"
else bad "PostScript conversion"; fi

# --- 3. PDF -> valid inline BJRaster3 ---
gs -q -dNOPAUSE -dBATCH -sDEVICE=pdfwrite -sOutputFile="$TMP/a.pdf" "$TMP/a.ps" 2>/dev/null
"$GEN" 1 u t 1 "" "$TMP/a.pdf" > "$TMP/pdf.prn" 2>/dev/null
if "$VERIFY" "$TMP/pdf.prn" | grep -q '"ok":true'; then pass "PDF -> inline BJRaster3 verified"
else bad "PDF conversion"; fi

# --- 4. CUPS raster -> valid inline BJRaster3 (only if a sample is provided) ---
if [ -n "${CANON_G2010_RASTER_SAMPLE:-}" ] && [ -f "${CANON_G2010_RASTER_SAMPLE:-}" ]; then
  "$GEN" 1 u t 1 "" "$CANON_G2010_RASTER_SAMPLE" > "$TMP/ras.prn" 2>/dev/null
  if "$VERIFY" "$TMP/ras.prn" | grep -q '"ok":true'; then pass "CUPS raster -> inline BJRaster3 verified"
  else bad "CUPS raster conversion"; fi
else
  say "SKIP: set CANON_G2010_RASTER_SAMPLE=/path/to/page.ras to test the raster branch"
fi

echo "-------------------------------------------"
echo "checks passed: $ok   failed: $fail"
exit $([ "$fail" = 0 ] && echo 0 || echo 1)
