"""Mod sources (folder, zip, IMG), file classification and the modloader/ folder rules."""

from __future__ import annotations

import struct
import zipfile

import pytest

from satk.modinspect.classify import classify, gta_path
from satk.modinspect.modloader import install_order_key, read_folder
from satk.modinspect.source import open_mod

from .conftest import img_v2, make_mod, rw_stub


@pytest.mark.parametrize("rel,head,plugin,kind,key", [
    ("cars/infernus.dff", b"", "std.stream", "dff", "stream:infernus.dff"),
    ("x/hud.txd", b"", "std.fx", "fx", "fx:hud.txd"),
    ("models/txd/LD_BEAT.txd", b"", "std.sprites", "sprite", "sprite:ld_beat.txd"),
    ("loadscreens/txd/loadsc1.txd", b"", "std.sprites", "sprite", "splash:loadsc1.txd"),
    ("data/handling.cfg", b"", "std.data", "data", "data:handling.cfg"),
    ("my/data/maps/la/x.ide", b"", "std.data", "ide", "ide:data/maps/la/x.ide"),
    ("x.ipl", b"inst", "std.data", "ipl", "ipl:x.ipl"),
    ("x_stream0.ipl", b"bnry", "std.stream", "ipl-bin", "stream:x_stream0.ipl"),
    ("data/timecyc.dat", b"", "std.data", "data", "data:timecyc.dat"),
    ("random.dat", b"", "", "other", "other:random.dat"),
    ("nodes/nodes3.dat", b"", "", "nodes", "other:nodes/nodes3.dat"),
    ("gta3.img/nodes3.dat", b"", "std.stream", "nodes", "stream:nodes3.dat"),
    ("clothes/player.img/coach.dff", b"", "std.stream", "dff", "stream:coach.dff#cloth"),
    ("plugin.asi", b"", "std.asi", "asi", "asi:plugin.asi"),
    ("cleo.asi", b"", "", "asi", "asi:cleo.asi"),
    ("s/script.cs", b"", "std.asi", "cleo", "cleo:s/script.cs"),
    ("readme.txt", b"", "std.data", "readme", "readme:readme.txt"),
    ("text/american.gxt", b"", "std.text", "gxt", "text:american.gxt"),
    ("shot.png", b"", "", "other", "other:shot.png"),
    ("rec/carrec100.rrr", b"", "std.stream", "rrr", "stream:carrec100.rrr"),
    ("decision/allowed/m_norm.ped", b"", "std.data", "data", "data:m_norm.ped"),
])
def test_classify(rel, head, plugin, kind, key):
    b = classify(rel, head)
    assert (b.plugin, b.kind, b.key) == (plugin, kind, key)


def test_gta_path():
    assert gta_path("mymod/data/maps/x.ide") == "data/maps/x.ide"
    assert gta_path("data/x.ide") == "data/x.ide"
    assert gta_path("stuff/x.ide") == "stuff/x.ide"


def test_folder_source_skips_dot_names_and_expands_img(tmp_path):
    mod = make_mod(tmp_path / "m", {"a.dff": rw_stub(), ".hidden/b.dff": rw_stub(), ".c.txd": rw_stub(),
                                    "pack/cars.img": img_v2([("x.dff", rw_stub()), ("y.txd", rw_stub())])})
    with open_mod(mod) as src:
        rels = [f.rel for f in src.files]
        assert rels == ["a.dff", "pack/cars.img", "pack/cars.img/x.dff", "pack/cars.img/y.txd"]
        assert any(w.startswith("SKIPPED: 2") for w in src.warnings)
        assert src.files[2].container == "pack/cars.img"


def test_zip_source_reads_in_place(tmp_path):
    z = tmp_path / "m.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("Top/data/handling.cfg", "x")
        zf.writestr("Top/cars.img", img_v2([("x.dff", rw_stub())]))
        zf.writestr("Top/../evil.dff", "x")
    with open_mod(z) as src:
        assert src.name == "Top" and src.kind == "zip"
        assert [f.rel for f in src.files] == ["cars.img", "cars.img/x.dff", "data/handling.cfg"]
        assert src.files[2].text() == "x"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["m.zip"]


def test_bad_img_is_a_warning(tmp_path):
    mod = make_mod(tmp_path / "m", {"bad.img": b"VER2" + struct.pack("<I", 999999)})
    with open_mod(mod) as src:
        assert any(w.startswith("BAD_IMG") for w in src.warnings)


def test_install_order_quirk():
    names = sorted(["zz", "aaa", "mid", "b"], key=lambda n: install_order_key(50, n))
    assert names == ["b", "zz", "aaa", "mid"]       # shorter names first, then byte order
    assert install_order_key(10, "zzzz") < install_order_key(50, "a")


def test_modloader_ini_rules(tmp_path):
    root = tmp_path / "game"
    for n in ("alpha", "beta", "gamma", "delta", "_ignore", ".data"):
        (root / "modloader" / n).mkdir(parents=True)
    (root / "modloader" / "modloader.ini").write_text(
        "[Folder.Config]\nProfile = Mine\n"
        "[Profiles.Base.Config]\nIgnoreAllMods=false\n"
        "[Profiles.Base.Priority]\nbeta=70\n"
        "[Profiles.Base.IgnoreMods]\n_ignore\n"
        "[Profiles.Mine.Config]\nParents = Base\n"
        "[Profiles.Mine.Priority]\ngamma=0\nalpha=120\n"
        "[Profiles.Mine.IgnoreFiles]\n*.psd\nsub/*.dff\n"
        "[Profiles.Other.ExclusiveMods]\ndelta\n", encoding="utf-8")
    f = read_folder(root)
    st = {m.name: (m.priority, m.ignored) for m in f.mods}
    assert st == {"alpha": (100, ""), "beta": (70, ""), "gamma": (0, "priority 0"),
                  "delta": (50, "exclusive to another profile"), "_ignore": (50, "IgnoreMods")}
    assert [m.name for m in f.loaded] == ["beta", "alpha"]
    assert f.file_ignored("art/x.PSD") and f.file_ignored("sub/a.dff") and not f.file_ignored("a.dff")
    (root / "modloader" / "modloader.ini").write_text(
        "[Profiles.Default.Config]\nExcludeAllMods=true\n[Profiles.Default.IncludeMods]\nb*\n", encoding="utf-8")
    assert [m.name for m in read_folder(root).loaded] == ["beta"]


def test_no_modloader_folder(tmp_path):
    f = read_folder(tmp_path)
    assert f.mods == [] and f.warnings[0].startswith("NO_MODLOADER")
