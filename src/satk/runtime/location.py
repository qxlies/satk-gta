"""Where the workspace and the game live: checks shared by ``satk init`` and ``satk doctor`` (M3 A2).

* the workspace inside a game folder (or the game inside ``<workspace>/work``) is refused: satk
  never writes into the game and its index/caches would land there;
* a workspace in OneDrive or Program Files gets a warning (sync of gigabytes of caches, locked
  files; admin rights and VirtualStore redirection);
* a game in OneDrive gets a warning (online-only placeholders), a game in Program Files with a
  VirtualStore copy too (the game then sees other files than satk);
* less than :data:`MIN_FREE_GB` free on the workspace drive gets a warning.

All functions take the environment as a dict so tests can fake Windows folders. stdlib only.
"""

from __future__ import annotations

import os
from pathlib import Path

from satk.core import detect
from satk.core.paths import jpath

from .sysinfo import drive_of, free_gb

__all__ = ["MIN_FREE_GB", "inside", "game_folder_above", "onedrive_root", "program_files_root", "virtualstore_of",
           "workspace_error", "workspace_warnings", "game_warnings"]

#: Below this many GB free on the workspace drive ``init``/``doctor`` warn (an index needs ~0.1-1 GB).
MIN_FREE_GB = 1.0


def _norm(p: str | os.PathLike) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(p)))


def inside(child: str | os.PathLike, root: str | os.PathLike) -> bool:
    """``child`` is ``root`` or below it (case-insensitive on Windows, no symlink resolution)."""
    c, r = _norm(child), _norm(root)
    try:
        return os.path.commonpath([c, r]) == r
    except ValueError:  # different drives
        return False


def game_folder_above(path: str | os.PathLike) -> Path | None:
    """The nearest folder at or above ``path`` that is a game folder (any edition), else ``None``."""
    p = Path(os.path.abspath(os.fspath(path)))
    for cand in (p, *p.parents):
        try:
            if cand.is_dir() and detect.edition(cand) is not None:
                return cand
        except OSError:
            continue
    return None


def onedrive_root(path: str | os.PathLike, env: dict[str, str] | None = None) -> Path | None:
    """The OneDrive folder ``path`` is in: ``%OneDrive%``/``%OneDriveConsumer%``/``%OneDriveCommercial%``,
    or a path component named ``OneDrive`` / ``OneDrive - <organisation>``."""
    env = os.environ if env is None else env
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        v = env.get(var)
        if v and inside(path, v):
            return Path(v)
    p = Path(os.path.abspath(os.fspath(path)))
    for cand in (p, *p.parents):
        n = cand.name.lower()
        if n == "onedrive" or n.startswith("onedrive - "):
            return cand
    return None


def program_files_root(path: str | os.PathLike, env: dict[str, str] | None = None) -> Path | None:
    """``%ProgramFiles%`` / ``%ProgramFiles(x86)%`` / ``%ProgramW6432%`` that holds ``path``, else ``None``."""
    env = os.environ if env is None else env
    for var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        v = env.get(var)
        if v and inside(path, v):
            return Path(v)
    return None


def virtualstore_of(path: str | os.PathLike, env: dict[str, str] | None = None) -> Path | None:
    """``%LOCALAPPDATA%\\VirtualStore\\<path without drive>`` when that folder exists (UAC redirection
    of writes into Program Files by programs without a manifest, e.g. old mod installers)."""
    env = os.environ if env is None else env
    la = env.get("LOCALAPPDATA")
    if not la or program_files_root(path, env) is None:
        return None
    drive, rest = os.path.splitdrive(os.path.abspath(os.fspath(path)))
    vs = Path(la) / "VirtualStore" / rest.lstrip("\\/")
    try:
        return vs if vs.is_dir() and any(vs.iterdir()) else None
    except OSError:
        return None


def workspace_error(ws: str | os.PathLike, game: str | os.PathLike | None = None) -> tuple[str, str] | None:
    """``(msg, hint)`` when the workspace may not be used: inside a game folder, or the game inside
    ``<workspace>/work``; ``None`` when the location is fine."""
    ws = Path(os.path.abspath(os.fspath(ws)))
    above = game_folder_above(ws)
    if game is not None and inside(ws, game):
        above = Path(os.path.abspath(os.fspath(game)))
    if above is not None:
        where = "is the game folder" if _norm(above) == _norm(ws) else f"is inside the game folder {jpath(above)}"
        return (f"the workspace {jpath(ws)} {where}. satk never writes into a game folder, and the workspace "
                "holds gigabytes of indexes and caches",
                f"choose a folder outside the game: satk init --workspace {jpath(above.parent / 'satk')} "
                "(if satk itself was unpacked into the game folder, move the satk folder out first)")
    if game is not None and inside(game, ws / "work"):
        return (f"the game folder {jpath(game)} is inside the satk work folder {jpath(ws / 'work')}, "
                "which satk rewrites and cleans",
                "move the game out of <workspace>/work, or pick another --workspace")
    return None


def workspace_warnings(ws: str | os.PathLike, env: dict[str, str] | None = None, *,
                       min_free_gb: float = MIN_FREE_GB) -> list[str]:
    """``CODE: text`` warnings about the workspace location (OneDrive, Program Files, free space)."""
    out: list[str] = []
    od = onedrive_root(ws, env)
    if od is not None:
        out.append(f"WORKSPACE_ONEDRIVE: the workspace is in OneDrive ({jpath(od)}): OneDrive would upload "
                   "gigabytes of indexes and caches and can lock files while syncing; prefer a folder outside "
                   "OneDrive (satk init --workspace <folder>)")
    pf = program_files_root(ws, env)
    if pf is not None:
        out.append(f"WORKSPACE_PROGRAM_FILES: the workspace is in {jpath(pf)}: writing there needs administrator "
                   "rights and Windows may redirect the files to VirtualStore; prefer a folder in your profile or "
                   "on another drive (satk init --workspace <folder>)")
    gb = free_gb(ws)
    if gb is not None and gb < min_free_gb:
        out.append(f"LOW_DISK: only {gb:.2f} GB free on {drive_of(ws)} (the workspace drive); an index needs up "
                   f"to 1 GB and texture caches more; free some space or pick another drive")
    return out


def game_warnings(game: str | os.PathLike, env: dict[str, str] | None = None) -> list[str]:
    """``CODE: text`` warnings about the game location (OneDrive placeholders, VirtualStore copies)."""
    out: list[str] = []
    od = onedrive_root(game, env)
    if od is not None:
        out.append(f"GAME_ONEDRIVE: the game is in OneDrive ({jpath(od)}): online-only files are downloaded when "
                   "satk reads them; mark the folder 'Always keep on this device' or move the game out")
    vs = virtualstore_of(game, env)
    if vs is not None:
        out.append(f"GAME_VIRTUALSTORE: Windows keeps redirected copies of game files in {jpath(vs)}; the game "
                   "sees those files, satk reads the originals. Move the copies into the game folder (as "
                   "administrator) or install the game outside Program Files")
    return out
