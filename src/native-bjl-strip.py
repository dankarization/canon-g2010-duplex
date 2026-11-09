#!/usr/bin/env python3
"""
native-bjl-strip.py — emit Canon inline-BJRaster3 in the *exact geometry of the
printer-accepted pilot*: 1584-dot (198-byte) raster lines, pilot-identical
preamble and eject, plus the pilot's trailing ESC(b)00 before EndJob.

Why: live test #2 proved the carriage is accepted (paper captured) but the page
did not finish. The accepted pilot used 1584-dot lines; our earlier stream used
full-A4 4958-dot (620-byte) lines. This generator reproduces the pilot geometry.

Scaffold bytes below are taken byte-for-byte from the accepted pilot
(sha256 3db77aee…); only the raster bands carry our document.
"""
import argparse
import pathlib
import struct
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent
# bjraster3.py lives next to this file in the installed flat layout
# (/usr/local/lib/canon-g2010/) and under src/ in the source tree.
_here = pathlib.Path(__file__).resolve().parent
_BJR = _here / "bjraster3.py"
if not _BJR.exists():
    _BJR = ROOT / "src" / "bjraster3.py"

# --- pilot scaffold (recovered, byte-exact) ---
P1_PRE = bytes.fromhex(
    "1b5b4b0200000f"
    "1b28240900" "010001000100010000"
    "1b28540300" "030404"
    "1b28620100" "01"
    "1b28630300" "300002"
    "1b28640400" "02580258"
    "1b28741800" "808001010002020003020003010002000000000000000000"
    "1b28702e00" "0282000001e000070000000002580000009700000046000012c00000190c0000000000000000000013ec000019c8"
    "1b28500900" "000d000001000100ff"
    "1b286c0300" "340000"
    "1b28760100" "00"
    "1b28770100" "00"
    "1b28430100" "00"
    "1b28710100" "01"
    "1b28620100" "01"
    "1b28490100" "01"
    "1b284a0100" "08"
    "1b28650200" "007a"
    "1b284c0100" "4b"
)
P2_PRE = bytes.fromhex(
    "1b28620100" "00"
    "1b28620100" "01"
    "1b28630300" "300002"
    "1b28640400" "02580258"
    "1b28741800" "808001010002020003020003010002000000000000000000"
    "1b28702e00" "0282000001e000070000000002580000009700000046000012c00000190c0000000000000000000013ec000019c8"
    "1b28500900" "000d000001000100ff"
    "1b286c0300" "340000"
    "1b28760100" "00"
    "1b28770100" "00"
    "1b28430100" "00"
    "1b28710100" "01"
    "1b28620100" "01"
    "1b28490100" "01"
    "1b284a0100" "08"
    "1b28650200" "007a"
    "1b284c0100" "4b"
)
EJECT = bytes.fromhex("1b2865020000010c")
TRAILER = bytes.fromhex("1b28620100" "00")   # pilot ESC(b)00 before EndJob

WIDTH_BYTES = 198          # 1584 dots — exactly the pilot's line width
# Height is selectable: 2240 = A4 aspect at this width (default, full page);
# 136 = the accepted pilot's literal strip height. Override with --height.
HEIGHT_LINES = 2240

import importlib.util as _ilu
_s = _ilu.spec_from_file_location("bjraster3", str(_BJR))
B = _ilu.module_from_spec(_s); _s.loader.exec_module(B)

XML_HEAD = '<?xml version="1.0" encoding="utf-8" ?>'
NS = "http://www.canon.com/ns/cmd/2008/07/common/"
NSC = "http://www.canon.com/ns/cmd/2008/07/canon/"


def _startjob(jobid, name, user, host, desc):
    return (XML_HEAD + f'<cmd xmlns:ivec="{NS}" xmlns:vcn="{NSC}">'
            '<ivec:contents><ivec:operation>StartJob</ivec:operation>'
            '<ivec:param_set servicetype="print">'
            f'<ivec:jobID>{jobid}</ivec:jobID><ivec:bidi>0</ivec:bidi>'
            f'<ivec:jobname><![CDATA[{name}]]></ivec:jobname>'
            f'<ivec:username><![CDATA[{user}]]></ivec:username>'
            f'<ivec:computername><![CDATA[{host}]]></ivec:computername>'
            f'<ivec:job_description><![CDATA[{desc}]]></ivec:job_description>'
            '<vcn:host_environment>windows</vcn:host_environment>'
            '</ivec:param_set></ivec:contents></cmd>')


def _setjobcfg(jobid, dt):
    return (XML_HEAD + f'<cmd xmlns:ivec="{NS}">'
            '<ivec:contents><ivec:operation>SetJobConfiguration</ivec:operation>'
            '<ivec:param_set servicetype="print">'
            f'<ivec:jobID>{jobid}</ivec:jobID><ivec:datetime>{dt}</ivec:datetime>'
            '</ivec:param_set></ivec:contents></cmd>')


def _modeshift(jobid):
    return (XML_HEAD + f'<cmd xmlns:ivec="{NS}" xmlns:vcn="{NSC}">'
            '<ivec:contents><ivec:operation>VendorCmd</ivec:operation>'
            '<ivec:param_set servicetype="print">'
            '<vcn:ijoperation>ModeShift</vcn:ijoperation><vcn:ijmode>1</vcn:ijmode>'
            f'<ivec:jobID>{jobid}</ivec:jobID>'
            '</ivec:param_set></ivec:contents></cmd>')


def _endjob(jobid):
    return (XML_HEAD + f'<cmd xmlns:ivec="{NS}">'
            '<ivec:contents><ivec:operation>EndJob</ivec:operation>'
            '<ivec:param_set servicetype="print">'
            f'<ivec:jobID>{jobid}</ivec:jobID>'
            '</ivec:param_set></ivec:contents></cmd>')


def _to_pdf(src):
    src = pathlib.Path(src)
    if src.suffix.lower() == ".pdf":
        return src
    pdf = src.with_suffix(".strip.pdf")
    p = subprocess.run(["gs", "-q", "-dNOPAUSE", "-dBATCH", "-sDEVICE=pdfwrite",
                        f"-sOutputFile={pdf}", str(src)], capture_output=True, timeout=300)
    if p.returncode:
        raise RuntimeError(p.stderr.decode(errors="replace"))
    return pdf


def _page_count(pdf):
    p = subprocess.run(["gs", "-q", "-dNODISPLAY", "-dNOSAFER", "-o", "/dev/null",
                        "-c", f"({pdf}) (r) file runpdfbegin pdfpagecount = quit"],
                       capture_output=True, timeout=120)
    o = p.stdout.strip()
    return int(o) if o.isdigit() else 0


def _render_strip(pdf, page, height, width_bytes=620, dpi=600):
    """Render one page at `dpi` (600) with the printer-correct framing.

    Vertical anchor = TOP-OF-INK (the pilot's proven anchor): the printer adds its
    own top margin from where the raster begins, so the raster must start at the
    first ink row — otherwise the PDF's page top margin is added too (double
    indent, content pushed down and clipped). Height spans from that first ink row
    down to the LAST ink row (so all content is captured, with the printer's
    bottom margin below it). `height` is an upper bound: if the content is taller,
    it is extended to the last ink row; if shorter, the raster ends at the content
    (no large leading/trailing blank block).

    Horizontal anchor = page left origin (byte 0) — proven correct (pilot ink at
    ~587 dots = the doc's left margin). Width stays `width_bytes` (620 = full A4).
    """
    sel = ["-dFirstPage=" + str(page), "-dLastPage=" + str(page)]
    cmd = ["gs", "-q", "-sDEVICE=pbmraw", f"-r{dpi}", *sel, "-o", "-", str(pdf)]
    p = subprocess.run(cmd, capture_output=True, timeout=600)
    if p.returncode:
        raise RuntimeError(p.stderr.decode(errors="replace"))
    d = p.stdout
    assert d[:2] == b"P4", d[:4]
    i = 2
    toks = []
    while len(toks) < 2:
        while d[i:i+1].isspace():
            i += 1
        if d[i:i+1] == b"#":
            while d[i:i+1] not in (b"\n", b""):
                i += 1
            continue
        j = i
        while not d[j:j+1].isspace():
            j += 1
        toks.append(int(d[i:j])); i = j
    i += 1
    w, h = toks
    stride = (w + 7) // 8
    rows = [d[i + r * stride:i + (r + 1) * stride] for r in range(h)]
    # Vertical: anchor at TOP-OF-INK (the pilot's proven anchor) and run to the
    # LAST ink row, so all content is captured with no leading blank block and no
    # doubled top margin. `height` is only a safety cap for pathological content.
    ink_rows = [r for r in range(h) if any(rows[r])]
    if ink_rows:
        top = ink_rows[0]
        end = ink_rows[-1] + 1
    else:
        top = end = 0
    if end - top > height:            # cap only if content exceeds `height`
        end = top + height
    band = rows[top:end]
    out = [r[0:width_bytes].ljust(width_bytes, b"\x00") for r in band]
    if not out:
        out = [b"\x00" * width_bytes]
    return out


LINES_PER_BAND = 8


def build_pass(pdf, pages, jobid, dt, desc, name, height, width_bytes=198):
    out = bytearray()
    out += _startjob(jobid, name, "dankar", "OPENCLAW", desc).encode()
    out += _setjobcfg(jobid, dt).encode()
    out += _modeshift(jobid).encode()
    for k, pg in enumerate(pages):
        out += P1_PRE if k == 0 else P2_PRE
        lines = _render_strip(pdf, pg, height, width_bytes)
        for off in range(0, len(lines), LINES_PER_BAND):
            band = lines[off:off + LINES_PER_BAND]
            body = B.encode_band(band)
            out += b"\x1b(F" + struct.pack("<H", len(body)) + body
        out += EJECT
    out += TRAILER
    out += _endjob(jobid).encode()
    return bytes(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source", type=pathlib.Path)
    ap.add_argument("outdir", type=pathlib.Path)
    ap.add_argument("--datetime", default="20260101000000")
    ap.add_argument("--height", type=int, default=7016,
                    help="raster height in lines (7016 = full A4 at 600 dpi)")
    ap.add_argument("--width", type=int, default=620,
                    help="line width in bytes (620 = 4960 dots = full A4 width at 600 dpi)")
    a = ap.parse_args()
    a.outdir.mkdir(parents=True, exist_ok=True)
    pdf = _to_pdf(a.source)
    n = _page_count(pdf)
    if n < 1:
        raise SystemExit("no pages")
    fronts = list(range(1, n + 1, 2))
    backs = list(range(2, n + 1, 2))
    fp = build_pass(pdf, fronts, "00000001", a.datetime,
                    "00000000-0000-0000-0000-0000000000f1", "Native fronts", a.height, a.width)
    (a.outdir / "fronts.prn").write_bytes(fp)
    print(f"fronts.prn {len(fp)} bytes, {len(fronts)} page(s), height={a.height}, width={a.width}B")
    if backs:
        bp = build_pass(pdf, backs, "00000001", a.datetime,
                        "00000000-0000-0000-0000-0000000000b1", "Native backs", a.height, a.width)
        (a.outdir / "backs.prn").write_bytes(bp)
        print(f"backs.prn {len(bp)} bytes, {len(backs)} page(s), height={a.height}, width={a.width}B")


if __name__ == "__main__":
    main()
