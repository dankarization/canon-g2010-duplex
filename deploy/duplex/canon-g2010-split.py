#!/usr/bin/env python3
"""canon-g2010-split — CUPS filter: manual duplex + COLOUR in one queue (Variant A).

Wiring (the proven-correct one):

    PDF/PS -> CUPS universal (pdftoraster) -> application/vnd.cups-raster
           -> [THIS filter]  -> colour BJRaster3 (application/vnd.cups-raw)
           -> socket://127.0.0.1:9100

The PPD declares:
    *cupsFilter: "application/vnd.cups-raster 100 canon-g2010-split"
(i.e. this filter REPLACES rastertogutenprint as the last stage) and calls the
stock Gutenprint filter internally, so the raster->BJRaster3 step stays the
vendor driver (colour, ESC(L)="CMYK").

Behaviour
---------
* 1-sided (no two-sided option, or `canon-duplex-pass=backs`): pass the whole
  raster through rastertogutenprint -> colour stream out. No state.
* 2-sided: split the CUPS raster into ODD pages (fronts) and EVEN pages (backs);
  render fronts now (colour), save the backs raster and register with duplexd,
  which later submits it back with `canon-duplex-pass=backs`.

Env: CUPS_SERVERBIN (filter dir), PPD (queue PPD), CANON_G2010_DUPLEX_STATE.
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import secrets
import subprocess
import sys
import tempfile
import time

STATE_DIR = pathlib.Path(os.environ.get("CANON_G2010_DUPLEX_STATE",
                                        "/var/tmp/canon-g2010-duplex"))
SERVERBIN = os.environ.get("CUPS_SERVERBIN", "/usr/lib/cups")
PPD = os.environ.get("PPD", "")

_rspec = None


def _raster():
    global _rspec
    if _rspec is None:
        here = pathlib.Path(__file__).resolve().parent
        for c in [here / "cupsraster_lib.py", here.parent / "cups" / "cupsraster.py",
                  pathlib.Path("/usr/local/lib/canon-g2010/cupsraster.py")]:
            if c.exists():
                spec = importlib.util.spec_from_file_location("cupsraster_lib", str(c))
                m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
                _rspec = m; break
    return _rspec


def _cupsraster_pages(data: bytes):
    """Split a CUPS/PWG raster stream into per-page byte blobs.

    Returns list of (page_bytes) where each entry = sync(4) + header(1796) +
    pixel data, using cups_page_header2_t offsets.
    """
    pages = []
    i = 0
    n = len(data)
    import struct
    while i + 4 + 1796 <= n:
        sw = data[i:i + 4]
        if sw not in (b"3SaR", b"2SaR", b"tSaR", b"UNIR", b"RaS3", b"RaS2"):
            break
        hdr = data[i + 4:i + 4 + 1796]
        bpl = struct.unpack_from("<I", hdr, 392)[0]
        h = struct.unpack_from("<I", hdr, 376)[0]
        size = 4 + 1796 + bpl * h
        pages.append(data[i:i + size])
        i += size
    return pages


def _gutenprint(raster: bytes, jobid, opts) -> bytes:
    fd, tmp = tempfile.mkstemp(prefix="canon-split-", suffix=".ras")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(raster)
        p = subprocess.run([f"{SERVERBIN}/filter/rastertogutenprint.5.3",
                            str(jobid), "cups", "job", "1", opts, tmp],
                           capture_output=True, timeout=1800)
        if p.returncode:
            raise RuntimeError(f"gutenprint rc={p.returncode}: "
                               f"{p.stderr.decode(errors='replace')[:300]}")
        return p.stdout
    finally:
        os.unlink(tmp)


def _duplex_requested(opts: str) -> bool:
    kv = dict(p.split("=", 1) for p in opts.split() if "=" in p)
    if kv.get("canon-duplex-pass", "").lower() == "backs":
        return False
    if kv.get("sides", "").lower().startswith("two-sided"):
        return True
    if kv.get("Duplex", "").lower() in ("duplexnotumble", "duplextumble"):
        return True
    return False


def _register(jobid, user, title, backs_raster: bytes, fronts, backs, queue):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(8)
    (STATE_DIR / f"{jobid}.backs.ras").write_bytes(backs_raster)
    rec = {"jobid": jobid, "user": user, "title": title, "token": token,
           "fronts_pages": fronts, "backs_pages": backs,
           "backs_raster": str(STATE_DIR / f"{jobid}.backs.ras"),
           "queue": queue, "created": time.time(), "state": "awaiting_confirm"}
    tmp = STATE_DIR / f".{jobid}.json.tmp"
    tmp.write_text(json.dumps(rec, indent=2))
    tmp.replace(STATE_DIR / f"{jobid}.json")
    return rec


def main() -> int:
    argv = sys.argv
    jobid = argv[1] if len(argv) > 1 else "1"
    user = argv[2] if len(argv) > 2 else "unknown"
    title = argv[3] if len(argv) > 3 else "untitled"
    opts = argv[5] if len(argv) > 5 else ""
    if len(argv) >= 7 and argv[6] not in ("-", "") and os.path.exists(argv[6]):
        data = pathlib.Path(argv[6]).read_bytes()
    else:
        data = sys.stdin.buffer.read()
    if not data:
        return 0

    queue = os.environ.get("CANON_G2010_QUEUE", "G2010-series")

    if not _duplex_requested(opts):
        out = _gutenprint(data, jobid, opts)
        sys.stdout.buffer.write(out); sys.stdout.buffer.flush()
        print(f"canon-g2010-split: 1-sided colour ({len(out)} B)", file=sys.stderr)
        return 0

    pages = _cupsraster_pages(data)
    if len(pages) < 2:
        out = _gutenprint(data, jobid, opts)
        sys.stdout.buffer.write(out); sys.stdout.buffer.flush()
        print("canon-g2010-split: 2-sided but <2 pages; 1-sided colour", file=sys.stderr)
        return 0

    fronts_idx = list(range(0, len(pages), 2))   # 0-based: pages 1,3,5...
    backs_idx = list(range(1, len(pages), 2))    # pages 2,4,6...
    fronts_raster = b"".join(pages[i] for i in fronts_idx)
    backs_raster = b"".join(pages[i] for i in backs_idx)

    out = _gutenprint(fronts_raster, jobid, opts)
    sys.stdout.buffer.write(out); sys.stdout.buffer.flush()

    rec = _register(jobid, user, title, backs_raster,
                    [i + 1 for i in fronts_idx], [i + 1 for i in backs_idx], queue)
    print(f"canon-g2010-split: 2-sided colour. fronts {rec['fronts_pages']} "
          f"({len(out)} B). backs {rec['backs_pages']} registered "
          f"token={rec['token']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
