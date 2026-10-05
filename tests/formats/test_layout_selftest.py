"""satk.formats.layout + selftest on a synthetic game root (pitfall #2, V8)."""

from __future__ import annotations

import json

import pytest

from satk.formats import selftest
from satk.formats.layout import ASSUMPTIONS, archives, loose_assets
from satk.formats.rw import FormatError


def _order(specs):
    return [(a.relpath, a.ns, a.load_ref, a.order) for a in specs]


def test_engine_order(mini_root):
    specs = archives(mini_root, ["data/default.dat", "data/gta.dat"], "engine")
    assert _order(specs) == [
        ("models/gta3.img", "main", "exe:InitImageList", 0),
        ("models/gta_int.img", "main", "exe:InitImageList", 1),
        ("data/paths/carrec.img", "main", "data/gta.dat:1", 2),   # listed although missing on disk
        # gta.dat:2 MODELS\GTA_INT.IMG is skipped like the engine; gta.dat:3 GTA3 is a repeat
        ("models/player.img", "player", "exe:CClothes::Init", 3),
        ("anim/anim.img", "anim", "exe:anim.img", 4),
        ("anim/cuts.img", "cuts", "exe:CCutsceneMgr", 5),
    ]


def test_samp_order(tmp_path, b):
    root = b.game_root(tmp_path / "g", samp=True)
    specs = archives(root, ["data/default.two", "data/gta.two"], "samp")
    main = [a.relpath for a in specs if a.ns == "main"]
    assert main == ["samp/samp.img", "models/gta3.img", "models/gta_int.img", "models/cutscene.img"]
    assert specs[0].load_ref == "data/default.two:1" and specs[2].load_ref == "data/default.two:3"
    assert [a.ns for a in specs[-3:]] == ["player", "anim", "cuts"]
    assert ASSUMPTIONS["samp"] and ASSUMPTIONS["engine"] == []


def test_layout_errors(mini_root):
    with pytest.raises(FormatError, match="img_order"):
        archives(mini_root, ["data/gta.dat"], "modloader")
    with pytest.raises(FormatError, match="DAT file not found"):
        archives(mini_root, ["data/nope.dat"], "engine")


def test_loose_assets(mini_root):
    assert loose_assets(mini_root) == ["anim/ped.ifp", "models/generic/vehicle.txd"]


def test_selftest_counts_on_synthetic_root(mini_root):
    r = selftest.run(mini_root, "samp-like", bench=True)  # no golden for this name -> counters only
    assert r["ok"] and r["failed"] == [] and r["checked"] == 0 and r["warn"]
    c = r["counts"]
    assert c["img.archives"] == 5 and c["img.missing"] == 1 and c["img.entries"] == 3
    assert c["img.gta3.txd"] == 1 and c["img.gta3.ipl"] == 1
    assert c["loose.total"] == 2 and c["blobs"] == 5
    assert c["txd.files"] == 3 and c["txd.empty"] == 1 and c["tex.total"] == 3
    assert c["tex.src.gta3"] == 2 and c["tex.src.loose"] == 1
    assert c["tex.fmt.DXT1"] == 2 and c["tex.fmt.A8R8G8B8"] == 1 and c["tex.unique_hashes"] == 2
    assert c["ide.files"] == 2 and c["ide.defs"] == 3 and c["ide.cars"] == 1 and c["ide.txdp"] == 1
    assert c["ide.max_id"] == 400 and c["ide.dup_ids"] == 0
    assert c["ipl.text_files"] == 1 and c["ipl.zon_files"] == 1 and c["ipl.text_inst"] == 2
    assert c["ipl.bin_files"] == 1 and c["ipl.bin_inst"] == 1 and c["ipl.inst"] == 3
    assert c["ipl.lod_links"] == 2 and c["ipl.lod_unresolved"] == 0 and c["ipl.bin_orphans"] == 0
    assert set(r["timings"]) >= {"img", "txd", "ide", "ipl", "total"}


def test_selftest_detects_mismatch(mini_root, monkeypatch):
    monkeypatch.setitem(selftest.GOLDEN, "mini", {"img.entries": 3, "ide.defs": 99})
    r = selftest.run(mini_root, "mini")
    assert not r["ok"] and r["failed"] == [["ide.defs", 3, 99]] and r["checked"] == 2


def test_selftest_quick_skips_txd(mini_root, monkeypatch, capsys):
    monkeypatch.setitem(selftest.GOLDEN, "mini", {"img.entries": 3, "tex.total": 12345})
    r = selftest.run(mini_root, "mini", quick=True)
    assert r["ok"] and r["checked"] == 1 and "tex.total" not in r["counts"]
    code = selftest.main(["--root", str(mini_root), "--profile", "mini", "--quick"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["ok"] and out["quick"]


def test_crafted_dat_paths_never_crash(mini_root):
    """IMG/IDE/IPL lines that try to leave the root are listed as missing, never opened, never a
    ValueError (review defect: ``data\\C:\\Windows\\win.ini`` made ``archives`` raise ValueError)."""
    (mini_root / "data" / "gta.dat").write_text(
        "IMG data\\C:\\Windows\\win.ini\nIMG ..\\..\\x.img\nIMG data\\NUL\nIMG MODELS\\GTA3.IMG\n"
        "IDE C:\\Windows\\win.ini\nIDE data\\D:x.ide\nIPL data\\..\\..\\y.ipl\nIPL DATA\\MAP.ZON\n",
        encoding="latin-1")
    specs = archives(mini_root, ["data/default.dat", "data/gta.dat"], "engine")
    rel = [a.relpath for a in specs if a.ns == "main"]
    assert rel == ["models/gta3.img", "models/gta_int.img", "data/c:/windows/win.ini", "../../x.img", "data/nul"]
    r = selftest.run(mini_root, "samp-like")
    c = r["counts"]
    assert c["img.missing"] == 3 and c["ide.missing"] == 2 and c["ipl.missing"] == 1
    assert "missing archive data/c:/windows/win.ini" in r["errors"]


def test_selftest_skip_stages(mini_root, monkeypatch):
    monkeypatch.setitem(selftest.GOLDEN, "mini", {"img.entries": 3, "col.files": 99, "tex.total": 3})
    r = selftest.run(mini_root, "mini", skip=("col",))
    assert r["ok"] and r["checked"] == 2 and r["skipped"] == ["col"]
    assert "col.files" not in r["counts"] and "col" not in r.get("timings", {}) and r["counts"]["tex.total"] == 3
    r = selftest.run(mini_root, "mini", skip=["dff"])
    assert r["skipped"] == ["dff", "texref"]
    assert not any(k.startswith(("dff.", "texref.")) for k in r["counts"])
    assert selftest.run(mini_root, "mini", quick=True)["skipped"] == ["dff", "texref", "txd"]
    with pytest.raises(ValueError, match="unknown selftest stage"):
        selftest.run(mini_root, "mini", skip=("nope",))


@pytest.mark.parametrize("quick", [False, True])
def test_selftest_geometry_merged_or_standalone(mini_root, b, monkeypatch, quick):
    """``geometry`` decodes from the DFF stage's walk (full run) or on its own (quick): same counters, and
    the geometry golden is checked in both modes (it used to be skipped under ``quick``)."""
    tx = b.txd([b.native("a")])
    (mini_root / "models" / "gta3.img").write_bytes(b.ver2([
        ("box.txd", tx), ("quad.dff", b.quad_dff()), ("bad.dff", b.chunk(0x10, bytes(3))),
        ("test_stream0.ipl", b.bnry([(12, 22, 3, 0, 0, 0, 1, 1, 0, 0)]))]))
    monkeypatch.setitem(selftest.GOLDEN, "mini", {"img.entries": 5})   # gta3 4 + gta_int 1
    monkeypatch.setattr(selftest, "GEOMETRY_GOLDEN", {"geo.decoded_geometries": 1, "geo.decoded_tris": 2,
                                                      "geo.decode_errors": 1})
    r = selftest.run(mini_root, "mini", quick=quick, geometry=True)
    c = r["counts"]
    assert r["ok"] and r["checked"] == 4, r["failed"]
    assert (c["geo.decoded_geometries"], c["geo.decoded_verts"], c["geo.decoded_tris"]) == (1, 4, 2)
    assert c["geo.decode_errors"] == 1 and "geometry" in r["timings"]
    assert c.get("dff.errors", 0) == (0 if quick else 1)
    monkeypatch.setattr(selftest, "GEOMETRY_GOLDEN", {"geo.decoded_geometries": 2})
    bad = selftest.run(mini_root, "mini", quick=quick, geometry=True)
    assert bad["failed"] == [["geo.decoded_geometries", 1, 2]]
