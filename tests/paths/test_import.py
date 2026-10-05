"""``satk paths import`` and the path database on a synthetic game root (M2-05)."""

from __future__ import annotations

import os
import sqlite3

import pytest

from satk.core.errors import SatkError
from satk.core.registry import invoke
from satk.paths import db as D


def test_import_counts_and_sources(game):
    env = invoke("paths.import", {})
    assert env["ok"], env
    assert (env["areas"], env["nodes"], env["vehicle"], env["car"], env["boat"], env["ped"]) == (2, 8, 6, 5, 1, 2)
    assert (env["navis"], env["links"]) == (4, 10)
    assert env["sources"] == {"models/gta3.img": 2}
    assert env["check"] == "valid: 0 errors, 0 warnings"
    assert env["reused"] is False
    assert env["db"].endswith("/work/index/paths-vanilla.sqlite")
    assert any(w.startswith("MISSING: no nodes*.dat for region(s) 2, 3,") for w in env["warn"])


def test_import_rows(game, pn):
    invoke("paths.import", {})
    conn = sqlite3.connect(D.db_path("vanilla"))
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == D.SCHEMA_VERSION
        rows = conn.execute("SELECT area, idx, kind, x, y, z, degree, width, flood, spawn, behaviour FROM node "
                            "ORDER BY area, idx").fetchall()
        assert rows[2] == (0, 2, "car", -2260.0, -2900.0, 10.5, 2, 0.0, 1, 7, 2)
        assert rows[3][2] == "boat" and rows[4][2] == "ped" and rows[1][7] == 1.0
        assert conn.execute("SELECT * FROM navi WHERE area = 1 AND idx = 0").fetchone() == (
            1, 0, -2250.0, -2900.0, 0, 2, -1.0, 0.0, 0.0, 0, 2, 0, 1, 0)
        links = conn.execute("SELECT node, to_area, to_idx, navi_area, navi_idx, dist, inter FROM link "
                             "WHERE area = 0 ORDER BY k").fetchall()
        assert links[3] == (2, 0, 1, 0, 1, 20, 1)
        assert links[5] == (4, 0, 5, None, None, 10, 0)  # pedestrian links carry no navi
        data = dict(conn.execute("SELECT area, data FROM area"))
        assert data == pn.network()
    finally:
        conn.close()


def test_reimport_is_reused_until_the_archive_changes(game):
    first = invoke("paths.import", {})
    db = D.db_path("vanilla")
    mtime = db.stat().st_mtime_ns
    again = invoke("paths.import", {})
    assert again["reused"] is True and again["nodes"] == first["nodes"]
    assert db.stat().st_mtime_ns == mtime
    forced = invoke("paths.import", {"force": True})
    assert forced["reused"] is False
    img = game / "models" / "gta3.img"
    st = img.stat()
    os.utime(img, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    near = invoke("paths.near", {"x": -2280, "y": -2900})
    assert any(w.startswith("INDEX_STALE:") for w in near["warn"])
    assert invoke("paths.import", {})["reused"] is False
    assert "warn" not in invoke("paths.near", {"x": -2280, "y": -2900})


def test_first_registered_archive_wins(satk_home, pn):
    from satk.core import config as _config

    net = pn.network()
    other = pn.region_bytes(1, nodes=[(-2240, -2900, 99, "car", 1, 0, 0, 15, 0)], links={}, navis=[])
    extra_only = pn.region_bytes(5, nodes=[(-1000, -2900, 1, "ped", 3, 0, 0, 15, 0)], links={}, navis=[])
    pn.make_root(satk_home / "gta-sa-clean", regions=net, extra={1: other, 5: extra_only})
    _config.reset()
    env = invoke("paths.import", {})
    assert env["sources"] == {"models/extra.img": 1, "models/gta3.img": 2}
    with D.PathsDB(D.db_path("vanilla")) as db:
        assert db.node(1, 0)["z"] == 10.0  # gta3.img (registered by the exe) beats extra.img (gta.dat)
        assert db.node(5, 0)["kind"] == "ped"


def test_corrupt_region_is_unsupported(satk_home, pn):
    from satk.core import config as _config

    bad = bytearray(pn.region_bytes(0))
    bad[0:4] = (999).to_bytes(4, "little")  # node count no longer matches
    pn.make_root(satk_home / "gta-sa-clean", regions={0: bytes(bad)})
    _config.reset()
    env = invoke("paths.import", {})
    assert env["ok"] is False and env["error"]["code"] == "UNSUPPORTED"
    assert "nodes0.dat" in env["error"]["msg"]
    assert not D.db_path("vanilla").exists()


def test_region_claiming_another_area_is_unsupported(satk_home, pn):
    from satk.core import config as _config

    pn.make_root(satk_home / "gta-sa-clean", regions={7: pn.region_bytes(1)})
    _config.reset()
    env = invoke("paths.import", {})
    assert env["error"]["code"] == "UNSUPPORTED" and "claims region 1" in env["error"]["msg"]


def test_no_regions_and_bad_profiles(satk_home, pn):
    from satk.core import config as _config

    pn.make_root(satk_home / "gta-sa-clean", regions={})
    _config.reset()
    assert invoke("paths.import", {})["error"]["code"] == "NOT_FOUND"
    assert invoke("paths.import", {"profile": "nosuch"})["error"]["code"] == "BAD_PARAMS"
    assert invoke("paths.import", {"profile": "../x"})["error"]["code"] == "BAD_PARAMS"


def test_missing_database_without_auto_import(game):
    with pytest.raises(SatkError) as ei:
        D.open_paths("vanilla", auto_import=False)
    assert ei.value.code == "INDEX_MISSING" and "satk paths import" in ei.value.hint


def test_game_files_are_only_read(game):
    before = {p: p.stat().st_mtime_ns for p in game.rglob("*") if p.is_file()}
    invoke("paths.import", {})
    invoke("paths.compile", {"write": "all"})
    assert {p: p.stat().st_mtime_ns for p in game.rglob("*") if p.is_file()} == before
