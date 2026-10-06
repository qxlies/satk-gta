"""A tiny, dependency-free DLL injector for the SP bridge (Windows x86 target).

Two ways to get ``satk_sp.asi`` into ``gta_sa.exe``:

* **Mod Loader / ASI loader**: copy the file next to the loader (no injection at all) - the launcher
  only copies and starts the game. This is the recommended path and needs no code here.
* **Start suspended + remote ``LoadLibrary``**: :func:`start_suspended` runs the exe with
  ``CREATE_SUSPENDED``, :func:`inject` writes the DLL path into the target and starts a thread at
  ``LoadLibraryW``, then the caller resumes. Used by ``satk sp start`` when asked to inject rather
  than rely on the loader.

Everything here is ``ctypes`` + the Windows API; no third-party package, no download. The functions
raise :class:`~satk.core.errors.SatkError` with a helpful hint on failure. This module performs the
mechanics only; **it never launches the game on its own** - the consent-gated op decides that.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..core.errors import SatkError

__all__ = ["Started", "start_suspended", "inject", "resume", "terminate", "CREATE_SUSPENDED"]

CREATE_SUSPENDED = 0x00000004
_MEM_COMMIT = 0x1000
_MEM_RESERVE = 0x2000
_MEM_RELEASE = 0x8000
_PAGE_RW = 0x04
_INFINITE = 0xFFFFFFFF


@dataclass
class Started:
    """A process started suspended: its pid, and the two handles to close when done."""

    pid: int
    h_process: int
    h_thread: int


def _require_windows() -> None:
    if os.name != "nt":
        raise SatkError("UNSUPPORTED", "the SP injector runs on Windows only")


def start_suspended(exe: str | Path, args: str = "", cwd: str | Path | None = None) -> Started:
    """Start ``exe`` with ``CREATE_SUSPENDED``; the game does not run until :func:`resume`.

    Args:
        exe: path to gta_sa.exe (must exist).
        args: command line appended after the image name.
        cwd: working directory (defaults to the exe's folder).
    """
    _require_windows()
    import ctypes
    from ctypes import wintypes

    exe = Path(exe)
    if not exe.is_file():
        raise SatkError("NOT_FOUND", f"executable not found: {exe}")
    cwd = Path(cwd) if cwd is not None else exe.parent

    class STARTUPINFO(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR), ("lpDesktop", wintypes.LPWSTR),
                    ("lpTitle", wintypes.LPWSTR), ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                    ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD), ("dwXCountChars", wintypes.DWORD),
                    ("dwYCountChars", wintypes.DWORD), ("dwFillAttribute", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
                    ("lpReserved2", ctypes.POINTER(ctypes.c_byte)), ("hStdInput", wintypes.HANDLE),
                    ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE)]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                    ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    si = STARTUPINFO()
    si.cb = ctypes.sizeof(STARTUPINFO)
    pi = PROCESS_INFORMATION()
    cmdline = f'"{exe}"' + (f" {args}" if args else "")
    ok = k32.CreateProcessW(str(exe), ctypes.create_unicode_buffer(cmdline), None, None, False,
                            CREATE_SUSPENDED, None, str(cwd), ctypes.byref(si), ctypes.byref(pi))
    if not ok:
        raise SatkError("INTERNAL", f"CreateProcessW failed (error {ctypes.get_last_error()})")
    return Started(pid=int(pi.dwProcessId), h_process=int(pi.hProcess), h_thread=int(pi.hThread))


def inject(h_process: int, dll: str | Path) -> None:
    """Load ``dll`` inside the process handle ``h_process`` via a remote ``LoadLibraryW`` thread."""
    _require_windows()
    import ctypes
    from ctypes import wintypes

    dll = Path(dll)
    if not dll.is_file():
        raise SatkError("NOT_FOUND", f"DLL not found: {dll}")
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.VirtualAllocEx.restype = wintypes.LPVOID
    k32.VirtualAllocEx.argtypes = (wintypes.HANDLE, wintypes.LPVOID, ctypes.c_size_t, wintypes.DWORD, wintypes.DWORD)
    k32.WriteProcessMemory.argtypes = (wintypes.HANDLE, wintypes.LPVOID, wintypes.LPCVOID, ctypes.c_size_t,
                                       ctypes.POINTER(ctypes.c_size_t))
    k32.GetModuleHandleW.restype = wintypes.HMODULE
    k32.GetProcAddress.restype = wintypes.LPVOID
    k32.GetProcAddress.argtypes = (wintypes.HMODULE, wintypes.LPCSTR)
    k32.CreateRemoteThread.restype = wintypes.HANDLE
    k32.CreateRemoteThread.argtypes = (wintypes.HANDLE, wintypes.LPVOID, ctypes.c_size_t, wintypes.LPVOID,
                                       wintypes.LPVOID, wintypes.DWORD, wintypes.LPVOID)

    path_w = str(dll.resolve())
    buf = ctypes.create_unicode_buffer(path_w)
    size = ctypes.sizeof(buf)
    remote = k32.VirtualAllocEx(h_process, None, size, _MEM_COMMIT | _MEM_RESERVE, _PAGE_RW)
    if not remote:
        raise SatkError("INTERNAL", f"VirtualAllocEx failed (error {ctypes.get_last_error()})")
    written = ctypes.c_size_t(0)
    if not k32.WriteProcessMemory(h_process, remote, buf, size, ctypes.byref(written)):
        k32.VirtualFreeEx(h_process, remote, 0, _MEM_RELEASE)
        raise SatkError("INTERNAL", f"WriteProcessMemory failed (error {ctypes.get_last_error()})")
    load = k32.GetProcAddress(k32.GetModuleHandleW("kernel32.dll"), b"LoadLibraryW")
    if not load:
        raise SatkError("INTERNAL", "could not resolve LoadLibraryW")
    thread = k32.CreateRemoteThread(h_process, None, 0, load, remote, 0, None)
    if not thread:
        k32.VirtualFreeEx(h_process, remote, 0, _MEM_RELEASE)
        raise SatkError("INTERNAL", f"CreateRemoteThread failed (error {ctypes.get_last_error()})")
    k32.WaitForSingleObject(thread, 10000)
    k32.CloseHandle(thread)
    k32.VirtualFreeEx(h_process, remote, 0, _MEM_RELEASE)


def resume(started: Started) -> None:
    """Resume the suspended main thread and close the handles."""
    _require_windows()
    import ctypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.ResumeThread(started.h_thread)
    k32.CloseHandle(started.h_thread)
    k32.CloseHandle(started.h_process)


def terminate(started: Started, code: int = 0) -> None:
    """Terminate a process we started (cleanup on failure); best-effort."""
    if os.name != "nt":
        return
    import ctypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    try:
        k32.TerminateProcess(started.h_process, code)
    finally:
        k32.CloseHandle(started.h_thread)
        k32.CloseHandle(started.h_process)
