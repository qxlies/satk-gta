"""``satk.mcp.proctree``: the process tree of a long-running MCP worker (WP-06 fix: orphaned Blender)."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from satk.mcp.proctree import ProcessTree, pid_alive

# Like satk.mcp.worker: report the pid, wait for the go (the request), then start a tool (Blender).
_CHILD = ("import os, subprocess, sys, time; open(sys.argv[1], 'w').write(str(os.getpid())); sys.stdin.readline(); "
          "c = subprocess.Popen([sys.executable, '-c', 'import os, sys, time; "
          "open(sys.argv[1], \"w\").write(str(os.getpid())); time.sleep(60)', sys.argv[2]]); time.sleep(60)")


def _wait_file(p: Path, timeout: float = 30.0) -> int:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if p.is_file() and p.read_text(encoding="utf-8").strip():
            return int(p.read_text(encoding="utf-8"))
        time.sleep(0.05)
    raise AssertionError(f"{p} not written")


def _gone(pid: int, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.05)
    return False


def _spawn(tmp_path: Path, tree: ProcessTree) -> tuple[subprocess.Popen, int, int]:
    """root (venv launcher) -> python child -> launcher -> python grandchild; returns pids of both pythons."""
    a, b = tmp_path / "child.pid", tmp_path / "grandchild.pid"
    p = subprocess.Popen([sys.executable, "-c", _CHILD, str(a), str(b)], stdin=subprocess.PIPE,
                         **ProcessTree.popen_kwargs())
    assert tree.add(p.pid)
    child = _wait_file(a)
    assert tree.add(child)  # the server does this on the worker's hello (the launcher may have been faster)
    p.stdin.write(b"go\n")
    p.stdin.close()
    return p, child, _wait_file(b)


def _cleanup(*pids: int) -> None:
    for pid in pids:
        if pid_alive(pid):
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGTERM)


def test_pid_alive():
    assert pid_alive(os.getpid())
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    assert _gone(p.pid)


def test_kill_takes_grandchildren_too(tmp_path):
    tree = ProcessTree()
    p, child, grandchild = _spawn(tmp_path, tree)
    try:
        if sys.platform == "win32":
            assert {p.pid, child, grandchild} <= set(tree.pids())
        tree.kill()
        tree.close()
        assert _gone(child) and _gone(grandchild)
        p.wait(timeout=10)
    finally:
        _cleanup(child, grandchild)


@pytest.mark.skipif(sys.platform != "win32", reason="kill-on-close is a Job Object feature")
def test_closing_the_job_kills_the_tree(tmp_path):
    """Kill-on-close: if the server dies mid-call (its handle closes), the worker tree dies with it."""
    tree = ProcessTree()
    p, child, grandchild = _spawn(tmp_path, tree)
    try:
        tree.close()
        assert _gone(child) and _gone(grandchild)
    finally:
        _cleanup(child, grandchild)
        p.wait(timeout=10)


def test_release_leaves_running_processes(tmp_path):
    tree = ProcessTree()
    p, child, grandchild = _spawn(tmp_path, tree)
    try:
        tree.release()
        time.sleep(0.3)
        assert pid_alive(child) and pid_alive(grandchild)
    finally:
        _cleanup(grandchild, child, p.pid)
        p.wait(timeout=10)
