"""Summaries of the SQLite schemas shipped in ``src/satk/<pkg>/schema.sql`` (help ``schema``, gen-docs).

The DDL is executed into an in-memory database and read back from ``sqlite_master`` and
``PRAGMA table_info``, so the summary is exactly what SQLite sees (FTS5/R-tree shadow tables
are hidden). Packages appear automatically when their ``schema.sql`` is merged.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from satk.core.config import SRC_ROOT

__all__ = ["DB_FILES", "SchemaTable", "SchemaInfo", "schema_files", "read_schema"]

#: package -> database file it describes (for the reader).
DB_FILES = {"index": "work/index/<profile>.sqlite", "re": "work/re/symdb.sqlite", "notes": "work/notes.sqlite"}
_ORDER = ("index", "re", "notes")
_SHADOW = re.compile(r"_(data|idx|content|docsize|config|node|parent|rowid)$")


@dataclass
class SchemaTable:
    name: str
    kind: str  # table | view | fts5 | rtree | virtual
    columns: list[tuple[str, str, bool]] = field(default_factory=list)  # (name, type, pk)


@dataclass
class SchemaInfo:
    pkg: str
    path: Path
    db: str
    user_version: int
    tables: list[SchemaTable]
    error: str | None = None


def schema_files() -> list[tuple[str, Path]]:
    """``[(pkg, path)]`` of every ``src/satk/<pkg>/schema.sql`` in this checkout (known order first)."""
    root = SRC_ROOT / "satk"
    found = {p.parent.name: p for p in sorted(root.glob("*/schema.sql"))}
    names = sorted(found, key=lambda n: (_ORDER.index(n) if n in _ORDER else len(_ORDER), n))
    return [(n, found[n]) for n in names]


def _virtual_kind(sql: str) -> str:
    m = re.search(r"USING\s+(\w+)", sql or "", re.I)
    if not m:
        return "virtual"
    mod = m.group(1).lower()
    return mod if mod in ("fts5", "rtree", "fts4") else "virtual"


def read_schema(pkg: str, path: Path) -> SchemaInfo:
    """Execute ``path`` in memory and describe its tables."""
    db = DB_FILES.get(pkg, f"({pkg})")
    sql = path.read_text(encoding="utf-8")
    con = sqlite3.connect(":memory:")
    try:
        try:
            con.executescript(sql)
        except sqlite3.Error as e:
            names = re.findall(r"CREATE\s+(?:VIRTUAL\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)", sql, re.I)
            return SchemaInfo(pkg, path, db, 0, [SchemaTable(n, "table") for n in names], error=str(e))
        uv = int(con.execute("PRAGMA user_version").fetchone()[0])
        rows = con.execute("SELECT type, name, sql FROM sqlite_master WHERE type IN ('table','view') "
                           "AND name NOT LIKE 'sqlite_%' ORDER BY rowid").fetchall()
        virtual = {n for t, n, s in rows if s and s.upper().startswith("CREATE VIRTUAL")}
        tables: list[SchemaTable] = []
        for typ, name, s in rows:
            base = _SHADOW.sub("", name)
            if name not in virtual and base != name and base in virtual:
                continue  # FTS5 / R-tree shadow table
            kind = _virtual_kind(s) if name in virtual else typ
            cols = [(r[1], r[2] or "", bool(r[5])) for r in con.execute(f'PRAGMA table_info("{name}")')]
            tables.append(SchemaTable(name, kind, cols))
        return SchemaInfo(pkg, path, db, uv, tables)
    finally:
        con.close()
