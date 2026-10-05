"""Schema of the index (SPEC §4.3.3, v3): compiles, has every table/view, FTS5 + R-tree work, v3 data views."""

from __future__ import annotations

import re
import sqlite3

import pytest

from satk.index import ddl


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    ddl.create_schema(c)
    yield c
    c.close()


def _names(c: sqlite3.Connection, type_: str) -> set[str]:
    return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = ?", (type_,))}


def test_user_version(conn):
    assert conn.execute("PRAGMA user_version").fetchone()[0] == ddl.SCHEMA_VERSION == 3


def test_tables_views_virtual(conn):
    tables = _names(conn, "table")
    for t in ddl.TABLES + ddl.VIRTUAL_TABLES:
        assert t in tables, t
    shadow = {t for t in tables if re.match(r"^(inst_rtree|item_rtree|water_rtree|fts_name)_", t)}
    assert tables - shadow == set(ddl.TABLES) | set(ddl.VIRTUAL_TABLES)
    assert _names(conn, "view") == set(ddl.VIEWS)


def test_indexes_present(conn):
    idx = _names(conn, "index")
    for name in ("blob_ns_stem", "blob_hash", "txd_name", "texture_name", "texture_hash", "dff_name",
                 "dff_mat_tex", "col_name", "model_active", "model_name", "model_tex_tid",
                 "inst_model", "inst_lod", "inst_pos", "zone_name", "object_data_name"):
        assert name in idx, name


def test_views_compile_on_empty_db(conn):
    for v in ddl.VIEWS:
        assert conn.execute(f"SELECT * FROM {v}").fetchall() == []


def test_fts_trigram_substring_and_cyrillic(conn):
    conn.executemany("INSERT INTO fts_name(sid, kind, name) VALUES (?,?,?)", [
        ("tex:a/ws_rooftarmac1", "tex", "ws_rooftarmac1"),
        ("model:1", "model", "grove_house"),
        ("note:1", "note", "стол у грув-стрит"),
    ])
    hit = lambda q: {r[0] for r in conn.execute("SELECT sid FROM fts_name WHERE fts_name MATCH ?", (q,))}
    assert hit('"tarmac"') == {"tex:a/ws_rooftarmac1"}
    assert hit('"ove_h"') == {"model:1"}
    assert hit('"сто"') == {"note:1"}


def test_rtree_query(conn):
    conn.execute("INSERT INTO inst_rtree VALUES (1, 0, 10, 0, 10, 0, 1)")
    conn.execute("INSERT INTO inst_rtree VALUES (2, 100, 110, 100, 110, 0, 1)")
    rows = conn.execute("SELECT id FROM inst_rtree WHERE minx <= 20 AND maxx >= 5 AND miny <= 20 AND maxy >= 5").fetchall()
    assert rows == [(1,)]


def test_model_active_unique(conn):
    conn.execute("INSERT INTO layer VALUES (1,'vanilla','vanilla',0,'x')")
    conn.execute("INSERT INTO source(id,layer_id,relpath,kind,size,mtime_ns) VALUES (1,1,'data/default.ide','ide',1,0)")
    conn.execute("INSERT INTO ide VALUES (1,1)")
    conn.execute("INSERT INTO model(rid,id,layer_id,name,sec,ide_id,line,active) VALUES (1,300,1,'a','objs',1,1,1)")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO model(rid,id,layer_id,name,sec,ide_id,line,active) VALUES (2,300,1,'b','objs',1,2,1)")


def test_check_constraints(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO layer VALUES (1,'x','bogus',0,'x')")


def test_v3_data_checks(conn):
    conn.execute("INSERT INTO layer VALUES (1,'vanilla','vanilla',0,'x')")
    conn.execute("INSERT INTO source(id,layer_id,relpath,kind,size,mtime_ns) VALUES (1,1,'data/water.dat','other',1,0)")
    with pytest.raises(sqlite3.IntegrityError):  # a water polygon has 3 or 4 vertices
        conn.execute("INSERT INTO water_quad(source_id,config,idx,line,nverts,minx,miny,minz,maxx,maxy,maxz,verts) "
                     "VALUES (1,0,0,1,5,0,0,0,1,1,0,'[]')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO handling(source_id,name,kind,line,data) VALUES (1,'X','tank',1,'{}')")
    conn.execute("INSERT INTO handling(source_id,name,kind,line,data) VALUES (1,'INFERNUS','car',1,'{}')")
    with pytest.raises(sqlite3.IntegrityError):  # one record per (id, kind), case-insensitive
        conn.execute("INSERT INTO handling(source_id,name,kind,line,data) VALUES (1,'infernus','car',2,'{}')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO ped_rel VALUES ('COP','loves','COP',1,1)")


def test_v3_views(conn):
    conn.execute("INSERT INTO layer VALUES (1,'vanilla','vanilla',0,'x')")
    conn.execute("INSERT INTO source(id,layer_id,relpath,kind,size,mtime_ns) VALUES (1,1,'data/vehicles.ide','ide',1,0)")
    conn.execute("INSERT INTO ide VALUES (1,1)")
    conn.execute("INSERT INTO model(rid,id,layer_id,name,sec,ide_id,line,extra,active) VALUES "
                 "(1,411,1,'infernus','cars',1,1,'{\"handling\":\"INFERNUS\",\"type\":\"car\"}',1), "
                 "(2,7,1,'male01','peds',1,2,'{\"pedtype\":\"CIVMALE\",\"voice1\":\"V1\"}',1)")
    conn.execute("INSERT INTO handling(source_id,name,kind,line,mass,data) VALUES (1,'Infernus','car',1,1400.0,'{}')")
    conn.execute("INSERT INTO car_color VALUES ('INFERNUS',0,1,2,NULL,NULL,1,1), ('infernus',1,2,1,NULL,NULL,1,1)")
    rows = conn.execute("SELECT sid, type, handling, mass, colors FROM v_vehicle").fetchall()
    assert rows == [("model:411", "car", "INFERNUS", 1400.0, 2)]
    assert conn.execute("SELECT sid, pedtype, voice1 FROM v_ped").fetchall() == [("model:7", "CIVMALE", "V1")]
