"""Contract Q1 (SPEC §4.3.4): shapes of the value types, signatures, real IndexDB basics."""

from __future__ import annotations

import ast
import dataclasses
import inspect
import math
import sqlite3
import threading
from pathlib import Path

import pytest

from satk.index.ddl import SCHEMA_VERSION

from satk.core.errors import SatkError
from satk.index import api
from satk.index.api import (
    BlobRef, ColRef, FakeIndexDB, IndexDB, IndexNotImplemented, InstRow, ModelFiles, TexRef,
)

SRC_INDEX = Path(api.__file__).parent

# ---------------------------------------------------------------- value types

EXPECTED_FIELDS = {
    BlobRef: ["sid", "path", "offset", "size", "name"],
    TexRef: ["sid", "pix", "path", "data_off", "data_size", "pal_off", "d3dfmt", "raster_fmt", "platform",
             "w", "h", "levels", "alpha"],
    ColRef: ["sid", "blob", "idx", "name", "via"],
    ModelFiles: ["model_id", "name", "sec", "dff", "txd_chain", "col"],
    InstRow: ["sid", "model_id", "name", "pos", "q_ipl", "area", "iflags", "lod_sid", "is_lod", "aabb"],
}


@pytest.mark.parametrize("cls", list(EXPECTED_FIELDS), ids=lambda c: c.__name__)
def test_value_type_fields(cls):
    assert [f.name for f in dataclasses.fields(cls)] == EXPECTED_FIELDS[cls]
    params = cls.__dataclass_params__
    assert params.frozen
    assert hasattr(cls, "__slots__")


def test_value_types_are_frozen():
    b = BlobRef("dff:x", Path("x.img"), 0, 1, "x.dff")
    with pytest.raises(dataclasses.FrozenInstanceError):
        b.size = 2  # type: ignore[misc]


# ---------------------------------------------------------------- signatures

EXPECTED_SIGS = {
    "__init__": "(self, profile: 'str' = 'vanilla', path: 'Path | None' = None)",
    "get": "(self, sid: 'str', fields: 'list[str] | None' = None) -> 'dict'",
    "find": "(self, q: 'str', kind: 'str | None' = None, limit: 'int' = 20, cursor: 'str | None' = None) -> 'dict'",
    "refs": "(self, sid: 'str', rel: 'str | None' = None, limit: 'int' = 50, cursor: 'str | None' = None) -> 'dict'",
    "near": "(self, x: 'float', y: 'float', z: 'float | None' = None, r: 'float' = 50.0, "
            "box: 'tuple[float, float, float, float] | None' = None, match: 'str' = 'aabb', "
            "kinds: 'tuple[str, ...]' = ('inst',), area: 'int | None' = 0, lod: 'str' = 'hd', "
            "limit: 'int' = 50) -> 'dict'",
    "insts": "(self, *, model: 'int | None' = None, box=None, center=None, r=None, area: 'int | None' = 0, "
             "lod: 'str' = 'hd', match: 'str' = 'aabb', limit: 'int | None' = None) -> 'list[InstRow]'",
    "model_files": "(self, model: 'int | str') -> 'ModelFiles'",
    "texture_ref": "(self, sid: 'str') -> 'TexRef'",
    "textures_of": "(self, sid: 'str') -> 'list[TexRef]'",
    "blob_ref": "(self, sid: 'str') -> 'BlobRef'",
    "read_blob": "(self, ref: 'BlobRef') -> 'bytes'",
    "match_runtime": "(self, model_id: 'int', pos, tol: 'float' = 0.05) -> 'list[str]'",
    "query": "(self, sql: 'str', params: 'list' = (), limit: 'int' = 200) -> 'dict'",
}


@pytest.mark.parametrize("name", list(EXPECTED_SIGS))
def test_indexdb_signature(name):
    assert str(inspect.signature(getattr(IndexDB, name))) == EXPECTED_SIGS[name]


@pytest.mark.parametrize("name", [n for n in EXPECTED_SIGS if n != "__init__"])
def test_fake_signature_matches(name):
    real = inspect.signature(getattr(IndexDB, name))
    fake = inspect.signature(getattr(FakeIndexDB, name))
    assert list(real.parameters) == list(fake.parameters)
    for p in real.parameters.values():
        q = fake.parameters[p.name]
        assert (p.kind, p.default) == (q.kind, q.default), p.name


def test_fake_init_accepts_real_args():
    sig = inspect.signature(FakeIndexDB.__init__)
    assert list(sig.parameters)[:3] == ["self", "profile", "path"]
    assert isinstance(FakeIndexDB(), IndexDB)


def test_fake_reexported_from_api():
    from satk.index.fake import FakeIndexDB as F

    assert api.FakeIndexDB is F is FakeIndexDB


# ---------------------------------------------------------------- real IndexDB (Q1 part)


@pytest.fixture
def db_file(tmp_path: Path) -> Path:
    return FakeIndexDB().to_sqlite(tmp_path / "index" / "vanilla.sqlite")


def test_missing_index(tmp_path: Path):
    with pytest.raises(SatkError) as e:
        IndexDB("vanilla", tmp_path / "nope.sqlite")
    assert e.value.code == "INDEX_MISSING"
    assert "index build" in e.value.hint


def test_missing_index_default_path(satk_home):
    with pytest.raises(SatkError) as e:
        IndexDB("vanilla")
    assert e.value.code == "INDEX_MISSING"
    assert e.value.data["path"].endswith("/work/index/vanilla.sqlite")


def test_unknown_profile(satk_home):
    with pytest.raises(SatkError) as e:
        IndexDB("vanila")
    assert e.value.code == "BAD_PARAMS"
    assert "vanilla" in e.value.did_you_mean


def test_wrong_schema_version(tmp_path: Path):
    p = tmp_path / "old.sqlite"
    c = sqlite3.connect(p)
    c.execute("PRAGMA user_version = 7")
    c.close()
    with pytest.raises(SatkError) as e:
        IndexDB("vanilla", p)
    assert e.value.code == "INDEX_MISSING"


def test_query_and_meta(db_file: Path):
    with IndexDB("vanilla", db_file) as db:
        assert db.meta()["profile"] == "vanilla"
        r = db.query("SELECT sid, name FROM v_model WHERE id = ?", [411])
        assert r["ok"] and r["cols"] == ["sid", "name"] and r["rows"] == [["model:411", "infernus"]]
        r = db.query("SELECT hash FROM image LIMIT 1")
        assert isinstance(r["rows"][0][0], str) and len(r["rows"][0][0]) == 24  # BLOB -> hex
        r = db.query("SELECT id FROM model", limit=2)
        assert r["n"] == 2 and r["total"] == 4 and any(w.startswith("LIMIT") for w in r["warn"])
        assert db.query("PRAGMA table_info(model)")["n"] > 5
        assert db.query("PRAGMA user_version")["rows"] == [[SCHEMA_VERSION]]


@pytest.mark.parametrize("sql", [
    "DELETE FROM model",
    "delete from model",
    "  /* c */ UPDATE model SET name='x'",
    "INSERT INTO meta VALUES ('a','b')",
    "DROP TABLE model",
    "CREATE TABLE x(a)",
    "ATTACH DATABASE 'x.sqlite' AS x",
    "PRAGMA user_version = 3",
    "WITH a AS (SELECT 1) DELETE FROM model",
    "REPLACE INTO meta VALUES ('a','b')",
])
def test_query_read_only(db_file: Path, sql: str):
    with IndexDB("vanilla", db_file) as db:
        with pytest.raises(SatkError) as e:
            db.query(sql)
        assert e.value.code == "READ_ONLY", sql
        assert db.query("SELECT count(*) FROM model")["rows"] == [[4]]


def test_query_bad_sql(db_file: Path):
    with IndexDB("vanilla", db_file) as db:
        for sql in ("SELECT * FROM no_such_table", "SELECT 1; SELECT 2", "", "SELECT"):
            with pytest.raises(SatkError) as e:
                db.query(sql)
            assert e.value.code == "BAD_PARAMS", sql


def test_query_threads(db_file: Path):
    db = IndexDB("vanilla", db_file)
    out: list = []

    def work():
        for _ in range(20):
            out.append(db.query("SELECT count(*) FROM blob")["rows"][0][0])

    ts = [threading.Thread(target=work) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    db.close()
    assert len(out) == 80 and len(set(out)) == 1


def test_index_not_implemented_kept_for_compat():
    e = IndexNotImplemented("x")
    assert isinstance(e, NotImplementedError) and e.code == "NOT_READY"


def test_read_blob(tmp_path: Path):
    f = tmp_path / "a.img"
    f.write_bytes(b"\0" * 10 + b"HELLO" + b"\0" * 5)
    ref = BlobRef("file:a.img/h.dff", f, 10, 5, "h.dff")
    assert api.read_blob_bytes(ref) == b"HELLO"
    with pytest.raises(SatkError) as e:
        api.read_blob_bytes(BlobRef("file:a.img/h.dff", f, 18, 5, "h.dff"))
    assert e.value.code == "NOT_FOUND"
    with pytest.raises(SatkError) as e:
        api.read_blob_bytes(BlobRef("file:b.img/h.dff", tmp_path / "b.img", 0, 1, "h.dff"))
    assert e.value.code == "NOT_FOUND"


# ---------------------------------------------------------------- factory


def test_open_index_fake_env(monkeypatch):
    monkeypatch.setenv("SATK_INDEX_FAKE", "1")
    api.clear_cache()
    try:
        a = api.open_index("vanilla")
        assert isinstance(a, FakeIndexDB) and a is api.open_index("vanilla")
        assert api.open_index("samp").profile == "samp"
    finally:
        api.clear_cache()


def test_override_index():
    fake = FakeIndexDB()
    with api.override_index(fake):
        assert api.open_index("vanilla") is fake
        with api.override_index(lambda p: FakeIndexDB(p)):
            assert api.open_index("samp").profile == "samp"
        assert api.open_index("installed") is fake


def test_open_index_cache_and_reopen(db_file: Path):
    api.clear_cache()
    try:
        a = api.open_index("vanilla", db_file)
        assert a is api.open_index("vanilla", db_file)
        # Windows cannot replace a file SQLite holds open: the rebuild happens after close
        a.close()
        FakeIndexDB().to_sqlite(db_file)  # atomic replace -> new mtime
        import os
        st = db_file.stat()
        os.utime(db_file, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
        b = api.open_index("vanilla", db_file)
        assert b is not a
        assert b.query("SELECT 1")["rows"] == [[1]]
    finally:
        api.clear_cache()


# ---------------------------------------------------------------- geometry helpers


def test_world_quat_and_rz():
    assert api.world_quat((0.1, 0.2, 0.3, 0.9)) == (-0.1, -0.2, -0.3, 0.9)
    assert api.world_rz((0, 0, 0, 1)) == 0.0
    s = math.sin(math.radians(45))
    # IPL stores the conjugate: q_ipl z = -sin(45) means world +90 degrees about Z
    assert api.world_rz((0, 0, -s, s)) == pytest.approx(90.0)
    assert api.world_rz((0, 0, s, s)) == pytest.approx(270.0)
    assert api.world_rz((s, 0, 0, s)) is None


def test_world_aabb_rotated():
    s = math.sin(math.radians(45))
    a = api.world_aabb((-1, -2, -3), (1, 2, 3), (10, 20, 30), (0, 0, -s, s))  # world +90 about Z
    assert a == pytest.approx((8, 19, 27, 12, 21, 33))
    b = api.world_aabb((0, 0, 0), (1, 1, 1), (0, 0, 0), (0, 0, 0, 1))
    assert b == pytest.approx((0, 0, 0, 1, 1, 1))


def test_rotate_matches_matrix():
    s = math.sin(math.radians(45))
    assert api.rotate((0, 0, s, s), (1, 0, 0)) == pytest.approx((0, 1, 0))


# ---------------------------------------------------------------- stdlib only


def test_no_heavy_imports_at_module_level():
    for f in SRC_INDEX.glob("*.py"):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in tree.body:
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for n in names:
                assert n.split(".")[0] not in ("PIL", "numpy", "mcp"), f"{f.name}: {n}"
