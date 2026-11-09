#!/usr/bin/env python3
"""canon-g2010-filter — CUPS filter: PostScript/PDF stdin -> inline BJRaster3 stdout.

Used by the local CUPS queue `G2010-native` so Linux clients print natively
without Wine. Reads the job from stdin, renders it with the proven native
generator (src/native-bjl-strip.py, which reuses src/bjraster3.py), and writes
the accepted inline-BJRaster3 carriage to stdout for the backend.

CUPS invokes filters as:  filter job-id user title copies options [file]
We ignore the arguments and read stdin (CUPS passes the file via stdin or argv[6]).

Environment overrides:
  CANON_G2010_HEIGHT  raster height in lines (default 7016, full A4 at 600dpi)
  CANON_G2010_WIDTH   line width in bytes  (default 620  = 4960 dots, full A4)
                      use 198 for the accepted pilot's 1584-dot strip framing.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(os.environ.get("CANON_G2010_ROOT", pathlib.Path(__file__).resolve().parents[2]))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    gen = _load("native_bjl_strip", ROOT / "src" / "native-bjl-strip.py")

    # CUPS filter argv:  job-id user title copies options [file]
    # The job data arrives on stdin, or (for some chains) as argv[6].
    if len(sys.argv) >= 7 and sys.argv[6] not in ("-", "") and os.path.exists(sys.argv[6]):
        with open(sys.argv[6], "rb") as fh:
            data = fh.read()
    else:
        data = sys.stdin.buffer.read()
    if not data:
        return 0

    height = int(os.environ.get("CANON_G2010_HEIGHT", "7016"))
    width = int(os.environ.get("CANON_G2010_WIDTH", "620"))

    # The generator takes a file path; give it a temp file with a sniffed suffix.
    suffix = ".pdf" if data[:4] == b"%PDF" else ".ps"
    fd, tmp = tempfile.mkstemp(prefix="canon-g2010-", suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        pdf = gen._to_pdf(pathlib.Path(tmp))
        n = max(1, gen._page_count(pdf))
        out = gen.build_pass(pdf, list(range(1, n + 1)), "00000001", "20260101000000",
                             "00000000-0000-0000-0000-0000000000f1",
                             "CUPS local", height, width)
        sys.stdout.buffer.write(out)
        sys.stdout.buffer.flush()
    except Exception as e:                          # report and fail the job
        print(f"canon-g2010-filter: error: {e}", file=sys.stderr)
        return 1
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
