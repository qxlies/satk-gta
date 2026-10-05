"""Read-only protection of the IMG archives of a game copy (SPEC §4.1, risk R13).

``satk game protect`` sets ``FILE_ATTRIBUTE_READONLY`` on the 8 IMG archives
(:data:`satk.game.manifests.PROTECT_IMGS`); ``unprotect`` clears it. This is safe: the game
and Ariane open IMG files with ``"rb"`` (SPEC §0.2 V7); only Ariane's explicit save path uses
``"r+b"`` and then fails instead of silently rewriting a 900 MB archive.

Changing an attribute is a write to the copy, so the root is checked by
:func:`satk.game.guard.write_target` (the clean copy root itself or a copy under ``work/``;
never the original install, ``src`` or an alias of them such as an extended-length or UNC
path) and every file by ``ensure_writable``; ``game_writer()`` is entered only for the clean
copy root. Content and mtime are untouched (``satk game verify`` stays ok).
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from satk.core.envelope import table
from satk.core.errors import SatkError
from satk.core.paths import ensure_writable, jpath

from .guard import canonical, write_target
from .manifests import PROTECT_IMGS, to_path

__all__ = ["is_readonly", "state", "set_protected", "summary"]


def is_readonly(path: str | os.PathLike) -> bool:
    """True if the file has the read-only attribute (Windows) / no write bit (elsewhere)."""
    st = os.stat(path)
    attrs = getattr(st, "st_file_attributes", None)
    if attrs is not None:
        return bool(attrs & stat.FILE_ATTRIBUTE_READONLY)
    return not (st.st_mode & stat.S_IWUSR)  # pragma: no cover - non-Windows


def _check_root(root: Path) -> Path:
    if not root.is_dir():
        raise SatkError("NOT_FOUND", f"game root not found: {jpath(root)}", hint="--root <game dir>")
    return root


def state(root: str | os.PathLike) -> list[tuple[str, bool, bool]]:
    """``[(relpath, exists, readonly)]`` for the protected IMG archives of ``root``."""
    r = _check_root(Path(root))
    out = []
    for rel in PROTECT_IMGS:
        p = to_path(r, rel)
        if p.is_file():
            out.append((rel, True, is_readonly(p)))
        else:
            out.append((rel, False, False))
    return out


def summary(root: str | os.PathLike) -> str:
    """``"8/8"`` = read-only IMG archives / expected archives."""
    st = state(root)
    return f"{sum(1 for _, ex, ro in st if ex and ro)}/{len(st)}"


def set_protected(root: str | os.PathLike, on: bool) -> dict:
    """Set (``on=True``) or clear the read-only attribute of the IMG archives of ``root``.

    Returns a table envelope ``[path, readonly]`` plus ``root``, ``protected`` (``"8/8"``),
    ``changed`` and ``missing``; raises ``PROTECTED_PATH`` for the original install / ``src``,
    any alias of them, a directory inside the clean copy or outside ``work/`` (before touching
    anything) and ``NOT_FOUND`` without a root.
    """
    _check_root(canonical(root, what="game root"))
    t = write_target(root, what="game root", allow_game_root=True)
    r = t.path
    changed = 0
    missing: list[str] = []
    rows: list[list] = []
    with t.unlock():  # game_writer() only for the clean copy root itself
        ensure_writable(r)
        targets = []
        for rel in PROTECT_IMGS:
            p = to_path(r, rel)
            if not p.is_file():
                missing.append(rel)
                continue
            targets.append((rel, ensure_writable(p)))
            if os.stat(p).st_nlink > 1:  # the attribute lives in the file record: it would change every link
                raise SatkError("PROTECTED_PATH", f"{jpath(p)} is a hard link ({os.stat(p).st_nlink} names); "
                                "its attribute may belong to another game copy",
                                hint="satk game clone makes real copies", data={"path": jpath(p), "root": jpath(r)})
        for rel, p in targets:
            ro = is_readonly(p)
            if ro != on:
                mode = os.stat(p).st_mode
                os.chmod(p, (mode & ~stat.S_IWRITE) if on else (mode | stat.S_IWRITE))
                changed += 1
            rows.append([rel, is_readonly(p)])
    n_ro = sum(1 for _, ro in rows if ro)
    out = table(["path", "readonly"], rows)
    out.update(root=jpath(r), protected=f"{n_ro}/{len(PROTECT_IMGS)}", changed=changed)
    if missing:
        out["missing"] = missing
    return out
