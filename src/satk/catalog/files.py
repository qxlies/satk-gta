"""Small file helpers of the catalog (owner M2-09): guarded writes that skip identical content, and the
removal of stale outputs inside the catalog directory only. Stdlib only."""

from __future__ import annotations

import itertools
import os
import threading
from pathlib import Path
from typing import Iterable

from ..core.paths import ensure_removable, ensure_writable

__all__ = ["write_if_changed", "remove_stale", "dir_size"]

_counter = itertools.count()


def write_if_changed(path: Path, data: bytes | str) -> bool:
    """Write ``data`` (atomic rename, no fsync) unless the file already holds exactly these bytes.

    Returns ``True`` when the file was (re)written.
    """
    raw = data.encode("utf-8") if isinstance(data, str) else data
    p = ensure_writable(path)
    try:
        if p.stat().st_size == len(raw) and p.read_bytes() == raw:
            return False
    except OSError:
        pass
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.{threading.get_ident()}.{next(_counter)}.tmp")
    ensure_writable(tmp)
    with open(tmp, "wb") as f:
        f.write(raw)
    try:
        os.replace(tmp, p)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return True


def remove_stale(root: Path, keep: Iterable[Path], suffixes: tuple[str, ...]) -> int:
    """Delete files under ``root`` with one of ``suffixes`` that are not in ``keep`` (and empty dirs left
    behind). Leftover ``*.tmp`` files of interrupted writes go too. Returns the number of files removed."""
    if not root.is_dir():
        return 0
    keep_set = {os.path.normcase(os.path.abspath(p)) for p in keep}
    removed = 0
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        for fn in filenames:
            if not (fn.endswith(suffixes) or fn.endswith(".tmp")):
                continue
            full = os.path.join(dirpath, fn)
            if os.path.normcase(os.path.abspath(full)) in keep_set:
                continue
            ensure_removable(full)
            try:
                os.unlink(full)
                removed += 1
            except OSError:
                pass
        if dirpath != os.fspath(root):
            try:
                if not os.listdir(dirpath):
                    ensure_removable(dirpath)
                    os.rmdir(dirpath)
            except OSError:
                pass
    return removed


def dir_size(root: Path) -> tuple[int, int]:
    """``(files, bytes)`` under ``root``."""
    n = size = 0
    for dirpath, _dirs, filenames in os.walk(root):
        for fn in filenames:
            try:
                size += os.path.getsize(os.path.join(dirpath, fn))
                n += 1
            except OSError:
                pass
    return n, size
