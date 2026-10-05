"""Plans for Blender jobs: which files to import and where to place them. Owner: WP-10. Stdlib only.

The runner calls this in the ``satk`` process before starting Blender (fast ``NOT_FOUND`` without a
5-second Blender start); the Blender side calls it itself when a request has no ``plan`` (GUI add-on).

Sources (``source=``):

* ``index`` - ``satk.index.api.open_index(profile)`` (WP-03): ``model_files`` and ``insts``;
* ``direct`` - :mod:`satk.blender.gamedata`: DAT/IDE/IPL/IMG parsed on the fly;
* ``auto`` (default) - the index when it is built and implemented, else ``direct`` with a warning.

Blobs are copied out of the IMG archives into a content-addressed cache
``work/blender/cache/blobs/<hh>/<sha16>/<name>`` (trimmed to the real RW size, file names as in
the archive because DragonFF names collections/images after them). A collision model that lives
in a COL archive is cut out as a one-model ``<name>.col`` (DragonFF would import the whole archive).

Plans are plain JSON (they travel inside ``request.json``). World rotation = conjugated IPL
quaternion (V9); ``q`` in a plan is already the world quaternion ``[x, y, z, w]``.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any

from ..core.errors import SatkError
from ..core.paths import ensure_writable, jpath, work
from ..formats.rw import rw_payload_size

__all__ = ["open_source", "plan_model", "plan_area", "cache_blob", "cache_col", "parse_model_spec",
           "carcols_for", "parse_carcols", "SLOT_COLORS", "zone_rect", "rect_of", "def_info", "fill_export_defs"]

#: Material colours that mark the paint slots 1-4 of a vehicle (VehicleModelInfo.cpp:805-812).
SLOT_COLORS = ((60, 255, 0), (255, 0, 175), (0, 255, 255), (255, 0, 255))


# --------------------------------------------------------------------------- sources


def def_info(sec: str | None, draw, flags, time_on=None, time_off=None, anim=None) -> dict:
    """IDE fields of a definition that an export must keep (``None`` values left out).

    ``{"sec", "draw", "flags"}`` plus ``"time": [on, off]`` for ``tobj`` and ``"anim"`` (IFP name)
    for ``anim`` definitions.
    """
    d: dict[str, Any] = {"sec": sec}
    if draw is not None:
        d["draw"] = float(draw)
    if flags is not None:
        d["flags"] = int(flags)
    if sec == "tobj" and time_on is not None and time_off is not None:
        d["time"] = [int(time_on), int(time_off)]
    if sec == "anim" and anim:
        d["anim"] = str(anim)
    return {k: v for k, v in d.items() if v is not None}


class _IndexSource:
    kind = "index"

    def __init__(self, db):
        self.db = db

    def model_files(self, spec: str):
        return self.db.model_files(spec)

    def insts(self, **kw):
        return self.db.insts(**kw)

    def read(self, ref) -> bytes:
        return self.db.read_blob(ref)

    def model_def(self, model_id: int) -> dict | None:
        env = self.db.query("SELECT sec, draw, flags, time_on, time_off, anim FROM model WHERE id = ? AND active = 1",
                            [int(model_id)], limit=1)
        rows = env.get("rows") or []
        return def_info(*rows[0]) if rows else None

    def inst_iflags(self, sid: str) -> int | None:
        try:
            v = self.db.get(sid, ["iflags"]).get("iflags")
        except SatkError:
            return None
        return int(v) if v is not None else None

    def zone(self, name: str) -> dict | None:
        try:
            return self.db.get(f"zone:{name.strip().lower()}")
        except SatkError as e:
            if e.code == "NOT_FOUND":
                return None
            raise


class _DirectSource:
    kind = "direct"

    def __init__(self, gd):
        self.gd = gd
        self._by_sid: dict[str, int] | None = None

    def model_files(self, spec: str):
        return self.gd.model_files(spec)

    def insts(self, **kw):
        return self.gd.insts(**kw)

    def read(self, ref) -> bytes:
        return self.gd.read(ref)

    def model_def(self, model_id: int) -> dict | None:
        d = self.gd.defs.get(int(model_id))
        return def_info(d.sec, d.draw, d.flags, d.time_on, d.time_off, d.anim) if d is not None else None

    def inst_iflags(self, sid: str) -> int | None:
        from ..formats.ipl import iflags

        if self._by_sid is None:
            self._by_sid = {f"inst:{i.ipl}#{i.idx}": i.interior for i in self.gd.placements}
        v = self._by_sid.get(sid.lower())
        return iflags(v) if v is not None else None

    def zone(self, name: str) -> dict | None:
        from ..formats.dat import read_text, resolve_ci
        from ..formats.zon import parse_zon

        want = name.strip().lower()
        for rel in ("data/info.zon", "data/map.zon"):  # info zones first, as the index's zone: SIDs
            p = resolve_ci(self.gd.root, rel)
            if p is None:
                continue
            for z in parse_zon(read_text(p)):
                if z["name"].lower() == want:
                    return {"id": f"zone:{want}", "name": z["name"], "min": list(z["min"]), "max": list(z["max"])}
        return None


def open_source(profile: str = "vanilla", source: str = "auto") -> tuple[Any, list[str]]:
    """``(source, warnings)``. ``auto`` probes the index with a cheap call and falls back."""
    if source not in ("auto", "index", "direct"):
        raise SatkError("BAD_PARAMS", f"source must be auto|index|direct, got {source!r}")
    warn: list[str] = []
    if source in ("auto", "index"):
        try:
            from ..index.api import open_index

            db = open_index(profile)
            db.model_files(411)  # raises NOT_READY on the Q1 stub
            return _IndexSource(db), warn
        except SatkError as e:
            if source == "index" or e.code not in ("NOT_READY", "INDEX_MISSING", "NOT_FOUND"):
                raise
            warn.append(f"INDEX_MISSING: {e.code} ({e.msg[:80]}); using direct DAT/IDE/IPL parsing")
    from . import gamedata

    return _DirectSource(gamedata.load(profile)), warn


# --------------------------------------------------------------------------- blob cache


def _cache_dir() -> Path:
    return work("blender", "cache", "blobs")


def _store(data: bytes, name: str) -> Path:
    h = hashlib.sha256(data).hexdigest()
    safe = re.sub(r"[^A-Za-z0-9_.\-]", "_", name) or "blob"
    d = _cache_dir() / h[:2] / h[:16]
    p = d / safe
    if p.is_file() and p.stat().st_size == len(data):
        return p
    ensure_writable(p)
    d.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{safe}.{os.getpid()}.tmp")
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, p)
    return p


def cache_blob(src, ref) -> Path:
    """Copy a DFF/TXD blob into the cache (trimmed to its RW size); returns the cached path."""
    data = src.read(ref)
    if ref.name.lower().endswith((".dff", ".txd")):
        n = rw_payload_size(data)
        if n:
            data = data[:n]
    return _store(data, ref.name.lower())


def cache_col(src, col) -> Path | None:
    """One-model ``<name>.col`` for a ``colfile`` ColRef (``None`` for embedded collisions)."""
    if col is None or col.via != "colfile":
        return None
    from ..formats.col import iter_col

    data = src.read(col.blob)
    off = 0
    for m in iter_col(data):
        if m.idx == col.idx:
            return _store(bytes(data[off:off + m.size]), f"{col.name}.col")
        off += m.size
    raise SatkError("NOT_FOUND", f"collision model {col.name!r} (#{col.idx}) not found in {col.blob.sid}")


# --------------------------------------------------------------------------- carcols


def parse_carcols(text: str) -> tuple[list[tuple[int, int, int]], dict[str, list[list[int]]]]:
    """``data/carcols.dat`` -> (palette, {model name: [[c1, c2, c3, c4], ...]}).

    Sections ``col`` (``r,g,b``), ``car`` (``name, c1,c2, c1,c2, ...``) and ``car4``
    (``name, c1,c2,c3,c4, ...``); ``car`` combinations get ``c3, c4 = c1, c2``.
    """
    pal: list[tuple[int, int, int]] = []
    cars: dict[str, list[list[int]]] = {}
    sec = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].replace(",", " ").strip()
        if not line:
            continue
        f = line.split()
        low = f[0].lower()
        if sec is None:
            if low in ("col", "car", "car4"):
                sec = low
            continue
        if low == "end":
            sec = None
            continue
        try:
            if sec == "col" and len(f) >= 3:
                pal.append((int(f[0]), int(f[1]), int(f[2])))
            elif sec in ("car", "car4") and len(f) >= 3:
                n = 2 if sec == "car" else 4
                nums = [int(x) for x in f[1:]]
                combos = [nums[i:i + n] for i in range(0, len(nums) - n + 1, n)]
                if n == 2:
                    combos = [c + c for c in combos]
                cars.setdefault(f[0].lower(), []).extend(combos)
        except ValueError:
            continue
    return pal, cars


def carcols_for(root: Path, model_name: str) -> dict | None:
    """First colour combination of a vehicle from ``data/carcols.dat`` of the game root."""
    from ..formats.dat import read_text, resolve_ci

    p = resolve_ci(root, "data/carcols.dat")
    if p is None:
        return None
    pal, cars = parse_carcols(read_text(p))
    combos = cars.get(model_name.lower())
    if not combos:
        return None
    c = combos[0]
    rgb = [list(pal[i]) if 0 <= i < len(pal) else [255, 255, 255] for i in c]
    return {"combo": c, "colors": rgb, "slots": [list(s) for s in SLOT_COLORS]}


# --------------------------------------------------------------------------- plans


def parse_model_spec(spec: str | int) -> str:
    """Normalise ``model:411`` / ``411`` / ``model:infernus`` / ``infernus`` to an index argument."""
    s = str(spec).strip()
    if s.lower().startswith("model:"):
        s = s[6:]
    if not s:
        raise SatkError("BAD_ID", f"bad model id {spec!r}", hint="model:411, model:infernus, 411 or a name")
    if "@" in s:
        raise SatkError("UNSUPPORTED", "Blender imports the active definition only (no @layer)")
    return s


def _model_plan(src, spec: str | int, *, col: bool, root: Path | None, warn: list[str]) -> dict:
    key = parse_model_spec(spec)
    mf = src.model_files(int(key) if key.lstrip("-").isdigit() else key)
    if mf.dff is None:
        raise SatkError("NOT_FOUND", f"model:{mf.model_id} ({mf.name}) has no DFF in the game files",
                        hint="placeholders such as cutscene 'hier' objects cannot be imported")
    plan: dict[str, Any] = {
        "sid": f"model:{mf.model_id}", "id": mf.model_id, "name": mf.name, "sec": mf.sec,
        "dff": jpath(cache_blob(src, mf.dff)),
        "txd": [jpath(cache_blob(src, t)) for t in mf.txd_chain],
        "txd_names": [t.name.rsplit(".", 1)[0].lower() for t in mf.txd_chain],
    }
    ide = src.model_def(mf.model_id)
    if ide:
        plan["ide"] = ide  # draw distance, flags (+ tobj times / anim IFP) for IDE fragments of an export
    if not mf.txd_chain:
        warn.append(f"NO_TXD: model:{mf.model_id} {mf.name} has no texture dictionary")
    if mf.col is not None:
        plan["col_via"] = mf.col.via
        if col and mf.col.via == "colfile":
            p = cache_col(src, mf.col)
            if p is not None:
                plan["col"] = jpath(p)
    if mf.sec == "cars" and root is not None:
        cc = carcols_for(root, mf.name)
        if cc:
            plan["vehicle"] = cc
    return plan


def _root(profile: str) -> Path | None:
    try:
        from ..core.paths import profile_root

        return Path(profile_root(profile))
    except SatkError:
        return None


def plan_model(spec: str | int, *, profile: str = "vanilla", col: bool = False, source: str = "auto") -> dict:
    """Plan of one model: cached DFF/TXD chain (+COL), vehicle colours. ``{"model": {...}, "warnings"}``."""
    src, warn = open_source(profile, source)
    m = _model_plan(src, spec, col=col, root=_root(profile), warn=warn)
    return {"source": src.kind, "profile": profile, "model": m, "warnings": warn}


def _wq(q) -> list[float]:
    x, y, z, w = (float(c) for c in q)
    return [-x, -y, -z, w]


def plan_area(*, center, r: float | None = None, box=None, match: str = "aabb", area: int | None = 0,
              lod: str = "hd", col: bool = False, limit: int = 2000, profile: str = "vanilla",
              source: str = "auto") -> dict:
    """Placements of an area and the models they need.

    ``box`` = ``[x0, y0, x1, y1]`` (absolute), or ``r`` around ``center``. Returns
    ``{"source", "query", "insts": [...], "models": [...], "missing": [...], "warnings", "truncated"}``.
    """
    src, warn = open_source(profile, source)
    kw: dict[str, Any] = {"area": area, "lod": lod, "match": match, "limit": limit + 1}
    if box is not None:
        kw["box"] = tuple(float(v) for v in box)
    else:
        kw["center"] = (float(center[0]), float(center[1]))
        kw["r"] = float(r)  # type: ignore[arg-type]
    rows = src.insts(**kw)
    truncated = len(rows) > limit
    rows = rows[:limit]
    if truncated:
        warn.append(f"TRUNCATED: more than {limit} placements; raise limit or shrink the area")
    root = _root(profile)
    models: dict[int, dict] = {}
    missing: dict[int, str] = {}
    for mid in sorted({r.model_id for r in rows}):
        try:
            models[mid] = _model_plan(src, mid, col=col, root=root, warn=warn)
        except SatkError as e:
            missing[mid] = e.msg
    insts = []
    for row in rows:
        if row.model_id not in models:
            continue
        insts.append({"sid": row.sid, "model": row.model_id, "pos": [float(c) for c in row.pos],
                      "q": _wq(row.q_ipl), "area": row.area, "iflags": int(row.iflags or 0),
                      "is_lod": bool(row.is_lod), "lod": row.lod_sid})
    if missing:
        warn.append(f"MODELS_SKIPPED: {len(missing)} model(s) without a DFF: "
                    + ", ".join(f"model:{k}" for k in sorted(missing)[:10]))
    return {
        "source": src.kind, "profile": profile,
        "query": {"center": [float(c) for c in center], "r": r, "box": list(box) if box is not None else None,
                  "match": match, "area": area, "lod": lod},
        "insts": insts,
        "models": [models[k] for k in sorted(models)],
        "missing": [{"model": k, "msg": v} for k, v in sorted(missing.items())],
        "truncated": truncated,
        "warnings": warn,
    }


# --------------------------------------------------------------------------- zones


def rect_of(obj: dict) -> tuple[float, float, float, float]:
    """``(x0, y0, x1, y1)`` of a zone-like object: ``min``/``max`` (index ``zone:`` objects),
    ``box``/``bbox``/``aabb`` (4 or 6 numbers) or a bare ``pos`` (a point)."""
    if obj.get("min") is not None and obj.get("max") is not None:
        lo, hi = obj["min"], obj["max"]
        x0, y0, x1, y1 = float(lo[0]), float(lo[1]), float(hi[0]), float(hi[1])
    else:
        b = obj.get("box") or obj.get("bbox") or obj.get("aabb")
        if b is not None and len(b) in (4, 6):
            k = len(b) // 2
            x0, y0, x1, y1 = float(b[0]), float(b[1]), float(b[k]), float(b[k + 1])
        elif obj.get("pos") is not None:
            x0, y0 = x1, y1 = float(obj["pos"][0]), float(obj["pos"][1])
        else:
            raise SatkError("BAD_PARAMS", f"no extent in {sorted(obj)}", hint="expected min/max, box/bbox or pos")
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


def zone_rect(name: str, *, profile: str = "vanilla", source: str = "auto") -> dict:
    """Zone by name (``GAN1`` or ``zone:gan1``): ``{"sid", "name", "rect": [x0,y0,x1,y1], "center": [x,y]}``.

    From the index, else from ``data/info.zon`` and ``data/map.zon`` of the profile root.
    """
    key = str(name).strip()
    if key.lower().startswith("zone:"):
        key = key[5:]
    if not key:
        raise SatkError("BAD_PARAMS", "zone name is empty", hint="e.g. GAN1 (satk asset find gan --kind zone)")
    src, _warn = open_source(profile, source)
    z = src.zone(key)
    if z is None:
        raise SatkError("NOT_FOUND", f"no zone {key!r} in profile {profile!r}",
                        hint=f"satk asset find {key} --kind zone")
    x0, y0, x1, y1 = rect_of(z)
    return {"sid": f"zone:{key.lower()}", "name": z.get("name") or key, "rect": [x0, y0, x1, y1],
            "center": [round((x0 + x1) / 2, 2), round((y0 + y1) / 2, 2)]}


# --------------------------------------------------------------------------- export definitions


def fill_export_defs(models: list[dict], *, profile: str = "vanilla", source: str = "auto") -> list[str]:
    """Complete ``export.json`` models made from an older ``.blend`` (imported before the import kept
    the IDE data): ``ide`` of each model and ``iflags`` of each placement, from the game data.

    Changes ``models`` in place; returns warnings (``IDE_DEFAULTS`` when the game data is unavailable;
    models still without a definition are reported by ``packaging.write_package``).
    """
    need_def = [m for m in models if not m.get("ide") and m.get("id") is not None and m.get("sec") not in ("cars", "peds")]
    need_flags = [p for m in models for p in m.get("insts", ()) if "iflags" not in p]
    warn: list[str] = []
    if not need_def and not need_flags:
        return warn
    try:
        src, _w = open_source(profile, source)
    except SatkError as e:
        return [f"IDE_DEFAULTS: game data of profile {profile!r} unavailable ({e.code}); placements keep iflags 0"]
    for m in need_def:
        d = src.model_def(int(m["id"]))
        if d:
            m["ide"] = d
    for p in need_flags:
        v = src.inst_iflags(str(p.get("sid") or ""))
        p["iflags"] = int(v or 0)
    return warn
