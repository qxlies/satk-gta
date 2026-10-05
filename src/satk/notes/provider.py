"""SID providers backed by ``notes.sqlite`` (SPEC §3.2, §4.5).

* :class:`NoteProvider` (kind ``note``) is registered by ``satk.notes.ops``; ``asset_find(query,
  kind="note")``, ``asset_get("note:17")`` and ``asset_refs("note:17")`` go through it.
* :class:`BookmarkCaptureProvider` (kinds ``bm``, ``cap``) is **not** registered here: those kinds
  belong to ``satk.viewer`` (WP-07, ``ids.KIND_OWNER``). The viewer package can serve them with
  ``register_provider(BookmarkCaptureProvider())`` from its ``ops`` module.
"""

from __future__ import annotations

from satk.core.envelope import clamp_limit, obj, table
from satk.core.errors import SatkError
from satk.core.ids import Sid

from . import db

__all__ = ["NoteProvider", "BookmarkCaptureProvider", "parse_cursor", "next_cursor", "INFO_CHARS"]

#: Length of the one-line ``info`` cell in ``asset_find`` rows.
INFO_CHARS = 80


def parse_cursor(cursor: str | None) -> int:
    """Offset encoded in a cursor (``None`` -> 0); ``BAD_PARAMS`` if malformed."""
    if cursor in (None, ""):
        return 0
    s = str(cursor).strip()
    if not s.isdigit():
        raise SatkError("BAD_PARAMS", f"bad cursor {cursor!r}", hint="pass the 'next' value of the previous page")
    return int(s)


def next_cursor(offset: int, n: int, total: int) -> str | None:
    """Cursor of the next page or ``None`` on the last one."""
    return str(offset + n) if offset + n < total else None


def _one_line(text: str, width: int = INFO_CHARS) -> str:
    t = " ".join(text.split())
    return t if len(t) <= width else t[: width - 1] + "…"


class NoteProvider:
    """Kind ``note``: notes are found by full text (Cyrillic prefixes work) or by the SID they annotate."""

    kinds = ("note",)

    def get(self, sid: Sid, fields: list[str] | None, profile: str) -> dict:
        n = db.get_note(sid.key)
        if n is None:
            raise SatkError("NOT_FOUND", f"no note {sid}", hint="satk note list")
        d = n.as_dict()
        d.pop("id", None)
        env = obj(n.note_sid, **d, links={"target": n.sid})
        if fields:
            from satk.core.envelope import select_fields

            env = select_fields(env, fields)
        return env

    def find(self, q: str, kind: str | None, limit: int, cursor: str | None, profile: str) -> dict:
        lim = clamp_limit(limit)
        off = parse_cursor(cursor)
        notes, total = db.list_notes(q=q, limit=lim, offset=off)
        rows = [[n.note_sid, "note", n.sid, _one_line(n.text)] for n in notes]
        return table(["id", "kind", "name", "info"], rows, total=total, next=next_cursor(off, len(rows), total))

    def refs(self, sid: Sid, rel: str | None, limit: int, cursor: str | None, profile: str) -> dict:
        n = db.get_note(sid.key)
        if n is None:
            raise SatkError("NOT_FOUND", f"no note {sid}", hint="satk note list")
        if rel is None:
            return obj(str(sid), rels={"target": 1})
        if rel != "target":
            raise SatkError("BAD_PARAMS", f"note has no relation {rel!r}", did_you_mean=["target"])
        return table(["id", "rel"], [[n.sid, "target"]])


class BookmarkCaptureProvider:
    """Kinds ``bm`` and ``cap`` from ``notes.sqlite`` (for ``satk.viewer`` to register)."""

    kinds = ("bm", "cap")

    def get(self, sid: Sid, fields: list[str] | None, profile: str) -> dict:
        if sid.kind == "bm":
            row = db.bookmark_get(sid.key)
        else:
            row = db.capture_get(str(sid))
        if row is None:
            raise SatkError("NOT_FOUND", f"no {sid}", hint="satk view bookmark list" if sid.kind == "bm" else None)
        row = dict(row)
        rid = row.pop("id")
        env = obj(rid, **row)
        if fields:
            from satk.core.envelope import select_fields

            env = select_fields(env, fields)
        return env

    def find(self, q: str, kind: str | None, limit: int, cursor: str | None, profile: str) -> dict:
        lim = clamp_limit(limit)
        off = parse_cursor(cursor)
        rows: list[list] = []
        total = 0
        if kind in (None, "bm"):
            bms, total = db.bookmark_list(q, limit=lim, offset=off)
            rows += [[b["id"], "bm", b["name"], _one_line(b.get("note") or "")] for b in bms]
        if kind == "cap":
            caps, total = db.capture_list(limit=lim, offset=off)
            caps = [c for c in caps if not q or q.lower() in c["id"]]
            rows += [[c["id"], "cap", c["target"], f"{c.get('w', '?')}x{c.get('h', '?')} {c['created_at']}"]
                     for c in caps]
        return table(["id", "kind", "name", "info"], rows, total=total, next=next_cursor(off, len(rows), total))

    def refs(self, sid: Sid, rel: str | None, limit: int, cursor: str | None, profile: str) -> dict:
        if self.get(sid, None, profile) and rel is None:
            return obj(str(sid), rels={})
        raise SatkError("BAD_PARAMS", f"{sid.kind} has no relation {rel!r}")
