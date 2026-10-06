"""Shared pieces of batch and recipes: operation lookup, the run policy, nesting, short result lines."""

from __future__ import annotations

import contextvars
import difflib
import re
from contextlib import contextmanager
from typing import Any, Iterator

from ..core.errors import SatkError
from ..core.registry import OpSpec, ParamSpec, all_ops

__all__ = ["find_op", "resolve_op", "refusal", "check_policy", "input_param", "nested", "one_line", "MAX_DEPTH",
           "NEVER"]

#: Operations that never run inside a batch or a recipe, whatever ``--yes`` says: they serve until
#: interrupted, are the MCP server itself, or run the whole test-suite.
NEVER: dict[str, str] = {
    "mcp": "it is the MCP server (stdio) and serves until stopped",
    "view.mock": "it serves until interrupted",
    "dev.gate": "the acceptance gate runs the whole test-suite for minutes",
}
#: Batches and recipes may nest (a recipe step can be a batch); this many levels at most.
MAX_DEPTH = 4
_depth: contextvars.ContextVar[int] = contextvars.ContextVar("satk_batch_depth", default=0)


def _key(text: str) -> str:
    """``"satk Asset lint"`` / ``"asset_lint"`` / ``"asset.lint"`` -> a comparable key."""
    s = text.strip().lower()
    if s.startswith("satk "):
        s = s[5:]
    return re.sub(r"[\s.]+", ".", s.strip()).replace("-", "_").strip(".")


def find_op(name: str) -> OpSpec | None:
    """Operation by dotted name, CLI words (``asset lint``) or MCP tool name; ``None`` when not registered."""
    if not isinstance(name, str) or not name.strip():
        return None
    ops = all_ops()
    by_name = {o.name: o for o in ops}
    k = _key(name)
    if k in by_name:
        return by_name[k]
    for o in ops:
        if k in (_key(o.cli), o.mcp_name or "", o.name.replace(".", "_")):
            return o
    return None


def resolve_op(name: str) -> OpSpec:
    """Like :func:`find_op` but ``NOT_FOUND`` with suggestions."""
    spec = find_op(name)
    if spec is not None:
        return spec
    names = [o.name for o in all_ops()]
    raise SatkError("NOT_FOUND", f"no operation {str(name).strip()!r}",
                    did_you_mean=difflib.get_close_matches(_key(str(name)), names, n=3, cutoff=0.5),
                    hint="satk help ops lists every operation (dotted names: asset.lint, formats.dump)")


def _surface() -> str:
    from ..core.cli import surface

    return surface()


def refusal(spec: OpSpec, *, yes: bool = False) -> tuple[str, str, str] | None:
    """``(code, message, hint)`` when ``spec`` must not run inside a batch/recipe, else ``None``.

    * :data:`NEVER` and every ``mcp.*`` operation: always ``UNSUPPORTED``;
    * operations an agent may not call (``satk.mcp.generic.denial``: CLI only or consent): allowed only
      with ``yes`` given on the command line -- an AI client (MCP, Python) can never give consent;
    * outside the command line, operations outside the server's ``SATK_MCP_GROUPS``: ``UNSUPPORTED``.
    """
    name = spec.name
    why = NEVER.get(name) or ("MCP server management" if name.startswith("mcp.") else None)
    if why:
        return ("UNSUPPORTED", f"{name} cannot run inside a batch or a recipe: {why}", f"run `satk {spec.cli}` alone")
    from ..mcp.generic import current_groups, denial

    cli = _surface() == "cli"
    d = denial(spec)
    if d is not None:
        code, reason = d
        if yes and cli:
            return None
        if yes:
            return (code, f"{name} needs the user's consent ({reason}); --yes counts only when the user types it "
                          "on the command line", f"ask the user to run it in a terminal: satk {spec.cli}")
        return (code, f"{name} is not run inside a batch or a recipe without consent: {reason}",
                "ask the user; in a terminal add --yes to the batch/recipe command")
    if not cli:
        groups = current_groups()
        if groups is not None and spec.mcp_group not in groups:
            return ("UNSUPPORTED", f"{name} is in MCP group {spec.mcp_group!r}, outside SATK_MCP_GROUPS",
                    f"run `satk {spec.cli}` in a terminal")
    return None


def check_policy(spec: OpSpec, *, yes: bool = False) -> None:
    """Raise the :func:`refusal` as a ``SatkError``."""
    r = refusal(spec, yes=yes)
    if r is not None:
        code, msg, hint = r
        raise SatkError(code, msg, hint=hint, data={"op": spec.name})


def input_param(spec: OpSpec, name: str | None = None) -> ParamSpec | None:
    """The parameter an input goes to: ``name``, else the first required one, else the first positional."""
    if name:
        key = name.replace("-", "_")
        for p in spec.params:
            if p.name == key:
                return p
        raise SatkError("BAD_PARAMS", f"{spec.name} has no parameter {name!r}",
                        did_you_mean=difflib.get_close_matches(key, [p.name for p in spec.params], n=3, cutoff=0.5),
                        data={"params": [p.name for p in spec.params]})
    for p in spec.params:
        if p.required:
            return p
    for p in spec.params:
        if p.positional:
            return p
    return None


@contextmanager
def nested(what: str) -> Iterator[int]:
    """Enter one more batch/recipe level; ``BAD_PARAMS`` beyond :data:`MAX_DEPTH`."""
    d = _depth.get() + 1
    if d > MAX_DEPTH:
        raise SatkError("BAD_PARAMS", f"{what}: batches and recipes nest {MAX_DEPTH} levels at most",
                        hint="a recipe that runs itself? check the recipe's steps")
    token = _depth.set(d)
    try:
        yield d
    finally:
        _depth.reset(token)


def _short(v: Any, n: int = 40) -> str:
    if isinstance(v, float):
        s = f"{v:.6g}"
    elif isinstance(v, (list, tuple)):
        s = f"[{len(v)}]"
    elif isinstance(v, dict):
        s = "{" + ",".join(f"{k}={_short(x, 12)}" for k, x in list(v.items())[:6] if not isinstance(x, (dict, list)))
        s += "}"
    else:
        s = str(v).replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


_COUNTERS = ("counts", "summary", "totals", "changes")


def one_line(env: dict, width: int = 160) -> str:
    """A one-line digest of an envelope: ``CODE: msg``, or counters (``counts ok=3 fail=1``), the row count and
    the first scalar fields."""
    if not isinstance(env, dict):
        return _short(env, width)
    if not env.get("ok", True):
        err = env.get("error") or {}
        s = f"{err.get('code', '?')}: {err.get('msg', '')}"
    else:
        parts = []
        for k in _COUNTERS:
            if isinstance(env.get(k), dict):
                kv = [f"{a}={_short(b, 12)}" for a, b in env[k].items() if not isinstance(b, (dict, list))]
                if kv:
                    parts.append(f"{k} " + " ".join(kv))
        if "cols" in env and "rows" in env and "counts" not in env:
            total = env.get("total", len(env["rows"]))
            parts.insert(0, f"{total} row(s)" if total else "no rows")
        skip = {"ok", "cols", "rows", "n", "total", "next", "warn", "hint", *_COUNTERS}
        parts += [f"{k}={_short(v, 50)}" for k, v in env.items() if k not in skip and not isinstance(v, (list, dict))]
        s = "; ".join(parts[:7]) or "ok"
    if env.get("warn"):
        s += f" (warn {len(env['warn'])})"
    s = s.replace("\n", " ")
    return s if len(s) <= width else s[: width - 1] + "…"
