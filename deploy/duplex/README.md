# Server-side manual duplex for `G2010-series` (any client, no client changes)

**Nothing on production is changed until the owner approves.** The package below
is offline-tested (14/14 locally and 14/14 on <server>), reversible, and does not
touch `G2010-manual-duplex`.

## Why server-side (honest premise)

The G2010 has **no hardware duplexer**. Two-sided printing is therefore always
**manual**: print one side → flip the stack → print the other. There is no way to
make the printer flip paper itself. What we *can* do is let the **server**
orchestrate the manual cycle, so **no client needs any change or install**: any
device (Windows/Mac/phone) sends a normal 2-sided job; the server prints the
fronts, shows a web page, and — after the human flips the sheets and clicks — prints
the backs.

## What the owner sees

1. Print a document with **2-sided** enabled in the normal print dialog.
2. The **front sides** come out immediately.
3. On any device, open **http://<server-ip>:8099** in a browser — it lists the
   pending job(s) and a single button **«Перевернул — Печатать обратные»**.
4. Flip the stack in the tray, press the button → the **back sides** print.

No app, no profile, no driver change on the client. The web page is plain HTML.

## Components

| file | role |
|---|---|
| `deploy/duplex/canon-g2010-duplex-filter.py` | CUPS filter: 1-sided → normal; 2-sided → split fronts/backs, print fronts now, register backs for confirmation |
| `deploy/duplex/canon-g2010-duplexd.py` | HTTP confirm service (:8099): job list, per-job button, token-gated one-shot confirm, prints backs on confirm, timeouts, `--dry-run` |
| `deploy/duplex/canon-g2010-duplex.ppd` | PPD exposing the `Duplex` option (Off / long-edge / short-edge); routes PS/PDF/raster to the duplex filter |
| `deploy/duplex/canon-g2010-duplexd.service` | systemd unit for the daemon |
| `deploy/duplex/install-duplex.sh` | idempotent installer (dry-run / apply / rollback) |
| `deploy/duplex/duplex-test.sh` | offline test suite (14 checks) |

## Behaviour details

* **Default is single-sided.** The `Duplex` option defaults to *Off*; a job that
  does not request 2-sided is printed exactly as before — **no pause, no state**.
* **2-sided split:** fronts = odd pages, backs = even pages, padded with one blank
  page when the count is odd (so the back pass lines up).
* **Confirmation is bound to the job-id + a per-job token**; a stray click cannot
  print the wrong job; confirming twice is idempotent.
* **Timeouts:** `CANON_G2010_DUPLEX_TIMEOUT` (seconds, 0 = never). An expired job
  refuses confirmation (HTTP 410) instead of printing stale backs.
* **Delivery:** on confirm the backs stream is sent to the native sink
  (`127.0.0.1:9100` → `/dev/usb/lp0`); `--dry-run` writes a file instead.

## Offline test evidence (no printing)

`bash deploy/duplex/duplex-test.sh` → **14 / 14 pass** (local and on <server>):

| check | result |
|---|---|
| 1-sided default: valid output, **no** duplex state created | ✅ |
| 2-sided: fronts pass valid, backs pass valid | ✅ |
| split state = fronts [1,3], backs [2,4] (padded), awaiting | ✅ |
| HTTP `/healthz`, `/status` lists job | ✅ |
| bad token → 403; correct token → 200 | ✅ |
| backs delivered (dry-run) and verify `ok:true` | ✅ |
| double-confirm idempotent (→ 200), state `done` | ✅ |
| timeout expiry → 410 | ✅ |
| backs reach the **sink** (dry sink) and verify `ok:true` | ✅ |

## Install (needs root; requires owner approval)

```bash
cd canon-g2010-native-ds41f
./deploy/duplex/install-duplex.sh --dry-run     # show plan, no changes
sudo ./deploy/duplex/install-duplex.sh          # apply
```

The installer: copies the filter+daemon to `/usr/local/lib/canon-g2010/`, installs
`/usr/lib/cups/filter/canon-g2010-duplex`, enables `canon-g2010-duplexd.service`,
**backs up** `printers.conf` + `G2010-series.ppd` to
`/var/tmp/canon-g2010-backup/<stamp>/`, then repoints **`G2010-series`** at
`socket://127.0.0.1:9100` with the duplex PPD. `G2010-manual-duplex` is untouched.

## Rollback (exact)

```bash
sudo /etc/.../install-duplex.sh --rollback --stamp <stamp>
# equivalently:
sudo cp -a /var/tmp/canon-g2010-backup/<stamp>/printers.conf.bak /etc/cups/printers.conf
sudo cp -a /var/tmp/canon-g2010-backup/<stamp>/G2010-series.ppd.bak /etc/cups/ppd/G2010-series.ppd
sudo systemctl disable --now canon-g2010-duplexd.service
sudo systemctl restart cups
```

## Known limits / STOP conditions

* The confirm step requires a **human** (that is inherent to manual duplex).
* A CUPS filter cannot block its output pipe for minutes, so **pass 2 is emitted
  by the daemon on confirmation**, not by the waiting filter. User-visible flow is
  still "print → flip → click".
* `sudo` on <server> requires a password; installation needs the owner's root action.
* Not installed on production yet; no printing performed by this task.
