"""IMG archives (SPEC §4.2 ``satk.formats.img``). Stdlib only, read-only.

* v2 (SA): ``"VER2"``, ``u32 count``, then ``count`` x 32-byte entries ``<IHH24s>``:
  offset (sectors), ``Size`` (u16, streaming size in sectors), ``SizeInArchive`` (u16), name.
  NOT ``<II24s>`` (the DragonFF bug glues the two u16 into one u32).
* v1 (III/VC): sibling ``.dir`` with ``<II24s>`` entries, data in ``.img``.

The engine takes ``SizeInArchive`` when it is non-zero (``Streaming.cpp:1189``), so
:attr:`ImgEntry.size` does too. Unknown magic (compressed/encrypted IMGs of third-party tools)
-> ``FormatError``. The archive is never read into memory: only the directory, entries on demand.

Example::

    from satk.formats.img import ImgArchive
    with ImgArchive.open(profile_root("vanilla") / "models" / "gta3.img") as a:
        e = a.find("infernus.dff")
        data = a.read(e)
"""

from __future__ import annotations

import os
import struct
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from ..core.paths import open_ro
from .rw import FormatError

__all__ = ["SECTOR", "ImgEntry", "ImgArchive", "parse_ver2_directory", "parse_v1_directory"]

SECTOR = 2048
_E2 = struct.Struct("<IHH24s")
_E1 = struct.Struct("<II24s")
#: Sanity limit for the number of directory entries (the largest vanilla IMG has 16 316).
MAX_ENTRIES = 1 << 20


@dataclass(frozen=True, slots=True)
class ImgEntry:
    """One directory entry. ``name`` as stored; ``stem``/``ext`` for lookups (``ext`` lower-case)."""

    idx: int
    name: str
    stem: str
    ext: str
    offset_sectors: int
    stream_sectors: int
    archive_sectors: int

    @property
    def abs_offset(self) -> int:
        """Byte offset of the entry data in the archive."""
        return self.offset_sectors * SECTOR

    @property
    def size(self) -> int:
        """Bytes the engine reads: ``(archive_sectors or stream_sectors) * 2048``."""
        return (self.archive_sectors or self.stream_sectors) * SECTOR

    @property
    def key(self) -> str:
        """Lower-case name, the case-insensitive lookup key."""
        return self.name.lower()


def _decode_name(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("latin-1")


def _entry(idx: int, name: str, off: int, ssz: int, asz: int) -> ImgEntry:
    dot = name.rfind(".")
    stem, ext = (name[:dot], name[dot + 1:].lower()) if dot > 0 else (name, "")
    return ImgEntry(idx, name, stem, ext, off, ssz, asz)


def parse_ver2_directory(buf, file_size: int | None = None) -> list[ImgEntry]:
    """Parse a VER2 header + directory from ``buf`` (must start with ``VER2``).

    With ``file_size`` every entry must end inside the archive (``FormatError`` otherwise);
    :meth:`ImgArchive.open` does not check this, so one broken entry does not hide the rest
    (reading it raises instead)."""
    if len(buf) < 8 or bytes(buf[:4]) != b"VER2":
        raise FormatError("img", 0, "not a VER2 archive")
    (count,) = struct.unpack_from("<I", buf, 4)
    if count > MAX_ENTRIES:
        raise FormatError("img", 4, f"implausible entry count {count}")
    need = 8 + count * 32
    if len(buf) < need:
        raise FormatError("img", 8, f"directory truncated: {count} entries need {need} bytes, have {len(buf)}")
    out = []
    for i in range(count):
        off, ssz, asz, raw = _E2.unpack_from(buf, 8 + i * 32)
        out.append(_entry(i, _decode_name(raw), off, ssz, asz))
    _check_bounds(out, file_size)
    return out


def parse_v1_directory(buf, file_size: int | None = None) -> list[ImgEntry]:
    """Parse a v1 ``.dir`` file (``<II24s>`` entries)."""
    if len(buf) % 32:
        raise FormatError("img", len(buf) - len(buf) % 32, f".dir size {len(buf)} is not a multiple of 32")
    count = len(buf) // 32
    if count > MAX_ENTRIES:
        raise FormatError("img", 0, f"implausible entry count {count}")
    out = []
    for i in range(count):
        off, sz, raw = _E1.unpack_from(buf, i * 32)
        out.append(_entry(i, _decode_name(raw), off, sz, 0))
    _check_bounds(out, file_size)
    return out


def _check_bounds(entries: list[ImgEntry], file_size: int | None) -> None:
    if file_size is None:
        return
    for e in entries:
        if e.abs_offset + e.size > file_size:
            raise FormatError(
                "img", e.idx * 32 + 8,
                f"entry {e.idx} '{e.name}' ends at {e.abs_offset + e.size}, beyond archive size {file_size}",
            )


def _find_ci(path: Path, suffix: str) -> Path | None:
    """Sibling of ``path`` with another suffix, matched case-insensitively."""
    cand = path.with_suffix(suffix)
    if cand.is_file():
        return cand
    want = (path.stem + suffix).lower()
    try:
        for p in path.parent.iterdir():
            if p.name.lower() == want and p.is_file():
                return p
    except OSError:
        return None
    return None


class ImgArchive:
    """An open IMG archive. Use :meth:`open` (also a context manager).

    Attributes:
        version: 1 (``.dir`` + ``.img``) or 2 (``VER2``).
        path: the ``.img`` file.
        entries: directory entries in directory order.
        file_size: size of the ``.img`` in bytes.
    """

    version: int
    path: Path
    entries: list[ImgEntry]
    file_size: int

    def __init__(self, path: Path, version: int, entries: list[ImgEntry], fh: BinaryIO, file_size: int):
        self.path = path
        self.version = version
        self.entries = entries
        self.file_size = file_size
        self._fh: BinaryIO | None = fh
        self._lock = threading.Lock()
        self._by_name: dict[str, ImgEntry] | None = None

    # ---------------------------------------------------------------- construction
    @classmethod
    def open(cls, path: str | os.PathLike) -> "ImgArchive":  # noqa: A003 - SPEC name
        """Open ``path`` (an ``.img``; for v1 a ``.dir`` path works too) read-only."""
        p = Path(path)
        if p.suffix.lower() == ".dir":
            img = _find_ci(p, ".img") or _find_ci(p, ".IMG")
            if img is None:
                raise FormatError("img", 0, f"no .img next to {p.name}")
            p = img
        fh = open_ro(p)
        try:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(0)
            head = fh.read(8)
            if head[:4] == b"VER2":
                if len(head) < 8:
                    raise FormatError("img", 0, "truncated VER2 header")
                (count,) = struct.unpack_from("<I", head, 4)
                if count > MAX_ENTRIES or 8 + count * 32 > size:
                    raise FormatError("img", 4, f"implausible entry count {count} for a {size}-byte file")
                fh.seek(0)
                entries = parse_ver2_directory(fh.read(8 + count * 32))
                return cls(p, 2, entries, fh, size)
            d = _find_ci(p, ".dir")
            if d is not None:
                with open_ro(d) as df:
                    dsize = os.fstat(df.fileno()).st_size   # check before reading: a mod .dir can be anything
                    if dsize % 32 or dsize // 32 > MAX_ENTRIES:
                        raise FormatError("img", 0, f"{d.name}: size {dsize} is not a plausible .dir "
                                                    f"(multiple of 32, <= {MAX_ENTRIES} entries)")
                    entries = parse_v1_directory(df.read(dsize))
                return cls(p, 1, entries, fh, size)
            raise FormatError(
                "img", 0,
                f"unknown IMG magic {bytes(head[:4])!r} and no .dir file (compressed/encrypted archive?)",
            )
        except BaseException:
            fh.close()
            raise

    # ---------------------------------------------------------------- access
    def read(self, e: ImgEntry) -> bytes:
        """Bytes of an entry (``e.size`` bytes, sector padded)."""
        return self.read_at(e.abs_offset, e.size)

    def read_at(self, abs_off: int, size: int) -> bytes:
        """``size`` bytes at ``abs_off``; ``FormatError`` if outside the archive."""
        if abs_off < 0 or size < 0 or abs_off + size > self.file_size:
            raise FormatError("img", max(abs_off, 0), f"read of {size} bytes at {abs_off} outside archive ({self.file_size})")
        fh = self._fh
        if fh is None:
            raise ValueError("I/O on closed ImgArchive")
        with self._lock:
            fh.seek(abs_off)
            data = fh.read(size)
        if len(data) != size:
            raise FormatError("img", abs_off, f"short read: {len(data)} of {size} bytes")
        return data

    def by_name(self) -> dict[str, ImgEntry]:
        """``{lower-case name: entry}``; for duplicates inside one archive the first entry wins."""
        if self._by_name is None:
            d: dict[str, ImgEntry] = {}
            for e in self.entries:
                d.setdefault(e.name.lower(), e)
            self._by_name = d
        return self._by_name

    def find(self, name: str) -> ImgEntry | None:
        """Entry by file name, case-insensitive (``"Infernus.DFF"`` finds ``infernus.dff``)."""
        return self.by_name().get(name.lower())

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def __enter__(self) -> "ImgArchive":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __len__(self) -> int:
        return len(self.entries)

    def __repr__(self) -> str:
        return f"ImgArchive({str(self.path)!r}, v{self.version}, {len(self.entries)} entries)"
