"""FakeIndexDB: the Q1 behaviour other packages test against (mirrors WP-03 acceptance on fake data)."""

from __future__ import annotations

from pathlib import Path

import pytest

from satk.core.envelope import dumps
from satk.core.errors import SatkError
from satk.core.ids import Sid
from satk.index.api import REFS_RELS, FakeIndexDB, IndexDB, InstRow


@pytest.fixture(scope="module")
def db() -> FakeIndexDB:
    return FakeIndexDB()


@pytest.fixture(scope="module")
def samp() -> FakeIndexDB:
    return FakeIndexDB("samp")


def _canonical(sid: str) -> bool:
    return str(Sid.parse(sid)) == sid


# ---------------------------------------------------------------- the three models


def test_q1_snippet(db):
    """SPEC WP-03 acceptance 13, on the fake."""
    mf = db.model_files(411)
    assert mf.dff.name == "infernus.dff"
    assert [b.name for b in mf.txd_chain] == ["infernus.txd", "vehicle.txd"]
    assert db.match_runtime(17613, (2489.3, -1668.5, 12.3)) == ["inst:lae2_stream0#4"]


def test_model_files_variants(db):
    for key in (411, "411", "infernus", "model:411", "model:INFERNUS"):
        mf = db.model_files(key)
        assert (mf.model_id, mf.name, mf.sec) == (411, "infernus", "cars")
    mf = db.model_files(411)
    assert mf.dff.sid == "dff:infernus" and mf.txd_chain[0].sid == "txd:infernus"
    assert mf.txd_chain[1].sid == "txd:vehicle" and mf.txd_chain[1].path.name == "vehicle.txd"
    assert mf.col.via == "embedded" and mf.col.blob.name == "infernus.dff"
    road = db.model_files(17613)
    assert road.col.via == "colfile" and road.col.blob.name == "lae2_4.col" and road.col.idx == 37
    assert [b.name for b in road.txd_chain] == ["lae2roadshub.txd"]
    cut = db.model_files(300)
    assert (cut.name, cut.sec, cut.dff, cut.txd_chain, cut.col) == ("cutobj01", "hier", None, [], None)


def test_get_model_411(db):
    o = db.get("model:411")
    assert o["ok"] and o["id"] == "model:411" and o["name"] == "infernus" and o["sec"] == "cars"
    assert o["links"]["txd_chain"] == ["txd:infernus", "txd:vehicle"]
    assert o["tex"] == {"total": 13, "missing": 0}
    assert db.get("model:infernus")["id"] == "model:411"
    assert db.get("model:411", ["name"]) == {"ok": True, "id": "model:411", "name": "infernus"}


def test_refs_inst_and_get_inst(db):
    r = db.refs("model:17613", rel="inst")
    row = dict(zip(r["cols"], r["rows"][0]))
    assert row["id"] == "inst:lae2_stream0#4" and row["pos"] == [2489.3, -1668.5, 12.3]
    o = db.get("inst:lae2_stream0#4")
    assert o["lod"] == "inst:lae2#198" and o["model"] == "model:17613" and o["rz"] == 0.0
    assert o["area"] == 0 and o["layer"] == "vanilla" and o["is_lod"] is False
    lod = db.get("inst:lae2#198")
    assert lod["is_lod"] is True and lod["model"] == "model:17858"
    kids = db.refs("inst:lae2#198", rel="hd_children")
    assert [r[0] for r in kids["rows"]] == ["inst:lae2_stream0#4"]


def test_shadowed_file(db):
    o = db.get("file:models/gta_int.img/lawest1.txd")
    assert o["active"] is False and o["shadowed_by"] == "file:models/gta3.img/lawest1.txd"
    q = db.query("SELECT count(*) FROM blob b JOIN source s ON s.id=b.source_id "
                 "WHERE s.relpath='models/gta_int.img' AND b.ext='txd' AND b.active=0")
    assert q["rows"] == [[1]]
    w = db.refs("file:models/gta3.img/lawest1.txd", rel="shadows")
    assert [r[0] for r in w["rows"]] == ["file:models/gta_int.img/lawest1.txd"]
    assert db.get("txd:lawest1")["file"] == "file:models/gta3.img/lawest1.txd"


def test_get_containers_and_loose(db):
    img = db.get("file:models/gta3.img")
    assert img["kind"] == "img" and img["load_order"] == 0 and img["entries"] == 10
    loose = db.get("file:models/generic/vehicle.txd")
    assert loose["ns"] == "loose" and loose["parsed"] == "txd:vehicle" and loose["offset"] == 0


def test_samp_profile(samp, db):
    a = samp.get("model:300")
    assert a["name"] == "lapdna" and a["layer"] == "samp" and a["links"]["overrides"] == ["model:300@vanilla"]
    b = samp.get("model:300@vanilla")
    assert b["name"] == "cutobj01" and b["active"] is False and b["id"] == "model:300@vanilla"
    assert samp.model_files(300).dff.name == "lapdna.dff"
    assert db.get("model:300")["name"] == "cutobj01"
    order = samp.meta()["img_order"]
    assert order.index("samp/samp.img") < order.index("models/gta3.img")


# ---------------------------------------------------------------- find / near / refs


def test_find(db):
    r = db.find("vent_64", kind="tex")
    assert r["total"] == 1 and r["rows"][0][0] == "tex:bistro/vent_64"
    r = db.find("infernus")
    assert r["rows"][0][0] == "model:411"  # exact names only (models before files) ...
    assert {row[1] for row in r["rows"]} == {"model", "dff", "txd", "col"}
    assert r["warn"][0].startswith("MORE: ")  # ... and a hint how many more contain the string
    r = db.find("*infernus*")  # pattern: substring
    assert {row[1] for row in r["rows"]} >= {"model", "dff", "txd", "tex"} and "warn" not in r
    assert db.find("infernus92*", kind="tex")["total"] == 3
    assert db.find("lawest1", kind="file")["total"] == 2
    assert db.find("infernus")["rows"][0][3] == "cars vehicles.ide"  # info as in SPEC 4.7
    assert db.find("vent_64")["rows"][0][3] == "X8R8G8B8 64x64"
    r = db.find("стол")
    assert r["ok"] and r["total"] == 0
    r = db.find("ve", kind="txd")  # < 3 chars: prefix only
    assert [row[0] for row in r["rows"]] == ["txd:vehicle"]
    with pytest.raises(SatkError) as e:
        db.find("x", kind="note")
    assert e.value.code == "BAD_PARAMS"


def test_find_paging(db):
    first = db.find("vehicle", kind="tex", limit=5)
    assert first["n"] == 5 and first["next"] == "o5" and first["total"] > 5
    seen = [r[0] for r in first["rows"]]
    cur = first["next"]
    while cur:
        page = db.find("vehicle", kind="tex", limit=5, cursor=cur)
        seen += [r[0] for r in page["rows"]]
        cur = page["next"]
    assert len(seen) == len(set(seen)) == first["total"]
    with pytest.raises(SatkError):
        db.find("vehicle", cursor="garbage")
    with pytest.raises(SatkError):
        db.find("vehicle", limit=0)


def test_near(db):
    r = db.near(2495, -1687, box=(2445, -1737, 2545, -1637), match="center", lod="all", area=None, limit=500)
    assert r["total"] == 2 and r["cols"][-1] == "dist"
    r = db.near(2489.3, -1668.5)  # defaults: r=50, hd only, area 0
    assert [row[0] for row in r["rows"]] == ["inst:lae2_stream0#4"]
    r = db.near(2489.3, -1668.5, lod="lod")
    assert [row[0] for row in r["rows"]] == ["inst:lae2#198"]
    assert db.near(2489.3, -1668.5, area=5)["total"] == 0
    # 'aabb' sees the road 40 m away (its COL box reaches it), 'center' does not
    assert db.near(2489.3 + 40, -1668.5, r=10.0)["total"] == 1
    assert db.near(2489.3 + 40, -1668.5, r=10.0, match="center")["total"] == 0
    assert db.near(0, 0, r=100)["total"] == 0
    for bad in ({"match": "x"}, {"lod": "x"}, {"kinds": ("model",)}):
        with pytest.raises(SatkError):
            db.near(0, 0, **bad)


def test_insts(db):
    rows = db.insts(model=17613)
    assert len(rows) == 1 and isinstance(rows[0], InstRow)
    row = rows[0]
    assert row.sid == "inst:lae2_stream0#4" and row.lod_sid == "inst:lae2#198" and row.is_lod is False
    assert row.pos == (2489.296875, -1668.5, 12.296875) and row.q_ipl == (0.0, 0.0, 0.0, 1.0)
    assert row.aabb[0] < row.pos[0] < row.aabb[3] and row.aabb[2] < row.pos[2] < row.aabb[5]
    assert [r.sid for r in db.insts(lod="all")] == ["inst:lae2#198", "inst:lae2_stream0#4"]
    assert [r.sid for r in db.insts(box=(2400, -1700, 2500, -1600), lod="all", limit=1)] == ["inst:lae2#198"]
    assert db.insts(center=(0, 0), r=10) == []


def test_match_runtime_tolerance(db):
    assert db.match_runtime(17613, (2489.33, -1668.5, 12.3)) == ["inst:lae2_stream0#4"]  # 0.033 m off
    assert db.match_runtime(17613, (2489.5, -1668.5, 12.3)) == []
    assert db.match_runtime(411, (2489.3, -1668.5, 12.3)) == []
    assert db.match_runtime(17613, (2489.5, -1668.5, 12.3), tol=1.0) == ["inst:lae2_stream0#4"]


def test_model_tex_refs(db):
    r = db.refs("model:411", rel="tex", limit=500)
    rows = [dict(zip(r["cols"], x)) for x in r["rows"]]
    assert len(rows) == 13 and all(x["id"] for x in rows)
    via = {x["texture"]: x["via"] for x in rows}
    assert via["infernus92wheel32"] == "own" and via["vehiclegeneric256"] == "vehicle"
    sql = db.query("SELECT via, count(*) FROM model_tex WHERE model_id = 411 GROUP BY via ORDER BY via")
    assert sql["rows"] == [["own", 3], ["vehicle", 10]]


@pytest.mark.parametrize("sid", [
    "model:411", "model:17613", "model:300", "tex:bistro/vent_64", "txd:infernus", "txd:vehicle", "dff:infernus",
    "col:lae2_roads89", "inst:lae2_stream0#4", "inst:lae2#198", "file:models/gta3.img/lawest1.txd",
    "ipl:lae2_stream0", "ipl:lae2", "ide:data/maps/la/lae2.ide",
])
def test_every_relation_runs_and_returns_canonical_sids(db, sid):
    kind = Sid.parse(sid).kind
    for rel in REFS_RELS[kind]:
        r = db.refs(sid, rel=rel, limit=500)
        assert r["ok"] and r["n"] == len(r["rows"])
        for row in r["rows"]:
            if row[0] is not None:
                assert _canonical(row[0]), (sid, rel, row)
    all_ = db.refs(sid)  # SPEC 4.7: counts per relation
    assert list(all_["rels"]) == list(REFS_RELS[kind])
    assert all(all_["rels"][r] == db.refs(sid, rel=r, limit=500)["total"] for r in REFS_RELS[kind])
    with pytest.raises(SatkError) as e:
        db.refs(sid, rel="bogus")
    assert e.value.code == "BAD_PARAMS" and e.value.data["rels"] == list(REFS_RELS[kind])
    o = db.get(sid)
    assert o["ok"] and _canonical(o["id"])
    dumps(o)  # JSON-serializable


def test_tex_and_pix(db):
    t = db.texture_ref("tex:bistro/vent_64")
    assert (t.w, t.h, t.d3dfmt, t.raster_fmt, t.alpha, t.pal_off) == (64, 64, "X8R8G8B8", 0x600, False, None)
    assert t.data_size == 64 * 64 * 4 and t.pix.startswith("pix:") and len(t.pix) == 4 + 24
    assert db.texture_ref(t.pix) == t
    p = db.get(t.pix)
    assert p["textures"] == ["tex:bistro/vent_64"]
    h = db.texture_ref("tex:infernus/infernus92handle32")
    assert h.d3dfmt == "DXT3" and h.alpha is True
    w = db.texture_ref("tex:bistro/sw_wallbrick_01")
    assert w.d3dfmt == "DXT1" and w.raster_fmt == 0x200 and w.alpha is False
    assert [x.sid for x in db.textures_of("txd:infernus")] == [
        "tex:infernus/infernus92wheel32", "tex:infernus/infernus92interior128", "tex:infernus/infernus92handle32"]
    assert len(db.textures_of("model:411")) == 13
    assert db.textures_of("model:300") == []


def test_blob_ref(db):
    b = db.blob_ref("dff:infernus")
    assert b.name == "infernus.dff" and b.path.name == "gta3.img" and b.offset % 2048 == 0
    f = db.blob_ref("file:models/gta_int.img/lawest1.txd")
    assert f.sid == "file:models/gta_int.img/lawest1.txd" and f.path.name == "gta_int.img"
    with pytest.raises(SatkError) as e:
        db.blob_ref("model:411")
    assert e.value.code == "BAD_ID"


def test_errors(db):
    with pytest.raises(SatkError) as e:
        db.get("model:infernos")
    assert e.value.code == "NOT_FOUND" and "model:infernus" in e.value.did_you_mean
    with pytest.raises(SatkError) as e:
        db.get("tex:bistro/vent_6")
    assert e.value.code == "NOT_FOUND" and "tex:bistro/vent_64" in e.value.did_you_mean
    with pytest.raises(SatkError) as e:
        db.get("fn:0x53bf09")
    assert e.value.code == "BAD_ID"
    with pytest.raises(SatkError) as e:
        db.get("garbage")
    assert e.value.code == "BAD_ID"
    with pytest.raises(SatkError) as e:
        FakeIndexDB("nope")
    assert e.value.code == "BAD_PARAMS"


def test_read_only_query(db):
    with pytest.raises(SatkError) as e:
        db.query("DELETE FROM model")
    assert e.value.code == "READ_ONLY"
    assert db.query("SELECT count(*) FROM model")["rows"] == [[4]]


def test_read_blob_without_files(db):
    with pytest.raises(SatkError) as e:
        db.read_blob(db.blob_ref("dff:infernus"))
    assert e.value.code == "NOT_FOUND"


# ---------------------------------------------------------------- payloads (synthetic bytes)


def test_payloads(tmp_path: Path):
    dff = b"\x10\x00\x00\x00" + b"synthetic-clump" * 3
    pix = bytes(range(256)) * 64  # 64x64 X8R8G8B8 = 16384 bytes
    vt = b"synthetic vehicle txd"
    fdb = FakeIndexDB(root=tmp_path / "root", payloads={"dff:infernus": dff, "tex:bistro/vent_64": pix,
                                                        "file:models/generic/vehicle.txd": vt})
    mf = fdb.model_files(411)
    assert fdb.read_blob(mf.dff) == dff and mf.dff.size == len(dff)
    assert fdb.read_blob(mf.txd_chain[1]) == vt
    t = fdb.texture_ref("tex:bistro/vent_64")
    with open(t.path, "rb") as f:
        f.seek(t.data_off)
        assert f.read(t.data_size) == pix
    # blobs never overlap inside an archive
    refs = sorted((fdb.blob_ref(f"file:models/gta3.img/{n}") for n in
                   ("infernus.dff", "infernus.txd", "bistro.txd", "lae2_4.col")), key=lambda b: b.offset)
    for a, b in zip(refs, refs[1:]):
        assert a.offset + a.size <= b.offset
    with pytest.raises(SatkError):
        FakeIndexDB(payloads={"dff:infernus": dff})  # no root
    with pytest.raises(SatkError):
        FakeIndexDB(root=tmp_path, payloads={"model:411": dff})


def test_payload_root_is_write_guarded(satk_home):
    root = satk_home / "gta-sa-clean"  # the clean copy of the isolated workspace is a protected root
    with pytest.raises(SatkError) as e:
        FakeIndexDB(root=root, payloads={"dff:infernus": b"x"})
    assert e.value.code == "PROTECTED_PATH"
    assert not root.exists()


# ---------------------------------------------------------------- SQL mirror == Python API


def test_sql_views_agree_with_api(db):
    v = db.query("SELECT sid, name, sec, n_inst, tex_total, tex_missing, layer FROM v_model ORDER BY id", limit=500)
    for sid, name, sec, n_inst, total, missing, layer in v["rows"]:
        o = db.get(sid)
        assert (o["name"], o["sec"], o["n_inst"], o["tex"]["total"], o["tex"]["missing"], o["layer"]) == (
            name, sec, n_inst, total, missing, layer)
    for sid, lod_sid in db.query("SELECT sid, lod_sid FROM v_inst")["rows"]:
        assert db.get(sid).get("lod") == lod_sid
    for sid, pix, active in db.query("SELECT sid, pix, active FROM v_tex", limit=500)["rows"]:
        if active:
            assert db.texture_ref(sid).pix == pix
    files = {r[0]: r for r in db.query("SELECT sid, active FROM v_file", limit=500)["rows"]}
    assert files["file:models/gta_int.img/lawest1.txd"][1] == 0
    for sid, (_, active) in files.items():
        assert db.get(sid)["active"] == bool(active)


def test_fts_mirror(db):
    r = db.query("SELECT sid FROM fts_name WHERE fts_name MATCH ? ORDER BY sid", ['"vent_6"'])
    assert r["rows"] == [["tex:bistro/vent_64"]]


def test_to_sqlite_roundtrip(tmp_path: Path, db):
    p = db.to_sqlite(tmp_path / "fake.sqlite")
    with IndexDB("vanilla", p) as real:
        for sql in ("SELECT * FROM v_model ORDER BY id", "SELECT * FROM v_inst ORDER BY rid",
                    "SELECT count(*) FROM texture", "SELECT * FROM meta ORDER BY key"):
            assert real.query(sql, limit=500) == db.query(sql, limit=500)
