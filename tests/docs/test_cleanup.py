"""satk.docs.cleanup (WP-12): removal that retries and reports, waiting for a process object."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from satk.docs.cleanup import remove_tree, wait_process_exit


def _tree(root: Path) -> Path:
    d = root / "run"
    (d / "logs").mkdir(parents=True)
    (d / "logs" / "viewer.log").write_text("x" * 100, encoding="utf-8")
    (d / "index").mkdir()
    (d / "index" / "a.sqlite").write_bytes(b"\0" * 10)
    return d


def test_remove_tree_plain_and_missing(tmp_path: Path):
    d = _tree(tmp_path)
    assert remove_tree(d, seconds=1) == [] and not d.exists()
    assert remove_tree(tmp_path / "never", seconds=0) == []


@pytest.mark.skipif(os.name != "nt", reason="open files block deletion only on Windows")
def test_remove_tree_retries_while_a_file_is_held(tmp_path: Path):
    """The e2e leftover: the log was still open when the single rmtree(ignore_errors=True) ran."""
    d = _tree(tmp_path)
    f = open(d / "logs" / "viewer.log", "ab")  # CPython opens without FILE_SHARE_DELETE
    threading.Timer(0.8, f.close).start()
    t0 = time.monotonic()
    assert remove_tree(d, seconds=10) == []
    assert not d.exists() and time.monotonic() - t0 >= 0.7


@pytest.mark.skipif(os.name != "nt", reason="open files block deletion only on Windows")
def test_remove_tree_reports_what_is_left(tmp_path: Path):
    d = _tree(tmp_path)
    with open(d / "logs" / "viewer.log", "ab"):
        left = remove_tree(d, seconds=0.3)
    assert left == ["logs/viewer.log"]
    assert remove_tree(d, seconds=1) == []


def test_wait_process_exit():
    assert wait_process_exit(None) is True and wait_process_exit(0) is True
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert wait_process_exit(p.pid, 0.2) is False  # still running
    finally:
        p.kill()
    assert wait_process_exit(p.pid, 10) is True
    p.wait()
