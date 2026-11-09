# Variant A (final) — colour + manual duplex in ONE queue

## Key discovery (why the first attempt failed)
CUPS's built-in **`universal`** filter already owns `application/pdf ->
application/vnd.cups-raster` and wins the chain. A `cupsFilter2 pdf→pdf`
pre-filter therefore either **loops** (CUPS drops the job to raw) or is bypassed.
Proof: with `cupsFilter2 pdf→pdf`, the queue delivered the **raw PDF**
(1,800,763 B) to the sink; the pure Gutenprint queue delivered the **colour
stream** (19,098,338 B, 13× `ESC(L)CMYK`).

## Correct wiring (terminal filter)
Replace the PPD's **last stage** (`rastertogutenprint`) with our filter:

```
PDF -> CUPS universal (pdftoraster) -> vnd.cups-raster
    -> canon-g2010-split  -> colour BJRaster3 (vnd.cups-raw)
    -> socket://127.0.0.1:9100
```

PPD line:
```
*cupsFilter: "application/vnd.cups-raster 100 canon-g2010-split"
```
Our filter receives the **CUPS raster** (already RGB, colour), splits pages when
2-sided, calls the **stock `rastertogutenprint.5.3`** internally for COLOUR, and
emits the final carriage as raw. The colour producer is still the vendor driver.

## Manual-chain segfault — solved
`rastertogutenprint.5.3` segfaults (rc=139) on a **cspace-19** raster
(`gstoraster`, or `pdftoraster` without `PPD` env). Fix: run `pdftoraster` **with
`PPD=<gutenprint.ppd>`** → cspace **1 (RGB)** → gutenprint succeeds. (In the real
queue CUPS does this for us.)

## Offline evidence (no physical printing)
| test | result |
|---|---|
| render 1 page via `pdftoraster`+PPD → `rastertogutenprint` | rc=0, StartJob 1, **ESC(F) 3404, `ESC(L)CMYK`** |
| 1-sided colour (terminal filter, real chain args) | 22,699,642 B, **13× `ESC(L)CMYK`** |
| 2-sided fronts | 12,782,866 B, **7× `ESC(L)CMYK`** (pages 1,3,5,7,9,11,13) |
| 2-sided backs (`canon-duplex-pass=backs`) | 9,930,935 B, **6× `ESC(L)CMYK`** (pages 2,4,6,8,10,12) |
| pure Gutenprint queue (reference) | 19,098,338 B, **13× `ESC(L)CMYK`** |

Both passes are CMYK colour. 1-sided path untouched.

## BLOCKER: install needs root
This agent has **no sudo** on <server> (`sudo -n` → password required) and **cannot
write** `/usr/lib/cups/filter` or `/usr/local/lib/canon-g2010` (root-owned). The
corrected files are in the tree; the install below must be run by the operator.

## Exact commands (<server>, root)
```bash
# from the tree directory
sudo install -m 0755 deploy/duplex/canon-g2010-split.py /usr/local/lib/canon-g2010/canon-g2010-split.py
sudo install -m 0755 deploy/duplex/canon-g2010-duplexd.py /usr/local/lib/canon-g2010/canon-g2010-duplexd.py
cat > /tmp/canon-g2010-split <<'EOF'
#!/bin/sh
exec python3 /usr/local/lib/canon-g2010/canon-g2010-split.py "$@"
EOF
sudo install -m 0755 /tmp/canon-g2010-split /usr/lib/cups/filter/canon-g2010-split
sudo install -m 0644 deploy/color/canon-g2010-color.ppd /usr/local/lib/canon-g2010/canon-g2010-color.ppd

STAMP=$(date +%Y%m%d-%H%M%S); sudo mkdir -p /var/tmp/canon-g2010-backup/$STAMP
sudo cp -a /etc/cups/printers.conf /var/tmp/canon-g2010-backup/$STAMP/printers.conf.bak
sudo cp -a /etc/cups/ppd/G2010-series.ppd /var/tmp/canon-g2010-backup/$STAMP/G2010-series.ppd.bak
sudo lpadmin -p G2010-series -P /usr/local/lib/canon-g2010/canon-g2010-color.ppd
sudo lpadmin -p G2010-series -o media=A4
sudo systemctl restart canon-g2010-duplexd cups
```

## Rollback
```bash
sudo cp -a /var/tmp/canon-g2010-backup/<STAMP>/printers.conf.bak /etc/cups/printers.conf
sudo cp -a /var/tmp/canon-g2010-backup/<STAMP>/G2010-series.ppd.bak /etc/cups/ppd/G2010-series.ppd
sudo systemctl restart cups
```

## Verify (no printing)
```bash
python3 /usr/local/lib/canon-g2010/canon-g2010-sink.py --listen 127.0.0.1 --port 9109 --dry-run --dry-run-dir /var/tmp/dv --lock /var/tmp/cc.lock &
lpadmin -p G2010-check -E -v socket://127.0.0.1:9109 -P /usr/local/lib/canon-g2010/canon-g2010-color.ppd
lp -d G2010-check evidence/fixtures/owner-contract-color.pdf
python3 -c "import glob;d=open(glob.glob('/var/tmp/dv/*.prn')[0],'rb').read();print('CMYK',d.count(b'\x1b(L\x04\x00CMYK'),'ESC(F',d.count(b'\x1b(F'))"
lpadmin -x G2010-check
```
