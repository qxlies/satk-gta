"""Release lock (packaging/release-lock.json) and wheel handling without pip (M3 A1)."""

from __future__ import annotations

import importlib.util
import io
import re
import zipfile

import pytest

from satk.core.errors import SatkError
from satk.release import fetch as F
from satk.release import lock as L
from satk.release.wheels import parse_filename, wheel_entries


def _lock(repo_root):
    return L.load(repo_root / L.LOCK_PATH)


def test_committed_lock_is_consistent(repo_root):
    lock = _lock(repo_root)
    assert lock["lock_version"] == L.LOCK_VERSION
    py = lock["python"]
    assert re.fullmatch(r"3\.12\.\d+", py["version"]) and py["file"] == f"python-{py['version']}-embed-amd64.zip"
    assert py["openpgp"] == L.PYTHON_PGP
    for entry in [py, *lock["wheels"]]:
        assert re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]), entry
        F.check_url(entry["url"])  # https on python.org / PyPI only
        assert entry["size"] > 0
    pins = L._lock_pins((repo_root / "requirements.lock").read_text(encoding="ascii"))
    names = {w["name"] for w in lock["wheels"]}
    assert {"numpy", "pillow", "mcp"} <= names
    assert not names & {"pytest", "pip", "pluggy", "iniconfig"}, "no test tools in the release"
    for w in lock["wheels"]:
        assert pins.get(w["name"]) == w["version"], f"{w['name']}: lock {w['version']} != requirements.lock"
        tags = parse_filename(w["file"])
        assert tags["plat"] in ("win_amd64", "any") and tags["py"] in ("cp312", "cp311", "py3", "py2.py3"), w["file"]
    assert lock["roots"] == L.runtime_roots((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    assert sorted(names | set(lock["excluded"])) == sorted(pins), "closure + excluded = requirements.lock"
    assert L.render(lock) == (repo_root / L.LOCK_PATH).read_text(encoding="utf-8")


def test_runtime_roots_skip_dev():
    text = '[project.optional-dependencies]\nimg = ["Pillow==1"]\nnum = ["numpy==2"]\ndev = ["pytest==8"]\n'
    assert L.runtime_roots(text) == ["numpy", "pillow"]


def test_parse_rejects_bad_locks():
    with pytest.raises(SatkError) as e:
        L.parse("{")
    assert e.value.code == "CHECK_FAILED"
    with pytest.raises(SatkError):
        L.parse('{"lock_version": 1, "python": {}, "wheels": []}')


needs_packaging = pytest.mark.skipif(importlib.util.find_spec("packaging") is None,
                                     reason="--relock needs 'packaging' (installed with pytest)")


class _Dist:
    def __init__(self, name, version, requires=()):
        self.metadata = {"Name": name}
        self.version = version
        self.requires = list(requires)


@needs_packaging
def test_closure_follows_markers_and_extras():
    dists = {
        "app": _Dist("app", "1", ["lib[crypto]>=1", "winonly; sys_platform == 'win32'",
                                 "linuxonly; sys_platform == 'linux'", "old; python_version < '3.10'",
                                 "testing; extra == 'dev'"]),
        "lib": _Dist("Lib", "2", ["crypto-dep; extra == 'crypto'", "plain"]),
        "winonly": _Dist("winonly", "3"),
        "crypto-dep": _Dist("crypto_dep", "4"),
        "plain": _Dist("plain", "5"),
    }

    def dist(name):
        return dists[L._canon(name)]

    got = L.closure(["app"], distribution=dist)
    assert got == {"app": ("app", "1"), "crypto-dep": ("crypto_dep", "4"), "lib": ("Lib", "2"),
                   "plain": ("plain", "5"), "winonly": ("winonly", "3")}
    with pytest.raises(SatkError) as e:
        L.closure(["missing"], distribution=lambda n: (_ for _ in ()).throw(KeyError(n)))
    assert e.value.code == "DEPENDENCY"


def _f(name, kind="bdist_wheel", yanked=False):
    return {"filename": name, "packagetype": kind, "yanked": yanked, "url": "https://files.pythonhosted.org/" + name}


@needs_packaging
def test_pick_wheel_prefers_the_exact_cpython_build():
    files = [_f("x-1.tar.gz", "sdist"), _f("x-1-py3-none-any.whl"), _f("x-1-cp311-abi3-win_amd64.whl"),
             _f("x-1-cp312-cp312-win_amd64.whl"), _f("x-1-cp312-cp312-win32.whl"), _f("x-1-cp313-cp313-win_amd64.whl")]
    assert L.pick_wheel(files)["filename"] == "x-1-cp312-cp312-win_amd64.whl"
    assert L.pick_wheel(files[:3])["filename"] == "x-1-cp311-abi3-win_amd64.whl"
    assert L.pick_wheel(files[:2])["filename"] == "x-1-py3-none-any.whl"
    assert L.pick_wheel([_f("x-1-cp312-cp312-win_amd64.whl", yanked=True), files[1]])["filename"] == \
        "x-1-py3-none-any.whl"
    with pytest.raises(SatkError):
        L.pick_wheel([files[0], files[4], files[5]])


# --------------------------------------------------------------------------- wheels


def _wheel(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for k, v in entries.items():
            zf.writestr(k, v)
    return buf.getvalue()


def test_wheel_entries_map_data_dirs(tmp_path):
    p = tmp_path / "demo-1.0-py3-none-any.whl"
    p.write_bytes(_wheel({
        "demo/__init__.py": b"x = 1\n",
        "demo-1.0.dist-info/METADATA": b"Name: demo\n",
        "demo-1.0.data/purelib/demo_extra.py": b"",
        "demo-1.0.data/platlib/demo_ext.pyd": b"MZ",
        "demo-1.0.data/scripts/demo-postinstall.py": b"",
        "demo-1.0.data/headers/demo.h": b"",
        "demo.pth": b"import demo\n",
    }))
    got = dict(wheel_entries(p))
    assert sorted(got) == ["demo-1.0.dist-info/METADATA", "demo.pth", "demo/__init__.py", "demo_ext.pyd",
                           "demo_extra.py"]
    assert got["demo_ext.pyd"] == b"MZ"


@pytest.mark.parametrize("bad", ["../evil.py", "/abs.py", "C:/x.py", "a/../../b.py", "demo-1.0.data/odd/x.py"])
def test_wheel_entries_refuse_unsafe_paths(tmp_path, bad):
    p = tmp_path / "demo-1.0-py3-none-any.whl"
    p.write_bytes(_wheel({bad: b""}))
    with pytest.raises(SatkError) as e:
        list(wheel_entries(p))
    assert e.value.code == "CHECK_FAILED"


def test_parse_wheel_filename():
    assert parse_filename("numpy-2.5.3-cp312-cp312-win_amd64.whl") == {
        "name": "numpy", "ver": "2.5.3", "py": "cp312", "abi": "cp312", "plat": "win_amd64"}
    assert parse_filename("pywin32-312-1-cp312-cp312-win_amd64.whl")["build"] == "1"
    with pytest.raises(SatkError):
        parse_filename("numpy-2.5.3.tar.gz")


# --------------------------------------------------------------------------- fetch (no network)


def test_fetch_reuses_verified_cache_and_respects_offline(tmp_path):
    data = b"cached wheel"
    sha = __import__("hashlib").sha256(data).hexdigest()
    f = tmp_path / "x-1-py3-none-any.whl"
    url = "https://files.pythonhosted.org/x-1-py3-none-any.whl"
    with pytest.raises(SatkError) as e:
        F.fetch(url, f, sha256=sha, offline=True)
    assert e.value.code == "NOT_READY"
    f.write_bytes(data)
    assert F.fetch(url, f, sha256=sha, size=len(data), offline=True) == f
    f.write_bytes(b"tampered!!!!")
    with pytest.raises(SatkError) as e:
        F.fetch(url, f, sha256=sha, offline=True)  # a corrupt cache is never used
    assert e.value.code == "NOT_READY"


@pytest.mark.parametrize("url", ["http://pypi.org/x", "https://example.com/x.whl", "file:///c:/x.whl"])
def test_only_https_on_allowed_hosts(url):
    with pytest.raises(SatkError) as e:
        F.check_url(url)
    assert e.value.code == "BAD_PARAMS"
