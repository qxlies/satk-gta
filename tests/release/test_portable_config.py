"""Portable mode of satk.core.config (M3 A1): portable.txt makes the code's folder the workspace."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from satk.core import config as C

# "Test satk" in Russian: the zip must work from a folder with Cyrillic letters and a space.
FOLDER = "\u0422\u0435\u0441\u0442 satk"


def test_portable_root_needs_the_marker(tmp_path):
    assert C.portable_root(tmp_path) is None
    (tmp_path / C.PORTABLE_MARKER).write_text("x", encoding="utf-8")
    assert C.portable_root(tmp_path) == tmp_path


def test_checkout_is_not_portable():
    assert C.PORTABLE_ROOT is None or (C.REPO_ROOT / C.PORTABLE_MARKER).is_file()


def test_derived_workspace_and_config_files_in_portable_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(C, "MAIN_ROOT", None)
    monkeypatch.setattr(C, "PORTABLE_ROOT", tmp_path)
    assert C.derived_workspace() == C.Workspace(tmp_path, "portable")
    monkeypatch.setattr(C, "_DERIVED", C.derived_workspace())  # computed at import in a real installation
    env = {"APPDATA": str(tmp_path / "appdata"), "LOCALAPPDATA": str(tmp_path / "local")}
    files = C.config_files(env)
    assert C.user_config_file(env) not in files
    assert files == [tmp_path / "satk.toml"]
    monkeypatch.setattr(C, "PORTABLE_ROOT", None)
    assert C.user_config_file(env) in C.config_files(env)


def _layout(tmp_path: Path, repo_root: Path) -> Path:
    root = tmp_path / FOLDER / "satk-0.0.0-win64"
    shutil.copytree(repo_root / "src" / "satk", root / "src" / "satk", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(repo_root / "data", root / "data")
    (root / C.PORTABLE_MARKER).write_text("portable\n", encoding="utf-8")
    return root


_PROBE = (
    "import json\n"
    "from satk.core import config as C, resources as R\n"
    "c = C.load()\n"
    "print(json.dumps({'portable': str(C.PORTABLE_ROOT), 'workspace': str(c.paths.workspace),\n"
    "  'source': c.workspace_source, 'work': str(c.paths.work), 'sources': c.sources,\n"
    "  'data': R.data_source(), 'warnings': c.warnings}))\n"
)


def _probe(root: Path, appdata: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("SATK_", "PYTHON"))}
    env.update(PYTHONPATH=str(root / "src"), PYTHONUTF8="1", APPDATA=str(appdata), LOCALAPPDATA=str(appdata),
               SATK_DETECT="0")
    p = subprocess.run([sys.executable, "-X", "utf8", "-c", _PROBE], cwd=root, env=env, capture_output=True,
                       text=True, encoding="utf-8", timeout=120)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout.strip().splitlines()[-1])


def test_unpacked_layout_uses_its_own_folder(tmp_path, repo_root):
    """The zip layout (src/satk + data + portable.txt) in a Cyrillic path: workspace = that folder,
    the per-user satk.toml (pointing elsewhere) is ignored, data comes from <folder>/data."""
    root = _layout(tmp_path, repo_root)
    appdata = tmp_path / "appdata"
    (appdata / "satk").mkdir(parents=True)
    other = tmp_path / "elsewhere"
    (appdata / "satk" / "satk.toml").write_text(f"[paths]\nworkspace = '{other.as_posix()}'\n", encoding="utf-8")
    d = _probe(root, appdata)
    assert Path(d["portable"]) == root
    assert Path(d["workspace"]) == root and d["source"] == "portable"
    assert Path(d["work"]) == root / "work"
    assert d["sources"] == ["defaults"]  # the user's file was not read
    assert d["data"] == "checkout"

    (root / "satk.toml").write_text("[index]\ndefault_profile = 'game'\n", encoding="utf-8")
    d = _probe(root, appdata)
    assert d["sources"] == ["defaults", (root / "satk.toml").as_posix()]
    assert Path(d["workspace"]) == root and not d["warnings"]

    # satk.toml copied from an older version's folder (or the folder was moved): the folder still wins
    (root / "satk.toml").write_text(f"[paths]\nworkspace = '{other.as_posix()}'\n", encoding="utf-8")
    d = _probe(root, appdata)
    assert Path(d["workspace"]) == root and d["source"] == "portable" and Path(d["work"]) == root / "work"
    assert [w for w in d["warnings"] if w.startswith("PORTABLE_WORKSPACE:")]


@pytest.mark.skipif(os.name != "nt", reason="the launchers are Windows .cmd files")
def test_portable_launcher_text(repo_root):
    for name in ("satk.cmd", "satk-mcp.cmd", "Start satk.cmd"):
        raw = (repo_root / "packaging" / "portable" / name).read_bytes()
        raw.decode("ascii")  # cmd.exe reads the file in the console code page: ASCII only
    main = (repo_root / "packaging" / "portable" / "satk.cmd").read_text(encoding="ascii")
    assert '"%~dp0python\\python.exe" -s -X utf8 -m satk %*' in main
    mcp = (repo_root / "packaging" / "portable" / "satk-mcp.cmd").read_text(encoding="ascii")
    assert "-m satk.mcp" in mcp
