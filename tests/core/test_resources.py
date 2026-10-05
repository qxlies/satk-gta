"""satk.core.resources: data/ for checkouts and installed packages (M2-12, PORTABILITY §7)."""

from __future__ import annotations

import hashlib
import json
import os
import runpy
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath

import pytest

from satk.core import config as C
from satk.core import detect as D
from satk.core import resources as R
from satk.core.errors import SatkError


@pytest.fixture
def fresh(monkeypatch):
    """Resolve the data root again in this test (and forget it afterwards)."""
    R.reset()
    yield monkeypatch
    R.reset()


@pytest.fixture
def setup_py(repo_root):
    """The stdlib part of ``setup.py`` (``data_files``, ``copy_data``) without running setuptools."""
    return runpy.run_path(str(repo_root / "setup.py"), run_name="satk_setup_under_test")


def _fake_data(root: Path, exe_db: dict | None = None) -> Path:
    (root / "manifests").mkdir(parents=True)
    (root / "manifests" / "a.json").write_text("{}", encoding="utf-8")
    (root / "manifests" / "b.txt").write_text("b", encoding="utf-8")
    (root / "exe_versions.json").write_text(json.dumps(exe_db or {"variants": []}), encoding="utf-8")
    return root


# --------------------------------------------------------------------------- checkout


def test_checkout_reads_repo_data(fresh, repo_root):
    assert R.data_source() == "checkout"
    assert R.data_root() == C.DATA_ROOT == repo_root / "data"
    db = R.read_json("exe_versions.json")
    assert db["format"] == "satk.exe-versions/1" and db["variants"]
    assert R.read_json("exe_versions.json") == json.loads((repo_root / "data" / "exe_versions.json").read_text("utf-8"))
    assert "manifests/stock-1.0us-hoodlum.json" in R.list_files("manifests", pattern="*.json")
    inf = R.info()
    assert inf["source"] == "checkout" and inf["files"] >= 3 and inf["root"] == (repo_root / "data").as_posix()


def test_names_whole_or_in_parts(fresh):
    a = R.data_path("manifests/stock-1.0us-hoodlum.json")
    assert a == R.data_path("manifests", "stock-1.0us-hoodlum.json")
    assert a == R.data_path(PurePosixPath("manifests") / "stock-1.0us-hoodlum.json")
    assert a == R.data_path(Path("manifests") / "stock-1.0us-hoodlum.json")  # backslashes on Windows are fine here
    assert R.data_path() == R.data_root() and R.data_path("./manifests/") == R.data_root() / "manifests"
    assert R.exists("manifests") and not R.exists("nope.json")
    assert R.read_bytes("manifests", "stock-1.0us-hoodlum.json") == a.read_bytes()


@pytest.mark.parametrize("bad", ["../pyproject.toml", "manifests/../../x", "/etc/passwd", "C:/x", "c:x",
                                 "manifests\\a.json"])
def test_bad_names_are_refused(fresh, bad):
    with pytest.raises(SatkError) as ei:
        R.data_path(bad)
    assert ei.value.code == "BAD_PARAMS"


def test_missing_file_is_not_found_with_hint(fresh):
    with pytest.raises(SatkError) as ei:
        R.read_json("manifests", "no-such.json")
    assert ei.value.code == "NOT_FOUND" and "pip install" in ei.value.hint
    with pytest.raises(SatkError) as ei:
        R.read_text()
    assert ei.value.code == "BAD_PARAMS"
    assert R.list_files("no-such-dir") == []


# --------------------------------------------------------------------------- resolution order


def test_package_copy_wins(fresh, tmp_path):
    pkg = _fake_data(tmp_path / "satk" / "_data", {"variants": [{"name": "x"}]})
    fresh.setattr(R, "_package_dir", lambda: pkg)
    assert R.data_source() == "package" and R.data_root() == pkg
    assert R.read_json("exe_versions.json") == {"variants": [{"name": "x"}]}
    assert R.list_files("manifests") == ["manifests/a.json", "manifests/b.txt"]
    assert R.list_files("manifests", pattern="*.json") == ["manifests/a.json"]
    assert R.info() == {"root": pkg.as_posix(), "source": "package", "files": 3}


def test_real_package_dir_is_absent_in_a_checkout(fresh, repo_root):
    assert R._package_dir() is None
    assert not (repo_root / "src" / "satk" / R.PACKAGE_DATA).exists()


def test_nothing_found(fresh, tmp_path):
    fresh.setattr(R, "_package_dir", lambda: None)
    fresh.setattr(R, "DATA_ROOT", tmp_path / "gone")
    assert R.data_source() == "missing" and R.data_root() == tmp_path / "gone"
    assert R.info()["files"] == 0 and not R.exists("exe_versions.json")
    with pytest.raises(SatkError) as ei:
        R.read_bytes("exe_versions.json")
    assert ei.value.code == "NOT_FOUND"


def test_broken_json_is_internal(fresh, tmp_path):
    pkg = _fake_data(tmp_path / "d")
    (pkg / "exe_versions.json").write_text("{nope", encoding="utf-8")
    (pkg / "latin.txt").write_bytes(b"\xff\xfe\xfa")
    fresh.setattr(R, "_package_dir", lambda: pkg)
    for name in ("exe_versions.json", "latin.txt"):
        with pytest.raises(SatkError) as ei:
            R.read_json(name)
        assert ei.value.code == "INTERNAL"


# --------------------------------------------------------------------------- readers converted to resources


def test_exe_versions_come_from_resources(fresh, tmp_path):
    blob = b"not an exe, just bytes"
    sha = hashlib.sha256(blob).hexdigest()
    db = {"variants": [{"sha256": sha, "name": "fake", "variant": "Fake 9.9", "layout": "x", "supported": False}]}
    pkg = _fake_data(tmp_path / "d", db)
    fresh.setattr(R, "_package_dir", lambda: pkg)
    fresh.setattr(D, "_EXE_VERSIONS", None)
    got = D.classify_exe(blob)
    assert got["name"] == "fake" and got["match"] == "sha256"
    # no data at all: every executable is unknown, nothing raises
    R.reset()
    fresh.setattr(R, "_package_dir", lambda: None)
    fresh.setattr(R, "DATA_ROOT", tmp_path / "gone")
    fresh.setattr(D, "_EXE_VERSIONS", None)
    assert D.classify_exe(blob)["variant"] == "unknown"


def test_game_manifests_live_in_the_data_root(fresh, tmp_path):
    from satk.game import manifests as M

    assert M.DATA_DIR == R.data_path("manifests")
    assert len(M.load_stock()) == len(R.read_json("manifests", M.STOCK_MANIFEST)["entries"])
    with pytest.raises(SatkError) as ei:
        M.load_stock(R.data_path("manifests", "missing.json"))
    assert ei.value.hint == R.HINT
    with pytest.raises(SatkError) as ei:
        M.load_stock(tmp_path / "m.json")
    assert "--manifest" in ei.value.hint


def test_version_and_doctor_name_the_data(fresh, satk_home, run_cli, tmp_path):
    from satk.core import ops as O

    assert run_cli(["version", "--json"]).json["data"] == R.data_root().as_posix()
    ok = O._check_package_data()
    assert ok["status"] == "ok" and "(checkout)" in ok["msg"] and "exe variants" in ok["msg"]
    R.reset()
    fresh.setattr(R, "_package_dir", lambda: None)
    fresh.setattr(R, "DATA_ROOT", tmp_path / "gone")
    bad = O._check_package_data()
    assert bad["status"] == "fail" and bad["fix"] == R.HINT
    R.reset()
    empty = _fake_data(tmp_path / "empty")
    shutil.rmtree(empty / "manifests")
    fresh.setattr(R, "_package_dir", lambda: empty)
    assert O._check_package_data()["status"] == "fail"


# --------------------------------------------------------------------------- build hook (setup.py) and installed layout


def test_setup_ships_every_data_file(setup_py, repo_root, tmp_path):
    files = setup_py["data_files"]()
    names = [rel for _, rel in files]
    on_disk = sorted(p.relative_to(repo_root / "data").as_posix() for p in (repo_root / "data").rglob("*")
                     if p.is_file() and "__pycache__" not in p.parts and not p.name.startswith("."))
    assert names == on_disk and "exe_versions.json" in names
    assert "manifests/stock-1.0us-hoodlum.json" in names
    # junk is never shipped
    src = _fake_data(tmp_path / "src")
    (src / "__pycache__").mkdir()
    (src / "__pycache__" / "x.pyc").write_bytes(b"x")
    (src / ".hidden").write_text("x", encoding="utf-8")
    (src / "Thumbs.db").write_bytes(b"x")
    assert [rel for _, rel in setup_py["data_files"](src)] == ["exe_versions.json", "manifests/a.json",
                                                              "manifests/b.txt"]
    written = setup_py["copy_data"](tmp_path / "build", src)
    out = tmp_path / "build" / "satk" / "_data"
    assert sorted(Path(w) for w in written) == sorted([out / "exe_versions.json", out / "manifests" / "a.json",
                                                       out / "manifests" / "b.txt"])
    assert (out / "manifests" / "b.txt").read_text(encoding="utf-8") == "b"
    with pytest.raises(RuntimeError):
        setup_py["copy_data"](tmp_path / "build2", tmp_path / "no-data")


def test_sdist_and_package_data_config(repo_root):
    import tomllib

    lines = (repo_root / "MANIFEST.in").read_text(encoding="utf-8").splitlines()
    assert "graft data" in lines and "graft src/satk" in lines
    meta = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    assert meta["tool"]["setuptools"]["packages"]["find"]["where"] == ["src"]
    assert meta["build-system"]["build-backend"] == "setuptools.build_meta"


def test_installed_layout_reads_packaged_data(setup_py, repo_root, tmp_path):
    """A non-editable install = src/satk + satk/_data in site-packages, no checkout around it."""
    site = tmp_path / "site"
    shutil.copytree(repo_root / "src" / "satk", site / "satk", ignore=shutil.ignore_patterns("__pycache__"))
    setup_py["copy_data"](site)
    (tmp_path / "ws" / "work").mkdir(parents=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("SATK_") and k != "PYTHONPATH"}
    env.update(PYTHONPATH=str(site), PYTHONUTF8="1", SATK_HOME=str(tmp_path / "ws"), SATK_CONFIG="none",
               SATK_DETECT="0")
    code = (
        "import json\n"
        "from satk.core import config, detect, resources\n"
        "from satk.game import manifests as M\n"
        "print(json.dumps({'info': resources.info(), 'repo_data': config.DATA_ROOT.is_dir(),\n"
        "  'variants': len(detect.exe_versions()['variants']), 'stock': len(M.load_stock()),\n"
        "  'data_dir': M.DATA_DIR.as_posix()}))\n"
    )
    p = subprocess.run([sys.executable, "-X", "utf8", "-c", code], cwd=tmp_path, env=env, capture_output=True,
                       text=True, encoding="utf-8", timeout=120)
    assert p.returncode == 0, p.stderr
    d = json.loads(p.stdout.strip().splitlines()[-1])
    pkg = (site / "satk" / "_data").as_posix()
    assert d["info"]["source"] == "package" and d["info"]["root"] == pkg and not d["repo_data"]
    assert d["data_dir"] == pkg + "/manifests"
    expect = json.loads((repo_root / "data" / "exe_versions.json").read_text(encoding="utf-8"))
    assert d["variants"] == len(expect["variants"]) > 0
    stock = json.loads((repo_root / "data" / "manifests" / "stock-1.0us-hoodlum.json").read_text(encoding="utf-8"))
    assert d["stock"] == len(stock["entries"])
