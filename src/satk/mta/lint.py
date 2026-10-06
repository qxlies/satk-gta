"""``satk mta lint``: static checks of MTA:SA resources and Lua files.

Every script is parsed once with :mod:`.luaparse` (Lua 5.1 rules and messages). The checks then run per side,
because the scripts of one side of a resource share one Lua VM: a global defined in ``a.lua`` (client) is
visible in ``b.lua`` (client) but not in the server scripts. ``shared`` scripts run on both sides.

Codes (``satk mta lint --codes`` filters them; ``-- satk:ignore [CODE ...]`` on a line silences it):

* Lua: ``SYNTAX`` ``ENCODING`` ``COMPILED`` ``ESCAPE_52`` ``UNDEFINED_GLOBAL`` ``IMPLICIT_GLOBAL``
  ``DUPLICATE_FUNCTION`` ``LUA_FIELD`` ``MISSING_LIBRARY`` ``DISABLED_FUNCTION`` ``NOT_EQ``;
* MTA API: ``UNKNOWN_FUNCTION`` ``WRONG_SIDE`` ``DEPRECATED`` ``REMOVED`` ``MIN_VERSION`` ``NEON_ONLY``
  ``ARG_COUNT`` ``ARG_TYPE`` ``ENUM_VALUE`` ``OOP_DISABLED``;
* events: ``EVENT_UNKNOWN`` ``EVENT_WRONG_SIDE`` ``EVENT_NOT_REMOTE``;
* common bugs: ``RENDER_HEAVY`` ``HANDLER_CALLED`` ``RESOURCE_START_ROOT`` ``DRAW_OUTSIDE_RENDER``
  ``ELEMENT_TYPE``;
* ``meta.xml`` (see :mod:`.resource`): ``META_MISSING`` ``META_XML`` ``META_ROOT`` ``META_VALUE`` ``BAD_PATH``
  ``FILE_MISSING`` ``FILE_CASE`` ``SCRIPT_TYPE`` ``SCRIPT_EXT`` ``DUPLICATE_FILE`` ``UNKNOWN_TAG``
  ``NOT_LISTED`` ``LUA_AS_FILE`` ``INCLUDE`` ``EXPORT_MISSING`` ``FILE_INVALID`` ``DOWNLOAD_SIZE`` ``FILE_SIZE``.
"""

from __future__ import annotations

import difflib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import luaparse as L
from .api import Reference, SideRef, lua_facts
from .resource import Finding, Resource, load_resource

__all__ = ["SEVERITIES", "CODES", "Unit", "LintResult", "lint_path", "lint_source"]

SEVERITIES = ("error", "warn", "info")
CODES = ("SYNTAX", "ENCODING", "COMPILED", "ESCAPE_52", "UNDEFINED_GLOBAL", "IMPLICIT_GLOBAL", "DUPLICATE_FUNCTION",
         "LUA_FIELD", "MISSING_LIBRARY", "DISABLED_FUNCTION", "NOT_EQ", "UNKNOWN_FUNCTION", "WRONG_SIDE",
         "DEPRECATED", "REMOVED", "MIN_VERSION", "NEON_ONLY", "ARG_COUNT", "ARG_TYPE", "ENUM_VALUE", "OOP_DISABLED",
         "EVENT_UNKNOWN", "EVENT_WRONG_SIDE", "EVENT_NOT_REMOTE", "RENDER_HEAVY", "HANDLER_CALLED",
         "RESOURCE_START_ROOT", "DRAW_OUTSIDE_RENDER", "ELEMENT_TYPE", "META_MISSING", "META_XML", "META_ROOT",
         "META_VALUE", "BAD_PATH", "FILE_MISSING", "FILE_CASE", "SCRIPT_TYPE", "SCRIPT_EXT", "DUPLICATE_FILE",
         "UNKNOWN_TAG", "NOT_LISTED", "LUA_AS_FILE", "INCLUDE", "EXPORT_MISSING", "FILE_INVALID", "DOWNLOAD_SIZE",
         "FILE_SIZE")
MAX_SCRIPT = 16 * 1024 * 1024
_IGNORE = re.compile(r"satk:ignore(-file)?\b([A-Z0-9_ ,]*)")
_NUMERIC = re.compile(r"\s*[-+]?(?:0[xX][0-9a-fA-F]+|(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*\Z")
_SIDE_WORD = {"client": "client", "server": "server", "any": "client or server"}
_VECTOR_ARITY = {"Vector2": 2, "Vector3": 3, "Vector4": 4}
_USERDATA = frozenset("""element ped vehicle player object gui-element xml-node resource colshape browser weapon file
marker account ban team blip sound textitem timer radararea pickup searchlight acl-group acl img light textdisplay
projectile texture shader water svg request effect db-connection dx-material txd dff col db-query dx-screensource
dx-font building userdata""".split())
_NUM_TYPES = frozenset(("int", "float", "number", "color"))


# --------------------------------------------------------------------------- per-file facts


@dataclass
class Facts:
    """What one parsed script defines and uses (side-independent)."""

    gdefs: dict = field(default_factory=dict)        # name -> [(pos, func, kind)]  kind: f (function) | w (assign)
    greads: dict = field(default_factory=dict)       # name -> [(pos, func)]  (not as a direct callee)
    gcalls: list = field(default_factory=list)       # [(name, Call)]
    fields: list = field(default_factory=list)       # [(global, member, pos, is_call, is_write)]
    gindexed: list = field(default_factory=list)     # [(global, pos, how)]  global used with . : or [ ]
    events_added: dict = field(default_factory=dict)  # name -> [(pos, remote True/False/None)]
    handlers: list = field(default_factory=list)     # [(event, elem, handler, pos, args)]
    triggers: list = field(default_factory=list)     # [(function, event, pos)]
    created_types: set = field(default_factory=set)
    type_strings: list = field(default_factory=list)  # [(value, pos, how)]
    handler_args: list = field(default_factory=list)  # [(function, arg expr, pos)]
    not_eq: list = field(default_factory=list)       # [pos]
    var_funcs: dict = field(default_factory=dict)    # LocalVar -> Func
    global_funcs: dict = field(default_factory=dict)  # name -> [Func]
    dynamic: bool = False                            # loadstring/load or _G[expr] = ... (globals made at run time)


def _str(e) -> str | None:
    return e.value if isinstance(e, L.Str) else None


def _gname(e) -> str | None:
    """Name of a global variable expression (``None`` for locals and other expressions)."""
    return e.name if isinstance(e, L.Name) and e.var is None else None


def collect(chunk: L.Chunk) -> Facts:
    f = Facts()
    callee_ids: set[int] = set()
    for node in L.walk(chunk.main.body):
        t = type(node)
        if t is L.Call:
            fn = node.fn
            name = _gname(fn)
            args = node.args
            if name is not None:
                callee_ids.add(id(fn))
                f.gcalls.append((name, node))
                a0 = _str(args[0]) if args else None
                if name == "addEvent" and a0 is not None:
                    remote = None
                    if len(args) < 2 or isinstance(args[1], (L.FalseE, L.Nil)):
                        remote = False
                    elif isinstance(args[1], L.TrueE):
                        remote = True
                    f.events_added.setdefault(a0, []).append((args[0].pos, remote))
                elif name == "addEventHandler" and a0 is not None:
                    f.handlers.append((a0, args[1] if len(args) > 1 else None, args[2] if len(args) > 2 else None,
                                       args[0].pos, args))
                elif name in ("triggerServerEvent", "triggerLatentServerEvent", "triggerEvent") and a0 is not None:
                    f.triggers.append((name, a0, args[0].pos))
                elif name in ("triggerClientEvent", "triggerLatentClientEvent") and args:
                    ev = a0 if a0 is not None else (_str(args[1]) if len(args) > 1 else None)
                    if ev is not None:
                        f.triggers.append((name, ev, (args[0] if a0 is not None else args[1]).pos))
                elif name == "createElement" and a0 is not None:
                    f.created_types.add(a0)
                elif name in ("loadstring", "load"):
                    f.dynamic = True
                ta = lua_facts()["element_type_args"].get(name)
                if ta is not None and len(args) > ta and _str(args[ta]) is not None:
                    f.type_strings.append((args[ta].value, args[ta].pos, f"{name} argument {ta + 1}"))
                hi = lua_facts()["handler_call_functions"].get(name)
                if hi is not None and len(args) > hi and isinstance(args[hi], (L.Call, L.MCall)):
                    f.handler_args.append((name, args[hi], args[hi].pos))
            elif isinstance(fn, L.Index) and _gname(fn.obj) is not None and isinstance(fn.key, L.Str) and fn.dot:
                callee_ids.add(id(fn))
                f.fields.append((fn.obj.name, fn.key.value, fn.key.pos, True, False))
        elif t is L.Index:
            g = _gname(node.obj)
            if g is not None:
                if isinstance(node.key, L.Str):
                    if id(node) not in callee_ids:
                        f.fields.append((g, node.key.value, node.key.pos, False, False))
                f.gindexed.append((g, node.obj.pos, "." + node.key.value if isinstance(node.key, L.Str) and node.dot
                                   else "[]"))
        elif t is L.MCall:
            g = _gname(node.obj)
            if g is not None:
                f.gindexed.append((g, node.obj.pos, ":" + node.name))
        elif t is L.BinOp:
            op = node.op
            if op in ("==", "~="):
                if isinstance(node.left, L.UnOp) and node.left.op == "not":
                    f.not_eq.append(node.left.pos)
                for a, b in ((node.left, node.right), (node.right, node.left)):
                    if isinstance(a, L.Call) and _gname(a.fn) == "getElementType" and isinstance(b, L.Str):
                        f.type_strings.append((b.value, b.pos, "getElementType comparison"))
        elif t is L.Assign:
            for tgt, val in zip(node.targets, node.exprs + [None] * (len(node.targets) - len(node.exprs))):
                if isinstance(tgt, L.Name):
                    if isinstance(val, L.Func):
                        if tgt.var is None:
                            f.global_funcs.setdefault(tgt.name, []).append(val)
                            val.name = val.name or tgt.name
                        else:
                            f.var_funcs.setdefault(tgt.var, val)
                elif isinstance(tgt, L.Index) and _gname(tgt.obj) is not None:
                    g = tgt.obj.name
                    if isinstance(tgt.key, L.Str):
                        f.fields.append((g, tgt.key.value, tgt.key.pos, False, True))
                        if g == "_G":
                            f.gdefs.setdefault(tgt.key.value, []).append((tgt.pos, tgt.obj.func, "w"))
                    elif g == "_G":
                        f.dynamic = True
        elif t is L.FuncStat:
            tg = node.target
            if isinstance(tg, L.Name) and tg.var is None:
                f.global_funcs.setdefault(tg.name, []).append(node.func)
            elif isinstance(tg, L.Name):
                f.var_funcs.setdefault(tg.var, node.func)
            elif isinstance(tg, L.Index) and isinstance(tg.key, L.Str) and _gname(tg.obj) is not None:
                f.fields.append((tg.obj.name, tg.key.value, tg.key.pos, False, True))
        elif t is L.LocalFunc:
            f.var_funcs[node.var] = node.func
        elif t is L.Local:
            for v, val in zip(node.vars, node.exprs):
                if isinstance(val, L.Func):
                    f.var_funcs[v] = val
                    val.name = val.name or v.name
    for nd in chunk.names:
        if nd.ctx in ("w", "f"):
            f.gdefs.setdefault(nd.name, []).append((nd.pos, nd.func, nd.ctx))
        elif id(nd) not in callee_ids:
            f.greads.setdefault(nd.name, []).append((nd.pos, nd.func))
    return f


# --------------------------------------------------------------------------- units


@dataclass
class Unit:
    rel: str
    sides: tuple[str, ...]
    src: L.Source | None = None
    chunk: L.Chunk | None = None
    facts: Facts | None = None
    ignore: dict = field(default_factory=dict)       # line -> set of codes (empty = all)
    ignore_file: set = field(default_factory=set)


@dataclass
class LintResult:
    findings: list[Finding]
    resource: Resource | None
    units: list[Unit]
    ref: Reference
    sides: list[str]
    downloads: list = field(default_factory=list)
    warn: list[str] = field(default_factory=list)


def _decode(data: bytes) -> tuple[str | None, str | None]:
    """``(text, problem)``: ``problem`` is ``compiled``/``obfuscated`` or ``ansi`` (decoded as cp1252)."""
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
        try:
            return data.decode("utf-8"), None
        except UnicodeDecodeError:
            return data.decode("utf-8", errors="replace"), "ansi"
    if data[:1] == b"\x1b":
        return None, "compiled"
    if data[:1] == b"\x1c":
        return None, "obfuscated"
    try:
        return data.decode("utf-8"), None
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace"), "ansi"


def _ignores(u: Unit) -> None:
    if u.chunk is None:
        return
    for pos, text in u.chunk.comments:
        m = _IGNORE.search(text)
        if not m:
            continue
        codes = {c for c in re.split(r"[ ,]+", m.group(2).strip()) if c}
        if m.group(1):
            u.ignore_file |= codes or {"*"}
        else:
            u.ignore.setdefault(u.src.line(pos), set()).update(codes or {"*"})


# --------------------------------------------------------------------------- the checker


class _Linter:
    def __init__(self, ref: Reference, res: Resource | None, units: list[Unit], budget_mb: float):
        self.ref = ref
        self.res = res
        self.units = units
        self.budget_mb = budget_mb
        self.facts = lua_facts()
        self.out: dict[tuple, Finding] = {}
        self.render_events = set(self.facts["render_events"])
        self._all_added: dict[str, list] | None = None
        self.sides: list[str] = []

    # -- output -------------------------------------------------------------------------

    def add(self, u: Unit, pos: int | None, sev: str, code: str, msg: str) -> None:
        line, col = u.src.linecol(pos) if (pos is not None and u.src is not None) else (0, 0)
        if "*" in u.ignore_file or code in u.ignore_file:
            return
        ign = u.ignore.get(line)
        if ign is not None and ("*" in ign or code in ign):
            return
        key = (u.rel, line, col, code, msg)
        if key not in self.out:
            self.out[key] = Finding(sev, u.rel, line, col, code, msg)

    # -- helpers ------------------------------------------------------------------------

    def side_units(self, side: str, with_any: bool = True) -> list[Unit]:
        """Scripts that run on ``side`` (``with_any``: also those of unknown side, for their definitions)."""
        return [u for u in self.units if u.facts is not None and (side in u.sides or (with_any and "any" in u.sides))]

    def known_global(self, name: str, side: str) -> bool:
        fx = self.facts
        if name in fx["lua_globals"] or name in fx["libraries"]:
            return True
        mg = fx["mta_globals"]
        if name in mg["shared"] or (side in ("client", "any") and name in mg["client"]) or \
                (side in ("server", "any") and name in mg["server"]):
            return True
        if side == "any":
            return name in self.ref.funcs or name in self.ref.classes
        return side in self.ref.funcs.get(name, ()) or side in self.ref.classes.get(name, ())

    def suggest(self, name: str, side: str, defined: set[str]) -> list[str]:
        cands = [n for n, s in self.ref.funcs.items() if side == "any" or side in s] + list(defined)
        hits = difflib.get_close_matches(name, cands, n=3, cutoff=0.8)
        if not hits:
            low = {c.lower(): c for c in cands}
            if name.lower() in low:
                hits = [low[name.lower()]]
        return hits

    def resolve_func(self, e, side_funcs: dict, facts: Facts) -> L.Func | None:
        if isinstance(e, L.Func):
            return e
        if isinstance(e, L.Name):
            if e.var is not None:
                return facts.var_funcs.get(e.var)
            fs = side_funcs.get(e.name)
            return fs[0] if fs else None
        return None

    # -- run ----------------------------------------------------------------------------

    def run(self) -> None:
        sides: list[str] = []
        for u in self.units:
            for s in u.sides:
                if s not in sides:
                    sides.append(s)
        self.sides = sorted(sides, key=lambda s: {"client": 0, "server": 1}.get(s, 2))
        for u in self.units:
            if u.facts is not None:
                self.file_checks(u)
        for side in self.sides:
            self.side_checks(side)
        self.type_checks()
        if self.res is not None and self.res.meta is not None:
            self.meta_checks()

    def type_checks(self) -> None:
        if not any(u.facts is not None and u.facts.type_strings for u in self.units):
            return
        known = self.element_types()
        low = {k.lower(): k for k in known}
        for u in self.units:
            if u.facts is None:
                continue
            for val, pos, how in u.facts.type_strings:
                if val in known:
                    continue
                if self.ref.element_types.get(val) == "neon":
                    self.add(u, pos, "info", "ELEMENT_TYPE",
                             f"'{_short(val)}' ({how}) is an element type of the MTA Neon fork, not of MTA:SA")
                    continue
                if val.lower() in low:
                    hint = [low[val.lower()]]
                else:
                    hint = difflib.get_close_matches(val, sorted(known), n=2, cutoff=0.8)
                # a near miss of a known type is a typo; anything else may be a custom type of another resource
                self.add(u, pos, "warn" if hint else "info", "ELEMENT_TYPE",
                         f"'{_short(val)}' ({how}) is not an element type MTA:SA creates, and no createElement or "
                         ".map file of this resource makes it" + (f"; did you mean {', '.join(hint)}?" if hint
                                                                  else " (fine if another resource creates it)"))

    # -- side-independent ---------------------------------------------------------------

    def file_checks(self, u: Unit) -> None:
        f = u.facts
        for code, pos, msg in u.chunk.notes:
            self.add(u, pos, "warn", code, msg)
        for pos in f.not_eq:
            self.add(u, pos, "warn", "NOT_EQ", "'not a == b' means '(not a) == b': write 'a ~= b' or 'not (a == b)'")

    def element_types(self) -> set[str]:
        out = set(self.facts["element_types"]) | {t for t, o in self.ref.element_types.items() if o == "mta"}
        for u in self.units:
            if u.facts is not None:
                out |= u.facts.created_types
        if self.res is not None:
            for e in self.res.entries_of("map"):
                for rel in e.files:
                    out |= _map_tags(self.res.root / rel)
        return out

    # -- per side -----------------------------------------------------------------------

    def side_checks(self, side: str) -> None:
        units = self.side_units(side)
        if not units:
            return
        defined: dict[str, list[tuple[Unit, int, L.Func, str]]] = {}
        side_funcs: dict[str, list[L.Func]] = {}
        fields_written: set[tuple[str, str]] = set()
        dynamic = False
        events_added: dict[str, list] = {}
        for u in units:
            f = u.facts
            for n, defs in f.gdefs.items():
                for pos, fn, kind in defs:
                    defined.setdefault(n, []).append((u, pos, fn, kind))
            for n, fs in f.global_funcs.items():
                side_funcs.setdefault(n, []).extend(fs)
            for g, m, _p, _c, w in f.fields:
                if w:
                    fields_written.add((g, m))
            dynamic = dynamic or f.dynamic
            for ev, items in f.events_added.items():
                events_added.setdefault(ev, []).extend(items)
        dset = set(defined)
        self.duplicate_functions(side, defined)
        oop = None if self.res is None or self.res.meta is None else bool(self.res.oop)
        for u in units:
            if side not in u.sides:
                continue
            shared = len(u.sides) > 1
            self.calls(u, side, shared, dset, dynamic)
            self.reads(u, side, shared, dset, dynamic, defined)
            self.lib_fields(u, side, dset, fields_written)
            if oop is False:
                self.oop_use(u, side, dset)
            self.events(u, side, shared, events_added)
            self.handler_checks(u, side, side_funcs)
        self.implicit_globals(side, defined, units)

    def duplicate_functions(self, side: str, defined: dict) -> None:
        for name, defs in defined.items():
            fdefs = [d for d in defs if d[3] == "f" and d[2].line == 0]     # top-level `function name()`
            if len(fdefs) < 2:
                continue
            first = fdefs[0]
            fl = first[0].src.line(first[1])
            for u, pos, _fn, _k in fdefs[1:]:
                self.add(u, pos, "warn", "DUPLICATE_FUNCTION",
                         f"global function {name} is also defined in {first[0].rel}:{fl} ({side} side): the one "
                         "loaded last wins")

    def calls(self, u: Unit, side: str, shared: bool, defined: set[str], dynamic: bool) -> None:
        ref = self.ref
        fx = self.facts
        for name, call in u.facts.gcalls:
            if name in defined:
                continue
            pos = call.fn.pos
            if name in fx["disabled"]:
                self.add(u, pos, "error", "DISABLED_FUNCTION", f"{name} is disabled in MTA (logs 'Unsafe function was called.'): {fx['disabled'][name]}")
                continue
            if name in ref.funcs or name in ref.classes:
                self.api_call(u, side, shared, name, call)
                continue
            if self.known_global(name, "any"):
                continue
            dep = ref.deprecation(name, side) if side != "any" else (
                ref.deprecation(name, "client") or ref.deprecation(name, "server"))
            if dep is not None:
                self.deprecated(u, pos, name, side, dep)
                continue
            if not ref:
                continue
            if name in fx["missing_libraries"]:
                continue
            sev = "warn" if dynamic else "error"
            hint = self.suggest(name, side, defined)
            msg = f"{name} is not an MTA:SA {_SIDE_WORD.get(side, side)} function and is not defined in this " \
                  f"{'resource' if self.res is not None and self.res.meta is not None else 'file'}"
            if hint:
                msg += f": did you mean {', '.join(hint)}?"
            if dynamic:
                msg += " (globals made at run time with loadstring or _G[...] are not seen)"
            self.add(u, pos, sev, "UNKNOWN_FUNCTION", msg)

    def deprecated(self, u: Unit, pos: int, name: str, side: str, dep: tuple) -> None:
        removed, how, ver = dep
        if ver:
            mine = (self.res.min_client if side == "client" else self.res.min_server) if self.res else ""
            if not mine or mine < ver:
                self.add(u, pos, "warn", "MIN_VERSION",
                         f"{name} {how} because <min_mta_version> {side} in meta.xml is below {ver}")
            return
        if removed:
            self.add(u, pos, "error", "REMOVED", f"{name} no longer works in MTA:SA: {how}")
        else:
            self.add(u, pos, "warn", "DEPRECATED", f"{name} is deprecated: use {how}")

    def api_call(self, u: Unit, side: str, shared: bool, name: str, call: L.Call) -> None:
        ref = self.ref
        pos = call.fn.pos
        sides = ref.funcs.get(name) or {}
        if not sides and name in ref.classes:
            sides_cls = ref.classes[name]
            if side != "any" and side not in sides_cls:
                self.wrong_side(u, pos, side, shared, f"{name} (OOP class)", call)
            return
        if side != "any" and side not in sides:
            other = next(iter(sides))
            dep = ref.deprecation(name, side)
            if dep is not None:
                self.deprecated(u, pos, name, side, dep)
                return
            self.wrong_side(u, pos, side, shared, f"{name} is {other}-only", call)
            return
        sref = sides.get(side) if side != "any" else (sides.get("client") or sides.get("server"))
        if side == "any" and len(sides) > 1:
            refs = list(sides.values())
            # An unclassified file may use either signature (e.g. server outputChatBox has a recipient).
            sref = SideRef(variants=[v for r in refs for v in r.variants], exact=all(r.exact for r in refs),
                           approx=any(r.approx for r in refs)) if all(r.variants for r in refs) else SideRef()
        dep_side = side if side != "any" else next(iter(sides), side)
        dep = ref.deprecation(name, dep_side)
        if side == "any" and any(ref.deprecation(name, s) != dep for s in sides):
            dep = None                              # a valid side may still support this name
        if dep is not None:
            self.deprecated(u, pos, name, dep_side, dep)
        if sref is None:
            return
        if sref.origin == "neon" and side != "any":
            self.add(u, pos, "warn", "NEON_ONLY",
                     f"{name} ({side}) exists only in the MTA Neon fork, not in MTA:SA (upstream)")
            return
        self.check_args(u, name, side, sref, call)

    def wrong_side(self, u: Unit, pos: int, side: str, shared: bool, what: str, call) -> None:
        top = call.fn.func.line == 0 if isinstance(call.fn, L.Name) else True
        if shared and not top:
            self.add(u, pos, "info", "WRONG_SIDE",
                     f"{what}: this shared script also runs on the {side}, where it does not exist (guard the call "
                     "or move it to a script of the right type)")
        else:
            self.add(u, pos, "error", "WRONG_SIDE",
                     f"{what}, but this script runs on the {side}"
                     + (" (shared script, top level)" if shared else ""))

    # -- arguments ----------------------------------------------------------------------

    def check_args(self, u: Unit, name: str, side: str, sref, call: L.Call) -> None:
        if not sref.variants:
            return
        args = call.args
        multi = bool(args) and isinstance(args[-1], (L.Call, L.MCall, L.Vararg))
        n = len(args) - (1 if multi else 0)
        mins, maxs = [], []
        for v in sref.variants:
            lo = hi = 0
            inf = False
            for _an, at, opt in v:
                if at == "..." or at.endswith("|...") or at.startswith("...|"):
                    inf = True
                    continue
                mx = max((_VECTOR_ARITY.get(p, 1) for p in at.split("|")), default=1)
                if not opt:
                    lo += 1
                hi += mx
            mins.append(lo)
            maxs.append(10 ** 6 if inf else hi)
        lo, hi = min(mins), max(maxs)
        exact = sref.exact
        if n < lo and not multi and (exact or (len(sref.variants) == 1 and not sref.approx)):
            sig = ", ".join(f"{a[1]} {a[0]}" for a in sref.variants[0] if not a[2])
            self.add(u, call.fn.pos, "warn", "ARG_COUNT",
                     f"{name} needs at least {lo} argument{'s' if lo != 1 else ''} ({sig}), got {n}")
            return
        if exact and n > hi:
            self.add(u, call.fn.pos, "warn", "ARG_COUNT",
                     f"{name} takes at most {hi} argument{'s' if hi != 1 else ''}, got {n}: MTA ignores the rest")
        # literal types: only at positions before any variable-size parameter
        for i, a in enumerate(args[:n]):
            a = _unparen(a)
            lt = _lit_type(a)
            if lt is None:
                continue
            ok = False
            judged = False
            for v in sref.variants:
                pt = _param_at(v, i, exact)
                if pt is None:
                    ok = True
                    break
                judged = True
                at, opt = pt
                if lt == "nil":
                    if opt or not exact:
                        ok = True
                        break
                    continue
                if _accepts(at, lt, a):
                    ok = True
                    break
            if judged and not ok:
                want = " or ".join(sorted({_param_at(v, i, exact)[0] for v in sref.variants
                                           if _param_at(v, i, exact)}))
                self.add(u, a.pos, "warn", "ARG_TYPE",
                         f"{name} argument {i + 1} expects {want}, got {'a ' if lt != 'nil' else ''}{lt}"
                         + (f" ({_short(a.value)})" if isinstance(a, L.Str) else ""))
        self.check_enum(u, name, sref, [_unparen(a) for a in args[:n]])

    def check_enum(self, u: Unit, name: str, sref, args: list) -> None:
        if len(sref.enums) != 1 or len(sref.variants) != 1:
            return
        v = sref.variants[0]
        spos = [i for i, (_n, t, _o) in enumerate(v) if t == "string"]
        if len(spos) != 1 or any(t in _VECTOR_ARITY for _n, t, _o in v[:spos[0]]):
            return
        i = spos[0]
        if i >= len(args) or not isinstance(args[i], L.Str):
            return
        vals = self.ref.enums.get(sref.enums[0])
        if not vals:
            return
        val = args[i].value
        if val.lower() in {x.lower() for x in vals}:
            return
        hint = difflib.get_close_matches(val, vals, n=3, cutoff=0.6)
        self.add(u, args[i].pos, "warn", "ENUM_VALUE",
                 f"{name} argument {i + 1}: '{_short(val)}' is not one of {_short('|'.join(vals), 120)}"
                 + (f"; did you mean {', '.join(hint)}?" if hint else ""))

    # -- globals ------------------------------------------------------------------------

    def reads(self, u: Unit, side: str, shared: bool, defined: set[str], dynamic: bool, defs: dict) -> None:
        fx = self.facts
        ref = self.ref
        for name, uses in u.facts.greads.items():
            if name in defined:
                continue
            pos = uses[0][0]
            if name in fx["missing_libraries"]:
                # `if io then` is a feature test (nil in MTA, so it works); `io.open` is the bug
                used = [p for g, p, _h in u.facts.gindexed if g == name]
                if used:
                    self.add(u, used[0], "error", "MISSING_LIBRARY", f"{name}: {fx['missing_libraries'][name]}")
                continue
            if name in fx["disabled"]:
                continue              # a value test (`if getfenv then`); calls are reported by calls()
            if self.known_global(name, side):
                continue
            mg = fx["mta_globals"]
            other = None
            if name in mg["client"] and side == "server":
                other = "client"
            elif name in mg["server"] and side == "client":
                other = "server"
            elif side != "any" and (name in ref.funcs or name in ref.classes):
                other = "server" if side == "client" else "client"
            if other is not None:
                top = uses[0][1].line == 0
                if shared and not top:
                    self.add(u, pos, "info", "WRONG_SIDE",
                             f"{name} exists only on the {other}; this shared script also runs on the {side}")
                else:
                    self.add(u, pos, "error" if name in mg["client"] or name in mg["server"] else "warn",
                             "WRONG_SIDE", f"{name} exists only in {other} scripts (nil on the {side})")
                continue
            dep = ref.deprecation(name, side) if side != "any" else None
            if dep is not None:
                self.deprecated(u, pos, name, side, dep)
                continue
            if not ref and side != "any":
                continue
            if not ref:
                continue
            hint = self.suggest(name, side, defined)
            n = len(uses)
            msg = f"{name} is never assigned on the {side} side: it is nil" if side != "any" else \
                f"{name} is never assigned in this file and is not an MTA:SA name: it is nil"
            if n > 1:
                msg += f" ({n} uses)"
            if hint:
                msg += f"; did you mean {', '.join(hint)}?"
            self.add(u, pos, "info" if dynamic else "warn", "UNDEFINED_GLOBAL", msg)

    def implicit_globals(self, side: str, defined: dict, units: list[Unit]) -> None:
        """A global written and read only inside one function should be local."""
        reads: dict[str, set] = {}
        for u in units:
            for n, uses in u.facts.greads.items():
                reads.setdefault(n, set()).update(id(fn) for _p, fn in uses)
            for g, _m, _p, _c, _w in u.facts.fields:
                reads.setdefault(g, set()).add(-1)
            for g, _p, _h in u.facts.gindexed:
                reads.setdefault(g, set()).add(-1)
        for name, defs in defined.items():
            if any(k == "f" for _u, _p, _f, k in defs):
                continue
            funcs = {id(fn) for _u, _p, fn, _k in defs}
            if len(funcs) != 1 or any(fn.line == 0 for _u, _p, fn, _k in defs):
                continue
            r = reads.get(name, set())
            if r and r != funcs:
                continue
            u, pos, fn, _k = defs[0]
            if self.known_global(name, side):
                continue
            self.add(u, pos, "info", "IMPLICIT_GLOBAL",
                     f"{name} is a global used only inside {fn.name or 'a function'} (line {fn.line}): "
                     f"declare it 'local {name}'")

    def lib_fields(self, u: Unit, side: str, defined: set[str], written: set) -> None:
        fx = self.facts
        libs = fx["libraries"]
        newer = fx["newer_lua"]
        for g, m, pos, is_call, is_write in u.facts.fields:
            if is_write or g in defined or (g, m) in written:
                continue
            full = f"{g}.{m}"
            if full in fx["disabled"]:
                self.add(u, pos, "error", "DISABLED_FUNCTION", f"{full} is disabled in MTA (logs 'Unsafe function was called.'): {fx['disabled'][full]}")
                continue
            if g not in libs or m in libs[g]:
                continue
            if full in newer:
                alt = newer[full]
                msg = f"{full} is Lua 5.2+; MTA runs Lua 5.1 where it is nil" + (f": use {alt}" if alt else "")
            else:
                hint = difflib.get_close_matches(m, libs[g], n=2, cutoff=0.7)
                msg = f"{full} does not exist in Lua 5.1 (MTA)" + (f": did you mean {g}.{hint[0]}?" if hint else "")
            self.add(u, pos, "error" if is_call else "warn", "LUA_FIELD", msg)

    def oop_use(self, u: Unit, side: str, defined: set[str]) -> None:
        ref = self.ref
        elems = set(self.facts["element_globals"]["shared"])
        if side in ("client", "any"):
            elems |= set(self.facts["element_globals"]["client"])
        seen: set[str] = set()
        always = set(self.facts["always_classes"])
        for name, call in u.facts.gcalls:
            if name in always:
                continue
            if name in ref.classes and name not in ref.funcs and name not in defined and name not in seen:
                seen.add(name)
                self.add(u, call.fn.pos, "error", "OOP_DISABLED",
                         f"{name}(...) needs <oop>true</oop> in meta.xml (without it {name} is nil)")
        for name, uses in u.facts.greads.items():
            if name in always:
                continue
            if name in ref.classes and name not in ref.funcs and name not in defined and name not in seen:
                seen.add(name)
                self.add(u, uses[0][0], "error", "OOP_DISABLED",
                         f"{name} needs <oop>true</oop> in meta.xml (without it {name} is nil)")
        for g, pos, how in u.facts.gindexed:
            if g in elems and g not in defined and (g, how[:1]) not in seen and how[:1] in (".", ":"):
                seen.add((g, how[:1]))
                self.add(u, pos, "error", "OOP_DISABLED",
                         f"{g}{how}{'()' if how[:1] == ':' else ''} is OOP syntax: add <oop>true</oop> to meta.xml "
                         "(without it elements have no fields or methods)")

    # -- events -------------------------------------------------------------------------

    def all_added(self) -> dict[str, list]:
        """Events added with addEvent by any script of the resource (both sides)."""
        if self._all_added is None:
            self._all_added = {}
            for x in self.units:
                if x.facts is not None:
                    for ev, items in x.facts.events_added.items():
                        self._all_added.setdefault(ev, []).extend((x, it) for it in items)
        return self._all_added

    def events(self, u: Unit, side: str, shared: bool, added: dict) -> None:
        ref = self.ref
        all_added = self.all_added()
        for ev, _elem, _h, pos, _args in u.facts.handlers:
            self.event_name(u, side, shared, ev, pos, added, all_added)
        for fn, ev, pos in u.facts.triggers:
            if fn == "triggerEvent":
                self.event_name(u, side, shared, ev, pos, added, all_added, trigger=True)
                continue
            target = "server" if "Server" in fn else "client"
            if side != "any" and target == side:
                continue          # triggerServerEvent in a server script is reported by WRONG_SIDE
            if ev in ref.events:
                continue
            tgt_units = [x for x in self.units if x.facts is not None and (target in x.sides)]
            if not tgt_units:
                continue          # the other side is not in this resource (or a single file)
            items = [it for x in tgt_units for it in x.facts.events_added.get(ev, [])]
            if not items:
                self.add(u, pos, "warn", "EVENT_NOT_REMOTE",
                         f"{fn}('{ev}'): no {target} script of this resource calls addEvent('{ev}', true) "
                         "(MTA drops the event)")
            elif not any(r is not False for _p, r in items):
                self.add(u, pos, "warn", "EVENT_NOT_REMOTE",
                         f"{fn}('{ev}'): the {target} adds it without allowRemoteTrigger (addEvent('{ev}', true))")

    def event_name(self, u: Unit, side: str, shared: bool, ev: str, pos: int, added: dict, all_added: dict,
                   trigger: bool = False) -> None:
        ref = self.ref
        if not ref:
            return
        sides = ref.event_sides(ev)
        if sides:
            if side != "any" and side not in sides:
                other = next(iter(sides))
                if shared:
                    self.add(u, pos, "info", "EVENT_WRONG_SIDE",
                             f"'{ev}' is a {other} event; on the {side} side of this shared script it never fires")
                else:
                    self.add(u, pos, "error", "EVENT_WRONG_SIDE",
                             f"'{ev}' is a {other} event: it never fires in a {side} script")
            return
        dep = ref.deprecation(ev, side) if side != "any" else None
        if dep is not None:
            self.deprecated(u, pos, ev, side, dep)
            return
        if ev in added or (side == "any" and ev in all_added):
            return
        if ev in all_added:
            self.add(u, pos, "warn", "EVENT_UNKNOWN",
                     f"'{ev}' is added with addEvent only on the other side: it never fires in this {side} script "
                     f"unless a {side} script adds it too")
            return
        sug = difflib.get_close_matches(ev, [e for e, s in ref.events.items() if side == "any" or side in s],
                                        n=2, cutoff=0.8)
        self.add(u, pos, "info" if trigger else "warn", "EVENT_UNKNOWN",
                 f"'{ev}' is not an MTA:SA {_SIDE_WORD.get(side, side)} event and no script of this resource adds "
                 "it with addEvent" + (f"; did you mean {', '.join(sug)}?" if sug else "")
                 + " (fine if another resource adds it)")

    # -- handlers -----------------------------------------------------------------------

    def handler_checks(self, u: Unit, side: str, side_funcs: dict) -> None:
        f = u.facts
        for ev, elem, h, pos, _args in f.handlers:
            fn = self.resolve_func(h, side_funcs, f) if h is not None else None
            if ev in self.render_events and side in ("client", "any") and fn is not None:
                self.render_heavy(u, ev, fn, side_funcs)
            if ev in ("onResourceStart", "onClientResourceStart") and fn is not None and elem is not None:
                if self.is_root(elem) and not _uses_resource(fn):
                    self.add(u, pos, "info", "RESOURCE_START_ROOT",
                             f"'{ev}' on root fires when ANY resource starts: attach it to resourceRoot or "
                             "check the started resource")
        for name, arg, pos in f.handler_args:
            callee = arg.fn if isinstance(arg, L.Call) else None
            target = self.resolve_func(callee, side_funcs, f) if callee is not None else None
            if target is not None:
                if _returns_function(target):
                    continue
            elif isinstance(callee, L.Name) and callee.var is None and callee.name in self.ref.funcs:
                sref = next(iter(self.ref.funcs[callee.name].values()))
                if "function" in (sref.ret or "") or callee.name in ("loadstring", "load", "getFunctionFromName"):
                    continue
            else:
                continue
            what = callee.name if isinstance(callee, L.Name) else "the function"
            self.add(u, pos, "warn", "HANDLER_CALLED",
                     f"{name} gets the result of {what}(...), which runs once now: pass the function itself "
                     f"({what}, or function() {what}(...) end)")
        if side in ("client", "any"):
            for name, call in f.gcalls:
                if name.startswith("dxDraw") and isinstance(call.fn, L.Name) and call.fn.func.line == 0:
                    self.add(u, call.fn.pos, "warn", "DRAW_OUTSIDE_RENDER",
                             f"{name} at the top level draws one frame at start: call it from an onClientRender "
                             "handler")

    def is_root(self, e) -> bool:
        if isinstance(e, L.Name) and e.var is None and e.name == "root":
            return True
        return isinstance(e, L.Call) and _gname(e.fn) == "getRootElement" and not e.args

    def render_heavy(self, u: Unit, ev: str, fn: L.Func, side_funcs: dict) -> None:
        heavy = self.facts["render_heavy"]
        seen_funcs = {id(fn)}
        todo = [(u, fn, None)]
        while todo:
            owner, f0, via = todo.pop()
            for call, cached in _calls_in(f0):
                name = _gname(call.fn)
                callee = None
                if name is not None and name in heavy:
                    if cached:
                        continue                              # assigned once to a persistent cache
                    if name == "getElementsByType" and len(call.args) > 2 and isinstance(call.args[2], L.TrueE):
                        continue                              # streamed-in elements only
                    where = f" (in {via}, called from the {ev} handler)" if via else f" in an {ev} handler"
                    self.add(owner, call.fn.pos, "warn", "RENDER_HEAVY",
                             f"{name}{where}: {heavy[name]}")
                    continue
                if not cached:
                    callee = self.resolve_func(call.fn, side_funcs, owner.facts)
                if callee is not None and id(callee) not in seen_funcs:
                    target = next((unit for unit in self.units if _in_unit(callee, unit)), None)
                    if target is not None:
                        seen_funcs.add(id(callee))
                        todo.append((target, callee, callee.name or "a function"))

    # -- meta ---------------------------------------------------------------------------

    def meta_checks(self) -> None:
        res = self.res
        for e in res.entries_of("export"):
            fn = e.attrs.get("function")
            t = e.attrs.get("type", "server").lower()
            if not fn:
                continue
            for s in {"server": ("server",), "client": ("client",), "shared": ("client", "server")}.get(t, ()):
                units = self.side_units(s)
                if not units:
                    continue
                if not any(fn in u.facts.gdefs for u in units):
                    res.add("warn", "meta.xml", e.line, "EXPORT_MISSING",
                            f"<export function=\"{fn}\" type=\"{t}\">: no {s} script defines a global function {fn}")
        dl = res.downloads()
        total = sum(b for _r, b, _k in dl)
        budget = self.budget_mb * 1024 * 1024
        if total > budget:
            res.add("warn", "meta.xml", 0, "DOWNLOAD_SIZE",
                    f"players download {total / 1048576:.1f} MB (budget {self.budget_mb:g} MB): compress textures "
                    "(satk texture optimize), drop unused files, or mark big optional files download=\"false\" and "
                    "fetch them with downloadFile")
        fbudget = self.facts["budget"]["file_mb"] * 1048576
        for rel, b, _k in dl:
            if b > fbudget:
                res.add("info", rel, 0, "FILE_SIZE", f"{b / 1048576:.1f} MB in one download file")


# --------------------------------------------------------------------------- small helpers


def _short(s: str, n: int = 60) -> str:
    s = s.replace("\n", "\\n")
    return s if len(s) <= n else s[:n - 3] + "..."


def _unparen(e):
    while isinstance(e, L.Paren):
        e = e.expr
    return e


def _lit_type(e) -> str | None:
    e = _unparen(e)
    t = type(e)
    if t is L.Str:
        return "string"
    if t is L.Num:
        return "number"
    if t is L.UnOp and e.op == "-" and _lit_type(e.operand) == "number":
        return "number"
    if t is L.TrueE or t is L.FalseE:
        return "bool"
    if t is L.Nil:
        return "nil"
    if t is L.Table:
        return "table"
    if t is L.Func:
        return "function"
    return None


def _param_at(v: list, i: int, exact: bool) -> tuple[str, bool] | None:
    """Type and optionality of the parameter that receives argument ``i`` (``None`` = unknown)."""
    for k, (_n, t, opt) in enumerate(v):
        if t == "..." or any(p in _VECTOR_ARITY or p in ("Matrix", "...") for p in t.split("|")):
            return None
        if not exact and opt:
            return None            # approximate signatures: trust only the leading required arguments
        if k == i:
            return t, opt
    return None


def _accepts(t: str, lt: str, node) -> bool:
    for p in t.split("|"):
        p = p.strip()
        if p in ("var", "...", "any", "") or p.startswith("CLua"):
            return True
        if p in _NUM_TYPES:
            if lt == "number" or (lt == "string" and _NUMERIC.match(node.value or "")):
                return True
            continue
        if p == "string":
            if lt in ("string", "number"):
                return True
            continue
        if p == "bool":
            if lt == "bool":
                return True
            continue
        if p in ("table", "function"):
            if lt == p:
                return True
            continue
        if p in _USERDATA:
            continue
        return True                # an unknown type name: do not judge
    return False


def _ref_key(e):
    """Identity of a variable or a constant-key field, independent of its source location."""
    e = _unparen(e)
    if isinstance(e, L.Name):
        return ("local", e.var) if e.var is not None else ("global", e.name)
    if isinstance(e, L.Index):
        base = _ref_key(e.obj)
        if base is not None and isinstance(e.key, (L.Str, L.Num)):
            return (base, type(e.key), e.key.value if isinstance(e.key, L.Str) else e.key.text)
    return None


def _persistent(e, fn: L.Func) -> bool:
    e = _unparen(e)
    if isinstance(e, L.Index):
        return _ref_key(e) is not None and _persistent(e.obj, fn)
    return isinstance(e, L.Name) and (e.var is None or e.var.func is not fn)


def _missing_refs(e) -> set:
    e = _unparen(e)
    if isinstance(e, L.BinOp) and e.op == "and":
        return _missing_refs(e.left) | _missing_refs(e.right)
    target = None
    if isinstance(e, L.UnOp) and e.op == "not":
        target = _unparen(e.operand)
        if isinstance(target, L.Call) and _gname(target.fn) == "isElement" and len(target.args) == 1:
            target = target.args[0]
    elif isinstance(e, L.BinOp) and e.op == "==":
        if isinstance(_unparen(e.right), (L.Nil, L.FalseE)):
            target = e.left
        elif isinstance(_unparen(e.left), (L.Nil, L.FalseE)):
            target = e.right
    key = _ref_key(target)
    return {key} if key is not None else set()


def _calls_in(fn: L.Func) -> list[tuple[L.Call, bool]]:
    """Calls in this function (not nested bodies); True only for persistent lazy-cache assignments."""
    out: list[tuple[L.Call, bool]] = []
    stack = [(st, set(), False) for st in reversed(fn.body)]
    while stack:
        x, missing, cached = stack.pop()
        if x is None:
            continue
        if isinstance(x, list):
            stack.extend((y, missing, cached) for y in reversed(x))
            continue
        if isinstance(x, tuple):
            stack.extend((y, missing, cached) for y in reversed(x) if y is not None)
            continue
        if not isinstance(x, L.Node) or isinstance(x, L.Func):
            continue
        t = type(x)
        if t is L.Call:
            out.append((x, cached))
            stack.append((x.args, missing, cached))
            stack.append((x.fn, missing, cached))
            continue
        if t is L.Assign:
            stack.append((x.targets, missing, cached))
            for i, expr in enumerate(x.exprs):
                target = x.targets[i] if i < len(x.targets) else None
                value = _unparen(expr)
                key = _ref_key(target)
                lazy = _persistent(target, fn) and (key in missing or
                    (isinstance(value, L.BinOp) and value.op == "or" and _ref_key(value.left) == key))
                stack.append((expr, missing, cached or lazy))
            continue
        if t is L.If:
            for c, b in x.tests:
                stack.append((c, missing, cached))
                stack.append((b, missing | _missing_refs(c), cached))
            if x.orelse is not None:
                stack.append((x.orelse, missing, cached))
            continue
        if t is L.Table:
            for k, v in x.items:
                stack.append((v, missing, cached))
                if k is not None:
                    stack.append((k, missing, cached))
            continue
        if t is L.MCall:
            stack.append((x.args, missing, cached))
            stack.append((x.obj, missing, cached))
            continue
        for fname in t.fields:
            stack.append((getattr(x, fname), missing, cached))
    return out


def _in_unit(fn: L.Func, u: Unit) -> bool:
    return any(f is fn for f in u.chunk.funcs) if u.chunk is not None else False


def _returns_function(fn: L.Func) -> bool:
    for node in L.walk(fn.body):
        if isinstance(node, L.Return):
            for e in node.exprs:
                if isinstance(e, (L.Func, L.Name, L.Index, L.Call, L.MCall, L.Paren)):
                    return True
    return False


def _uses_resource(fn: L.Func) -> bool:
    for node in L.walk(fn.body):
        if isinstance(node, L.Name):
            if node.var is None and node.name in ("resourceRoot", "resource", "source", "sourceResource",
                                                   "sourceResourceRoot", "getThisResource", "resourceName",
                                                   "getResourceName", "getResourceRootElement"):
                return True
            if node.var is not None and node.var in fn.params:
                return True
    return False


_MAP_TAG = re.compile(r"<([A-Za-z_][\w.-]*)")


def _map_tags(p: Path) -> set[str]:
    try:
        if p.stat().st_size > 64 * 1024 * 1024:
            return set()
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    return {t for t in _MAP_TAG.findall(text) if t != "map"}


# --------------------------------------------------------------------------- entry points


def _guess_side(rel: str) -> tuple[str, ...]:
    low = Path(rel).stem.lower()
    if low.startswith(("c_", "cl_")) or low.endswith(("_c", "_cl")) or "client" in low:
        return ("client",)
    if low.startswith(("s_", "sv_")) or low.endswith(("_s", "_sv")) or "server" in low:
        return ("server",)
    return ("any",)


def _unit(root: Path, rel: str, sides: tuple[str, ...], data: bytes | None = None) -> tuple[Unit, list[Finding]]:
    u = Unit(rel=rel, sides=sides)
    found: list[Finding] = []
    if data is None:
        p = root / rel
        try:
            if p.stat().st_size > MAX_SCRIPT:
                found.append(Finding("warn", rel, 0, 0, "SYNTAX", "larger than 16 MB: not checked"))
                return u, found
            data = p.read_bytes()
        except OSError as e:
            found.append(Finding("error", rel, 0, 0, "FILE_MISSING", f"cannot read: {e}"))
            return u, found
    text, problem = _decode(data)
    if problem in ("compiled", "obfuscated"):
        found.append(Finding("info", rel, 0, 0, "COMPILED", f"{problem} Lua: not checked"))
        return u, found
    if problem == "ansi":
        found.append(Finding("warn", rel, 0, 0, "ENCODING", "not UTF-8: MTA loads it as ANSI (the server's code "
                                                            "page); save the file as UTF-8"))
    u.src = L.Source(text)
    try:
        u.chunk = L.parse(u.src)
    except L.LuaSyntaxError as e:
        found.append(Finding("error", rel, e.line, e.col, "SYNTAX", _syntax_msg(e, u.src)))
        return u, found
    u.facts = collect(u.chunk)
    _ignores(u)
    return u, found


def _syntax_msg(e: L.LuaSyntaxError, src: L.Source) -> str:
    text = e.msg + (f" near '{_short(e.near, 50)}'" if e.near is not None else "")
    line = src.line_text(e.line)
    hint = ""
    if re.search(r"\bgoto\b|::\w+::", line):
        hint = "Lua 5.1 (MTA) has no goto/labels: restructure the loop or use a flag"
    elif re.search(r"\bcontinue\b", line):
        hint = "Lua has no 'continue': wrap the loop body in if/end or use repeat ... until true"
    elif "!=" in line:
        hint = "Lua writes 'not equal' as ~="
    elif "//" in line or re.search(r"<<|>>|[^~=<>]~[^=]|&|\|", line):
        hint = "Lua 5.1 (MTA) has no // or bitwise operators: use math.floor(a / b), bitAnd, bitOr, bitXor"
    elif re.search(r"\blocal\s+\w+\s*<\w+>", line):
        hint = "attributes like <const> and <close> are Lua 5.4"
    elif e.msg.startswith("malformed number") and ".." in (e.near or ""):
        hint = "put spaces around .. after a number (1 .. x)"
    return text + (f" ({hint})" if hint else "")


def lint_source(text: str, name: str = "script.lua", side: str = "any", ref: Reference | None = None) -> LintResult:
    """Lint one script given as text (tests, editors)."""
    ref = ref or Reference()
    sides = ("client", "server") if side == "shared" else (side,)
    u, found = _unit(Path("."), name, sides, text.encode("utf-8"))
    lt = _Linter(ref, None, [u], 20.0)
    lt.run()
    return LintResult(findings=_sorted(found + list(lt.out.values())), resource=None, units=[u], ref=ref,
                      sides=list(lt.sides))


MAX_ZIP = 512 * 1024 * 1024
MAX_ZIP_ENTRIES = 20000


def unzip_resource(z: Path) -> Path:
    """Extract a resource ``.zip`` under work/tmp/mta-lint/ (once per content); returns the folder with meta.xml."""
    import hashlib
    import shutil
    import stat
    import tempfile
    import zipfile
    import zlib
    from pathlib import PureWindowsPath

    from ..core.errors import SatkError
    from ..core.paths import atomic_write, ensure_writable, tmp

    h = hashlib.sha256()
    with open(z, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    stem = re.sub(r"[^A-Za-z0-9_-]", "_", z.stem) or "resource"
    if PureWindowsPath(stem).is_reserved():
        stem = "resource_" + stem
    base = tmp("mta-lint") / f"v2-{h.hexdigest()[:24]}-{stem}"
    dest = base / stem
    marker = base / ".done"             # never writable by an archive entry
    if not marker.is_file():
        try:
            with zipfile.ZipFile(z) as zf:
                infos = zf.infolist()
                if len(infos) > MAX_ZIP_ENTRIES or sum(i.file_size for i in infos) > MAX_ZIP:
                    raise SatkError("BAD_PARAMS", f"{z.name}: more than {MAX_ZIP_ENTRIES} entries or 512 MB unpacked")
                files, dirs, plan = set(), set(), []
                for i in infos:
                    name = i.orig_filename.replace("\\", "/")
                    parts = [x for x in name.split("/") if x not in ("", ".")]
                    if name.startswith("/") or any(p == ".." or p.endswith((".", " "))
                            or re.search(r'[<>:"|?*\x00-\x1f]', p) or PureWindowsPath(p).is_reserved() for p in parts):
                        raise SatkError("BAD_PARAMS", f"{z.name}: unsafe entry name {i.orig_filename!r}")
                    if stat.S_ISLNK(i.external_attr >> 16) or i.flag_bits & 1:
                        raise SatkError("BAD_PARAMS", f"{z.name}: symlink or encrypted entry {i.filename!r}")
                    if not parts:
                        continue
                    key = "/".join(parts).lower()
                    parents = {"/".join(parts[:n]).lower() for n in range(1, len(parts))}
                    directory = name.endswith("/")
                    if key in files or parents & files or (not directory and key in dirs):
                        raise SatkError("BAD_PARAMS", f"{z.name}: conflicting entry {i.filename!r}")
                    dirs.update(parents)
                    (dirs if directory else files).add(key)
                    if not directory:
                        plan.append((i, dest.joinpath(*parts)))
                ensure_writable(dest)
                dest.mkdir(parents=True, exist_ok=True)
                for i, out in plan:
                    ensure_writable(out)
                    out.parent.mkdir(parents=True, exist_ok=True)
                    fd, name = tempfile.mkstemp(prefix=".unzip-", suffix=".tmp", dir=out.parent)
                    staging = Path(name)
                    try:
                        with os.fdopen(fd, "wb") as output, zf.open(i) as source:
                            shutil.copyfileobj(source, output, 1 << 20)
                        os.replace(staging, out)
                    finally:
                        if staging.exists():
                            staging.unlink()
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError, zlib.error) as e:
            raise SatkError("BAD_PARAMS", f"{z.name}: cannot unpack resource zip: {e}",
                            hint="use a complete, unencrypted ZIP archive") from None
        atomic_write(marker, "")
    if not (dest / "meta.xml").is_file():
        subs = [d for d in dest.iterdir() if d.is_dir()]
        if len(subs) == 1 and (subs[0] / "meta.xml").is_file():
            return subs[0]
    return dest


def lint_path(path: Path, ref: Reference, side: str = "auto", budget_mb: float = 20.0) -> LintResult:
    """Lint a resource folder (with ``meta.xml``), its ``meta.xml``, a resource ``.zip`` or one ``.lua`` file."""
    path = Path(os.path.abspath(path))
    if path.is_file() and path.suffix.lower() == ".zip":
        path = unzip_resource(path)
    found: list[Finding] = []
    res: Resource | None = None
    units: list[Unit] = []
    if path.is_dir() or path.name.lower() == "meta.xml":
        root = path if path.is_dir() else path.parent
        res = load_resource(root)
        taken: set[tuple[str, str]] = set()
        for rel, sides, _e in res.scripts:
            sides = tuple(s for s in sides if s == "any" or (rel.lower(), s) not in taken)
            if not sides:
                continue                  # a duplicate <script>: MTA ignores it (meta.xml DUPLICATE_FILE)
            taken.update((rel.lower(), s) for s in sides)
            if rel.lower().endswith(".luac"):
                found.append(Finding("info", rel, 0, 0, "COMPILED", "compiled Lua: not checked"))
                continue
            if side != "auto" and res.meta is None:
                sides = ("client", "server") if side == "shared" else (side,)
            elif sides == ("any",):
                sides = _guess_side(rel)
            u, f = _unit(root, rel, sides)
            units.append(u)
            found += f
    else:
        sides = _guess_side(path.name) if side == "auto" else (("client", "server") if side == "shared" else (side,))
        u, f = _unit(path.parent, path.name, sides)
        units.append(u)
        found += f
    lt = _Linter(ref, res, units, budget_mb)
    lt.run()
    found += list(lt.out.values())
    if res is not None:
        found += res.findings
    return LintResult(findings=_sorted(found), resource=res, units=units, ref=ref, sides=list(lt.sides),
                      downloads=res.downloads() if res is not None and res.meta is not None else [])


def _sorted(fs: list[Finding]) -> list[Finding]:
    order = {s: i for i, s in enumerate(SEVERITIES)}
    return sorted(fs, key=lambda f: (order.get(f.sev, 9), f.file != "meta.xml", f.file, f.line, f.col, f.code))
