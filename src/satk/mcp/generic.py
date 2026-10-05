"""Generic MCP access to every registered operation: ``satk_ops(query)`` and ``satk_op(op, args)`` (M2-01).

Only a few operations have their own MCP tool: ``tools/list`` must stay under 16 KB, and every new
operation is ``mcp=False`` by default. These two tools reach all the others without growing the
list:

* ``satk_ops(query, limit)`` -- search the registry (name, CLI words, summaries, parameters, docs,
  examples); rows ``op | args | summary`` with a compact signature, and the full JSON ``schema``
  when the query has exactly one hit or names an operation exactly;
* ``satk_op(op, args)`` -- resolve ``op`` (dotted name, CLI words, MCP tool name), check the policy
  below, validate ``args`` with the operation's own schema (the CLI path: ``OpSpec.bind``) and run
  it. In the MCP server ordinary operations run in a worker thread under their group lock and
  ``long_running`` ones in the worker subprocess inside a ProcessTree (``satk.mcp.server``).

Policy (:func:`denial`): an operation is agent-callable unless

* it is listed in :data:`DENY` (server management, interactive setup, destructive writers of the
  game copy, the dev gate, ...) -- code ``UNSUPPORTED`` (CLI only) or ``CONSENT_REQUIRED``;
* its package marked it with :func:`cli_only` (``OpSpec.extra["agent"] = False``);
* its MCP group is outside ``SATK_MCP_GROUPS`` of the server (``UNSUPPORTED``).

Stdlib only (the server, the CLI ops, ``satk help`` and ``satk dev gen-docs`` import it).
"""

from __future__ import annotations

import contextvars
import difflib
import json
import os
import re
from contextlib import contextmanager
from typing import Any, Callable, Iterator

from satk.core.envelope import clamp_limit, table
from satk.core.errors import SatkError
from satk.core.registry import OpSpec, all_ops

__all__ = [
    "FIND_OP",
    "RUN_OP",
    "DENY",
    "cli_only",
    "denial",
    "callable_ops",
    "current_groups",
    "serving",
    "resolve",
    "check",
    "prepare",
    "args_signature",
    "search",
]

#: Registry operations behind the two generic MCP tools (``satk mcp ops`` / ``satk mcp op``).
FIND_OP = "mcp.ops"
RUN_OP = "mcp.op"

_CLI = "UNSUPPORTED"
_ASK = "CONSENT_REQUIRED"

#: Operations an agent must not run through ``satk_op``: name -> (error code, reason).
#: ``UNSUPPORTED`` = CLI only (would break the MCP session, block, or is a developer/maintenance
#: command); ``CONSENT_REQUIRED`` = changes the user's setup or deletes data: ask the user.
#: Any other ``mcp`` / ``mcp.*`` operation is denied as server management (see :func:`denial`).
DENY: dict[str, tuple[str, str]] = {
    "mcp": (_CLI, "it is the MCP server itself (stdio)"),
    "mcp.selftest": (_CLI, "it starts a second MCP server"),
    "mcp.config": (_CLI, "it rewrites MCP client configs (.mcp.json, the user's AI clients)"),
    "agent.install_skill": (_ASK, "it writes skill files into the user's AI client folders"),
    FIND_OP: (_CLI, "it is the satk_ops tool itself"),
    RUN_OP: (_CLI, "it is the satk_op tool itself"),
    "init": (_ASK, "first-time setup writes satk.toml and may ask questions on the terminal"),
    "dev.gate": (_CLI, "the acceptance gate runs the whole test-suite for minutes"),
    "dev.gen_docs": (_CLI, "it rewrites generated docs of the checkout"),
    "dev.sync_agent_docs": (_CLI, "it publishes agent docs into the workspace"),
    "game.clone": (_ASK, "it writes the clean game copy (gigabytes)"),
    "game.protect": (_ASK, "it changes the write protection of a game folder"),
    "game.unprotect": (_ASK, "it removes the write protection of a game folder"),
    "engine.setup": (_ASK, "it may download client dependencies (consent D3)"),
    "note.rm": (_ASK, "it deletes a note for good"),
    "view.mock": (_CLI, "it serves until interrupted and would block the call"),
}


def cli_only(reason: str, *, consent: bool = False) -> Callable:
    """Mark an operation as not callable through ``satk_op``; put it **above** ``@op``::

        @cli_only("rewrites the user's config")
        @op("x.reset", summary=..., summary_ru=..., mcp=False)
        def reset() -> dict: ...

    ``consent=True`` answers ``CONSENT_REQUIRED`` (ask the user) instead of ``UNSUPPORTED``.
    """

    def deco(fn: Callable) -> Callable:
        spec = getattr(fn, "__satk_op__", None)
        if not isinstance(spec, OpSpec):
            raise TypeError("@cli_only must be placed above @op")
        spec.extra["agent"] = False
        spec.extra["agent_reason"] = reason
        spec.extra["agent_code"] = _ASK if consent else _CLI
        return fn

    return deco


def denial(spec: OpSpec) -> tuple[str, str] | None:
    """``(error code, reason)`` when ``spec`` must not be run by an agent through ``satk_op``."""
    hit = DENY.get(spec.name)
    if hit is not None:
        return hit
    if spec.extra.get("agent") is False:
        code = spec.extra.get("agent_code")
        return (code if code in (_CLI, _ASK) else _CLI, str(spec.extra.get("agent_reason") or "marked CLI-only"))
    if spec.name == "mcp" or spec.name.startswith("mcp."):
        return _CLI, "MCP server management"
    return None


# --------------------------------------------------------------------------- groups

_ENV = object()
_groups_var: contextvars.ContextVar[Any] = contextvars.ContextVar("satk_generic_groups", default=_ENV)


def current_groups() -> set[str] | None:
    """MCP groups ``satk_op`` may reach: the serving server's, else ``SATK_MCP_GROUPS`` (``None`` = all).

    Outside the server an unknown group name is ignored (the server itself refuses to start).
    """
    g = _groups_var.get()
    if g is not _ENV:
        return g
    from .adapter import parse_groups

    return parse_groups(os.environ.get("SATK_MCP_GROUPS"))[0]


@contextmanager
def serving(groups: set[str] | None) -> Iterator[None]:
    """Make :func:`current_groups` return ``groups`` (the MCP server wraps its calls in this)."""
    token = _groups_var.set(groups)
    try:
        yield
    finally:
        _groups_var.reset(token)


def _in_groups(spec: OpSpec, groups: set[str] | None) -> bool:
    return groups is None or spec.mcp_group in groups


def callable_ops(groups: set[str] | None = None) -> list[OpSpec]:
    """Operations ``satk_op`` runs for these groups (``None`` = all), sorted by name."""
    return [o for o in all_ops() if denial(o) is None and _in_groups(o, groups)]


# --------------------------------------------------------------------------- resolve / check


def _key(text: str) -> str:
    """``"satk Dev guard-test"`` / ``"dev.guard_test"`` -> ``"dev.guard_test"``."""
    s = text.strip().lower()
    if s.startswith("satk "):
        s = s[5:]
    return re.sub(r"[\s.]+", ".", s.strip()).replace("-", "_").strip(".")


def _aliases() -> dict[str, OpSpec]:
    from .adapter import GENERIC_TOOLS

    ops = all_ops()
    by_name = {o.name: o for o in ops}
    out: dict[str, OpSpec] = dict(by_name)  # dotted names win over every other spelling
    for tool, name in GENERIC_TOOLS.items():
        if name in by_name:
            out.setdefault(tool, by_name[name])
    for o in ops:
        for k in (o.mcp_name, _key(o.cli), o.name.replace(".", "_")):
            if k:
                out.setdefault(k, o)
    return out


def resolve(name: str) -> OpSpec:
    """Operation by dotted name, CLI words or MCP tool name; ``NOT_FOUND`` with suggestions."""
    if not isinstance(name, str) or not name.strip():
        raise SatkError("BAD_PARAMS", "op: give an operation name, e.g. \"index.status\"",
                        hint="satk_ops(query) lists operations")
    aliases = _aliases()
    raw = name.strip().lower()
    spec = aliases.get(raw) or aliases.get(_key(name))
    if spec is not None:
        return spec
    names = [o.name for o in callable_ops(None)]
    word = re.split(r"[\s._-]+", name.strip())[-1]
    raise SatkError("NOT_FOUND", f"no operation {name.strip()!r}",
                    did_you_mean=difflib.get_close_matches(_key(name), names, n=3, cutoff=0.5),
                    hint=f"find it with satk_ops(\"{word}\")")


def check(spec: OpSpec, groups: set[str] | None = None) -> None:
    """Raise the policy error when ``spec`` is not agent-callable for ``groups``."""
    why = denial(spec)
    if why is not None:
        code, reason = why
        hint = (f"ask the user; in a terminal: satk {spec.cli}" if code == _ASK
                else f"CLI only: run `satk {spec.cli}` in a terminal")
        raise SatkError(code, f"{spec.name} is not callable through satk_op: {reason}", hint=hint,
                        data={"op": spec.name})
    if not _in_groups(spec, groups):
        allowed = ",".join(sorted(groups or ()))
        raise SatkError(_CLI, f"{spec.name} is in MCP group {spec.mcp_group!r}, outside this server's "
                        f"SATK_MCP_GROUPS={allowed}",
                        hint=f"add {spec.mcp_group!r} to SATK_MCP_GROUPS, or run `satk {spec.cli}` in a terminal",
                        data={"op": spec.name, "group": spec.mcp_group})


def prepare(name: str, args: dict | None, groups: set[str] | None = None) -> tuple[OpSpec, dict]:
    """Resolve, check and validate one call; returns ``(spec, args)`` ready for ``invoke``.

    Validation is ``OpSpec.bind`` -- exactly what the CLI and the op's own MCP tool do -- so a
    bad argument is ``BAD_PARAMS`` (with ``did_you_mean``) before anything runs or spawns.
    """
    spec = resolve(name)
    check(spec, groups)
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise SatkError("BAD_PARAMS", f"args: expected a JSON object, got {type(args).__name__}",
                        hint=f"satk_op(op=\"{spec.name}\", args={{...}}); parameters: satk_help(\"{spec.name}\")")
    try:
        spec.bind(args)
    except SatkError as e:
        if not e.hint:
            e.hint = f"parameters: satk_help(\"{spec.name}\") or satk_ops(\"{spec.name}\")"
        raise
    return spec, dict(args)


# --------------------------------------------------------------------------- search

_TYPES = {"str": "str", "path": "str", "int": "int", "float": "num", "bool": "bool", "dict": "{}",
          "list[float]": "[num]", "list[int]": "[int]"}


def _ptype(p: Any) -> str:
    if p.kind == "enum":
        return "|".join(map(str, p.choices or ()))
    if p.kind == "list":
        return f"[{_TYPES.get(p.item or 'str', p.item or 'str')}]"
    return _TYPES.get(p.kind, p.kind)


def _jdefault(v: Any) -> str:
    if isinstance(v, tuple):
        v = list(v)
    try:
        return json.dumps(v, ensure_ascii=False)
    except (TypeError, ValueError):
        return json.dumps(str(v), ensure_ascii=False)


def args_signature(spec: OpSpec) -> str:
    """Compact argument schema: ``target:str, level:stats|full|tree="stats", kind?:str``.

    ``name:type`` = required; ``name?:type`` = optional (null); ``name:type=default``. Types:
    ``str int num bool {}`` (JSON object), ``[str]`` (list), ``a|b`` (enum).
    """
    parts = []
    for p in spec.params:
        t = _ptype(p)
        if p.required:
            parts.append(f"{p.name}:{t}")
        elif not p.has_default or p.default is None:
            parts.append(f"{p.name}?:{t}")
        else:
            parts.append(f"{p.name}:{t}={_jdefault(p.default)}")
    return ", ".join(parts)


def _first_sentence(text: str, cap: int = 160) -> str:
    cut = text.find(". ")
    s = text if cut < 0 else text[: cut + 1]
    return s if len(s) <= cap else s[: cap - 3].rstrip() + "..."


def _haystack(o: OpSpec) -> list[tuple[str, int]]:
    from .adapter import tool_name

    params = " ".join(p.name for p in o.params)
    more = " ".join(f"{p.help} {' '.join(map(str, p.choices or ()))}" for p in o.params)
    return [(f"{o.name} {o.cli} {tool_name(o) or ''}".lower(), 8), (o.summary.lower(), 4),
            (o.summary_ru.lower(), 3), (params.lower(), 2), (o.doc.lower(), 1), (more.lower(), 1),
            (" ".join(o.examples).lower(), 1)]


def _score(o: OpSpec, words: list[str], hay: list[tuple[str, int]]) -> tuple[int, int]:
    """(matched words, score)."""
    segs = set(re.split(r"[._\s-]+", f"{o.name} {o.cli}".lower()))
    matched = score = 0
    for w in words:
        best = max((wt for text, wt in hay if w in text), default=0)
        if best:
            matched += 1
            score += best + (6 if w in segs else 0)
    return matched, score


def _row(o: OpSpec, *, full: bool) -> list:
    tags = ([f"tool {o.mcp_name}"] if o.mcp_name else []) + (["long-running"] if o.long_running else [])
    s = o.summary if full else _first_sentence(o.summary)
    return [o.name, args_signature(o), s + (f" ({', '.join(tags)})" if tags else "")]


def _why_not(o: OpSpec, groups: set[str] | None) -> str:
    why = denial(o)
    return f"{o.name}: {why[1]}" if why else f"{o.name}: MCP group {o.mcp_group!r} is outside SATK_MCP_GROUPS"


def search(query: str | None = None, limit: int = 20, groups: set[str] | None = None) -> dict:
    """Table ``op | args | summary`` of agent-callable operations matching ``query``.

    Every word must match (name, CLI words, summaries, parameter names, docs, examples); when no
    operation matches all words, operations matching most of them are listed with a warning.
    With exactly one row, or a query that names an operation, the result also has ``schema``
    (the operation's full JSON Schema with parameter descriptions) and ``examples``. Matching
    operations that ``satk_op`` refuses are named in ``cli_only`` (with the reason).
    """
    lim = clamp_limit(limit)
    pool = callable_ops(groups)
    in_pool = {o.name for o in pool}
    q = (query or "").strip()
    words = re.findall(r"[\w.\-]+", q.lower())
    warn: list[str] = []
    exact: OpSpec | None = None
    blocked: list[OpSpec] = []
    if words:
        try:
            cand: OpSpec | None = resolve(q)
        except SatkError:
            cand = None
        if cand is not None:
            if cand.name in in_pool:
                exact = cand
            else:
                blocked.append(cand)
        scored = []
        for o in pool:
            matched, score = _score(o, words, _haystack(o))
            if matched:
                scored.append((matched, score, o))
        top = max((m for m, _, _ in scored), default=0)
        if top and top < len(words):
            warn.append(f"NO_FULL_MATCH: no operation matches all of {words}; showing the closest")
        hits = [o for m, _, o in sorted(scored, key=lambda t: (-t[0], -t[1], t[2].name)) if m == top]
        if exact is not None:
            hits = [exact] + [o for o in hits if o is not exact]
        # Say which refused operations match: by name always, by any text when no callable operation
        # matched every word (never the generic tools themselves).
        partial = top < len(words)
        for o in all_ops():
            if o.name in in_pool or o.name in (FIND_OP, RUN_OP) or any(b is o for b in blocked):
                continue
            hay = _haystack(o)
            if all(w in hay[0][0] for w in words) or (partial and _score(o, words, hay)[0] == len(words)):
                blocked.append(o)
    else:
        hits = pool
    shown = hits[:lim]
    single = len(hits) == 1 or exact is not None
    env = table(["op", "args", "summary"], [_row(o, full=single and i == 0) for i, o in enumerate(shown)],
                total=len(hits), warn=warn)
    if shown and single:
        env["schema"] = shown[0].input_schema()
        if shown[0].examples:
            env["examples"] = list(shown[0].examples[:3])
    if blocked:
        env["cli_only"] = [_why_not(o, groups) for o in blocked[:10]]
    if shown:
        env["hint"] = f"run: satk_op(op=\"{shown[0].name}\", args={{...}}); docs: satk_help(\"{shown[0].name}\")"
    else:
        env["hint"] = "try other words, or satk_ops() for the full list; satk_help(\"tools\") lists the tools"
    return env
