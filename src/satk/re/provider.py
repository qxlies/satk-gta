"""SID provider for ``fn``/``g``/``vt``/``patch`` (SPEC §3.2; owner WP-09).

Serves ``asset_get``/``asset_find``/``asset_refs`` (WP-03) for symbol-DB kinds:

* ``fn:0x53bf09`` (any address inside a function) / ``fn:cped::update`` (name, case-insensitive);
* ``g:0xc8d4c0`` (address inside a global) / ``g:ggamestate``;
* ``vt:0x86c538`` / ``vt:cpedattractor``;
* ``patch:<origin>/<symbol>`` (``patch:neon/hookpos_cstreaming_update_caller``).
"""

from __future__ import annotations

from ..core.envelope import clamp_limit, obj, select_fields, table
from ..core.errors import SatkError
from ..core.ids import Sid
from . import api
from .db import open_db

__all__ = ["ReProvider"]


def _offset(cursor: str | None) -> int:
    if not cursor:
        return 0
    if cursor.startswith("o:") and cursor[2:].isdigit():
        return int(cursor[2:])
    raise SatkError("BAD_PARAMS", f"bad cursor {cursor!r}")


class ReProvider:
    """Registered from ``satk.re.ops``; profile is ignored (one exe for all profiles)."""

    kinds = ("fn", "g", "vt", "patch")

    def get(self, sid: Sid, fields: list[str] | None, profile: str) -> dict:
        db = open_db()
        if sid.kind == "fn":
            start, _f, _o = api.resolve_fn(db, sid.key)
            d = api.addr_detail(db, sid.addr if sid.addr is not None else start)
            ident = d.pop("id", None)
            env = obj(ident, **d)
        elif sid.kind == "g":
            env = api.global_detail(db, sid.key)
        elif sid.kind == "vt":
            env = api.vtable_detail(db, sid.key)
        elif sid.kind == "patch":
            env = api.patch_detail(db, sid.key)
        else:  # pragma: no cover
            raise SatkError("BAD_ID", f"kind {sid.kind!r} is not served by satk.re")
        return select_fields(env, fields)

    def find(self, q: str, kind: str | None, limit: int, cursor: str | None, profile: str) -> dict:
        db = open_db()
        k = {"fn": "func", "g": "global", "vt": "vtable", "patch": "patch"}.get(kind or "", None)
        if kind and k is None:
            return table(["id", "kind", "name", "info"], [])
        return api.find(db, q, k, clamp_limit(limit, default=20), _offset(cursor))

    def refs(self, sid: Sid, rel: str | None, limit: int, cursor: str | None, profile: str) -> dict:
        db = open_db()
        lim = clamp_limit(limit, default=50)
        off = _offset(cursor)
        if sid.kind == "fn":
            start, _f, _o = api.resolve_fn(db, sid.key)
            return api.fn_refs(db, start, rel, lim, off)
        if sid.kind == "g":
            env = api.global_detail(db, sid.key)
            pb = env.get("patches") or {"cols": api.PATCH_COLS, "rows": [], "total": 0}
            if rel is None:
                return {"ok": True, "id": env["id"], "rels": {"patches": pb["total"]}}
            if rel != "patches":
                raise SatkError("BAD_PARAMS", f"unknown rel {rel!r} for g:", did_you_mean=["patches"])
            return table(pb["cols"], pb["rows"][off:off + lim], total=pb["total"])
        if sid.kind == "vt":
            env = api.vtable_detail(db, sid.key, slot_limit=4096)
            t = env["table"]
            if rel is None:
                return {"ok": True, "id": env["id"], "rels": {"slots": len(t["rows"])}}
            if rel != "slots":
                raise SatkError("BAD_PARAMS", f"unknown rel {rel!r} for vt:", did_you_mean=["slots"])
            rows = t["rows"][off:off + lim]
            return table(t["cols"], rows, total=len(t["rows"]),
                         next=f"o:{off + lim}" if len(t["rows"]) > off + lim else None)
        if sid.kind == "patch":
            env = api.patch_detail(db, sid.key)
            s = env["sites"]
            if rel is None:
                return {"ok": True, "id": env["id"], "rels": {"sites": s["total"]}}
            return table(s["cols"], s["rows"][off:off + lim], total=s["total"])
        raise SatkError("BAD_ID", f"kind {sid.kind!r} is not served by satk.re")  # pragma: no cover
