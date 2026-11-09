#!/usr/bin/env bash
# uninstall.sh — reverse of install.sh. Removes ONLY what install.sh added.
# Never touches G2010-manual-duplex / G2010-series or their PPDs.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="/usr/local/lib/canon-g2010"
QUEUE="G2010-native"

as_root() { if [ "$(id -u)" = 0 ]; then "$@"; else sudo "$@"; fi; }

echo "== canon-g2010 native uninstall =="

if systemctl list-unit-files 2>/dev/null | grep -q '^canon-g2010-sink\.service'; then
  as_root systemctl disable --now canon-g2010-sink.service 2>/dev/null || true
  as_root rm -f /etc/systemd/system/canon-g2010-sink.service
  as_root systemctl daemon-reload
  echo "== removed canon-g2010-sink.service"
fi

lpadmin -x "$QUEUE" 2>/dev/null && echo "== removed CUPS queue $QUEUE" || echo "== queue $QUEUE absent"

as_root rm -f /usr/lib/cups/filter/canon-g2010 2>/dev/null || true
as_root rm -rf "$PREFIX" 2>/dev/null || true
echo "== removed payload $PREFIX and CUPS filter wrapper"

echo "== done. Existing queues untouched:"
lpstat -p 2>/dev/null | sed 's/^/   /'
