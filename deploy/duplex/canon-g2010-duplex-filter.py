#!/usr/bin/env python3
"""canon-g2010-duplex-filter — CUPS filter with SERVER-SIDE manual duplex.

The G2010 has no hardware duplexer, so two-sided printing is inherently MANUAL
(print one side, flip the stack, print the other). This filter lets that be
orchestrated by the SERVER for ANY client, with no client changes and no extra
software on the client.

Behaviour
---------
* Single-sided (default, i.e. the job does NOT request duplex): identical to the
  normal native path — render the whole document to inline BJRaster3 and emit it.
  No pauses, no state. Existing one-sided printing is unaffected.
* Two-sided (job requests duplex, e.g. `sides=two-sided-long-edge` or the PPD's
  `Duplex` option): split the document into FRONTS (odd pages) and BACKS (even
  pages, padded with one blank page if the count is odd). Emit ONLY the fronts
  now, and register the backs with the confirm daemon (`canon-g2010-duplexd`).
  The human opens http://<<server>>:8099, flips the stack, presses Continue, and
  the daemon prints the backs.

Why the filter does not block waiting: a CUPS filter cannot hold its output pipe
open for minutes (the backend/sink would time out), so pass 2 is delivered by the
daemon on confirmation. The user-visible flow is still "print, flip, click".

CUPS filter argv:  job-id user title copies options [file]
Options string is argv[5] (space-separated name=value).

Env:
  CANON_G2010_ROOT   locate the native generator (optional)
  CANON_G2010_DUPLEX_STATE  state dir (default /var/tmp/canon-g2010-duplex)
  CANON_DUPLEX       1 forces duplex-on, 0 forces off (testing)
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


def _find(name: str) -> pathlib.Path:
    env = os.environ.get("CANON_G2010_ROOT")
    here = pathlib.Path(__file__).resolve().parent
    cands = []
    if env:
        cands += [pathlib.Path(env) / name, pathlib.Path(env) / "src" / name]
    cands += [here / name, here.parent / name, here.parent / "src" / name,
              here.parent.parent / "src" / name, here.parent.parent / name]
    for c in cands:
        if c.exists():
            return c
    raise FileNotFoundError(name)


def _load(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _duplex_requested(opts: str) -> bool:
    force = os.environ.get("CANON_DUPLEX")
    if force in ("0", "false"):
        return False
    if force in ("1", "true"):
        return True
    kv = dict(p.split("=", 1) for p in opts.split() if "=" in p)
    sides = kv.get("sides", kv.get("Sides", "")).lower()
    if sides.startswith("two-sided"):
        return True
    dup = kv.get("Duplex", kv.get("duplex", "")).lower()
    if dup in ("duplexnotumble", "duplextumble"):
        return True
    return False


def _merge_pdf(pdf_a: pathlib.Path, pdf_b: pathlib.Path, out: pathlib.Path) -> pathlib.Path:
    p = subprocess.run(["gs", "-q", "-dNOPAUSE", "-dBATCH", "-sDEVICE=pdfwrite",
                        f"-sOutputFile={out}", str(pdf_a), str(pdf_b)],
                       capture_output=True, timeout=300)
    if p.returncode:
        raise RuntimeError(p.stderr.decode(errors="replace"))
    return out


def _blank_pdf(tmpdir: pathlib.Path) -> pathlib.Path:
    ps = tmpdir / "blank.ps"
    ps.write_text("%!PS\nshowpage\n")
    out = tmpdir / "blank.pdf"
    p = subprocess.run(["gs", "-q", "-dNOPAUSE", "-dBATCH", "-sDEVICE=pdfwrite",
                        f"-sOutputFile={out}", str(ps)], capture_output=True, timeout=120)
    if p.returncode:
        raise RuntimeError(p.stderr.decode(errors="replace"))
    return out


def _render_color(pdf, pages, tmpdir, gp_ppd, opts, height, width, gen):
    """Render the pages into a COLOUR inline-BJRaster3 stream via the stock
    Gutenprint driver (the proven colour path). Falls back to the mono encoder
    if the colour chain is unavailable/fails, so printing never breaks.

    Chain: [extract pages] -> pdftoraster (with PPD env -> RGB cspace) ->
           rastertogutenprint.5.3 -> colour BJRaster3.
    """
    try:
        subset = tmpdir / ("subset-%s.pdf" % ("_".join(str(p) for p in pages) or "none"))
        if not pages:
            gen.build_pass(pdf, [], "1", "20260101000000",
                           "00000000-0000-0000-0000-000000000000",
                           "back blank", height, width)  # ensures libs import
            ps = tmpdir / "blank.ps"; ps.write_text("%!PS\nshowpage\n")
            subprocess.run(["ps2pdf", str(ps), str(subset)], check=True,
                           capture_output=True, timeout=120)
        else:
            parts = []
            for p in pages:
                pp = tmpdir / ("pp%d.pdf" % p)
                subprocess.run(["gs", "-q", "-dNOPAUSE", "-dBATCH", "-sDEVICE=pdfwrite",
                                "-dFirstPage=%d" % p, "-dLastPage=%d" % p,
                                "-sOutputFile=%s" % pp, str(pdf)],
                               check=True, capture_output=True, timeout=300)
                parts.append(str(pp))
            subprocess.run(["gs", "-q", "-dNOPAUSE", "-dBATCH", "-sDEVICE=pdfwrite",
                            "-sOutputFile=%s" % subset, *parts],
                           check=True, capture_output=True, timeout=300)
        env = dict(os.environ, PPD=str(gp_ppd), CUPS_SERVERBIN="/usr/lib/cups")
        qopts = "media=A4 Resolution=601x600dpi"
        ras = subprocess.run(["/usr/lib/cups/filter/pdftoraster", "1", "u", "t", "1", qopts],
                             input=subset.read_bytes(), capture_output=True, timeout=1200, env=env)
        if ras.returncode != 0 or not ras.stdout:
            raise RuntimeError("pdftoraster rc=%d" % ras.returncode)
        gp = subprocess.run(["/usr/lib/cups/filter/rastertogutenprint.5.3", "1", "u", "t", "1", qopts],
                            input=ras.stdout, capture_output=True, timeout=1200, env=env)
        if gp.returncode != 0 or not gp.stdout or b"\x1b(L\x04\x00CMYK" not in gp.stdout:
            raise RuntimeError("gutenprint rc=%d" % gp.returncode)
        return gp.stdout, "color"
    except Exception as e:  # noqa: BLE001 - fall back to mono, never break printing
        print("canon-g2010-duplex: colour path failed (%s), using mono" % e, file=sys.stderr)
        out = gen.build_pass(pdf, pages, "00000001", "20260101000000",
                             "00000000-0000-0000-0000-0000000000f1",
                             "CUPS duplex (mono fallback)", height, width)
        return out, "mono"


def _register(jobid: str, user: str, title: str, backs: bytes,
              fronts_pages, backs_pages, sink_host, sink_port) -> dict:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(8)
    rec = {
        "jobid": jobid, "user": user, "title": title,
        "token": token,
        "fronts_pages": fronts_pages, "backs_pages": backs_pages,
        "sink_host": sink_host, "sink_port": sink_port,
        "created": time.time(),
        "state": "awaiting_confirm",
    }
    (STATE_DIR / f"{jobid}.prn").write_bytes(backs)
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

    height = int(os.environ.get("CANON_G2010_HEIGHT", "7016"))
    width = int(os.environ.get("CANON_G2010_WIDTH", "620"))
    sink_host = os.environ.get("CANON_G2010_SINK_HOST", "127.0.0.1")
    sink_port = int(os.environ.get("CANON_G2010_SINK_PORT", "9100"))

    gen = _load("native_bjl_strip", _find("native-bjl-strip.py"))

    # Colour path: the stock Gutenprint driver renders colour inline-BJRaster3.
    # We locate its PPD (installed copy or the queue's backup).
    gp_ppd = None
    for cand in ["/usr/local/lib/canon-g2010/canon-g2010-color.ppd",
                 "/usr/local/lib/canon-g2010/canon-g2010-series-color.ppd",
                 os.environ.get("CANON_G2010_GP_PPD", "")]:
        if cand and os.path.exists(cand):
            gp_ppd = pathlib.Path(cand); break
    if gp_ppd is None:
        import glob as _glob
        baks = sorted(_glob.glob("/var/tmp/canon-g2010-backup/*/G2010-series.ppd.bak"))
        if baks:
            gp_ppd = pathlib.Path(baks[-1])

    # Ready Canon raw (already inline-BJRaster3, e.g. the Windows/Mac driver's
    # stream) is delivered byte-for-byte and cannot be split server-side; pass it
    # through untouched (color CMYK streams included).
    def _is_canon_raw(d: bytes) -> bool:
        head = d[:1024].lstrip(b"\xef\xbb\xbf \t\r\n\x00")
        return head.startswith(b"<?xml") and b"StartJob" in d[:8192]
    if _is_canon_raw(data):
        print(f"canon-g2010-duplex: ready Canon raw passthrough ({len(data)} bytes)",
              file=sys.stderr)
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()
        return 0

    tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="canon-duplex-"))
    suffix = ".pdf" if data[:4] == b"%PDF" else ".ps"
    src = tmpdir / f"doc{suffix}"
    src.write_bytes(data)

    duplex = _duplex_requested(opts)

    try:
        pdf = gen._to_pdf(src)
        n = max(1, gen._page_count(pdf))

        if not duplex:
            if gp_ppd:
                out, mode = _render_color(pdf, list(range(1, n + 1)), tmpdir, gp_ppd,
                                          opts, height, width, gen)
            else:
                out = gen.build_pass(pdf, list(range(1, n + 1)), "00000001",
                                     "20260101000000",
                                     "00000000-0000-0000-0000-0000000000f1",
                                     "CUPS duplex filter (1-sided)", height, width)
                mode = "mono"
            sys.stdout.buffer.write(out)
            sys.stdout.buffer.flush()
            print(f"canon-g2010-duplex: 1-sided, {n} page(s), {len(out)} bytes, {mode}",
                  file=sys.stderr)
            return 0

        # two-sided: pad to even page count for a clean back pass
        padded = pdf
        total = n
        if n % 2 == 1:
            padded = _merge_pdf(pdf, _blank_pdf(tmpdir), tmpdir / "padded.pdf")
            total = n + 1
        fronts = list(range(1, total + 1, 2))
        backs = list(range(2, total + 1, 2))

        if gp_ppd:
            fronts_bytes, fmode = _render_color(padded, fronts, tmpdir, gp_ppd, opts, height, width, gen)
            backs_bytes, bmode = _render_color(padded, backs, tmpdir, gp_ppd, opts, height, width, gen)
        else:
            fronts_bytes = gen.build_pass(padded, fronts, "00000001", "20260101000000",
                                          "00000000-0000-0000-0000-0000000000f1",
                                          "CUPS duplex front", height, width)
            backs_bytes = gen.build_pass(padded, backs, "00000002", "20260101000000",
                                         "00000000-0000-0000-0000-0000000000b1",
                                         "CUPS duplex back", height, width)
            fmode = bmode = "mono"

        # pass 1: fronts now
        sys.stdout.buffer.write(fronts_bytes)
        sys.stdout.buffer.flush()

        # pass 2: register for confirmation
        rec = _register(jobid, user, title, backs_bytes,
                        fronts, backs, sink_host, sink_port)
        print(f"canon-g2010-duplex: 2-sided. fronts pages {fronts} printed "
              f"({len(fronts_bytes)} B). backs pages {backs} awaiting confirm "
              f"({len(backs_bytes)} B). token={rec['token']} state={STATE_DIR}",
              file=sys.stderr)
        return 0
    finally:
        for f in tmpdir.glob("*"):
            try:
                f.unlink()
            except OSError:
                pass
        try:
            tmpdir.rmdir()
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
