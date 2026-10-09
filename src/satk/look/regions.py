"""Review regions per asset kind (contract K8): ``data/kit/regions/<kind>.json``.

A kind file names the close-up regions a reviewer (a person, a critic model) checks one by one, and the
light-leak rules of the kind::

    {"kind": "automobile", "version": 1, "summary": "...", "ref": ["chassis"]?,
     "leak": {"cull": false, "gap": true, "inside": true, "floor_exclude": ["wheel*"], "floor_pct": 1.0},
     "regions": [{"name": "front", "summary": "...", "box": {"y": [0.62, 1.0]},
                  "cameras": [{"az": 0, "el": 8}, {"az": 38, "el": 16}]},
                 {"name": "wheels", "frames": ["wheel_*_dummy"], "radius_h": 0.42,
                  "cameras": [{"az": "out", "az_off": 15, "el": 6}]}, ...]}

Cameras: ``az`` degrees from +Y towards +X (0 = in front, 90 = right, -90 = left) or ``out`` (the side the frame
is on), ``el`` degrees, ``ortho``, ``fov``; ``inside`` + ``at`` = a camera standing inside (a room). ``box``
fractions of the reference box (x 0 = left .. 1 = right, y 0 = rear .. 1 = front, z 0 = bottom .. 1 = top), or
``frames`` (one view per matching frame within ``radius_h`` x the reference height). Options: ``hide``,
``glass: hide``, ``ground: false``, ``states``, ``leak: false`` (the Blender side: ``satk_blender.look.regions``).

Leak rules (``satk_blender.look.leak``): ``cull`` = back faces are invisible in the game (world objects, peds,
weapons; vehicles are drawn double-sided), ``gap`` = enclosed see-through between parts is a defect (closed
bodies; off where open frames are the design), ``inside`` = the inside of a shell seen through an opening is a
defect (vehicles), ``slit`` = a narrow lane of background between two parts is a defect (``slit_max_m`` wide at most,
``slit_min_cm2`` big at least; off for road vehicles), ``floor_*`` = what "under the model" means. Stdlib only.
"""

from __future__ import annotations

import difflib

from ..core.errors import SatkError

__all__ = ["VEHICLE_KINDS", "NO_CULL_FLAG", "kinds", "load", "select", "leak_config", "kind_of_row", "validate"]

#: Kinds the engine draws without back-face culling (CVehicle::SetupRender sets cull none).
VEHICLE_KINDS = ("automobile", "mtruck", "quad", "bike", "bmx", "boat", "plane", "heli", "trailer", "train",
                 "vehicle_upgrade")
#: IDE object flag that turns back-face culling off for a map object.
NO_CULL_FLAG = 0x200000
#: Leak thresholds shared by every kind (pixels and cm^2 of one cluster).
MIN_PX = 6
MIN_CM2 = 2.0
#: An inner surface this far (m) behind the rim of an opening is "seen across the body" (an open arch shows the
#: far side); vanilla arch liners, grilles and truck frames stay below it (calibrated on 35 vanilla vehicles).
INSIDE_DEPTH = 1.0
#: ... and the opening is at least this big (cm^2; vanilla arch edges and grille slots stay below it).
INSIDE_CM2 = 60.0
#: A lane of background between two surfaces at most this wide (m) is a slit (a seam between parts).
SLIT_MAX_M = 0.035
#: ... and at least this big (cm^2). Vehicles leave slits off: roof rails, bull bars, grilles and spoilers stand off
#: their body by design (their shell has the arch and inside checks); map models, weapons and planes have it on.
SLIT_MIN_CM2 = 10.0


def kinds() -> list[str]:
    """Kinds with a region file."""
    from ..core.resources import list_files

    names = list_files("kit", "regions", pattern="*.json")
    return sorted(str(n).replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0] for n in names)


def load(kind: str) -> dict:
    """The region file of ``kind`` (aliases such as ``car`` resolved through the kit catalog)."""
    from ..core.resources import exists, read_json

    k = str(kind or "").strip().lower()
    if not exists("kit", "regions", f"{k}.json"):
        try:
            from ..kit.kinds import canonical

            k = canonical(k)
        except SatkError:
            pass
    if not exists("kit", "regions", f"{k}.json"):
        raise SatkError("NOT_FOUND", f"no review regions for kind {kind!r}", hint="kinds: " + ", ".join(kinds()),
                        did_you_mean=difflib.get_close_matches(str(kind).lower(), kinds(), n=3, cutoff=0.4))
    return read_json("kit", "regions", f"{k}.json")


def select(defn: dict, names: list[str] | None) -> list[dict]:
    """The regions named in ``names`` (``None``, ``[]`` or ``all`` = every region), in file order."""
    regs = list(defn.get("regions") or [])
    want = [str(n).strip().lower() for n in (names or []) if str(n).strip()]
    if not want or want == ["all"]:
        return regs
    have = {r["name"]: r for r in regs}
    bad = [n for n in want if n not in have]
    if bad:
        raise SatkError("BAD_PARAMS", f"kind {defn.get('kind')}: no region {', '.join(bad)}",
                        hint="regions: " + ", ".join(have), did_you_mean=difflib.get_close_matches(bad[0], list(have)))
    return [r for r in regs if r["name"] in want]


def leak_config(defn: dict, *, ide_flags: int | None = None, cull: bool | None = None) -> dict:
    """The leak rules of a kind for the Blender job (``cull`` off for a map object with the IDE no-cull flag)."""
    lk = dict(defn.get("leak") or {})
    c = bool(lk.get("cull", defn.get("kind") not in VEHICLE_KINDS))
    if ide_flags is not None and int(ide_flags) & NO_CULL_FLAG:
        c = False
    if cull is not None:
        c = bool(cull)
    return {"kind": defn.get("kind"), "cull": c, "gap": bool(lk.get("gap", False)),
            "inside": bool(lk.get("inside", False)), "floor_exclude": list(lk.get("floor_exclude") or []),
            "floor_pct": float(lk.get("floor_pct", 1.0)), "min_px": int(lk.get("min_px", MIN_PX)),
            "min_cm2": float(lk.get("min_cm2", MIN_CM2)), "inside_depth": float(lk.get("inside_depth", INSIDE_DEPTH)),
            "inside_exclude": list(lk.get("inside_exclude", ["wheel*"])),
            "inside_cm2": float(lk.get("inside_cm2", INSIDE_CM2)),
            "inside_regions": list(lk.get("inside_regions") or []),
            "see_through_block": bool(lk.get("see_through_block", True)),
            "escape_max_m": lk.get("escape_max_m"), "slit": bool(lk.get("slit", True)),
            "slit_max_m": float(lk.get("slit_max_m", SLIT_MAX_M)),
            "slit_min_cm2": float(lk.get("slit_min_cm2", SLIT_MIN_CM2))}


def kind_of_row(row: dict | None, *, profile: str = "vanilla") -> str | None:
    """The kit kind of an index model row (``satk.look.lineup.model_row``): vehicles by IDE type, peds, weapons;
    map objects by interior placements (area 13 is drawn everywhere: exterior), LOD links and size; a model placed
    only as a LOD is ``lod``."""
    if not row:
        return None
    from ..kit.kinds import kind_for

    sec = row.get("sec")
    if sec in ("cars", "peds", "weap", "anim"):
        k = kind_for(sec, row.get("type"), model_id=row.get("id"), name=row.get("name"))
        if k == "weapon" and _melee(row):
            return "weapon_melee"
        return k
    interior = has_lod = only_lod = False
    try:
        from ..index.api import open_index

        env = open_index(profile).query(
            "SELECT MAX(area NOT IN (0, 13)), MAX(lod_id IS NOT NULL), MIN(is_lod), COUNT(*) FROM inst "
            "WHERE model_id = ?", [int(row["id"])], limit=1)
        r = (env.get("rows") or [[0, 0, 0, 0]])[0]
        interior, has_lod = bool(r[0]), bool(r[1])
        only_lod = bool(r[3]) and bool(r[2])
    except (SatkError, KeyError, TypeError, ValueError):
        pass
    if only_lod:
        return "lod"
    if sec == "objs" and _terrain(row):
        return "terrain"
    size = max(float(x) for x in (row.get("dims") or [0.0]))
    return kind_for(sec, None, model_id=row.get("id"), name=row.get("name"), interior=interior, size_m=size,
                    has_lod=has_lod)


def _terrain(row: dict) -> bool:
    """A large flat ground piece (road, land, hill): seen from above, never from street level below its edge."""
    from ..style.classes import _TERRAIN_RE

    d = [float(x) for x in (row.get("dims") or [])]
    if len(d) != 3:
        return False
    big = max(d[0], d[1])
    return big >= 20.0 and d[2] < 0.35 * big and bool(_TERRAIN_RE.search(str(row.get("name") or "")))


def _melee(row: dict) -> bool:
    """A melee or thrown weapon (modelled long along Z, no muzzle): its regions and starter differ from a gun's."""
    d = [float(x) for x in (row.get("dims") or [])]
    if len(d) != 3:
        return False
    return d[2] >= max(d[0], d[1]) * 1.15


def validate(defn: dict) -> list[str]:
    """Problems of a region file (empty = valid); the tests run it on every file."""
    out: list[str] = []
    if not defn.get("kind") or not isinstance(defn.get("regions"), list) or not defn["regions"]:
        return ["kind and a non-empty regions list are required"]
    names = set()
    for r in defn["regions"]:
        n = r.get("name")
        if not n or n in names:
            out.append(f"region name missing or repeated: {n!r}")
        names.add(n)
        if not r.get("cameras"):
            out.append(f"{n}: no cameras")
        if r.get("box") and r.get("frames"):
            out.append(f"{n}: box and frames are exclusive")
        for ax, rng in (r.get("box") or {}).items():
            if ax not in "xyz" or len(rng) != 2 or not 0.0 <= rng[0] < rng[1] <= 1.0:
                out.append(f"{n}: box {ax} {rng}")
        for c in r.get("cameras") or []:
            az = c.get("az")
            if not (isinstance(az, (int, float)) or az in ("out", "in")):
                out.append(f"{n}: camera az {az!r}")
            if az in ("out", "in") and not r.get("frames"):
                out.append(f"{n}: az {az} needs frames")
            if not -90 <= float(c.get("el", 0)) <= 90:
                out.append(f"{n}: camera el {c.get('el')}")
            if c.get("inside") and len(c.get("at") or [0, 0, 0]) != 3:
                out.append(f"{n}: inside camera needs at [x, y, z]")
        if r.get("glass") not in (None, "hide"):
            out.append(f"{n}: glass {r.get('glass')!r}")
        for s in r.get("states") or []:
            if s not in ("ok", "dam", "vlo", "col"):
                out.append(f"{n}: state {s!r}")
    if not any(r.get("leak", True) for r in defn["regions"]):
        out.append("no leak camera")
    return out
