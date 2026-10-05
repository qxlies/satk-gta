"""DXT/raster decoding to RGBA8 (SPEC §4.2 ``satk.formats.dxt``). Milestone F3 (spike S2). Stdlib core.

Three backends for the block-compressed formats (DXT1/DXT3/DXT5):

* ``"pillow"`` -- ``Image.frombuffer("RGBA", (w, h), data, "bcn", n)`` (Pillow's C decoder, BcnDecode.c);
* ``"numpy"`` -- vectorised decoder (whole texture at once);
* ``"python"`` -- the stdlib reference (per block, ~3 Mpix/s);
* ``"auto"`` -- the first one available in that order.

Uncompressed and paletted formats (``A8R8G8B8 X8R8G8B8 R8G8B8 R5G6B5 X1R5G5B5 A1R5G5B5 A4R4G4B4 L8 A8L8 A8
PAL8 PAL4``) always use the shared stdlib path: byte slicing and ``bytes.translate`` run at C speed, so the
backend does not matter for them. Pillow/numpy are imported only inside functions (SPEC §2.3).

Semantics (pitfalls #6, #7): DXT1 with ``TexInfo.alpha`` false (raster 565) is opaque -- index 3 of a
``c0 <= c1`` block is black with A=255; DXT1 with alpha (raster 1555) keeps the 1-bit transparency;
DXT3/DXT5 always carry alpha; PAL8/PAL4 palettes are RGBA as stored by RW; formats without alpha get A=255.
Only mip level 0 is decoded (79 % of the textures have no other level).

Example::

    t = parse_txd(blob).textures[0]
    rgba = decode_rgba(t, mip0_bytes(blob, t), palette_bytes(blob, t))   # len == t.w * t.h * 4
    pw, ph, small = preview_rgba(t, mip0_bytes(blob, t))                  # 1/4 size from block endpoints
"""

from __future__ import annotations

import struct
from functools import lru_cache
from itertools import repeat
from operator import add, rshift

from .rw import FormatError
from .txd import TexInfo

__all__ = ["BACKENDS", "DXT_FORMATS", "RAW_FORMATS", "decode_rgba", "preview_rgba", "mean_rgba",
           "available_backends", "resolve_backend"]

BACKENDS = ("auto", "pillow", "numpy", "python")
DXT_FORMATS = ("DXT1", "DXT3", "DXT5")
RAW_FORMATS = ("A8R8G8B8", "X8R8G8B8", "R8G8B8", "R5G6B5", "X1R5G5B5", "A1R5G5B5", "A4R4G4B4", "L8", "A8L8",
               "A8", "PAL8", "PAL4")
_BPP = {"A8R8G8B8": 4, "X8R8G8B8": 4, "R8G8B8": 3, "R5G6B5": 2, "X1R5G5B5": 2, "A1R5G5B5": 2, "A4R4G4B4": 2,
        "L8": 1, "A8L8": 2, "A8": 1}


# ----------------------------------------------------------------------------- backends
@lru_cache(maxsize=None)
def _have(module: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(module) is not None


def available_backends() -> list[str]:
    """Backends usable in this interpreter (``python`` always)."""
    return [b for b, m in (("pillow", "PIL"), ("numpy", "numpy")) if _have(m)] + ["python"]


def resolve_backend(backend: str = "auto") -> str:
    """``auto`` -> first available of pillow/numpy/python; an unavailable explicit backend -> ``DEPENDENCY``."""
    if backend not in BACKENDS:
        raise ValueError(f"unknown DXT backend {backend!r}; expected one of {BACKENDS}")
    if backend == "auto":
        return available_backends()[0]
    if backend != "python" and backend not in available_backends():
        from ..core.errors import SatkError, bootstrap_hint

        pkg = "Pillow" if backend == "pillow" else "numpy"
        raise SatkError("DEPENDENCY", f"python package '{pkg}' is not installed (needed for the {backend} DXT backend)",
                        hint=bootstrap_hint() + " (or use backend='python')", data={"backend": backend})
    return backend


# ----------------------------------------------------------------------------- helpers
def _exp5(v: int) -> int:
    return (v << 3) | (v >> 2)


def _exp6(v: int) -> int:
    return (v << 2) | (v >> 4)


def _c565(c: int) -> tuple[int, int, int]:
    return _exp5(c >> 11), _exp6((c >> 5) & 63), _exp5(c & 31)


def _blocks(w: int, h: int) -> tuple[int, int]:
    return max(1, (w + 3) // 4), max(1, (h + 3) // 4)


def _check(t: TexInfo, data, need: int, what: str = "mip0") -> None:
    if t.unsupported:
        raise FormatError("dxt", 0, f"texture {t.name!r}: platform 0x{t.platform:X} is not supported")
    if t.w <= 0 or t.h <= 0:
        raise FormatError("dxt", 0, f"texture {t.name!r}: bad size {t.w}x{t.h}")
    if len(data) < need:
        raise FormatError("dxt", len(data), f"texture {t.name!r}: {what} has {len(data)} bytes, {t.d3dfmt} "
                                            f"{t.w}x{t.h} needs {need}")


# ----------------------------------------------------------------------------- python reference (DXT)
def _py_dxt(data: bytes, w: int, h: int, kind: int) -> bytearray:
    out = bytearray(w * h * 4)
    bw, bh = _blocks(w, h)
    bs = 8 if kind == 1 else 16
    unpack = struct.unpack_from
    off = 0
    for by in range(bh):
        y0 = by * 4
        for bx in range(bw):
            x0 = bx * 4
            co = off if kind == 1 else off + 8
            c0, c1, bits = unpack("<HHI", data, co)
            r0, g0, b0 = _c565(c0)
            r1, g1, b1 = _c565(c1)
            if c0 > c1 or kind != 1:
                pal = (bytes((r0, g0, b0, 255)), bytes((r1, g1, b1, 255)),
                       bytes(((2 * r0 + r1) // 3, (2 * g0 + g1) // 3, (2 * b0 + b1) // 3, 255)),
                       bytes(((r0 + 2 * r1) // 3, (g0 + 2 * g1) // 3, (b0 + 2 * b1) // 3, 255)))
            else:
                pal = (bytes((r0, g0, b0, 255)), bytes((r1, g1, b1, 255)),
                       bytes(((r0 + r1) // 2, (g0 + g1) // 2, (b0 + b1) // 2, 255)), b"\0\0\0\0")
            if kind == 3:
                (abits,) = unpack("<Q", data, off)
                alpha = [((abits >> (4 * i)) & 15) * 17 for i in range(16)]
            elif kind == 5:
                a0, a1 = data[off], data[off + 1]
                abits = int.from_bytes(data[off + 2:off + 8], "little")
                if a0 > a1:
                    ap = [a0, a1] + [((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)]
                else:
                    ap = [a0, a1] + [((5 - i) * a0 + i * a1) // 5 for i in range(1, 5)] + [0, 255]
                alpha = [ap[(abits >> (3 * i)) & 7] for i in range(16)]
            else:
                alpha = None
            cols = min(4, w - x0)
            for py in range(min(4, h - y0)):
                sh = 8 * py
                row = b"".join(pal[(bits >> (sh + 2 * px)) & 3] for px in range(cols))
                o = ((y0 + py) * w + x0) * 4
                out[o:o + 4 * cols] = row
                if alpha is not None:
                    out[o + 3:o + 4 * cols:4] = bytes(alpha[4 * py:4 * py + cols])
            off += bs
    return out


# ----------------------------------------------------------------------------- numpy (DXT)
def _np_dxt(data: bytes, w: int, h: int, kind: int) -> bytes:
    import numpy as np

    bw, bh = _blocks(w, h)
    bs = 8 if kind == 1 else 16
    nb = bw * bh
    blk = np.frombuffer(data, dtype=np.uint8, count=nb * bs).reshape(nb, bs)
    col = blk[:, bs - 8:]
    c = col[:, 0:4].copy().view("<u2")
    c0 = c[:, 0].astype(np.int32)
    c1 = c[:, 1].astype(np.int32)

    def exp565(v):
        out = np.empty((v.shape[0], 3), dtype=np.int32)
        r, g, b = (v >> 11) & 31, (v >> 5) & 63, v & 31
        out[:, 0] = (r << 3) | (r >> 2)
        out[:, 1] = (g << 2) | (g >> 4)
        out[:, 2] = (b << 3) | (b >> 2)
        return out

    p0, p1 = exp565(c0), exp565(c1)
    pal = np.empty((nb, 4, 4), dtype=np.uint8)
    pal[:, 0, :3] = p0
    pal[:, 1, :3] = p1
    pal[:, :, 3] = 255
    if kind == 1:
        four = c0 > c1
        f = four[:, None]
        pal[:, 2, :3] = np.where(f, (2 * p0 + p1) // 3, (p0 + p1) // 2)
        pal[:, 3, :3] = np.where(f, (p0 + 2 * p1) // 3, 0)
        pal[:, 3, 3] = np.where(four, 255, 0)
    else:
        pal[:, 2, :3] = (2 * p0 + p1) // 3
        pal[:, 3, :3] = (p0 + 2 * p1) // 3
    bits = col[:, 4:8].copy().view("<u4")[:, 0]
    idx = ((bits[:, None] >> (2 * np.arange(16, dtype=np.uint32))) & 3).astype(np.intp)
    idx += (np.arange(nb, dtype=np.intp) * 4)[:, None]
    px = pal.reshape(nb * 4, 4)[idx]                  # (nb, 16, 4) uint8
    if kind == 3:
        a = blk[:, :8]
        nib = np.empty((nb, 8, 2), dtype=np.uint8)
        nib[:, :, 0] = a & 15
        nib[:, :, 1] = a >> 4
        px[:, :, 3] = nib.reshape(nb, 16) * 17
    elif kind == 5:
        a0 = blk[:, 0].astype(np.int32)
        a1 = blk[:, 1].astype(np.int32)
        abits = np.zeros(nb, dtype=np.uint64)
        for k in range(6):
            abits |= blk[:, 2 + k].astype(np.uint64) << np.uint64(8 * k)
        i7 = np.arange(1, 7, dtype=np.int32)
        i5 = np.arange(1, 5, dtype=np.int32)
        big = (a0 > a1)[:, None]
        ap = np.empty((nb, 8), dtype=np.uint8)
        ap[:, 0], ap[:, 1] = a0, a1
        seven = ((7 - i7) * a0[:, None] + i7 * a1[:, None]) // 7
        five = ((5 - i5) * a0[:, None] + i5 * a1[:, None]) // 5
        ap[:, 2:6] = np.where(big, seven[:, :4], five)
        ap[:, 6] = np.where(big[:, 0], seven[:, 4], 0)
        ap[:, 7] = np.where(big[:, 0], seven[:, 5], 255)
        aidx = ((abits[:, None] >> (np.uint64(3) * np.arange(16, dtype=np.uint64))) & np.uint64(7)).astype(np.intp)
        aidx += (np.arange(nb, dtype=np.intp) * 8)[:, None]
        px[:, :, 3] = ap.reshape(-1)[aidx]
    img = px.reshape(bh, bw, 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(bh * 4, bw * 4, 4)
    if bh * 4 != h or bw * 4 != w:
        img = img[:h, :w]
    return img.tobytes()


# ----------------------------------------------------------------------------- pillow (DXT)
def _pil_dxt(data: bytes, w: int, h: int, kind: int) -> bytes:
    from PIL import Image

    n = {1: 1, 3: 2, 5: 3}[kind]
    return Image.frombuffer("RGBA", (w, h), data, "bcn", n).tobytes()


_DXT_IMPL = {"python": _py_dxt, "numpy": _np_dxt, "pillow": _pil_dxt}


# ----------------------------------------------------------------------------- raw / paletted (stdlib, all backends)
@lru_cache(maxsize=None)
def _table16(fmt: str) -> tuple[bytes, ...]:
    """65 536-entry lookup u16 -> 4 RGBA bytes for the 16-bit formats (built once, ~50 ms)."""
    out = []
    for v in range(65536):
        if fmt == "R5G6B5":
            px = (*_c565(v), 255)
        elif fmt in ("X1R5G5B5", "A1R5G5B5"):
            px = (_exp5((v >> 10) & 31), _exp5((v >> 5) & 31), _exp5(v & 31),
                  (255 if v & 0x8000 else 0) if fmt == "A1R5G5B5" else 255)
        else:  # A4R4G4B4
            px = (((v >> 8) & 15) * 17, ((v >> 4) & 15) * 17, (v & 15) * 17, (v >> 12) * 17)
        out.append(bytes(px))
    return tuple(out)


def _interleave(r, g, b, a, n: int) -> bytearray:
    out = bytearray(4 * n)
    out[0::4], out[1::4], out[2::4], out[3::4] = r, g, b, a
    return out


def _palette_lookup(idx: bytes, pal: bytes, alpha: bool, n: int) -> bytearray:
    pal = bytes(pal) + b"\0" * (1024 - len(pal))
    a = idx.translate(pal[3::4]) if alpha else b"\xff" * n
    return _interleave(idx.translate(pal[0::4]), idx.translate(pal[1::4]), idx.translate(pal[2::4]), a, n)


_LO_NIB = bytes(b & 15 for b in range(256))
_HI_NIB = bytes(b >> 4 for b in range(256))


def _decode_raw(t: TexInfo, data, palette) -> bytearray:
    w, h, fmt = t.w, t.h, t.d3dfmt
    n = w * h
    if fmt in ("PAL8", "PAL4"):
        if palette is None:
            raise FormatError("dxt", 0, f"texture {t.name!r}: {fmt} needs a palette (txd.palette_bytes)")
        if fmt == "PAL8":
            _check(t, data, n)
            idx = bytes(data[:n])
        else:
            _check(t, data, (n + 1) // 2)
            raw = bytes(data[:(n + 1) // 2])
            pairs = bytearray(2 * len(raw))
            pairs[0::2] = raw.translate(_LO_NIB)
            pairs[1::2] = raw.translate(_HI_NIB)
            idx = bytes(pairs[:n])
        return _palette_lookup(idx, palette, t.alpha, n)
    bpp = _BPP.get(fmt)
    if bpp is None:
        raise FormatError("dxt", 0, f"texture {t.name!r}: format {fmt} is not supported")
    _check(t, data, n * bpp)
    d = bytes(data[:n * bpp])
    ff = b"\xff" * n
    if fmt in ("A8R8G8B8", "X8R8G8B8"):
        return _interleave(d[2::4], d[1::4], d[0::4], d[3::4] if fmt == "A8R8G8B8" else ff, n)
    if fmt == "R8G8B8":
        return _interleave(d[2::3], d[1::3], d[0::3], ff, n)
    if fmt == "L8":
        return _interleave(d, d, d, ff, n)
    if fmt == "A8L8":
        lum = d[0::2]
        return _interleave(lum, lum, lum, d[1::2], n)
    if fmt == "A8":
        z = bytes(n)
        return _interleave(z, z, z, d, n)
    tbl = _table16(fmt)
    vals = struct.unpack(f"<{n}H", d)
    return bytearray(b"".join(map(tbl.__getitem__, vals)))


# ----------------------------------------------------------------------------- public API
def decode_rgba(t: TexInfo, mip0: bytes, palette: bytes | None = None, backend: str = "auto") -> bytes:
    """Decode mip0 of ``t`` to RGBA8 (``w*h*4`` bytes, rows top to bottom).

    Args:
        t: texture header from ``parse_txd``.
        mip0: raw level-0 data (``txd.mip0_bytes``).
        palette: raw palette for PAL4/PAL8 (``txd.palette_bytes``).
        backend: ``auto|pillow|numpy|python`` (matters for DXT only, see the module docstring).

    Raises ``FormatError(kind="dxt")`` for unsupported formats/platforms or short data.
    """
    fmt = t.d3dfmt
    if fmt in DXT_FORMATS:
        kind = int(fmt[3])
        bw, bh = _blocks(t.w, t.h)
        need = bw * bh * (8 if kind == 1 else 16)
        _check(t, mip0, need)
        data = bytes(mip0[:need])
        out = _DXT_IMPL[resolve_backend(backend)](data, t.w, t.h, kind)
        if kind == 1 and not t.alpha:            # DXT1 + 565: opaque, index 3 = black (pitfall #6)
            out = bytearray(out)
            out[3::4] = b"\xff" * (t.w * t.h)
        return bytes(out)
    if backend not in BACKENDS:
        raise ValueError(f"unknown DXT backend {backend!r}; expected one of {BACKENDS}")
    return bytes(_decode_raw(t, mip0, palette))


# --- endpoint tables (u16 colour bytes -> expanded channel parts)
_TR = bytes(_exp5(x >> 3) for x in range(256))                       # high byte -> R
_TG_HI = bytes(32 * (x & 7) + ((x & 7) >> 1) for x in range(256))    # high byte -> G part
_TG_LO = bytes(4 * (x >> 5) for x in range(256))                     # low byte  -> G part
_TB = bytes(_exp5(x & 31) for x in range(256))                       # low byte  -> B
_NIBSUM = bytes((x & 15) + (x >> 4) for x in range(256))
_A16 = bytes(min(255, s * 17 // 16) for s in range(256))
_GREY_PAL = bytes(v for i in range(256) for v in (i, i, i, 255))


def _avg2(a: bytes, b: bytes) -> bytes:
    return bytes(map(rshift, map(add, a, b), repeat(1)))


def _sum_bytes(*parts: bytes) -> bytes:
    acc = parts[0]
    for p in parts[1:]:
        acc = bytes(map(add, acc, p))
    return acc


def _dxt_endpoints(t: TexInfo, mip0) -> tuple[int, bytes, int]:
    kind = int(t.d3dfmt[3])
    bw, bh = _blocks(t.w, t.h)
    bs = 8 if kind == 1 else 16
    need = bw * bh * bs
    _check(t, mip0, need)
    return kind, bytes(mip0[:need]), bs


def preview_rgba(t: TexInfo, mip0: bytes) -> tuple[int, int, bytes]:
    """Cheap ``(w/4, h/4, rgba)`` preview (one pixel per 4x4 block).

    DXT: the average of the two endpoint colours of each block (alpha: DXT3 mean of the 16 values, DXT5
    mean of the two endpoints, DXT1 255) -- about 16x cheaper than a decode, pure stdlib. Other formats:
    the top-left pixel of every 4x4 cell of the full decode (palettes are not needed for PAL formats: pass
    them through :func:`decode_rgba` when exact colours matter; here PAL textures come out grey).
    """
    if t.d3dfmt in DXT_FORMATS:
        kind, d, bs = _dxt_endpoints(t, mip0)
        o = 0 if kind == 1 else 8
        lb0, hb0, lb1, hb1 = d[o::bs], d[o + 1::bs], d[o + 2::bs], d[o + 3::bs]
        r = _avg2(hb0.translate(_TR), hb1.translate(_TR))
        g = bytes(map(rshift, map(add, map(add, hb0.translate(_TG_HI), lb0.translate(_TG_LO)),
                                  map(add, hb1.translate(_TG_HI), lb1.translate(_TG_LO))), repeat(1)))
        b = _avg2(lb0.translate(_TB), lb1.translate(_TB))
        nb = len(r)
        if kind == 3:
            a = _sum_bytes(*(d[k::bs].translate(_NIBSUM) for k in range(8))).translate(_A16)
        elif kind == 5:
            a = _avg2(d[0::bs], d[1::bs])
        else:
            a = b"\xff" * nb
        bw, bh = _blocks(t.w, t.h)
        return bw, bh, bytes(_interleave(r, g, b, a, nb))
    # PAL: a grey ramp palette (RGBA) so indices are visible without the real palette
    full = _decode_raw(t, mip0, _GREY_PAL if t.d3dfmt in ("PAL8", "PAL4") else None)
    w, h = t.w, t.h
    pw, ph = _blocks(w, h)
    rows = []
    for y in range(0, h, 4):
        row = full[y * w * 4:(y + 1) * w * 4]
        rows.append(bytes(_interleave(row[0::16], row[1::16], row[2::16], row[3::16], pw)))
    return pw, ph, b"".join(rows)


def mean_rgba(t: TexInfo, mip0: bytes, palette: bytes | None = None) -> int:
    """Average colour as ``0xRRGGBBAA`` (index ``image.mean_rgba``).

    DXT: mean of the block endpoint colours (C-speed via ``bytes.translate``; no decode). Other formats:
    mean of the decoded pixels (``palette`` needed for an exact PAL mean; without it PAL comes out grey).
    """
    if t.d3dfmt in DXT_FORMATS:
        kind, d, bs = _dxt_endpoints(t, mip0)
        o = 0 if kind == 1 else 8
        lb0, hb0, lb1, hb1 = d[o::bs], d[o + 1::bs], d[o + 2::bs], d[o + 3::bs]
        nb = len(lb0)
        n2 = 2 * nb
        r = (sum(hb0.translate(_TR)) + sum(hb1.translate(_TR))) / n2
        g = (sum(hb0.translate(_TG_HI)) + sum(lb0.translate(_TG_LO)) + sum(hb1.translate(_TG_HI))
             + sum(lb1.translate(_TG_LO))) / n2
        b = (sum(lb0.translate(_TB)) + sum(lb1.translate(_TB))) / n2
        if kind == 3:
            a = sum(sum(d[k::bs].translate(_NIBSUM)) for k in range(8)) * 17 / (16 * nb)
        elif kind == 5:
            a = (sum(d[0::bs]) + sum(d[1::bs])) / n2
        else:
            a = 255.0
    else:
        if palette is None and t.d3dfmt in ("PAL8", "PAL4"):
            palette = _GREY_PAL
        px = _decode_raw(t, mip0, palette)
        n = t.w * t.h
        r, g, b, a = (sum(px[c::4]) / n for c in range(4))
    return (round(r) << 24) | (round(g) << 16) | (round(b) << 8) | round(a)
