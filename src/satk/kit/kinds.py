"""Kinds catalog, material presets, segment tables, game-ready classes and the scene contract.

Stdlib only (also imported from Blender's Python). Data: ``data/kit/{kinds,materials,segments,classes}.json``
and ``data/kit/atlas/*.json`` - vanilla names and numbers only.

Example::

    from satk.kit import kinds
    kinds.get("automobile")["like"]          # 'model:426'
    kinds.kind_for("cars", "bike")           # 'bike'
    kinds.preset("glass")["rgba"]            # [255, 255, 255, 128]
"""

from __future__ import annotations

import difflib
import functools
from typing import Any

from ..core import resources
from ..core.errors import SatkError

__all__ = [
    "TIERS", "VEHICLE_TYPES", "catalog", "names", "get", "kind_for", "materials", "preset", "presets_for",
    "segments", "classes", "game_ready_class", "texel_density", "scene_spec", "SHARED_TEXTURES", "is_shared",
    "col_choice",
]

TIERS = ("vanilla", "sa_plus")
#: vehicles.ide type -> kit kind.
VEHICLE_TYPES = {"car": "automobile", "mtruck": "mtruck", "quad": "quad", "bike": "bike", "bmx": "bmx",
                 "boat": "boat", "plane": "plane", "heli": "heli", "trailer": "trailer", "train": "train"}
#: Textures of shared TXDs a model references by name and never packs into its own TXD.
SHARED_TEXTURES = frozenset({
    "vehiclegeneric256", "vehiclegrunge256", "vehiclelights128", "vehiclelightson128", "vehicletyres128",
    "vehiclescratch64", "vehicleshatter128", "vehiclespecdot64", "vehicleenvmap128", "xvehicleenv128",
    "vehiclepoldecals128", "vehicledash32", "vehiclesteering128", "carplate", "carpback", "plateback1",
    "plateback2", "plateback3",
})


@functools.lru_cache(maxsize=None)
def _data(name: str) -> dict:
    return resources.read_json("kit", name)


def catalog() -> dict:
    """The whole ``kinds.json`` document."""
    return _data("kinds.json")


def names() -> list[str]:
    return list(catalog()["kinds"])


def get(kind: str) -> dict:
    """One kind (``NOT_FOUND`` with close names)."""
    k = str(kind or "").strip().lower()
    ks = catalog()["kinds"]
    if k in ks:
        return ks[k]
    alias = VEHICLE_TYPES.get(k) or {"car": "automobile", "upgrade": "vehicle_upgrade", "object": "prop",
                                      "interior": "interior_shell"}.get(k)
    if alias in ks:
        return ks[alias]
    raise SatkError("NOT_FOUND", f"no asset kind {kind!r}", hint="satk kit kinds",
                    did_you_mean=difflib.get_close_matches(k, list(ks), n=3, cutoff=0.4))


def canonical(kind: str) -> str:
    """The catalog name of ``kind`` (aliases resolved)."""
    k = str(kind or "").strip().lower()
    if k in catalog()["kinds"]:
        return k
    want = get(k)
    return next(n for n, v in catalog()["kinds"].items() if v is want)


def kind_for(sec: str | None, vtype: str | None = None, *, model_id: int | None = None,
             name: str | None = None, interior: bool = False, size_m: float | None = None,
             has_lod: bool = False, breakable: bool = False) -> str:
    """Kit kind of a vanilla model from its IDE section and type (best guess for map objects)."""
    s = (sec or "").lower()
    if s == "cars":
        return VEHICLE_TYPES.get((vtype or "car").lower(), "automobile")
    if s == "peds":
        return "ped"
    if s == "weap":
        return "weapon"
    if s == "anim":
        return "animated_object"
    if model_id is not None and 1000 <= model_id <= 1193:
        return "vehicle_upgrade"
    if name and name.lower() in {e.lower() for e in get("pickup").get("examples", [])}:
        return "pickup"
    if breakable:
        return "breakable"
    if interior:
        return "interior_shell" if (size_m or 0) >= 8 else "interior_prop"
    if has_lod or (size_m or 0) >= 10:
        return "building"
    return "prop"


# --------------------------------------------------------------------------- materials, segments, classes


def materials() -> dict:
    return _data("materials.json")


def preset(role: str, model: str | None = None) -> dict:
    """Material preset of ``role`` (``<model>`` in texture names replaced by ``model``)."""
    roles = materials()["roles"]
    r = str(role).strip().lower()
    if r not in roles:
        raise SatkError("NOT_FOUND", f"no material preset {role!r}", hint="satk kit kinds --materials",
                        did_you_mean=difflib.get_close_matches(r, list(roles), n=3, cutoff=0.4))
    p = {k: v for k, v in roles[r].items() if k != "measured"}
    p["role"] = r
    tex = p.get("texture")
    if model and isinstance(tex, str) and "<model>" in tex:
        p["texture"] = tex.replace("<model>", model.lower())
    return p


def presets_for(kind: str, model: str | None = None, extra: list[str] | tuple[str, ...] = ()) -> dict[str, dict]:
    """``{role: preset}`` of a kind's material roles (plus ``extra`` roles), catalog order."""
    out: dict[str, dict] = {}
    for r in list(get(kind).get("material_roles") or []) + [x for x in extra if x]:
        if r not in out and r in materials()["roles"]:
            out[r] = preset(r, model)
    return out


def segments() -> dict:
    return _data("segments.json")


def classes() -> dict:
    return _data("classes.json")


def game_ready_class(cls: str) -> dict:
    cs = classes()["classes"]
    c = str(cls).strip().lower()
    if c not in cs:
        raise SatkError("BAD_PARAMS", f"no game-ready class {cls!r}", hint="classes: " + ", ".join(cs),
                        did_you_mean=difflib.get_close_matches(c, list(cs), n=3, cutoff=0.4))
    return dict(cs[c], name=c)


def texel_density(cls: str, size_m: float, tier: str = "vanilla") -> float:
    """Texel density (px/m) for a model of class ``cls`` whose largest side is ``size_m`` metres."""
    doc = classes()
    c = game_ready_class(cls)
    if c.get("texel_px_m"):
        base = float(c["texel_px_m"])
    else:
        base = float(doc["texel_px_m_by_size"][-1]["px_m"])
        for row in doc["texel_px_m_by_size"]:
            if size_m <= float(row["max_m"]):
                base = float(row["px_m"])
                break
    return round(base * float(doc["tier_texel_factor"].get(tier, 1.0)), 1)


def col_choice(kind: str, shape: dict | None) -> tuple[str, dict, str]:
    """``(col.gen mode, extra arguments, why)`` of a map kind by the class rule (``data/kit/classes.json`` col_rule).

    Small props, interior props and pickups (up to ``primitive_max_m``) get primitives: one sphere for a pickup or a
    ball-like solid, one box for a solid block (closed volume over 55 % of its bounding box), a few boxes for thin or
    open shapes. Everything else keeps the class's mode (hull, mesh, box).

    ``shape`` is the exporter's ``{"bbox", "size", "volume"?}``; without it the class default is used."""
    cls = get(kind).get("game_ready_class")
    rule = classes().get("col_rule") or {}
    default = {"building": "mesh", "interior_shell": "mesh", "pickup": "box"}.get(kind, "hull")
    if cls not in rule.get("classes", ()) or not shape or not shape.get("size"):
        return default, {}, f"class {cls}: {default}"
    size = float(shape["size"])
    if size > float(rule.get("primitive_max_m", 8.0)):
        return default, {}, f"{size:g} m is above the primitive limit: {default}"
    if cls == "pickup":
        pk = rule["pickup"]
        return pk["mode"], {"max_prims": pk["max_prims"]}, "pickup: one sphere"
    lo, hi = shape["bbox"]
    ext = [max(float(b) - float(a), 1e-6) for a, b in zip(lo, hi)]
    box_vol = ext[0] * ext[1] * ext[2]
    vol = shape.get("volume")
    fill = None if vol is None else float(vol) / box_vol
    if fill is None or fill > 1.0 + 1e-3:
        n = next((m for lim, m in rule["boxes"]["max_prims_by_size"] if size <= lim), rule["boxes"]["max_prims_by_size"][-1][1])
        return "boxes", {"max_prims": n}, "open or unmeasured volume: a few boxes"
    sp = rule["sphere"]
    if max(ext) / min(ext) <= sp["aspect_max"] and sp["fill"][0] <= fill <= sp["fill"][1]:
        return "spheres", {"max_prims": sp["max_prims"]}, f"ball-like (fill {fill:.2f}): one sphere"
    if fill >= rule["box"]["fill_min"]:
        return "box", {}, f"solid block (fill {fill:.2f}): one box"
    n = next((m for lim, m in rule["boxes"]["max_prims_by_size"] if size <= lim), rule["boxes"]["max_prims_by_size"][-1][1])
    return "boxes", {"max_prims": n}, f"thin or hollow (fill {fill:.2f}): up to {n} boxes"


def is_shared(texture: str | None) -> bool:
    return bool(texture) and str(texture).lower() in SHARED_TEXTURES


# --------------------------------------------------------------------------- scene contract

#: The Blender scene contract every kit method, ``blender.export`` and ``kit.export`` share.
SCENE_CONTRACT: dict[str, Any] = {
    "clump": "collection '<model>.dff' (custom props satk_kit = kind, satk_name, satk_tier, satk_plan)",
    "frames": "one object per frame: EMPTY for a dummy, MESH for a part (dff.is_frame = true); Blender parenting "
              "= the frame tree; dff.frame_index = vanilla order (parents before children); "
              "dff.atomic_index orders the atomics",
    "names": "object name = frame name (DragonFF drops a '.001' suffix); the root frame is the model name",
    "tags": {
        "satk_role": "root | dummy | part | bone | col | shadow | lod | ghost",
        "satk_part": "part name without the slot suffix (door_lf for door_lf_ok)",
        "satk_slot": "ok | dam | vlo | hd (whole-model part) | flash",
        "satk_ghost": "vanilla reference model: never exported, not counted by stats",
        "satk_sec": "IDE section (cars, peds, weap, objs) on the root",
        "satk_generated": "helper object: never exported",
    },
    "dragonff": {
        "obj.dff.type": "OBJ (render), COL (collision sphere/box/mesh), SHA (shadow mesh), 2DFX",
        "obj.dff.export_split_normals": "true after kit.shade (custom/corner normals reach the DFF)",
        "obj.dff.uv_map1 / uv_map2": "UV set 1 (texture) / 2 (vehicle env sheen)",
        "obj.dff.day_cols / night_cols": "map prelight colour attributes",
        "mat.dff": "ambient/specular/diffuse, export_env_map + env_map_tex + env_map_coef, export_specular + "
                   "specular_level + specular_texture, export_reflection + reflection_intensity",
        "material colour": "Principled Base Color default_value (RGBA, 0..1 = byte/255, alpha = material alpha)",
        "material texture": "image of the Base Color texture node; its name (or node label) is the texture name",
        "COL sphere": "EMPTY display SPHERE, empty_display_size = radius, location in model space, "
                      "dff.col_material / col_flags (piece) / col_brightness / col_day_light / col_night_light",
        "COL box": "EMPTY display CUBE, location = centre, scale = half extents",
    },
    "collision": "vehicles: collection '<model>_col' (embedded in the DFF, named <model>_col); world: '<model>.col' "
                 "file with one model named <model>",
    "lod": "map LOD: a separate clump collection 'lod<name[3:]>.dff'; vehicles: the *_vlo atomic",
    "export": "kit.export: own textures only in the TXD, frame-local bounding spheres, re-import diff",
}


def scene_spec(kind: str | None = None) -> dict:
    """The scene contract, plus the frames/slots/roles of ``kind`` when given."""
    out = {"contract": SCENE_CONTRACT}
    if kind:
        k = get(kind)
        out["kind"] = canonical(kind)
        out["frames"] = k.get("frames")
        out["slots"] = k.get("slots")
        out["material_roles"] = k.get("material_roles")
        out["col"] = k.get("col")
        out["lod"] = k.get("lod")
        out["txd"] = k.get("txd")
    return out
