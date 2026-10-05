"""Dynamic object physics ``data/object.dat`` (``CObjectData::Initialise``), SPEC §4.2. Stdlib only.

One object per line, keyed by model name; commas count as spaces. ``;`` and ``#`` start comment lines.
**The engine stops reading at the first line that starts with** ``*``: in vanilla that is line 1232
(``*********melee weapons*************``), so the entries after it (``flowera`` ...) are never loaded
(:attr:`ObjectRec.loaded` is ``False`` for them).

Fields (:data:`FIELDS`; the first 13 after the name are required): mass, turn mass, air resistance,
elasticity, percent submerged, uproot limit, collision damage multiplier, collision damage effect
(0 none, 1 change model, 20 smash, 21 change then smash, 200 breakable, 202 breakable and removed),
special collision response (0 none, 1 lamppost, 2 small box, 3 big box, 4 fence part, 5 grenade,
6 swing door, 7 lock door, 8 hanging, 9 pool ball), camera avoid, causes explosion, FX type; optional
FX offset x/y/z, FX name, and breakable info (smash multiplier, break velocity x/y/z, velocity randomness,
gun break mode, sparks on impact). A later loaded line for the same model wins.

Example::

    objs = parse_object_dat(read_text(resolve_ci(root, "data/object.dat")))
    box = next(o for o in objs if o.name.lower() == "cardboardbox2")
    box.values["mass"], box.values["dmg_effect"], box.values.get("fx_name")   # 20.0 20 'explosion_crate'
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .rw import FormatError

__all__ = ["ObjectRec", "parse_object_dat", "FIELDS", "N_REQUIRED", "DMG_EFFECTS", "COL_RESPONSES"]

#: ``(name, type)`` after the model name; type ``f`` float, ``i`` int, ``s`` string.
FIELDS: tuple[tuple[str, str], ...] = (
    ("mass", "f"), ("turn_mass", "f"), ("air_res", "f"), ("elasticity", "f"), ("submerged", "f"),
    ("uproot", "f"), ("dmg_mult", "f"), ("dmg_effect", "i"), ("col_response", "i"), ("cam_avoid", "i"),
    ("explodes", "i"), ("fx_type", "i"),
    ("fx_x", "f"), ("fx_y", "f"), ("fx_z", "f"), ("fx_name", "s"),
    ("smash_mult", "f"), ("break_vx", "f"), ("break_vy", "f"), ("break_vz", "f"), ("break_rand", "f"),
    ("gun_break", "i"), ("sparks", "i"),
)
#: Values the engine requires (``sscanf(...) >= 13`` with the name).
N_REQUIRED = 12
DMG_EFFECTS = {0: "none", 1: "change_model", 20: "smash", 21: "change_then_smash", 200: "breakable",
               202: "breakable_remove"}
COL_RESPONSES = {0: "none", 1: "lamppost", 2: "smallbox", 3: "bigbox", 4: "fencepart", 5: "grenade",
                 6: "swingdoor", 7: "lockdoor", 8: "hanging", 9: "poolball"}


@dataclass(frozen=True, slots=True)
class ObjectRec:
    """One object line: ``values`` holds the fields present on the line (see :data:`FIELDS`)."""

    name: str
    line: int
    loaded: bool
    values: dict = field(default_factory=dict)


def _conv(tok: str, typ: str):
    if typ == "s":
        return tok
    if typ == "i":
        return int(float(tok)) if "." in tok else int(tok)
    return float(tok)


def parse_object_dat(text: str, *, strict: bool = False, errors: list | None = None) -> list[ObjectRec]:
    """Object lines in file order (also the ones after the ``*`` terminator, with ``loaded=False``).

    Args:
        text: file contents (latin-1 decoded).
        strict: raise ``FormatError(kind="object", offset=<line>)`` on a malformed line.
        errors: if given, ``(line, message)`` of skipped lines are appended.
    """
    out: list[ObjectRec] = []
    loaded = True
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line[0] in ";#":
            continue
        if line[0] == "*":
            loaded = False
            continue
        toks = line.replace(",", " ").split()
        vals: dict = {}
        try:
            for (name, typ), tok in zip(FIELDS, toks[1:]):
                try:
                    vals[name] = _conv(tok, typ)
                except ValueError:
                    break  # sscanf stops at the first unreadable value
            if len(vals) < N_REQUIRED:
                raise ValueError(f"object: {len(vals)} readable values, need {N_REQUIRED}")
        except (ValueError, OverflowError) as e:
            if strict:
                raise FormatError("object", n, str(e)) from None
            if errors is not None:
                errors.append((n, str(e)))
            continue
        out.append(ObjectRec(toks[0], n, loaded, vals))
    return out
