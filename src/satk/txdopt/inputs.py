"""Inputs of ``satk.txdopt`` as one bundle of files, opened read-only.

A target is one of:

* a TXD of the game: ``txd:<name>`` (index), ``file:<relpath>``, ``<img>/<entry>`` (``models/gta3.img/bistro.txd``);
* a ``.txd`` file, a mod folder, a ``.zip`` (read in place, never extracted) or an ``.img`` (its entries), given
  as an absolute path, relative to the current folder, or relative to the profile root (``models/gta3.img``,
  ``modloader/mymod``).

Folders, zips and IMGs are read through :func:`satk.modinspect.source.open_mod` (Mod Loader's file order, IMG
entries as ``<img>/<entry>``); game TXDs through :func:`satk.texmod.api.load_txd`. Stdlib only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..core.errors import SatkError
from ..core.paths import jpath

__all__ = ["SECTOR", "Item", "Bundle", "load_bundle", "stream_size"]

SECTOR = 2048
#: Largest single file read into memory (a 16-bit IMG entry holds at most 128 MiB).
MAX_READ = 1 << 30


def stream_size(n: int) -> int:
    """Bytes the streamer accounts for a file of ``n`` bytes (whole 2048-byte sectors)."""
    return (int(n) + SECTOR - 1) // SECTOR * SECTOR


@dataclass(frozen=True)
class Item:
    """One file of a bundle. ``rel`` uses forward slashes; IMG entries are ``<img rel>/<entry>``."""

    rel: str
    size: int
    container: str | None = None
    reader: Callable[[], bytes] | None = field(default=None, repr=False, compare=False)

    @property
    def name(self) -> str:
        return self.rel.rsplit("/", 1)[-1]

    @property
    def ext(self) -> str:
        n = self.name.lower()
        return n.rsplit(".", 1)[-1] if "." in n else ""

    @property
    def stem(self) -> str:
        """Lower-case name without the extension (the game's key for DFF/TXD)."""
        n = self.name.lower()
        return n.rsplit(".", 1)[0] if "." in n else n

    def read(self) -> bytes:
        if self.reader is None:
            raise SatkError("UNSUPPORTED", f"{self.rel}: contents are not readable here (an IMG inside a .zip?)",
                            hint="extract the archive and pass the folder")
        if self.size > MAX_READ:
            raise SatkError("UNSUPPORTED", f"{self.rel}: {self.size} bytes is too large to read")
        return self.reader()


@dataclass
class Bundle:
    """Files of one target. ``kind``: ``game`` (one TXD of the game), ``file``, ``dir``, ``zip`` or ``img``."""

    kind: str
    name: str
    label: str
    path: Path | None
    items: list[Item]
    warn: list[str] = field(default_factory=list)
    _close: Callable[[], None] | None = field(default=None, repr=False)

    def of(self, *exts: str) -> list[Item]:
        """Items with one of the extensions (``of("txd")``); IMG files themselves are never TXD/DFF."""
        want = {e.lower() for e in exts}
        return [i for i in self.items if i.ext in want]

    def close(self) -> None:
        if self._close is not None:
            try:
                self._close()
            finally:
                self._close = None

    def __enter__(self) -> "Bundle":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _profile_root(profile: str) -> Path | None:
    from ..core.paths import profile_root

    try:
        return profile_root(profile)
    except SatkError:
        return None


def _find(target: str, profile: str) -> Path | None:
    p = Path(target)
    if p.is_absolute():
        return p if p.exists() else None
    if (Path.cwd() / p).exists():
        return Path(os.path.abspath(Path.cwd() / p))
    root = _profile_root(profile)
    if root is not None:
        from ..formats.dat import resolve_ci

        hit = resolve_ci(root, target.replace("/", "\\"))
        if hit is not None and hit.exists():
            return hit
    return None


def _game_txd(target: str, profile: str) -> Bundle:
    from ..texmod.api import load_txd

    src = load_txd(target, profile)
    data = src.data
    item = Item(src.file, len(data), None, lambda: data)
    return Bundle("game", src.stem.lower(), src.label, None, [item])


def load_bundle(target: str, profile: str = "vanilla") -> Bundle:
    """Open ``target`` (see the module docstring); ``NOT_FOUND`` with a hint when it does not exist."""
    s = str(target or "").strip().strip('"')
    if not s:
        raise SatkError("BAD_PARAMS", "no target given", hint="satk texture audit txd:bistro (or a mod folder)")
    low = s.lower().replace("\\", "/")
    if low.startswith(("txd:", "file:")):
        return _game_txd(s, profile)
    p = _find(s, profile)
    if p is None and ".img/" in low:
        return _game_txd(s, profile)
    if p is None:
        root = _profile_root(profile)
        raise SatkError("NOT_FOUND", f"no such file or folder: {s}",
                        hint="a mod folder, .zip, .img or .txd (absolute, relative to the current folder or to the "
                             f"{profile} root), txd:<name> or models/gta3.img/<name>.txd",
                        data={"profile_root": jpath(root)} if root else None)
    from ..modinspect.source import open_mod

    src = open_mod(p)
    items = [Item(f.rel, f.size, f.container, f.reader) for f in src.files]
    label = jpath(p)
    root = _profile_root(profile)
    if root is not None:
        try:
            rel = os.path.relpath(p, root)
            if not rel.startswith(".."):
                label = rel.replace("\\", "/")
        except ValueError:
            pass
    name = p.stem if p.is_file() else p.name
    return Bundle(src.kind, name.lower() if src.kind == "file" else name, label, p, items, list(src.warnings),
                  src.close)
