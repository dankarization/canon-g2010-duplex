# Redeploy the fixed payload to <server> (cropping + A4)

The currently **installed** production payload
(`/usr/local/lib/canon-g2010/`) still has the OLD renderer (height 2240 =
3.73", top-of-ink crop). The corrected files are in this tree. To make
full-A4 + A4-default live, reinstall the payload and re-apply the PPD.

## Exact commands (run on <server>, needs root)

```bash
cd canon-g2010-native-ds41f

# 1. payload (renderer + filters)
sudo install -m 0644 src/bjraster3.py            /usr/local/lib/canon-g2010/
sudo install -m 0755 src/native-bjl-strip.py     /usr/local/lib/canon-g2010/
sudo install -m 0755 deploy/cups/canon-g2010-smart.py    /usr/local/lib/canon-g2010/
sudo install -m 0755 deploy/cups/canon-g2010-filter.py   /usr/local/lib/canon-g2010/
sudo install -m 0755 deploy/duplex/canon-g2010-duplex-filter.py /usr/local/lib/canon-g2010/
sudo install -m 0644 deploy/cups/cupsraster.py   /usr/local/lib/canon-g2010/

# 2. A4-only PPDs
sudo install -m 0644 deploy/cups/canon-g2010-native.ppd  /usr/local/lib/canon-g2010/
sudo install -m 0644 deploy/duplex/canon-g2010-duplex.ppd /usr/local/lib/canon-g2010/

# 3. re-apply the queue PPD + A4 default (G2010-series is the shared queue)
STAMP=$(date +%Y%m%d-%H%M%S); sudo mkdir -p /var/tmp/canon-g2010-backup/$STAMP
sudo cp -a /etc/cups/printers.conf /var/tmp/canon-g2010-backup/$STAMP/printers.conf.bak
sudo cp -a /etc/cups/ppd/G2010-series.ppd /var/tmp/canon-g2010-backup/$STAMP/G2010-series.ppd.bak
sudo lpadmin -p G2010-series -P /usr/local/lib/canon-g2010/canon-g2010-duplex.ppd
sudo lpadmin -p G2010-series -o media=A4
sudo systemctl restart cups
# (duplexd keeps running; no service change needed)
```

`G2010-manual-duplex` is not touched.

## Rollback
```bash
sudo cp -a /var/tmp/canon-g2010-backup/<STAMP>/printers.conf.bak /etc/cups/printers.conf
sudo cp -a /var/tmp/canon-g2010-backup/<STAMP>/G2010-series.ppd.bak /etc/cups/ppd/G2010-series.ppd
sudo systemctl restart cups
```

## Verify (no printing)
```bash
# full-page height (expect 877 bands = 7016 lines)
printf '%%!PS\n/Helvetica findfont 24 scalefont setfont 72 770 moveto (T) show 72 60 moveto (B) show showpage\n' > /tmp/f.ps
python3 /usr/local/lib/canon-g2010/canon-g2010-smart.py 1 u t 1 "" /tmp/f.ps \
  | ~/canon-g2010-native/build/out/bjlverify /dev/stdin    # -> "ok": true, esc_f_blocks 877

# A4 default + A4-only media
ipptool -tv ipp://localhost:631/printers/G2010-series get-printer-attributes.test \
  | grep -iE 'media-default|media-supported'
# expect: media-default = iso_a4_210x297mm ; media-supported = iso_a4_210x297mm (only)
lpoptions -p G2010-series | grep -o 'media=A4'
```

## One-sided not broken
The duplex filter defaults to 1-sided when the job does not request 2-sided; that
path renders the whole document (now full A4) with no pause and no state.
Verified: `duplex 1-sided: ok true, bands 877`.
