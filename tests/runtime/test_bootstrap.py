"""``scripts/bootstrap.ps1`` (M3 A2): Python 3.12-3.14, -Deps without pytest, -Dev with pytest and hooks.

Nothing here downloads: the script runs against the venv the tests run in, without -Deps/-Dev.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt" or shutil.which("powershell") is None,
                                reason="Windows PowerShell script")


def _script(repo_root: Path) -> Path:
    return repo_root / "scripts" / "bootstrap.ps1"


def _run(repo_root: Path, tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "SATK_HOME": str(tmp_path / "ws"), "PYTHONUTF8": "1"}
    return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                           str(_script(repo_root)), "-TempDir", str(tmp_path / "t"), *args],
                          capture_output=True, encoding="utf-8", errors="replace", timeout=180, env=env,
                          stdin=subprocess.DEVNULL)


def test_rejects_unsupported_python(repo_root, tmp_path):
    r = _run(repo_root, tmp_path, "-PythonVersion", "3.11", "-Venv", str(tmp_path / "v"))
    assert r.returncode != 0 and "Python 3.11 is not supported; use one of 3.12, 3.13, 3.14" in r.stdout + r.stderr
    assert not (tmp_path / "v").exists()


@pytest.mark.skipif(sys.prefix == sys.base_prefix, reason="tests do not run in a venv")
def test_existing_venv_without_downloads(repo_root, tmp_path):
    venv = Path(sys.prefix)
    r = _run(repo_root, tmp_path, "-Venv", str(venv))
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert f"python {sys.version.split()[0]} at" in out and '"ok":true' in out
    assert f"SATK_PYTHON={venv / 'Scripts' / 'python.exe'}" in out
    assert "pip install" not in out and "git hooks" not in out  # no -Deps/-Dev: no network, no hooks


def test_deps_and_dev_split():
    """-Deps installs the run-time extras only; pytest, hooks and the lock belong to -Dev."""
    text = _script(Path(__file__).resolve().parents[2]).read_text(encoding="utf-8")
    assert '$extras = @("img", "num", "mcp")' in text and 'if ($Dev) { $extras += "dev" }' in text
    assert "if ($Dev -and -not $NoHooks)" in text
    assert '$supported = @("3.12", "3.13", "3.14")' in text
    assert text.isascii()  # Windows PowerShell 5.1 reads BOM-less scripts in the ANSI code page
