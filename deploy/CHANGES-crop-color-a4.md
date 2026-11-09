# Round 3: cropping fix, color research, A4 default

## 1. Cropping FIXED (full A4 geometry)
* Root cause: renderer took a window from the FIRST INK ROW and defaulted to
  height=2240 (3.73"), so only the top of the page printed.
* Fix (`src/native-bjl-strip.py::_render_strip`):
  - anchor at the page's ABSOLUTE origin (row 0, byte 0) — no top-of-ink shift;
  - defaults now height=**7016**, width=**620** (4960 dots) = full A4 at 600 dpi.
* Numbers: old 2240 lines = **3.73"**; new 7016 lines = **11.69"** (A4 = 297 mm).
* Proof: a doc with text at page top AND bottom renders both
  (`TOP of page` @ row 452 = 0.75", `BOTTOM of page` @ row 6560 = 10.93");
  output = 877 bands = 7016 lines, `bjlverify ok:true`.

## 2. Color — verdict from research (NOT yet an encoder)
* The carriage HAS a per-page **plane-set selector** `ESC ( L`:
  - pilot (mono): `1b 28 4c 01 00 4b` = `ESC(L) "K"`;
  - real color streams: `1b 28 4c 04 00 43 4d 59 4b` = `ESC(L) "CMYK"`
    (verified in milestone5/file-port, milestone4/*, milestone11/*, windows-manual4).
* So **color clients already print color** through the smart/duplex filters' raw
  passthrough (verified byte-for-byte on a 7.1 MB CMYK stream).
* The **server-side PDF/PS→printer encoder is still 1 bpp monochrome**: the exact
  multi-plane packing inside `ESC ( F` is NOT recovered, so no bytes were invented.
  Details + next step: `evidence/COLOR-RESEARCH.md`.

## 3. A4 is the default; Letter not offered
* PPDs (`deploy/cups/canon-g2010-native.ppd`, `deploy/duplex/canon-g2010-duplex.ppd`):
  `DefaultPageSize: A4`, Letter entries removed → only A4 is advertised.
* Queue default set with `lpadmin -o media=A4`.
* Verified on a scratch queue (not production):
  `media-supported = iso_a4_210x297mm` (only), `media-default = iso_a4_210x297mm`,
  `lpoptions PageSize: *A4`.
