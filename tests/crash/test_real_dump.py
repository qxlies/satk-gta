"""A real minidump written by Windows dbghelp (MiniDumpWriteDump of a sleeping child Python, AMD64).

Checks the reader against the format as Windows writes it (module list with CodeView records, threads,
system info, memory list) and the export naming of system DLLs; no game or MTA involved.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="dbghelp MiniDumpWriteDump is Windows-only")


def _write_dump(pid_handle: int, pid: int, path: str) -> None:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    dbghelp = ctypes.WinDLL("dbghelp", use_last_error=True)
    fn = dbghelp.MiniDumpWriteDump
    fn.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                   ctypes.c_void_p, ctypes.c_void_p]
    fn.restype = wintypes.BOOL
    with open(path, "wb") as f:
        ok = fn(pid_handle, pid, msvcrt.get_osfhandle(f.fileno()), 0, None, None, None)   # MiniDumpNormal
    if not ok:
        pytest.skip(f"MiniDumpWriteDump failed: {ctypes.get_last_error()}")


@pytest.fixture(scope="module")
def real_dump(tmp_path_factory):
    p = subprocess.Popen([sys.executable, "-c", "import sys,time; print('ready', flush=True); time.sleep(60)"],
                         stdout=subprocess.PIPE, text=True)
    try:
        assert p.stdout.readline().strip() == "ready"
        time.sleep(0.3)
        path = tmp_path_factory.mktemp("real") / "python.dmp"
        _write_dump(int(p._handle), p.pid, str(path))  # noqa: SLF001 - the Popen process handle
        yield path, p.pid
    finally:
        p.kill()
        p.wait(10)


def test_real_dump_structure(real_dump):
    from satk.crash.minidump import Minidump

    path, pid = real_dump
    with Minidump.open(path) as d:
        assert d.arch == "amd64" and d.warnings == []
        names = {m.name.lower() for m in d.modules}
        assert "ntdll.dll" in names and any(n.startswith("python") for n in names)
        nt = d.module_named("ntdll.dll")
        assert nt.pdb and nt.pdb.lower().endswith(".pdb") and len(nt.pdb_id) >= 33
        assert d.threads and all(t.context is not None for t in d.threads)
        assert d.pid == pid
        main = d.threads[0]
        lo, data = d.stack_bytes(main, main.context.sp)
        assert lo == main.context.sp or len(data) > 0
        assert d.module_at(main.context.ip) is not None


def test_real_dump_analyze_names_system_frames(real_dump, satk_home):
    from satk.crash.analyze import analyze_file

    path, _pid = real_dump
    env = analyze_file(str(path), limit=20)
    assert any(w.startswith("NO_EXCEPTION") for w in env["warn"])
    rows = [dict(zip(env["cols"], r)) for r in env["rows"]]
    assert rows and rows[0]["via"] == "ip"
    exported = [r for r in rows if r["src"] == "export"]
    assert exported, rows                                         # ntdll/kernelbase frames got export names
    assert all(r["at"].split("+")[0].lower().endswith((".dll", ".exe")) for r in rows if r["at"])
