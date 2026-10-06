"""Fixtures for the ``satk.shader`` tests (no game files).

``index``: the fake index of :mod:`satk.index.fake` written as a real SQLite file plus a few synthetic rows (a ped,
a tree with the IDE tree flag, a road sign, a recolourable paint material, ``waterclear256`` in a loose
``particle.txd``), opened as :class:`satk.index.api.IndexDB` and served by ``open_index`` for every profile.
``fxc``: the local ``fxc.exe`` or a skip.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest


def _add_txd(con: sqlite3.Connection, name: str, textures: list[tuple[str, int]], *, ns: str = "main") -> int:
    src = con.execute("SELECT max(id) FROM source").fetchone()[0] + 1
    con.execute("INSERT INTO source(id, layer_id, relpath, kind, size, mtime_ns) VALUES (?, 1, ?, 'txd', 1, 0)",
                (src, f"synthetic/{name}.txd"))
    bid = con.execute("SELECT max(id) FROM blob").fetchone()[0] + 1
    con.execute("INSERT INTO blob(id, source_id, idx, name, stem, ext, abs_off, size, ns, active) "
                "VALUES (?, ?, 0, ?, ?, 'txd', 0, 1, ?, 1)", (bid, src, f"{name}.txd", name, ns))
    tid = con.execute("SELECT max(id) FROM txd").fetchone()[0] + 1
    con.execute("INSERT INTO txd(id, blob_id, name, rw_version, tex_count) VALUES (?, ?, ?, 0x36003, ?)",
                (tid, bid, name, len(textures)))
    for i, (tex, alpha) in enumerate(textures):
        con.execute("INSERT INTO texture(txd_id, idx, name, platform, raster_fmt, d3dfmt, w, h, levels, alpha, "
                    "data_off, data_size, hash) VALUES (?, ?, ?, 9, 0, 'DXT1', 64, 64, 1, ?, 0, 1, ?)",
                    (tid, i, tex, alpha, bytes([i, tid])))
    return tid


def _add_model(con: sqlite3.Connection, mid: int, name: str, sec: str, txd: str, textures: list[str], *,
               flags: int = 0, n_inst: int = 0) -> None:
    rid = con.execute("SELECT max(rid) FROM model").fetchone()[0] + 1
    con.execute("INSERT INTO model(rid, id, layer_id, name, txd, sec, ide_id, line, flags, active) "
                "VALUES (?, ?, 1, ?, ?, ?, 1, 1, ?, 1)", (rid, mid, name, txd, sec, flags))
    con.execute("INSERT INTO model_link(id, n_inst, tex_total) VALUES (?, ?, ?)", (mid, n_inst, len(textures)))
    for t in textures:
        con.execute("INSERT INTO model_tex(model_id, texture, via, uses) VALUES (?, ?, 'own', 1)", (mid, t))


def build_index(path: Path) -> Path:
    from satk.index.fake import FakeIndexDB

    FakeIndexDB().to_sqlite(path)
    con = sqlite3.connect(path)
    try:
        _add_txd(con, "pedtest", [("bmyst_body", 0), ("bmyst_head", 0)])
        _add_model(con, 7, "bmyst", "peds", "pedtest", ["bmyst_body", "bmyst_head"])
        _add_txd(con, "vegtest", [("oakleaf1", 1), ("oakbark64", 0)])
        _add_model(con, 700, "sm_veg_tree1", "objs", "vegtest", ["oakleaf1", "oakbark64"], flags=8192, n_inst=3)
        _add_txd(con, "signs", [("roadsign01_128", 0), ("glass_64", 1), ("ab_window", 1)])
        _add_model(con, 1233, "roadsign", "objs", "signs", ["roadsign01_128", "glass_64", "ab_window"], n_inst=2)
        _add_model(con, 1234, "roadsign2", "objs", "signs", ["roadsign01_128"], n_inst=5)
        _add_model(con, 1235, "brokenref", "objs", "signs", ["roadsign01_128", "no_such_texture"])
        _add_txd(con, "particle", [("waterclear256", 1), ("waterwake", 1), ("cloud1", 1)], ns="loose")
        # infernus body: a recolourable paint material (colour slot 1) on vehiclegrunge256
        con.execute("UPDATE dff_mat SET color_slot = 1 WHERE texture = 'vehiclegrunge256'")
        con.commit()
    finally:
        con.close()
    return path


@pytest.fixture
def index(tmp_path: Path, satk_home: Path):
    """``IndexDB`` over the synthetic index, served by ``open_index`` for every profile."""
    from satk.index import api
    from satk.index.api import IndexDB, override_index

    p = build_index(tmp_path / "idx" / "vanilla.sqlite")
    db = IndexDB("vanilla", path=p)
    try:
        with override_index(db):
            yield db
    finally:
        db.close()
        api.clear_cache()


@pytest.fixture(scope="session")
def fxc() -> Path:
    from satk.shader.check import find_fxc

    hit = find_fxc()
    if hit.path is None:
        reason = "fxc.exe not found (Windows SDK)"
        if os.environ.get("SATK_TEST_NO_SKIP") == "1":
            pytest.fail(reason, pytrace=False)
        pytest.skip(reason)
    return hit.path


@pytest.fixture
def write(tmp_path: Path):
    """``write(rel, text)`` -> path under ``tmp_path/res``."""
    root = tmp_path / "res"

    def w(rel: str, text: str) -> Path:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    return w
