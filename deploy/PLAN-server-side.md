# Server-side plan: route the owner's existing clients through the native path

**Nothing is changed on production queues until the owner confirms.** All commands
below are shown with their exact rollback.

## 1. Facts: how the owner's clients actually reach the printer (verified on <server>)

Read-only inspection of <server> (`<server-host>`, `<server-ip>`):

| fact | value |
|---|---|
| Mobility Print service | `pc-mobility-print.service` — **active**, binary `/home/papercut/pc-mobility-print/pc-mobility-print` (PaperCut, root) |
| Mobility ports | **9163** (and 9164) listening, `*:9163` / `*:9164` |
| cupsd | active on **:631**; `cups-browsed` active; avahi active |
| Windows/Mac/phone queue | **`G2010-series`** — job history shows **59 jobs** from `SM-S918B`, `dankarization`, `mobile`, `WORKGROUP\MAIN-PC$` |
| other queue | `G2010-manual-duplex` (1 job history) |
| our queue | `G2010-native` (created earlier; 1 job) |
| `G2010-series` device | `usb://Canon/G2010%20series?serial=<printer-serial>&interface=1` |
| `G2010-series` PPD | Gutenprint v5.3.4 (Canon G2000 series), the classic filter chain |
| printer USB | `Bus 002 Device 008: ID 04a9:183a Canon G2010 series`; `/dev/usb/lp0` `root:lp` |

**Conclusion:** all client jobs (Mobility/BYOD/phone, Mac, Windows) land on the
single shared queue **`G2010-series`**. That is the queue we redirect. The second
queue (`G2010-manual-duplex`) is **not** touched.

> Note: Mobility Print republishes the local CUPS queues it is configured to
> share; the observable evidence (job history + ports) shows `G2010-series` is
> the queue receiving them. If the owner later wants, we can dump PaperCut's
> config with root to confirm its share list exactly.

## 2. Reversible redirection of `G2010-series` to the native path

Script: `deploy/repoint-shared-queue.sh` (`--dry-run` / `--apply` / `--rollback`).

It changes **only** `G2010-series`: backups first, then device-uri + the native
filter PPD. Nothing is written before `--apply`.

### Exact backup + change (`sudo`)
```bash
STAMP=$(date +%Y%m%d-%H%M%S); DIR=/var/tmp/canon-g2010-backup/$STAMP
sudo install -d -m0700 "$DIR"
sudo cp -a /etc/cups/printers.conf "$DIR/printers.conf.bak"
sudo cp -a /etc/cups/ppd/G2010-series.ppd "$DIR/G2010-series.ppd.bak"
# point the shared queue at the native sink and use our filter
sudo lpadmin -p G2010-series -E -v socket://127.0.0.1:9100
sudo lpadmin -p G2010-series -P <tree>/deploy/cups/canon-g2010-native.ppd
sudo lpadmin -p G2010-series -o job-sheets=none
```
(Or simply `sudo deploy/repoint-shared-queue.sh --apply`, which does the above.)

### Exact rollback
```bash
sudo cp -a "$DIR/printers.conf.bak" /etc/cups/printers.conf
sudo cp -a "$DIR/G2010-series.ppd.bak" /etc/cups/ppd/G2010-series.ppd
sudo systemctl restart cups
```
(Or `sudo deploy/repoint-shared-queue.sh --rollback --stamp $STAMP`.)

`--dry-run` prints the same commands and writes nothing (verified on <server>).

## 3. Smart filter (`deploy/cups/canon-g2010-smart.py`)

One filter for every input the shared queue can receive:

1. **Ready Canon raw** — starts with `<?xml` and contains `StartJob` (the Windows
   Canon driver's raw inline-BJRaster3, or our own generated stream):
   **byte-for-byte passthrough**.
2. **PDF / PostScript** — rendered to inline BJRaster3 by the proven native
   generator (`src/native-bjl-strip.py` → `src/bjraster3.py`).
3. **CUPS/PWG raster** (`RaS3`/`3SaR`… 1796-byte `cups_page_header2_t`) — decoded
   by `deploy/cups/cupsraster.py` (gray/RGB/CMYK, 8/24/32 bpp, uncompressed) and
   resampled, then encoded to inline BJRaster3.
4. **Anything else** — passthrough with a warning (domain is 1:1 raw).

Offline tests: offline dry-run checks:

| input | result |
|---|---|
| ready Canon raw (32728 B) | byte-for-byte identical ✅ |
| PostScript | inline BJRaster3 `ok:true`, 280 bands ✅ |
| PDF | inline BJRaster3 `ok:true` ✅ |
| real CUPS raster (2400×3000 sRGB) | decoded → inline BJRaster3 `ok:true`, 280 bands ✅ |

Run: `bash deploy/cups/smart-test.sh` (set `CANON_G2010_RASTER_SAMPLE=/tmp/r.ras`
for the raster case, generated with `gstoraster`).

## 4. Manual duplex for clients that have no popup — honest limits + options

**Honest limitation:** the "reinsert sheets, press OK" **flip prompt is a
client-driver feature.** The G2010 itself has no duplexer, so no server-side
trick can make a single-sided printer flip paper on its own. A server can,
however, orchestrate a two-pass job. Options, best first:

- **A (recommended) — Server-side manual duplex with a pause anchor.**
  The queue accepts a normal two-sided request. Our filter splits the document
  into **odd pages (fronts)** and **even pages (backs)** and prints **fronts
  first**, then **waits** for a physical "paper reloaded" signal, then prints
  **backs**. The signal can be, in order of preference: (1) a small
  **reload-confirm** file/lock the user touches (or a one-key helper
  `canon-g2010-duplex continue`), or (2) a printer **panel/continue** action we
  already model with a refeed ACK token (`canon-duplex.py` gate). This preserves
  current clients (they just see one queue) and requires no per-client change.
  *Caveat:* the pause makes the job wait on a human; the queue stays "processing".
- **B — Two separate queues:** `G2010-fronts` and `G2010-backs`; the user prints
  front-sides, flips the stack, prints back-sides. Zero magic, fully transparent,
  but the user must issue two print commands (a client-side step).
- **C — Windows driver native popup over raw 9100** (previous design): the
  Windows *driver* does the popup and streams raw; the Mac/phone clients still
  get single-sided through `G2010-series`. Good for Windows only.

**Recommendation:** implement **A** for `G2010-series`, because it is the only
option that gives real two-sided output **from unchanged clients** and matches
the G2010's manual-duplex reality. Its pause/ACK mechanism is already prototyped
in `src/canon-duplex.py` (refeed token gate). B is the safe fallback if the
owner dislikes any wait; C stays available for the Windows PC.

## 5. What is NOT done / blockers

* No production queue was modified (only `--dry-run` was run).
* Sink **service** is already installed and running as root on :9100 (present at
  recon); the CUPS filter wrapper is installed at `/usr/lib/cups/filter/canon-g2010`
  (currently pointing at `canon-g2010-filter.py`; `install.sh` now ships the
  **smart** filter — repointing the wrapper needs root).
* `sudo` on <server> requires a password, so `--apply` and the filter-wrapper update
  need the owner's root action.
