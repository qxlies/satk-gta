"""``satk mta logs``: MTA:SA logs as rows (resource, file:line, level, message) with a hint per row.

Reads ``server.log`` (``[YYYY-MM-DD HH:MM:SS] ...``), the server console output (``[HH:MM:SS] ...``),
``clientscript.log`` and text copied from the in-game debug window (no timestamps). Script messages have the
form ``LEVEL: resource/file.lua:LINE: message [DUP xN]`` (``CScriptDebugging``); the resource loader and the
resource checker write their own lines (``Loading of resource 'x' failed``, ``Couldn't find file(s) ...``,
``x/file.lua(Line 5) [Client] f is deprecated ...``). Hints come from satk's rules and the MTA reference
(``Bad argument @ 'f'`` gets the signature of ``f``; ``attempt to call global 'x'`` gets the side of ``x`` or a
spelling suggestion).
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from pathlib import Path

__all__ = ["LogRow", "parse_lines", "read_tail", "hint_for", "LEVELS"]

LEVELS = ("error", "warning", "info", "log")
MAX_READ = 32 * 1024 * 1024

_TS = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}|\d{2}:\d{2}:\d{2})\]\s?")
_DUP = re.compile(r"\s*\[DUP x(\d+)\]\s*$")
_LEVEL = re.compile(r"^(SCRIPT ERROR|ERROR|WARNING|INFO|DEBUG)\s*:\s*")
_AT = re.compile(r"^(?P<file>(?:@?[\w.\-\[\]# ]+/)?[\w.\-/\\ ]+?\.(?:lua|luac|xml|map)|\[string \"[^\"]*\"\])"
                 r":(?P<line>\d+):\s*(?P<msg>.*)$")
_CHECKER = re.compile(r"^(?P<res>[\w.\-]+)/(?P<file>[\w.\-/ ]+)\(Line (?P<line>\d+)\) \[(?P<side>Client|Server)\] "
                      r"(?P<msg>.*)$")
_LOADING = re.compile(r"^(?:Loading script failed|Loading of resource '(?P<res>[^']+)' failed)\s*:?\s*(?P<rest>.*)$")
_RES_IN = re.compile(r"(?:for resource '?(?P<res>[\w.\-]+)'?|resource '(?P<res2>[\w.\-]+)')")


@dataclass(slots=True)
class LogRow:
    line: int
    time: str
    level: str          # error | warning | info | log
    resource: str
    at: str             # file:line inside the resource ("" when unknown)
    msg: str
    count: int = 1
    hint: str = ""


def read_tail(p: Path, max_bytes: int = MAX_READ) -> tuple[list[str], int, bool]:
    """Last ``max_bytes`` as ``(lines, first line number, truncated)``; first=0 when the number is unknown."""
    size = p.stat().st_size
    with open(p, "rb") as f:
        truncated = size > max_bytes
        if truncated:
            f.seek(size - max_bytes - 1)
            boundary = f.read(1) == b"\n"
            data = f.read(max_bytes)
            if not boundary:
                end = data.find(b"\n")
                data = data[end + 1:] if end >= 0 else b""
        else:
            data = f.read(max_bytes)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")
    if text.startswith("\ufeff"):
        text = text[1:]
    lines = text.splitlines()
    first = 1
    if truncated:
        first = 0         # absolute log line numbers require reading the discarded prefix
    return lines, first, truncated


def _split_at(rest: str) -> tuple[str, str, str]:
    """``resource``, ``file:line`` and the message of ``res/file.lua:12: msg``."""
    m = _AT.match(rest)
    if not m:
        return "", "", rest
    f = m.group("file").replace("\\", "/").lstrip("@")
    res = ""
    if f.startswith("[string"):
        return "", f"{f}:{m.group('line')}", m.group("msg")
    if "/" in f:
        res, f = f.split("/", 1)
    return res, f"{f}:{m.group('line')}", m.group("msg")


def parse_lines(lines: list[str], first: int = 1) -> list[LogRow]:
    rows: list[LogRow] = []
    for i, raw in enumerate(lines):
        s = raw.rstrip()
        if not s.strip():
            continue
        t = ""
        m = _TS.match(s)
        if m:
            t = m.group(1)
            s = s[m.end():]
        count = 1
        dm = _DUP.search(s)
        if dm:
            count = int(dm.group(1))
            s = s[:dm.start()]
        level = "log"
        lm = _LEVEL.match(s)
        if lm:
            word = lm.group(1)
            level = {"SCRIPT ERROR": "error", "ERROR": "error", "WARNING": "warning", "INFO": "info",
                     "DEBUG": "info"}[word]
            s = s[lm.end():]
        res = at = ""
        msg = s
        cm = _CHECKER.match(s)
        if cm:
            res = cm.group("res")
            at = f"{cm.group('file')}:{cm.group('line')}"
            msg = cm.group("msg")
            if level == "log":
                level = "warning"
        else:
            ld = _LOADING.match(s)
            if ld:
                level = "error"
                if ld.group("res"):
                    res = ld.group("res")
                    msg = s
                else:
                    r2, at2, m2 = _split_at(ld.group("rest"))
                    res, at, msg = r2, at2, "Loading script failed: " + m2
            else:
                res, at, msg = _split_at(s)
                if not res:
                    rm = _RES_IN.search(s)
                    if rm and (s.startswith(("Couldn't", "WARNING", "ERROR")) or level != "log"):
                        res = rm.group("res") or rm.group("res2") or ""
                    if s.startswith(("Couldn't find", "Couldn't parse")):
                        level = "error"
        rows.append(LogRow(line=first + i if first else 0, time=t, level=level, resource=res, at=at, msg=msg.strip(),
                           count=count))
    return rows


# --------------------------------------------------------------------------- hints

_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"attempt to index (?:global|field|local|upvalue) '?(\w*)'? \(a (nil|boolean) value\)"),
     "{1} is {2} here: check that it exists before using it (a failed create*/get* returns false or nil)"),
    (re.compile(r"attempt to (?:perform arithmetic on|concatenate|compare) .*nil"),
     "a value is nil: print it with iprint() before this line; a get* function may have returned false/nil"),
    (re.compile(r"attempt to concatenate .* \(a boolean value\)"),
     "a function returned false (failure): check its result before concatenating; tostring() shows it"),
    (re.compile(r"bad argument #(\d+) to '(\w+)' \(table expected, got (nil|boolean)\)"),
     "{2} got {3} instead of a table: the function that produced it failed"),
    (re.compile(r"Infinite/too long execution|Aborting; infinite running script"),
     "a loop ran too long (MTA aborts after a few seconds): add a break, spread work over setTimer or "
     "coroutines"),
    (re.compile(r"stack overflow"), "endless recursion: a function calls itself (or an event triggers itself)"),
    (re.compile(r"not encoded in UTF-8"), "save the script as UTF-8 (without changing its text)"),
    (re.compile(r"Couldn't find (?:file\(s\)|script\(s\)|map|config|html) (.+?) for resource"),
     "the file listed in meta.xml is missing or misspelled (Linux servers are case-sensitive): satk mta lint"),
    (re.compile(r"Couldn't parse meta file"), "meta.xml is not valid XML: satk mta lint shows the line"),
    (re.compile(r"Loading script failed: .*expected|Loading script failed: .*unexpected symbol|malformed number"),
     "a syntax error: satk mta lint <resource> shows it with a hint (Lua 5.1 rules)"),
    (re.compile(r"is not marked as remotely triggerable|not added clientside|not added serverside"),
     "add the event on the receiving side with addEvent(name, true)"),
    (re.compile(r"Server triggered clientside event (\S+), but event is not added clientside"),
     "the client has no addEvent('{1}', true) (or the client script failed to load first)"),
    (re.compile(r"Client \(.*\) triggered serverside event (\S+), but event is not added serverside"),
     "the server has no addEvent('{1}', true)"),
    (re.compile(r"Unsafe function was called"), "dofile/loadfile/require/getfenv and some os.* are disabled in MTA"),
    (re.compile(r"(\w+) is deprecated"), "satk mta lint lists every deprecated call with its replacement"),
    (re.compile(r"min_mta_version"), "raise <min_mta_version> in meta.xml to the version the message names"),
    (re.compile(r"requests some acl rights"), "the resource asks for ACL rights: 'aclrequest list <resource>' in "
                                              "the server console, then allow them"),
    (re.compile(r"Access denied @ '(\w+)'"), "the resource lacks the ACL right function.{1}: grant it in acl.xml "
                                             "or with <aclrequest> in meta.xml"),
    (re.compile(r"Expected (\w[\w-]*) at argument (\d+), got (none|nil|boolean)"),
     "argument {2} is missing or failed earlier: the value given is {3}"),
]
_BAD_ARG = re.compile(r"Bad argument @ '(\w+)'")
_CALL_GLOBAL = re.compile(r"attempt to call (?:global|field|method) '(\w+)' \(a nil value\)")


def hint_for(row: LogRow, ref) -> str:
    """A short fix hint for a row (``""`` when none applies)."""
    msg = row.msg
    m = _CALL_GLOBAL.search(msg)
    if m:
        name = m.group(1)
        if ref is not None and ref:
            sides = ref.sides(name)
            if sides and len(sides) == 1:
                return f"{name} is a {next(iter(sides))}-only function: call it from a script of that type"
            if not sides:
                near = difflib.get_close_matches(name, list(ref.funcs), n=2, cutoff=0.8)
                if near:
                    return f"{name} is not an MTA function: did you mean {', '.join(near)}?"
        return (f"{name} is nil: a typo, a script missing from meta.xml, a function defined later in the file, "
                "or an export of a resource that is not running")
    m = _BAD_ARG.search(msg)
    if m and ref is not None and ref:
        name = m.group(1)
        sref = (ref.funcs.get(name) or {})
        if sref:
            v = next(iter(sref.values()))
            if v.variants:
                args = ", ".join(f"{t} {n}" if not o else f"[{t} {n}]" for n, t, o in v.variants[0])
                return f"{name}({args})"
    for rx, text in _RULES:
        mm = rx.search(msg)
        if mm:
            out = text
            for k, g in enumerate(mm.groups(), 1):
                out = out.replace("{" + str(k) + "}", g or "")
            return out
    return ""
