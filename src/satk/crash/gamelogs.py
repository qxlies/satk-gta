"""Logs of a single-player game folder: ``modloader.log``, ``scrlog.log`` and the CLEO log.

* ``modloader/modloader.log`` (Mod Loader 0.3.x): banner ``=== Mod Loader 0.3.7 ===``, ``Game
  version:``, plugin modules, ``Using profile named "..."``, quoted paths of mod files
  (``modloader\\<mod>\\...``), errors, and crash reports (:mod:`.sptext`).
* ``scrlog.log`` (SCRLog): executed script commands as ``[038B] REQUEST_MODEL 400``; a line
  ``script <name>`` (or a ``<name>:`` / ``[<name>]`` prefix) says which script runs. The last
  command and script are what the CrashInfo list keys its "By commands" / "By scripts" entries on;
  model IDs of ``REQUEST_MODEL``/``CREATE_*`` right before the crash are suspects.
* ``cleo.log`` (CLEO 4.4/5, lines ``DD/MM/YYYY hh:mm:ss.mmm text``): game version, plugins,
  custom scripts loaded/registered, errors (with opcodes ``[0AB1]`` and script names).

The parsers never fail on unknown lines; they return what they recognized (``n_lines`` tells how
much was read). Files are read with ``open_ro``; only the tail of a big log is read. Stdlib only.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .sptext import parse as parse_crashes

__all__ = ["LOG_FILES", "find_logs", "read_log", "detect_kind", "parse_modloader", "parse_scrlog", "parse_cleo",
           "parse_any", "MAX_LOG_BYTES"]

#: Where each log lives, relative to the game folder (first existing wins).
LOG_FILES = {
    "modloader": ("modloader/modloader.log",),
    "scrlog": ("scrlog.log", "modloader/scrlog.log", "scripts/scrlog.log", "cleo/scrlog.log"),
    "cleo": ("cleo.log", "cleo/cleo.log", "cleo/.cleo.log", ".cleo.log"),
}
MAX_LOG_BYTES = 8 << 20
_TAIL = {"scrlog": 2 << 20}
_ERR = re.compile(r"(?i)\b(error|errors|failed|failure|fail|cannot|can't|could not|couldn't|not found|missing|invalid|"
                  r"exception|crash(?:ed)?|unknown opcode|warning)\b")
_QUOTED_MOD = re.compile(r'(?i)"(?:[^"]*?[\\/])?modloader[\\/]([^\\/"]+)[\\/]([^"]*)"')
_TS_CLEO = re.compile(r"^\s*\d{1,2}/\d{1,2}/\d{2,4}\s+\d{1,2}:\d{2}:\d{2}(?:\.\d+)?\s+")
_TS_ANY = re.compile(r"^\s*(?:\[\s*[\d:.\s/-]+\]|\d{1,2}:\d{2}:\d{2}(?:\.\d+)?)\s*")
_SCR_CMD = re.compile(r"^(?P<pre>.*?)\[(?P<op>[0-9A-Fa-f]{4})\]\s*:?\s*(?P<text>.*)$")
_SCR_SCRIPT = re.compile(r"(?i)^\W*(?:script|thread)\s*[:=]?\s*['\"]?(?P<name>[^'\"\[\]:]{1,24}?)['\"]?\s*:?\s*$")
#: command name -> index of the model argument (REQUEST_MODEL 400, CREATE_CHAR 4 102 ...).
MODEL_ARG = {"REQUEST_MODEL": 0, "CREATE_CAR": 0, "CREATE_OBJECT": 0, "CREATE_OBJECT_NO_OFFSET": 0,
             "CREATE_PICKUP": 0, "CREATE_PICKUP_WITH_AMMO": 0, "CREATE_CHAR": 1, "CREATE_CHAR_INSIDE_CAR": 2,
             "CREATE_CHAR_AS_PASSENGER": 2, "MARK_MODEL_AS_NO_LONGER_NEEDED": 0, "HAS_MODEL_LOADED": 0}
MODEL_OPCODES = {"0247": "REQUEST_MODEL", "00A5": "CREATE_CAR", "0107": "CREATE_OBJECT",
                 "029B": "CREATE_OBJECT_NO_OFFSET", "0213": "CREATE_PICKUP", "032B": "CREATE_PICKUP_WITH_AMMO",
                 "009A": "CREATE_CHAR", "0129": "CREATE_CHAR_INSIDE_CAR", "01C8": "CREATE_CHAR_AS_PASSENGER",
                 "0249": "MARK_MODEL_AS_NO_LONGER_NEEDED", "0248": "HAS_MODEL_LOADED"}


def find_logs(game: str | os.PathLike) -> dict[str, Path]:
    """``{kind: path}`` of the logs present in a game folder."""
    g = Path(game)
    out: dict[str, Path] = {}
    for kind, rels in LOG_FILES.items():
        for rel in rels:
            p = g.joinpath(*rel.split("/"))
            if p.is_file():
                out[kind] = p
                break
    return out


def read_log(path: str | os.PathLike, kind: str | None = None) -> str:
    """Text of a log (UTF-8, else cp1252); big logs: only the last :data:`MAX_LOG_BYTES` (``scrlog``: 2 MiB)."""
    from ..core.paths import open_ro

    p = Path(path)
    size = p.stat().st_size
    limit = _TAIL.get(kind or "", MAX_LOG_BYTES)
    with open_ro(p) as f:
        if size > limit:
            f.seek(size - limit)
        raw = f.read(limit)
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", "replace")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", "replace")
    if size > limit:
        text = text.split("\n", 1)[-1]          # drop the cut first line
    return text


def detect_kind(name: str, text: str) -> str | None:
    """``modloader`` | ``scrlog`` | ``cleo`` | ``None`` from the file name, else the content."""
    low = name.lower()
    if low == "modloader.log":
        return "modloader"
    if "scrlog" in low:
        return "scrlog"
    if low in ("cleo.log", ".cleo.log"):
        return "cleo"
    head = text[:4096]
    if re.search(r"=+\s*Mod Loader\s+[\w.]+", head):
        return "modloader"
    lines = [ln for ln in text.splitlines()[:400] if ln.strip()]
    if lines and sum(1 for ln in lines if _TS_CLEO.match(ln)) >= 0.6 * len(lines) and re.search(r"(?i)cleo|script", head):
        return "cleo"
    if lines and sum(1 for ln in lines if _SCR_CMD.match(ln)) >= 0.4 * len(lines):
        return "scrlog"
    return None


def _errors(lines: list[str], *, skip: re.Pattern | None = None, limit: int = 6) -> tuple[list[str], int]:
    out: list[str] = []
    n = 0
    for ln in lines:
        s = ln.strip()
        if not s or (skip is not None and skip.search(s)):
            continue
        if _ERR.search(s):
            n += 1
            if len(out) < limit:
                out.append(s[:200])
    return out, n


def _tail(lines: list[str], n: int = 3) -> list[str]:
    return [ln.strip()[:200] for ln in lines if ln.strip()][-n:]


def _crashes(text: str) -> dict:
    cr = parse_crashes(text)
    if not cr:
        return {}
    first = cr[0]
    at = f"{first.module}+0x{first.off:x}" if first.module and first.off is not None else f"0x{first.addr:08x}"
    return {"crashes": len(cr), "crash": at}


# --------------------------------------------------------------------------- modloader.log


def parse_modloader(text: str) -> dict:
    lines = text.splitlines()
    out: dict = {"kind": "modloader", "n_lines": len(lines)}
    m = re.search(r"=+\s*Mod Loader\s+([\w.\-]+)\s*=+", text)
    if m:
        out["version"] = m.group(1)
    m = re.search(r"(?m)^\s*Game version:\s*(.+?)\s*$", text)
    if m:
        out["game"] = m.group(1)
    m = None
    for m in re.finditer(r'Using profile named "([^"]+)"', text):
        pass
    if m:
        out["profile"] = m.group(1)
    plugins = re.findall(r'Plugin module "[^"]+" loaded as ([\w.\-]+)', text)
    if plugins:
        out["plugins"] = len(plugins)
    m = re.search(r'CLEO library version (\d+) found', text)
    if m:
        out["cleo"] = m.group(1)
    mods: dict[str, int] = {}
    for ln in lines:
        for mm in _QUOTED_MOD.finditer(ln):
            name = mm.group(1)
            if name.startswith("."):
                continue
            mods[name] = mods.get(name, 0) + 1
    if mods:
        out["mods"] = sorted(mods, key=str.lower)
        out["mod_lines"] = sum(mods.values())
    crash_at = next((c.start for c in parse_crashes(text)), None)
    body = (text if crash_at is None else text[:crash_at]).splitlines()
    while body and (not body[-1].strip() or re.search(r"(?i)^\W*=+.*=+\W*$|exception|crash", body[-1])):
        body.pop()                                   # the banner of the crash report
    errs, n = _errors(body, skip=re.compile(r"(?i)^Removing imported model file"))
    if n:
        out["errors"] = errs
        out["n_errors"] = n
    out["last"] = _tail(body)
    out.update(_crashes(text))
    return out


# --------------------------------------------------------------------------- scrlog.log


def _script_of(pre: str) -> str | None:
    s = _TS_ANY.sub("", pre).strip()
    if not s:
        return None
    m = re.search(r"(?i)\b(?:script|thread)\s*[:=]?\s*['\"]?([^'\"\[\]:]{1,24}?)['\"]?\s*[:\]]?\s*$", s)
    if m:
        return m.group(1).strip()
    m = re.fullmatch(r"\[?\s*['\"]?([\w .\-]{1,24}?)['\"]?\s*\]?\s*:?", s)
    if m:
        return m.group(1).strip()
    return None


def _model_of(op: str, text: str) -> tuple[str, int] | None:
    t = text.strip()
    name = None
    m = re.match(r"([A-Z_][A-Z0-9_]{2,})\b\s*(.*)$", t)
    rest = t
    if m:
        name, rest = m.group(1), m.group(2)
    if name not in MODEL_ARG:
        name = MODEL_OPCODES.get(op.upper())
        if name is None:
            return None
    nums = re.findall(r"(?<![\w.$@])-?\d+(?![\w.])", rest)
    i = MODEL_ARG[name]
    if len(nums) <= i:
        return None
    v = int(nums[i])
    return (name, v) if 0 < v < 65536 else None


def parse_scrlog(text: str) -> dict:
    lines = text.splitlines()
    out: dict = {"kind": "scrlog", "n_lines": len(lines)}
    cmds: list[tuple[str | None, str, str]] = []     # (script, opcode, text)
    script: str | None = None
    for ln in lines:
        sm = _SCR_SCRIPT.match(ln)
        if sm and "[" not in ln:
            script = sm.group("name").strip()
            continue
        cm = _SCR_CMD.match(ln)
        if cm:
            who = _script_of(cm.group("pre")) or script
            cmds.append((who, cm.group("op").upper(), cm.group("text").strip()))
    out["commands"] = len(cmds)
    if not cmds:
        out["warn"] = "no script commands recognized ([XXXX] COMMAND lines)"
        out.update(_crashes(text))
        return out
    last_script, last_op, last_text = cmds[-1]
    out["last_command"] = f"[{last_op}] {last_text}".strip()
    out["last_opcodes"] = [op for _s, op, _t in cmds[::-1][:3]]
    if last_script:
        out["last_script"] = last_script
    seen: list[str] = []
    for s, _op, _t in cmds[::-1]:
        if s and s not in seen:
            seen.append(s)
        if len(seen) >= 6:
            break
    if seen:
        out["scripts"] = seen
    models: list[dict] = []
    for s, op, t in cmds[::-1][:40]:
        hit = _model_of(op, t)
        if hit and all(m["id"] != hit[1] for m in models):
            models.append({"id": hit[1], "command": hit[0], "script": s})
    if models:
        out["models"] = models[:5]
    out["recent"] = [f"{s + ': ' if s else ''}[{op}] {t}".strip()[:160] for s, op, t in cmds[-5:]]
    out.update(_crashes(text))
    return out


# --------------------------------------------------------------------------- cleo.log


def parse_cleo(text: str) -> dict:
    lines = [_TS_CLEO.sub("", ln) for ln in text.splitlines()]
    out: dict = {"kind": "cleo", "n_lines": len(lines)}
    joined = "\n".join(lines)
    m = re.search(r"(?i)\bCLEO\b[^\n]{0,40}?\bv?(\d+\.\d+(?:\.\d+)*(?:[\w.\-]*)?)", joined)
    if m:
        out["version"] = m.group(1)
    m = re.search(r"(?im)^\s*Started on game of version:\s*(.+?)\s*$", joined)
    if m:
        out["game"] = m.group(1)
    plugins = [p.replace("\\", "/").rsplit("/", 1)[-1] for p in re.findall(r"(?im)^\s*Loading plugin\s+(\S+)", joined)]
    if plugins:
        out["plugins"] = plugins
    scripts = [s.rstrip(".") for s in re.findall(r"(?im)^\s*Loading custom script\s+(.+?)\s*$", joined)]
    if scripts:
        out["scripts"] = scripts
    running: list[str] = []
    for ln in lines:
        rm = re.match(r"(?i)^\s*(Registering|Unregistering) custom script named\s+(.+?)\s*$", ln)
        if rm:
            name = rm.group(2)
            if rm.group(1).lower() == "registering":
                if name not in running:
                    running.append(name)
            elif name in running:
                running.remove(name)
    if running:
        out["running"] = running
    errs, n = _errors(lines, skip=re.compile(r"(?i)hardware acceleration|^Found sound device"))
    if n:
        out["errors"] = errs
        out["n_errors"] = n
        ops: list[str] = []
        names: list[str] = []
        for e in errs:
            for a, b in re.findall(r"\[([0-9A-Fa-f]{4})\]|(?i:opcode)\s+([0-9A-Fa-f]{4})\b", e):
                ops.append((a or b).upper())
            names += re.findall(r"(?i)script\s+['\"]?([\w .\-]+?\.cs\w*)['\"]?", e)
        if ops:
            out["error_opcodes"] = list(dict.fromkeys(ops))
        if names:
            out["error_scripts"] = list(dict.fromkeys(names))
    out["last"] = _tail(lines)
    out.update(_crashes(text))
    return out


def parse_any(path: str | os.PathLike, kind: str | None = None) -> dict:
    """Parse a log file of a known kind (detected when ``kind`` is ``None``)."""
    from ..core.errors import SatkError
    from ..core.paths import jpath

    p = Path(path)
    if not p.is_file():
        raise SatkError("NOT_FOUND", f"no such log: {jpath(p)}", hint="satk crash logs --game DIR")
    text = read_log(p, kind)
    k = kind or detect_kind(p.name, text)
    if k is None:
        raise SatkError("UNSUPPORTED", f"{p.name}: not a modloader.log, scrlog.log or CLEO log",
                        hint="give --kind modloader|scrlog|cleo")
    res = {"modloader": parse_modloader, "scrlog": parse_scrlog, "cleo": parse_cleo}[k](text)
    res["file"] = jpath(p)
    return res
