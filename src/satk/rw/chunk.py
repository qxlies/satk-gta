"""Lossless RenderWare chunk tree (``satk.rw.chunk``). Stdlib only.

:func:`parse` splits a RW binary stream into :class:`Chunk` nodes: container chunks (Clump, FrameList,
GeometryList, Geometry, MaterialList, Material, Texture, Atomic, Extension, Light, ...; librw's stream
structure) get ``kids``, everything else keeps its payload bytes untouched. :meth:`RwStream.to_bytes`
writes the tree back with every chunk size recomputed from its content, so an unmodified tree
reproduces the input bit for bit and an edited payload only changes the sizes on its path to the root.

A container whose payload does not split into whole child chunks is kept as an opaque leaf
(``Chunk.opaque``): nothing is lost, it just cannot be edited. Bytes after the last top-level chunk
(the zero padding of IMG sectors) are kept in :attr:`RwStream.tail`.

Library IDs follow librw (``libraryIDPack``): ``0x1803FFFF`` = RW 3.6.0.3 (San Andreas),
``0x0C02FFFF`` = 3.3.0.2 (Vice City), ``0x0401FFFF`` = 3.1.0.1 (GTA III).

Example::

    s = parse(blob)
    for node, path in s.walk():
        if node.type == STRING:
            ...
    assert s.to_bytes() == blob
"""

from __future__ import annotations

import struct
from typing import Iterator

from ..formats.rw import CHUNK_NAMES, plausible_libid, rw_version

__all__ = [
    "Chunk", "RwStream", "parse", "parse_chunk", "pack_libid", "rw_version", "GAME_VERSIONS", "CONTAINERS",
    "STRUCT", "STRING", "EXTENSION", "CAMERA", "TEXTURE", "MATERIAL", "MATLIST", "FRAMELIST", "GEOMETRY", "CLUMP",
    "LIGHT", "ATOMIC", "GEOMLIST", "UVANIMDICT", "ANIMATION", "SKIN", "HANIM", "MATFX", "UVANIM", "BINMESH",
    "SPECULAR", "FX2D", "NIGHT", "COLMODEL", "REFLECTION", "BREAKABLE", "FRAMENAME", "chunk_name",
]

STRUCT, STRING, EXTENSION, CAMERA, TEXTURE, MATERIAL, MATLIST = 0x01, 0x02, 0x03, 0x05, 0x06, 0x07, 0x08
FRAMELIST, GEOMETRY, CLUMP, LIGHT, ATOMIC, TEXNATIVE, TEXDICT = 0x0E, 0x0F, 0x10, 0x12, 0x14, 0x15, 0x16
GEOMLIST, ANIMATION, UVANIMDICT = 0x1A, 0x1B, 0x2B
SKIN, HANIM, MATFX, UVANIM, BINMESH, NATIVEDATA = 0x116, 0x11E, 0x120, 0x135, 0x50E, 0x510
SPECULAR, FX2D, NIGHT, COLMODEL = 0x253F2F6, 0x253F2F8, 0x253F2F9, 0x253F2FA
REFLECTION, BREAKABLE, FRAMENAME = 0x253F2FC, 0x253F2FD, 0x253F2FE

#: Chunk types whose payload is a sequence of child chunks (librw stream structure).
CONTAINERS = frozenset({EXTENSION, CAMERA, TEXTURE, MATERIAL, MATLIST, FRAMELIST, GEOMETRY, CLUMP, LIGHT, ATOMIC,
                        TEXNATIVE, TEXDICT, GEOMLIST, UVANIMDICT, UVANIM, NATIVEDATA})

#: ``--rw-version`` names -> RW version (librw numbering).
GAME_VERSIONS = {"iii": 0x31001, "vc": 0x33002, "sa": 0x36003}

_HDR = struct.Struct("<III")


def chunk_name(t: int) -> str:
    return CHUNK_NAMES.get(t, f"0x{t:X}")


def pack_libid(version: int, build: int = 0xFFFF) -> int:
    """librw ``libraryIDPack``: RW version (``0x36003``) -> library ID stamp (``0x1803FFFF``)."""
    if version <= 0x31000:
        return version >> 8
    return (((version - 0x30000) & 0x3FF00) << 14) | ((version & 0x3F) << 16) | (build & 0xFFFF)


class Chunk:
    """One chunk. Containers have ``kids`` (``data is None``), leaves have ``data``.

    ``tail`` holds bytes after the last child that do not form a chunk (kept on write).
    """

    __slots__ = ("type", "libid", "data", "kids", "tail", "opaque")

    def __init__(self, type: int, libid: int, data: bytes | None = None, kids: list["Chunk"] | None = None,  # noqa: A002
                 tail: bytes = b"", opaque: bool = False):
        self.type = type
        self.libid = libid
        self.data = data
        self.kids = kids
        self.tail = tail
        self.opaque = opaque

    # ------------------------------------------------------------------ properties
    @property
    def version(self) -> int:
        return rw_version(self.libid)

    @property
    def name(self) -> str:
        return chunk_name(self.type)

    @property
    def is_container(self) -> bool:
        return self.kids is not None

    def child(self, ctype: int) -> "Chunk | None":
        """First direct child of type ``ctype``."""
        for k in self.kids or ():
            if k.type == ctype:
                return k
        return None

    def children(self, ctype: int) -> list["Chunk"]:
        return [k for k in self.kids or () if k.type == ctype]

    # ------------------------------------------------------------------ writing
    def _emit(self, out: list) -> int:
        """Append header + payload parts to ``out``; returns the total size (header included)."""
        if self.kids is None:
            data = self.data or b""
            out.append(_HDR.pack(self.type, len(data), self.libid))
            out.append(data)
            return 12 + len(data)
        at = len(out)
        out.append(b"")
        size = 0
        for k in self.kids:
            size += k._emit(out)
        if self.tail:
            out.append(self.tail)
            size += len(self.tail)
        out[at] = _HDR.pack(self.type, size, self.libid)
        return 12 + size

    def to_bytes(self) -> bytes:
        parts: list = []
        self._emit(parts)
        return b"".join(parts)

    def payload(self) -> bytes:
        """The payload as it would be written (children serialized)."""
        if self.kids is None:
            return self.data or b""
        return self.to_bytes()[12:]

    def walk(self, path: tuple = ()) -> Iterator[tuple["Chunk", tuple]]:
        """Depth-first ``(node, parent types)`` over this node and its descendants."""
        yield self, path
        if self.kids:
            p = path + (self.type,)
            for k in self.kids:
                yield from k.walk(p)

    def __repr__(self) -> str:
        what = f"{len(self.kids)} kids" if self.kids is not None else f"{len(self.data or b'')} B"
        return f"Chunk({self.name}, 0x{self.libid:08X}, {what})"


def _split(buf: bytes, start: int, end: int) -> tuple[list[Chunk], bytes] | None:
    """Children of ``buf[start:end]`` or ``None`` when the bytes are not a chunk sequence."""
    kids: list[Chunk] = []
    off = start
    while off + 12 <= end:
        t, s, v = _HDR.unpack_from(buf, off)
        if off + 12 + s > end:
            return None
        kids.append(_node(buf, t, v, off + 12, off + 12 + s))
        off += 12 + s
    return kids, bytes(buf[off:end])


def _node(buf: bytes, t: int, v: int, start: int, end: int) -> Chunk:
    if t in CONTAINERS:
        sp = _split(buf, start, end)
        if sp is not None and (not sp[1] or sp[0]):
            return Chunk(t, v, kids=sp[0], tail=sp[1])
        return Chunk(t, v, data=bytes(buf[start:end]), opaque=True)
    return Chunk(t, v, data=bytes(buf[start:end]))


def parse_chunk(buf: bytes, off: int = 0) -> Chunk:
    """The chunk at ``buf[off:]`` (``ValueError`` if its header does not fit)."""
    if off + 12 > len(buf):
        raise ValueError("truncated chunk header")
    t, s, v = _HDR.unpack_from(buf, off)
    if off + 12 + s > len(buf):
        raise ValueError(f"chunk 0x{t:X} of {s} bytes overruns the buffer")
    return _node(buf, t, v, off + 12, off + 12 + s)


class RwStream:
    """Top-level chunks of a RW file plus the bytes after them (``tail``)."""

    __slots__ = ("chunks", "tail")

    def __init__(self, chunks: list[Chunk], tail: bytes = b""):
        self.chunks = chunks
        self.tail = tail

    def to_bytes(self, *, with_tail: bool = True) -> bytes:
        parts: list = []
        for c in self.chunks:
            c._emit(parts)
        if with_tail and self.tail:
            parts.append(self.tail)
        return b"".join(parts)

    def walk(self) -> Iterator[tuple[Chunk, tuple]]:
        for c in self.chunks:
            yield from c.walk()

    def find_all(self, ctype: int) -> list[Chunk]:
        return [n for n, _p in self.walk() if n.type == ctype]

    @property
    def payload_size(self) -> int:
        return sum(12 + len(c.payload()) for c in self.chunks)


def parse(buf: bytes) -> RwStream:
    """Split ``buf`` into top-level chunks; stops at zero padding or an implausible header.

    Same rule as :func:`satk.formats.rw.rw_payload_size` (``UVAnimDict`` + ``Clump``, the 3 clumps
    of ``player.img`` entries). ``ValueError`` if ``buf`` does not start with a RW chunk.
    """
    buf = bytes(buf)
    n = len(buf)
    off = 0
    chunks: list[Chunk] = []
    while off + 12 <= n:
        t, s, v = _HDR.unpack_from(buf, off)
        if t == 0 or not plausible_libid(v) or off + 12 + s > n:
            break
        chunks.append(_node(buf, t, v, off + 12, off + 12 + s))
        off += 12 + s
    if not chunks:
        raise ValueError("not a RenderWare stream (no plausible top-level chunk)")
    return RwStream(chunks, buf[off:])
