"""Advice for a crash report: known entry with solution, suspects, culprit, game logs.

:func:`advise` adds these keys to the envelope of ``satk crash analyze`` (each one line):

* ``known`` - matching CrashInfo entries (``#n head (how): title``) or "not in the list";
* ``solution`` - the solution text of the best entry (the variant whose backtrace address is on
  the stack, when the entry has variants), shortened, with the list date;
* ``suspects`` - model IDs from registers/SCRLog attributed to mods (:mod:`.culprit`);
* ``culprit`` - the mod (or the missing model) the evidence points at;
* ``logs`` - one-line digest of the game folder's modloader.log, scrlog.log and CLEO log.

Inputs are plain facts (addresses, registers, a game folder), so dumps, MTA logs and
single-player logs share it. Stdlib only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import crashlist
from .culprit import Candidate, Resolver, attribute, reg_candidates

__all__ = ["Facts", "advise", "SOLUTION_MAX"]

SOLUTION_MAX = 360
_HINT_REG = re.compile(r"\b(EAX|EBX|ECX|EDX|ESI|EDI)\b")


@dataclass
class Facts:
    ip: int | None = None                 # absolute crash address
    module: str | None = None             # module of the crash site (base name)
    stack: list[int] = field(default_factory=list)   # absolute frame addresses below the crash site
    regs: dict[str, int] = field(default_factory=dict)
    game: Path | None = None              # game folder (logs, modloader)
    profile: str | None = None            # index profile for model attribution


def _short(text: str, n: int = SOLUTION_MAX) -> str:
    t = " ".join(text.split())
    return t if len(t) <= n else t[:n - 1].rsplit(" ", 1)[0] + " ..."


def _query(m: crashlist.Match) -> str:
    e = m.entry
    if m.how in ("ip", "ip~bt", "stack") and m.via:
        return m.via
    if e.commands:
        return e.commands[0]
    if e.script:
        return f"script:{e.script}"
    if e.modules:
        return e.modules[0]
    return e.head[:40]


def _logs(game: Path) -> tuple[dict, list[str]]:
    from .gamelogs import find_logs, parse_cleo, parse_modloader, parse_scrlog, read_log

    out: dict = {}
    warns: list[str] = []
    for kind, p in find_logs(game).items():
        try:
            text = read_log(p, kind)
        except OSError as e:
            warns.append(f"NOT_FOUND: cannot read {p.name}: {e}")
            continue
        out[kind] = {"modloader": parse_modloader, "scrlog": parse_scrlog, "cleo": parse_cleo}[kind](text)
    return out, warns


def _logs_line(logs: dict) -> str | None:
    parts = []
    ml = logs.get("modloader")
    if ml:
        s = f"modloader {ml.get('version', '?')}: {len(ml.get('mods', []))} mods in the log"
        if ml.get("n_errors"):
            s += f", errors {ml['n_errors']}"
        parts.append(s)
    sc = logs.get("scrlog")
    if sc:
        if sc.get("last_command"):
            who = f"script {sc['last_script']}, " if sc.get("last_script") else ""
            parts.append(f"scrlog: {who}last {sc['last_command'][:60]}")
        else:
            parts.append("scrlog: no commands recognized")
    cl = logs.get("cleo")
    if cl:
        s = f"cleo: {len(cl.get('running', cl.get('scripts', [])))} scripts"
        if cl.get("n_errors"):
            s += f", errors {cl['n_errors']}"
        parts.append(s)
    return "; ".join(parts) or None


def advise(env: dict, facts: Facts) -> list[str]:
    """Add ``known``/``solution``/``suspects``/``culprit``/``logs`` to ``env``; returns warnings."""
    warns: list[str] = []
    logs: dict = {}
    if facts.game is not None:
        logs, w = _logs(facts.game)
        warns += w
    sc, cl = logs.get("scrlog") or {}, logs.get("cleo") or {}
    # [0001] WAIT as the last command says nothing about the crash (the list's own entry says so)
    commands = [op for op in list(sc.get("last_opcodes", []))[:1] + list(cl.get("error_opcodes", []))
                if op != "0001"]
    scripts = ([sc["last_script"]] if sc.get("last_script") else []) + [
        s.rsplit(".", 1)[0] for s in cl.get("error_scripts", [])]
    if facts.ip is None and not facts.stack and not facts.module and not commands and not scripts:
        return warns
    try:
        matches = crashlist.match(ip=facts.ip, module=facts.module, stack=facts.stack, commands=commands,
                                  scripts=scripts)
        _entries, meta = crashlist.load()
    except Exception as e:  # noqa: BLE001 - the data file is optional for a report
        warns.append(f"NOT_READY: CrashInfo list unavailable ({e})")
        matches, meta = [], {}
    label = crashlist.source_label(meta) if meta else "CrashInfo list"
    if matches:
        shown = matches[:1] + [m for m in matches[1:2] if m.how in ("ip", "ip~bt", "stack")
                               or matches[0].how not in ("ip", "ip~bt")]
        known = [f"#{m.entry.n} {m.entry.head[:40]} ({m.how}): {_short(m.entry.title(m.variant) or '-', 110)}"
                 for m in shown]
        if facts.ip is not None and not any(m.how in ("ip", "ip~bt") for m in shown):
            known.insert(0, f"0x{facts.ip:08X} itself is not in the list")
        env["known"] = known
        top = next((m for m in shown if m.entry.solution(m.variant)), None)
        if top is not None:
            env["solution"] = f"{_short(top.entry.solution(top.variant))} [{label}; satk crash known {_query(top)}]"
    elif facts.ip is not None:
        env["known"] = f"none: 0x{facts.ip:08X} is not in the {label}"
    else:
        env["known"] = f"none: no entry of the {label} matches"
    # suspects: registers named by the matching entry, SCRLog model commands, other registers
    hinted: list[str] = []
    for m in matches[:2]:
        if m.how not in ("ip", "ip~bt"):
            continue
        for _k, _v, t in m.entry.fields:
            for r in _HINT_REG.findall(t):
                if r.lower() not in hinted:
                    hinted.append(r.lower())
    regs = reg_candidates(facts.regs, hinted)
    logged = [Candidate(int(mm["id"]), f"scrlog {mm['command']} {mm['id']}"
                        + (f" (script {mm['script']})" if mm.get("script") else ""), "log")
              for mm in sc.get("models", [])]                # most recent first
    cands = [c for c in regs if c.strength == "hint"] + logged + [c for c in regs if c.strength != "hint"]
    culprit = None
    if cands and (facts.game is not None or facts.profile is not None or hinted):
        res = Resolver(facts.game, facts.profile)
        warns += res.warn
        if res.usable:
            lines, culprit = attribute(cands, res)
            if lines:
                env["suspects"] = lines
    if culprit is None:
        for m in matches:
            if m.how == "script":
                mod = (m.entry.texts("mod") or [None])[0]
                culprit = f"script {m.entry.script}: {_short(mod, 80) if mod else 'see the entry'} [{label}]"
                break
    if culprit is None and sc.get("last_script") and any(m.how == "command" for m in matches):
        culprit = f"script {sc['last_script']} (last command in scrlog.log)"
    if culprit:
        env["culprit"] = culprit
    line = _logs_line(logs)
    if line:
        env["logs"] = line
    return warns
