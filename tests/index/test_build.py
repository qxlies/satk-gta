"""Index builder on a synthetic game root (tests/index/conftest.py): pipeline, winners, links, AABB,
determinism, process pool, replace-while-open, layers, golden verify."""

from __future__ import annotations

import json
import hashlib
import struct
import math
import sqlite3
from pathlib import Path

import pytest

from satk.index.ddl import SCHEMA_VERSION

from satk.core.errors import SatkError
from satk.index import api
from satk.index.api import IndexDB
from satk.index.build import build, content_hash
from satk.index.golden import compute_metrics, verify
from satk.index.layers import HashCache, discover

DATS = ["data/default.dat", "data/gta.dat"]


def _build(root: Path, out: Path, jobs: int = 1, **kw) -> dict:
    return build("vanilla", jobs=jobs, out=out, root=root, dat_files=DATS, img_order="engine",
                 vanilla_manifest=root / "MANIFEST.sha256", hashcache=HashCache(out.parent / "hc.sqlite"), **kw)


@pytest.fixture
def built(game_root: Path, tmp_path: Path):
    out = tmp_path / "idx" / "vanilla.sqlite"
    rep = _build(game_root, out)
    db = IndexDB("vanilla", out)
    yield rep, db, game_root
    db.close()


def test_report(built):
    rep, db, _root = built
    assert rep["ok"] and rep["user_version"] == SCHEMA_VERSION and rep["size_mb"] < 1
    c = rep["counts"]
    assert (c["blobs"], c["txd"], c["texture"], c["dff"], c["models"], c["inst"]) == (12, 5, 6, 4, 4, 4)
    assert rep["lod"] == {"links": 2, "unresolved": 0}
    assert "errors" not in rep, rep.get("errors")
    m = db.meta()
    assert m["profile"] == "vanilla" and json.loads(m["img_order"])[:2] == ["models/gta3.img", "models/gta_int.img"]
    assert m["content_hash"] == rep["content_hash"]
    assert db.query("PRAGMA user_version")["rows"] == [[SCHEMA_VERSION]]


def test_metrics(built):
    _rep, db, _root = built
    m = compute_metrics(db._conn())
    assert m["img.archives"] == 2 and m["img.entries.gta3"] == 8 and m["img.entries.gta_int"] == 2
    assert m["loose.txd"] == 2 and m["blobs"] == 12  # vehicle.txd + extra.txd
    assert m["ipl.text_files"] == 1 and m["ipl.bin_files"] == 2 and m["ipl.inst"] == 4
    assert m["ipl.bin_orphans"] == 1 and m["ipl.bin_orphan_inst"] == 1
    assert m["col.files"] == 1 and m["col.models"] == 1 and m["col.embedded"] == 1
    assert m["shadow.gta_int.txd"] == 1
    assert m["ide.files"] == 3 and m["ide.defs"] == 4 and m["ide.txdp"] == 1
    assert m["texref.missing_main"] == 1  # box1 -> nosuchtex
    assert m["layer.modded.blobs"] == 1 and m["layer.vanilla.blobs"] == 11


def test_model_car(built):
    _rep, db, _root = built
    o = db.get("model:400")
    assert o["name"] == "testcar" and o["sec"] == "cars"
    assert o["links"]["txd_chain"] == ["txd:testcar", "txd:vehicle"]
    assert o["links"]["col_via"] == "embedded" and o["links"]["col"] == "col:testcar"
    assert o["tex"] == {"total": 2, "missing": 0}
    rows = {r[1]: r for r in db.refs("model:400", rel="tex")["rows"]}
    assert rows["carbody"][2] == "own" and rows["vehiclegeneric256"][2] == "vehicle"
    mf = db.model_files("testcar")
    assert mf.dff.name == "testcar.dff" and [b.name for b in mf.txd_chain] == ["testcar.txd", "vehicle.txd"]
    assert mf.col.via == "embedded" and mf.col.blob.name == "testcar.dff"
    # read_blob returns the real bytes of the IMG entry
    data = db.read_blob(mf.dff)
    assert data[:4] == (0x10).to_bytes(4, "little")


def test_model_box_and_missing_texture(built):
    _rep, db, _root = built
    o = db.get("model:1000")
    assert o["tex"] == {"total": 2, "missing": 1} and o["links"]["col"] == "col:box1"
    assert o["links"]["col_via"] == "colfile" and o["n_inst"] == 3  # incl. the orphan bnry
    tex = {r[1]: r[2] for r in db.refs("model:1000", rel="tex")["rows"]}
    assert tex == {"boxtex": "own", "nosuchtex": "missing"}
    assert [t.sid for t in db.textures_of("model:1000")] == ["tex:boxes/boxtex"]
    t = db.texture_ref("tex:boxes/boxtex")
    assert (t.w, t.h, t.d3dfmt, t.alpha) == (4, 4, "DXT1", False)
    with open(t.path, "rb") as f:
        f.seek(t.data_off)
        raw = f.read(t.data_size)
    assert raw == bytes((3 * 31 + i * 7) & 0xFF for i in range(8))
    assert db.texture_ref(t.pix).sid == "tex:boxes/boxtex"


def test_shadowing(built):
    _rep, db, _root = built
    f = db.get("file:models/gta_int.img/boxes.txd")
    assert f["active"] is False and f["shadowed_by"] == "file:models/gta3.img/boxes.txd"
    assert db.get("txd:boxes")["file"] == "file:models/gta3.img/boxes.txd"
    # same layer loser: only addressable by file:, its texture by @layer
    rows = db.refs("file:models/gta3.img/boxes.txd", rel="shadows")["rows"]
    assert rows == [["file:models/gta_int.img/boxes.txd", "boxes.txd", "vanilla"]]
    assert db.get("tex:boxes/boxtex@vanilla")["active"] is False
    assert db.get("tex:boxes/boxtex")["active"] is True


@pytest.fixture
def duplicate_txds(game_root, tmp_path, asset_builders):
    b = asset_builders
    added = {
        "models/player.img": b.img_v2([("boxes.txd", b.txd([("coach", 11), ("boxtex", 12)]))]),
        "models/boxes.txd": b.txd([("loose_only", 13), ("boxtex", 14)]),
    }
    manifest = game_root / "MANIFEST.sha256"
    with manifest.open("a", encoding="ascii") as f:
        for rel, data in added.items():
            (game_root / rel).write_bytes(data)
            f.write(f"{hashlib.sha256(data).hexdigest()}  {rel}\n")
    # Repeated names within a TXD also need distinct, resolvable content identities.
    (game_root / "models/extra.txd").write_bytes(b.txd([("extratex", 1), ("extratex", 2)]))
    out = tmp_path / "duplicates.sqlite"
    _build(game_root, out)
    with IndexDB("vanilla", out) as db:
        yield db


def test_texture_search_sid_opens_same_player_texture(duplicate_txds):
    db = duplicate_txds
    result = db.find("coach", kind="tex", limit=100)
    assert result["n"] == 1
    sid, kind, name, info = result["rows"][0]
    ref = db.texture_ref(sid)
    assert ref.path.name.lower() == "player.img" and kind == "tex" and name == "coach"
    assert sid == ref.sid == ref.pix  # namespace cannot be expressed as tex:<txd>/<name>@<layer>
    assert info == "DXT1 4x4"
    assert db.texture_ref(ref.sid) == ref
    assert db.texture_ref(ref.pix) == ref


def test_every_emitted_texture_sid_roundtrips(duplicate_txds):
    db = duplicate_txds
    found = db.find("*", kind="tex", limit=500)
    expected = {"pix:" + row[0].lower() for row in db.query("SELECT hex(hash) FROM texture")["rows"]}
    refs = [db.texture_ref(row[0]) for row in found["rows"]]
    assert {ref.pix for ref in refs} == expected
    assert len(refs) == len(expected)  # all synthetic mip payloads in this fixture differ
    for row, ref in zip(found["rows"], refs):
        assert ref.sid == row[0]
        assert db.texture_ref(ref.sid) == ref
        assert db.get(row[0])["id"] == row[0]
    extra = db.textures_of("txd:extra")
    assert len(extra) == 2 and extra[0].sid == "tex:extra/extratex" and extra[1].sid == extra[1].pix
    assert [row[0] for row in db.refs("txd:extra", rel="textures")["rows"]] == [ref.sid for ref in extra]
    assert db.texture_ref("tex:boxes/boxtex@vanilla").path.name.lower() == "gta_int.img"


@pytest.mark.parametrize("name", ["tw@t_wall1", "speeder92interior128 ", "slash/name", "control\x01"])
def test_texture_names_outside_sid_grammar_use_pixel_identity(game_root, tmp_path, asset_builders, name):
    (game_root / "models/extra.txd").write_bytes(asset_builders.txd([(name, 19)]))
    out = tmp_path / "texture_name.sqlite"
    _build(game_root, out)
    with IndexDB("vanilla", out) as db:
        (ref,) = db.textures_of("txd:extra")
        assert ref.sid == ref.pix and db.texture_ref(ref.sid) == ref
        found = db.find("*", kind="tex", limit=100)
        row = next(row for row in found["rows"] if row[2] == name)
        assert row[0] == ref.sid and row[3] == "DXT1 4x4"
        assert db.refs("txd:extra", rel="textures")["rows"][0][0] == ref.sid


def test_shadowed_col_bundle_cannot_supply_active_model_collision(game_root, tmp_path, asset_builders):
    b = asset_builders
    (game_root / "models/gta_int.img").write_bytes(b.img_v2([
        ("test.col", b.col3("lodbox1", (-100, -100, -100), (100, 100, 100))),
    ]))
    out = tmp_path / "shadowed_col.sqlite"
    _build(game_root, out)
    with IndexDB("vanilla", out) as db:
        assert db.get("file:models/gta_int.img/test.col")["active"] is False
        assert db.query("SELECT active FROM col WHERE name='lodbox1'")["rows"] == [[0]]
        assert db.model_files(1001).col is None
        assert db.get("inst:test#0")["bbox_src"] == "dff"


def test_explicit_colfile_keeps_precedence_over_img_bundle(game_root, tmp_path, asset_builders):
    loose = game_root / "models/test.col"
    loose.write_bytes(asset_builders.col3("box1", (-20, -20, -20), (20, 20, 20)))
    with (game_root / "data/gta.dat").open("a", encoding="ascii") as f:
        f.write("COLFILE 0 MODELS\\TEST.COL\n")
    out = tmp_path / "explicit_col.sqlite"
    _build(game_root, out)
    with IndexDB("vanilla", out) as db:
        assert db.get("file:models/test.col")["active"] is False  # IMG still wins the blob namespace
        col = db.model_files(1000).col
        assert col is not None and col.blob.path == loose and col.via == "colfile"
        assert db.query("SELECT bmin_x,bmax_x FROM col WHERE name='box1' AND active=1")["rows"] == [[-20, 20]]


def test_placements_lod_and_aabb(built):
    _rep, db, _root = built
    i = db.get("inst:test#1")
    assert i["model"] == "model:1000" and i["lod"] == "inst:test#0" and i["rz"] == pytest.approx(90.0)
    assert i["bbox_src"] == "col"
    # COL box (-1,-2,0)-(1,2,1) turned +90 deg about Z: x extent +-2, y extent +-1
    assert i["aabb"] == pytest.approx([98.0, 199.0, 10.0, 102.0, 201.0, 11.0], abs=0.02)
    b = db.get("inst:test_stream0#0")
    assert b["lod"] == "inst:test#0" and b["ipl"] == "ipl:test_stream0"
    lod = db.get("inst:test#0")
    assert lod["is_lod"] is True and lod["bbox_src"] == "dff"
    assert db.get("ipl:test_stream0")["parent"] == "ipl:test"
    assert db.get("ipl:orphan_stream0")["loaded"] is False
    kids = [r[0] for r in db.refs("inst:test#0", rel="hd_children")["rows"]]
    assert kids == ["inst:test#1", "inst:test_stream0#0"]
    assert db.match_runtime(1000, (150, 250, 5)) == ["inst:test_stream0#0"]
    assert db.match_runtime(1000, (100.01, 200.0, 10.0)) == ["inst:test#1"]


def test_near_and_items(built):
    _rep, db, _root = built
    r = db.near(100, 200, r=10)
    assert [x[0] for x in r["rows"]] == ["inst:test#1"]  # hd only by default
    r = db.near(100, 200, r=10, lod="all")
    assert [x[0] for x in r["rows"]] == ["inst:test#0", "inst:test#1"]
    r = db.near(None, None, box=(90, 190, 160, 260), match="center", lod="all", area=None)
    assert r["total"] == 3
    r = db.near(100, 200, r=5, kinds=("inst", "item", "zone"), lod="all")
    kinds = {x[0].split(":")[0] for x in r["rows"]}
    assert kinds == {"inst", "item", "zone"}
    assert db.get("zone:testzone")["label"] == "TESTZ"
    assert db.get("item:test#enex#0")["sec"] == "enex"
    assert db.get("item:test_stream0#cars#0")["data"]["model_id"] == 400
    assert db.refs("zone:testzone", rel="inst")["total"] == 3


@pytest.mark.parametrize("x,y,z,r,match,distance", [
    (49.25, 149.25, None, 1, "aabb", None),  # square candidate, outside the circle
    (50, 150, 999999, 1, "aabb", None),
    (50, 150, 100, 0, "aabb", 0),
    (50, 150, None, 0, "aabb", 0),
    (47, 146, 45, 5, "aabb", 5),             # exact 3-4-5 corner distance
    (47, 146, 45, 4.99, "aabb", None),
    (46, 150, -13, 5, "aabb", 5),           # X and Z both contribute
    (46, 150, -13, 4.99, "aabb", None),
    (125, 225, 46, 1, "center", 1),
    (50, 150, 45, 1, "center", None),
])
def test_zone_queries_apply_exact_radius_and_z(built, x, y, z, r, match, distance):
    _, db, _ = built
    result = db.near(x, y, z=z, r=r, match=match, kinds=("zone",), area=None)
    assert result["total"] == (0 if distance is None else 1)
    if distance is not None:
        assert result["rows"][0][0] == "zone:testzone"
        assert result["rows"][0][-1] == pytest.approx(distance)


@pytest.mark.parametrize("match", ["center", "aabb"])
def test_insts_intersects_box_and_radius(built, match):
    _, db, _ = built
    box = (90, 190, 160, 260)
    kw = {"box": box, "match": match, "lod": "all", "area": None}
    assert db.insts(center=(0, 0, 0), r=1, **kw) == []
    hits = db.insts(center=(100, 200, 10), r=0, **kw)
    assert {i.sid for i in hits} == {"inst:test#0", "inst:test#1"}
    assert db.insts(center=(150, 250, 1000), r=1, **kw) == []
    hits = db.insts(center=(100, 200, 10), r=0, model=1000, limit=1, **kw)
    assert [i.sid for i in hits] == ["inst:test#1"]
    # near() retains its documented point-or-box semantics even if x/y/r are supplied.
    assert db.near(0, 0, r=1, **kw)["total"] == 3


def test_insts_combined_filters_use_requested_distance_mode(built):
    _, db, _ = built
    kw = {"box": (99.5, 199.5, 100.5, 200.5), "center": (97, 200, 10.5), "r": 1}
    assert [i.sid for i in db.insts(match="aabb", **kw)] == ["inst:test#1"]
    assert db.insts(match="center", **kw) == []


def test_find(built):
    _rep, db, _root = built
    r = db.find("box1")
    assert [x[0] for x in r["rows"]] == ["model:1000", "dff:box1", "col:box1"]
    assert r["warn"][0].startswith("MORE: 2 ")  # model and dff lodbox1
    assert db.find("*box1*", kind="model")["total"] == 2
    assert db.find("grove", kind="tex")["rows"][0][0] == "tex:boxes/grove_tag"
    assert db.find("bo", kind="txd")["rows"][0][0] == "txd:boxes"
    assert db.find("boxes.txd", kind="file")["total"] == 2
    assert db.find("стол")["total"] == 0


def test_layers(game_root: Path, tmp_path: Path):
    d = discover("vanilla", root=game_root, dat_files=DATS, img_order="engine",
                 vanilla_manifest=game_root / "MANIFEST.sha256", hashcache=HashCache(tmp_path / "hc.sqlite"))
    by = d.by_relpath()
    assert by["models/gta3.img"].layer == "vanilla" and by["models/gta3.img"].load_order == 0
    assert by["models/extra.txd"].layer == "modded" and by["models/extra.txd"].loose
    assert by["samp/samp.ide"].layer == "samp" and by["samp/samp.ide"].load_ref is None
    assert by["data/maps/test/test.ide"].load_ref == "data/gta.dat:1"
    assert by["data/map.zon"].kind == "zon"
    assert [la.name for la in d.layers] == ["vanilla", "samp", "modded"]
    assert d.hash_stats["hashed"] > 0
    # second run: everything from the cache
    d2 = discover("vanilla", root=game_root, dat_files=DATS, img_order="engine",
                  vanilla_manifest=game_root / "MANIFEST.sha256", hashcache=HashCache(tmp_path / "hc.sqlite"))
    assert d2.hash_stats["hashed"] == 0 and d2.hash_stats["cached"] == d.hash_stats["hashed"]


def test_modified_vanilla_file_becomes_modded(game_root: Path, tmp_path: Path):
    p = game_root / "data" / "maps" / "test" / "test.ide"
    p.write_text(p.read_text() + "\n", encoding="latin-1")
    d = discover("vanilla", root=game_root, dat_files=DATS, img_order="engine",
                 vanilla_manifest=game_root / "MANIFEST.sha256", hashcache=HashCache(tmp_path / "hc.sqlite"))
    assert d.by_relpath()["data/maps/test/test.ide"].layer == "modded"


def test_modloader_internal_directories_are_not_mods(game_root: Path, tmp_path: Path):
    for rel in ("ModLoader/.data/plugins/std.dll", "ModLoader/.profiles/default.ini",
                "ModLoader/ExampleMod/config.ini", "ModLoader/ExampleMod/.settings/options.ini"):
        path = game_root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic", encoding="ascii")
    discovery = discover("vanilla", root=game_root, dat_files=DATS, img_order="engine",
                         vanilla_manifest=game_root / "MANIFEST.sha256", hashcache=HashCache(tmp_path / "hc.sqlite"))
    sources = discovery.by_relpath()
    assert "modloader/.data/plugins/std.dll" not in sources
    assert "modloader/.profiles/default.ini" not in sources
    assert sources["modloader/examplemod/config.ini"].layer == "modloader:examplemod"
    assert "modloader/examplemod/.settings/options.ini" in sources
    assert not any(layer.name.startswith("modloader:.") for layer in discovery.layers)


def test_deterministic_and_parallel(game_root: Path, tmp_path: Path):
    a = _build(game_root, tmp_path / "a" / "vanilla.sqlite", jobs=1)
    b = _build(game_root, tmp_path / "b" / "vanilla.sqlite", jobs=2)
    assert a["content_hash"] == b["content_hash"]
    c = sqlite3.connect(tmp_path / "b" / "vanilla.sqlite")
    try:
        assert content_hash(c) == a["content_hash"]
    finally:
        c.close()



def test_hash_changes_with_content(make_game_fn, tmp_path: Path):
    r1 = make_game_fn(tmp_path / "g1")
    r2 = make_game_fn(tmp_path / "g2", variant=1)  # one texture differs
    a = _build(r1, tmp_path / "a" / "vanilla.sqlite")
    b = _build(r2, tmp_path / "b" / "vanilla.sqlite")
    assert a["content_hash"] != b["content_hash"]


def test_rebuild_while_open(game_root: Path, tmp_path: Path):
    out = tmp_path / "idx" / "vanilla.sqlite"
    first = _build(game_root, out)
    api.clear_cache()
    db = api.open_index("vanilla", out)
    assert db.get("model:400")["name"] == "testcar"
    second = _build(game_root, out)  # Windows: the open file cannot be renamed over -> backup API
    assert second["replaced_by"] in ("replace", "backup")
    assert second["content_hash"] == first["content_hash"]
    db2 = api.open_index("vanilla", out)  # mtime changed -> reopened
    assert db2.get("model:400")["name"] == "testcar"
    assert not list(out.parent.glob(".*.tmp"))
    api.clear_cache()


def test_stale_warning(built):
    _rep, db, root = built
    assert db.stale_warnings() == []
    p = root / "data" / "maps" / "test" / "test.ipl"
    p.write_bytes(p.read_bytes() + b"# touched\r\n")
    db._fresh = None  # drop the 2 s cache
    w = db.stale_warnings()
    assert w and w[0].startswith("INDEX_STALE: data/maps/test/test.ipl changed")


def test_verify_golden(built):
    _rep, db, _root = built
    golden = {"metrics": {"blobs": 12, "ipl.inst": 4, "texref.missing_main": 1},
              "examples": [{"get": "model:400", "expect": {"links.txd_chain": ["txd:testcar", "txd:vehicle"]}},
                           {"find": "box1", "kind": "model", "total": 1},
                           {"near": {"x": 100, "y": 200, "r": 10, "lod": "all"}, "total": 2},
                           {"model_files": 400, "expect": {"txd_chain": ["testcar.txd", "vehicle.txd"]}},
                           {"match_runtime": [1000, [150, 250, 5]], "expect": ["inst:test_stream0#0"]},
                           {"refs": "model:1000", "rel": "inst", "contains": "inst:test#1"}]}
    r = verify(db, golden)
    assert r["ok"] and r["checked"] == 9, r["failed"]
    bad = verify(db, {"metrics": {"blobs": 13}, "examples": [{"get": "model:400", "expect": {"name": "x"}}]})
    assert not bad["ok"] and bad["failed"][0] == ["blobs", 12, 13] and "name" in bad["failed"][1][1]


def test_missing_root_and_dat(tmp_path: Path):
    with pytest.raises(SatkError) as e:
        build("vanilla", out=tmp_path / "x.sqlite", root=tmp_path / "nope", dat_files=DATS, img_order="engine")
    assert e.value.code == "NOT_FOUND"
    (tmp_path / "g").mkdir()
    with pytest.raises(SatkError) as e:
        build("vanilla", out=tmp_path / "x.sqlite", root=tmp_path / "g", dat_files=DATS, img_order="engine")
    assert e.value.code == "NOT_FOUND" and "DAT" in str(e.value)
    assert not (tmp_path / "x.sqlite").exists()


def test_broken_blob_does_not_stop_the_build(game_root: Path, tmp_path: Path):
    from satk.formats.img import ImgArchive

    p = game_root / "models" / "gta_int.img"
    with ImgArchive.open(p) as a:
        e = a.find("intbox.dff")
    data = bytearray(p.read_bytes())
    data[e.abs_offset:e.abs_offset + 24] = struct.pack("<III", 0x10, 0xFFFFFF, 0x1803FFFF) * 2  # claims 16 MB
    p.write_bytes(bytes(data))
    rep = _build(game_root, tmp_path / "idx" / "vanilla.sqlite")
    assert rep["ok"] and rep["counts"]["dff"] == 3
    assert any("intbox.dff" in x for x in rep["errors"])


def test_writes_only_under_work(game_root: Path, tmp_path: Path, satk_home: Path):
    out = satk_home / "src" / "x.sqlite"  # src\ of the isolated workspace is a protected root
    with pytest.raises(SatkError) as e:
        build("vanilla", out=out, root=game_root, dat_files=DATS, img_order="engine")
    assert e.value.code == "PROTECTED_PATH"
    assert not out.exists()


def test_zone_refs_formats_only_the_requested_page(built, monkeypatch):
    from satk.index.queries import Q

    _rep, db, _root = built
    expected = db.refs("zone:testzone", rel="inst", limit=500)
    calls = []
    original = Q.inst_cells

    def cells(query, row):
        calls.append(row["id"])
        return original(query, row)

    monkeypatch.setattr(Q, "inst_cells", cells)
    assert db.refs("zone:testzone")["rels"]["inst"] == expected["total"] == 3
    for offset in range(3):
        calls.clear()
        page = db.refs("zone:testzone", rel="inst", limit=1, cursor=f"o{offset}")
        assert page["rows"] == expected["rows"][offset:offset + 1] and page["total"] == 3
        assert page["next"] == (f"o{offset + 1}" if offset < 2 else None)
        assert len(calls) == 1  # formatting work must scale with the page, not the entire zone


@pytest.mark.parametrize("kinds", [("inst",), ("inst", "item", "zone")])
def test_large_near_formats_only_returnable_instances(built, monkeypatch, kinds):
    from satk.index.queries import Q

    _rep, db, _root = built
    args = dict(r=100000, lod="all", area=None, kinds=kinds)
    full = db.near(100, 200, limit=500, **args)
    calls = []
    original = Q.inst_cells

    def cells(query, row):
        calls.append(row["id"])
        return original(query, row)

    monkeypatch.setattr(Q, "inst_cells", cells)
    page = db.near(100, 200, limit=1, **args)
    assert page["rows"] == full["rows"][:1] and page["total"] == full["total"]
    assert len(calls) == 1
