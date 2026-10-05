"""Small, fast system probes shared by ``doctor`` and ``status`` (stdlib only, no side effects)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

__all__ = ["file_version", "run_version", "free_gb", "drive_of"]

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def file_version(path: str | os.PathLike) -> str | None:
    """``"5.1.1"`` from the Windows version resource of an executable (``None`` if unavailable).

    Reads ``VS_FIXEDFILEINFO`` through ``version.dll`` (~1 ms; the program is not started).
    Trailing ``.0`` components are dropped, a non-zero build number is kept (``"18.9.1.35102"``).
    """
    if sys.platform != "win32":
        return None
    p = os.fspath(path)
    if not os.path.isfile(p):
        return None
    try:
        import ctypes
        import ctypes.wintypes as wt

        ver = ctypes.WinDLL("version")
        ver.GetFileVersionInfoSizeW.argtypes = [wt.LPCWSTR, ctypes.POINTER(wt.DWORD)]
        ver.GetFileVersionInfoW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p]
        ver.VerQueryValueW.argtypes = [ctypes.c_void_p, wt.LPCWSTR, ctypes.POINTER(ctypes.c_void_p),
                                       ctypes.POINTER(wt.UINT)]
        size = ver.GetFileVersionInfoSizeW(p, None)
        if not size:
            return None
        buf = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(p, 0, size, buf):
            return None
        ptr = ctypes.c_void_p()
        n = wt.UINT()
        if not ver.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(n)) or not ptr.value:
            return None

        class _Fixed(ctypes.Structure):
            _fields_ = [(k, wt.DWORD) for k in ("sig", "struc", "fv_ms", "fv_ls", "pv_ms", "pv_ls", "mask", "flags",
                                                "os", "type", "sub", "date_ms", "date_ls")]

        fi = ctypes.cast(ptr, ctypes.POINTER(_Fixed)).contents
        if fi.sig != 0xFEEF04BD:
            return None
        parts = [fi.fv_ms >> 16, fi.fv_ms & 0xFFFF, fi.fv_ls >> 16, fi.fv_ls & 0xFFFF]
        while len(parts) > 2 and parts[-1] == 0:
            parts.pop()
        return ".".join(map(str, parts))
    except (OSError, AttributeError, ValueError):
        return None


def run_version(argv: list[str], timeout: float = 10.0) -> str | None:
    """First non-empty output line of ``argv`` (e.g. ``["git", "--version"]``) or ``None``.

    stdin is the null device (never the MCP pipe), no console window, bounded time.
    """
    exe = argv[0]
    if not os.path.isfile(exe):
        found = shutil.which(exe)
        if not found:
            return None
        argv = [found, *argv[1:]]
    try:
        r = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout,
                           creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    for raw in (r.stdout, r.stderr):
        text = raw.decode("utf-8", "replace") if raw else ""
        for line in text.splitlines():
            if line.strip():
                return line.strip()
    return None


def drive_of(path: str | os.PathLike) -> str:
    """``"D:"`` for ``D:\\ws`` (or the mount root elsewhere)."""
    p = os.path.abspath(os.fspath(path))
    d = os.path.splitdrive(p)[0]
    return d.upper() if d else os.path.sep


def free_gb(path: str | os.PathLike) -> float | None:
    """Free space in GB (GiB, as Explorer shows it) on the volume of ``path``; ``None`` on error."""
    p = Path(os.path.abspath(os.fspath(path)))
    while not p.exists() and p.parent != p:
        p = p.parent
    try:
        return shutil.disk_usage(p).free / 2**30
    except OSError:
        return None
