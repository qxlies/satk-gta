"""Process-tree containment for the long-running MCP workers (stdlib only, owner WP-06).

A ``long_running`` operation runs in ``python -m satk.mcp.worker``, which may start Blender,
MSBuild or other programs. On a timeout or a cancelled call the whole tree must go, not only the
process the server started: ``Scripts/python.exe`` of a venv is a launcher that runs the real
interpreter as its child, so the worker is already a grandchild, and Blender one level below.

* **Windows**: one Job Object per call. The launcher is assigned right after it starts; the
  worker reports its own pid first (``{"hello": {"pid": N}}``) and is assigned too if it was
  created before that, and only then receives its request, so nothing it starts can be outside
  the job. :meth:`ProcessTree.kill` terminates every process in the job. The job is
  kill-on-close while the call runs, so the tree also dies with the server process.
  :meth:`ProcessTree.release` (normal completion) drops that flag first: a helper the operation
  left running on purpose (a PDB server, a viewer) survives as it did without the job.
  ``CREATE_BREAKAWAY_FROM_JOB`` is allowed for programs that must outlive the call.
* **POSIX**: the worker starts a new session; :meth:`kill` signals its process group.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys

__all__ = ["ProcessTree", "pid_alive"]

_WIN = sys.platform == "win32"

if _WIN:
    import ctypes
    from ctypes import wintypes

    _K32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _BasicLimits(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class _IoCounters(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOperationCount", "WriteOperationCount",
                                                    "OtherOperationCount", "ReadTransferCount",
                                                    "WriteTransferCount", "OtherTransferCount")]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _BasicLimits), ("IoInfo", _IoCounters),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    class _PidList(ctypes.Structure):  # JOBOBJECT_BASIC_PROCESS_ID_LIST with room for 256 ids
        _fields_ = [("NumberOfAssignedProcesses", wintypes.DWORD), ("NumberOfProcessIdsInList", wintypes.DWORD),
                    ("ProcessIdList", ctypes.c_size_t * 256)]

    _K32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    _K32.CreateJobObjectW.restype = wintypes.HANDLE
    _K32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    _K32.SetInformationJobObject.restype = wintypes.BOOL
    _K32.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                               ctypes.POINTER(wintypes.DWORD)]
    _K32.QueryInformationJobObject.restype = wintypes.BOOL
    _K32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _K32.AssignProcessToJobObject.restype = wintypes.BOOL
    _K32.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
    _K32.IsProcessInJob.restype = wintypes.BOOL
    _K32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _K32.TerminateJobObject.restype = wintypes.BOOL
    _K32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _K32.OpenProcess.restype = wintypes.HANDLE
    _K32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    _K32.GetExitCodeProcess.restype = wintypes.BOOL
    _K32.CloseHandle.argtypes = [wintypes.HANDLE]
    _K32.CloseHandle.restype = wintypes.BOOL

    _EXTENDED_LIMIT_INFORMATION = 9
    _BASIC_PROCESS_ID_LIST = 3
    _KILL_ON_JOB_CLOSE = 0x2000
    _BREAKAWAY_OK = 0x0800
    _PROCESS_TERMINATE = 0x0001
    _PROCESS_SET_QUOTA = 0x0100
    _PROCESS_QUERY_LIMITED = 0x1000
    _STILL_ACTIVE = 259

    def _err(what: str) -> OSError:
        code = ctypes.get_last_error()
        return OSError(code, f"{what}: {ctypes.FormatError(code).strip()}")


def pid_alive(pid: int) -> bool:
    """True while a process with this pid runs (used by tests and logs)."""
    if _WIN:
        h = _K32.OpenProcess(_PROCESS_QUERY_LIMITED, False, int(pid))
        if not h:
            return False
        try:
            code = wintypes.DWORD()
            return bool(_K32.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == _STILL_ACTIVE
        finally:
            _K32.CloseHandle(h)
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class ProcessTree:
    """Owns the tree of one worker process (see the module docstring)."""

    def __init__(self) -> None:
        self._job = None
        self._root: int | None = None
        if _WIN:
            job = _K32.CreateJobObjectW(None, None)  # handle is not inheritable
            if not job:
                raise _err("CreateJobObject")
            self._job = job
            try:
                self._set_flags(_KILL_ON_JOB_CLOSE | _BREAKAWAY_OK)
            except BaseException:
                self.close()
                raise

    # -- spawning -------------------------------------------------------------------------

    @staticmethod
    def popen_kwargs() -> dict:
        """Extra ``open_process``/``Popen`` arguments for the root process."""
        if _WIN:
            return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
        return {"start_new_session": True}

    def add(self, pid: int) -> bool:
        """Put a process (and everything it starts from now on) into the tree.

        Returns False when it could not be contained (it exited, or access was denied); the
        caller logs that. The first pid added is the root (POSIX: its process group).
        """
        if self._root is None:
            self._root = int(pid)
        if not _WIN:
            return True
        h = _K32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE | _PROCESS_QUERY_LIMITED, False, int(pid))
        if not h:
            return False
        try:
            inside = wintypes.BOOL()
            if _K32.IsProcessInJob(h, self._job, ctypes.byref(inside)) and inside.value:
                return True
            return bool(_K32.AssignProcessToJobObject(self._job, h))
        finally:
            _K32.CloseHandle(h)

    def pids(self) -> list[int]:
        """Processes in the tree now (Windows: the job's list; POSIX: the root only)."""
        if not _WIN:
            return [self._root] if self._root is not None and pid_alive(self._root) else []
        if not self._job:
            return []
        lst = _PidList()
        if not _K32.QueryInformationJobObject(self._job, _BASIC_PROCESS_ID_LIST, ctypes.byref(lst),
                                              ctypes.sizeof(lst), None):
            return []
        return [int(lst.ProcessIdList[i]) for i in range(min(lst.NumberOfProcessIdsInList, len(lst.ProcessIdList)))]

    # -- ending ---------------------------------------------------------------------------

    def kill(self) -> None:
        """Terminate every process of the tree (timeout, cancelled call, error)."""
        if _WIN:
            if self._job and not _K32.TerminateJobObject(self._job, 1):
                raise _err("TerminateJobObject")
            return
        if self._root is not None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(self._root, signal.SIGKILL)

    def release(self) -> None:
        """Normal completion: stop owning the tree, leave helpers that are still running alone."""
        if _WIN and self._job:
            with contextlib.suppress(OSError):
                self._set_flags(_BREAKAWAY_OK)
        self.close()

    def close(self) -> None:
        """Close the job handle (kills what is left unless :meth:`release` cleared kill-on-close)."""
        if _WIN and self._job:
            _K32.CloseHandle(self._job)
            self._job = None

    def __del__(self) -> None:  # pragma: no cover - safety net, close() is called explicitly
        with contextlib.suppress(Exception):
            self.close()

    def _set_flags(self, flags: int) -> None:
        info = _ExtendedLimits()
        info.BasicLimitInformation.LimitFlags = flags
        if not _K32.SetInformationJobObject(self._job, _EXTENDED_LIMIT_INFORMATION, ctypes.byref(info),
                                            ctypes.sizeof(info)):
            raise _err("SetInformationJobObject")
