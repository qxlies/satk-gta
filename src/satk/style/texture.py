"""``style.texture``: does a texture look like a vanilla SA texture of its role?

* :func:`texture_stats` - the ``tex.*`` metrics of one RGBA image (:mod:`satk.style.registry`);
* :func:`role_of` - the role from the texture name (``data/style/roles.json``);
* :func:`vanilla` - per-role distributions of the vanilla textures (a deterministic sample of the own
  textures of each role, decoded once and cached next to the style cache);
* :func:`judge_texture` - one texture against its role: verdict rows with the fence of :mod:`satk.style.profile`.

Photo-like vanilla textures have hundreds of colours, low saturation and real high-frequency detail;
vector art (a few flat colours) and bright interiors behind tinted glass fall out of band.
"""

from __future__ import annotations

import functools
import json
import os
import re
from pathlib import Path

from ..core import paths, resources
from ..core.errors import SatkError
from . import classes as C
from .profile import fence, judge

__all__ = ["texture_stats", "role_of", "roles", "vanilla", "judge_texture", "judge_metric", "verdict_of",
           "load_images", "loo"]

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
    """``{"texture", "role", "used_role", "verdict", "rows": [[metric, value, p10, p50, p90, verdict]]}``."""
    rr = dist["roles"]
    used = role if rr.get(role, {}).get("n", 0) >= int(roles()["min_samples"]) else "generic"
    peer = rr.get(used, {}).get("metrics", {})
    rows = []
    for m in roles()["check"]:
        if m not in stats or m not in peer:
            continue
        s = peer[m]
        rows.append([m, stats[m], s[0], s[1], s[2], judge_metric(m, stats[m], s)])
    return {"texture": name, "role": role, "used_role": used, "size": f"{stats['w']}x{stats['h']}",
            "verdict": verdict_of(rows), "rows": rows}


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
