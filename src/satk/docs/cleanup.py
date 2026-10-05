"""Reliable clean-up of temporary work directories (``docs-smoke`` private work, e2e runs; WP-12).

On Windows a directory tree cannot be removed while any process still has one of its files open: the
viewer's log right after ``view stop`` (the process object is signaled only after its handles are
closed), or a scanner that opens a file the moment it is closed. ``shutil.rmtree(ignore_errors=True)``
then silently leaves the tree behind. :func:`wait_process_exit` waits for the process object itself,
:func:`remove_tree` retries and reports what is left instead of hiding it.

Standard library only.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

__all__ = ["wait_process_exit", "remove_tree"]

_SYNCHRONIZE = 0x00100000
_WAIT_OBJECT_0 = 0


def wait_process_exit(pid: int | None, seconds: float = 15.0) -> bool:
    """True when the process ``pid`` is gone (or never existed) within ``seconds``.

    Windows: ``WaitForSingleObject`` on a ``SYNCHRONIZE`` handle, i.e. until the process has fully
    terminated and closed its handles (an exit code alone is not enough). Elsewhere: ``kill(pid, 0)``.
    """
    if not pid or int(pid) <= 0:
        return True
    pid = int(pid)
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenProcess.restype = wintypes.HANDLE
        k.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        k.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        k.WaitForSingleObject.restype = wintypes.DWORD
        k.CloseHandle.argtypes = (wintypes.HANDLE,)
        h = k.OpenProcess(_SYNCHRONIZE, False, pid)
        if not h:
            return True  # no such process (any more)
        try:
            return k.WaitForSingleObject(h, max(0, int(seconds * 1000))) == _WAIT_OBJECT_0
        finally:
            k.CloseHandle(h)
    t_end = time.monotonic() + seconds
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            pass
        if time.monotonic() > t_end:
            return False
        time.sleep(0.1)


def remove_tree(path: str | os.PathLike, seconds: float = 15.0) -> list[str]:
    """Remove a directory tree, retrying for up to ``seconds``; returns what is left (relative, ``/``).

    ``[]`` = removed (or did not exist). The caller decides whether leftovers are an error; they are
    never silently ignored.
    """
    p = Path(path)
    t_end = time.monotonic() + seconds
    while True:
        shutil.rmtree(p, ignore_errors=True)
        if not p.exists():
            return []
        if time.monotonic() > t_end:
            break
        time.sleep(0.25)
    left = sorted(x.relative_to(p).as_posix() for x in p.rglob("*") if x.is_file())
    return left or ["."]
