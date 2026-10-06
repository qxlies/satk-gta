"""Per-texture facts for audit and optimisation: sizes, pixel hash, alpha use, DXT1 punch-through texels.

Sizes: ``native`` = bytes of the ``TextureNative`` chunk in the file (what the TXD shrinks by), ``data`` = the
level data plus the palette. Alpha (decoded mip 0, :func:`satk.formats.dxt.decode_rgba`):

* ``opaque`` -- every texel 255 (or the texture has no alpha flag and is not DXT1);
* ``binary`` -- only 0 and 255 (DXT1 with 1-bit alpha holds it losslessly);
* ``smooth`` -- other values (DXT5 territory).

``holes`` -- a DXT1 texture with transparent texels (3-colour blocks using index 3). With the alpha flag off they
render black. Stdlib at import; numpy inside the functions.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..formats.rw import FormatError
from ..formats.txd import TexInfo, mip0_bytes, palette_bytes, texture_hash

__all__ = ["RAW", "DXT", "TexFacts", "decode", "facts", "alpha_kind", "is_pow2", "full_levels", "chain_bytes",
           "fmt_label"]

DXT = ("DXT1", "DXT3", "DXT5")
#: Formats D3D9 keeps uncompressed in memory (paletted textures are expanded to 32 bit on load).
RAW = ("A8R8G8B8", "X8R8G8B8", "R8G8B8", "R5G6B5", "A1R5G5B5", "X1R5G5B5", "A4R4G4B4", "L8", "A8L8", "A8",
       "PAL8", "PAL4")
_BPP = {"A8R8G8B8": 4, "X8R8G8B8": 4, "R8G8B8": 3, "R5G6B5": 2, "A1R5G5B5": 2, "X1R5G5B5": 2, "A4R4G4B4": 2,
        "L8": 1, "A8L8": 2, "A8": 1, "PAL8": 1, "PAL4": 1}


def is_pow2(n: int) -> bool:
    return n > 0 and n & (n - 1) == 0


def full_levels(w: int, h: int) -> int:
    """Levels of a full mip chain down to 1x1."""
    return max(1, w, h).bit_length()


def level_bytes(fmt: str, w: int, h: int) -> int:
    if fmt in DXT:
        return max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * (8 if fmt == "DXT1" else 16)
    return w * h * _BPP.get(fmt, 4)


def chain_bytes(fmt: str, w: int, h: int, levels: int) -> int:
    """Level data of ``levels`` mips (``4`` bytes of size field each), without the 100-byte native header."""
    return sum(4 + level_bytes(fmt, max(1, w >> i), max(1, h >> i)) for i in range(levels))


def fmt_label(fmt: str, alpha: bool, w: int, h: int, levels: int) -> str:
    """``DXT1+a 512x512 m10`` (``+a`` only for DXT1 with alpha, ``mN`` only with mips)."""
    s = f"{fmt}{'+a' if fmt == 'DXT1' and alpha else ''} {w}x{h}"
    return s + (f" m{levels}" if levels > 1 else "")


@dataclass
class TexFacts:
    """Facts of one texture of one TXD."""

    t: TexInfo
    native: int
    data: int
    hash: str
    alpha: str | None = None       # opaque | binary | smooth | None (not decoded / unsupported)
    holes: bool = False

    @property
    def label(self) -> str:
        t = self.t
        return fmt_label(t.d3dfmt, t.alpha, t.w, t.h, t.levels)


def decode(buf: bytes, t: TexInfo):
    """Mip 0 of ``t`` as an ``(h, w, 4)`` uint8 array."""
    from ..formats.dxt import decode_rgba
    from ..texmod.encode import to_array

    return to_array(t.w, t.h, decode_rgba(t, mip0_bytes(buf, t), palette_bytes(buf, t)))


def alpha_kind(img) -> str:
    a = img[:, :, 3]
    if bool((a == 255).all()):
        return "opaque"
    if bool(((a == 0) | (a == 255)).all()):
        return "binary"
    return "smooth"


def facts(buf: bytes, t: TexInfo, native: int, *, pixels: bool = True) -> TexFacts:
    """Facts of ``t`` in the TXD ``buf``; ``pixels`` decodes mip 0 for the alpha checks."""
    data = (t.pal_size or 0) + _levels_size(buf, t)
    f = TexFacts(t, native, data, texture_hash(buf, t).hex() if not t.unsupported else "")
    if not pixels or t.unsupported or t.w == 0 or t.h == 0:
        return f
    import dataclasses

    try:
        # DXT1 without the alpha flag decodes index 3 as opaque black; decode it as 1-bit alpha to see the holes
        img = decode(buf, dataclasses.replace(t, alpha=True) if t.d3dfmt == "DXT1" else t)
    except (FormatError, ValueError):
        return f
    kind = alpha_kind(img)
    f.holes = t.d3dfmt == "DXT1" and kind != "opaque"
    f.alpha = kind if t.alpha else "opaque"
    return f


def _levels_size(buf: bytes, t: TexInfo) -> int:
    import struct

    if t.unsupported:
        return 0
    total = 0
    p = t.mip0_off - 4
    for _ in range(t.levels):
        (n,) = struct.unpack_from("<I", buf, p)
        total += n
        p += 4 + n
    return total
