"""PE32 image access for gta_sa.exe (SPEC §4.9; owner WP-09).

``pe_sections``/``va_to_off`` are the shared ``satk.formats.pe`` helpers (WP-02, SPEC §4.2),
re-exported here. :class:`PeImage` adds what the symbol DB needs on top of them and what the
shared tuple API does not carry: section characteristics (executable or not), VA-based reads,
the checksum and the file hash. Standard library only.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from pathlib import Path

from ..formats.pe import pe_sections, va_to_off

__all__ = ["PeError", "Section", "PeImage", "pe_sections", "va_to_off",
           "IMAGE_SCN_MEM_EXECUTE", "IMAGE_SCN_CNT_CODE"]

IMAGE_SCN_CNT_CODE = 0x00000020
IMAGE_SCN_MEM_EXECUTE = 0x20000000


class PeError(ValueError):
    """Not a PE32 image (or a truncated one)."""


@dataclass(frozen=True, slots=True)
class Section:
    """One section header; ``va`` is absolute (image base added)."""

    index: int
    name: str
    va: int
    vsize: int
    raw_off: int
    raw_size: int
    flags: int

    @property
    def end(self) -> int:
        """Exclusive virtual end (``va + max(vsize, raw_size)``)."""
        return self.va + max(self.vsize, self.raw_size)

    @property
    def executable(self) -> bool:
        return bool(self.flags & (IMAGE_SCN_MEM_EXECUTE | IMAGE_SCN_CNT_CODE))

    def contains(self, va: int) -> bool:
        return self.va <= va < self.end

    def file_off(self, va: int) -> int | None:
        """File offset of ``va`` if it is backed by raw data, else ``None``."""
        rel = va - self.va
        if 0 <= rel < self.raw_size:
            return self.raw_off + rel
        return None


def _parse(buf: bytes) -> tuple[int, int, int, int, list[Section]]:
    if len(buf) < 0x40 or buf[:2] != b"MZ":
        raise PeError("missing MZ header")
    pe = struct.unpack_from("<I", buf, 0x3C)[0]
    if pe + 24 > len(buf) or buf[pe:pe + 4] != b"PE\0\0":
        raise PeError("missing PE signature")
    nsec = struct.unpack_from("<H", buf, pe + 6)[0]
    timestamp = struct.unpack_from("<I", buf, pe + 8)[0]
    optsz = struct.unpack_from("<H", buf, pe + 20)[0]
    opt = pe + 24
    if optsz < 96 or opt + optsz > len(buf):
        raise PeError("truncated optional header")
    magic = struct.unpack_from("<H", buf, opt)[0]
    if magic != 0x10B:
        raise PeError(f"not a PE32 image (optional header magic 0x{magic:x})")
    entry = struct.unpack_from("<I", buf, opt + 16)[0]
    base = struct.unpack_from("<I", buf, opt + 28)[0]
    checksum = struct.unpack_from("<I", buf, opt + 64)[0]
    off = opt + optsz
    secs: list[Section] = []
    for i in range(nsec):
        if off + 40 > len(buf):
            raise PeError("truncated section table")
        name = buf[off:off + 8].rstrip(b"\0").decode("ascii", "replace")
        vsize, va, rsize, roff = struct.unpack_from("<IIII", buf, off + 8)
        flags = struct.unpack_from("<I", buf, off + 36)[0]
        secs.append(Section(i, name, base + va, vsize, roff, rsize, flags))
        off += 40
    return base, entry + base, timestamp, checksum, secs


class PeImage:
    """A loaded PE32 image with VA-based reads (no relocation, ASLR is off for gta_sa.exe)."""

    def __init__(self, data: bytes, path: Path | None = None):
        self.data = data
        self.path = path
        self.image_base, self.entry, self.timestamp, self.checksum, self.sections = _parse(data)
        self._sha256: str | None = None

    @classmethod
    def open(cls, path: str | Path) -> "PeImage":
        from ..core.paths import open_ro

        p = Path(path)
        with open_ro(p) as f:
            return cls(f.read(), p)

    @property
    def sha256(self) -> str:
        if self._sha256 is None:
            self._sha256 = hashlib.sha256(self.data).hexdigest()
        return self._sha256

    def section_at(self, va: int) -> Section | None:
        for s in self.sections:
            if s.contains(va):
                return s
        return None

    def section(self, name: str, nth: int = 0) -> Section | None:
        """The ``nth`` section called ``name`` (gta_sa.exe has two ``.text`` and two ``.data``)."""
        k = 0
        for s in self.sections:
            if s.name == name:
                if k == nth:
                    return s
                k += 1
        return None

    def read(self, va: int, n: int) -> bytes:
        """Up to ``n`` raw bytes at ``va`` (shorter at the end of raw data, ``b''`` if unbacked)."""
        s = self.section_at(va)
        if s is None:
            return b""
        off = s.file_off(va)
        if off is None:
            return b""
        avail = s.raw_off + s.raw_size - off
        return self.data[off:off + max(0, min(n, avail))]

    def u32(self, va: int) -> int | None:
        b = self.read(va, 4)
        return struct.unpack("<I", b)[0] if len(b) == 4 else None

    def raw(self, s: Section) -> bytes:
        """Raw bytes of a section."""
        return self.data[s.raw_off:s.raw_off + s.raw_size]
