"""``asset.check``: a model against vanilla - structure, metrics and semantics, one row per finding.

Rows are ``[check, part, value, p10, p50, p90, verdict, hint, ref]``:

* **structure** (``veh.frames``, ``struct.*``, ``geom.*``, ``col.*``, ``txd.*``): frames and parents
  against the vehicle type (and the ``--like`` model), parts, geometry flags, collision, texture formats;
* **metrics** (``veh.hd_tris``, ``shade.normal_bend``, ``dims.L``, ...): the value against the tier band
  of the peer set; verdict ``ok``/``edge``/``low``/``high``. Bands are advisory: an out-of-band row asks for
  a look or a written reason, it never blocks;
* **semantics** (``veh.paint_dirt``, ``veh.lamp_tex``, ``veh.ped_arm``, ...): engine conventions from
  ``data/style/semantics.json``; verdict ``error`` (blocks) / ``warn`` / ``info``.

``ref`` names the help topic with the rule (``satk help style_vehicle``). ``col.check`` and ``fx2d.check``
(when those operations exist) add one summary row each.
"""

from __future__ import annotations

import functools
import re

from ..core import resources
from ..core.errors import SatkError
from . import cache as K
from . import classes as C
from .measure import frame_names, is_vehicle_scene, measure
from .profile import _check_tier, judge, metric_list, resolve_target, tier_band
from .subject import Subject, col_summary

__all__ = ["COLS", "check_subject", "pick_class", "semantics", "BLOCKING"]

COLS = ["check", "part", "value", "p10", "p50", "p90", "verdict", "hint", "ref"]
#: Verdicts that make ``asset.check`` fail.
BLOCKING = ("error",)
_SIDE_L = re.compile(r"_l(f|b|r|m)?(_|$)")
_SIDE_R = re.compile(r"_r(f|b|r|m)?(_|$)")


@functools.lru_cache(maxsize=1)
def semantics() -> dict:
    return resources.read_json("style", "semantics.json")


def _rule(rid: str) -> dict:
    return semantics()["rules"][rid]


def _row(check: str, part: str = "", value=None, p10=None, p50=None, p90=None, verdict: str = "info",
         hint: str = "", ref: str = "") -> list:
    return [check, part, value, p10, p50, p90, verdict, hint, ref]


def _sem(rid: str, part: str, value, msg: str = "") -> list:
    r = _rule(rid)
    hint = r["hint"] if not msg else f"{msg}; {r['hint']}"
    return _row(rid, part, value, verdict=r["sev"], hint=hint, ref=r["ref"])


def _ref_of(metric: str, fam: str) -> str:
    if metric.startswith("shade.") or metric == "dff.verts_per_tri":
        return "style_shading"
    if metric.startswith("tex."):
        return "style_texture"
    return {"vehicle": "style_vehicle", "map": "style_world", "ped": "style_ped_weapon",
            "weapon": "style_ped_weapon"}.get(fam, "style")


# ----------------------------------------------------------------------------- class
def _guess_vehicle_type(names: set[str], cache: K.StyleCache) -> tuple[str | None, float]:
    best, score = None, 0.0
    for vt in C.VEHICLE_TYPES:
        rows = cache.data.get("frames", {}).get(vt)
        if not rows:
            continue
        req = {r[0] for r in rows if r[1] >= 0.9}
        if not req:
            continue
        s = len(req & names) / len(req | (names & {r[0] for r in rows}))
        if s > score:
            best, score = vt, s
    return best, score


def pick_class(subj: Subject, cache: K.StyleCache, cls: str | None = None, like: str | None = None) -> dict:
    """``{"class", "peer_set", "how", "like"?}``: explicit class, ``--like``, the index, or a guess."""
    if like:
        tg = resolve_target(like, cache)
        tg["how"] = f"like {tg.get('like', {}).get('sid', like)}"
        return tg
    if cls and cls != "auto":
        tg = resolve_target(C.resolve(cls), cache)
        tg["how"] = "--class"
        return tg
    if subj.model_id is not None and cache.model(subj.model_id):
        tg = resolve_target(f"model:{subj.model_id}", cache)
        tg["how"] = "index class of the model"
        tg.pop("like", None)
        return tg
    mid = cache.model_id(subj.name)
    if subj.origin == "file" and mid is not None:
        tg = resolve_target(f"model:{mid}", cache)
        tg["how"] = f"file name = vanilla model {subj.name} (replacement): like model:{mid}"
        return tg
    if subj.manifest.get("like"):
        tg = resolve_target(subj.manifest["like"], cache)
        tg["how"] = "asset.json like"
        return tg
    sc = subj.scene
    names = set(frame_names(sc))
    m = None
    if subj.sec:
        f = {"sec": subj.sec, "type": subj.ide.get("type"), "name": subj.name, "flags": subj.ide.get("flags"),
             "draw": subj.ide.get("draw")}
        if subj.sec not in ("cars", "peds", "weap"):
            m = measure(sc)
            if "dims.L" in m:
                f["dims"] = (m["dims.W"], m["dims.L"], m["dims.H"])
        c, b = C.classify(f)
        tg = resolve_target(C.peer_key(c, b), cache)
        tg["how"] = f"IDE section {subj.sec}"
        return tg
    if is_vehicle_scene(sc):
        vt, score = _guess_vehicle_type(names, cache)
        tg = resolve_target(vt or "car", cache)
        tg["how"] = f"frames match vehicle type {vt or 'car'} ({score:.0%})"
        return tg
    if "pelvis" in names:
        tg = resolve_target("ped", cache)
        tg["how"] = "skinned (pelvis frame): ped"
        return tg
    if "gunflash" in names:
        tg = resolve_target("weapon", cache)
        tg["how"] = "gunflash frame: weapon"
        return tg
    m = m or measure(sc)
    size = m.get("dims.size")
    c = "prop" if size is None or size < 6 else "building"
    tg = resolve_target(C.peer_key(c, C.size_bucket(size)), cache)
    tg["how"] = f"no frames: map {c} by size ({size} m); pass --class or --like to be exact"
    return tg


# ----------------------------------------------------------------------------- metric rows
def metric_rows(m: dict, cache: K.StyleCache, peer_key: str, tier: str, *, all_rows: bool = False) -> list[list]:
    peer = cache.peers[peer_key]["metrics"]
    chain = C.fallback_chain(peer_key)
    parent = cache.peers.get(chain[1], {}).get("metrics", {}) if len(chain) > 1 else {}
    fam = C.family(peer_key)
    rows = []
    for metric in metric_list(peer_key, "check"):
        if metric not in m or metric not in peer:
            continue
        v = m[metric]
        s = peer[metric]
        tb = tier_band(metric, s, peer_key, tier)
        verdict = judge(float(v), s, tb, parent.get(metric))
        if verdict == "ok" and not all_rows:
            continue
        # every message names the band it was judged by: the tier, its limits and where they come from
        band = f"the {tier} band {_n(tb['lo'])}..{_n(tb['hi'])}"
        tail = " (proposal)" if tb["status"] == "proposal" else " (vanilla)"
        if verdict == "ok":
            hint = f"inside {band}{tail}"
        else:
            word = "below" if float(v) < tb["lo"] else "above"
            hint = f"{word} {band}" + (", inside the fence" if verdict == "edge" else "") + tail
        part = metric[metric.index("[") + 1:-1] if "[" in metric else ""
        rows.append(_row(metric, part, v, s[0], s[1], s[2], verdict, hint, _ref_of(metric, fam)))
    return rows


def _n(x):
    if isinstance(x, float):
        return int(x) if x == int(x) and abs(x) >= 10 else round(x, 3)
    return x


# ----------------------------------------------------------------------------- structure + semantics
def _mat_key(m) -> str:
    return f"{m.rgba >> 24},{(m.rgba >> 16) & 255},{(m.rgba >> 8) & 255}"


def _vtype(peer_key: str) -> str:
    return C.fallback_chain(C.split_peer(peer_key)[0])[-1]


def vehicle_semantics(subj: Subject, m: dict, peer_key: str = "car") -> list[list]:
    sc = subj.scene
    sem = semantics()
    vt = _vtype(peer_key)
    rows: list[list] = []
    hd = [p for p in sc.parts if p.kind == "atomic" and not p.name.lower().endswith(("_dam", "_vlo"))]
    paint_tris = paint_dirt = 0
    paint_no_env = paint_mats = 0
    lamp_bad: dict[str, int] = {}
    env_1uv: set[str] = set()
    seen_geo: set[int] = set()
    shared = set(sem["vehicle_txd_textures"])
    own = {n for n, t in subj.tex.items() if t.get("via", "own") == "own" and n not in shared}
    paint_ok = own | {sem["dirt_texture"]}
    for p in hd:
        if p.geom in seen_geo:
            continue
        seen_geo.add(p.geom)
        mesh = sc.meshes[p.geom]
        mats = sc.materials[p.geom]
        counts = [0] * max(len(mats), 1)
        for mid in mesh.mat_ids:
            if mid < len(counts):
                counts[mid] += 1
        for i, mt in enumerate(mats):
            key = _mat_key(mt)
            tex = (mt.texture or "").lower()
            if key in sem["paint_keys"] and sem["paint_keys"][key] in ("paint1", "paint2"):
                paint_tris += counts[i]
                paint_mats += 1
                if tex in paint_ok:
                    paint_dirt += counts[i]
                if "env" not in mt.effects:
                    paint_no_env += 1
            if key in sem["lamp_keys"] and tex != sem["lamp_texture"] and counts[i]:
                lamp_bad[sem["lamp_keys"][key]] = lamp_bad.get(sem["lamp_keys"][key], 0) + counts[i]
        if len(mesh.uv) < 2 and any("env" in mt.effects and counts[i] and _mat_key(mt) in sem["paint_keys"]
                                    for i, mt in enumerate(mats)):
            env_1uv.add(p.name)
    r = _rule("veh.paint_dirt")
    if paint_tris and vt in r["types"]:
        share = round(paint_dirt / paint_tris, 3)
        if share < r["min_share"]:
            rows.append(_sem("veh.paint_dirt", "paint", share,
                             f"{share:.0%} of {paint_tris} paint triangles on {sem['dirt_texture']} or an own "
                             "body texture"))
    for what, n in sorted(lamp_bad.items()):
        rows.append(_sem("veh.lamp_tex", what, n, f"{n} triangles of the {what} key off {sem['lamp_texture']}"))
    for part in sorted(env_1uv) if vt in _rule("veh.env_uv2")["types"] else []:
        rows.append(_sem("veh.env_uv2", part, 1, "env map on a painted geometry with one UV set"))
    if paint_mats and paint_no_env * 2 > paint_mats:
        rows.append(_sem("veh.sheen_missing", "paint", f"{paint_no_env}/{paint_mats}",
                         "paint materials without the env sheen"))
    ws = subj.ide.get("wheel_scale")
    wd = m.get("veh.wheel_mesh_d")
    if ws and wd and vt in _rule("veh.wheel_scale")["types"]:
        r = _rule("veh.wheel_scale")
        ratio = round(wd / float(ws), 3)
        if not r["lo"] <= ratio <= r["hi"]:
            rows.append(_sem("veh.wheel_scale", "wheel", ratio, f"mesh {wd} m vs wheel_scale {ws}"))
    names = frame_names(sc)
    pos = {n: f.model[9:12] for n, f in zip(names, sc.frames) if n}
    ds = _rule("veh.dummy_side")
    if vt in ds["types"]:
        off = ds["min_offset"]
        if "ped_arm" in pos and pos["ped_arm"][0] > off:
            rows.append(_sem("veh.dummy_side", "ped_arm", round(pos["ped_arm"][0], 2), "driver arm at x > 0"))
        for n, (x, _y, _z) in sorted(pos.items()):
            if n == "ped_arm":
                continue
            if _SIDE_L.search(n) and x > off:
                rows.append(_sem("veh.dummy_side", n, round(x, 2), "left frame at x > 0"))
            elif _SIDE_R.search(n) and x < -off:
                rows.append(_sem("veh.dummy_side", n, round(x, 2), "right frame at x < 0"))
    w = m.get("dims.W")
    if w and vt in _rule("veh.bumper_corner")["types"]:
        share = _rule("veh.bumper_corner")["min_x_share"]
        for n in ("bump_front_dummy", "bump_rear_dummy"):
            if n in pos and abs(pos[n][0]) < share * w:
                rows.append(_sem("veh.bumper_corner", n, round(pos[n][0], 2),
                                 f"|x| < {share:.0%} of the width {w} m"))
    return rows


def structure_rows(subj: Subject, cache: K.StyleCache, peer_key: str, like_scene=None,
                   replacing: bool = True) -> list[list]:
    """Structure rows. ``replacing``: the subject stands in for the ``--like`` model (same name); a new model has
    optional state parts (``_dam``, ``_vlo``), and a map model has one atomic whatever the like model has."""
    sc = subj.scene
    fam = C.family(peer_key)
    rows: list[list] = []
    names = frame_names(sc)
    if fam == "vehicle":
        base = C.fallback_chain(C.split_peer(peer_key)[0])[-1]
        table = cache.data.get("frames", {}).get(base, [])
        fr = _rule("veh.frames")
        have = set(names)
        missing = [r for r in table if r[1] >= fr["required_share"] and r[0] not in have]
        for r in missing:
            row = _sem("veh.frames", r[0], 0, f"{r[1]:.0%} of vanilla {base} models have it")
            if r[0] not in fr["critical"]:
                row[6] = "warn"
            rows.append(row)
        roots = {f.idx for f in sc.frames if f.parent < 0}
        parent_of = {n: ("<root>" if f.parent in roots else names[f.parent] if 0 <= f.parent < len(names) else "")
                     for n, f in zip(names, sc.frames)}
        pmin = _rule("veh.frame_parent")["min_parent_share"]
        for r in table:
            n, share, par = r[0], r[1], r[2]
            pshare = r[6] if len(r) > 6 else 0.0
            if share >= 0.9 and pshare >= pmin and n in parent_of and parent_of[n] and parent_of[n] != par:
                rows.append(_sem("veh.frame_parent", n, parent_of[n], f"{pshare:.0%} of vanilla use parent {par}"))
        if subj.col is None:
            rows.append(_sem("col.missing", "", 0))
    if like_scene is not None:
        ln = [n for n in frame_names(like_scene) if n]
        lset, have = set(ln), set(n for n in names if n)
        miss = [n for n in ln if n not in have and n != ln[0]]
        if miss:
            rows.append(_row("struct.frame_missing", ",".join(miss[:8]) + ("..." if len(miss) > 8 else ""),
                             len(miss), verdict="warn", hint="frames the like model has", ref="style_vehicle"))
        extra = [n for n in names if n and n not in lset and n != names[0]]
        if extra:
            rows.append(_row("struct.frame_extra", ",".join(extra[:8]) + ("..." if len(extra) > 8 else ""),
                             len(extra), verdict="info", hint="frames the like model does not have",
                             ref="style_vehicle"))
        common = [n for n in ln if n in have]
        mine = [n for n in names if n in lset]
        if common and mine != common:
            rows.append(_row("struct.frame_order", "", f"{sum(a != b for a, b in zip(common, mine))} moved",
                             verdict="info", hint="frame order differs from the like model", ref="style_vehicle"))
        lparts = {p.name.lower() for p in like_scene.parts if p.kind == "atomic"}
        mparts = {p.name.lower() for p in sc.parts if p.kind == "atomic"}
        pmiss = sorted(lparts - mparts)
        if pmiss and fam != "map":      # props and buildings: parts are not part of the contract
            rows.append(_row("struct.part_missing", ",".join(pmiss[:8]) + ("..." if len(pmiss) > 8 else ""),
                             len(pmiss), verdict="warn" if replacing else "info",
                             hint="atomics the like model has (ok/dam/vlo states)"
                                  + ("" if replacing else "; optional for a new model"), ref="style_vehicle"))
    # geometry flags
    if fam in ("vehicle", "ped", "weapon", "upgrade"):
        no_n = sorted({p.name for p in sc.parts if p.kind == "atomic" and sc.meshes[p.geom].tris
                       and sc.meshes[p.geom].normals is None})
        if no_n:
            rows.append(_sem("geom.normals", ",".join(no_n[:6]), len(no_n)))
    if fam == "map":
        no_p = sorted({p.name for p in sc.parts if sc.meshes[p.geom].tris and sc.meshes[p.geom].prelit is None})
        if no_p:
            rows.append(_sem("geom.prelit", ",".join(no_p[:6]), len(no_p)))
        draw = subj.ide.get("draw")
        if draw is not None and subj.col is not None and C.split_peer(peer_key)[0] != "lod" \
                and float(draw) > _rule("ide.draw_hd_col")["max_draw"]:
            rows.append(_sem("ide.draw_hd_col", "", draw))
    cs = col_summary(subj.col)
    if cs and cs.get("light_dominant") == 0 and fam == "map":
        rows.append(_sem("col.face_light", "", cs.get("light0_share")))
    # texture formats of the own TXD
    if fam in ("vehicle", "ped", "weapon") and subj.tex:
        bad = sorted(n for n, t in subj.tex.items() if t.get("via", "own") == "own"
                     and (str(t.get("fmt", "")).upper() == "DXT5" or int(t.get("levels") or 1) > 1))
        if bad:
            rows.append(_sem("txd.vehicle_format", ",".join(bad[:6]), len(bad), "DXT5 or mip levels"))
    return rows


def _w2_rows(subj: Subject, profile: str) -> list[list]:
    """One summary row per w2 checker (``col.check``, ``fx2d.check``) when the operation exists."""
    from ..core.registry import get_op, invoke

    rows = []
    target = subj.label                     # SID, or the path with forward slashes
    for op, arg, has in (("col.check", "target", subj.col is not None),
                         ("fx2d.check", "src", bool(subj.scene.info.effects))):
        if not has:
            continue
        try:
            get_op(op)
        except SatkError:
            continue
        env = invoke(op, {arg: target, "profile": profile, "limit": 5})
        if not env.get("ok"):
            continue
        worst = env.get("summary") or {}
        n = env.get("total", len(env.get("rows", [])))
        if not n:
            continue
        # col.check counts by severity in "summary", fx2d.check in "errors"; vanilla collisions carry warnings too
        sev = "warn" if worst.get("error") or env.get("errors") else "info"
        rows.append(_row(op, "", n, verdict=sev, hint=f"satk {op.replace('.', ' ')} {target}",
                         ref="style_world" if op == "fx2d.check" else "style_vehicle"))
    return rows


# ----------------------------------------------------------------------------- one subject
def check_subject(subj: Subject, cache: K.StyleCache, *, cls: str | None = None, like: str | None = None,
                  tier: str | None = None, all_rows: bool = False, w2: bool = True, profile: str = "vanilla") -> dict:
    """Check one subject; returns ``{"target", "class", "peer_set", "how", "tier", "rows", "counts", "verdict"}``."""
    tier = _check_tier(tier or subj.manifest.get("tier"))
    tg = pick_class(subj, cache, cls, like or subj.manifest.get("like"))
    key = tg["peer_set"]
    sc = subj.scene
    cs = col_summary(subj.col)
    m = measure(sc, tex_sizes=subj.tex_sizes() or None, wheel_scale=subj.ide.get("wheel_scale"),
                col=({"spheres": cs["spheres"], "boxes": cs["boxes"], "faces": cs["faces"],
                      "shadow_faces": cs["shadow_faces"]} if cs else None))
    like_scene = None
    if tg.get("like") and tg["like"]["sid"] != subj.label:
        from .subject import load_sid

        try:
            like_scene = load_sid(tg["like"]["sid"], profile).scene
        except SatkError:
            like_scene = None
    lk = tg.get("like") or {}
    replacing = not lk or str(lk.get("name", "")).lower() == subj.name.lower() or subj.origin == "index"
    rows = structure_rows(subj, cache, key, like_scene, replacing)
    if C.family(key) == "vehicle":
        rows += vehicle_semantics(subj, m, key)
    rows += metric_rows(m, cache, key, tier, all_rows=all_rows)
    if w2:
        rows += _w2_rows(subj, profile)
    counts: dict[str, int] = {}
    for r in rows:
        counts[r[6]] = counts.get(r[6], 0) + 1
    out_band = counts.get("low", 0) + counts.get("high", 0)
    verdict = "fail" if any(counts.get(b) for b in BLOCKING) else ("review" if out_band or counts.get("warn")
                                                                   else "pass")
    order = {"error": 0, "warn": 1, "low": 2, "high": 2, "edge": 3, "info": 4, "ok": 5}
    rows.sort(key=lambda r: (order.get(r[6], 9), r[0], r[1]))
    return {"target": subj.label, "class": tg["class"], "peer_set": key, "how": tg.get("how", ""),
            "like": tg.get("like", {}).get("sid"), "tier": tier, "rows": rows, "counts": counts,
            "out_of_band": out_band, "verdict": verdict, "metrics": m, "notes": subj.notes + tg.get("fallback", [])}
