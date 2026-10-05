"""A mod as a list of files, read in place: a folder, a ``.zip`` (never extracted) or an ``.img``.

``open_mod(path)`` returns a :class:`ModSource` whose :attr:`ModSource.files` are :class:`ModFile`
records sorted the way Mod Loader walks a mod folder. File bytes are read lazily, only for the
files that need parsing (data files, readmes, IMG directories); DFF/TXD payloads are never read.

* Names that start with ``.`` (files or folders) are skipped, like Mod Loader's ``FilesWalk``.
* An ``.img`` inside the mod is expanded: its directory entries become files ``<img>/<entry>``
  (``ModFile.container`` = the IMG). For a zip only the IMG directory bytes are read.
* A single file (``handling.cfg``, ``infernus.dff``, ``readme.txt``) is a one-file mod.

Stdlib only.
"""

from __future__ import annotations

import os
import struct
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..core.errors import SatkError
from ..core.paths import jpath, open_ro

__all__ = ["ModFile", "ModSource", "open_mod", "mod_sort_key", "MAX_TEXT_BYTES"]

#: Text files (data files, readmes) larger than this are not parsed (Mod Loader reads readmes <= 60000 bytes).
MAX_TEXT_BYTES = 16 * 1024 * 1024
#: Upper bound of IMG directory entries (same plausibility limit as ``satk.formats.img``).
_MAX_IMG_ENTRIES = 1 << 20
_SECTOR = 2048


def mod_sort_key(rel: str) -> str:
    """Mod Loader keeps a mod's files in a ``std::map`` keyed by the normalized path (lower case, backslashes)."""
    return rel.lower().replace("/", "\\")


@dataclass(frozen=True, slots=True)
class ModFile:
    """One file of a mod.

    Attributes:
        rel: path inside the mod, forward slashes, original case (``data/handling.cfg``,
            ``cars.img/infernus.dff`` for an IMG entry).
        size: bytes (IMG entries: directory size).
        container: for IMG entries, ``rel`` of the IMG they come from.
    """

    rel: str
    size: int
    container: str | None = None
    reader: Callable[[], bytes] | None = field(default=None, repr=False, compare=False)

    @property
    def name(self) -> str:
        return self.rel.rsplit("/", 1)[-1]

    @property
    def lname(self) -> str:
        return self.name.lower()

    @property
    def ext(self) -> str:
        n = self.lname
        return n.rsplit(".", 1)[-1] if "." in n else ""

    def read(self, limit: int = MAX_TEXT_BYTES) -> bytes:
        """File bytes; ``UNSUPPORTED`` when larger than ``limit`` or not readable (IMG entries)."""
        if self.reader is None:
            raise SatkError("UNSUPPORTED", f"{self.rel}: contents are not readable here")
        if self.size > limit:
            raise SatkError("UNSUPPORTED", f"{self.rel}: {self.size} bytes is larger than the {limit}-byte limit")
        return self.reader()

    def text(self, limit: int = MAX_TEXT_BYTES) -> str:
        """Contents decoded as latin-1 (the game reads bytes)."""
        return self.read(limit).decode("latin-1")


@dataclass
class ModSource:
    """A mod opened for inspection.

    Attributes:
        path: what was opened.
        kind: ``dir`` | ``zip`` | ``img`` | ``file``.
        name: mod name (folder name, zip/IMG stem, or the single top folder of a zip).
        files: files in Mod Loader order (:func:`mod_sort_key`).
        warnings: ``CODE: text`` strings (unreadable entries, skipped names ...).
    """

    path: Path
    kind: str
    name: str
    files: list[ModFile]
    warnings: list[str] = field(default_factory=list)
    _closers: list[Callable[[], None]] = field(default_factory=list, repr=False)

    def close(self) -> None:
        for c in self._closers:
            try:
                c()
            except OSError:  # pragma: no cover
                pass
        self._closers.clear()

    def __enter__(self) -> "ModSource":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# --------------------------------------------------------------------------- IMG directories


def _img_entries(head: bytes, total: int | None, read_dir: Callable[[], bytes] | None) -> list[tuple[str, int, int]]:
    """``(name, size, byte offset)`` of IMG entries from the first bytes of a VER2 archive or a v1 ``.dir`` reader."""
    if head[:4] == b"VER2":
        if len(head) < 8:
            raise ValueError("truncated VER2 header")
        (count,) = struct.unpack_from("<I", head, 4)
        if count > _MAX_IMG_ENTRIES or (total is not None and 8 + 32 * count > total):
            raise ValueError(f"implausible entry count {count}")
        if len(head) < 8 + 32 * count:
            raise ValueError("short IMG directory")
        out = []
        for i in range(count):
            off, ssz, asz, raw = struct.unpack_from("<IHH24s", head, 8 + 32 * i)
            name = raw.split(b"\0", 1)[0].decode("latin-1")
            out.append((name, (asz or ssz) * _SECTOR, off * _SECTOR))
        return out
    if read_dir is None:
        raise ValueError(f"unknown IMG magic {head[:4]!r} and no .dir file")
    d = read_dir()
    if len(d) % 32 or len(d) // 32 > _MAX_IMG_ENTRIES:
        raise ValueError(f".dir size {len(d)} is not a multiple of 32")
    out = []
    for i in range(len(d) // 32):
        off, size, raw = struct.unpack_from("<II24s", d, 32 * i)
        out.append((raw.split(b"\0", 1)[0].decode("latin-1"), size * _SECTOR, off * _SECTOR))
    return out


def _expand_img(img: ModFile, head: bytes, total: int | None, read_dir, warnings: list[str],
                path: Path | None = None) -> list[ModFile]:
    """Entries of an IMG as files ``<img>/<entry>``; readable when the IMG is a file on disk (``path``)."""
    try:
        ents = _img_entries(head, total, read_dir)
    except (ValueError, struct.error, OSError) as e:
        warnings.append(f"BAD_IMG: {img.rel}: {e}")
        return []

    def reader(off: int, size: int) -> Callable[[], bytes]:
        def read() -> bytes:
            with open_ro(path) as fh:
                fh.seek(off)
                return fh.read(size)
        return read

    return [ModFile(f"{img.rel}/{n}", s, img.rel, reader(o, s) if path is not None else None)
            for n, s, o in ents if n]


# --------------------------------------------------------------------------- folders


def _from_dir(root: Path) -> ModSource:
    files: list[ModFile] = []
    warnings: list[str] = []
    skipped = 0
    for dp, dn, fn in os.walk(root):
        keep = [d for d in dn if not d.startswith(".")]
        skipped += len(dn) - len(keep)
        dn[:] = sorted(keep)
        for f in sorted(fn):
            if f.startswith("."):
                skipped += 1
                continue
            full = Path(dp) / f
            rel = Path(os.path.relpath(full, root)).as_posix()
            try:
                size = full.stat().st_size
            except OSError as e:
                warnings.append(f"UNREADABLE: {rel}: {e}")
                continue

            def reader(p: Path = full) -> bytes:
                with open_ro(p) as fh:
                    return fh.read()

            mf = ModFile(rel, size, None, reader)
            files.append(mf)
            if mf.ext == "img":
                files += _dir_img(full, mf, warnings)
    if skipped:
        warnings.append(f"SKIPPED: {skipped} name(s) starting with '.' (Mod Loader ignores them)")
    return ModSource(root, "dir", root.name, sorted(files, key=lambda m: mod_sort_key(m.rel)), warnings)


def _dir_img(full: Path, mf: ModFile, warnings: list[str]) -> list[ModFile]:
    try:
        with open_ro(full) as fh:
            head = fh.read(8)
            if head[:4] == b"VER2" and len(head) == 8:
                (count,) = struct.unpack_from("<I", head, 4)
                head += fh.read(32 * min(count, _MAX_IMG_ENTRIES + 1))
    except OSError as e:
        warnings.append(f"UNREADABLE: {mf.rel}: {e}")
        return []
    dir_path = full.with_suffix(".dir")
    if not dir_path.exists():
        dir_path = next((p for p in full.parent.iterdir() if p.name.lower() == full.stem.lower() + ".dir"), dir_path)

    def read_dir() -> bytes:
        with open_ro(dir_path) as fh:
            return fh.read(32 * (_MAX_IMG_ENTRIES + 1))

    return _expand_img(mf, head, mf.size, read_dir if dir_path.exists() else None, warnings, full)


# --------------------------------------------------------------------------- zip archives


def _zip_name(info: zipfile.ZipInfo) -> str | None:
    """Normalized member name or ``None`` for directories, absolute/``..`` names and dot names."""
    n = info.filename.replace("\\", "/")
    if n.endswith("/"):
        return None
    parts = [p for p in n.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." or ":" in p for p in parts):
        return None
    return "/".join(parts)


def _from_zip(path: Path) -> ModSource:
    fh = open_ro(path)
    try:
        zf = zipfile.ZipFile(fh)
    except (zipfile.BadZipFile, OSError) as e:
        fh.close()
        raise SatkError("UNSUPPORTED", f"{jpath(path)}: not a readable zip archive ({e})",
                        hint="extract it with an archiver (7z/rar are not read) and inspect the folder") from None
    warnings: list[str] = []
    members: list[tuple[str, zipfile.ZipInfo]] = []
    skipped = 0
    for info in zf.infolist():
        n = _zip_name(info)
        if n is None:
            continue
        if any(p.startswith(".") for p in n.split("/")):
            skipped += 1
            continue
        members.append((n, info))
    # a single top folder is the mod folder (the usual "MyMod/..." layout)
    tops = {n.split("/", 1)[0] for n, _ in members}
    name = path.stem
    strip = ""
    if len(tops) == 1 and all("/" in n for n, _ in members):
        name = strip = next(iter(tops))
    if strip.lower() == "modloader" or any(n.lower().startswith("modloader/") for n, _ in members):
        warnings.append("NESTED: the archive contains a modloader/ folder; all its mods are inspected as one")
    files: list[ModFile] = []
    by_lower = {n.lower(): info for n, info in members}
    for n, info in members:
        rel = n[len(strip) + 1:] if strip else n
        if info.flag_bits & 0x1:
            warnings.append(f"ENCRYPTED: {n}: password-protected entry, not read")
            files.append(ModFile(rel, info.file_size, None, None))
            continue

        def reader(i: zipfile.ZipInfo = info) -> bytes:
            try:
                return zf.read(i)
            except (zipfile.BadZipFile, NotImplementedError, RuntimeError, OSError) as e:
                raise SatkError("UNSUPPORTED", f"zip entry {i.filename}: {e}") from None

        mf = ModFile(rel, info.file_size, None, reader)
        files.append(mf)
        if mf.ext == "img":
            try:
                with zf.open(info) as s:
                    head = s.read(8)
                    if head[:4] == b"VER2" and len(head) == 8:
                        (count,) = struct.unpack_from("<I", head, 4)
                        head += s.read(32 * min(count, _MAX_IMG_ENTRIES + 1))
            except (zipfile.BadZipFile, NotImplementedError, RuntimeError, OSError) as e:
                warnings.append(f"UNREADABLE: {n}: {e}")
                continue
            dir_info = by_lower.get(n.lower()[:-4] + ".dir")
            files += _expand_img(mf, head, info.file_size,
                                 (lambda di=dir_info: zf.read(di)) if dir_info is not None else None, warnings)
    if skipped:
        warnings.append(f"SKIPPED: {skipped} name(s) starting with '.' (Mod Loader ignores them)")
    src = ModSource(path, "zip", name, sorted(files, key=lambda m: mod_sort_key(m.rel)), warnings)
    src._closers += [zf.close, fh.close]
    return src


# --------------------------------------------------------------------------- entry point


def open_mod(path: str | os.PathLike) -> ModSource:
    """Open a mod for reading: folder, ``.zip``, ``.img`` (its entries) or a single file.

    Raises:
        SatkError: ``NOT_FOUND`` if the path does not exist; ``UNSUPPORTED`` for unreadable archives.
    """
    p = Path(os.path.abspath(os.fspath(path)))
    if p.is_dir():
        return _from_dir(p)
    if not p.is_file():
        raise SatkError("NOT_FOUND", f"no such file or folder: {jpath(p)}",
                        hint="give a mod folder, a .zip, an .img or a single mod file")
    if p.suffix.lower() == ".zip":
        return _from_zip(p)
    size = p.stat().st_size

    def reader(q: Path = p) -> bytes:
        with open_ro(q) as fh:
            return fh.read()

    if p.suffix.lower() == ".img":
        warnings: list[str] = []
        img = ModFile(p.name, size, None, reader)
        ents = _dir_img(p, img, warnings)
        # the IMG itself is the mod: its entries are the files (as if dropped into a mod folder)
        files = [ModFile(e.rel.split("/", 1)[1], e.size, p.name, e.reader) for e in ents]
        return ModSource(p, "img", p.stem, sorted(files, key=lambda m: mod_sort_key(m.rel)), warnings)
    return ModSource(p, "file", p.stem, [ModFile(p.name, size, None, reader)])
