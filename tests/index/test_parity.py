"""The SQL implementation (real ``IndexDB``) must answer exactly like the Q1 reference ``FakeIndexDB``.

The real ``IndexDB`` runs over the fake's own schema-v1 SQLite dump (``FakeIndexDB.to_sqlite``),
so every query method is exercised without game files.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from satk.core.errors import SatkError
from satk.core.ids import Sid
from satk.index.api import REFS_RELS, FakeIndexDB, IndexDB

SIDS = [
    "model:411", "model:infernus", "model:17613", "model:17858", "model:300", "tex:bistro/vent_64",
    "tex:bistro/sw_wallbrick_01", "tex:infernus/infernus92handle32", "tex:vehicle/vehiclegeneric256",
    "txd:infernus", "txd:vehicle", "txd:lawest1", "dff:infernus", "dff:lae2_roads89", "col:lae2_roads89",
    "col:infernus", "inst:lae2_stream0#4", "inst:lae2#198", "file:models/gta3.img/lawest1.txd",
    "file:models/gta_int.img/lawest1.txd", "file:models/gta3.img", "file:models/generic/vehicle.txd",
    "ipl:lae2_stream0", "ipl:lae2", "ide:data/maps/la/lae2.ide", "ide:data/vehicles.ide",
]


@pytest.fixture(scope="module", params=["vanilla", "samp"])
def pair(request, tmp_path_factory):
    fake = FakeIndexDB(request.param)
    path = fake.to_sqlite(tmp_path_factory.mktemp("parity") / f"{request.param}.sqlite")
    real = IndexDB(request.param, path)
    yield fake, real
    real.close()


def _close(a, b) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        return isinstance(a, (int, float)) and isinstance(b, (int, float)) and math.isclose(a, b, abs_tol=0.011)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_close(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_close(a[k], b[k]) for k in a)
    return a == b


def _same(a, b, what):
    assert _close(a, b), f"{what}:\n fake={a}\n real={b}"


@pytest.mark.parametrize("sid", SIDS)
def test_get_parity(pair, sid):
    fake, real = pair
    try:
        want = fake.get(sid)
    except SatkError as e:
        with pytest.raises(SatkError) as e2:
            real.get(sid)
        assert e2.value.code == e.code
        return
    _same(want, real.get(sid), f"get {sid}")


@pytest.mark.parametrize("sid", SIDS)
def test_refs_parity(pair, sid):
    fake, real = pair
    kind = Sid.parse(sid).kind
    try:
        fake.get(sid)
    except SatkError:
        return
    _same(fake.refs(sid), real.refs(sid), f"refs {sid}")
    for rel in REFS_RELS[kind]:
        _same(fake.refs(sid, rel=rel, limit=500), real.refs(sid, rel=rel, limit=500), f"refs {sid} --rel {rel}")


@pytest.mark.parametrize("q,kind", [
    ("infernus", None), ("*infernus*", None), ("vent_64", "tex"), ("vehicle", "tex"), ("ve", "txd"), ("lae2", None),
    ("lawest1", "file"), ("стол", None), ("infernus92*", "tex"), ("lae2*", "ipl"),
])
def test_find_parity(pair, q, kind):
    fake, real = pair
    _same(fake.find(q, kind, limit=500), real.find(q, kind, limit=500), f"find {q!r} {kind}")


def test_find_paging_parity(pair):
    fake, real = pair
    a = fake.find("vehicle", kind="tex", limit=4, cursor="o4")
    b = real.find("vehicle", kind="tex", limit=4, cursor="o4")
    _same(a, b, "find page 2")


@pytest.mark.parametrize("args,kw", [
    ((2489.3, -1668.5), {}), ((2489.3, -1668.5), {"lod": "lod"}), ((2489.3, -1668.5), {"lod": "all", "r": 5.0}),
    ((None, None), {"box": (2445, -1737, 2545, -1637), "match": "center", "lod": "all", "area": None, "limit": 500}),
    ((None, None), {"box": (2400, -1700, 2500, -1600), "lod": "all"}),
    ((2489.3 + 40, -1668.5), {"r": 10.0, "match": "center"}), ((0, 0), {"r": 100.0}),
    ((2489.3, -1668.5, 12.3), {"lod": "all"}),
])
def test_near_parity(pair, args, kw):
    fake, real = pair
    _same(fake.near(*args, **kw), real.near(*args, **kw), f"near {args} {kw}")


def test_typed_parity(pair):
    fake, real = pair
    for m in (411, "infernus", "model:17613", 300, "model:300"):
        assert fake.model_files(m) == real.model_files(m), m
    for sid in ("tex:bistro/vent_64", "tex:infernus/infernus92wheel32"):
        t = fake.texture_ref(sid)
        assert t == real.texture_ref(sid)
        assert real.texture_ref(t.pix) == fake.texture_ref(t.pix)
    for sid in ("txd:infernus", "txd:vehicle", "model:411", "model:300"):
        assert fake.textures_of(sid) == real.textures_of(sid), sid
    for sid in ("dff:infernus", "txd:vehicle", "file:models/gta_int.img/lawest1.txd"):
        assert fake.blob_ref(sid) == real.blob_ref(sid), sid
    assert real.match_runtime(17613, (2489.33, -1668.5, 12.3)) == ["inst:lae2_stream0#4"]
    assert real.match_runtime(17613, (2489.5, -1668.5, 12.3)) == []
    a = fake.insts(model=17613)
    b = real.insts(model=17613)
    assert [r.sid for r in a] == [r.sid for r in b] and a[0].pos == b[0].pos and a[0].lod_sid == b[0].lod_sid
    assert all(math.isclose(x, y, abs_tol=1e-3) for x, y in zip(a[0].aabb, b[0].aabb))
    assert [r.sid for r in real.insts(lod="all")] == [r.sid for r in fake.insts(lod="all")]
    assert ([r.sid for r in real.insts(box=(2400, -1700, 2500, -1600), lod="all", limit=1)]
            == [r.sid for r in fake.insts(box=(2400, -1700, 2500, -1600), lod="all", limit=1)])


@pytest.mark.parametrize("sid,code", [
    ("model:infernos", "NOT_FOUND"), ("tex:bistro/vent_6", "NOT_FOUND"), ("fn:0x53bf09", "BAD_ID"),
    ("garbage", "BAD_ID"), ("inst:lae2#9999", "NOT_FOUND"), ("txd:nope", "NOT_FOUND"),
])
def test_error_parity(pair, sid, code):
    fake, real = pair
    for db in (fake, real):
        with pytest.raises(SatkError) as e:
            db.get(sid)
        assert e.value.code == code, (db, sid)


def test_did_you_mean(pair):
    _fake, real = pair
    with pytest.raises(SatkError) as e:
        real.get("model:infernos")
    assert "model:infernus" in e.value.did_you_mean
    with pytest.raises(SatkError) as e:
        real.get("tex:bistro/vent_6")
    assert "tex:bistro/vent_64" in e.value.did_you_mean


@pytest.mark.parametrize("sid", ["inst:lae2_stream0#99999", "inst:lae2#99999", "inst:lae2_streum0#4"])
def test_placement_suggestions_are_resolvable(pair, sid):
    _fake, real = pair
    with pytest.raises(SatkError) as e:
        real.get(sid)
    assert e.value.code == "NOT_FOUND" and e.value.did_you_mean
    for candidate in e.value.did_you_mean:
        assert Sid.parse(candidate).kind in ("inst", "ipl")
        assert real.get(candidate)["ok"]


def test_bad_params(pair):
    _fake, real = pair
    for kw in ({"match": "x"}, {"lod": "x"}, {"kinds": ("model",)}):
        with pytest.raises(SatkError) as e:
            real.near(0, 0, **kw)
        assert e.value.code == "BAD_PARAMS"
    with pytest.raises(SatkError):
        real.near(None, None)
    with pytest.raises(SatkError):
        real.find("x", kind="note")
    with pytest.raises(SatkError):
        real.find("   ")
    with pytest.raises(SatkError):
        real.refs("model:411", rel="bogus")


def test_root_and_paths(pair):
    fake, real = pair
    assert real.root == fake.root
    assert real.blob_ref("dff:infernus").path == fake.root / "models" / "gta3.img"
    assert isinstance(real.blob_ref("txd:vehicle").path, Path)
