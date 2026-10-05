"""DFF checks (``dff.*`` rules): structure, limits, UV/prelight/normals, vertex data, budgets per class.

The model class decides the class rules (prelight for map objects, normals for cars and peds, budgets):
it comes from the IDE definition (``objs``/``tobj`` -> ``map``, or ``lod`` for LOD names / long draw
distances; ``cars``, ``peds``, ``weap``; ``anim``/``hier`` -> ``other``), else it is guessed from the DFF
(skinned -> ``peds``, embedded collision -> ``cars``, otherwise ``map``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..formats.dff import F_COL, F_NIGHT, F_SKIN, GEO_NATIVE, GEO_NORMALS, GEO_PRELIT, DffInfo, decode_geometries, \
    find_embedded_col, scan_dff
from ..formats.rw import FormatError
from .col import check_col
from .rules import Collector

__all__ = ["DffFacts", "ModelDef", "check_dff", "model_class", "CLASSES"]

CLASSES: tuple[str, ...] = ("map", "lod", "cars", "peds", "weap", "other")
_COL_MAGIC = (b"COLL", b"COL2", b"COL3")          # what ClumpCollisionStreamRead switches on
_SEC_CLASS = {"cars": "cars", "peds": "peds", "weap": "weap", "anim": "other", "hier": "other"}


@dataclass(frozen=True, slots=True)
class ModelDef:
    """A model definition (IDE line or index row). ``origin`` = ``<ide label>:<line>`` or ``index``."""

    id: int
    name: str
    txd: str | None
    sec: str
    draw: float | None
    origin: str
    meshes_form: bool = False          # old objs form with a mesh count
    chain: tuple[str, ...] | None = None  # TXD chain from the index (SID targets)


@dataclass(frozen=True, slots=True)
class DffFacts:
    """What the link checks need from one DFF."""

    stem: str
    label: str
    archive: str | None
    loose: bool
    textures: tuple[str, ...]          # lower-case material texture names, one per material
    bbox: tuple[float, ...] | None


def model_class(rules_classes: dict, d: ModelDef | None, info: DffInfo | None, *, guess: bool = True) -> str:
    """``map``/``lod``/``cars``/``peds``/``weap``/``other`` (see the module docstring).

    Without a definition the class is guessed from ``info`` when ``guess`` is true (a lone file), else it is
    ``other`` (the set has definitions, so a DFF without one is no map object: cutscene, clothes, ...).
    """
    if d is not None:
        if d.sec in ("objs", "tobj"):
            lod = str(rules_classes.get("lod_name", "lod")).lower()
            far = float(rules_classes.get("lod_draw", 300.0))
            if (lod and lod in d.name.lower()) or (d.draw is not None and d.draw > far):
                return "lod"
            return "map"
        return _SEC_CLASS.get(d.sec, "other")
    if not guess:
        return "other"
    if info is not None:
        if info.flags & F_SKIN:
            return "peds"
        if info.flags & F_COL:
            return "cars"
    return "map"


def _finite(a) -> bool:
    try:
        return math.isfinite(math.fsum(a)) if len(a) < 4096 else math.isfinite(sum(a))
    except (OverflowError, ValueError):
        return False


def check_dff(c: Collector, label: str, data: bytes, d: ModelDef | None, *, archive: str | None = None,
              loose: bool = False, guess: bool = True) -> DffFacts | None:
    """Run the ``dff.*`` rules (and ``col.*`` on an embedded collision) on one DFF blob."""
    stem = label.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
    try:
        info = scan_dff(data)
    except FormatError as e:
        c.add("dff.parse", label, err=str(e))
        return None
    cls = model_class(c.rules.classes, d, info, guess=guess)
    ver = f"0x{info.rw_version:X}"
    known = [str(k).upper().replace("0X", "0x") for k in c.rules.param("dff.rw_version", "known", [])]
    if known and ver not in known:
        c.add("dff.rw_version", label, ver=ver)
    if info.atomics == 0:
        c.add("dff.no_atomics", label)
    fmax = int(c.rules.param("dff.frame_name_len", "max", 23))
    for f in info.frames:
        if f.name and len(f.name) > fmax:
            c.add("dff.frame_name_len", label, frame=f.idx, name=f.name, n=len(f.name))
    vmax = int(c.rules.param("dff.verts_max", "max", 65535))
    uvmax = int(c.rules.param("dff.uv_sets", "max", 2))
    tmax = int(c.rules.param("dff.tex_name_len", "max", 31))
    norm_classes = set(c.rules.param("dff.normals_missing", "classes", []) or [])
    mats_by_geom: dict[int, list] = {}
    for m in info.materials:
        mats_by_geom.setdefault(m.geom, []).append(m)
        if m.texture and len(m.texture) > tmax:
            c.add("dff.tex_name_len", label, geom=m.geom, idx=m.idx, tex=m.texture, n=len(m.texture))
    native = False
    for g in info.geoms:
        if g.rw_flags & GEO_NATIVE:
            native = True
            c.add("dff.native", label, geom=g.idx)
            continue
        if g.verts > vmax:
            c.add("dff.verts_max", label, geom=g.idx, verts=g.verts)
        if g.tris == 0:
            c.add("dff.empty_geometry", label, geom=g.idx, verts=g.verts)
        textured = sorted({m.texture for m in mats_by_geom.get(g.idx, ()) if m.texture}, key=str.lower)
        if textured and g.uv_sets == 0:
            c.add("dff.uv_missing", label, geom=g.idx, tex=textured[:5])
        if g.uv_sets > uvmax:
            c.add("dff.uv_sets", label, geom=g.idx, n=g.uv_sets)
        if cls in ("map", "lod") and not g.rw_flags & (GEO_PRELIT | GEO_NORMALS) and g.tris:
            c.add("dff.prelight_missing", label, geom=g.idx, cls=cls)
        if cls in norm_classes and not g.rw_flags & GEO_NORMALS and g.tris:
            c.add("dff.normals_missing", label, geom=g.idx, cls=cls)
    if cls in ("map", "lod") and not info.flags & F_NIGHT and any(g.rw_flags & GEO_PRELIT for g in info.geoms):
        c.add("dff.night_missing", label)
    if info.bbox is not None:
        bb = info.bbox
        r = max(info.bsphere[3] if info.bsphere else 0.0, math.dist(bb[:3], bb[3:]) / 2)
        if math.isfinite(r) and r > float(c.rules.param("dff.bounds_size", "max_radius", 3000.0)):
            c.add("dff.bounds_size", label, r=round(r, 1))
    budget = c.rules.param("dff.tris_budget", "budget", {}) or {}
    lim = budget.get(cls, budget.get("other"))
    if lim is not None and info.tris > int(lim):
        c.add("dff.tris_budget", label, tris=info.tris, limit=int(lim), cls=cls)
    mbudget = c.rules.param("dff.materials_budget", "budget", {}) or {}
    mlim = mbudget.get(cls, mbudget.get("other"))
    if mlim is not None and len(info.materials) > int(mlim):
        c.add("dff.materials_budget", label, n=len(info.materials), limit=int(mlim), cls=cls)
    if not native and any(c.on(r) for r in ("dff.bad_vertex", "dff.uv_range", "dff.prelight_black", "dff.bsphere")):
        _deep(c, label, data, info, cls)
    if info.flags & F_COL:
        try:
            emb = find_embedded_col(data)
        except FormatError:
            emb = None
        if emb is not None:
            blob = data[emb[0]:emb[0] + emb[1]]
            if blob[:4] in _COL_MAGIC:
                check_col(c, label + "#col", blob, embedded=True)
            else:
                c.add("dff.embedded_col", label, magic=blob[:4].hex())
    tex = tuple(m.texture.lower() for m in info.materials if m.texture)
    return DffFacts(stem, label, archive, loose, tex, tuple(info.bbox) if info.bbox else None)


def _beyond(pos, sphere, tol: float = 0.0) -> float:
    """How far the farthest of ``pos`` (xyz interleaved) lies outside ``sphere`` = ``(x, y, z, r)``
    (0 when even the corners of their bounding box are within ``r + tol``)."""
    cx, cy, cz, r = sphere
    xs, ys, zs = pos[0::3], pos[1::3], pos[2::3]
    corner = math.sqrt(max((min(xs) - cx) ** 2, (max(xs) - cx) ** 2) + max((min(ys) - cy) ** 2, (max(ys) - cy) ** 2)
                       + max((min(zs) - cz) ** 2, (max(zs) - cz) ** 2))
    if corner <= r + tol:                         # fast path: the whole bounding box is close enough
        return 0.0
    d2 = max((x - cx) ** 2 + (y - cy) ** 2 + (z - cz) ** 2 for x, y, z in zip(xs, ys, zs))
    return math.sqrt(d2) - r


def _deep(c: Collector, label: str, data: bytes, info: DffInfo, cls: str) -> None:
    """Checks on decoded vertex arrays: NaN/inf, UV range, all-black prelight."""
    try:
        meshes = decode_geometries(data)
    except FormatError as e:
        c.add("dff.parse", label, err=str(e))
        return
    uvlim = float(c.rules.param("dff.uv_range", "max_abs", 256.0))
    stol = float(c.rules.param("dff.bsphere", "tol", 0.5))
    for g, mesh in zip(info.geoms, meshes):
        bad = []
        if len(mesh.positions) and not _finite(mesh.positions):
            bad.append("positions")
        if mesh.normals is not None and len(mesh.normals) and not _finite(mesh.normals):
            bad.append("normals")
        if bad:
            c.add("dff.bad_vertex", label, geom=g.idx, what=", ".join(bad))
        elif len(mesh.positions) and g.bsphere[3] > 0:
            d = _beyond(mesh.positions, g.bsphere, stol)
            if d > stol:
                c.add("dff.bsphere", label, geom=g.idx, d=round(d, 2))
        for s, uv in enumerate(mesh.uv):
            if not len(uv):
                continue
            if not _finite(uv):
                c.add("dff.uv_range", label, geom=g.idx, set=s, v="NaN/inf")
                continue
            v = max(-min(uv), max(uv))
            if v > uvlim:
                c.add("dff.uv_range", label, geom=g.idx, set=s, v=round(v, 1))
        p = mesh.prelit
        if cls in ("map", "lod") and p is not None and len(p) >= 4 and g.tris:
            if max(p[0::4]) == 0 and max(p[1::4]) == 0 and max(p[2::4]) == 0:
                c.add("dff.prelight_black", label, geom=g.idx, verts=len(p) // 4)
