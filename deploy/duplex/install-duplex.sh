#!/usr/bin/env bash
# install-duplex.sh — enable SERVER-SIDE manual duplex on the shared queue
# `G2010-series` for ALL clients (Windows/Mac/phone), reversibly.
#
# What it does (idempotent):
#   1. installs the duplex filter + confirm daemon into /usr/local/lib/canon-g2010/
#   2. installs the CUPS filter wrapper /usr/lib/cups/filter/canon-g2010-duplex
#   3. enables canon-g2010-duplexd.service (HTTP :8099)
#   4. backs up printers.conf + G2010-series.ppd, then repoints G2010-series at
#      the native sink (socket://127.0.0.1:9100) using the duplex PPD and the
#      duplex filter. `G2010-manual-duplex` is NEVER touched.
#
# One-sided printing is unaffected: the filter prints normally unless the job
# requests 2-sided.
#
# Usage: sudo ./install-duplex.sh            # apply (needs root)
#        ./install-duplex.sh --dry-run       # show plan, no changes
#        sudo ./install-duplex.sh --rollback [--stamp STAMP]
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TREE="$(cd "$HERE/../.." && pwd)"
PREFIX="/usr/local/lib/canon-g2010"
QUEUE="G2010-series"
OTHER="G2010-manual-duplex"
SINK="socket://127.0.0.1:9100"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="/var/tmp/canon-g2010-backup/$STAMP"

as_root() { if [ "$(id -u)" = 0 ]; then "$@"; else sudo "$@"; fi; }
mode="${1:---dry-run}"
[ "${1:-}" = "--stamp" ] && mode="--rollback"

case "$mode" in
  --dry-run)
    echo "== install-duplex (dry-run) =="
    echo "WOULD: install filter+daemon to $PREFIX"
    echo "WOULD: install /usr/lib/cups/filter/canon-g2010-duplex"
    echo "WOULD: systemctl enable --now canon-g2010-duplexd.service"
    echo "WOULD: backup printers.conf + $QUEUE.ppd -> $BACKUP_DIR"
    echo "WOULD: lpadmin -p $QUEUE -E -v $SINK"
    echo "WOULD: lpadmin -p $QUEUE -P $HERE/canon-g2010-duplex.ppd"
    echo "WOULD NOT touch: $OTHER"
    echo "current device of $QUEUE: $(lpstat -v $QUEUE 2>/dev/null | sed 's/.*: //')"
    exit 0 ;;
  --rollback)
    stamp=""; [ "${2:-}" = "--stamp" ] && stamp="${3:-}"
    dir="/var/tmp/canon-g2010-backup/${stamp:-$STAMP}"
    [ -d "$dir" ] || { echo "no backup at $dir"; ls -1 /var/tmp/canon-g2010-backup/ 2>/dev/null; exit 1; }
    as_root cp -a "$dir/printers.conf.bak" /etc/cups/printers.conf
    [ -f "$dir/$QUEUE.ppd.bak" ] && as_root cp -a "$dir/$QUEUE.ppd.bak" "/etc/cups/ppd/$QUEUE.ppd"
    as_root systemctl disable --now canon-g2010-duplexd.service 2>/dev/null || true
    as_root systemctl restart cups
    echo "rolled back $QUEUE from $dir; duplexd disabled; cups restarted."
    exit 0 ;;
esac

# ---- apply ----
as_root install -d -m0755 "$PREFIX"
as_root install -m 0755 "$HERE/canon-g2010-duplex-filter.py" "$PREFIX/"
as_root install -m 0755 "$HERE/canon-g2010-duplexd.py" "$PREFIX/"
as_root install -m 0644 "$HERE/../cups/cupsraster.py" "$PREFIX/" 2>/dev/null || true
as_root install -m 0644 "$TREE/src/bjraster3.py" "$PREFIX/" 2>/dev/null || true
as_root install -m 0755 "$TREE/src/native-bjl-strip.py" "$PREFIX/" 2>/dev/null || true

cat > /tmp/canon-g2010-duplex-wrapper <<'EOF'
#!/bin/sh
exec python3 /usr/local/lib/canon-g2010/canon-g2010-duplex-filter.py "$@"
EOF
as_root install -m 0755 /tmp/canon-g2010-duplex-wrapper /usr/lib/cups/filter/canon-g2010-duplex
rm -f /tmp/canon-g2010-duplex-wrapper

as_root install -m 0644 "$HERE/canon-g2010-duplexd.service" /etc/systemd/system/canon-g2010-duplexd.service
as_root systemctl daemon-reload
as_root systemctl enable --now canon-g2010-duplexd.service

as_root install -d -m0700 "$BACKUP_DIR"
as_root cp -a /etc/cups/printers.conf "$BACKUP_DIR/printers.conf.bak"
as_root cp -a "/etc/cups/ppd/$QUEUE.ppd" "$BACKUP_DIR/$QUEUE.ppd.bak" 2>/dev/null || true

as_root lpadmin -p "$QUEUE" -E -v "$SINK"
as_root lpadmin -p "$QUEUE" -P "$HERE/canon-g2010-duplex.ppd"
as_root lpadmin -p "$QUEUE" -o job-sheets=none
as_root lpadmin -p "$QUEUE" -o media=A4

echo "applied. $QUEUE -> $SINK via canon-g2010-duplex (server-side manual duplex on :8099)."
echo "rollback: sudo $0 --rollback --stamp $STAMP"
