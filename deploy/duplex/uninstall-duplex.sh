#!/usr/bin/env bash
# uninstall-duplex.sh — reverse install-duplex.sh. Removes only duplexd pieces.
# Restores printers.conf + G2010-series.ppd from the newest backup (or --stamp).
set -euo pipefail
QUEUE="G2010-series"
STAMP="${1:-}"
as_root() { if [ "$(id -u)" = 0 ]; then "$@"; else sudo "$@"; fi; }

if [ -z "$STAMP" ]; then
  STAMP="$(ls -1t /var/tmp/canon-g2010-backup/ 2>/dev/null | head -1)"
fi
DIR="/var/tmp/canon-g2010-backup/$STAMP"
echo "using backup: $DIR"
if [ -d "$DIR" ]; then
  as_root cp -a "$DIR/printers.conf.bak" /etc/cups/printers.conf
  [ -f "$DIR/$QUEUE.ppd.bak" ] && as_root cp -a "$DIR/$QUEUE.ppd.bak" "/etc/cups/ppd/$QUEUE.ppd"
  echo "restored printers.conf + $QUEUE.ppd"
else
  echo "WARNING: no backup found; leaving CUPS config as-is"
fi
as_root systemctl disable --now canon-g2010-duplexd.service 2>/dev/null || true
as_root rm -f /etc/systemd/system/canon-g2010-duplexd.service /usr/lib/cups/filter/canon-g2010-duplex
as_root rm -f /usr/local/lib/canon-g2010/canon-g2010-duplex-filter.py \
               /usr/local/lib/canon-g2010/canon-g2010-duplexd.py
as_root systemctl daemon-reload
as_root systemctl restart cups
echo "duplex uninstalled. Existing queues:"
lpstat -p 2>/dev/null | sed 's/^/   /'
