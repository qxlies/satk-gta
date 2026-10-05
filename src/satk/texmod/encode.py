"""Pixels -> texture levels: format choice, mip chain (box filter), raw and DXT encoding, PSNR (owner M2-03).

Formats written (:data:`FORMATS`): ``DXT1 DXT3 DXT5`` (:mod:`.bc`) and the uncompressed D3D9 formats
``A8R8G8B8 X8R8G8B8 R5G6B5 A1R5G5B5 A4R4G4B4`` (BGRA byte order / little-endian 16-bit words, as
:mod:`satk.formats.dxt` reads them).

Mip chain: level ``i`` is ``max(1, w >> i) x max(1, h >> i)``; a full chain ends at 1x1 (vanilla SA does the
same). Each level is a 2x2 box filter of the previous one (odd sides: exact area-weighted box); with alpha
the colour is averaged with alpha weights, so transparent texels do not bleed dark fringes into the
smaller levels.

numpy is imported inside the functions only (SPEC §2.3).
"""

from __future__ import annotations

import math

from .bc import ALPHA_CUT, DXT_FORMATS, QUALITIES, dxt_size, encode_dxt

__all__ = ["FORMATS", "RAW_FORMATS", "ALPHA_FORMATS", "QUALITIES", "full_levels", "level_dims", "level_size",
           "alpha_kind", "auto_format", "mip_chain", "encode_level", "psnr", "to_array"]

RAW_FORMATS = ("A8R8G8B8", "X8R8G8B8", "R5G6B5", "A1R5G5B5", "A4R4G4B4")
FORMATS = DXT_FORMATS + RAW_FORMATS
#: Formats whose texture carries alpha (DXT1 only when written with 1-bit alpha).
ALPHA_FORMATS = ("DXT3", "DXT5", "A8R8G8B8", "A1R5G5B5", "A4R4G4B4")
_BPP = {"A8R8G8B8": 4, "X8R8G8B8": 4, "R5G6B5": 2, "A1R5G5B5": 2, "A4R4G4B4": 2}


def _np():
    from ..core.errors import require_module

    return require_module("numpy", purpose="texture encoding (satk texture pack/replace)")


def to_array(w: int, h: int, rgba: bytes):
    """RGBA8 bytes -> ``(h, w, 4)`` uint8 array (a copy, writable)."""
    np = _np()
    return np.frombuffer(bytes(rgba), dtype=np.uint8, count=w * h * 4).reshape(h, w, 4).copy()


def full_levels(w: int, h: int) -> int:
    """Levels of a full mip chain down to 1x1: ``floor(log2(max(w, h))) + 1``."""
    return int(math.floor(math.log2(max(1, w, h)))) + 1


def level_dims(w: int, h: int, i: int) -> tuple[int, int]:
    return max(1, w >> i), max(1, h >> i)


def level_size(fmt: str, w: int, h: int) -> int:
    """Bytes of one ``w x h`` level in ``fmt``."""
    if fmt in DXT_FORMATS:
        return dxt_size(fmt, w, h)
    return w * h * _BPP[fmt]


def alpha_kind(img) -> str:
    """``opaque`` (all 255), ``binary`` (only 0 / 255 after the 1-bit cut is lossless) or ``smooth``."""
    a = img[:, :, 3]
    if bool((a == 255).all()):
        return "opaque"
    if bool(((a == 0) | (a == 255)).all()):
        return "binary"
    return "smooth"


def auto_format(img, base: str | None = None) -> tuple[str, bool]:
    """Pick ``(format, alpha)`` for an image (``alpha`` = the texture's alpha flag).

    Without ``base`` (a new texture): DXT1 (opaque), DXT1 + 1-bit alpha (only 0/255 alpha), DXT5
    (smooth alpha). Sides that are not multiples of 4 get A8R8G8B8/X8R8G8B8 instead (D3D9 wants whole
    blocks on the top level). With ``base`` (the format of the texture being replaced): the same family --
    a DXT texture stays DXT (the alpha of the new image picks the variant, DXT3 stays DXT3 when it needs
    alpha), an uncompressed one stays uncompressed (X8R8G8B8 / A8R8G8B8 by alpha, 16-bit formats kept when
    they can hold the alpha).
    """
    kind = alpha_kind(img)
    h, w = img.shape[:2]
    blocks_ok = w % 4 == 0 and h % 4 == 0
    if base is not None and base not in DXT_FORMATS and not (base.startswith("PAL") or base.startswith("PLATFORM")):
        if base in ("R5G6B5", "X8R8G8B8", "X1R5G5B5", "R8G8B8", "L8") and kind != "opaque":
            return "A8R8G8B8", True
        if base == "A1R5G5B5" and kind == "smooth":
            return "A8R8G8B8", True
        if base in RAW_FORMATS:
            return base, base in ALPHA_FORMATS
        return ("X8R8G8B8", False) if kind == "opaque" else ("A8R8G8B8", True)
    if not blocks_ok:
        return ("X8R8G8B8", False) if kind == "opaque" else ("A8R8G8B8", True)
    if kind == "opaque":
        return "DXT1", False
    if kind == "binary":
        return "DXT1", True
    return ("DXT3" if base == "DXT3" else "DXT5"), True


# ----------------------------------------------------------------------------- mips


def _box_matrix(np, n_in: int, n_out: int):
    """(n_out, n_in) area-weighted box filter (rows sum to 1)."""
    m = np.zeros((n_out, n_in), dtype=np.float64)
    scale = n_in / n_out
    for o in range(n_out):
        a, b = o * scale, (o + 1) * scale
        for i in range(int(math.floor(a)), min(n_in, int(math.ceil(b)))):
            m[o, i] = min(b, i + 1) - max(a, i)
    return m / m.sum(1, keepdims=True)


def _half(np, f, nw: int, nh: int):
    """Box-reduce a float (h, w, c) array to (nh, nw, c)."""
    h, w = f.shape[:2]
    if h == 2 * nh and w == 2 * nw:
        return (f[0::2, 0::2] + f[1::2, 0::2] + f[0::2, 1::2] + f[1::2, 1::2]) * 0.25
    if h == 2 * nh and w == nw:
        return (f[0::2] + f[1::2]) * 0.5
    if h == nh and w == 2 * nw:
        return (f[:, 0::2] + f[:, 1::2]) * 0.5
    my, mx = _box_matrix(np, h, nh), _box_matrix(np, w, nw)
    return np.einsum("yh,hwc,xw->yxc", my, f, mx)


def mip_chain(img, levels: int, *, alpha_weighted: bool = True) -> list:
    """``levels`` images, level 0 = ``img`` (uint8 (h, w, 4) arrays)."""
    np = _np()
    out = [img]
    h, w = img.shape[:2]
    cur = img.astype(np.float64)
    weighted = alpha_weighted and bool((img[:, :, 3] != 255).any())
    if weighted:
        cur[:, :, :3] *= cur[:, :, 3:4] / 255.0
    for i in range(1, levels):
        nw, nh = level_dims(w, h, i)
        cur = _half(np, cur, nw, nh)
        lvl = cur.copy()
        if weighted:
            a = lvl[:, :, 3:4]
            lvl[:, :, :3] = np.where(a > 0, lvl[:, :, :3] * 255.0 / np.maximum(a, 1e-9), 0)
        out.append(np.clip(np.rint(lvl), 0, 255).astype(np.uint8))
    return out


# ----------------------------------------------------------------------------- encoding


def _raw(np, img, fmt: str) -> bytes:
    r, g, b, a = (img[:, :, k].astype(np.uint32) for k in range(4))
    if fmt in ("A8R8G8B8", "X8R8G8B8"):
        out = np.empty(img.shape, dtype=np.uint8)
        out[:, :, 0], out[:, :, 1], out[:, :, 2] = img[:, :, 2], img[:, :, 1], img[:, :, 0]
        out[:, :, 3] = img[:, :, 3] if fmt == "A8R8G8B8" else 255
        return out.tobytes()

    def q(v, bits):
        return (v * ((1 << bits) - 1) + 127) // 255

    if fmt == "R5G6B5":
        v = (q(r, 5) << 11) | (q(g, 6) << 5) | q(b, 5)
    elif fmt == "A1R5G5B5":
        v = ((a >= ALPHA_CUT).astype(np.uint32) << 15) | (q(r, 5) << 10) | (q(g, 5) << 5) | q(b, 5)
    elif fmt == "A4R4G4B4":
        v = (q(a, 4) << 12) | (q(r, 4) << 8) | (q(g, 4) << 4) | q(b, 4)
    else:
        raise ValueError(f"cannot write format {fmt!r}")
    return v.astype("<u2").tobytes()


def encode_level(img, fmt: str, *, alpha: bool = False, quality: str = "normal") -> bytes:
    """One level of ``fmt`` (``alpha``: DXT1 with 1-bit alpha). ``img`` is ``(h, w, 4)`` uint8."""
    np = _np()
    if fmt in DXT_FORMATS:
        return encode_dxt(img, fmt, alpha=alpha, quality=quality)
    if fmt not in RAW_FORMATS:
        raise ValueError(f"cannot write format {fmt!r}; expected one of {FORMATS}")
    return _raw(np, np.ascontiguousarray(img, dtype=np.uint8), fmt)


def psnr(src, decoded, *, alpha: bool) -> float:
    """PSNR in dB between two (h, w, 4) uint8 images, capped at 100 (identical).

    ``alpha=False``: RGB only. ``alpha=True``: premultiplied RGB plus the alpha channel, so colours under
    fully transparent texels (which DXT1 stores as black) do not count.
    """
    np = _np()
    x = src.astype(np.float64)
    y = decoded.astype(np.float64)
    if alpha:
        x = np.concatenate([x[:, :, :3] * x[:, :, 3:4] / 255.0, x[:, :, 3:4]], axis=2)
        y = np.concatenate([y[:, :, :3] * y[:, :, 3:4] / 255.0, y[:, :, 3:4]], axis=2)
    else:
        x, y = x[:, :, :3], y[:, :, :3]
    mse = float(((x - y) ** 2).mean())
    if mse <= 1e-10:
        return 100.0
    return min(100.0, round(10 * math.log10(255.0 ** 2 / mse), 2))
