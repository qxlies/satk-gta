"""Paths and the write guard (SPEC §3.4, §2.1 rules)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path

import pytest

from satk.core import config as C
from satk.core import paths as P
from satk.core.errors import SatkError

#: Paths under the hard-floor workspace (relative to it) that must stay protected.
FLOOR_PROTECTED = [
    r"GTA San Andreas\x.tmp",
    r"src\x.tmp",
    r"gta-sa-clean\x.tmp",
    r"gta-sa-clean\models\gta3.img",
    r"src\ariane\deep\nested\file.cpp",
    r"GTA San Andreas",                       # the root itself
    r"work\..\src\x.tmp",                     # traversal is resolved
    r"gta-sa-clean\.\data\..\x.tmp",
]


@pytest.fixture
def floor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """A hard floor like the one of a ``<workspace>/tools`` checkout, at a temp workspace that the config
    does not name (``satk_home`` points the config elsewhere). Returns the floor workspace as a string."""
    ws = tmp_path / "floor-ws"
    monkeypatch.setattr(P, "HARD_PROTECTED", {"installed": str(ws / "GTA San Andreas"), "src": str(ws / "src"),
                                              "game": str(ws / "gta-sa-clean")})
    return str(ws)


def test_real_floor_follows_the_checkout():
    """The real floor is the layout of the workspace derived from the checkout location (none elsewhere)."""
    ws = C.derived_workspace()
    if ws is None:
        assert P.HARD_PROTECTED == {}
    else:
        assert P.HARD_PROTECTED == {"installed": str(ws.path / "GTA San Andreas"), "src": str(ws.path / "src"),
                                    "game": str(ws.path / "gta-sa-clean")}


@pytest.mark.parametrize("rel", FLOOR_PROTECTED + ["case"])
def test_hard_protected_roots(satk_home, floor, rel):
    # satk_home points the config elsewhere: the floor roots are protected by the hard floor alone.
    if rel == "case":  # case and slashes do not matter
        path = floor.lower().replace("\\", "/") + "/SRC/x.tmp"
    else:
        path = os.path.join(floor, rel)
    with pytest.raises(SatkError) as ei:
        P.ensure_writable(path)
    e = ei.value
    assert e.code == "PROTECTED_PATH" and e.exit_code == 1
    assert e.data["root"].startswith(P.jpath(floor) + "/")
    if path.endswith("x.tmp"):
        assert not os.path.exists(path)  # the guard never creates anything


def test_configured_roots_protected(satk_home):
    for sub in ("GTA San Andreas", "src", "gta-sa-clean"):
        with pytest.raises(SatkError) as ei:
            P.ensure_writable(satk_home / sub / "x")
        assert ei.value.code == "PROTECTED_PATH"


def test_similar_names_are_not_protected(satk_home, floor):
    for p in (floor + r"\src2\x", floor + r"\gta-sa-clean-old\x", floor + r"\work\src\x",
              str(satk_home / "work" / "x")):
        assert P.ensure_writable(p) == Path(os.path.abspath(p))


@pytest.mark.parametrize("guard", ["ensure_writable", "ensure_removable"])
@pytest.mark.parametrize("unlocked", [False, True], ids=["normal", "game-writer"])
def test_local_paths_do_not_traverse_file_identities(satk_home, monkeypatch, guard, unlocked):
    target = satk_home / "work" / "cache" / "tex" / "ab" / "deadbeef.png"

    def unexpected(*args):
        raise AssertionError("identity traversal on a plain local path")

    monkeypatch.setattr(P, "_ancestor_identities", unexpected)
    with P.game_writer() if unlocked else nullcontext():
        assert getattr(P, guard)(target) == target
    assert not target.exists()


def test_ancestor_root_is_stable_across_hash_seeds(satk_home, repo_root):
    code = """\
import json
import os
import sys
from satk.core.errors import SatkError
from satk.core.paths import ensure_removable

results = []
for path in (sys.argv[1], sys.argv[1] + os.sep):
    try:
        ensure_removable(path)
    except SatkError as exc:
        results.append(exc.to_dict())
    else:
        raise AssertionError('protected ancestor was allowed')
print(json.dumps(results))
"""
    roots = {}
    for seed in ("0", "1", "2", "3", "17", "42"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        env["PYTHONPATH"] = os.pathsep.join((str(repo_root / "src"), env.get("PYTHONPATH", "")))
        result = subprocess.run([sys.executable, "-X", "utf8", "-c", code, str(satk_home)],
                                cwd=repo_root, env=env, capture_output=True, text=True, encoding="utf-8", timeout=30)
        assert result.returncode == 0, result.stderr
        errors = [item["error"] for item in json.loads(result.stdout)]
        assert all(error["code"] == "PROTECTED_PATH" for error in errors)
        roots[seed] = [error["data"]["root"] for error in errors]
    assert {root for values in roots.values() for root in values} == {P.jpath(satk_home / "GTA San Andreas")}, roots


def test_extra_protected_root_from_env(satk_home, floor, monkeypatch, tmp_path):
    extra = tmp_path / "precious"
    monkeypatch.setenv("SATK_SAFETY_PROTECTED_ROOTS", f'["{extra.as_posix()}"]')
    from satk.core import config

    config.reset()
    with pytest.raises(SatkError):
        P.ensure_writable(extra / "a.txt")
    # the hard floor stays even though the configured list no longer names it
    with pytest.raises(SatkError):
        P.ensure_writable(floor + r"\src\x")


@pytest.mark.parametrize("action", ["remove", "rename"])
def test_destructive_guard_preserves_protected_descendants(satk_home, tmp_path, action):
    import shutil

    root = satk_home / "src"
    root.mkdir()
    victim = root / "untouched.txt"
    victim.write_text("protected", encoding="utf-8")
    # Every mutation in this test is confined to this temporary fixture.
    assert satk_home.resolve().is_relative_to(tmp_path.resolve())
    assert P.ensure_writable(satk_home) == satk_home  # existing write/mkdir contract
    with pytest.raises(SatkError) as exc:
        checked = P.ensure_removable(satk_home)
        if action == "remove":
            shutil.rmtree(checked)
        else:
            checked.rename(tmp_path / "moved")
    assert exc.value.code == "PROTECTED_PATH"
    assert victim.read_text(encoding="utf-8") == "protected"
    assert not (tmp_path / "moved").exists()


@pytest.mark.parametrize("form", ["drive", "parent", "workspace", "long-drive", "long-workspace"])
def test_real_protected_ancestors_readonly(satk_home, floor, form):
    ws = Path(floor)
    path = {"drive": ws.anchor, "parent": str(ws.parent), "workspace": str(ws),
            "long-drive": "\\\\?\\" + ws.anchor, "long-workspace": "\\\\?\\" + str(ws)}[form]
    assert P.is_protected(path, include_ancestors=True) is not None


def test_removal_guard_respects_game_writer(satk_home):
    clean = satk_home / "gta-sa-clean"
    with pytest.raises(SatkError):
        P.ensure_removable(clean)
    with P.game_writer():
        assert P.ensure_removable(clean) == clean
        for path in (satk_home, satk_home / "src", satk_home / "GTA San Andreas"):
            with pytest.raises(SatkError) as exc:
                P.ensure_removable(path)
            assert exc.value.code == "PROTECTED_PATH"


def test_removal_guard_allows_unprotected_tree(satk_home, tmp_path):
    import shutil

    tree = satk_home / "work" / "discard"
    tree.mkdir()
    (tree / "file.txt").write_text("temporary", encoding="utf-8")
    assert tree.resolve().is_relative_to(tmp_path.resolve())
    shutil.rmtree(P.ensure_removable(tree))
    assert not tree.exists()


def test_game_writer_unlocks_only_the_clean_copy(satk_home, floor):
    clean = floor + r"\gta-sa-clean\x.tmp"
    clean_cfg = satk_home / "gta-sa-clean" / "x"
    with P.game_writer():
        assert P.ensure_writable(clean)
        assert P.ensure_writable(clean_cfg)
        with P.game_writer():  # nesting
            assert P.ensure_writable(clean)
        assert P.ensure_writable(clean)
        for never in (floor + r"\GTA San Andreas\x.tmp", floor + r"\src\x.tmp", satk_home / "src" / "x"):
            with pytest.raises(SatkError):
                P.ensure_writable(never)
    with pytest.raises(SatkError):
        P.ensure_writable(clean)


def test_game_writer_is_per_thread(satk_home, floor):
    import threading

    seen = {}

    def other():
        seen["protected"] = P.is_protected(floor + r"\gta-sa-clean\x") is not None

    with P.game_writer():
        t = threading.Thread(target=other)
        t.start()
        t.join()
    assert seen["protected"] is True


@pytest.mark.parametrize("bad", ['D:\\x\\a"b', "D:\\x\\a|b", "D:\\x\\a?b", "D:\\x\\a\x00b", "D:\\x\\a:b"])
def test_invalid_path_characters(satk_home, bad):
    if os.name != "nt":
        pytest.skip("Windows path rules")
    with pytest.raises(SatkError) as ei:
        P.ensure_writable(bad)
    assert ei.value.code == "BAD_PARAMS"


def test_work_and_tmp(satk_home):
    w = P.work("index")
    assert w == satk_home / "work" / "index" and w.is_dir()
    f = P.work("index", "vanilla.sqlite")
    assert f.name == "vanilla.sqlite" and f.parent.is_dir() and not f.exists()
    t = P.tmp("WP-00")
    assert t == satk_home / "work" / "tmp" / "WP-00" and t.is_dir()
    for bad in ("", "a/b", "..", "c:x"):
        with pytest.raises(SatkError):
            P.tmp(bad)
    with pytest.raises(SatkError):
        P.work("..", "escape")


def test_profile_root(satk_home):
    assert P.profile_root() == satk_home / "gta-sa-clean"
    assert P.profile_root("samp") == satk_home / "GTA San Andreas"
    with pytest.raises(SatkError) as ei:
        P.profile_root("vanila")
    assert ei.value.code == "BAD_PARAMS" and "vanilla" in ei.value.did_you_mean


def test_jpath():
    assert P.jpath(r"d:\ws\work\x.png") == "D:/ws/work/x.png"
    assert P.jpath(Path("D:/ws")) == "D:/ws"
    rel = P.jpath("rel/x")
    assert os.path.isabs(rel) and "\\" not in rel


def test_atomic_write(satk_home):
    target = satk_home / "work" / "out" / "a.json"
    assert P.atomic_write(target, "привет") == target
    assert target.read_text(encoding="utf-8") == "привет"
    P.atomic_write(target, b"\x00\x01")
    assert target.read_bytes() == b"\x00\x01"
    assert [p.name for p in target.parent.iterdir()] == ["a.json"]  # no temp leftovers
    protected = satk_home / "src" / "x.json"
    with pytest.raises(SatkError):
        P.atomic_write(protected, "x")
    assert not protected.exists()


def test_open_ro_is_binary_readonly(tmp_path):
    f = tmp_path / "x.bin"
    f.write_bytes(b"abc")
    with P.open_ro(f) as h:
        assert h.mode == "rb" and h.read() == b"abc"
        with pytest.raises(Exception):
            h.write(b"x")  # type: ignore[arg-type]
