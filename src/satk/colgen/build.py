"""Collision models from render meshes (``satk.colgen.build``).

:func:`generate` turns one DFF into a :class:`satk.rw.col.ColModel` (written by the existing COL writer):

=========  ===========================================================================================
mode       what the collision is made of
=========  ===========================================================================================
box        one box = the bounding box of the colliding parts
boxes      boxes from a voxel decomposition (per part shape, merged to ``max_prims``), hugging the mesh
hull       one outer convex hull with at most ``max_faces`` triangles (contains the mesh exactly)
mesh       the render mesh welded to the COL grid and decimated (QEM) to ``max_faces``
spheres    inscribed spheres covering the body (vehicle style: mirrored pairs, ``CAR`` surface and the
           damage piece of the part under each sphere)
bounds     no primitives: only the bounding box/sphere (what vanilla LOD models carry)
auto       vehicles -> spheres + shadow; big or detailed models (> 25 m or > 2000 triangles) -> mesh;
           anything else -> hull
=========  ===========================================================================================

``shadow`` adds a COL3 shadow mesh: the closed boundary of the solid voxels (wheels included), smoothed
and decimated to ``shadow_faces``. Bounds always cover the colliding render geometry and every primitive
(vanilla COL bounds double as the model's culling box). Faces are written with the winding the engine
expects: ``CColTrianglePlane`` takes the normal as ``(C - A) x (B - A)``, the opposite of RenderWare's
render winding.
"""

from __future__ import annotations

import fnmatch
import math
from collections import Counter
from dataclasses import dataclass, field

from ._np import np

from ..core.errors import SatkError
from ..formats.rw import FormatError
from ..rw import col as COL
from .geom import GRID, RenderMesh, render_mesh, tri_normals, weld
from .nearest import nearest_tri, outside_distance
from .surface import SurfaceTable

__all__ = ["GenOptions", "GenResult", "generate", "MODES", "DEFAULT_BUDGETS", "VEHICLE_PIECES"]

MODES = ("auto", "box", "boxes", "hull", "mesh", "spheres", "bounds")
#: Default budgets: faces for hull/mesh, primitives for boxes/spheres, shadow faces.
DEFAULT_BUDGETS = {"hull": 64, "mesh": 1000, "boxes": 8, "spheres": 20, "shadow": 300}
#: Voxel cells along the longest axis per use (coarser cells give rounder, fuller sphere sets).
RESOLUTION = {"boxes": 32, "spheres": 24, "shadow": 40}
#: Damage piece byte of vehicle spheres by the part under them (vanilla: 3 front, 4 rear, 5-8 doors).
VEHICLE_PIECES = {"bump_front": 3, "bump_rear": 4, "door_lf": 5, "door_rf": 6, "door_lr": 7, "door_rr": 8}
#: Brightness byte of boxes and spheres in vanilla collisions (day 11 / night 11).
PRIM_LIGHTING = 187
CAR = 63
_MAX_COORD = 32767 / 128.0


@dataclass
class GenOptions:
    mode: str = "auto"
    surface: str = "auto"
    rules: list[tuple[str, int]] = field(default_factory=list)   # (texture glob, surface id) first
    shadow: bool | None = None                                   # None = vehicles only (mode auto)
    max_faces: int | None = None
    max_prims: int | None = None
    shadow_faces: int | None = None
    version: int = 3
    exclude: list[str] = field(default_factory=list)
    lighting: bool = True
    resolution: int | None = None                                # voxel cells (None = per mode)


@dataclass
class GenResult:
    model: COL.ColModel
    mode: str
    surfaces: Counter            # surface id -> faces/prims
    how: Counter                 # table/txd/keyword/default/rule/fixed -> faces/prims
    fit: dict                    # containment / deviation numbers
    notes: list[str]
    render_tris: int


# ----------------------------------------------------------------------------- surfaces
class _Surfaces:
    """Surface id per render triangle (auto table, rules or a fixed value)."""

    def __init__(self, rm: RenderMesh, opts: GenOptions, st: SurfaceTable, txd: str | None):
        self.rm = rm
        n = rm.tris
        self.sid = np.zeros(n, dtype=np.int64)
        self.how: list[str] = ["default"] * n
        fixed = None if opts.surface.strip().lower() == "auto" else st.id_of(opts.surface)
        cache: dict[int, tuple[int, str]] = {}
        for t in range(-1, len(rm.textures)):
            name = rm.textures[t] if t >= 0 else None
            hit = None
            if name is not None:
                for pat, sid in opts.rules:
                    if fnmatch.fnmatchcase(name, pat):
                        hit = (sid, "rule")
                        break
            if hit is None:
                hit = (fixed, "fixed") if fixed is not None else st.lookup(name, txd)
            cache[t] = hit
        for i, t in enumerate(rm.tex.tolist()):
            s, how = cache[t]
            self.sid[i] = s
            self.how[i] = how.split(":", 1)[0]
        self.area = 0.5 * np.linalg.norm(tri_normals(rm.P, rm.T), axis=1)

    def dominant(self, mask: np.ndarray) -> tuple[int, str]:
        """Surface covering most render area among the triangles in ``mask`` (all when none)."""
        if not mask.any():
            mask = np.ones(len(self.sid), dtype=bool)
        w = np.bincount(self.sid[mask], weights=self.area[mask] + 1e-12)
        s = int(np.argmax(w))
        hows = Counter(h for h, ok, si in zip(self.how, mask.tolist(), self.sid.tolist()) if ok and si == s)
        return s, (hows.most_common(1)[0][0] if hows else "default")


# ----------------------------------------------------------------------------- helpers
def _quant(P: np.ndarray, *, outward_from: np.ndarray | None = None) -> np.ndarray:
    """Model-space metres -> int16 / 128 grid (outward from a centre when given: keeps containment)."""
    x = P / GRID
    if outward_from is not None:
        c = outward_from / GRID
        q = np.where(x >= c, np.ceil(x - 1e-9), np.floor(x + 1e-9))
    else:
        q = np.rint(x)
    if np.abs(q).max(initial=0) > 32767:
        worst = float(np.abs(P).max())
        raise SatkError("BAD_PARAMS", f"a collision vertex is {worst:.1f} m from the model origin; COL2/3 store "
                                      f"at most +-{_MAX_COORD:.0f} m",
                        hint="split the model, move its origin closer, or use --mode box/boxes/spheres "
                             "(primitives are floats)")
    return q.astype(np.int64)


def _clean_faces(Q: np.ndarray, T: np.ndarray) -> np.ndarray:
    """Indices of faces that are still triangles after quantisation."""
    a, b, c = Q[T[:, 0]], Q[T[:, 1]], Q[T[:, 2]]
    n = np.cross((b - a).astype(np.float64), (c - a).astype(np.float64))
    return np.nonzero(np.linalg.norm(n, axis=1) > 0.5)[0]


def _game_faces(T: np.ndarray) -> np.ndarray:
    """Counter-clockwise-from-outside triangles -> the engine's winding (A, C, B)."""
    return T[:, [0, 2, 1]]


def _piece(c: np.ndarray, boxes: dict[str, tuple]) -> int:
    x, y = float(c[0]), float(c[1])
    for part in ("bump_front", "bump_rear"):
        b = boxes.get(part)
        if b is not None and b[0][1] - 0.05 <= y <= b[1][1] + 0.05:
            return VEHICLE_PIECES[part]
    for part in ("door_lf", "door_rf", "door_lr", "door_rr"):
        b = boxes.get(part)
        if b is None:
            continue
        side = (b[0][0] + b[1][0]) / 2
        if b[0][1] <= y <= b[1][1] and side * x > 0:
            return VEHICLE_PIECES[part]
    return 0


def _choose_mode(rm: RenderMesh) -> str:
    if rm.vehicle:
        return "spheres"
    ext = float((rm.P.max(axis=0) - rm.P.min(axis=0)).max())
    if ext > 25.0 or rm.tris > 2000:
        return "mesh"
    return "hull"


# ----------------------------------------------------------------------------- generate
def generate(dff: bytes, name: str, *, sec: str | None = None, txd: str | None = None,
             opts: GenOptions | None = None, table: SurfaceTable | None = None) -> GenResult:
    """One collision model for one DFF (see the module doc)."""
    opts = opts or GenOptions()
    if opts.mode not in MODES:
        raise SatkError("BAD_PARAMS", f"unknown mode {opts.mode!r}", did_you_mean=[m for m in MODES if m[0] == opts.mode[:1]])
    if opts.version not in (2, 3):
        raise SatkError("BAD_PARAMS", "version must be 2 or 3 (COL2/COL3)")
    st = table or SurfaceTable.load()
    rm = render_mesh(dff, name, sec, exclude=opts.exclude)
    mode = _choose_mode(rm) if opts.mode == "auto" else opts.mode
    shadow = opts.shadow if opts.shadow is not None else (opts.mode == "auto" and rm.vehicle)
    notes: list[str] = []
    excl = [x for x in rm.skipped if x.endswith("(excluded)") or x.endswith("(excluded textures)")]
    if excl:
        notes.append("excluded " + ", ".join(excl[:8]) + (" ..." if len(excl) > 8 else ""))
    surf = _Surfaces(rm, opts, st, txd)
    m = COL.ColModel(version=opts.version, name=name[:21])
    if len(name) > 21:
        notes.append(f"name cut to 21 characters: {name[:21]}")
    stats_s: Counter = Counter()
    stats_h: Counter = Counter()
    fit: dict = {}
    P, T = rm.P, rm.T
    lo, hi = P.min(axis=0), P.max(axis=0)

    def prim_surface(mask: np.ndarray) -> tuple[int, int, int, int]:
        s, how = surf.dominant(mask)
        stats_s[s] += 1
        stats_h[how] += 1
        return (int(s), 0, PRIM_LIGHTING, 0)

    if mode == "box":
        thin = np.maximum(hi - lo, 2 * GRID)
        c = (lo + hi) / 2
        blo, bhi = c - thin / 2, c + thin / 2
        m.boxes.append((tuple(map(float, blo)), tuple(map(float, bhi)), prim_surface(np.ones(rm.tris, bool))))
        fit["outside"] = 0.0
    elif mode in ("boxes", "spheres"):
        from . import voxel

        budget = opts.max_prims or DEFAULT_BUDGETS[mode]
        vx = voxel.voxelize(P, T, cells=opts.resolution or RESOLUTION[mode])
        cen = P[T].mean(axis=1)
        if mode == "boxes":
            bx = voxel.boxes(vx, budget)
            for b in bx:
                inside = ((cen >= b[:3] - 1e-6) & (cen <= b[3:] + 1e-6)).all(axis=1)
                m.boxes.append((tuple(map(float, b[:3])), tuple(map(float, b[3:])), prim_surface(inside)))
            out = _outside_boxes(P, bx)
            fit["outside"] = round(out, 3)
        else:
            sp = voxel.spheres(vx, budget, mirror_x=rm.vehicle)
            solid_c = vx.centres(vx.solid)
            covered = np.zeros(len(solid_c), dtype=bool)
            for x, y, z, r in sp:
                covered |= ((solid_c - (x, y, z)) ** 2).sum(axis=1) <= r * r + 1e-9
                if rm.vehicle and opts.surface.strip().lower() == "auto" and not opts.rules:
                    s = (CAR, _piece(np.array([x, y, z]), rm.part_boxes), PRIM_LIGHTING, 0)
                    stats_s[CAR] += 1
                    stats_h["vehicle"] += 1
                else:
                    inside = ((cen - (x, y, z)) ** 2).sum(axis=1) <= (r + vx.cell) ** 2
                    s = prim_surface(inside)
                m.spheres.append(((float(x), float(y), float(z)), float(r), s))
            fit["coverage"] = round(float(covered.mean()) if len(covered) else 0.0, 3)
            if len(sp) and fit["coverage"] < 0.35:
                notes.append("spheres cover little of this shape (thin or open mesh): try --mode hull or boxes")
    elif mode == "hull":
        from .hull import outer_hull

        budget = opts.max_faces or DEFAULT_BUDGETS["hull"]
        h = outer_hull(P[np.unique(T)], budget, normals=tri_normals(P, T))
        centre = h.P.mean(axis=0)
        Q = _quant(h.P, outward_from=centre)
        keep = _clean_faces(Q, h.T)
        HT = h.T[keep]
        fc = (h.P[HT[:, 0]] + h.P[HT[:, 1]] + h.P[HT[:, 2]]) / 3
        j, _d = nearest_tri(fc, P, T)
        _set_mesh(m, Q, HT, j, surf, rm, st, opts, stats_s, stats_h)
        fit["dev"] = round(h.max_dev, 3)
        fit["exact"] = h.exact
        fit["outside"] = round(outside_distance(P[np.unique(T)], Q * GRID, HT), 3)
    elif mode == "mesh":
        from .decimate import cluster, decimate

        Pw, Tw, src = weld(P, T)
        Pref = Pw[np.unique(Tw)] if len(Tw) else Pw              # what a COL can hold: snapped, no slivers
        budget = opts.max_faces or DEFAULT_BUDGETS["mesh"]
        attr = surf.sid[src]
        if len(Tw) > max(20000, 8 * budget):
            Pw, Tw, s2 = cluster(Pw, Tw, 4 * budget)
            src, attr = src[s2], attr[s2]
            notes.append(f"pre-reduced by vertex clustering to {len(Tw)} faces")
        P2, T2, s3 = decimate(Pw, Tw, budget, attr=attr)
        src = src[s3]
        Q = _quant(P2)
        keep = _clean_faces(Q, T2)
        _set_mesh(m, Q, T2[keep], src[keep], surf, rm, st, opts, stats_s, stats_h)
        if len(T2[keep]) > budget:
            notes.append(f"{len(T2[keep])} faces: the mesh could not be reduced to {budget} without folding")
        fit["dev"] = round(_deviation(Pref, Q * GRID, T2[keep]), 3)
    if shadow:
        if opts.version < 3:
            notes.append("no shadow mesh: COL2 has none (use --version 3)")
        else:
            _shadow(m, dff, name, sec, opts, rm, st, notes)
    if m.faces:
        COL.face_groups(m)
    _bounds(m, rm)
    m.flags = COL.canonical_flags(m)
    return GenResult(m, mode, stats_s, stats_h, fit, notes, rm.tris)


def _set_mesh(m: COL.ColModel, Q: np.ndarray, T: np.ndarray, src: np.ndarray, surf: _Surfaces, rm: RenderMesh,
              st: SurfaceTable, opts: GenOptions, stats_s: Counter, stats_h: Counter) -> None:
    used = np.unique(T) if len(T) else np.zeros(0, dtype=np.int64)
    remap = np.full(len(Q), -1, dtype=np.int64)
    remap[used] = np.arange(len(used))
    m.vertices = [tuple(int(c) for c in q) for q in Q[used]]
    G = _game_faces(remap[T]) if len(T) else T
    faces = []
    for (a, b, c), j in zip(G.tolist(), src.tolist()):
        s = int(surf.sid[j])
        light = st.light(float(rm.day[j]), float(rm.night[j])) if opts.lighting else 0xFF
        faces.append((a, b, c, s, light))
        stats_s[s] += 1
        stats_h[surf.how[j]] += 1
    m.faces = faces


def _shadow(m: COL.ColModel, dff: bytes, name: str, sec: str | None, opts: GenOptions, rm: RenderMesh,
            st: SurfaceTable, notes: list[str]) -> None:
    from . import voxel
    from .decimate import decimate

    try:
        full = render_mesh(dff, name, sec, exclude=opts.exclude, wheels=True, preview=True)
    except FormatError:                                    # nothing more than the colliding parts
        full = rm
    vx = voxel.voxelize(full.P, full.T, cells=opts.resolution or RESOLUTION["shadow"])
    SP, STr = voxel.surface(vx, smooth=4)
    if not len(STr):
        notes.append("no shadow mesh: the model has no volume")
        return
    budget = opts.shadow_faces or DEFAULT_BUDGETS["shadow"]
    P2, T2, _src = decimate(SP, STr, budget, closed_pairs=True)
    Q = _quant(P2)
    keep = _clean_faces(Q, T2)
    T2 = T2[keep]
    used = np.unique(T2)
    remap = np.full(len(Q), -1, dtype=np.int64)
    remap[used] = np.arange(len(used))
    j, _d = nearest_tri((P2[T2[:, 0]] + P2[T2[:, 1]] + P2[T2[:, 2]]) / 3, full.P, full.T)
    m.shadow_vertices = [tuple(int(c) for c in q) for q in Q[used]]
    G = _game_faces(remap[T2])
    m.shadow_faces = [(a, b, c, 0, st.light(float(full.day[k]), float(full.night[k])) if opts.lighting else 0xFF)
                      for (a, b, c), k in zip(G.tolist(), j.tolist())]


def _bounds(m: COL.ColModel, rm: RenderMesh) -> None:
    pts = [rm.P]
    if m.vertices:
        pts.append(np.asarray(m.vertices, dtype=np.float64) * GRID)
    for lo, hi, _s in m.boxes:
        pts.append(np.asarray([lo, hi], dtype=np.float64))
    for c, r, _s in m.spheres:
        cc = np.asarray(c)
        pts.append(np.asarray([cc - r, cc + r]))
    A = np.concatenate(pts)
    lo, hi = A.min(axis=0), A.max(axis=0)
    c = (lo + hi) / 2
    r = float(np.sqrt(((A - c) ** 2).sum(axis=1)).max())
    for (sc, sr, _s) in m.spheres:
        r = max(r, math.dist(tuple(c), sc) + sr)
    for blo, bhi, _s in m.boxes:
        for x in (blo[0], bhi[0]):
            for y in (blo[1], bhi[1]):
                for z in (blo[2], bhi[2]):
                    r = max(r, math.dist(tuple(c), (x, y, z)))
    m.bmin = tuple(float(v) for v in lo)
    m.bmax = tuple(float(v) for v in hi)
    m.center = tuple(float(v) for v in c)
    m.radius = r * (1 + 1e-6) + 1e-4


# ----------------------------------------------------------------------------- fit measures
def _outside_boxes(P: np.ndarray, bx: np.ndarray) -> float:
    """How far the farthest render vertex lies outside every box (0 = all inside)."""
    if not len(bx):
        return float("inf")
    d = np.maximum(np.maximum(bx[None, :, :3] - P[:, None, :], P[:, None, :] - bx[None, :, 3:]), 0.0)
    return float(np.sqrt((d ** 2).sum(axis=2)).min(axis=1).max())


def _deviation(P: np.ndarray, V: np.ndarray, T: np.ndarray) -> float:
    """Largest distance from a render vertex to the collision mesh (sampled: at most 4000 vertices)."""
    if not len(T):
        return float("inf")
    idx = np.arange(len(P))
    if len(idx) > 4000:
        idx = idx[:: int(math.ceil(len(idx) / 4000))]
    _j, d = nearest_tri(P[idx], V, T)
    return float(d.max())
