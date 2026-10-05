"""Rotation math of SA objects: Euler (SA-MP / MTA) <-> matrix <-> quaternion <-> IPL (stdlib only).

Conventions (report 23 §5, checked against gta-reversed ``Core/Matrix.cpp:228-240, 312-330`` and
``FileLoader.cpp:1036-1052``):

* **Euler** ``(rx, ry, rz)`` in degrees is what ``CreateObject``/``CreateDynamicObject`` and MTA
  ``<object rotX rotY rotZ>`` take. Both end in ``CPlaceable::SetOrientation`` ->
  ``CMatrix::SetRotate(x, y, z)``, which builds ``R = Rz(z) * Rx(x) * Ry(y)`` (MTA calls it "ZXY"):
  ``right = (cZcY - sZsXsY, cZsXsY + sZcY, -sYcX)``, ``forward = (-sZcX, cZcX, sX)``,
  ``up = (cZsY + sZsXcY, sZsY - cZsXcY, cYcX)`` - the columns of ``R`` (world = R * local).
* **Quaternions** are ``(x, y, z, w)``, Hamilton, active; ``CMatrix::SetRotate(quat)`` builds the
  standard matrix of ``quat``, so :func:`quat_matrix` is the same formula.
* **IPL** stores the *conjugate* of the world rotation (the loader negates ``imag`` before
  ``SetRotate``). And if ``|qx| <= 0.05`` and ``|qy| <= 0.05`` (unless the instance has the
  ``dont_stream`` flag and both ``qx``, ``qy`` are non-zero) the engine keeps only the heading
  ``acos(qw) * (2 if qz < 0 else -2)``: tilts below about 5.73 degrees silently disappear.

Decomposition ``matrix -> Euler`` returns ``rx`` in ``[-90, 90]``; at the gimbal lock
(``rx = +-90``) it sets ``ry = 0``. Angles are normalised to ``(-180, 180]``. Different Euler
triples can mean one rotation (``(180, 0, 0) == (0, 180, 180)``): compare with :func:`same_rotation`.
"""

from __future__ import annotations

import math
from typing import Sequence

__all__ = [
    "Vec3", "Quat", "Mat",
    "euler_matrix", "matrix_euler", "quat_matrix", "matrix_quat", "euler_quat", "quat_euler",
    "ipl_quat", "ipl_euler", "ipl_heading_only", "norm_angle", "same_rotation", "rotation_delta_deg",
    "TILT_EPS", "DONT_STREAM",
]

Vec3 = tuple[float, float, float]
Quat = tuple[float, float, float, float]
Mat = tuple[Vec3, Vec3, Vec3]  # (right, forward, up) = columns of R

#: The loader keeps the full rotation only if ``|qx|`` or ``|qy|`` exceeds this (``FileLoader.cpp``).
TILT_EPS = 0.05
#: ``iflags`` bit of ``dont_stream`` (``interior >> 8``, bit 9 of the raw field).
DONT_STREAM = 2
_GIMBAL = 1e-7


def norm_angle(a: float) -> float:
    """Angle in degrees normalised to ``(-180, 180]`` (no ``-0.0``)."""
    a = math.fmod(float(a), 360.0)
    if a <= -180.0:
        a += 360.0
    elif a > 180.0:
        a -= 360.0
    return a + 0.0


def euler_matrix(rx: float, ry: float, rz: float) -> Mat:
    """``CMatrix::SetRotate(x, y, z)`` for angles in degrees: ``(right, forward, up)``."""
    x, y, z = math.radians(rx), math.radians(ry), math.radians(rz)
    sx, cx = math.sin(x), math.cos(x)
    sy, cy = math.sin(y), math.cos(y)
    sz, cz = math.sin(z), math.cos(z)
    right = (cz * cy - sz * sx * sy, cz * sx * sy + sz * cy, -(sy * cx))
    forward = (-(sz * cx), cz * cx, sx)
    up = (cz * sy + sz * sx * cy, sz * sy - cz * sx * cy, cy * cx)
    return right, forward, up


def matrix_euler(m: Mat) -> Vec3:
    """Inverse of :func:`euler_matrix`: ``(rx, ry, rz)`` in degrees, ``rx`` in ``[-90, 90]``."""
    right, forward, up = m
    sx = max(-1.0, min(1.0, forward[2]))
    x = math.asin(sx)
    if math.hypot(forward[0], forward[1]) > _GIMBAL:
        z = math.atan2(-forward[0], forward[1])
        y = math.atan2(-right[2], up[2])
    else:  # gimbal lock: only z + y (or z - y) is defined; put it all into z
        y = 0.0
        z = math.atan2(right[1], right[0])
    return norm_angle(math.degrees(x)), norm_angle(math.degrees(y)), norm_angle(math.degrees(z))


def quat_matrix(q: Sequence[float]) -> Mat:
    """``CMatrix::SetRotate(quat)``: matrix of a (normalised) quaternion ``(x, y, z, w)``."""
    x, y, z, w = _unit(q)
    x2, y2, z2 = x + x, y + y, z + z
    xx, yx, zx = x2 * x, y2 * x, z2 * x
    yy, zy, zz = y2 * y, z2 * y, z2 * z
    xw, yw, zw = x2 * w, y2 * w, z2 * w
    right = (1.0 - (zz + yy), zw + yx, zx - yw)
    forward = (yx - zw, 1.0 - (zz + xx), xw + zy)
    up = (yw + zx, zy - xw, 1.0 - (yy + xx))
    return right, forward, up


def matrix_quat(m: Mat) -> Quat:
    """Unit quaternion ``(x, y, z, w)`` of a rotation matrix (columns ``right, forward, up``), ``w >= 0``."""
    (m00, m10, m20), (m01, m11, m21), (m02, m12, m22) = m
    tr = m00 + m11 + m22
    if tr > 0.0:
        s = math.sqrt(tr + 1.0) * 2.0
        w, x, y, z = 0.25 * s, (m21 - m12) / s, (m02 - m20) / s, (m10 - m01) / s
    elif m00 > m11 and m00 > m22:
        s = math.sqrt(1.0 + m00 - m11 - m22) * 2.0
        w, x, y, z = (m21 - m12) / s, 0.25 * s, (m01 + m10) / s, (m02 + m20) / s
    elif m11 > m22:
        s = math.sqrt(1.0 + m11 - m00 - m22) * 2.0
        w, x, y, z = (m02 - m20) / s, (m01 + m10) / s, 0.25 * s, (m12 + m21) / s
    else:
        s = math.sqrt(1.0 + m22 - m00 - m11) * 2.0
        w, x, y, z = (m10 - m01) / s, (m02 + m20) / s, (m12 + m21) / s, 0.25 * s
    return _canon(_unit((x, y, z, w)))


def _qmul(a: Quat, b: Quat) -> Quat:
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz)


def _unit(q: Sequence[float]) -> Quat:
    x, y, z, w = (float(c) for c in q)
    n = math.sqrt(x * x + y * y + z * z + w * w)
    if not n or not math.isfinite(n):
        raise ValueError(f"not a rotation quaternion: {tuple(q)!r}")
    return (x / n, y / n, z / n, w / n)


def _canon(q: Quat) -> Quat:
    """``q`` and ``-q`` are one rotation: prefer ``w >= 0`` (then the first non-zero part > 0); no ``-0.0``."""
    if q[3] < 0.0 or (q[3] == 0.0 and next((c for c in q[:3] if c), 0.0) < 0.0):
        q = (-q[0], -q[1], -q[2], -q[3])
    return tuple(c + 0.0 for c in q)  # type: ignore[return-value]


def euler_quat(rx: float, ry: float, rz: float) -> Quat:
    """World rotation quaternion of SA-MP/MTA Euler angles (degrees): ``qz * qx * qy``, ``w >= 0``."""
    hx, hy, hz = (math.radians(a) * 0.5 for a in (rx, ry, rz))
    qx = (math.sin(hx), 0.0, 0.0, math.cos(hx))
    qy = (0.0, math.sin(hy), 0.0, math.cos(hy))
    qz = (0.0, 0.0, math.sin(hz), math.cos(hz))
    return _canon(_unit(_qmul(_qmul(qz, qx), qy)))


def quat_euler(q: Sequence[float]) -> Vec3:
    """SA-MP/MTA Euler angles (degrees) of a world rotation quaternion."""
    return matrix_euler(quat_matrix(q))


def ipl_quat(rx: float, ry: float, rz: float) -> Quat:
    """The quaternion to *write* into an IPL for Euler angles: the conjugate of the world rotation."""
    x, y, z, w = euler_quat(rx, ry, rz)
    return (-x + 0.0, -y + 0.0, -z + 0.0, w)


def ipl_heading_only(q_file: Sequence[float], iflags: int = 0) -> bool:
    """True if the engine ignores the tilt of this IPL quaternion and keeps only the heading."""
    x, y = float(q_file[0]), float(q_file[1])
    if abs(x) > TILT_EPS or abs(y) > TILT_EPS:
        return False
    return not ((iflags & DONT_STREAM) and x != 0.0 and y != 0.0)


def ipl_euler(q_file: Sequence[float], iflags: int = 0) -> tuple[Vec3, bool]:
    """Euler angles (degrees) the engine shows for an IPL quaternion, and whether a tilt was dropped.

    Same rule as ``CFileLoader::LoadObjectInstance``: a heading-only placement becomes
    ``(0, 0, heading)``; otherwise the conjugate quaternion is decomposed. ``dropped`` is True when
    the file quaternion has a tilt (``qx`` or ``qy`` non-zero beyond 1e-6) that the engine ignores.
    """
    x, y, z, w = (float(c) for c in q_file)
    if ipl_heading_only((x, y, z, w), iflags):  # the raw (not normalised) w, exactly like the loader
        heading = math.acos(max(-1.0, min(1.0, w))) * (2.0 if z < 0.0 else -2.0)
        dropped = abs(x) > 1e-6 or abs(y) > 1e-6
        return (0.0, 0.0, norm_angle(math.degrees(heading))), dropped
    return quat_euler((-x, -y, -z, w)), False


def rotation_delta_deg(a: Sequence[float], b: Sequence[float]) -> float:
    """Angle in degrees of the rotation between two Euler triples (0 = same orientation)."""
    qa, qb = euler_quat(*a), euler_quat(*b)
    x, y, z, w = _qmul((-qa[0], -qa[1], -qa[2], qa[3]), qb)  # relative rotation conj(a) * b
    return math.degrees(2.0 * math.atan2(math.sqrt(x * x + y * y + z * z), abs(w)))  # stable near 0


def same_rotation(a: Sequence[float], b: Sequence[float], tol_deg: float = 0.01) -> bool:
    """True if Euler triples ``a`` and ``b`` give the same orientation within ``tol_deg``."""
    return rotation_delta_deg(a, b) <= tol_deg
