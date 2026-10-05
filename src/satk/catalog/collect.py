"""Everything the catalog shows, read from the index through its public API (owner M2-09).

Only :meth:`IndexDB.query` (read-only SQL, keyset-paged by 500 rows) and :meth:`IndexDB.insts` (placements
with world AABBs) are used, so the same code runs over :class:`~satk.index.fake.FakeIndexDB` in tests.

:func:`collect` returns a :class:`Data`: models (by id), active TXDs (by name), their textures, the unique
images (pixel hashes), zones, placements per model, model <-> texture links and per-zone model counts.
``limit`` keeps the first N models (by id) and only the textures/TXDs they use (a quick sample catalog).
Stdlib only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Iterator

from ..core.errors import SatkError

__all__ = ["Data", "collect", "PAGE", "WORLD"]

#: Rows per ``IndexDB.query`` call (its maximum).
PAGE = 500
#: World square of the map (SA: +-3000 m).
WORLD = (-3000.0, -3000.0, 3000.0, 3000.0)
#: Placements kept per model page (vanilla maximum is 1 362).
MAX_PLACEMENTS = 5000
#: Models listed per zone page (most placed first).
ZONE_TOP = 120


@dataclass
class Data:
    """Catalog content (plain lists/dicts; indices into the sorted lists are the catalog's ids)."""

    profile: str
    index_hash: str
    limited: bool
    models: list[dict] = field(default_factory=list)       # sorted by id
    txds: list[dict] = field(default_factory=list)         # sorted by (name, id)
    textures: list[dict] = field(default_factory=list)     # sorted by (txd order, idx in TXD)
    images: list[str] = field(default_factory=list)        # 24-hex pixel hashes, sorted
    zones: list[dict] = field(default_factory=list)        # sorted by id
    ipls: list[str] = field(default_factory=list)          # sorted IPL names of placements
    insts: dict[int, list[list]] = field(default_factory=dict)       # model id -> [[ipl_i, idx, x, y, z, area, lod]]
    model_tex: dict[int, list[list]] = field(default_factory=dict)   # model id -> [[name, via, uses, tex_i]]
    tex_models: dict[int, list[list]] = field(default_factory=dict)  # tex_i -> [[model id, uses]]
    zone_models: dict[int, list[list]] = field(default_factory=dict)  # zone_i -> [[model id, count]]
    footprints: list[tuple] = field(default_factory=list)  # exterior (minx, miny, maxx, maxy, minz, maxz, lod)
    counts: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- SQL helpers


def _pages(db, sql: str, start: list) -> Iterator[list]:
    """Keyset paging: ``sql`` compares its key with ``?`` placeholders (``> ?`` or ``> (?, ?)``),
    orders by that key and ends with ``LIMIT 500``; the first ``len(start)`` columns are the key."""
    key = list(start)
    n = len(start)
    while True:
        rows = db.query(sql, key, limit=PAGE)["rows"]
        yield from rows
        if len(rows) < PAGE:
            return
        key = list(rows[-1][:n])


def _r(v, nd: int = 2):
    return None if v is None else round(float(v), nd)


def _json(v):
    if not v:
        return None
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return v


# --------------------------------------------------------------------------- models / TXDs / textures

_MODEL_SQL = (
    "SELECT m.id, m.name, m.sec, m.txd, m.draw, m.flags, m.time_on, m.time_off, m.anim, m.extra, m.line, "
    "s.relpath, la.name, l.txd_id, l.col_via, l.n_inst, l.tex_total, l.tex_missing, "
    "d.name, d.tris, d.verts, d.geoms, d.materials, d.flags, d.bs_r, "
    "d.bmin_x, d.bmin_y, d.bmin_z, d.bmax_x, d.bmax_y, d.bmax_z, "
    "c.name, c.version, c.spheres, c.boxes, c.faces "
    "FROM model m JOIN ide i ON i.id = m.ide_id JOIN source s ON s.id = i.source_id "
    "JOIN layer la ON la.id = m.layer_id LEFT JOIN model_link l ON l.id = m.id "
    "LEFT JOIN dff d ON d.id = l.dff_id LEFT JOIN col c ON c.id = l.col_id "
    "WHERE m.active = 1 AND m.id > ? ORDER BY m.id LIMIT 500")

_TXD_SQL = (
    "SELECT t.id, t.name, t.tex_count, t.parent, t.parent_via, b.ns, s.relpath, s.kind "
    "FROM txd t JOIN blob b ON b.id = t.blob_id JOIN source s ON s.id = b.source_id "
    "WHERE b.active = 1 AND t.id > ? ORDER BY t.id LIMIT 500")

_TEX_SQL = (
    "SELECT x.id, x.txd_id, x.idx, x.name, x.mask, x.w, x.h, x.d3dfmt, x.alpha, x.platform, lower(hex(x.hash)) "
    "FROM texture x JOIN txd t ON t.id = x.txd_id JOIN blob b ON b.id = t.blob_id "
    "WHERE b.active = 1 AND x.id > ? ORDER BY x.id LIMIT 500")

_MODEL_TEX_SQL = (
    "SELECT model_id, texture, texture_id, via, uses FROM model_tex "
    "WHERE (model_id, texture) > (?, ?) ORDER BY model_id, texture LIMIT 500")

_ZONE_SQL = (
    "SELECT id, name, label, title, type, level, minx, miny, minz, maxx, maxy, maxz FROM zone "
    "WHERE id > ? ORDER BY id LIMIT 500")


def _models(db, limit: int | None) -> tuple[list[dict], dict[int, int]]:
    """Active models sorted by id, and ``{model id: own txd row id}``."""
    out: list[dict] = []
    own_txd: dict[int, int] = {}
    for r in _pages(db, _MODEL_SQL, [-1]):
        (mid, name, sec, txd, draw, flags, t_on, t_off, anim, extra, line, ide, layer, txd_id, col_via, n_inst,
         tex_total, tex_missing, dname, tris, verts, geoms, mats, dflags, bs_r, x0, y0, z0, x1, y1, z1,
         cname, cver, csph, cbox, cfaces) = r
        m = {"id": int(mid), "name": str(name), "sec": str(sec), "txd": txd, "draw": _r(draw, 1), "flags": flags,
             "time": [t_on, t_off] if t_on is not None or t_off is not None else None, "anim": anim,
             "extra": _json(extra), "ide": f"{ide}:{line}", "layer": layer, "n_inst": int(n_inst or 0),
             "tex_total": int(tex_total or 0), "tex_missing": int(tex_missing or 0), "dff": None, "col": None}
        if dname is not None:
            m["dff"] = [str(dname), tris, verts, geoms, mats, int(dflags or 0), _r(bs_r),
                        [_r(x0), _r(y0), _r(z0)], [_r(x1), _r(y1), _r(z1)]]
        if cname is not None or col_via:
            m["col"] = [cname, col_via, cver, csph, cbox, cfaces]
        if txd_id is not None:
            own_txd[int(mid)] = int(txd_id)
        out.append(m)
        if limit is not None and len(out) >= limit:
            break
    return out, own_txd


def _txds(db) -> list[dict]:
    rows = []
    for tid, name, ntex, parent, via, ns, relpath, skind in _pages(db, _TXD_SQL, [-1]):
        arch = str(relpath) if skind == "img" else str(relpath).rsplit("/", 1)[0] + "/"
        rows.append({"rid": int(tid), "name": str(name), "ntex": int(ntex or 0), "parent": parent, "via": via,
                     "ns": str(ns), "archive": arch, "file": str(relpath)})
    rows.sort(key=lambda t: (t["name"].lower(), t["ns"] != "main", t["rid"]))
    return rows


def _textures(db, txd_index: dict[int, int]) -> list[dict]:
    rows = []
    for xid, txd_id, idx, name, mask, w, h, fmt, alpha, platform, hexh in _pages(db, _TEX_SQL, [-1]):
        ti = txd_index.get(int(txd_id))
        if ti is None:
            continue
        rows.append({"rid": int(xid), "txd_i": ti, "idx": int(idx), "name": str(name), "mask": mask or None,
                     "w": int(w), "h": int(h), "fmt": str(fmt), "alpha": int(bool(alpha)),
                     "pc": int(platform) in (8, 9) and int(w) > 0 and int(h) > 0, "pix": str(hexh)})
    rows.sort(key=lambda x: (x["txd_i"], x["idx"], x["rid"]))
    return rows


# --------------------------------------------------------------------------- placements / zones


def _placements(db, model_ids: list[int] | None) -> list:
    """InstRows of every placement (all areas, HD and LOD); ``model_ids`` = only these models."""
    box = (-1e7, -1e7, 1e7, 1e7)
    if model_ids is None:
        return list(db.insts(box=box, area=None, lod="all"))
    rows = []
    for mid in model_ids:
        rows.extend(db.insts(model=mid, area=None, lod="all"))
    return rows


def _zone_of(zones: list[dict]):
    """``f(x, y, z) -> zone index | None``: the smallest zone (by area) containing the point."""
    order = sorted(range(len(zones)), key=lambda i: ((zones[i]["box"][3] - zones[i]["box"][0])
                                                     * (zones[i]["box"][4] - zones[i]["box"][1]), i))

    def find(x: float, y: float, z: float) -> int | None:
        for i in order:
            b = zones[i]["box"]
            if b[0] <= x <= b[3] and b[1] <= y <= b[4] and b[2] <= z <= b[5]:
                return i
        return None

    return find


def _zone_models(zones: list[dict], placed: list[tuple[int, float, float, float]]) -> dict[int, list[list]]:
    """``{zone index: [[model id, count], ...]}`` - HD exterior placements inside each zone box (top N)."""
    cell = 250.0
    grid: dict[tuple[int, int], list[tuple[int, float, float, float]]] = {}
    for p in placed:
        grid.setdefault((int(p[1] // cell), int(p[2] // cell)), []).append(p)
    out: dict[int, list[list]] = {}
    for zi, z in enumerate(zones):
        x0, y0, z0, x1, y1, z1 = z["box"]
        cnt: dict[int, int] = {}
        for gx in range(int(x0 // cell), int(x1 // cell) + 1):
            for gy in range(int(y0 // cell), int(y1 // cell) + 1):
                for mid, x, y, zz in grid.get((gx, gy), ()):
                    if x0 <= x <= x1 and y0 <= y <= y1 and z0 <= zz <= z1:
                        cnt[mid] = cnt.get(mid, 0) + 1
        z["ninst"] = sum(cnt.values())
        z["nmodels"] = len(cnt)
        if cnt:
            out[zi] = [[m, n] for m, n in sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))[:ZONE_TOP]]
    return out


# --------------------------------------------------------------------------- collect


def collect(db, profile: str, limit: int | None = None) -> Data:
    """Read the catalog content from an open index (``limit`` = first N models only)."""
    if limit is not None and limit < 1:
        raise SatkError("BAD_PARAMS", f"limit must be >= 1, got {limit}")
    meta = db.meta()
    data = Data(profile=profile, index_hash=str(meta.get("content_hash") or ""), limited=limit is not None)

    models, own_txd = _models(db, limit)
    data.models = models
    model_ids = {m["id"] for m in models}

    # model -> material textures (resolved through the TXD chain by the index)
    mtex_raw: dict[int, list] = {}
    last_id = max(model_ids, default=-1)
    for mid, tex, tex_id, via, uses in _pages(db, _MODEL_TEX_SQL, [-1, ""]):
        mid = int(mid)
        if mid in model_ids:
            mtex_raw.setdefault(mid, []).append([str(tex), str(via), int(uses or 0),
                                                 None if tex_id is None else int(tex_id)])
        elif limit is not None and mid > last_id:
            break

    txds_all = _txds(db)
    tex_all = _textures(db, {t["rid"]: i for i, t in enumerate(txds_all)})
    if limit is None:
        txds, textures = txds_all, tex_all
    else:
        # only the TXDs of the chosen models (own + txdp parents) and the TXDs of their resolved textures
        by_rid = {t["rid"]: t for t in txds_all}
        by_name: dict[str, dict] = {}
        for t in txds_all:
            by_name.setdefault(t["name"].lower(), t)
        keep: set[int] = set()
        for mid in sorted(model_ids):
            rid = own_txd.get(mid)
            while rid is not None and rid not in keep and rid in by_rid:
                keep.add(rid)
                parent = by_rid[rid]["parent"]
                nxt = by_name.get(str(parent).lower()) if parent else None
                rid = nxt["rid"] if nxt else None
        tex_ids = {e[3] for lst in mtex_raw.values() for e in lst if e[3] is not None}
        keep |= {txds_all[x["txd_i"]]["rid"] for x in tex_all if x["rid"] in tex_ids}
        new_i: dict[int, int] = {}
        txds = []
        for i, t in enumerate(txds_all):
            if t["rid"] in keep:
                new_i[i] = len(txds)
                txds.append(t)
        textures = [{**x, "txd_i": new_i[x["txd_i"]]} for x in tex_all if x["txd_i"] in new_i]
    data.txds = txds
    txd_index = {t["rid"]: i for i, t in enumerate(txds)}
    data.textures = textures
    tex_index = {x["rid"]: i for i, x in enumerate(textures)}
    data.images = sorted({x["pix"] for x in textures})

    # TXD parents / model counts
    by_name_i: dict[str, int] = {}
    for i, t in enumerate(txds):
        by_name_i.setdefault(t["name"].lower(), i)
    for t in txds:
        t["parent_i"] = by_name_i.get(str(t["parent"]).lower(), -1) if t["parent"] else -1
        t["nmodels"] = 0
    for m in models:
        ti = txd_index.get(own_txd.get(m["id"], -1))
        m["txd_i"] = -1 if ti is None else ti
        if ti is not None:
            txds[ti]["nmodels"] += 1

    for mid, lst in mtex_raw.items():
        rows = []
        for name, via, uses, tid in lst:
            ti = tex_index.get(tid) if tid is not None else None
            rows.append([name, via, uses, -1 if ti is None else ti])
            if ti is not None:
                data.tex_models.setdefault(ti, []).append([mid, uses])
        data.model_tex[mid] = rows
    for x in textures:
        x["nmodels"] = 0
    for ti, lst in data.tex_models.items():
        lst.sort()
        textures[ti]["nmodels"] = len(lst)

    # zones
    for zid, name, label, title, typ, level, x0, y0, z0, x1, y1, z1 in _pages(db, _ZONE_SQL, [-1]):
        box = [float(v) if v is not None else 0.0 for v in (x0, y0, z0, x1, y1, z1)]
        data.zones.append({"rid": int(zid), "name": str(name), "label": label, "title": title, "type": typ,
                           "level": level, "box": box})

    # placements
    rows = _placements(db, None if limit is None else sorted(model_ids))
    ipls = sorted({r.sid[5:].rsplit("#", 1)[0] for r in rows})
    ipl_i = {n: i for i, n in enumerate(ipls)}
    data.ipls = ipls
    placed: list[tuple[int, float, float, float]] = []
    n_ext = 0
    for r in sorted(rows, key=lambda r: (r.model_id, r.sid.rsplit("#", 1)[0], int(r.sid.rsplit("#", 1)[1]))):
        if r.model_id not in model_ids:
            continue
        ipl, idx = r.sid[5:].rsplit("#", 1)
        x, y, z = r.pos
        data.insts.setdefault(r.model_id, []).append(
            [ipl_i[ipl], int(idx), _r(x), _r(y), _r(z), int(r.area), int(bool(r.is_lod))])
        if r.area == 0:
            n_ext += 1
            a = r.aabb
            data.footprints.append((a[0], a[1], a[3], a[4], a[2], a[5], bool(r.is_lod)))
            if not r.is_lod:
                placed.append((r.model_id, x, y, z))
    for mid, lst in data.insts.items():
        if len(lst) > MAX_PLACEMENTS:
            data.insts[mid] = lst[:MAX_PLACEMENTS]
    data.zone_models = _zone_models(data.zones, placed)

    data.counts = {"models": len(models), "textures": len(textures), "images": len(data.images),
                   "txds": len(txds), "zones": len(data.zones), "placements": len(rows), "exterior": n_ext}
    return data
