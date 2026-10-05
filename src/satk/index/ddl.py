"""Index schema helpers (SPEC §4.3.3). Owner: WP-03.

The DDL itself lives next to this module in ``schema.sql`` (copied verbatim from SPEC §4.3.3,
verified by V12). One SQLite file per load profile: ``work/index/<profile>.sqlite``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

__all__ = ["SCHEMA_VERSION", "SCHEMA_PATH", "schema_sql", "create_schema", "TABLES", "VIEWS", "VIRTUAL_TABLES"]

#: ``PRAGMA user_version`` of an index built with this schema.
SCHEMA_VERSION = 3  # v2: zone.title (in-game zone names); v3: water/timecyc/handling/carcols/ped.dat/object.dat

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

#: Regular tables of the schema (tests check that the DDL creates exactly these).
TABLES: tuple[str, ...] = (
    "meta", "layer", "source", "blob",
    "txd", "image", "texture",
    "dff", "dff_frame", "dff_geom", "dff_mat", "fx2d",
    "col",
    "ide", "model", "model_link", "model_tex",
    "ipl", "inst", "ipl_item", "zone",
    "ifp", "anim",
    "water_quad", "timecyc", "handling", "carcol", "car_color", "ped_rel", "object_data",  # v3
)
#: Virtual tables (R-tree and FTS5).
VIRTUAL_TABLES: tuple[str, ...] = ("inst_rtree", "item_rtree", "water_rtree", "fts_name")
#: SID views that ``index_query`` users should read first.
VIEWS: tuple[str, ...] = ("v_model", "v_inst", "v_tex", "v_file", "v_vehicle", "v_ped")

_cache: str | None = None


def schema_sql() -> str:
    """Text of ``schema.sql`` (UTF-8)."""
    global _cache
    if _cache is None:
        _cache = SCHEMA_PATH.read_text(encoding="utf-8")
    return _cache


def create_schema(conn: sqlite3.Connection) -> None:
    """Create all tables, indexes, virtual tables and views of the schema in ``conn``.

    Sets ``PRAGMA user_version`` = :data:`SCHEMA_VERSION`. The connection must point at an empty database.
    """
    conn.executescript(schema_sql())
    v = conn.execute("PRAGMA user_version").fetchone()[0]
    if v != SCHEMA_VERSION:  # pragma: no cover - schema.sql and SCHEMA_VERSION out of sync
        raise RuntimeError(f"schema.sql sets user_version={v}, expected {SCHEMA_VERSION}")
