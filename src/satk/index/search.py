"""Step 6 of ``index build`` (``fts_name``) and the name-search query helpers. Owner: WP-03.

``fts_name(sid, kind, name)`` uses the FTS5 ``trigram`` tokenizer: any substring of >= 3
characters matches (case-insensitive, Unicode). Shorter queries fall back to a ``LIKE`` prefix.

Kinds: ``model dff txd tex col ipl zone ifp anim handling tcyc`` (``file`` is searched with ``LIKE`` on blob
names, see ``queries.find``). Ranking: exact name, then prefix, then substring; then kind order
(:data:`KIND_ORDER`), then name.

Stdlib only.
"""

from __future__ import annotations

import sqlite3
from typing import Iterable

__all__ = ["fill_fts", "match_expr", "KIND_ORDER", "MIN_TRIGRAM"]

KIND_ORDER = ("model", "dff", "txd", "tex", "col", "ipl", "zone", "ifp", "anim", "handling", "tcyc", "file")
MIN_TRIGRAM = 3


def fill_fts(conn: sqlite3.Connection, rows: Iterable[tuple[str, str, str]]) -> int:
    """Insert ``(sid, kind, name)`` rows and merge the FTS segments; returns the row count."""
    rows = list(rows)
    conn.executemany("INSERT INTO fts_name(sid, kind, name) VALUES (?,?,?)", rows)
    conn.execute("INSERT INTO fts_name(fts_name) VALUES ('optimize')")
    return len(rows)


def match_expr(q: str) -> str:
    """FTS5 phrase for ``q`` (double quotes escaped): substring match with the trigram tokenizer."""
    return '"' + q.replace('"', '""') + '"'
