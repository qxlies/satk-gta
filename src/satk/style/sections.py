"""The model as ``satk.style.form`` / ``satk.style.fit`` see it: HD parts in model space, wheels and frames.

:func:`scene_parts` turns a decoded :class:`satk.model3d.mesh.ModelScene` into part dicts (positions, corner
normals, UVs, materials with texture names, alpha and key colours) and :func:`wheels_of` gives the wheel
centres and radius. numpy is imported inside the functions.
"""

from __future__ import annotations

from ..model3d.mesh import WHEEL_DUMMIES, ModelScene
from .metrics import is_hd_part

__all__ = ["scene_parts", "wheels_of", "frame_table", "mat_key", "is_wheel_part"]


def mat_key(rgba: int) -> str:
    return f"{rgba >> 24},{(rgba >> 16) & 255},{(rgba >> 8) & 255}"


def is_wheel_part(name: str) -> bool:
    return (name or "").strip().lower().startswith("wheel")


def _m4(np, m):
    rx, ry, rz, ux, uy, uz, ax, ay, az, px, py, pz = m
    R = np.array([[rx, ux, ax], [ry, uy, ay], [rz, uz, az]], dtype=np.float64)
    return R, np.array([px, py, pz], dtype=np.float64)


def scene_parts(scene: ModelScene, *, keep=is_hd_part, copies: bool = False,
                tex_alpha: set | None = None) -> list[dict]:
    """Part dicts (``satk.style.form`` input) of the HD atomics of ``scene`` in model space.

    ``copies``: also the wheel copies the preview puts on empty wheel dummies (role ``wheel``).
    ``tex_alpha``: names of textures with an alpha channel (``talpha`` per material: decals, foliage, fences)."""
    import numpy as np

    out = []
    src = scene.preview_parts if copies else scene.parts
    for p in src:
        if p.kind == "orphan" or (p.kind == "atomic" and not keep(p.name)) or (p.kind == "copy" and not copies):
            continue
        if p.hidden and p.kind != "copy":
            continue
        mesh = scene.meshes[p.geom]
        if not len(mesh.tris):
            continue
        R, t = _m4(np, p.matrix)
        if scene.root is not None and tuple(scene.root) != (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0):
            R0, t0 = _m4(np, scene.root)
            R, t = R0 @ R, R0 @ t + t0
        P = np.frombuffer(mesh.positions, dtype=np.float32).reshape(-1, 3).astype(np.float64) @ R.T + t
        T = np.frombuffer(mesh.tris, dtype=np.uint32).reshape(-1, 3).astype(np.int64)
        if np.linalg.det(R) < 0:
            T = T[:, [0, 2, 1]]
        N = None
        if mesh.normals is not None and len(mesh.normals) == len(mesh.positions):
            Ni = np.linalg.inv(R).T
            N = np.frombuffer(mesh.normals, dtype=np.float32).reshape(-1, 3).astype(np.float64) @ Ni.T
        UV = None
        if mesh.uv and len(mesh.uv[0]) // 2 == len(P):
            UV = np.frombuffer(mesh.uv[0], dtype=np.float32).reshape(-1, 2).astype(np.float64)
        mats = scene.materials[p.geom]
        M = np.frombuffer(mesh.mat_ids, dtype=np.uint16).astype(np.int64)
        name = p.name.strip()
        out.append({"name": name, "pos": P, "tris": T, "normals": N, "uv": UV, "mat": M,
                    "tex": [(m.texture or "").lower() for m in mats], "alpha": [m.rgba & 0xFF for m in mats],
                    "keys": [mat_key(m.rgba) for m in mats], "frame": p.frame,
                    "talpha": [(m.texture or "").lower() in (tex_alpha or ()) for m in mats],
                    "role": "wheel" if is_wheel_part(name) else "hd"})
    return out


def frame_table(scene: ModelScene) -> dict:
    """``{name (lower): {"pos": (x, y, z), "axes": 3x3 (columns right, up, at), "parent": name}}``."""
    out = {}
    names = [(f.name or "").strip().lower() for f in scene.frames]
    root = tuple(scene.root) if scene.root is not None else None
    ident = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0)
    rot = root is not None and root != ident
    for f, n in zip(scene.frames, names):
        if not n or n in out:
            continue
        m = f.model
        pos, right, up, at = (m[9], m[10], m[11]), (m[0], m[1], m[2]), (m[3], m[4], m[5]), (m[6], m[7], m[8])
        if rot:            # the same root the parts get (scene_parts): positions and axes in one space
            pos = _apply(root, pos, True)
            right, up, at = (_apply(root, v, False) for v in (right, up, at))
        out[n] = {"pos": pos, "right": right, "up": up, "at": at,
                  "parent": names[f.parent] if 0 <= f.parent < len(names) else "", "idx": f.idx}
    return out


def _apply(m, v, point: bool) -> tuple:
    rx, ry, rz, ux, uy, uz, ax, ay, az, px, py, pz = m
    x, y, z = v
    out = (rx * x + ux * y + ax * z, ry * x + uy * y + ay * z, rz * x + uz * y + az * z)
    if point:
        out = (out[0] + px, out[1] + py, out[2] + pz)
    return out


def wheels_of(scene: ModelScene, parts: list[dict], wheel_scale: float | None = None) -> list[dict]:
    """``[{name, center, radius}]``: car wheel dummies with the wheel mesh radius (or ``wheel_scale`` / 2), or the
    bike wheels ``wheel_front``/``wheel_rear`` at their frames."""
    import numpy as np

    fr = frame_table(scene)
    radius = None
    for p in parts:
        if p["name"].lower() in ("wheel",):
            ext = p["pos"].max(axis=0) - p["pos"].min(axis=0)
            radius = float(max(ext[1], ext[2])) / 2
            break
    if radius is None and wheel_scale:
        radius = float(wheel_scale) / 2
    out = []
    for n in WHEEL_DUMMIES:
        if n in fr and radius:
            out.append({"name": n, "center": fr[n]["pos"], "radius": radius})
    if not out:
        for p in parts:
            n = p["name"].lower()
            if n in ("wheel_front", "wheel_rear") and n in fr:
                P = p["pos"]
                ext = P.max(axis=0) - P.min(axis=0)
                out.append({"name": n, "center": fr[n]["pos"], "radius": float(max(ext[1], ext[2])) / 2,
                            "bike": True})
    return [w for w in out if np.isfinite(w["center"]).all()]
