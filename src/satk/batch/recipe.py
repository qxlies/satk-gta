"""Recipes: a named list of registered operations with variables and references to earlier results.

A recipe is a YAML (subset, :mod:`satk.batch.yamlite`), JSON or TOML file::

    name: lint-one-file
    summary: Lint a file and dump it.
    vars:
      file: {required: true, help: "a DFF/TXD/COL file"}
      sev: warn                       # shorthand: a default value
    steps:
      - id: lint
        op: asset.lint                # or a list of candidates: [texture.audit, txd.audit]
        input: ${file}                # goes to the operation's first required parameter
        args: {sev: "${sev}"}
        on_error: continue            # stop (default) | continue
        fail_if: "${steps.lint.summary.error|default:0}"   # turn an ok answer into a failure
      - id: dump
        op: formats.dump
        input: ${file}
        when: "${steps.lint.ok}"      # run only when this is true (unless: the opposite)

References ``${...}``: a variable (``${file}``), a builtin (``${work}`` ``${cwd}`` ``${recipe_dir}``
``${recipe}``) or a field of an earlier step's answer (``${steps.lint.summary.fatal}``,
``${steps.list.rows.0.0}``, ``${steps.list.col.path}`` = a column as a list). Filters after ``|``: ``not``
``bool`` ``len`` ``first`` ``json`` ``name`` ``stem`` ``parent`` ``ext`` ``lower`` ``eq:<value>`` (true when
equal, text compared case-insensitively) ``default:<value>`` (used when the value is missing or null). A
string that is exactly one reference takes the value as it is (a list, a number); inside a longer string it is
formatted as text. ``$${`` is a literal ``${``. Arguments that resolve to ``null`` are left out.

Run rules: an operation that is not registered (another package not installed yet) makes its step
``skipped: op not available`` -- also when an operation listed under the step's ``requires`` is missing (a
``batch`` step whose inner operation may not be installed); a step that needs a skipped step is skipped too
(unless its reference has a ``default:``). Operations an agent may not run need ``--yes`` on the command line
(:func:`common.refusal`), checked
for every step before anything runs. A failed step stops the recipe unless ``on_error: continue``; any failed
step makes the answer ``CHECK_FAILED``. ``dry_run`` validates and shows the plan without running or writing
anything.
"""

from __future__ import annotations

import contextvars
import difflib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.envelope import dumps, table
from ..core.errors import SatkError
from ..core.paths import atomic_write, cfg, jpath
from ..core.registry import OpSpec, invoke, progress_handler, report_progress
from .common import find_op, input_param, nested, one_line, refusal

__all__ = ["Recipe", "Step", "Var", "load_recipe", "parse_recipe", "list_recipes", "run_recipe", "show_recipe",
           "FILTERS", "user_dir"]

FILTERS = ("not", "bool", "len", "first", "json", "name", "stem", "parent", "ext", "lower", "eq", "default")
BUILTINS = ("work", "cwd", "recipe_dir", "recipe")
EXTS = (".yaml", ".yml", ".json", ".toml")
_TOP = ("name", "summary", "description", "vars", "steps")
_STEP = ("id", "op", "input", "args", "when", "unless", "on_error", "fail_if", "requires", "note")
_VAR = ("default", "required", "help")
_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
_REF = re.compile(r"\$\$\{|\$\{([^}]*)\}")
_UNSET: Any = object()
_running: contextvars.ContextVar[tuple[str, ...]] = contextvars.ContextVar("satk_recipe_chain", default=())
_FALSY = ("", "0", "false", "no", "off", "none", "null")


def _bad(src: str, msg: str, **kw) -> SatkError:
    return SatkError("BAD_PARAMS", f"{src}: {msg}", **kw)


@dataclass
class Var:
    name: str
    default: Any = None
    required: bool = False
    help: str = ""


@dataclass
class Step:
    id: str
    ops: list[str]
    input: Any = _UNSET
    args: dict = field(default_factory=dict)
    when: Any = _UNSET
    unless: Any = _UNSET
    on_error: str = "stop"
    fail_if: Any = _UNSET
    note: str = ""
    requires: list[str] = field(default_factory=list)


@dataclass
class Recipe:
    name: str
    summary: str
    description: str
    vars: dict[str, Var]
    steps: list[Step]
    source: str          # shipped | user | file
    path: Path | None = None


# --------------------------------------------------------------------------- loading


def user_dir() -> Path:
    """``<work>/recipes``: the user's own recipes (listed and found by name; they win over shipped ones)."""
    return Path(os.path.abspath(cfg().paths.work)) / "recipes"


def _shipped() -> dict[str, str]:
    """``name -> data path`` of the recipes shipped in ``data/recipes``."""
    from ..core import resources

    out: dict[str, str] = {}
    for ext in EXTS:
        try:
            names = resources.list_files("recipes", pattern=f"*{ext}")
        except SatkError:
            names = []
        for n in names:
            stem = n.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            out.setdefault(stem, "recipes/" + n.rsplit("/", 1)[-1])
    return out


def _user() -> dict[str, Path]:
    d = user_dir()
    out: dict[str, Path] = {}
    if d.is_dir():
        for p in sorted(d.iterdir(), key=lambda q: q.name.lower()):
            if p.is_file() and p.suffix.lower() in EXTS:
                out.setdefault(p.stem, p)
    return out


def _decode(text: str, suffix: str, src: str) -> Any:
    if suffix == ".json":
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise _bad(src, f"invalid JSON: {e}") from None
    if suffix == ".toml":
        import tomllib

        try:
            return tomllib.loads(text)
        except tomllib.TOMLDecodeError as e:
            raise _bad(src, f"invalid TOML: {e}") from None
    from .yamlite import loads

    return loads(text, source=src)


def load_recipe(ref: str) -> Recipe:
    """A recipe by file path or name (``<work>/recipes`` first, then the shipped ``data/recipes``)."""
    if not isinstance(ref, str) or not ref.strip():
        raise SatkError("BAD_PARAMS", "give a recipe name or file", hint="satk recipe list")
    ref = ref.strip()
    p = Path(ref)
    looks_like_file = p.suffix.lower() in EXTS or "/" in ref or "\\" in ref
    if p.is_file():
        return parse_recipe(_decode(p.read_text(encoding="utf-8-sig"), p.suffix.lower(), p.name), source="file",
                            path=p, default_name=p.stem)
    if looks_like_file:
        raise SatkError("NOT_FOUND", f"recipe file not found: {jpath(p)}", hint="satk recipe list")
    user = _user()
    if ref in user:
        q = user[ref]
        return parse_recipe(_decode(q.read_text(encoding="utf-8-sig"), q.suffix.lower(), q.name), source="user",
                            path=q, default_name=q.stem)
    shipped = _shipped()
    if ref in shipped:
        from ..core import resources

        rel = shipped[ref]
        text = resources.read_text(rel)
        return parse_recipe(_decode(text, Path(rel).suffix.lower(), rel), source="shipped",
                            path=resources.data_path(rel), default_name=ref)
    names = sorted(set(user) | set(shipped))
    raise SatkError("NOT_FOUND", f"no recipe {ref!r}",
                    did_you_mean=difflib.get_close_matches(ref, names, n=3, cutoff=0.4),
                    hint="satk recipe list (or give a .yaml file)")


def _check_keys(d: dict, allowed: tuple[str, ...], where: str, src: str) -> None:
    for k in d:
        if k not in allowed:
            raise _bad(src, f"{where}: unknown key {k!r}", did_you_mean=difflib.get_close_matches(str(k), allowed, n=2),
                       hint=f"allowed: {', '.join(allowed)}")


def parse_recipe(data: Any, *, source: str = "file", path: Path | None = None, default_name: str = "recipe") -> Recipe:
    """Validate a decoded recipe document (see the module docstring); ``BAD_PARAMS`` names the problem."""
    src = path.name if path is not None else default_name
    if not isinstance(data, dict):
        raise _bad(src, "a recipe is a mapping with 'steps' (and optional name, summary, vars)")
    _check_keys(data, _TOP, "recipe", src)
    name = str(data.get("name") or default_name)
    vars_: dict[str, Var] = {}
    raw_vars = data.get("vars") or {}
    if not isinstance(raw_vars, dict):
        raise _bad(src, "'vars' must be a mapping name -> default or {default, required, help}")
    for k, v in raw_vars.items():
        if not _ID.match(str(k)) or "-" in str(k):
            raise _bad(src, f"bad variable name {k!r} (letters, digits, _)")
        if k in BUILTINS or k == "steps":
            raise _bad(src, f"variable name {k!r} is reserved ({', '.join(BUILTINS)}, steps)")
        if isinstance(v, dict):
            _check_keys(v, _VAR, f"vars.{k}", src)
            vars_[k] = Var(k, v.get("default"), bool(v.get("required", False)), str(v.get("help") or ""))
        else:
            vars_[k] = Var(k, v)
        _check_refs(vars_[k].default, {n: x for n, x in vars_.items() if n != k}, set(), src, f"vars.{k} default")
    raw_steps = data.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise _bad(src, "'steps' must be a non-empty list")
    steps: list[Step] = []
    ids: set[str] = set()
    for i, s in enumerate(raw_steps, 1):
        if not isinstance(s, dict):
            raise _bad(src, f"step {i}: expected a mapping with 'op'")
        _check_keys(s, _STEP, f"step {i}", src)
        sid = str(s.get("id") or f"step{i}")
        if not _ID.match(sid):
            raise _bad(src, f"step {i}: bad id {sid!r}")
        if sid in ids:
            raise _bad(src, f"step {i}: duplicate id {sid!r}")
        ops = s.get("op")
        ops = [ops] if isinstance(ops, str) else ops
        if not isinstance(ops, list) or not ops or not all(isinstance(o, str) and o.strip() for o in ops):
            raise _bad(src, f"step {sid}: 'op' must be an operation name or a list of candidates")
        args = s.get("args") or {}
        if not isinstance(args, dict):
            raise _bad(src, f"step {sid}: 'args' must be a mapping")
        on_error = s.get("on_error", "stop")
        if on_error not in ("stop", "continue"):
            raise _bad(src, f"step {sid}: on_error must be stop or continue")
        requires = s.get("requires") or []
        requires = [requires] if isinstance(requires, str) else requires
        if not isinstance(requires, list) or not all(isinstance(r, str) and r.strip() for r in requires):
            raise _bad(src, f"step {sid}: 'requires' must be an operation name or a list of them")
        step = Step(sid, [o.strip() for o in ops], s.get("input", _UNSET),
                    {str(k).replace("-", "_"): v for k, v in args.items()},
                    s.get("when", _UNSET), s.get("unless", _UNSET), on_error, s.get("fail_if", _UNSET),
                    str(s.get("note") or ""), [r.strip() for r in requires])
        for what, val in (("input", step.input), ("args", step.args), ("when", step.when), ("unless", step.unless)):
            _check_refs(val, vars_, ids, src, f"step {sid} {what}")
        _check_refs(step.fail_if, vars_, ids | {sid}, src, f"step {sid} fail_if")
        ids.add(sid)
        steps.append(step)
    return Recipe(name, str(data.get("summary") or "").strip(), str(data.get("description") or "").strip(), vars_,
                  steps, source, path)


def _refs(value: Any) -> list[str]:
    out: list[str] = []
    if isinstance(value, str):
        out += [m.group(1) for m in _REF.finditer(value) if m.group(1) is not None]
    elif isinstance(value, list):
        for v in value:
            out += _refs(v)
    elif isinstance(value, dict):
        for v in value.values():
            out += _refs(v)
    return out


def _split_expr(expr: str) -> tuple[list[str], list[str]]:
    head, *filters = [x.strip() for x in expr.split("|")]
    return [p for p in head.split(".")], filters


def _check_refs(value: Any, vars_: dict[str, Var], ids: set[str], src: str, where: str) -> None:
    if value is _UNSET:
        return
    for expr in _refs(value):
        path, filters = _split_expr(expr)
        head = path[0] if path else ""
        if not head:
            raise _bad(src, f"{where}: empty reference ${{{expr}}}")
        if head == "steps":
            if len(path) < 2 or path[1] not in ids:
                raise _bad(src, f"{where}: ${{{expr}}} names no earlier step",
                           did_you_mean=difflib.get_close_matches(path[1] if len(path) > 1 else "", sorted(ids), n=2))
        elif head not in vars_ and head not in BUILTINS:
            raise _bad(src, f"{where}: unknown variable ${{{expr}}}",
                       did_you_mean=difflib.get_close_matches(head, [*vars_, *BUILTINS], n=2),
                       hint="declare it under 'vars'")
        for f in filters:
            if f.split(":", 1)[0] not in FILTERS:
                raise _bad(src, f"{where}: unknown filter {f!r} in ${{{expr}}}", hint=f"filters: {', '.join(FILTERS)}")


# --------------------------------------------------------------------------- evaluation


class _Skip(Exception):
    """A reference needs a step that did not run."""


class _BadRef(Exception):
    """A reference names a field the step's answer does not have."""


@dataclass
class _Ctx:
    vars: dict[str, Any]
    steps: dict[str, tuple[str, dict | None]] = field(default_factory=dict)  # id -> (status, envelope)


def _dig(env: Any, path: list[str], label: str) -> Any:
    cur = env
    i = 0
    while i < len(path):
        part = path[i]
        if part == "col" and isinstance(cur, dict) and "cols" in cur and "rows" in cur and i + 1 < len(path):
            name = path[i + 1]
            if name not in cur["cols"]:
                raise _BadRef(f"{label}: no column {name!r} (columns: {', '.join(cur['cols'])})")
            k = cur["cols"].index(name)
            cur = [r[k] for r in cur["rows"]]
            i += 2
            continue
        if isinstance(cur, dict):
            if part not in cur:
                keys = ", ".join(list(cur)[:12])
                raise _BadRef(f"{label}: no field {part!r} (fields: {keys})")
            cur = cur[part]
        elif isinstance(cur, list) and re.fullmatch(r"-?\d+", part):
            k = int(part)
            if not -len(cur) <= k < len(cur):
                raise _BadRef(f"{label}: index {k} out of range (length {len(cur)})")
            cur = cur[k]
        else:
            raise _BadRef(f"{label}: cannot take {part!r} of a {type(cur).__name__}")
        i += 1
    return cur


def _truthy(v: Any) -> bool:
    if isinstance(v, str):
        return v.strip().lower() not in _FALSY
    return bool(v)


def _literal(s: str) -> Any:
    from .yamlite import _plain

    return _plain(s.strip()) if s.strip() else ""


def _apply(v: Any, f: str) -> Any:
    name, _, arg = f.partition(":")
    if name == "not":
        return not _truthy(v)
    if name == "bool":
        return _truthy(v)
    if name == "len":
        return len(v) if isinstance(v, (list, dict, str)) else 0
    if name == "first":
        return (v[0] if v else None) if isinstance(v, list) else v
    if name == "json":
        return json.dumps(v, ensure_ascii=False, sort_keys=True)
    if name == "lower":
        return v.lower() if isinstance(v, str) else v
    if name == "eq":
        want = _literal(arg)
        return v == want or _text(v).lower() == _text(want).lower()
    s = "" if v is None else str(v).replace("\\", "/").rstrip("/")
    if name == "name":
        return s.rsplit("/", 1)[-1]
    if name == "parent":
        return s.rsplit("/", 1)[0] if "/" in s else ""
    base = s.rsplit("/", 1)[-1]
    if name == "stem":
        return base.rsplit(".", 1)[0] if "." in base else base
    if name == "ext":
        return base.rsplit(".", 1)[1].lower() if "." in base else ""
    raise _BadRef(f"unknown filter {f!r}")  # pragma: no cover - checked at load


def _eval(expr: str, ctx: _Ctx) -> Any:
    path, filters = _split_expr(expr)
    default = next((f.partition(":")[2] for f in filters if f.startswith("default")), _UNSET)
    try:
        if path[0] == "steps":
            sid = path[1]
            status, env = ctx.steps.get(sid, ("not run", None))
            if env is None:
                raise _Skip(f"needs step '{sid}' ({status})")
            v = _dig(env, path[2:], f"${{{expr}}}")
        else:
            if path[0] not in ctx.vars:
                raise _BadRef(f"unknown variable {path[0]!r}")
            v = _dig(ctx.vars[path[0]], path[1:], f"${{{expr}}}")
    except (_Skip, _BadRef):
        if default is _UNSET:
            raise
        v = None
    for f in filters:
        if f.startswith("default"):
            if v is None:
                v = _literal(default)
            continue
        v = _apply(v, f)
    return v


def _text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def _resolve(value: Any, ctx: _Ctx) -> Any:
    if isinstance(value, str):
        m = re.fullmatch(r"\$\{([^}]*)\}", value.strip())
        if m and value.strip() == value:
            return _eval(m.group(1), ctx)
        return _REF.sub(lambda mm: "${" if mm.group(1) is None else _text(_eval(mm.group(1), ctx)), value)
    if isinstance(value, list):
        return [_resolve(v, ctx) for v in value]
    if isinstance(value, dict):
        return {k: _resolve(v, ctx) for k, v in value.items()}
    return value


def _has_step_refs(value: Any) -> bool:
    return any(_split_expr(e)[0][0] == "steps" for e in _refs(value))


# --------------------------------------------------------------------------- planning and running


def _pick(step: Step) -> OpSpec | None:
    """The first registered candidate of ``op``; ``None`` also when an operation of ``requires`` is missing."""
    if any(find_op(r) is None for r in step.requires):
        return None
    for name in step.ops:
        spec = find_op(name)
        if spec is not None:
            return spec
    return None


def _missing(step: Step) -> str:
    gone = [r for r in step.requires if find_op(r) is None]
    return f"op not available ({', '.join(gone or step.ops)})"


def _bind_vars(rec: Recipe, given: dict[str, Any]) -> dict[str, Any]:
    unknown = [k for k in given if k not in rec.vars]
    if unknown:
        raise SatkError("BAD_PARAMS", f"recipe {rec.name}: unknown variable {unknown[0]!r}",
                        did_you_mean=difflib.get_close_matches(unknown[0], list(rec.vars), n=3, cutoff=0.5),
                        hint=f"satk recipe show {rec.name}", data={"vars": list(rec.vars)})
    missing = [v.name for v in rec.vars.values() if v.required and v.name not in given]
    if missing:
        raise SatkError("BAD_PARAMS", f"recipe {rec.name}: missing variable(s): {', '.join(missing)}",
                        hint=f"--var {missing[0]}=... (satk recipe show {rec.name})",
                        data={"vars": {v.name: v.help for v in rec.vars.values() if v.required}})
    cwd = jpath(os.getcwd())
    out: dict[str, Any] = {"work": jpath(cfg().paths.work), "cwd": cwd, "recipe": rec.name,
                           "recipe_dir": jpath(rec.path.parent) if rec.path is not None else cwd}
    for k, v in rec.vars.items():  # defaults (and given values) may use builtins and earlier variables
        val = given.get(k, v.default)
        if isinstance(val, str) and "${" in val:
            try:
                val = _resolve(val, _Ctx(out))
            except (_Skip, _BadRef) as e:
                raise SatkError("BAD_PARAMS", f"recipe {rec.name}: variable {k}: {e}") from None
        out[k] = val
    return out


def _step_args(step: Step, spec: OpSpec, ctx: _Ctx) -> dict:
    args = _resolve(step.args, ctx)
    if step.input is not _UNSET:
        prm = input_param(spec)
        if prm is None:
            raise _BadRef(f"{spec.name} has no positional parameter for 'input'; use args")
        val = _resolve(step.input, ctx)
        if prm.kind == "list" and val is not None and not isinstance(val, list):
            val = [val]
        args[prm.name] = val
    return _prune(args)


def _prune(v: Any) -> Any:
    """Drop ``None`` values from mappings and lists (a null variable means "not given")."""
    if isinstance(v, dict):
        return {k: _prune(x) for k, x in v.items() if x is not None}
    if isinstance(v, list):
        return [_prune(x) for x in v if x is not None]
    return v


def _gate(step: Step, ctx: _Ctx) -> str | None:
    """Why the step does not run (``when``/``unless``), else ``None``."""
    if step.when is not _UNSET and not _truthy(_resolve(step.when, ctx)):
        return "when: false"
    if step.unless is not _UNSET and _truthy(_resolve(step.unless, ctx)):
        return "unless: true"
    return None


def _preflight(rec: Recipe, ctx: _Ctx, yes: bool) -> list[tuple[Step, OpSpec | None, str | None]]:
    """(step, operation or None, static problem) per step; raises when a step needs consent or is refused."""
    plan: list[tuple[Step, OpSpec | None, str | None]] = []
    refused: list[tuple[str, tuple[str, str, str]]] = []
    for step in rec.steps:
        spec = _pick(step)
        if spec is None:
            plan.append((step, None, f"skipped: {_missing(step)}"))
            continue
        r = refusal(spec, yes=yes)
        if r is not None:
            refused.append((step.id, r))
        problem = None
        gated = False
        if not _has_step_refs([step.when, step.unless]):
            try:
                gated = _gate(step, ctx) is not None  # a step that will not run is not validated
            except (_Skip, _BadRef) as e:
                problem = f"BAD_PARAMS: {e}"
        if not gated and problem is None and not _has_step_refs([step.args, step.input]):
            try:
                spec.bind(_step_args(step, spec, ctx))
            except SatkError as e:
                problem = f"{e.code}: {e.msg}"
            except _BadRef as e:
                problem = f"BAD_PARAMS: {e}"
        plan.append((step, spec, problem))
    if refused:
        sid, (code, msg, hint) = refused[0]
        more = f" (+{len(refused) - 1} more step(s))" if len(refused) > 1 else ""
        raise SatkError(code, f"recipe {rec.name}: step {sid}: {msg}{more}", hint=hint,
                        data={"steps": [s for s, _ in refused]})
    bad = [(s, p) for s, sp, p in plan if sp is not None and p]
    if bad:
        s, p = bad[0]
        raise SatkError("BAD_PARAMS", f"recipe {rec.name}: step {s.id}: {p}",
                        hint=f"satk recipe show {rec.name}; satk {next(sp for st, sp, _ in plan if st is s).cli} -h",
                        data={"step": s.id})
    return plan


def _out_path(rec: Recipe, out: str | None) -> Path:
    if out:
        return Path(os.path.abspath(out))
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", rec.name).strip("._") or "recipe"
    return Path(os.path.abspath(cfg().paths.work)) / "out" / "recipes" / f"{slug}.json"


def _args_text(step: Step) -> str:
    d = dict(step.args)
    if step.input is not _UNSET:
        d = {"(input)": step.input, **d}
    return json.dumps(d, ensure_ascii=False, sort_keys=False, default=str)[:160]


def run_recipe(ref: str, *, var: list[str] | None = None, vars: dict | None = None, dry_run: bool = False,  # noqa: A002
               yes: bool = False, out: str | None = None) -> dict:
    """Run (or with ``dry_run`` only plan) a recipe; see the module docstring."""
    from .runner import parse_kv

    rec = load_recipe(ref)
    ident = jpath(rec.path) if rec.path is not None else rec.name
    chain = _running.get()
    if ident in chain:
        names = " -> ".join([*(Path(c).stem for c in chain), rec.name])
        raise SatkError("BAD_PARAMS", f"recipe {rec.name} runs itself: {names}",
                        hint="a step calls recipe.run on its own recipe")
    given = parse_kv(var, "--var")
    if vars:
        given.update({str(k): v for k, v in vars.items()})
    ctx = _Ctx(_bind_vars(rec, given))
    shown_vars = {k: ctx.vars[k] for k in rec.vars if ctx.vars.get(k) is not None}
    token = _running.set((*chain, ident))
    try:
        with nested(f"recipe {rec.name}"):
            return _run(rec, ctx, shown_vars, dry_run, yes, out)
    finally:
        _running.reset(token)


def _run(rec: Recipe, ctx: "_Ctx", shown_vars: dict, dry_run: bool, yes: bool, out: str | None) -> dict:
    """Plan (``dry_run``) or execute an already loaded recipe with bound variables."""
    plan = _preflight(rec, ctx, yes)
    out_path = _out_path(rec, out)
    if dry_run:
        rows = []
        for step, spec, problem in plan:
            if spec is None:
                state = problem
            elif _has_step_refs([step.when, step.unless]):
                state = "run (when/unless after earlier steps)" if (step.when is not _UNSET
                                                                     or step.unless is not _UNSET) else "run"
            else:
                try:
                    state = _gate(step, ctx) or "run"
                except (_Skip, _BadRef) as e:
                    state = f"skipped: {e}"
            if spec is not None and _has_step_refs([step.args, step.input]):
                args_s = _args_text(step)
            elif spec is not None:
                try:
                    args_s = json.dumps(_step_args(step, spec, ctx), ensure_ascii=False, default=str)[:160]
                except (_Skip, _BadRef) as e:
                    args_s = str(e)
            else:
                args_s = _args_text(step)
            rows.append([step.id, spec.name if spec else " | ".join(step.ops), state, args_s])
        env = table(["step", "op", "state", "args"], rows)
        env.update({"recipe": rec.name, "dry_run": True, "vars": shown_vars or None, "out": jpath(out_path)})
        return {k: v for k, v in env.items() if v is not None}
    return _execute(rec, plan, ctx, out_path, shown_vars)


def _mute(done: float, total: float | None, msg: str | None) -> None:
    return None


def _execute(rec: Recipe, plan: list, ctx: _Ctx, out_path: Path, shown_vars: dict) -> dict:
    rows: list[list] = []
    saved: list[dict] = []
    counts: dict[str, int] = {}
    stopped_by: str | None = None
    n = len(plan)
    for i, (step, spec, problem) in enumerate(plan):
        report_progress(i, n, f"recipe {rec.name}: {step.id}")
        rec_step: dict[str, Any] = {"id": step.id, "op": spec.name if spec else " | ".join(step.ops)}
        env: dict | None = None
        reason: str | None = None
        if stopped_by is not None:
            status, reason = "not run", f"stopped after step {stopped_by}"
        elif spec is None:
            status, reason = "skipped", (problem or "skipped").removeprefix("skipped: ")
        else:
            try:
                reason = _gate(step, ctx)
                args = None if reason else _step_args(step, spec, ctx)
            except _Skip as e:
                reason, args = f"{e}", None
            except _BadRef as e:
                args = None
                env = SatkError("BAD_PARAMS", f"step {step.id}: {e}", hint=f"satk recipe show {rec.name}").to_dict()
            if env is not None:
                status = "fail"
            elif reason is not None:
                status = "skipped"
            else:
                rec_step["args"] = args
                with progress_handler(_mute):
                    env = invoke(spec, args)
                status = "ok" if env.get("ok", True) else "fail"
            if env is not None:
                ctx.steps[step.id] = (status, env)
                if status == "ok" and step.fail_if is not _UNSET:
                    conds = step.fail_if if isinstance(step.fail_if, list) else [step.fail_if]
                    hit = None
                    try:
                        for c in conds:
                            val = _resolve(c, ctx)
                            if _truthy(val):
                                hit = f"fail_if {c} = {_text(val)}"
                                break
                    except (_Skip, _BadRef) as e:
                        status, reason = "fail", f"fail_if: {e}"
                    if hit is not None:
                        status, reason = "fail", hit
                    ctx.steps[step.id] = (status, env)
            else:
                ctx.steps[step.id] = (status, None)
            if status == "fail" and step.on_error == "stop":
                stopped_by = step.id
        if step.id not in ctx.steps:
            ctx.steps[step.id] = (status, None)
        counts[status] = counts.get(status, 0) + 1
        info = reason if reason and env is None else (f"{reason}; " if reason else "") + (one_line(env) if env else "")
        rows.append([step.id, rec_step["op"], status, info])
        rec_step["status"] = status
        if reason:
            rec_step["reason"] = reason
        if env is not None:
            rec_step["result"] = env
        saved.append(rec_step)
    report_progress(n, n, f"recipe {rec.name}: done")
    doc = {"recipe": rec.name, "source": rec.source, "vars": shown_vars, "counts": counts, "steps": saved}
    atomic_write(out_path, dumps(doc, pretty=True) + "\n")
    fields = {"recipe": rec.name, "vars": shown_vars or None, "counts": counts, "out": jpath(out_path)}
    fields = {k: v for k, v in fields.items() if v is not None}
    if counts.get("fail"):
        failed = [r[0] for r in rows if r[2] == "fail"]
        tail = f"; stopped at {stopped_by}" if stopped_by else ""
        raise SatkError("CHECK_FAILED",
                        f"recipe {rec.name}: {counts['fail']} step(s) failed ({', '.join(failed)}){tail}",
                        hint=f"full answers of every step: {jpath(out_path)}",
                        data={"cols": ["step", "op", "status", "info"], "rows": rows, **fields})
    env = table(["step", "op", "status", "info"], rows)
    env.update(fields)
    return env


# --------------------------------------------------------------------------- list / show


def list_recipes() -> dict:
    """Table ``name source summary vars`` of the user's and the shipped recipes."""
    rows: list[list] = []
    warn: list[str] = []
    seen: set[str] = set()
    entries: list[tuple[str, str]] = [(n, "user") for n in _user()] + [(n, "shipped") for n in _shipped()]
    for name, source in entries:
        if name in seen:
            continue
        seen.add(name)
        try:
            r = load_recipe(name)
        except SatkError as e:
            warn.append(f"BAD_RECIPE: {name}: {e.msg}")
            rows.append([name, source, "(invalid, see warn)", ""])
            continue
        vs = ", ".join(v.name + ("*" if v.required else "") for v in r.vars.values())
        rows.append([r.name if r.name == name else name, source, r.summary, vs])
    rows.sort(key=lambda r: r[0])
    env = table(["name", "source", "summary", "vars"], rows, warn=warn)
    env["user_dir"] = jpath(user_dir())
    env["hint"] = "satk recipe show <name>; satk recipe run <name> --var k=v [--dry-run]; * = required variable"
    return env


def show_recipe(ref: str) -> dict:
    """The recipe's variables and steps (operation availability included)."""
    r = load_recipe(ref)
    rows = []
    for step in r.steps:
        spec = _pick(step)
        avail = spec.name if spec else _missing(step).replace("op not available", "not available")
        cond = []
        if step.when is not _UNSET:
            cond.append(f"when {step.when}")
        if step.unless is not _UNSET:
            cond.append(f"unless {step.unless}")
        if step.on_error != "stop":
            cond.append("on_error continue")
        if step.fail_if is not _UNSET:
            cond.append(f"fail_if {step.fail_if}")
        rows.append([step.id, " | ".join(step.ops), avail, "; ".join(cond), _args_text(step)])
    env = table(["step", "op", "available", "flow", "args"], rows)
    env.update({k: v for k, v in {
        "recipe": r.name, "source": r.source, "path": jpath(r.path) if r.path else None, "summary": r.summary or None,
        "description": r.description or None,
        "vars": {v.name: {k: x for k, x in (("default", v.default), ("required", v.required or None),
                                              ("help", v.help or None)) if x is not None}
                 for v in r.vars.values()} or None,
        "run": f"satk recipe run {r.name}" + "".join(f" --var {v.name}=..." for v in r.vars.values() if v.required),
    }.items() if v is not None})
    return env
