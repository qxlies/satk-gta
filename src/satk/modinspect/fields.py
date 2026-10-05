"""Field names and short value diffs for report rows (``mass 1400->1500, max_vel 240->260``). Stdlib only."""

from __future__ import annotations

from ..formats.handling import BIKE_FIELDS, BOAT_FIELDS, CAR_FIELDS, FLYING_FIELDS
from .traits import Rec, Trait, cells_equal

__all__ = ["field_names", "diff_detail", "fmt", "short"]

_IDE_FIELDS = {
    "objs": ("id", "name", "txd"), "tobj": ("id", "name", "txd"),
    "anim": ("id", "name", "txd", "ifp", "draw", "flags"),
    "weap": ("id", "name", "txd", "anim", "meshes", "draw", "flags"),
    "hier": ("id", "name", "txd"),
    "cars": ("id", "name", "txd", "type", "handling", "gxt", "anims", "class", "freq", "flags", "comprules",
             "wheel_id", "wheel_scale_f", "wheel_scale_r", "wheel_upgrade"),
    "peds": ("id", "name", "txd", "pedtype", "stat", "animgroup", "cars_mask", "flags", "ifp", "radio1", "radio2",
             "voice_type", "voice1", "voice2"),
    "txdp": ("child", "parent"),
}
_HANDLING = {
    "car": ("id",) + tuple(n for n, _ in CAR_FIELDS),
    "boat": ("type", "id") + tuple(n for n, _ in BOAT_FIELDS),
    "bike": ("type", "id") + tuple(n for n, _ in BIKE_FIELDS),
    "plane": ("type", "id") + tuple(n for n, _ in FLYING_FIELDS),
}


def field_names(trait: Trait, rec: Rec) -> tuple[str, ...]:
    if trait.name == "handling.cfg":
        return _HANDLING.get("car" if rec.section == "veh" else rec.section, ())
    if trait.name == "*.ide":
        return _IDE_FIELDS.get(rec.section, ())
    if trait.name == "carcols.dat":
        return ("name", "colours") if rec.key[0] == "car" else ("r", "g", "b")
    if trait.name == "object.dat":
        return ("name", "mass", "turn_mass", "air_res", "elasticity", "buoyancy", "uproot_limit", "col_dmg_mult",
                "col_dmg_effect", "col_resp_case", "cam_avoid", "cause_explosion", "fx_type")
    return ()


def fmt(v) -> str:
    if isinstance(v, float):
        return f"{v:.6g}"
    if isinstance(v, tuple):
        return ",".join(fmt(x) for x in v)
    return str(v)


def short(s: str, n: int = 80) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 3] + "..."


def _cell_diffs(names: tuple[str, ...], a: tuple, b: tuple, prefix: str = "") -> list[str]:
    out = []
    for i in range(max(len(a), len(b))):
        x = a[i] if i < len(a) else None
        y = b[i] if i < len(b) else None
        if x is not None and y is not None and cells_equal((x,), (y,)):
            continue
        name = names[i] if i < len(names) else f"f{i}"
        out.append(f"{prefix}{name} {fmt(x) if x is not None else '-'}->{fmt(y) if y is not None else '-'}")
    return out


def diff_detail(trait: Trait, old: Rec, new: Rec, limit: int = 4) -> str:
    """``field old->new`` for the fields that differ (bundled handling parts included), at most ``limit``."""
    diffs: list[str] = []
    if old.section != new.section:
        diffs.append(f"section {old.section}->{new.section}")
    diffs += _cell_diffs(field_names(trait, new), old.cells, new.cells)
    for a, b, tag in zip(old.parts or (None,) * 3, new.parts or (None,) * 3, ("boat", "bike", "plane")):
        if a is None and b is None:
            continue
        if a is None or b is None:
            diffs.append(f"{tag} line {'added' if a is None else 'removed'}")
            continue
        diffs += _cell_diffs(field_names(trait, b), a.cells, b.cells, prefix=f"{tag}.")
    if not diffs:
        return "same values"
    more = len(diffs) - limit
    return ", ".join(diffs[:limit]) + (f" (+{more} more)" if more > 0 else "")
