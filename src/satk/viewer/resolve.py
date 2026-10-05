"""Runtime entity -> stable SID (SPEC §4.8.5) and index access for the viewer.

``link_entity(entity)`` turns a SAAP ``EntityRef`` into ``{"id", "link", "candidates"?}``:

1. ``subject`` given by the endpoint -> that SID, ``exact``;
2. ``src.ipl_bin`` -> ``inst:<ipl>#<idx>``; ``src.ipl_text`` -> ``inst:<file stem>#<idx>``, ``exact``;
3. ``src.runtime`` (MTA element) -> ``el:<type>/<id>``, ``exact``;
4. otherwise ``IndexDB.match_runtime(model_id, pos, tol=0.05)``: one -> ``nearest``,
   several -> ``ambiguous`` (+ ``candidates``), none -> ``none``.

:class:`IndexAccess` uses ``satk.index.api.open_index(profile)`` (WP-03 Q1; honours
``SATK_INDEX_FAKE=1`` and ``override_index``). When the index is not built yet (``INDEX_MISSING``)
or a query method is still a Q1 stub (``IndexNotImplemented``), it falls back per call to
WP-03's ``satk.index.fake.FakeIndexDB`` and adds an ``INDEX_MISSING`` warning to the answer.
"""

from __future__ import annotations

import math
from pathlib import PurePosixPath
from typing import Any, Sequence

from ..core.errors import SatkError
from ..core.ids import Sid

__all__ = ["IndexAccess", "link_entity", "src_to_sid", "TOL"]

TOL = 0.05
_FALLBACK = (ImportError, AttributeError, NotImplementedError)


class IndexAccess:
    """Lazy index handle with a per-call fallback to the fake index."""

    def __init__(self, profile: str = "vanilla", *, db: Any = None):
        self.profile = profile
        self._db = db
        self._tried = db is not None
        self._fake: Any = None
        self.fake = bool(getattr(db, "fake", False)) or type(db).__name__ == "FakeIndexDB"
        self.warnings: list[str] = []

    def _real(self) -> Any | None:
        if not self._tried:
            self._tried = True
            try:
                from ..index.api import open_index

                self._db = open_index(self.profile)
                self.fake = type(self._db).__name__ == "FakeIndexDB"
            except _FALLBACK:
                self._db = None
                self._why = "satk.index is not available"
            except SatkError as e:
                if e.code not in ("INDEX_MISSING", "NOT_READY"):
                    raise
                self._db = None
                self._why = e.msg
        return self._db

    def _use_fake(self, why: str) -> Any:
        msg = f"INDEX_MISSING: {why}; SIDs were resolved with the built-in FakeIndexDB (3 models) - run `satk index build`"
        if msg not in self.warnings:
            self.warnings.append(msg)
        self.fake = True
        if self._fake is None:
            from ..index.fake import FakeIndexDB

            self._fake = FakeIndexDB(self.profile)
        return self._fake

    def _call(self, name: str, *a, **kw):
        db = self._real()
        if db is None:
            return getattr(self._use_fake(getattr(self, "_why", "no index")), name)(*a, **kw)
        try:
            return getattr(db, name)(*a, **kw)
        except NotImplementedError:  # Q1 stub (IndexNotImplemented)
            return getattr(self._use_fake(f"index API '{name}' is not implemented yet"), name)(*a, **kw)
        except SatkError as e:
            if e.code not in ("INDEX_MISSING", "NOT_READY"):
                raise
            return getattr(self._use_fake(e.msg), name)(*a, **kw)

    # -- API used by the viewer ------------------------------------------------------------------

    def match_runtime(self, model_id: int, pos: Sequence[float], tol: float = TOL) -> list[str]:
        return list(self._call("match_runtime", int(model_id), tuple(float(c) for c in pos), tol))

    def get(self, sid: str, fields: list[str] | None = None) -> dict:
        return self._call("get", sid, fields)

    def insts(self, **kw) -> list:
        return list(self._call("insts", **kw))

    def locate(self, sid: str) -> tuple[tuple[float, float, float], float, dict]:
        """(centre, bounding radius, object) of an ``inst:``/``model:`` SID for framing."""
        s = Sid.parse(sid)
        if s.kind == "model":
            m = self.get(str(s))
            mid = int(str(m.get("id", "model:0")).split(":")[1])
            rows = self.insts(model=mid, area=None, lod="all", limit=1)
            if not rows:
                raise SatkError("NOT_FOUND", f"{sid} has no placements to fly to",
                                hint=f"satk asset refs {sid} --rel inst")
            s = Sid.parse(rows[0].sid)
        if s.kind == "zone":
            return self._locate_zone(s)
        if s.kind != "inst":
            raise SatkError("UNSUPPORTED", f"cannot frame a {s.kind!r} SID (use inst:, model:, zone:, bm:, cap:)")
        o = self.get(str(s))
        pos = tuple(float(c) for c in o["pos"])
        r = 5.0
        aabb = o.get("aabb")
        if aabb and len(aabb) == 6:
            r = 0.5 * math.dist(aabb[:3], aabb[3:])
            pos = tuple((aabb[i] + aabb[i + 3]) / 2 for i in range(3))
        else:
            try:
                bs = (self.get(str(o["model"])).get("geo") or {}).get("bs")
                if bs and len(bs) >= 4:
                    r = float(bs[3])
            except SatkError:
                pass
        return pos, r, o  # type: ignore[return-value]

    #: Zones such as "Los Santos" span kilometres; frame the core of a zone (a third of its
    #: diagonal), at most this radius, so the view stays inside draw distance and out of the fog.
    ZONE_MAX_RADIUS = 250.0

    def _locate_zone(self, s: Sid) -> tuple[tuple[float, float, float], float, dict]:
        """Centre and radius of a zone. Zone boxes have no ground height (often 0..200 or
        -100..1500), so the height is the median of the placements inside the box."""
        o = self.get(str(s))
        mn, mx = o["min"], o["max"]
        cx, cy = (mn[0] + mx[0]) / 2, (mn[1] + mx[1]) / 2
        rows = self.insts(box=(mn[0], mn[1], mx[0], mx[1]), area=0, lod="hd", match="center", limit=200)
        zs = sorted(float(r.pos[2]) for r in rows)
        cz = zs[len(zs) // 2] if zs else float(mn[2]) + 10.0
        r = min(0.35 * math.hypot(mx[0] - mn[0], mx[1] - mn[1]), self.ZONE_MAX_RADIUS)
        return (cx, cy, cz), max(r, 20.0), o


def src_to_sid(src: dict | None) -> str | None:
    """Exact SID from an EntityRef ``src`` (``None`` for ``model_pos``/unknown)."""
    if not isinstance(src, dict):
        return None
    k = src.get("kind")
    try:
        if k == "ipl_bin" and src.get("ipl") and src.get("idx") is not None:
            return str(Sid("inst", f"{src['ipl']}#{int(src['idx'])}"))
        if k == "ipl_text" and src.get("file") and src.get("idx") is not None:
            stem = PurePosixPath(str(src["file"]).replace("\\", "/")).stem
            return str(Sid("inst", f"{stem}#{int(src['idx'])}"))
        if k == "runtime" and src.get("type") and src.get("id") is not None:
            return str(Sid("el", f"{src['type']}/{src['id']}"))
    except (SatkError, ValueError, TypeError):
        return None
    return None


def link_entity(entity: dict | None, idx: IndexAccess | None) -> dict:
    """``{"id": sid|None, "link": "exact"|"nearest"|"ambiguous"|"none", "candidates"?: [...]}``."""
    if not entity:
        return {"id": None, "link": "none"}
    subj = entity.get("subject")
    if isinstance(subj, str):
        try:
            return {"id": str(Sid.parse(subj)), "link": "exact"}
        except SatkError:
            pass
    sid = src_to_sid(entity.get("src"))
    if sid:
        return {"id": sid, "link": "exact"}
    mid, pos = entity.get("model_id"), entity.get("pos")
    if idx is None or mid is None or not pos:
        return {"id": None, "link": "none"}
    try:
        cands = idx.match_runtime(int(mid), pos, TOL)
    except SatkError:
        return {"id": None, "link": "none"}
    if len(cands) == 1:
        return {"id": cands[0], "link": "nearest"}
    if len(cands) > 1:
        return {"id": cands[0], "link": "ambiguous", "candidates": cands[:10]}
    return {"id": None, "link": "none"}
