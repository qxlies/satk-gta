"""Detail coverage: which details of the class checklist a model has (``data/style/coverage/<class>.json``).

A checklist lists the details a vanilla model of the class carries in geometry or on its shared textures (lamps on
their key colours, mirrors, handles, seats, steering wheel, gauges, engine bay, plates, exhaust, lined arches ...).
Each item names a detector; the answer is information for the detail pass, never a verdict.

Detectors (``how``):

* ``keys`` / ``keys_all``: any / every listed material key colour (``"255,175,0"``) is used;
* ``texture``: a triangle uses a texture whose name contains one of the strings;
* ``parts``: an atomic whose name matches the regular expression;
* ``frame_near``: geometry within ``radius`` m of the frame (``{"frame": "exhaust", "radius": 0.25}``);
* ``region``: at least ``min_tris`` triangles whose centres lie in a vehicle region (``cabin``, ``engine_bay``)
  outside the panels named in ``skip``;
* ``outboard``: geometry beyond the body side by ``min`` m above the wheel tops (mirrors);
* ``small_pieces``: a separate piece of at most ``max_tris`` triangles in the parts matching ``parts`` (handles);
* ``above_roof``: a separate piece of the body at least ``min_len`` m long on top of the roof (rails);
* ``lined_arches``: no see-through arch (``satk.style.form`` ``see_through``) and at least one arch;
* ``seat``: a surface under ``ped_frontseat`` (bikes);
* ``flags``: model facts ``prelit``, ``night``, ``collision``, ``textured``, ``skinned``;
* ``glass``: a material with alpha below 255 that is not a lamp key colour (windows anywhere: windscreen part,
  doors or the body);
* ``mirrors``: a mirror part, or geometry standing out of the body side by ``mirrors`` m above the wheel tops (the
  body width is measured below that band, so mirrors modelled into the body or the doors count);
* ``frames``: a frame whose name matches the regular expression;
* ``frame_geom``: an atomic whose name matches the regular expression with at least ``min_tris`` triangles;
* ``near_frames``: geometry within ``radius`` m of every listed frame (ped head, hands, feet);
* ``lod_model``: a LOD model for it (index: placements with a LOD; files: a ``lod*.dff`` next to it).

numpy inside the functions.
"""

from __future__ import annotations

import functools
import re

from ..core import resources
from ..core.errors import SatkError

__all__ = ["checklist", "coverage_rows", "list_classes"]


def list_classes() -> list[str]:
    return [n.rsplit("/", 1)[-1][:-5] for n in resources.list_files("style", "coverage", pattern="*.json")]


@functools.lru_cache(maxsize=32)
def _load(name: str) -> dict:
    return resources.read_json("style", "coverage", f"{name}.json")


def checklist(peer_key: str) -> dict | None:
    """The checklist of a class: ``<class>.json``, else its parents (``car.suv_pickup`` -> ``car``)."""
    from . import classes as C

    have = set(list_classes())
    for k in [*C.fallback_chain(C.split_peer(peer_key)[0]), C.family(peer_key)]:
        if k in have:
            try:
                return _load(k)
            except SatkError:
                return None
    return None


def _tris(np, parts):
    A, M = [], []
    for p in parts:
        if p.get("role") == "wheel":
            continue
        X = p["pos"][p["tris"]]
        A.append(X)
        mats = p.get("mat")
        tex = p.get("tex") or []
        keys = p.get("keys") or []
        idx = np.asarray(mats, dtype=np.int64) if mats is not None else np.zeros(len(X), np.int64)
        M += [(p["name"].lower(), tex[i] if 0 <= i < len(tex) else "", keys[i] if 0 <= i < len(keys) else "")
              for i in idx]
    X = np.concatenate(A) if A else np.zeros((0, 3, 3))
    return X, M


def _regions(np, frames: dict, X) -> dict:
    """Vehicle regions (boxes ``(lo, hi)``) from the wheel dummies and the body box."""
    w = {n: np.asarray(f["pos"], dtype=np.float64) for n, f in frames.items() if n.startswith("wheel_")
         and n.endswith("_dummy")}
    if len(w) < 2 or not len(X):
        return {}
    ys = [p[1] for p in w.values()]
    zs = [p[2] for p in w.values()]
    xs = [abs(p[0]) for p in w.values()]
    yf, yr, zc, half = max(ys), min(ys), float(np.mean(zs)), max(xs)
    V = X.reshape(-1, 3)
    top, front = float(V[:, 2].max()), float(V[:, 1].max())
    wb = yf - yr
    return {"cabin": (np.array([-half, yr + 0.1 * wb, zc]), np.array([half, yf - 0.25 * wb, top - 0.15])),
            "engine_bay": (np.array([-half + 0.1, yf - 0.2 * wb, zc - 0.1]),
                           np.array([half - 0.1, front - 0.25, top]))}


def _pieces(np, part: dict):
    from .form import pieces_of

    _preps, pcs = pieces_of([dict(part, role="hd")])
    return pcs


def _detect(np, how: dict, parts, frames, X, M, ctx: dict) -> bool | None:
    names = {p["name"].lower() for p in parts}
    if "keys" in how or "keys_all" in how:
        used = {k for _n, _t, k in M}
        if "keys" in how:
            return bool(used & set(how["keys"]))
        return set(how["keys_all"]) <= used
    if "texture" in how:
        subs = [s.lower() for s in how["texture"]]
        return any(any(s in t for s in subs) for _n, t, _k in M if t)
    if "parts" in how:
        rx = re.compile(how["parts"])
        return any(rx.search(n) for n in names | ctx.get("all_parts", set()))
    if "frame_near" in how:
        spec = how["frame_near"]
        f = frames.get(spec["frame"])
        if f is None or not len(X):
            return None
        p = np.asarray(f["pos"], dtype=np.float64)
        from .form import _closest

        d, _ = _closest(np, np.repeat(p[None, :], len(X), axis=0), X[:, 0], X[:, 1], X[:, 2])
        return bool(d.min() <= float(spec.get("radius", 0.25)))
    if "region" in how:
        spec = how["region"]
        reg = ctx.setdefault("regions", _regions(np, frames, X)).get(spec["name"])
        if reg is None:
            return None
        lo, hi = reg
        skip = re.compile(spec["skip"]) if spec.get("skip") else None
        c = X.mean(axis=1)
        inside = ((c >= lo) & (c <= hi)).all(axis=1)
        if skip is not None:
            inside &= np.array([not skip.search(n) for n, _t, _k in M], dtype=bool)
        return int(inside.sum()) >= int(spec.get("min_tris", 1))
    if "outboard" in how:
        body = next((p for p in parts if p["name"].lower() == "chassis"), None)
        if body is None or not len(X):
            return None
        half = float(np.abs(body["pos"][:, 0]).max())
        wz = [f["pos"][2] for n, f in frames.items() if n.startswith("wheel_") and n.endswith("_dummy")]
        z0 = (max(wz) + 0.3) if wz else -1e9
        V = X.reshape(-1, 3)
        return bool(((np.abs(V[:, 0]) >= half + float(how["outboard"])) & (V[:, 2] >= z0)).any())
    if "small_pieces" in how:
        spec = how["small_pieces"]
        rx = re.compile(spec["parts"])
        for p in parts:
            if rx.search(p["name"].lower()):
                pcs = _pieces(np, p)
                if len(pcs) > 1 and any(pc["tris"] <= int(spec.get("max_tris", 24)) and not pc["glass"]
                                        for pc in pcs):
                    return True
        return False
    if "above_roof" in how:
        body = next((p for p in parts if p["name"].lower() == "chassis"), None)
        if body is None:
            return None
        top = float(body["pos"][:, 2].max())
        pcs = sorted(_pieces(np, body), key=lambda pc: -pc["tris"])
        return any(pc["lo"][2] >= top - 0.2 and (pc["hi"][1] - pc["lo"][1]) >= float(how["above_roof"])
                   for pc in pcs[1:])
    if "lined_arches" in how:
        st = ctx.get("see_through")
        if st is None:
            return None
        return not st and ctx.get("arches", 0) > 0
    if "seat" in how:
        from .fit import rider_points

        return "seat" in rider_points(np, parts, frames) if "ped_frontseat" in frames else None
    if "flags" in how:
        f = how["flags"]
        return bool(ctx.get("flags", {}).get(f))
    if "glass" in how:
        lamp = {"255,175,0", "0,255,200", "185,255,0", "255,60,0"}
        for p in parts:
            if p.get("role") == "wheel":
                continue
            al, ks = p.get("alpha") or [], p.get("keys") or []
            used = set(np.unique(np.asarray(p["mat"])).tolist()) if p.get("mat") is not None else {0}
            for m in used:
                if 0 <= m < len(al) and al[m] < 255 and not (0 <= m < len(ks) and ks[m] in lamp):
                    return True
        return any(re.search(r"^windscreen|glass|window", p["name"].lower()) for p in parts)
    if "mirrors" in how:
        return _mirrors(np, parts, frames, X, float(how["mirrors"]))
    if "frames" in how:
        rx = re.compile(how["frames"])
        return any(rx.search(n) for n in ctx.get("frames_all", set(frames)))
    if "frame_geom" in how:
        spec = how["frame_geom"]
        rx = re.compile(spec["parts"] if isinstance(spec, dict) else spec)
        mt = int(spec.get("min_tris", 1)) if isinstance(spec, dict) else 1
        return any(rx.search(p["name"].lower()) and len(p["tris"]) >= mt for p in parts)
    if "near_frames" in how:
        spec = how["near_frames"]
        if not len(X):
            return None
        from .form import _closest

        r = float(spec.get("radius", 0.15))
        for fn in spec["frames"]:
            f = frames.get(fn)
            if f is None:
                return None
            q = np.asarray(f["pos"], dtype=np.float64)
            d, _ = _closest(np, np.repeat(q[None, :], len(X), axis=0), X[:, 0], X[:, 1], X[:, 2])
            if d.min() > r:
                return False
        return True
    if "lod_model" in how:
        v = ctx.get("lod")
        return None if v is None else bool(v)
    return None


def surface_points(np, X, step: float):
    """Points on triangles ``X`` (T, 3, 3) about ``step`` m apart (a barycentric lattice per triangle)."""
    if not len(X):
        return np.zeros((0, 3))
    edge = np.max(np.linalg.norm(X - np.roll(X, 1, axis=1), axis=2), axis=1)
    ks = np.clip(np.ceil(edge / step), 1, 24).astype(np.int64)
    out = []
    for k in np.unique(ks).tolist():
        ij = np.array([(i, j) for i in range(k + 1) for j in range(k + 1 - i)], dtype=np.float64) / k
        w = np.stack([1.0 - ij[:, 0] - ij[:, 1], ij[:, 0], ij[:, 1]], axis=1)          # (n, 3)
        T = X[ks == k]
        out.append(np.einsum("nk,tkd->tnd", w, T).reshape(-1, 3))
    return np.concatenate(out)


def _mirrors(np, parts, frames, X, out_m: float) -> bool | None:
    """Door mirrors: a part named after them, or a local bump on the body side above the wheel tops: in a 5 cm
    height slice, the side reaches ``out_m`` farther out than it does 0.3-0.8 m ahead and behind (the glasshouse
    and the bonnet are narrower), so mirrors built into the body or the doors count as well as separate ones."""
    if any(re.search(r"mirror", p["name"].lower()) for p in parts):
        return True
    if not len(X):
        return None
    wf = [f["pos"] for n, f in frames.items() if n.startswith("wheel_") and n.endswith("_dummy")]
    if not wf:
        return None
    z0 = max(w[2] for w in wf) + 0.3
    ymid = (max(w[1] for w in wf) + min(w[1] for w in wf)) / 2      # mirrors sit in the front half
    keep = (X[:, :, 2].max(axis=1) >= z0) & (X[:, :, 1].max(axis=1) >= ymid)
    V = surface_points(np, X[keep], 0.04)
    V = V[(V[:, 2] >= z0) & (V[:, 1] >= ymid)]
    if not len(V):
        return False
    zi = np.floor((V[:, 2] - z0) / 0.05).astype(np.int64)
    yi = np.floor(V[:, 1] / 0.1).astype(np.int64)
    y0 = int(yi.min())
    grid = np.full((int(zi.max()) + 1, int(yi.max()) - y0 + 1), -np.inf)
    np.maximum.at(grid, (zi, yi - y0), np.abs(V[:, 0]))
    ny = grid.shape[1]
    for row in grid:
        for y in np.flatnonzero(np.isfinite(row)).tolist():
            near = [row[k] for k in (*range(y - 8, y - 2), *range(y + 3, y + 9)) if 0 <= k < ny and np.isfinite(row[k])]
            if len(near) >= 2 and row[y] >= max(near) + out_m:
                return True
    return False


def coverage_rows(peer_key: str, parts: list[dict], frames: dict, ctx: dict | None = None) -> list[dict]:
    """``[{check: cov.<id>, part, value: present|missing|unknown, verdict, hint, ref}]`` for the class checklist."""
    import numpy as np

    cl = checklist(peer_key)
    if not cl:
        return []
    ctx = dict(ctx or {})
    X, M = _tris(np, parts)
    rows = []
    for it in cl["items"]:
        got = _detect(np, it["how"], parts, frames, X, M, ctx)
        opt = bool(it.get("optional"))
        if got is None:
            continue
        verdict = "present" if got else "missing"
        hint = it["what"] if got else (it["what"] + "; " + it["fix"] + (" (optional)" if opt else ""))
        rows.append({"check": f"cov.{it['id']}", "part": "optional" if opt else "", "value": verdict,
                     "verdict": verdict, "hint": hint, "ref": cl.get("ref", "style_vehicle")})
    return rows
