#!/usr/bin/env bash
# repoint-shared-queue.sh — REVERSIBLE redirection of the ONE shared queue that
# the owner's Windows/Mac/phone clients already use, so jobs flow through the
# native path (our smart filter -> socket://127.0.0.1:9100 -> sink -> printer).
#
#   Target queue : G2010-series          (the queue Mobility/Windows/Mac/phone use)
#   Untouched    : G2010-manual-duplex   (never modified)
#
# It changes ONLY the device-uri and the filter of G2010-series, after backing up
# printers.conf and the PPD. Nothing is changed until you pass --apply.
#
# Usage:
#   ./repoint-shared-queue.sh --dry-run     # show what WOULD change; no writes
#   sudo ./repoint-shared-queue.sh --apply  # make the change (needs root)
#   sudo ./repoint-shared-queue.sh --rollback  # restore the backup exactly
set -euo pipefail

QUEUE="G2010-series"
OTHER="G2010-manual-duplex"
SINK="socket://127.0.0.1:9100"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="/var/tmp/canon-g2010-backup/$STAMP"

as_root() { if [ "$(id -u)" = 0 ]; then "$@"; else sudo "$@"; fi; }
mode="${1:---dry-run}"

echo "== queue redirection ($mode) =="
echo "target:   $QUEUE"
echo "untouched:$OTHER"
echo "backup:   $BACKUP_DIR"

current_dev="$(lpstat -v "$QUEUE" 2>/dev/null | sed 's/.*: //')"
echo "current device-uri of $QUEUE: $current_dev"

case "$mode" in
  --dry-run)
    echo
    echo "WOULD run (requires root):"
    echo "  install -d -m0700 $BACKUP_DIR"
    echo "  cp -a /etc/cups/printers.conf $BACKUP_DIR/printers.conf.bak"
    echo "  cp -a /etc/cups/ppd/$QUEUE.ppd $BACKUP_DIR/$QUEUE.ppd.bak"
    echo "  lpadmin -p $QUEUE -E -v $SINK"
    echo "  # install smart filter as the queue filter (keeps PS/PDF conversion,"
    echo "  # passes ready Canon raw byte-for-byte) via the native PPD:"
    echo "  lpadmin -p $QUEUE -P <tree>/deploy/cups/canon-g2010-native.ppd"
    echo "  # (or, to only repoint the device and keep the Gutenprint PPD:)"
    echo "  # lpadmin -p $QUEUE -E -v $SINK   # Gutenprint PPD left in place"
    echo
    echo "NOT changed (dry-run). Re-run with --apply to perform."
    exit 0
    ;;
  --apply)
    as_root install -d -m0700 "$BACKUP_DIR"
    as_root cp -a /etc/cups/printers.conf "$BACKUP_DIR/printers.conf.bak"
    as_root cp -a "/etc/cups/ppd/$QUEUE.ppd" "$BACKUP_DIR/$QUEUE.ppd.bak" || true
    echo "backed up printers.conf + $QUEUE.ppd -> $BACKUP_DIR"

    # 1. repoint the device at the sink
    as_root lpadmin -p "$QUEUE" -E -v "$SINK"

    # 2. put our smart filter in front (native PPD). Comment out to keep Gutenprint.
    HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    as_root lpadmin -p "$QUEUE" -P "$HERE/cups/canon-g2010-native.ppd"
    as_root lpadmin -p "$QUEUE" -o job-sheets=none
    as_root lpadmin -p "$QUEUE" -o media=A4

    echo "applied. $QUEUE now -> $SINK via canon-g2010 native filter."
    echo "rollback: sudo $0 --rollback --stamp $STAMP"
    ;;
  --rollback)
    stamp=""
    if [ "${2:-}" = "--stamp" ]; then stamp="${3:-}"; fi
    dir="/var/tmp/canon-g2010-backup/${stamp:-$STAMP}"
    if [ ! -d "$dir" ]; then
      echo "no backup at $dir"; echo "available:"; ls -1 /var/tmp/canon-g2010-backup/ 2>/dev/null; exit 1
    fi
    as_root cp -a "$dir/printers.conf.bak" /etc/cups/printers.conf
    [ -f "$dir/$QUEUE.ppd.bak" ] && as_root cp -a "$dir/$QUEUE.ppd.bak" "/etc/cups/ppd/$QUEUE.ppd"
    as_root systemctl restart cups
    echo "rolled back $QUEUE from $dir and restarted cups."
    ;;
  *)
    echo "usage: $0 [--dry-run|--apply|--rollback [--stamp STAMP]]"; exit 2
    ;;
esac
