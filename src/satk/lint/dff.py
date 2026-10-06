"""DFF checks (``dff.*`` rules): structure, limits, UV/prelight/normals, vertex data, budgets per class.

The model class decides the class rules (prelight for map objects, normals for cars and peds, budgets):
it comes from the IDE definition, else it is guessed from the DFF (skinned -> ``peds``, embedded collision ->
``cars``, otherwise ``map``):

* ``objs``/``tobj`` -> ``lod`` for LOD names / long draw distances, ``pickup`` for the pickup models,
  ``upgrade`` for definitions of the vehicle-upgrade IDE (``veh_mods.ide``), ``interior_shell`` /
  ``interior_prop`` for interior IDEs or ``gta_int.img`` (shell when the model is at least
  ``interior_shell_size`` metres across), otherwise ``map``;
* ``cars``, ``peds``, ``weap``; ``anim``/``hier`` -> ``other``.

The class names follow ``satk.style``; ``cars`` is the vehicle class (every vehicle type). Semantic rules for
vehicles, peds and weapons live in :mod:`satk.lint.semantics`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..formats.dff import F_COL, F_NIGHT, F_SKIN, GEO_NATIVE, GEO_NORMALS, GEO_PRELIT, DffInfo, decode_geometries, \
    find_embedded_col, scan_dff
from ..formats.rw import FormatError
from .col import check_col
from .rules import Collector

__all__ = ["DffFacts", "ModelDef", "check_dff", "model_class", "CLASSES", "MAP_LIKE", "PRELIT", "class_budget"]

CLASSES: tuple[str, ...] = ("map", "lod", "cars", "peds", "weap", "upgrade", "pickup", "interior_prop",
                            "interior_shell", "other")
#: Classes defined in ``objs``/``tobj`` that are placed objects (map budgets, collision expected).
MAP_LIKE = frozenset({"map", "upgrade", "pickup", "interior_prop", "interior_shell"})
#: Classes lit by prelight (vertex colours) instead of dynamic lights.
PRELIT = MAP_LIKE | {"lod"}
_COL_MAGIC = (b"COLL", b"COL2", b"COL3")          # what ClumpCollisionStreamRead switches on
_SEC_CLASS = {"cars": "cars", "peds": "peds", "weap": "weap", "anim": "other", "hier": "other"}


@dataclass(frozen=True, slots=True)
class ModelDef:
    """A model definition (IDE line or index row). ``origin`` = ``<ide label>:<line>`` or ``index``.

    ``flags`` = IDE flags; ``extra`` = section fields (``cars``: ``type``, ``wheel_scale_f`` ...); ``ide`` =
    the IDE file (lower case, forward slashes) the definition comes from."""

    id: int
    name: str
    txd: str | None
    sec: str
    draw: float | None
    origin: str
    meshes_form: bool = False          # old objs form with a mesh count
    chain: tuple[str, ...] | None = None  # TXD chain from the index (SID targets)
    flags: int | None = None
    extra: dict | None = None
    ide: str = ""


@dataclass(frozen=True, slots=True)
class DffFacts:
    """What the link checks need from one DFF."""

    stem: str
    label: str
    archive: str | None
    loose: bool
    textures: tuple[str, ...]          # lower-case material texture names, one per material
    bbox: tuple[float, ...] | None
    cls: str = "other"
    mat_alpha: bool = False            # a material colour with alpha < 255


def _size(info: DffInfo | None) -> float | None:
    if info is None or info.bbox is None:
        return None
    b = info.bbox
    return max(b[3] - b[0], b[4] - b[1], b[5] - b[2])


def model_class(rules_classes: dict, d: ModelDef | None, info: DffInfo | None, *, guess: bool = True,
                archive: str | None = None) -> str:
    """Model class (see the module docstring).

    Without a definition the class is guessed from ``info`` when ``guess`` is true (a lone file), else it is
    ``other`` (the set has definitions, so a DFF without one is no map object: cutscene, clothes, ...).
    An archive listed in ``archive_class`` (``player.img``: clothes) fixes the class of its entries.
    """
    arch_cls = (rules_classes.get("archive_class") or {}).get((archive or "").lower())
    if arch_cls:
        return str(arch_cls)
    if d is not None:
        if d.sec in ("objs", "tobj"):
            lod = str(rules_classes.get("lod_name", "lod")).lower()
            far = float(rules_classes.get("lod_draw", 300.0))
            name = d.name.lower()
            if (lod and lod in name) or (d.draw is not None and d.draw > far):
                return "lod"
            if name in {str(x).lower() for x in rules_classes.get("pickup_names", ())}:
                return "pickup"
            ide = (d.ide or d.origin.rpartition(":")[0]).lower().replace("\\", "/")
            if any(str(p).lower() in ide for p in rules_classes.get("upgrade_ide", ())):
                return "upgrade"
            arcs = {str(a).lower() for a in rules_classes.get("interior_archives", ())}
            if any(str(p).lower() in ide for p in rules_classes.get("interior_ide", ())) or (archive or "") in arcs:
                size = _size(info)
                big = float(rules_classes.get("interior_shell_size", 8.0))
                return "interior_shell" if size is not None and size >= big else "interior_prop"
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


def class_budget(budget: dict, cls: str):
    """The budget of ``cls`` in a ``{class: limit}`` table: the class, else ``map`` for map-like classes,
    else ``other``."""
    if cls in budget:
        return budget[cls]
    if cls in MAP_LIKE and "map" in budget:
        return budget["map"]
    return budget.get("other")


def _finite(a) -> bool:
    try:
        return math.isfinite(math.fsum(a)) if len(a) < 4096 else math.isfinite(sum(a))
    except (OverflowError, ValueError):
        return False


class _Lazy:
    """Decode the geometries of a DFF once, on first use (``None`` when they cannot be decoded)."""

    def __init__(self, data: bytes):
        self.data = data
        self._meshes = None
        self.error: str | None = None
        self._done = False

    @property
    def meshes(self):
        if not self._done:
            self._done = True
            try:
                self._meshes = decode_geometries(self.data)
            except FormatError as e:
                self.error = str(e)
        return self._meshes


def check_dff(c: Collector, label: str, data: bytes, d: ModelDef | None, *, archive: str | None = None,
              loose: bool = False, guess: bool = True, ix=None) -> DffFacts | None:
    """Run the ``dff.*`` rules (and ``col.*`` on an embedded collision, the semantic rules of the model
    class) on one DFF blob. ``ix``: an :class:`~satk.lint.link.IndexView` for reference lookups."""
    stem = label.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
    try:
        info = scan_dff(data)
    except FormatError as e:
        c.add("dff.parse", label, err=str(e))
        return None
    cls = model_class(c.rules.classes, d, info, guess=guess, archive=archive)
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
        if cls in PRELIT and not g.rw_flags & (GEO_PRELIT | GEO_NORMALS) and g.tris:
            c.add("dff.prelight_missing", label, geom=g.idx, cls=cls)
        if cls in norm_classes and not g.rw_flags & GEO_NORMALS and g.tris:
            c.add("dff.normals_missing", label, geom=g.idx, cls=cls)
    if cls in PRELIT and not info.flags & F_NIGHT and any(g.rw_flags & GEO_PRELIT for g in info.geoms):
        c.add("dff.night_missing", label, cls=cls)
    if info.bbox is not None:
        bb = info.bbox
        r = max(info.bsphere[3] if info.bsphere else 0.0, math.dist(bb[:3], bb[3:]) / 2)
        if math.isfinite(r) and r > float(c.rules.param("dff.bounds_size", "max_radius", 3000.0)):
            c.add("dff.bounds_size", label, r=round(r, 1))
    budget = c.rules.param("dff.tris_budget", "budget", {}) or {}
    lim = class_budget(budget, cls)
    c.seen("dff.tris_budget")
    if lim is not None and info.tris > int(lim):
        c.add("dff.tris_budget", label, tris=info.tris, limit=int(lim), cls=cls)
    mbudget = c.rules.param("dff.materials_budget", "budget", {}) or {}
    mlim = class_budget(mbudget, cls)
    c.seen("dff.materials_budget")
    if mlim is not None and len(info.materials) > int(mlim):
        c.add("dff.materials_budget", label, n=len(info.materials), limit=int(mlim), cls=cls)
    lazy = _Lazy(data)
    if not native and any(c.on(r) for r in ("dff.bad_vertex", "dff.uv_range", "dff.prelight_black", "dff.bsphere")):
        _deep(c, label, lazy, info, cls)
    emb_blob = None
    if info.flags & F_COL:
        try:
            emb = find_embedded_col(data)
        except FormatError:
            emb = None
        if emb is not None:
            blob = data[emb[0]:emb[0] + emb[1]]
            if blob[:4] in _COL_MAGIC:
                emb_blob = blob
                check_col(c, label + "#col", blob, embedded=True)
            else:
                c.add("dff.embedded_col", label, magic=blob[:4].hex())
    if not native and cls in ("cars", "peds", "weap"):
        from .semantics import check_semantics

        check_semantics(c, label, data, info, cls, d, lazy, emb_blob, ix)
    tex = tuple(m.texture.lower() for m in info.materials if m.texture)
    alpha = any((m.rgba & 0xFF) < 255 for m in info.materials)
    return DffFacts(stem, label, archive, loose, tex, tuple(info.bbox) if info.bbox else None, cls, alpha)


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


def _deep(c: Collector, label: str, lazy: _Lazy, info: DffInfo, cls: str) -> None:
    """Checks on decoded vertex arrays: NaN/inf, UV range, all-black prelight."""
    meshes = lazy.meshes
    if meshes is None:
        c.add("dff.parse", label, err=lazy.error or "cannot decode the geometries")
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
            c.seen("dff.bsphere")
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
        if cls in PRELIT and p is not None and len(p) >= 4 and g.tris:
            if max(p[0::4]) == 0 and max(p[1::4]) == 0 and max(p[2::4]) == 0:
                c.add("dff.prelight_black", label, geom=g.idx, verts=len(p) // 4)
