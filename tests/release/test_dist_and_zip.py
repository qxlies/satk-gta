"""Wheel, sdist and portable zip of satk dev release, from synthetic trees and the real HEAD (M3 A1)."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import os
import runpy
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from satk.core.errors import SatkError
from satk.release import build as B
from satk.release import dist as D
from satk.release.tree import Tree, export, read_version

PYPROJECT = b"""[project]
name = "satk-gta"
version = "9.9.9"
description = "demo"
readme = "README.md"
requires-python = ">=3.12"
license = { text = "MIT" }
dependencies = []

[project.optional-dependencies]
img = ["Pillow==1.0"]
dev = ["pytest==8"]

[project.scripts]
satk = "satk.core.cli:main"
"""


def _tree(**extra: bytes) -> Tree:
    files = {
        "pyproject.toml": PYPROJECT,
        "setup.py": b"# setup\n",
        "MANIFEST.in": b"graft data\ngraft src/satk\ngraft vendor\n",
        "README.md": b"# satk\n",
        "LICENSE": b"MIT License\n",
        "NOTICE.md": b"# NOTICE\n",
        "satk.toml.example": b"[paths]\n",
        "src/satk/__init__.py": b'__version__ = "9.9.9"\n',
        "src/satk/core/cli.py": b"def main():\n    return 0\n",
        "src/satk/index/schema.sql": b"CREATE TABLE t(x);\n",
        "src/satk/__pycache__/x.cpython-312.pyc": b"junk",
        "data/exe_versions.json": b'{"variants": []}\n',
        "data/.hidden": b"x",
        "vendor/foo/foo.py": b"FOO = 1\n",
        "vendor/foo/LICENSE": b"MIT\n",
        "docs/en/README.md": b"# docs\n",
        "docs/agent/workspace-CLAUDE.md": b"# our workspace\n",
        "tests/test_x.py": b"def test(): pass\n",
        "scripts/bootstrap.ps1": b"#\n",
        "blender/satk_blender/LICENSE": b"GPL\n",
        "packaging/CHANGELOG.md": b"# Changelog\n",
        "packaging/portable/satk.cmd": b"@echo off\r\n",
        "packaging/portable/Start satk.cmd": b"@echo off\r\ncmd /k\r\n",
        "packaging/portable/README-FIRST.txt": b"line 1\nline 2\n",
        "packaging/portable/portable.txt": b"portable\n",
        "packaging/release-lock.json": b"{}",
    }
    files.update(extra)
    return Tree(commit="0123456789abcdef0123456789abcdef01234567", timestamp=1_760_000_000, files=files)


# --------------------------------------------------------------------------- dist


def test_metadata_lists_extras_and_readme():
    text = D.metadata(_tree())
    head, body = text.split("\n\n", 1)
    lines = head.splitlines()
    assert lines[:4] == ["Metadata-Version: 2.1", "Name: satk-gta", "Version: 9.9.9", "Summary: demo"]
    assert "License: MIT" in lines and "Requires-Python: >=3.12" in lines
    assert "Provides-Extra: img" in lines and 'Requires-Dist: Pillow==1.0; extra == "img"' in lines
    assert "Description-Content-Type: text/markdown" in lines and body == "# satk\n"


def _record_ok(zf: zipfile.ZipFile, info: str) -> list[str]:
    rows = list(csv.reader(io.StringIO(zf.read(f"{info}/RECORD").decode("utf-8"))))
    for path, h, size in rows:
        if path == f"{info}/RECORD":
            assert h == size == ""
            continue
        data = zf.read(path)
        assert h == "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        assert int(size) == len(data)
    assert sorted(r[0] for r in rows) == sorted(zf.namelist())
    return [r[0] for r in rows]


def test_wheel_layout_and_record(tmp_path):
    w = D.build_wheel(_tree(), tmp_path)
    assert w.name == "satk_gta-9.9.9-py3-none-any.whl"
    with zipfile.ZipFile(w) as zf:
        names = zf.namelist()
        info = "satk_gta-9.9.9.dist-info"
        _record_ok(zf, info)
        assert names[-1].startswith(info)  # dist-info last
        assert {"satk/__init__.py", "satk/core/cli.py", "satk/index/schema.sql", "satk/_data/exe_versions.json",
                "satk/_vendor/foo/foo.py", "satk/_vendor/foo/LICENSE", f"{info}/licenses/LICENSE"} <= set(names)
        assert not [n for n in names if "__pycache__" in n or n.endswith(".hidden") or n.startswith(("tests/", "docs/"))]
        assert zf.read(f"{info}/entry_points.txt") == b"[console_scripts]\nsatk = satk.core.cli:main\n"
        assert b"Tag: py3-none-any" in zf.read(f"{info}/WHEEL")


def test_wheel_and_sdist_are_reproducible(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    for d in (a, b):
        D.build_wheel(_tree(), d)
        D.build_sdist(_tree(), d)
    for name in ("satk_gta-9.9.9-py3-none-any.whl", "satk_gta-9.9.9.tar.gz"):
        assert (a / name).read_bytes() == (b / name).read_bytes(), name


def test_sdist_contents(tmp_path):
    s = D.build_sdist(_tree(), tmp_path)
    with tarfile.open(s) as tf:
        names = tf.getnames()
        assert all(n.startswith("satk_gta-9.9.9/") for n in names)
        rel = {n.split("/", 1)[1] for n in names}
        assert {"PKG-INFO", "pyproject.toml", "setup.py", "MANIFEST.in", "README.md", "LICENSE", "NOTICE.md",
                "data/exe_versions.json", "src/satk/__init__.py", "vendor/foo/foo.py"} <= rel
        assert not [r for r in rel if r.startswith(("tests/", "docs/", "packaging/")) or "__pycache__" in r]
        assert tf.extractfile("satk_gta-9.9.9/PKG-INFO").read().startswith(b"Metadata-Version: 2.1\nName: satk-gta\n")


def test_sdist_trees_follow_manifest_in(repo_root):
    grafts = [ln.split()[1] for ln in (repo_root / "MANIFEST.in").read_text(encoding="utf-8").splitlines()
              if ln.startswith("graft ")]
    assert sorted(g.rstrip("/") + "/" for g in grafts) == sorted(D.SDIST_TREES)
    setup = runpy.run_path(str(repo_root / "setup.py"), run_name="satk_setup_under_test")
    assert tuple(setup["TARGET"]) == ("satk", D.PACKAGED_TREES["data/"])
    assert tuple(setup["VENDOR_TARGET"]) == ("satk", D.PACKAGED_TREES["vendor/"])


def test_shipped_filter_matches_setup_py(tmp_path, repo_root):
    setup = runpy.run_path(str(repo_root / "setup.py"), run_name="satk_setup_under_test")
    names = ["a.json", "m/x.json", ".hidden", "m/.git/x", "__pycache__/c.pyc", "m/Thumbs.db", "desktop.ini",
             "x.pyc", "y.pyo", "deep/dir/z.txt", "m/.DS_Store"]
    for n in names:
        p = tmp_path / "root" / n
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    by_setup = sorted(rel for _, rel in setup["data_files"](tmp_path / "root"))
    assert by_setup == sorted(n for n in names if D.shipped(n))


def test_read_version_needs_both_files_to_agree():
    assert read_version(_tree()) == "9.9.9"
    with pytest.raises(SatkError) as e:
        read_version(_tree(**{"src/satk/__init__.py": b'__version__ = "1.0.0"\n'}))
    assert e.value.code == "CHECK_FAILED"


# --------------------------------------------------------------------------- zip


EMBED_PTH = b"python312.zip\r\n.\r\n\r\n# Uncomment to run site.main() automatically\r\n#import site\r\n"


def _cache(tmp_path: Path) -> tuple[Path, dict]:
    cache = tmp_path / "cache"
    cache.mkdir()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("python.exe", b"MZ fake")
        zf.writestr("python312._pth", EMBED_PTH)
        zf.writestr("python312.zip", b"PK fake stdlib")
    embed = buf.getvalue()
    (cache / "python-3.12.10-embed-amd64.zip").write_bytes(embed)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("demo/__init__.py", b"")
        zf.writestr("demo-1.0.dist-info/METADATA", b"Name: demo\n")
        zf.writestr("demo-1.0.data/scripts/demo.exe", b"MZ")
    whl = buf.getvalue()
    (cache / "demo-1.0-py3-none-any.whl").write_bytes(whl)
    lock = {
        "lock_version": 1, "target": {"python": "3.12.10", "platform": "win_amd64"},
        "python": {"version": "3.12.10", "file": "python-3.12.10-embed-amd64.zip", "size": len(embed),
                   "url": "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip",
                   "sha256": hashlib.sha256(embed).hexdigest()},
        "wheels": [{"name": "demo", "version": "1.0", "file": "demo-1.0-py3-none-any.whl", "size": len(whl),
                    "url": "https://files.pythonhosted.org/demo-1.0-py3-none-any.whl",
                    "sha256": hashlib.sha256(whl).hexdigest()}],
    }
    return cache, lock


def test_zip_layout(tmp_path):
    cache, lock = _cache(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    z, manifest = B.build_zip(_tree(), lock, cache, out, offline=True, ref="HEAD")
    assert z.name == "satk-9.9.9-win64.zip" and not list(out.glob("*.part"))
    with zipfile.ZipFile(z) as zf:
        names = [n.split("/", 1)[1] for n in zf.namelist()]
        top = {n.split("/", 1)[0] for n in zf.namelist()}
        assert top == {"satk-9.9.9-win64"}

        def read(rel: str) -> bytes:
            return zf.read("satk-9.9.9-win64/" + rel)

        assert {"satk.cmd", "Start satk.cmd", B.START_RU, "README-FIRST.txt", "portable.txt", "CHANGELOG.md",
                "LICENSE", "NOTICE.md", "README.md", "satk.toml.example", "RELEASE.json", "python/python.exe",
                "python/python312.zip", "python/python312._pth", "python/Lib/site-packages/demo/__init__.py",
                "python/Lib/site-packages/demo-1.0.dist-info/METADATA", "src/satk/__init__.py",
                "src/satk/index/schema.sql", "data/exe_versions.json", "vendor/foo/foo.py", "docs/en/README.md",
                "blender/satk_blender/LICENSE"} <= set(names)
        for gone in ("docs/agent/workspace-CLAUDE.md", "tests/test_x.py", "scripts/bootstrap.ps1", "setup.py",
                     "pyproject.toml", "packaging/release-lock.json", "data/.hidden"):
            assert gone not in names, gone
        assert not [n for n in names if "__pycache__" in n or "/scripts/" in n]
        assert read(B.START_RU) == read("Start satk.cmd")
        assert read("README-FIRST.txt") == b"line 1\r\nline 2\r\n"
        assert read("python/python312._pth") == b"python312.zip\r\n.\r\n..\\src\r\nimport site\r\n"
        rel = json.loads(read("RELEASE.json"))
        assert rel["version"] == "9.9.9" and rel["commit"].startswith("0123456789") and rel["files"] == len(names)
        assert rel["wheels"] == {"demo": "1.0"} and rel["python"] == "3.12.10"
        info = zf.getinfo("satk-9.9.9-win64/" + B.START_RU)
        assert info.flag_bits & 0x800  # UTF-8 file name flag: Explorer and Expand-Archive keep the Cyrillic name
    assert manifest["files"] == len(names)
    again = tmp_path / "again"
    again.mkdir()
    z2, _ = B.build_zip(_tree(), lock, cache, again, offline=True, ref="HEAD")
    assert z2.read_bytes() == z.read_bytes(), "same tree + same lock = same zip"


def test_zip_refuses_bad_inputs(tmp_path):
    cache, lock = _cache(tmp_path)
    (tmp_path / "out").mkdir()
    bad = dict(lock, wheels=[dict(lock["wheels"][0], sha256="0" * 64)])
    with pytest.raises(SatkError) as e:
        B.build_zip(_tree(), bad, cache, tmp_path / "out", offline=True, ref="HEAD")
    assert e.value.code == "NOT_READY"  # offline + wrong digest: the cached file is not trusted
    tree = _tree()
    del tree.files["packaging/portable/Start satk.cmd"]
    with pytest.raises(SatkError) as e:
        B.build_zip(tree, lock, cache, tmp_path / "out", offline=True, ref="HEAD")
    assert e.value.code == "CHECK_FAILED"
    assert not list((tmp_path / "out").iterdir())
    clash = _tree(**{"packaging/portable/RELEASE.json": b"{}"})
    with pytest.raises(SatkError):
        B.build_zip(clash, lock, cache, tmp_path / "out", offline=True, ref="HEAD")


def test_excluded_files_never_reach_the_release_files(tmp_path):
    """dev release drops what data/public-exclude.txt lists; the built archives are read back."""
    from satk.release import public as P

    cache, lock = _cache(tmp_path)
    tree = _tree(**{"data/manifests/install-2026-10-04.json": b'{"mine": 1}\n',
                    "data/manifests/stock.json": b"{}\n",
                    P.EXCLUDE_FILE: b"/data/manifests/install-*.json\n"})
    rules = P.load_rules(tree)
    for name, t in (("raw", tree), ("filtered", P.apply_excludes(tree, rules)[0])):
        out = tmp_path / name
        out.mkdir()
        z, _ = B.build_zip(t, lock, cache, out, offline=True, ref="HEAD")
        found = P.check_release_files([z, *B.build_dists(t, out)], rules, strict=True)
        if name == "raw":
            assert sorted(f.path.split("!")[0] for f in found) == sorted(
                ["satk-9.9.9-win64.zip", "satk_gta-9.9.9-py3-none-any.whl", "satk_gta-9.9.9.tar.gz"])
            assert {f.rule for f in found} == {"excluded"}
        else:
            assert found == []
            with zipfile.ZipFile(z) as zf:
                names = zf.namelist()
            assert "satk-9.9.9-win64/data/manifests/stock.json" in names
            assert not [n for n in names if "install-" in n]


def test_pth_text_keeps_entries_and_adds_src():
    assert B.pth_text(EMBED_PTH.decode()) == "python312.zip\r\n.\r\n..\\src\r\nimport site\r\n"
    assert B.pth_text("python313.zip\n.\nimport site\n") == "python313.zip\r\n.\r\n..\\src\r\nimport site\r\n"


def test_sha256sums_format(tmp_path):
    a, b = tmp_path / "b.zip", tmp_path / "a.whl"
    a.write_bytes(b"1"), b.write_bytes(b"2")
    sums = B.sha256sums([a, b], tmp_path / "SHA256SUMS.txt")
    text = (tmp_path / "SHA256SUMS.txt").read_text(encoding="utf-8")
    assert text == "".join(f"{hashlib.sha256(x).hexdigest()}  {n}\n" for n, x in (("a.whl", b"2"), ("b.zip", b"1")))
    assert list(sums) == ["a.whl", "b.zip"]


def test_clean_env_drops_developer_variables(tmp_path, monkeypatch):
    monkeypatch.setenv("SATK_HOME", "x")
    monkeypatch.setenv("PYTHONPATH", "y")
    monkeypatch.setenv("VIRTUAL_ENV", "z")
    env = B.clean_env(tmp_path / "t")
    assert not [k for k in env if k.upper().startswith(("SATK_", "PYTHON", "VIRTUAL_ENV"))]
    assert env["TEMP"] == env["TMP"] == str(tmp_path / "t") and (tmp_path / "t").is_dir()


# --------------------------------------------------------------------------- the real HEAD


@pytest.fixture(scope="module")
def head_tree(request) -> Tree:
    repo = Path(__file__).resolve().parents[2]
    try:
        return export(repo, "HEAD")
    except SatkError as e:  # pragma: no cover - not a git checkout
        pytest.skip(f"no git checkout: {e.msg}")


def test_head_has_what_the_release_needs(head_tree):
    from satk.release.lock import LOCK_PATH, parse

    assert read_version(head_tree)
    parse(head_tree.text(LOCK_PATH))
    names = [p[len(B.PORTABLE_DIR):] for p in head_tree.under(B.PORTABLE_DIR)]
    assert {"satk.cmd", "satk-mcp.cmd", "Start satk.cmd", "README-FIRST.txt", "README-FIRST.ru.txt",
            "portable.txt"} <= set(names)
    assert head_tree.files["packaging/portable/satk.cmd"].count(b"\r\n") >= 3  # .gitattributes: *.cmd eol=crlf
    assert B.CHANGELOG in head_tree.files
    text = head_tree.text("packaging/portable/README-FIRST.txt")
    text.encode("ascii")  # the English readme is ASCII; the Russian one is a separate file
    ru = head_tree.text("packaging/portable/README-FIRST.ru.txt")
    assert any(0x400 <= ord(ch) <= 0x4FF for ch in ru)


def test_head_wheel_installs_by_unzipping(head_tree, tmp_path):
    """The wheel of HEAD unpacked into a bare site dir: satk imports, reads satk/_data, has its metadata."""
    w = D.build_wheel(head_tree, tmp_path)
    site = tmp_path / "site"
    with zipfile.ZipFile(w) as zf:
        info = next(n.split("/")[0] for n in zf.namelist() if n.endswith(".dist-info/RECORD"))
        _record_ok(zf, info)
        zf.extractall(site)
    (tmp_path / "ws" / "work").mkdir(parents=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("SATK_", "PYTHON"))}
    env.update(PYTHONPATH=str(site), PYTHONUTF8="1", SATK_HOME=str(tmp_path / "ws"), SATK_CONFIG="none",
               SATK_DETECT="0")
    code = ("import json, importlib.metadata as md\n"
            "from satk.core import resources\n"
            "print(json.dumps({'source': resources.data_source(), 'version': md.version('satk-gta'),\n"
            "  'scripts': [e.name for e in md.entry_points(group='console_scripts') if e.value.startswith('satk.')]}))\n")
    p = subprocess.run([sys.executable, "-X", "utf8", "-c", code], cwd=tmp_path, env=env, capture_output=True,
                       text=True, encoding="utf-8", timeout=120)
    assert p.returncode == 0, p.stderr
    d = json.loads(p.stdout.strip().splitlines()[-1])
    assert d == {"source": "package", "version": read_version(head_tree), "scripts": ["satk"]}
