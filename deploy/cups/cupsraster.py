#!/usr/bin/env python3
"""cupsraster.py — minimal, dependency-free CUPS/PWG raster decoder.

Decodes the subset that CUPS delivers to a printer filter and converts each page
to the 1-bit line format the native BJRaster3 encoder consumes.

Supported
---------
* sync words: 3SaR (v3), 2SaR (v2/PWG), tSaR (v1), UNIR (Apple, treated like v2)
* page header: 1796 bytes (cups_page_header2_t), after the 4-byte sync word
* color: CUPS_CSPACE_W/SW (8bpp gray), CUPS_CSPACE_K (8bpp), CUPS_CSPACE_SRGB/RGB
  (24bpp, chunked or banded), CUPS_CSPACE_CMYK/KCMY (32bpp, luminance = average)
* compression: 0 (none). Non-zero compression raises (caller can fall back).

Only the fields we need are read; offsets come from cups/raster.h
(cups_page_header2_t is exactly 1796 bytes, verified by offsetof).
"""
from __future__ import annotations

import struct

SYNC = {
    0x52615333: 3,   # RaS3
    0x52615332: 2,   # RaS2 (PWG)
    0x52615374: 1,   # RaSt
    0x554E4952: 2,   # UNIR (Apple)
}
SYNC_BE = {  # big-endian spellings of the same words
    0x33536152: 3,   # 3SaR
    0x32536152: 2,   # 2SaR
    0x74536152: 1,   # tSaR
}

HEADER = 1796
F_WIDTH, F_HEIGHT, F_BPC, F_BPP, F_BPL = 372, 376, 384, 388, 392
F_ORDER, F_CSPACE, F_COMP, F_NCOLORS = 396, 400, 404, 420
F_HWRES = 276
CSPACE_W, CSPACE_K, CSPACE_RGB, CSPACE_SRGB = 0, 3, 1, 19
CSPACE_CMYK, CSPACE_KCMY = 6, 8


def _u32(h, off):
    return struct.unpack_from("<I", h, off)[0]


def _pixel_is_ink(cspace, bpp, order, p):
    """Return True if pixel bytes `p` should be printed (dark)."""
    if cspace in (CSPACE_W, CSPACE_K):
        return p[0] < 128
    if cspace in (CSPACE_SRGB, CSPACE_RGB):
        if bpp >= 24 and order == 0:            # chunked RGB
            r, g, b = p[0], p[1], p[2]
        elif order == 2:                        # planar/banded RGB
            r, g, b = p[0], p[1], p[2]
        else:
            r, g, b = p[0], p[1], p[2]
        return (r + g + b) / 3.0 < 128
    if cspace in (CSPACE_CMYK, CSPACE_KCMY):
        v = (p[0] + p[1] + p[2]) / 3.0
        return v < 128
    # unknown: treat first byte
    return p[0] < 128


def iter_pages(data: bytes):
    """Yield dicts with keys: width,height,res_x,res_y,lines (list[bytes] of 1bpp
    rows at the page's native pixel grid, MSB-first, padded to whole bytes)."""
    i = 0
    n = len(data)
    while i + 4 <= n:
        sw = struct.unpack_from("<I", data, i)[0]
        ver = SYNC.get(sw) or SYNC_BE.get(struct.unpack_from(">I", data, i)[0])
        if ver is None:
            break
        hdr = data[i + 4:i + 4 + HEADER]
        if len(hdr) < HEADER:
            break
        w = _u32(hdr, F_WIDTH)
        h = _u32(hdr, F_HEIGHT)
        bpp = _u32(hdr, F_BPP)
        bpl = _u32(hdr, F_BPL)
        order = _u32(hdr, F_ORDER)
        cspace = _u32(hdr, F_CSPACE)
        comp = _u32(hdr, F_COMP)
        res_x = _u32(hdr, F_HWRES)
        res_y = _u32(hdr, F_HWRES + 4)
        if w == 0 or h == 0 or bpl == 0:
            break
        if comp != 0:
            raise ValueError(f"unsupported CUPS raster compression {comp}")
        start = i + 4 + HEADER
        pix = data[start:start + bpl * h]
        if len(pix) < bpl * h:
            break
        lines = _rows_to_1bpp(pix, w, h, bpl, bpp, order, cspace)
        yield {"width": w, "height": h, "res_x": res_x or 600, "res_y": res_y or 600,
               "lines": lines}
        i = start + bpl * h


def _rows_to_1bpp(pix, w, h, bpl, bpp, order, cspace):
    bpp_bytes = max(1, bpp // 8)
    stride = (w + 7) // 8
    rows = []
    for r in range(h):
        base = r * bpl
        out = bytearray(stride)
        for x in range(w):
            off = base + x * bpp_bytes
            if off + bpp_bytes > len(pix):
                break
            if _pixel_is_ink(cspace, bpp, order, pix[off:off + bpp_bytes]):
                out[x >> 3] |= 0x80 >> (x & 7)
        rows.append(bytes(out))
    return rows


def page_to_lines(page, dst_w_dots, dst_h_lines, target_dpi=600):
    """Resample a decoded page to the printer grid: dst_w_dots wide, dst_h_lines
    tall, at target_dpi, returned as 1bpp byte lines padded to dst_w_dots/8."""
    src = page["lines"]
    sh = len(src)
    sw_dots = page["width"]
    out_stride = (dst_w_dots + 7) // 8
    out = []
    for y in range(dst_h_lines):
        sy = int(y * sh / dst_h_lines) if dst_h_lines else 0
        sy = min(sy, sh - 1) if sh else 0
        srow = src[sy] if sh else b""
        o = bytearray(out_stride)
        for x in range(dst_w_dots):
            sx = int(x * sw_dots / dst_w_dots) if dst_w_dots else 0
            if sx < sw_dots and (srow[sx >> 3] >> (7 - (sx & 7))) & 1:
                o[x >> 3] |= 0x80 >> (x & 7)
        out.append(bytes(o))
    return out
