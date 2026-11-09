#!/usr/bin/env python3
"""
bjraster3.py — native Linux generator for Canon's *inline* BJRaster3 (BJL)
command stream — the format the real G2010 accepts — with NO Wine and NO PE.

Recovered grammar (evidence: evidence/MECHANISM-MAP.md, accepted pilot payload)
-----------------------------------------------------------------------------
    <?xml…StartJob…host_environment=windows?>       envelope open
    <?xml…SetJobConfiguration…datetime?>            job config
    <?xml…VendorCmd…<vcn:ijoperation>ModeShift…?>    enter raster mode
        ESC[K <4>                                    BJL mode-shift block
        ESC($ … ESC(T … ESC(b 01 … ESC(c … ESC(d …)  page setup (600 dpi)
        ESC(t … ESC(p … ESC(P … ESC(l …)  ESC(v 0 ESC(w 0 ESC(C 0
        ESC(q 1 ESC(b 1 ESC(I 1 ESC(J 8 ESC(e 007a) ESC(L 4b
        ESC(F <len16le> <packbits band>              ×bands  (8 lines each)
        ESC(e 02 00 00 01 0c)                        page eject
    <?xml…EndJob?>                                   envelope close

Line codec (recovered from the accepted pilot, semantically exact 34/34 bands):
    a band is a concatenation of lines; each line is a PackBits run stream
    terminated by a 0x80 byte:
        0x00..0x7f : literal run of (n+1) bytes
        0x81..0xff : repeat next byte (257-n) times
        0x80       : end of line (delimiter)

The envelope/command constants below are taken from the printer's own accepted
output (a working template). The codec and the assembly are original code.
"""
import struct

# ---- page-setup preamble recovered from the accepted pilot (A4, 600 dpi,
#      BJRaster3 1-bit-per-pixel). Reused byte-for-byte from the printer-accepted
#      stream so printer behaviour is anchored to known-good setup words. ----
def _esc(cmd: str, payload: bytes) -> bytes:
    return b"\x1b(" + cmd.encode() + struct.pack("<H", len(payload)) + payload

SETUP_AFTER_T = (
    _esc("b", bytes.fromhex("01"))
    + _esc("c", bytes.fromhex("300002"))
    + _esc("d", bytes.fromhex("02580258"))          # 600 x 600 dpi
    + _esc("t", bytes.fromhex("808001010002020003020003010002000000000000000000"))
    + _esc("p", bytes.fromhex("0282000001e0000700000000025800000097"
                              "00000046000012c00000190c00000000000000"
                              "00000013ec000019c8"))
    + _esc("P", bytes.fromhex("000d000001000100ff"))
    + _esc("l", bytes.fromhex("340000"))
    + _esc("v", bytes.fromhex("00"))
    + _esc("w", bytes.fromhex("00"))
    + _esc("C", bytes.fromhex("00"))
    + _esc("q", bytes.fromhex("01"))
    + _esc("b", bytes.fromhex("01"))
    + _esc("I", bytes.fromhex("01"))
    + _esc("J", bytes.fromhex("08"))
    + _esc("e", bytes.fromhex("007a"))
    + _esc("L", bytes.fromhex("4b"))
)
# first page: ESC[K mode block + ESC($ + ESC(T + the shared body
PAGE1_PRE = (b"\x1b[K\x02\x00\x00\x0f"
             + _esc("$", bytes.fromhex("010001000100010000"))
             + _esc("T", bytes.fromhex("030404"))
             + SETUP_AFTER_T)
# later pages: ESC(b)00 then the shared body (no ESC[K/$/T)
PAGE_N_PRE = _esc("b", bytes.fromhex("00")) + SETUP_AFTER_T
# Eject/advance: the accepted pilot encodes ESC(e) with a 2-byte payload 00 01
# followed by a SEPARATE raw 0x0c byte. Encoding len=3 (as an earlier revision
# did) swallows the 0x0c into the payload, so the printer never sees the feed
# byte — a real cause of the captured-but-not-advanced page.
EJECT = _esc("e", bytes.fromhex("0001")) + b"\x0c"
TRAILER = _esc("b", bytes.fromhex("00"))   # pilot emits this before EndJob

XML_HEAD = '<?xml version="1.0" encoding="utf-8" ?>'
NS = ("http://www.canon.com/ns/cmd/2008/07/common/")
NS_C = ("http://www.canon.com/ns/cmd/2008/07/canon/")


def packbits_line(line: bytes) -> bytes:
    """TIFF PackBits encode one raster line (no 0x80 terminator)."""
    out = bytearray()
    n = len(line)
    i = 0
    while i < n:
        j = i
        while j + 1 < n and line[j + 1] == line[i] and (j + 1 - i) < 128:
            j += 1
        if (j - i + 1) >= 3:
            out.append((1 - (j - i + 1)) & 0xFF)
            out.append(line[i])
            i = j + 1
            continue
        start = i
        lit = 0
        while i < n and lit < 128:
            if i + 2 < n and line[i] == line[i + 1] == line[i + 2]:
                break
            i += 1
            lit += 1
        out.append(lit - 1)
        out += line[start:i]
    return bytes(out)


def encode_band(lines) -> bytes:
    return b"".join(packbits_line(bytes(l)) + b"\x80" for l in lines)


def decode_band(data: bytes):
    """Inverse of encode_band (verification helper)."""
    lines, cur, k = [], bytearray(), 0
    while k < len(data):
        c = data[k]
        if c == 0x80:
            lines.append(bytes(cur)); cur = bytearray(); k += 1
        elif c < 0x80:
            n = c + 1; cur += data[k + 1:k + 1 + n]; k += 1 + n
        else:
            n = 257 - c; cur += bytes([data[k + 1]]) * n; k += 2
    if cur:
        lines.append(bytes(cur))
    return lines


def _xml_startjob(jobid, jobname, username, computername, jobdesc):
    return (XML_HEAD +
            f'<cmd xmlns:ivec="{NS}" xmlns:vcn="{NS_C}">' +
            '<ivec:contents><ivec:operation>StartJob</ivec:operation>'
            '<ivec:param_set servicetype="print">'
            f'<ivec:jobID>{jobid}</ivec:jobID><ivec:bidi>0</ivec:bidi>'
            f'<ivec:jobname><![CDATA[{jobname}]]></ivec:jobname>'
            f'<ivec:username><![CDATA[{username}]]></ivec:username>'
            f'<ivec:computername><![CDATA[{computername}]]></ivec:computername>'
            f'<ivec:job_description><![CDATA[{jobdesc}]]></ivec:job_description>'
            '<vcn:host_environment>windows</vcn:host_environment>'
            '</ivec:param_set></ivec:contents></cmd>')


def _xml_setjobconfig(jobid, datetime):
    return (XML_HEAD + f'<cmd xmlns:ivec="{NS}">'
            '<ivec:contents><ivec:operation>SetJobConfiguration</ivec:operation>'
            '<ivec:param_set servicetype="print">'
            f'<ivec:jobID>{jobid}</ivec:jobID><ivec:datetime>{datetime}</ivec:datetime>'
            '</ivec:param_set></ivec:contents></cmd>')


def _xml_modeshift(jobid):
    return (XML_HEAD + f'<cmd xmlns:ivec="{NS}" xmlns:vcn="{NS_C}">'
            '<ivec:contents><ivec:operation>VendorCmd</ivec:operation>'
            '<ivec:param_set servicetype="print">'
            '<vcn:ijoperation>ModeShift</vcn:ijoperation><vcn:ijmode>1</vcn:ijmode>'
            f'<ivec:jobID>{jobid}</ivec:jobID>'
            '</ivec:param_set></ivec:contents></cmd>')


def _xml_endjob(jobid):
    return (XML_HEAD + f'<cmd xmlns:ivec="{NS}">'
            '<ivec:contents><ivec:operation>EndJob</ivec:operation>'
            '<ivec:param_set servicetype="print">'
            f'<ivec:jobID>{jobid}</ivec:jobID>'
            '</ivec:param_set></ivec:contents></cmd>')


LINES_PER_BAND = 8


def build_job(pages_lines, *, jobid="00000001", jobname="Native BJL job",
              username="linux", computername="OPENCLAW",
              jobdesc="00000000-0000-0000-0000-000000000000",
              datetime="20260101000000"):
    """Assemble a full inline-BJRaster3 job.

    pages_lines: list of pages; each page is a list of raster scanline byte
    strings (1 bit/pixel), all with equal length.
    """
    out = bytearray()
    out += _xml_startjob(jobid, jobname, username, computername, jobdesc).encode()
    out += _xml_setjobconfig(jobid, datetime).encode()
    out += _xml_modeshift(jobid).encode()
    first = True
    for lines in pages_lines:
        out += PAGE1_PRE if first else PAGE_N_PRE
        first = False
        # bands of LINES_PER_BAND lines
        i = 0
        n = len(lines)
        while i < n:
            band = lines[i:i + LINES_PER_BAND]
            i += LINES_PER_BAND
            body = encode_band(band)
            out += b"\x1b(F" + struct.pack("<H", len(body)) + body
        out += EJECT
    out += TRAILER
    out += _xml_endjob(jobid).encode()
    return bytes(out)
