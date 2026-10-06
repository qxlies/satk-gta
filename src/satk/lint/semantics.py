"""Semantic and crash-linked rules for vehicles, peds and weapons (``veh.*``, ``ped.*``, ``weap.*``,
``dff.flat_shading``, ``dff.vert_sharing``).

Format checks pass a model the game will load but render wrong or crash on later: the wheel the engine looks
up by name is missing, lamps that never light, paint that never gets dirty, a muzzle flash without a mesh.
These rules encode the engine conventions measured on the vanilla game (every threshold is in
``data/lint_rules.json`` with its source) and stay quiet on vanilla files (``satk asset lint <game root>
--baseline vanilla`` prints each rule's rate).

Engine facts used (gta-reversed, CrashInfo list):

* lamps: a material lights up only when its texture is ``vehiclelights128`` and its colour is a lamp key
  (255,175,0 / 0,255,200 / 185,255,0 / 255,60,0); ``CVehicleModelInfo::SetEditableMaterialsCB``;
* paint: the primary/secondary keys (60,255,0 / 255,0,175) recolour any texture, but dirt is drawn by
  changing ``vehiclegrunge256`` only;
* the ``xvehicleenv128`` sheen reads the second UV set (every vanilla material with it is on 2-UV geometry);
* the wheel size of the IDE line is the physical wheel (ride height, wheel spheres); the mesh is not scaled;
* the stencil shadow uses the shadow mesh of the embedded COL3 (crash ``0x0070FB39`` without it);
* tuning parts attach to ``ug_*`` frames (crash ``0x007F0BF7`` when the frame is missing);
* the skin of a ped is drawn with 32 bones and at most 4 weights per vertex (crash ``0x007C4781``).
"""

from __future__ import annotations

import statistics
import struct
from collections import Counter

from ..formats.dff import FX_ENV, DffInfo
from .anat import FrameRec, read_frames, side_of
from .metrics import geometry_bend, model_shading
from .rules import Collector

__all__ = ["check_semantics", "vehicle_type", "LAMP_KEYS"]

#: Lamp key colours -> lamp index (VehicleModelInfo.cpp SetEditableMaterialsCB).
LAMP_KEYS = {(255, 175, 0): 0, (0, 255, 200): 1, (185, 255, 0): 2, (255, 60, 0): 3}
_LIGHTS_TEX = "vehiclelights128"
_GRUNGE_TEX = "vehiclegrunge256"


def _rgb(rgba: int) -> tuple[int, int, int]:
    return (rgba >> 24) & 0xFF, (rgba >> 16) & 0xFF, (rgba >> 8) & 0xFF


def vehicle_type(d, frames: list[FrameRec]) -> str | None:
    """Vehicle type (``car``, ``bike``, ``heli`` ...): the IDE ``cars`` line, else guessed from the frames."""
    if d is not None and d.extra:
        t = d.extra.get("type")
        if t:
            return str(t).lower()
    names = {f.name for f in frames}
    if {"wheel_lf_dummy", "wheel_rb_dummy"} <= names:
        return "car"
    if {"wheel_front", "wheel_rear"} <= names:
        return "bike"
    return None


def check_semantics(c: Collector, label: str, data: bytes, info: DffInfo, cls: str, d, lazy, col_blob, ix) -> None:
    """Run the semantic rules of model class ``cls`` (``cars``, ``peds``, ``weap``) on one DFF."""
    frames = read_frames(data)
    names = {f.idx: f.name for f in frames}
    if cls == "cars":
        _vehicle(c, label, data, info, d, lazy, col_blob, ix, frames, names)
    elif cls == "peds":
        _ped(c, label, data, info)
    elif cls == "weap":
        _weapon(c, label, info, d, ix, frames, names)
    if cls in ("cars", "peds"):
        _shading(c, label, info, cls, lazy, names)


# --------------------------------------------------------------------------- shading


def _shading(c: Collector, label: str, info: DffInfo, cls: str, lazy, names: dict[int, str]) -> None:
    flat_on = c.on("dff.flat_shading")
    vs_on = c.on("dff.vert_sharing")
    if not (flat_on or vs_on):
        return
    meshes = lazy.meshes
    if meshes is None:
        return
    if flat_on:
        lim = (c.rules.param("dff.flat_shading", "min_bend", {}) or {}).get(cls)
        sh = model_shading(meshes, names)
        if lim is not None and sh is not None:
            c.seen("dff.flat_shading")
            if sh["shade.normal_bend"] < float(lim):
                p10 = (c.rules.param("dff.flat_shading", "vanilla_p10", {}) or {}).get(cls, "?")
                fold = sh.get("geo.median_dihedral")
                fp10 = (c.rules.param("dff.flat_shading", "fold_p10", {}) or {}).get(cls)
                why = ""
                if fold is not None and fp10 and fold < float(fp10) / 2:
                    why = (f"; the median fold between faces is only {fold:.1f} deg (vanilla {cls} p10 {fp10}): "
                           "mostly flat panels, so smoothing the normals alone cannot round them")
                c.add("dff.flat_shading", label, bend=sh["shade.normal_bend"], flat=sh["shade.flat_share"], cls=cls,
                      p10=p10, why=why)
    if vs_on:
        vmax = (c.rules.param("dff.vert_sharing", "max", {}) or {}).get(cls)
        bmax = (c.rules.param("dff.vert_sharing", "max_bend", {}) or {}).get(cls)
        tmin = int(c.rules.param("dff.vert_sharing", "min_tris", 200))
        if vmax is None or bmax is None:
            return
        worst = None
        seen = False
        for g, m in zip(info.geoms, meshes):
            name = names.get(g.frame, "")
            if g.tris < tmin or m.normals is None or name.endswith("_dam") or name.endswith("_vlo"):
                continue
            seen = True
            vpt = g.verts / g.tris
            if vpt <= float(vmax):
                continue
            bend = geometry_bend(m.positions, m.tris, m.normals)
            if bend < float(bmax) and (worst is None or vpt > worst[0]):
                worst = (vpt, g.idx, name or f"geometry {g.idx}", bend)
        if seen:
            c.seen("dff.vert_sharing")
        if worst is not None:
            c.add("dff.vert_sharing", label, geom=worst[1], part=worst[2], vpt=round(worst[0], 2),
                  bend=round(worst[3], 2), cls=cls)


# --------------------------------------------------------------------------- vehicles


def _part(name: str) -> str | None:
    """Budget key of a vehicle frame: chassis, chassis_vlo, wheel, door, bump, bonnet, boot, windscreen."""
    if name in ("chassis", "chassis_vlo", "wheel"):
        return name
    if not name.endswith("_ok"):
        return None
    base = name[:-3]
    for key in ("door", "bump", "bonnet", "boot", "windscreen"):
        if base == key or base.startswith(key + "_"):
            return key
    return None


def _env_textures(data: bytes) -> dict[tuple[int, int], str]:
    """``(geometry, slot) -> MatFX environment texture name`` (lower case) of every material that has one."""
    from ..rw import codecs as C
    from ..rw.chunk import EXTENSION, MATFX, STRING
    from ..rw.dff import DffDoc, PatchError

    out: dict[tuple[int, int], str] = {}
    try:
        doc = DffDoc.parse(data)
        refs = doc.materials()
    except (PatchError, ValueError, struct.error):
        return out
    for r in refs:
        ext = r.node.child(EXTENSION)
        for plg in (ext.kids or ()) if ext is not None else ():
            if plg.type != MATFX or not plg.data:
                continue
            try:
                fx = C.decode_matfx(plg.data)
            except (C.CodecError, ValueError, struct.error):
                continue
            for t in fx.textures():
                s = t.children(STRING)
                if s:
                    out[(r.geom, r.slot)] = C.decode_string(s[0].data or b"").text.lower()
                    break
    return out


def _vehicle(c: Collector, label: str, data: bytes, info: DffInfo, d, lazy, col_blob, ix,
             frames: list[FrameRec], names: dict[int, str]) -> None:
    vtype = vehicle_type(d, frames)
    fset = {f.name for f in frames}
    geo_frame = {g.idx: names.get(g.frame, "") for g in info.geoms}
    # ---- veh.frames
    req = (c.rules.param("veh.frames", "required", {}) or {}).get(vtype or "", [])
    if req and frames:
        c.seen("veh.frames")
        miss = [f for f in req if f not in fset]
        if miss:
            c.add("veh.frames", label, vtype=vtype, missing=miss[:8], n=len(miss))
    # ---- veh.dummy_side
    if vtype in set(c.rules.param("veh.dummy_side", "types", []) or []) and frames:
        c.seen("veh.dummy_side")
        off = float(c.rules.param("veh.dummy_side", "min_offset", 0.25))
        bad = [f for f in frames if side_of(f.name) and abs(f.pos[0]) >= off and (f.pos[0] > 0) != (side_of(f.name) > 0)]
        if bad:
            c.add("veh.dummy_side", label, frames=[f"{f.name} x={f.pos[0]:+.2f}" for f in bad[:5]], n=len(bad))
    # ---- veh.shadow_mesh
    if vtype in set(c.rules.param("veh.shadow_mesh", "types", []) or []):
        c.seen("veh.shadow_mesh")
        if col_blob is None:
            c.add("veh.shadow_mesh", label, what="no embedded collision (the car falls through the map and has "
                                                 "no shadow mesh)")
        else:
            from ..formats.col import iter_col
            from ..formats.rw import FormatError

            try:
                cms = list(iter_col(col_blob))
            except FormatError:
                cms = []
            if cms and cms[0].shadow_faces == 0:
                c.add("veh.shadow_mesh", label, what="the embedded collision has no shadow mesh")
    # ---- materials: lamp keys, environment sheen, paint
    lamp_bad: Counter = Counter()
    for m in info.materials:
        k = _rgb(m.rgba)
        if k in LAMP_KEYS and (m.texture or "").lower() != _LIGHTS_TEX:
            lamp_bad[(m.texture or "(none)").lower()] += 1
    if any(_rgb(m.rgba) in LAMP_KEYS for m in info.materials):
        c.seen("veh.light_key_tex")
    if lamp_bad:
        c.add("veh.light_key_tex", label, n=sum(lamp_bad.values()), tex=sorted(lamp_bad)[:4])
    env_tex = {str(t).lower() for t in c.rules.param("veh.env_uv2", "textures", []) or []}
    if c.on("veh.env_uv2") and env_tex and any(m.fx & FX_ENV for m in info.materials):
        envs = _env_textures(data)
        uvs = {g.idx: g.uv_sets for g in info.geoms}
        bad: list[tuple[str, str]] = []
        anyenv = False
        for m in info.materials:
            t = envs.get((m.geom, m.idx))
            if t in env_tex:
                anyenv = True
                if uvs.get(m.geom, 0) < 2:
                    bad.append((geo_frame.get(m.geom, "") or f"geometry {m.geom}", t))
        if anyenv:
            c.seen("veh.env_uv2")
        if bad:
            c.add("veh.env_uv2", label, n=len(bad), tex=sorted({t for _p, t in bad})[0],
                  parts=sorted({p for p, _t in bad})[:5])
    paint_types = set(c.rules.param("veh.paint_dirt", "types", []) or [])
    if vtype in paint_types and c.on("veh.paint_dirt") and any(m.color_slot == 1 for m in info.materials):
        meshes = lazy.meshes
        if meshes is not None:
            slots = {(m.geom, m.idx): m for m in info.materials}
            by_tex: Counter = Counter()
            for g, mesh in zip(info.geoms, meshes):
                part = geo_frame.get(g.idx, "")
                if part.endswith("_dam") or part.endswith("_vlo"):
                    continue
                for mi, k in Counter(mesh.mat_ids).items():
                    m = slots.get((g.idx, mi))
                    if m is not None and m.color_slot == 1:
                        by_tex[(m.texture or "(none)").lower()] += k
            tot = sum(by_tex.values())
            if tot:
                c.seen("veh.paint_dirt")
                share = by_tex.get(_GRUNGE_TEX, 0) / tot
                shared = {str(t).lower() for t in c.rules.param("veh.paint_dirt", "shared", []) or []}
                on_shared = sum(k for t, k in by_tex.items() if t in shared or t == "(none)") / tot
                if share < float(c.rules.param("veh.paint_dirt", "min_share", 0.25)) and on_shared >= 0.5:
                    top = [t for t, _k in by_tex.most_common(2)]
                    c.add("veh.paint_dirt", label, share=round(share, 2), tex=top)
    # ---- veh.wheel_scale
    if vtype in set(c.rules.param("veh.wheel_scale", "types", []) or []) and d is not None and d.extra:
        ws = d.extra.get("wheel_scale_f")
        wheel = next((g for g in info.geoms if geo_frame.get(g.idx) == "wheel"), None)
        meshes = lazy.meshes if wheel is not None else None
        if isinstance(ws, (int, float)) and ws > 0 and wheel is not None and meshes is not None:
            p = meshes[wheel.idx].positions
            if len(p) >= 3:
                c.seen("veh.wheel_scale")
                dia = max(max(p[1::3]) - min(p[1::3]), max(p[2::3]) - min(p[2::3]))
                r = dia / ws
                lo, hi = float(c.rules.param("veh.wheel_scale", "lo", 0.85)), float(c.rules.param("veh.wheel_scale", "hi", 1.15))
                if not lo <= r <= hi:
                    c.add("veh.wheel_scale", label, d=round(dia, 3), ws=round(float(ws), 3), ratio=round(r, 2),
                          gap=round(abs(dia - ws) / 2, 2))
    # ---- budgets: veh.hd_tris, veh.part_tris, veh.dam_ratio
    per_frame: Counter = Counter()
    seen_geo: set[int] = set()
    for g in info.geoms:
        if g.idx in seen_geo:
            continue
        seen_geo.add(g.idx)
        per_frame[geo_frame.get(g.idx, "") or f"geometry {g.idx}"] += g.tris
    hd = sum(t for n, t in per_frame.items() if not (n.endswith("_dam") or n.endswith("_vlo")))
    hb = c.rules.param("veh.hd_tris", "budget", {}) or {}
    hlim = hb.get(vtype or "", hb.get("default"))
    if hlim is not None and hd:
        c.seen("veh.hd_tris")
        if hd > int(hlim):
            c.add("veh.hd_tris", label, tris=hd, limit=int(hlim), vtype=vtype or "vehicle")
    pb = c.rules.param("veh.part_tris", "budget", {}) or {}
    if pb and (vtype in set(c.rules.param("veh.part_tris", "types", []) or []) or vtype is None):
        c.seen("veh.part_tris")
        over = []
        for n, t in sorted(per_frame.items()):
            key = _part(n)
            if key is not None and key in pb and t > int(pb[key]):
                over.append(f"{n} {t} > {int(pb[key])}")
        if over:
            c.add("veh.part_tris", label, parts=over[:6], n=len(over))
    lo, hi = float(c.rules.param("veh.dam_ratio", "lo", 0.5)), float(c.rules.param("veh.dam_ratio", "hi", 1.4))
    mint = int(c.rules.param("veh.dam_ratio", "min_tris", 24))
    pairs = []
    for n, t in per_frame.items():
        if n.endswith("_ok") and t >= mint:
            dam = per_frame.get(n[:-3] + "_dam")
            if dam is not None:
                pairs.append((n[:-3], dam / t))
    if len(pairs) >= 2:
        c.seen("veh.dam_ratio")
        med = statistics.median(r for _p, r in pairs)
        if not lo <= med <= hi:
            pairs.sort(key=lambda x: abs(x[1] - 1.0), reverse=True)
            c.add("veh.dam_ratio", label, median=round(med, 2), parts=[f"{p} {r:.2f}" for p, r in pairs[:5]])
    # ---- veh.upgrade_frames (against the vanilla model of the same name)
    if ix is not None and getattr(ix, "available", False) and c.on("veh.upgrade_frames"):
        stem = label.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
        ref = ix.ref_frames(stem)
        if ref:
            ug = {f for f in ref if f.startswith("ug_")}
            if ug:
                c.seen("veh.upgrade_frames")
                miss = sorted(ug - fset)
                if miss:
                    c.add("veh.upgrade_frames", label, name=stem, missing=miss[:8], n=len(miss))


# --------------------------------------------------------------------------- peds


def _ped(c: Collector, label: str, data: bytes, info: DffInfo) -> None:
    if not c.on("ped.skin"):
        return
    from ..rw import codecs as C
    from ..rw.chunk import SKIN
    from ..rw.dff import DffDoc, PatchError

    c.seen("ped.skin")
    want = int(c.rules.param("ped.skin", "bones", 32))
    wmax = int(c.rules.param("ped.skin", "max_weights", 4))
    try:
        geoms = DffDoc.parse(data).geometries()
    except (PatchError, ValueError):
        return
    problems: list[str] = []
    skins = 0
    for gr in geoms:
        ext = gr.ext()
        sk = next((k for k in (ext.kids or ()) if k.type == SKIN and k.data), None) if ext is not None else None
        if sk is None:
            continue
        skins += 1
        try:
            nv = gr.data().num_verts
            s = C.decode_skin(sk.data, nv)
        except (C.CodecError, ValueError, struct.error) as e:
            problems.append(f"geometry {gr.index}: unreadable skin ({e})")
            continue
        if s.num_bones != want:
            problems.append(f"geometry {gr.index}: {s.num_bones} bones (the ped skeleton has {want})")
        if s.max_weights > wmax:
            problems.append(f"geometry {gr.index}: {s.max_weights} weights per vertex > {wmax}")
        idx, wts = s.indices, s.weights
        if len(idx) >= 4 * nv and len(wts) >= 16 * nv and nv:
            w = struct.unpack_from(f"<{4 * nv}f", wts)
            bad = sum(1 for i in range(4 * nv) if w[i] > 0 and idx[i] >= s.num_bones)
            if bad:
                problems.append(f"geometry {gr.index}: {bad} vertex weights point past bone {s.num_bones - 1}")
    if not skins:
        problems.append("no Skin plugin: a ped model must be skinned to the 32-bone skeleton")
    if problems:
        c.add("ped.skin", label, what="; ".join(problems[:3]))


# --------------------------------------------------------------------------- weapons


def _weapon(c: Collector, label: str, info: DffInfo, d, ix, frames: list[FrameRec], names: dict[int, str]) -> None:
    if not c.on("weap.flash"):
        return
    fset = {f.name for f in frames}
    firearm = "gunflash" in fset
    if not firearm and ix is not None and getattr(ix, "available", False):
        stem = label.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
        firearm = "gunflash" in (ix.ref_frames(stem) or ())
    if not firearm:
        return
    c.seen("weap.flash")
    if "gunflash" not in fset:
        c.add("weap.flash", label, what="no 'gunflash' frame, but the vanilla model of this name has one: the "
                                        "weapon fires without a muzzle flash")
        return
    geo = [g for g in info.geoms if names.get(g.frame) == "gunflash"]
    if not geo:
        c.add("weap.flash", label, what="the 'gunflash' frame has no atomic (no flash mesh)")
        return
    if not any(m.texture for m in info.materials if m.geom in {g.idx for g in geo}):
        c.add("weap.flash", label, what="the 'gunflash' mesh has no texture (vanilla: muzzle_texture4)")
