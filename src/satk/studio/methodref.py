"""Parameter tables of studio methods, read from their source code (no Blender needed).

A studio method is ``fn(ctx, p)`` that reads its parameters from the dict ``p``; there is no declared
schema. This module finds them in the function's syntax tree:

* ``p.get("key", default)``, ``p["key"]`` (required) and ``"key" in p``;
* helper calls ``helper(p, "key", ...)``: when the helper's definition is found (same module, a relative import or
  a ``satk``/``satk_blender`` module) its signature names the default (the parameter called ``default``) and a
  helper that raises when the value is missing makes the key required; otherwise the studio helpers
  (``num(p, key, method, default)``, ``text(..., required=True, choices=...)``) and the kit helpers
  (``num(p, key, default, method)``, ``need(p, key, method)``) are told apart by the third argument;
* ``names_of(p, ...)`` / ``resolve(ctx, p, ...)`` (``object`` or ``objects``) and ``mesh_obj(ctx, p, ...)``;
* functions that get ``p`` passed on, in the same module or imported (followed with the branch they are called in).

**Branches.** A key read only inside ``if``/``elif``/``else`` branches (or after an early ``return`` guard) is
not always needed: the table says when. Tests on a parameter are understood (``kind in ("cylinder", "cone")``,
``kind == "grid"``, ``p.get("points") is not None``, ``"span" in p``; a local such as ``kind = p.get("kind")``
and a helper's argument stand for the key they hold), so a key gets ``required_if`` (``"kind=cylinder|cone"``)
or ``when`` (an optional key used only then) instead of a plain ``required``. A guard ``if kind not in KINDS:
raise`` gives the key its allowed values. Other tests leave the key ``required_if: "some cases"``.

:func:`params_of` works on a live function (the session answers ``author.methods`` for one exact name with
it); :func:`scan` and :func:`render` read the plug-in modules statically for the generated reference
``docs/agent/studio-methods.md`` (``satk dev gen-docs``). Stdlib only: also imported inside Blender.
"""

from __future__ import annotations

import ast
import functools
import inspect
import os
import sys
from pathlib import Path
from typing import Any

__all__ = ["Param", "Expr", "params_of", "params_from_source", "scan", "render", "PLUGIN_MODULES", "rows"]

#: Plug-in modules of the reference, relative to ``blender/`` (packages are scanned file by file).
PLUGIN_MODULES = ("satk_blender/studio/methods", "satk_blender/kit/methods.py", "satk_blender/look/methods.py")
#: Helpers that make their key required.
_REQUIRED_HELPERS = frozenset({"need", "points2", "req", "require"})
_DEF_MAX = 40
#: Shown values of one condition (more are cut with "...").
_WHEN_MAX = 72
#: At most this many function visits per method (branches multiply the visits of shared helpers).
_VISITS_MAX = 400
_SRC_SATK = Path(__file__).resolve().parents[1]          # <checkout>/src/satk


class Param(dict):
    """``{"name", "default"?, "required"?, "required_if"?, "when"?, "choices"?}`` (a dict, so it is JSON as it is)."""


class Expr(str):
    """A default that is code, not a literal (``G.DIRT_DEFAULT``): shown as it is written."""


def _literal(node: ast.AST) -> Any:
    try:
        v = ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        try:
            s = ast.unparse(node)
        except Exception:  # noqa: BLE001
            return Expr("?")
        return Expr(s if len(s) <= _DEF_MAX else s[: _DEF_MAX - 3] + "...")
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    if isinstance(v, (list, tuple)) and len(repr(v)) <= _DEF_MAX:
        return list(v)
    return repr(v)[:_DEF_MAX]


def _is_name(node: ast.AST, name: str) -> bool:
    return isinstance(node, ast.Name) and node.id == name


def _str(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _func_name(call: ast.Call) -> str:
    f = call.func
    if isinstance(f, ast.Attribute):
        return f.attr
    if isinstance(f, ast.Name):
        return f.id
    return ""


# --------------------------------------------------------------------------- modules and imports


class _Mod:
    """One parsed source file: its top-level functions, literal constants and imports."""

    def __init__(self, path: str, tree: ast.Module):
        self.path = path
        self.tree = tree
        self.defs = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        self.consts: dict[str, tuple] = {}
        for n in tree.body:
            tgt, val = None, None
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
                tgt, val = n.targets[0].id, n.value
            elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.value is not None:
                tgt, val = n.target.id, n.value
            if tgt is not None:
                vals = _const_values(val, {})
                if vals is not None:
                    self.consts[tgt] = vals
        self.imports: dict[str, tuple[int, str, str | None]] = {}   # alias -> (level, module, name | None)
        for n in tree.body:
            if isinstance(n, ast.ImportFrom):
                for a in n.names:
                    self.imports[a.asname or a.name] = (n.level, n.module or "", a.name)
            elif isinstance(n, ast.Import):
                for a in n.names:
                    self.imports[a.asname or a.name.split(".")[0]] = (0, a.name if a.asname else a.name.split(".")[0],
                                                                      None)


def _const_values(node: ast.AST | None, consts: dict[str, tuple]) -> tuple | None:
    """Values of a literal collection (tuple, list, set, ``frozenset({...})``), a constant or a module constant."""
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, int, float)) \
            and not isinstance(node.value, bool):
        return (node.value,)
    if isinstance(node, ast.Name):
        return consts.get(node.id)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
            and node.func.id in ("frozenset", "set", "tuple") and len(node.args) == 1 and not node.keywords:
        return _const_values(node.args[0], consts)
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        out = []
        for e in node.elts:
            if not (isinstance(e, ast.Constant) and isinstance(e.value, (str, int, float))) \
                    or isinstance(e.value, bool):
                return None
            out.append(e.value)
        return tuple(out)
    return None


@functools.lru_cache(maxsize=128)
def _module(path: str) -> _Mod | None:
    try:
        tree = ast.parse(Path(path).read_text(encoding="utf-8"), filename=path)
    except (OSError, SyntaxError, ValueError):
        return None
    return _Mod(path, tree)


def _abs_module_file(modname: str, near: str) -> Path | None:
    """The source file of an absolute ``satk``/``satk_blender`` module (never imported for this)."""
    parts = modname.split(".")
    if parts[0] not in ("satk", "satk_blender"):
        return None
    loaded = sys.modules.get(modname)
    f = getattr(loaded, "__file__", None) if loaded is not None else None
    if f and f.endswith(".py"):
        return Path(f)
    if parts[0] == "satk":
        root = _SRC_SATK
    else:
        p = Path(near).resolve()
        root = next((q for q in p.parents if q.name == "satk_blender"), None)
        if root is None:
            return None
    base = root.joinpath(*parts[1:])
    for cand in (base.with_suffix(".py"), base / "__init__.py"):
        if cand.is_file():
            return cand
    return None


def _import_target(mod: _Mod, alias: str) -> tuple[Path | None, str | None]:
    """``(file, function name | None)`` an imported name stands for: a module file (``from . import _util as U``)
    or a function in a module file (``from .mesh import _fill_uvs``)."""
    imp = mod.imports.get(alias)
    if imp is None:
        return None, None
    level, module, name = imp
    if level >= 1:
        base = Path(mod.path).parent
        for _ in range(level - 1):
            base = base.parent
        pkg = base.joinpath(*module.split(".")) if module else base
        if name is not None:
            sub = pkg / f"{name}.py"
            if sub.is_file():
                return sub, None
            subpkg = pkg / name / "__init__.py"
            if subpkg.is_file():
                return subpkg, None
        f = pkg.with_suffix(".py") if module else None
        if f is not None and f.is_file():
            return f, name
        if (pkg / "__init__.py").is_file():
            return pkg / "__init__.py", name
        return None, None
    if name is not None:                                     # from satk.studio.mock import primitive_counts
        sub = _abs_module_file(f"{module}.{name}", mod.path)
        if sub is not None:
            return sub, None
        f = _abs_module_file(module, mod.path)
        return (f, name) if f is not None else (None, None)
    f = _abs_module_file(module, mod.path)                   # import satk.x as y
    return (f, None) if f is not None else (None, None)


def _consts(mod: _Mod) -> dict[str, tuple]:
    """Literal constants of the module plus the ones it imports by name (``from .mock import SOFT_KINDS``)."""
    cached = getattr(mod, "_all_consts", None)
    if cached is not None:
        return cached
    out = dict(mod.consts)
    for alias, (_lvl, _m, name) in mod.imports.items():
        if alias in out or name is None or not name.isupper():
            continue
        path, cname = _import_target(mod, alias)
        if path is not None and cname:
            m2 = _module(str(path))
            if m2 is not None and cname in m2.consts:
                out[alias] = m2.consts[cname]
    mod._all_consts = out  # type: ignore[attr-defined]
    return out


def _callee(mod: _Mod, call: ast.Call) -> tuple[_Mod, ast.FunctionDef] | None:
    """The definition a call goes to, when it is found in the sources."""
    f = call.func
    if isinstance(f, ast.Name):
        if f.id in mod.defs:
            return mod, mod.defs[f.id]
        path, fname = _import_target(mod, f.id)
        if path is not None and fname:
            m2 = _module(str(path))
            if m2 is not None and fname in m2.defs:
                return m2, m2.defs[fname]
        return None
    if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
        path, fname = _import_target(mod, f.value.id)
        if path is not None and fname is None:
            m2 = _module(str(path))
            if m2 is not None and f.attr in m2.defs:
                return m2, m2.defs[f.attr]
    return None


def _args_of(fn: ast.FunctionDef) -> list[str]:
    return [a.arg for a in fn.args.posonlyargs + fn.args.args]


def _raises_on_none(fn: ast.FunctionDef) -> bool:
    """``if v is None: raise ...`` directly in the helper: a missing value is an error (no ``required`` switch)."""
    for n in ast.walk(fn):
        if isinstance(n, ast.If) and isinstance(n.test, ast.Compare) and len(n.test.ops) == 1 \
                and isinstance(n.test.ops[0], ast.Is) and isinstance(n.test.comparators[0], ast.Constant) \
                and n.test.comparators[0].value is None and n.body and isinstance(n.body[0], ast.Raise):
            return True
    return False


# --------------------------------------------------------------------------- conditions

# A constraint is ("in", key, values, positive) | ("has", key, positive) | ("other",); a context is a tuple of them.
_OTHER = ("other",)


def _key_of(node: ast.AST, pname: str, aliases: dict[str, str]) -> str | None:
    """The parameter a test expression reads: ``p.get("k")``, ``p["k"]``, ``helper(p, "k", ...)``, an alias."""
    if isinstance(node, ast.Name):
        return aliases.get(node.id)
    if isinstance(node, ast.Subscript) and _is_name(node.value, pname):
        return _str(node.slice)
    if isinstance(node, ast.Call):
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr == "get" and _is_name(f.value, pname) and node.args:
            return _str(node.args[0])
        pos = next((i for i, a in enumerate(node.args) if _is_name(a, pname)), None)
        if pos is not None and pos + 1 < len(node.args):
            return _str(node.args[pos + 1])
        if isinstance(f, ast.Name) and f.id in ("str", "int", "float", "bool", "list", "tuple") and len(node.args) == 1:
            return _key_of(node.args[0], pname, aliases)
        if isinstance(f, ast.Attribute) and f.attr in ("lower", "strip", "upper") and isinstance(f.value, ast.AST):
            return _key_of(f.value, pname, aliases)
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or) and node.values:
        return _key_of(node.values[0], pname, aliases)
    return None


def _classify(test: ast.AST, pname: str, aliases: dict, consts: dict) -> tuple:
    """A test as a tuple of constraints (a conjunction); ``(_OTHER,)`` when it is not understood."""
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
        out: tuple = ()
        for v in test.values:
            out += _classify(v, pname, aliases, consts)
        return out
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.Or):
        parts = [_classify(v, pname, aliases, consts) for v in test.values]
        if all(len(c) == 1 and c[0][0] == "in" and c[0][3] for c in parts) and len({c[0][1] for c in parts}) == 1:
            vals: list = []
            for c in parts:
                vals += [x for x in c[0][2] if x not in vals]
            return (("in", parts[0][0][1], tuple(vals), True),)
        return (_OTHER,)
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        inner = _classify(test.operand, pname, aliases, consts)
        return _negate(inner)
    if isinstance(test, ast.Compare) and len(test.ops) == 1:
        op, left, right = test.ops[0], test.left, test.comparators[0]
        if isinstance(op, (ast.In, ast.NotIn)) and _is_name(right, pname) and _str(left):
            return (("has", _str(left), isinstance(op, ast.In)),)
        if isinstance(op, (ast.Is, ast.IsNot)) and isinstance(right, ast.Constant) and right.value is None:
            k = _key_of(left, pname, aliases)
            return (("has", k, isinstance(op, ast.IsNot)),) if k else (_OTHER,)
        if isinstance(op, (ast.In, ast.NotIn, ast.Eq, ast.NotEq)):
            k, other = _key_of(left, pname, aliases), right
            if k is None and isinstance(op, (ast.Eq, ast.NotEq)):
                k, other = _key_of(right, pname, aliases), left
            vals = _const_values(other, consts) if k else None
            if k and vals:
                return (("in", k, vals, isinstance(op, (ast.In, ast.Eq))),)
        return (_OTHER,)
    k = _key_of(test, pname, aliases)
    if k:
        return (("has", k, True),)
    return (_OTHER,)


def _negate(ctx: tuple) -> tuple:
    if len(ctx) != 1:
        return (_OTHER,)
    c = ctx[0]
    if c[0] == "in":
        return (("in", c[1], c[2], not c[3]),)
    if c[0] == "has":
        return (("has", c[1], not c[2]),)
    return (_OTHER,)


def _clean(ctx: tuple) -> bool:
    return bool(ctx) and all(c[0] != "other" for c in ctx)


def _join_vals(vals) -> str:
    s = "|".join(str(v) for v in vals)
    return s if len(s) <= _WHEN_MAX else s[: _WHEN_MAX - 3] + "..."


def _when(key: str, contexts: list[tuple], choices_of, *, required: bool) -> str | None:
    """One readable condition for the union of ``contexts`` (``None``: unconditional).

    Tests that are not understood are left out of a context (the condition said is a little wider than the real
    one); a read under such tests only reads as ``some cases`` for a required key, and as unconditional otherwise.
    """
    norm: list[tuple] = []
    vague = False
    for ctx in contexts:
        ctx = tuple(c for c in ctx if not (c[0] == "has" and c[1] == key))   # "if p.get(k)" needs no saying
        if not ctx:
            return None                                       # read without any condition somewhere
        clean = tuple(c for c in ctx if c[0] != "other")
        if not clean:
            vague = True
            continue
        norm.append(clean)
    if vague or not norm:
        return "some cases" if required else None
    # one key tested by value everywhere: a set of allowed values
    if all(all(c[0] == "in" for c in ctx) for ctx in norm) and len({c[1] for ctx in norm for c in ctx}) == 1:
        k = norm[0][0][1]
        universe = list(choices_of(k) or []) or None
        allowed: list = []                                     # union of the contexts
        excluded: set | None = None                            # co-finite part of the union (None = none)
        for ctx in norm:
            inc: list | None = None
            exc: set = set()
            for c in ctx:
                if c[3]:
                    inc = [v for v in c[2] if inc is None or v in inc]
                else:
                    exc |= set(c[2])
            if inc is not None:
                allowed += [v for v in inc if v not in exc and v not in allowed]
            elif universe is not None:
                allowed += [v for v in universe if v not in exc and v not in allowed]
            else:
                excluded = exc if excluded is None else (excluded & exc)
        if excluded is not None:
            excluded -= set(allowed)
            return f"{k} not {_join_vals(sorted(excluded, key=str))}" if excluded else None
        if universe is not None:
            allowed = [v for v in universe if v in allowed]
            rest = [v for v in universe if v not in allowed]
            if not rest:
                return None
            if allowed and len(_join_vals(rest)) + 4 < len(_join_vals(allowed)):
                return f"{k} not {_join_vals(rest)}"
        return f"{k}={_join_vals(allowed)}" if allowed else "never"
    # tests on given/omitted keys: say the positive ones ("with points")
    parts: list[str] = []
    for ctx in norm:
        with_ = any(c[0] == "has" and c[2] for c in ctx)      # "with points" says enough: drop the "without"s
        keep = [c for c in ctx if not (with_ and c[0] == "has" and not c[2])]
        s = " and ".join(_say(c) for c in keep)
        if s not in parts:
            parts.append(s)
    s = " or ".join(parts)
    return s if len(s) <= _WHEN_MAX else s[: _WHEN_MAX - 3] + "..."


def _say(c: tuple) -> str:
    if c[0] == "has":
        return f"with {c[1]}" if c[2] else f"without {c[1]}"
    if c[0] == "in":
        return f"{c[1]}={_join_vals(c[2])}" if c[3] else f"{c[1]} not {_join_vals(c[2])}"
    return "some cases"


# --------------------------------------------------------------------------- the collector


class _Collector:
    """Parameters of one function (and the helpers it hands ``p`` to)."""

    def __init__(self, mod: _Mod):
        self.root = mod
        self.out: dict[str, Param] = {}
        self.order: dict[str, tuple] = {}
        self.reads: dict[str, list[tuple[str, tuple]]] = {}   # key -> [(how, context)]
        self.seen: set = set()
        self.visits = 0

    def add(self, key: str, node: ast.AST, cond: tuple, *, default: Any = None, has_default: bool = False,
            required: bool = False, soft: bool = False, index: bool = False, choices: Any = None,
            depth: int = 0) -> None:
        """``required``: a helper says so; ``index``: read as ``p[key]`` (required unless also read softly);
        ``soft``: read with ``get`` or tested with ``in``."""
        if not key or len(key) > 48:
            return
        p = self.out.get(key)
        if p is None:
            p = self.out[key] = Param(name=key)
            self.order[key] = (depth, getattr(node, "lineno", 0), getattr(node, "col_offset", 0))
            self.reads[key] = []
        if has_default and default is not None and ("default" not in p or (isinstance(p["default"], Expr)
                                                                          and not isinstance(default, Expr))):
            p["default"] = default
        for how, on in (("required", required), ("soft", soft), ("index", index)):
            if on:
                self.reads[key].append((how, cond))
        if not (required or soft or index):
            self.reads[key].append(("soft", cond))
        if choices is not None and "choices" not in p and isinstance(choices, list):
            p["choices"] = choices

    def visit(self, mod: _Mod, fn, pname: str, aliases: dict[str, str] | None = None, cond: tuple = (),
              depth: int = 0) -> None:
        key = (mod.path, getattr(fn, "name", "<lambda>"), getattr(fn, "lineno", 0), pname,
               tuple(sorted((aliases or {}).items())), cond)
        if key in self.seen or depth > 4 or self.visits >= _VISITS_MAX:
            return
        self.seen.add(key)
        self.visits += 1
        al = dict(aliases or {})
        al.update(self._aliases(fn, pname, al))
        st = {"mod": mod, "pname": pname, "aliases": al, "depth": depth}
        body = fn.body if isinstance(fn.body, list) else [fn.body]
        self._body(body, cond, st)

    @staticmethod
    def _aliases(fn, pname: str, outer: dict[str, str]) -> dict[str, str]:
        """Locals that hold one parameter (``kind = p.get("kind")``); a name bound to two different keys is dropped."""
        found: dict[str, str | None] = {}
        for n in ast.walk(fn):
            tgt = val = None
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
                tgt, val = n.targets[0].id, n.value
            elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.value is not None:
                tgt, val = n.target.id, n.value
            if tgt is None or tgt == pname:
                continue
            k = _key_of(val, pname, {**outer, **{a: b for a, b in found.items() if b}})
            if tgt in found and found[tgt] != k:
                found[tgt] = None
            else:
                found[tgt] = k
        return {a: b for a, b in found.items() if b}

    def _body(self, stmts: list, cond: tuple, st: dict) -> None:
        extra: tuple = ()
        mod, pname, al = st["mod"], st["pname"], st["aliases"]
        for s in stmts:
            self._walk(s, cond + extra, st)
            if isinstance(s, ast.If) and not s.orelse and s.body:
                t = _classify(s.test, pname, al, _consts(mod))
                last = s.body[-1]
                if isinstance(last, ast.Raise) and len(t) == 1 and t[0][0] == "in" and not t[0][3] \
                        and isinstance(s.test, ast.Compare) and isinstance(s.test.ops[0], ast.NotIn) \
                        and len(t[0][2]) > 1:
                    k, vals = t[0][1], t[0][2]               # if kind not in KINDS: raise -> the allowed values
                    p = self.out.get(k)
                    if p is not None and "choices" not in p and all(isinstance(v, str) for v in vals):
                        p["choices"] = list(vals)
                    elif p is None:
                        self.add(k, s, cond + extra, soft=True, choices=[v for v in vals], depth=st["depth"])
                if isinstance(last, ast.Return) and _clean(t):
                    extra = extra + _negate(t) if len(t) == 1 else extra + (_OTHER,)

    def _walk(self, node: ast.AST, cond: tuple, st: dict) -> None:
        mod, pname, al = st["mod"], st["pname"], st["aliases"]
        if isinstance(node, ast.If):
            self._walk(node.test, cond, st)
            t = _classify(node.test, pname, al, _consts(mod))
            self._body(node.body, cond + t, st)
            self._body(node.orelse, cond + _negate(t), st)
            return
        if isinstance(node, ast.IfExp):
            self._walk(node.test, cond, st)
            t = _classify(node.test, pname, al, _consts(mod))
            self._walk(node.body, cond + t, st)
            self._walk(node.orelse, cond + _negate(t), st)
            return
        if isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            self._walk(node.iter if not isinstance(node, ast.While) else node.test, cond, st)
            self._body(node.body, cond, st)
            self._body(node.orelse, cond, st)
            return
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for it in node.items:
                self._walk(it.context_expr, cond, st)
            self._body(node.body, cond, st)
            return
        if isinstance(node, ast.Try):
            self._body(node.body, cond, st)
            for h in node.handlers:
                self._body(h.body, cond, st)
            self._body(node.orelse, cond, st)
            self._body(node.finalbody, cond, st)
            return
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self._body(node.body, cond, st)              # a nested function sees the same p
            return
        stop = self._inspect(node, cond, st)
        if stop:
            return
        for child in ast.iter_child_nodes(node):
            self._walk(child, cond, st)

    def _inspect(self, node: ast.AST, cond: tuple, st: dict) -> bool:
        """Record what ``node`` reads; ``True`` when its children need no walk."""
        pname, depth = st["pname"], st["depth"]
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or) and len(node.values) == 2:
            g = node.values[0]  # p.get("k") or <default>
            if isinstance(g, ast.Call) and isinstance(g.func, ast.Attribute) and g.func.attr == "get" \
                    and _is_name(g.func.value, pname) and len(g.args) == 1 and _str(g.args[0]):
                self.add(_str(g.args[0]), g, cond, default=_literal(node.values[1]), has_default=True, soft=True,
                         depth=depth)
        if isinstance(node, ast.Call):
            self._call(node, cond, st)
        elif isinstance(node, ast.Subscript) and _is_name(node.value, pname) and isinstance(node.ctx, ast.Load):
            k = _str(node.slice)
            if k:
                self.add(k, node, cond, index=True, depth=depth)
        elif isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], (ast.In, ast.NotIn)) \
                and _is_name(node.comparators[0], pname):
            k = _str(node.left)
            if k:
                self.add(k, node, cond, soft=True, depth=depth)
        return False

    def _call(self, call: ast.Call, cond: tuple, st: dict) -> None:
        mod, pname, al, depth = st["mod"], st["pname"], st["aliases"], st["depth"]
        f = call.func
        kw = {k.arg: k.value for k in call.keywords if k.arg}
        if isinstance(f, ast.Attribute) and f.attr in ("get", "pop", "setdefault") and _is_name(f.value, pname):
            k = _str(call.args[0]) if call.args else None
            if k:
                if len(call.args) > 1:
                    self.add(k, call, cond, default=_literal(call.args[1]), has_default=True, soft=True, depth=depth)
                else:
                    self.add(k, call, cond, soft=True, depth=depth)
            return
        pos = next((i for i, a in enumerate(call.args) if _is_name(a, pname)), None)
        if pos is None:
            return
        name = _func_name(call)
        if name in ("names_of", "resolve"):
            single = _str(kw.get("single")) or "object"
            many = _str(kw.get("key")) or "objects"
            self.add(f"{single}|{many}", call, cond, required=True, depth=depth)
            return
        if name == "mesh_obj":
            self.add("object", call, cond, required=True, depth=depth)
            return
        target = _callee(mod, call)
        k = _str(call.args[pos + 1]) if pos + 1 < len(call.args) else None
        if k is not None:
            self._helper(call, pos, k, kw, target, cond, depth)
            return
        if target is not None:                            # p handed on: follow it with the aliases it gets
            m2, fn = target
            args = _args_of(fn)
            if pos < len(args):
                sub: dict[str, str] = {}
                for i, a in enumerate(call.args):
                    if i < len(args) and i != pos:
                        kk = _key_of(a, pname, al)
                        if kk:
                            sub[args[i]] = kk
                for kname, v in kw.items():
                    kk = _key_of(v, pname, al)
                    if kk and kname in args:
                        sub[kname] = kk
                self.visit(m2, fn, args[pos], sub, cond, depth + 1)

    def _helper(self, call: ast.Call, pos: int, k: str, kw: dict, target, cond: tuple, depth: int) -> None:
        """``helper(p, "key", ...)``: default, required and choices from the call (and the helper's signature)."""
        name = _func_name(call)
        rest = call.args[pos + 2:]
        default, has = None, False
        raises = False
        if "default" in kw:
            default, has = _literal(kw["default"]), True
        elif target is not None:
            args = _args_of(target[1])
            if "default" in args:
                i = args.index("default") - pos             # the position of 'default' after p
                if 0 <= i < len(call.args) - pos and i >= 2:
                    default, has = _literal(call.args[pos + i]), True
            raises = _raises_on_none(target[1])
        elif rest:
            # studio helpers: (p, key, method, default); kit helpers: (p, key, default, method)
            method_arg = isinstance(rest[0], ast.Name) or ("." in (_str(rest[0]) or ""))
            if method_arg:
                if len(rest) > 1:
                    default, has = _literal(rest[1]), True
            else:
                default, has = _literal(rest[0]), True
        if has and default is None:
            has = False
        req = name in _REQUIRED_HELPERS or (isinstance(kw.get("required"), ast.Constant)
                                            and kw["required"].value is True) or (raises and not has)
        ch = _literal(kw["choices"]) if "choices" in kw else None
        if name == "axis" and ch is None:
            ch = ["x", "y", "z"]
        self.add(k, call, cond, default=default, has_default=has, required=req and not has, soft=not req,
                 choices=ch, depth=depth)

    def result(self) -> list[Param]:
        keys = sorted(self.out, key=lambda k: self.order[k])

        def choices_of(k: str):
            q = self.out.get(k)
            return q.get("choices") if q is not None else None

        out = []
        for k in keys:
            p = self.out[k]
            reads = self.reads[k]
            soft_ctx = [c for how, c in reads if how == "soft"]
            req_ctx = [c for how, c in reads if how == "required" or (how == "index" and not soft_ctx)]
            if "default" not in p and req_ctx:
                w = _when(k, req_ctx, choices_of, required=True)
                if w is None:
                    p["required"] = True
                elif w != "never":
                    p["required_if"] = w
            if not p.get("required") and not p.get("required_if"):
                w = _when(k, [c for _h, c in reads], choices_of, required=False)
                if w is not None and w != "never":
                    p["when"] = w
            out.append(p)
        return out


def _def_at(tree: ast.Module, name: str, line: int) -> ast.FunctionDef | None:
    hit = None
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            first = min([n.lineno] + [d.lineno for d in n.decorator_list])
            if first <= line <= (n.end_lineno or n.lineno):
                if hit is None or n.lineno >= hit.lineno:
                    hit = n
    return hit


def _pname(fn: ast.FunctionDef) -> str | None:
    args = _args_of(fn)
    return args[1] if len(args) >= 2 else None


def params_from_source(path: str, fn: ast.FunctionDef) -> list[Param]:
    mod = _module(path)
    if mod is None:
        try:
            mod = _Mod(path, ast.parse(Path(path).read_text(encoding="utf-8")))
        except (OSError, SyntaxError, ValueError):
            return []
    pname = _pname(fn)
    if not pname:
        return []
    c = _Collector(mod)
    c.visit(mod, fn, pname)
    return c.result()


def params_of(fn) -> list[Param]:
    """The parameters a live method function reads (wrappers such as the kit's ``_dff`` are unwrapped)."""
    try:
        fn = inspect.unwrap(fn)
        path = inspect.getsourcefile(fn)
        line = fn.__code__.co_firstlineno
    except (TypeError, AttributeError, ValueError):
        return []
    if not path:
        return []
    mod = _module(os.path.abspath(path))
    if mod is None:
        return []
    node = _def_at(mod.tree, fn.__name__, line)
    return params_from_source(os.path.abspath(path), node) if node is not None else []


def _show(v: Any) -> str:
    import json

    return str(v) if isinstance(v, Expr) else json.dumps(v, ensure_ascii=False)


def rows(params: list[Param]) -> list[list[str]]:
    """``[[name, default]]``: ``required``, ``required if <condition>``, the default as JSON (code as written) with
    ``if <condition>`` for a key used only then, or ``""``; allowed values follow in brackets (``"z" (x|y|z)``)."""
    out = []
    for p in params:
        if p.get("required"):
            d = "required"
        elif p.get("required_if"):
            d = f"required if {p['required_if']}"
        else:
            d = _show(p["default"]) if "default" in p else ""
            if p.get("when"):
                d = (d + " " if d else "") + f"if {p['when']}"
        if p.get("choices") and isinstance(p["choices"], list):
            d = (d + " " if d else "") + f"({'|'.join(str(x) for x in p['choices'][:12])})"
        out.append([p["name"], d])
    return out


# --------------------------------------------------------------------------- static scan (the reference)


def _target(node: ast.AST) -> tuple[str | None, list[str]]:
    """Function name behind a METHODS value (``fn``, ``wrap(fn)``) and the wrapper names."""
    wraps: list[str] = []
    while isinstance(node, ast.Call) and node.args:
        wraps.append(_func_name(node))
        node = node.args[0]
    return (node.id if isinstance(node, ast.Name) else None), wraps


def _flags(fn: ast.FunctionDef, wraps: list[str]) -> bool:
    names = {d.id if isinstance(d, ast.Name) else getattr(d, "attr", "") for d in fn.decorator_list}
    return "readonly" in names or "readonly" in wraps


def _scan_file(path: Path) -> list[dict]:
    mod = _module(str(path))
    if mod is None:
        return []
    tree, defs = mod.tree, mod.defs
    tables: list[ast.Dict] = []
    for n in tree.body:
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Dict) \
                and any(_is_name(t, "METHODS") for t in n.targets):
            tables.append(n.value)
        elif isinstance(n, ast.Expr) and isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Attribute) \
                and n.value.func.attr == "update" and _is_name(n.value.func.value, "METHODS"):
            tables += [a for a in n.value.args if isinstance(a, ast.Dict)]  # METHODS.update({...}) adds methods too
    if not tables:
        return []
    out = []
    for k, v in ((k, v) for table in tables for k, v in zip(table.keys, table.values)):
        name = _str(k)
        if not name:
            continue
        fname, wraps = _target(v)
        fn, where = (defs.get(fname), path) if fname else (None, path)
        if fn is None and fname:
            other, oname = _import_target(mod, fname)
            if other is not None:
                m2 = _module(str(other))
                if m2 is not None:
                    fn, where = m2.defs.get(oname or fname), other
        row: dict[str, Any] = {"name": name, "module": path.stem}
        if fn is not None:
            doc = (ast.get_docstring(fn) or "").strip().splitlines()
            row["doc"] = doc[0].strip() if doc else ""
            row["readonly"] = _flags(fn, wraps)
            row["params"] = params_from_source(str(where), fn)
        out.append(row)
    return out


def _builtins() -> list[dict]:
    """The ``session.*`` methods every host serves (nested functions of :mod:`satk.studio.core`)."""
    from . import core as K

    table = K._builtin_methods(None)  # noqa: SLF001 - the closures only need the core when they run
    out = []
    for name, m in table.items():
        doc = (m.fn.__doc__ or "").strip().splitlines()
        out.append({"name": name, "module": "core", "doc": doc[0].strip() if doc else "",
                    "readonly": m.readonly, "params": params_of(m.fn)})
    return out


def scan(blender_dir: str | os.PathLike | None = None) -> list[dict]:
    """``[{name, module, doc, readonly, params}]`` of every plug-in method, sorted by name."""
    if blender_dir is None:
        from ..core.config import REPO_ROOT

        blender_dir = REPO_ROOT / "blender"
    base = Path(blender_dir)
    rows_: dict[str, dict] = {}
    for rel in PLUGIN_MODULES:
        p = base / rel
        files = sorted(f for f in p.glob("*.py") if not f.name.startswith("_")) if p.is_dir() else [p]
        for f in files:
            for r in _scan_file(f):
                rows_.setdefault(r["name"], r)
    for r in _builtins():
        rows_.setdefault(r["name"], r)
    return [rows_[k] for k in sorted(rows_)]


def _md(s: Any) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def _param_cell(params: list[Param]) -> str:
    parts = []
    for p, (name, d) in zip(params, rows(params)):
        if p.get("required"):
            parts.append(f"**{name}**{d[8:]}")
        elif p.get("required_if"):
            parts.append(f"{name} ({d})")
        elif not d or d.startswith("(") or d.startswith("if "):
            parts.append(f"{name} {d}".strip() if not d.startswith("if ") else f"{name} ({d})")
        elif p.get("when"):
            dv, _sep, rest = d.partition(" if ")
            parts.append(f"{name}={dv} (if {rest})")
        else:
            parts.append(f"{name}={d}")
    return ", ".join(parts)


def render(blender_dir: str | os.PathLike | None = None) -> str:
    """The generated reference ``docs/agent/studio-methods.md`` (deterministic)."""
    ms = scan(blender_dir)
    lines = ["<!-- generated by `satk dev gen-docs` from the studio plug-in sources; do not edit by hand -->",
             "", "# Studio methods (generated reference)", "",
             f"{len(ms)} methods of a live Blender session (`satk blender call <method> --params JSON`). One line "
             "each: **bold** = required, `(required if kind=a|b)` = required only then, `name=default (if ...)` = used "
             "only then, `(a|b)` = allowed values; `object|objects` = one name or a list of names/globs. Mesh edits "
             "also take a face selector `select` (`side`, `normal`+`within`, `where`, `box`, `group`, `material`, "
             "`near`, `loop`, `grow`, `linked`, `item`, `invert`). `satk blender methods --query <name>` gives the "
             "full help of one method.", "", "| method | ro | parameters | what it does |", "|---|---|---|---|"]
    for m in ms:
        cell = _param_cell(m.get("params") or [])
        lines.append(f"| `{m['name']}` | {'ro' if m.get('readonly') else ''} | {_md(cell) or '-'} | "
                     f"{_md(m.get('doc') or '')} |")
    lines.append("")
    return "\n".join(lines)
