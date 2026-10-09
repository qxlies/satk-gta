"""``style.texture``: does a texture look like a vanilla SA texture of its role?

* :func:`texture_stats` - the ``tex.*`` metrics of one RGBA image (:mod:`satk.style.registry`);
* :func:`role_of` - the role from the texture name (``data/style/roles.json``);
* :func:`vanilla` - per-role distributions of the vanilla textures (a deterministic sample of the own
  textures of each role, decoded once and cached next to the style cache);
* :func:`judge_texture` - one texture against its role: verdict rows with the fence of :mod:`satk.style.profile`;
* :func:`look_advice` - the look against the role: ``flat/CG-clean`` (no fine tonal variation, dead-flat patches)
  and ``too sharp`` (crisp marks on a flat ground), advice only.

Photo-like vanilla textures have hundreds of colours, low saturation and real high-frequency detail;
vector art (a few flat colours) and bright interiors behind tinted glass fall out of band. Within a region a photo
varies softly everywhere (light, tone, a little hue); a clean CG fill is dead flat between crisp painted marks.
"""

from __future__ import annotations

import functools
import json
import math
import os
import re
from pathlib import Path

from ..core import paths, resources
from ..core.errors import SatkError
from . import classes as C
from .profile import fence, judge

__all__ = ["texture_stats", "role_of", "role_for", "roles", "vanilla", "judge_texture", "judge_metric",
           "verdict_of", "load_images", "loo", "look_stats", "look_advice", "LOOK_METRICS"]

#: The look metrics (:func:`look_stats`): reported by ``style.texture --full`` and judged by :func:`look_advice`.
LOOK_METRICS = ("tex.tone_lo", "tex.tone_mid", "tex.chroma_lo", "tex.flat_share", "tex.crisp")

_lock_cache: dict[str, dict] = {}


@functools.lru_cache(maxsize=1)
def roles() -> dict:
    return resources.read_json("style", "roles.json")


def role_of(name: str) -> str:
    n = (name or "").lower()
    for role, r in roles()["roles"].items():
        if r["match"] and re.search(r["match"], n):
            return role
    return "generic"


#: Roles a family's textures may take from their names, and the class default when the name says nothing.
_FAMILY_ROLES = {"vehicle": ("interior", "wheel", "decal", "body"), "map": ("wall", "ground", "prop"),
                 "upgrade": ("wheel", "decal", "body"), "ped": ("ped",), "weapon": ("weapon",)}
_CLASS_ROLE = {"building": "wall", "interior_shell": "wall", "terrain": "ground", "seabed": "ground",
               "vegetation": "generic", "lod": "generic", "overlay": "generic"}


def role_for(name: str, cls: str | None) -> str:
    """The role of a texture of a model of class ``cls``: the name decides within the roles of the model's family
    (``ls_bin_paint`` of a prop is a prop texture, not car paint), else the class default (vehicles: body;
    buildings: wall; terrain: ground; other map models: prop; peds; weapons)."""
    r = role_of(name)
    if not cls:
        return r
    try:
        base = C.split_peer(cls)[0]
        fam = C.family(base)
    except (KeyError, SatkError):
        return r
    allowed = _FAMILY_ROLES.get(fam)
    if allowed is None:
        return r
    if r in allowed:
        return r
    if fam == "vehicle":
        return "body"
    if fam == "map":
        return _CLASS_ROLE.get(base, "prop")
    return allowed[0]


def texture_stats(rgba: bytes, w: int, h: int) -> dict:
    """``tex.*`` metrics of an RGBA8 image (pixels with alpha 0 are ignored, the Laplacian uses all)."""
    import numpy as np

    a = np.frombuffer(bytes(rgba), dtype=np.uint8).reshape(h, w, 4).astype(np.float64)
    rgb = a[:, :, :3]
    luma = 0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]
    vis = a[:, :, 3] > 0
    if not vis.any():
        vis = np.ones((h, w), dtype=bool)
    px = rgb[vis].astype(np.int64)
    out = {"w": int(w), "h": int(h)}
    out["tex.colours"] = int(len(np.unique(px[:, 0] * 65536 + px[:, 1] * 256 + px[:, 2])))
    q = px >> 3
    out["tex.colours15"] = int(len(np.unique(q[:, 0] * 1024 + q[:, 1] * 32 + q[:, 2])))
    lv = luma[vis]
    out["tex.lum_mean"] = round(float(lv.mean()), 2)
    out["tex.lum_std"] = round(float(lv.std()), 2)
    mx = px.max(axis=1).astype(np.float64)
    mn = px.min(axis=1).astype(np.float64)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1), 0.0)
    out["tex.sat_mean"] = round(float(sat.mean()), 4)
    out["tex.val_mean"] = round(float((mx / 255.0).mean()), 4)
    if h >= 3 and w >= 3:
        lap = (4 * luma[1:-1, 1:-1] - luma[:-2, 1:-1] - luma[2:, 1:-1] - luma[1:-1, :-2] - luma[1:-1, 2:])
        out["tex.hf_energy"] = round(float(np.abs(lap).mean()), 3)
    if h >= 8 and w >= 8:
        out.update(look_stats(np, rgb, luma, vis))
    return out


# ----------------------------------------------------------------------------- the look (photo-like or CG-clean)
#: Scales of the look metrics, as fractions of the shorter side: fine = 1/64 (about 2 px at 128), coarse = 1/8.
LOOK_FINE, LOOK_COARSE = 1.0 / 64.0, 1.0 / 8.0
#: A pixel is in a dead-flat patch when its 5x5 window spans at most this many luma levels.
FLAT_LEVELS = 2.0
#: Strong edges (the marks of the "crisp" ratio) span at least this many luma levels in their 5x5 window ...
EDGE_LEVELS = 24.0
#: ... and the ratio needs at least this many edge pixels.
EDGE_MIN = 20


def _box(np, a, r: int, axis: int):
    """Box filter of radius ``r`` along ``axis`` (wrap-around, textures tile)."""
    if r <= 0:
        return a
    n = a.shape[axis]
    pad = [(0, 0)] * a.ndim
    pad[axis] = (r + 1, r)
    c = np.cumsum(np.pad(a, pad, mode="wrap"), axis=axis)
    hi = np.take(c, np.arange(2 * r + 1, 2 * r + 1 + n), axis=axis)
    lo = np.take(c, np.arange(0, n), axis=axis)
    return (hi - lo) / (2 * r + 1)


def gauss(np, a, sigma: float):
    """Approximate Gaussian blur of a 2-D array (three box passes per axis, wrap-around)."""
    if sigma <= 0:
        return a
    r = max(1, int(round((math.sqrt(4.0 * sigma * sigma + 1.0) - 1.0) / 2.0)))
    for axis in (0, 1):
        for _ in range(3):
            a = _box(np, a, r, axis)
    return a


def _win5(np, a, fn):
    """5x5 max or min filter (wrap-around): separable shifts."""
    out = a
    for axis in (0, 1):
        s = out
        for k in (-2, -1, 1, 2):
            s = fn(s, np.roll(out, k, axis=axis))
        out = s
    return out


def look_stats(np, rgb, luma, vis) -> dict:
    """The look metrics of an image (``tex.tone_lo``, ``tex.tone_mid``, ``tex.chroma_lo``, ``tex.flat_share``,
    ``tex.crisp``): photo-like textures vary softly everywhere; CG-clean ones are dead flat between crisp marks.

    * ``tex.tone_lo``   - median |luma blurred at 1/64 - luma blurred at 1/8 of the side|: soft tonal drift and
      light gradients inside regions (a flat fill is 0 away from its edges);
    * ``tex.tone_mid``  - median |luma - luma blurred at 1/64|: fine tonal variation (the grain and texture of a
      photo); the median ignores the few pixels on painted marks;
    * ``tex.chroma_lo`` - median of the largest channel of the same band as ``tone_lo`` on the colour minus luma:
      slight hue and saturation drift;
    * ``tex.flat_share`` - share of pixels whose 5x5 window spans at most 2 luma levels (dead-flat patches);
    * ``tex.crisp``     - median 5x5 contrast at strong edges / max(``tone_mid``, 0.5): crisp marks on a flat
      ground score high (omitted with fewer than 20 edge pixels)."""
    side = min(luma.shape)
    s1, s2 = max(1.0, side * LOOK_FINE), max(2.0, side * LOOK_COARSE)
    l1 = gauss(np, luma, s1)
    out = {"tex.tone_lo": round(float(np.median(np.abs(l1 - gauss(np, luma, s2))[vis])), 3),
           "tex.tone_mid": round(float(np.median(np.abs(luma - l1)[vis])), 3)}
    ch = rgb - luma[..., None]
    band = np.stack([gauss(np, ch[..., k], s1) - gauss(np, ch[..., k], s2) for k in range(3)], axis=-1)
    out["tex.chroma_lo"] = round(float(np.median(np.abs(band).max(axis=-1)[vis])), 3)
    rng = _win5(np, luma, np.maximum) - _win5(np, luma, np.minimum)
    out["tex.flat_share"] = round(float((rng[vis] <= FLAT_LEVELS).mean()), 4)
    d = np.maximum(np.abs(np.roll(luma, -1, axis=1) - luma), np.abs(np.roll(luma, -1, axis=0) - luma))
    edge = (d >= _win5(np, d, np.maximum) - 1e-9) & (rng >= EDGE_LEVELS) & vis
    if int(edge.sum()) >= EDGE_MIN:
        out["tex.crisp"] = round(float(np.median(rng[edge])) / max(out["tex.tone_mid"], 0.5), 2)
    return out


def _pct(values: list, q: float) -> float | None:
    import numpy as np

    v = [float(x) for x in values if x is not None]
    return float(np.percentile(np.asarray(v), q)) if v else None


def look_advice(stats: dict, dist: dict, role: str) -> list[dict]:
    """Advice on the look of one texture against the vanilla textures of ``role`` (``data/style/roles.json``
    ``look``): ``flat/CG-clean`` (fine tonal variation below the role's low percentile, or a large share of
    dead-flat patches) and ``too sharp`` (crisp marks far above the role's variation). Each item is ``{"look",
    "why", "fix", "metrics"}``; empty when the texture reads photo-like. Advice only: it never fails a check."""
    rr = dist.get("roles") or {}
    used = role if rr.get(role, {}).get("n", 0) >= int(roles()["min_samples"]) else "generic"
    vals = (rr.get(used) or {}).get("values") or {}
    cfg = roles().get("look") or {}
    out: list[dict] = []
    tm, fs = stats.get("tex.tone_mid"), stats.get("tex.flat_share")
    tm_lo = _pct(vals.get("tex.tone_mid", []), float(cfg.get("tone_mid_pct", 5)))
    fs_hi = _pct(vals.get("tex.flat_share", []), float(cfg.get("flat_share_pct", 95)))
    p50 = _pct(vals.get("tex.tone_mid", []), 50.0)
    why, which = [], []
    if tm is not None and tm_lo is not None and tm < tm_lo:
        why.append(f"fine tonal variation {tm:g} levels (vanilla {used} p{cfg.get('tone_mid_pct', 5)} {tm_lo:.2f}, "
                   f"p50 {p50:.2f})")
        which.append("tex.tone_mid")
    if fs is not None and fs_hi is not None and fs > fs_hi and fs >= float(cfg.get("flat_share_min", 0.2)):
        why.append(f"{fs:.0%} of the pixels in dead-flat patches (vanilla {used} p{cfg.get('flat_share_pct', 95)} "
                   f"{fs_hi:.0%})")
        which.append("tex.flat_share")
    if why:
        out.append({"look": "flat/CG-clean", "why": "; ".join(why), "metrics": which,
                    "fix": "add the quiet variation of a photo, not dirt: soft low-frequency tonal drift, a slight hue "
                           "drift and a soft light gradient (texture.finish --photo 0.6 on the 4x paint), then look at "
                           "it after DXT1 at native size"})
    cr = stats.get("tex.crisp")
    cr_hi = _pct(vals.get("tex.crisp", []), float(cfg.get("crisp_pct", 95)))
    if cr is not None and cr_hi is not None and cr > cr_hi:
        out.append({"look": "too sharp", "metrics": ["tex.crisp"],
                    "why": f"marks {cr:g}x the variation around them (vanilla {used} p{cfg.get('crisp_pct', 95)} "
                           f"{cr_hi:.1f})",
                    "fix": "soften the painted marks (paint at 4x, texture.finish --soft 1) and put tonal variation "
                           "under them, so lines read like a downscaled photo, not vector art"})
    return out


# ----------------------------------------------------------------------------- inputs
def _decode_png(data: bytes) -> tuple[int, int, bytes]:
    from ..media.png import decode_png

    try:
        return decode_png(data)
    except ValueError:
        from io import BytesIO

        from ..core.errors import require_module

        Image = require_module("PIL.Image", "Pillow")
        im = Image.open(BytesIO(data)).convert("RGBA")
        return im.width, im.height, im.tobytes()


def _txd_images(buf: bytes, label: str) -> list[tuple[str, int, int, bytes]]:
    from ..formats.dxt import decode_rgba
    from ..formats.txd import mip0_bytes, palette_bytes, parse_txd

    out = []
    for t in parse_txd(buf).textures:
        if t.unsupported:
            continue
        try:
            out.append((f"{label}/{t.name}", t.w, t.h, decode_rgba(t, mip0_bytes(buf, t), palette_bytes(buf, t))))
        except Exception:  # noqa: BLE001 - an undecodable texture is skipped
            continue
    return out


def load_images(target: str) -> list[tuple[str, int, int, bytes]]:
    """``[(name, w, h, rgba)]`` of a ``.png``, a ``.txd`` or a folder of them (sorted, at most 200)."""
    p = Path(os.path.abspath(target))
    if p.is_dir():
        files = sorted(x for x in p.rglob("*") if x.suffix.lower() in (".png", ".txd") and x.is_file())[:200]
    elif p.is_file():
        files = [p]
    else:
        return _sid_images(target)
    out = []
    for f in files:
        with paths.open_ro(f) as fh:
            data = fh.read()
        if f.suffix.lower() == ".txd":
            out += _txd_images(data, f.stem)
        else:
            w, h, rgba = _decode_png(data)
            out.append((f.stem, w, h, rgba))
    if not out:
        raise SatkError("NOT_FOUND", f"no PNG or TXD textures in {paths.jpath(p)}")
    return out


def _sid_images(ident: str) -> list[tuple[str, int, int, bytes]]:
    from ..index.api import open_index
    from ..media.texture import decode_ref

    s = ident.strip()
    if not s.lower().startswith(("tex:", "txd:")):
        raise SatkError("NOT_FOUND", f"no file {s!r}", hint="give a .png, a .txd, a folder, tex:<txd>/<name> or txd:<name>")
    db = open_index("vanilla")
    refs = [db.texture_ref(s)] if s.lower().startswith("tex:") else db.textures_of(s)
    out = []
    for r in refs[:200]:
        w, h, rgba = decode_ref(r)
        out.append((str(r.sid).split("/", 1)[-1], w, h, rgba))
    return out


# ----------------------------------------------------------------------------- vanilla distributions
def _tex_path(profile: str) -> Path:
    from .cache import cache_paths

    import hashlib

    prof, _m = cache_paths(profile)
    sig = hashlib.sha1(resources.read_bytes("style", "roles.json")).hexdigest()[:8]   # a new role map = new sample
    return prof.with_name(prof.name.replace(".json", f".tex-{sig}.json"))


def _sample(profile: str) -> dict[str, list]:
    """Role -> sorted ``[(pix, sid)]`` of the vanilla own textures (deterministic)."""
    import sqlite3

    from ..index.api import open_index
    from .cache import load

    db = open_index(profile)
    sc = load(profile, db=db)
    conn = sqlite3.connect("file:" + Path(db.path).as_posix() + "?mode=ro", uri=True)
    try:
        rows = conn.execute("""
            SELECT DISTINCT lower(t.name), lower(x.name), lower(hex(x.hash)), mt.model_id
            FROM model_tex mt JOIN texture x ON x.id = mt.texture_id JOIN txd t ON t.id = x.txd_id
            WHERE mt.via = 'own' ORDER BY 3""").fetchall()
    finally:
        conn.close()
    rr = roles()["roles"]
    by: dict[str, dict[str, str]] = {r: {} for r in rr}
    for txd, name, pix, mid in rows:
        m = sc.model(mid)
        if m is None:
            continue
        cls = m[1]
        fam = C.family(cls)
        sid = f"tex:{txd}/{name}"
        for role, r in rr.items():
            v = r["vanilla"]
            if "family" in v and v["family"] != fam:
                continue
            if "families" in v and fam not in v["families"]:
                continue
            if "classes" in v and cls not in v["classes"]:
                continue
            if "class_prefix" in v and not cls.startswith(v["class_prefix"]):
                continue
            if "name" in v and not re.search(v["name"], name):
                continue
            by[role].setdefault(pix, sid)
    n = int(roles()["per_role"])
    out = {}
    for role, d in by.items():
        items = sorted(d.items())
        step = max(1, len(items) // n)
        out[role] = items[::step][:n]
    return out


def vanilla(profile: str = "vanilla") -> dict:
    """``{"roles": {role: {"n", "metrics": {metric: [p10, p50, p90, n]}}}}`` (built and cached on first use)."""
    path = _tex_path(profile)
    key = str(path)
    if key in _lock_cache:
        return _lock_cache[key]
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        from ..index.api import open_index
        from ..media.texture import Reader, decode_ref
        from .cache import percentiles

        db = open_index(profile)
        rd = Reader()
        data = {"format": "satk.style-textures/1", "roles": {}}
        try:
            for role, items in _sample(profile).items():
                vals: dict[str, list] = {}
                for _pix, sid in items:
                    try:
                        w, h, rgba = decode_ref(db.texture_ref(sid), rd)
                    except Exception:  # noqa: BLE001 - unsupported/undecodable vanilla texture
                        continue
                    for k, v in texture_stats(rgba, w, h).items():
                        if k.startswith("tex."):
                            vals.setdefault(k, []).append(v)
                n = len(next(iter(vals.values()), []))
                data["roles"][role] = {"n": n, "metrics": {k: percentiles(v) for k, v in sorted(vals.items())
                                                           if len(v) >= 3},
                                       "values": {k: v for k, v in sorted(vals.items())}}
        finally:
            rd.close()
        paths.atomic_write(path, json.dumps(data, separators=(",", ":")))
    _lock_cache[key] = data
    return data


def _tx(metric: str, x: float) -> float:
    import math

    return math.log1p(max(float(x), 0.0)) if metric in roles().get("log_metrics", ()) else float(x)


def judge_metric(metric: str, value: float, stats: list) -> str:
    """``ok``/``edge``/``low``/``high`` of one texture metric (counts compared in log space)."""
    t = [_tx(metric, x) for x in stats[:3]] + list(stats[3:])
    return judge(_tx(metric, value), t, {"lo": t[0], "hi": t[2], "status": "measured"})


def verdict_of(rows: list[list]) -> str:
    """``out`` when a metric is beyond its fence or ``max_edges`` metrics are outside p10..p90, else ``in``."""
    if any(r[5] in ("low", "high") for r in rows):
        return "out"
    edges = sum(1 for r in rows if r[5] != "ok")
    return "out" if edges >= int(roles().get("max_edges", 99)) else "in"


def judge_texture(name: str, stats: dict, dist: dict, role: str) -> dict:
    """``{"texture", "role", "used_role", "verdict", "rows": [[metric, value, p10, p50, p90, verdict]], "look":
    [advice], "look_rows": [[metric, value, p10, p50, p90, verdict]]}`` (the verdict comes from the band metrics;
    the look is advice, :func:`look_advice`)."""
    rr = dist["roles"]
    used = role if rr.get(role, {}).get("n", 0) >= int(roles()["min_samples"]) else "generic"
    peer = rr.get(used, {}).get("metrics", {})
    rows = []
    for m in roles()["check"]:
        if m not in stats or m not in peer:
            continue
        s = peer[m]
        rows.append([m, stats[m], s[0], s[1], s[2], judge_metric(m, stats[m], s)])
    look = look_advice(stats, dist, role)
    flagged = {m for a in look for m in a.get("metrics", ())}
    look_rows = []                      # advice, never a band verdict: "advice" on the metrics behind an advice
    for m in LOOK_METRICS:
        if m in stats and m in peer:
            s = peer[m]
            look_rows.append([m, stats[m], s[0], s[1], s[2], "advice" if m in flagged else "ok"])
    return {"texture": name, "role": role, "used_role": used, "size": f"{stats['w']}x{stats['h']}",
            "verdict": verdict_of(rows), "rows": rows, "look": look, "look_rows": look_rows}


def loo(dist: dict) -> dict:
    """Leave-one-out share of vanilla textures judged ``in`` per role."""
    import numpy as np

    from .cache import percentiles

    out = {}
    for role, d in dist["roles"].items():
        vals = d.get("values") or {}
        if not vals:
            continue
        n = len(next(iter(vals.values())))
        ok = 0
        for i in range(n):
            rows = []
            for m in roles()["check"]:
                v = vals.get(m)
                if not v or len(v) != n:
                    continue
                rest = v[:i] + v[i + 1:]
                rows.append([m, v[i], 0, 0, 0, judge_metric(m, v[i], percentiles(rest))])
            ok += verdict_of(rows) == "in"
        out[role] = {"textures": n, "in_share": round(ok / n, 3) if n else None}
    return out


def lo_hi(stats: list) -> tuple[float, float]:
    return fence(stats)
