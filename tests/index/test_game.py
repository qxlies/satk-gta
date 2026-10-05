"""Acceptance of WP-03 on the real game files (read-only; marker ``game``, ``slow``).

Each profile is built into a temp directory (not ``work/index``) and checked against
``tests/golden/index_<profile>.json`` (SPEC Appendix A) plus the card's acceptance examples.
Set ``SATK_TEST_PERF=1`` on an idle machine to enforce the 30-second build budget.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from satk.core.errors import SatkError
from satk.core.paths import cfg
from satk.index.api import IndexDB
from satk.index.build import build
from satk.index.golden import load_golden, verify
from satk.index.layers import HashCache

pytestmark = [pytest.mark.game, pytest.mark.slow]


def _root_ok(profile: str) -> bool:
    try:
        return (cfg().profile(profile).root / "gta_sa.exe").is_file()
    except SatkError:
        return False


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out: dict[str, tuple[dict, IndexDB]] = {}
    base = tmp_path_factory.mktemp("index-game")
    hc = HashCache(base / "hashcache.sqlite")

    def get(profile: str):
        if profile not in out:
            if not _root_ok(profile):
                pytest.skip(f"root of profile {profile} not found")
            rep = build(profile, out=base / f"{profile}.sqlite", hashcache=hc)
            out[profile] = (rep, IndexDB(profile, base / f"{profile}.sqlite"))
        return out[profile]

    yield get
    for _rep, db in out.values():
        db.close()
    hc.close()


def test_vanilla_build_and_golden(built, record_property):
    rep, db = built("vanilla")
    record_property("build_seconds", rep["build_seconds"])
    assert rep["ok"] and rep["size_mb"] <= 80 and rep["user_version"] == 3
    res = verify(db, load_golden("vanilla"))
    assert res["ok"], res["failed"]
    assert res["metrics"]["texref.unresolved_main"] == 462 and res["metrics"]["texref.unresolved_all"] == 467
    assert res["unresolved_texrefs_main"] == 462 and res["unresolved_texrefs_all"] == 467


@pytest.mark.skipif(os.environ.get("SATK_TEST_PERF") != "1", reason="build timing requires SATK_TEST_PERF=1 on an idle machine")
def test_vanilla_build_budget(built):
    rep, _db = built("vanilla")
    assert rep["build_seconds"] <= 30


def test_vanilla_acceptance_examples(built):
    _rep, db = built("vanilla")
    o = db.get("model:411")
    assert (o["name"], o["sec"], o["links"]["txd_chain"], o["tex"]["missing"]) == (
        "infernus", "cars", ["txd:infernus", "txd:vehicle"], 0)
    rows = db.refs("model:17613", rel="inst")["rows"]
    assert ["inst:lae2_stream0#4", [2489.3, -1668.5, 12.3]] == rows[0][:1] + rows[0][3:4]
    i = db.get("inst:lae2_stream0#4")
    assert i["lod"] == "inst:lae2#198" and i["model"] == "model:17613"
    f = db.get("file:models/gta_int.img/lawest1.txd")
    assert f["active"] is False and f["shadowed_by"] == "file:models/gta3.img/lawest1.txd"
    assert db.query("SELECT count(*) FROM blob b JOIN source s ON s.id=b.source_id WHERE s.relpath='models/gta_int.img' "
                    "AND b.ext='txd' AND b.active=0")["rows"] == [[4]]
    assert db.find("ws_rooftarmac1", kind="tex")["total"] == 257
    assert db.query("SELECT count(DISTINCT dff_id) FROM dff_mat WHERE texture='ws_rooftarmac1'")["rows"] == [[470]]
    assert db.near(None, None, box=(2445, -1737, 2545, -1637), match="center", lod="all", area=None,
                   limit=500)["total"] == 187
    mf = db.model_files(411)
    assert mf.dff.name == "infernus.dff" and [b.name for b in mf.txd_chain] == ["infernus.txd", "vehicle.txd"]
    assert db.match_runtime(17613, (2489.3, -1668.5, 12.3)) == ["inst:lae2_stream0#4"]
    with pytest.raises(SatkError) as e:
        db.query("DELETE FROM model")
    assert e.value.code == "READ_ONLY"
    assert db.find("стол")["ok"]


def test_vanilla_data_files(built):
    """Schema v3 acceptance (M2-10): model:411 shows its handling and colours; water, timecyc, handling counts."""
    _rep, db = built("vanilla")
    o = db.get("model:411")
    assert o["handling"]["mass"] == 1400.0 and o["handling"]["max_vel"] == 240.0 and o["links"]["handling"] == "handling:infernus"
    assert len(o["colors"]) == 8 and o["color_rgb"]["1"] == "#f5f5f5"
    rows = db.query("SELECT config, nverts, count(*) FROM water_quad GROUP BY config, nverts ORDER BY 1, 2")["rows"]
    assert rows == [[0, 3, 6], [0, 4, 301], [1, 3, 6], [1, 4, 261]]
    per = db.query("SELECT count(DISTINCT weather), count(DISTINCT hour), count(*) FROM timecyc")["rows"]
    assert per == [[23, 8, 184]]
    kinds = db.query("SELECT kind, count(*) FROM handling GROUP BY kind ORDER BY kind")["rows"]
    assert kinds == [["bike", 13], ["boat", 12], ["car", 210], ["flying", 24]]
    assert db.get("tcyc:rainy_countryside/20")["nread"] == 20
    assert db.near(-1500, -1700, r=10, kinds=("water",))["rows"][0][0] == "water:0"


def test_vanilla_latency(built):
    _rep, db = built("vanilla")
    for fn in (lambda: db.get("model:411"), lambda: db.find("grove"), lambda: db.refs("model:17613"),
               lambda: db.near(2495, -1687, r=100.0)):
        fn()
        ts = []
        for _ in range(20):
            t0 = time.perf_counter()
            fn()
            ts.append(time.perf_counter() - t0)
        assert sorted(ts)[18] < 0.05


def test_installed_and_diff(built):
    rep, db = built("installed")
    assert rep["ok"]
    res = verify(db, load_golden("installed"))
    assert res["ok"], res["failed"]
    _r, v = built("vanilla")
    from satk.index.ops import _diff_sets, _snap

    added, changed, removed, _m = _diff_sets(_snap(v, "txd"), _snap(db, "txd"), "txd")
    assert added == ["txd:ps3btns", "txd:sixaxis", "txd:x360btns"] and not changed and not removed


def test_samp(built):
    rep, db = built("samp")
    assert rep["ok"]
    res = verify(db, load_golden("samp"))
    assert res["ok"], res["failed"]
    import json

    assert json.loads(db.meta()["img_order"])[:7] == [
        "samp/custom.img", "samp/samp.img", "models/gta3.img", "models/gta_int.img", "samp/sampcol.img",
        "data/script/script.img", "models/cutscene.img"]


def test_rebuild_same_hash(built, tmp_path: Path):
    rep, _db = built("vanilla")
    again = build("vanilla", out=tmp_path / "vanilla.sqlite", hashcache=HashCache(tmp_path / "hc.sqlite"))
    assert again["content_hash"] == rep["content_hash"]
