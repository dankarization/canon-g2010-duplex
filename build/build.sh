#!/usr/bin/env bash
# build.sh — build the offline stream verifier (our own code, no Canon sources).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/build/out"
mkdir -p "$OUT"
gcc -O2 -Wall "$ROOT/src/bjlverify.c" -o "$OUT/bjlverify"
echo "built: $OUT/bjlverify"
