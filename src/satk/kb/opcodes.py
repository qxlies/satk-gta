"""Script command (opcode) reference from the cleo-ai repository (owner M2-02).

cleo-ai renders the Sanny Builder Library (``sa.json``) into Markdown:

* ``reference/opcode-index.md`` — one table row per command:
  ``| `0A8C` | WRITE_MEMORY | Memory | CLEO | 4 | static | Writes the value ... |``;
* ``reference/opcodes-by-class/*.md`` and ``reference/ext-*.md`` — one section per command:
  ``### `0A8C` WRITE_MEMORY``, description, ``**Class:** `Memory.Write```, ``**Flags:**``,
  ``**Input:**``/``**Output:**`` lists and optional ``**Details:**`` prose, ended by ``---``.

:func:`parse_reference` merges both into :class:`Opcode` records. The text stays in
``work/kb/kb.sqlite`` (local-only: the library has no license file).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = ["Opcode", "parse_index", "parse_sections", "parse_reference", "INDEX_PATH", "DETAIL_PREFIXES"]

INDEX_PATH = "reference/opcode-index.md"
DETAIL_PREFIXES = ("reference/opcodes-by-class/", "reference/ext-")

_ROW = re.compile(r"^\|\s*`([0-9A-Fa-f]{4})`\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*(\d*)\s*\|"
                  r"\s*([^|]*?)\s*\|\s*(.*?)\s*\|\s*$")
_HEAD = re.compile(r"^###\s+`([0-9A-Fa-f]{4})`\s+(\S+)\s*$")
_PARAM = re.compile(r"^-\s+`([^`]*)`\s*(.*)$")


@dataclass
class Opcode:
    op: str                      # "0A8C" (upper case)
    name: str
    ext: str = ""
    cls: str = ""
    member: str = ""
    nparams: int | None = None
    flags: str = ""
    descr: str = ""
    input: list[str] = field(default_factory=list)
    output: list[str] = field(default_factory=list)
    details: str = ""
    path: str = ""
    line: int = 0


def parse_index(text: str, path: str = INDEX_PATH) -> list[Opcode]:
    """Rows of ``opcode-index.md``."""
    out = []
    for i, ln in enumerate(text.splitlines(), 1):
        m = _ROW.match(ln)
        if not m:
            continue
        out.append(Opcode(op=m.group(1).upper(), name=m.group(2), cls=m.group(3), ext=m.group(4),
                          nparams=int(m.group(5)) if m.group(5) else None, flags=m.group(6),
                          descr=m.group(7), path=path, line=i))
    return out


def parse_sections(text: str, path: str) -> list[Opcode]:
    """``### `XXXX` NAME`` sections of a detail page."""
    out: list[Opcode] = []
    cur: Opcode | None = None
    mode = ""
    details: list[str] = []

    def flush() -> None:
        nonlocal cur, details
        if cur is not None:
            cur.details = "\n".join(details).strip()
            out.append(cur)
        cur, details = None, []

    for i, ln in enumerate(text.splitlines(), 1):
        m = _HEAD.match(ln)
        if m:
            flush()
            cur = Opcode(op=m.group(1).upper(), name=m.group(2), path=path, line=i)
            mode = "descr"
            continue
        if cur is None:
            continue
        s = ln.strip()
        if s == "---":
            flush()
            mode = ""
            continue
        if s.startswith("## "):
            flush()
            continue
        if s.startswith("**Class:**"):
            cm = re.search(r"`([^`]*)`", s)
            if cm:
                cls, _, member = cm.group(1).partition(".")
                cur.cls, cur.member = cls, member
            mode = ""
            continue
        if s.startswith("**Flags:**"):
            cur.flags = s.split("**", 2)[-1].strip()
            continue
        if s.startswith("**Input:**"):
            mode = "input"
            continue
        if s.startswith("**Output:**"):
            mode = "output"
            continue
        if s.startswith("**Details:**"):
            mode = "details"
            continue
        if mode in ("input", "output"):
            pm = _PARAM.match(s)
            if pm:
                (cur.input if mode == "input" else cur.output).append((pm.group(1) + " " + pm.group(2)).strip())
            continue
        if mode == "descr" and s and not cur.descr:
            cur.descr = s
            continue
        if mode == "details":
            details.append(ln.rstrip())
    flush()
    return out


def parse_reference(files: dict[str, str]) -> list[Opcode]:
    """Merge the index with the detail sections; ``files`` maps repo paths to text."""
    index = parse_index(files.get(INDEX_PATH, ""))
    detail: dict[tuple[str, str], Opcode] = {}
    for path in sorted(files):
        if path.startswith(DETAIL_PREFIXES) and path.endswith(".md"):
            for o in parse_sections(files[path], path):
                detail.setdefault((o.op, o.name.upper()), o)
    out: list[Opcode] = []
    seen: set[tuple[str, str]] = set()
    for o in index:
        d = detail.get((o.op, o.name.upper()))
        if d is not None:
            o.member = d.member or o.member
            o.cls = o.cls or d.cls
            o.input, o.output, o.details = d.input, d.output, d.details
            o.flags = o.flags or d.flags
            o.descr = o.descr or d.descr
            o.path, o.line = d.path, d.line
        out.append(o)
        seen.add((o.op, o.name.upper()))
    for key, d in sorted(detail.items()):
        if key not in seen:
            out.append(d)
    return out
