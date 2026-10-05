"""RenderWare binary stream chunks (SPEC §4.2 ``satk.formats.rw``). Stdlib only.

A RW chunk is a 12-byte header ``<u32 type, u32 size, u32 libid>`` followed by ``size`` bytes.
Containers (Clump, TexDictionary, Geometry, ...) hold child chunks back to back.

Rules (SPEC §4.2): a child never extends past its parent (``FormatError`` otherwise, or a clamp
with ``strict=False``); no function raises ``IndexError``/``struct.error`` on broken data.

Example::

    from satk.formats.rw import iter_children, rw_version
    for ch in iter_children(buf, 12, 12 + top_size):
        print(hex(ch.type), ch.size, hex(rw_version(ch.libid)))
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Iterator

__all__ = [
    "FormatError",
    "Chunk",
    "CHUNK_NAMES",
    "read_chunk",
    "iter_children",
    "find_child",
    "rw_version",
    "rw_payload_size",
    "plausible_libid",
]

_HDR = struct.Struct("<III")

#: Chunk type -> name (core RW + SA plugins used by the parsers and dumps).
CHUNK_NAMES: dict[int, str] = {
    0x01: "Struct", 0x02: "String", 0x03: "Extension", 0x05: "Camera", 0x06: "Texture",
    0x07: "Material", 0x08: "MaterialList", 0x0E: "FrameList", 0x0F: "Geometry",
    0x10: "Clump", 0x12: "Light", 0x14: "Atomic", 0x15: "TextureNative", 0x16: "TexDictionary",
    0x1A: "GeometryList", 0x2B: "UVAnimDict",
    0x105: "MorphPLG", 0x116: "SkinPLG", 0x11E: "HAnimPLG", 0x11F: "UserDataPLG",
    0x120: "MatEffectsPLG", 0x135: "UVAnimPLG", 0x50E: "BinMeshPLG", 0x510: "NativeDataPLG",
    0x253F2F3: "PipelineSet", 0x253F2F6: "SpecularMat", 0x253F2F8: "2dEffect",
    0x253F2F9: "ExtraVertColour", 0x253F2FA: "CollisionModel", 0x253F2FC: "ReflectionMat",
    0x253F2FD: "Breakable", 0x253F2FE: "FrameName",
}


class FormatError(ValueError):
    """Malformed or unsupported data.

    Args:
        kind: format/area, e.g. ``"rw"``, ``"img"``, ``"txd"``, ``"ipl"``.
        offset: byte offset (binary formats) or 1-based line number (text formats).
        msg: short English description.
    """

    def __init__(self, kind: str, offset: int, msg: str):
        self.kind = str(kind)
        self.offset = int(offset)
        self.msg = str(msg)
        super().__init__(f"{self.kind} @0x{self.offset:x}: {self.msg}")

    def __reduce__(self):  # picklable across ProcessPoolExecutor
        return (FormatError, (self.kind, self.offset, self.msg))


@dataclass(frozen=True, slots=True)
class Chunk:
    """One chunk header. ``data_off`` = first payload byte, ``end`` = ``data_off + size``."""

    type: int
    size: int
    libid: int
    data_off: int
    end: int

    @property
    def version(self) -> int:
        """Decoded RW version (``0x36003`` for SA PC)."""
        return rw_version(self.libid)

    @property
    def name(self) -> str:
        return CHUNK_NAMES.get(self.type, f"0x{self.type:X}")


def rw_version(libid: int) -> int:
    """Decode a RW library ID stamp to a version number.

    New-style stamps (high 16 bits set): ``((id>>14)&0x3FF00)+0x30000 | ((id>>16)&0x3F)``;
    old-style stamps (``0x310`` etc.): ``id << 8``. SA PC -> ``0x36003``.
    """
    libid &= 0xFFFFFFFF
    if libid & 0xFFFF0000:
        return (((libid >> 14) & 0x3FF00) + 0x30000) | ((libid >> 16) & 0x3F)
    return libid << 8


def plausible_libid(libid: int) -> bool:
    """True if ``libid`` decodes to a RW 3.x version (0x30000..0x3FFFF)."""
    v = rw_version(libid)
    return 0x30000 <= v <= 0x3FFFF


def read_chunk(buf, off: int, end: int | None = None) -> Chunk:
    """Read the chunk header at ``off``; the chunk must fit before ``end`` (default: buffer end)."""
    n = len(buf)
    limit = n if end is None else min(end, n)
    if off < 0 or off + 12 > limit:
        raise FormatError("rw", max(off, 0), "truncated chunk header")
    t, s, v = _HDR.unpack_from(buf, off)
    data = off + 12
    if data + s > limit:
        raise FormatError("rw", off, f"chunk 0x{t:X} size {s} overruns its parent/buffer by {data + s - limit}")
    return Chunk(t, s, v, data, data + s)


def iter_children(buf, start: int, end: int, *, strict: bool = True) -> Iterator[Chunk]:
    """Yield consecutive chunks in ``[start, end)``.

    A child larger than the remaining parent space raises ``FormatError`` (``strict=False``:
    the child is clamped to the parent end and iteration stops). Fewer than 12 trailing bytes
    are ignored (padding).
    """
    n = len(buf)
    if end > n:
        if strict:
            raise FormatError("rw", start, f"container end {end} beyond buffer size {n}")
        end = n
    off = start
    while off + 12 <= end:
        t, s, v = _HDR.unpack_from(buf, off)
        data = off + 12
        if data + s > end:
            if strict:
                raise FormatError("rw", off, f"child chunk 0x{t:X} size {s} overruns parent by {data + s - end}")
            yield Chunk(t, end - data, v, data, end)
            return
        yield Chunk(t, s, v, data, data + s)
        off = data + s


def find_child(buf, start: int, end: int, ctype: int) -> Chunk | None:
    """First child of type ``ctype`` in ``[start, end)`` or ``None``."""
    for ch in iter_children(buf, start, end):
        if ch.type == ctype:
            return ch
    return None


def rw_payload_size(buf, start: int = 0) -> int | None:
    """Real size of the RW data at ``buf[start:]`` (IMG entries are padded to 2048-byte sectors).

    Sums consecutive top-level chunks with a plausible header (``UVAnimDict`` + ``Clump``, the
    multi-clump entries of ``player.img``) and stops at zero padding. ``None`` if the data does
    not start with a plausible RW chunk or the first chunk is truncated.
    """
    n = len(buf)
    off = start
    total = 0
    while off + 12 <= n:
        t, s, v = _HDR.unpack_from(buf, off)
        if t == 0 or not plausible_libid(v) or off + 12 + s > n:
            break
        total += 12 + s
        off += 12 + s
    return total or None
