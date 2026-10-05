"""TXD checks (``txd.*`` rules): platform, format, names, sizes (power of two, DXT blocks), mip chain.

The mip chain is walked from :attr:`TexInfo.mip0_off` (``u32 size`` + data per level, already bounds-checked
by :func:`satk.formats.txd.parse_txd`); each level must hold at least the bytes its format and size need.
"""

from __future__ import annotations

import struct
from collections import Counter
from dataclasses import dataclass

from ..formats.rw import FormatError
from ..formats.txd import TexInfo, parse_txd
from .rules import Collector

__all__ = ["TxdFacts", "check_txd", "level_bytes"]

#: Bits per pixel of uncompressed formats.
_BPP = {"A8R8G8B8": 32, "X8R8G8B8": 32, "R8G8B8": 24, "R5G6B5": 16, "A1R5G5B5": 16, "X1R5G5B5": 16,
        "A4R4G4B4": 16, "A8L8": 16, "L8": 8, "A8": 8, "PAL8": 8}
_BLOCK = {"DXT1": 8, "DXT2": 16, "DXT3": 16, "DXT4": 16, "DXT5": 16}


@dataclass(frozen=True, slots=True)
class TxdFacts:
    """What the link checks need from one TXD: lower-case texture names."""

    stem: str
    label: str
    archive: str | None
    loose: bool
    textures: frozenset[str]


def level_bytes(fmt: str, w: int, h: int, depth: int = 0) -> int | None:
    """Bytes one mip level of ``fmt`` needs at ``w`` x ``h`` (``None`` = unknown format)."""
    if fmt in _BLOCK:
        return max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * _BLOCK[fmt]
    bpp = _BPP.get(fmt) or (depth if depth in (8, 16, 24, 32) else 0)
    if not bpp:
        return None
    return w * h * bpp // 8


def _pow2(n: int) -> bool:
    return n > 0 and n & (n - 1) == 0


def _max_levels(w: int, h: int) -> int:
    return max(w, h, 1).bit_length()


def _mips(c: Collector, label: str, buf, t: TexInfo) -> None:
    need0 = level_bytes(t.d3dfmt, t.w, t.h, t.depth)
    if t.levels == 0:
        c.add("txd.mip_size", label, tex=t.name, lvl=0, size=0, fmt=t.d3dfmt, w=t.w, h=t.h, need=need0 or "?")
        return
    if t.levels > _max_levels(t.w, t.h):
        c.add("txd.mip_levels", label, tex=t.name, levels=t.levels, max=_max_levels(t.w, t.h), w=t.w, h=t.h)
    if need0 is None or not c.on("txd.mip_size"):
        return
    p = t.mip0_off - 4
    for lvl in range(t.levels):
        (size,) = struct.unpack_from("<I", buf, p)
        w, h = max(1, t.w >> lvl), max(1, t.h >> lvl)
        need = level_bytes(t.d3dfmt, w, h, t.depth)
        if need is not None and size < need and not (size == 0 and t.d3dfmt in _BLOCK and min(w, h) < 4):
            c.add("txd.mip_size", label, tex=t.name, lvl=lvl, size=size, fmt=t.d3dfmt, w=w, h=h, need=need)
            return
        p += 4 + size


def check_txd(c: Collector, label: str, data: bytes, *, archive: str | None = None, loose: bool = False
              ) -> TxdFacts | None:
    """Run the ``txd.*`` rules on one TXD blob."""
    stem = label.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
    try:
        txd = parse_txd(data)
    except FormatError as e:
        c.add("txd.parse", label, err=str(e))
        return None
    texs = txd.textures
    if not texs:
        c.add("txd.empty", label)
    if txd.count != len(texs):
        c.add("txd.count", label, count=txd.count, n=len(texs))
    formats = {str(f).upper() for f in c.rules.param("txd.format", "formats", []) or []}
    alpha_fmts = {str(f).upper() for f in c.rules.param("txd.alpha_off", "formats", []) or []}
    smax = int(c.rules.param("txd.size_max", "max", 2048))
    nmax = int(c.rules.param("txd.name_len", "max", 31))
    mip_min = int(c.rules.param("txd.mips_missing", "min_size", 64))
    names = Counter(t.name.lower() for t in texs if t.name)
    for n, k in sorted(names.items()):
        if k > 1:
            c.add("txd.dup_name", label, tex=n, n=k)
    for t in texs:
        shown = t.name or f"#{t.idx}"
        if not t.name:
            c.add("txd.name_empty", label, idx=t.idx)
        elif len(t.name) > nmax:
            c.add("txd.name_len", label, tex=t.name, n=len(t.name))
        if t.unsupported:
            c.add("txd.platform", label, tex=shown, platform=t.platform)
            continue
        fmt = t.d3dfmt.upper()
        if formats and fmt not in formats:
            c.add("txd.format", label, tex=shown, fmt=t.d3dfmt)
        if not (_pow2(t.w) and _pow2(t.h)):
            c.add("txd.pow2", label, tex=shown, w=t.w, h=t.h)
        if max(t.w, t.h) > smax:
            c.add("txd.size_max", label, tex=shown, w=t.w, h=t.h)
        if fmt in _BLOCK and (t.w % 4 or t.h % 4):
            c.add("txd.dxt_block", label, tex=shown, fmt=t.d3dfmt, w=t.w, h=t.h)
        if t.levels <= 1 and max(t.w, t.h) >= mip_min:
            c.add("txd.mips_missing", label, tex=shown, w=t.w, h=t.h)
        if fmt in alpha_fmts and not t.alpha:
            c.add("txd.alpha_off", label, tex=shown, fmt=t.d3dfmt)
        try:
            _mips(c, label, data, t)
        except (struct.error, IndexError):  # parse_txd validated the chain; be defensive
            c.add("txd.mip_size", label, tex=shown, lvl="?", size="?", fmt=t.d3dfmt, w=t.w, h=t.h, need="?")
    mb = len(data) / (1024 * 1024)
    if mb > float(c.rules.param("txd.total_size", "max_mb", 8.0)):
        c.add("txd.total_size", label, mb=round(mb, 1))
    return TxdFacts(stem, label, archive, loose, frozenset(names))
