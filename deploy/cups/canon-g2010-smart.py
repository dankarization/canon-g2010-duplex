#!/usr/bin/env python3
"""canon-g2010-smart — one CUPS filter that feeds the G2010 native path from any
input the shared queue receives.

Decision logic (in order):

  1. Already-Canon raw stream — starts with `<?xml` and contains `StartJob`
     (the Windows Canon driver's raw inline-BJRaster3 carriage, or our own
     generated stream): copy BYTE-FOR-BYTE to stdout (target application/vnd.cups-raw).
     This is the key case for the owner's Windows/Mac driver, which renders
     locally and just needs a raw pipe to the printer.
  2. PDF (`%PDF`) or PostScript (`%!PS`): render with the proven native
     generator (src/native-bjl-strip.py -> src/bjraster3.py) to inline BJRaster3.
  3. Anything else: pass through unchanged, but log a warning to stderr, so the
     backend/device decides (usually a 1:1 target that also accepts raw).

CUPS filter argv:  job-id user title copies options [file]
Document data on stdin, or argv[6] when CUPS passes a file.

Env overrides:
  CANON_G2010_HEIGHT  raster height in lines (default 7016 = full A4 at 600 dpi)
  CANON_G2010_WIDTH   line width in bytes  (default 620 = 4960 dots; 198 = pilot strip)
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import struct
import sys
import tempfile

def _find_gen() -> pathlib.Path:
    """Locate native-bjl-strip.py: installed flat (same dir) or source tree."""
    env = os.environ.get("CANON_G2010_ROOT")
    cands = []
    if env:
        cands += [pathlib.Path(env) / "native-bjl-strip.py",
                  pathlib.Path(env) / "src" / "native-bjl-strip.py"]
    here = pathlib.Path(__file__).resolve().parent
    cands.append(here / "native-bjl-strip.py")
    for up in (here, here.parent, here.parent.parent, here.parent.parent.parent):
        cands.append(up / "native-bjl-strip.py")
        cands.append(up / "src" / "native-bjl-strip.py")
    for c in cands:
        if c.exists():
            return c
    raise FileNotFoundError("native-bjl-strip.py not found (set CANON_G2010_ROOT)")


GEN_PATH = _find_gen()


def _find_sibling(name: str) -> pathlib.Path:
    """Locate a helper module next to this file, or in the source tree."""
    here = pathlib.Path(__file__).resolve().parent
    for c in (here / name, here.parent / "src" / name,
              here.parent.parent / "src" / name, here.parent / name):
        if c.exists():
            return c
    return here / name


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _is_canon_raw(data: bytes) -> bool:
    head = data[:1024].lstrip(b"\xef\xbb\xbf \t\r\n\x00")
    return head.startswith(b"<?xml") and b"StartJob" in data[:8192]


def _is_cups_raster(data: bytes) -> bool:
    if len(data) < 8:
        return False
    return struct.unpack_from("<I", data, 0)[0] in (
        0x52615333, 0x52615332, 0x52615374, 0x554E4952,   # big-endian words
        0x33536152, 0x32536152, 0x74536152)               # byte-swapped spellings


def convert(data: bytes) -> bytes:
    """Convert any supported input to inline BJRaster3 (or pass ready raw through).

    This is the single conversion entry point shared by the plain smart filter and
    the duplex filter. Returns the bytes to emit.
    """
    if not data:
        return b""
    # 1. ready Canon raw -> byte-for-byte passthrough
    if _is_canon_raw(data):
        return data
    # 2. PDF / PostScript -> native inline BJRaster3
    if data[:4] == b"%PDF" or data[:4] == b"%!PS":
        height = int(os.environ.get("CANON_G2010_HEIGHT", "7016"))
        width = int(os.environ.get("CANON_G2010_WIDTH", "620"))
        gen = _load("native_bjl_strip", GEN_PATH)
        suffix = ".pdf" if data[:4] == b"%PDF" else ".ps"
        fd, tmp = tempfile.mkstemp(prefix="canon-g2010-", suffix=suffix)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            pdf = gen._to_pdf(pathlib.Path(tmp))
            n = max(1, gen._page_count(pdf))
            return gen.build_pass(pdf, list(range(1, n + 1)), "00000001",
                                  "20260101000000",
                                  "00000000-0000-0000-0000-0000000000f1",
                                  "CUPS smart", height, width)
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    # 2b. CUPS/PWG raster -> decode -> native inline BJRaster3
    if _is_cups_raster(data):
        height = int(os.environ.get("CANON_G2010_HEIGHT", "7016"))
        width = int(os.environ.get("CANON_G2010_WIDTH", "620"))
        cr = _load("cupsraster", _find_sibling("cupsraster.py"))
        B = _load("bjraster3", _find_sibling("bjraster3.py"))
        dst_dots = width * 8
        pages_lines = [cr.page_to_lines(p, dst_dots, height, target_dpi=600)
                       for p in cr.iter_pages(data)]
        if not pages_lines:
            return data
        return B.build_job(pages_lines, jobid="00000001",
                           jobname="CUPS smart raster", username="cups",
                           computername="OPENCLAW",
                           jobdesc="00000000-0000-0000-0000-0000000000f1",
                           datetime="20260101000000")
    # 3. unknown -> passthrough
    return data


def main() -> int:
    if len(sys.argv) >= 7 and sys.argv[6] not in ("-", "") and os.path.exists(sys.argv[6]):
        data = pathlib.Path(sys.argv[6]).read_bytes()
    else:
        data = sys.stdin.buffer.read()
    if not data:
        return 0
    out = convert(data)
    kind = "passthrough" if out is data or out == data else "converted"
    print(f"canon-g2010-smart: {kind} ({len(data)} -> {len(out)} bytes)",
          file=sys.stderr)
    sys.stdout.buffer.write(out)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
