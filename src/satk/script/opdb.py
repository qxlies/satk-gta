"""Opcode database for the SCM tools: command names, parameter kinds, varargs.

Three sources, tried in this order by ``load_db("auto")``:

* ``kb`` - the ``opcode`` table of ``work/kb/kb.sqlite`` (``satk kb build``; every SA command plus
  the CLEO 5, CLEO+, NewOpcodes, SAMPFUNCS and plugin extensions);
* ``cleo-ai`` - the same reference parsed straight from the read-only ``src/cleo-ai`` clone (used when
  the kb is not built, e.g. in a private work directory);
* ``core`` - a small subset shipped in ``data/script/core_opcodes.json`` (the commands of the templates
  and the most common ones), so templates assemble on any machine.

A parameter is described the way the kb prints it: ``"name: type (source)"``, e.g.
``"time: int"``, ``"x: float (variable)"``, ``"label"``, ``"args: arguments"``. Outputs follow the
inputs in the binary. A parameter of type ``arguments`` starts the variable part: everything after
the fixed parameters up to a ``0x00`` byte (inputs and outputs alike, e.g. ``0AB1 CLEO_CALL``).

Several extensions reuse ids (``0B20``: CLEO+ and SAMPFUNCS); :data:`EXT_ORDER` decides, and
``{$USE SAMPFUNCS}`` in a source file (or ``prefer=``) moves an extension to the front.
"""

from __future__ import annotations

import difflib
import json
import os
import re
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

from ..core.errors import SatkError

__all__ = ["Param", "Command", "OpcodeDB", "load_db", "parse_param", "EXT_ORDER", "SOURCES", "ENDS_FLOW",
           "STRING_TYPES", "reset_cache"]

#: Resolution order when two extensions define the same id or name.
EXT_ORDER: tuple[str, ...] = ("default", "CLEO", "memory", "file", "ini", "input", "text", "math", "bitwise",
                              "audio", "debug", "clipboard", "imgui", "Sphere", "CLEO+", "NewOpcodes", "SAMPFUNCS")
SOURCES: tuple[str, ...] = ("auto", "kb", "cleo-ai", "core")
#: Types whose literal is a text label/string (an untyped 8-byte string is legal there).
STRING_TYPES = frozenset({"string", "string128", "gxt_key", "zone_key", "GarageName", "AnimGroup"})
#: Commands after which execution never falls through to the next instruction.
ENDS_FLOW = frozenset({0x0002, 0x004E, 0x0051, 0x0A93, 0x0AB2, 0x2002, 0x2003})

_PARAM = re.compile(r"^(?:([A-Za-z_][A-Za-z0-9_]*):\s+)?(\S+)(?:\s+\((.*)\))?$")
_SOURCE = {"global var": "gvar", "local var": "lvar", "variable": "var", "literal": "lit"}
_SOURCE_TEXT = {v: k for k, v in _SOURCE.items()}


@dataclass(frozen=True, slots=True)
class Param:
    """One parameter: ``kind`` is int, float, string, label, model, any or args; ``source`` is any, var,
    gvar, lvar or lit; ``type`` is the original type name (``Car``, ``model_vehicle``, ...)."""

    kind: str
    type: str
    source: str = "any"
    name: str = ""
    out: bool = False

    def describe(self) -> str:
        s = f"{self.name}: {self.type}" if self.name else self.type
        return s + (f" ({_SOURCE_TEXT[self.source]})" if self.source != "any" else "")


def parse_param(text: str, out: bool = False) -> Param:
    """``"x: float (variable)"`` -> ``Param(kind="float", type="float", source="var", name="x")``."""
    m = _PARAM.match(text.strip())
    if not m:
        return Param("any", text.strip() or "any", out=out)
    name, typ, src = m.group(1) or "", m.group(2), (m.group(3) or "").strip()
    if typ == "arguments":
        kind = "args"
    elif typ == "label":
        kind = "label"
    elif typ == "float":
        kind = "float"
    elif typ in STRING_TYPES:
        kind = "string"
    elif typ.startswith("model_"):
        kind = "model"
    elif typ == "any":
        kind = "any"
    else:
        kind = "int"
    return Param(kind, typ, _SOURCE.get(src, "any"), name, out)


@dataclass(frozen=True)
class Command:
    """One script command. ``op`` is 0..0x7FFF (the NOT bit lives on the instruction)."""

    op: int
    name: str
    ext: str = "default"
    params: tuple[Param, ...] = ()
    flags: frozenset[str] = field(default_factory=frozenset)

    @cached_property
    def nfixed(self) -> int:
        """Parameters before the variable part (all of them when there is none)."""
        for i, p in enumerate(self.params):
            if p.kind == "args":
                return i
        return len(self.params)

    @cached_property
    def variadic(self) -> bool:
        return any(p.kind == "args" for p in self.params)

    @property
    def condition(self) -> bool:
        return "condition" in self.flags

    @property
    def ends_flow(self) -> bool:
        return self.op in ENDS_FLOW

    def signature(self) -> str:
        """``0001 WAIT(time: int)`` for error messages."""
        ins = [p.describe() for p in self.params if not p.out]
        outs = [p.describe() for p in self.params if p.out]
        s = f"{self.op:04X} {self.name}({', '.join(ins)})"
        return s + (f" -> {', '.join(outs)}" if outs else "")


def _rank(c: Command, prefer: tuple[str, ...]) -> tuple:
    order = list(prefer) + [e for e in EXT_ORDER if e not in prefer]
    bad = bool(c.flags & {"unsupported", "nop"})
    return (order.index(c.ext) if c.ext in order else len(order), bad, c.op)


class OpcodeDB:
    """Commands by id and by name. Build with :meth:`from_rows` or :func:`load_db`."""

    def __init__(self, commands: list[Command], source: str, prefer: tuple[str, ...] = ()):
        self.commands = commands
        self.source = source
        self.prefer = tuple(prefer)
        by_op: dict[int, list[Command]] = {}
        by_name: dict[str, list[Command]] = {}
        for c in commands:
            by_op.setdefault(c.op, []).append(c)
            by_name.setdefault(c.name.upper(), []).append(c)
        key = lambda c: _rank(c, self.prefer)  # noqa: E731
        self._op = {k: sorted(v, key=key)[0] for k, v in by_op.items()}
        self._name = {k: sorted(v, key=key)[0] for k, v in by_name.items()}
        self._alts = {k: v for k, v in by_op.items() if len(v) > 1}
        self._names_sorted: list[str] | None = None

    # ---------------------------------------------------------------- construction
    @classmethod
    def from_rows(cls, rows, source: str, prefer: tuple[str, ...] = ()) -> "OpcodeDB":
        """Rows ``{op, name, ext, flags, input, output}`` (input/output: lists of param strings)."""
        cmds = []
        for r in rows:
            try:
                op = int(str(r["op"]), 16)
            except (KeyError, ValueError):
                continue
            ins = [parse_param(p) for p in (r.get("input") or [])]
            outs = [parse_param(p, out=True) for p in (r.get("output") or [])]
            flags = frozenset(f.strip() for f in (r.get("flags") or "").split(",") if f.strip())
            cmds.append(Command(op & 0x7FFF, str(r["name"]).upper(), str(r.get("ext") or "default"),
                                tuple(ins + outs), flags))
        return cls(cmds, source, prefer)

    def preferring(self, exts: tuple[str, ...]) -> "OpcodeDB":
        """The same commands with ``exts`` first in the resolution order."""
        exts = tuple(e for e in exts if e)
        if exts == self.prefer:
            return self
        return OpcodeDB(self.commands, self.source, exts)

    # ---------------------------------------------------------------- lookups
    def get(self, op: int) -> Command | None:
        return self._op.get(op & 0x7FFF)

    def find(self, name: str) -> Command | None:
        return self._name.get(name.upper())

    def alternatives(self, op: int) -> list[Command]:
        return list(self._alts.get(op & 0x7FFF, ()))

    def suggest(self, name: str, n: int = 5) -> list[str]:
        """Close command names (lower case) for ``did_you_mean``."""
        if self._names_sorted is None:
            self._names_sorted = sorted(self._name)
        u = name.upper()
        out = difflib.get_close_matches(u, self._names_sorted, n=n, cutoff=0.6)
        if len(out) < n:
            out += [k for k in self._names_sorted if u in k and k not in out][: n - len(out)]
        return [x.lower() for x in out[:n]]

    def exts(self) -> list[str]:
        return sorted({c.ext for c in self.commands})

    def __len__(self) -> int:
        return len(self._op)

    def describe(self) -> str:
        return f"{self.source} ({len(self)} commands)"


# --------------------------------------------------------------------------- loading

_CACHE: dict[tuple, OpcodeDB] = {}


def reset_cache() -> None:
    _CACHE.clear()


def _from_kb() -> OpcodeDB:
    from contextlib import closing

    from ..kb.query import connect, kb_path

    p = kb_path()
    key = ("kb", str(p), p.stat().st_mtime_ns if p.is_file() else 0)
    if key in _CACHE:
        return _CACHE[key]
    with closing(connect(p)) as con:
        rows = [{"op": r["op"], "name": r["name"], "ext": r["ext"], "flags": r["flags"],
                 "input": json.loads(r["input"]) if r["input"] else [],
                 "output": json.loads(r["output"]) if r["output"] else []}
                for r in con.execute("SELECT op, name, ext, flags, input, output FROM opcode ORDER BY id")]
    if not rows:
        raise SatkError("NOT_READY", "the knowledge base has no opcodes", hint="satk kb build")
    db = OpcodeDB.from_rows(rows, "kb")
    _CACHE[key] = db
    return db


def _from_cleo_ai() -> OpcodeDB:
    from ..core.paths import cfg
    from ..kb.opcodes import DETAIL_PREFIXES, INDEX_PATH, parse_reference
    from ..re.gitsrc import DirTree, GitTree

    repo = Path(cfg().paths.src) / "cleo-ai"
    if not repo.is_dir():
        raise SatkError("NOT_FOUND", f"cleo-ai reference not found: {repo.as_posix()}", hint="satk kb build")
    tree = GitTree(repo, "HEAD") if (repo / ".git").exists() else DirTree(repo)
    key = ("cleo-ai", str(repo), getattr(tree, "rev", "") or "")
    if key in _CACHE:
        return _CACHE[key]
    paths = [p for p in tree.list(("reference",), (".md",)) if p == INDEX_PATH or p.startswith(DETAIL_PREFIXES)]
    ops = parse_reference(dict(tree.read_many(paths)))
    if not ops:
        raise SatkError("NOT_FOUND", f"no opcodes in {repo.as_posix()}/reference")
    rows = [{"op": o.op, "name": o.name, "ext": o.ext, "flags": o.flags, "input": o.input, "output": o.output}
            for o in ops]
    db = OpcodeDB.from_rows(rows, "cleo-ai")
    _CACHE[key] = db
    return db


def _from_core() -> OpcodeDB:
    from ..core import resources

    key = ("core",)
    if key not in _CACHE:
        data = resources.read_json("script", "core_opcodes.json")
        rows = [{"op": r[0], "name": r[1], "ext": r[2], "flags": r[3], "input": r[4], "output": r[5]}
                for r in data["commands"]]
        _CACHE[key] = OpcodeDB.from_rows(rows, "core")
    return _CACHE[key]


def _from_file(path: str) -> OpcodeDB:
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SatkError("BAD_PARAMS", f"cannot read opcode file {p.as_posix()}: {e}",
                        hint='a JSON file {"commands": [["0001","WAIT","default","",["time: int"],[]], ...]}') from None
    rows = [{"op": r[0], "name": r[1], "ext": r[2], "flags": r[3], "input": r[4], "output": r[5]}
            for r in data.get("commands", [])]
    return OpcodeDB.from_rows(rows, f"file {p.name}")


def load_db(source: str = "auto", *, warn: list[str] | None = None) -> OpcodeDB:
    """Opcode database from ``source`` (``auto kb cleo-ai core`` or a JSON file path).

    ``auto`` takes the kb, else the cleo-ai clone, else the bundled core subset and appends an
    ``OPDB:`` warning to ``warn`` when it had to fall back.
    """
    if source == "kb":
        return _from_kb()
    if source == "cleo-ai":
        return _from_cleo_ai()
    if source == "core":
        return _from_core()
    if source != "auto":
        if os.path.isfile(source):
            return _from_file(source)
        raise SatkError("BAD_PARAMS", f"unknown opcode db {source!r}", did_you_mean=list(SOURCES),
                        hint="auto | kb | cleo-ai | core | path to a JSON file")
    why = []
    for name, fn in (("kb", _from_kb), ("cleo-ai", _from_cleo_ai)):
        try:
            return fn()
        except SatkError as e:
            why.append(f"{name}: {e.msg}")
    db = _from_core()
    if warn is not None:
        warn.append(f"OPDB: using the bundled core subset ({len(db)} commands): {'; '.join(why)}; "
                    "run 'satk kb build' for every command")
    return db


def export_core(db: OpcodeDB, ops: list[int]) -> dict:
    """The ``core_opcodes.json`` document for ``ops`` taken from ``db`` (parameter names dropped).

    Regenerate after a kb update::

        from satk.script.opdb import export_core, load_db
        doc = export_core(load_db("kb"), [c.op for c in load_db("core").commands])
    """
    rows = []
    for op in sorted(set(ops)):
        c = db.get(op)
        if c is None:
            continue
        strip = [Param(p.kind, p.type, p.source, "", p.out) for p in c.params]
        rows.append([f"{c.op:04X}", c.name, c.ext, ", ".join(sorted(c.flags & {"condition", "branch"})),
                     [p.describe() for p in strip if not p.out], [p.describe() for p in strip if p.out]])
    return {"about": "Core subset of the SA script commands for satk.script when the kb is not built: id, name, "
                     "extension, flags, input and output parameter types (outputs follow inputs in the binary; "
                     "'arguments' = variable part ended by a 0x00 byte). Regenerate with "
                     "satk.script.opdb.export_core.",
            "commands": rows}
