"""Test-only operations for the MCP server tests (loaded by file path, never by discovery).

The tests load this file inside ``isolated_registry()`` so these ops never leak into the real
registry; the long-running worker subprocess loads it by path (``satk.mcp.worker``).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from satk.core.registry import op, report_progress


@op("zz.slow_steps", summary="Long-running test op with progress.", summary_ru="Тест: долгая операция.",
    mcp="zz_slow_steps", long_running=True)
def slow_steps(n: int = 3, fail: bool = False) -> dict:
    """Report n progress steps (prints to stdout on purpose).

    Args:
        n: number of steps.
        fail: raise instead of returning.
    """
    for i in range(n):
        print(f"stray stdout line {i}")  # must never reach the MCP stream
        os.write(1, b"raw fd1 write\n")
        report_progress(i + 1, n, f"step {i + 1}")
    if fail:
        raise RuntimeError("worker op failed")
    return {"steps": n, "pid": os.getpid()}


_SLEEPER = "import os, sys, time; open(sys.argv[1], 'w').write(str(os.getpid())); time.sleep(float(sys.argv[2]))"


def _start_sleeper(pid_file: str, seconds: float):
    """A child Python (through the venv launcher, like Blender through a .cmd) that writes its pid."""
    import subprocess

    from pathlib import Path

    p = subprocess.Popen([sys.executable, "-c", _SLEEPER, pid_file, str(seconds)],
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    f = Path(pid_file)
    deadline = time.time() + 30
    while time.time() < deadline and not (f.is_file() and f.read_text(encoding="utf-8").strip()):
        time.sleep(0.05)
    return p


@op("zz.spawn_and_wait", summary="Start a child process and wait for it (process-tree test).",
    summary_ru="Тест: дочерний процесс.", mcp="zz_spawn_and_wait", long_running=True)
def spawn_and_wait(pid_file: str, seconds: float = 60.0) -> dict:
    """Start a sleeping grandchild of the MCP server and wait for it.

    Args:
        pid_file: where the child writes its pid.
        seconds: how long the child sleeps.
    """
    p = _start_sleeper(pid_file, seconds)
    report_progress(1, 2, "child started")
    p.wait()
    return {"waited": seconds}


@op("zz.spawn_and_leave", summary="Start a child process and return (process-tree test).",
    summary_ru="Тест: оставить дочерний процесс.", mcp="zz_spawn_and_leave", long_running=True)
def spawn_and_leave(pid_file: str, seconds: float = 30.0) -> dict:
    """Start a sleeping child and return while it still runs (a helper left on purpose).

    Args:
        pid_file: where the child writes its pid.
        seconds: how long the child sleeps.
    """
    _start_sleeper(pid_file, seconds)
    return {"left": pid_file}


@op("zz.sleep", summary="Sleep (timeout test).", summary_ru="Тест: пауза.", mcp="zz_sleep")
def sleep(seconds: float = 1.0) -> dict:
    """Sleep.

    Args:
        seconds: how long.
    """
    time.sleep(seconds)
    return {"slept": seconds}


@op("zz.picture", summary="Return a picture path (inline test).", summary_ru="Тест: картинка.", mcp="zz_picture")
def picture(path: str, inline: bool = False) -> dict:
    """Return the given image path.

    Args:
        path: image file.
        inline: embed the image in the MCP result.
    """
    print("stray print in a thread", file=sys.stdout)
    return {"file": path}


@op("zz.process_tree", summary="Spawn a child and grandchild for cleanup tests.",
    summary_ru="Тест: дерево процессов.", long_running=True)
def process_tree(directory: str, finish: bool = False) -> dict:
    root = Path(directory)
    (root / "worker.pid").write_text(str(os.getpid()), encoding="ascii")
    child = subprocess.Popen(
        [sys.executable, str(Path(__file__).with_name("process_tree_child.py")), directory, "child"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    deadline = time.monotonic() + 10
    ready = root / "grandchild.pid"
    while not ready.exists() or not ready.read_text(encoding="ascii").strip():
        if time.monotonic() >= deadline or child.poll() is not None:
            raise RuntimeError("test descendants did not start")
        time.sleep(0.01)
    report_progress(1, 1, "tree ready")
    if not finish:
        time.sleep(30)
    return {"child": child.pid}


@op("zz.cli_steps", summary="CLI-only long-running test op (reached through satk_op).",
    summary_ru="Тест: долгая операция без MCP-имени.", mcp=False, long_running=True)
def cli_steps(n: int = 2, label: str = "x") -> dict:
    """Report n progress steps from the worker subprocess.

    Args:
        n: number of steps.
        label: echoed back.
    """
    for i in range(n):
        print(f"stray stdout line {i}")  # must never reach the MCP stream
        report_progress(i + 1, n, f"cli step {i + 1}")
    return {"steps": n, "label": label, "pid": os.getpid()}


@op("zz.cli_quick", summary="CLI-only ordinary test op (reached through satk_op).",
    summary_ru="Тест: обычная операция без MCP-имени.", mcp=False)
def cli_quick(word: str, times: int = 1) -> dict:
    """Echo a word.

    Args:
        word: the word.
        times: repetitions.
    """
    return {"echo": " ".join([word] * times), "pid": os.getpid()}


@op("zz.cli_exit", summary="CLI-only op that exits like a CLI command (SystemExit guard test).",
    summary_ru="Тест: SystemExit.", mcp=False)
def cli_exit(code: int = 3) -> dict:
    """Raise SystemExit.

    Args:
        code: exit code.
    """
    raise SystemExit(code)
