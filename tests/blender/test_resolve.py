"""Plans for Blender jobs (satk.blender.resolve) over FakeIndexDB and stub sources. No game, no Blender."""

from __future__ import annotations

from pathlib import Path

import pytest

from satk.blender import resolve as R
from satk.core.errors import SatkError
from satk.index.api import BlobRef, ColRef, FakeIndexDB, override_index


@pytest.fixture
def fake_db(satk_home, tmp_path, blobs):
    payloads = {"dff:infernus": blobs["dff"](b"INF"), "txd:infernus": blobs["txd"](b"I"), "txd:vehicle": blobs["txd"](b"V"),
                "dff:lae2_roads89": blobs["dff"](b"ROAD"), "txd:lae2roadshub": blobs["txd"](b"HUB")}
    return FakeIndexDB(root=tmp_path / "game", payloads=payloads)


def test_plan_model_from_index(fake_db, satk_home, blobs):
    with override_index(fake_db):
        plan = R.plan_model("model:infernus")
    assert plan["source"] == "index" and plan["warnings"] == []
    m = plan["model"]
    assert m["sid"] == "model:411" and m["sec"] == "cars" and m["txd_names"] == ["infernus", "vehicle"]
    dff = Path(m["dff"])
    assert dff.name == "infernus.dff" and dff.is_file()
    assert str(satk_home).replace("\\", "/") in m["dff"]  # cache lives under the (isolated) work dir
    data = dff.read_bytes()
    assert data.endswith(b"INF") and len(data) < 2048  # trimmed to the RW size (IMG padding dropped)
    assert m["col_via"] == "embedded" and "col" not in m
    # same bytes -> same cached file (content addressed)
    with override_index(fake_db):
        again = R.plan_model(411)
    assert again["model"]["dff"] == m["dff"]


def test_plan_model_no_dff(fake_db):
    with override_index(fake_db), pytest.raises(SatkError) as e:
        R.plan_model("model:300")  # cutobj01: hier placeholder without files
    assert e.value.code == "NOT_FOUND"


def test_plan_model_rejects_layer(fake_db):
    with override_index(fake_db), pytest.raises(SatkError) as e:
        R.plan_model("model:300@vanilla")
    assert e.value.code == "UNSUPPORTED"


def test_plan_area_world_quaternion(fake_db):
    with override_index(fake_db):
        plan = R.plan_area(center=[2489.3, -1668.5], box=[2480, -1680, 2500, -1660], match="center", area=0, lod="all")
    assert plan["source"] == "index"
    sids = {i["sid"]: i for i in plan["insts"]}
    assert "inst:lae2_stream0#4" in sids
    i = sids["inst:lae2_stream0#4"]
    row = fake_db.insts(box=(2480, -1680, 2500, -1660), match="center", area=0, lod="all")
    q = next(r.q_ipl for r in row if r.sid == "inst:lae2_stream0#4")
    assert i["q"] == [-q[0], -q[1], -q[2], q[3]]  # V9: world rotation = conjugate of the IPL quaternion
    assert i["model"] == 17613 and i["lod"] == "inst:lae2#198"
    assert [m["id"] for m in plan["models"]] == sorted({x["model"] for x in plan["insts"]})


def test_plan_area_limit(fake_db):
    with override_index(fake_db):
        plan = R.plan_area(center=[2489.3, -1668.5], r=3000, match="center", area=None, lod="all", limit=1)
    assert len(plan["insts"]) <= 1 and plan["truncated"] is True
    assert any(w.startswith("TRUNCATED") for w in plan["warnings"])


class _Stub:
    kind = "direct"

    def __init__(self, data: dict[str, bytes]):
        self.data = data

    def read(self, ref):
        return self.data[ref.sid]


def test_cache_col_slices_one_model(satk_home, blobs):
    arch = blobs["coll"]("first") + blobs["coll"]("lae2_roads89") + blobs["coll"]("third") + b"\0" * 300
    b = BlobRef("file:models/gta3.img/lae2_4.col", Path("x.img"), 0, len(arch), "lae2_4.col")
    p = R.cache_col(_Stub({b.sid: arch}), ColRef("col:lae2_roads89", b, 1, "lae2_roads89", "colfile"))
    assert p.name == "lae2_roads89.col"
    assert p.read_bytes() == blobs["coll"]("lae2_roads89")
    assert R.cache_col(_Stub({}), ColRef("col:x", b, 0, "x", "embedded")) is None
    with pytest.raises(SatkError):
        R.cache_col(_Stub({b.sid: arch}), ColRef("col:zz", b, 7, "zz", "colfile"))


def test_open_source_falls_back_to_direct(satk_home, monkeypatch):
    from satk.blender import gamedata

    sentinel = object()
    monkeypatch.setattr(gamedata, "load", lambda profile="vanilla": sentinel)

    class _Stubbed(FakeIndexDB):
        def model_files(self, model):
            raise SatkError("NOT_READY", "stub")

    with override_index(_Stubbed()):
        src, warn = R.open_source("vanilla")
    assert src.kind == "direct" and src.gd is sentinel
    assert warn and warn[0].startswith("INDEX_MISSING")
    with override_index(_Stubbed()), pytest.raises(SatkError):
        R.open_source("vanilla", "index")
    with pytest.raises(SatkError):
        R.open_source("vanilla", "nope")


CARCOLS = """# test
col
0,0,0   # black
245,245,245
42,119,161
end
car
infernus, 1,0, 2,1
end
car4
bus, 1,2,0,1
end
"""


def test_parse_carcols():
    pal, cars = R.parse_carcols(CARCOLS)
    assert pal == [(0, 0, 0), (245, 245, 245), (42, 119, 161)]
    assert cars["infernus"] == [[1, 0, 1, 0], [2, 1, 2, 1]]
    assert cars["bus"] == [[1, 2, 0, 1]]


def test_carcols_for(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "carcols.dat").write_text(CARCOLS, encoding="latin-1")
    c = R.carcols_for(tmp_path, "INFERNUS")
    assert c["combo"] == [1, 0, 1, 0] and c["colors"][0] == [245, 245, 245] and len(c["slots"]) == 4
    assert R.carcols_for(tmp_path, "nosuchcar") is None


def test_parse_model_spec():
    assert R.parse_model_spec("model:411") == "411"
    assert R.parse_model_spec(" infernus ") == "infernus"
    with pytest.raises(SatkError):
        R.parse_model_spec("model:")


# --------------------------------------------------------------------------- zones (add-on "Import Area" by zone)

#: ``IndexDB.get("zone:gan1")`` on the real vanilla index: min/max only, no box/pos (the add-on once
#: read ``z["pos"]`` and raised KeyError).
ZONE_GAN1 = {"ok": True, "id": "zone:gan1", "name": "GAN1", "label": "GAN", "type": 0, "level": 1,
             "min": [2222.56, -1722.33, -89.08], "max": [2632.83, -1628.53, 110.92], "file": "file:data/info.zon"}


class _ZoneDB:
    def get(self, sid, fields=None):
        if sid == "zone:gan1":
            return dict(ZONE_GAN1)
        raise SatkError("NOT_FOUND", f"no {sid}")


def test_zone_rect_from_index_object(monkeypatch):
    monkeypatch.setattr(R, "open_source", lambda profile="vanilla", source="auto": (R._IndexSource(_ZoneDB()), []))
    z = R.zone_rect("GAN1")
    assert z["sid"] == "zone:gan1" and z["rect"] == [2222.56, -1722.33, 2632.83, -1628.53]
    assert z["center"] == pytest.approx([2427.69, -1675.43], abs=0.01)
    assert R.zone_rect("zone:GAN1")["center"] == z["center"]
    with pytest.raises(SatkError) as e:
        R.zone_rect("nosuchzone")
    assert e.value.code == "NOT_FOUND"
    with pytest.raises(SatkError) as e:
        R.zone_rect("  ")
    assert e.value.code == "BAD_PARAMS"


def test_rect_of_shapes():
    assert R.rect_of(ZONE_GAN1) == (2222.56, -1722.33, 2632.83, -1628.53)
    assert R.rect_of({"box": [5, 6, 1, 2]}) == (1, 2, 5, 6)
    assert R.rect_of({"bbox": [1, 2, 0, 5, 6, 9]}) == (1, 2, 5, 6)
    assert R.rect_of({"pos": [3, 4, 5]}) == (3, 4, 3, 4)
    with pytest.raises(SatkError):
        R.rect_of({"name": "x"})


def test_zone_direct_from_zon_files(tmp_path):
    root = tmp_path / "game"
    (root / "data").mkdir(parents=True)
    (root / "data" / "info.zon").write_text(
        "zone\nGAN1, 0, 2222.56, -1722.33, -89.08, 2632.83, -1628.53, 110.92, 1, GAN\nend\n", encoding="latin-1")
    (root / "data" / "map.zon").write_text(
        "zone\nLA, 3, 44.6, -2892.97, -242.99, 2997.06, -768.02, 900.0, 1, LA\nend\n", encoding="latin-1")

    class _GD:
        pass

    gd = _GD()
    gd.root = root
    src = R._DirectSource(gd)
    assert R.rect_of(src.zone("gan1")) == (2222.56, -1722.33, 2632.83, -1628.53)
    assert src.zone("LA")["name"] == "LA"  # map zones too
    assert src.zone("nope") is None


# --------------------------------------------------------------------------- IDE data for exports


def test_plans_carry_ide_and_iflags(fake_db):
    with override_index(fake_db):
        plan = R.plan_area(center=[2489.3, -1668.5], box=[2480, -1680, 2500, -1660], match="center", area=0, lod="all")
        mplan = R.plan_model("model:infernus")
    road = next(m for m in plan["models"] if m["id"] == 17613)
    assert road["ide"] == {"sec": "objs", "draw": 150.0, "flags": 1}  # lae2.ide: lae2_roads89 150, flags 1
    assert plan["insts"] and all(isinstance(i["iflags"], int) for i in plan["insts"])
    assert mplan["model"]["ide"]["sec"] == "cars"


def test_def_info_sections():
    assert R.def_info("objs", 150, 1) == {"sec": "objs", "draw": 150.0, "flags": 1}
    assert R.def_info("tobj", 30, 0, 20, 6) == {"sec": "tobj", "draw": 30.0, "flags": 0, "time": [20, 6]}
    assert R.def_info("anim", 100, 0, None, None, "ifp1") == {"sec": "anim", "draw": 100.0, "flags": 0, "anim": "ifp1"}
    assert R.def_info("objs", None, None) == {"sec": "objs"}


def test_fill_export_defs(fake_db):
    models = [{"name": "lae2_roads89", "id": 17613, "sec": "objs", "insts": [{"sid": "inst:lae2_stream0#4"}]},
              {"name": "infernus", "id": 411, "sec": "cars", "insts": []},
              {"name": "kept", "id": 17613, "sec": "objs", "ide": {"sec": "objs", "draw": 10.0, "flags": 4},
               "insts": [{"sid": "inst:x#1", "iflags": 2}]},
              {"name": "newobj", "id": None, "sec": "objs", "insts": []}]
    with override_index(fake_db):
        assert R.fill_export_defs(models) == []
    assert models[0]["ide"] == {"sec": "objs", "draw": 150.0, "flags": 1} and models[0]["insts"][0]["iflags"] == 0
    assert "ide" not in models[1] and "ide" not in models[3]  # vehicles / unknown IDs stay without
    assert models[2]["ide"]["draw"] == 10.0 and models[2]["insts"][0]["iflags"] == 2  # data from the scene wins
