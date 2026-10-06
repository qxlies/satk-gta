"""Helpers for running the test suite inside the Codex Windows sandbox (restricted token, ``unelevated``).

Inside that sandbox three things differ from a normal shell, and none of them is a satk bug:

* Git Bash / MSYS programs cannot start (``CreateFileMapping ... Win32 error 5``).
* Python 3.12.4+ creates ``os.mkdir(path, 0o700)`` directories (``tempfile.mkdtemp``, pytest's ``tmp_path``) with an
  owner-only ACL that the restricted token cannot use, so every file written into them fails.
* ``multiprocessing`` pipes and asyncio subprocess pipes (overlapped named pipes) fail with ``WinError 5``.
  satk falls back to in-process work for its own pools (``satk.core.procpool``), so no test needs a special case
  there; tests that start a subprocess through asyncio / anyio (the MCP server's worker, the SDK's stdio client)
  skip with ``skip_unless_async_subprocess``.

This module is used in two ways. As a plain helper it gives tests a once-per-session probe of an external
shell (``skip_unless_shell_runs``). As a pytest plugin (``pytest -p sandbox_compat``, with ``tests`` on
``PYTHONPATH``; ``satk dev gate`` does both when needed) it downgrades the ``0o700`` mkdir mode when, and only
when, a probe shows that such directories are unusable. Outside the sandbox nothing changes.
"""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest

#: Skip reasons start with this word so the gate can count them (``satk dev gate`` summary).
PREFIX = "sandbox"

_original_mkdir = os.mkdir


@functools.cache
def shell_failure(exe: str) -> str | None:
    """Why ``<exe> -c true`` cannot run here, or ``None`` when the shell works (probed once per exe and session)."""
    try:
        p = subprocess.run([exe, "-c", "true"], capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as e:
        return f"{type(e).__name__}: {e}"[:200]
    if p.returncode == 0:
        return None
    text = (p.stdout + p.stderr).decode("utf-8", "replace")
    if "CreateFileMapping" in text:  # the MSYS runtime cannot map its shared memory (no pid or SID: stable text)
        return "MSYS CreateFileMapping denied, Win32 error 5"
    last = (text.strip().splitlines() or ["no output"])[-1]
    return f"exit {p.returncode}: {' '.join(last.split())[:120]}"


def skip_unless_shell_runs(exe: str | None) -> None:
    """``pytest.skip`` with a clear reason when the POSIX shell ``exe`` exists but cannot start (sandbox)."""
    if not exe:
        return
    why = shell_failure(str(exe))
    if why:
        pytest.skip(f"{PREFIX}: {Path(exe).name} cannot start in this environment ({why})")


def find_sh() -> str | None:
    """``sh`` on ``PATH``, else Git for Windows' own ``sh.exe`` next to ``git`` (what ``git`` itself spawns)."""
    sh = shutil.which("sh")
    if sh:
        return sh
    git = shutil.which("git")
    if git:
        root = Path(git).resolve().parents[1]  # <Git>/cmd/git.exe -> <Git>
        for c in (root / "bin" / "sh.exe", root / "usr" / "bin" / "sh.exe"):
            if c.is_file():
                return str(c)
    return None


@functools.cache
def async_subprocess_failure() -> str | None:
    """Why ``asyncio`` cannot start a subprocess with pipes here (``None``: it can; probed once per session)."""
    import asyncio

    box: list[str | None] = [None]

    async def probe() -> None:
        p = await asyncio.create_subprocess_exec(sys.executable, "-c", "pass", stdin=asyncio.subprocess.PIPE,
                                                 stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await p.communicate()

    def run() -> None:  # a private thread and loop: the probe never touches the loop of a test
        try:
            asyncio.run(probe())
        except Exception as e:  # noqa: BLE001
            box[0] = f"{type(e).__name__}: {e}"[:200]

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(120)
    return box[0] if not t.is_alive() else "probe timed out"


def skip_unless_async_subprocess() -> None:
    """``pytest.skip`` when asyncio subprocess pipes are denied (Codex sandbox: ``WinError 5``)."""
    why = async_subprocess_failure()
    if why:
        pytest.skip(f"{PREFIX}: asyncio subprocess pipes are not available in this environment ({why})")


@functools.cache
def owner_only_dirs_unusable() -> bool:
    """True when a directory made with ``os.mkdir(path, 0o700)`` cannot be written to (restricted token)."""
    d = Path(tempfile.gettempdir()) / ".satk-acl-probe"  # fixed name: an unremovable probe dir is left only once
    try:
        _original_mkdir(d, 0o700)
    except FileExistsError:
        pass  # an earlier probe made it: the write test below tells whether it is usable
    except OSError:
        return False  # temp itself unusable: not this problem, let the tests report it
    try:
        (d / "probe").write_bytes(b"x")
        (d / "probe").unlink()
        return False
    except OSError:
        return True
    finally:
        try:
            d.rmdir()
        except OSError:
            pass


def _sandbox_mkdir(path, mode=0o777, *, dir_fd=None):
    return _original_mkdir(path, 0o777 if mode == 0o700 else mode, dir_fd=dir_fd)


def apply_mkdir_workaround() -> bool:
    """Make ``os.mkdir(..., 0o700)`` create ordinary directories when owner-only ones are unusable. Idempotent."""
    if os.mkdir is _sandbox_mkdir or not owner_only_dirs_unusable():
        return os.mkdir is _sandbox_mkdir
    os.mkdir = _sandbox_mkdir
    return True


def pytest_configure(config: pytest.Config) -> None:  # plugin hook (``-p sandbox_compat``)
    apply_mkdir_workaround()
