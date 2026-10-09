"""``asset.check``: a model against vanilla SA, one row per finding, in eight sections.

Rows are ``[check, part, value, p10, p50, p90, verdict, hint, ref, section]``:

* ``engine`` - the hard rules the engine needs (frames and parents, parts, normals, prelight, collision, TXD
  formats, paint and lamp keys, UV2 for the sheen, wheel = ``wheel_scale``, dummy sides, draw distance):
  verdict ``error`` (blocks) / ``warn`` / ``info`` (``data/style/semantics.json``);
* ``form`` - composition with locations (``satk.style.form``): ``form.floating``, ``form.intersect``,
  ``form.loose_share``, ``form.hard_corners``, ``form.see_through``, ``form.dense_flat`` (fine triangles on flat
  or gently curved surface: a defect for map models, advice elsewhere);
* ``mesh`` - mesh bugs with locations (``satk.style.defects``): ``mesh.crack``, ``mesh.zfight``, ``mesh.flipped``,
  ``mesh.inside_out``, ``mesh.normals``, ``mesh.degenerate``, ``mesh.unused``, ``mesh.stray``, ``mesh.far``,
  ``mesh.uv_smear``, ``mesh.texel``, ``col.outside``, ``col.uncovered``;
* ``fit`` - vehicles: wheels centred in their arches, lamp/exhaust/petrol-cap dummies on their geometry, hinges on
  the part edge; bikes: steering parts on the steering axis, rider contact points as on the like model
  (``satk.style.fit``);
* ``symmetry`` - left/right parts and dummies that do not mirror each other (``sym.*``, information: vanilla
  models are not always symmetric);
* ``coverage`` - the class checklist, the definition of done of the class (``data/style/coverage/<class>.json``,
  generated from the vanilla models of the class): ``present`` / ``missing``;
* ``texture`` - the look of the model's own textures (a DFF file with its TXD) against the vanilla textures of
  their role (``satk.style.texture.look_advice``): ``tex.look`` ``warn`` = advice (``flat/CG-clean``, ``too
  sharp``; it never blocks ``--strict`` and never changes the verdict), ``info`` = photo-like;
* ``reference`` - vanilla numbers of the peer set (triangles, shading, UVs, dims) with p10/p50/p90: verdict
  ``info``, information only. Triangle counts are never a target, a gate or a warning.

Every class has its rules (:func:`class_rules`, ``data/style/form.json`` ``classes``; :func:`mesh_limits`,
``data/style/defects.json``), calibrated on every vanilla model of SA 1.0 US (14,790, all classes): vanilla shows
0 defects. Road cars and motorbikes are strict (every form/fit finding is a defect); the other classes allow the
vanilla construction patterns and call a finding beyond the class's vanilla range a ``defect``, a smaller one a
``warn`` (a real flaw at the place given, vanilla has it too).

Verdict: any ``error`` -> ``fail``; a ``defect`` -> ``review``; else ``pass``. :func:`strict_status` (``asset.check
--strict``, contract K7) is the definition of done: ``done`` and ``blocking`` = engine errors and warnings, every
form/fit/mesh defect and warning, required checklist items missing, inventory items not built
(``design/inventory.json``, ``satk.inventory.api.report``) and light-leak gaps (``checks/<stem>.leak.json`` of
``look.leak``).

``ref`` names the help topic with the rule (``satk help style_vehicle``). ``col.check`` and ``fx2d.check``
(when those operations exist) add one summary row each to ``engine``.
"""

from __future__ import annotations

import functools
import re

from ..core import paths, resources
from ..core.errors import SatkError
from . import cache as K
from . import classes as C
from .measure import frame_names, is_vehicle_scene, measure
from .profile import _check_tier, metric_list, resolve_target
from .subject import Subject, col_summary

__all__ = ["COLS", "SECTIONS", "check_subject", "pick_class", "semantics", "BLOCKING", "REVIEW", "HIDDEN",
           "form_config"]

COLS = ["check", "part", "value", "p10", "p50", "p90", "verdict", "hint", "ref", "section"]
#: Sections of the answer, in output order.
SECTIONS = ("engine", "form", "mesh", "fit", "symmetry", "coverage", "texture", "reference")
#: Verdicts that make ``asset.check`` fail, and those that ask for a review.
BLOCKING = ("error",)
REVIEW = ("defect",)
#: Verdicts hidden unless ``--full`` (information and reference numbers, present checklist items).
HIDDEN = ("ok", "info", "present")
_SIDE_L = re.compile(r"_l(f|b|r|m)?(_|$)")
_SIDE_R = re.compile(r"_r(f|b|r|m)?(_|$)")


@functools.lru_cache(maxsize=1)
def semantics() -> dict:
    return resources.read_json("style", "semantics.json")


def _rule(rid: str) -> dict:
    return semantics()["rules"][rid]


def _row(check: str, part: str = "", value=None, p10=None, p50=None, p90=None, verdict: str = "info",
         hint: str = "", ref: str = "", section: str = "engine") -> list:
    return [check, part, value, p10, p50, p90, verdict, hint, ref, section]


@functools.lru_cache(maxsize=1)
def form_config() -> dict:
    return resources.read_json("style", "form.json")


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
    """The vehicle type whose frames fit best: the share of its usual frames (in 90 % of its vanilla models) the
    model has, minus half the share of the model's frames that type never has (a car is not a trailer because it
    lacks a hookup and carries doors no trailer has)."""
    best, score = None, -1.0
    for vt in C.VEHICLE_TYPES:
        rows = cache.data.get("frames", {}).get(vt)
        if not rows:
            continue
        req = {r[0] for r in rows if r[1] >= 0.9}
        if not req:
            continue
        known = {r[0] for r in rows}
        mine = {n for n in names if n}
        s = len(req & mine) / len(req) - 0.5 * (len(mine - known) / max(len(mine), 1))
        if s > score:
            best, score = vt, s
    return best, max(score, 0.0)


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
    if subj.name.lower().startswith("lod") or subj.name.lower().endswith("_lod"):
        c = "lod"                                          # the LOD of a building next to it (no collision)
    tg = resolve_target(C.peer_key(c, C.size_bucket(size)), cache)
    tg["how"] = f"no frames: map {c} by {'name and ' if c == 'lod' else ''}size ({size} m); pass --class or "                 "--like to be exact"
    return tg


# ----------------------------------------------------------------------------- reference rows
def metric_rows(m: dict, cache: K.StyleCache, peer_key: str, tier: str = "vanilla", *,
                all_rows: bool = True) -> list[list]:
    """Reference rows: the model's numbers next to p10/p50/p90 of its vanilla peer set (verdict ``info``).

    Information only: whether a number sits inside or outside the vanilla range never changes the verdict, and
    triangle counts are never a target. ``tier`` and ``all_rows`` are kept for callers of the old signature."""
    peer = cache.peers[peer_key]["metrics"]
    fam = C.family(peer_key)
    rows = []
    for metric in metric_list(peer_key, "reference"):
        if metric not in m or metric not in peer:
            continue
        v = m[metric]
        s = peer[metric]
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        where = "inside" if s[0] <= fv <= s[2] else ("below" if fv < s[0] else "above")
        hint = f"{where} vanilla p10..p90 {_n(s[0])}..{_n(s[2])} of {peer_key} (reference, not a target)"
        part = metric[metric.index("[") + 1:-1] if "[" in metric else ""
        rows.append(_row(metric, part, v, s[0], s[1], s[2], "info", hint, _ref_of(metric, fam), "reference"))
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
    if fam in ("ped", "weapon"):
        rows += _lint_rows(subj, fam)
    if fam == "map":
        no_p = sorted({p.name for p in sc.parts if sc.meshes[p.geom].tris and sc.meshes[p.geom].prelit is None})
        if no_p:
            rows.append(_sem("geom.prelit", ",".join(no_p[:6]), len(no_p)))
        draw = subj.ide.get("draw")
        # LODs, time objects (night windows) and overlays are drawn far by design
        if draw is not None and subj.col is not None and C.split_peer(peer_key)[0] not in _FAR_CLASSES \
                and not subj.ide.get("tobj") and float(draw) > _rule("ide.draw_hd_col")["max_draw"]:
            rows.append(_sem("ide.draw_hd_col", "", draw))
    cs = col_summary(subj.col)
    if cs and cs.get("light_dominant") == 0 and fam == "map":
        rows.append(_sem("col.face_light", "", cs.get("light0_share")))
    # texture formats of the own TXD
    if fam in ("vehicle", "ped", "weapon") and subj.tex:
        bad = sorted(n for n, t in subj.tex.items() if t.get("via", "own") == "own"
                     and (str(t.get("fmt", "")).upper() == "DXT5" or int(t.get("levels") or 1) > 1))
        if bad:
            row = _sem("txd.vehicle_format", ",".join(bad[:6]), len(bad), "DXT5 or mip levels")
            row[7] = f"DXT5 or mip levels; satk texture pack --class {fam}"
            rows.append(row)
    return rows


#: Map classes drawn far by design (no ``ide.draw_hd_col``): LODs, time objects (night windows) and overlays.
_FAR_CLASSES = frozenset({"lod", "time_object", "overlay"})


def _lint_rows(subj: Subject, fam: str) -> list[list]:
    """Engine rows of the lint semantic rules of a ped (``ped.skin``: Skin plugin, 32 bones, at most 4 weights) or
    a weapon (``weap.flash``): faults that crash or break the game, whatever the model's IDE linking."""
    try:
        from ..formats.dff import read_frames, scan_dff
        from ..lint import semantics as LS
        from ..lint.rules import Collector, Rules

        rules = Rules.load("game")
        c = Collector(rules)
        info = scan_dff(subj.dff)
        label = subj.label
        if fam == "ped":
            LS._ped(c, label, subj.dff, info)            # noqa: SLF001 - the lint rule itself
        else:
            frames = read_frames(subj.dff)
            names = {f.idx: f.name for f in frames}
            LS._weapon(c, label, info, None, None, frames, names)  # noqa: SLF001
    except Exception:  # noqa: BLE001 - an unreadable DFF is reported by the other engine rows
        return []
    out = []
    for f in c.findings:
        v = "error" if f.sev == "error" else "warn" if f.sev == "warn" else "info"
        hint = f.msg
        if f.rule == "ped.skin":
            hint += ("; a ped must keep a vanilla ped's skin (32 bones): re-skin a vanilla ped (texture only), kit "
                     "export cannot write a skinned ped")
        out.append(_row(f.rule, "", 1, verdict=v, hint=hint[:300], ref="style_ped_weapon"))
    return out


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


# ----------------------------------------------------------------------------- form, fit, symmetry, coverage
def _strict(cls: str) -> bool:
    return bool(class_rules(cls).get("strict"))


def class_rules(cls: str) -> dict:
    """Composition rules of a class (``data/style/form.json`` ``classes``), merged from the most general key to
    the most specific: ``default`` <- family <- parent classes <- the class (``car.sedan``: default, vehicle, car,
    car.sedan). ``limits`` merge key by key; every other key is replaced."""
    rules = form_config().get("classes", {})
    base = C.split_peer(cls)[0]
    chain = ["default", C.family(base), *reversed(C.fallback_chain(base))]
    out: dict = {"limits": {}, "chain": []}
    for k in dict.fromkeys(chain):
        r = rules.get(k)
        if not r:
            continue
        out["chain"].append(k)
        for key, val in r.items():
            if key == "limits":
                out["limits"].update(val)
            elif not key.startswith("_"):
                out[key] = val
    if base in set(form_config().get("strict_classes", [])):
        out["strict"] = True
    return out


def _xyz(v) -> str:
    return "(" + ", ".join(f"{float(x):.2f}" for x in v) + ")"


def _floating_verdict(f: dict, rules: dict) -> str:
    """``defect`` beyond the class's vanilla range (every floating group for the strict classes), else ``warn``
    (a near miss vanilla also shows; ``--strict`` still blocks it)."""
    if rules.get("strict"):
        return "defect"
    g = f.get("gap_mm")
    if g is None:
        return "defect" if rules.get("far_defect", True) else "warn"
    lim = rules.get("floating_defect_mm")
    return "defect" if lim is not None and g > float(lim) else "warn"


def form_rows(res: dict, strict, rules: dict | None = None) -> list[list]:
    """``form`` rows of a :func:`satk.style.form.analyse` result. ``strict``: the class rules
    (:func:`class_rules`) or a bool (True = every finding is a defect, the road-car and bike rules)."""
    if rules is None:
        rules = {"strict": bool(strict)}
    st = bool(rules.get("strict"))
    ref = "style_construction"
    rows = []
    for f in res.get("floating", []):
        gap = f"{f['gap_mm']:.0f} mm" if f.get("gap_mm") is not None else "more than 300 mm"
        what = f"{f['pieces']} piece" + ("s" if f["pieces"] > 1 else "") + f", {f['tris']} triangles"
        v = _floating_verdict(f, rules)
        tail = "" if v == "defect" else " (vanilla shows near misses like this one; --strict blocks it)"
        rows.append(_row("form.floating", f["part"], f.get("gap_mm"), verdict=v, ref=ref, section="form",
                         hint=f"{what} at {_xyz(f['at'])} (size {_xyz(f['size'])} m) touch nothing: {gap} from what "
                              f"carries it; attach or weld it to what it sits on (mesh.attach), or delete it" + tail))
    fc = form_config()
    for f in res.get("intersect", []):
        v = "info" if fc.get("intersect_is_info", True) else ("defect" if st else "info")
        rows.append(_row("form.intersect", f["part"], f["depth_mm"], verdict=v, ref=ref, section="form",
                         hint=f"a {f['tris']}-triangle piece at {_xyz(f['at'])} is {f['depth_mm']:.0f} mm deep inside "
                              f"{f['into']} ({f['share']:.0%} of it behind that surface): hidden or jammed geometry; "
                              "shape it from the body or remove it (information: vanilla buries engines and arms "
                              "too)"))
    ls = res.get("loose_share")
    if ls:
        rows.append(_row("form.loose_share", ls["part"], ls["share"], verdict="defect" if st else "info", ref=ref,
                         section="form",
                         hint=f"{ls['share']:.0%} of {ls['part']} ({ls['tris']} triangles, {ls['pieces']} pieces) lies "
                              "outside its largest welded piece (vanilla road cars 16-57 %): build one welded shell "
                              "and cut the panels from it; weld flares, sills and pillars into it"))
    hc = res.get("hard_corners")
    if hc:
        cnt = res.get("counts", {})
        hv = rules.get("hard_corners", "defect" if st else "info")
        for r in hc[:4]:
            at = "; ".join(_xyz(x) for x in r.get("at", [])[:3])
            rows.append(_row("form.hard_corners", r["part"], r["creases"], verdict=hv, ref="style_shading",
                             section="form",
                             hint=f"{r['creases']} of {r['folds']} folds of 60-100 deg are hard without a seam (model: "
                                  f"crease share {cnt.get('crease_share', 0):.0%}, soft {cnt.get('soft_share', 0):.0%}; "
                                  f"vanilla soft at least 10 %), e.g. at {at}: round the corner in 2-3 smooth steps "
                                  "and keep hard edges on material/UV seams (kit.shade)"))
    for stt in res.get("see_through", []):
        rows.append(_row("form.see_through", stt["wheel"], stt["share"], verdict="defect" if st else "warn", ref=ref,
                         section="form",
                         hint=f"looking into the arch of {stt['wheel']} from the side, {stt['share']:.0%} of the view "
                              f"above the tyre goes through the vehicle (near {_xyz(stt['at'])}): close the arch with "
                              "a return face and a liner (mesh.flare)"))
    rows += _dense_rows(res, rules)
    return rows


def _dense_rows(res: dict, rules: dict) -> list[list]:
    """``form.dense_flat`` rows: fine triangles on flat or gently curved surface (a defect for map models, which
    the game places many times; advice for every other family)."""
    found = res.get("dense_flat") or []
    if not found:
        return []
    from .form import limits as form_limits

    lim = form_limits(rules.get("limits") or None)
    vmax = (form_config().get("dense_flat") or {}).get("vanilla_max_share", 0.26)
    cnt = res.get("counts", {})
    v = rules.get("dense_flat", "warn")
    tail = (" (map models are placed many times: vanilla map models placed 10 or more times waste at most a few "
            "dozen triangles)" if v == "defect" else " (advice: it does not block --strict)")
    rows = []
    for d in found:
        regs = "; ".join(f"{r['tris']} triangles on {r['area_m2']:g} m2 at {_xyz(r['at'])} (size {_xyz(r['size'])} m, "
                         f"edges about {r['edge_mm']:.0f} mm, folds about {r['fold_deg']:g} deg)"
                         for r in d.get("regions", [])[:2])
        rows.append(_row("form.dense_flat", d["part"], d["share"], verdict=v, ref="style_construction", section="form",
                         hint=f"{d['wasted']} of {d['tris']} triangles ({d['share']:.0%}) only add density on flat or "
                              f"gently curved surface (folds under {lim['dense_fold_deg']:g} deg, edges under "
                              f"{lim['dense_fine_mm'] / 10:g} cm; model {cnt.get('dense_share', 0):.0%}, vanilla at most "
                              f"{vmax:.0%}): {regs}: move density to where the outline turns (rims, edges, openings, "
                              "the silhouette) and let normals make it soft; long triangles on flat and gently curved "
                              "areas (a 0.5 m barrel: 12-16 segments around, no rings on the straight part)" + tail))
    return rows


def _fit_rows(rows: list[dict], strict, section: str, ref: str, skip=()) -> list[list]:
    out = []
    for r in rows:
        if any(r["check"].startswith(f"fit.{k}") for k in skip):
            continue
        v = r["verdict"]
        if v == "defect" and not strict:
            v = "warn"
        out.append(_row(r["check"], r["part"], r.get("value"), verdict=v, hint=r["hint"], ref=ref, section=section))
    return out


def _flags(subj: Subject) -> dict:
    sc = subj.scene
    prelit = any(m.prelit is not None for m in sc.meshes)
    night = any(getattr(m, "night", None) is not None for m in sc.meshes)
    textured = all(bool(m.texture) for ms in sc.materials for m in ms) if sc.materials else False
    try:                                                # the Skin plugin, not a frame name: frames do not skin
        from ..formats.dff import F_SKIN, scan_dff

        skinned = bool(scan_dff(subj.dff).flags & F_SKIN)
    except Exception:  # noqa: BLE001 - unreadable: not skinned
        skinned = False
    return {"prelit": prelit, "night": night, "collision": subj.col is not None, "textured": textured,
            "skinned": skinned}


# ----------------------------------------------------------------------------- mesh defects (section "mesh")
#: Mesh checks: (check id, result key, value key, unit, how to fix).
MESH_CHECKS = (
    ("mesh.crack", "crack", "len_mm", "mm",
     "an open seam inside one surface shows the background: weld the borders (merge by distance) or bridge the gap"),
    ("mesh.zfight", "zfight", "area_cm2", "cm2",
     "coplanar overlapping faces of different pieces or materials flicker: delete the hidden one, or lift the "
     "detail a few millimetres off the surface (or make it an alpha decal)"),
    ("mesh.flipped", "flipped", "flip_cm2", "cm2",
     "faces wound against their neighbours are culled from outside (a hole): recalculate normals outside"),
    ("mesh.inside_out", "flipped", "inside_out_m3", "m3",
     "a closed piece faces inwards (invisible from outside): flip its normals"),
    ("mesh.normals", "flipped", "normals_share", "share",
     "vertex normals point against the winding (lit from behind): recalculate normals"),
    ("mesh.degenerate", "degenerate", "zero", "tris",
     "zero-area triangles: merge by distance and dissolve degenerate faces"),
    ("mesh.unused", "degenerate", "unused", "verts", "vertices no triangle uses: delete loose vertices"),
    ("mesh.stray", "stray", "pieces", "pieces",
     "tiny islands smaller than a few millimetres: delete them (select linked, by size)"),
    ("mesh.far", "stray", "dist_m", "m", "a piece far away from the rest of the model: delete or move it"),
    ("mesh.uv_smear", "uv_stretch", "share", "share",
     "texture smeared along one direction on a large area: unwrap these faces again"),
    ("mesh.texel", "texel", "share", "share",
     "texel density far off the rest of this material: scale the UV island to match its neighbours"),
    ("col.outside", "col", "over_m", "m", "collision far outside the mesh: regenerate or move it (kit.col)"),
    ("col.uncovered", "col", "share", "share",
     "a large part of the mesh has no collision near it: extend the collision (kit.col, satk col gen)"),
)


def mesh_config() -> dict:
    return resources.read_json("style", "defects.json")


def mesh_limits(cls: str) -> dict:
    """Mesh defect limits of a class: ``data/style/defects.json`` ``limits`` <- ``classes`` along the class chain."""
    from . import defects as D

    base = C.split_peer(cls)[0]
    chain = ["default", C.family(base), *reversed(C.fallback_chain(base))]
    return D.class_limits(list(dict.fromkeys(chain)))


def _mesh_value(check: str, x: dict):
    key = {c[0]: c[2] for c in MESH_CHECKS}[check]
    if check == "mesh.flipped" and "winding" not in x.get("kind", []):
        return None
    if check == "mesh.inside_out" and "inside_out" not in x.get("kind", []):
        return None
    if check == "mesh.normals" and "normals" not in x.get("kind", []):
        return None
    if check == "mesh.degenerate" and "zero" not in x.get("kind", []):
        return None
    if check == "mesh.unused" and "unused" not in x.get("kind", []):
        return None
    if check == "mesh.stray" and x.get("kind") != "tiny":
        return None
    if check == "mesh.far" and x.get("kind") != "far":
        return None
    if check == "col.outside" and x.get("kind") != "outside":
        return None
    if check == "col.uncovered" and x.get("kind") != "uncovered":
        return None
    return x.get(key)


def mesh_rows(res: dict, lim: dict) -> list[list]:
    """``mesh`` rows of a :func:`satk.style.defects.analyse` result: ``defect`` beyond the class's vanilla range
    (``<check>_defect`` in the limits), else ``warn`` (a real flaw at the place given; ``--strict`` blocks it)."""
    rows = []
    for check, key, _vk, unit, fix in MESH_CHECKS:
        for x in res.get(key, []):
            v = _mesh_value(check, x)
            if v is None:
                continue
            ck = check.replace(".", "_")
            dlim = lim.get(ck + "_defect")
            verdict = "defect" if dlim is not None and float(v) > float(dlim) else                 ("info" if lim.get(ck + "_warn") is False else "warn")
            what = _mesh_what(check, x, v, unit)
            at = f" at {_xyz(x['at'])}" if x.get("at") else ""
            rows.append(_row(check, x.get("part", ""), v, verdict=verdict, ref="style_construction", section="mesh",
                             hint=f"{what}{at}: {fix}"))
    return rows


def _mesh_what(check: str, x: dict, v, unit: str) -> str:
    if check == "mesh.crack":
        return f"{x['len_mm']:.0f} mm of open seam up to {x['gap_mm']:.1f} mm wide ({x['pairs']} border pairs)"
    if check == "mesh.zfight":
        return f"{x['area_cm2']:.0f} cm2 of {x['part']} and {x['other']} overlap in one plane ({x['tris']} triangles)"
    if check == "mesh.flipped":
        return f"{x['flip_cm2']:.0f} cm2 of faces wound against their neighbours"
    if check == "mesh.inside_out":
        return f"closed pieces of {x['inside_out_m3']} m3 face inwards"
    if check == "mesh.normals":
        return f"{x['normals_share']:.0%} of the area is lit from behind"
    if check == "mesh.degenerate":
        return f"{x['zero']} zero-area triangles of {x['tris']}"
    if check == "mesh.unused":
        return f"{x['unused']} unused vertices of {x['verts']}"
    if check == "mesh.stray":
        return f"{x['pieces']} tiny islands ({x['tris']} triangles)"
    if check == "mesh.far":
        return f"a {x['tris']}-triangle piece {x['dist_m']} m away from the rest"
    if check in ("mesh.uv_smear", "mesh.texel"):
        return f"{x['share']:.0%} of the {x['texture']} area ({x['area_cm2']:.0f} cm2)"
    if check == "col.outside":
        return f"the collision {x['what']} reaches {x['over_m']} m beyond the mesh"
    if check == "col.uncovered":
        return f"{x['share']:.0%} of the mesh area is farther than {x['gap_m']} m from any collision"
    return f"{v} {unit}"


def _col_of(subj: Subject):
    if not subj.col:
        return None
    try:
        from ..rw.col import decode_model
        from .defects import col_dict

        return col_dict(decode_model(subj.col))
    except Exception:  # noqa: BLE001 - an undecodable collision is reported by the engine rows
        return None


def _lod_of(subj: Subject) -> bool | None:
    """Has the model a LOD? Index: a placement of it with a LOD; files: a ``lod*.dff`` (or ``*_lod.dff``) next to
    it; None when unknown."""
    try:
        if subj.origin == "index" and subj.model_id is not None:
            from ..index.api import open_index

            r = open_index("vanilla").query("SELECT 1 FROM inst WHERE model_id = ? AND lod_id IS NOT NULL",
                                            [subj.model_id], limit=1)
            return bool(r["rows"])
        if subj.path is not None:
            stem = subj.name.lower()
            for f in subj.path.parent.glob("*.dff"):
                n = f.stem.lower()
                if n != stem and (n.startswith("lod") or n.endswith("_lod") or n.endswith("lod")):
                    return True
            return False
    except Exception:  # noqa: BLE001 - unknown, the item is skipped
        return None
    return None


def _lod_scene(subj: Subject):
    """The scene of a map model's LOD: the IPL LOD of a game model, or the LOD DFF next to a file (``None``)."""
    try:
        if subj.origin == "index" and subj.model_id is not None:
            from ..index.api import open_index
            from .subject import load_sid

            r = open_index("vanilla").query("SELECT DISTINCT j.model_id FROM inst i JOIN inst j ON j.id = i.lod_id "
                                            "WHERE i.model_id = ?", [subj.model_id], limit=1)
            if not r["rows"] or int(r["rows"][0][0]) == subj.model_id:
                return None
            return load_sid(f"model:{r['rows'][0][0]}").scene
        if subj.path is not None:
            from ..look.ops import lod_of_file
            from ..model3d.mesh import build_scene

            f = lod_of_file(subj.path)
            if f is None:
                return None
            return build_scene(f.read_bytes(), name=f.stem)
    except Exception:  # noqa: BLE001 - no LOD to compare
        return None
    return None


def lod_rows(subj: Subject, parts: list[dict], cls: str) -> list[list]:
    """``lod.silhouette``: does the LOD keep the HD model's outline (side, front, top)? (map models with a LOD)."""
    base = C.split_peer(cls)[0]
    lim = form_config().get("lod") or {}
    if C.family(base) != "map" or base == "lod" or base in set(lim.get("skip_classes") or ()):
        return []
    sc = _lod_scene(subj)
    if sc is None:
        return []
    from . import lodcheck as LC
    from . import sections as SX

    lp = SX.scene_parts(sc)
    if not lp:
        return [_row("lod.silhouette", sc.name, 0.0, verdict="defect", ref="style_world", section="form",
                     hint=f"the LOD {sc.name} has no geometry: rebuild it (kit.lod)")]
    r = LC.compare(parts, lp)
    cov, spill, worst = r["coverage"], r["spill"], r["worst"]
    wc, ws = float(lim.get("warn_coverage", 0.65)), float(lim.get("warn_spill", 1.0))
    v = "warn" if cov < wc or spill > ws else "info"
    views = ", ".join(f"{k} {c:.0%}/{sp:.0%}" for k, (c, sp) in r["views"].items())
    return [_row("lod.silhouette", sc.name, cov, verdict=v, ref="style_world", section="form",
                 hint=f"the LOD {sc.name} covers {cov:.0%} of the HD outline seen from the {worst} and spills "
                      f"{spill:.0%} (covered/spill per view: {views})"
                      + ("" if v == "info" else ": rebuild it so its outline matches the HD model's (kit.lod keeps "
                                                 "the closed outline; boxes per block for a simple building)"))]


def design_sections(subj: Subject, peer_key: str, cls: str, like_scene=None,
                    like_name: str = "", authored: bool = False) -> tuple[list[list], dict]:
    """Rows of the ``form``, ``fit``, ``symmetry``, ``coverage`` and ``mesh`` sections, and the form counts.
    ``authored``: an authored model (kit export, asset project) gets the ``authored`` form limits (it stands on its
    own: no part rests on a neighbouring model)."""
    from . import coverage as CV
    from . import defects as D
    from . import fit as FT
    from . import form as FM
    from . import sections as SX
    from . import symmetry as SY

    fam = C.family(peer_key)
    sc = subj.scene
    talpha = {n for n, t in subj.tex.items() if t.get("alpha")}
    parts = SX.scene_parts(sc, tex_alpha=talpha)
    if not parts:
        return [], {}
    frames = SX.frame_table(sc)
    rules = class_rules(cls)
    strict = bool(rules.get("strict"))
    vt = _vtype(peer_key) if fam == "vehicle" else ""
    wheels = SX.wheels_of(sc, parts, subj.ide.get("wheel_scale")) if fam == "vehicle" else []
    car = vt in ("car", "mtruck", "quad", "trailer")
    checks = list(rules.get("checks", ["floating", "intersect", "hard_corners"]))
    if fam == "vehicle" and C.split_peer(cls)[0] in set(form_config().get("loose_classes", [])):
        checks.append("loose_share")
    if car and "see_through" not in checks and rules.get("see_through", True):
        checks.append("see_through")
    body = rules.get("body", "chassis" if fam == "vehicle" else None)
    lim_over = dict(rules.get("limits") or {})
    if authored:
        lim_over.update({k: v for k, v in (form_config().get("authored") or {}).items() if not k.startswith("_")})
    res = FM.analyse(parts, body=body, wheels=[w for w in wheels if not w.get("bike")] if car else None,
                     movable=vt not in ("bike", "bmx"), checks=tuple(checks),
                     limits_override=lim_over or None, ground=rules.get("ground", "anchor"))
    rows = form_rows(res, strict, rules)
    if fam == "vehicle":
        lp = lf = None
        if like_scene is not None:
            lp, lf = SX.scene_parts(like_scene), SX.frame_table(like_scene)
        rows += _fit_rows(FT.fit_check(parts, frames, wheels, vtype=vt or "car", like_parts=lp, like_frames=lf,
                                       like_name=like_name or "the like model"), strict, "fit", "style_vehicle",
                          skip=rules.get("fit_skip", ()))
        rows += _fit_rows(SY.symmetry_check(parts, frames), False, "symmetry", "style_construction")
    # mesh defects: cracks, z-fighting, flipped faces, degenerate and stray geometry, UVs, collision vs mesh
    mlim = mesh_limits(cls)
    flags = int(subj.ide.get("flags") or 0)
    mchecks = [c for c in D.CHECKS if c not in set(mlim.get("skip", []))]
    if flags & 0x200000 and "flipped" in mchecks:       # IDE flag: backface culling off (both sides drawn)
        mlim = dict(mlim, flip_cm2=float("inf"))
    mres = D.analyse(parts, checks=tuple(mchecks), tex_sizes=subj.tex_sizes() or None, col=_col_of(subj),
                     lim=mlim, inside_ok=bool(mlim.get("inside_ok")), tex_alpha=talpha)
    rows += mesh_rows(mres, mlim)
    ctx = {"see_through": res.get("see_through", []), "arches": res.get("counts", {}).get("arches", 0),
           "flags": _flags(subj), "all_parts": {p.name.lower() for p in sc.parts}, "tex": subj.tex,
           "frames_all": set(frame_names(sc)), "lod": _lod_of(subj)}
    if fam == "map":
        rows += lod_rows(subj, parts, cls)
    for r in CV.coverage_rows(cls, parts, frames, ctx):
        rows.append(_row(r["check"], r["part"], r["value"], verdict=r["verdict"], hint=r["hint"], ref=r["ref"],
                         section="coverage"))
    counts = dict(res.get("counts", {}))
    counts.update({k: v for k, v in mres.get("counts", {}).items() if v})
    counts["rules"] = ",".join(rules.get("chain", []))
    return rows, counts


# ----------------------------------------------------------------------------- texture look (advice)
def texture_rows(subj: Subject, cls: str, profile: str = "vanilla") -> list[list] | None:
    """``texture`` rows of a DFF file's own textures: ``tex.look`` ``warn`` (advice: flat/CG-clean, too sharp) or
    ``info`` (photo-like) per texture the model uses, against the vanilla textures of its role
    (:func:`satk.style.texture.role_for`). None when there is nothing to judge (a SID, no TXD, no vanilla index)."""
    if subj.origin != "file" or subj.txd_file is None:
        return None
    from . import texture as T

    try:
        with paths.open_ro(subj.txd_file) as fh:
            images = T._txd_images(fh.read(), subj.txd_file.stem)
        dist = T.vanilla(profile)
    except (SatkError, OSError, ValueError, KeyError):
        return None
    used = {(m.texture or "").lower() for mats in subj.scene.materials for m in mats} - {""}
    rows = []
    for label, w, h, rgba in images:
        name = label.split("/", 1)[-1]
        if used and name.lower() not in used:
            continue
        st = T.texture_stats(rgba, w, h)
        j = T.judge_texture(name, st, dist, T.role_for(name, cls))
        nums = ", ".join(f"{m[4:]} {v:g} (vanilla p10..p90 {p10}..{p90})" for m, v, p10, _p50, p90, _vd in
                         j["look_rows"] if m in ("tex.tone_mid", "tex.flat_share", "tex.crisp"))
        for a in j["look"]:
            rows.append(_row("tex.look", name, a["look"], verdict="warn", ref="style_texture", section="texture",
                             hint=f"{a['look']} ({w}x{h}, role {j['used_role']}): {a['why']}: {a['fix']} (advice: it "
                                  "never blocks --strict)"))
        if not j["look"]:
            rows.append(_row("tex.look", name, "photo-like", verdict="info", ref="style_texture", section="texture",
                             hint=f"{w}x{h}, role {j['used_role']}: {nums}"))
    return rows


# ----------------------------------------------------------------------------- strict (definition of done)
#: Verdicts that block ``--strict`` per section (coverage: a required item that is missing); a warning goes through
#: :func:`strict_policy` first (``data/style/strict.json``: above the vanilla range it blocks, else it is advice).
STRICT_BLOCKS = {"engine": ("error", "warn"), "form": ("defect", "warn"), "mesh": ("defect", "warn"),
                 "fit": ("defect", "warn"), "coverage": ("missing",)}


@functools.lru_cache(maxsize=1)
def strict_config() -> dict:
    return resources.read_json("style", "strict.json")


def strict_policy(cls: str) -> dict:
    """``{check: {"over"?, "width_over"?, "self"?, "advice"?}}`` of a class: ``data/style/strict.json`` merged
    default <- family <- parent classes <- the class."""
    wb = strict_config().get("warn_block") or {}
    base = C.split_peer(cls or "")[0]
    chain = ["default"] + ([C.family(base)] if base else []) + (list(reversed(C.fallback_chain(base))) if base else [])
    out: dict = {}
    for k in dict.fromkeys(chain):
        for check, rule in (wb.get(k) or {}).items():
            out[check] = dict(out.get(check) or {}, **rule)
    return out


def _warn_blocks(r: list, pol: dict) -> bool:
    """Does a warning row block ``--strict`` under the class policy (``False``: advice)?"""
    rule = pol.get(r[0])
    if not rule:
        return True
    if rule.get("advice"):
        return False
    try:
        v = float(r[2]) if r[2] is not None else None
    except (TypeError, ValueError):
        v = None
    if v is None:
        return True                     # a group with nothing near it, an unknown size: always
    if rule.get("under") is not None:            # a share where less is worse (lod.silhouette coverage)
        return v < float(rule["under"])
    lim = rule.get("over")
    if r[0] == "mesh.zfight":
        m = re.search(r"cm2 of (\S+) and (\S+) overlap", str(r[7] or ""))
        if m and m.group(1) == m.group(2) and rule.get("self") is not None:
            lim = rule["self"]
    if r[0] == "mesh.crack" and rule.get("width_over") is not None:
        m = re.search(r"up to ([\d.]+) mm wide", str(r[7] or ""))
        if m and float(m.group(1)) > float(rule["width_over"]):
            return True
    return lim is None or v > float(lim)


def _project_of(subj: Subject, project: str | None) -> "Path | None":
    """The asset project of a subject: ``--project`` (it must exist: ``NOT_FOUND``), else the folders above the DFF."""
    if project:
        from ..studio.project import project_dir

        pd = project_dir(project)
        if not (pd / "asset.json").is_file() and not (pd / "design" / "inventory.json").is_file():
            from ..core.paths import jpath

            raise SatkError("NOT_FOUND", f"no asset project {jpath(pd)} (no asset.json, no design/inventory.json)",
                            hint="satk asset status lists nothing there: check the name, or drop --project (the "
                                 "kit export sidecar names the project)")
        return pd
    if subj.path is None:
        return None
    cur = subj.path.parent
    for _ in range(4):
        if (cur / "asset.json").is_file() or (cur / "design" / "inventory.json").is_file():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


def _sidecar_doc(subj: Subject) -> "tuple[Path | None, dict | None]":
    """``(path, document)`` of a kit export's inventory sidecar (``<stem>.inventory.json``); a sidecar that does not
    read gives ``(path, None)``."""
    import json as _json

    if subj.path is None:
        return None, None
    try:
        from ..inventory.api import locate
    except ImportError:
        return None, None
    side = locate(subj.path)
    if side is None:
        return None, None
    try:
        doc = _json.loads(side.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, ValueError):
        return side, None
    return side, doc if isinstance(doc, dict) else None


def _sidecar_project(subj: Subject) -> "Path | None":
    """The project a kit export's inventory sidecar names (``<stem>.inventory.json`` -> ``project``)."""
    from pathlib import Path

    _side, doc = _sidecar_doc(subj)
    proj = (doc or {}).get("project")
    if not proj:
        return None
    p = Path(str(proj))
    return p if (p / "design" / "inventory.json").is_file() or (p / "asset.json").is_file() else None


def _session_project(session: str | None) -> "Path | None":
    if not session:
        return None
    try:
        from ..inventory.sidecar import session_project
    except ImportError:
        return None
    return session_project(session)


def _kit_export(subj: Subject) -> "Path | None":
    """The ``export.json`` of the kit export that wrote this DFF (up to three folders above it), or ``None``."""
    import json as _json

    if subj.path is None:
        return None
    cur = subj.path.parent
    for _ in range(4):
        f = cur / "export.json"
        if f.is_file():
            try:
                doc = _json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                doc = None
            if isinstance(doc, dict) and str(doc.get("stem") or "").lower() == subj.name.lower():
                return f
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


def _leak_files(subj: Subject, proj) -> list:
    """Candidate leak files of a DFF, nearest first: ``checks/<stem>.leak.json``, then the old shared
    ``checks/leak.json`` (it counts only when it names this DFF)."""
    if subj.path is None:
        return []
    from ..look.review import LEAK_DIR, LEAK_FILE, LEAK_SUFFIX

    out = []
    cur = subj.path.parent
    for _ in range(4):
        for f in (cur / LEAK_DIR / f"{subj.path.stem}{LEAK_SUFFIX}", cur.joinpath(*LEAK_FILE)):
            if f.is_file():
                out.append(f)
        if proj is not None and cur == proj or cur.parent == cur:
            break
        cur = cur.parent
    return out


def _brief(r: list) -> str:
    hint = str(r[7] or "")
    return f"{r[9]}/{r[0]} {r[1]}: {hint[:180]}".replace("  ", " ")


def _commission(proj) -> dict:
    import json as _json

    if proj is None:
        return {}
    try:
        data = _json.loads((proj / "asset.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _inventory_status(subj: Subject, proj, session: str | None, blocking: list[str], out: dict) -> bool:
    """Add the inventory verdicts (contract K6) to ``blocking``; returns whether the model is an authored asset
    (it has an inventory, a kit export sidecar or a project), which must also pass the leak check."""
    from ..core.paths import jpath

    inv_file = proj / "design" / "inventory.json" if proj is not None else None
    has_proj = inv_file is not None and inv_file.is_file()
    side, side_doc = _sidecar_doc(subj)
    kit = _kit_export(subj)
    asset = _commission(proj)
    authored = has_proj or side is not None or kit is not None or bool(asset)
    if asset.get("detail") == "none" and not has_proj:
        blocking.append(f"inventory: project {proj.name} opted out of the inventory (asset init --detail none): an "
                        "authored asset is done only with an itemised design/inventory.json (satk inventory starter "
                        f"{asset.get('kind') or '<kind>'} --project {proj.name})")
        return True
    try:
        from ..inventory.api import report  # contract K6 (lane inventory)
    except ImportError:
        if has_proj:
            blocking.append("inventory: design/inventory.json exists but satk.inventory is not installed, so the "
                            "items cannot be verified (update satk)")
        return authored
    if side is not None and side_doc is None:
        blocking.append(f"inventory: the sidecar {side.name} does not read: re-export the model (kit export writes it)")
        return True
    side_inv = bool(side_doc and side_doc.get("inventory"))
    if not has_proj and not side_inv:
        if authored:
            where = (f"the kit export {jpath(kit)}" if kit is not None else
                     f"the sidecar {side.name}" if side is not None else f"project {proj.name}")
            blocking.append(f"inventory: no inventory for this authored model ({where}): give the project its "
                            "design/inventory.json (satk asset init <name> --kind <kind> --detail hero, or satk "
                            "inventory starter <kind> --project <name>), tag the parts (scene.tag) and export again")
        return authored
    dff = str(subj.path) if subj.path is not None else None
    rep = None
    try:
        if side_inv or (side is not None and has_proj):
            rep = report(proj if has_proj else None, dff=dff, strict=True)
        else:
            rep = report(proj, session=session, strict=True)
    except SatkError as e:
        blocking.append(f"inventory: the report failed: {e.msg}" + (f" ({e.hint})" if getattr(e, "hint", None) else ""))
    except Exception as e:  # noqa: BLE001 - a broken input is a blocking row, never a crash of the check
        blocking.append(f"inventory: the report failed ({type(e).__name__}: {str(e)[:120]})")
    if rep is None:
        return True
    out["inventory"] = rep.get("counts", {})
    pairs: set[str] = set()
    for it in rep.get("items", []):
        if it.get("status") != "built" and it.get("required", True):
            why = it.get("why") or it.get("reason")
            blocking.append(f"inventory/{it.get('id')} {it.get('name', '')}: {it.get('status')}"
                            + (f" ({', '.join(it.get('objects', [])[:3])})" if it.get("objects") else "")
                            + (f" - {str(why)[:120]}" if why else ""))
        if isinstance(it.get("count"), int) and it["count"] >= 2:
            for n in list(it.get("objects") or []) + list(it.get("frames") or []):
                pairs.add(str(n).split(".")[0].lower())
    if pairs:
        out["_pairs"] = pairs
    for prob in (rep.get("problems") or [])[:10]:
        blocking.append(f"inventory: {str(prob)[:200]}")
    if not rep.get("complete", False) and not any(b.startswith("inventory") for b in blocking):
        blocking.append("inventory: the report says the inventory is not complete")
    return True


def _lod_owner(subj: Subject) -> str | None:
    """The HD model whose kit export names this DFF as its LOD (its leak check is required too)."""
    try:
        from ..look.review import lod_sibling_of
    except ImportError:
        return None
    return lod_sibling_of(subj.path) if subj.path is not None else None


def _leak_status(subj: Subject, proj, required: bool, blocking: list[str], out: dict, kind: str | None = None) -> None:
    """Add the light-leak gaps of ``checks/<stem>.leak.json`` (contract K8) to ``blocking``; ``required``: an
    authored asset (and the LOD of one) must have run a full leak check on this very export, recorded by satk."""
    import hashlib
    import json as _json

    from ..core.paths import jpath

    cands = _leak_files(subj, proj)
    sha_now = None
    if subj.path is not None:
        try:
            sha_now = hashlib.sha256(subj.path.read_bytes()).hexdigest()
        except OSError:
            sha_now = None
    lf = data = None
    for f in cands:
        try:
            d = _json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            if f.name != "leak.json":
                blocking.append(f"leak: {f.name} does not read ({type(e).__name__}); run look.leak again")
                return
            continue
        if f.name == "leak.json":           # the old shared file: only when it names this DFF
            named = str((d or {}).get("dff") or "") if isinstance(d, dict) else ""
            if not named or named.replace("\\", "/").rsplit("/", 1)[-1].lower() != subj.path.name.lower():
                continue
        lf, data = f, d
        break
    stem = subj.path.stem if subj.path is not None else subj.name
    if lf is None:
        if required and subj.path is not None:
            blocking.append(f"leak: no checks/{stem}.leak.json next to {subj.path.name}: run satk look leak "
                            f"{jpath(subj.path)} --package {jpath(subj.path.parent)}")
        return
    gaps = data.get("gaps", data.get("leaks", [])) if isinstance(data, dict) else data
    gaps = gaps if isinstance(gaps, list) else []
    out["leak"] = {"file": jpath(lf), "gaps": len(gaps)}
    sha = data.get("dff_sha256") if isinstance(data, dict) else None
    if subj.path is not None:
        if sha:
            stale = sha_now is not None and sha_now != sha
        else:
            stale = lf.stat().st_mtime < subj.path.stat().st_mtime
        if stale:
            blocking.append(f"leak: {lf.name} is older than the model: run look.leak on the new export")
        elif required and isinstance(data, dict):
            from ..look import review as RV

            cov = data.get("coverage") if isinstance(data.get("coverage"), dict) else None
            if cov is None:
                blocking.append(f"leak: {lf.name} does not say which regions it covered (an old or hand-written "
                                "file): run look.leak again over every region")
            elif not cov.get("full"):
                blocking.append(f"leak: {lf.name} covers only part of the model ({cov.get('why') or 'not full'}): "
                                "run look.leak with the kind's regions and the default settings")
            if kind and data.get("kind") and str(data["kind"]) != kind:
                blocking.append(f"leak: {lf.name} checked the model as {data['kind']}, the asset is a {kind}: run "
                                f"look.leak --kind {kind}")
            why = RV.record_mismatch(data)
            if why:
                blocking.append(f"leak: {lf.name} does not match satk's own record ({why}): run look.leak again")
    for g in gaps[:20]:
        if isinstance(g, dict):
            at = g.get("at") or g.get("point") or g.get("p3d")
            where = f" near ({', '.join(f'{float(x):.2f}' for x in at)})" if isinstance(at, (list, tuple)) \
                and len(at) == 3 else ""
            size = (f"{g['area_cm2']} cm2" if g.get("area_cm2") else
                    f"{g.get('px') or g.get('pixels') or g.get('area')} px"
                    if g.get("px") or g.get("pixels") or g.get("area") else "")
            label = "/".join(str(x) for x in (g.get("region"), g.get("view") or "view") if x)
            what = "background shows through the model" if g.get("kind", "gap") == "gap" else \
                f"{g['kind']} (background shows through)"
            blocking.append(f"leak/{label}{' ' + str(g['id']) if g.get('id') else ''}: {what}{where}"
                            + (f" next to {g['near']}" if g.get("near") else "") + (f" ({size})" if size else ""))
        else:
            blocking.append(f"leak: {str(g)[:160]}")
    if len(gaps) > 20:
        blocking.append(f"leak: {len(gaps) - 20} more gaps in {lf.name}")
    if isinstance(data, dict) and data.get("clean") is False and not gaps:
        blocking.append(f"leak: {lf.name} is not clean ({data.get('blocking')} blocking)")


def _kind_of(subj: Subject, proj) -> str | None:
    """The kit kind of an authored subject (its sidecar inventory, else its project)."""
    _side, doc = _sidecar_doc(subj)
    k = ((doc or {}).get("inventory") or {}).get("kind") if isinstance((doc or {}).get("inventory"), dict) else None
    if not k:
        k = _commission(proj).get("kind")
    return str(k) if k else None


def strict_status(subj: Subject, result: dict, *, project: str | None = None, session: str | None = None) -> dict:
    """``{"done", "blocking": [text], "inventory"?, "leak"?}``: the definition of done of contract K7 = engine
    clean (no error, no warning) + no form/fit/mesh defect or warning + every required checklist item present +
    every inventory item built (``design/inventory.json`` of the project, or the inventory sidecar of a kit export,
    through ``satk.inventory.api.report``; an authored model - a kit export or a project - must have one) + no
    asymmetry of the parts of an item counted in pairs + no light-leak gap (``<package>/checks/<stem>.leak.json``
    of a full ``look.leak`` run on this export, matching satk's record; required for an authored asset and its
    LOD)."""
    blocking: list[str] = []
    advice: list[str] = []
    pol = strict_policy(str(result.get("class") or ""))
    for r in result["rows"]:
        if r[9] == "texture" and r[6] == "warn":      # the texture look is advice, never blocking
            advice.append(_brief(r))
            continue
        bad = STRICT_BLOCKS.get(r[9], ())
        if r[6] in bad and not (r[9] == "coverage" and r[1] == "optional"):
            if r[6] == "warn" and not _warn_blocks(r, pol):
                advice.append(_brief(r))
            else:
                blocking.append(_brief(r))
    out: dict = {}
    proj = _project_of(subj, project) or _sidecar_project(subj) or _session_project(session)
    owner = _lod_owner(subj)
    if owner:                         # the LOD of an authored model: its inventory is the HD's; its leak run is its own
        out["lod_of"] = owner
        authored = False
    else:
        authored = _inventory_status(subj, proj, session, blocking, out)
    pairs = out.pop("_pairs", None)
    if pairs:                         # an item counted in pairs (mirrors, lamps): its parts must mirror
        for r in result["rows"]:
            if r[9] == "symmetry" and r[6] in ("warn", "defect") \
                    and str(r[1]).split(".")[0].lower() in pairs:
                blocking.append(_brief(r) + " (an inventory item counted in pairs)")
    _leak_status(subj, proj, authored or owner is not None, blocking, out,
                 kind="lod" if owner else _kind_of(subj, proj))
    out["done"] = not blocking
    out["blocking"] = blocking
    if advice:
        out["advice"] = advice[:30]
    return out


# ----------------------------------------------------------------------------- one subject
def is_authored(subj: Subject) -> bool:
    """A model built with satk: a kit export (its ``export.json`` or inventory sidecar) or a DFF inside an asset
    project. Vanilla and third-party DFFs are not."""
    if subj.path is None:
        return False
    try:
        side, _doc = _sidecar_doc(subj)
        return side is not None or _kit_export(subj) is not None or _project_of(subj, None) is not None
    except SatkError:
        return False


def check_subject(subj: Subject, cache: K.StyleCache, *, cls: str | None = None, like: str | None = None,
                  tier: str | None = None, all_rows: bool = False, w2: bool = True, profile: str = "vanilla",
                  design: bool = True, authored: bool | None = None) -> dict:
    """Check one subject; returns ``{"target", "class", "peer_set", "how", "tier", "rows", "counts", "sections",
    "defects", "verdict", "form"?, "metrics", "notes"}`` (every row; ``all_rows`` is kept for old callers).
    ``design``: run the form, fit, symmetry and coverage sections (numpy; off = engine and reference only)."""
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
    if w2:
        rows += _w2_rows(subj, profile)
    form_counts: dict = {}
    if design:
        drows, form_counts = design_sections(subj, key, tg["class"], like_scene, str(lk.get("name") or ""),
                                             authored=is_authored(subj) if authored is None else bool(authored))
        rows += drows
    trows = texture_rows(subj, tg["class"], profile) if design else None
    if trows is not None:
        rows += trows
    rows += metric_rows(m, cache, key, tier)
    counts: dict[str, int] = {}
    # every section that ran is listed (an empty one = checked, nothing to report)
    ran = {"engine", "reference"}
    if form_counts:
        ran |= {"form", "mesh", "coverage"} | ({"fit", "symmetry"} if C.family(key) == "vehicle" else set())
    if trows is not None:
        ran.add("texture")
    sections: dict[str, dict] = {s_: {} for s_ in SECTIONS if s_ in ran}
    for r in rows:
        counts[r[6]] = counts.get(r[6], 0) + 1
        sec = sections.setdefault(r[9], {})
        sec[r[6]] = sec.get(r[6], 0) + 1
    defects = sum(1 for r in rows if r[6] == "defect")
    verdict = "fail" if any(counts.get(b) for b in BLOCKING) else ("review" if any(counts.get(v) for v in REVIEW)
                                                                   else "pass")
    sorder = {s_: i for i, s_ in enumerate(SECTIONS)}
    order = {"error": 0, "defect": 1, "warn": 2, "missing": 3, "info": 4, "present": 5, "ok": 6}
    rows.sort(key=lambda r: (sorder.get(r[9], 9), order.get(r[6], 9), r[0], str(r[1])))
    out = {"target": subj.label, "class": tg["class"], "peer_set": key, "how": tg.get("how", ""),
           "like": tg.get("like", {}).get("sid"), "tier": tier, "rows": rows, "counts": counts, "sections": sections,
           "defects": defects, "verdict": verdict, "metrics": m, "notes": subj.notes + tg.get("fallback", [])}
    if form_counts:
        out["form"] = form_counts
    return out
