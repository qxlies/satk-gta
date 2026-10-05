"""SID -> model files: the index API and the stage-Q1 SQL fallback give the same answer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from satk.core.errors import SatkError
from satk.index.api import IndexDB, clear_cache
from satk.model3d import resolve as R


@pytest.fixture
def sql_db(fake_db, satk_home):
    """The fake index written as a real schema-v1 file at ``work/index/vanilla.sqlite`` (Q1 ``IndexDB``)."""
    p = fake_db.to_sqlite(satk_home / "work" / "index" / "vanilla.sqlite")
    db = IndexDB("vanilla", p)
    yield db
    db.close()
    clear_cache()


def _norm(mf):
    def b(ref):
        return None if ref is None else (ref.sid, str(ref.path).lower(), ref.offset, ref.size, ref.name.lower())
    col = None if mf.col is None else (mf.col.sid, b(mf.col.blob), mf.col.idx, mf.col.name.lower(), mf.col.via)
    return (mf.model_id, mf.name.lower(), mf.sec, b(mf.dff), [b(x) for x in mf.txd_chain], col)


@pytest.mark.parametrize("model", [411, 17613, 300, "infernus", "lae2_roads89"])
def test_sql_fallback_matches_index_api(fake_db, sql_db, model):
    assert _norm(R._sql_model_files(sql_db, model)) == _norm(fake_db.model_files(model))
    assert _norm(R.model_files(sql_db, model)) == _norm(fake_db.model_files(model))   # API or fallback


def test_sql_fallback_list_and_blob(fake_db, sql_db):
    assert R.list_models(sql_db) == R.list_models(fake_db)
    assert [m[0] for m in R.list_models(sql_db)] == [411, 17613, 17858]   # by TXD: infernus, lae2roadshub, laeast2_lod
    ref = R.blob_ref(sql_db, "dff:infernus")
    assert ref == fake_db.blob_ref("dff:infernus")
    with pytest.raises(SatkError) as e:
        R.blob_ref(sql_db, "dff:nothing_here")
    assert e.value.code == "NOT_FOUND"
    with pytest.raises(SatkError) as e:
        R.model_files(sql_db, "no_such_model_zz")
    assert e.value.code == "NOT_FOUND"


def test_resolve_kinds(fake_db):
    assert R.resolve("model:411").name == "infernus"
    assert R.resolve("411").sid == "model:411"
    assert R.resolve("dff:infernus").sid == "model:411"
    s = R.resolve("inst:lae2_stream0#4")
    assert s.sid == "model:17613" and s.notes
    for bad, code in (("tex:bistro/vent_64", "BAD_ID"), ("inst:lae2_stream0#x", "BAD_ID"),
                      ("inst:lae2_stream0#999", "NOT_FOUND"), ("", "BAD_PARAMS")):
        with pytest.raises(SatkError) as e:
            R.resolve(bad)
        assert e.value.code == code, bad


@pytest.mark.slow
def test_thumbnails_worker_processes(sql_db, run_cli):
    """``--jobs 2``: spawned workers open the index file themselves (SQL fallback) and render."""
    r = run_cli(["model", "image", "--all", "--size", "24", "--views", "1", "--jobs", "2", "--json"])
    assert r.code == 0, r.out
    j = r.json
    assert j["jobs"] == 2 and j["rendered"] == 3 and j["written"] == 2 and j["failed"] == 1
    man = json.loads(Path(j["manifest"]).read_text(encoding="utf-8"))
    assert all(Path(p).is_file() for p in man["thumbs"].values())
