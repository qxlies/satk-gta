"""The CrashInfo crash list: known crash addresses of GTA SA 1.0 US with problem and solution.

Data: ``data/crashlist/gta-sa-10us-en.txt`` is ``Lists/GTA-SA-10US/EN-CrashList.txt`` of
JuniorDjjr/CrashInfo (MIT, notice ``data/notices/crashinfo.txt``), stored byte for byte;
provenance in ``data/crashlist/source.json``. The text is free-form, so it is parsed here:

* the first part has entries ``Error: 0x00456809`` (one or more crash addresses; addresses
  after the word "Backtrace" on that line are expected on the stack instead), then fields
  ``Problem:``, ``Solution:``, ``About:``, ``Type:``, ``Backtrace:``, ``Stack:``; numbered
  fields (``Problem 2:``, ``Solution2:``) are variants of one entry;
* "By commands": ``Last command: [0001] WAIT`` (SCRLog opcodes);
* "By scripts": ``script <name>`` followed by ``Mod:`` / ``About:``;
* "Others" / "Other problems": ``Error: CLEO.asi`` (a module) or a plain description.

:func:`match` ranks entries for a crash (instruction pointer, stack, module, SCRLog command and
script); :func:`lookup` finds entries by address, opcode, script, module or words. Entries are
community knowledge, not verified facts: callers label them with :func:`source_label`.
Stdlib only; the parsed list is cached per process.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field

__all__ = ["Entry", "Match", "load", "match", "lookup", "source_info", "source_label", "parse", "LIST_ID"]

LIST_ID = "gta-sa-10us"
_ADDR = re.compile(r"\b0x([0-9A-Fa-f]{6,8})\b")
_OPCODE = re.compile(r"\[\s*([0-9A-Fa-f]{4})\s*\]")
_MODULE = re.compile(r"(?<![\w.+~-])([\w+~-][\w.+~-]*\.(?:asi|dll|cleo|exe))\b", re.I)
_FIELD = re.compile(r"^(?P<key>[A-Za-z][A-Za-z ]{0,30}?)\s*(?P<num>\d{1,2})?\s*:\s*(?P<text>.*)$")
_FIELD_KEYS = {"error", "problem", "issue", "solution", "about", "type", "backtrace", "stack", "mod", "last command",
               "during gameplay", "when trying to start the game", "note"}
_BT_NOTE = re.compile(r'^0x([0-9A-Fa-f]{6,8})\s+in\s+"?backtrace"?\s*:?\s*(.*)$', re.I)
_HEADERS = {"scrlog": None, "by commands:": "commands", "by scripts:": "scripts", "others": "modules",
            "other problems:": "other"}
_DATE = re.compile(r"^(\d{2})/(\d{2})/(\d{2})\b")
_GTA_LO, _GTA_HI = 0x400000, 0x1577000      # gta_sa.exe 1.0 US image


@dataclass(slots=True)
class Entry:
    """One entry of the list."""

    n: int                                   # 1-based number in list order (stable for one revision)
    line: int                                # 1-based line of the head in the file
    section: str                             # offsets | commands | scripts | modules | other
    head: str                                # head text: "0x00456809", "[038B]", "script nebo", "CLEO.asi"
    addrs: tuple[int, ...] = ()              # crash addresses (instruction pointer)
    backtrace: tuple[int, ...] = ()          # addresses expected on the stack
    modules: tuple[str, ...] = ()            # lower-case module names
    commands: tuple[str, ...] = ()           # upper-case SCRLog opcodes
    script: str | None = None                # lower-case script (thread) name
    fields: list[tuple[str, int, str]] = field(default_factory=list)   # (key, variant, text); variant 0 = common

    @property
    def key(self) -> str:
        return self.head

    def texts(self, key: str, variant: int | None = None) -> list[str]:
        return [t for k, v, t in self.fields if k == key and (variant is None or v == variant)]

    def variants(self) -> list[int]:
        return sorted({v for _k, v, _t in self.fields if v})

    def variant_for(self, stack: set[int]) -> int | None:
        """Numbered variant whose text names an address that is on ``stack`` (e.g. "If you have
        0x005279B6 in the second line of Backtrace")."""
        for k, v, t in self.fields:
            if v and k in ("problem", "issue", "about", "type") and any(int(a, 16) in stack for a in _ADDR.findall(t)):
                return v
        return None

    def title(self, variant: int | None = None) -> str | None:
        """Short "what happened": the first problem/issue/about/type/mod text (variant first)."""
        order = ("mod", "problem", "issue", "about", "type") if self.section == "scripts" else \
            ("problem", "issue", "about", "type", "mod")
        for want in ([variant] if variant else []) + [0, None]:
            for k in order:
                t = self.texts(k, want)
                if t:
                    return t[0]
        return self.fields[0][2] if self.fields else None

    def solution(self, variant: int | None = None) -> str | None:
        for want in ([variant] if variant else []) + [0, None]:
            t = self.texts("solution", want)
            if t:
                return t[0]
        return None

    def as_dict(self) -> dict:
        """All fields for ``satk crash known``: ``{key[_n]: text}`` in list order."""
        out: dict[str, str] = {}
        for k, v, t in self.fields:
            name = k.replace(" ", "_") + (f"_{v}" if v else "")
            i = 2
            base = name
            while name in out:
                name = f"{base}_{i}"
                i += 1
            out[name] = t
        return out


@dataclass(slots=True)
class Match:
    entry: Entry
    how: str          # ip | ip~bt | stack | module | command | script
    score: int
    variant: int | None = None
    via: str | None = None   # the address / opcode / script that matched


# --------------------------------------------------------------------------- parsing


def _field(line: str) -> tuple[str, int, str] | None:
    m = _FIELD.match(line)
    if not m:
        return None
    key = m.group("key").strip().lower()
    if key == "last command":
        return "last command", 0, m.group("text").strip()
    if key not in _FIELD_KEYS:
        return None
    return key, int(m.group("num") or 0), m.group("text").strip()


def _head_kind(line: str, section: str) -> str | None:
    low = line.lower()
    if low.startswith("error:"):
        return "error"
    if low.startswith("last command:"):
        return "command"
    if section == "scripts" and low.startswith("script ") and len(line) <= 40:
        return "script"
    return None


def _uniq(xs) -> tuple:
    seen, out = set(), []
    for x in xs:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return tuple(out)


def _field_backtrace(e: Entry) -> list[int]:
    bt: list[int] = []
    for k, _v, t in e.fields:
        if k in ("backtrace", "stack"):
            bt += [int(a, 16) for a in re.findall(r"\b0x([0-9A-Fa-f]{5,8})\b", t)]
        elif k == "note":
            m = _BT_NOTE.match(t)
            if m:
                bt.append(int(m.group(1), 16))
    return bt


def _finish(e: Entry) -> Entry:
    if e.section in ("offsets", "modules", "other") and e.fields and e.fields[0][0] == "error":
        text = e.fields.pop(0)[2]
        cut = re.search(r"back\s*trace", text, re.I)
        before, after = (text[:cut.start()], text[cut.start():]) if cut else (text, "")
        addrs = [int(a, 16) for a in _ADDR.findall(before)]
        bt = [int(a, 16) for a in _ADDR.findall(after)] + _field_backtrace(e)
        e.addrs, e.backtrace = _uniq(addrs), _uniq(bt)
        if not addrs and not _ADDR.search(text):
            e.modules = _uniq(m.lower() for m in _MODULE.findall(re.sub(r"\s*~\s*", "~", text)))
        if e.addrs or e.backtrace:
            e.head = ", ".join(f"0x{a:08X}" for a in e.addrs) or "backtrace " + ", ".join(
                f"0x{a:08X}" for a in e.backtrace)
            e.section = "offsets"
        else:
            e.head = text.strip()
            if e.section == "offsets":
                e.section = "modules" if e.modules else "other"
        return e
    if e.section == "commands" and e.fields and e.fields[0][0] == "last command":
        text = e.fields.pop(0)[2]
        e.commands = _uniq(op.upper() for op in _OPCODE.findall(text))
        e.head = text.strip()
    elif e.section == "scripts":
        e.script = e.head[len("script "):].strip().lower() or None
    e.backtrace = _uniq(list(e.backtrace) + _field_backtrace(e))
    return e


def parse(text: str) -> tuple[list[Entry], dict]:
    """``(entries, meta)``; ``meta`` = ``{"date": "2026-10-02", "sections": {...}}``."""
    entries: list[Entry] = []
    section = "offsets"
    cur: Entry | None = None
    meta: dict = {}
    lines = text.splitlines()
    for i, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line:
            continue
        if i <= 2 and "date" not in meta:
            dm = _DATE.match(line)
            if dm:
                meta["date"] = f"20{dm.group(3)}-{dm.group(2)}-{dm.group(1)}"
                continue
        low = line.lower()
        if low in _HEADERS:
            if cur is not None:
                entries.append(_finish(cur))
                cur = None
            section = _HEADERS[low] or section
            continue
        kind = _head_kind(line, section)
        if kind is not None:
            if cur is not None:
                entries.append(_finish(cur))
            if kind == "error":
                sec = section if section != "commands" else "other"
                cur = Entry(len(entries) + 1, i, sec, line, fields=[("error", 0, line.split(":", 1)[1].strip())])
            elif kind == "command":
                cur = Entry(len(entries) + 1, i, "commands", line, fields=[("last command", 0, line.split(":", 1)[1])])
            else:
                cur = Entry(len(entries) + 1, i, "scripts", line)
            continue
        if cur is None:
            continue                      # stray text between sections
        f = _field(line)
        if f is not None and f[0] not in ("error", "last command"):
            cur.fields.append(f)
        elif _BT_NOTE.match(line):
            cur.fields.append(("note", 0, line))
        elif cur.fields:
            k, v, t = cur.fields[-1]
            cur.fields[-1] = (k, v, f"{t} {line}")
        else:
            cur.fields.append(("note", 0, line))
    if cur is not None:
        entries.append(_finish(cur))
    for n, e in enumerate(entries, 1):
        e.n = n
    sections: dict[str, int] = {}
    for e in entries:
        sections[e.section] = sections.get(e.section, 0) + 1
    meta["sections"] = sections
    return entries, meta


# --------------------------------------------------------------------------- loading

_lock = threading.Lock()
_cache: dict[str, tuple[list[Entry], dict]] = {}


def source_info(list_id: str = LIST_ID) -> dict:
    """Provenance of the vendored list (``data/crashlist/source.json``)."""
    from ..core import resources

    return dict(resources.read_json("crashlist", "source.json")[list_id])


def load(list_id: str = LIST_ID) -> tuple[list[Entry], dict]:
    """Parsed entries and meta (``date``, ``sections``, ``source``), cached."""
    with _lock:
        hit = _cache.get(list_id)
        if hit is not None:
            return hit
    from ..core import resources

    src = source_info(list_id)
    entries, meta = parse(resources.read_text("crashlist", src["file"]))
    meta["source"] = src
    meta.setdefault("date", src.get("list_date"))
    with _lock:
        _cache[list_id] = (entries, meta)
    return entries, meta


def source_label(meta: dict) -> str:
    return f"CrashInfo list {meta.get('date') or '?'}"


# --------------------------------------------------------------------------- matching


def match(*, ip: int | None = None, module: str | None = None, stack: list[int] | tuple[int, ...] = (),
           commands: list[str] | tuple[str, ...] = (), scripts: list[str] | tuple[str, ...] = (),
           list_id: str = LIST_ID) -> list[Match]:
    """Entries for a crash, best first.

    Args:
        ip: absolute crash address (instruction pointer; gta_sa.exe addresses as 0x4xxxxx).
        module: base name of the module the crash is in (``CLEO.asi``); ``gta_sa.exe`` is ignored.
        stack: absolute addresses of stack frames (return addresses), innermost first.
        commands: SCRLog opcodes executed last (``038B``), most recent first.
        scripts: script names running when it crashed, most recent first.
    """
    entries, _meta = load(list_id)
    st = [a for a in stack if a is not None]
    stset = set(st)
    if ip is not None:
        stset.add(ip)
    best: dict[int, Match] = {}

    def add(e: Entry, how: str, score: int, via: str) -> None:
        old = best.get(e.n)
        if old is None or score > old.score:
            best[e.n] = Match(e, how, score, e.variant_for(stset), via)

    mod = (module or "").strip().lower()
    cmds = [c.upper() for c in commands if c]
    scr = [s.strip().lower() for s in scripts if s and s.strip()]
    for e in entries:
        # Backtrace addresses identify an entry on their own only when its crash address is not a gta_sa.exe
        # address (a garbage EIP, "0x*", none): otherwise they choose a variant after an IP match. A frame equal
        # to an entry's crash address is not a match either (0x0053E986 is the return address of every frame
        # in the main loop).
        bt_alone = bool(e.backtrace) and not any(_GTA_LO <= a < _GTA_HI for a in e.addrs)
        if ip is not None:
            if ip in e.addrs:
                add(e, "ip", 100, f"0x{ip:08X}")
            elif bt_alone and ip in e.backtrace:
                add(e, "ip~bt", 70, f"0x{ip:08X}")
        if st and bt_alone:
            for depth, a in enumerate(st[:32]):
                if a in e.backtrace:
                    add(e, "stack", 80 - min(depth, 20), f"0x{a:08X}")
                    break
        if mod and mod != "gta_sa.exe" and mod in e.modules:
            add(e, "module", 60, module or "")
        if cmds and e.commands:
            for depth, c in enumerate(cmds[:3]):
                if c in e.commands:
                    add(e, "command", 58 - 10 * depth, f"[{c}]")
                    break
        if scr and e.script:
            for depth, s in enumerate(scr[:3]):
                if s == e.script:
                    add(e, "script", 55 - 10 * depth, s)
                    break
    return sorted(best.values(), key=lambda m: (-m.score, m.entry.n))


def _parse_addr(q: str) -> int | None:
    s = q.strip()
    m = re.fullmatch(r"(?i)(?:gta[_-]?sa(?:\.exe)?)\s*\+\s*(?:0x)?([0-9a-f]{1,8})", s)
    if m:
        return 0x400000 + int(m.group(1), 16)
    m = re.fullmatch(r"(?i)(?:0x)([0-9a-f]{1,8})|([0-9a-f]{8})", s)
    if m:
        return int(m.group(1) or m.group(2), 16)
    return None


def lookup(query: str | None = None, *, kind: str = "auto", list_id: str = LIST_ID) -> list[tuple[Entry, str]]:
    """Entries for a user query: ``[(entry, how)]`` in list order.

    ``kind``: ``auto`` (address ``0x...``/``gta_sa.exe+0x...``, opcode ``038B``/``[038B]``, module
    ``*.asi|*.dll|*.cleo``, ``script:<name>``, else words), or one of ``addr``, ``command``,
    ``script``, ``module``, ``text``.
    """
    entries, _meta = load(list_id)
    q = (query or "").strip()
    if not q:
        return [(e, "all") for e in entries]
    k = kind
    if k == "auto":
        if q.lower().startswith("script:"):
            k, q = "script", q[7:].strip()
        elif re.fullmatch(r"\[?\s*[0-9A-Fa-f]{4}\s*\]?", q):
            k = "command"
        elif _parse_addr(q) is not None:
            k = "addr"
        elif re.fullmatch(r"(?i)[\w+\-~ .]+\.(?:asi|dll|cleo|exe)", q):
            k = "module"
        else:
            k = "text"
    out: list[tuple[Entry, str]] = []
    if k == "addr":
        a = _parse_addr(q)
        if a is None:
            return []
        for e in entries:
            if a in e.addrs:
                out.append((e, "addr"))
            elif a in e.backtrace:
                out.append((e, "backtrace"))
        out.sort(key=lambda p: (p[1] != "addr", p[0].n))
        return out
    if k == "command":
        op = re.sub(r"[\[\]\s]", "", q).upper()
        return [(e, "command") for e in entries if op in e.commands]
    if k == "script":
        s = q.lower()
        return [(e, "script") for e in entries if e.script == s]
    if k == "module":
        m = q.lower()
        return [(e, "module") for e in entries if m in e.modules]
    words = [w for w in re.split(r"\s+", q.lower()) if w]
    for e in entries:
        hay = (e.head + " " + " ".join(t for _k, _v, t in e.fields)).lower()
        if all(w in hay for w in words):
            out.append((e, "text"))
    return out
