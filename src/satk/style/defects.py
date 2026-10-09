"""Mesh defects of satk.style ("no gaps, no bugs"): located defects, calibrated on vanilla SA.

Each check finds a fault an agent can fix at a place; none of them is a number to chase:

* ``crack``      - open seams inside one part: boundary edges that run along each other closer than ``crack_mm``
                   (a T-junction or two unwelded borders) with the faces on both sides continuing the surface;
                   in game the background sparkles through the hairline;
* ``zfight``     - coplanar overlapping faces of different pieces or materials closer than ``zfight_mm``,
                   facing the same way: they flicker in game;
* ``flipped``    - faces wound against their welded neighbours (back faces are culled: a hole from outside),
                   closed pieces turned inside out, and faces whose normals point against their winding (lit
                   from behind);
* ``degenerate`` - zero-area triangles and vertices no triangle uses;
* ``stray``      - tiny islands smaller than ``stray_mm`` and pieces far away from the rest of the model;
* ``uv_stretch`` - texture stretched more than ``uv_stretch`` times in one direction over a share of a material;
* ``texel``      - triangles whose texel density is off the median of their material by more than
                   ``texel_ratio`` over a share of that material;
* ``col``        - collision far outside the mesh (``col_outside``) and mesh far from any collision
                   (``col_uncovered``).

Limits: :data:`DEFAULTS`, updated by ``data/style/defects.json`` (``limits`` and per-class ``classes``
overrides; :func:`limits`). numpy only and nothing from satk at module level, so Blender can run it too.

Input parts are the dicts of :mod:`satk.style.form` (``pos``, ``tris``, ``normals``, ``uv``, ``mat``, ``tex``,
``alpha``, ``role``). Example::

    from satk.style.defects import analyse
    r = analyse(parts, tex_sizes={"body": (256, 256)}, col=None)
    r["zfight"]     # [{"part": "chassis", "other": "chassis", "area_cm2": 120.0, "at": [...], ...}]
"""

from __future__ import annotations

__all__ = ["DEFAULTS", "CHECKS", "limits", "class_limits", "analyse", "col_dict"]

#: Built-in limits, equal to ``data/style/defects.json`` ``limits`` (the file wins). A finding above them is a real
#: flaw at a place (``warn``); ``<check>_defect`` per class in the file marks the vanilla range (``defect`` beyond).
DEFAULTS: dict = {
    "weld_m": 1e-4,
    # crack: two borders of one part run along each other ...
    "crack_mm": 25.0,               # ... closer than this ...
    "crack_open_mm": 1.0,           # ... but farther apart than this across the surface (closer: a T-junction) ...
    "crack_par_deg": 8.0,           # ... nearly parallel ...
    "crack_fold_deg": 45.0,         # ... with the faces on both sides within this angle (one surface) ...
    "crack_min_mm": 20.0,           # ... over at least this length per pair ...
    "crack_back_m": 2.0,            # ... and no face looks back within this distance behind the gap
    "crack_len_mm": 50.0,           # a part with more crack length than this is reported
    "crack_max_pairs": 3000,        # longest seam pairs tested per model
    # z-fighting: faces of different pieces or materials in one plane, facing one way, showing different texels
    "zfight_mm": 1.0,
    "zfight_deg": 3.0,
    "zfight_uv_px": 2.0,            # the same texture at UVs closer than this (pixels) shows the same: no fight
    "zfight_cm2": 4.0,              # overlap area per piece pair reported from this on
    # parts the game shows one at a time (variants): they may overlap each other
    "alternatives": [r"^extra\d+$", r"^(?:static|moving)_(rotor2?|prop2?)$"],
    # flipped
    "flip_smooth_deg": 30.0,        # winding is compared across edges of one smooth surface only
    "flip_cm2": 10.0,               # faces wound against their neighbours, area per part
    "inside_out_m3": 1e-06,         # a closed piece with a volume below minus this is inside out
    "normal_flip_share": 0.05,      # share of a part's area lit from behind above this ...
    "normal_flip_cm2": 50.0,        # ... and at least this area
    # degenerate and stray
    "degen_share": 0.01,            # zero-area triangles above this share of a part ...
    "degen_min": 8,                 # ... and at least this many
    "unused_share": 0.02,           # vertices no triangle uses above this share of a part ...
    "unused_min": 16,               # ... and at least this many
    "stray_mm": 5.0,                # a piece smaller than this in every direction is a stray island ...
    "stray_min": 1,                 # ... reported from this many on
    "far_m": 5.0,                   # a piece farther than this from the rest ...
    "far_rel": 1.0,                 # ... and farther than this x the size of the rest (and smaller) is a stray
    # UVs (per material of a part)
    "uv_solid_px": 2.0,             # a UV triangle smaller than this (pixels) samples one colour: never judged
    "uv_stretch": 100.0,            # texture smeared: stretched more than this in one direction ...
    "uv_share": 0.5,                # ... over more than this share of the material's area ...
    "uv_cm2": 2000.0,               # ... of at least this area
    "texel_ratio": 16.0,            # density off the material median by more than this factor ...
    "texel_share": 0.25,            # ... over more than this share of the material ...
    "texel_cm2": 2000.0,            # ... of at least this area
    # collision vs mesh
    "col_out_m": 0.5,               # collision beyond the mesh box by more than this ...
    "col_out_rel": 0.1,             # ... and this share of the mesh size
    "col_gap_m": 0.5,               # mesh farther than this from any collision ...
    "col_gap_rel": 0.1,             # ... and this share of the mesh size ...
    "col_share": 0.1,               # ... over more than this share of the solid mesh area
    "col_max_tris": 3000,           # the largest triangles that carry the share
    "examples": 3,
}

#: Every check of :func:`analyse`.
CHECKS = ("crack", "zfight", "flipped", "degenerate", "stray", "uv", "col")

_LIMITS: list = []


def _data() -> dict:
    try:
        from ..core import resources  # stdlib only; absent when this file is loaded on its own

        return resources.read_json("style", "defects.json")
    except Exception:  # noqa: BLE001 - the built-in defaults stand in
        return {}


def limits(override: dict | None = None) -> dict:
    """:data:`DEFAULTS` updated by ``data/style/defects.json`` (``limits``) and ``override``."""
    if not _LIMITS:
        lim = dict(DEFAULTS)
        lim.update(_data().get("limits", {}))
        _LIMITS.append(lim)
    out = dict(_LIMITS[0])
    out.update(override or {})
    return out


def class_limits(chain: list[str]) -> dict:
    """Limits for a class: :func:`limits` updated by ``classes[<key>]`` of the data file for every key of
    ``chain`` from the most general to the most specific (``["car", "car.sedan"]``)."""
    cls = _data().get("classes", {})
    out = limits()
    for k in chain:
        over = cls.get(k) or {}
        out.update({a: b for a, b in over.items() if not a.startswith("_")})
    return out


def _np():
    import numpy as np

    return np


def _r(x, nd: int = 2) -> float:
    return float(round(float(x), nd)) + 0.0


def _xyz(v) -> list:
    return [_r(x) for x in v]


# ----------------------------------------------------------------------------- welded edges of one part
def _edges(np, pp: dict):
    """Directed edges of the kept triangles of a prepared part: ``(face, a, b, key, sorted order, group starts,
    group counts)`` with ``a``/``b`` welded vertex ids and ``key = min * nw + max``."""
    keep = pp["keep"]
    fidx = np.flatnonzero(keep)
    W = pp["W"][fidx]
    a = W[:, [0, 1, 2]].reshape(-1)
    b = W[:, [1, 2, 0]].reshape(-1)
    f = np.repeat(fidx, 3)
    corner = np.tile(np.arange(3), len(fidx))
    nw = int(pp["nw"]) + 1
    key = np.minimum(a, b) * nw + np.maximum(a, b)
    o = np.argsort(key, kind="stable")
    key, a, b, f, corner = key[o], a[o], b[o], f[o], corner[o]
    if not len(key):
        return f, a, b, key, corner, np.zeros(0, np.int64), np.zeros(0, np.int64)
    st = np.flatnonzero(np.r_[True, key[1:] != key[:-1]])
    cnt = np.diff(np.r_[st, len(key)])
    return f, a, b, key, corner, st, cnt


# ----------------------------------------------------------------------------- degenerate and stray
def _degenerate(np, preps: list[dict], lim: dict) -> list[dict]:
    out = []
    for pp in preps:
        nt = len(pp["T"])
        if not nt:
            continue
        zero = int((~pp["keep"]).sum())
        nv = len(pp["P"])
        unused = nv - len(np.unique(pp["T"].reshape(-1)))
        row = {"part": pp["name"], "tris": nt, "zero": zero, "verts": nv, "unused": int(unused)}
        bad = []
        if zero >= lim["degen_min"] and zero > lim["degen_share"] * nt:
            bad.append("zero")
            T = pp["T"][~pp["keep"]]
            row["at"] = _xyz(pp["P"][T.reshape(-1)].mean(axis=0))
        if unused >= lim["unused_min"] and unused > lim["unused_share"] * nv:
            bad.append("unused")
        if bad:
            row["kind"] = bad
            out.append(row)
    return out


def _stray(np, preps: list[dict], pieces: list[dict], lim: dict) -> list[dict]:
    if len(pieces) < 2:
        return []
    lo = np.array([p["lo"] for p in pieces])
    hi = np.array([p["hi"] for p in pieces])
    ext = (hi - lo).max(axis=1)
    out = []
    tiny = np.flatnonzero(ext < lim["stray_mm"] / 1000.0)
    by_part: dict = {}
    for i in tiny.tolist():
        by_part.setdefault(preps[pieces[i]["part"]]["name"], []).append(i)
    for name, ids in sorted(by_part.items()):
        if len(ids) >= lim["stray_min"]:
            out.append({"part": name, "kind": "tiny", "pieces": len(ids),
                        "tris": int(sum(pieces[i]["tris"] for i in ids)),
                        "at": _xyz(np.mean([pieces[i]["centre"] for i in ids], axis=0))})
    # far pieces: box distance to the box of all the other pieces (second extreme per axis)
    olo, ohi = np.empty_like(lo), np.empty_like(hi)
    for ax in range(3):
        o = np.argsort(lo[:, ax], kind="stable")
        olo[:, ax] = lo[o[0], ax]
        olo[o[0], ax] = lo[o[1], ax]
        o = np.argsort(-hi[:, ax], kind="stable")
        ohi[:, ax] = hi[o[0], ax]
        ohi[o[0], ax] = hi[o[1], ax]
    gap = np.maximum(np.maximum(olo - hi, lo - ohi), 0.0)
    dist = np.linalg.norm(gap, axis=1)
    size = (ohi - olo).max(axis=1)
    area = np.array([p["area"] for p in pieces])
    # a stray is far from the rest and smaller than it (two equal halves are no strays of each other)
    far = np.flatnonzero((dist > lim["far_m"]) & (dist > lim["far_rel"] * size) & (area < area.sum() - area))
    for i in far.tolist()[: int(lim["examples"])]:
        pc = pieces[i]
        out.append({"part": preps[pc["part"]]["name"], "kind": "far", "pieces": 1, "tris": pc["tris"],
                    "dist_m": _r(dist[i]), "at": _xyz(pc["centre"])})
    return out


# ----------------------------------------------------------------------------- flipped
def _parity_components(nt: int, fa, fb, flip) -> tuple[list[int], list[int]]:
    """Union-find with parity: faces ``fa[i]`` and ``fb[i]`` are linked, ``flip[i]`` = one of them is wound the
    other way. Returns ``(root, parity)`` per face (contradicting links are ignored)."""
    par = list(range(nt))
    rel = [0] * nt

    def find(x):
        path = []
        p = 0
        while par[x] != x:
            path.append(x)
            x = par[x]
        root = x
        # compress: parity of every node on the path relative to the root
        acc = 0
        for node in reversed(path):
            acc ^= rel[node]
            rel[node] = acc
            par[node] = root
        if path:
            p = rel[path[0]]
        return root, p

    for x, y, s in zip(fa, fb, flip):
        rx, px = find(x)
        ry, py = find(y)
        if rx != ry:
            par[ry] = rx
            rel[ry] = px ^ py ^ int(s)
    roots, parity = [0] * nt, [0] * nt
    for i in range(nt):
        roots[i], parity[i] = find(i)
    return roots, parity


def _flipped(np, preps: list[dict], lim: dict, inside_ok: bool = False) -> list[dict]:
    """Faces wound against their neighbours across smooth edges (one surface), closed pieces inside out, faces
    whose normals point against their winding."""
    out = []
    cos_smooth = np.cos(np.radians(lim.get("flip_smooth_deg", 30.0)))
    for pp in preps:
        kept = pp["keep"]
        if not kept.any():
            continue
        f, a, b, key, _corner, st, cnt = _edges(np, pp)
        nt = len(pp["T"])
        area, fn = pp["area"], pp["fn"]
        row: dict = {"part": pp["name"]}
        two = st[cnt == 2]
        fa, fb = f[two], f[two + 1]
        same = a[two] == a[two + 1]                 # traversed the same way by both faces: one is flipped
        dot = (fn[fa] * fn[fb]).sum(1)
        # only edges inside one smooth surface tell the winding: there the faces are nearly coplanar, so a
        # flipped neighbour shows as an opposite normal (sharp folds and T-junctions of separate sheets say nothing)
        smooth = (np.abs(dot) >= cos_smooth) & ~(same & (dot > 0)) & ~(~same & (dot < 0))
        flip_area, flip_at = 0.0, None
        if (same & smooth).any():
            roots, parity = _parity_components(nt, fa[smooth].tolist(), fb[smooth].tolist(),
                                               same[smooth].tolist())
            roots, parity = np.asarray(roots), np.asarray(parity)
            for r_ in np.unique(roots[kept]).tolist():
                m = kept & (roots == r_)
                a1 = float(area[m & (parity == 1)].sum())
                if a1 <= 0.0:
                    continue
                a0 = float(area[m & (parity == 0)].sum())
                small = 1 if a1 < a0 else 0
                fa_ = min(a0, a1)
                flip_area += fa_
                if flip_at is None or fa_ > flip_at[0]:
                    sel = m & (parity == small)
                    flip_at = (fa_, pp["P"][pp["T"][sel].reshape(-1)].mean(axis=0))
        if flip_area > 0 and flip_at is not None and flip_area * 1e4 >= lim["flip_cm2"]:
            row.update(kind=["winding"], flip_cm2=_r(flip_area * 1e4, 1), at=_xyz(flip_at[1]))
        # closed pieces turned inside out (every edge shared by two faces, every one traversed both ways)
        if not inside_ok and "lab" in pp:
            lab = pp["lab"]
            open_faces = np.zeros(nt, bool)
            bad = cnt != 2
            if bad.any():
                idx = np.concatenate([f[s_:s_ + c_] for s_, c_ in zip(st[bad].tolist(), cnt[bad].tolist())])
                open_faces[idx] = True
            open_faces[fa[same]] = True
            open_faces[fb[same]] = True
            for k in np.unique(lab[kept]).tolist():
                m = kept & (lab == k)
                if open_faces[m].any() or m.sum() < 4:
                    continue
                T = pp["T"][m]
                A, B, C = pp["P"][T[:, 0]], pp["P"][T[:, 1]], pp["P"][T[:, 2]]
                vol = float((A * np.cross(B, C)).sum()) / 6.0
                if vol < -lim["inside_out_m3"]:
                    row.setdefault("kind", []).append("inside_out")
                    row["inside_out_m3"] = _r(row.get("inside_out_m3", 0.0) - vol, 4)
                    row.setdefault("at", _xyz((A + B + C).mean(axis=0) / 3))
        # normals against the winding (lit from behind)
        CN = pp.get("CN")
        if CN is not None:
            n = CN.mean(axis=1)
            n = n / np.maximum(np.linalg.norm(n, axis=1), 1e-30)[:, None]
            d = (n * fn).sum(axis=1)
            back = kept & (d < -0.5)
            ba = float(area[back].sum())
            tot = float(area[kept].sum())
            if tot > 0 and ba > 0 and ba * 1e4 >= lim["normal_flip_cm2"] and ba > lim["normal_flip_share"] * tot:
                row.setdefault("kind", []).append("normals")
                row["normals_share"] = _r(ba / tot, 3)
                if "at" not in row:
                    k = np.flatnonzero(back)[int(np.argmax(area[back]))]
                    row["at"] = _xyz(pp["P"][pp["T"][k]].mean(axis=0))
        if row.get("kind"):
            out.append(row)
    return out


# ----------------------------------------------------------------------------- cracks
def _global_ids(np, sc, preps) -> list:
    """Per prepared part: local triangle index -> global triangle index of the scene (-1 = not kept)."""
    gid = [np.full(len(pp["T"]), -1, np.int64) for pp in preps]
    if len(sc.owner):
        part_of = np.array([pc["part"] for pc in sc.pieces], dtype=np.int64)[sc.owner]
        for pi in range(len(preps)):
            m = part_of == pi
            gid[pi][sc.ltri[m]] = np.flatnonzero(m)
    return gid


def front_hit(np, sc, grid, O, D, tmax: float, skip=None, chunk: int = 400_000):
    """For rays ``O + t D`` (``t`` in 1e-4..``tmax``): True where an opaque face facing the ray is hit (back faces
    are culled in game: they do not stop the view). ``skip`` (K, 2): global triangles each ray ignores."""
    from .form import _seg_hits

    n = len(O)
    hit = np.zeros(n, bool)
    if not n or not len(sc.A):
        return hit
    S0 = O + D * 1e-4
    S1 = O + D * tmax
    qi, ti = grid.box_pairs(np.minimum(S0, S1), np.maximum(S0, S1))
    if not len(qi):
        return hit
    m = ~sc.tglass[ti] & ((sc.fn[ti] * D[qi]).sum(1) < 0)
    if skip is not None:
        m &= (ti != skip[qi, 0]) & (ti != skip[qi, 1])
    qi, ti = qi[m], ti[m]
    for a0 in range(0, len(qi), chunk):
        q, t = qi[a0:a0 + chunk], ti[a0:a0 + chunk]
        h, _x = _seg_hits(np, S0[q], S1[q], sc.A[t], sc.B[t], sc.C[t])
        hit[q[h]] = True
    return hit


def _cracks(np, preps: list[dict], lim: dict, sc=None, grid=None) -> tuple[list[dict], dict]:
    """Open seams inside one part that show the background (nothing facing the viewer behind the gap)."""
    from .form import _Grid

    tol = lim["crack_mm"] / 1000.0
    opn_tol = lim["crack_open_mm"] / 1000.0
    cos_par = np.cos(np.radians(lim["crack_par_deg"]))
    cos_fold = np.cos(np.radians(lim["crack_fold_deg"]))
    min_len = lim["crack_min_mm"] / 1000.0
    tj_total = 0.0
    cands = []                                         # (part index, e1 tri, e2 tri, base, u, t_lo, overlap, gap, n)
    for pi, pp in enumerate(preps):
        f, a, b, key, corner, st, cnt = _edges(np, pp)
        one = st[cnt == 1]
        if len(one) < 2:
            continue
        fe, ce = f[one], corner[one]
        T, P = pp["T"], pp["P"]
        S0, S1, C3 = P[T[fe, ce]], P[T[fe, (ce + 1) % 3]], P[T[fe, (ce + 2) % 3]]
        g = _Grid(np, np.minimum(S0, S1), np.maximum(S0, S1), tol)
        qi, ti = g.box_pairs(np.minimum(S0, S1) - tol, np.maximum(S0, S1) + tol)
        m = qi < ti
        qi, ti = qi[m], ti[m]
        if not len(qi):
            continue
        pair = np.unique(qi * (len(S0) + 1) + ti)
        qi, ti = pair // (len(S0) + 1), pair % (len(S0) + 1)
        d1 = S1[qi] - S0[qi]
        L1 = np.linalg.norm(d1, axis=1)
        d2 = S1[ti] - S0[ti]
        L2 = np.linalg.norm(d2, axis=1)
        ok = (L1 > 1e-6) & (L2 > 1e-6)
        u1 = d1 / np.maximum(L1, 1e-30)[:, None]
        u2 = d2 / np.maximum(L2, 1e-30)[:, None]
        ok &= np.abs((u1 * u2).sum(1)) >= cos_par
        # the other edge's ends in the frame of the first: along (t) and off (q) its line
        p0, p1 = S0[ti] - S0[qi], S1[ti] - S0[qi]
        t0, t1 = (p0 * u1).sum(1), (p1 * u1).sum(1)
        q0, q1 = p0 - t0[:, None] * u1, p1 - t1[:, None] * u1
        r0, r1 = np.linalg.norm(q0, axis=1), np.linalg.norm(q1, axis=1)
        ok &= np.maximum(r0, r1) <= tol
        tlo = np.maximum(0.0, np.minimum(t0, t1))
        ov = np.minimum(L1, np.maximum(t0, t1)) - tlo
        ok &= ov >= min_len
        # faces on opposite sides of the seam, continuing one surface
        s1 = C3[qi] - S0[qi]
        s1 = s1 - (s1 * u1).sum(1)[:, None] * u1
        s2 = C3[ti] - S0[ti]
        s2 = s2 - (s2 * u1).sum(1)[:, None] * u1
        ok &= (s1 * s2).sum(1) < 0
        fn = pp["fn"]
        ok &= (fn[fe[qi]] * fn[fe[ti]]).sum(1) >= cos_fold
        # the gap across the seam inside the surface (seen along the normal); negative = the borders overlap
        sh = s1 / np.maximum(np.linalg.norm(s1, axis=1), 1e-30)[:, None]
        lat = -((q0 * sh).sum(1) + (q1 * sh).sum(1)) / 2
        closed = ok & (lat < opn_tol) & (np.maximum(r0, r1) < opn_tol)
        tj_total += float(ov[closed].sum())
        ok &= lat >= opn_tol
        for k in np.flatnonzero(ok).tolist():
            nrm = fn[fe[qi[k]]] + fn[fe[ti[k]]]
            nrm = nrm / max(float(np.linalg.norm(nrm)), 1e-30)
            # the middle of the gap: half way from the first border towards the second
            base = S0[qi[k]] - sh[k] * (lat[k] / 2)
            cands.append((pi, int(fe[qi[k]]), int(fe[ti[k]]), base, u1[k], float(tlo[k]), float(ov[k]),
                          float(lat[k]), nrm))
    counts = {"tjunction_mm": _r(tj_total * 1000.0, 0)}
    if not cands:
        counts["crack_mm"] = 0.0
        return [], counts
    cands.sort(key=lambda c: -c[6])
    cands = cands[: int(lim.get("crack_max_pairs", 3000))]
    leak = np.ones(len(cands), bool)
    if sc is not None and grid is not None:
        gid = _global_ids(np, sc, preps)
        fr = (0.25, 0.5, 0.75)
        O = np.array([c[3] + c[4] * (c[5] + c[6] * w) + c[8] * 1e-3 for c in cands for w in fr])
        D = np.array([-c[8] for c in cands for _w in fr])
        skip = np.array([[gid[c[0]][c[1]], gid[c[0]][c[2]]] for c in cands for _w in fr], dtype=np.int64)
        back = front_hit(np, sc, grid, O, D, float(lim.get("crack_back_m", 2.0)), skip=skip)
        leak = back.reshape(len(cands), len(fr)).sum(axis=1) < 2
    by_part: dict = {}
    for c, lk in zip(cands, leak.tolist()):
        if lk:
            by_part.setdefault(c[0], []).append(c)
    out, total = [], 0.0
    for pi, cs in sorted(by_part.items()):
        length = sum(c[6] for c in cs)
        total += length
        if length * 1000.0 < lim["crack_len_mm"]:
            continue
        k = max(cs, key=lambda c: c[6])
        mid = k[3] + k[4] * (k[5] + k[6] / 2)
        out.append({"part": preps[pi]["name"], "len_mm": _r(length * 1000.0, 0), "pairs": len(cs),
                    "gap_mm": _r(max(c[7] for c in cs) * 1000.0, 2),
                    "area_mm2": _r(sum(c[6] * c[7] for c in cs) * 1e6, 0), "at": _xyz(mid)})
    out.sort(key=lambda r: (-r["len_mm"], r["part"]))
    counts["crack_mm"] = _r(total * 1000.0, 0)
    return out, counts


# ----------------------------------------------------------------------------- z-fighting
_SAMPLES = ((1 / 3, 1 / 3, 1 / 3), (2 / 3, 1 / 6, 1 / 6), (1 / 6, 2 / 3, 1 / 6), (1 / 6, 1 / 6, 2 / 3),
            (1 / 6, 5 / 12, 5 / 12), (5 / 12, 1 / 6, 5 / 12), (5 / 12, 5 / 12, 1 / 6))


def _bary(np, Q, A, B, C):
    """Barycentric ``(v, w)`` of points ``Q`` projected on the planes of triangles ``A, B, C`` (pairwise)."""
    v0, v1, v2 = B - A, C - A, Q - A
    d00, d01, d11 = (v0 * v0).sum(1), (v0 * v1).sum(1), (v1 * v1).sum(1)
    d20, d21 = (v2 * v0).sum(1), (v2 * v1).sum(1)
    den = d00 * d11 - d01 * d01
    den = np.where(np.abs(den) < 1e-30, 1e-30, den)
    return (d11 * d20 - d01 * d21) / den, (d00 * d21 - d01 * d20) / den


def _inside(np, Q, A, B, C, eps: float = 1e-6):
    """Points ``Q`` (K, 3) projected on the planes of triangles ``A, B, C`` (K, 3): inside (barycentric)."""
    v, w = _bary(np, Q, A, B, C)
    return (v >= -eps) & (w >= -eps) & (v + w <= 1 + eps)


def _zfight(np, sc, preps, pieces, lim: dict, grid=None, tex_sizes=None, tex_alpha=None
            ) -> tuple[list[dict], dict]:
    tol = lim["zfight_mm"] / 1000.0
    cos_d = np.cos(np.radians(lim["zfight_deg"]))
    if not len(sc.A):
        return [], {}
    g = grid or sc.grid(tol)
    qi, ti = g.box_pairs(sc.tlo - tol, sc.thi + tol)
    m = qi < ti
    qi, ti = qi[m], ti[m]
    if not len(qi):
        return [], {"zfight_cm2": 0.0}
    nt = len(sc.A) + 1
    pair = np.unique(qi.astype(np.int64) * nt + ti)
    qi, ti = pair // nt, pair % nt
    # material of each global triangle
    mats = np.concatenate([preps[pc["part"]]["M"][pc["tri"]] for pc in pieces])
    diff = (sc.owner[qi] != sc.owner[ti]) | (mats[qi] != mats[ti])
    alt = _alt_pieces(np, preps, pieces, lim)
    if alt is not None:
        pa, pb = sc.owner[qi], sc.owner[ti]
        diff &= ~((alt[pa] >= 0) & (alt[pa] == alt[pb]) & (np.array([pieces[i]["part"] for i in range(len(pieces))])[pa]
                  != np.array([pieces[i]["part"] for i in range(len(pieces))])[pb]))
    qi, ti = qi[diff], ti[diff]
    ok = (sc.fn[qi] * sc.fn[ti]).sum(1) >= cos_d
    qi, ti = qi[ok], ti[ok]
    if not len(qi):
        return [], {"zfight_cm2": 0.0}
    # the smaller triangle of a pair is sampled against the larger one
    area = 0.5 * np.linalg.norm(np.cross(sc.B - sc.A, sc.C - sc.A), axis=1)
    sw = area[qi] > area[ti]
    big = np.where(sw, qi, ti)
    small = np.where(sw, ti, qi)
    n = sc.fn[big]
    dv = np.stack([((X[small] - sc.A[big]) * n).sum(1) for X in (sc.A, sc.B, sc.C)], axis=1)
    ok = np.abs(dv).max(axis=1) <= tol
    big, small = big[ok], small[ok]
    if not len(big):
        return [], {"zfight_cm2": 0.0}
    # what each triangle shows: texture, key colour, UVs (a coplanar duplicate that shows the same is invisible)
    tex_g = np.concatenate([np.array([_tex(preps[pc["part"]], m) for m in preps[pc["part"]]["M"][pc["tri"]]],
                                     dtype=object) for pc in pieces])
    key_g = np.concatenate([np.array([_key(preps[pc["part"]], m) for m in preps[pc["part"]]["M"][pc["tri"]]],
                                     dtype=object) for pc in pieces])
    uv_g = np.concatenate([preps[pc["part"]]["UV"][pc["tri"]] if preps[pc["part"]].get("UV") is not None
                           else np.full((len(pc["tri"]), 3, 2), np.nan) for pc in pieces])
    sizes = {str(k).lower(): v for k, v in (tex_sizes or {}).items()}
    differs = (tex_g[big] != tex_g[small]) | (key_g[big] != key_g[small])
    # alpha decals (alpha texture or material alpha below 255) are drawn after the opaque faces: no fight
    ta = {str(t).lower() for t in (tex_alpha or ())}
    dec = np.concatenate([np.array([_decal(preps[pc["part"]], m, ta) for m in preps[pc["part"]]["M"][pc["tri"]]],
                                   dtype=bool) for pc in pieces])
    nd = ~(dec[big] | dec[small])
    big, small, differs = big[nd], small[nd], differs[nd]
    if not len(big):
        return [], {"zfight_cm2": 0.0}
    wh = np.array([sizes.get(t, (256, 256)) for t in tex_g[small]], dtype=np.float64).reshape(-1, 2)
    hits = np.zeros(len(big))
    shows = differs.copy()
    for w0, w1, w2 in _SAMPLES:
        Q = sc.A[small] * w0 + sc.B[small] * w1 + sc.C[small] * w2
        v, w = _bary(np, Q, sc.A[big], sc.B[big], sc.C[big])
        ins = (v >= -1e-6) & (w >= -1e-6) & (v + w <= 1 + 1e-6)
        hits += ins
        ub = uv_g[big, 0] * (1 - v - w)[:, None] + uv_g[big, 1] * v[:, None] + uv_g[big, 2] * w[:, None]
        us = uv_g[small, 0] * w0 + uv_g[small, 1] * w1 + uv_g[small, 2] * w2
        dpx = (np.abs(ub - us) * wh).max(axis=1)
        shows |= ins & ~(dpx <= lim.get("zfight_uv_px", 2.0))       # NaN (no UVs) counts as different
    share = hits / len(_SAMPLES)
    ov = share * area[small]
    keep = (ov > 0) & shows
    big, small, ov = big[keep], small[keep], ov[keep]
    agg: dict = {}
    for bi, si, o in zip(big.tolist(), small.tolist(), ov.tolist()):
        pa, pb = int(sc.owner[bi]), int(sc.owner[si])
        k = (min(pa, pb), max(pa, pb))
        r = agg.setdefault(k, [0.0, np.zeros(3), 0, set()])
        r[0] += o
        r[1] = r[1] + o * (sc.A[si] + sc.B[si] + sc.C[si]) / 3
        r[2] += 1
        r[3].add((int(mats[bi]), int(mats[si])))
    rows = []
    tot = 0.0
    for (pa, pb), (o, c, cnt, mp) in sorted(agg.items()):
        tot += o
        if o * 1e4 < lim["zfight_cm2"]:
            continue
        na, nb = preps[pieces[pa]["part"]], preps[pieces[pb]["part"]]
        rows.append({"part": na["name"], "other": nb["name"], "same_piece": pa == pb, "area_cm2": _r(o * 1e4, 1),
                     "tris": cnt, "at": _xyz(c / o),
                     "textures": sorted({t for x, y in mp for t in (_tex(na, x), _tex(nb, y)) if t})[:4]})
    rows.sort(key=lambda r: (-r["area_cm2"], r["part"]))
    return rows, {"zfight_cm2": _r(tot * 1e4, 1)}


def _alt_key(name: str, pats) -> str | None:
    import re

    for i, rx in enumerate(pats or []):
        m = re.match(rx, (name or "").lower())
        if m:
            return f"{i}:{m.group(1) if m.groups() else ''}"
    return None


def _alt_pieces(np, preps, pieces, lim):
    """Per piece an alternative-group id (-1 = none): pieces of different parts in one group never show together."""
    keys = [_alt_key(preps[pc["part"]]["name"], lim.get("alternatives")) for pc in pieces]
    if not any(keys):
        return None
    ids = {k: i for i, k in enumerate(sorted({k for k in keys if k}))}
    return np.array([ids[k] if k else -1 for k in keys], dtype=np.int64)


def _decal(pp: dict, m: int, ta: set) -> bool:
    if 0 <= m < len(pp["alpha"]) and pp["alpha"][m] < 255:
        return True
    if 0 <= m < len(pp.get("talpha") or []) and pp["talpha"][m]:
        return True
    return _tex(pp, m) in ta


def _key(pp: dict, m: int) -> str:
    keys = pp.get("keys") or []
    return keys[m] if 0 <= m < len(keys) else ""


def _tex(pp: dict, m: int) -> str:
    return pp["tex"][m] if 0 <= m < len(pp["tex"]) else ""


# ----------------------------------------------------------------------------- UVs
def _uv(np, preps: list[dict], lim: dict, tex_sizes: dict | None) -> tuple[list[dict], list[dict]]:
    stretch_rows, texel_rows = [], []
    sizes = {str(k).lower(): v for k, v in (tex_sizes or {}).items()}
    for pp in preps:
        UV = pp.get("UV")
        if UV is None:
            continue
        keep = pp["keep"]
        T, P = pp["T"], pp["P"]
        A, B, C = P[T[:, 0]], P[T[:, 1]], P[T[:, 2]]
        e1, e2 = B - A, C - A
        L = np.linalg.norm(e1, axis=1)
        x = e1 / np.maximum(L, 1e-30)[:, None]
        n = pp["fn"]
        y = np.cross(n, x)
        p1x, p2x, p2y = L, (e2 * x).sum(1), (e2 * y).sum(1)
        for mi in np.unique(pp["M"][keep]).tolist():
            m = keep & (pp["M"] == mi)
            tex = _tex(pp, mi)
            if not tex or not m.any():
                continue
            w, h = sizes.get(tex, (256, 256))
            q1 = (UV[m, 1] - UV[m, 0]) * np.array([w, h], dtype=np.float64)
            q2 = (UV[m, 2] - UV[m, 0]) * np.array([w, h], dtype=np.float64)
            det = p1x[m] * p2y[m]
            okd = np.abs(det) > 1e-14
            det = np.where(okd, det, 1.0)
            # J = [q1 q2] @ inv([[p1x, p2x], [0, p2y]])
            j00 = q1[:, 0] / p1x[m].clip(1e-30)
            j10 = q1[:, 1] / p1x[m].clip(1e-30)
            j01 = (q2[:, 0] - j00 * p2x[m]) / np.where(okd, p2y[m], 1.0)
            j11 = (q2[:, 1] - j10 * p2x[m]) / np.where(okd, p2y[m], 1.0)
            dj = np.abs(j00 * j11 - j01 * j10)
            fro = j00 ** 2 + j01 ** 2 + j10 ** 2 + j11 ** 2
            disc = np.sqrt(np.maximum(fro ** 2 - 4 * dj ** 2, 0.0))
            s1 = np.sqrt(np.maximum((fro + disc) / 2, 0.0))
            s2 = np.sqrt(np.maximum((fro - disc) / 2, 0.0))
            ar = pp["area"][m]
            # the UV triangle in pixels: below uv_solid_px it samples one colour (a vanilla trick, never smeared)
            upx = np.maximum(np.maximum(np.linalg.norm(q1, axis=1), np.linalg.norm(q2, axis=1)),
                             np.linalg.norm(q2 - q1, axis=1))
            solid = upx < lim["uv_solid_px"]
            tot = float(ar[okd & ~solid].sum())
            if tot <= 0:
                continue
            st = s1 / np.maximum(s2, 1e-12 * np.maximum(s1, 1e-30))
            smear = okd & ~solid & (st > lim["uv_stretch"])
            live = okd & ~solid & ~smear & (s2 > 0)
            dens = np.sqrt(dj)
            ba = float(ar[smear].sum())
            fi = np.flatnonzero(m)
            if ba > 0 and ba > lim["uv_share"] * tot and ba * 1e4 >= lim["uv_cm2"]:
                k = int(np.argmax(np.where(smear, ar, -1)))
                stretch_rows.append({"part": pp["name"], "texture": tex, "share": _r(ba / tot, 3),
                                     "area_cm2": _r(ba * 1e4, 1), "tris": int(smear.sum()),
                                     "at": _xyz((A[fi[k]] + B[fi[k]] + C[fi[k]]) / 3)})
            if not live.any():
                continue
            dl, al = dens[live], ar[live]
            o = np.argsort(dl, kind="stable")
            cum = np.cumsum(al[o])
            med = float(dl[o][min(int(np.searchsorted(cum, cum[-1] / 2)), len(o) - 1)])
            if med <= 0:
                continue
            ratio = np.where(live, dens / med, 1.0)
            off = live & ((ratio > lim["texel_ratio"]) | (ratio < 1.0 / lim["texel_ratio"]))
            oa = float(ar[off].sum())
            if oa > 0 and oa > lim["texel_share"] * tot and oa * 1e4 >= lim["texel_cm2"]:
                k = int(np.argmax(np.where(off, ar, -1)))
                texel_rows.append({"part": pp["name"], "texture": tex, "share": _r(oa / tot, 3),
                                   "area_cm2": _r(oa * 1e4, 0), "median_px_m": _r(med, 1),
                                   "ratio": _r(float(ratio[k]), 2),
                                   "at": _xyz((A[fi[k]] + B[fi[k]] + C[fi[k]]) / 3)})
    return stretch_rows, texel_rows


# ----------------------------------------------------------------------------- collision vs mesh
def col_dict(model) -> dict | None:
    """``{"spheres": [(c, r)], "boxes": [(lo, hi)], "verts": [...], "faces": [(a, b, c)]}`` of a decoded
    :class:`satk.rw.col.ColModel` (``None`` without solid collision)."""
    from ..rw.col import _verts_f

    verts = _verts_f(model)
    d = {"spheres": [(tuple(c), float(r)) for c, r, _s in model.spheres],
         "boxes": [(tuple(lo), tuple(hi)) for lo, hi, _s in model.boxes],
         "verts": [tuple(v) for v in verts], "faces": [tuple(f[:3]) for f in model.faces]}
    return d if (d["spheres"] or d["boxes"] or d["faces"]) else None


def _col(np, sc, preps, pieces, col: dict, lim: dict, solid_mask=None) -> tuple[list[dict], dict]:
    from .form import _Grid, _closest

    if not col or not len(sc.A):
        return [], {}
    solid = ~sc.tglass if solid_mask is None else solid_mask
    if not solid.any():
        return [], {}
    A, B, C = sc.A[solid], sc.B[solid], sc.C[solid]
    V = np.concatenate([A, B, C])
    mlo, mhi = V.min(axis=0), V.max(axis=0)
    size = float((mhi - mlo).max())
    rows: list[dict] = []
    # elements of the collision as boxes (lo, hi, kind, centre)
    els = []
    for c, r in col.get("spheres", []):
        c = np.asarray(c, dtype=np.float64)
        els.append((c - r, c + r, "sphere", c))
    for lo, hi in col.get("boxes", []):
        lo, hi = np.asarray(lo, dtype=np.float64), np.asarray(hi, dtype=np.float64)
        els.append((lo, hi, "box", (lo + hi) / 2))
    CV = np.asarray(col.get("verts") or [], dtype=np.float64).reshape(-1, 3)
    CF = np.asarray(col.get("faces") or [], dtype=np.int64).reshape(-1, 3)
    if len(CF):
        CF = CF[(CF < len(CV)).all(axis=1)]
    if len(CF):
        used = CV[np.unique(CF.reshape(-1))]
        els.append((used.min(axis=0), used.max(axis=0), "mesh", (used.min(axis=0) + used.max(axis=0)) / 2))
    thr_out = max(lim["col_out_m"], lim["col_out_rel"] * size)
    worst = None
    for lo, hi, kind, c in els:
        over = float(np.maximum(np.maximum(mlo - lo, hi - mhi), 0.0).max())
        if over > thr_out and (worst is None or over > worst[0]):
            worst = (over, kind, c)
    if worst is not None:
        rows.append({"kind": "outside", "what": worst[1], "over_m": _r(worst[0]), "at": _xyz(worst[2])})
    # mesh far from any collision: triangle centroids of the solid mesh
    thr = max(lim["col_gap_m"], lim["col_gap_rel"] * size)
    Q = (A + B + C) / 3
    area = 0.5 * np.linalg.norm(np.cross(B - A, C - A), axis=1)
    cap = int(lim.get("col_max_tris", 3000))
    if len(Q) > cap:                     # the share is by area: the largest triangles carry it
        big = np.sort(np.argsort(-area, kind="stable")[:cap])
        Q, area = Q[big], area[big]
    covered = np.zeros(len(Q), bool)
    for c, r in col.get("spheres", []):
        covered |= np.linalg.norm(Q - np.asarray(c, dtype=np.float64), axis=1) - float(r) <= thr
    for lo, hi in col.get("boxes", []):
        lo, hi = np.asarray(lo, dtype=np.float64), np.asarray(hi, dtype=np.float64)
        covered |= np.linalg.norm(np.maximum(np.maximum(lo - Q, Q - hi), 0.0), axis=1) <= thr
    if len(CF) and not covered.all():
        FA, FB, FC = CV[CF[:, 0]], CV[CF[:, 1]], CV[CF[:, 2]]
        g = _Grid(np, np.minimum(np.minimum(FA, FB), FC), np.maximum(np.maximum(FA, FB), FC), thr)
        rest = np.flatnonzero(~covered)
        qi, ti = g.pairs(Q[rest])
        if len(qi):                      # the face's box grown by the gap must hold the point
            flo = np.minimum(np.minimum(FA, FB), FC) - thr
            fhi = np.maximum(np.maximum(FA, FB), FC) + thr
            q = Q[rest][qi]
            inb = ((q >= flo[ti]) & (q <= fhi[ti])).all(axis=1)
            qi, ti = qi[inb], ti[inb]
        if len(qi):
            d, _ = _closest(np, Q[rest][qi], FA[ti], FB[ti], FC[ti])
            near = d <= thr
            covered[rest[np.unique(qi[near])]] = True
    tot = float(area.sum())
    un = float(area[~covered].sum())
    counts = {"col_uncovered": _r(un / tot, 3) if tot else 0.0}
    if tot and un > lim["col_share"] * tot:
        k = int(np.argmax(np.where(~covered, area, -1)))
        rows.append({"kind": "uncovered", "share": _r(un / tot, 3), "gap_m": _r(thr), "at": _xyz(Q[k])})
    return rows, counts


# ----------------------------------------------------------------------------- entry point
def analyse(parts: list[dict], *, checks: tuple = CHECKS, tex_sizes: dict | None = None, col: dict | None = None,
            lim: dict | None = None, inside_ok: bool = False, prepared: tuple | None = None,
            tex_alpha: set | None = None) -> dict:
    """Mesh defects of a set of parts (see the module docstring).

    Args:
        parts: part dicts in model space (wheels are skipped).
        checks: subset of :data:`CHECKS`.
        tex_sizes: ``{texture name: (w, h)}`` for the UV checks (unknown textures count as 256 x 256).
        col: :func:`col_dict` of the model's collision for the ``col`` check.
        lim: limits (default :func:`limits`); per class: :func:`class_limits`.
        inside_ok: closed pieces may face inwards (interior shells are seen from inside).
        prepared: ``(preps, pieces)`` of :func:`satk.style.form.pieces_of` when the caller has them.
        tex_alpha: textures with an alpha channel: a face using one is a decal and never z-fights (the engine
            draws alpha after the opaque faces); parts may also carry ``talpha`` per material.

    Returns ``{"crack": [...], "zfight": [...], "flipped": [...], "degenerate": [...], "stray": [...],
    "uv_stretch": [...], "texel": [...], "col": [...], "counts": {...}}`` (lists only when something is found).
    """
    from .form import _Scene, pieces_of

    np = _np()
    lim = lim or limits()
    parts = [p for p in parts if p.get("role") != "wheel"]
    preps, pieces = prepared if prepared is not None else pieces_of(parts, lim["weld_m"])
    out: dict = {}
    counts: dict = {}
    hd = [p for p in preps if p.get("role", "hd") != "context"]
    if "degenerate" in checks:
        r = _degenerate(np, hd, lim)
        if r:
            out["degenerate"] = r
    if "stray" in checks:
        r = _stray(np, preps, pieces, lim)
        if r:
            out["stray"] = r
    if "flipped" in checks:
        r = _flipped(np, hd, lim, inside_ok=inside_ok)
        if r:
            out["flipped"] = r
    sc = _Scene(np, preps, pieces) if pieces and ({"zfight", "col", "crack"} & set(checks)) else None
    grid = sc.grid(lim["zfight_mm"] / 1000.0) if sc is not None and len(sc.A) else None
    if "crack" in checks:
        r, c = _cracks(np, preps, lim, sc, grid)
        counts.update(c)
        hdn = {p["name"] for p in hd}
        r = [x for x in r if x["part"] in hdn]
        if r:
            out["crack"] = r
    if "zfight" in checks and sc is not None:
        r, c = _zfight(np, sc, preps, pieces, lim, grid, tex_sizes, tex_alpha)
        counts.update(c)
        if r:
            out["zfight"] = r
    if "uv" in checks:
        s, t = _uv(np, hd, lim, tex_sizes)
        if s:
            out["uv_stretch"] = s
        if t:
            out["texel"] = t
    if "col" in checks and sc is not None and col:
        ta = {str(t).lower() for t in (tex_alpha or ())}
        dec = np.concatenate([np.array([_decal(preps[pc["part"]], m, ta) for m in preps[pc["part"]]["M"][pc["tri"]]],
                                       dtype=bool) for pc in pieces]) if pieces else np.zeros(0, bool)
        r, c = _col(np, sc, preps, pieces, col, lim, solid_mask=~dec)
        counts.update(c)
        if r:
            out["col"] = r
    out["counts"] = counts
    return out
