"""The ``satk.cmd`` / ``satk.sh`` shims, including git-worktree use (SPEC §2.3, §5.1 rule 2)."""

from __future__ import annotations

import satk
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
WINDOWS = os.name == "nt"


def _shared_workspace() -> Path | None:
    """Workspace whose ``tools/.venv`` the shims fall back to (``SATK_HOME``)."""
    from satk.core.config import MAIN_ROOT

    cands = []
    if REPO.name == "tools":
        cands.append(REPO.parent)
    if MAIN_ROOT is not None and MAIN_ROOT.name.lower() == "tools":
        cands.append(MAIN_ROOT.parent)
    cands += [Path(os.environ["SATK_HOME"])] if os.environ.get("SATK_HOME") else []
    for ws in cands:
        if (ws / "tools" / ".venv" / "Scripts" / "python.exe").is_file():
            return ws
    return None


def _main_venv_python() -> Path | None:
    from satk.core.config import MAIN_ROOT

    if MAIN_ROOT is None:
        return None
    p = MAIN_ROOT / ".venv" / "Scripts" / "python.exe"
    return p if p.is_file() else None


def _fake_worktree(tmp_path: Path, shim: str) -> Path:
    """A worktree anywhere: shim + src and a ``.git`` file whose gitdir's ``commondir`` names the real
    common dir of this repository (read only), like ``git worktree add <anywhere>`` creates."""
    from satk.core.config import git_common_dir

    wt = tmp_path / "any where" / "wt"
    wt.mkdir(parents=True)
    shutil.copy2(REPO / shim, wt / shim)
    shutil.copytree(REPO / "src", wt / "src", ignore=shutil.ignore_patterns("__pycache__"))
    gitdir = tmp_path / "gitdir"
    gitdir.mkdir()
    (gitdir / "commondir").write_text(str(git_common_dir(REPO)) + "\n", encoding="utf-8")
    (wt / ".git").write_text(f"gitdir: {gitdir.as_posix()}\n", encoding="utf-8")
    return wt


@pytest.mark.skipif(not WINDOWS, reason="satk.cmd is Windows-only")
def test_satk_cmd_worktree_anywhere_finds_the_main_venv(tmp_path):
    """PORTABILITY acceptance 6: no SATK_HOME; .git -> gitdir -> commondir -> <main>/.venv."""
    py = _main_venv_python()
    if py is None:
        pytest.skip("the main checkout has no .venv")
    wt = _fake_worktree(tmp_path, "satk.cmd")
    r = subprocess.run(["cmd", "/c", str(wt / "satk.cmd"), "config", "show", "--section", "paths"], cwd=tmp_path,
                       capture_output=True, env=_clean_env(), timeout=60)
    assert r.returncode == 0, r.stderr
    from satk.core.config import MAIN_ROOT, derived_workspace

    want = derived_workspace()
    if want is not None and MAIN_ROOT is not None and MAIN_ROOT.name.lower() == "tools":
        assert json.loads(r.stdout)["paths"]["workspace"] == _jp(want.path)
    r = subprocess.run(["cmd", "/c", str(wt / "satk.cmd"), "version"], cwd=tmp_path, capture_output=True,
                       env=_clean_env(), timeout=60)
    d = json.loads(r.stdout)
    assert d["exe"] == _jp(py) and d["src"] == _jp(wt / "src")


@pytest.mark.skipif(shutil.which("sh") is None and shutil.which("git") is None, reason="no POSIX sh (Git Bash) found")
def test_satk_sh_worktree_anywhere_finds_the_main_venv(tmp_path):
    py = _main_venv_python()
    sh = _find_sh()
    if py is None or sh is None:
        pytest.skip("no main .venv or no sh")
    wt = _fake_worktree(tmp_path, "satk.sh")
    r = subprocess.run([sh, (wt / "satk.sh").as_posix(), "version"], cwd=tmp_path, capture_output=True,
                       env=_clean_env(), timeout=60)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["exe"] == _jp(py)


def _clean_env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("SATK_", "PYTHON"))}
    env["SATK_DETECT"] = "nocache"  # discovery runs, the shared work/cache stays untouched
    env.update(extra)
    return env


def _jp(p: Path) -> str:
    s = os.path.abspath(p).replace("\\", "/")
    return s[0].upper() + s[1:]


@pytest.mark.skipif(not WINDOWS, reason="satk.cmd is Windows-only")
def test_satk_cmd_version(tmp_path):
    if not (REPO / ".venv" / "Scripts" / "python.exe").is_file() and _main_venv_python() is None:
        pytest.skip("no .venv in this checkout or its main checkout")
    r = subprocess.run(["cmd", "/c", str(REPO / "satk.cmd"), "version"], cwd=tmp_path, capture_output=True,
                       env=_clean_env(), timeout=60)
    assert r.returncode == 0, r.stderr
    d = json.loads(r.stdout)
    assert d["ok"] and d["python"].startswith("3.12.") and d["venv"] is True
    assert d["src"] == _jp(REPO / "src")


@pytest.mark.skipif(not WINDOWS, reason="satk.cmd is Windows-only")
def test_satk_cmd_exit_codes(tmp_path):
    ws = tmp_path / "ws"  # an isolated workspace: its src\ is a protected root
    (ws / "work").mkdir(parents=True)
    env = _clean_env(SATK_HOME=str(ws), SATK_CONFIG="none")
    r = subprocess.run(["cmd", "/c", str(REPO / "satk.cmd"), "dev", "guard-test", str(ws / "src" / "x.tmp")],
                       cwd=tmp_path, capture_output=True, env=env, timeout=60)
    assert r.returncode == 1 and json.loads(r.stdout)["error"]["code"] == "PROTECTED_PATH"
    r2 = subprocess.run(["cmd", "/c", str(REPO / "satk.cmd"), "no-such-command"], cwd=tmp_path,
                        capture_output=True, env=_clean_env(), timeout=60)
    assert r2.returncode == 2


@pytest.mark.skipif(not WINDOWS, reason="satk.cmd is Windows-only")
def test_satk_cmd_worktree_falls_back_to_shared_venv(tmp_path):
    ws = _shared_workspace()
    if ws is None:
        pytest.skip("no shared tools/.venv found")
    wt = tmp_path / "wt"  # looks like a worktree: shim + src, no .venv
    wt.mkdir()
    shutil.copy2(REPO / "satk.cmd", wt / "satk.cmd")
    shutil.copytree(REPO / "src", wt / "src", ignore=shutil.ignore_patterns("__pycache__"))
    r = subprocess.run(["cmd", "/c", str(wt / "satk.cmd"), "version"], cwd=tmp_path, capture_output=True,
                       env=_clean_env(SATK_HOME=str(ws)), timeout=60)
    assert r.returncode == 0, r.stderr
    d = json.loads(r.stdout)
    assert d["src"] == _jp(wt / "src")                       # code from the worktree
    assert d["exe"] == _jp(ws / "tools" / ".venv" / "Scripts" / "python.exe")  # shared interpreter
    assert d["repo"] == _jp(wt)


@pytest.mark.skipif(not WINDOWS, reason="cmd.exe code pages")
def test_satk_cmd_cp1251_console_with_cyrillic_paths(tmp_path):
    """WP-00 acceptance 7: chcp 1251 + PYTHONIOENCODING=cp1251, Cyrillic in a path."""
    shim = REPO / "satk.cmd"
    env = _clean_env(SATK_PATHS_BLENDER=r"D:\Игры\Блендер\blender.exe")
    r = subprocess.run(f'cmd /c "chcp 1251 >nul & set PYTHONIOENCODING=cp1251& "{shim}" config show --table"',
                       cwd=tmp_path, capture_output=True, env=env, timeout=60)
    assert r.returncode == 0, r.stderr
    text = r.stdout.decode("cp1251")
    assert "paths.blender: D:/Игры/Блендер/blender.exe" in text


def _find_sh() -> str | None:
    """``sh`` on PATH, else Git for Windows' own ``bin/sh.exe`` next to ``git``."""
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


SH = _find_sh()


@pytest.mark.skipif(SH is None, reason="no POSIX sh (Git Bash) found")
def test_satk_sh_version(tmp_path):
    env = _clean_env()
    ws = _shared_workspace()
    if ws is not None:
        env["SATK_HOME"] = str(ws)
    r = subprocess.run([SH, (REPO / "satk.sh").as_posix(), "version"], cwd=tmp_path, capture_output=True,
                       env=env, timeout=60)
    assert r.returncode == 0, r.stderr
    d = json.loads(r.stdout)
    assert d["ok"] and d["python"].startswith("3.12.")


def test_python_dash_m_satk(tmp_path):
    env = _clean_env(PYTHONPATH=str(REPO / "src"), PYTHONUTF8="1")
    r = subprocess.run([sys.executable, "-X", "utf8", "-m", "satk", "version"], cwd=tmp_path, capture_output=True,
                       env=env, timeout=60)
    assert r.returncode == 0 and json.loads(r.stdout)["satk"] == satk.__version__
