"""Build new IMG archives and compare archives (``satk.rw.img``). Stdlib only.

:func:`build_img` always writes a **new** file (temp file + ``os.replace``) and never opens an
existing archive for writing. The layout is computed before the first byte is written, so the
regressions INU_Core listed in its ``BUGS.md`` (2026-09-27) cannot happen here:

1. the VER2 directory growing over the first file: the data starts at the first sector after the
   *whole* directory (``8 + 32 * N`` bytes, rounded up to 2048);
2. ``rebuild`` breaking archives of 64+ entries: the same rule for every entry count;
3. VER1 (III/VC) archives getting a VER2 header: ``version=1`` writes ``.img`` + ``.dir`` and no header;
4. the size field read as one ``u32``: entries are ``<IHH24s>`` (streaming size, archive size = 0, as in
   Rockstar's archives); more than 65 535 sectors (128 MB) in one VER2 entry is refused;
6. names of 24+ characters without the terminating NUL: names are 1..23 printable ASCII characters,
   duplicates (case-insensitive) are refused. reVC skips names whose dot is after the 20th character;
   that is a warning for ``version=1``.

:func:`diff_img` compares two archives by entry name (case-insensitive) and content (trailing zero
padding ignored).
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
import struct
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from ..core.paths import ensure_writable, open_ro
from ..formats.img import SECTOR, ImgArchive

__all__ = ["ImgBuildError", "Source", "check_name", "dir_sources", "base_sources", "plan_layout", "build_img",
           "diff_img", "MAX_V2_SECTORS"]

MAX_V2_SECTORS = 0xFFFF


class ImgBuildError(ValueError):
    """An archive that cannot be written (bad name, duplicate, size limit)."""


@dataclass
class Source:
    """One entry to write: ``name`` as stored, ``size`` in bytes, ``read()`` returns the data."""

    name: str
    size: int
    read: Callable[[], bytes]
    origin: str = ""

    @property
    def sectors(self) -> int:
        return (self.size + SECTOR - 1) // SECTOR


def check_name(name: str, version: int = 2) -> list[str]:
    """Raise :class:`ImgBuildError` for names the games cannot store; return warnings."""
    if not name or len(name) > 23:
        raise ImgBuildError(f"entry name {name!r}: 1..23 characters (the field is 24 bytes with the NUL)")
    if not all(0x20 < ord(c) < 0x7F for c in name) or any(c in name for c in "/\\"):
        raise ImgBuildError(f"entry name {name!r}: printable ASCII without spaces or slashes only")
    warn = []
    dot = name.rfind(".")
    if version == 1 and dot > 20:
        warn.append(f"LONG_NAME: {name}: Vice City skips entries whose extension starts after character 20")
    if dot <= 0:
        warn.append(f"NO_EXTENSION: {name}: the streaming code finds files by extension (dff/txd/col/ipl/ifp/...)")
    return warn


#: Files never taken from a source folder (Explorer and editor litter).
SKIP_FILES = frozenset({"thumbs.db", "desktop.ini", ".ds_store"})


def dir_sources(folder: Path, *, recursive: bool = False) -> list[Source]:
    """Files of ``folder`` (sorted by lower-case name); subfolders only with ``recursive``.

    Hidden dot-files, ``Thumbs.db`` and ``desktop.ini`` are skipped."""
    if not folder.is_dir():
        raise ImgBuildError(f"not a folder: {folder}")
    files = sorted((p for p in (folder.rglob("*") if recursive else folder.iterdir())
                    if p.is_file() and not p.name.startswith(".") and p.name.lower() not in SKIP_FILES),
                   key=lambda p: (p.name.lower(), str(p)))
    out = []
    for p in files:
        size = p.stat().st_size

        def read(p: Path = p) -> bytes:
            with open_ro(p) as f:
                return f.read()

        out.append(Source(p.name, size, read, f"file:{p}"))
    return out


def base_sources(arc: ImgArchive, include: Iterable[str] = ()) -> list[Source]:
    """Entries of an open archive in directory order (``include``: fnmatch patterns, case-insensitive)."""
    pats = [p.lower() for p in include]
    out = []
    for e in arc.entries:
        if pats and not any(fnmatch.fnmatchcase(e.name.lower(), p) for p in pats):
            continue
        out.append(Source(e.name, e.size, lambda e=e: arc.read(e), f"base:{e.idx}"))
    return out


def plan_layout(sources: list[Source], version: int = 2) -> tuple[int, list[int]]:
    """``(first data sector, offsets in sectors)``; the directory never overlaps data."""
    if version == 2:
        first = (8 + 32 * len(sources) + SECTOR - 1) // SECTOR
    else:
        first = 0
    offs = []
    cur = first
    for s in sources:
        offs.append(cur)
        cur += s.sectors
    return first, offs


def _validate(sources: list[Source], version: int) -> list[str]:
    warn: list[str] = []
    seen: dict[str, str] = {}
    for s in sources:
        warn += check_name(s.name, version)
        k = s.name.lower()
        if k in seen:
            raise ImgBuildError(f"duplicate entry name {s.name!r} ({seen[k]} and {s.origin})")
        seen[k] = s.origin
        if version == 2 and s.sectors > MAX_V2_SECTORS:
            raise ImgBuildError(f"{s.name}: {s.size} bytes = {s.sectors} sectors, VER2 stores at most {MAX_V2_SECTORS}")
    return warn


def _atomic_stream(path: Path, write: Callable[[object], None]) -> None:
    p = ensure_writable(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".tmp", dir=p.parent)
    tmp = Path(name)
    try:
        with os.fdopen(fd, "wb") as f:
            write(f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    finally:
        tmp.unlink(missing_ok=True)


def build_img(out: Path, sources: list[Source], *, version: int = 2) -> dict:
    """Write ``sources`` into the new archive ``out`` (VER2, or VER1 = ``out`` + ``out.dir``)."""
    if version not in (1, 2):
        raise ImgBuildError("version must be 1 (III/VC .img + .dir) or 2 (SA VER2)")
    warn = _validate(sources, version)
    first, offs = plan_layout(sources, version)
    directory = bytearray()
    for s, o in zip(sources, offs):
        nm = s.name.encode("ascii").ljust(24, b"\0")
        directory += struct.pack("<IHH24s", o, s.sectors, 0, nm) if version == 2 else struct.pack("<II24s", o, s.sectors, nm)
    digest = hashlib.blake2b(digest_size=16)

    def write_img(f) -> None:
        pos = 0
        if version == 2:
            head = b"VER2" + struct.pack("<I", len(sources)) + bytes(directory)
            head += b"\0" * (first * SECTOR - len(head))
            f.write(head)
            digest.update(head)
            pos = first * SECTOR
        for s, o in zip(sources, offs):
            data = s.read()
            if len(data) != s.size:
                raise ImgBuildError(f"{s.name}: size changed while building ({s.size} -> {len(data)})")
            assert pos == o * SECTOR
            blob = data + b"\0" * (s.sectors * SECTOR - len(data))
            f.write(blob)
            digest.update(blob)
            pos += len(blob)

    _atomic_stream(out, write_img)
    res = {"path": out, "version": version, "entries": len(sources), "first_data_sector": first,
           "size": (first + sum(s.sectors for s in sources)) * SECTOR, "blake2b": digest.hexdigest()}
    if version == 1:
        dpath = out.with_suffix(".dir")
        _atomic_stream(dpath, lambda f: f.write(bytes(directory)))
        res["dir"] = dpath
    if warn:
        res["warn"] = warn
    return res


def _content_hash(data: bytes) -> bytes:
    return hashlib.blake2b(data.rstrip(b"\0"), digest_size=16).digest()


def diff_img(a: ImgArchive, b: ImgArchive, *, content: bool = True) -> tuple[dict, list[list]]:
    """``(counts, rows)``; rows ``[name, change, size_a, size_b]`` sorted by lower-case name.

    ``change``: ``added`` (only in B), ``removed`` (only in A), ``changed`` (content differs; trailing
    zero padding ignored), ``same`` rows are not listed. ``content=False`` compares sizes only.
    """
    da, db = a.by_name(), b.by_name()
    counts = {"same": 0, "changed": 0, "added": 0, "removed": 0}
    rows: list[list] = []
    for k in sorted(set(da) | set(db)):
        ea, eb = da.get(k), db.get(k)
        if ea is None:
            counts["added"] += 1
            rows.append([eb.name, "added", None, eb.size])
            continue
        if eb is None:
            counts["removed"] += 1
            rows.append([ea.name, "removed", ea.size, None])
            continue
        if content:
            same = _content_hash(a.read(ea)) == _content_hash(b.read(eb))
        else:
            same = ea.size == eb.size
        if same:
            counts["same"] += 1
        else:
            counts["changed"] += 1
            rows.append([ea.name, "changed", ea.size, eb.size])
    dup_a, dup_b = len(a.entries) - len(da), len(b.entries) - len(db)
    if dup_a or dup_b:
        counts["duplicates_a"], counts["duplicates_b"] = dup_a, dup_b
    return counts, rows
