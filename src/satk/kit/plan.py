"""Template plans (``kit.template``): what to scaffold in Blender for a new asset, from vanilla NAMES and NUMBERS.

A plan copies the frame tree of a vanilla model (``--like SID``, or the catalog exemplar of ``--kind``) in its
order: names, parents, local matrices (positions scaled to ``--dims``), roles, slots, material roles per part,
material presets, a COL skeleton (sphere/box centres and radii with surface bytes, face counts), the LOD pair,
own-texture names and the IDE line fields. It never holds vertices, faces or pixels of the vanilla model;
the optional ghost is the vanilla file itself, imported read-only into a ``satk_ghost`` collection that is
never exported.

Example::

    from satk.kit.plan import template_plan
    p = template_plan(like="model:426", name="mycar", dims=[5.9, 2.3, 1.5], tier="sa_plus")
    len(p["frames"]), p["counts"]            # 51, {"dummies": ..., "slots": 22, ...}
"""

from __future__ import annotations

import json
import math
import re
import struct
from typing import Any

from ..core.errors import SatkError
from . import kinds as K

__all__ = ["template_plan", "role_of", "slot_of", "check_name", "lod_name", "MAT12", "NAME_MAX"]

#: Longest model name (IDE/DFF names hold 23 characters; vehicle COL names add ``_col`` to 21 + NUL).
NAME_MAX = {"vehicle": 17, "world": 19, "character": 19}
NAME_RE = re.compile(r"^[a-z0-9_]+$")
MAT12 = ("rx", "ry", "rz", "ux", "uy", "uz", "ax", "ay", "az", "px", "py", "pz")
_KEYS = {(60, 255, 0): "paint1", (255, 0, 175): "paint2", (0, 255, 255): "paint1", (255, 0, 255): "paint2",
         (255, 175, 0): "lamp_fl", (0, 255, 200): "lamp_fr", (185, 255, 0): "lamp_rl", (255, 60, 0): "lamp_rr"}
_HANIM, _EXT, _FRAMELIST, _CLUMP, _FRAMENAME = 0x11E, 0x03, 0x0E, 0x10, 0x253F2FE


def lod_name(name: str) -> str:
    """Name of a model's LOD: ``lod`` + the name without its first 3 characters (the vanilla form, 95 % of LOD names), at
    most 19 characters; names of 3 or fewer characters get ``lod`` in front."""
    n = str(name).lower()
    return ("lod" + (n[3:] if len(n) > 3 else n))[:19]


def check_name(name: str, group: str) -> str:
    n = str(name or "").strip().lower()
    mx = NAME_MAX.get(group, 19)
    if not n or not NAME_RE.match(n) or len(n) > mx:
        raise SatkError("BAD_PARAMS", f"name: 1-{mx} characters a-z 0-9 _, got {name!r}",
                        hint="vehicle names stay <= 17 characters (the embedded COL is <name>_col, 21 max)")
    return n


# --------------------------------------------------------------------------- roles


def slot_of(frame: str, kind_group: str) -> tuple[str, str]:
    """``(part, slot)`` of an atomic frame name: ``door_lf_ok`` -> ``(door_lf, ok)``."""
    n = (frame or "").strip().lower()
    for suf, slot in (("_ok", "ok"), ("_dam", "dam"), ("_vlo", "vlo"), ("_hi_ok", "ok")):
        if n.endswith(suf):
            return n[: -len(suf)], slot
    if n == "gunflash":
        return n, "flash"
    return n, "hd"


def role_of(rgba: int, texture: str | None, group: str, *, part: str = "") -> str:
    """Material role (``data/kit/materials.json``) of a vanilla material."""
    r, g, b, a = (rgba >> 24) & 255, (rgba >> 16) & 255, (rgba >> 8) & 255, rgba & 255
    t = (texture or "").lower()
    if group == "character":
        if "muzzle" in t or part == "gunflash":
            return "gunflash"
        return "ped" if part != "weapon" else "weapon"
    if group == "world":
        return "map_alpha" if a < 255 else "map"
    if (r, g, b) in _KEYS:
        k = _KEYS[(r, g, b)]
        return k if not k.startswith("lamp") or t == "vehiclelights128" else "lens"
    if t == "vehiclelights128":
        return "lens"
    if t == "vehicletyres128":
        return "tyre"
    if t in ("carplate", "carpback"):
        return "plate"
    if t == "vehiclescratch64":
        return "scratch"
    if t == "vehicleshatter128":
        return "shatter"
    if t in ("vehicledash32", "vehiclesteering128") or "interior" in t:
        return "interior"
    if "wheel" in t:
        return "rim"
    if t == "vehiclegeneric256":
        if a < 255:
            return "glass"
        if (r, g, b) == (255, 255, 255):
            return "chrome"
        return "trim"
    if not t:
        if a < 255:
            return "glass"
        return "black" if max(r, g, b) < 40 else "trim"
    return "decal"


# --------------------------------------------------------------------------- matrices


def _rot(m) -> list[list[float]]:
    """3x3 with columns right, up, at (RW 12-float matrix)."""
    return [[m[0], m[3], m[6]], [m[1], m[4], m[7]], [m[2], m[5], m[8]]]


def _inv3(a: list[list[float]]) -> list[list[float]]:
    (a00, a01, a02), (a10, a11, a12), (a20, a21, a22) = a
    det = a00 * (a11 * a22 - a12 * a21) - a01 * (a10 * a22 - a12 * a20) + a02 * (a10 * a21 - a11 * a20)
    if abs(det) < 1e-12:
        return [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    inv = [[(a11 * a22 - a12 * a21), -(a01 * a22 - a02 * a21), (a01 * a12 - a02 * a11)],
           [-(a10 * a22 - a12 * a20), (a00 * a22 - a02 * a20), -(a00 * a12 - a02 * a10)],
           [(a10 * a21 - a11 * a20), -(a00 * a21 - a01 * a20), (a00 * a11 - a01 * a10)]]
    return [[v / det for v in row] for row in inv]


def _mv(a: list[list[float]], v: list[float]) -> list[float]:
    return [a[0][0] * v[0] + a[0][1] * v[1] + a[0][2] * v[2], a[1][0] * v[0] + a[1][1] * v[1] + a[1][2] * v[2],
            a[2][0] * v[0] + a[2][1] * v[1] + a[2][2] * v[2]]


def _r(v: float, nd: int = 5) -> float:
    x = round(float(v), nd)
    return 0.0 if x == 0 else x


# --------------------------------------------------------------------------- vanilla data


def _bones(buf: bytes) -> dict[int, int]:
    """``{frame index: HAnim node id}`` of the first clump."""
    from ..formats.rw import iter_children

    out: dict[int, int] = {}

    def walk(off: int, end: int) -> bool:
        for ch in iter_children(buf, off, end, strict=False):
            if ch.type == _FRAMELIST:
                exts = [k for k in iter_children(buf, ch.data_off, ch.end, strict=False) if k.type == _EXT]
                for i, e in enumerate(exts):
                    for k in iter_children(buf, e.data_off, e.end, strict=False):
                        if k.type == _HANIM and k.size >= 8:
                            out[i] = struct.unpack_from("<i", buf, k.data_off + 4)[0]
                return True
            if ch.type == _CLUMP and walk(ch.data_off, ch.end):
                return True
        return False

    try:
        walk(0, len(buf))
    except Exception:  # noqa: BLE001 - names-only templates work without ids
        return {}
    return out


def _ide(db, model_id: int | None) -> dict:
    if model_id is None:
        return {}
    env = db.query("SELECT sec, txd, draw, flags, extra FROM model WHERE id=? AND active=1", [model_id], limit=1)
    if not env["rows"]:
        return {}
    row = dict(zip(env["cols"], env["rows"][0]))
    try:
        row["extra"] = json.loads(row.get("extra") or "{}")
    except ValueError:
        row["extra"] = {}
    return row


def _own_textures(db, txd: str | None, like: str, name: str) -> list[dict]:
    if not txd:
        return []
    env = db.query("SELECT t.name, t.w, t.h, t.d3dfmt, t.alpha FROM txd x JOIN blob b ON b.id = x.blob_id "
                   "JOIN texture t ON t.txd_id = x.id WHERE x.name = ? AND b.active = 1 ORDER BY t.idx", [txd], limit=64)
    out = []
    pat = re.compile(re.escape(like), re.IGNORECASE)
    for n, w, h, fmt, alpha in env["rows"]:
        nn = pat.sub(name, n).lower()
        if nn == n.lower() and "92" in nn:  # another car's texture copied into this TXD: <name>92<what>
            nn = name + nn[nn.index("92"):]
        out.append({"name": nn[:31], "like": n, "size": [w, h], "format": fmt, "alpha": bool(alpha)})
    return out


def _col(db, src, dff: bytes | None) -> Any:
    """The vanilla COL model of ``src`` decoded with :mod:`satk.rw.col` (``None`` when there is none)."""
    from ..formats.dff import find_embedded_col
    from ..rw import col as RC

    try:
        if src.col is not None and src.col.via != "embedded":
            recs, _tail = RC.split_models(db.read_blob(src.col.blob))
            return RC.decode_model(recs[src.col.idx]) if src.col.idx < len(recs) else None
        if dff is not None:
            r = find_embedded_col(dff)
            if r:
                return RC.decode_model(bytes(dff[r[0]:r[0] + r[1]]))
    except Exception:  # noqa: BLE001 - a template works without the COL skeleton
        return None
    return None


def _lod_of(db, model_id: int | None) -> dict | None:
    if model_id is None:
        return None
    env = db.query("SELECT DISTINCT l.model_id, m.name, m.draw FROM inst i JOIN inst l ON l.id = i.lod_id "
                   "JOIN model m ON m.id = l.model_id AND m.active = 1 WHERE i.model_id = ? LIMIT 1", [model_id], limit=1)
    if not env["rows"]:
        return None
    mid, name, draw = env["rows"][0]
    return {"like": f"model:{mid}", "like_name": name, "draw": draw}


def _dims(scene) -> tuple[list[float], list[float]]:
    """``(bmin, bmax)`` of the undamaged parts in model space (numbers only)."""
    from ..style.metrics import is_hd_part

    lo = [math.inf] * 3
    hi = [-math.inf] * 3
    for p in scene.parts:
        if p.kind != "atomic" or not is_hd_part(p.name):
            continue
        m = scene.meshes[p.geom]
        pos = m.positions
        mm = p.matrix
        R = _rot(mm)
        for i in range(0, len(pos) - 2, 3):
            v = _mv(R, [pos[i], pos[i + 1], pos[i + 2]])
            for k in range(3):
                x = v[k] + mm[9 + k]
                lo[k] = min(lo[k], x)
                hi[k] = max(hi[k], x)
    if not math.isfinite(lo[0]):
        return [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
    return lo, hi


# --------------------------------------------------------------------------- the plan


def template_plan(like: str | None = None, kind: str | None = None, name: str | None = None,
                  dims: list[float] | None = None, tier: str = "sa_plus", profile: str = "vanilla",
                  ghost: bool = False, lod: bool = False) -> dict:
    """The template plan (see the module docstring). ``dims`` = ``[L, W, H]`` metres (Y, X, Z extents); ``lod`` gives a
    map model that is not a building (a prop) a LOD slot too (buildings always have one)."""
    from ..model3d.api import load
    from ..model3d.resolve import open_db

    if tier not in K.TIERS:
        raise SatkError("BAD_PARAMS", f"tier must be one of {', '.join(K.TIERS)}, got {tier!r}")
    if not like and not kind:
        raise SatkError("BAD_PARAMS", "give --like SID or --kind KIND", hint="satk kit kinds")
    kind_name = K.canonical(kind) if kind else None
    like_sid = like or K.get(kind_name)["like"]
    L = load(like_sid, profile)
    src = L.src
    if L.scene is None or L.dff is None:
        raise SatkError("NOT_FOUND", f"{src.sid} ({src.name}) has no DFF in profile {profile!r}",
                        hint="pick another --like model")
    db = open_db(profile)
    ide = _ide(db, src.model_id)
    extra = ide.get("extra") or {}
    guessed = K.kind_for(src.sec, extra.get("type"), model_id=src.model_id, name=src.name)
    if kind_name is None:
        kind_name = guessed
    kd = K.get(kind_name)
    group = kd["group"]
    warn: list[str] = []
    if K.get(guessed)["group"] != group:
        warn.append(f"KIND_MISMATCH: {src.name} looks like a {guessed}, the template uses kind {kind_name}")
    like_name = src.name.lower()
    new = check_name(name or like_name, group)
    scene = L.scene
    lo, hi = _dims(scene)
    d0 = {"L": hi[1] - lo[1], "W": hi[0] - lo[0], "H": hi[2] - lo[2]}
    scale = [1.0, 1.0, 1.0]
    if dims:
        if len(dims) != 3 or any((not isinstance(v, (int, float))) or v <= 0 for v in dims):
            raise SatkError("BAD_PARAMS", "dims takes L,W,H in metres (three positive numbers)")
        want = {"L": float(dims[0]), "W": float(dims[1]), "H": float(dims[2])}
        scale = [want["W"] / d0["W"] if d0["W"] > 1e-6 else 1.0, want["L"] / d0["L"] if d0["L"] > 1e-6 else 1.0,
                 want["H"] / d0["H"] if d0["H"] > 1e-6 else 1.0]
    bones = _bones(L.dff) if kind_name == "ped" else {}
    atoms: dict[int, list] = {}
    for p in scene.parts:
        if p.kind == "atomic":
            atoms.setdefault(p.frame, []).append(p)
    # scaled model-space positions, then local matrices that keep each frame's rotation
    mpos = {f.idx: [f.model[9] * scale[0], f.model[10] * scale[1], f.model[11] * scale[2]] for f in scene.frames}
    frames: list[dict] = []
    roots = 0
    tris_like = 0
    for f in scene.frames:
        nm = f.name
        role = "dummy"
        if f.parent < 0:
            roots += 1
            role = "root"
            nm = new
        if f.idx in bones and kind_name == "ped" and f.parent >= 0:
            role = "bone"
        loc = list(f.local)
        if f.parent >= 0:
            pm = scene.frames[f.parent].model
            t = _mv(_inv3(_rot(pm)), [mpos[f.idx][k] - mpos[f.parent][k] for k in range(3)])
            loc[9:12] = t
        else:
            loc[9:12] = mpos[f.idx]
        row: dict[str, Any] = {"i": f.idx, "name": nm if nm is not None else "", "parent": f.parent, "role": role,
                               "matrix": [_r(v) for v in loc]}
        if f.idx in bones:
            row["bone_id"] = bones[f.idx]
        parts = atoms.get(f.idx) or []
        if parts:
            if role != "root":
                row["role"] = "part"
            part, slot = (new, "hd") if (f.parent < 0 or kind_name == "ped") else slot_of(nm or "", group)
            row["part"], row["slot"] = part, slot
            mats: list[str] = []
            ntris = 0
            uvs = 0
            for p in parts:
                m = scene.meshes[p.geom]
                ntris += len(m.tris) // 3
                uvs = max(uvs, len(m.uv))
                for mt in scene.materials[p.geom]:
                    rr = role_of(mt.rgba, mt.texture, group,
                                 part="gunflash" if part == "gunflash" else ("weapon" if kind_name == "weapon" else ""))
                    if rr not in mats:
                        mats.append(rr)
            row["roles"] = mats
            row["vanilla_tris"] = ntris
            row["uv_sets"] = uvs
            if slot in ("ok", "hd", "flash"):
                tris_like += ntris
            row["atomic_index"] = min(p.idx for p in parts)
        frames.append(row)
    # roles used + the kind's roles -> presets
    used: list[str] = []
    for fr in frames:
        for rr in fr.get("roles", []):
            if rr not in used:
                used.append(rr)
    presets = K.presets_for(kind_name, new, extra=used)
    # COL skeleton (numbers only)
    cm = _col(db, src, L.dff)
    col: dict[str, Any] = {"name": f"{new}_col" if group == "vehicle" and kind_name != "vehicle_upgrade" else new,
                           "embedded": group == "vehicle" and kind_name != "vehicle_upgrade"}
    if cm is not None:
        sm = sum(scale) / 3.0
        col["spheres"] = [{"c": [_r(c[0] * scale[0], 3), _r(c[1] * scale[1], 3), _r(c[2] * scale[2], 3)],
                           "r": _r(r * sm, 3), "surface": list(s)} for c, r, s in cm.spheres]
        col["boxes"] = [{"min": [_r(a[k] * scale[k], 3) for k in range(3)], "max": [_r(b[k] * scale[k], 3) for k in range(3)],
                         "surface": list(s)} for a, b, s in cm.boxes]
        col["mesh_faces"] = len(cm.faces)
        col["shadow_faces"] = len(cm.shadow_faces)
        surf: dict[int, int] = {}
        for f in cm.faces:
            surf[f[3]] = surf.get(f[3], 0) + 1
        col["face_surfaces"] = {str(k): v for k, v in sorted(surf.items())}
    else:
        col["note"] = "no vanilla COL decoded: generate one with kit.col or at export"
    # LOD
    lod_d: dict[str, Any] = dict(kd.get("lod") or {})
    if lod and group != "world":
        raise SatkError("BAD_PARAMS", f"--lod is for map models (props, buildings), not for a {kind_name}",
                        hint="vehicles carry their _vlo state; satk kit kinds --kind " + kind_name)
    if kind_name == "building" or (lod and group == "world"):
        pair = _lod_of(db, src.model_id)
        lod_d["name"] = lod_name(new)
        if pair:
            lod_d["like"], lod_d["like_name"], lod_d["like_draw"] = pair["like"], pair["like_name"], pair["draw"]
    # own textures and IDE
    own = _own_textures(db, ide.get("txd"), like_name, new) if group != "world" else []
    ide_out: dict[str, Any] = {"sec": src.sec, "txd": new if group != "world" else ide.get("txd"),
                               "draw": ide.get("draw"), "flags": ide.get("flags")}
    if src.sec == "cars":
        for k in ("type", "handling", "class", "wheel_scale_f", "wheel_scale_r", "wheel_id", "anims"):
            if k in extra:
                ide_out[k] = extra[k]
    seg = K.segments()
    plan: dict[str, Any] = {
        "version": 1, "kind": kind_name, "group": group, "name": new, "tier": tier,
        "like": {"sid": src.sid, "name": like_name}, "profile": profile,
        "dims": {"like": {k: _r(v, 3) for k, v in d0.items()},
                 "target": {"L": _r(d0["L"] * scale[1], 3), "W": _r(d0["W"] * scale[0], 3), "H": _r(d0["H"] * scale[2], 3)},
                 "scale": [_r(s, 4) for s in scale], "bbox_like": [[_r(v, 3) for v in lo], [_r(v, 3) for v in hi]]},
        "anchors": {}, "frames": frames, "presets": presets, "col": col, "lod": lod_d,
        "txd": {"name": new if group != "world" else ide.get("txd"), "class": (kd.get("txd") or {}).get("class"),
                "own": own, "shared_parent": "vehicle" if src.sec == "cars" else None},
        "ide": ide_out,
        "segments": {"wheel": seg["round"]["wheel"][tier], "round_prop": seg["round"]["round_prop"][tier]},
        "warn": warn,
    }
    if extra.get("wheel_scale_f"):
        plan["anchors"]["wheel_scale"] = float(extra["wheel_scale_f"])
    if kind_name == "ped" or group == "world":
        plan["anchors"]["ped_height"] = 1.84
    budgets = {k: v for k, v in seg["tris"].items() if k.startswith(kind_name + ".") or k in ("wheel", "vlo")}
    if budgets:
        plan["budgets"] = {k: v.get(tier) for k, v in budgets.items()}
    nslots = sum(1 for fr in frames if fr.get("part"))
    plan["counts"] = {"frames": len(frames), "roots": roots, "slots": nslots,
                      "dummies": sum(1 for fr in frames if fr["role"] == "dummy"),
                      "bones": sum(1 for fr in frames if fr["role"] == "bone"),
                      "presets": len(presets), "col_spheres": len(col.get("spheres", [])),
                      "vanilla_tris": tris_like}
    if ghost:
        from ..model3d.api import export as m3export

        raw = m3export(src.sid, "raw", profile=profile)
        files = list(raw.get("files") or [])
        plan["ghost"] = {"dff": next((f for f in files if f.lower().endswith(".dff")), None),
                         "txd": [f for f in files if f.lower().endswith(".txd")]}
    return plan
