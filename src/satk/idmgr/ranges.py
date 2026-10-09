"""ID space facts of ``satk.idmgr`` (sources in the comments) and range helpers.

* :data:`MAX_ID` - the last model id of the stock engine: ``CModelInfo::NUM_MODEL_INFOS = 20000``
  (gta-reversed ``Models/ModelInfo.h``). A limit adjuster raises it: fastman92 LA's ID limit patch sets the
  number of DFF ids (:func:`f92_max_id` reads it from the ini when present).
* :data:`STORES` - model-info stores of the stock engine (same header): 212 vehicles, 278 peds, 51 weapons,
  14000 + 70 atomic objects (plain + damageable), 169 timed objects. More definitions than that need a
  limit adjuster (Open Limit Adjuster or fastman92 LA).
* :data:`ENGINE_RESERVED` - ids the engine uses without an IDE line (gta-reversed ``Enums/eModelID.h``).
* :data:`SAMP_RESERVED` - ids SA-MP 0.3.7+ takes besides ``samp.ide`` (client skins) and its object ranges.
* :data:`SAMP_DL` - SA-MP 0.3.DL / open.mp custom model ids: ``AddSimpleModel`` objects -1000..-30000
  (report 23 §8), ``AddCharModel`` skins 20001..30000 (open.mp docs say 20000-30000; 20000 is skipped).
"""

from __future__ import annotations

import re

from ..core.errors import SatkError

__all__ = ["KINDS", "KIND_SECTIONS", "MAX_ID", "DEFAULT_RANGE", "STORES", "LOW_SLOTS", "ENGINE_RESERVED", "KIND_RESERVED",
           "WEAPON_BLOCK", "kind_reserved", "SAMP_RESERVED",
           "SAMP_DL", "VANILLA_VEHICLES", "parse_ranges", "fmt_ranges", "fmt_spec", "iter_range", "f92_max_id"]

KINDS = ("vehicle", "ped", "weapon", "object")
#: IDE sections whose definitions a kind of model competes with for store slots.
KIND_SECTIONS = {"vehicle": ("cars",), "ped": ("peds",), "weapon": ("weap",), "object": ("objs", "tobj", "anim")}
MAX_ID = 19999
VANILLA_VEHICLES = (400, 611)
#: Default search range per kind (vehicles start at the stock vehicle range).
DEFAULT_RANGE = {"vehicle": (400, MAX_ID), "ped": (1, MAX_ID), "weapon": (1, MAX_ID), "object": (1, MAX_ID)}
#: Stock model-info store sizes: kind -> (store size, IDE sections counted against it).
STORES = {"vehicle": (212, ("cars",)), "ped": (278, ("peds",)), "weapon": (51, ("weap",)),
          "object": (14070, ("objs",))}
#: Fewer free store slots than this -> a STORE_LOW warning (vanilla: ped 2, weapon 1, object 25 left).
LOW_SLOTS = 10

#: (first, last, why) - taken for every kind of model.
ENGINE_RESERVED: tuple[tuple[int, int, str], ...] = (
    (2, 3, "engine: cutscene loader values MODEL_LOAD_THIS / MODEL_USE_PREV"),
    (290, 299, "engine: special characters SPECIAL01-10, swapped in at run time"),
    (300, 319, "engine: cutscene objects CUTOBJ01-20"),
    (374, 399, "engine: run-time models (TEMPCOL 374-381, CLOTHES01 384-393, hands 394-397)"),
)
#: (first, last, why) - taken for every kind of model except the one named (``KIND_RESERVED[kind]`` lists the
#: blocks a kind must stay out of): the weapon block holds the weapon models the engine and weapon.dat expect.
WEAPON_BLOCK = (321, 373, "the weapon model block 321-373 (weapon.dat, pickups): weapons only")
KIND_RESERVED: dict[str, tuple[tuple[int, int, str], ...]] = {
    "vehicle": (WEAPON_BLOCK,), "ped": (WEAPON_BLOCK,), "object": (WEAPON_BLOCK,), "weapon": ()}


def kind_reserved(kind: str) -> tuple[tuple[int, int, str], ...]:
    """Blocks a kind of model keeps out of (on top of :data:`ENGINE_RESERVED`)."""
    return KIND_RESERVED.get(str(kind or "").lower(), (WEAPON_BLOCK,))


#: (first, last, why) - taken when SA-MP is considered.
SAMP_RESERVED: tuple[tuple[int, int, str], ...] = (
    (1, 6, "SA-MP: client skins (0.3.7)"), (8, 8, "SA-MP: client skin"), (42, 42, "SA-MP: client skin"),
    (65, 65, "SA-MP: client skin"), (74, 74, "SA-MP: invalid skin"), (86, 86, "SA-MP: client skin"),
    (119, 119, "SA-MP: client skin"), (149, 149, "SA-MP: client skin"), (208, 208, "SA-MP: client skin"),
    (265, 273, "SA-MP: client skins"), (289, 289, "SA-MP: client skin"),
    (300, 311, "SA-MP: skins of samp.ide"),
    (11682, 11753, "SA-MP: objects (0.3.7)"), (18631, 19999, "SA-MP: objects (samp.img)"),
)
#: kind -> (first, last) of SA-MP DL / open.mp custom models (searched from ``first`` towards ``last``).
SAMP_DL = {"object": (-1000, -30000), "ped": (20001, 30000)}

_DASH = re.compile(r"^(-?\d+)(?:-(\d+))?$")


def parse_ranges(spec: str | None) -> list[tuple[int, int]]:
    """``"612-799,15000..15099,-1000..-1100,20500"`` -> ``[(612, 799), ...]`` (order kept; ``a..b`` may descend)."""
    out: list[tuple[int, int]] = []
    for part in [p for p in re.split(r"[,;\s]+", str(spec or "")) if p]:
        try:
            if ".." in part:
                a_s, b_s = part.split("..", 1)
                a, b = int(a_s), int(b_s)
            else:
                m = _DASH.match(part)
                if not m:
                    raise ValueError(part)
                a = int(m.group(1))
                b = int(m.group(2)) if m.group(2) is not None else a
                if b < a:
                    raise SatkError("BAD_PARAMS", f"range {part!r} goes down; write {a}..{b} for a descending range")
        except ValueError:
            raise SatkError("BAD_PARAMS", f"bad id range {part!r}",
                            hint="ranges look like 612-799, 15000..15099 or -1000..-1100; several with commas") from None
        out.append((a, b))
    return out


def iter_range(a: int, b: int):
    """Ids from ``a`` to ``b`` inclusive, in either direction."""
    return range(a, b + 1) if a <= b else range(a, b - 1, -1)


def _run(a: int, b: int) -> str:
    if a == b:
        return str(a)
    return f"{a}-{b}" if 0 <= a < b else f"{a}..{b}"


def fmt_spec(ranges: list[tuple[int, int]]) -> str:
    """``[(612, 799), (-1000, -1100)]`` -> ``"612-799,-1000..-1100"`` (the syntax :func:`parse_ranges` reads)."""
    return ",".join(_run(a, b) for a, b in ranges)


def fmt_ranges(ids) -> list[str]:
    """``[612, 613, 614, 700]`` -> ``["612-614", "700"]`` (input order; runs of step +1 or -1 merged)."""
    ids = list(ids)
    out: list[str] = []
    i = 0
    while i < len(ids):
        j = i
        if j + 1 < len(ids) and abs(ids[j + 1] - ids[j]) == 1:
            step = ids[j + 1] - ids[j]
            while j + 1 < len(ids) and ids[j + 1] - ids[j] == step:
                j += 1
        out.append(_run(ids[i], ids[j]))
        i = j + 1
    return out


_F92_DFF = re.compile(r"^\s*(?:file_type_)?dff\s*=\s*(\d+)", re.IGNORECASE | re.MULTILINE)


def f92_max_id(ini_text: str) -> int | None:
    """Last model id allowed by a fastman92 LA ini (``dff = N`` / ``FILE_TYPE_DFF = N`` -> ``N - 1``)."""
    m = _F92_DFF.search(ini_text)
    if not m:
        return None
    n = int(m.group(1))
    return n - 1 if n > 0 else None
