"""satk.game.verify on synthetic trees (+ read-only checks of the real copy, marker game)."""

from __future__ import annotations

import os
import time

import pytest

from satk.core.errors import SatkError
from satk.game import manifests as M
from satk.game.clone import clone
from satk.game.verify import check_clean_manifest, check_tree, info


@pytest.fixture
def copy(fake_game, work):
    """A clean synthetic copy produced by clone() (also exercises clone end to end).

    game.* writers only accept targets under ``paths.work`` (satk.game.guard).
    """
    dst = work / "clean"
    clone(fake_game.root, dst, fake_game.stock)
    return dst


def test_clean_copy_verifies(fake_game, copy):
    rep = check_tree(copy, fake_game.stock)
    assert rep.problems == [] and rep.extra == []
    assert rep.checked == fake_game.stock_count and rep.bytes == fake_game.stock.total_bytes
    assert rep.fast == sum(1 for e in fake_game.stock if M.is_fast(e.path) and e.mtime is not None)
    assert rep.fast + rep.hashed == rep.checked
    assert check_clean_manifest(copy, fake_game.stock) == ("ok", [])
    deep = check_tree(copy, fake_game.stock, deep=True)
    assert deep.problems == [] and deep.hashed == deep.checked and deep.fast == 0


def test_fast_mode_trusts_size_mtime_of_img_only(fake_game, copy):
    img = M.to_path(copy, "models/gta3.img")
    st = img.stat()
    data = bytearray(img.read_bytes())
    data[100] ^= 0xFF  # same size, same mtime: invisible to the fast check by design
    img.write_bytes(bytes(data))
    os.utime(img, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert check_tree(copy, fake_game.stock).problems == []
    deep = check_tree(copy, fake_game.stock, deep=True)
    assert [(p.path, p.problem) for p in deep.problems] == [("models/gta3.img", "sha256")]


def test_touched_img_is_hashed_not_reported(fake_game, copy):
    img = M.to_path(copy, "anim/cuts.img")
    os.utime(img, (time.time(), time.time()))
    rep = check_tree(copy, fake_game.stock)
    assert rep.problems == [] and rep.hashed == rep.checked - rep.fast and M.key("anim/cuts.img") in rep.hashes


def test_changed_small_file_is_detected_in_fast_mode(fake_game, copy):
    p = M.to_path(copy, "data/gta.dat")
    p.write_bytes(b"X" * p.stat().st_size)
    rep = check_tree(copy, fake_game.stock)
    assert [(x.path, x.problem) for x in rep.problems] == [("data/gta.dat", "sha256")]


def test_missing_size_extra(fake_game, copy):
    M.to_path(copy, "text/american.gxt").unlink()
    M.to_path(copy, "movies/Logo.mpg").write_bytes(b"short")
    (copy / "new.log").write_text("x", encoding="utf-8")
    rep = check_tree(copy, fake_game.stock)
    assert rep.missing == 1 and rep.mismatch == 1
    assert {(p.path, p.problem) for p in rep.problems} == {("text/american.gxt", "missing"), ("movies/Logo.mpg", "size")}
    assert rep.extra == ["new.log"]  # MANIFEST.sha256 is not extra


def test_manifest_file_cross_check(fake_game, copy):
    (copy / M.CLEAN_MANIFEST_NAME).unlink()
    st, warns = check_clean_manifest(copy, fake_game.stock)
    assert st == "missing" and warns[0].startswith("NO_MANIFEST")
    (copy / M.CLEAN_MANIFEST_NAME).write_text(M.format_sha256_file([("data/gta.dat", "0" * 64)]), encoding="utf-8")
    st, warns = check_clean_manifest(copy, fake_game.stock)
    assert st == "differs" and "1 hash(es) differ" in warns[0] and "stock path(s) not listed" in warns[0]


def test_missing_root(tmp_path, fake_game):
    with pytest.raises(SatkError) as ei:
        check_tree(tmp_path / "nope", fake_game.stock)
    assert ei.value.code == "NOT_FOUND"


def test_install_scope(fake_game):
    inst = fake_game.install
    stock_only = inst.subset(lambda e: M.classify(e.path) in ("stock", "restore"))
    assert len(stock_only) == fake_game.stock_count
    (fake_game.root / "chatlog.txt").write_text("played a bit", encoding="utf-8")
    assert check_tree(fake_game.root, stock_only, scan_extra=False).problems == []
    full = check_tree(fake_game.root, inst, ignore=())
    assert [(p.path, p.problem) for p in full.problems] == [("chatlog.txt", "size")]


def test_info_on_fake_install(fake_game, copy):
    i = info(fake_game.root, fake_game.stock)
    assert i["exe"] == "unknown" and i["vorbisfile"] == "unknown"
    assert i["asi"] == 2 and i["nonstock"] == fake_game.nonstock_count
    assert i["nonstock_roles"] == {"exclude": 4, "nonstock": 7}
    assert i["stock_missing"] == 0 and i["protected"] == "0/8"
    c = info(copy, fake_game.stock)
    assert c["nonstock"] == 0 and c["asi"] == 0 and c["manifest"] is True and c["files"] == fake_game.stock_count + 1


# --------------------------------------------------------------------------- real copy (read-only)


@pytest.mark.game
def test_real_clean_copy_fast(clean_root):
    rep = check_tree(clean_root, M.load_stock())
    assert rep.problems == [] and rep.extra == []
    assert (rep.checked, rep.bytes) == (416, 5_029_186_364)
    assert rep.fast == 40
    assert check_clean_manifest(clean_root, M.load_stock())[0] == "ok"


@pytest.mark.game
def test_real_clean_info(clean_root):
    i = info(clean_root, M.load_stock())
    assert i["exe"] == "hoodlum-stock"
    assert i["exe_sha256"] == "a559aa772fd136379155efa71f00c47aad34bbfeae6196b0fe1047d0645cbd26"
    assert i["vorbisfile_sha256"] == "a08923479000cec366967fb8259e0920b7aa18859722c7dda1415726bed4774f"
    assert (i["asi"], i["nonstock"], i["stock_missing"]) == (0, 0, 0)


@pytest.mark.game
@pytest.mark.skipif(not (M.DATA_DIR / M.INSTALL_MANIFEST).is_file(), reason="no install audit manifest (not published)")
def test_real_install_unchanged_stock_scope(installed_root):
    inst = M.load_install()
    ref = inst.subset(lambda e: M.classify(e.path) in ("stock", "restore"))
    rep = check_tree(installed_root, ref, scan_extra=False)
    assert rep.checked == 416 and rep.problems == []
    assert rep.hashes[M.key("gta_sa.exe")].startswith("1d7c531f")
    assert rep.hashes[M.key("vorbisFile.dll")].startswith("6ccf4258")
