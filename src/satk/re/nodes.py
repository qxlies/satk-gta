"""Frame-name tables of gta_sa.exe 1.0 US: which frame names the engine looks up per vehicle type and for peds.

``CVehicleModelInfo::ms_vehicleDescs`` (0x8A7740) holds 12 pointers, one per vehicle type in engine order
(:data:`VEHICLE_TYPES`); each points at a list of 12-byte records ``{char* name; int id; uint32 flags}``
ended by a null name. ``CPedModelInfo::m_pPedIds`` (0x8A6268) is the ped list in the same layout. The
engine finds a clump's frames by these names when it sets the model up, so a model that misses a name
loses that part (no wheel, no door, no seat position).

Rows carry the name, the id the engine stores (a node number for parts, a position index for
dummies, 0 for extras), the raw flags and short flag words (:data:`FLAG_WORDS`, our own vocabulary
for the bits). With the knowledge base (``satk kb build``) the node and position numbers are also
named after the gta-reversed enums (``CAR_WHEEL_LF``, ``DUMMY_SEAT_FRONT``). Only names and numbers
are read; nothing of the executable is copied. Standard library only.
"""

from __future__ import annotations

import struct
from pathlib import Path

from ..core.envelope import table
from ..core.errors import SatkError
from ..core.paths import jpath

__all__ = ["VEHICLE_TYPES", "DESCS_VA", "PED_IDS_VA", "FLAG_WORDS", "read_tables", "nodes", "default_exe"]

#: eVehicleType order = index into ms_vehicleDescs.
VEHICLE_TYPES = ("automobile", "mtruck", "quad", "heli", "plane", "boat", "train", "fheli", "fplane", "bike",
                 "bmx", "trailer")
DESCS_VA = 0x8A7740
PED_IDS_VA = 0x8A6268
_MAX_ROWS = 128
#: Bit -> short word (vehicle component flags as the engine tests them).
FLAG_WORDS = {
    0x1: "struct", 0x2: "damageable", 0x4: "wheel", 0x8: "dummy", 0x10: "door", 0x20: "left", 0x40: "right",
    0x80: "front", 0x100: "rear", 0x200: "extra", 0x400: "alpha", 0x800: "glass", 0x1000: "cull",
    0x2000: "rear_door", 0x4000: "front_door", 0x8000: "swinging", 0x10000: "main_wheel", 0x20000: "upgrade",
    0x40000: "no_reflections", 0x100000: "bogie_front", 0x200000: "bogie_rear", 0x400000: "render_always",
}
#: Node enums of gta-reversed per table (names only, read from the knowledge base when it is built).
NODE_ENUMS = {"automobile": "eCarNodes", "mtruck": "eMonsterTruckNodes", "quad": "eQuadBikeNodes",
              "heli": "eHeliNodes", "plane": "ePlaneNodes", "boat": "eBoatNodes", "train": "eTrainNodes",
              "bike": "eBikeNodes", "bmx": "eBmxNodes", "trailer": "eTrailerNodes", "ped": "ePedNode"}
DUMMY_ENUM = "eVehicleDummy"


def default_exe() -> Path:
    """The clean copy's gta_sa.exe, else the configured game's (as ``satk re build`` picks it)."""
    from ..core.paths import cfg

    c = cfg()
    exe = c.paths.game / "gta_sa.exe"
    gr = c.paths.get("game_root")
    if not exe.is_file() and gr is not None and (gr / "gta_sa.exe").is_file():
        exe = gr / "gta_sa.exe"
    return exe


def _open(exe: str | None):
    from ..core.detect import identify_exe
    from .pe import PeError, PeImage

    p = Path(exe) if exe else default_exe()
    if not p.is_file():
        raise SatkError("NOT_FOUND", f"gta_sa.exe not found: {jpath(p)}", hint="satk game verify, or --exe <gta_sa.exe>")
    try:
        info = identify_exe(p)
    except OSError as e:
        raise SatkError("NOT_FOUND", f"cannot read {jpath(p)}: {e}") from None
    if info.get("layout") != "1.0us":
        raise SatkError("UNSUPPORTED", f"{p.name} is {info.get('variant')}; the tables are read at 1.0 US addresses",
                        hint="use the clean 1.0 US copy (satk game verify) or --exe <1.0 US gta_sa.exe>")
    try:
        return PeImage.open(p), p
    except PeError as e:
        raise SatkError("UNSUPPORTED", f"{jpath(p)} is not a PE image: {e}") from None


def _cstr(img, va: int) -> str | None:
    b = img.read(va, 64)
    end = b.find(b"\0")
    if end <= 0:
        return None
    s = b[:end]
    return s.decode("latin-1") if all(32 < c < 127 for c in s) else None


def _records(img, va: int) -> list[tuple[str, int, int]]:
    """``[(name, id, flags)]`` from ``va`` up to the null-name end record."""
    out: list[tuple[str, int, int]] = []
    for k in range(_MAX_ROWS):
        raw = img.read(va + 12 * k, 12)
        if len(raw) != 12:
            break
        p_name, ident, flags = struct.unpack("<IiI", raw)
        if p_name == 0:
            break
        name = _cstr(img, p_name)
        if name is None:
            raise SatkError("UNSUPPORTED", f"no frame name at 0x{p_name:X} (table 0x{va:X}, row {k}): not the 1.0 US "
                                           "layout?")
        out.append((name, ident, flags))
    return out


def read_tables(img) -> dict[str, tuple[int, list[tuple[str, int, int]]]]:
    """``{type: (table address, [(name, id, flags)])}`` for the 12 vehicle types and ``ped``."""
    out: dict[str, tuple[int, list[tuple[str, int, int]]]] = {}
    for i, t in enumerate(VEHICLE_TYPES):
        va = img.u32(DESCS_VA + 4 * i)
        if not va:
            raise SatkError("UNSUPPORTED", f"ms_vehicleDescs[{i}] is empty: not the 1.0 US layout?")
        out[t] = (va, _records(img, va))
    out["ped"] = (PED_IDS_VA, _records(img, PED_IDS_VA))
    return out


def _words(flags: int) -> str:
    return "|".join(w for b, w in FLAG_WORDS.items() if flags & b) or "-"


def _role(kind: str, flags: int) -> str:
    if kind == "ped":
        return "node"
    if flags & 0x200:
        return "extra"
    if flags & 0x8:
        return "dummy"
    return "part"


def _enum_names(owner: str) -> dict[int, str]:
    """``{value: member}`` of a gta-reversed enum from the knowledge base; ``{}`` without it."""
    try:
        from ..kb.query import enum_members

        return {v: n for n, v in enum_members(owner, source="gta-reversed") if v is not None}
    except SatkError:
        return {}


def nodes(kind: str | None = None, exe: str | None = None, limit: int = 100) -> dict:
    """Overview of the 13 tables, or the rows of one (``kind`` = a vehicle type or ``ped``)."""
    img, path = _open(exe)
    tables = read_tables(img)
    if kind is None:
        rows = []
        first: dict[int, str] = {}
        for t, (va, recs) in tables.items():
            same = first.setdefault(va, t)
            roles = [_role(t, f) for _n, _i, f in recs]
            rows.append([t, f"0x{va:X}", len(recs), roles.count("part") + roles.count("node"), roles.count("dummy"),
                         roles.count("extra"), None if same == t else same])
        env = table(["type", "table", "names", "parts", "dummies", "extras", "same_as"], rows)
        env.update({"vehicle_tables": len(VEHICLE_TYPES), "exe": jpath(path),
                    "hint": "rows of one table: satk re nodes --type automobile (or bike, heli, ped, ...)"})
        return env
    if kind not in tables:
        raise SatkError("BAD_PARAMS", f"unknown type {kind!r}", did_you_mean=[*VEHICLE_TYPES, "ped"])
    va, recs = tables[kind]
    parts = _enum_names(NODE_ENUMS[kind]) if kind in NODE_ENUMS else {}
    dummies = _enum_names(DUMMY_ENUM) if kind != "ped" else {}
    rows = []
    for name, ident, flags in recs:
        role = _role(kind, flags)
        node = dummies.get(ident) if role == "dummy" else parts.get(ident) if role in ("part", "node") else None
        rows.append([name, ident, role, f"0x{flags:X}", _words(flags), node])
    env = table(["name", "id", "role", "flags", "flag_words", "node"], rows[:max(1, limit)], total=len(rows))
    env.update({"type": kind, "table": f"0x{va:X}", "exe": jpath(path)})
    if parts or dummies:
        env["named_by"] = "knowledge base (gta-reversed enums)"
    elif kind in NODE_ENUMS:
        env["warn"] = ["NO_KB: node names need the knowledge base (satk kb build); ids and flags are complete"]
    return env
