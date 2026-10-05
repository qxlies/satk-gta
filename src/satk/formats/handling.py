"""Vehicle handling ``data/handling.cfg`` (``cHandlingDataMgr::LoadHandlingData``), SPEC §4.2. Stdlib only.

One record per line, whitespace separated; ``;`` starts a comment line and ``;the end`` ends the data.
The first character selects the record type:

* none - standard vehicle data (35 values, :data:`CAR_FIELDS`), keyed by the handling id that
  ``vehicles.ide`` names in its ``handling`` column (``INFERNUS``);
* ``!`` - bike data (15, :data:`BIKE_FIELDS`), ``$`` - flying data (21, :data:`FLYING_FIELDS`),
  ``%`` - boat data (14, :data:`BOAT_FIELDS`); these complement the standard line of the same id;
* ``^`` - vehicle anim groups (not vehicle handling; counted in :attr:`Handling.anim_groups`).

Values are kept in file units (km/h, ms^-2 ...; the engine converts them on load). ``model_flags`` and
``handling_flags`` are hexadecimal in the file. A later line with the same (id, type) replaces the earlier
one (same engine slot). Vanilla quirk: the ``$ RCRAIDER`` line has ``0.1s`` (an extra ``s`` that the
engine skips with ``%*c``).

Example::

    h = parse_handling(read_text(resolve_ci(root, "data/handling.cfg")))
    inf = h.get("INFERNUS", "car")
    inf.values["mass"], inf.values["max_vel"], inf.values["drive"]   # 1400.0 240.0 '4'
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .rw import FormatError

__all__ = ["HandlingRec", "Handling", "parse_handling", "CAR_FIELDS", "BIKE_FIELDS", "BOAT_FIELDS",
           "FLYING_FIELDS", "KINDS", "PREFIX_KIND", "MODEL_FLAGS", "HANDLING_FLAGS", "flag_names"]

#: ``(name, type)`` of the standard line after the id; type ``f`` float, ``i`` int, ``c`` char, ``x`` hex.
CAR_FIELDS: tuple[tuple[str, str], ...] = (
    ("mass", "f"), ("turn_mass", "f"), ("drag", "f"),
    ("com_x", "f"), ("com_y", "f"), ("com_z", "f"),
    ("submerged", "i"),
    ("traction_mult", "f"), ("traction_loss", "f"), ("traction_bias", "f"),
    ("gears", "i"), ("max_vel", "f"), ("accel", "f"), ("inertia", "f"), ("drive", "c"), ("engine", "c"),
    ("brake", "f"), ("brake_bias", "f"), ("abs", "i"), ("steer_lock", "f"),
    ("susp_force", "f"), ("susp_damping", "f"), ("susp_high_spd_damp", "f"), ("susp_upper", "f"),
    ("susp_lower", "f"), ("susp_bias", "f"), ("susp_anti_dive", "f"),
    ("seat_offset", "f"), ("dmg_mult", "f"), ("value", "i"),
    ("model_flags", "x"), ("handling_flags", "x"),
    ("front_lights", "i"), ("rear_lights", "i"), ("anim_group", "i"),
)
BIKE_FIELDS: tuple[tuple[str, str], ...] = tuple((n, "f") for n in (
    "lean_fwd_com", "lean_fwd_force", "lean_back_com", "lean_back_force", "max_lean", "full_anim_lean",
    "des_lean", "speed_steer", "slip_steer", "no_player_com_z", "wheelie_ang", "stoppie_ang", "wheelie_steer",
    "wheelie_stab_mult", "stoppie_stab_mult"))
BOAT_FIELDS: tuple[tuple[str, str], ...] = tuple((n, "f") for n in (
    "thrust_y", "thrust_z", "thrust_app_z", "aq_plane_force", "aq_plane_limit", "aq_plane_offset",
    "wave_audio_mult", "move_res_x", "move_res_y", "move_res_z", "turn_res_x", "turn_res_y", "turn_res_z",
    "look_lr_behind_cam_height"))
FLYING_FIELDS: tuple[tuple[str, str], ...] = tuple((n, "f") for n in (
    "thrust", "thrust_falloff", "yaw", "yaw_stab", "side_slip", "roll", "roll_stab", "pitch", "pitch_stab",
    "form_lift", "attack_lift", "gear_up_r", "gear_down_l", "wind_mult", "move_res", "turn_res_x",
    "turn_res_y", "turn_res_z", "speed_res_x", "speed_res_y", "speed_res_z"))

#: Bits of ``model_flags`` (hex digit 1 = lowest bits), as documented in the header of ``handling.cfg``.
MODEL_FLAGS: tuple[str, ...] = (
    "IS_VAN", "IS_BUS", "IS_LOW", "IS_BIG", "REVERSE_BONNET", "HANGING_BOOT", "TAILGATE_BOOT", "NOSWING_BOOT",
    "NO_DOORS", "TANDEM_SEATS", "SIT_IN_BOAT", "CONVERTIBLE", "NO_EXHAUST", "DOUBLE_EXHAUST", "NO1FPS_LOOK_BEHIND",
    "FORCE_DOOR_CHECK", "AXLE_F_NOTILT", "AXLE_F_SOLID", "AXLE_F_MCPHERSON", "AXLE_F_REVERSE", "AXLE_R_NOTILT",
    "AXLE_R_SOLID", "AXLE_R_MCPHERSON", "AXLE_R_REVERSE", "IS_BIKE", "IS_HELI", "IS_PLANE", "IS_BOAT",
    "BOUNCE_PANELS", "DOUBLE_RWHEELS", "FORCE_GROUND_CLEARANCE", "IS_HATCHBACK",
)
#: Bits of ``handling_flags`` (``None`` = unused bit).
HANDLING_FLAGS: tuple[str | None, ...] = (
    "1G_BOOST", "2G_BOOST", "NPC_ANTI_ROLL", "NPC_NEUTRAL_HANDL", "NO_HANDBRAKE", "STEER_REARWHEELS",
    "HB_REARWHEEL_STEER", "ALT_STEER_OPT", "WHEEL_F_NARROW2", "WHEEL_F_NARROW", "WHEEL_F_WIDE", "WHEEL_F_WIDE2",
    "WHEEL_R_NARROW2", "WHEEL_R_NARROW", "WHEEL_R_WIDE", "WHEEL_R_WIDE2", "HYDRAULIC_GEOM", "HYDRAULIC_INST",
    "HYDRAULIC_NONE", "NOS_INST", "OFFROAD_ABILITY", "OFFROAD_ABILITY2", "HALOGEN_LIGHTS", "PROC_REARWHEEL_1ST",
    "USE_MAXSP_LIMIT", "LOW_RIDER", "STREET_RACER", None, "SWINGING_CHASSIS", None, None, None,
)


def flag_names(value: int, table: tuple[str | None, ...]) -> list[str]:
    """Names of the set bits of ``value`` (unknown bits as ``bit<N>``)."""
    out = []
    for bit in range(32):
        if value >> bit & 1:
            name = table[bit] if bit < len(table) else None
            out.append(name or f"bit{bit}")
    return out


#: Record type -> fields.
KINDS: dict[str, tuple[tuple[str, str], ...]] = {
    "car": CAR_FIELDS, "bike": BIKE_FIELDS, "boat": BOAT_FIELDS, "flying": FLYING_FIELDS}
#: First character of a line -> record type.
PREFIX_KIND = {"!": "bike", "$": "flying", "%": "boat"}

_FLOAT_PREFIX = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")


@dataclass(frozen=True, slots=True)
class HandlingRec:
    """One record: ``name`` = handling id as written (``INFERNUS``), ``kind`` car|bike|boat|flying."""

    name: str
    kind: str
    line: int
    values: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Handling:
    """Parsed ``handling.cfg``: winning records by ``(lower name, kind)`` in file order."""

    records: dict[tuple[str, str], HandlingRec]
    anim_groups: int = 0
    duplicates: int = 0

    def get(self, name: str, kind: str = "car") -> HandlingRec | None:
        return self.records.get((name.lower(), kind))

    def __iter__(self):
        return iter(self.records.values())

    def __len__(self) -> int:
        return len(self.records)


#: Fields followed by ``%*c`` in the engine's format: one junk character after the number is skipped.
_SKIP_CHAR_AFTER = frozenset({("flying", "wind_mult")})


def _value(tok: str, typ: str, skip_char: bool):
    if typ == "c":
        if len(tok) != 1:
            raise ValueError(f"expected one character, got {tok!r}")
        return tok.upper()
    if typ == "x":
        return int(tok, 16)
    if typ == "i":
        return int(float(tok)) if "." in tok else int(tok)
    m = _FLOAT_PREFIX.match(tok)
    if not m or len(tok) - m.end() > (1 if skip_char else 0):
        raise ValueError(f"not a number: {tok!r}")
    return float(m.group(0))


def parse_handling(text: str, *, strict: bool = False, errors: list | None = None) -> Handling:
    """Parse ``handling.cfg``.

    Args:
        text: file contents (latin-1 decoded).
        strict: raise ``FormatError(kind="handling", offset=<line>)`` on a malformed line.
        errors: if given, ``(line, message)`` of skipped lines are appended.
    """
    recs: dict[tuple[str, str], HandlingRec] = {}
    groups = 0
    dups = 0
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith(";"):
            if line.rstrip().lower() == ";the end":
                break
            continue
        if line[0] == "^":
            groups += 1
            continue
        kind = PREFIX_KIND.get(line[0], "car")
        toks = (line[1:] if kind != "car" else line).split()
        try:
            fields = KINDS[kind]
            if len(toks) < 1 + len(fields):
                raise ValueError(f"handling: {kind} line has {len(toks) - 1} values, need {len(fields)}")
            vals = {name: _value(tok, typ, (kind, name) in _SKIP_CHAR_AFTER)
                    for (name, typ), tok in zip(fields, toks[1:])}
        except (ValueError, OverflowError) as e:
            if strict:
                raise FormatError("handling", n, str(e)) from None
            if errors is not None:
                errors.append((n, str(e)))
            continue
        key = (toks[0].lower(), kind)
        if key in recs:
            dups += 1
            del recs[key]
        recs[key] = HandlingRec(toks[0], kind, n, vals)
    return Handling(recs, groups, dups)
