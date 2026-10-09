"""Canonical mesh metrics of satk.style (contract K1): one definition per number, numpy only.

This module imports nothing from satk and nothing heavy at module level, so it runs unchanged in
satk's own Python, in Blender's bundled Python 3.13 (``from satk.style.metrics import mesh_metrics``
or loaded by file path) and in tests. The meaning, unit and scope of every key is in
:mod:`satk.style.registry` (``REGISTRY``).

Inputs (any array-like: numpy arrays, lists, ``array('f')``; flat or shaped):

* ``pos``: vertex positions ``(V, 3)`` in metres (game axes: +X right, +Y forward, +Z up).
* ``tris``: triangles ``(T, 3)`` of vertex indices (a flat triangle list works too).
* ``corner_normals``: one normal per triangle corner, ``(T, 3, 3)`` or ``(3T, 3)`` in ``tris`` order
  (Blender's ``mesh.corner_normals`` of the loop triangles). ``normals=`` takes per-vertex normals
  ``(V, 3)`` instead (a DFF). Without normals the ``shade.*`` keys are left out.
* ``uv``: UV set 0, per vertex ``(V, 2)`` or per corner ``(T, 3, 2)`` (a flat ``(3T, 2)`` is read
  per corner only when ``V != 3T``). Without UVs the ``uv.*`` keys are left out.
* ``mat``: one material index per triangle ``(T,)``; borders between materials are seams.
* ``groups``: optional per-vertex group id; vertices weld only inside one group (parts of a model).

Topology is measured on the mesh welded by position (``WELD_M``), so split normals and UV seams do
not cut it into pieces. Undefined values (no normals, no hard edges, no manifold edge) are left out
instead of being ``None``. The same input always gives the same output.

Example (Blender, evaluated mesh of an object)::

    import numpy as np
    from satk.style.metrics import mesh_metrics
    me = obj.evaluated_get(depsgraph).to_mesh()
    me.calc_loop_triangles()
    lt = me.loop_triangles
    pos = np.empty(len(me.vertices) * 3, np.float32); me.vertices.foreach_get("co", pos)
    tris = np.empty(len(lt) * 3, np.int32); lt.foreach_get("vertices", tris)
    loops = np.empty(len(lt) * 3, np.int32); lt.foreach_get("loops", loops)
    cn = np.empty(len(me.loops) * 3, np.float32); me.corner_normals.foreach_get("vector", cn)
    m = mesh_metrics(pos, tris, corner_normals=cn.reshape(-1, 3)[loops])
"""

from __future__ import annotations

__all__ = [
    "mesh_metrics", "concat", "is_hd_part", "KEYS",
    "WELD_M", "FLAT_DEG", "HARD_DEG", "SEAM_UV", "SLIVER_DEG", "UV_ZERO", "DIHEDRAL_BINS",
]

#: Weld tolerance for topology (positions rounded to this grid, metres).
WELD_M = 1e-4
#: A triangle is flat when all three corner normals are within this angle of its face normal (deg).
FLAT_DEG = 1.0
#: An edge is hard when the two faces' corner normals at one of its ends differ by more than this (deg).
HARD_DEG = 1.0
#: Corner UVs that differ by more than this (either axis) on the two sides of an edge make a UV seam.
SEAM_UV = 1e-4
#: A triangle is a sliver when its smallest interior angle is below this (deg).
SLIVER_DEG = 8.0
#: A triangle has zero UV area when ``|cross(uv1 - uv0, uv2 - uv0)|`` (twice the UV area) is below this.
UV_ZERO = 1e-7
#: Dihedral bins (deg) of ``shade.hard_by_dihedral``; the last bin is open.
DIHEDRAL_BINS = (0.0, 10.0, 20.0, 30.0, 45.0, 60.0, 90.0)
#: Faces with ``|cross|`` (twice the area, m^2) at or below this are degenerate (no face normal).
_DEGEN = 1e-12

#: Every key :func:`mesh_metrics` can return, in output order.
KEYS = (
    "geo.tris", "dff.verts", "dff.verts_per_tri", "geo.area_m2", "geo.tris_per_m2", "geo.median_edge_m",
    "geo.median_dihedral", "geo.pieces", "geo.largest_piece_share", "geo.sliver_share", "geo.open_edges",
    "geo.nonmanifold_edges", "shade.normal_bend", "shade.flat_share", "shade.hard_edge_share",
    "shade.hard_at_seam", "shade.hard_by_dihedral", "uv.zero_area_share", "uv.span", "bbox",
)


def _np():
    import numpy as np

    return np


def _r(x, nd: int) -> float:
    return float(round(float(x), nd))


def _vec(a, width: int, what: str, np, dtype):
    """``a`` as an ``(n, width)`` array (flat input reshaped)."""
    arr = np.asarray(a, dtype=dtype)
    if arr.size % width:
        raise ValueError(f"{what}: {arr.size} values is not a multiple of {width}")
    return arr.reshape(-1, width)


def _corners(a, width: int, nv: int, nt: int, what: str, np, *, per_vertex_ok: bool):
    """Per-corner ``(T, 3, width)`` from per-corner ``(T,3,w)``/``(3T,w)`` or per-vertex ``(V,w)`` data."""
    arr = np.asarray(a, dtype=np.float64)
    if arr.ndim == 3:
        if arr.shape != (nt, 3, width):
            raise ValueError(f"{what}: shape {arr.shape} != ({nt}, 3, {width})")
        return arr, False
    arr = _vec(arr, width, what, np, np.float64)
    if per_vertex_ok and len(arr) == nv:
        return arr, True
    if len(arr) == 3 * nt:
        return arr.reshape(nt, 3, width), False
    want = f"{nv} (per vertex) or {3 * nt}" if per_vertex_ok else f"{3 * nt}"
    raise ValueError(f"{what}: {len(arr)} rows, expected {want} (per corner)")


def _unit(v, np):
    ln = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.maximum(ln, 1e-30)


def _angle(u, v, np):
    """Angle in degrees between unit vectors along the last axis."""
    return np.degrees(np.arccos(np.clip(np.einsum("...i,...i->...", u, v), -1.0, 1.0)))


def _components(wt, nw: int, np):
    """Connected-component label per triangle (union by min label + pointer jumping, vectorised)."""
    lab = np.arange(nw, dtype=np.int64)
    flat = wt.reshape(-1)
    while True:
        lt = lab[wt]
        m = np.repeat(lt.min(axis=1), 3)
        new = lab.copy()
        np.minimum.at(new, lt.reshape(-1), m)
        while True:
            nn = new[new]
            if np.array_equal(nn, new):
                break
            new = nn
        if np.array_equal(new, lab):
            break
        lab = new
    return lab[flat[0::3]]


def mesh_metrics(pos, tris, corner_normals=None, uv=None, mat=None, *, normals=None, groups=None) -> dict:
    """Canonical metrics of one triangle mesh (keys and meaning: :data:`satk.style.registry.REGISTRY`).

    Args:
        pos: vertex positions (V, 3), metres.
        tris: triangle vertex indices (T, 3).
        corner_normals: normals per triangle corner (T, 3, 3) or (3T, 3); exclusive with ``normals``.
        uv: UV set 0 per vertex (V, 2) or per corner (T, 3, 2).
        mat: material index per triangle (T,).
        normals: normals per vertex (V, 3) (a DFF stores them so).
        groups: group id per vertex (V,); welding never joins two groups.

    Returns a dict with the keys of :data:`KEYS` that are defined for this input. ``ValueError`` on
    inconsistent shapes or out-of-range indices.
    """
    np = _np()
    P = _vec(pos, 3, "pos", np, np.float64)
    T = _vec(tris, 3, "tris", np, np.int64)
    nv, nt = len(P), len(T)
    if nt and (T.min() < 0 or T.max() >= nv):
        raise ValueError(f"tris: index out of range 0..{nv - 1}")
    if corner_normals is not None and normals is not None:
        raise ValueError("pass corner_normals or normals, not both")
    out: dict = {"geo.tris": nt}
    if nt == 0:
        if nv:
            out["bbox"] = [_r(x, 6) for x in (*P.min(axis=0), *P.max(axis=0))]
        return out

    # ---- per-corner attributes
    CN = None
    if corner_normals is not None:
        CN, _pv = _corners(corner_normals, 3, nv, nt, "corner_normals", np, per_vertex_ok=False)
    elif normals is not None:
        N = _vec(normals, 3, "normals", np, np.float64)
        if len(N) != nv:
            raise ValueError(f"normals: {len(N)} rows, expected {nv} (per vertex)")
        CN = N[T]
    if CN is not None:
        CN = np.nan_to_num(CN, nan=0.0, posinf=0.0, neginf=0.0)  # broken files: non-finite = no normal
    UVC = None
    if uv is not None:
        u, per_vertex = _corners(uv, 2, nv, nt, "uv", np, per_vertex_ok=True)
        UVC = np.nan_to_num(u[T] if per_vertex else u, nan=0.0, posinf=0.0, neginf=0.0)
    M = None
    if mat is not None:
        M = np.asarray(mat, dtype=np.int64).reshape(-1)
        if len(M) != nt:
            raise ValueError(f"mat: {len(M)} values, expected {nt} (one per triangle)")
    G = None
    if groups is not None:
        G = np.asarray(groups, dtype=np.int64).reshape(-1)
        if len(G) != nv:
            raise ValueError(f"groups: {len(G)} values, expected {nv} (one per vertex)")

    # ---- DFF vertex estimate: distinct (vertex, corner normal, corner UV)
    cols = [T.reshape(-1, 1)]
    if CN is not None:
        cols.append(np.round(_unit(CN, np).reshape(-1, 3) * 1e3).astype(np.int64))
    if UVC is not None:
        cols.append(np.round(UVC.reshape(-1, 2) * 1e5).astype(np.int64))
    dverts = len(np.unique(np.hstack(cols), axis=0)) if len(cols) > 1 else len(np.unique(T))
    out["dff.verts"] = int(dverts)
    out["dff.verts_per_tri"] = _r(dverts / nt, 4)

    # ---- faces
    A, B, C = P[T[:, 0]], P[T[:, 1]], P[T[:, 2]]
    cr = np.cross(B - A, C - A)
    L2 = np.linalg.norm(cr, axis=1)
    ok = L2 > _DEGEN
    area = L2 / 2.0
    FN = np.zeros_like(cr)
    FN[ok] = cr[ok] / L2[ok, None]
    tot_area = float(area.sum())
    out["geo.area_m2"] = _r(tot_area, 4)
    if tot_area > 0:
        out["geo.tris_per_m2"] = _r(nt / tot_area, 2)

    # ---- welded half-edges -> edges
    key = np.round(P / WELD_M).astype(np.int64)
    if G is not None:
        key = np.column_stack([G, key])
    _u, wid = np.unique(key, axis=0, return_inverse=True)
    wid = np.asarray(wid).reshape(-1)
    nw = int(wid.max()) + 1
    WT = wid[T]
    c0 = np.arange(3 * nt, dtype=np.int64)                     # corner id = 3 * face + k
    c1 = 3 * (c0 // 3) + (c0 % 3 + 1) % 3
    w0, w1 = WT.reshape(-1), WT.reshape(-1)[c1]
    keep = w0 != w1
    c0, c1, w0, w1 = c0[keep], c1[keep], w0[keep], w1[keep]
    swap = w0 > w1
    clo, chi = np.where(swap, c1, c0), np.where(swap, c0, c1)  # corner at the lower / higher welded vertex
    ekey = np.minimum(w0, w1) * nw + np.maximum(w0, w1)
    order = np.argsort(ekey, kind="stable")
    ekey, clo, chi = ekey[order], clo[order], chi[order]
    starts = np.flatnonzero(np.r_[True, ekey[1:] != ekey[:-1]]) if len(ekey) else np.zeros(0, np.int64)
    counts = np.diff(np.r_[starts, len(ekey)])
    if len(starts):
        Pc = P[T.reshape(-1)]
        elen = np.linalg.norm(Pc[clo[starts]] - Pc[chi[starts]], axis=1)
        out["geo.median_edge_m"] = _r(np.median(elen), 4)
    out["geo.open_edges"] = int((counts == 1).sum())
    out["geo.nonmanifold_edges"] = int((counts > 2).sum())
    s2 = starts[counts == 2]
    f0, f1 = clo[s2] // 3, clo[s2 + 1] // 3
    both = ok[f0] & ok[f1]
    dih = _angle(FN[f0], FN[f1], np)
    if both.any():
        out["geo.median_dihedral"] = _r(np.median(dih[both]), 3)

    # ---- pieces (connected components of the welded mesh, by triangles)
    comp = _components(WT, nw, np)
    _cu, csize = np.unique(comp, return_counts=True)
    out["geo.pieces"] = int(len(csize))
    out["geo.largest_piece_share"] = _r(csize.max() / nt, 4)

    # ---- slivers: smallest interior angle
    e_ab, e_ac, e_bc = _unit(B - A, np), _unit(C - A, np), _unit(C - B, np)
    ang_a = _angle(e_ab, e_ac, np)
    ang_b = _angle(-e_ab, e_bc, np)
    ang_c = 180.0 - ang_a - ang_b
    mins = np.minimum(np.minimum(ang_a, ang_b), ang_c)
    mins[~ok] = 0.0
    out["geo.sliver_share"] = _r((mins < SLIVER_DEG).mean(), 4)

    # ---- shading
    if CN is not None:
        cu = _unit(CN, np)
        dev = _angle(cu, FN[:, None, :], np)                   # (T, 3) corner vs face normal
        if ok.any():
            w = area[ok]
            out["shade.normal_bend"] = _r((dev[ok].mean(axis=1) * w).sum() / w.sum(), 3)
            out["shade.flat_share"] = _r((dev[ok].max(axis=1) < FLAT_DEG).mean(), 4)
        cflat = cu.reshape(-1, 3)
        lo0, hi0, lo1, hi1 = clo[s2], chi[s2], clo[s2 + 1], chi[s2 + 1]
        # the two faces' corners at the same welded vertex: lo0 & lo1, hi0 & hi1
        hard = (_angle(cflat[lo0], cflat[lo1], np) > HARD_DEG) | (_angle(cflat[hi0], cflat[hi1], np) > HARD_DEG)
        if len(hard):
            out["shade.hard_edge_share"] = _r(hard.mean(), 4)
        if hard.any():
            seam = np.zeros(len(hard), dtype=bool)
            if M is not None:
                seam |= M[f0] != M[f1]
            if UVC is not None:
                uvf = UVC.reshape(-1, 2)
                seam |= (np.abs(uvf[lo0] - uvf[lo1]).max(axis=1) > SEAM_UV)
                seam |= (np.abs(uvf[hi0] - uvf[hi1]).max(axis=1) > SEAM_UV)
            if M is not None or UVC is not None:
                out["shade.hard_at_seam"] = _r(seam[hard].mean(), 4)
        if both.any():
            bins = {}
            idx = np.searchsorted(np.asarray(DIHEDRAL_BINS[1:]), dih[both], side="right")
            hb = hard[both]
            names = [f"{int(a)}-{int(b)}" for a, b in zip(DIHEDRAL_BINS, DIHEDRAL_BINS[1:])]
            names.append(f"{int(DIHEDRAL_BINS[-1])}+")
            for i, name in enumerate(names):
                sel = idx == i
                if sel.any():
                    bins[name] = _r(hb[sel].mean(), 3)
            out["shade.hard_by_dihedral"] = bins

    # ---- UVs
    if UVC is not None:
        d1, d2 = UVC[:, 1] - UVC[:, 0], UVC[:, 2] - UVC[:, 0]
        uarea = np.abs(d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0])
        out["uv.zero_area_share"] = _r((uarea < UV_ZERO).mean(), 4)
        flat_uv = UVC.reshape(-1, 2)
        span = flat_uv.max(axis=0) - flat_uv.min(axis=0)
        out["uv.span"] = [_r(span[0], 4), _r(span[1], 4)]

    used = P[np.unique(T)]
    out["bbox"] = [_r(x, 6) for x in (*used.min(axis=0), *used.max(axis=0))]
    return {k: out[k] for k in KEYS if k in out}


def is_hd_part(name: str) -> bool:
    """True for a part that renders undamaged at full detail: not ``*_dam`` and not ``*_vlo``."""
    n = (name or "").strip().lower().rstrip("0123456789")
    return not (n.endswith("_dam") or n.endswith("_vlo"))


def concat(parts) -> dict:
    """Merge parts into one mesh for :func:`mesh_metrics` (``mesh_metrics(**concat(parts))``).

    Each part is a dict with ``pos``, ``tris`` and optional ``corner_normals`` or ``normals``, ``uv``,
    ``mat`` and ``matrix`` (4x4, column vectors: ``p' = M @ [x, y, z, 1]``, Blender's ``matrix_world``).
    Positions are moved by the matrix, normals by its inverse transpose; a mirroring matrix flips the
    winding. Every part is its own weld group, and its material indices are offset so that two parts
    never share a material. When some parts have normals (or UVs), parts without them get their face
    normals (flat) or zero UVs, so they count as flat shading / zero UV area.
    """
    np = _np()
    parts = list(parts)
    any_n = any(p.get("corner_normals") is not None or p.get("normals") is not None for p in parts)
    any_uv = any(p.get("uv") is not None for p in parts)
    Ps, Ts, Ns, Us, Ms, Gs = [], [], [], [], [], []
    voff = moff = 0
    for gi, p in enumerate(parts):
        P = _vec(p["pos"], 3, "pos", np, np.float64)
        T = _vec(p["tris"], 3, "tris", np, np.int64)
        nv, nt = len(P), len(T)
        if nt and (T.min() < 0 or T.max() >= nv):
            raise ValueError(f"part {gi}: tris index out of range 0..{nv - 1}")
        CN = None
        if p.get("corner_normals") is not None:
            CN, _pv = _corners(p["corner_normals"], 3, nv, nt, "corner_normals", np, per_vertex_ok=False)
        elif p.get("normals") is not None:
            CN = _vec(p["normals"], 3, "normals", np, np.float64)[T]
        UVC = None
        if p.get("uv") is not None:
            u, pv = _corners(p["uv"], 2, nv, nt, "uv", np, per_vertex_ok=True)
            UVC = u[T] if pv else u
        mtx = p.get("matrix")
        if mtx is not None:
            Mx = np.asarray(mtx, dtype=np.float64).reshape(4, 4)
            P = P @ Mx[:3, :3].T + Mx[:3, 3]
            if CN is not None:
                CN = CN @ np.linalg.inv(Mx[:3, :3])                # (inverse transpose) applied to rows
            if np.linalg.det(Mx[:3, :3]) < 0:
                T = T[:, [0, 2, 1]]
                if CN is not None:
                    CN = CN[:, [0, 2, 1]]
                if UVC is not None:
                    UVC = UVC[:, [0, 2, 1]]
        if any_n and CN is None:
            A, B, C = P[T[:, 0]], P[T[:, 1]], P[T[:, 2]]
            CN = np.repeat(_unit(np.cross(B - A, C - A), np)[:, None, :], 3, axis=1)
        if any_uv and UVC is None:
            UVC = np.zeros((nt, 3, 2))
        mt = np.asarray(p["mat"], dtype=np.int64).reshape(-1) if p.get("mat") is not None else np.zeros(nt, np.int64)
        if len(mt) != nt:
            raise ValueError(f"part {gi}: mat has {len(mt)} values, expected {nt}")
        Ps.append(P)
        Ts.append(T + voff)
        Gs.append(np.full(nv, gi, dtype=np.int64))
        Ms.append(mt + moff)
        if CN is not None:
            Ns.append(CN)
        if UVC is not None:
            Us.append(UVC)
        voff += nv
        moff += int(mt.max()) + 1 if nt else 1
    out = {
        "pos": np.concatenate(Ps) if Ps else np.zeros((0, 3)),
        "tris": np.concatenate(Ts) if Ts else np.zeros((0, 3), np.int64),
        "mat": np.concatenate(Ms) if Ms else np.zeros(0, np.int64),
        "groups": np.concatenate(Gs) if Gs else np.zeros(0, np.int64),
    }
    if any_n:
        out["corner_normals"] = np.concatenate(Ns) if Ns else np.zeros((0, 3, 3))
    if any_uv:
        out["uv"] = np.concatenate(Us) if Us else np.zeros((0, 3, 2))
    return out

