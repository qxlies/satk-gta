"""Operation registry (SPEC §3.1). FROZEN contract.

One operation = one CLI command = one MCP tool. Packages declare operations with
:func:`op` in ``satk.<pkg>.ops``; :func:`discover` imports those modules. There is no
central list to edit.

Example (in ``src/satk/index/ops.py``)::

    from typing import Literal
    from satk.core.registry import op
    from satk.core.envelope import table

    @op("asset.find", summary="Find assets by name (FTS)", summary_ru="Поиск ассетов по имени",
        examples=("satk asset find grove --kind tex",))
    def asset_find(q: str, kind: Literal["model", "tex", "txd"] | None = None,
                   limit: int = 20, cursor: str | None = None, profile: str = "vanilla") -> dict:
        \"\"\"Find assets.

        Args:
            q: text to search for.
            kind: restrict to one SID kind.
        \"\"\"
        return table(["id", "kind", "name", "info"], rows)

Naming: ``"index.build"`` -> CLI ``satk index build`` and MCP ``index_build`` (``mcp=True``);
underscores in the name become dashes in the CLI (``dev.guard_test`` -> ``satk dev guard-test``).
Parameters come from annotations: ``str``, ``int``, ``float``, ``bool``, ``list[str]``,
``list[int]``, ``list[float]``, ``list[list[float]]``, ``dict[str, Any]`` (JSON), ``Literal[...]``,
``Path`` (string), ``X | None``. Defaults come from the signature; parameters without a
default are CLI positionals in signature order, the rest are ``--kebab-case`` options.
Parameter descriptions come from the Google-style ``Args:`` docstring section. ``summary``
(the MCP tool description) is at most 300 characters.
"""

from __future__ import annotations

import contextvars
import copy
import difflib
import importlib
import importlib.util
import inspect
import json
import os
import pkgutil
import re
import threading
import types
import typing
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Literal

from .errors import SatkError

__all__ = [
    "Group",
    "GROUPS",
    "MCP_GROUPS",
    "MAX_SUMMARY",
    "ParamSpec",
    "OpSpec",
    "op",
    "doctor_check",
    "status_provider",
    "discover",
    "all_ops",
    "get_op",
    "op_by_mcp",
    "invoke",
    "import_errors",
    "doctor_checks",
    "status_providers",
    "report_progress",
    "progress_handler",
    "isolated_registry",
]

Group = Literal["core", "game", "formats", "index", "asset", "world", "texture", "model", "map", "note",
                "view", "re", "blender", "engine", "dev", "mcp"]
GROUPS: tuple[str, ...] = typing.get_args(Group)
#: Values allowed in ``SATK_MCP_GROUPS`` / ``mcp_group``.
MCP_GROUPS: tuple[str, ...] = ("index", "media", "view", "re", "blender", "engine", "core")
_GROUP_TO_MCP = {"index": "index", "asset": "index", "world": "index", "formats": "index",
                 "texture": "media", "map": "media", "model": "media",
                 "view": "view", "re": "re", "blender": "blender", "engine": "engine"}
MAX_SUMMARY = 300
#: Parameter names taken by global CLI flags.
RESERVED_PARAMS = frozenset({"json", "table", "timing", "quiet", "q", "help", "h"})

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")
_MCP_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_EMPTY = inspect.Parameter.empty


# --------------------------------------------------------------------------- parameters


def _bad_params(msg: str, **kw) -> SatkError:
    return SatkError("BAD_PARAMS", msg, **kw)


@dataclass(frozen=True)
class ParamSpec:
    """One operation parameter, derived from the function signature.

    ``kind`` is one of ``str int float bool path enum list dict``; for ``list`` the element
    type is in ``item`` (``str int float list[float] list[int]``); for ``enum`` the allowed
    values are in ``choices``.
    """

    name: str
    kind: str
    item: str | None = None
    choices: tuple | None = None
    optional: bool = False  # accepts None
    default: Any = _EMPTY
    help: str = ""
    annotation: str = ""

    @property
    def has_default(self) -> bool:
        return self.default is not _EMPTY

    @property
    def positional(self) -> bool:
        """CLI positional: parameters without a default (SPEC §3.1)."""
        return not self.has_default

    @property
    def required(self) -> bool:
        """Must be given (no default and ``None`` not accepted)."""
        return not self.has_default and not self.optional

    @property
    def flag(self) -> str:
        return "--" + self.name.replace("_", "-")

    # -- schema -------------------------------------------------------------------------

    def _scalar_schema(self, kind: str) -> dict:
        return {
            "str": {"type": "string"},
            "path": {"type": "string"},
            "int": {"type": "integer"},
            "float": {"type": "number"},
            "bool": {"type": "boolean"},
            "dict": {"type": "object"},
            "list[float]": {"type": "array", "items": {"type": "number"}},
            "list[int]": {"type": "array", "items": {"type": "integer"}},
        }[kind]

    def json_schema(self) -> dict:
        """JSON Schema of this parameter (MCP ``inputSchema`` property)."""
        if self.kind == "enum":
            vals = list(self.choices or ())
            s: dict = {"type": "integer" if vals and isinstance(vals[0], int) else "string", "enum": vals}
        elif self.kind == "list":
            s = {"type": "array", "items": self._scalar_schema(self.item or "str")}
        else:
            s = dict(self._scalar_schema(self.kind))
        if self.help:
            s["description"] = self.help
        if self.has_default and self.default is not None:
            d = self.default
            if isinstance(d, tuple):
                d = list(d)
            if isinstance(d, Path):
                d = str(d)
            try:
                json.dumps(d)
                s["default"] = d
            except (TypeError, ValueError):
                pass
        return s

    # -- coercion -----------------------------------------------------------------------

    def _scalar(self, kind: str, v: Any) -> Any:
        n = self.name
        if kind in ("str", "path"):
            if isinstance(v, bool) or not isinstance(v, (str, int, float, os.PathLike)):
                raise _bad_params(f"{n}: expected a string, got {type(v).__name__}")
            s = os.fspath(v) if isinstance(v, os.PathLike) else str(v)
            return Path(s) if kind == "path" else s
        if kind == "int":
            if isinstance(v, bool):
                raise _bad_params(f"{n}: expected an integer, got a boolean")
            if isinstance(v, int):
                return v
            if isinstance(v, float) and v.is_integer():
                return int(v)
            if isinstance(v, str):
                try:
                    return int(v.strip(), 0) if v.strip().lower().startswith(("0x", "-0x")) else int(v.strip())
                except ValueError:
                    pass
            raise _bad_params(f"{n}: expected an integer, got {v!r}")
        if kind == "float":
            if isinstance(v, bool):
                raise _bad_params(f"{n}: expected a number, got a boolean")
            if isinstance(v, (int, float)):
                return float(v)
            if isinstance(v, str):
                try:
                    return float(v.strip())
                except ValueError:
                    pass
            raise _bad_params(f"{n}: expected a number, got {v!r}")
        if kind == "bool":
            if isinstance(v, bool):
                return v
            if isinstance(v, int) and v in (0, 1):
                return bool(v)
            if isinstance(v, str) and v.strip().lower() in ("1", "true", "yes", "on", "0", "false", "no", "off"):
                return v.strip().lower() in ("1", "true", "yes", "on")
            raise _bad_params(f"{n}: expected true/false, got {v!r}")
        if kind == "dict":
            if isinstance(v, dict):
                return v
            if isinstance(v, str):
                s = v.strip()
                if s.startswith("@"):
                    try:
                        s = Path(s[1:]).read_text(encoding="utf-8")
                    except (OSError, UnicodeDecodeError) as e:
                        raise _bad_params(f"{n}: cannot read {s[1:]!r}: {e}") from None
                try:
                    d = json.loads(s)
                except json.JSONDecodeError as e:
                    raise _bad_params(f"{n}: invalid JSON: {e}") from None
                if isinstance(d, dict):
                    return d
            raise _bad_params(f"{n}: expected a JSON object, got {v!r}")
        if kind in ("list[float]", "list[int]"):
            sub = "float" if kind == "list[float]" else "int"
            if isinstance(v, str):
                s = v.strip()
                v = json.loads(s) if s.startswith("[") else [x for x in s.split(",") if x.strip()]
            if not isinstance(v, (list, tuple)):
                raise _bad_params(f"{n}: expected a list of numbers, got {v!r}")
            return [self._scalar(sub, x) for x in v]
        raise AssertionError(kind)  # pragma: no cover

    def coerce(self, value: Any) -> Any:
        """Validate/convert a JSON (MCP) or string (CLI) value; raises ``BAD_PARAMS``."""
        if value is None:
            if self.optional or self.default is None:
                return None
            raise _bad_params(f"{self.name}: must not be null")
        if self.kind == "enum":
            choices = self.choices or ()
            if choices and isinstance(choices[0], int) and isinstance(value, str):
                try:
                    value = int(value)
                except ValueError:
                    pass
            if value in choices:
                return value
            if isinstance(value, str):
                low = {str(c).lower(): c for c in choices}
                if value.lower() in low:
                    return low[value.lower()]
            raise _bad_params(
                f"{self.name}: {value!r} is not one of {', '.join(map(str, choices))}",
                did_you_mean=difflib.get_close_matches(str(value), [str(c) for c in choices], n=3, cutoff=0.5),
            )
        if self.kind == "list":
            item = self.item or "str"
            if isinstance(value, str):
                s = value.strip()
                if s.startswith("["):
                    try:
                        value = json.loads(s)
                    except json.JSONDecodeError as e:
                        raise _bad_params(f"{self.name}: invalid JSON list: {e}") from None
                elif item in ("list[float]", "list[int]"):
                    value = [g for g in s.split(";") if g.strip()]
                else:
                    value = [x.strip() for x in s.split(",") if x.strip()]
            elif not isinstance(value, (list, tuple)):
                value = [value]
            return [self._scalar(item, x) for x in value]
        return self._scalar(self.kind, value)

    def coerce_cli(self, raw: Any) -> Any:
        """Convert argparse output (a string or a list of tokens) to the value."""
        if self.kind == "list" and isinstance(raw, list):
            item = self.item or "str"
            tokens: list[str] = []
            for t in raw:
                if self.positional and item == "str":
                    tokens.append(t)  # shell already split positionals; keep commas
                elif item in ("list[float]", "list[int]"):
                    tokens.extend(g for g in t.split(";") if g.strip())
                elif t.strip().startswith("["):
                    return self.coerce(t)
                else:
                    tokens.extend(x.strip() for x in t.split(",") if x.strip())
            return [self._scalar(item, x) for x in tokens]
        return self.coerce(raw)

    @property
    def type_label(self) -> str:
        if self.kind == "enum":
            return "{" + ",".join(map(str, self.choices or ())) + "}"
        if self.kind == "list":
            return f"list[{self.item}]"
        return self.kind


def _type_name(tp: Any) -> str:
    try:
        return inspect.formatannotation(tp)
    except Exception:  # pragma: no cover
        return repr(tp)


def _analyze(name: str, tp: Any) -> tuple[str, str | None, tuple | None, bool]:
    """(kind, item, choices, optional) for an annotation; ``TypeError`` if unsupported."""
    optional = False
    origin = typing.get_origin(tp)
    if origin in (typing.Union, types.UnionType):
        args = typing.get_args(tp)
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) != 1 or len(args) != 2:
            raise TypeError(f"parameter {name!r}: only 'X | None' unions are supported, got {_type_name(tp)}")
        optional = True
        tp = non_none[0]
        origin = typing.get_origin(tp)
    if tp is str:
        return "str", None, None, optional
    if tp is bool:
        return "bool", None, None, optional
    if tp is int:
        return "int", None, None, optional
    if tp is float:
        return "float", None, None, optional
    if tp is Path or (isinstance(tp, type) and issubclass(tp, Path)):
        return "path", None, None, optional
    if origin is Literal:
        choices = typing.get_args(tp)
        if not choices or not (all(isinstance(c, str) for c in choices)
                               or all(isinstance(c, int) and not isinstance(c, bool) for c in choices)):
            raise TypeError(f"parameter {name!r}: Literal values must be all str or all int")
        return "enum", None, tuple(choices), optional
    if tp is list or origin is list:
        args = typing.get_args(tp)
        it = args[0] if args else str
        if it is str:
            return "list", "str", None, optional
        if it is int:
            return "list", "int", None, optional
        if it is float:
            return "list", "float", None, optional
        if typing.get_origin(it) is list and typing.get_args(it) in ((float,), (int,)):
            return "list", "list[float]" if typing.get_args(it) == (float,) else "list[int]", None, optional
        raise TypeError(f"parameter {name!r}: unsupported list element type {_type_name(it)}")
    if tp is dict or origin is dict:
        return "dict", None, None, optional
    raise TypeError(f"parameter {name!r}: unsupported annotation {_type_name(tp)}")


_ARGS_HDR = re.compile(r"^\s*(Args|Arguments|Parameters|Params)\s*:\s*$")
_ARG_LINE = re.compile(r"^(\s+)\*{0,2}([A-Za-z_][A-Za-z0-9_]*)\s*(\([^)]*\))?\s*:\s*(.*)$")


def parse_docstring(doc: str | None) -> tuple[str, dict[str, str]]:
    """Split a Google-style docstring into (description, {param: help})."""
    if not doc:
        return "", {}
    lines = inspect.cleandoc(doc).splitlines()
    desc: list[str] = []
    params: dict[str, str] = {}
    i = 0
    while i < len(lines) and not _ARGS_HDR.match(lines[i]):
        desc.append(lines[i])
        i += 1
    i += 1
    cur: str | None = None
    indent: int | None = None
    while i < len(lines):
        line = lines[i]
        if line.strip() == "":
            i += 1
            continue
        lead = len(line) - len(line.lstrip())
        if lead == 0:
            break  # next section ("Returns:" etc.)
        m = _ARG_LINE.match(line)
        if m and (indent is None or len(m.group(1)) <= indent):
            indent = len(m.group(1))
            cur = m.group(2)
            params[cur] = m.group(4).strip()
        elif cur is not None:
            params[cur] = (params[cur] + " " + line.strip()).strip()
        i += 1
    return "\n".join(desc).strip(), params


# --------------------------------------------------------------------------- operations


@dataclass
class OpSpec:
    """A registered operation (see :func:`op`)."""

    name: str
    fn: Callable[..., dict]
    summary: str
    summary_ru: str
    mcp: bool | str = True
    mcp_group_explicit: str | None = None
    long_running: bool = False
    examples: tuple[str, ...] = ()
    group: str = "core"
    params: tuple[ParamSpec, ...] = ()
    doc: str = ""
    module: str = ""
    qualname: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def cli_path(self) -> tuple[str, ...]:
        """``("dev", "guard-test")`` for ``dev.guard_test``."""
        return tuple(p.replace("_", "-") for p in self.name.split("."))

    @property
    def cli(self) -> str:
        return " ".join(self.cli_path)

    @property
    def mcp_name(self) -> str | None:
        """MCP tool name or ``None`` for CLI-only operations."""
        if self.mcp is False:
            return None
        if self.mcp is True:
            return self.name.replace(".", "_")
        return str(self.mcp)

    @property
    def mcp_group(self) -> str:
        """Group for ``SATK_MCP_GROUPS`` (explicit or derived from the name group)."""
        return self.mcp_group_explicit or _GROUP_TO_MCP.get(self.group, "core")

    @property
    def description(self) -> str:
        """Tool description (= ``summary``, at most 300 characters)."""
        return self.summary

    def param(self, name: str) -> ParamSpec:
        for p in self.params:
            if p.name == name:
                return p
        raise KeyError(name)

    def input_schema(self) -> dict:
        """JSON Schema of the arguments (MCP ``inputSchema``); same source as the CLI."""
        props = {p.name: p.json_schema() for p in self.params}
        schema: dict = {"type": "object", "properties": props}
        req = [p.name for p in self.params if p.required]
        if req:
            schema["required"] = req
        schema["additionalProperties"] = False
        return schema

    def bind(self, args: dict | None) -> dict:
        """Validate a JSON argument dict into keyword arguments; raises ``BAD_PARAMS``."""
        given = {str(k).replace("-", "_"): v for k, v in (args or {}).items()}
        names = [p.name for p in self.params]
        unknown = [k for k in given if k not in names]
        if unknown:
            k = unknown[0]
            raise _bad_params(
                f"{self.name}: unknown parameter {k!r}",
                did_you_mean=difflib.get_close_matches(k, names, n=3, cutoff=0.5),
                data={"params": names},
            )
        kwargs: dict = {}
        missing: list[str] = []
        for p in self.params:
            if p.name in given:
                kwargs[p.name] = p.coerce(given[p.name])
            elif p.has_default:
                kwargs[p.name] = copy.copy(p.default)
            elif p.optional:
                kwargs[p.name] = None
            else:
                missing.append(p.name)
        if missing:
            raise _bad_params(f"{self.name}: missing required parameter(s): {', '.join(missing)}",
                              hint=f"satk {self.cli} -h")
        return kwargs

    def call(self, args: dict | None = None) -> dict:
        """Bind, run and normalize the result (adds ``"ok": true``). Raises ``SatkError``."""
        kwargs = self.bind(args)
        result = self.fn(**kwargs)
        if result is None:
            return {"ok": True}
        if not isinstance(result, dict):
            raise TypeError(f"operation {self.name} returned {type(result).__name__}, expected dict")
        if "ok" not in result:
            result = {"ok": True, **result}
        return result


_lock = threading.RLock()
_ops: dict[str, OpSpec] = {}
_mcp_names: dict[str, str] = {}
_doctor: dict[str, Callable] = {}
_status: dict[str, Callable] = {}
_import_errors: dict[str, str] = {}
_discovered = False


def _same_fn(a: Callable, b: Callable) -> bool:
    return (getattr(a, "__module__", None), getattr(a, "__qualname__", None)) == (
        getattr(b, "__module__", None), getattr(b, "__qualname__", None))


def _build_params(fn: Callable, doc_params: dict[str, str]) -> tuple[ParamSpec, ...]:
    sig = inspect.signature(fn)
    try:
        hints = typing.get_type_hints(fn)
    except Exception as e:
        raise TypeError(f"{fn.__qualname__}: cannot resolve annotations: {e}") from e
    out: list[ParamSpec] = []
    for p in sig.parameters.values():
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            raise TypeError(f"{fn.__qualname__}: *args/**kwargs are not supported in operations")
        if p.name in RESERVED_PARAMS:
            raise TypeError(f"{fn.__qualname__}: parameter name {p.name!r} is reserved for CLI flags")
        if p.name not in hints:
            raise TypeError(f"{fn.__qualname__}: parameter {p.name!r} needs a type annotation")
        kind, item, choices, optional = _analyze(p.name, hints[p.name])
        if p.default is None:
            optional = True
        if kind == "bool" and p.default is _EMPTY:
            raise TypeError(f"{fn.__qualname__}: bool parameter {p.name!r} needs a default")
        out.append(ParamSpec(
            name=p.name, kind=kind, item=item, choices=choices, optional=optional,
            default=p.default, help=doc_params.get(p.name, ""), annotation=_type_name(hints[p.name]),
        ))
    return tuple(out)


def _register(spec: OpSpec) -> None:
    with _lock:
        cur = _ops.get(spec.name)
        if cur is not None and not _same_fn(cur.fn, spec.fn):
            raise ValueError(f"operation {spec.name!r} already registered by {cur.module}.{cur.qualname}")
        mname = spec.mcp_name
        if mname is not None:
            if not _MCP_NAME_RE.match(mname):
                raise ValueError(f"bad MCP tool name {mname!r}")
            owner = _mcp_names.get(mname)
            if owner is not None and owner != spec.name:
                raise ValueError(f"MCP tool name {mname!r} already used by operation {owner!r}")
        if cur is not None and cur.mcp_name and cur.mcp_name != mname:
            _mcp_names.pop(cur.mcp_name, None)
        _ops[spec.name] = spec
        if mname is not None:
            _mcp_names[mname] = spec.name


def op(name: str, *, summary: str, summary_ru: str,
       mcp: bool | str = True,
       mcp_group: str | None = None,
       long_running: bool = False,
       examples: tuple[str, ...] = (),
       group: str | None = None) -> Callable:
    """Register a function as an operation (CLI command + optional MCP tool).

    Args:
        name: dotted lower-case name, ``"index.build"``.
        summary: English one-liner, also the MCP tool description (<= 300 chars).
        summary_ru: Russian one-liner for ``satk help --ru``.
        mcp: ``True`` -> MCP name = name with ``.``->``_``; a string -> explicit MCP name;
            ``False`` -> CLI only.
        mcp_group: group for ``SATK_MCP_GROUPS`` (index|media|view|re|blender|engine|core);
            derived from the name if omitted.
        long_running: MCP runs it with progress notifications and a 600 s timeout.
        examples: CLI examples shown in help.
        group: help group (one of :data:`GROUPS`); derived from the first name segment if
            omitted (``"core"`` when that segment is not a group). Additive to SPEC §3.1.
    """
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise ValueError(f"bad operation name {name!r} (lower-case dotted, e.g. 'index.build')")
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError(f"operation {name!r}: summary is required")
    if len(summary) > MAX_SUMMARY:
        raise ValueError(f"operation {name!r}: summary is {len(summary)} chars, max {MAX_SUMMARY}")
    if not isinstance(summary_ru, str) or not summary_ru.strip():
        raise ValueError(f"operation {name!r}: summary_ru is required")
    first = name.split(".")[0]
    grp = group or (first if first in GROUPS else "core")
    if grp not in GROUPS:
        raise ValueError(f"operation {name!r}: unknown group {grp!r}")
    if mcp_group is not None and mcp_group not in MCP_GROUPS:
        raise ValueError(f"operation {name!r}: unknown mcp_group {mcp_group!r}")
    if not isinstance(mcp, (bool, str)):
        raise ValueError(f"operation {name!r}: mcp must be bool or str")

    def deco(fn: Callable) -> Callable:
        desc, doc_params = parse_docstring(fn.__doc__)
        spec = OpSpec(
            name=name, fn=fn, summary=summary.strip(), summary_ru=summary_ru.strip(), mcp=mcp,
            mcp_group_explicit=mcp_group, long_running=bool(long_running), examples=tuple(examples),
            group=grp, params=_build_params(fn, doc_params), doc=desc,
            module=getattr(fn, "__module__", ""), qualname=getattr(fn, "__qualname__", ""),
        )
        _register(spec)
        try:
            fn.__satk_op__ = spec  # type: ignore[attr-defined]
        except (AttributeError, TypeError):  # pragma: no cover
            pass
        return fn

    return deco


def _named_registry(store: dict[str, Callable], what: str, name: str) -> Callable:
    if not isinstance(name, str) or not name:
        raise ValueError(f"{what} needs a name")

    def deco(fn: Callable) -> Callable:
        with _lock:
            cur = store.get(name)
            if cur is not None and not _same_fn(cur, fn):
                raise ValueError(f"{what} {name!r} already registered by {cur.__module__}.{cur.__qualname__}")
            store[name] = fn
        return fn

    return deco


def doctor_check(name: str) -> Callable:
    """Register ``fn() -> {"status": "ok"|"warn"|"fail", "msg": str, "fix": str|None}`` for ``satk doctor``."""
    return _named_registry(_doctor, "doctor check", name)


def status_provider(name: str) -> Callable:
    """Register ``fn(deep: bool) -> dict``; its result becomes section ``name`` of ``satk_status``."""
    return _named_registry(_status, "status provider", name)


# --------------------------------------------------------------------------- discovery


def discover(force: bool = False) -> None:
    """Import ``satk.<pkg>.ops`` for every subpackage of ``satk`` (pkgutil).

    An import error does not break the CLI: that package's operations are skipped and the
    error is kept in :func:`import_errors` (``satk doctor`` check ``ops_import``).
    """
    global _discovered
    with _lock:
        if _discovered and not force:
            return
        import satk

        for m in sorted(pkgutil.iter_modules(satk.__path__), key=lambda m: m.name):
            if not m.ispkg:
                continue
            modname = f"satk.{m.name}.ops"
            try:
                if importlib.util.find_spec(modname) is None:
                    continue
                importlib.import_module(modname)
                _import_errors.pop(modname, None)
            except Exception as e:  # noqa: BLE001 - reported via doctor
                _import_errors[modname] = f"{type(e).__name__}: {e}"
                try:
                    from .log import get_logger

                    get_logger("registry").warning("cannot import %s: %s", modname, e)
                except Exception:  # pragma: no cover
                    pass
        _discovered = True


def import_errors() -> dict[str, str]:
    """``{"satk.x.ops": "ImportError: ..."}`` from the last :func:`discover`."""
    with _lock:
        return dict(_import_errors)


def all_ops() -> list[OpSpec]:
    """All registered operations (after :func:`discover`), sorted by name."""
    discover()
    with _lock:
        return [_ops[k] for k in sorted(_ops)]


def get_op(name: str) -> OpSpec:
    """Operation by dotted name; ``NOT_FOUND`` with suggestions otherwise."""
    discover()
    with _lock:
        spec = _ops.get(name)
        names = list(_ops)
    if spec is None:
        raise SatkError("NOT_FOUND", f"no operation {name!r}",
                        did_you_mean=difflib.get_close_matches(name, names, n=3, cutoff=0.5), hint="satk help")
    return spec


def op_by_mcp(mcp_name: str) -> OpSpec:
    """Operation by MCP tool name; ``UNKNOWN_METHOD`` otherwise."""
    discover()
    with _lock:
        name = _mcp_names.get(mcp_name)
        names = list(_mcp_names)
    if name is None:
        raise SatkError("UNKNOWN_METHOD", f"no MCP tool {mcp_name!r}",
                        did_you_mean=difflib.get_close_matches(mcp_name, names, n=3, cutoff=0.5))
    return _ops[name]


def doctor_checks() -> dict[str, Callable]:
    """Registered doctor checks (after discovery)."""
    discover()
    with _lock:
        return dict(_doctor)


def status_providers() -> dict[str, Callable]:
    """Registered status providers (after discovery)."""
    discover()
    with _lock:
        return dict(_status)


def invoke(op_or_name: OpSpec | str, args: dict | None = None) -> dict:
    """Run an operation and always return an envelope (never raises ``SatkError``).

    ``SatkError`` becomes its error envelope; any other exception becomes ``INTERNAL``
    with the traceback appended to ``work/logs/errors.log``. ``KeyboardInterrupt`` propagates.
    """
    try:
        spec = op_or_name if isinstance(op_or_name, OpSpec) else get_op(op_or_name)
        return spec.call(args)
    except SatkError as e:
        return e.to_dict()
    except Exception as e:  # noqa: BLE001
        from .log import record_exception

        where = record_exception(e, context=f"op={getattr(op_or_name, 'name', op_or_name)}")
        hint = f"traceback in {str(where).replace(os.sep, '/')}" if where else None
        return SatkError("INTERNAL", f"{type(e).__name__}: {e}", hint=hint).to_dict()


# --------------------------------------------------------------------------- progress

_progress: contextvars.ContextVar[Callable[[float, float | None, str | None], None] | None] = (
    contextvars.ContextVar("satk_progress", default=None))


def report_progress(done: float, total: float | None = None, msg: str | None = None) -> None:
    """Report progress from inside a (long-running) operation; no-op without a handler."""
    h = _progress.get()
    if h is not None:
        try:
            h(done, total, msg)
        except Exception:  # noqa: BLE001 - progress must never break the operation
            pass


@contextmanager
def progress_handler(fn: Callable[[float, float | None, str | None], None]) -> Iterator[None]:
    """Install a progress callback for operations run in this context (MCP, CLI)."""
    token = _progress.set(fn)
    try:
        yield
    finally:
        _progress.reset(token)


# --------------------------------------------------------------------------- tests


@contextmanager
def isolated_registry(discovered: bool = True) -> Iterator[None]:
    """Test helper: run with an empty registry, restore the real one afterwards.

    With ``discovered=True`` (default) :func:`discover` does nothing inside the block.
    """
    global _ops, _mcp_names, _doctor, _status, _import_errors, _discovered
    with _lock:
        saved = (_ops, _mcp_names, _doctor, _status, _import_errors, _discovered)
        _ops, _mcp_names, _doctor, _status, _import_errors = {}, {}, {}, {}, {}
        _discovered = discovered
    try:
        yield
    finally:
        with _lock:
            _ops, _mcp_names, _doctor, _status, _import_errors, _discovered = saved
