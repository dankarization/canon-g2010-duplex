# Inline-BJRaster3 carriage — the format the real G2010 accepted

This note closes the gap found by the live test: the CNCL path
(`tocnpwg | tocanonij` → `SendData`/PWG) is **ignored** by the G2010, while the
Windows-DDI-style **inline BJRaster3** stream (taken from the accepted pilot)
**printed 2 pages**. Below is what was recovered and what is still unknown.

## Evidence base

* **Accepted pilot** (printed 2 physical pages), then used as the format anchor:
  `canon-port-proof/output/milestone35/…/c46e67….prn`
  sha256 `3db77aee5742df2907ce1dc1384218d388457f65e8ec8e494c0733391550e8f4`,
  20194 bytes.
* **Our rejected CNCL output**: `evidence/job-two/fronts.prn`
  (`StartJob/VendorCmd/EndJob` mini-job + `SetConfiguration/…/SendData ×N`).

## Structural diff (ours-CNCL vs pilot-accepted), first page

| aspect | our CNCL stream | accepted pilot |
|---|---|---|
| job ops | StartJob, **SetConfiguration**, VendorCmd, **SendData ×N**, EndJob | StartJob, **SetJobConfiguration**, VendorCmd(**ModeShift**), EndJob |
| raster carriage | PWG page inside `SendData` XML | inline `ESC(F` PackBits bands inside `VendorCmd` |
| `ESC(F` band count | 0 | 34 (2 pages × 17) |
| raster bytes | 0 inline | 18562 inline |

Root cause: the G2010 (protocol 1) does **not** consume the CNCL `SendData`
PWG carriage; it consumes the raw BJL-style inline raster.

## Recovered grammar (inline BJRaster3)

```
<?xml…StartJob…host_environment=windows?>
<?xml…SetJobConfiguration…datetime?>
<?xml…VendorCmd…<vcn:ijoperation>ModeShift</vcn:ijoperation><vcn:ijmode>1</vcn:ijmode>…?>
    ESC[K 02 00 00 0f
    ESC($ 010001000100010000        ESC(T 030404
    ESC(b 01  ESC(c 300002  ESC(d 02580258 (600×600 dpi)
    ESC(t 80…  ESC(p 02…  ESC(P 00 0d…  ESC(l 340000
    ESC(v 00 ESC(w 00 ESC(C 00 ESC(q 01 ESC(b 01 ESC(I 01 ESC(J 08
    ESC(e 007a  ESC(L 4b
    ESC(F <len16le> <band>          ×  N bands (8 raster lines each)
    ESC(e 02 00 00 01 0c            page eject
<?xml…EndJob?>
```

**Line codec** (recovered; verified semantically exact on all 34 pilot bands):
each band is a concatenation of lines, each line a PackBits run stream ended by
`0x80`:
* `0x00..0x7f` → literal run of `n+1` bytes
* `0x81..0xff` → repeat next byte `257-n` times
* `0x80` → line delimiter

## What this prototype reproduces

`src/bjraster3.py` — the encoder + assembler.
`src/native-bjl.py` — renders a document with native Ghostscript to 1bpp and
builds `fronts.prn` / `backs.prn`.
`src/canon-bjl-duplex.py` — one-submit manual-duplex gate (fronts → explicit
refeed → backs) over this carriage.
`src/bjlverify.c` → `build/out/bjlverify` — offline verifier.

Proven offline:
* the verifier `ok:true` on the **accepted pilot** (34/34 bands decode clean);
* our **page-1 setup preamble is byte-identical** to the accepted pilot's;
* `canon-bjl-duplex.py submit … continue` produces valid fronts/backs with no
  backs before the refeed token;
* all 17 checks in `tests/run-tests.sh` pass.

## Residual unknown (honest)

The accepted pilot's raster was **1584 dots wide × 136 lines**, i.e. a narrow
strip, yet it printed a full A4 page — so the printer scales/positions the
raster from the `ESC(` setup words. Our default output renders the **full A4
page** (4960×7014 at 600 dpi → ~878 bands), assuming the printer scales by the
setup words the same way. This is the one thing that cannot be settled without
another physical test.

Two candidate deliverables are provided for main's live re-test, both
format-verified:
* `evidence/live-retest/fronts.prn`, `backs.prn` — full-page render
  (native-bjl.py default).
* The pilot's own geometry can be requested with `--dpi 191` style settings if
  full-page scaling is rejected.

Neither stream has been printed by this subagent. Physical acceptance remains
main's live gate under `/var/tmp/canon-live-test.lock`.
