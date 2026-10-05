"""satk.game.protect: read-only IMG archives (SPEC §4.1, risk R13)."""

from __future__ import annotations

import os
import shutil
import stat

import pytest

from satk.core.errors import SatkError
from satk.game import manifests as M
from satk.game.protect import is_readonly, set_protected, state, summary


@pytest.fixture
def game_copy(fake_game, satk_home):
    """A synthetic clean copy at the configured paths.game of an isolated workspace."""
    from satk.game.clone import clone

    root = satk_home / "gta-sa-clean"
    clone(fake_game.root, root, fake_game.stock)
    yield root
    for rel in M.PROTECT_IMGS:  # let pytest delete tmp files
        p = M.to_path(root, rel)
        if p.exists():
            os.chmod(p, stat.S_IREAD | stat.S_IWRITE)


def test_protect_unprotect_cycle(game_copy):
    assert summary(game_copy) == "0/8"
    before = {rel: M.to_path(game_copy, rel).stat().st_mtime_ns for rel in M.PROTECT_IMGS}
    r = set_protected(game_copy, True)
    assert r["protected"] == "8/8" and r["changed"] == 8 and r["n"] == 8
    assert all(ro for _, ex, ro in state(game_copy))
    with pytest.raises(PermissionError):
        open(M.to_path(game_copy, "models/gta3.img"), "r+b")  # noqa: SIM115
    with open(M.to_path(game_copy, "models/gta3.img"), "rb") as f:  # readers are unaffected
        assert f.read(4)
    assert set_protected(game_copy, True)["changed"] == 0  # idempotent
    r = set_protected(game_copy, False)
    assert r["protected"] == "0/8" and r["changed"] == 8
    assert not is_readonly(M.to_path(game_copy, "anim/cuts.img"))
    after = {rel: M.to_path(game_copy, rel).stat().st_mtime_ns for rel in M.PROTECT_IMGS}
    assert after == before  # attributes only, content and mtime untouched


def test_missing_img_is_reported(game_copy):
    M.to_path(game_copy, "data/script/script.img").unlink()
    r = set_protected(game_copy, True)
    assert r["missing"] == ["data/script/script.img"] and r["protected"] == "7/8"


def test_original_install_is_refused(satk_home):
    inst = satk_home / "GTA San Andreas" / "models"
    inst.mkdir(parents=True)
    (inst / "gta3.img").write_bytes(b"x")
    with pytest.raises(SatkError) as ei:
        set_protected(satk_home / "GTA San Andreas", True)
    assert ei.value.code == "PROTECTED_PATH"
    assert not is_readonly(inst / "gta3.img")


@pytest.mark.parametrize("root", [r"D:\Mods\GTA\GTA San Andreas", "\\\\?\\D:\\Mods\\GTA\\GTA San Andreas",
                                  "//?/D:/Mods/GTA/GTA San Andreas", "\\\\.\\D:\\Mods\\GTA\\GTA San Andreas",
                                  "\\\\localhost\\D$\\Mods\\GTA\\GTA San Andreas", "\\\\?\\D:\\Mods\\GTA\\src"])
def test_real_install_is_refused(root):
    """Also behind extended-length, device and UNC spellings (verifier finding); nothing is changed."""
    with pytest.raises(SatkError) as ei:
        set_protected(root, True)
    assert ei.value.code in ("PROTECTED_PATH", "NOT_FOUND")


def test_alias_of_configured_install_is_refused(satk_home):
    inst = satk_home / "GTA San Andreas" / "models"
    inst.mkdir(parents=True)
    (inst / "gta3.img").write_bytes(b"x")
    with pytest.raises(SatkError) as ei:
        set_protected("\\\\?\\" + str(inst.parent), True)
    assert ei.value.code == "PROTECTED_PATH"
    assert not is_readonly(inst / "gta3.img")


def test_directory_inside_clean_copy_is_refused(game_copy):
    with pytest.raises(SatkError) as ei:
        set_protected(game_copy / "models", True)
    assert ei.value.code == "PROTECTED_PATH"
    assert summary(game_copy) == "0/8"


def test_copy_under_work_and_outside_work(fake_game, work, tmp_path):
    from satk.game.clone import clone

    c = work / "copy"
    clone(fake_game.root, c, fake_game.stock)
    try:
        assert set_protected(c, True)["protected"] == "8/8"
    finally:
        assert set_protected(c, False)["protected"] == "0/8"
    outside = tmp_path / "elsewhere"
    shutil.copytree(c, outside)
    with pytest.raises(SatkError) as ei:
        set_protected(outside, True)
    assert ei.value.code == "PROTECTED_PATH" and "outside the work directory" in ei.value.msg
    assert summary(outside) == "0/8"


def test_hard_linked_img_is_refused(fake_game, work):
    """The read-only attribute is shared by all names of a file: never change it through a link."""
    from satk.game.clone import clone

    c = work / "copy"
    clone(fake_game.root, c, fake_game.stock)
    other = work / "other.img"
    other.write_bytes(b"other copy")
    img = M.to_path(c, "data/script/script.img")
    img.unlink()
    os.link(other, img)
    with pytest.raises(SatkError) as ei:
        set_protected(c, True)
    assert ei.value.code == "PROTECTED_PATH" and "hard link" in ei.value.msg
    assert summary(c) == "0/8" and not is_readonly(other)


def test_missing_root(tmp_path):
    with pytest.raises(SatkError) as ei:
        set_protected(tmp_path / "none", True)
    assert ei.value.code == "NOT_FOUND"


@pytest.mark.game
def test_real_copy_state_readonly_check_only(clean_root):
    """Reads the attributes of the real copy (never changes them in tests)."""
    st = state(clean_root)
    assert len(st) == 8 and all(ex for _, ex, _ in st)
