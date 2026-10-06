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
    return walls


def _jambs(m: Mesh, part: str, depth: float, role: str, into: str, *, axis_x: bool = True) -> list[int]:
    """Jamb walls around the faces of ``part`` (the opening it will leave in the shell): the boundary vertices are copied
    ``depth`` inwards along the part's normals and joined to the old ones by quads that belong to ``into``; the part
    itself stays where it is. Boundary edges on the mirror plane (x = 0) get no wall."""
    fids = [i for i, p in enumerate(m.part) if p == part]
    region = set(fids)
    nrm = _vertex_normals(m, fids)
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
#: Paint V (Blender, 0 = bottom of the image) of the rows on ``vehiclegrunge256``: up-facing panels map to the clean
#: upper band, the sides to the grime band that rises from the bottom edge, the underbody to the bottom strip; the
#: three islands meet at the shoulder and at the sill, which are UV seams.
_V_TOP = {"top_c": 0.97, "top_q": 0.93, "top_s": 0.88}
_V_SIDE = {"top_s": 0.62, "glass_m": 0.58, "belt": 0.52, "door_m": 0.32, "rocker": 0.12, "sill": 0.055}
_V_UNDER = {"sill": 0.04, "floor_c": 0.0}
_ARCH_P = 2.4        # super-ellipse exponent of the wheel opening (a stadium-like arch)
_MIN_STATION_M = 0.03

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
    """Stations along the length: key loops (parts, pillars, nose and tail rounding), subdivisions, arch columns."""
    st = body["stations"]
    pl = body["plan"]
    key = {0.0, 1.0} | {float(v) for v in st.values()}
    for s in tier["ring"]:                  # rounding rings: distances from the ends as shares of the rounding length
        key.add(_r(1.0 - s * pl["front_m"] / L, 6))
        key.add(_r(s * pl["rear_m"] / L, 6))
    keys = sorted(key)
    keys = [t for i, t in enumerate(keys) if i == 0 or (t - keys[i - 1]) * L >= 0.012]
    pts: list[tuple[float, bool]] = []
    for t0, t1 in zip(keys, keys[1:]):
        pts.append((t0, True))
        span = (t1 - t0) * L
        n = max(1, int(round(span / float(tier["cell_m"])))) if span >= 0.2 else 1
        for k in range(1, n):
            pts.append((t0 + (t1 - t0) * k / n, False))
    pts.append((keys[-1], True))
    cols = int(tier["arch_cols"])
    for ya in a["axle_y"]:
        for k in range(cols):
            u = -1.0 + 2.0 * k / (cols - 1)
            pts.append(((ya + ra * u - y_rear) / L, False))
    pts.sort(key=lambda p: (p[0], not p[1]))
    out: list[dict] = []
    for t, is_key in pts:
        if out and (t - out[-1]["t"]) * L < _MIN_STATION_M:
            if is_key and not out[-1]["key"]:
                out[-1].update(t=t, key=True)
            continue
        out.append({"t": t, "key": is_key})
    out[0]["t"], out[-1]["t"] = 0.0, 1.0
    return out


def _car(plan: dict) -> dict:
    body = plan["profile"]
    tier = body["tier"]
    d, a = plan["dims"], plan["anchors"]
    L, W, H = float(d["L"]), float(d["W"]), float(d["H"])
    hw = W / 2.0
    zg, yR = float(a["ground_z"]), float(a["y_rear"])
    yF = yR + L
    wd = float(a["wheel_d"])
    ra = wd / 2.0 + float(body["wheel_clear"])
    zc = float(a.get("wheel_z", zg + wd / 2.0)) - zg                  # wheel centre above the ground
    h_crown = zc + ra
    pl = body["plan"]
    top_k, belt_k = body["top"], body["belt"]
    creases = body["crease_t"]
    floor_k = body.get("floor") or [[0.0, 0.30], [0.035, 0.19], [0.965, 0.19], [1.0, 0.31]]
    sill0 = float(body["sill_h"])
    stn = body["stations"]
    deck_h = float(_curve(top_k, float(stn["rear_window_base"]), creases)) * H
    stations = _car_stations(body, L, yR, tier, a, ra)
    n = len(stations)
    crown = float(body.get("crown", 0.05))
    crown_roof = float(body.get("crown_roof", crown))
    pillow = float(body.get("pillow", 0.0))
    bulge = float(body.get("bulge", 1.01))
    ring_names = list(body.get("uv_rings") or [])
    ring_t = sorted(float(stn[nm]) for nm in ring_names if nm in stn)
    ring_gap = float(body.get("uv_ring_gap", 0.015))
    all_rings = bool(body.get("uv_all_rings"))
    row_seam = float(body.get("uv_row_gap", 0.0))
    names = list(body.get("rows") or _ROWS)
    ri = {nm: i for i, nm in enumerate(names)}
    row_seams = [nm for nm in body.get("uv_row_seams", []) if nm in ri] if row_seam else []
    nrow = len(names)

    # ---- the section of every station: rows of (x, z)
    secs: list[list[tuple[float, float]]] = []
    cab: list[float] = []
    inside: list[bool] = []
    for s in stations:
        t = s["t"]
        y = yR + t * L
        f = _plan_factor(y, yR, yF, pl["rear_m"], pl["front_m"], pl["exp"])
        h_top = _curve(top_k, t, creases) * H
        c = _clamp((h_top - deck_h) / max(H - deck_h, _EPS), 0.0, 1.0)
        if pillow:                                         # an arch along the length of the bonnet and of the boot
            for t0, t1 in ((stn["cowl"], stn["hood_end"]), (stn["boot_end"], stn["rear_window_base"])):
                if t0 < t < t1:
                    h_top += pillow * 4.0 * ((t - t0) / (t1 - t0)) * (1.0 - (t - t0) / (t1 - t0))
        belt = min(_curve(belt_k, t, ()) * H, h_top - 0.045)
        h_floor = _curve(floor_k, t, ())
        h_sill = max(sill0, h_floor + 0.05)
        h_rock = h_sill + 0.05
        u = min(((y - ya) / ra for ya in a["axle_y"]), key=abs)
        zb = h_rock + (h_crown - h_rock) * _arch_g(u)
        inside.append(abs(u) < 1.0 - 1e-9)
        cr = _lerp(crown, crown_roof, c)
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
            "sill": (xb * 0.84 * f, h_sill),
            "floor_s": (xb * 0.45 * f, (h_sill + h_floor) / 2.0),
            "floor_c": (0.0, h_sill + 0.03),
        }
        secs.append([(rowd[nm][0], zg + rowd[nm][1]) for nm in names])
        cab.append(c)

    m = Mesh()
    vid = [[m.v(x, yR + stations[i]["t"] * L, z) for x, z in secs[i]] for i in range(n)]

    def roles_parts(i: int, up: str) -> tuple[str, str]:
        tc = (stations[i]["t"] + stations[i + 1]["t"]) / 2.0
        c = (cab[i] + cab[i + 1]) / 2.0
        hood, boot = tc >= stn["cowl"], tc <= stn["rear_window_base"]
        wind = stn["roof_front"] <= tc <= stn["cowl"]
        rglass = stn["roof_rear"] <= tc <= stn["rear_window_base"]
        roof = stn["roof_rear"] <= tc <= stn["roof_front"]
        door_f = stn["door_f_rear"] <= tc <= stn["cowl"]
        door_r = stn["door_r_rear"] <= tc <= stn["door_r_front"]
        pillar_b = stn["door_r_front"] <= tc <= stn["door_f_rear"]
        bump_r = tc <= stn["bump_rear_end"]
        bump_f = tc >= stn["bump_front_start"]
        hb = "bonnet" if hood else "boot" if boot else "chassis"
        if up == "top_c":
            if wind:
                return "glass", "windscreen"
            if rglass:
                return "glass", "chassis"
            return "paint1", "chassis" if roof else hb
        if up == "top_q":
            return "paint1", hb
        if up in ("top_s", "glass_m"):
            if door_f or door_r:
                return ("glass" if c > 0.25 else "paint1"), ("door_f" if door_f else "door_r")
            if pillar_b:
                return ("black" if c > 0.25 else "paint1"), "chassis"
            return "paint1", "chassis"
        if up in ("belt", "door_m"):
            if up == "door_m" and bump_f:
                return "chrome", "bump_front"
            if up == "door_m" and bump_r:
                return "chrome", "bump_rear"
            if door_f:
                return "paint1", "door_f"
            if door_r:
                return "paint1", "door_r"
            return "paint1", "chassis"
        if up == "rocker":
            return "trim", "bump_front" if bump_f else "bump_rear" if bump_r else "chassis"
        return "black", "bump_front" if bump_f else "bump_rear" if bump_r else "chassis"

    cell: dict[tuple[int, str], int] = {}
    for i in range(n - 1):
        ui, uj = stations[i]["t"], stations[i + 1]["t"]
        for k in range(nrow - 1):
            up, lo = names[k], names[k + 1]
            if up == "rocker" and (inside[i] or inside[i + 1]):
                continue                                  # the wheel opening: no rocker strip under the arch
            tab = _V_TOP if up in _V_TOP and lo in _V_TOP else _V_UNDER if up == "sill" else _V_SIDE
            v0, v1 = tab[up], (tab[lo] if lo in tab else _V_UNDER[lo])
            role, part = roles_parts(i, up)
            sh = ring_gap * (i if all_rings else sum(1 for r in ring_t if r <= (ui + uj) / 2.0))   # a UV island per ring
            vs = row_seam * sum(1 for nm in row_seams if ri[nm] <= k)       # a UV step after the rows in uv_row_seams
            cell[(i, up)] = m.f((vid[i][k], vid[i][k + 1], vid[i + 1][k + 1], vid[i + 1][k]), role, part,
                                ((ui + sh, v0 + vs), (ui + sh, v1 + vs), (uj + sh, v1 + vs), (uj + sh, v0 + vs)))
    for i in range(n - 1):
        m.mark_seam(vid[i][ri["sill"]], vid[i + 1][ri["sill"]])
        m.mark_seam(vid[i][ri["top_s"]], vid[i + 1][ri["top_s"]])

    # ---- layered details: recessed glass, lamps and grille, wheel wells, mirrors
    det = body.get("detail", {})
    ycell = {key: sum(m.verts[v][1] for v in m.faces[fi]) / 4.0 for key, fi in cell.items()}
    xcell = {key: sum(abs(m.verts[v][0]) for v in m.faces[fi]) / 4.0 for key, fi in cell.items()}
    if det.get("window_depth"):
        for part in ("door_f", "door_r", "windscreen", "chassis"):
            fs = [fi for key, fi in cell.items() if m.role[fi] == "glass" and m.part[fi] == part]
            if fs:
                _inset(m, fs, -float(det["window_depth"]), "black")
    if det.get("lamp_depth"):
        lamp_len = float(det.get("lamp_len", 0.85))
        for end in (1, -1):
            sel = [fi for (i, k), fi in cell.items() if k == "belt" and m.part[fi] == "chassis"
                   and ((end > 0 and ycell[(i, k)] > yF - lamp_len) or (end < 0 and ycell[(i, k)] < yR + lamp_len - 0.05))
                   and 0.30 * hw < xcell[(i, k)] < 0.95 * hw]
            for fi in sel:
                m.role[fi] = "lens"
            if sel:
                _inset(m, sel, -float(det["lamp_depth"]), "black")
        grille = [fi for (i, k), fi in cell.items() if k == "belt" and m.part[fi] == "chassis"
                  and ycell[(i, k)] > yF - lamp_len and xcell[(i, k)] < 0.30 * hw]
        for fi in grille:
            m.role[fi] = "black"
        if grille:
            _inset(m, grille, -float(det["lamp_depth"]), "black")
    if det.get("well_depth"):
        _arch_wells(m, vid, inside, float(det["well_depth"]), ri["rocker"], int(det.get("well_segments", 1)))
    if det.get("jamb_depth"):
        for part in ("door_f", "door_r", "bonnet", "boot", "windscreen"):
            _jambs(m, part, float(det["jamb_depth"]), "black", "chassis")
    if det.get("mirror"):
        t_c = float(stn["cowl"])
        zb = zg + _curve(belt_k, t_c, ()) * H
        _gbox(m, (hw + 0.02, yR + t_c * L - 0.30, zb + 0.06), (hw + 0.17, yR + t_c * L - 0.12, zb + 0.17), (1, 1, 1),
              0.03, "paint1", "door_f", uv="skew")

    pieces = [_piece("body", m)]
    if plan.get("interior", True):
        pieces.append(_piece("interior", _car_interior(a, zg, yR, L, hw, det), part="chassis"))
    if det.get("exhaust", True):                          # one tailpipe on the right, under the rear bumper
        mx = Mesh()
        _prism(mx, (hw * 0.5, yR + 0.55, zg + 0.24), (hw * 0.5, yR + 0.04, zg + 0.24), 0.08, 0.08, "chrome", "exhaust", 6)
        pieces.append(_piece("exhaust", mx, part="exhaust", mirror=False))
    return {"pieces": pieces, "parts": [dict(p) for p in _CAR_PARTS]}


def _arch_wells(m: Mesh, vid: list[list[int]], inside: list[bool], depth: float, row: int, segs: int = 1) -> None:
    """Strips of quads from each arch edge (the rocker row over the opening) inwards and upwards: the wheel house tub."""
    n = len(vid)
    i = 0
    while i < n:
        if not inside[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and inside[j + 1]:
            j += 1
        lo, hi = max(i - 1, 0), min(j + 1, n - 1)            # include the end columns of the arch
        prev = {s: vid[s][row] for s in range(lo, hi + 1)}
        for k in range(1, segs + 1):
            f = k / segs
            cur = {}
            for s in range(lo, hi + 1):
                x, y, z = m.verts[vid[s][row]]
                cur[s] = m.v(x - depth * f, y, z + 0.06 * f * f)
            for s in range(lo, hi):
                quad = (prev[s], cur[s], cur[s + 1], prev[s + 1])
                m.f(quad, "black", "chassis", _skew_uv(m, quad))
            prev = cur
        i = j + 1


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
    }


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
            "tunnel": ((0.0, ya_r + 0.35, zg + 0.20), (0.22, ya_f - 0.40, fl - 0.02)),
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
                                                       "ped_", "headlights", "taillights"))},
                           "wheel_scale": (t.get("anchors") or {}).get("wheel_scale"), "warn": list(t.get("warn") or [])}
    try:                                    # the chassis width of the style metrics (mirrors on doors excluded)
        from ..style import cache as SC

        rec = SC.load(profile).record(int(sid.split(":", 1)[1]))
        if rec and rec["m"].get("dims.W"):
            out["dims"]["W"] = float(rec["m"]["dims.W"])
    except Exception:  # noqa: BLE001 - the bounding box width stays when there is no style cache
        out["warn"].append("INFO: no style cache: the width is the bounding box width (mirrors included)")
    return out


def _car_body_for(like_model: str | None, requested: str | None) -> str:
    bodies = _automobile()["bodies"]
    if requested:
        key = requested.strip().lower()
        alias = {"coupe_muscle": "coupe", "suv_pickup": "suv", "truck_bus": "van", "wagon": "sedan",
                 "emergency": "sedan", "sport": "sports"}
        key = alias.get(key, key)
        if key not in bodies:
            import difflib

            raise SatkError("BAD_PARAMS", f"no automobile body {requested!r}",
                            hint="bodies: " + ", ".join(bodies),
                            did_you_mean=difflib.get_close_matches(key, list(bodies), n=3, cutoff=0.4))
        return key
    if like_model:
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

    pr = copy.deepcopy(prof)
    tr = copy.deepcopy(_automobile()["tiers"][tier])
    ov = tr.pop("profile", {})
    for k, v in ov.items():                       # the tier's own shape and detail numbers over the body's
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
        plan.update(_other_plan(k, nm, like, dv, tier, profile))
    plan["slots"] = {p["name"]: part_slot(p, None, nm) for p in _parts_of(k["name"])}
    return plan


def _parts_of(kind: str) -> list[dict]:
    if kind == "automobile":
        return [dict(p) for p in _CAR_PARTS]
    return [dict(p) for p in kinds()["kinds"][kind].get("parts", [])]


def _other_plan(k: dict, name: str, like: str | None, dims, tier: str, profile: str) -> dict:
    """Dimensions, anchors and the tier's numbers of a blank that is not an automobile."""
    kind = k["name"]
    warn: list[str] = []
    ln = _like_numbers(like, name, tier, profile) if like else None
    base = k["dims"]
    dv = _dims_arg(dims)
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
    a: dict[str, Any] = {}
    if k["group"] == "vehicle":
        zg = -0.45 * H
        a["y_rear"] = _r(-L / 2.0, 3)
        a["ground_z"] = _r(zg, 3)
        if kind == "bike":
            wd = float((ln or {}).get("wheel_scale") or 0.68)
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
    gen = {"automobile": _car, "bike": _bike, "boat": _boat, "heli": _heli, "plane": _plane, "prop_box": _prop_box,
           "prop_cyl": _prop_cyl, "building_box": _building}.get(kind)
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
