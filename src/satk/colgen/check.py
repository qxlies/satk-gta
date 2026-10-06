"""Structural checks of collision models (``satk col check``, ``satk.colgen.check``). Stdlib only.

Every model of a ``.col`` (or a vehicle DFF's embedded COL) is decoded with :mod:`satk.rw.col` and checked:

==================  =====  =================================================================================
check               sev    meaning
==================  =====  =================================================================================
parse               error  the record cannot be decoded
name                error  empty name or longer than 21 characters (the game matches collisions by name)
index               error  a face points past the vertex list
surface             error  a surface id above 178 (``RAIL_TRACK``) on a face, box or sphere
box_inverted        error  a box with min > max on some axis
sphere_radius       error  a sphere with radius <= 0
bounds              error  a vertex, box or sphere sticks out of the bounding box by > 0.5 m (warn: > 1 cm)
bsphere             error  ... or out of the bounding sphere by > 0.5 m (warn: > 1 cm)
face_groups         error  face groups that do not cover their faces (> 2 cm) / point past the face list
limit               warn   more faces/vertices than any vanilla model (5191 / 3408): slow collision
degenerate          warn   faces with a repeated vertex or no area (they never collide)
no_face_groups      warn   more than 80 faces without face groups (every face is tested; vanilla always groups)
flags               warn   the flags word disagrees with the content (0x02 primitives, 0x08 groups, 0x10 shadow)
shadow_open         warn   the shadow mesh has border edges (shadow volumes need a closed mesh)
shadow_winding      warn   the closed shadow mesh is turned inside out for the engine's face normal
duplicate           info   faces with the same three vertices
unused_vertices     info   vertices no face uses
empty               info   no spheres, boxes or faces (bounds only, like vanilla LOD models)
==================  =====  =================================================================================

The engine's face normal is ``(C - A) x (B - A)`` (``CColTrianglePlane``), so a closed mesh written for the
engine has a *negative* signed volume in RenderWare's counter-clockwise convention.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

from ..rw import col as COL

__all__ = ["Finding", "ModelReport", "check_blob", "check_model", "SEVERITIES", "VANILLA_MAX_FACES",
           "VANILLA_MAX_VERTS"]

SEVERITIES = ("info", "warn", "error")
VANILLA_MAX_FACES = 5191
VANILLA_MAX_VERTS = 3408
MAX_SURFACE = 178
_TOL = 0.01
_BAD = 0.5                     # like the lint rule col.outside_bounds: vanilla has a few cm-level misses
_GROUP_TOL = 0.02              # group boxes are floats, faces int16 / 128: vanilla misses by up to 1 cm


@dataclass
class Finding:
    model: str
    sev: str
    check: str
    msg: str

    def row(self) -> list:
        return [self.model, self.sev, self.check, self.msg]


@dataclass
class ModelReport:
    name: str
    version: int
    spheres: int = 0
    boxes: int = 0
    verts: int = 0
    faces: int = 0
    shadow_faces: int = 0
    surfaces: Counter = field(default_factory=Counter)
    findings: list[Finding] = field(default_factory=list)

    def add(self, sev: str, check: str, msg: str) -> None:
        self.findings.append(Finding(self.name, sev, check, msg))

    def worst(self) -> str:
        if not self.findings:
            return "ok"
        return max((f.sev for f in self.findings), key=SEVERITIES.index)


def _verts(m: COL.ColModel, shadow: bool = False) -> list[tuple[float, float, float]]:
    vs = m.shadow_vertices if shadow else m.vertices
    if m.version == 1:
        return [tuple(map(float, p)) for p in vs]
    return [(p[0] / 128.0, p[1] / 128.0, p[2] / 128.0) for p in vs]


def _face_idx(m: COL.ColModel, shadow: bool = False) -> list[tuple[int, int, int]]:
    fs = m.shadow_faces if shadow else m.faces
    return [(int(f[0]), int(f[1]), int(f[2])) for f in fs]


def _face_mats(m: COL.ColModel) -> list[int]:
    if m.version == 1:
        return [int(f[3][0]) for f in m.faces]
    return [int(f[3]) for f in m.faces]


def _cross(u, v):
    return (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def check_model(m: COL.ColModel) -> ModelReport:
    """Run every check on one decoded model."""
    r = ModelReport(m.name or "#?", m.version, len(m.spheres), len(m.boxes), len(m.vertices), len(m.faces),
                    len(m.shadow_faces))
    if not m.name:
        r.add("error", "name", "empty model name: the game cannot match it to a model")
    elif len(m.name) > 21:
        r.add("error", "name", f"name {m.name!r} is {len(m.name)} characters (max 21)")
    V = _verts(m)
    F = _face_idx(m)
    mats = _face_mats(m)
    for s in mats:
        r.surfaces[s] += 1
    for _c, _r, s in m.spheres:
        r.surfaces[int(s[0])] += 1
    for _lo, _hi, s in m.boxes:
        r.surfaces[int(s[0])] += 1
    bad_s = sorted(s for s in r.surfaces if s > MAX_SURFACE)
    if bad_s:
        r.add("error", "surface", f"surface id(s) {bad_s[:5]} above {MAX_SURFACE} (RAIL_TRACK)")
    nv = len(V)
    bad_idx = sum(1 for f in F if max(f) >= nv)
    if bad_idx:
        r.add("error", "index", f"{bad_idx} face(s) point past the {nv} vertices")
    for i, (lo, hi, _s) in enumerate(m.boxes):
        ax = "".join(a for a, x0, x1 in zip("xyz", lo, hi) if x0 > x1)
        if ax:
            r.add("error", "box_inverted", f"box {i}: min > max on {ax}")
    for i, (_c, rad, _s) in enumerate(m.spheres):
        if not rad > 0:
            r.add("error", "sphere_radius", f"sphere {i}: radius {rad:.3f}")
    # bounds
    pts = [V[i] for i in sorted({i for f in F for i in f if i < nv})]
    for lo, hi, _s in m.boxes:
        pts += [tuple(lo), tuple(hi)]
    worst, what = 0.0, ""
    bmin, bmax = m.bmin, m.bmax
    for p in pts:
        d = max(max(bmin[k] - p[k], p[k] - bmax[k]) for k in range(3))
        if d > worst:
            worst, what = d, "a vertex/box corner"
    for i, (c, rad, _s) in enumerate(m.spheres):
        d = max(max(bmin[k] - (c[k] - rad), (c[k] + rad) - bmax[k]) for k in range(3))
        if d > worst:
            worst, what = d, f"sphere {i}"
    if worst > _TOL:
        r.add("error" if worst > _BAD else "warn", "bounds", f"{what} sticks {worst:.2f} m out of the bounding box")
    worst, what = 0.0, ""
    for p in pts:
        d = math.dist(p, m.center) - m.radius
        if d > worst:
            worst, what = d, "a vertex/box corner"
    for lo, hi, _s in m.boxes:
        for x in (lo[0], hi[0]):
            for y in (lo[1], hi[1]):
                for z in (lo[2], hi[2]):
                    d = math.dist((x, y, z), m.center) - m.radius
                    if d > worst:
                        worst, what = d, "a box corner"
    for i, (c, rad, _s) in enumerate(m.spheres):
        d = math.dist(c, m.center) + rad - m.radius
        if d > worst:
            worst, what = d, f"sphere {i}"
    if worst > _TOL:
        r.add("error" if worst > _BAD else "warn", "bsphere", f"{what} sticks {worst:.2f} m out of the bounding sphere")
    # limits and mesh quality
    if len(F) > VANILLA_MAX_FACES or nv > VANILLA_MAX_VERTS:
        r.add("warn", "limit", f"{len(F)} faces / {nv} vertices: more than any vanilla model "
                               f"({VANILLA_MAX_FACES} / {VANILLA_MAX_VERTS}); split it or decimate (satk col gen --mode mesh)")
    degen = 0
    seen: Counter = Counter()
    for f in F:
        if len(set(f)) < 3 or max(f) >= nv:
            degen += 1
            continue
        a, b, c = V[f[0]], V[f[1]], V[f[2]]
        n = _cross(_sub(b, a), _sub(c, a))
        if n[0] * n[0] + n[1] * n[1] + n[2] * n[2] < 1e-12:
            degen += 1
        seen[tuple(sorted(f))] += 1
    if degen:
        r.add("warn", "degenerate", f"{degen} face(s) with a repeated vertex or no area")
    dups = sum(c - 1 for c in seen.values() if c > 1)
    if dups:
        r.add("info", "duplicate", f"{dups} duplicate face(s)")
    unused = nv - len({i for f in F for i in f if i < nv})
    if unused > 0 and nv:
        r.add("info", "unused_vertices", f"{unused} of {nv} vertices are not used by any face")
    if m.version >= 2 and len(F) >= COL.FG_MIN_FACES and not m.groups:
        r.add("warn", "no_face_groups", f"{len(F)} faces without face groups (vanilla groups every model above 80)")
    for i, (lo, hi, s, e) in enumerate(m.groups):
        if not (0 <= s <= e < len(F)):
            r.add("error", "face_groups", f"group {i}: faces {s}..{e} outside 0..{len(F) - 1}")
            break
        out = 0.0
        for f in F[s:e + 1]:
            for k in f:
                if k < nv:
                    p = V[k]
                    out = max(out, max(max(lo[j] - p[j], p[j] - hi[j]) for j in range(3)))
        if out > _GROUP_TOL:
            r.add("error", "face_groups", f"group {i}: its faces stick {out:.2f} m out of its box")
            break
    if m.version >= 2 and m.flags != COL.canonical_flags(m):
        r.add("warn", "flags", f"flags 0x{m.flags:02x}, content says 0x{COL.canonical_flags(m):02x}")
    if m.shadow_faces:
        _check_shadow(m, r)
    if not (m.spheres or m.boxes or m.faces):
        r.add("info", "empty", "no spheres, boxes or faces: bounds only")
    return r


def _check_shadow(m: COL.ColModel, r: ModelReport) -> None:
    SV = _verts(m, True)
    SF = _face_idx(m, True)
    if any(max(f) >= len(SV) for f in SF):
        r.add("error", "index", "a shadow face points past the shadow vertices")
        return
    uses: Counter = Counter()
    for a, b, c in SF:
        for x, y in ((a, b), (b, c), (c, a)):
            uses[(x, y) if x < y else (y, x)] += 1
    border = sum(1 for n in uses.values() if n % 2)
    if border:
        r.add("warn", "shadow_open", f"shadow mesh has {border} border edge(s): not closed")
        return
    vol = 0.0
    for a, b, c in SF:
        pa, pb, pc = SV[a], SV[b], SV[c]
        vol += (pa[0] * (pb[1] * pc[2] - pb[2] * pc[1]) - pa[1] * (pb[0] * pc[2] - pb[2] * pc[0])
                + pa[2] * (pb[0] * pc[1] - pb[1] * pc[0])) / 6.0
    if vol > 1e-6:
        r.add("warn", "shadow_winding", "closed shadow mesh is inside out for the engine's normal (C-A)x(B-A)")


def check_blob(blob: bytes) -> tuple[list[ModelReport], list[str]]:
    """Reports of every model in a collision blob, plus file-level problems."""
    problems: list[str] = []
    try:
        recs, tail = COL.split_models(blob)
    except COL.ColError as e:
        return [], [str(e)]
    out = []
    names: Counter = Counter()
    for i, rec in enumerate(recs):
        try:
            m = COL.decode_model(rec)
        except COL.ColError as e:
            rep = ModelReport(f"#{i}", 0)
            rep.add("error", "parse", str(e))
            out.append(rep)
            continue
        names[m.name.lower()] += 1
        out.append(check_model(m))
    for n, k in sorted(names.items()):
        if k > 1:
            problems.append(f"DUPLICATE_NAME: {n} appears {k} times (the game takes the first)")
    if tail.strip(b"\0"):
        problems.append(f"TRAILING_DATA: {len(tail)} bytes after the last model are not zero padding")
    return out, problems
