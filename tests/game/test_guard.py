"""satk.game.guard: write targets of game.* (aliases of protected roots, clean copy, work/, repositories).

Nothing here writes outside tmp: every check that names a real root must refuse before writing.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from satk.core.errors import SatkError
from satk.game import guard as G

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows path semantics")


@pytest.mark.parametrize("raw,expect", [
    ("\\\\?\\D:\\Mods\\x", r"D:\Mods\x"),
    ("//?/D:/Mods/x", r"D:\Mods\x"),
    ("\\\\.\\D:\\Mods\\x", r"D:\Mods\x"),
    ("\\??\\D:\\Mods\\x", r"D:\Mods\x"),
    ("D:/Mods/./GTA/../x", r"D:\Mods\x"),
])
def test_canonical_spellings(raw, expect):
    assert os.path.normcase(G.canonical(raw)) == os.path.normcase(expect)


@pytest.mark.parametrize("raw,code", [
    ("\\\\localhost\\D$\\Mods\\GTA\\GTA San Andreas", "PROTECTED_PATH"),
    ("\\\\127.0.0.1\\D$\\Mods", "PROTECTED_PATH"),
    ("\\\\?\\UNC\\localhost\\D$\\Mods", "PROTECTED_PATH"),
    ("//server/share/x", "PROTECTED_PATH"),
    ("\\\\?\\GLOBALROOT\\Device\\HarddiskVolume1\\x", "BAD_PARAMS"),
    ("\\\\?\\Volume{01234567-89ab-cdef-0123-456789abcdef}\\x", "BAD_PARAMS"),
    ("\\\\.\\PhysicalDrive0", "BAD_PARAMS"),
    ("\\\\?\\D:\\Mods\\GTA\\work\\..\\GTA San Andreas", "BAD_PARAMS"),  # '..' is literal in \\?\ paths
    ("D:\\Mods\\GTA\\GTA San Andreas.\\x", "BAD_PARAMS"),
    ("D:\\Mods\\GTA\\GTA San Andreas  \\x", "BAD_PARAMS"),
    ("\\\\?\\D:\\Mods\\GTA\\GTA San Andreas.", "BAD_PARAMS"),
    ("", "BAD_PARAMS"),
])
def test_canonical_refuses(raw, code):
    with pytest.raises(SatkError) as ei:
        G.canonical(raw)
    assert ei.value.code == code


@pytest.mark.parametrize("raw", [
    "\\\\?\\D:\\Mods\\GTA\\GTA San Andreas", "\\\\?\\D:\\Mods\\GTA\\GTA San Andreas\\nested",
    "\\\\localhost\\D$\\Mods\\GTA\\GTA San Andreas", "\\\\?\\D:\\Mods\\GTA\\src",
    "\\\\.\\d:\\mods\\gta\\src\\x", "\\\\?\\D:\\Mods\\GTA\\gta-sa-clean\\nested", "D:\\Mods\\GTA\\gta-sa-clean\\nested",
])
@pytest.mark.parametrize("allow_game_root", [False, True])
def test_real_roots_behind_aliases(raw, allow_game_root):
    """The verifier's probe_paths2 aliases: refused in every mode (only the exact clean root may be unlocked)."""
    with pytest.raises(SatkError) as ei:
        G.write_target(raw, allow_game_root=allow_game_root)
    assert ei.value.code == "PROTECTED_PATH"


def test_clean_copy_root_only_where_allowed(satk_home):
    game = satk_home / "gta-sa-clean"
    game.mkdir()
    t = G.write_target(game, allow_game_root=True)
    assert t.game_root and t.path == game
    t = G.write_target("\\\\?\\" + str(game), allow_game_root=True)
    assert t.game_root and os.path.normcase(t.path) == os.path.normcase(game)
    for p, allow in ((game, False), (game / "nested", True), (game / "a" / "b", True)):
        with pytest.raises(SatkError) as ei:
            G.write_target(p, allow_game_root=allow)
        assert ei.value.code == "PROTECTED_PATH", p


def test_location_rule(work, satk_home):
    assert G.write_target(work / "re" / "bin" / "x.exe").path == work / "re" / "bin" / "x.exe"
    assert G.write_target(work).path == work
    for p in (satk_home / "docs" / "x.exe", satk_home / "tools" / "x.exe", satk_home.parent / "x"):
        with pytest.raises(SatkError) as ei:
            G.write_target(p)
        assert ei.value.code == "PROTECTED_PATH" and "outside the work directory" in ei.value.msg
    # the staging sibling of an accepted target skips only the location rule
    assert G.write_target(satk_home / "gta-sa-clean.partial", location=False).path.name == "gta-sa-clean.partial"
    with pytest.raises(SatkError):
        G.write_target(satk_home / "src" / "x.partial", location=False)


def test_git_working_trees_are_refused(work):
    wt = work / "wt" / "WP-77"
    wt.mkdir(parents=True)
    (wt / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")  # a worktree has a .git file
    repo = work / "repo"
    (repo / ".git").mkdir(parents=True)
    for p in (wt / "x.exe", wt / "a" / "b" / "c", repo / "x.exe"):
        with pytest.raises(SatkError) as ei:
            G.write_target(p)
        assert ei.value.code == "PROTECTED_PATH" and "git working tree" in ei.value.msg
    assert G.write_target(work / "wt" / "x.exe").path.name == "x.exe"  # beside the worktrees is fine


def _junction(link: Path, target: Path) -> None:
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
    if r.returncode != 0 or not link.exists():
        pytest.skip(f"cannot create a junction: {r.stdout} {r.stderr}")


def test_junction_into_protected_root(work, satk_home):
    inst = satk_home / "GTA San Andreas"
    inst.mkdir()
    link = work / "link"
    _junction(link, inst)
    try:
        with pytest.raises(SatkError) as ei:
            G.write_target(link / "nested")
        assert ei.value.code == "PROTECTED_PATH"
        assert G.inside(link / "nested", inst)
    finally:
        os.rmdir(link)  # removes the junction only


def test_junction_out_of_work_is_judged_by_its_target(work, satk_home):
    """work/link -> elsewhere: the real location is outside work/, so it is refused."""
    elsewhere = satk_home / "elsewhere"
    elsewhere.mkdir()
    link = work / "out"
    _junction(link, elsewhere)
    try:
        with pytest.raises(SatkError) as ei:
            G.write_target(link / "x.exe")
        assert ei.value.code == "PROTECTED_PATH" and "outside the work directory" in ei.value.msg
    finally:
        os.rmdir(link)


def test_identity_check_catches_unresolved_aliases(work, satk_home, monkeypatch):
    """If an alias survives the spelling and realpath steps (e.g. a subst drive), file identity
    (os.path.samestat of the existing ancestors) still finds the protected root."""
    inst = satk_home / "GTA San Andreas"
    inst.mkdir()
    link = work / "alias"
    _junction(link, inst)
    try:
        monkeypatch.setattr(os.path, "realpath", lambda p, *a, **k: os.path.abspath(p))  # resolves nothing now
        with pytest.raises(SatkError) as ei:
            G.write_target(link / "nested")
        assert ei.value.code == "PROTECTED_PATH"
        assert ei.value.data["root"] == G.jpath(inst)  # core or game identity guard can refuse first
        assert G.inside(link / "nested", inst)
    finally:
        monkeypatch.undo()
        os.rmdir(link)
