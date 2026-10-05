"""Level DAT files (``default.dat``, ``gta.dat``, SA-MP ``*.two``) and case-insensitive paths.

SPEC §4.2 ``satk.formats.dat``. Stdlib only.

``gta.dat`` uses backslashes and a "foreign" case (``DATA\\MAPS\\LA\\LAwn.IDE`` vs ``LaWn.ide``
on disk), so files are found with :func:`resolve_ci` (pitfall #12).

Example::

    lines = parse_dat(read_text(root / "data" / "gta.dat"))
    ides = [resolve_ci(root, l.path) for l in lines if l.key == "IDE"]
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..core.paths import open_ro

__all__ = ["DatLine", "parse_dat", "resolve_ci", "read_text", "split_dos_path", "canon_relpath"]


@dataclass(frozen=True, slots=True)
class DatLine:
    """One directive. ``key`` upper-case (``IMG``, ``IDE``, ``IPL``, ``COLFILE``, ``TEXDICTION``,
    ``MODELFILE``, ``HIERFILE``, ``SPLASH``, ...), ``arg`` = rest of the line, ``line`` 1-based."""

    key: str
    arg: str
    line: int

    @property
    def path(self) -> str:
        """The file argument: ``COLFILE 0 MODELS\\COLL\\WEAPONS.COL`` -> ``MODELS\\COLL\\WEAPONS.COL``."""
        if self.key == "COLFILE":
            parts = self.arg.split(None, 1)
            return parts[1].strip() if len(parts) == 2 else self.arg
        return self.arg


def read_text(path: str | os.PathLike) -> str:
    """Read a game text file (always ``"rb"`` via ``open_ro``) and decode it as latin-1."""
    with open_ro(path) as f:
        return f.read().decode("latin-1")


def parse_dat(text: str) -> list[DatLine]:
    """Directives of a DAT file; ``#`` comments and blank lines are skipped."""
    out: list[DatLine] = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split(None, 1)
        out.append(DatLine(parts[0].upper(), parts[1].strip() if len(parts) > 1 else "", n))
    return out


#: Characters that cannot appear in a Windows file name (``:`` would also start a drive or an ADS).
_BAD_CHARS = frozenset('<>:"|?*') | frozenset(chr(i) for i in range(32))
#: DOS device names (with or without an extension): ``data\NUL`` opens a device, not a file.
_DEVICES = frozenset({"con", "prn", "aux", "nul", "conin$", "conout$"}
                     | {f"{p}{i}" for p in ("com", "lpt") for i in "0123456789¹²³"})


def _plain_component(p: str) -> bool:
    """A file/directory name that stays inside its parent on every OS: no drive, ``..``, ADS or device."""
    if not p.rstrip(" ."):                       # "..", "...", ". ." (Win32 trims trailing dots/spaces)
        return False
    if not _BAD_CHARS.isdisjoint(p):
        return False
    return p.split(".", 1)[0].rstrip(" ").lower() not in _DEVICES


def split_dos_path(dos_path: str) -> list[str] | None:
    """``DATA\\MAPS\\x.IDE`` -> ``['DATA', 'MAPS', 'x.IDE']``.

    ``None`` for paths that could leave the directory they are resolved against: absolute paths
    (leading slash, drive, UNC), ``..`` (also ``...``/``. .``), a drive or ``:`` in *any* component
    (``data\\C:\\x`` would make ``Path.joinpath`` jump to drive C:), characters invalid in Windows names
    and DOS device names (``NUL``, ``CON.txt``, ``COM1`` ...).
    """
    s = dos_path.strip().strip('"').replace("\\", "/")
    if not s or s.startswith("/"):
        return None
    parts = [p for p in s.split("/") if p not in ("", ".")]
    if not parts or not all(map(_plain_component, parts)):
        return None
    return parts


def canon_relpath(dos_path: str) -> str:
    """Canonical relpath used in SIDs and the index: lower-case, forward slashes."""
    parts = split_dos_path(dos_path)
    return "/".join(parts).lower() if parts else dos_path.strip().replace("\\", "/").lower()


_dir_cache: dict[str, dict[str, str]] = {}


def _listing(d: Path) -> dict[str, str]:
    key = os.path.normcase(os.path.abspath(d))
    lst = _dir_cache.get(key)
    if lst is None:
        try:
            names = os.listdir(d)
        except OSError:
            names = []
        lst = {}
        for n in names:
            lst.setdefault(n.lower(), n)
        if len(_dir_cache) > 4096:
            _dir_cache.clear()
        _dir_cache[key] = lst
    return lst


def resolve_ci(root: Path, dos_path: str) -> Path | None:
    """Find ``dos_path`` (backslashes, any case) under ``root``; ``None`` if missing.

    The exact path is tried first; otherwise each component is matched case-insensitively
    against the directory listing (cached per process). Paths that could escape ``root``
    (see :func:`split_dos_path`: absolute, ``..``, a drive in any component, devices) -> ``None``,
    so a hit is always ``root`` joined with plain names (links inside ``root`` are followed).
    """
    parts = split_dos_path(dos_path)
    if parts is None:
        return None
    root = Path(root)
    direct = root.joinpath(*parts)
    if direct.exists():
        return direct
    cur = root
    for part in parts:
        hit = _listing(cur).get(part.lower())
        if hit is None:
            # the cached listing may be stale (file created later): retry once uncached
            _dir_cache.pop(os.path.normcase(os.path.abspath(cur)), None)
            hit = _listing(cur).get(part.lower())
            if hit is None:
                return None
        cur = cur / hit
    return cur if cur.exists() else None
