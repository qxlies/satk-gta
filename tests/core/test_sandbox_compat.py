"""``tests/sandbox_compat.py``: probes and workarounds for the Codex Windows sandbox (nothing changes elsewhere)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))  # tests/: sandbox helper; appended, so it never shadows a conftest
import sandbox_compat as S  # noqa: E402


def _fake_run(code: int, text: bytes = b""):
    def run(argv, **_kw):
        return subprocess.CompletedProcess(argv, code, stdout=b"", stderr=text)

    return run


def test_shell_failure_reports_why_a_shell_cannot_run(monkeypatch):
    probe = S.shell_failure.__wrapped__  # the uncached function
    monkeypatch.setattr(subprocess, "run", _fake_run(0))
    assert probe("sh") is None
    msys = b"0 [main] sh (123) sh.exe: *** fatal error - CreateFileMapping S-1-5-21-1-2-3-1001.1, Win32 error 5.  Terminating."
    monkeypatch.setattr(subprocess, "run", _fake_run(256, msys))
    assert probe("sh") == "MSYS CreateFileMapping denied, Win32 error 5"
    monkeypatch.setattr(subprocess, "run", _fake_run(2, b"some\n  other   failure\n"))
    assert probe("sh") == "exit 2: other failure"

    def missing(argv, **_kw):
        raise FileNotFoundError(2, "no such file")

    monkeypatch.setattr(subprocess, "run", missing)
    assert "FileNotFoundError" in probe("nope")


def test_skip_unless_shell_runs(monkeypatch):
    S.skip_unless_shell_runs(None)  # no shell found: the test's own skip condition handles it
    monkeypatch.setattr(S, "shell_failure", lambda exe: None)
    S.skip_unless_shell_runs("sh")
    monkeypatch.setattr(S, "shell_failure", lambda exe: "MSYS CreateFileMapping denied, Win32 error 5")
    with pytest.raises(pytest.skip.Exception) as e:
        S.skip_unless_shell_runs("C:/Git/usr/bin/sh.exe")
    assert str(e.value).startswith("sandbox: sh.exe cannot start") and "CreateFileMapping" in str(e.value)


def test_skip_unless_async_subprocess(monkeypatch):
    monkeypatch.setattr(S, "async_subprocess_failure", lambda: None)
    S.skip_unless_async_subprocess()
    monkeypatch.setattr(S, "async_subprocess_failure", lambda: "PermissionError: [WinError 5] Access is denied")
    with pytest.raises(pytest.skip.Exception) as e:
        S.skip_unless_async_subprocess()
    assert str(e.value).startswith("sandbox: asyncio subprocess pipes")


def test_mkdir_workaround_only_downgrades_the_owner_only_mode(monkeypatch, tmp_path):
    modes = []
    monkeypatch.setattr(S, "_original_mkdir", lambda path, mode=0o777, *, dir_fd=None: modes.append(mode))
    S._sandbox_mkdir(tmp_path / "a", 0o700)
    S._sandbox_mkdir(tmp_path / "b", 0o755)
    S._sandbox_mkdir(tmp_path / "c")
    assert modes == [0o777, 0o755, 0o777]


def test_apply_mkdir_workaround_is_a_noop_where_owner_only_dirs_work(monkeypatch):
    monkeypatch.setattr(os, "mkdir", S._original_mkdir)  # start unpatched (the plugin may be loaded); restored afterwards
    monkeypatch.setattr(S, "owner_only_dirs_unusable", lambda: False)
    before = os.mkdir
    assert S.apply_mkdir_workaround() is False and os.mkdir is before
    monkeypatch.setattr(S, "owner_only_dirs_unusable", lambda: True)
    assert S.apply_mkdir_workaround() is True and os.mkdir is S._sandbox_mkdir
    assert S.apply_mkdir_workaround() is True  # idempotent


def test_the_acl_probe_matches_reality(tmp_path):
    d = tmp_path / "owner-only"
    S._original_mkdir(d, 0o700)
    try:
        (d / "f").write_bytes(b"x")
        usable = True
    except OSError:
        usable = False
    assert S.owner_only_dirs_unusable() is (not usable)
