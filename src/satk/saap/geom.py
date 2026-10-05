"""Camera geometry for SAAP poses (stdlib only): pose parsing, bases, projection, rays.

Conventions (SAAP-v1.md §6): Z up, right-handed; ``yaw = 0`` looks along +Y and grows
counter-clockwise seen from above (GTA heading); positive pitch looks up; roll rotates the image
clockwise. ``fov_h_deg`` is the horizontal FOV of the produced image.

Example::

    cam = Camera.from_pose({"pos": [0, 0, 10], "ypr": [90, -10, 0]}, w=800, h=600, fov_h_deg=70)
    px = cam.project((5, 20, 0))            # (x, y, depth) or None when behind the camera
    origin, d = cam.ray(400, 300)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

from ..core.errors import SatkError

__all__ = [
    "Vec",
    "add", "sub", "mul", "dot", "cross", "length", "normalize", "dist",
    "dir_from_ypr", "ypr_from_dir",
    "parse_pose", "pose_dict",
    "Camera",
    "quat_to_dir",
    "frame_sphere",
    "ray_aabb",
]

Vec = tuple[float, float, float]
DEFAULT_FOV = 70.0
NEAR = 0.1


def add(a: Sequence[float], b: Sequence[float]) -> Vec:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def sub(a: Sequence[float], b: Sequence[float]) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def mul(a: Sequence[float], s: float) -> Vec:
    return (a[0] * s, a[1] * s, a[2] * s)


def dot(a: Sequence[float], b: Sequence[float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a: Sequence[float], b: Sequence[float]) -> Vec:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def length(a: Sequence[float]) -> float:
    return math.sqrt(dot(a, a))


def normalize(a: Sequence[float]) -> Vec:
    n = length(a)
    if n < 1e-12:
        raise SatkError("BAD_PARAMS", "zero-length direction")
    return (a[0] / n, a[1] / n, a[2] / n)


def dist(a: Sequence[float], b: Sequence[float]) -> float:
    return length(sub(a, b))


def dir_from_ypr(yaw: float, pitch: float) -> Vec:
    """Unit view direction for yaw/pitch in degrees."""
    y, p = math.radians(yaw), math.radians(pitch)
    return (-math.sin(y) * math.cos(p), math.cos(y) * math.cos(p), math.sin(p))


def ypr_from_dir(d: Sequence[float]) -> tuple[float, float]:
    """(yaw, pitch) in degrees of a direction (inverse of :func:`dir_from_ypr`)."""
    x, y, z = normalize(d)
    yaw = math.degrees(math.atan2(-x, y))
    pitch = math.degrees(math.asin(max(-1.0, min(1.0, z))))
    return yaw, pitch


def _vec3(v, what: str) -> Vec:
    if not isinstance(v, (list, tuple)) or len(v) != 3:
        raise SatkError("BAD_PARAMS", f"{what} must be [x, y, z]")
    try:
        out = (float(v[0]), float(v[1]), float(v[2]))
    except (TypeError, ValueError):
        raise SatkError("BAD_PARAMS", f"{what} must contain numbers") from None
    if not all(math.isfinite(c) for c in out):
        raise SatkError("BAD_PARAMS", f"{what} must be finite")
    return out


@dataclass(frozen=True)
class Pose:
    """Normalized pose: position, unit forward, roll (deg), look distance, optional FOV."""

    pos: Vec
    forward: Vec
    roll: float = 0.0
    look_dist: float = 10.0
    fov_h_deg: float | None = None

    @property
    def look(self) -> Vec:
        return add(self.pos, mul(self.forward, self.look_dist))

    @property
    def ypr(self) -> tuple[float, float, float]:
        y, p = ypr_from_dir(self.forward)
        return (y, p, self.roll)


def parse_pose(pose: dict, fov_h_deg: float | None = None) -> Pose:
    """Validate a SAAP Pose (``{pos, look}`` | ``{pos, ypr}``); ``BAD_PARAMS`` otherwise."""
    if not isinstance(pose, dict) or "pos" not in pose:
        raise SatkError("BAD_PARAMS", "pose needs 'pos' and one of 'look'/'ypr'")
    pos = _vec3(pose["pos"], "pose.pos")
    has_look, has_ypr = "look" in pose, "ypr" in pose
    if has_look == has_ypr:
        raise SatkError("BAD_PARAMS", "pose needs exactly one of 'look' and 'ypr'")
    fov = fov_h_deg if fov_h_deg is not None else pose.get("fov_h_deg")
    if fov is not None:
        fov = float(fov)
        if not 0 < fov < 180:
            raise SatkError("BAD_PARAMS", "fov_h_deg must be in (0, 180)")
    if has_look:
        look = _vec3(pose["look"], "pose.look")
        d = sub(look, pos)
        n = length(d)
        if n < 1e-3:
            raise SatkError("BAD_PARAMS", "pose.look must differ from pose.pos")
        return Pose(pos, mul(d, 1.0 / n), 0.0, n, fov)
    ypr = _vec3(pose["ypr"], "pose.ypr")
    return Pose(pos, dir_from_ypr(ypr[0], ypr[1]), ypr[2], 10.0, fov)


def _r(x: float, nd: int) -> float:
    v = round(float(x), nd)
    return 0.0 if v == 0 else v


def pose_dict(p: Pose, fov_h_deg: float | None = None, nd: int = 4) -> dict:
    """SAAP Pose in ``look`` form (+ ``fov_h_deg`` when known)."""
    d = {"pos": [_r(c, nd) for c in p.pos], "look": [_r(c, nd) for c in p.look]}
    f = fov_h_deg if fov_h_deg is not None else p.fov_h_deg
    if f is not None:
        d["fov_h_deg"] = _r(f, 3)
    return d


class Camera:
    """Pinhole camera for a pose and an image size (square pixels)."""

    def __init__(self, pose: Pose, w: int, h: int, fov_h_deg: float | None = None):
        self.pose = pose
        self.w, self.h = int(w), int(h)
        self.fov = float(fov_h_deg or pose.fov_h_deg or DEFAULT_FOV)
        f = pose.forward
        world_up = (0.0, 0.0, 1.0) if abs(f[2]) < 0.995 else (0.0, 1.0, 0.0)
        r = normalize(cross(f, world_up))
        u = cross(r, f)
        if pose.roll:
            a = math.radians(pose.roll)
            ca, sa = math.cos(a), math.sin(a)
            r, u = (add(mul(r, ca), mul(u, -sa)), add(mul(r, sa), mul(u, ca)))
        self.f, self.r, self.u = f, r, u
        self.fx = (self.w / 2.0) / math.tan(math.radians(self.fov) / 2.0)
        self.fy = self.fx

    @classmethod
    def from_pose(cls, pose: dict | Pose, w: int, h: int, fov_h_deg: float | None = None) -> "Camera":
        p = pose if isinstance(pose, Pose) else parse_pose(pose, fov_h_deg)
        return cls(p, w, h, fov_h_deg)

    @property
    def fov_v(self) -> float:
        return math.degrees(2 * math.atan((self.h / 2.0) / self.fy))

    def to_cam(self, p: Sequence[float]) -> Vec:
        """World point -> (right, up, depth) camera coordinates."""
        d = sub(p, self.pose.pos)
        return (dot(d, self.r), dot(d, self.u), dot(d, self.f))

    def project(self, p: Sequence[float], *, clamp_near: bool = False) -> tuple[float, float, float] | None:
        """Pixel ``(x, y, depth)`` of a world point; ``None`` if behind the near plane."""
        x, y, z = self.to_cam(p)
        if z <= NEAR:
            if not clamp_near:
                return None
            z = NEAR
        return (self.w / 2.0 + x / z * self.fx, self.h / 2.0 - y / z * self.fy, z)

    def ray(self, px: float, py: float) -> tuple[Vec, Vec]:
        """World ray (origin, unit direction) through the centre of pixel ``(px, py)``."""
        x = (px + 0.5 - self.w / 2.0) / self.fx
        y = (self.h / 2.0 - (py + 0.5)) / self.fy
        d = normalize(add(self.f, add(mul(self.r, x), mul(self.u, y))))
        return self.pose.pos, d

    def project_box(self, lo: Sequence[float], hi: Sequence[float]) -> tuple[float, float, float, float, float] | None:
        """Screen bbox ``(x0, y0, x1, y1, min_depth)`` of an AABB, clipped to the image; ``None`` if invisible."""
        corners = [(x, y, z) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
        cams = [self.to_cam(c) for c in corners]
        if all(c[2] <= NEAR for c in cams):
            return None
        xs, ys = [], []
        for x, y, z in cams:
            z = max(z, NEAR)
            xs.append(self.w / 2.0 + x / z * self.fx)
            ys.append(self.h / 2.0 - y / z * self.fy)
        x0, x1 = max(0.0, min(xs)), min(float(self.w), max(xs))
        y0, y1 = max(0.0, min(ys)), min(float(self.h), max(ys))
        if x1 <= x0 or y1 <= y0:
            return None
        return (x0, y0, x1, y1, max(NEAR, min(c[2] for c in cams)))


def quat_to_dir(q: Sequence[float]) -> Vec:
    """Rotate +Y by quaternion ``[x, y, z, w]`` (used for heading of entities)."""
    x, y, z, w = q
    vx, vy, vz = 0.0, 1.0, 0.0
    tx = 2 * (y * vz - z * vy)
    ty = 2 * (z * vx - x * vz)
    tz = 2 * (x * vy - y * vx)
    return (vx + w * tx + (y * tz - z * ty), vy + w * ty + (z * tx - x * tz), vz + w * tz + (x * ty - y * tx))


def frame_sphere(center: Sequence[float], radius: float, *, yaw: float = 0.0, elevation: float = 30.0,
                 fov_h_deg: float | None = None) -> dict:
    """Pose that frames a bounding sphere: distance ``2.5·r + 5`` m at ``elevation`` degrees (SPEC §4.10.5).

    The camera sits on the side opposite to ``yaw`` (yaw 0 = looking north, from the south).
    """
    d = 2.5 * max(0.0, float(radius)) + 5.0
    fwd = dir_from_ypr(yaw, -elevation)
    pos = sub(center, mul(fwd, d))
    out = {"pos": [_r(c, 3) for c in pos], "look": [_r(c, 3) for c in center]}
    if fov_h_deg is not None:
        out["fov_h_deg"] = fov_h_deg
    return out


def ray_aabb(origin: Sequence[float], d: Sequence[float], lo: Sequence[float], hi: Sequence[float],
             t_max: float = math.inf) -> tuple[float, Vec] | None:
    """Slab test: (t, normal) of the first hit of ray ``origin + t·d`` with the box, or ``None``."""
    t0, t1 = 0.0, t_max
    normal: Vec = (0.0, 0.0, 1.0)
    for axis in range(3):
        o, v = origin[axis], d[axis]
        if abs(v) < 1e-12:
            if o < lo[axis] or o > hi[axis]:
                return None
            continue
        ta, tb = (lo[axis] - o) / v, (hi[axis] - o) / v
        n_sign = -1.0
        if ta > tb:
            ta, tb = tb, ta
            n_sign = 1.0
        if ta > t0:
            t0 = ta
            nv = [0.0, 0.0, 0.0]
            nv[axis] = n_sign
            normal = (nv[0], nv[1], nv[2])
        t1 = min(t1, tb)
        if t0 > t1:
            return None
    return t0, normal


def bbox_union(boxes: Iterable[Sequence[float]]) -> tuple[float, float, float, float, float, float] | None:
    """Union of ``(x0, y0, z0, x1, y1, z1)`` boxes."""
    out = None
    for b in boxes:
        if out is None:
            out = list(b)
        else:
            out = [min(out[0], b[0]), min(out[1], b[1]), min(out[2], b[2]),
                   max(out[3], b[3]), max(out[4], b[4]), max(out[5], b[5])]
    return tuple(out) if out else None  # type: ignore[return-value]
