"""Sanity checks of 2dEffect entries (``satk.fx2d.check``). Stdlib only.

:func:`check_items` looks at decoded entries (see :mod:`satk.fx2d.schema`) and returns issues
``(where, severity, code, message)``; severity ``error`` = the game cannot use the entry as written,
``warn`` = it loads but probably does not do what was meant. Resources (:class:`Resources`):

* particle names must be effect systems of ``models/effects.fxp`` (matched without case, like
  ``FxManager_c::FindFxSystemBP``); an unknown name creates no effect;
* light corona/shadow textures are read from ``models/particle.txd`` (the plugin reader switches to that TXD);
  a missing one is not drawn.

Ranges are deliberately loose: they flag values far outside what the 2 203 vanilla lights use.
"""

from __future__ import annotations

import difflib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .schema import ATTRACTOR, COVER, FLASH, GAME_DFF_TYPES, SPECS, TYPE_IDS

__all__ = ["Resources", "Issue", "check_items", "fxp_names", "txd_names", "load_resources"]

#: (low, high, what vanilla uses) per light float.
_LIGHT_RANGES = {"corona_size": (0.0, 20.0, "0..9"), "far_clip": (0.0, 1500.0, "0.3..900"),
                 "range": (0.0, 100.0, "0..55"), "shadow_size": (0.0, 50.0, "0..20")}


@dataclass
class Resources:
    """Names the game resolves 2dEffect strings against (``None`` = not available, check skipped)."""

    fxp: set[str] | None = None
    fxp_path: str | None = None
    txd: set[str] | None = None
    txd_path: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class Issue:
    where: str
    sev: str
    code: str
    msg: str

    def row(self) -> list:
        return [self.where, self.sev, self.code, self.msg]


def fxp_names(text: str) -> set[str]:
    """Effect system names of an ``effects.fxp`` text (the first ``NAME:`` after each ``FX_SYSTEM_DATA:``)."""
    out: set[str] = set()
    want = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("FX_SYSTEM_DATA:"):
            want = True
        elif want and s.startswith("NAME:"):
            out.add(s[5:].strip().lower())
            want = False
    return out


def txd_names(blob: bytes) -> set[str]:
    from ..formats.txd import parse_txd

    return {t.name.lower() for t in parse_txd(blob).textures}


def _close(name: str, names: set[str]) -> str:
    hit = difflib.get_close_matches(name.lower(), sorted(names), 2, 0.6)
    return f" (did you mean {' or '.join(hit)}?)" if hit else ""


def _finite(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _len(v: list) -> float | None:
    if not all(_finite(x) for x in v):
        return None
    return math.sqrt(sum(x * x for x in v))


def check_items(items: Iterable[tuple[str, int | None, dict]], res: Resources,
                spheres: dict[int, tuple[float, float, float, float]] | None = None) -> list[Issue]:
    """Check ``(where, geometry, decoded entry)`` triples."""
    out: list[Issue] = []
    seen: dict[tuple, str] = {}

    def add(where: str, sev: str, code: str, msg: str) -> None:
        out.append(Issue(where, sev, code, msg))

    for where, geom, e in items:
        t = e.get("type")
        code = TYPE_IDS.get(t, t) if isinstance(t, str) else t
        pos = e.get("pos") or []
        if not all(_finite(x) for x in pos):
            add(where, "error", "NOT_FINITE", f"position {pos} is not a finite number")
        key = (geom, json.dumps({k: v for k, v in e.items() if k != "keep"}, sort_keys=True))
        if key in seen:      # the same position alone is fine: coronas of two colours on one lamp
            add(where, "warn", "DUPLICATE", f"identical to {seen[key]}")
        else:
            seen[key] = where
        if code not in GAME_DFF_TYPES:
            hint = " (sun glare belongs to the IDE 2dfx section)" if code == 4 else ""
            add(where, "error", "TYPE_NOT_READ", f"the game's DFF reader does not read type {t}{hint}")
            continue
        if "data" in e:
            spec = SPECS.get(code)
            sizes = " or ".join(map(str, spec.sizes)) if spec else "?"
            add(where, "error", "BAD_SIZE", f"{t} with {len(e['data']) // 2} data bytes: the game reads only "
                                            f"{sizes} and skips it")
            continue
        for k, v in e.items():
            if k in ("type", "pos", "keep", "data"):
                continue
            vals = v if isinstance(v, list) else [v]
            if any(isinstance(x, str) and x.lower().startswith("0x") for x in vals):
                add(where, "error", "NOT_FINITE", f"{k} is NaN or infinite ({v})")
        # road-sign positions are world coordinates (CEntity::CreateEffects translates the sign by pos only)
        if spheres is not None and geom in spheres and code != 7 and all(_finite(x) for x in pos):
            cx, cy, cz, r = spheres[geom]
            d = math.dist(pos, (cx, cy, cz))
            if d > 2 * r + 20:
                add(where, "warn", "FAR_FROM_MODEL", f"{d:.1f} m from the centre of a model of radius {r:.1f} m "
                                                     "(positions are relative to the object)")
        _CHECKS.get(code, lambda *_: None)(where, e, res, add)
    return out


# ============================================================================ per type


def _light(where: str, e: dict, res: Resources, add) -> None:
    flags = e.get("flags", [])
    for k in ("corona", "shadow"):
        name = e.get(k, "")
        if len(name) >= 24:
            add(where, "error", "NAME_TOO_LONG", f"{k} texture name {name!r} has no terminator (max 23 characters)")
    corona = e.get("corona", "")
    if not corona:
        if "without_corona" not in flags:
            add(where, "warn", "NO_TEXTURE", "no corona texture: the corona is not drawn")
    elif res.txd is not None and corona.lower() not in res.txd:
        add(where, "warn", "TEX_MISSING", f"corona texture {corona!r} is not in particle.txd: it is not drawn"
                                          + _close(corona, res.txd))
    shadow = e.get("shadow", "")
    if shadow and res.txd is not None and shadow.lower() not in res.txd:
        add(where, "warn", "TEX_MISSING", f"shadow texture {shadow!r} is not in particle.txd: no light shadow"
                                          + _close(shadow, res.txd))
    elif not shadow and _finite(e.get("shadow_size")) and e["shadow_size"] > 0:
        add(where, "warn", "NO_TEXTURE", "shadow_size > 0 but no shadow texture")
    if "at_day" not in flags and "at_night" not in flags and e.get("flash") != "random_when_wet":
        add(where, "warn", "NEVER_SHOWN", "neither at_day nor at_night: the light is never switched on")
    for k, (lo, hi, van) in _LIGHT_RANGES.items():
        v = e.get(k)
        if _finite(v) and not lo <= v <= hi:
            add(where, "warn", "RANGE", f"{k} {v} is outside {lo:g}..{hi:g} (vanilla lights use {van})")
    if _finite(e.get("far_clip")) and e["far_clip"] <= 0:
        add(where, "warn", "RANGE", "far_clip <= 0: the corona is never drawn")
    if e.get("flash") not in FLASH.values():
        add(where, "warn", "BAD_ENUM", f"unknown flash type {e.get('flash')!r} (0..13)")
    if isinstance(e.get("flare"), int) and e["flare"] > 2:
        add(where, "warn", "BAD_ENUM", f"flare {e['flare']} (0 none, 1 sun, 2 headlights)")
    if "check_direction" in flags and e.get("look_dir") == [0, 0, 0]:
        add(where, "warn", "LOOK_DIR_ZERO", "check_direction is set but look_dir is 0,0,0")


def _particle(where: str, e: dict, res: Resources, add) -> None:
    name = e.get("name", "")
    if not name:
        add(where, "warn", "EMPTY_NAME", "empty particle name: no effect is created")
    elif len(name) >= 24:
        add(where, "error", "NAME_TOO_LONG", f"particle name {name!r} has no terminator (max 23 characters)")
    elif res.fxp is not None and name.lower() not in res.fxp:
        add(where, "warn", "PARTICLE_MISSING", f"{name!r} is not an effect system of effects.fxp: no effect is "
                                               f"created{_close(name, res.fxp)}")


def _unit(where: str, k: str, v: list, add, tol: float = 0.01) -> None:
    n = _len(v)
    if n is not None and abs(n - 1) > tol:
        add(where, "warn", "DIR_NOT_UNIT", f"{k} has length {n:.3f} (the game expects a unit vector)")


def _attractor(where: str, e: dict, res: Resources, add) -> None:
    at = e.get("atype")
    if at not in ATTRACTOR.values() or at in ("undefined", "scripted"):
        add(where, "warn", "BAD_ENUM", f"attractor type {at!r} is not one peds use (atm, seat, stop, pizza, shelter, "
                                       "trigger_script, look_at, park, step)")
    for k in ("queue_dir", "use_dir", "fwd_dir"):
        _unit(where, k, e.get(k, []), add)
    script = e.get("script", "")
    if len(script) >= 8:
        add(where, "error", "NAME_TOO_LONG", f"script name {script!r} has no terminator (max 7 characters)")
    if at == "trigger_script" and script.lower() in ("", "none"):
        add(where, "warn", "SCRIPT_NONE", "trigger_script attractor without a script name")
    p = e.get("probability")
    if isinstance(p, int) and not 0 <= p <= 100:
        add(where, "warn", "RANGE", f"probability {p} is outside 0..100")


def _enex(where: str, e: dict, res: Resources, add) -> None:
    name = e.get("name", "")
    if len(name) >= 8:
        add(where, "error", "NAME_TOO_LONG", f"interior name {name!r} has no terminator (max 7 characters)")
    elif not name:
        add(where, "warn", "EMPTY_NAME", "no interior name")
    r = e.get("radius", [])
    if all(_finite(x) for x in r) and any(x <= 0 for x in r):
        add(where, "warn", "RANGE", f"radius {r}: the marker cannot be entered")
    for k in ("time_on", "time_off"):
        if isinstance(e.get(k), int) and e[k] > 24:
            add(where, "warn", "RANGE", f"{k} {e[k]} is not an hour (0..24)")


def _roadsign(where: str, e: dict, res: Resources, add) -> None:
    lines, chars = e.get("lines", 4), e.get("chars", 16)
    for i, line in enumerate(e.get("text", [])):
        if i >= lines and line:
            add(where, "warn", "TEXT_IGNORED", f"text line {i + 1} is not drawn (lines = {lines})")
        elif len(line) > chars:
            add(where, "warn", "TEXT_TOO_LONG", f"text line {i + 1} has {len(line)} characters, only {chars} are drawn")
    s = e.get("size", [])
    if all(_finite(x) for x in s) and any(x <= 0 for x in s):
        add(where, "warn", "RANGE", f"size {s}: the sign is invisible")


def _cover(where: str, e: dict, res: Resources, add) -> None:
    if e.get("usage") not in COVER.values():
        add(where, "warn", "BAD_ENUM",
            f"unknown cover usage {e.get('usage')!r} (low_cover, wall_to_left, wall_to_right)")
    _unit(where, "dir", e.get("dir", []), add, 0.02)


def _escalator(where: str, e: dict, res: Resources, add) -> None:
    if not isinstance(e.get("up"), bool):
        add(where, "warn", "BAD_ENUM", f"direction {e.get('up')!r} (0 = down, 1 = up)")


_CHECKS = {0: _light, 1: _particle, 3: _attractor, 6: _enex, 7: _roadsign, 9: _cover, 10: _escalator}


def load_resources(fxp: Path | None, txd: Path | None, read) -> Resources:
    """Read the name sets (``read(path) -> bytes`` opens game files read-only)."""
    res = Resources()
    if fxp is not None:
        res.fxp = fxp_names(read(fxp).decode("latin-1"))
        res.fxp_path = str(fxp)
    if txd is not None:
        res.txd = txd_names(read(txd))
        res.txd_path = str(txd)
    return res
