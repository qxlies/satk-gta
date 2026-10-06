"""One model -> its style metrics: the numbers that profiles, ``asset.check`` and session stats compare.

The same function measures a vanilla model (style cache) and an agent's file (``asset.check``), so both
sides of every comparison use one definition (names and meaning: :mod:`satk.style.registry`).

* :func:`hd_parts` - the parts that render undamaged at full detail (no ``*_dam``/``*_vlo``; a vehicle's
  single ``wheel`` atomic counts once), in model space;
* :func:`measure` - a flat ``{metric: value}`` dict of one decoded model (:class:`satk.model3d.mesh.ModelScene`).

numpy is imported inside the functions.

Example::

    from satk.model3d.mesh import build_scene
    from satk.style.measure import measure
    m = measure(build_scene(dff_bytes, name="premier", sec="cars"), wheel_scale=0.7)
    m["veh.hd_tris"], m["shade.normal_bend"], m["dims.L"]
"""

from __future__ import annotations

from ..model3d.mesh import WHEEL_DUMMIES, ModelScene
from .metrics import concat, is_hd_part, mesh_metrics

__all__ = ["hd_parts", "measure", "hd_tris", "frame_names", "is_vehicle_scene", "SCALAR_K1"]

#: K1 keys stored as scalars (the vector keys ``bbox``, ``uv.span``, ``shade.hard_by_dihedral`` are derived).
SCALAR_K1 = ("geo.tris", "dff.verts", "dff.verts_per_tri", "geo.area_m2", "geo.tris_per_m2", "geo.median_edge_m",
             "geo.median_dihedral", "geo.pieces", "geo.largest_piece_share", "geo.sliver_share",
             "geo.open_edges", "geo.nonmanifold_edges", "shade.normal_bend", "shade.flat_share",
             "shade.hard_edge_share", "shade.hard_at_seam", "uv.zero_area_share")


def _mat4(m) -> list[list[float]]:
    rx, ry, rz, ux, uy, uz, ax, ay, az, px, py, pz = m
    return [[rx, ux, ax, px], [ry, uy, ay, py], [rz, uz, az, pz], [0.0, 0.0, 0.0, 1.0]]


def frame_names(scene: ModelScene) -> list[str]:
    return [(f.name or "").strip().lower() for f in scene.frames]


def is_vehicle_scene(scene: ModelScene) -> bool:
    names = set(frame_names(scene))
    return scene.sec == "cars" or "chassis_dummy" in names or any(w in names for w in WHEEL_DUMMIES)


def hd_parts(scene: ModelScene, keep=is_hd_part) -> list[dict]:
    """Parts of ``scene`` as dicts for :func:`satk.style.metrics.concat` (+ ``name``, ``geom``)."""
    out = []
    for p in scene.parts:
        if not keep(p.name):
            continue
        m = scene.meshes[p.geom]
        if not m.tris:
            continue
        out.append({"name": p.name, "geom": p.geom, "pos": m.positions, "tris": m.tris, "normals": m.normals,
                    "uv": m.uv[0] if m.uv else None, "mat": m.mat_ids, "matrix": _mat4(p.matrix)})
    return out


def hd_tris(part_tris: dict[str, int]) -> int:
    """``veh.hd_tris`` from ``{part name: triangles}``: chassis + every ``*_ok`` part except ``exhaust_ok`` +
    the wheel mesh once. Optional parts (``extraN``, ``misc_*``, plates without ``_ok``) are not counted."""
    return sum(int(t) for n, t in part_tris.items()
               if n in ("chassis", "wheel") or (n.endswith("_ok") and n != "exhaust_ok"))


def _r(x: float, nd: int = 4) -> float:
    return float(round(float(x), nd))


def _texel(np, parts: list[dict], scene: ModelScene, tex_sizes: dict) -> float | None:
    """Pooled texel density (px/m) over the triangles whose texture size is known."""
    num = den = 0.0
    for p in parts:
        mesh = scene.meshes[p["geom"]]
        if not mesh.uv:
            continue
        mats = scene.materials[p["geom"]]
        wh = np.zeros(max(len(mats), 1))
        for i, mt in enumerate(mats):
            s = tex_sizes.get((mt.texture or "").lower()) if mt.texture else None
            if s:
                wh[i] = float(s[0]) * float(s[1])
        if not wh.any():
            continue
        P = np.frombuffer(mesh.positions, dtype=np.float32).reshape(-1, 3).astype(np.float64)
        U = np.nan_to_num(np.frombuffer(mesh.uv[0], dtype=np.float32).reshape(-1, 2).astype(np.float64))
        T = np.frombuffer(mesh.tris, dtype=np.uint32).reshape(-1, 3).astype(np.int64)
        M = np.frombuffer(mesh.mat_ids, dtype=np.uint16).astype(np.int64)
        M = np.where(M < len(wh), M, 0)
        A = np.linalg.norm(np.cross(P[T[:, 1]] - P[T[:, 0]], P[T[:, 2]] - P[T[:, 0]]), axis=1) / 2
        d1, d2 = U[T[:, 1]] - U[T[:, 0]], U[T[:, 2]] - U[T[:, 0]]
        UA = np.abs(d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0]) / 2
        w = wh[M]
        sel = w > 0
        num += float((UA[sel] * w[sel]).sum())
        den += float(A[sel].sum())
    if den <= 1e-9 or num <= 0:
        return None
    return _r((num / den) ** 0.5, 2)


def _light(np, parts: list[dict], scene: ModelScene) -> dict:
    day, night = [], []
    for p in parts:
        mesh = scene.meshes[p["geom"]]
        if mesh.prelit is None:
            continue
        d = np.frombuffer(mesh.prelit, dtype=np.uint8).reshape(-1, 4).astype(np.float64)
        day.append(d)
        if mesh.night is not None and len(mesh.night) == len(mesh.prelit):
            night.append(np.frombuffer(mesh.night, dtype=np.uint8).reshape(-1, 4).astype(np.float64))
    if not day:
        return {}
    D = np.concatenate(day)
    luma = lambda a: 0.299 * a[:, 0] + 0.587 * a[:, 1] + 0.114 * a[:, 2]  # noqa: E731
    ld = luma(D)
    out = {"light.prelit_lum_p50": _r(np.median(ld), 1)}
    if night and sum(len(n) for n in night) == len(D):
        N = np.concatenate(night)
        ln = luma(N)
        md = float(np.median(ld))
        if md > 0:
            out["light.night_ratio"] = _r(float(np.median(ln)) / md, 3)
        out["light.night_tint"] = _r(N[:, 0].mean() - N[:, 2].mean(), 1)
        out["light.lit_vertex_share"] = _r((ln > ld).mean(), 4)
    return out


def _bbox(np, parts: list[dict], root=None) -> list[float] | None:
    pts = []
    for p in parts:
        P = np.frombuffer(p["pos"], dtype=np.float32).reshape(-1, 3).astype(np.float64)
        Mx = np.asarray(p["matrix"], dtype=np.float64)
        pts.append(P @ Mx[:3, :3].T + Mx[:3, 3])
    if not pts:
        return None
    A = np.concatenate(pts)
    A = A[np.isfinite(A).all(axis=1)]
    if root is not None:
        R = np.asarray(_mat4(root), dtype=np.float64)
        A = A @ R[:3, :3].T + R[:3, 3]
    if not len(A):
        return None
    return [_r(x, 3) for x in (*A.min(axis=0), *A.max(axis=0))]


def measure(scene: ModelScene, *, tex_sizes: dict | None = None, wheel_scale: float | None = None,
            col: dict | None = None) -> dict:
    """Flat ``{metric: value}`` of one model (keys of :mod:`satk.style.registry`; undefined ones left out).

    Args:
        scene: the decoded model.
        tex_sizes: ``{texture name (lower): (w, h)}`` for ``uv.texel_px_m`` (vanilla: the TXD chain).
        wheel_scale: IDE ``wheel_scale`` (front) of a vehicle -> ``dims.wheel_d``.
        col: collision counts ``{"spheres", "boxes", "faces", "shadow_faces"}`` -> ``col.*``.
    """
    import numpy as np

    parts = hd_parts(scene)
    out: dict = {"file.tris": int(scene.tris)}
    vehicle = is_vehicle_scene(scene)
    if parts:
        k1 = mesh_metrics(**concat(parts))
        out.update({k: k1[k] for k in SCALAR_K1 if k in k1})
        if "uv.span" in k1:
            out["uv.span_max"] = max(k1["uv.span"])
        mats = {(p["geom"], i) for p in parts for i in range(len(scene.materials[p["geom"]]))}
        out["mat.count"] = len(mats)
    root = scene.root if scene.root != (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0) else None
    bb = _bbox(np, parts, root)
    if bb:
        out["bbox"] = bb
        sx, sy, sz = bb[3] - bb[0], bb[4] - bb[1], bb[5] - bb[2]
        out["dims.L"], out["dims.W"], out["dims.H"] = _r(sy, 3), _r(sx, 3), _r(sz, 3)
        out["dims.size"] = _r(max(sx, sy, sz), 3)
    if tex_sizes:
        tx = _texel(np, parts, scene, tex_sizes)
        if tx is not None:
            out["uv.texel_px_m"] = tx
    out.update(_light(np, parts, scene))
    if col:
        for src, key in (("spheres", "col.spheres"), ("boxes", "col.boxes"), ("faces", "col.mesh_faces"),
                         ("shadow_faces", "col.shadow_faces")):
            if col.get(src) is not None:
                out[key] = int(col[src])
    if vehicle:
        out.update(_vehicle(np, scene, parts, out, wheel_scale))
    return out


def _vehicle(np, scene: ModelScene, parts: list[dict], base: dict, wheel_scale: float | None) -> dict:
    out: dict = {}
    names = frame_names(scene)
    by_name = {n: f for n, f in zip(names, scene.frames) if n}
    tris_of: dict[str, int] = {}
    for p in scene.parts:
        if p.kind != "atomic":
            continue
        n = p.name.strip().lower()
        tris_of[n] = tris_of.get(n, 0) + len(scene.meshes[p.geom].tris) // 3
    for n, t in tris_of.items():
        out[f"part.tris[{n}]"] = t
    for n, t in tris_of.items():
        if n.endswith("_ok") and t:
            dam = tris_of.get(n[:-3] + "_dam")
            if dam:
                out[f"dam.ok_ratio[{n[:-3]}]"] = _r(dam / t, 3)
    hd = hd_tris(tris_of)
    if hd:
        out["veh.hd_tris"] = hd
    wheel_t = tris_of.get("wheel", 0)
    dummies = [n for n in WHEEL_DUMMIES if n in by_name]
    out["veh.hi_tris"] = hd + max(len(dummies) - 1, 0) * wheel_t if wheel_t else hd
    pos = {n: tuple(by_name[n].model[9:12]) for n in dummies}
    if "wheel_lf_dummy" in pos and "wheel_lb_dummy" in pos:
        out["dims.wheelbase"] = _r(abs(pos["wheel_lf_dummy"][1] - pos["wheel_lb_dummy"][1]), 3)
    if "wheel_lf_dummy" in pos and "wheel_rf_dummy" in pos:
        out["dims.track"] = _r(abs(pos["wheel_lf_dummy"][0] - pos["wheel_rf_dummy"][0]), 3)
    if wheel_scale:
        out["dims.wheel_d"] = _r(wheel_scale, 3)
    for p in scene.parts:
        if p.name.strip().lower() == "wheel" and p.kind == "atomic":
            P = np.frombuffer(scene.meshes[p.geom].positions, dtype=np.float32).reshape(-1, 3)
            if len(P):
                ext = P.max(axis=0) - P.min(axis=0)
                out["veh.wheel_mesh_d"] = _r(max(float(ext[1]), float(ext[2])), 3)
            break
    ch = next((p for p in scene.parts if p.name.strip().lower() == "chassis" and p.kind == "atomic"), None)
    if ch is not None:
        P = np.frombuffer(scene.meshes[ch.geom].positions, dtype=np.float32).reshape(-1, 3).astype(np.float64)
        if len(P):
            Mx = np.asarray(_mat4(ch.matrix), dtype=np.float64)
            X = (P @ Mx[:3, :3].T + Mx[:3, 3])[:, 0]
            X = X[np.isfinite(X)]
            if len(X):
                out["dims.W"] = _r(X.max() - X.min(), 3)       # chassis only: mirrors on doors excluded
    if parts and base.get("bbox"):
        c = concat(parts)
        P, T = np.asarray(c["pos"]), np.asarray(c["tris"])
        if len(T):
            cy = P[T].mean(axis=1)[:, 1]
            y0, y1 = base["bbox"][1], base["bbox"][4]
            if y1 - y0 > 1e-6:
                k = np.clip(((cy - y0) / (y1 - y0) * 3).astype(int), 0, 2)
                sh = np.bincount(k, minlength=3) / len(T)
                out["geo.thirds"] = [_r(sh[2], 3), _r(sh[1], 3), _r(sh[0], 3)]   # front (+Y), middle, rear
    wd = out.get("dims.wheel_d") or out.get("veh.wheel_mesh_d")
    if wd and wd > 0.05:                       # intrinsic anchors: sizes in wheel diameters
        for k in ("dims.L", "dims.W", "dims.H", "dims.wheelbase", "dims.track"):
            v = out.get(k, base.get(k))
            if v:
                out[f"{k}_over_wheel"] = _r(v / wd, 3)
    return out
