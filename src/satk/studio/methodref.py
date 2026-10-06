"""Parameter tables of studio methods, read from their source code (no Blender needed).

A studio method is ``fn(ctx, p)`` that reads its parameters from the dict ``p``; there is no declared
schema. This module finds them in the function's syntax tree:

* ``p.get("key", default)``, ``p["key"]`` (required) and ``"key" in p``;
* helper calls ``helper(p, "key", ...)``: the studio helpers (``num(p, key, method, default)``,
  ``text(..., required=True, choices=...)``) and the kit helpers (``num(p, key, default, method)``,
  ``need(p, key, method)``);
* ``names_of(p, ...)`` / ``resolve(ctx, p, ...)`` (``object`` or ``objects``) and ``mesh_obj(ctx, p, ...)``;
* functions of the same module that get ``p`` passed on (followed once each).

:func:`params_of` works on a live function (the session answers ``author.methods`` for one exact name with
it); :func:`scan` and :func:`render` read the plug-in modules statically for the generated reference
``docs/agent/studio-methods.md`` (``satk dev gen-docs``). Stdlib only: also imported inside Blender.
"""

from __future__ import annotations

import ast
import functools
import inspect
import os
from pathlib import Path
from typing import Any

__all__ = ["Param", "Expr", "params_of", "params_from_source", "scan", "render", "PLUGIN_MODULES", "rows"]

#: Plug-in modules of the reference, relative to ``blender/`` (packages are scanned file by file).
PLUGIN_MODULES = ("satk_blender/studio/methods", "satk_blender/kit/methods.py", "satk_blender/look/methods.py")
#: Helpers that make their key required.
_REQUIRED_HELPERS = frozenset({"need", "points2", "req", "require"})
_DEF_MAX = 40


class Param(dict):
    """``{"name", "default"?, "required"?, "choices"?}`` (a dict, so it is JSON as it is)."""


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


class _Collector:
    """Parameters of one function (and the same-module helpers it hands ``p`` to)."""

    def __init__(self, defs: dict[str, ast.FunctionDef]):
        self.defs = defs
        self.out: dict[str, Param] = {}
        self.order: dict[str, tuple] = {}
        self.how: dict[str, set] = {}
        self.seen: set[tuple[str, str]] = set()

    def add(self, key: str, node: ast.AST, *, default: Any = None, has_default: bool = False,
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
            self.how[key] = set()
        if has_default and "default" not in p and default is not None:
            p["default"] = default
        self.how[key] |= {x for x, on in (("required", required), ("soft", soft), ("index", index)) if on}
        if choices is not None and "choices" not in p and isinstance(choices, list):
            p["choices"] = choices

    def visit(self, fn: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda, pname: str, depth: int = 0) -> None:
        key = (getattr(fn, "name", "<lambda>"), pname)
        if key in self.seen or depth > 4:
            return
        self.seen.add(key)
        for node in ast.walk(fn):
            if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or) and len(node.values) == 2:
                g = node.values[0]  # p.get("k") or <default>
                if isinstance(g, ast.Call) and isinstance(g.func, ast.Attribute) and g.func.attr == "get" \
                        and _is_name(g.func.value, pname) and len(g.args) == 1 and _str(g.args[0]):
                    self.add(_str(g.args[0]), g, default=_literal(node.values[1]), has_default=True, soft=True,
                             depth=depth)
            if isinstance(node, ast.Call):
                self._call(node, pname, depth)
            elif isinstance(node, ast.Subscript) and _is_name(node.value, pname) and isinstance(node.ctx, ast.Load):
                k = _str(node.slice)
                if k:
                    self.add(k, node, index=True, depth=depth)
            elif isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], (ast.In, ast.NotIn)) \
                    and _is_name(node.comparators[0], pname):
                k = _str(node.left)
                if k:
                    self.add(k, node, soft=True, depth=depth)

    def _call(self, call: ast.Call, pname: str, depth: int) -> None:
        f = call.func
        kw = {k.arg: k.value for k in call.keywords if k.arg}
        if isinstance(f, ast.Attribute) and f.attr in ("get", "pop", "setdefault") and _is_name(f.value, pname):
            k = _str(call.args[0]) if call.args else None
            if k:
                if len(call.args) > 1:
                    self.add(k, call, default=_literal(call.args[1]), has_default=True, soft=True, depth=depth)
                else:
                    self.add(k, call, soft=True, depth=depth)
            return
        pos = next((i for i, a in enumerate(call.args) if _is_name(a, pname)), None)
        if pos is None:
            return
        name = _func_name(call)
        if name in ("names_of", "resolve"):
            single = _str(kw.get("single")) or "object"
            many = _str(kw.get("key")) or "objects"
            self.add(f"{single}|{many}", call, required=True, depth=depth)
            return
        if name == "mesh_obj":
            self.add("object", call, required=True, depth=depth)
            return
        k = _str(call.args[pos + 1]) if pos + 1 < len(call.args) else None
        if k is not None:
            rest = call.args[pos + 2:]
            default, has = None, False
            if "default" in kw:
                default, has = _literal(kw["default"]), True
            elif rest:
                # studio helpers: (p, key, method, default); kit helpers: (p, key, default, method)
                method_arg = isinstance(rest[0], ast.Name) or ("." in (_str(rest[0]) or ""))
                if method_arg:
                    if len(rest) > 1:
                        default, has = _literal(rest[1]), True
                else:
                    default, has = _literal(rest[0]), True
            req = name in _REQUIRED_HELPERS or (isinstance(kw.get("required"), ast.Constant)
                                                and kw["required"].value is True)
            ch = _literal(kw["choices"]) if "choices" in kw else None
            if name == "axis" and ch is None:
                ch = ["x", "y", "z"]
            self.add(k, call, default=default, has_default=has, required=req and not has, soft=not req,
                     choices=ch, depth=depth)
            return
        callee = self.defs.get(name) if isinstance(f, ast.Name) else None
        if callee is not None:
            args = [a.arg for a in callee.args.posonlyargs + callee.args.args]
            if pos < len(args):
                self.visit(callee, args[pos], depth + 1)

    def result(self) -> list[Param]:
        keys = sorted(self.out, key=lambda k: self.order[k])
        out = []
        for k in keys:
            p = self.out[k]
            how = self.how[k]
            if "default" not in p and ("required" in how or ("index" in how and "soft" not in how)):
                p["required"] = True
            out.append(p)
        return out


@functools.lru_cache(maxsize=64)
def _module(path: str) -> tuple[ast.Module, dict[str, ast.FunctionDef]] | None:
    try:
        tree = ast.parse(Path(path).read_text(encoding="utf-8"), filename=path)
    except (OSError, SyntaxError, ValueError):
        return None
    defs = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    return tree, defs


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
    args = [a.arg for a in fn.args.posonlyargs + fn.args.args]
    return args[1] if len(args) >= 2 else None


def params_from_source(path: str, fn: ast.FunctionDef) -> list[Param]:
    mod = _module(path)
    defs = mod[1] if mod else {}
    pname = _pname(fn)
    if not pname:
        return []
    c = _Collector(defs)
    c.visit(fn, pname)
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
    node = _def_at(mod[0], fn.__name__, line)
    return params_from_source(os.path.abspath(path), node) if node is not None else []


def _show(v: Any) -> str:
    import json

    return str(v) if isinstance(v, Expr) else json.dumps(v, ensure_ascii=False)


def rows(params: list[Param]) -> list[list[str]]:
    """``[[name, default]]``: ``required``, the default as JSON (code as written), or ``""``; allowed values
    follow in brackets (``"z" (x|y|z)``)."""
    out = []
    for p in params:
        if p.get("required"):
            d = "required"
        elif "default" in p:
            d = _show(p["default"])
        else:
            d = ""
        if p.get("choices") and isinstance(p["choices"], list):
            d = (d + " " if d else "") + f"({'|'.join(str(x) for x in p['choices'][:8])})"
        out.append([p["name"], d])
    return out


# --------------------------------------------------------------------------- static scan (the reference)


def _resolve_import(tree: ast.Module, name: str, path: Path) -> Path | None:
    for n in tree.body:
        if isinstance(n, ast.ImportFrom) and n.level >= 1 and any((a.asname or a.name) == name for a in n.names):
            base = path.parent
            for _ in range(n.level - 1):
                base = base.parent
            mod = (n.module or "").split(".") if n.module else []
            cand = base.joinpath(*mod).with_suffix(".py") if mod else None
            if cand is not None and cand.is_file():
                return cand
    return None


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
    tree, defs = mod
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
            other = _resolve_import(tree, fname, path)
            if other is not None:
                m2 = _module(str(other))
                if m2 is not None:
                    fn, where = m2[1].get(fname), other
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
    for name, d in rows(params):
        if d.startswith("required"):
            parts.append(f"**{name}**{d[8:]}")
        elif not d or d.startswith("("):
            parts.append(f"{name} {d}".strip())
        else:
            parts.append(f"{name}={d}")
    return ", ".join(parts)


def render(blender_dir: str | os.PathLike | None = None) -> str:
    """The generated reference ``docs/agent/studio-methods.md`` (deterministic)."""
    ms = scan(blender_dir)
    lines = ["<!-- generated by `satk dev gen-docs` from the studio plug-in sources; do not edit by hand -->",
             "", "# Studio methods (generated reference)", "",
             f"{len(ms)} methods of a live Blender session (`satk blender call <method> --params JSON`). One line "
             "each: **bold** = required, `name=default`, `(a|b)` = allowed values; `object|objects` = one name or a "
             "list of names/globs. Mesh edits also take a face selector `select` (`side`, `normal`+`within`, "
             "`where`, `box`, `group`, `material`). `satk blender methods --query <name>` gives the full help of "
             "one method.", "", "| method | ro | parameters | what it does |", "|---|---|---|---|"]
    for m in ms:
        cell = _param_cell(m.get("params") or [])
        lines.append(f"| `{m['name']}` | {'ro' if m.get('readonly') else ''} | {_md(cell) or '-'} | "
                     f"{_md(m.get('doc') or '')} |")
    lines.append("")
    return "\n".join(lines)
