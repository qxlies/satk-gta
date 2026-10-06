"""Read-only executable evidence, installed settings, and separate adjuster suggestions."""

from __future__ import annotations

import configparser
import math
import struct
from collections import defaultdict

from ..core.errors import SatkError
from ..core.paths import open_ro
from .catalog import Limit


def exe_evidence(root, limits: list[Limit]) -> dict:
    from ..formats.pe import image_base, pe_sections, va_to_off
    from ..game.exe import identify, read_exe

    path = root / "gta_sa.exe"
    if not path.is_file():
        return {"status": "unavailable", "note": "Stock reference remains the curated KB; no executable was read."}
    try:
        raw = read_exe(path)
        identity = identify(raw)
        sections = pe_sections(raw)
        base = image_base(raw)
    except (OSError, ValueError, SatkError) as exc:
        return {"status": "unreadable", "note": str(exc)}
    checks = {}
    for lim in limits:
        operand = lim.source.get("operand")
        if not operand:
            continue
        va = int(operand["address"], 16)
        off = va_to_off(sections, va, base)
        fmt = {"u8": "<B", "u16": "<H", "u32": "<I"}[operand["type"]]
        size = struct.calcsize(fmt)
        if off is None or off + size > len(raw):
            checks[lim.key] = {**operand, "status": "unmapped"}
        else:
            value = struct.unpack_from(fmt, raw, off)[0]
            checks[lim.key] = {**operand, "observed": value,
                               "status": "matches" if value == operand["expected"] else "differs"}
    return {"identity": identity, "image_base": f"0x{base:08X}", "checks": checks,
            "note": "Observed bytes do not prove runtime ASI hooks. Stock comparisons always use the 1.0 US KB values."}


def installed_settings(world) -> list[dict]:
    """Report known config files as declarations, never as proof of active patches."""
    names = {"fastman92limitadjuster_gtasa.ini": "fastman92", "limit_adjuster_gta3vcsa.ini": "open_limit_adjuster",
             "iii.vc.sa.limitadjuster.ini": "open_limit_adjuster"}
    files = []
    for name, adjuster in names.items():
        for prefix in ("", "scripts/", "plugins/"):
            path = world.base.file(prefix + name)
            if path and path.is_file() and path.stat().st_size <= 1024 * 1024:
                with open_ro(path) as stream:
                    files.append((adjuster, prefix + name, stream.read().decode("utf-8-sig", errors="replace")))
    for i, src in enumerate(world.sources):
        for f in src.files:
            if f.lname in names:
                files.append((names[f.lname], f"{world.label(i)}/{f.rel}", f.read(1024 * 1024).decode("utf-8-sig", errors="replace")))
    out = []
    for adjuster, origin, text in files:
        parser = configparser.ConfigParser(interpolation=None, strict=False, inline_comment_prefixes=(";", "#"))
        try:
            parser.read_string(text)
            entries = {s: dict(parser.items(s)) for s in parser.sections()}
            out.append({"adjuster": adjuster, "file": origin, "declared": entries, "runtime_verified": False})
        except configparser.Error:
            out.append({"adjuster": adjuster, "file": origin, "status": "unreadable", "runtime_verified": False})
    return out


def how_to_raise(lim: Limit) -> str:
    choices = []
    if lim.f92:
        choices.append(f"fastman92 [{lim.f92[0]}] {lim.f92[1]}")
    if lim.ola:
        choices.append(f"OLA [SALIMITS] {lim.ola}")
        if lim.ola_max:
            choices[-1] += f" (maximum {lim.ola_max} {lim.unit})"
    return "; ".join(choices) if choices else lim.advice


def declared_limits(lim: Limit, demand, settings: list[dict]) -> list[dict]:
    """Compare demand with declared config values without treating an INI as a loaded patch."""
    out = []
    for setting in settings:
        option = lim.f92 if setting["adjuster"] == "fastman92" else ("SALIMITS", lim.ola) if lim.ola else None
        if not option:
            continue
        section = next((v for k, v in setting.get("declared", {}).items() if k.casefold() == option[0].casefold()), {})
        raw = section.get(option[1].lower())
        if raw is None:
            continue
        row = {"adjuster": setting["adjuster"], "file": setting["file"], "value": raw,
               "runtime_verified": False}
        if raw.isascii() and raw.isdecimal():
            value = int(raw)
            if lim.key == "streaming.memory":
                value *= 1024 * 1024
            if setting["adjuster"] == "open_limit_adjuster" and lim.ola_max:
                value = min(value, lim.ola_max)
            row["capacity"] = value
            if demand.need is not None:
                row["headroom"] = value - demand.need
                row["comparison"] = "exceeded" if demand.need > value else "fits_measured_demand"
        else:
            row["comparison"] = "dynamic" if raw.lower() == "unlimited" else "unknown"
        out.append(row)
    return out


def fragments(limits: list[Limit], demands: dict, settings: list[dict] | None = None) -> dict[str, str]:
    """Only measured excesses become assignments; combine shared settings using their maximum."""
    configs = {"fastman92": defaultdict(dict), "open_limit_adjuster": defaultdict(dict)}
    notes = {k: [] for k in configs}
    for lim in limits:
        demand = demands.get(lim.key)
        if demand is None or demand.need is None or lim.stock is None or demand.need <= lim.stock:
            continue
        if demand.basis == "inventory":
            continue
        # Ten percent spare capacity, in slots; the memory options use MiB, not bytes.
        suggested = (demand.need * 110 + 99) // 100
        if lim.key == "streaming.memory":
            suggested = math.ceil(suggested / (1024 * 1024))
        for name in configs:
            option = lim.f92 if name == "fastman92" else ("SALIMITS", lim.ola) if lim.ola else None
            if option:
                declared = [s for s in declared_limits(lim, demand, settings or []) if s["adjuster"] == name]
                if any("capacity" not in s for s in declared):
                    notes[name].append(f"; {lim.key}: existing symbolic declaration needs review; no numeric override is suggested.")
                    continue
                target = max([suggested] + [math.ceil(s["capacity"] / (1024 * 1024))
                                            if lim.key == "streaming.memory" else s["capacity"] for s in declared])
                if name == "open_limit_adjuster" and lim.ola_max:
                    if demand.need > lim.ola_max:
                        notes[name].append(f"; {lim.key}: need exceeds OLA maximum {lim.ola_max} {lim.unit}; no sufficient setting.")
                        continue
                    maximum = lim.ola_max // (1024 * 1024) if lim.key == "streaming.memory" else lim.ola_max
                    target = min(target, maximum)
                section, key = option
                configs[name][section][key] = max(configs[name][section].get(key, 0), target)
                notes[name].append(f"; {lim.key}: need {demand.need} {lim.unit}, stock {lim.stock}; {demand.basis}.")
            else:
                notes[name].append(f"; {lim.key}: no verified setting here. {lim.advice}")
    out = {}
    for name, sections in configs.items():
        if name == "fastman92" and "ID LIMITS" in sections:
            sections["ID LIMITS"]["Apply limits"] = 1
        lines = ["; Generated by satk limits plan for GTA SA 1.0 US.",
                 "; Review this fragment against the documentation of your installed adjuster version.",
                 "; Use one adjuster for each limit: overlapping fastman92/OLA hooks can crash.",
                 "; Values target 10 percent spare capacity, bounded by known adjuster maxima; memory values are MiB.",
                 "; Unknown runtime demand is not assigned a guessed value."]
        lines.extend(notes[name])
        if not sections:
            lines.append("; No automatic assignments for this plan.")
        for section in sorted(sections):
            lines += ["", f"[{section}]"]
            lines += [f"{key} = {value}" for key, value in sorted(sections[section].items())]
        out[name] = "\n".join(lines) + "\n"
    return out


def known_crashes(lim: Limit, entries: dict, source: str) -> list[dict]:
    out = []
    for n in lim.crashes:
        entry = entries.get(n)
        if entry is not None:
            out.append({"n": n, "addresses": [f"0x{a:08X}" for a in entry.addrs], "title": entry.title(),
                        "solution": entry.solution(), "source": source,
                        "lookup": "satk crash known " + (f"0x{entry.addrs[0]:08X}" if entry.addrs else str(n))})
    return out
