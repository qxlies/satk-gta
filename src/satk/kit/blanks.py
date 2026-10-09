"""Body blanks (``kit.blank``): clean low-poly quad base meshes that an agent shapes instead of typing cross-sections.

A blank is OUR parametric construction from class dimensions and anchors (length, width, height, wheelbase, track,
wheel diameter, tier); no vanilla vertex, face or pixel is read (``--like`` only supplies numbers: dimensions, wheel
dummies, wheel scale). The kinds (``data/kit/blanks/kinds.json``):

* ``automobile`` - a half body (x >= 0, MIRROR modifier) through stations along the length with loops at the wheel
  arches, belt line, pillars, nose and tail; the vanilla part boundaries (chassis, bonnet, boot, doors, bumpers,
  windscreen) are a face attribute, the shell has recessed glass, lamps, wheel houses and door jambs, seats and
  underbody boxes, mirrors and an exhaust. Bodies: sedan, coupe, sports, suv, van; tiers set the density;
* ``bike``, ``boat``, ``heli``, ``plane`` - a lofted hull or fuselage with the kind's typical extras (wheels and fork,
  console, skids and rotor, wing and tail);
* ``prop_box``, ``prop_cyl``, ``building_box`` - chamfered boxes and cylinders, a building with storey and bay loops,
  plinth and cornice; tiling UVs in metres.

Stdlib only (the Blender side imports it): :func:`blank_plan` turns arguments and ``--like`` numbers into a JSON plan,
:func:`mesh_spec` turns a plan into pieces ``{verts, faces, role, part, uv, seam}`` (faces are quads and n-gons wound
outwards; ``part`` indexes ``part_names``; the piece is the half x >= 0 when ``mirror``).

Example::

    from satk.kit import blanks
    plan = blanks.blank_plan("automobile", name="mycar", tier="sa_plus")     # numbers only, no game data needed
    spec = blanks.mesh_spec(plan)
    [(p["name"], blanks.tri_count(p["faces"])) for p in spec["pieces"]]       # body, interior, exhaust (half meshes)
"""

from __future__ import annotations

import functools
import math
from typing import Any

from ..core import resources
from ..core.errors import SatkError

__all__ = ["KINDS", "TIERS", "kinds", "blank_plan", "mesh_spec", "tri_count", "check_spec", "part_slot"]

KINDS = ("automobile", "bike", "boat", "heli", "plane", "prop_box", "prop_cyl", "building_box")
TIERS = ("vanilla", "sa_plus")
_EPS = 1e-9


# --------------------------------------------------------------------------- data


@functools.lru_cache(maxsize=None)
def _data(name: str) -> dict:
    return resources.read_json("kit", "blanks", name)


def kinds() -> dict:
    """The catalog ``data/kit/blanks/kinds.json`` (per kind: summary, class dimensions, parts, notes)."""
    return _data("kinds.json")


def _kind(kind: str) -> dict:
    k = str(kind or "").strip().lower().replace("-", "_")
    alias = {"car": "automobile", "box": "prop_box", "cylinder": "prop_cyl", "cyl": "prop_cyl",
             "building": "building_box", "helicopter": "heli", "aeroplane": "plane", "motorbike": "bike"}
    k = alias.get(k, k)
    ks = kinds()["kinds"]
    if k not in ks:
        import difflib

        raise SatkError("NOT_FOUND", f"no blank kind {kind!r}", hint="satk kit blank (lists the kinds)",
                        did_you_mean=difflib.get_close_matches(k, list(ks), n=3, cutoff=0.4))
    return dict(ks[k], name=k)


# --------------------------------------------------------------------------- small maths


def _r(v: float, nd: int = 5) -> float:
    x = round(float(v), nd)
    return 0.0 if x == 0 else x


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def _curve(knots: list, t: float, creases: tuple | list = ()) -> float:
    """Value at ``t`` of a Catmull-Rom spline through ``[[t, v], ...]``; tangents break at ``creases`` (a knot t)."""
    n = len(knots)
    if t <= knots[0][0]:
        return float(knots[0][1])
    if t >= knots[-1][0]:
        return float(knots[-1][1])
    i = 0
    while i < n - 2 and knots[i + 1][0] < t:
        i += 1
    t0, v0 = knots[i]
    t1, v1 = knots[i + 1]
    h = t1 - t0
    s = (t - t0) / h if h > 0 else 0.0

    def tangent(k: int, side: int) -> float:
        """Slope at knot ``k`` on its ``side`` (-1 left, +1 right)."""
        tk, vk = knots[k]
        if k == 0:
            return (knots[1][1] - vk) / max(knots[1][0] - tk, _EPS)
        if k == n - 1:
            return (vk - knots[k - 1][1]) / max(tk - knots[k - 1][0], _EPS)
        if any(abs(tk - c) < 1e-6 for c in creases):
            nb = knots[k + side]
            return (nb[1] - vk) / (nb[0] - tk)
        return (knots[k + 1][1] - knots[k - 1][1]) / (knots[k + 1][0] - knots[k - 1][0])

    m0 = tangent(i, 1) * h
    m1 = tangent(i + 1, -1) * h
    s2, s3 = s * s, s * s * s
    return (2 * s3 - 3 * s2 + 1) * v0 + (s3 - 2 * s2 + s) * m0 + (-2 * s3 + 3 * s2) * v1 + (s3 - s2) * m1


# --------------------------------------------------------------------------- mesh kernel


class Mesh:
    """Vertices, polygon faces and per-face labels. Faces wind counter-clockwise seen from outside."""

    def __init__(self) -> None:
        self.verts: list[tuple[float, float, float]] = []
        self.faces: list[tuple[int, ...]] = []
        self.role: list[str] = []
        self.part: list[str] = []
        self.uv: list[tuple] = []          # per face, one (u, v) per corner
        self.seam: set[tuple[int, int]] = set()
        self.wall: set[int] = set()        # faces that are walls of a recess or a jamb (not the shell surface)

    def v(self, x: float, y: float, z: float) -> int:
        self.verts.append((float(x), float(y), float(z)))
        return len(self.verts) - 1

    def f(self, idx, role: str, part: str, uv=None) -> int:
        self.faces.append(tuple(int(i) for i in idx))
        self.role.append(role)
        self.part.append(part)
        self.uv.append(tuple(uv) if uv is not None else ())
        return len(self.faces) - 1

    def mark_seam(self, a: int, b: int) -> None:
        self.seam.add((a, b) if a < b else (b, a))


def _sub(a: tuple, b: tuple) -> tuple:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a: tuple, b: tuple) -> tuple:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a: tuple, b: tuple) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _normal(m: Mesh, f: tuple[int, ...]) -> tuple[float, float, float]:
    """Newell normal (unnormalised) of a face."""
    nx = ny = nz = 0.0
    for k in range(len(f)):
        p, q = m.verts[f[k]], m.verts[f[(k + 1) % len(f)]]
        nx += (p[1] - q[1]) * (p[2] + q[2])
        ny += (p[2] - q[2]) * (p[0] + q[0])
        nz += (p[0] - q[0]) * (p[1] + q[1])
    return nx, ny, nz


def tri_count(faces) -> int:
    return sum(max(0, len(f) - 2) for f in faces)


def _box(m: Mesh, lo, hi, div, role: str, part: str, *, uvf=None) -> None:
    """A closed quad box ``lo..hi`` with ``div`` = (nx, ny, nz) cells; ``uvf(mesh, face, side)`` gives the corner UVs."""
    nx, ny, nz = div
    xs = [_lerp(lo[0], hi[0], i / nx) for i in range(nx + 1)]
    ys = [_lerp(lo[1], hi[1], i / ny) for i in range(ny + 1)]
    zs = [_lerp(lo[2], hi[2], i / nz) for i in range(nz + 1)]
    cache: dict = {}

    def vid(i: int, j: int, k: int) -> int:
        key = (i, j, k)
        if key not in cache:
            cache[key] = m.v(xs[i], ys[j], zs[k])
        return cache[key]

    def quad(a, b, c, d, face: str) -> None:
        m.f((a, b, c, d), role, part, uvf(m, (a, b, c, d), face) if uvf else None)

    for j in range(ny):
        for k in range(nz):
            quad(vid(0, j, k), vid(0, j, k + 1), vid(0, j + 1, k + 1), vid(0, j + 1, k), "-x")
            quad(vid(nx, j, k), vid(nx, j + 1, k), vid(nx, j + 1, k + 1), vid(nx, j, k + 1), "+x")
    for i in range(nx):
        for k in range(nz):
            quad(vid(i, 0, k), vid(i + 1, 0, k), vid(i + 1, 0, k + 1), vid(i, 0, k + 1), "-y")
            quad(vid(i, ny, k), vid(i, ny, k + 1), vid(i + 1, ny, k + 1), vid(i + 1, ny, k), "+y")
    for i in range(nx):
        for j in range(ny):
            quad(vid(i, j, 0), vid(i, j + 1, 0), vid(i + 1, j + 1, 0), vid(i + 1, j, 0), "-z")
            quad(vid(i, j, nz), vid(i + 1, j, nz), vid(i + 1, j + 1, nz), vid(i, j + 1, nz), "+z")


# --------------------------------------------------------------------------- face-region edits (kernel)


def _edges_of(m: Mesh, fids) -> dict[tuple[int, int], list[tuple[int, int, int]]]:
    """``{(lo, hi): [(face, a, b), ...]}`` directed edges (a -> b as the face winds) of the faces ``fids``."""
    out: dict = {}
    for fi in fids:
        f = m.faces[fi]
        for k in range(len(f)):
            a, b = f[k], f[(k + 1) % len(f)]
            out.setdefault((a, b) if a < b else (b, a), []).append((fi, a, b))
    return out


def _vertex_normals(m: Mesh, fids) -> dict[int, tuple[float, float, float]]:
    """Unit area-independent average normal of the faces ``fids`` at each of their vertices."""
    acc: dict[int, list[float]] = {}
    for fi in fids:
        n = _normal(m, m.faces[fi])
        ln = math.sqrt(_dot(n, n)) or 1.0
        for v in m.faces[fi]:
            a = acc.setdefault(v, [0.0, 0.0, 0.0])
            for k in range(3):
                a[k] += n[k] / ln
    out = {}
    for v, a in acc.items():
        ln = math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2]) or 1.0
        out[v] = (a[0] / ln, a[1] / ln, a[2] / ln)
    return out


def _wall_uv(uva: tuple, uvb: tuple, width: float = 0.012) -> tuple:
    """Corner UVs of a wall quad along the edge ``uva -> uvb``, ``width`` away from it in UV space (never zero area)."""
    du, dv = uvb[0] - uva[0], uvb[1] - uva[1]
    ln = math.hypot(du, dv)
    pu, pv = (-dv / ln * width, du / ln * width) if ln > 1e-9 else (0.0, width)
    return (uva, uvb, (uvb[0] + pu, uvb[1] + pv), (uva[0] + pu, uva[1] + pv))


def _move(m: Mesh, v: int, n: tuple[float, float, float], dist: float, *, axis_x: bool = True) -> int:
    """A new vertex ``dist`` from ``v`` along ``n``; a vertex on the mirror plane (x = 0) stays on it."""
    x, y, z = m.verts[v]
    dx = 0.0 if axis_x and abs(x) < 1e-9 else n[0] * dist
    return m.v(x + dx, y + n[1] * dist, z + n[2] * dist)


def _inset(m: Mesh, fids: list[int], depth: float, wall_role: str, *, axis_x: bool = True) -> list[int]:
    """Push the faces ``fids`` along their normals by ``depth`` (negative = into the body) and close the rim with walls.

    The boundary vertices are duplicated: the neighbours keep the old ones, the region moves onto the copies, and one
    quad per boundary edge joins them. Boundary edges lying on the mirror plane (x = 0, both ends) get no wall: the
    region continues across the plane. Returns the ids of the wall faces.
    """
    region = set(fids)
    nrm = _vertex_normals(m, fids)
    new = {v: _move(m, v, nrm[v], depth, axis_x=axis_x) for v in sorted(nrm)}
    old_uv = {(fi, v): m.uv[fi][k] for fi in fids for k, v in enumerate(m.faces[fi]) if m.uv[fi]}
    walls: list[int] = []
    for _key, items in sorted(_edges_of(m, fids).items()):
        inside = [it for it in items if it[0] in region]
        if len(inside) != 1:
            continue
        fi, a, b = inside[0]
        if axis_x and abs(m.verts[a][0]) < 1e-9 and abs(m.verts[b][0]) < 1e-9:
            continue
        uv = _wall_uv(old_uv.get((fi, a), (0.0, 0.0)), old_uv.get((fi, b), (0.0, 0.0)))
        walls.append(m.f((a, b, new[b], new[a]), wall_role, m.part[fi], uv))
    for fi in fids:
        m.faces[fi] = tuple(new[v] for v in m.faces[fi])
    m.wall.update(walls)
    return walls


def _jambs(m: Mesh, part: str, depth: float, role: str, into: str, *, axis_x: bool = True) -> list[int]:
    """Jamb walls around the faces of ``part`` (the opening it will leave in the shell): the boundary vertices are copied
    ``depth`` inwards along the part's normals and joined to the old ones by quads that belong to ``into``; the part
    itself stays where it is. Boundary edges on the mirror plane (x = 0) get no wall."""
    fids = [i for i, p in enumerate(m.part) if p == part]
    region = set(fids)
    # the direction into the body: the normals of the shell around each boundary vertex (both sides of the cut),
    # never of the black walls of a recessed window or lamp (they would point the jamb out of the body)
    bverts = {v for _k, items in _edges_of(m, fids).items() if sum(1 for it in items if it[0] in region) == 1
              for v in _k}
    shell = [fi for fi, f in enumerate(m.faces) if fi not in m.wall and any(v in bverts for v in f)]
    nrm = _vertex_normals(m, fids)
    nrm.update(_vertex_normals(m, shell))
    inner: dict[int, int] = {}
    walls: list[int] = []
    for _key, items in sorted(_edges_of(m, fids).items()):
        inside = [it for it in items if it[0] in region]
        if len(inside) != 1:
            continue
        fi, a, b = inside[0]
        if len(items) == 1 and axis_x and abs(m.verts[a][0]) < 1e-9 and abs(m.verts[b][0]) < 1e-9:
            continue
        for v in (a, b):
            if v not in inner:
                inner[v] = _move(m, v, nrm[v], -depth, axis_x=axis_x)
        uva = m.uv[fi][m.faces[fi].index(a)] if m.uv[fi] else (0.0, 0.0)
        uvb = m.uv[fi][m.faces[fi].index(b)] if m.uv[fi] else (0.0, 0.0)
        # the shell side keeps the edge a -> b of the opening; the wall goes inwards from it
        walls.append(m.f((b, a, inner[a], inner[b]), role, into, _wall_uv(uva, uvb)))
    m.wall.update(walls)
    return walls


#: Axes of the skewed projection of detail UVs: a continuous map that gives every axis-aligned face a non-zero area.
_UV_E1 = (0.80, 0.40, 0.0)
_UV_E2 = (-0.30, 0.30, 0.90)


def _skew_uv(m: Mesh, f: tuple, scale: float = 0.5, off=(0.5, 0.5)) -> tuple:
    """Per-corner UVs of a face from a fixed linear map of the position (no seams, no zero-area face)."""
    return tuple((round(off[0] + scale * _dot(m.verts[v], _UV_E1), 5), round(off[1] + scale * _dot(m.verts[v], _UV_E2), 5))
                 for v in f)


def _planar_uv(m: Mesh, f: tuple, scale: float = 1.0, off=(0.5, 0.5)) -> tuple:
    """Per-corner UVs of a face projected along its dominant normal axis (metres * scale + offset): never zero area."""
    n = _normal(m, f)
    a = max(range(3), key=lambda i: abs(n[i]))
    ax = [i for i in range(3) if i != a]
    return tuple((round(off[0] + m.verts[v][ax[0]] * scale, 5), round(off[1] + m.verts[v][ax[1]] * scale, 5)) for v in f)


# --------------------------------------------------------------------------- automobile

#: Section rows from the roof centre down the side to the underbody centre (half body, x >= 0). A profile may use
#: a subset (``rows``): the zone of a cell follows the name of its upper row.
_ROWS = ("top_c", "top_q", "top_s", "glass_m", "belt", "door_m", "rocker", "sill", "floor_c")
#: Paint V (Blender, 0 = bottom of the image) of each row on ``vehiclegrunge256``. ONE continuous map: no UV seam
#: inside the paint (``kit.shade`` keeps the body smooth across it). The roof and the bonnet lie in the clean top band,
#: the shoulders below it, the sides in the grime band that rises from the bottom edge, the belly in the bottom strip.
_V_PAINT = {"top_c": 0.97, "top_q": 0.945, "top_s": 0.9, "glass_m": 0.76, "belt": 0.62, "door_m": 0.38,
            "rocker": 0.16, "sill": 0.07, "floor_s": 0.035, "floor_c": 0.0}
#: Paint U along the length (rear .. front): the clean left strip of the texture (vanilla up-facing paint lies at
#: u 0.03-0.23; the top band right of it holds dark drips).
_U_PAINT = (0.03, 0.23)
#: Clean paint rectangle (Blender u0, v0, u1, v1) for small painted details (mirror heads).
_PAINT_DETAIL = (0.05, 0.86, 0.2, 0.96)
_ARCH_P = 2.0        # super-ellipse exponent of the wheel opening: 2 = a round arch centred on the wheel
_MIN_STATION_M = 0.012
#: Shared atlas region of each non-paint role of the body: its faces get planar UVs fitted into the region.
_ATLAS = {"glass": "generic.glass_core", "chrome": "generic.chrome_strip", "trim": "generic.black",
          "lens": "lights.amber_bar", "lamp_fr": "lights.front_measured", "lamp_fl": "lights.front_measured",
          "lamp_rr": "lights.rear_measured", "lamp_rl": "lights.rear_measured"}
#: Fold (degrees) above which an edge is hard even inside one material (box caps, prism ends).
_HARD_DEG = 85.0

_CAR_PARTS = (
    {"name": "chassis", "slot": "chassis"},
    {"name": "bonnet", "slot": "bonnet_ok"},
    {"name": "boot", "slot": "boot_ok"},
    {"name": "windscreen", "slot": "windscreen_ok"},
    {"name": "door_f", "slot": "door_{s}f_ok", "sided": True},
    {"name": "door_r", "slot": "door_{s}r_ok", "sided": True},
    {"name": "bump_front", "slot": "bump_front_ok"},
    {"name": "bump_rear", "slot": "bump_rear_ok"},
    {"name": "exhaust", "slot": "exhaust_ok"},
)


def part_slot(part: dict, side: str | None = None, model: str | None = None) -> str:
    """Kit slot (frame) of a blank part; sided parts take ``l`` or ``r``, ``{model}`` is the model name."""
    return str(part["slot"]).replace("{s}", side or "r").replace("{model}", model or "")


def _arch_g(u: float) -> float:
    a = abs(u)
    return 0.0 if a >= 1.0 else (1.0 - a ** _ARCH_P) ** (1.0 / _ARCH_P)


def _plan_factor(y: float, y_rear: float, y_front: float, rear_m: float, front_m: float, q: float) -> float:
    """Plan-view half-width factor 0..1: a super-elliptic rounding of the nose and of the tail."""
    s, span = y_front - y, front_m
    if s >= front_m:
        s, span = y - y_rear, rear_m
        if s >= rear_m:
            return 1.0
    u = _clamp(s / span, 0.0, 1.0)
    return (1.0 - (1.0 - u) ** q) ** (1.0 / q)


def _car_stations(body: dict, L: float, y_rear: float, tier: dict, a: dict, ra: float) -> list[dict]:
    """Stations along the length: key loops (parts, pillars, nose and tail rounding), subdivisions and the arch
    columns, spaced by equal angles around each wheel (the opening is a polygon of ``arch_cols - 1`` segments)."""
    st = body["stations"]
    pl = body["plan"]
    key = {0.0, 1.0} | {float(v) for v in st.values()}
    for s in tier["ring"]:                  # rounding rings: distances from the ends as shares of the rounding length
        key.add(_r(1.0 - s * pl["front_m"] / L, 6))
        key.add(_r(s * pl["rear_m"] / L, 6))
    keys = sorted(k for k in key if 0.0 <= k <= 1.0)
    keys = [t for i, t in enumerate(keys) if i == 0 or (t - keys[i - 1]) * L >= 0.012]
    pts: list[tuple[float, bool, int]] = []                # (t, key, priority: 2 = an arch end, 1 = a key loop)
    for t0, t1 in zip(keys, keys[1:]):
        pts.append((t0, True, 1))
        span = (t1 - t0) * L
        n = max(1, int(round(span / float(tier["cell_m"])))) if span >= 0.2 else 1
        for k in range(1, n):
            pts.append((t0 + (t1 - t0) * k / n, False, 0))
    pts.append((keys[-1], True, 1))
    cols = max(3, int(tier["arch_cols"]))
    for ya in a["axle_y"]:
        for k in range(cols):
            u = -math.cos(math.pi * k / (cols - 1))
            pts.append(((ya + ra * u - y_rear) / L, False, 2 if k in (0, cols - 1) else 0))
    pts.sort(key=lambda p: (p[0], -p[2]))
    out: list[dict] = []
    for t, is_key, prio in pts:
        if out and (t - out[-1]["t"]) * L < _MIN_STATION_M:
            # one station for both: the arch ends keep their place (the opening stays round), keys stay keys
            if prio > out[-1]["prio"]:
                out[-1].update(t=t, prio=prio)
            out[-1]["key"] = out[-1]["key"] or is_key
            continue
        out.append({"t": t, "key": is_key, "prio": prio})
    out[0]["t"], out[-1]["t"] = 0.0, 1.0
    return out


def _shrink(m: Mesh, fids: list[int], width: float, ring_role: str, *, axis_x: bool = True) -> list[int]:
    """Inset the region ``fids`` within its own surface: its boundary moves ``width`` inwards and a ring of quads
    (``ring_role``) fills the band outside it (a bezel). Boundary edges on the mirror plane get no ring."""
    region = set(fids)
    nrm = _vertex_normals(m, fids)
    bnd: list[tuple[int, int, int]] = []
    for _key, items in sorted(_edges_of(m, fids).items()):
        inside = [it for it in items if it[0] in region]
        if len(inside) != 1:
            continue
        fi, a, b = inside[0]
        if axis_x and abs(m.verts[a][0]) < 1e-9 and abs(m.verts[b][0]) < 1e-9:
            continue
        bnd.append(inside[0])
    lefts: dict[int, list[tuple]] = {}
    for _fi, a, b in bnd:
        e = _sub(m.verts[b], m.verts[a])
        le = math.sqrt(_dot(e, e)) or 1.0
        e = (e[0] / le, e[1] / le, e[2] / le)
        for v in (a, b):
            lf = _cross(nrm[v], e)                     # the inside of a counter-clockwise face is left of its edge
            ll = math.sqrt(_dot(lf, lf)) or 1.0
            lefts.setdefault(v, []).append((lf[0] / ll, lf[1] / ll, lf[2] / ll))
    new: dict[int, int] = {}
    for v, ls in sorted(lefts.items()):
        d = [sum(lf[k] for lf in ls) for k in range(3)]
        x, y, z = m.verts[v]
        if axis_x and abs(x) < 1e-9:
            d[0] = 0.0
        ln = math.sqrt(d[0] * d[0] + d[1] * d[1] + d[2] * d[2]) or 1.0
        d = [c / ln for c in d]
        dist = width / max(0.5, min(_dot(tuple(d), lf) for lf in ls))
        new[v] = m.v(x + d[0] * dist, y + d[1] * dist, z + d[2] * dist)
    old_uv = {(fi, v): m.uv[fi][k] for fi in fids for k, v in enumerate(m.faces[fi]) if m.uv[fi]}
    ring: list[int] = []
    for fi, a, b in bnd:
        ua, ub = old_uv.get((fi, a), (0.0, 0.0)), old_uv.get((fi, b), (0.0, 0.0))
        if ring_role.startswith("paint"):          # a band of the shell: its UVs continue the paint map
            uv = (ua, ub, (ub[0], ub[1] - 0.004), (ua[0], ua[1] - 0.004))
        else:
            uv = _wall_uv(ua, ub)
        ring.append(m.f((a, b, new[b], new[a]), ring_role, m.part[fi], uv))
    for fi in fids:
        m.faces[fi] = tuple(new.get(v, v) for v in m.faces[fi])
    return ring


def _face_toward(m: Mesh, fids, point) -> None:
    """Wind the faces ``fids`` so that their normals point towards ``point`` (a wheel house faces its wheel)."""
    for fi in fids:
        f = m.faces[fi]
        n = _normal(m, f)
        mid = tuple(sum(m.verts[v][k] for v in f) / len(f) for k in range(3))
        if _dot(n, _sub(point, mid)) < 0:
            m.faces[fi] = tuple(reversed(f))
            if m.uv[fi]:
                m.uv[fi] = tuple(reversed(m.uv[fi]))


def _rect_uv(m: Mesh, fids, rect) -> None:
    """Planar UVs (each face along its dominant axis) of ``fids`` fitted into the Blender rectangle ``rect``."""
    proj = {}
    for fi in fids:
        n = _normal(m, m.faces[fi])
        ax = max(range(3), key=lambda i: abs(n[i]))
        a, b = ((1, 2), (0, 2), (0, 1))[ax]
        proj[fi] = [(m.verts[v][a], m.verts[v][b]) for v in m.faces[fi]]
    if not proj:
        return
    us = [p[0] for ps in proj.values() for p in ps]
    vs = [p[1] for ps in proj.values() for p in ps]
    u0, v0, u1, v1 = rect
    mu, mv = (u1 - u0) * 0.08, (v1 - v0) * 0.08
    su = (u1 - u0 - 2 * mu) / max(max(us) - min(us), 1e-6)
    sv = (v1 - v0 - 2 * mv) / max(max(vs) - min(vs), 1e-6)
    for fi, ps in proj.items():
        m.uv[fi] = tuple((round(u0 + mu + (pu - min(us)) * su, 5), round(v0 + mv + (pv - min(vs)) * sv, 5))
                         for pu, pv in ps)


def _atlas_uvs(m: Mesh, regions: dict[str, str]) -> None:
    """Faces of the roles in ``regions`` get UVs inside their shared atlas region (``satk.kit.atlas``)."""
    from . import atlas as A

    for role, reg in sorted(regions.items()):
        fids = [i for i, r in enumerate(m.role) if r == role]
        if fids:
            _rect_uv(m, fids, A.to_blender(A.region(reg)["rect"]))


def _car(plan: dict) -> dict:
    body = plan["profile"]
    tier = body["tier"]
    d, a = plan["dims"], plan["anchors"]
    L, W, H = float(d["L"]), float(d["W"]), float(d["H"])
    hw = W / 2.0
    zg, yR = float(a["ground_z"]), float(a["y_rear"])
    out_b = float((body.get("detail") or {}).get("bumper_out", 0.0))
    if out_b:                     # the bumpers stand proud of the shell: the shell is shorter, the car keeps its length
        yR, L = yR + out_b, L - 2.0 * out_b
    yF = yR + L
    wd = float(a["wheel_d"])
    ra = wd / 2.0 + float(body["wheel_clear"])
    zc = float(a.get("wheel_z", zg + wd / 2.0)) - zg                  # wheel centre above the ground
    pl = body["plan"]
    top_k, belt_k = body["top"], body["belt"]
    creases = body["crease_t"]
    floor_k = body.get("floor") or [[0.0, 0.30], [0.035, 0.19], [0.965, 0.19], [1.0, 0.31]]
    sill0 = float(body["sill_h"])
    stn = body["stations"]
    hatch, bed = bool(body.get("hatch")), bool(body.get("bed"))
    deck_h = float(_curve(top_k, float(stn["rear_window_base"]), creases)) * H
    stations = _car_stations(body, L, yR, tier, a, ra)
    n = len(stations)
    crown = float(body.get("crown", 0.05))
    crown_roof = float(body.get("crown_roof", crown))
    pillow = float(body.get("pillow", 0.0))
    bulge = float(body.get("bulge", 1.01))
    names = list(body.get("rows") or _ROWS)
    ri = {nm: i for i, nm in enumerate(names)}
    nrow = len(names)
    det = body.get("detail", {})
    wells = bool(det.get("well_depth"))
    # the liner plane of the wheel houses: inside the tyre's inner face
    x_in = min(max(0.2, float(a["track"]) / 2.0 - 0.18 * wd - 0.05), hw * 0.9)

    # ---- the section of every station: rows of (x, z)
    secs: list[list[tuple[float, float]]] = []
    cab: list[float] = []
    inside: list[bool] = []
    belts: list[float] = []
    for s in stations:
        t = s["t"]
        y = yR + t * L
        f = _plan_factor(y, yR, yF, pl["rear_m"], pl["front_m"], pl["exp"])
        h_top = _curve(top_k, t, creases) * H
        c = _clamp((h_top - deck_h) / max(H - deck_h, _EPS), 0.0, 1.0)
        if pillow:                                         # an arch along the length of the bonnet and of the boot
            for t0, t1 in ((stn["cowl"], stn["hood_end"]), (stn.get("boot_end", 0.0), stn["rear_window_base"])):
                if t0 < t < t1:
                    h_top += pillow * 4.0 * ((t - t0) / (t1 - t0)) * (1.0 - (t - t0) / (t1 - t0))
        belt = min(_curve(belt_k, t, ()) * H, h_top - 0.045)
        h_floor = _curve(floor_k, t, ())
        h_sill = max(sill0, h_floor + 0.05)
        h_rock = h_sill + 0.05
        u = min(((y - ya) / ra for ya in a["axle_y"]), key=abs)
        # the opening: an arc around the wheel centre, then straight down to the sill
        zb = max(h_rock, zc + ra * _arch_g(u)) if abs(u) <= 1.0 else h_rock
        inside.append(abs(u) < 1.0 - 1e-9)
        # the crown never pushes the shoulder under the belt (no fold): at most 60 % of the drop to the belt
        cr = min(_lerp(crown, crown_roof, c), max(0.0, 0.6 * (h_top - belt)))
        xts = hw * _lerp(pl["deck_w"], pl["roof_w"], c)
        xb = hw * pl["belt_w"] / max(bulge, 1.0)               # the widest row (the door bulge) is half the width W
        z_ts = h_top - cr
        rowd = {
            "top_c": (0.0, h_top),
            "top_q": (0.78 * xts * f, h_top - 0.45 * cr),
            "top_s": (xts * f, z_ts),
            "glass_m": (_lerp(xts, xb, 0.5) * f, _lerp(z_ts, belt, 0.5)),
            "belt": (xb * f, belt),
            "door_m": (xb * bulge * f, (belt + zb) / 2.0),
            "rocker": (xb * 0.95 * f, zb),
            "sill": (min(xb * 0.84 * f, x_in) if wells and abs(u) < 1.0 - 1e-9 else xb * 0.84 * f, h_sill),
            "floor_s": (xb * 0.45 * f, (h_sill + h_floor) / 2.0),
            "floor_c": (0.0, h_sill + 0.03),
        }
        secs.append([(rowd[nm][0], zg + rowd[nm][1]) for nm in names])
        cab.append(c)
        belts.append(zg + belt)

    m = Mesh()
    vid = [[m.v(x, yR + stations[i]["t"] * L, z) for x, z in secs[i]] for i in range(n)]
    bump_role = str(body.get("bumper_role", "trim"))
    det = body.get("detail", {})
    bumper_vol = bool(det.get("bumper_out", 0.0))
    tailgate_t = float(stn.get("tailgate", -1.0))
    bed_t = float(stn.get("bed_front", -1.0)) if bed else -1.0

    def roles_parts(i: int, up: str) -> tuple[str, str]:
        tc = (stations[i]["t"] + stations[i + 1]["t"]) / 2.0
        c = (cab[i] + cab[i + 1]) / 2.0
        hood = tc >= stn["cowl"]
        wind = stn["roof_front"] <= tc < stn["cowl"]
        roof = stn["roof_rear"] <= tc < stn["roof_front"]
        rglass = stn["rear_window_base"] <= tc < stn["roof_rear"]
        door_f = stn["door_f_rear"] <= tc <= stn["cowl"]
        door_r = stn["door_r_rear"] <= tc <= stn["door_r_front"]
        pillar_b = stn["door_r_front"] <= tc <= stn["door_f_rear"]
        quarter = "quarter_rear" in stn and stn["quarter_rear"] <= tc <= stn["quarter_front"]
        gate = (hatch or bed) and tc < tailgate_t
        bump_r = tc <= stn["bump_rear_end"]
        bump_f = tc >= stn["bump_front_start"]
        if up in ("top_c", "top_q"):
            if hood:
                return "paint1", "bonnet"
            if wind:
                return ("glass", "windscreen") if up == "top_c" else ("paint1", "chassis")
            if roof:
                return "paint1", "chassis"
            if rglass:                                 # a hatch's glass spans the tailgate, a sedan's has C pillars
                return ("glass" if up == "top_c" or hatch else "paint1"), ("boot" if hatch else "chassis")
            if bed:
                return "paint1", "boot" if gate else "chassis"
            return "paint1", "boot"
        if up in ("top_s", "glass_m"):
            if door_f or door_r:
                return ("glass" if c > 0.25 else "paint1"), ("door_f" if door_f else "door_r")
            if pillar_b:
                return ("black" if c > 0.25 else "paint1"), "chassis"
            if quarter and c > 0.25:
                return "glass", "chassis"
            return "paint1", "boot" if gate else "chassis"
        if up in ("belt", "door_m"):
            if up == "door_m" and (bump_f or bump_r):
                if bumper_vol:                        # the backing behind the bumper volume
                    return "black", "chassis"
                return bump_role, "bump_front" if bump_f else "bump_rear"
            if door_f:
                return "paint1", "door_f"
            if door_r:
                return "paint1", "door_r"
            return "paint1", "boot" if gate else "chassis"
        if up == "rocker":
            if bumper_vol:
                return ("black" if bump_f or bump_r else "trim"), "chassis"
            return "trim", "bump_front" if bump_f else "bump_rear" if bump_r else "chassis"
        if bumper_vol:
            return "black", "chassis"
        return "black", "bump_front" if bump_f else "bump_rear" if bump_r else "chassis"

    u0p, u1p = _U_PAINT
    cell: dict[tuple[int, str], int] = {}
    for i in range(n - 1):
        ui = u0p + (u1p - u0p) * stations[i]["t"]
        uj = u0p + (u1p - u0p) * stations[i + 1]["t"]
        for k in range(nrow - 1):
            up, lo = names[k], names[k + 1]
            if up == "rocker" and (inside[i] or inside[i + 1]):
                continue                                  # the wheel opening: no rocker strip under the arch
            v0, v1 = _V_PAINT[up], _V_PAINT[lo]
            role, part = roles_parts(i, up)
            cell[(i, up)] = m.f((vid[i][k], vid[i][k + 1], vid[i + 1][k + 1], vid[i + 1][k]), role, part,
                                ((ui, v0), (ui, v1), (uj, v1), (uj, v0)))
    for i in range(n - 1):                                # unwrap seams (the UVs stay continuous across them)
        m.mark_seam(vid[i][ri["sill"]], vid[i + 1][ri["sill"]])
        m.mark_seam(vid[i][ri["top_s"]], vid[i + 1][ri["top_s"]])

    # ---- layered details: recessed glass, an open bed, lamps and grille, wheel houses, jambs, mirrors
    ycell = {key: sum(m.verts[v][1] for v in m.faces[fi]) / 4.0 for key, fi in cell.items()}
    xcell = {key: sum(abs(m.verts[v][0]) for v in m.faces[fi]) / 4.0 for key, fi in cell.items()}
    tcell = {key: (stations[key[0]]["t"] + stations[key[0] + 1]["t"]) / 2.0 for key in cell}
    if det.get("window_depth"):
        for part in ("door_f", "door_r", "windscreen", "chassis", "boot"):
            fs = [fi for key, fi in cell.items() if m.role[fi] == "glass" and m.part[fi] == part]
            if fs:
                _inset(m, fs, -float(det["window_depth"]), "black")
    if bed:
        fs = [fi for (i, k), fi in cell.items() if k in ("top_c", "top_q") and m.part[fi] == "chassis"
              and tailgate_t + 0.004 < tcell[(i, k)] < bed_t]
        if fs:
            rim_uv = {v: uv for fi in fs for v, uv in zip(m.faces[fi], m.uv[fi])}
            for wi in _inset(m, fs, -float(body.get("bed_depth", 0.4)), "paint1"):
                a_, b_ = m.faces[wi][0], m.faces[wi][1]            # the rim keeps the deck's paint UVs
                (ua, va), (ub, vb) = rim_uv.get(a_, (0.1, 0.9)), rim_uv.get(b_, (0.1, 0.9))
                m.uv[wi] = ((ua, va), (ub, vb), (ub, vb - 0.12), (ua, va - 0.12))
            for fi in fs:
                m.role[fi] = "trim"
    if det.get("lamp_depth"):
        lamp_len = float(det.get("lamp_len", 0.85))
        bezel = float(det.get("lamp_bezel", 0.02))
        for end in (1, -1):
            sel = [fi for (i, k), fi in cell.items() if k == "belt" and m.part[fi] in ("chassis", "boot")
                   and ((end > 0 and ycell[(i, k)] > yF - lamp_len) or (end < 0 and ycell[(i, k)] < yR + lamp_len - 0.05))
                   and 0.30 * hw < xcell[(i, k)] < 0.95 * hw]
            if not sel:
                continue
            if bezel > 0:                                 # the surround: a band of the shell around the bucket
                _shrink(m, sel, bezel, m.role[sel[0]])
            for fi in sel:
                m.role[fi] = "lamp_fr" if end > 0 else "lamp_rr"
            _inset(m, sel, -float(det["lamp_depth"]), "black")     # the bucket walls, the lens at its bottom
            if bezel > 0:
                _shrink(m, sel, 0.6 * bezel, "black")              # a smaller lens: the bucket narrows
        grille = [fi for (i, k), fi in cell.items() if k == "belt" and m.part[fi] == "chassis"
                  and ycell[(i, k)] > yF - lamp_len and xcell[(i, k)] < 0.30 * hw]
        if grille:
            if bezel > 0:
                _shrink(m, grille, bezel, m.role[grille[0]])
            for fi in grille:
                m.role[fi] = "black"                         # the dark backing; teeth are the modeller's detail
            _inset(m, grille, -float(det.get("grille_depth", det["lamp_depth"])), "black")
    if bumper_vol:
        out_m = float(det["bumper_out"])
        for ya, end in ((max(a["axle_y"]), 1), (min(a["axle_y"]), -1)):
            _car_bumper(m, stations, secs, ri, yR, L, ya + end * (ra + 0.03), end, out_m, bump_role,
                        "bump_front" if end > 0 else "bump_rear")
    if det.get("well_depth"):
        for ya in a["axle_y"]:
            _arch_well(m, vid, inside, stations, yR, L, ya, ra, ri, x_in, (float(a["track"]) / 2.0, ya, zg + zc),
                       int(det.get("well_segments", 2)))
    if det.get("jamb_depth"):
        for part in ("door_f", "door_r", "bonnet", "boot", "windscreen"):
            _jambs(m, part, float(det["jamb_depth"]), "black", "chassis")
    if det.get("mirror"):
        t_m = float(stn["cowl"]) - 0.035
        y_m = yR + t_m * L
        i_m = min(range(n), key=lambda i: abs(stations[i]["t"] - t_m))
        x_door = secs[i_m][ri["belt"]][0] if "belt" in ri else hw
        _car_mirror(m, x_door, y_m, belts[i_m] + 0.03, "door_f")
    _atlas_uvs(m, _ATLAS)

    pieces = [_piece("body", m)]
    if plan.get("interior", True):
        lift = max(0.0, sill0 - 0.27)                      # a high body (SUV, pickup) lifts its floor and boxes
        pieces.append(_piece("interior", _car_interior(a, zg + lift, yR, L, hw, det), part="chassis"))
    if det.get("exhaust", True):                          # one tailpipe on the right, under the rear bumper
        mx = Mesh()
        ze = zg + 0.24 + max(0.0, sill0 - 0.27)
        _prism(mx, (hw * 0.5, yR + 0.55, ze), (hw * 0.5, yR + 0.04, ze), 0.08, 0.08, "chrome", "exhaust", 6)
        _atlas_uvs(mx, {"chrome": "generic.chrome_pipe"})
        pieces.append(_piece("exhaust", mx, part="exhaust", mirror=False))
    return {"pieces": pieces, "parts": [dict(p) for p in _CAR_PARTS]}


def _car_bumper(m: Mesh, stations: list[dict], secs: list, ri: dict, yR: float, L: float, y_arch: float, end: int,
                out: float, role: str, part: str) -> None:
    """A bumper as its own wrap-around volume (vanilla rule 11): a C-section swept along the body's outline at the
    lower nose (or tail) from just past the wheel arch to the centre line, ``out`` metres proud of the body with a
    rounded 4-segment profile; its top tucks into the body (a shut line and a step, not a painted row)."""
    ii = [i for i, st in enumerate(stations) if (yR + st["t"] * L - y_arch) * end >= 0.0]
    ii = sorted(ii, key=lambda i: end * stations[i]["t"])           # from the arch towards the end of the car
    if len(ii) < 2:
        return
    path = []
    for i in ii:
        x_dm, z_dm = secs[i][ri["door_m"]]
        x_rk, z_rk = secs[i][ri["rocker"]]
        path.append((x_dm, yR + stations[i]["t"] * L, z_dm, z_rk))
    first = len(m.faces)
    rings, normals = [], []
    # one straight band: the height at the arch end, the bottom at the lowest rocker point (the ends of the body
    # rise; a band that followed them would droop); the dark backing shows as a shut line above it at the nose
    zt0, zb0 = path[0][2], min(pt[3] for pt in path)
    for k, (x, y, _zt, _zb) in enumerate(path):
        a_ = path[max(k - 1, 0)]
        b_ = path[min(k + 1, len(path) - 1)]
        tx, ty = b_[0] - a_[0], b_[1] - a_[1]
        nx, ny = ty, -tx                                       # the plan normal ...
        if nx * x + ny * end < 0:                              # ... pointing out of the body (sideways, then ahead)
            nx, ny = -nx, -ny
        ln = math.hypot(nx, ny) or 1.0
        nx, ny = nx / ln, ny / ln
        if x <= 1e-6:                                          # the centre line: straight ahead, on the mirror plane
            nx, ny, x = 0.0, float(end), 0.0
        zt, zb = zt0 - 0.01, zb0 + 0.03
        zm = (zt + zb) / 2.0
        prof = [(-0.03, zt), (0.7 * out, zt), (out, zm), (0.7 * out, zb), (-0.03, zb)]
        rings.append([m.v(0.0 if x == 0.0 else x + nx * o, y + ny * o, z) for o, z in prof])
        normals.append((nx, ny))
    for k in range(len(rings) - 1):
        for j in range(len(rings[0]) - 1):
            f = (rings[k][j], rings[k][j + 1], rings[k + 1][j + 1], rings[k + 1][j])
            m.f(f, role, part, _skew_uv(m, f))
    # one winding for the whole sweep: the face at the front of the first ring points out of the body
    _flip_all(m, range(first, len(m.faces)), first + 1, (normals[0][0], normals[0][1], 0.0))
    cap = tuple(rings[0])                                      # the end next to the arch faces the arch
    fc = m.f(cap, role, part, _skew_uv(m, cap))
    back = tuple(m.verts[rings[0][2]][c] - m.verts[rings[1][2]][c] for c in range(3))
    if _dot(_normal(m, m.faces[fc]), back) < 0:
        m.faces[fc] = tuple(reversed(m.faces[fc]))
        m.uv[fc] = tuple(reversed(m.uv[fc]))


def _car_mirror(m: Mesh, x0: float, y0: float, z0: float, part: str) -> None:
    """A door mirror: a short black stalk from inside the door skin at ``(x0, y0, z0)`` out to a rounded painted head
    (vanilla: an 8-26 triangle head of about 20 x 10 x 13 cm on a 3-6 triangle stalk that touches the door)."""
    first = len(m.faces)
    _prism(m, (x0 - 0.03, y0 + 0.01, z0 + 0.02), (x0 + 0.1, y0 - 0.005, z0 + 0.05), 0.04, 0.03, "black", part, 4)
    _gbox(m, (x0 + 0.08, y0 - 0.06, z0 - 0.01), (x0 + 0.27, y0 + 0.045, z0 + 0.12), (1, 1, 1), 0.03, "paint1", part,
          uv="skew")
    _rect_uv(m, [i for i in range(first, len(m.faces)) if m.role[i] == "paint1"], _PAINT_DETAIL)


def _arch_well(m: Mesh, vid: list[list[int]], inside: list[bool], stations: list[dict], yR: float, L: float,
               ya: float, ra: float, ri: dict, x_in: float, wheel, segs: int = 2) -> None:
    """The wheel house of the arch at ``ya``: a tub from the arch edge (the rocker row over the opening) in to
    ``x_in``, a flat vertical liner plate there and a return wall at each end of the opening down to the sill, so the
    body is never see-through (vanilla: a short return face and a liner plate inside every arch). Faces face the
    wheel."""
    cols = [i for i in range(len(vid)) if inside[i] and abs(yR + stations[i]["t"] * L - ya) < ra]
    if not cols:
        return
    lo, hi = max(min(cols) - 1, 0), min(max(cols) + 1, len(vid) - 1)    # include the end columns of the arch
    row, sill = ri["rocker"], ri["sill"]
    first = len(m.faces)
    prev = {s: vid[s][row] for s in range(lo, hi + 1)}
    for k in range(1, segs + 1):
        f = k / segs
        cur = {}
        for s in range(lo, hi + 1):
            x, y, z = m.verts[vid[s][row]]
            cur[s] = m.v(_lerp(x, x_in, f), y, z + 0.06 * f * f)
        for s in range(lo, hi):
            quad = (prev[s], cur[s], cur[s + 1], prev[s + 1])
            m.f(quad, "black", "chassis", _skew_uv(m, quad))
        prev = cur
    bottom: dict[int, int] = {}
    for s in (lo, hi):
        x, y, z = m.verts[vid[s][sill]]
        bottom[s] = m.v(x_in, y, z)
        quad = (vid[s][row], vid[s][sill], bottom[s], prev[s])
        m.f(quad, "black", "chassis", _skew_uv(m, quad))
    # the liner: the inner rim of the tub over the top, the sill line under the arch (moved in to x_in) below;
    # the wheel house stays open towards the ground like a real one
    liner = tuple(prev[s] for s in range(lo, hi + 1)) + (bottom[hi],) + tuple(
        vid[s][sill] for s in range(hi - 1, lo, -1)) + (bottom[lo],)
    m.f(liner, "black", "chassis", _skew_uv(m, liner))
    _face_toward(m, range(first, len(m.faces)), wheel)


def _piece(name: str, m: Mesh, part: str | None = None, slot: str | None = None, mirror: bool = True) -> dict:
    """A piece of the spec: JSON-friendly arrays of one mesh; ``part`` forces one part name for every face."""
    names: list[str] = []
    for p in ([part] if part else m.part):
        if p not in names:
            names.append(p)
    return {
        "name": name, "slot": slot, "mirror": mirror,
        "verts": [[_r(x), _r(y), _r(z)] for x, y, z in m.verts],
        "faces": [list(f) for f in m.faces],
        "role": list(m.role),
        "part": [names.index(part or p) for p in m.part],
        "part_names": names,
        "uv": [[[_r(u, 5), _r(v, 5)] for u, v in uv] for uv in m.uv],
        "seam": sorted([list(e) for e in m.seam]),
        "sharp": _sharp_edges(m),
    }


def _sharp_edges(m: Mesh) -> list[list[int]]:
    """Hard edges of a mesh by the vanilla rule: material (role) borders and real folds (above ``_HARD_DEG``, box
    caps and prism ends); corners inside one material stay smooth."""
    faces_of: dict[tuple[int, int], list[int]] = {}
    for fi, f in enumerate(m.faces):
        for k in range(len(f)):
            a, b = f[k], f[(k + 1) % len(f)]
            faces_of.setdefault((a, b) if a < b else (b, a), []).append(fi)
    unit: dict[int, tuple[float, float, float]] = {}

    def nrm(fi: int) -> tuple[float, float, float]:
        if fi not in unit:
            nx, ny, nz = _normal(m, m.faces[fi])
            ln = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
            unit[fi] = (nx / ln, ny / ln, nz / ln)
        return unit[fi]

    lim = math.cos(math.radians(_HARD_DEG))
    out = []
    for e, fs in faces_of.items():
        if len(fs) != 2:
            continue
        if m.role[fs[0]] != m.role[fs[1]] or _dot(nrm(fs[0]), nrm(fs[1])) < lim:
            out.append(list(e))
    return sorted(out)


_UNDERBODY = ("tunnel", "engine", "tank", "radiator", "axle_f", "axle_r")
_INTERIOR = ("seat_f", "back_f", "seat_r", "back_r", "dash")


def _car_interior(a: dict, zg: float, yR: float, L: float, hw: float, det: dict) -> Mesh:
    """Seats, dashboard and tunnel (half, x >= 0) in dark interior material, plus the hidden engine and underbody boxes."""
    m = Mesh()
    ya_r, ya_f = a["axle_y"]
    wb = ya_f - ya_r
    yF = yR + L
    fl = zg + 0.26                           # the floor of the cabin
    seat_y = ya_f - 0.50 * wb
    rear_y = ya_r + 0.18 * wb
    xi = hw * 0.62
    c = float(det.get("interior_chamfer", 0.07))
    want = det.get("interior_parts", _INTERIOR)
    items = {
        "seat_f": ((0.12, seat_y - 0.28, fl), (xi, seat_y + 0.28, fl + 0.20)),
        "back_f": ((0.12, seat_y - 0.42, fl + 0.20), (xi, seat_y - 0.30, fl + 0.82)),
        "seat_r": ((0.12, rear_y - 0.30, fl), (xi + 0.1, rear_y + 0.26, fl + 0.20)),
        "back_r": ((0.12, rear_y - 0.46, fl + 0.20), (xi + 0.1, rear_y - 0.32, fl + 0.80)),
        "dash": ((0.0, ya_f - 0.34, fl + 0.02), (hw * 0.8, ya_f + 0.10, fl + 0.46)),
    }
    for nm in _INTERIOR:
        if nm in want:
            _gbox(m, items[nm][0], items[nm][1], (1, 1, 1), c, "interior", "chassis", 2.0, "skew" if c > 0 else "planar")
    ub = det.get("underbody")
    if ub:
        ub = _UNDERBODY if ub is True else ub
        cu = float(det.get("underbody_chamfer", 0.05))
        under = {
            # the tunnel reaches up into the body floor (its centre is ~4 cm above the cabin floor line): a box
            # hanging under the floor would be a floating piece
            "tunnel": ((0.0, ya_r + 0.35, zg + 0.20), (0.22, ya_f - 0.40, fl + 0.06)),
            "engine": ((0.0, yF - 1.45, zg + 0.34), (0.55, yF - 0.75, zg + 0.74)),
            "tank": ((0.0, yR + 0.35, zg + 0.24), (0.62, yR + 0.95, zg + 0.46)),
            "radiator": ((0.0, yF - 0.60, zg + 0.40), (0.62, yF - 0.48, zg + 0.70)),
            "axle_f": ((0.0, ya_f - 0.06, zg + 0.20), (hw * 0.80, ya_f + 0.06, zg + 0.30)),
            "axle_r": ((0.0, ya_r - 0.06, zg + 0.20), (hw * 0.80, ya_r + 0.06, zg + 0.30)),
        }
        for nm in _UNDERBODY:
            if nm in ub:
                _gbox(m, under[nm][0], under[nm][1], (1, 1, 1), cu, "black", "chassis", 2.0, "skew" if cu > 0 else "planar")
    return m


# --------------------------------------------------------------------------- shared shapes (hulls, lathes, boxes)


def _superell(a: float, b: float, p: float, n: int) -> list[tuple[float, float]]:
    """``n + 1`` points (x, z) of the half super-ellipse x = a cos^(2/p), z = b sin^(2/p) from the top centre (x = 0)
    down to the bottom centre (x = 0); ``p`` = 2 is an ellipse, larger is boxier."""
    pts = []
    for k in range(n + 1):
        t = math.radians(90.0 - 180.0 * k / n)
        c, s = math.cos(t), math.sin(t)
        pts.append((0.0 if k in (0, n) else a * abs(c) ** (2.0 / p), b * math.copysign(abs(s) ** (2.0 / p), s)))
    return pts


def _loft_half(m: Mesh, rings: list[tuple[float, list[tuple[float, float]]]], role, part) -> list[list[int]]:
    """Quads between consecutive rings ``(y, [(x, z), ...])`` (rear to front, points top to bottom, x >= 0 half).

    ``role`` and ``part`` are strings or functions ``(ring_index, row_index) -> str``. Returns the vertex ids per ring.
    """
    ids = [[m.v(x, y, z) for x, z in pts] for y, pts in rings]
    n = len(rings)
    nr = len(rings[0][1])
    for i in range(n - 1):
        for k in range(nr - 1):
            r = role(i, k) if callable(role) else role
            pt = part(i, k) if callable(part) else part
            f = (ids[i][k], ids[i][k + 1], ids[i + 1][k + 1], ids[i + 1][k])
            u0, u1 = i / (n - 1), (i + 1) / (n - 1)
            v0, v1 = k / (nr - 1), (k + 1) / (nr - 1)
            m.f(f, r, pt, ((u0, v0), (u0, v1), (u1, v1), (u1, v0)))
    return ids


def _orient(m: Mesh, first: int, centre_of) -> None:
    """Flip the faces from ``first`` on whose normal points towards ``centre_of(face_middle)``."""
    for fi in range(first, len(m.faces)):
        f = m.faces[fi]
        n = _normal(m, f)
        mid = tuple(sum(m.verts[v][k] for v in f) / len(f) for k in range(3))
        c = centre_of(mid)
        if _dot(n, tuple(mid[k] - c[k] for k in range(3))) < 0:
            m.faces[fi] = tuple(reversed(f))
            if m.uv[fi]:
                m.uv[fi] = tuple(reversed(m.uv[fi]))


def _lathe(m: Mesh, prof: list[tuple[float, float]], sides: int, role: str, part: str, *, axis: int = 2,
           centre=(0.0, 0.0, 0.0), u_scale: float = 1.0, v_scale: float = 1.0) -> None:
    """A surface of revolution: ``prof`` = [(radius, position along the axis), ...] traversed so that the outside is on
    its right-hand side (bottom to top along the outer wall); radius 0 makes a pole (a fan of triangles)."""
    ax = [(1, 2), (2, 0), (0, 1)][axis]                      # the two axes of the circle plane
    rings: list = []
    for r, h in prof:
        if r <= 1e-9:
            p = [0.0, 0.0, 0.0]
            p[axis] = h
            rings.append(m.v(centre[0] + p[0], centre[1] + p[1], centre[2] + p[2]))
            continue
        row = []
        for i in range(sides):
            a = 2 * math.pi * i / sides
            p = [0.0, 0.0, 0.0]
            p[axis] = h
            p[ax[0]] = r * math.cos(a)
            p[ax[1]] = r * math.sin(a)
            row.append(m.v(centre[0] + p[0], centre[1] + p[1], centre[2] + p[2]))
        rings.append(row)
    nseg = len(prof) - 1
    for j in range(nseg):
        r0, r1 = rings[j], rings[j + 1]
        v0, v1 = j / nseg * v_scale, (j + 1) / nseg * v_scale
        dr, dh = prof[j + 1][0] - prof[j][0], prof[j + 1][1] - prof[j][1]
        right = (dh, -dr)                                    # the outside, in the (radius, axis) half plane
        for i in range(sides):
            i2 = (i + 1) % sides
            u0, u1 = i / sides * u_scale, (i + 1) / sides * u_scale
            if isinstance(r0, int):
                if isinstance(r1, int):
                    continue
                f, uv = (r0, r1[i2], r1[i]), ((u0, v0), (u1, v1), (u0, v1))
            elif isinstance(r1, int):
                f, uv = (r0[i], r0[i2], r1), ((u0, v0), (u1, v0), (u0, v1))
            else:
                f, uv = (r0[i], r0[i2], r1[i2], r1[i]), ((u0, v0), (u1, v0), (u1, v1), (u0, v1))
            a_mid = 2 * math.pi * (i + 0.5) / sides
            want = [0.0, 0.0, 0.0]
            want[axis] = right[1]
            want[ax[0]] = right[0] * math.cos(a_mid)
            want[ax[1]] = right[0] * math.sin(a_mid)
            if _dot(_normal(m, f), tuple(want)) < 0:
                f, uv = tuple(reversed(f)), tuple(reversed(uv))
            m.f(f, role, part, uv)


def _prism(m: Mesh, p0, p1, w: float, h: float, role: str, part: str, sides: int = 4) -> None:
    """A tube of ``sides`` faces from ``p0`` to ``p1`` (``w`` x ``h`` across), capped with n-gons."""
    first = len(m.faces)
    d = [p1[i] - p0[i] for i in range(3)]
    ln = math.sqrt(_dot(tuple(d), tuple(d))) or 1.0
    d = [x / ln for x in d]
    up = (0.0, 0.0, 1.0) if abs(d[2]) < 0.9 else (1.0, 0.0, 0.0)
    s = _cross(tuple(d), up)
    sl = math.sqrt(_dot(s, s)) or 1.0
    s = tuple(x / sl for x in s)
    t = _cross(s, tuple(d))
    k = 1.4142 if sides == 4 else 1.0
    rings = []
    for q in (p0, p1):
        row = []
        for i in range(sides):
            a = 2 * math.pi * (i + 0.5) / sides if sides == 4 else 2 * math.pi * i / sides
            cx, cy = math.cos(a) * (w / 2) * k, math.sin(a) * (h / 2) * k
            row.append(m.v(*(q[j] + s[j] * cx + t[j] * cy for j in range(3))))
        rings.append(row)
    for i in range(sides):
        i2 = (i + 1) % sides
        f = (rings[0][i], rings[0][i2], rings[1][i2], rings[1][i])
        m.f(f, role, part, _skew_uv(m, f))
    for row in rings:
        m.f(tuple(row), role, part, _skew_uv(m, tuple(row)))
    cen = tuple((p0[j] + p1[j]) / 2 for j in range(3))
    _orient(m, first, lambda mid: cen)


def _gbox(m: Mesh, lo, hi, div, c: float, role: str, part: str, uv_tile: float = 1.0, uv: str = "planar") -> None:
    """A closed box ``lo..hi`` with ``div`` = (nx, ny, nz) cells along each edge and every edge chamfered by ``c``
    (``c`` = 0: a plain subdivided box). ``uv``: ``planar`` = one planar map per face (``uv_tile`` metres per repeat, a
    UV seam on every edge) or ``skew`` = one continuous map (no seam, no zero-area face)."""
    n_of = [max(1, int(d)) for d in div]
    tile = 1.0 / uv_tile
    cen = tuple((lo[i] + hi[i]) / 2.0 for i in range(3))
    c = min(c, min((hi[i] - lo[i]) / 2.2 for i in range(3)))

    def uv_of(mm: Mesh, f: tuple) -> tuple:
        return _skew_uv(mm, f) if uv == "skew" else _planar_uv(mm, f, tile, (0.0, 0.0))

    if c <= 1e-9:
        _box(m, lo, hi, tuple(n_of), role, part, uvf=lambda mm, f, face: uv_of(mm, f))
        return
    grid: dict = {}

    def gv(fax: int, side: int, idx: dict) -> int:
        """Vertex of the face (axis ``fax``, ``side``) at grid indices ``idx`` (by axis); the face grid runs over the
        two other axes inside the box shrunk by ``c`` (the edge band is left for the chamfer)."""
        o = [i for i in range(3) if i != fax]
        key = (fax, side, idx[o[0]], idx[o[1]])
        if key not in grid:
            p = [0.0, 0.0, 0.0]
            p[fax] = hi[fax] if side else lo[fax]
            for a_ in o:
                p[a_] = _lerp(lo[a_] + c, hi[a_] - c, idx[a_] / n_of[a_])
            grid[key] = m.v(*p)
        return grid[key]

    def face(vs) -> None:
        f = tuple(vs)
        nrm = _normal(m, f)
        mid = tuple(sum(m.verts[v][i] for v in f) / len(f) for i in range(3))
        if _dot(nrm, _sub(mid, cen)) < 0:
            f = tuple(reversed(f))
        m.f(f, role, part, uv_of(m, f))

    for fax in range(3):                                      # the six big faces
        o = [i for i in range(3) if i != fax]
        for side in (0, 1):
            for ia in range(n_of[o[0]]):
                for ib in range(n_of[o[1]]):
                    face([gv(fax, side, {o[0]: ia, o[1]: ib}), gv(fax, side, {o[0]: ia + 1, o[1]: ib}),
                          gv(fax, side, {o[0]: ia + 1, o[1]: ib + 1}), gv(fax, side, {o[0]: ia, o[1]: ib + 1})])
    for e in range(3):                                        # chamfer strips along each edge direction
        a_ax, b_ax = [i for i in range(3) if i != e]
        for sa in (0, 1):
            for sb in (0, 1):
                ia_end = n_of[a_ax] if sa else 0
                ib_end = n_of[b_ax] if sb else 0
                for k in range(n_of[e]):
                    face([gv(a_ax, sa, {e: k, b_ax: ib_end}), gv(a_ax, sa, {e: k + 1, b_ax: ib_end}),
                          gv(b_ax, sb, {e: k + 1, a_ax: ia_end}), gv(b_ax, sb, {e: k, a_ax: ia_end})])
    for sx in (0, 1):                                         # corner triangles
        for sy in (0, 1):
            for sz in (0, 1):
                s = (sx, sy, sz)
                tri = []
                for fax in range(3):
                    o = [i for i in range(3) if i != fax]
                    tri.append(gv(fax, s[fax], {i: (n_of[i] if s[i] else 0) for i in o}))
                face(tri)


# --------------------------------------------------------------------------- props and buildings


def _origin(plan: dict) -> tuple[float, float, float]:
    o = plan.get("anchors", {}).get("origin") or (0.0, 0.0, 0.0)
    return float(o[0]), float(o[1]), float(o[2])


def _prop_box(plan: dict) -> dict:
    d, p = plan["dims"], plan["profile"]
    L, W, H = float(d["L"]), float(d["W"]), float(d["H"])
    cell = float(p["cell_m"])
    div = (max(1, round(W / cell)), max(1, round(L / cell)), max(1, round(H / cell)))
    c = min(float(p["chamfer_m"]), 0.12 * min(L, W, H))
    m = Mesh()
    ox, oy, oz = _origin(plan)
    _gbox(m, (ox - W / 2, oy - L / 2, oz), (ox + W / 2, oy + L / 2, oz + H), div, c, "map", "prop", float(p["uv_tile_m"]))
    return {"pieces": [_piece("prop", m, mirror=False)], "parts": [{"name": "prop", "slot": "{model}"}]}


def _merge_cap_fans(m: Mesh) -> None:
    """Replace each fan of triangles around a pole vertex (a vertex used only by triangles) by one n-gon."""
    use: dict[int, list[int]] = {}
    for fi, f in enumerate(m.faces):
        for v in f:
            use.setdefault(v, []).append(fi)
    drop: set[int] = set()
    add: list[tuple] = []
    for v, fis in use.items():
        if len(fis) >= 3 and all(len(m.faces[fi]) == 3 for fi in fis):
            nxt = {}
            for fi in fis:
                f = m.faces[fi]
                k = f.index(v)
                nxt[f[(k + 1) % 3]] = f[(k + 2) % 3]
            start = next(iter(nxt))
            poly = [start]
            cur = nxt[start]
            while cur != start and len(poly) <= len(fis):
                poly.append(cur)
                cur = nxt.get(cur, start)
            if len(poly) == len(fis):
                drop.update(fis)
                add.append((tuple(poly), m.role[fis[0]], m.part[fis[0]]))
    if not drop:
        return
    keep = [i for i in range(len(m.faces)) if i not in drop]
    m.faces = [m.faces[i] for i in keep]
    m.role = [m.role[i] for i in keep]
    m.part = [m.part[i] for i in keep]
    m.uv = [m.uv[i] for i in keep]
    for poly, r, pt in add:
        m.f(poly, r, pt)


def _prop_cyl(plan: dict) -> dict:
    d, p = plan["dims"], plan["profile"]
    L, W, H = float(d["L"]), float(d["W"]), float(d["H"])
    r = max(L, W) / 2.0
    sides = int(p["sides"])
    c = min(float(p["chamfer_m"]), 0.25 * r, 0.12 * H)
    rings = max(0, int(round(H / float(p["cell_m"]))) - 1)
    prof = [(0.0, 0.0), (r - c, 0.0), (r, c)]
    prof += [(r, c + (H - 2 * c) * k / (rings + 1)) for k in range(1, rings + 1)]
    prof += [(r, H - c), (r - c, H), (0.0, H)]
    tile = float(p["uv_tile_m"])
    m = Mesh()
    _lathe(m, prof, sides, "map", "prop", axis=2, centre=_origin(plan), u_scale=2 * math.pi * r / tile, v_scale=H / tile)
    _merge_cap_fans(m)
    for fi, f in enumerate(m.faces):                         # the n-gon caps get planar UVs
        if len(f) > 4 or not m.uv[fi]:
            m.uv[fi] = _planar_uv(m, f, 1.0 / tile, (0.0, 0.0))
    return {"pieces": [_piece("prop", m, mirror=False)], "parts": [{"name": "prop", "slot": "{model}"}]}


def _building(plan: dict) -> dict:
    d, p = plan["dims"], plan["profile"]
    L, W, H = float(d["L"]), float(d["W"]), float(d["H"])
    storey, bay = float(p["storey_m"]), float(p["bay_m"])
    plinth, led = float(p["plinth_m"]), float(p["cornice_m"])
    nst = max(1, int(round((H - plinth - led) / storey)))
    body_h = H - led
    a0, b0 = W / 2.0, L / 2.0
    prof = [(0.0, a0 + 0.12, b0 + 0.12), (plinth, a0 + 0.12, b0 + 0.12), (plinth, a0, b0)]
    prof += [(plinth + (body_h - plinth) * k / nst, a0, b0) for k in range(1, nst)]
    prof += [(body_h, a0, b0), (body_h, a0 + 0.25, b0 + 0.25), (H - led * 0.4, a0 + 0.25, b0 + 0.25),
             (H - led * 0.4, a0 + 0.05, b0 + 0.05), (H, a0 + 0.05, b0 + 0.05)]
    nbx, nby = max(1, int(round(W / bay))), max(1, int(round(L / bay)))
    m = Mesh()

    def outline(a: float, b: float) -> list[tuple[float, float]]:
        pts = [(-a + 2 * a * i / nbx, -b) for i in range(nbx)]
        pts += [(a, -b + 2 * b * j / nby) for j in range(nby)]
        pts += [(a - 2 * a * i / nbx, b) for i in range(nbx)]
        pts += [(-a, b - 2 * b * j / nby) for j in range(nby)]
        return pts

    ox, oy, oz = _origin(plan)
    rings = [[m.v(ox + x, oy + y, oz + z) for x, y in outline(a, b)] for z, a, b in prof]
    n = len(rings[0])
    tile = 1.0 / float(p["uv_tile_m"])
    perim = 2 * W + 2 * L
    for k in range(len(rings) - 1):
        z0, z1 = prof[k][0], prof[k + 1][0]
        for i in range(n):
            i2 = (i + 1) % n
            f = (rings[k][i], rings[k][i2], rings[k + 1][i2], rings[k + 1][i])
            if abs(z1 - z0) < 1e-9:                          # a ledge: planar map from above
                m.f(f, "map", "building", _planar_uv(m, f, tile, (0.0, 0.0)))
            else:
                u0, u1 = i / n * perim * tile, (i + 1) / n * perim * tile
                m.f(f, "map", "building", ((u0, z0 * tile), (u1, z0 * tile), (u1, z1 * tile), (u0, z1 * tile)))
    top = tuple(rings[-1])
    m.f(top, "map", "building", _planar_uv(m, top, tile, (0.0, 0.0)))
    return {"pieces": [_piece("building", m, mirror=False)], "parts": [{"name": "building", "slot": "{model}"}]}


# --------------------------------------------------------------------------- bike, boat, heli, plane (lofted hulls)


#: Scooter anchors without ``--like`` (numbers of a typical SA scooter frame layout for L 1.94 x W 0.94 x H 1.33, y
#: forward, z up, ground at -0.5): wheels, steering pivot and axis, rider pelvis, lamps, exhaust tip.
_SCOOTER = {"dims": (1.94, 0.94, 1.33), "y_rear": -1.03, "ground_z": -0.5, "wheel_d": 0.464,
            "axle_y": (-0.674, 0.675), "pivot": (0.0, 0.526, 0.105), "axis": (0.0, -0.105, 0.995),
            "seat": (0.0, -0.337, 0.417), "headlights": (0.0, 0.501, 0.532), "taillights": (0.0, -0.96, 0.165),
            "exhaust": (0.127, -0.82, -0.317)}


def _with_wheel(a: dict, wheel_d: float | None) -> dict:
    """``a`` with another wheel diameter: the hubs stay on their frames, the ground moves."""
    if wheel_d:
        a["wheel_d"] = _r(float(wheel_d), 4)
        a["ground_z"] = _r(min(a["wheel_z"]) - float(wheel_d) / 2.0, 4)
    return a


def _scooter_anchors(L: float, W: float, H: float, ln: dict | None) -> dict:
    """Anchors of a scooter blank: the like model's frames (scaled to the dimensions) or the defaults."""
    if ln is not None and "forks_front" in ln["frames"] and "wheel_front" in ln["frames"]:
        fr = ln["frames"]
        Ll, Wl, Hl = ln["dims"]["L"], ln["dims"]["W"], ln["dims"]["H"]
        sx, sy, sz = W / Wl, L / Ll, H / Hl
        wd = float(ln.get("wheel_scale") or _SCOOTER["wheel_d"])

        def at(name: str, default) -> list[float]:
            p = fr.get(name)
            return [_r(p[0] * sx, 4), _r(p[1] * sy, 4), _r(p[2] * sz, 4)] if p else list(default)

        wf, wr = at("wheel_front", (0, 0, 0)), at("wheel_rear", (0, 0, 0))
        zg = min(wf[2], wr[2]) - wd / 2.0
        axis = ln.get("axes", {}).get("forks_front") or list(_SCOOTER["axis"])
        bw = (ln["bbox"][1][0] - ln["bbox"][0][0]) * sx       # over the grips (the style width is the body only)
        return {"y_rear": _r(ln["bbox"][0][1] * sy, 4), "ground_z": _r(zg, 4), "wheel_d": _r(wd, 4),
                "width": _r(max(bw, W), 4),
                "axle_y": [wr[1], wf[1]], "wheel_z": [wr[2], wf[2]], "pivot": at("forks_front", _SCOOTER["pivot"]),
                "axis": [_r(v, 4) for v in axis], "seat": at("ped_frontseat", _SCOOTER["seat"]),
                "headlights": at("headlights", _SCOOTER["headlights"]),
                "taillights": at("taillights2", at("taillights", _SCOOTER["taillights"])),
                "exhaust": at("exhaust", _SCOOTER["exhaust"])}
    L0, W0, H0 = _SCOOTER["dims"]
    sx, sy, sz = W / W0, L / L0, H / H0

    def sc(p) -> list[float]:
        return [_r(p[0] * sx, 4), _r(p[1] * sy, 4), _r(p[2] * sz, 4)]

    wd = _SCOOTER["wheel_d"] * min(sy, sz)
    zg = _SCOOTER["ground_z"] * sz
    return {"y_rear": _r(_SCOOTER["y_rear"] * sy, 4), "ground_z": _r(zg, 4), "wheel_d": _r(wd, 4), "width": _r(W, 4),
            "axle_y": [_r(v * sy, 4) for v in _SCOOTER["axle_y"]], "wheel_z": [_r(zg + wd / 2, 4)] * 2,
            "pivot": sc(_SCOOTER["pivot"]), "axis": list(_SCOOTER["axis"]), "seat": sc(_SCOOTER["seat"]),
            "headlights": sc(_SCOOTER["headlights"]), "taillights": sc(_SCOOTER["taillights"]),
            "exhaust": sc(_SCOOTER["exhaust"])}


def _cap(m: Mesh, ring: list[int], role: str, part: str, outward) -> None:
    """An n-gon over a half ring (x >= 0, ends on the mirror plane) facing ``outward``."""
    f = tuple(ring)
    n = _normal(m, f)
    if _dot(n, outward) < 0:
        f = tuple(reversed(f))
    fi = m.f(f, role, part, _planar_uv(m, f, 1.0))
    m.uv[fi] = _planar_uv(m, m.faces[fi], 1.0)


def _paint_rect(m: Mesh, fids, along: tuple[float, float], axis: int = 1) -> None:
    """Continuous paint UVs of a lofted part: u along ``axis`` (``along`` = its range) in the clean strip, v by
    height (up-facing tops in the clean band, low sides in the grime band)."""
    u0, u1 = _U_PAINT
    for fi in fids:
        uv = []
        for v in m.faces[fi]:
            p = m.verts[v]
            t = _clamp((p[axis] - along[0]) / max(along[1] - along[0], 1e-6), 0.0, 1.0)
            uv.append((round(u0 + (u1 - u0) * t, 5), round(_clamp(0.25 + p[2] * 0.6, 0.05, 0.97), 5)))
        m.uv[fi] = tuple(uv)


def _flip_all(m: Mesh, fids, probe: int, outward) -> None:
    """A lofted surface is wound one way throughout: flip all of ``fids`` when the face ``probe`` (known to be on the
    outside) does not point along ``outward``."""
    if _dot(_normal(m, m.faces[probe]), outward) >= 0:
        return
    for fi in fids:
        m.faces[fi] = tuple(reversed(m.faces[fi]))
        if m.uv[fi]:
            m.uv[fi] = tuple(reversed(m.uv[fi]))


def _ring_loft(m: Mesh, rings: list[list[tuple[float, float, float]]], role: str, part: str, *, closed: bool = False) -> list[list[int]]:
    """Quads between consecutive rings of 3-D points (same count); ``closed`` joins the last point to the first."""
    ids = [[m.v(*p) for p in ring] for ring in rings]
    npt = len(rings[0])
    for i in range(len(ids) - 1):
        for k in range(npt if closed else npt - 1):
            k2 = (k + 1) % npt
            f = (ids[i][k], ids[i][k2], ids[i + 1][k2], ids[i + 1][k])
            m.f(f, role, part, ((i / len(ids), k / npt), (i / len(ids), (k + 1) / npt),
                                ((i + 1) / len(ids), (k + 1) / npt), ((i + 1) / len(ids), k / npt)))
    return ids


def _scooter(plan: dict) -> dict:
    """A scooter built around its frames: rear cowl and floorboard as one loft, a leg shield that encloses the
    steering axis, seat, headset with the lamp and bars at the axis top, fender and fork on the front wheel, engine
    and muffler ending at the exhaust dummy, a tail lamp at the tail light dummy, wheels with a rim."""
    d, a, p = plan["dims"], plan["anchors"], plan["profile"]
    L, W, H = float(d["L"]), float(d["W"]), float(d["H"])
    n = int(p["rows"])
    sides = int(p["sides"])
    zg, yR = float(a["ground_z"]), float(a["y_rear"])
    wd = float(a["wheel_d"])
    yr, yf = (float(v) for v in a["axle_y"])
    zr, zf = (float(v) for v in a.get("wheel_z") or (zg + wd / 2, zg + wd / 2))
    pv, ax = [float(v) for v in a["pivot"]], [float(v) for v in a["axis"]]
    seat_y, seat_pz = float(a["seat"][1]), float(a["seat"][2])
    hl, tl, ex = a["headlights"], a["taillights"], a["exhaust"]
    tan = -ax[1] / max(ax[2], 1e-3)                        # the axis leans back by this much per metre of height

    def axis_y(z: float) -> float:
        return pv[1] - (z - pv[2]) * tan

    seat_z = seat_pz - 0.10                                # the rider's pelvis sits 10 cm above the seat
    cowl = seat_z - 0.07
    floor_t, floor_b = zg + 0.21, zg + 0.12
    W = float(a.get("width") or W)                         # over the grips
    hw = min(0.27, W * 0.28)
    shield_z = [floor_t - 0.04, floor_t + 0.12, floor_t + 0.34, floor_t + 0.56, float(hl[2]) - 0.08, float(hl[2]) - 0.03]
    y_shield = axis_y(floor_t) - 0.05                      # the back of the shield at the floor
    # ---- rear cowl and floorboard: one loft (y, half width, top, bottom, exponent)
    secs = [(yR, 0.05, cowl - 0.12, zr - 0.02, 2.2), (yR + 0.06, hw * 0.66, cowl - 0.04, zr - 0.05, 2.4),
            (yR + 0.18, hw * 0.94, cowl, zr - 0.07, 2.6), (seat_y - 0.15, hw, cowl + 0.005, zr - 0.06, 2.6),
            (seat_y + 0.10, hw * 0.94, cowl - 0.015, floor_b + 0.02, 2.8),
            (seat_y + 0.24, hw * 0.82, cowl - 0.07, floor_b, 2.8), (seat_y + 0.33, hw * 0.76, floor_t + 0.10, floor_b, 3.2),
            (seat_y + 0.40, hw * 0.72, floor_t, floor_b, 3.4), (y_shield - 0.02, hw * 0.72, floor_t, floor_b, 3.4),
            (y_shield + 0.04, hw * 0.64, floor_t - 0.01, floor_b + 0.01, 3.0)]
    m = Mesh()
    rings = []
    for y, w, top, bot, q in secs:
        rings.append((y, [(x, (top + bot) / 2.0 + z) for x, z in _superell(w, (top - bot) / 2.0, q, n)]))
    ids = _loft_half(m, rings, "paint1", "chassis")
    _cap(m, ids[0], "paint1", "chassis", (0.0, -1.0, 0.0))
    _cap(m, ids[-1], "paint1", "chassis", (0.0, 1.0, 0.0))
    _paint_rect(m, range(len(m.faces)), (yR, yR + L))
    # ---- leg shield: horizontal half crescents around the axis, wide at the floor, narrow under the headset
    first = len(m.faces)
    srings = []
    for k, z in enumerate(shield_z):
        c = axis_y(z)
        w = _lerp(hw * 0.98, hw * 0.62, k / (len(shield_z) - 1))
        srings.append([(0.0, c + 0.08, z), (0.45 * w, c + 0.07, z), (0.8 * w, c + 0.035, z), (w, c - 0.01, z),
                       (0.85 * w, c - 0.045, z), (0.4 * w, c - 0.05, z), (0.0, c - 0.05, z)])
    sids = _ring_loft(m, srings, "paint1", "chassis")
    _flip_all(m, range(first, len(m.faces)), first, (0.0, 1.0, 0.0))     # the first face is the front centre
    _cap(m, sids[0], "paint1", "chassis", (0.0, 0.0, -1.0))
    _cap(m, sids[-1], "paint1", "chassis", (0.0, 0.0, 1.0))
    _paint_rect(m, range(first, len(m.faces)), (yR, yR + L))
    # ---- seat: a soft loft on the cowl (the rider's contact: its top is 10 cm under the pelvis dummy)
    ms = Mesh()
    srows = [(seat_y - 0.30, 0.10, seat_z - 0.03), (seat_y - 0.24, 0.14, seat_z), (seat_y + 0.05, 0.15, seat_z),
             (seat_y + 0.18, 0.13, seat_z - 0.01), (seat_y + 0.23, 0.08, seat_z - 0.04)]
    sr = [(y, [(x, (top + cowl - 0.03) / 2.0 + z) for x, z in _superell(w, (top - cowl + 0.03) / 2.0, 2.6, max(4, n - 2))])
          for y, w, top in srows]
    sids2 = _loft_half(ms, sr, "black", "chassis")
    _cap(ms, sids2[0], "black", "chassis", (0.0, -1.0, 0.0))
    _cap(ms, sids2[-1], "black", "chassis", (0.0, 1.0, 0.0))
    # ---- engine on the right beside the rear wheel, muffler ending at the exhaust dummy, tail lamp, axle link
    me_ = Mesh()
    _gbox(me_, (0.075, yr - 0.30, zr - 0.13), (0.21, yr + 0.14, zr + 0.12), (1, 2, 1), 0.04, "black", "chassis", uv="skew")
    exx, exy, exz = (float(v) for v in ex)
    _prism(me_, (exx, exy + 0.32, exz + 0.04), (exx, exy - 0.01, exz), 0.09, 0.09, "chrome", "chassis", 8)
    tlx, tly, tlz = (float(v) for v in tl)
    _gbox(me_, (-0.07, tly - 0.02, tlz - 0.035), (0.07, tly + 0.06, tlz + 0.035), (1, 1, 1), 0.012, "lamp_rr", "chassis",
          uv="skew")
    _atlas_uvs(me_, {"chrome": "generic.chrome_pipe", "lamp_rr": "lights.rear_measured"})
    # ---- headset at the top of the axis: the headlamp at the headlights dummy, bars and grips, short mirrors
    mh = Mesh()
    hz = float(hl[2]) + 0.06
    hy = axis_y(hz)
    _gbox(mh, (-0.12, hy - 0.10, hz - 0.08), (0.12, hy + 0.11, hz + 0.08), (1, 1, 1), 0.035, "paint1", "handlebars",
          uv="skew")
    _rect_uv(mh, range(len(mh.faces)), _PAINT_DETAIL)
    lamp_y = max(float(hl[1]), hy + 0.10)
    _gbox(mh, (-0.075, lamp_y - 0.02, float(hl[2]) - 0.04), (0.075, lamp_y + 0.035, float(hl[2]) + 0.05), (1, 1, 1), 0.015,
          "lamp_fr", "handlebars", uv="skew")
    gx = W / 2.0 - 0.01
    bz = hz + 0.06
    by = axis_y(bz) - 0.02
    _prism(mh, (-gx + 0.11, by, bz), (gx - 0.11, by, bz), 0.035, 0.035, "chrome", "handlebars", 8)
    for sx in (-1.0, 1.0):
        _prism(mh, (sx * (gx - 0.13), by, bz), (sx * gx, by, bz), 0.045, 0.045, "black", "handlebars", 8)
        _prism(mh, (sx * (gx - 0.16), by, bz), (sx * (gx - 0.19), by - 0.04, min(bz + 0.12, zg + H - 0.05)),
               0.015, 0.015, "black", "handlebars", 4)
        mz = min(bz + 0.12, zg + H - 0.05)
        _gbox(mh, (sx * (gx - 0.19) - 0.05, by - 0.07, mz - 0.03), (sx * (gx - 0.19) + 0.05, by - 0.03, mz + 0.035),
              (1, 1, 1), 0.012, "chrome", "handlebars", uv="skew")
    _atlas_uvs(mh, {"chrome": "generic.chrome_pipe", "lamp_fr": "lights.front_measured"})
    # ---- front: fender over the wheel and a single fork arm from the shield down to the hub
    mf = Mesh()
    rf = wd / 2.0 + 0.045
    prof = [(-0.08, -0.03), (-0.06, 0.0), (0.0, 0.015), (0.06, 0.0), (0.08, -0.03), (0.068, -0.035), (0.05, -0.013),
            (0.0, 0.0), (-0.05, -0.013), (-0.068, -0.035)]
    arc = []
    for k in range(9):
        ang = math.radians(25.0 + 125.0 * k / 8)          # from ahead of the hub over the top to behind it
        cy, cz = math.cos(ang), math.sin(ang)
        arc.append([(x, yf + (rf + r) * cy, zf + (rf + r) * cz) for x, r in prof])
    fids = _ring_loft(mf, arc, "paint1", "forks_front", closed=True)

    a0 = math.radians(25.0)                                # face 1 of ring 0 is on the outer top of the section
    _flip_all(mf, range(len(mf.faces)), 1, (0.0, math.cos(a0), math.sin(a0)))
    _cap(mf, fids[0], "paint1", "forks_front", (0.0, math.sin(math.radians(25.0)), -math.cos(math.radians(25.0))))
    _cap(mf, fids[-1], "paint1", "forks_front", (0.0, -math.sin(math.radians(150.0)), math.cos(math.radians(150.0))))
    _rect_uv(mf, range(len(mf.faces)), _PAINT_DETAIL)
    _prism(mf, (-0.075, axis_y(floor_t + 0.10), floor_t + 0.10), (-0.075, yf, zf), 0.045, 0.06, "black",
           "forks_front", 6)
    mg = Mesh()                                            # the axle link that follows the front suspension
    _prism(mg, (-0.10, yf, zf), (0.03, yf, zf), 0.05, 0.05, "black", "mudguard", 8)
    pieces = [_piece("body", m), _piece("seat", ms, part="chassis"),
              _piece("engine", me_, part="chassis", mirror=False), _piece("handlebars", mh, part="handlebars", mirror=False),
              _piece("forks_front", mf, part="forks_front", mirror=False), _piece("mudguard", mg, part="mudguard", mirror=False)]
    tw = wd * 0.26
    for nm, y, z in (("wheel_front", yf, zf), ("wheel_rear", yr, zr)):
        mw = Mesh()
        tprof = [(wd * 0.31, -tw * 0.5), (wd * 0.36, -tw * 0.5), (wd * 0.46, -tw * 0.45), (wd * 0.5, -tw * 0.25),
                 (wd * 0.5, tw * 0.25), (wd * 0.46, tw * 0.45), (wd * 0.36, tw * 0.5), (wd * 0.31, tw * 0.5)]
        _lathe(mw, tprof, sides, "tyre", nm, axis=0, centre=(0.0, y, z))
        rprof = [(0.0, -tw * 0.38), (wd * 0.315, -tw * 0.5), (wd * 0.315, tw * 0.5), (0.0, tw * 0.38)]
        _lathe(mw, rprof, max(8, sides // 2), "rim", nm, axis=0, centre=(0.0, y, z))
        _atlas_uvs(mw, {"tyre": "tyres.sidewall"})
        pieces.append(_piece(nm, mw, part=nm, mirror=False))
    return {"pieces": pieces, "parts": [dict(x) for x in _SCOOTER_PARTS]}


_SCOOTER_PARTS = ({"name": "chassis", "slot": "chassis"}, {"name": "forks_front", "slot": "forks_front"},
                  {"name": "handlebars", "slot": "handlebars"}, {"name": "mudguard", "slot": "mudguard"},
                  {"name": "wheel_front", "slot": "wheel_front"}, {"name": "wheel_rear", "slot": "wheel_rear"})


def _bike(plan: dict) -> dict:
    d, a, p = plan["dims"], plan["anchors"], plan["profile"]
    L, W, H = float(d["L"]), float(d["W"]), float(d["H"])
    wd = float(a["wheel_d"])
    yr, yf = float(a["axle_y"][0]), float(a["axle_y"][1])
    zg, yR = float(a["ground_z"]), float(a["y_rear"])
    zc = zg + wd / 2.0
    n = int(p["rows"])
    m = Mesh()
    # (t along the length, half width share, half height share, centre height share): tail, seat, tank, fairing nose
    k = [(0.00, 0.08, 0.10, 0.52), (0.10, 0.15, 0.13, 0.55), (0.26, 0.20, 0.17, 0.53), (0.40, 0.23, 0.22, 0.52),
         (0.55, 0.26, 0.25, 0.55), (0.70, 0.22, 0.24, 0.58), (0.84, 0.20, 0.20, 0.52), (0.95, 0.12, 0.14, 0.46),
         (1.00, 0.04, 0.07, 0.42)]
    rings = []
    for t, a_, b_, c_ in k:
        pts = _superell(max(a_ / 0.26 * W / 2.0, 0.02), b_ * H * 0.5, 2.6, n)
        rings.append((yR + t * L, [(x, zg + c_ * H + z) for x, z in pts]))
    _loft_half(m, rings, lambda i, kk: "paint1" if kk < n - 1 else "black", "chassis")
    pieces = [_piece("body", m)]
    sides = int(p["sides"])
    tw = wd * 0.16
    for nm, y in (("wheel_front", yf), ("wheel_rear", yr)):
        mw = Mesh()
        prof = [(0.0, -tw * 0.4), (wd * 0.28, -tw * 0.4), (wd * 0.30, -tw), (wd * 0.46, -tw * 0.9), (wd * 0.5, -tw * 0.45),
                (wd * 0.5, tw * 0.45), (wd * 0.46, tw * 0.9), (wd * 0.30, tw), (wd * 0.28, tw * 0.4), (0.0, tw * 0.4)]
        _lathe(mw, prof, sides, "tyre", nm, axis=0, centre=(0.0, y, zc))
        pieces.append(_piece(nm, mw, part=nm, mirror=False))
    top = zg + 0.92 * H
    mf = Mesh()
    _prism(mf, (0.0, yf - 0.02, zc + 0.05), (0.0, yf - 0.16 * L, top - 0.12), 0.07, 0.07, "chrome", "forks_front", 8)
    pieces.append(_piece("forks_front", mf, part="forks_front", mirror=False))
    mh = Mesh()
    _prism(mh, (-W / 2.0, yf - 0.16 * L, top), (W / 2.0, yf - 0.16 * L, top), 0.045, 0.045, "chrome", "handlebars", 8)
    pieces.append(_piece("handlebars", mh, part="handlebars", mirror=False))
    return {"pieces": pieces, "parts": [dict(x) for x in _parts_of("bike")]}


def _boat(plan: dict) -> dict:
    d, p = plan["dims"], plan["profile"]
    L, W, H = float(d["L"]), float(d["W"]), float(d["H"])
    yR, zg = float(plan["anchors"]["y_rear"]), float(plan["anchors"]["ground_z"])
    n = int(p["rows"])
    m = Mesh()
    hull_h = 0.55 * H
    # (t, half beam share, deck height share, keel height share): transom, flare, bow
    k = [(0.00, 0.78, 0.62, 0.0), (0.06, 0.92, 0.64, 0.02), (0.20, 1.0, 0.66, 0.06), (0.45, 1.0, 0.68, 0.10),
         (0.68, 0.84, 0.72, 0.18), (0.84, 0.50, 0.78, 0.30), (0.94, 0.20, 0.84, 0.50), (1.00, 0.04, 0.90, 0.70)]
    rings = []
    for t, hb, dk, kl in k:
        top, bot = hull_h * dk, hull_h * kl
        pts = _superell(max(W / 2.0 * hb, 0.02), (top - bot) / 2.0, 2.4, n)
        rings.append((yR + t * L, [(x, zg + (top + bot) / 2.0 + z) for x, z in pts]))
    _loft_half(m, rings, lambda i, kk: "paint1" if kk < n - 2 else "black", "boat")
    cy = yR + 0.52 * L
    _gbox(m, (0.0, cy - 0.7, zg + hull_h * 0.68), (W * 0.3, cy + 0.5, zg + hull_h * 0.68 + 0.55), (1, 2, 1), 0.04, "paint1",
          "boat")
    return {"pieces": [_piece("hull", m)], "parts": [dict(x) for x in _parts_of("boat")]}


def _heli(plan: dict) -> dict:
    d, p = plan["dims"], plan["profile"]
    L, W, H = float(d["L"]), float(d["W"]), float(d["H"])
    yR, zg = float(plan["anchors"]["y_rear"]), float(plan["anchors"]["ground_z"])
    n = int(p["rows"])
    m = Mesh()
    # (t, half width share, half height share, centre height share): tail tip, boom, cabin pod, nose
    k = [(0.00, 0.03, 0.04, 0.62), (0.12, 0.04, 0.06, 0.64), (0.40, 0.07, 0.09, 0.60), (0.62, 0.20, 0.30, 0.50),
         (0.74, 0.42, 0.36, 0.42), (0.86, 0.40, 0.34, 0.40), (0.95, 0.22, 0.24, 0.40), (1.00, 0.05, 0.10, 0.40)]
    rings = []
    for t, a_, b_, c_ in k:
        pts = _superell(max(a_ * W * (0.7 if t > 0.5 else 0.5), 0.05), b_ * H, 2.2, n)
        rings.append((yR + t * L, [(x, zg + c_ * H + z) for x, z in pts]))
    _loft_half(m, rings, lambda i, kk: "glass" if (i >= 5 and kk in (1, 2)) else "paint1", "chassis")
    pieces = [_piece("fuselage", m)]
    ms = Mesh()                                              # skids and struts
    for sx in (-1, 1):
        _prism(ms, (sx * W * 0.34, yR + 0.52 * L, zg + 0.04), (sx * W * 0.34, yR + 0.95 * L, zg + 0.04), 0.07, 0.07,
               "chrome", "chassis", 8)
        for ty in (0.62, 0.84):
            _prism(ms, (sx * W * 0.34, yR + ty * L, zg + 0.06), (sx * W * 0.22, yR + ty * L, zg + 0.36 * H), 0.05, 0.05,
                   "chrome", "chassis", 4)
    pieces.append(_piece("skids", ms, part="chassis", mirror=False))
    mt = Mesh()                                              # tail fin and stabiliser
    ty0 = yR + 0.88 * L
    _gbox(mt, (-0.03, ty0, zg + 0.40 * H), (0.03, ty0 + 0.10 * L, zg + 0.70 * H), (1, 2, 2), 0.0, "paint1", "chassis")
    _gbox(mt, (-W * 0.18, ty0 + 0.02 * L, zg + 0.42 * H), (W * 0.18, ty0 + 0.08 * L, zg + 0.42 * H + 0.03), (3, 1, 1), 0.0,
          "paint1", "chassis")
    pieces.append(_piece("tail", mt, part="chassis", mirror=False))
    mr = Mesh()                                              # mast and a four-blade rotor
    ry = yR + 0.66 * L
    _prism(mr, (0.0, ry, zg + 0.72 * H), (0.0, ry, zg + 0.90 * H), 0.16, 0.16, "chrome", "chassis", 8)
    r = 0.23 * L
    for ang in (0, 90, 180, 270):
        a = math.radians(ang)
        x1, y1 = r * math.cos(a), r * math.sin(a)
        _gbox(mr, (min(0.0, x1) - 0.05, ry + min(0.0, y1) - 0.05, zg + 0.90 * H),
              (max(0.0, x1) + 0.05, ry + max(0.0, y1) + 0.05, zg + 0.90 * H + 0.04), (2, 2, 1), 0.0, "black", "chassis")
    pieces.append(_piece("rotor", mr, part="static_rotor", mirror=False))
    return {"pieces": pieces, "parts": [dict(x) for x in _parts_of("heli")]}


def _plane(plan: dict) -> dict:
    d, p = plan["dims"], plan["profile"]
    L, W, H = float(d["L"]), float(d["W"]), float(d["H"])
    yR, zg = float(plan["anchors"]["y_rear"]), float(plan["anchors"]["ground_z"])
    n = int(p["rows"])
    m = Mesh()
    # (t, radius share, centre height share): tail tip, tail cone, cabin, nose
    k = [(0.00, 0.10, 0.62), (0.10, 0.18, 0.62), (0.30, 0.30, 0.60), (0.48, 0.46, 0.58), (0.62, 0.50, 0.56),
         (0.80, 0.40, 0.55), (0.93, 0.30, 0.56), (1.00, 0.22, 0.56)]
    rings = []
    for t, rr, cc in k:
        r = max(rr * H * 0.7, 0.06)
        rings.append((yR + t * L, [(x, zg + cc * H + z) for x, z in _superell(r, r * 1.05, 2.0, n)]))
    _loft_half(m, rings, lambda i, kk: "glass" if (i in (3, 4) and kk == 1) else "paint1", "chassis")
    pieces = [_piece("fuselage", m)]
    mw = Mesh()                                              # wing: root to tip, a thin tapered aerofoil (half, x >= 0)
    wy0, wy1 = yR + 0.46 * L, yR + 0.70 * L
    wz = zg + 0.46 * H
    ids = []
    for u, ch in ((0.0, 1.00), (0.35, 0.82), (0.7, 0.62), (1.0, 0.46)):
        c = (wy1 - wy0) * ch
        y_le = wy0 + (1 - ch) * 0.3 * (wy1 - wy0)
        pts = [(y_le + c, wz), (y_le + c * 0.6, wz + 0.10 * c), (y_le + 0.1 * c, wz + 0.07 * c), (y_le, wz),
               (y_le + 0.15 * c, wz - 0.03 * c), (y_le + c * 0.7, wz - 0.02 * c)]
        ids.append([mw.v(u * W / 2.0, y, z) for y, z in pts])
    npt = len(ids[0])
    for i in range(len(ids) - 1):
        for kk in range(npt):
            k2 = (kk + 1) % npt
            mw.f((ids[i][kk], ids[i + 1][kk], ids[i + 1][k2], ids[i][k2]), "paint1", "chassis",
                 ((i / 3, kk / npt), ((i + 1) / 3, kk / npt), ((i + 1) / 3, (kk + 1) / npt), (i / 3, (kk + 1) / npt)))
    mw.f(tuple(ids[-1]), "paint1", "chassis")
    mw.uv[-1] = _planar_uv(mw, tuple(ids[-1]), 1.0)
    cy = (wy0 + wy1) / 2.0
    _orient(mw, 0, lambda mid: (mid[0], cy, wz))             # outwards = away from the wing's centre line
    pieces.append(_piece("wing", mw, part="chassis"))
    mt = Mesh()                                              # tailplane and fin
    ty0 = yR + 0.88 * L
    _gbox(mt, (0.0, ty0, zg + 0.56 * H - 0.02), (W * 0.18, ty0 + 0.10 * L, zg + 0.56 * H + 0.02), (3, 1, 1), 0.0, "paint1",
          "chassis")
    _gbox(mt, (-0.03, ty0, zg + 0.56 * H), (0.03, ty0 + 0.10 * L, zg + 0.98 * H), (1, 2, 3), 0.0, "paint1", "chassis")
    pieces.append(_piece("tail", mt, part="chassis", mirror=False))
    return {"pieces": pieces, "parts": [dict(x) for x in _parts_of("plane")]}


# --------------------------------------------------------------------------- plan (numbers)


def _dims_arg(dims, what: str = "dims") -> list[float] | None:
    if dims is None:
        return None
    if isinstance(dims, str):
        dims = [x for x in dims.replace("x", ",").replace(" ", ",").split(",") if x]
    try:
        v = [float(x) for x in dims]
    except (TypeError, ValueError):
        raise SatkError("BAD_PARAMS", f"{what} takes L,W,H in metres, got {dims!r}") from None
    if len(v) != 3 or any(not math.isfinite(x) or x <= 0 for x in v):
        raise SatkError("BAD_PARAMS", f"{what} takes L,W,H in metres (three positive numbers), got {dims!r}")
    return v


def _compose(frames: list[dict]) -> dict[str, tuple[float, float, float]]:
    """Model-space position of every named frame of a template plan (local matrices composed along the parents)."""
    world: dict[int, tuple[list[float], list[float]]] = {}
    out: dict[str, tuple[float, float, float]] = {}
    for fr in frames:
        mt = fr["matrix"]
        rot = [[mt[0], mt[3], mt[6]], [mt[1], mt[4], mt[7]], [mt[2], mt[5], mt[8]]]       # columns right, up, at
        pos = [mt[9], mt[10], mt[11]]
        pw = world.get(fr["parent"])
        if pw is not None:
            prot, ppos = pw
            rot = [[sum(prot[r][k] * rot[k][c] for k in range(3)) for c in range(3)] for r in range(3)]
            pos = [ppos[r] + sum(prot[r][k] * pos[k] for k in range(3)) for r in range(3)]
        world[fr["i"]] = (rot, pos)
        if fr.get("name"):
            out[str(fr["name"]).lower()] = (pos[0], pos[1], pos[2])
    return out


def _frame_axes(frames: list[dict]) -> dict[str, tuple[float, float, float]]:
    """Model-space direction of every named frame's local Z axis (``at``; a bike's steering axis)."""
    world: dict[int, list[list[float]]] = {}
    out: dict[str, tuple[float, float, float]] = {}
    for fr in frames:
        mt = fr["matrix"]
        rot = [[mt[0], mt[3], mt[6]], [mt[1], mt[4], mt[7]], [mt[2], mt[5], mt[8]]]
        pw = world.get(fr["parent"])
        if pw is not None:
            rot = [[sum(pw[r][k] * rot[k][c] for k in range(3)) for c in range(3)] for r in range(3)]
        world[fr["i"]] = rot
        if fr.get("name"):
            out[str(fr["name"]).lower()] = (rot[0][2], rot[1][2], rot[2][2])
    return out


def _like_numbers(like: str, name: str | None, tier: str, profile: str) -> dict:
    """Numbers of the ``--like`` model: dimensions (style metrics: chassis width), bounding box, wheel dummies and IDE
    wheel scale, kit kind. Numbers only: no vertex, face or pixel is kept."""
    from . import plan as P

    t = P.template_plan(like=like, name=name, dims=None, tier=tier, profile=profile)
    pos = _compose(t["frames"])
    d = t["dims"]
    sid = t["like"]["sid"]
    out: dict[str, Any] = {"sid": sid, "model": t["like"]["name"], "kind": t["kind"],
                           "dims": {k: float(v) for k, v in d["like"].items()},
                           "bbox": [[float(v) for v in d["bbox_like"][0]], [float(v) for v in d["bbox_like"][1]]],
                           "frames": {k: [round(v, 4) for v in p] for k, p in pos.items()
                                      if k.startswith(("wheel", "door", "bonnet", "boot", "bump", "windscreen",
                                                       "ped_", "headlights", "taillights", "forks", "handlebars",
                                                       "mudguard", "exhaust", "engine"))},
                           "axes": {k: [round(v, 4) for v in ax] for k, ax in _frame_axes(t["frames"]).items()
                                    if k in ("forks_front", "handlebars")},
                           "wheel_scale": (t.get("anchors") or {}).get("wheel_scale"), "warn": list(t.get("warn") or [])}
    try:                                    # the chassis width of the style metrics (mirrors on doors excluded)
        from ..style import cache as SC

        rec = SC.load(profile).record(int(sid.split(":", 1)[1]))
        if rec and rec["m"].get("dims.W"):
            out["dims"]["W"] = float(rec["m"]["dims.W"])
    except Exception:  # noqa: BLE001 - the bounding box width stays when there is no style cache
        out["warn"].append("INFO: no style cache: the width is the bounding box width (mirrors included)")
    return out


#: Other names of the automobile bodies (style classes and common words).
_BODY_ALIAS = {"coupe_muscle": "coupe", "suv_pickup": "suv", "truck_bus": "van", "emergency": "sedan",
               "sport": "sports", "hatch": "hatchback", "estate": "wagon", "suv_90s": "suv_boxy", "boxy": "suv_boxy",
               "truck": "pickup", "crossover": "suv"}


def _car_body_for(like_model: str | None, requested: str | None) -> str:
    bodies = _automobile()["bodies"]
    if requested:
        key = requested.strip().lower()
        key = _BODY_ALIAS.get(key, key)
        if key not in bodies:
            import difflib

            raise SatkError("BAD_PARAMS", f"no automobile body {requested!r}",
                            hint="bodies: " + ", ".join(bodies),
                            did_you_mean=difflib.get_close_matches(key, list(bodies), n=3, cutoff=0.4))
        return key
    if like_model:
        own = _automobile().get("like_body", {}).get(like_model.lower())
        if own:
            return own
        try:
            from ..style import classes as C

            body = C.taxonomy()["body_of"].get(like_model.lower())
        except Exception:  # noqa: BLE001 - the class is a hint only
            body = None
        if body:
            return _car_body_for(None, body)
    return "sedan"


def _automobile() -> dict:
    return _data("automobile.json")


def _tier_check(tier: str) -> str:
    t = str(tier or "sa_plus").strip().lower().replace("-", "_").replace("+", "_plus")
    if t == "saplus":
        t = "sa_plus"
    if t not in TIERS:
        raise SatkError("BAD_PARAMS", f"tier must be one of {', '.join(TIERS)}, got {tier!r}")
    return t


def _car_plan(like: str | None, name: str | None, dims, tier: str, body: str | None, profile: str,
              wheel_d: float | None) -> dict:
    warn: list[str] = []
    ln = _like_numbers(like, name, tier, profile) if like else None
    if ln is not None and ln["kind"] not in ("automobile", "mtruck", "trailer"):
        raise SatkError("BAD_PARAMS", f"--like {like} is a {ln['kind']}: an automobile blank needs a car",
                        hint="pick a car (satk kit kinds --kind automobile) or leave --like out")
    key = _car_body_for(ln["model"] if ln else None, body)
    prof = _automobile()["bodies"][key]
    dd = prof["dims"]
    dv = _dims_arg(dims)
    if ln is not None:
        fr = ln["frames"]
        try:
            rf, rb = fr["wheel_rf_dummy"], fr["wheel_rb_dummy"]
        except KeyError:
            raise SatkError("BAD_PARAMS", f"{ln['sid']} has no wheel dummies") from None
        Ll, Wl, Hl = ln["dims"]["L"], ln["dims"]["W"], ln["dims"]["H"]
        L, W, H = dv or [Ll, Wl, Hl]
        sx, sy, sz = W / Wl, L / Ll, H / Hl
        wd = float(wheel_d or ln["wheel_scale"] or dd["wheel_d"])
        zg_l = rf[2] - wd / 2.0
        axle = [rb[1] * sy, rf[1] * sy]
        track = 2.0 * abs(rf[0]) * sx
        zg = zg_l * sz
        wheel_z = zg + wd / 2.0
        y_rear = ln["bbox"][0][1] * sy
        like_out = {"sid": ln["sid"], "name": ln["model"]}
        warn += ln["warn"]
    else:
        L, W, H = dv or [dd["L"], dd["W"], dd["H"]]
        wd = float(wheel_d or dd["wheel_d"])
        wb = dd["wheelbase"] * L / dd["L"]
        track = dd["track"] * W / dd["W"]
        y_rear = -(prof["axle_rear_t"] * L + wb / 2.0)
        axle = [y_rear + prof["axle_rear_t"] * L, y_rear + prof["axle_rear_t"] * L + wb]
        zg = -0.455 * H
        wheel_z = zg + wd / 2.0
        like_out = None
    import copy

    tr = copy.deepcopy(_automobile()["tiers"][tier])
    pr = tr.pop("profile", {})
    for k, v in copy.deepcopy(prof).items():      # the tier's numbers are defaults: the body's own values win
        if isinstance(v, dict) and isinstance(pr.get(k), dict):
            pr[k].update(v)
        else:
            pr[k] = v
    pr["tier"] = tr
    return {
        "dims": {"L": _r(L, 3), "W": _r(W, 3), "H": _r(H, 3)},
        "anchors": {"wheel_d": _r(wd, 3), "wheelbase": _r(axle[1] - axle[0], 3), "track": _r(track, 3),
                    "axle_y": [_r(axle[0], 3), _r(axle[1], 3)], "wheel_z": _r(wheel_z, 3), "ground_z": _r(zg, 3),
                    "y_rear": _r(y_rear, 3)},
        "body": key, "profile": pr, "like": like_out, "warn": warn,
    }


def blank_plan(kind: str, *, name: str | None = None, like: str | None = None, dims=None, tier: str = "sa_plus",
               body: str | None = None, interior: bool = True, wheel_d: float | None = None,
               profile: str = "vanilla") -> dict:
    """The plan of a blank: kind, name, tier, dimensions and anchors in metres. ``like`` (a SID) only supplies numbers.

    Args:
        kind: one of :data:`KINDS`.
        name: model name (a-z 0-9 _); default ``blank_<kind>``.
        like: a vanilla model (SID or name) whose class dimensions, wheel dummies and IDE wheel scale are used.
        dims: ``[L, W, H]`` in metres (or ``"L,W,H"``): overrides the class (or ``like``) dimensions.
        tier: ``sa_plus`` (default) or ``vanilla``: the density of the loops.
        body: automobile body (``sedan``, ``coupe``, ...), default from ``like`` or ``sedan``.
        interior: automobile only, add the seat/dash piece.
        wheel_d: wheel diameter in metres (vehicles), default the like model's wheel scale or the class.
        profile: game profile that resolves ``like``.
    """
    k = _kind(kind)
    tier = _tier_check(tier)
    dv = _dims_arg(dims)
    nm = (name or f"blank_{k['name']}").strip().lower()
    import re

    if not re.fullmatch(r"[a-z0-9_]{1,17}", nm):
        raise SatkError("BAD_PARAMS", f"name: 1-17 characters a-z 0-9 _, got {name!r}")
    plan: dict[str, Any] = {"version": 1, "kind": k["name"], "name": nm, "tier": tier, "group": k["group"]}
    if k["name"] == "automobile":
        plan.update(_car_plan(like, nm, dv, tier, body, profile, wheel_d))
        plan["interior"] = bool(interior)
    else:
        plan.update(_other_plan(k, nm, like, dv, tier, profile, body, wheel_d))
    parts = _SCOOTER_PARTS if plan.get("body") == "scooter" else _parts_of(k["name"])
    plan["slots"] = {p["name"]: part_slot(p, None, nm) for p in parts}
    return plan


def _parts_of(kind: str) -> list[dict]:
    if kind == "automobile":
        return [dict(p) for p in _CAR_PARTS]
    return [dict(p) for p in kinds()["kinds"][kind].get("parts", [])]


def _bike_body(k: dict, like_model: str | None, requested: str | None) -> str:
    bodies = k.get("bodies") or {"sport": ""}
    if requested:
        key = requested.strip().lower()
        key = {"moped": "scooter", "vespa": "scooter", "sportbike": "sport", "motorbike": "sport"}.get(key, key)
        if key not in bodies:
            import difflib

            raise SatkError("BAD_PARAMS", f"no bike body {requested!r}", hint="bodies: " + ", ".join(bodies),
                            did_you_mean=difflib.get_close_matches(key, list(bodies), n=3, cutoff=0.4))
        return key
    return str((k.get("like_body") or {}).get((like_model or "").lower()) or "sport")


def _other_plan(k: dict, name: str, like: str | None, dims, tier: str, profile: str, body: str | None = None,
                wheel_d: float | None = None) -> dict:
    """Dimensions, anchors and the tier's numbers of a blank that is not an automobile."""
    kind = k["name"]
    warn: list[str] = []
    ln = _like_numbers(like, name, tier, profile) if like else None
    base = k["dims"]
    dv = _dims_arg(dims)
    if body and kind != "bike":
        raise SatkError("BAD_PARAMS", f"body {body!r}: only automobile and bike blanks have bodies")
    bike_body = _bike_body(k, ln["model"] if ln else None, body) if kind == "bike" else None
    if bike_body == "scooter":
        if ln is not None and ln["kind"] != "bike":
            warn.append(f"KIND_MISMATCH: {ln['sid']} is a {ln['kind']}, the blank is a bike")
        L, W, H = dv or ([ln["dims"]["L"], ln["dims"]["W"], ln["dims"]["H"]] if ln else list(_SCOOTER["dims"]))
        return {"dims": {"L": _r(L, 3), "W": _r(W, 3), "H": _r(H, 3)}, "body": "scooter",
                "warn": warn + list((ln or {}).get("warn", [])),
                "like": {"sid": ln["sid"], "name": ln["model"]} if ln else None, "profile": dict(k["tiers"][tier]),
                "anchors": _with_wheel(_scooter_anchors(L, W, H, ln), wheel_d)}
    if ln is not None:
        if k["group"] == "vehicle" and ln["kind"] != k.get("kit_kind"):
            warn.append(f"KIND_MISMATCH: {ln['sid']} is a {ln['kind']}, the blank is a {kind}")
        L, W, H = dv or [ln["dims"]["L"], ln["dims"]["W"], ln["dims"]["H"]]
        warn += ln["warn"]
        like_out = {"sid": ln["sid"], "name": ln["model"]}
    else:
        L, W, H = dv or [base["L"], base["W"], base["H"]]
        like_out = None
    out: dict[str, Any] = {"dims": {"L": _r(L, 3), "W": _r(W, 3), "H": _r(H, 3)}, "like": like_out, "warn": warn,
                           "profile": dict(k["tiers"][tier])}
    if bike_body:
        out["body"] = bike_body
    a: dict[str, Any] = {}
    if k["group"] == "vehicle":
        zg = -0.45 * H
        a["y_rear"] = _r(-L / 2.0, 3)
        a["ground_z"] = _r(zg, 3)
        if kind == "bike":
            wd = float(wheel_d or (ln or {}).get("wheel_scale") or 0.68)
            fr = (ln or {}).get("frames") or {}
            if "wheel_front" in fr and "wheel_rear" in fr:
                axle = [fr["wheel_rear"][1], fr["wheel_front"][1]]
                a["y_rear"] = _r(ln["bbox"][0][1], 3)
                a["ground_z"] = _r(fr["wheel_front"][2] - wd / 2.0, 3)
            else:
                wb = 0.64 * L
                axle = [-L / 2.0 + 0.17 * L, -L / 2.0 + 0.17 * L + wb]
            a.update(wheel_d=_r(wd, 3), axle_y=[_r(axle[0], 3), _r(axle[1], 3)], wheelbase=_r(axle[1] - axle[0], 3))
    else:
        a["ground_z"] = 0.0
        if ln is not None:                       # the like model's own origin: centre of its footprint, its lowest z
            lo, hi = ln["bbox"]
            a["origin"] = [_r((lo[0] + hi[0]) / 2.0, 3), _r((lo[1] + hi[1]) / 2.0, 3), _r(lo[2], 3)]
    out["anchors"] = a
    return out


def mesh_spec(plan: dict) -> dict:
    """The pieces of a plan (see the module docstring): ``{"kind", "name", "parts", "pieces": [...]}``."""
    kind = plan["kind"]
    gen = {"automobile": _car, "bike": _scooter if plan.get("body") == "scooter" else _bike, "boat": _boat,
           "heli": _heli, "plane": _plane, "prop_box": _prop_box, "prop_cyl": _prop_cyl,
           "building_box": _building}.get(kind)
    if gen is None:
        raise SatkError("NOT_FOUND", f"no generator for blank kind {kind!r}")
    out = gen(plan)
    out.update(kind=kind, name=plan["name"], tier=plan["tier"])
    return out


def check_spec(spec: dict) -> dict:
    """Topology numbers of a spec for tests: per piece ``tris``, ``open_edges``, ``nonmanifold``, ``degenerate``."""
    rows = {}
    for p in spec["pieces"]:
        ec: dict[tuple[int, int], int] = {}
        degenerate = 0
        mm = Mesh()
        mm.verts = [tuple(v) for v in p["verts"]]
        directed: dict[tuple[int, int], list[int]] = {}
        for f in p["faces"]:
            for a, b in zip(f, f[1:] + f[:1]):
                e = (a, b) if a < b else (b, a)
                ec[e] = ec.get(e, 0) + 1
                directed.setdefault(e, []).append(1 if a < b else -1)
            nx, ny, nz = _normal(mm, tuple(f))
            if nx * nx + ny * ny + nz * nz < 1e-12:
                degenerate += 1
        flipped = sum(1 for e, dirs in directed.items() if len(dirs) == 2 and dirs[0] == dirs[1])
        rows[p["name"]] = {"tris": tri_count(p["faces"]), "faces": len(p["faces"]), "verts": len(p["verts"]),
                           "flipped_edges": flipped,
                           "open_edges": sum(1 for c in ec.values() if c == 1),
                           "nonmanifold": sum(1 for c in ec.values() if c > 2), "degenerate": degenerate}
    return rows
