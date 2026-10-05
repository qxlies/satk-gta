"""GTA San Andreas text files (``text/*.gxt``): tables of hashed keys -> strings.

SA format (version 4): ``u16 version=4, u16 bits_per_char (8 or 16)``, then a ``TABL`` block
listing ``(char name[8], u32 offset)`` per table. Each table starts (except ``MAIN``) with its
8-byte name, followed by ``TKEY`` (``u32 size`` + ``(u32 tdat_offset, u32 key_hash)`` entries)
and ``TDAT`` (``u32 size`` + zero-terminated strings). Keys are stored only as JAMCRC32 of the
upper-case key name (:func:`key_hash`), so lookups go by name -> hash.

Strings are decoded as latin-1 (8-bit files: the game's own code page; for English text this is
ASCII). Formatting tokens such as ``~r~`` are kept as they are.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from ..core.paths import open_ro
from .rw import FormatError

__all__ = ["Gxt", "key_hash", "parse_gxt", "load_gxt"]

#: Upper bound on table count and text size; the stock american.gxt has 127 tables, 0.74 MB.
MAX_TABLES = 4096
MAX_SIZE = 64 << 20


def key_hash(key: str) -> int:
    """JAMCRC32 (CRC-32 without the final inversion) of the upper-case key, as SA stores it."""
    return zlib.crc32(key.upper().encode("latin-1")) ^ 0xFFFFFFFF


@dataclass
class Gxt:
    bits: int
    #: table name -> {key hash -> text}
    tables: dict[str, dict[int, str]] = field(default_factory=dict)

    def text(self, key: str, table: str = "MAIN") -> str | None:
        """The text of ``key`` in ``table`` (``None`` if absent)."""
        return self.tables.get(table, {}).get(key_hash(key))


def _block(data: bytes, off: int, magic: bytes) -> tuple[int, int]:
    if data[off:off + 4] != magic:
        raise FormatError("gxt", off, f"expected {magic.decode()} block")
    if off + 8 > len(data):
        raise FormatError("gxt", off, "truncated block header")
    size = struct.unpack_from("<I", data, off + 4)[0]
    start = off + 8
    if start + size > len(data):
        raise FormatError("gxt", off, f"{magic.decode()} block of {size} bytes exceeds the file")
    return start, size


def _string(data: bytes, start: int, end: int, bits: int) -> str:
    if bits == 8:
        stop = data.find(b"\0", start, end)
        if stop < 0:
            raise FormatError("gxt", start, "unterminated string")
        return data[start:stop].decode("latin-1")
    i = start
    while i + 1 < end and data[i:i + 2] != b"\0\0":
        i += 2
    if i + 1 >= end:
        raise FormatError("gxt", start, "unterminated string")
    return data[start:i].decode("utf-16-le", errors="replace")


def parse_gxt(data: bytes) -> Gxt:
    """Parse an SA (version 4) GXT file. Other games' GXT layouts raise :class:`FormatError`."""
    if len(data) > MAX_SIZE:
        raise FormatError("gxt", 0, f"file too large ({len(data)} bytes)")
    if len(data) < 8:
        raise FormatError("gxt", 0, "truncated header")
    version, bits = struct.unpack_from("<HH", data, 0)
    if version != 4 or bits not in (8, 16):
        raise FormatError("gxt", 0, f"unsupported GXT (version {version}, {bits} bits per char); only GTA SA is supported")
    tabl, tsize = _block(data, 4, b"TABL")
    if tsize % 12 or tsize // 12 > MAX_TABLES:
        raise FormatError("gxt", 4, f"bad TABL size {tsize}")
    out = Gxt(bits)
    for i in range(tsize // 12):
        raw, off = struct.unpack_from("<8sI", data, tabl + i * 12)
        name = raw.split(b"\0", 1)[0].decode("latin-1")
        if name != "MAIN":
            if data[off:off + 8].split(b"\0", 1)[0] != raw.split(b"\0", 1)[0]:
                raise FormatError("gxt", off, f"table {name!r} does not start with its name")
            off += 8
        kstart, ksize = _block(data, off, b"TKEY")
        if ksize % 8:
            raise FormatError("gxt", off, f"bad TKEY size {ksize} in table {name!r}")
        dstart, dsize = _block(data, kstart + ksize, b"TDAT")
        texts: dict[int, str] = {}
        for k in range(ksize // 8):
            toff, h = struct.unpack_from("<II", data, kstart + k * 8)
            if toff >= dsize:
                raise FormatError("gxt", kstart + k * 8, f"text offset {toff} outside TDAT of table {name!r}")
            texts[h] = _string(data, dstart + toff, dstart + dsize, bits)
        out.tables[name] = texts
    return out


def load_gxt(path: str | Path) -> Gxt:
    """Read and parse a GXT file (read-only)."""
    with open_ro(path) as f:
        return parse_gxt(f.read(MAX_SIZE + 1))
