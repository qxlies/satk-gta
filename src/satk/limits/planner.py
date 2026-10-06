"""Capacity report assembly and content-addressed output files."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from pathlib import Path

from ..core.envelope import clamp_limit, compact, table
from ..core.errors import SatkError
from ..core.paths import atomic_write, cfg, jpath, work
from .catalog import load
from .inventory import Measure, collect

COLS = ["limit", "need", "stock", "headroom", "risk", "how_to_raise", "unit", "basis"]


def _world(profile, mods, base):
    from ..modinspect.base import BaseGame
    from ..modinspect.modloader import mods_from_paths, read_folder
    from ..modinspect.world import World

    tokens = list(profile) if isinstance(profile, (list, tuple)) else [profile] if profile else []
    if any(not isinstance(s, str) or not s.strip() for s in tokens + list(mods or [])):
        raise SatkError("BAD_PARAMS", "profile and mods must contain nonempty names or paths",
                        hint="--profile installed, --profile vanilla, or --profile <mod folders>")
    profile_name = base
    if tokens and tokens[0] in cfg().profiles:
        profile_name = tokens.pop(0)
    game = BaseGame(profile_name)
    folder = read_folder(game.root) if cfg().canonical_profile(profile_name) != "vanilla" else None
    entries = list(folder.loaded) if folder else []
    inputs = []
    seen_paths = {e.path.resolve() for e in entries}
    for raw in tokens + list(mods or []):
        path = Path(raw)
        if not path.exists():
            relative = game.root / path
            if relative.exists():
                path = relative
            else:
                raise SatkError("NOT_FOUND", f"mod path does not exist: {raw}",
                                hint="--profile installed|vanilla or existing mod directories/ZIPs/IMGs")
        if not path.is_dir() and path.suffix.lower() not in (".img", ".zip"):
            raise SatkError("BAD_PARAMS", f"not a mod directory, ZIP or IMG: {raw}",
                            hint="pass the containing mod folder")
        if path.resolve() in seen_paths:
            continue
        seen_paths.add(path.resolve())
        inputs.append(str(path))
    extra = mods_from_paths(inputs)
    seen = {e.lname for e in entries}
    for e in extra:
        if e.lname in seen:
            raise SatkError("BAD_PARAMS", f"duplicate mod name: {e.name}", hint="use distinct mod folder names")
        entries.append(e)
        seen.add(e.lname)
    world = World(game, entries, file_ignored=folder.file_ignored if folder else None)
    if folder:
        world.warnings += folder.warnings
    unreadable = next((w for w in world.warnings if w.startswith(("MOD:", "BAD_IMG:", "UNREADABLE:", "ENCRYPTED:"))), None)
    if unreadable:
        world.close()
        raise SatkError("BAD_PARAMS", f"cannot plan unreadable mod content: {unreadable}",
                        hint="check or extract the mod into a readable directory, then retry")
    return world


def _risk(demand: Measure, stock: int | None) -> str:
    from ..modinspect.check import LOW_SLOTS

    if demand.need is None:
        return "runtime" if demand.basis == "runtime" else "unknown"
    if stock is None or demand.basis == "inventory":
        return "inventory"
    if demand.need > stock:
        return "exceeded"
    if demand.need == stock:
        return "at_stock"
    if demand.basis in ("lower_bound", "scenario"):
        return "runtime" if demand.basis == "lower_bound" else "estimate"
    return "low" if stock - demand.need < LOW_SLOTS else "ok"


def _write_once(path: Path, content: str) -> None:
    if path.exists():
        if path.read_text(encoding="utf-8") != content:
            raise SatkError("EXISTS", f"refusing to replace a different existing plan file: {jpath(path)}",
                            hint="keep the existing file and choose an isolated SATK_PATHS_WORK directory")
        return
    atomic_write(path, content)


def plan(*, profile=None, mods=None, base="installed", area=None, limit=20, cursor=None) -> dict:
    from ..crash.crashlist import load as load_crashes, source_label
    from .advice import declared_limits, exe_evidence, fragments, how_to_raise, installed_settings, known_crashes

    if area is not None:
        if len(area) != 3 or any(isinstance(v, bool) or not isinstance(v, (float, int)) or
                                 not math.isfinite(v) for v in area) or area[2] <= 0:
            raise SatkError("BAD_PARAMS", "area must be finite x y radius with radius > 0",
                            hint="--area 2495 -1666 150")
        area = [float(v) for v in area]
    page_size = clamp_limit(limit)
    if cursor is not None and (not isinstance(cursor, str) or len(cursor) > 10 or
                               not cursor.isascii() or not cursor.isdecimal()):
        raise SatkError("BAD_PARAMS", "invalid limits cursor", hint="pass the previous page's next cursor")
    offset = int(cursor or 0)
    limits = load()
    with _world(profile, mods, base) as world:
        demands, inventory = collect(world, area)
        inputs = {"profile": world.base.profile, "root": jpath(world.base.root),
                  "mods": [{"name": e.name, "path": jpath(e.path), "priority": e.priority} for e in world.mods],
                  "area": area}
        evidence = exe_evidence(world.base.root, limits)
        configured = installed_settings(world)
        warn = list(dict.fromkeys(world.warnings + world.base.warnings))
    entries, crash_meta = load_crashes()
    entries = {e.n: e for e in entries}
    crash_source = source_label(crash_meta)
    rows = []
    detail = {}
    risks = Counter()
    exceeded = []
    for lim in limits:
        demand = demands.get(lim.key, Measure(None, "runtime", "Peak demand depends on scripts, traffic and streaming; files alone cannot determine it."))
        risk = _risk(demand, lim.stock)
        risks[risk] += 1
        need = demand.need if demand.need is not None else "runtime" if demand.basis == "runtime" else "unknown"
        headroom = lim.stock - demand.need if lim.stock is not None and demand.need is not None and risk != "inventory" else "unknown"
        rows.append([lim.key, need, lim.stock if lim.stock is not None else "not a global pool", headroom,
                     risk, how_to_raise(lim), lim.unit, demand.basis])
        detail[lim.key] = {"title": lim.title, "source": lim.source, "count": demand.detail, "note": demand.note,
                           "advice": lim.advice,
                           "adjusters": {"fastman92": list(lim.f92) if lim.f92 else "no verified setting",
                                         "ola": ["SALIMITS", lim.ola] if lim.ola else "no verified setting"}}
        detail[lim.key]["configured"] = declared_limits(lim, demand, configured)
        if lim.ola_max:
            detail[lim.key]["ola_maximum"] = lim.ola_max
        if risk == "exceeded":
            exceeded.append(lim.key)
            crashes = known_crashes(lim, entries, crash_source)
            detail[lim.key]["known_crashes"] = crashes or "No matching signature is catalogued by satk crash known."
    rank = {v: i for i, v in enumerate(("exceeded", "at_stock", "low", "unknown", "ok", "estimate", "runtime", "inventory"))}
    rows.sort(key=lambda r: (rank[r[4]], r[0]))
    warn.append("RUNTIME: lower bounds and area scenarios are not peak-use measurements; live entities, scripts and concurrent streaming need runtime verification")
    summary = {"profile": inputs["profile"], "limits": len(limits), "risks": dict(sorted(risks.items())),
               "exceeded": exceeded, "model_sections": inventory["model_sections"],
               "runtime_verified": False}
    ini = fragments(limits, demands, configured)
    report = compact({"format": "satk.limits.plan/1", "inputs": inputs, "cols": COLS, "rows": rows,
                      "details": detail, "summary": summary, "inventory": inventory, "exe": evidence,
                      "configured_adjusters": configured, "warn": warn})
    encoded = json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    digest = hashlib.sha256((encoded + ini["fastman92"] + ini["open_limit_adjuster"]).encode("utf-8")).hexdigest()[:20]
    output = work("out", "limits", digest)
    names = {"report": "plan.json", "fastman92": "fastman92limitAdjuster_GTASA.ini",
             "open_limit_adjuster": "limit_adjuster_gta3vcsa.ini"}
    _write_once(output / names["report"], encoded)
    for key, content in ini.items():
        _write_once(output / names[key], content)
    page = rows[offset:offset + page_size]
    env = table(COLS, page, total=len(rows), next=str(offset + page_size) if offset + page_size < len(rows) else None,
                warn=warn)
    env.update(summary=summary, files={k: jpath(output / v) for k, v in names.items()},
               details=compact({r[0]: detail[r[0]] for r in page}), plan=digest)
    return env
