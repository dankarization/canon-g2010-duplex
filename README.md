# Canon G2010 — server-side colour + manual duplex for Linux

Print **colour, A4, two-sided** documents to a Canon PIXMA G2010/G3010-class
inkjet from **any client** (Windows, macOS, phone) **without installing anything
on the client** and **without a Windows driver**.

The G2010 has no automatic duplexer, so two-sided printing is inherently manual.
Canon's Windows driver implements it as: print odd pages → popup → reinsert →
print even pages. This project moves that whole cycle **onto a Linux print
server** (CUPS), so the client just sends a normal job.

```
 Client (Windows / macOS / phone)          Linux server (CUPS)                Printer
 --------------------------------          --------------------               -------
 any app, IPP or raw JetDirect  ───────►   canon-g2010-duplex filter:
                                           • render pages (Gutenprint, colour)
                                           • emit FRONTS now (odd pages)
                                           • store BACKS + register job
                                           canon-g2010-duplexd (HTTP :8099)
                                             "flipped it — print backs"
                                                     │
                                                     ▼
                                           canon-g2010-sink (TCP 9100)  ───►  /dev/usb/lp0 → G2010
```

## Why it exists

* The G2010 speaks **inline BJRaster3** (Canon IVEC XML + ESC-escaped raster)
  over USB. Canon's *Linux* driver (`cnijfilter2`) emits **PWG raster**, which
  the G2010 ignores — so the stock Linux driver cannot print to it.
* Open drivers (Gutenprint) can render the G2010, but have **no manual duplex**.
* This project keeps Gutenprint for rendering (it produces colour correctly) and
  adds the **missing server-side duplex orchestration** on top.

## Features

* **Colour** output via the Gutenprint chain (`pdftoraster → rastertogutenprint`).
* **A4 by default**, correct geometry (no right-edge clipping).
* **Manual duplex orchestrated on the server**: fronts print immediately; a web
  page (`http://<server>:8099`) asks you to flip the stack, then prints backs.
* **Zero client install**: clients print over IPP (`http://<server>:631/printers/G2010-series`)
  or raw JetDirect (`<server>:9100`).
* **Safe defaults**: single-writer lock on the device, bounded jobs, `--dry-run`
  everywhere, reversible install/uninstall that never touches other queues.

## Repository layout

| path | purpose |
|---|---|
| `deploy/duplex/canon-g2010-duplex-filter.py` | **core**: CUPS filter that renders and splits fronts/backs |
| `deploy/duplex/canon-g2010-duplexd.py` | HTTP confirm service (`:8099`) that prints the backs after you flip |
| `deploy/sink/canon-g2010-sink.py` | TCP 9100 → `/dev/usb/lp0` sink (single-writer `flock`, bounded, `--dry-run`) |
| `deploy/color/canon-g2010-color.ppd` | colour PPD used for rendering (A4 defaults) |
| `deploy/duplex/canon-g2010-duplex.ppd` | PPD for the server-side duplex queue |
| `deploy/duplex/install-duplex.sh`, `install-color-duplex.sh` | installers (dry-run / apply / rollback) |
| `deploy/systemd/`, `deploy/duplex/canon-g2010-duplexd.service` | systemd units |
| `deploy/cups/canon-g2010-smart.py`, `cupsraster.py` | optional smart filter (raw passthrough / raster decode) |
| `src/bjraster3.py`, `src/native-bjl-strip.py` | inline-BJRaster3 generator (used by the filter) |
| `src/bjlverify.c` | offline stream verifier |
| `deploy/check.sh` | non-printing health check |
| `evidence/` | mechanism notes and a demo PostScript |

## Requirements

* Linux with CUPS, root for install.
* `printer-driver-gutenprint` (rendering), `ghostscript` (`gs`/`ps2pdf`).
* A Canon G2010/G3010-class printer reachable locally (USB `/dev/usb/lp0`; or
  point the sink at a network address instead).

## Install (server)

```bash
git clone https://github.com/<you>/canon-g2010-duplex.git
cd canon-g2010-duplex
sudo ./deploy/duplex/install-color-duplex.sh --dry-run   # preview
sudo ./deploy/duplex/install-color-duplex.sh             # apply (colour + duplex queue)
./deploy/check.sh                                        # non-printing verification
```

This creates/updates the CUPS queue `G2010-series` (colour + server-side duplex)
and installs the sink (`canon-g2010-sink.service`) and confirm service
(`canon-g2010-duplexd.service`). Both installers are reversible
(`--rollback`) and never touch queues they do not own.

## Use

1. From any client, print to `Canon G2010 series @ <server>`
   (IPP) or send raw to `<server>:9100`.
2. For two-sided jobs, the fronts print first. The job then pauses.
3. Open `http://<server>:8099`, flip the printed sheets, press **Print backs**.
4. The backs print. Done.

## Verify offline (no printer)

```bash
bash build/build.sh              # build the verifier
bash deploy/cups/smart-test.sh   # smart filter: passthrough + PS/PDF conversion
bash deploy/duplex/duplex-test.sh# duplex: split, HTTP confirm, timeouts, dry sink
```

Expected: `3 passed` and `14 passed`, no printing involved.

## How the two carriages differ

* The G2010 accepts the **inline BJRaster3** carriage (Windows-DDI style),
  reproduced here byte-exactly from an accepted pilot.
* The Canon Linux CNCL carriage (`SendData`/PWG) is **ignored** by the hardware —
  see `evidence/INLINE-BJRASTER3.md`.

## Notes & limits

* Installers assume a CUPS setup where the `lpadmin` user may add queues; the
  sink and filter wrappers need root.
* The duplex "flip" step is manual by nature — the web page is the
  acknowledgement channel (any device on the network can open it).
* Tested on a Canon G2010 series (USB `04a9:183a`). G3010-class models share the
  same driver family.

## License

MIT — see [LICENSE](LICENSE). This project contains only original code; it does
not redistribute Canon's proprietary binaries or source.
