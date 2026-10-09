"""Synthetic DFF/COL bytes for satk.style tests (generated here; no game data in the repository)."""

from __future__ import annotations

import struct

import numpy as np

SA_LIBID = 0x1803FFFF


def chunk(t: int, payload: bytes) -> bytes:
    return struct.pack("<III", t, len(payload), SA_LIBID) + payload


def rw_string(s: str) -> bytes:
    raw = s.encode("latin-1") + b"\0"
    return chunk(0x02, raw + b"\0" * (-len(raw) % 4))


def _texture(name: str) -> bytes:
    return chunk(0x06, chunk(0x01, struct.pack("<HH", 0x1106, 0)) + rw_string(name) + rw_string("")
                 + chunk(0x03, b""))


def material(rgba=(255, 255, 255, 255), tex: str | None = None, *, env: float | None = None) -> bytes:
    """A Material (optionally with a MatFX env map xvehicleenv128 of coefficient env)."""
    body = chunk(0x01, struct.pack("<I4BII3f", 0, *rgba, 0, 1 if tex else 0, 1.0, 1.0, 1.0))
    if tex:
        body += _texture(tex)
    ext = b""
    if env is not None:
        ext = chunk(0x120, struct.pack("<II", 2, 2) + struct.pack("<fii", env, 0, 1) + _texture("xvehicleenv128")
                    + struct.pack("<I", 0))
    return chunk(0x07, body + chunk(0x03, ext))


def geometry(pos, tris, mats=None, *, uv_sets: int = 1, normals=None, prelit=None, uv=None) -> bytes:
    """A Geometry with the triangle list tris = [(a, b, c, mat)]; normals/prelit/uv per vertex."""
    nv = len(pos)
    flags = 0x02 | 0x20 | (0x08 if prelit is not None else 0) | (0x10 if normals is not None else 0)
    flags |= 0x80 if uv_sets >= 2 else (0x04 if uv_sets == 1 else 0)
    st = struct.pack("<IiiI", flags | (uv_sets << 16), len(tris), nv, 1)
    if prelit is not None:
        st += b"".join(bytes(c) for c in prelit)
    for _s in range(uv_sets):
        st += b"".join(struct.pack("<2f", *(uv[i] if uv is not None else (i * 0.37 % 1, i * 0.61 % 1)))
                       for i in range(nv))
    st += b"".join(struct.pack("<4H", b, a, m, c) for a, b, c, m in tris)
    st += struct.pack("<4fII", 0.0, 0.0, 0.0, 10.0, 1, 1 if normals is not None else 0)
    st += b"".join(struct.pack("<3f", *p) for p in pos)
    if normals is not None:
        st += b"".join(struct.pack("<3f", *n) for n in normals)
    mats = mats if mats is not None else [material()]
    ml = chunk(0x01, struct.pack(f"<I{len(mats)}i", len(mats), *([-1] * len(mats)))) + b"".join(mats)
    return chunk(0x0F, chunk(0x01, st) + chunk(0x08, ml) + chunk(0x03, b""))


def clump(geoms, frames, atomics, col: bytes | None = None) -> bytes:
    """frames = [(parent, name, (x, y, z))] with identity rotation; atomics = [(frame, geom)]."""
    st = chunk(0x01, struct.pack("<III", len(atomics), 0, 0))
    fl = struct.pack("<I", len(frames))
    for parent, _name, p in frames:
        fl += struct.pack("<12fiI", 1, 0, 0, 0, 1, 0, 0, 0, 1, *p, parent, 0)
    fext = b"".join(chunk(0x03, chunk(0x253F2FE, name.encode()) if name else b"") for _p, name, _x in frames)
    body = st + chunk(0x0E, chunk(0x01, fl) + fext)
    body += chunk(0x1A, chunk(0x01, struct.pack("<I", len(geoms))) + b"".join(geoms))
    for fi, gi in atomics:
        body += chunk(0x14, chunk(0x01, struct.pack("<4I", fi, gi, 5, 0)) + chunk(0x03, b""))
    body += chunk(0x03, chunk(0x253F2FA, col) if col is not None else b"")
    return chunk(0x10, body)


def box_mesh(sx: float, sy: float, sz: float, cx: float = 0.0, cy: float = 0.0, cz: float = 0.0):
    """Closed box: (pos, tris (a, b, c), radial normals), 8 shared vertices, outward winding."""
    c = np.array([cx, cy, cz])
    P = [(cx + x * sx / 2, cy + y * sy / 2, cz + z * sz / 2) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
    quads = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    A = np.asarray(P)
    out = []
    for a, b, d, e in quads:
        for tri in ((a, b, d), (a, d, e)):
            i, j, k = tri
            n = np.cross(A[j] - A[i], A[k] - A[i])
            out.append(tri if np.dot(n, (A[i] + A[j] + A[k]) / 3 - c) > 0 else (i, k, j))
    N = [tuple((np.asarray(p) - c) / np.linalg.norm(np.asarray(p) - c)) for p in P]
    return P, out, N


def col3(name: str, spheres, face_light: int | None = None) -> bytes:
    """A COL3 record: spheres [(x, y, z, r, piece)] and, with face_light, one face with that light byte."""
    nf = 1 if face_light is not None else 0
    hdr = struct.pack("<10f", -1, -1, -1, 1, 1, 1, 0, 0, 0, 2)
    sph = b"".join(struct.pack("<4f4B", x, y, z, r, 0, piece, 0, 255) for x, y, z, r, piece in spheres)
    vrt = struct.pack("<9h", 0, 0, 0, 128, 0, 0, 0, 128, 0) + b"\0\0" if nf else b""
    face = struct.pack("<3H2B", 0, 1, 2, 0, face_light) if nf else b""
    base = 32 + 76 + 12                                  # COL3 header size (fourcc .. end of the v3 fields)
    o_sph = base - 4 if spheres else 0                  # offsets count from fourcc + 4
    o_vrt = base - 4 + len(sph) if nf else 0
    o_face = base - 4 + len(sph) + len(vrt) if nf else 0
    v2 = struct.pack("<HHHBBI6I", len(spheres), 0, nf, 0, 0, 0x02 if nf else 0, o_sph, 0, 0, o_vrt, o_face, 0)
    v3 = struct.pack("<3I", 0, 0, 0)
    rec = name.encode().ljust(22, b"\0") + struct.pack("<H", 0) + hdr + v2 + v3 + sph + vrt + face
    return b"COL3" + struct.pack("<I", len(rec)) + rec


CAR_FRAMES = [(-1, "car", (0, 0, 0)), (0, "chassis_dummy", (0, 0, 0)), (1, "chassis", (0, 0, 0)),
              (0, "wheel_lf_dummy", (-0.9, 1.6, -0.3)), (0, "wheel_rf_dummy", (0.9, 1.6, -0.3)),
              (0, "wheel_lb_dummy", (-0.9, -1.6, -0.3)), (0, "wheel_rb_dummy", (0.9, -1.6, -0.3)),
              (4, "wheel", (0, 0, 0)), (1, "ped_arm", (-0.9, 0.2, 0.3)), (2, "bump_front_dummy", (-0.8, 2.3, -0.3)),
              (9, "bump_front_ok", (0, 0, 0)), (2, "door_lf_dummy", (-1.0, 1.0, 0.0)), (11, "door_lf_ok", (0, 0, 0))]


def car_dff(*, paint_tex: str = "vehiclegrunge256", lamp_tex: str = "vehiclelights128", arm_x: float = -0.9,
            door_x: float = -1.0, env: bool = True, uv_sets: int = 2, col: bool = True) -> bytes:
    """A small synthetic car: chassis (paint + lamp), wheel, front bumper, left door; 13 frames."""
    frames = [list(f) for f in CAR_FRAMES]
    frames[8][2] = (arm_x, 0.2, 0.3)
    frames[11][2] = (door_x, 1.0, 0.0)
    P, T, N = box_mesh(2.2, 5.6, 1.4)
    mats = [material((60, 255, 0, 255), paint_tex, env=0.5 if env else None), material((255, 175, 0, 255), lamp_tex)]
    chassis = geometry(P, [(a, b, c, 0 if i < 10 else 1) for i, (a, b, c) in enumerate(T)], mats,
                       uv_sets=uv_sets, normals=N)
    wP, wT, wN = box_mesh(0.3, 0.7, 0.7)
    wheel = geometry(wP, [(a, b, c, 0) for a, b, c in wT], [material(tex="vehicletyres128")], normals=wN)
    bP, bT, bN = box_mesh(2.0, 0.3, 0.4)
    bump = geometry(bP, [(a, b, c, 0) for a, b, c in bT], [material((60, 255, 0, 255), paint_tex)], normals=bN)
    dP, dT, dN = box_mesh(0.1, 1.2, 0.9, cy=-0.6)                  # hinged at its front edge
    door = geometry(dP, [(a, b, c, 0) for a, b, c in dT], [material((60, 255, 0, 255), paint_tex)], normals=dN)
    c = col3("car", [(0, 1.4, 0, 1.2, 0), (0, -1.4, 0, 1.2, 2)]) if col else None
    return clump([chassis, wheel, bump, door], [tuple(f) for f in frames], [(2, 0), (7, 1), (10, 2), (12, 3)], col=c)


def prop_dff(size: float = 1.0, *, prelit: bool = True, night: bool = False) -> bytes:
    """A prelit box prop (one frame, one atomic)."""
    P, T, _N = box_mesh(size, size, size)
    pre = [(90, 80, 70, 255)] * len(P) if prelit else None
    g = geometry(P, [(a, b, c, 0) for a, b, c in T], [material(tex="crate64")], prelit=pre)
    return clump([g], [(-1, "box", (0, 0, 0))], [(0, 0)])
