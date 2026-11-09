# Native Canon G2010 print path on <server> (Windows-driver manual duplex over raw 9100)

Status: **deliverables authored and offline-verified on <server>.** Sink dry-run,
raw queue → sink passthrough, and the local render CLI were all exercised
**without printing** and **without root**. Physical print is the owner's gate.

## Architecture (and why this is the right shape)

The G2010 has **no automatic duplex**. Canon's Windows driver implements
"2-sided printing" as a **manual cycle**: print odd pages, show a popup
("reinsert sheets, press OK"), print even pages. That logic lives in the driver,
not the printer. So the production path is:

```
 Windows PC                          <server> (Linux)                     Printer
 ----------                          --------------                    -------
 Canon Win driver  --raw 9100-->  canon-g2010-sink  --write-->  /dev/usb/lp0 -> G2010
 (Manual Duplex popup, native)     (single-writer lock)
                                       ^
 Linux apps  --------------------------+  (raw CUPS queue "G2010-native" -> sink,
   - via CUPS raw queue, or            |   or the no-root render CLI canon-g2010-print.py)
   - via canon-g2010-print.py
```

* The **Windows driver keeps doing native manual duplex** (its popup). It streams
  its raw inline-BJRaster3 bytes (the format we proved prints) to `:9100`; the
  sink writes them to the printer **byte-for-byte**. **No Wine, no PE, no
  translation.**
* **Linux clients** print by either:
  * the **raw CUPS queue** `G2010-native` (byte passthrough — ideal for the
    Windows raw stream too), or
  * the **no-root CLI** `canon-g2010-print.py`, which renders PS/PDF with the
    proven generator and posts the carriage to the sink.
* One writer at a time, enforced by `flock` on `/var/tmp/canon-live-test.lock`
  (the same lock the live-test harness uses).

### Design decision: raw queue, not a CUPS filter
CUPS **filters and backends run as root** from `/usr/lib/cups/filter`; a
non-root user cannot install them. Since <server> gives us `lpadmin` but **not**
passwordless sudo, the shipped queue is **raw** (no root filter needed) and local
conversion is done by the CLI. A production install **with root** can
additionally drop a filter wrapper so the queue does native conversion — the
`cups/canon-g2010-filter.py` + `cups/canon-g2010-native.ppd` are provided for that.

## Files

| path | purpose |
|---|---|
| `deploy/sink/canon-g2010-sink.py` | raw 9100 receiver → `/dev/usb/lp0`; flock; bounded job; SIGTERM-safe; `--dry-run` |
| `deploy/canon-g2010-print.py` | **no-root** local print CLI (PS/PDF → inline BJRaster3 → sink or device) |
| `deploy/cups/canon-g2010-filter.py` | optional CUPS filter (PS/PDF stdin → inline BJRaster3 stdout) |
| `deploy/cups/canon-g2010-smart.py` | **smart** filter: ready Canon raw passthrough, PDF/PS conversion, CUPS/PWG raster decode |
| `deploy/cups/cupsraster.py` | dependency-free CUPS/PWG raster decoder |
| `deploy/cups/smart-test.sh` | offline tests for the smart filter (4 input types) |
| `deploy/repoint-shared-queue.sh` | reversible redirect of `G2010-series` to the native path (backup + rollback) |
| `deploy/PLAN-server-side.md` | server-side plan: routing facts, exact change/rollback, duplex options |
| `deploy/cups/canon-g2010-native.ppd` | optional PPD for the root-installed native filter chain |
| `deploy/systemd/canon-g2010-sink.service` | runs the sink as `root:lp` (can open `root:lp` lp0) |
| `deploy/install.sh` | idempotent install; `--no-service` = queue only (no root) |
| `deploy/uninstall.sh` | exact reverse; never touches existing queues |
| `deploy/check.sh` | **non-printing** offline verification (sink, queue, dry-run 9100) |

## Privilege — READ FIRST

On <server> the login user (`<user>`) **cannot** write `/dev/usb/lp0`
(`crw-rw---- root lp`; not in group `lp`) and **cannot** `sudo` non-interactively
(`sudo` demands a password). `lpadmin` **does** work (group `lpadmin`).

Two supported options:

1. **With root (recommended, full path):**
   `sudo deploy/install.sh` — installs the sink **service as `root:lp`** (no human
   added to `lp`) and the optional filter wrapper; creates the raw queue.
2. **Without root (works today for everything except live device delivery):**
   `deploy/install.sh --no-service` creates the raw queue. Run the sink by hand
   when needed:
   ```bash
   python3 deploy/sink/canon-g2010-sink.py --listen 0.0.0.0 --port 9100
   ```
   (device delivery then needs root or membership in `lp`). For **dry-run** and
   **local rendering**, no root is required at all.

**No packages installed. No existing queue, PPD, or `cupsd.conf` modified.**

## Install

```bash
cd canon-g2010-native-ds41f
sudo ./deploy/install.sh          # full path (service + queue)
# or
./deploy/install.sh --no-service  # queue only, no root
```

## Verify (NO printing)

```bash
./deploy/check.sh
```

`check.sh` confirms: sink active + `:9100` listening; `:631` (cupsd) listening;
queue present and pointing at the sink; existing queues untouched; and a
**dry-run 9100 receive** (writes to `/var/tmp/canon-g2010-dryrun`, never the device).

Offline, no-root checks you can run right now:

```bash
# 1. render + verify the native carriage (no device)
./deploy/canon-g2010-print.py --dry-run --out /tmp/x.prn test.ps
build/out/bjlverify /tmp/x.prn            # -> "ok": true

# 2. dry-run sink by hand (file only)
python3 deploy/sink/canon-g2010-sink.py --port 9101 --dry-run \
        --dry-run-dir /var/tmp/canon-g2010-dryrun &
printf 'hello' | nc 127.0.0.1 9101; sleep 1; ls -l /var/tmp/canon-g2010-dryrun/

# 3. raw queue -> sink passthrough with the queue pointed at the dry sink
lpadmin -p G2010-native -v socket://127.0.0.1:9101        # point at dry sink
lp -d G2010-native -o raw test.ps                         # lands as a file
```

## Windows steps (for <user>)

1. On the Windows PC, install the **Canon G2010 Windows driver** (standard Canon
   G2010-series installer).
2. Add a port: **Settings → Bluetooth & devices → Printers & scanners → Add a
   printer → "The printer that I want isn't listed" → Add a printer using an IP
   address or hostname → Device type: TCP/IP Device → Hostname:** `<server-ip>`
   **Port:** `9100` (Standard TCP/IP Port).
   *Alternative (IPP):* `http://<server-ip>:631/printers/G2010-native`.
3. Choose the **Canon G2010 series** driver when asked.
4. In **Printing Preferences**, enable **2-Sided Printing (Manual Duplex)**.
   Print a test doc: Windows prints side 1, shows the **manual-duplex popup**
   ("reinsert sheets, press OK"), prints side 2 — **natively on Windows**.
5. The raw stream lands on <server>'s sink and is written to the printer unchanged.

## Safety / limits

* `--dry-run` **never** opens the device (writes to `/var/tmp/canon-g2010-dryrun`).
* Sink refuses a job if the flock is held (no byte interleaving).
* Jobs capped (`--max-bytes`, default 64 MiB) and idle-timed (`--idle-timeout`).
* Uninstall is exact; `G2010-manual-duplex` / `G2010-series` are never touched.

STOP conditions respected: this tree makes **no** physical print.
