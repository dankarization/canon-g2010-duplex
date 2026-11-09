#!/usr/bin/env bash
# install.sh — idempotent, reversible install of the native Canon G2010 path on <server>.
#
# Installs:
#   1. /usr/local/lib/canon-g2010/            (sink + generator + filter, owned by root)
#   2. canon-g2010-sink.service               (TCP 9100 -> /dev/usb/lp0)  [needs root]
#   3. CUPS queue "G2010-native"              (our filter -> socket://127.0.0.1:9100) [user-lpadmin]
#   4. optional IPP exposure check            (cupsd already listens on 631)
#
# Does NOT touch existing queues (G2010-manual-duplex, G2010-series), their PPDs,
# or cupsd.conf. Does NOT install packages. Does NOT print.
#
# Requires root for the service + /usr/local/lib copy. If you cannot sudo, use
#   ./install.sh --no-service
# which installs the CUPS queue only (device must then be reachable another way).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TREE="$(cd "$HERE/.." && pwd)"
PREFIX="/usr/local/lib/canon-g2010"
QUEUE="G2010-native"
SINK_PORT="9100"
WITH_SERVICE=1
[ "${1:-}" = "--no-service" ] && WITH_SERVICE=0

as_root() { if [ "$(id -u)" = 0 ]; then "$@"; else sudo "$@"; fi; }

echo "== canon-g2010 native install =="
echo "tree: $TREE"

# --- 1. payload copy ---------------------------------------------------------
if [ "$WITH_SERVICE" = 1 ]; then
  as_root install -d -m 0755 "$PREFIX"
  as_root install -m 0755 "$HERE/sink/canon-g2010-sink.py" "$PREFIX/"
  as_root install -m 0644 "$TREE/src/bjraster3.py" "$PREFIX/"
  as_root install -m 0755 "$TREE/src/native-bjl-strip.py" "$PREFIX/"
  as_root install -m 0755 "$HERE/cups/canon-g2010-filter.py" "$PREFIX/"
  as_root install -m 0755 "$HERE/cups/canon-g2010-smart.py" "$PREFIX/"
  as_root install -m 0644 "$HERE/cups/cupsraster.py" "$PREFIX/"
  as_root install -m 0755 "$HERE/canon-g2010-print.py" "$PREFIX/"
  echo "== 1. payload installed to $PREFIX"

  # --- 2. systemd service ----------------------------------------------------
  as_root install -m 0644 "$HERE/systemd/canon-g2010-sink.service" \
      /etc/systemd/system/canon-g2010-sink.service
  as_root systemctl daemon-reload
  as_root systemctl enable --now canon-g2010-sink.service
  echo "== 2. canon-g2010-sink.service enabled and started"
else
  echo "== 1-2. skipped (--no-service): sink not installed as a service"
fi

# --- 3. CUPS queue -----------------------------------------------------------
# The queue is RAW (byte passthrough): this is exactly what the Canon Windows
# driver's raw 9100 stream needs, and it needs no root-installed filter. Linux
# clients use deploy/canon-g2010-print.py (no root) or install the optional
# filter at /usr/lib/cups/filter/canon-g2010 as root for a native CUPS chain.
if [ "$WITH_SERVICE" = 1 ]; then
  cat > /tmp/canon-g2010-filter <<'EOF'
#!/bin/sh
exec python3 /usr/local/lib/canon-g2010/canon-g2010-smart.py "$@"
EOF
  as_root install -m 0755 /tmp/canon-g2010-filter /usr/lib/cups/filter/canon-g2010
  rm -f /tmp/canon-g2010-filter
  echo "== 3a. CUPS filter (smart) installed at /usr/lib/cups/filter/canon-g2010"
fi

lpadmin -p "$QUEUE" -E -v "socket://127.0.0.1:${SINK_PORT}" -m raw
lpadmin -p "$QUEUE" -o printer-is-shared=true
lpadmin -p "$QUEUE" -o job-sheets=none
lpadmin -p "$QUEUE" -o media=A4
echo "== 3b. CUPS raw queue '$QUEUE' -> socket://127.0.0.1:${SINK_PORT} (A4 default)"

# --- 4. exposure summary -----------------------------------------------------
echo "== 4. IPP is served by cupsd on :631; raw JetDirect via the sink on :${SINK_PORT}"
echo
echo "Verify (no printing):"
echo "  systemctl status canon-g2010-sink --no-pager"
echo "  ss -ltn | grep -E ':(631|9100)'"
echo "  lpstat -v $QUEUE"
echo
echo "Dry-run sink test (writes to a file, never the device):"
echo "  $HERE/sink/canon-g2010-sink.py --port 9101 --dry-run --dry-run-dir /var/tmp/canon-g2010-dryrun &"
echo "  printf 'hello' | nc 127.0.0.1 9101 ; sleep 1 ; ls -l /var/tmp/canon-g2010-dryrun/"
echo
echo "Uninstall: $HERE/uninstall.sh"
