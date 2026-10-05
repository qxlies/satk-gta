"""Bookmarks (camera poses by name) for ``view_bookmark`` and the ``bm:`` SID provider.

SPEC §4.5 puts bookmarks into ``work/notes.sqlite`` (table ``bookmark``, schema owned by
WP-06). This module uses that table when it exists and otherwise a JSON fallback
``work/run/bookmarks.json``; entries from the fallback are copied into the table as soon as
it appears. WP-07 never creates or alters tables of ``notes.sqlite``.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import sqlite3
from pathlib import Path

from ..core import paths
from ..core.errors import SatkError

__all__ = ["save", "get", "remove", "list_all", "backend_name", "NAME_RE"]

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.\-]{0,63}$")


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _check(name: str) -> str:
    n = str(name or "").strip().lower()
    if n.startswith("bm:"):
        n = n[3:]
    if not NAME_RE.match(n):
        raise SatkError("BAD_PARAMS", f"bad bookmark name {name!r} (a-z 0-9 _ . -, up to 64 chars)")
    return n


def _json_path() -> Path:
    return paths.work("run", "bookmarks.json")


def _notes_db() -> sqlite3.Connection | None:
    p = Path(paths.cfg().paths.work) / "notes.sqlite"
    if not p.is_file():
        return None
    try:
        con = sqlite3.connect(str(p), timeout=5)
        ok = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='bookmark'").fetchone()
        if not ok:
            con.close()
            return None
        return con
    except sqlite3.Error:
        return None


def _read_json() -> dict[str, dict]:
    try:
        d = json.loads(_json_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(d: dict[str, dict]) -> None:
    paths.atomic_write(_json_path(), json.dumps(d, ensure_ascii=False, indent=1, sort_keys=True))


def _migrate(con: sqlite3.Connection) -> None:
    d = _read_json()
    if not d:
        return
    with con:
        for n, b in d.items():
            con.execute("INSERT OR IGNORE INTO bookmark(name, pose, env, note, created_at) VALUES (?,?,?,?,?)",
                        (n, json.dumps(b["pose"]), json.dumps(b.get("env")) if b.get("env") else None,
                         b.get("note"), b.get("created_at") or _now()))
    _write_json({})


def backend_name() -> str:
    con = _notes_db()
    if con is None:
        return "json"
    con.close()
    return "notes.sqlite"


def save(name: str, pose: dict, env: dict | None = None, note: str | None = None) -> dict:
    n = _check(name)
    rec = {"name": n, "pose": pose, "env": env, "note": note, "created_at": _now()}
    con = _notes_db()
    if con is not None:
        try:
            _migrate(con)
            with con:
                con.execute("INSERT OR REPLACE INTO bookmark(name, pose, env, note, created_at) VALUES (?,?,?,?,?)",
                            (n, json.dumps(pose), json.dumps(env) if env else None, note, rec["created_at"]))
        finally:
            con.close()
        return rec
    d = _read_json()
    d[n] = {k: v for k, v in rec.items() if k != "name"}
    _write_json(d)
    return rec


def list_all() -> list[dict]:
    con = _notes_db()
    if con is not None:
        try:
            _migrate(con)
            rows = con.execute("SELECT name, pose, env, note, created_at FROM bookmark ORDER BY name").fetchall()
        finally:
            con.close()
        return [{"name": r[0], "pose": json.loads(r[1]), "env": json.loads(r[2]) if r[2] else None, "note": r[3],
                 "created_at": r[4]} for r in rows]
    return [{"name": n, **b} for n, b in sorted(_read_json().items())]


def get(name: str) -> dict:
    n = _check(name)
    for b in list_all():
        if b["name"] == n:
            return b
    import difflib

    names = [b["name"] for b in list_all()]
    raise SatkError("NOT_FOUND", f"no bookmark {n!r}", did_you_mean=[f"bm:{x}" for x in difflib.get_close_matches(n, names, 3, 0.5)],
                    hint="satk view bookmark list")


def remove(name: str) -> bool:
    n = _check(name)
    con = _notes_db()
    if con is not None:
        try:
            _migrate(con)
            with con:
                cur = con.execute("DELETE FROM bookmark WHERE name = ?", (n,))
            return cur.rowcount > 0
        finally:
            con.close()
    d = _read_json()
    if n in d:
        del d[n]
        _write_json(d)
        return True
    return False
