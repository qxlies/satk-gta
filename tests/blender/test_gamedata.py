"""Direct game-data source (no index) against gta-sa-clean: golden numbers of SPEC Appendix A.1."""

from __future__ import annotations

import pytest

from satk.blender import gamedata as G
from satk.core.errors import SatkError

pytestmark = pytest.mark.game


@pytest.fixture(scope="module")
def gd():
    from satk.core import config

    p = config.load().profile("vanilla")
    if not (p.root / "gta_sa.exe").is_file():
        pytest.skip(f"game copy not found: {p.root}")
    g = G.GameData(p.root, list(p.dat), p.img_order, "vanilla")
    yield g
    g.close()


def test_golden_counts(gd):
    s = gd.stats
    assert s.ide_files == 57 and s.defs == 14832            # A.1 IDE: 57 files, 14 832 definitions
    assert s.inst == 50935 and s.binary_ipl == 190          # A.1 IPL: 50 935 placements, 190 bnry
    assert s.lod_links == 6103 and s.lod_unresolved == 0    # A.1: 6 103 LOD links, all resolved
    assert s.errors == []


def test_spec_box_187(gd):
    rows = gd.insts(box=(2445, -1737, 2545, -1637), area=None, lod="all", match="center")
    assert len(rows) == 187                                  # A.1 "Запросы": box by centre, all LOD and areas
    hd = gd.insts(box=(2445, -1737, 2545, -1637), area=None, lod="hd", match="center")
    lod = gd.insts(box=(2445, -1737, 2545, -1637), area=None, lod="lod", match="center")
    assert len(hd) + len(lod) == 187 and all(r.is_lod for r in lod)


def test_v13_link(gd):
    r = next(r for r in gd.insts(center=(2489.3, -1668.5), r=1.0, match="center", lod="all", area=None)
             if r.sid == "inst:lae2_stream0#4")
    assert r.model_id == 17613 and r.name.lower() == "lae2_roads89"
    assert r.pos == pytest.approx((2489.30, -1668.50, 12.30), abs=0.01)
    assert r.lod_sid == "inst:lae2#198"


def test_model_files(gd):
    mf = gd.model_files("model:411")
    assert mf.name == "infernus" and mf.sec == "cars"
    assert [b.name.lower() for b in mf.txd_chain] == ["infernus.txd", "vehicle.txd"]
    assert mf.txd_chain[1].sid == "file:models/generic/vehicle.txd" and mf.col.via == "embedded"
    road = gd.model_files(17613)
    assert road.col is not None and road.col.via == "colfile" and road.col.blob.name.lower() == "lae2_4.col"
    ped = gd.model_files("bfori")
    assert ped.sec == "peds" and ped.dff.name.lower() == "bfori.dff"


def test_unknown_model_suggests(gd):
    with pytest.raises(SatkError) as e:
        gd.resolve_model("infernos")
    assert e.value.code == "NOT_FOUND" and "model:411" in e.value.did_you_mean


def test_aabb_match_superset_of_center(gd):
    box = (2445, -1737, 2545, -1637)
    c = {r.sid for r in gd.insts(box=box, area=0, lod="hd", match="center")}
    a = {r.sid for r in gd.insts(box=box, area=0, lod="hd", match="aabb")}
    assert c <= a and len(a) > len(c)


def test_direct_source_definitions_and_zones(gd):
    """What an export needs to write the original IDE/IPL data back, and the add-on's zone centre."""
    from satk.blender.resolve import _DirectSource, rect_of

    src = _DirectSource(gd)
    assert src.model_def(17613) == {"sec": "objs", "draw": 150.0, "flags": 1}       # lae2.ide line
    assert src.model_def(1657) == {"sec": "tobj", "draw": 30.0, "flags": 0, "time": [20, 6]}
    assert src.inst_iflags("inst:lae2_stream2#169") == 2                          # interior field 512
    assert src.inst_iflags("inst:lae2_stream0#4") == 0 and src.inst_iflags("inst:nope#1") is None
    assert rect_of(src.zone("GAN1")) == pytest.approx((2222.56, -1722.33, 2632.83, -1628.53), abs=0.01)
