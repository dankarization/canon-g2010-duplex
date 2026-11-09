#!/usr/bin/env python3
"""canon-g2010-print — print a PS/PDF locally with the NATIVE inline-BJRaster3 path.

Renders the document with the proven generator (src/native-bjl-strip.py, which
reuses src/bjraster3.py) and delivers the accepted raw carriage to the printer
either through the sink socket (default) or straight to /dev/usb/lp0.

This is the no-root path for Linux clients: it needs no CUPS filter installation.
For a fully CUPS-native experience, install the queue+filter as root instead
(see deploy/install.sh) and print with `lp -d G2010-native`.

Examples
--------
  ./canon-g2010-print.py report.pdf
  ./canon-g2010-print.py --strip report.pdf        # pilot 1584-dot framing
  ./canon-g2010-print.py --dry-run report.pdf      # write to a file, no device
  ./canon-g2010-print.py --to-device report.pdf    # bypass the sink, write lp0
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
TREE = HERE.parent
GEN_PATH = TREE / "src" / "native-bjl-strip.py"


def _load_gen():
    spec = importlib.util.spec_from_file_location("native_bjl_strip", str(GEN_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _render(gen, src: pathlib.Path, height: int, width: int) -> bytes:
    pdf = gen._to_pdf(src)
    n = max(1, gen._page_count(pdf))
    return gen.build_pass(pdf, list(range(1, n + 1)), "00000001", "20260101000000",
                          "00000000-0000-0000-0000-0000000000a1",
                          "Native local", height, width)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", type=pathlib.Path, help="PostScript or PDF to print")
    ap.add_argument("--host", default="127.0.0.1", help="sink host (default 127.0.0.1)")
    ap.add_argument("--port", type=int, default=9100, help="sink port (default 9100)")
    ap.add_argument("--to-device", action="store_true",
                    help="write straight to /dev/usb/lp0 instead of the sink")
    ap.add_argument("--device", default="/dev/usb/lp0")
    ap.add_argument("--strip", action="store_true",
                    help="use the accepted pilot's 1584-dot strip framing (136 lines)")
    ap.add_argument("--dry-run", action="store_true",
                    help="write the carriage to a file and do not send it")
    ap.add_argument("--out", type=pathlib.Path, default=None,
                    help="dry-run output file")
    a = ap.parse_args()

    if not a.file.exists():
        print(f"error: no such file: {a.file}", file=sys.stderr)
        return 2

    gen = _load_gen()
    height = 136 if a.strip else 2240
    width = 198 if a.strip else 620
    data = _render(gen, a.file, height, width)
    print(f"rendered {len(data)} bytes (height={height}, width={width}B)")

    if a.dry_run:
        out = a.out or pathlib.Path(tempfile.mkstemp(prefix="canon-g2010-", suffix=".prn")[1])
        out.write_bytes(data)
        print(f"dry-run: wrote {out} (never sent; no device touched)")
        return 0

    if a.to_device:
        fd = os.open(a.device, os.O_WRONLY | os.O_NOCTTY)
        try:
            off = 0
            while off < len(data):
                off += os.write(fd, data[off:off + 65536])
            os.fsync(fd)
        finally:
            os.close(fd)
        print(f"sent {len(data)} bytes to {a.device}")
        return 0

    with socket.create_connection((a.host, a.port), timeout=10) as s:
        s.sendall(data)
        try:
            s.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        time.sleep(0.5)
    print(f"sent {len(data)} bytes to sink {a.host}:{a.port}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
