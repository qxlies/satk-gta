"""Software preview renderer ``soft`` (SPEC §4.4): numpy z-buffer rasterizer, no GPU, no window.

What it draws (game-like, deterministic byte for byte on one machine):

* perspective camera on a sphere around the model: azimuth 0 = camera on +Y looking at -Y (front of
  a vehicle), growing counter-clockwise seen from above; elevation 25° by default; each view is
  framed tightly on the projected model;
* colour = texture (nearest, per-triangle mip level) × material colour × prelit vertex colour,
  with a soft two-sided headlight term (models without prelit colours get ambient + diffuse);
* alpha: textures with alpha are alpha-clipped at 0.5; translucent material colours (vehicle
  glass) are blended once over the nearest opaque surface;
* 2× supersampling (box filter) when the view is at most 256 px.

All numpy work happens inside functions (``satk`` modules never import numpy at import time).

Example::

    prep = prepare(scene, mats)
    imgs = render(prep, [(45, 25), (135, 25)], size=256)   # list of (256, 256, 3) uint8 arrays
    png = sheet_png(imgs, cols=2)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..core.errors import require_module
from .mesh import ModelScene, mat_mul
from .textures import MatInfo, decode

__all__ = ["RENDER_VERSION", "DEFAULT_BG", "Prepared", "prepare", "render", "sheet", "sheet_png", "view_angles",
           "placeholder"]

#: Bump when the output of the renderer changes (part of the thumbnail cache key).
RENDER_VERSION = 2
DEFAULT_BG = (178, 186, 196)
_HFOV = 30.0
_MARGIN = 0.05
_CHUNK = 1 << 21


def _np():
    return require_module("numpy", purpose="the soft model renderer")


def view_angles(n: int, el: float = 25.0) -> list[tuple[float, float]]:
    """``n`` views around the model: azimuths 45, 45 + 360/n, ... (4 -> 45/135/225/315)."""
    if not 1 <= n <= 16:
        raise ValueError("views must be 1..16")
    return [((45.0 + 360.0 * i / n) % 360.0, float(el)) for i in range(n)]


@dataclass
class Prepared:
    """Numpy arrays of the visible parts (model space, root applied)."""

    P: object                  # (N, 3) float64 positions
    N: object                  # (N, 3) float64 unit normals
    UV: object                 # (N, 2) float64
    C: object                  # (N, 3) float32 prelit rgb 0..1 (1 where the part has none)
    T: object                  # (M, 3) int64 triangles
    tri_mat: object            # (M,) int32 material index
    tri_lit: object            # (M,) bool: triangle has prelit colours
    mat_rgba: object           # (K, 4) float32 0..1
    mat_tex: object            # (K,) int32 texture index or -1
    mat_mode: object           # (K,) int8: 0 opaque, 1 alpha clip, 2 blend
    textures: list = field(default_factory=list)   # per texture: list of mip levels (h, w, 4) uint8
    tex_addr: list = field(default_factory=list)   # per texture: (uaddr, vaddr)
    tris: int = 0
    tex_missing: list = field(default_factory=list)


def _mips(np, rgba: bytes, w: int, h: int) -> list:
    lv = [np.frombuffer(rgba, dtype=np.uint8).reshape(h, w, 4)]
    cur = lv[0].astype(np.float32)
    while max(cur.shape[0], cur.shape[1]) > 1 and len(lv) < 12:
        hh, ww = cur.shape[0], cur.shape[1]
        if hh > 1:
            cur = cur[: hh // 2 * 2].reshape(hh // 2, 2, cur.shape[1], 4).mean(axis=1)
        if ww > 1:
            cur = cur[:, : ww // 2 * 2].reshape(cur.shape[0], ww // 2, 2, 4).mean(axis=2)
        lv.append(np.clip(np.floor(cur + 0.5), 0, 255).astype(np.uint8))
    return lv


def _smooth_normals_np(np, P, T):
    a, b, c = P[T[:, 0]], P[T[:, 1]], P[T[:, 2]]
    fn = np.cross(b - a, c - a)
    acc = np.zeros_like(P)
    for k in range(3):
        np.add.at(acc, T[:, k], fn)
    ln = np.linalg.norm(acc, axis=1, keepdims=True)
    out = np.where(ln > 1e-20, acc / np.maximum(ln, 1e-30), np.array([0.0, 0.0, 1.0]))
    return out


def prepare(scene: ModelScene, mats: list[list[MatInfo]], *, parts=None) -> Prepared:
    """Flatten the visible preview parts into numpy arrays (textures decoded via the shared cache)."""
    np = _np()
    parts = [p for p in (scene.preview_parts if parts is None else parts) if not p.hidden]
    Ps, Ns, UVs, Cs, Ts, TMs, TLs = [], [], [], [], [], [], []
    mat_rgba, mat_tex, mat_mode = [], [], []
    tex_index: dict[tuple, int] = {}
    pixel_mips: dict[tuple, list] = {}
    textures, tex_addr = [], []
    missing: set[str] = set()
    vbase = 0
    geom_mats: dict[int, int] = {}            # geometry -> first material index in the table
    for p in parts:
        mesh = scene.meshes[p.geom]
        nv = len(mesh.positions) // 3
        nt = len(mesh.tris) // 3
        if nv == 0 or nt == 0:
            continue
        m = mat_mul(scene.root, p.matrix)
        R = np.array([[m[0], m[1], m[2]], [m[3], m[4], m[5]], [m[6], m[7], m[8]]], dtype=np.float64)
        t = np.array([m[9], m[10], m[11]], dtype=np.float64)
        pos = np.frombuffer(mesh.positions.tobytes(), dtype=np.float32).astype(np.float64).reshape(nv, 3)
        P = pos @ R + t
        T = np.frombuffer(mesh.tris.tobytes(), dtype=np.uint32).astype(np.int64).reshape(nt, 3)
        fin = np.isfinite(P).all(axis=1)
        tri_ok = fin[T].all(axis=1) if not fin.all() else None   # drop triangles on NaN/inf vertices
        if tri_ok is not None:
            P = np.where(fin[:, None], P, 0.0)
            T = T[tri_ok]
        if mesh.normals is not None:
            nrm = np.frombuffer(mesh.normals.tobytes(), dtype=np.float32).astype(np.float64).reshape(nv, 3) @ R
            ln = np.linalg.norm(nrm, axis=1, keepdims=True)
            bad = (ln[:, 0] < 1e-12) | ~np.isfinite(ln[:, 0])
            nrm = np.where(ln > 1e-12, nrm / np.maximum(ln, 1e-30), 0.0)
            if bad.any():
                nrm[bad] = _smooth_normals_np(np, P, T)[bad]
        else:
            nrm = _smooth_normals_np(np, P, T)
        if mesh.uv:
            uv = np.frombuffer(mesh.uv[0].tobytes(), dtype=np.float32).astype(np.float64).reshape(nv, 2)
            uv = np.where(np.isfinite(uv), uv, 0.0)              # NaN/inf UVs exist in a few game models
        else:
            uv = np.zeros((nv, 2))
        if mesh.prelit is not None:
            C = np.frombuffer(bytes(mesh.prelit), dtype=np.uint8).reshape(nv, 4)[:, :3].astype(np.float32) / 255.0
            lit = True
        else:
            C = np.ones((nv, 3), dtype=np.float32)
            lit = False
        if p.geom not in geom_mats:
            geom_mats[p.geom] = len(mat_rgba)
            for mi in (mats[p.geom] if p.geom < len(mats) else []):
                mat_rgba.append([c / 255.0 for c in mi.rgba])
                ti = -1
                if mi.tex is not None:
                    d = decode(mi.tex)
                    key = mi.tex.binding_key
                    ti = tex_index.get(key, -1)
                    if ti < 0:
                        pk = mi.tex.pixel_key
                        if pk not in pixel_mips:
                            pixel_mips[pk] = _mips(np, d.rgba, d.w, d.h)
                        textures.append(pixel_mips[pk])
                        tex_addr.append((d.uaddr, d.vaddr))
                        ti = tex_index[key] = len(textures) - 1
                    alpha = d.alpha
                else:
                    alpha = False
                    if mi.missing:
                        missing.add(mi.tex_name)
                mat_tex.append(ti)
                mat_mode.append(2 if mi.rgba[3] < 250 else (1 if alpha else 0))
            nslots = len(mats[p.geom]) if p.geom < len(mats) else 0
            if nslots == 0:                         # no material list: plain white
                mat_rgba.append([1.0, 1.0, 1.0, 1.0])
                mat_tex.append(-1)
                mat_mode.append(0)
        base = geom_mats[p.geom]
        nslots = max(1, len(mats[p.geom]) if p.geom < len(mats) else 0)
        mids = np.frombuffer(mesh.mat_ids.tobytes(), dtype=np.uint16).astype(np.int32)
        mids = np.where(mids < nslots, mids, 0) + base
        if tri_ok is not None:
            mids = mids[tri_ok]
        Ps.append(P)
        Ns.append(nrm)
        UVs.append(uv)
        Cs.append(C)
        Ts.append(T + vbase)
        TMs.append(mids)
        TLs.append(np.full(len(T), lit))
        vbase += nv
    if not Ts:
        z3 = np.zeros((0, 3))
        return Prepared(z3, z3, np.zeros((0, 2)), np.zeros((0, 3), np.float32), np.zeros((0, 3), np.int64),
                        np.zeros(0, np.int32), np.zeros(0, bool), np.zeros((0, 4), np.float32),
                        np.zeros(0, np.int32), np.zeros(0, np.int8), [], [], 0, sorted(missing))
    T = np.concatenate(Ts)
    return Prepared(np.concatenate(Ps), np.concatenate(Ns), np.concatenate(UVs), np.concatenate(Cs), T,
                    np.concatenate(TMs).astype(np.int32), np.concatenate(TLs),
                    np.array(mat_rgba, dtype=np.float32).reshape(-1, 4), np.array(mat_tex, dtype=np.int32),
                    np.array(mat_mode, dtype=np.int8), textures, tex_addr, int(len(T)), sorted(missing))


# ----------------------------------------------------------------------------- sampling
def _wrap(np, u, mode: int):
    u = np.where(np.isfinite(u), u, 0.0)                    # NaN/inf UVs exist in a few game models
    if mode == 3 or mode == 4:                              # clamp / border
        return np.clip(u, 0.0, 1.0)
    if mode == 2:                                           # mirror
        t = np.mod(u, 2.0)
        return np.where(t > 1.0, 2.0 - t, t)
    return u - np.floor(u)                                  # wrap


def _sample(np, prep: Prepared, tex, lvl, u, v):
    """RGBA uint8 (n, 4) for fragments (nearest); ``tex`` >= 0 for all of them."""
    out = np.empty((len(tex), 4), dtype=np.uint8)
    if len(tex) == 0:
        return out
    key = tex.astype(np.int64) * 16 + lvl
    order = np.argsort(key, kind="stable")
    ks = key[order]
    cuts = np.flatnonzero(np.diff(ks)) + 1
    for grp in np.split(order, cuts):
        k = int(key[grp[0]])
        ti, li = k // 16, k % 16
        levels = prep.textures[ti]
        img = levels[min(li, len(levels) - 1)]
        h, w = img.shape[0], img.shape[1]
        ua, va = prep.tex_addr[ti]
        uu = _wrap(np, u[grp], ua)
        vv = _wrap(np, v[grp], va)
        x = np.clip((uu * w).astype(np.int64), 0, w - 1)
        y = np.clip((vv * h).astype(np.int64), 0, h - 1)
        out[grp] = img[y, x]
    return out


# ----------------------------------------------------------------------------- render
def _camera(np, prep: Prepared, az: float, el: float, S: int):
    used = np.unique(prep.T)
    Pu = prep.P[used]
    lo, hi = Pu.min(axis=0), Pu.max(axis=0)
    center = (lo + hi) / 2.0
    radius = float(np.sqrt(((Pu - center) ** 2).sum(axis=1)).max())
    radius = max(radius, 1e-3)
    a, e = math.radians(az), math.radians(el)
    d = np.array([-math.sin(a) * math.cos(e), math.cos(a) * math.cos(e), math.sin(e)])
    dist = radius / math.sin(math.radians(_HFOV) / 2.0)
    eye = center + d * dist
    f = -d
    up0 = np.array([0.0, 0.0, 1.0]) if abs(f[2]) < 0.999 else np.array([0.0, 1.0, 0.0])
    right = np.cross(f, up0)
    right /= np.linalg.norm(right)
    up = np.cross(right, f)
    V = prep.P - eye
    X, Y, Z = V @ right, V @ up, V @ f
    Z = np.maximum(Z, dist * 1e-4)
    xp, yp = X / Z, Y / Z
    xu, yu = xp[used], yp[used]
    xc, yc = (xu.min() + xu.max()) / 2.0, (yu.min() + yu.max()) / 2.0
    ext = max(float(xu.max() - xu.min()), float(yu.max() - yu.min()), 1e-9)
    s = S * (1.0 - 2.0 * _MARGIN) / ext
    sx = S / 2.0 + (xp - xc) * s
    sy = S / 2.0 - (yp - yc) * s
    light = -0.35 * right + 0.55 * up - 0.75 * f
    light /= np.linalg.norm(light)
    return sx, sy, 1.0 / Z, light


def _lod(np, prep: Prepared, sx, sy, area2):
    """Per-triangle mip level from texel/pixel area ratio."""
    T = prep.T
    tex = prep.mat_tex[prep.tri_mat]
    lvl = np.zeros(len(T), dtype=np.int64)
    has = tex >= 0
    if not has.any():
        return lvl
    uv0, uv1, uv2 = prep.UV[T[:, 0]], prep.UV[T[:, 1]], prep.UV[T[:, 2]]
    uva = np.abs((uv1[:, 0] - uv0[:, 0]) * (uv2[:, 1] - uv0[:, 1]) - (uv2[:, 0] - uv0[:, 0]) * (uv1[:, 1] - uv0[:, 1]))
    wh = np.zeros(len(T))
    nlev = np.ones(len(T), dtype=np.int64)
    sizes = np.array([lv[0].shape[0] * lv[0].shape[1] for lv in prep.textures], dtype=np.float64)
    counts = np.array([len(lv) for lv in prep.textures], dtype=np.int64)
    wh[has] = sizes[tex[has]]
    nlev[has] = counts[tex[has]]
    ratio = uva * wh / np.maximum(np.abs(area2), 1e-9)
    with np.errstate(divide="ignore", invalid="ignore"):
        lod = 0.5 * np.log2(np.where(ratio > 0, ratio, 1.0))
    lvl = np.clip(np.floor(lod + 0.5), 0, nlev - 1).astype(np.int64)
    lvl[~has] = 0
    return lvl


def _render_one(np, prep: Prepared, az: float, el: float, S: int, bg):
    T = prep.T
    sx, sy, iz, light = _camera(np, prep, az, el, S)
    x0, x1, x2 = sx[T[:, 0]], sx[T[:, 1]], sx[T[:, 2]]
    y0, y1, y2 = sy[T[:, 0]], sy[T[:, 1]], sy[T[:, 2]]
    area2 = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
    lvl = _lod(np, prep, sx, sy, area2)
    ix0 = np.ceil(np.minimum(np.minimum(x0, x1), x2) - 0.5)
    ix1 = np.floor(np.maximum(np.maximum(x0, x1), x2) - 0.5)
    iy0 = np.ceil(np.minimum(np.minimum(y0, y1), y2) - 0.5)
    iy1 = np.floor(np.maximum(np.maximum(y0, y1), y2) - 0.5)
    ix0, iy0 = np.clip(ix0, 0, S - 1), np.clip(iy0, 0, S - 1)
    ix1, iy1 = np.clip(ix1, -1, S - 1), np.clip(iy1, -1, S - 1)
    bw = (ix1 - ix0 + 1).astype(np.int64)
    bh = (iy1 - iy0 + 1).astype(np.int64)
    valid = (np.abs(area2) > 1e-12) & (bw > 0) & (bh > 0) & np.isfinite(area2)
    with np.errstate(divide="ignore", invalid="ignore"):
        inv = np.where(valid, 1.0 / np.where(valid, area2, 1.0), 0.0)
    A0, B0, C0 = (y1 - y2) * inv, (x2 - x1) * inv, (x1 * y2 - x2 * y1) * inv
    A1, B1, C1 = (y2 - y0) * inv, (x0 - x2) * inv, (x2 * y0 - x0 * y2) * inv
    iz0, iz1, iz2 = iz[T[:, 0]], iz[T[:, 1]], iz[T[:, 2]]
    used = np.unique(T)
    izmin, izmax = float(iz[used].min()), float(iz[used].max())
    span = max(izmax - izmin, 1e-30)
    M = len(T)
    tri_bits = max(21, int(M).bit_length())
    dq_bits = 62 - tri_bits
    dq_max = (1 << dq_bits) - 1
    INF = np.int64((1 << 62) + 1)
    mode = prep.mat_mode[prep.tri_mat]
    zb = np.full(S * S, INF, dtype=np.int64)
    zt = np.full(S * S, INF, dtype=np.int64)

    idx = np.flatnonzero(valid)
    counts = (bw * bh)[idx]
    cum = np.cumsum(counts)
    start = 0
    while start < len(idx):
        base = int(cum[start - 1]) if start else 0
        stop = int(np.searchsorted(cum, base + _CHUNK, side="right"))
        stop = max(stop, start + 1)
        sel = idx[start:stop]
        c = counts[start:stop]
        n = int(c.sum())
        tl = np.repeat(np.arange(len(sel)), c)
        offs = np.cumsum(c) - c
        local = np.arange(n, dtype=np.int64) - offs[tl]
        tri = sel[tl]
        bwt = bw[tri]
        px = ix0[tri].astype(np.int64) + local % bwt
        py = iy0[tri].astype(np.int64) + local // bwt
        cx, cy = px + 0.5, py + 0.5
        w0 = A0[tri] * cx + B0[tri] * cy + C0[tri]
        w1 = A1[tri] * cx + B1[tri] * cy + C1[tri]
        w2 = 1.0 - w0 - w1
        ins = (w0 >= -1e-9) & (w1 >= -1e-9) & (w2 >= -1e-9)
        tri, px, py, w0, w1, w2 = tri[ins], px[ins], py[ins], w0[ins], w1[ins], w2[ins]
        izf = w0 * iz0[tri] + w1 * iz1[tri] + w2 * iz2[tri]
        dq = np.clip(((izmax - izf) / span * dq_max), 0, dq_max).astype(np.int64)
        key = (dq << tri_bits) | tri
        pix = py * S + px
        md = mode[tri]
        keep = md == 0
        alpha_sel = np.flatnonzero(md != 0)
        if len(alpha_sel):
            ta = tri[alpha_sel]
            texa = prep.mat_tex[prep.tri_mat[ta]]
            b0 = w0[alpha_sel] * iz0[ta] / izf[alpha_sel]
            b1 = w1[alpha_sel] * iz1[ta] / izf[alpha_sel]
            b2 = 1.0 - b0 - b1
            Ta = T[ta]
            u = b0 * prep.UV[Ta[:, 0], 0] + b1 * prep.UV[Ta[:, 1], 0] + b2 * prep.UV[Ta[:, 2], 0]
            v = b0 * prep.UV[Ta[:, 0], 1] + b1 * prep.UV[Ta[:, 1], 1] + b2 * prep.UV[Ta[:, 2], 1]
            a = np.ones(len(ta), dtype=np.float32)
            ht = texa >= 0
            if ht.any():
                a[ht] = _sample(np, prep, texa[ht], lvl[ta[ht]], u[ht], v[ht])[:, 3].astype(np.float32) / 255.0
            a = a * prep.mat_rgba[prep.tri_mat[ta], 3]
            ma = md[alpha_sel]
            clip_ok = (ma == 1) & (a >= 0.5)
            blend_ok = (ma == 2) & (a > 0.02)
            keep[alpha_sel[clip_ok]] = True
            bsel = alpha_sel[blend_ok]
            if len(bsel):
                np.minimum.at(zt, pix[bsel], key[bsel])
        if keep.any():
            np.minimum.at(zb, pix[keep], key[keep])
        start = stop

    img = np.empty((S * S, 3), dtype=np.float32)
    img[:] = np.array(bg, dtype=np.float32) / 255.0
    mask = (1 << tri_bits) - 1
    cov = np.flatnonzero(zb != INF)
    if len(cov):
        rgb, _a = _shade(np, prep, zb[cov] & mask, cov, S, A0, B0, C0, A1, B1, C1, iz0, iz1, iz2, lvl, light)
        img[cov] = rgb
    tcov = np.flatnonzero((zt != INF) & ((zt >> tri_bits) < (zb >> tri_bits)))
    if len(tcov):
        rgb, a = _shade(np, prep, zt[tcov] & mask, tcov, S, A0, B0, C0, A1, B1, C1, iz0, iz1, iz2, lvl, light)
        a = a[:, None]
        img[tcov] = rgb * a + img[tcov] * (1.0 - a)
    return img.reshape(S, S, 3), len(cov)


def _shade(np, prep: Prepared, tri, pix, S, A0, B0, C0, A1, B1, C1, iz0, iz1, iz2, lvl, light):
    cx = (pix % S) + 0.5
    cy = (pix // S) + 0.5
    w0 = A0[tri] * cx + B0[tri] * cy + C0[tri]
    w1 = A1[tri] * cx + B1[tri] * cy + C1[tri]
    w2 = 1.0 - w0 - w1
    izf = w0 * iz0[tri] + w1 * iz1[tri] + w2 * iz2[tri]
    b0 = w0 * iz0[tri] / izf
    b1 = w1 * iz1[tri] / izf
    b2 = 1.0 - b0 - b1
    Tt = prep.T[tri]
    bb = (b0[:, None], b1[:, None], b2[:, None])

    def interp(arr):
        return bb[0] * arr[Tt[:, 0]] + bb[1] * arr[Tt[:, 1]] + bb[2] * arr[Tt[:, 2]]

    mat = prep.tri_mat[tri]
    rgba = prep.mat_rgba[mat]
    col = rgba[:, :3].astype(np.float32).copy()
    alpha = rgba[:, 3].astype(np.float32).copy()
    tex = prep.mat_tex[mat]
    ht = tex >= 0
    if ht.any():
        uv = interp(prep.UV)
        s = _sample(np, prep, tex[ht], lvl[tri[ht]], uv[ht, 0], uv[ht, 1]).astype(np.float32) / 255.0
        col[ht] *= s[:, :3]
        alpha[ht] *= s[:, 3]
    n = interp(prep.N)
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    n = n / np.maximum(ln, 1e-12)
    ndl = np.abs(n @ light).astype(np.float32)[:, None]
    lit = prep.tri_lit[tri]
    shade = np.where(lit[:, None], 0.80 + 0.30 * ndl, 0.42 + 0.68 * ndl).astype(np.float32)
    if lit.any():
        pre = interp(prep.C.astype(np.float64)).astype(np.float32)
        col[lit] *= np.minimum(pre[lit] * 1.15, 1.0)
    col = np.clip(col * shade, 0.0, 1.0)
    return col, np.clip(alpha, 0.0, 1.0)


def render(prep: Prepared, views: list[tuple[float, float]], size: int, *, bg=DEFAULT_BG,
           ss: int | None = None) -> tuple[list, list[float]]:
    """``([(size, size, 3) uint8], [coverage share per view])``; ``ss`` = supersampling (default 2 if
    ``size <= 256`` else 1)."""
    np = _np()
    if ss is None:
        ss = 2 if size <= 256 else 1
    S = size * ss
    out, cover = [], []
    for az, el in views:
        if prep.tris == 0:
            img = placeholder(size, bg)
            out.append(img)
            cover.append(0.0)
            continue
        f, ncov = _render_one(np, prep, float(az), float(el), S, bg)
        if ss > 1:
            f = f.reshape(size, ss, size, ss, 3).mean(axis=(1, 3))
        out.append(np.clip(np.floor(f * 255.0 + 0.5), 0, 255).astype(np.uint8))
        cover.append(ncov / float(S * S))
    return out, cover


def placeholder(size: int, bg=DEFAULT_BG):
    """A view with nothing to draw: background with a dark diagonal cross."""
    np = _np()
    img = np.empty((size, size, 3), dtype=np.uint8)
    img[:] = np.array(bg, dtype=np.uint8)
    dark = (np.array(bg, dtype=np.float32) * 0.6).astype(np.uint8)
    r = np.arange(size)
    m = max(1, size // 8)
    for k in range(max(1, size // 128)):
        rr = r[m:size - m]
        img[np.clip(rr + k, 0, size - 1), rr] = dark
        img[np.clip(rr + k, 0, size - 1), size - 1 - rr] = dark
    return img


def sheet(images: list, cols: int | None = None, *, bg=DEFAULT_BG, line: int = 1):
    """Tile equally sized views row-major into one image (``cols`` default ``ceil(sqrt(n))``);
    1 px darker separators between cells."""
    np = _np()
    n = len(images)
    cols = cols or max(1, math.ceil(math.sqrt(n)))
    rows = math.ceil(n / cols)
    h, w = images[0].shape[0], images[0].shape[1]
    out = np.empty((rows * h, cols * w, 3), dtype=np.uint8)
    out[:] = np.array(bg, dtype=np.uint8)
    for i, im in enumerate(images):
        r, c = divmod(i, cols)
        out[r * h:(r + 1) * h, c * w:(c + 1) * w] = im
    if line and (rows > 1 or cols > 1):
        dark = (np.array(bg, dtype=np.float32) * 0.55).astype(np.uint8)
        for c in range(1, cols):
            out[:, c * w - line:c * w] = dark
        for r in range(1, rows):
            out[r * h - line:r * h, :] = dark
    return out


def sheet_png(images: list, cols: int | None = None, *, bg=DEFAULT_BG) -> bytes:
    """PNG bytes of :func:`sheet` (or of the single view)."""
    from .png import encode_png

    img = images[0] if len(images) == 1 else sheet(images, cols, bg=bg)
    h, w = img.shape[0], img.shape[1]
    return encode_png(w, h, img.tobytes(), 3)
