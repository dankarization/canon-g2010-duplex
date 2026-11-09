#!/usr/bin/env bash
# install-color-duplex.sh — Variant A: colour + manual duplex in ONE queue.
#
# Keeps the Gutenprint driver (which emits COLOUR inline-BJRaster3) and adds our
# split pre-filter so 2-sided jobs are orchestrated server-side.
#
# Usage:
#   ./install-color-duplex.sh --dry-run
#   sudo ./install-color-duplex.sh
#   sudo ./install-color-duplex.sh --rollback [--stamp STAMP]
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
QUEUE="G2010-series"
OTHER="G2010-manual-duplex"
SINK="socket://127.0.0.1:9100"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="/var/tmp/canon-g2010-backup/$STAMP"
as_root() { if [ "$(id -u)" = 0 ]; then "$@"; else sudo "$@"; fi; }
mode="${1:---dry-run}"

case "$mode" in
  --dry-run)
    echo "== install-color-duplex (dry-run) =="
    echo "WOULD: install /usr/lib/cups/filter/canon-g2010-split"
    echo "WOULD: install $HERE/../color/canon-g2010-color.ppd /usr/local/lib/canon-g2010/"
    echo "WOULD: backup printers.conf + $QUEUE.ppd -> $BACKUP_DIR"
    echo "WOULD: lpadmin -p $QUEUE -P <color ppd>  (Gutenprint colour + split pre-filter)"
    echo "WOULD: lpadmin -p $QUEUE -o media=A4"
    echo "WOULD NOT touch: $OTHER"
    exit 0 ;;
  --rollback)
    stamp=""; [ "${2:-}" = "--stamp" ] && stamp="${3:-}"
    dir="/var/tmp/canon-g2010-backup/${stamp:-$STAMP}"
    [ -d "$dir" ] || { echo "no backup at $dir"; ls -1 /var/tmp/canon-g2010-backup/ 2>/dev/null; exit 1; }
    as_root cp -a "$dir/printers.conf.bak" /etc/cups/printers.conf
    [ -f "$dir/$QUEUE.ppd.bak" ] && as_root cp -a "$dir/$QUEUE.ppd.bak" "/etc/cups/ppd/$QUEUE.ppd"
    as_root rm -f /usr/lib/cups/filter/canon-g2010-split
    as_root systemctl restart cups
    echo "rolled back $QUEUE from $dir"
    exit 0 ;;
esac

as_root install -m 0755 "$HERE/canon-g2010-split.py" /usr/lib/cups/filter/canon-g2010-split
as_root install -d -m0755 /usr/local/lib/canon-g2010
as_root install -m 0644 "$HERE/../color/canon-g2010-color.ppd" /usr/local/lib/canon-g2010/
as_root install -m 0755 "$HERE/canon-g2010-duplexd.py" /usr/local/lib/canon-g2010/

as_root install -d -m0700 "$BACKUP_DIR"
as_root cp -a /etc/cups/printers.conf "$BACKUP_DIR/printers.conf.bak"
as_root cp -a "/etc/cups/ppd/$QUEUE.ppd" "$BACKUP_DIR/$QUEUE.ppd.bak" 2>/dev/null || true

as_root lpadmin -p "$QUEUE" -E -P /usr/local/lib/canon-g2010/canon-g2010-color.ppd
as_root lpadmin -p "$QUEUE" -o media=A4
as_root systemctl restart canon-g2010-duplexd cups

echo "applied Variant A: $QUEUE = Gutenprint colour + split pre-filter (A4)."
echo "rollback: sudo $0 --rollback --stamp $STAMP"
