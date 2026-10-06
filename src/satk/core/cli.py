"""The ``satk`` command line, generated from the registry (SPEC §3.1, §3.3).

* ``satk <words...> [args]`` — words select an operation (``satk dev guard-test PATH``);
  a word that is not a sub-command starts the operation's own arguments.
* Global flags anywhere: ``--json`` / ``--table`` (default: JSON when stdout is not a TTY,
  tables in a terminal), ``--timing`` (elapsed time on stderr), ``-q`` (no output on
  success), ``-h`` (help). ``--fields a,b`` reduces the result of any operation that does
  not declare its own ``fields`` parameter.
* ``--out FILE`` writes the whole envelope (JSON) to ``FILE`` and prints a one-line summary instead;
  ``--json-out FILE`` is the same for operations with an ``--out`` parameter of their own.
  ``--summary`` prints only the one-line summary. Both keep the exit code. Neither applies to an
  operation that declares a parameter of that name (``re limits --summary`` is its own).
* In JSON mode an error envelope is printed on stdout and also echoed as one line on stderr.
* Aliases: ``satk ops <words>`` = ``satk mcp ops <words>`` (search operations), ``satk op <name>`` =
  ``satk mcp op <name>``; ``satk help --find <words>`` searches help topics and operations.
* Exit codes: 0 ok, 1 error, 2 bad arguments (``BAD_PARAMS``), 3 not ready
  (``NOT_READY``/``INDEX_MISSING``/``DEPENDENCY``).
* Negative numbers and lists work as values: ``--pos 2495,-1720,60``, ``world near 2495 -1687``.
* ``dict`` parameters take JSON or ``@file.json``.
* Help for people: ``satk`` (in a terminal), ``satk -h`` and ``satk help`` show the short
  "I want to..." guide; ``satk --all`` / ``satk help --all`` list every command; ``--ru`` gives
  the Russian text. ``satk help <topic>`` keeps the agent topics. Operations can ask
  :func:`surface` whether they run from this command line (``"cli"``) or elsewhere (``"api"``).
"""

from __future__ import annotations

import argparse
import contextvars
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Sequence, TextIO

from . import envelope
from .errors import EXIT_OK, SatkError, exit_code_for
from .registry import OpSpec, all_ops, invoke

__all__ = ["main", "build_parser", "parse_args", "render", "summarize", "CommandTree", "surface", "ALIASES"]

GLOBAL_FLAGS = {"--json": "json", "--table": "table", "--timing": "timing",
                "-q": "quiet", "--quiet": "quiet", "-h": "help", "--help": "help"}
_CELL_MAX = 60
#: Flags of the people's guide (``satk --all``, ``satk --ru``); not global flags.
_GUIDE_FLAGS = ("--all", "--ru")
#: First words that stand for longer commands when no command of that name exists.
ALIASES: dict[str, tuple[str, ...]] = {"ops": ("mcp", "ops"), "op": ("mcp", "op")}
_SUMMARY_MAX = 400

_SURFACE: contextvars.ContextVar[str] = contextvars.ContextVar("satk_surface", default="api")


def surface() -> str:
    """``"cli"`` while an operation runs from :func:`main`, else ``"api"`` (MCP, tests, Python)."""
    return _SURFACE.get()


class _UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):  # type: ignore[override]
        raise _UsageError(message)

    def exit(self, status: int = 0, message: str | None = None):  # type: ignore[override]
        raise _UsageError(message or f"exit {status}")


def build_parser(spec: OpSpec) -> argparse.ArgumentParser:
    """argparse parser for one operation (positionals = params without default)."""
    epilog = "examples:\n  " + "\n  ".join(spec.examples) if spec.examples else None
    p = _Parser(prog=f"satk {spec.cli}", description=spec.summary + (f"\n\n{spec.doc}" if spec.doc else ""),
                add_help=False, allow_abbrev=False, epilog=epilog,
                formatter_class=argparse.RawDescriptionHelpFormatter)
    # Treat "-1687" and "-2495,100" as values, not options (no option looks like a number).
    p._negative_number_matcher = re.compile(r"^-\.?\d")  # type: ignore[attr-defined]
    for prm in spec.params:
        help_ = prm.help or ""
        extra = f"[{prm.type_label}]"
        if prm.has_default and prm.default not in (None, (), []):
            extra += f" (default: {prm.default})"
        # argparse %-formats help strings ("%LOCALAPPDATA%" broke "satk init -h"; Python 3.14 even
        # rejects them in add_argument), so a literal % is doubled.
        help_ = f"{help_} {extra}".strip().replace("%", "%%")
        if prm.positional:
            if prm.kind == "list":
                nargs: Any = "*" if prm.optional else "+"
            else:
                nargs = "?" if prm.optional else None
            # metavar = the parameter name, so errors name the same thing as JSON/MCP ("required: y")
            p.add_argument(prm.name, nargs=nargs, default=None, metavar=prm.name, help=help_)
        elif prm.kind == "bool" and prm.flag.startswith("--no-"):
            # "--no-deps" is already a negation (Python 3.14 refuses BooleanOptionalAction for it)
            p.add_argument(prm.flag, dest=prm.name, action="store_const", const=True, default=None, help=help_)
        elif prm.kind == "bool":
            p.add_argument(prm.flag, dest=prm.name, action=argparse.BooleanOptionalAction,
                           default=None, help=help_)
        elif prm.kind == "list":
            p.add_argument(prm.flag, dest=prm.name, nargs="+", action="extend", default=None,
                           metavar=prm.name.upper(), help=help_)
        else:
            metavar = prm.type_label if prm.kind == "enum" else prm.name.upper()
            p.add_argument(prm.flag, dest=prm.name, default=None, metavar=metavar, help=help_)
    return p


def _is_option_token(parser: argparse.ArgumentParser, t: str) -> bool:
    return t.startswith("-") and t != "-" and not parser._negative_number_matcher.match(t)  # type: ignore[attr-defined]


def _free_required_positionals(spec: OpSpec, parser: argparse.ArgumentParser, argv: list[str]) -> list[str]:
    """Give required positionals back the tokens a multi-value list option swallowed.

    A list option takes every following value (``--tags a b``), so in ``satk asset get --fields name
    model:411`` argparse hands ``model:411`` to ``--fields`` and then misses ``id``. When the
    required positionals lack tokens, the missing number is taken from the end of the last list
    option runs (each run keeps at least one value) and all positional tokens are moved, in their
    original order, in front of the options. Nothing changes when ``--`` is used or enough
    positional tokens are present.
    """
    need = sum(1 for p in spec.params if p.positional and p.required)
    if not need or "--" in argv:
        return argv
    arity: dict[str, str] = {}
    for a in parser._actions:  # noqa: SLF001 - our own parser
        kind = "many" if a.nargs == "+" else ("flag" if a.nargs == 0 else "one")
        for s in a.option_strings:
            arity[s] = kind
    free: list[int] = []
    runs: list[list[int]] = []
    i, n = 0, len(argv)
    while i < n:
        t = argv[i]
        if not _is_option_token(parser, t):
            free.append(i)
            i += 1
            continue
        kind = arity.get(t.split("=", 1)[0])
        if "=" in t or kind in (None, "flag"):
            i += 1
        elif kind == "one":
            i += 2
        else:
            j = i + 1
            while j < n and not _is_option_token(parser, argv[j]):
                j += 1
            runs.append(list(range(i + 1, j)))
            i = j
    missing = need - len(free)
    if missing <= 0 or not runs:
        return argv
    moved: list[int] = []
    for vals in reversed(runs):
        take = min(missing, len(vals) - 1)
        if take > 0:
            moved += vals[len(vals) - take:]
            missing -= take
        if missing <= 0:
            break
    if not moved:
        return argv
    pos = sorted(free + moved)
    taken = set(pos)
    return [argv[k] for k in pos] + [t for k, t in enumerate(argv) if k not in taken]


def _join_query(spec: OpSpec, parser: argparse.ArgumentParser, argv: list[str]) -> list[str]:
    """``satk kb search CStreaming RequestModel`` = ``satk kb search "CStreaming RequestModel"``.

    Only for operations whose single positional parameter is a ``query`` string: their free words
    (tokens that are neither options nor option values) are joined with spaces into that one value.
    """
    pos = [p for p in spec.params if p.positional]
    if len(pos) != 1 or pos[0].name != "query" or pos[0].kind not in ("str", "path") or "--" in argv:
        return argv
    arity: dict[str, str] = {}
    for a in parser._actions:  # noqa: SLF001 - our own parser
        for s in a.option_strings:
            arity[s] = "many" if a.nargs == "+" else ("flag" if a.nargs == 0 else "one")
    free: list[int] = []
    i = 0
    while i < len(argv):
        t = argv[i]
        if not _is_option_token(parser, t):
            free.append(i)
            i += 1
            continue
        kind = arity.get(t.split("=", 1)[0])
        i += 1
        if "=" in t or kind in (None, "flag"):
            continue
        if kind == "one":
            i += 1
            continue
        while i < len(argv) and not _is_option_token(parser, argv[i]):  # a list option takes the run
            i += 1
    if len(free) < 2:
        return argv
    taken = set(free)
    return [" ".join(argv[k] for k in free)] + [t for k, t in enumerate(argv) if k not in taken]


def parse_args(spec: OpSpec, argv: Sequence[str]) -> dict:
    """CLI tokens -> JSON-style argument dict (only given parameters); ``BAD_PARAMS`` on errors."""
    parser = build_parser(spec)
    try:
        ns = parser.parse_args(_free_required_positionals(spec, parser, _join_query(spec, parser, list(argv))))
    except _UsageError as e:
        raise SatkError("BAD_PARAMS", f"satk {spec.cli}: {e}", hint=f"satk {spec.cli} -h") from None
    out: dict = {}
    for prm in spec.params:
        raw = getattr(ns, prm.name, None)
        if raw is None or (prm.positional and prm.kind == "list" and raw == [] and prm.optional):
            continue
        out[prm.name] = prm.coerce_cli(raw)
    return out


# --------------------------------------------------------------------------- command tree


@dataclass
class CommandTree:
    word: str = ""
    spec: OpSpec | None = None
    children: dict[str, "CommandTree"] = field(default_factory=dict)

    @classmethod
    def build(cls, ops: list[OpSpec]) -> "CommandTree":
        root = cls()
        for spec in ops:
            node = root
            for w in spec.cli_path:
                node = node.children.setdefault(w, cls(word=w))
            node.spec = spec
        return root

    def walk(self, tokens: list[str]) -> tuple["CommandTree", list[str], list[str]]:
        """(node, consumed words, remaining tokens)."""
        node, used = self, []
        i = 0
        while i < len(tokens) and tokens[i] in node.children:
            node = node.children[tokens[i]]
            used.append(tokens[i])
            i += 1
        return node, used, tokens[i:]

    def iter_ops(self) -> list[OpSpec]:
        out = [self.spec] if self.spec else []
        for k in sorted(self.children):
            out.extend(self.children[k].iter_ops())
        return out


# --------------------------------------------------------------------------- rendering


def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        s = f"{v:.6g}" if abs(v) < 1e15 else repr(v)
    elif isinstance(v, (list, tuple, dict)):
        s = envelope.dumps(v)
    else:
        s = str(v)
    s = s.replace("\n", " ")
    return s if len(s) <= _CELL_MAX else s[: _CELL_MAX - 1] + "…"


def _flatten(d: dict, prefix: str = "") -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict) and v:
            out.extend(_flatten(v, key + "."))
        else:
            out.append((key, v))
    return out


def _value(v: Any) -> str:
    if isinstance(v, (list, tuple)) and all(not isinstance(x, (list, tuple, dict)) for x in v):
        return ", ".join("" if x is None else str(x) for x in v)
    if isinstance(v, (list, tuple, dict)):
        return envelope.dumps(v)
    if isinstance(v, bool):
        return "true" if v else "false"
    return "" if v is None else str(v)


def _is_checklist(v: Any) -> bool:
    """A list of ``{"name", "status", ...}`` records (doctor-style checks)."""
    return (isinstance(v, list) and bool(v)
            and all(isinstance(x, dict) and "name" in x and "status" in x for x in v))


def _checklist(key: str, items: list[dict]) -> list[str]:
    """``status  name: msg`` lines plus an indented ``fix:`` line (other fields stay in --json)."""
    sw = max(len(str(x.get("status", ""))) for x in items)
    out = [f"{key}:"]
    for x in items:
        msg = str(x.get("msg") or x.get("message") or "").replace("\n", " ")
        out.append(f"  {str(x.get('status', '')).ljust(sw)}  {x.get('name', '')}: {msg}".rstrip())
        if x.get("fix"):
            out.append(" " * (sw + 4) + f"fix: {x['fix']}")
    return out


def render(env: dict) -> str:
    """Human-readable rendering of an envelope (tables, ``key: value`` lines, errors).

    Help topics (``topic`` + ``text``) print their text as it is; lists of ``{name, status, msg}``
    records (doctor checks) print as an aligned checklist.
    """
    lines: list[str] = []
    if not env.get("ok", True):
        err = env.get("error") or {}
        lines.append(f"error {err.get('code', '?')}: {err.get('msg', '')}")
        if err.get("hint"):
            lines.append(f"  hint: {err['hint']}")
        if err.get("did_you_mean"):
            lines.append(f"  did you mean: {', '.join(err['did_you_mean'])}")
        data = err.get("data") or {}
        if isinstance(data, dict) and "cols" in data and "rows" in data:
            lines.append(render({"ok": True, "cols": data["cols"], "rows": data["rows"],
                                 "n": len(data["rows"]), "total": len(data["rows"]), "next": None}))
        elif data:
            for k, v in _flatten(data):
                lines.append(f"  {k}: {_value(v)}")
        return "\n".join(lines)
    if envelope.is_table(env):
        cols = [str(c) for c in env["cols"]]
        rows = [[_cell(c) for c in r] for r in env["rows"]]
        widths = [len(c) for c in cols]
        for r in rows:
            for i, c in enumerate(r):
                widths[i] = max(widths[i], len(c))
        lines.append("  ".join(c.ljust(widths[i]) for i, c in enumerate(cols)).rstrip())
        lines.append("  ".join("-" * w for w in widths))
        for r in rows:
            lines.append("  ".join(c.ljust(widths[i]) for i, c in enumerate(r)).rstrip())
        foot = f"({env.get('n', len(rows))} of {env.get('total', len(rows))})"
        if env.get("next"):
            foot += f"  next: --cursor {env['next']}"
        lines.append(foot)
        rest = {k: v for k, v in env.items() if k not in ("ok", "cols", "rows", "n", "total", "next", "warn")}
        for k, v in _flatten(rest):
            lines.append(f"{k}: {_value(v)}")
    elif "topic" in env and isinstance(env.get("text"), str):
        lines.append(env["text"].rstrip("\n"))
    else:
        rest = {k: v for k, v in env.items() if k not in ("ok", "warn")}
        lists = {k: v for k, v in rest.items() if _is_checklist(v)}
        for k, v in _flatten({k: v for k, v in rest.items() if k not in lists}):
            lines.append(f"{k}: {_value(v)}")
        for k, v in lists.items():
            lines.extend(_checklist(k, v))
    for w in env.get("warn") or ():
        lines.append(f"warn: {w}")
    return "\n".join(lines)


def _short(v: Any, n: int = 48) -> str:
    s = _value(v).replace("\n", " ")
    return s if len(s) <= n else s[: n - 3] + "..."


def summarize(env: dict) -> str:
    """One line about an envelope: rows/total/next of a table, scalar fields and sizes of the rest.

    ``ok: 20 of 51 rows (cols idx, name); next 20`` or ``ok: kind=dff; frames=51; rows[20]; warn 1: X``.
    """
    if not env.get("ok", True):
        err = env.get("error") or {}
        return f"error {err.get('code', '?')}: {err.get('msg', '')}"
    parts: list[str] = []
    if envelope.is_table(env) and isinstance(env.get("rows"), list):
        rows = env["rows"]
        count = f"{env.get('n', len(rows))} of {env['total']}" if "total" in env else str(len(rows))
        parts.append(f"{count} rows (cols {', '.join(map(str, env.get('cols') or []))})")
        if env.get("next"):
            parts.append(f"next {env['next']}")
    skip = {"ok", "warn", "cols", "rows", "n", "total", "next"} if parts else {"ok", "warn"}
    for k, v in env.items():
        if k in skip or v is None:
            continue
        if isinstance(v, dict):
            parts.append(f"{k}{{{len(v)}}}")
        elif isinstance(v, (list, tuple)):
            parts.append(f"{k}[{len(v)}]")
        else:
            parts.append(f"{k}={_short(v)}")
    warns = env.get("warn") or []
    if warns:
        parts.append(f"warn {len(warns)}: " + str(warns[0]).split(":", 1)[0])
    text = "ok: " + "; ".join(parts) if parts else "ok"
    return text if len(text) <= _SUMMARY_MAX else text[: _SUMMARY_MAX - 3] + "..."


def _summary_env(env: dict, out_path: str | None = None, size: int | None = None) -> dict:
    """The small envelope printed by ``--summary`` / ``--out`` (an error envelope stays whole)."""
    if not env.get("ok", True):
        res = dict(env)
    else:
        res = {"ok": True, "summary": summarize(env)}
        if env.get("warn"):
            res["warn"] = list(env["warn"])[:3]
    if out_path:
        res["out"] = out_path
        res["bytes"] = size
    return res


def _is_utf(stream: TextIO) -> bool:
    enc = (getattr(stream, "encoding", None) or "").lower().replace("-", "").replace("_", "")
    return enc in ("utf8", "utf8sig")


def _encodable(text: str, stream: TextIO) -> str:
    """Replace characters the stream's encoding cannot represent (cp1251 consoles etc.)."""
    enc = getattr(stream, "encoding", None)
    if not enc or _is_utf(stream):
        return text
    try:
        return text.encode(enc, "replace").decode(enc)
    except LookupError:  # pragma: no cover - unknown codec name
        return text


def _emit(env: dict, mode: str, out: TextIO, err: TextIO) -> None:
    if mode == "json":
        out.write(envelope.dumps(env, ascii=not _is_utf(out)) + "\n")
        if not env.get("ok", True):  # visible even when stdout is parsed or redirected
            e = env.get("error") or {}
            line = f"error {e.get('code', '?')}: {e.get('msg', '')}"
            if e.get("hint"):
                line += f" (hint: {e['hint']})"
            err.write(_encodable(line.replace("\n", " "), err) + "\n")
            err.flush()
    else:
        stream = out if env.get("ok", True) else err
        stream.write(_encodable(render(env), stream) + "\n")
        stream.flush()
    out.flush()


def _listing(node: CommandTree, prefix: list[str]) -> dict:
    rows = []
    for spec in node.iter_ops():
        rows.append([f"satk {spec.cli}", spec.mcp_name, spec.summary])
    env = envelope.table(["command", "mcp", "summary"], rows)
    env["usage"] = "satk " + (" ".join(prefix) + " " if prefix else "") + "<command> [args] [--json|--table] [-h]"
    return env


def _take_fields(tokens: list[str]) -> tuple[list[str], list[str] | None]:
    """Remove a global ``--fields a,b`` (or ``--fields=a,b``) from ``tokens``.

    Used only for operations that do not declare a ``fields`` parameter themselves: the
    envelope is then reduced with :func:`envelope.select_fields` after the call.
    """
    out: list[str] = []
    fields: list[str] | None = None
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t == "--":
            out.extend(tokens[i:])
            break
        if t == "--fields" and i + 1 < len(tokens):
            fields = (fields or []) + [f.strip() for f in tokens[i + 1].split(",") if f.strip()]
            i += 2
            continue
        if t.startswith("--fields="):
            fields = (fields or []) + [f.strip() for f in t[len("--fields="):].split(",") if f.strip()]
            i += 1
            continue
        out.append(t)
        i += 1
    return out, fields


def _take_output(tokens: list[str], *, out: bool, summary: bool) -> tuple[list[str], str | None, bool]:
    """Remove the global ``--json-out FILE`` (always), ``--out FILE`` (when ``out``) and ``--summary``
    (when ``summary``) from ``tokens``: ``(rest, file or None, summary wanted)``."""
    rest: list[str] = []
    path: str | None = None
    want = False
    names = ("--json-out",) + (("--out",) if out else ())
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t == "--":
            rest.extend(tokens[i:])
            break
        key, eq, val = t.partition("=")
        if key in names:
            if eq:
                path = val
                i += 1
                continue
            if i + 1 >= len(tokens):
                raise SatkError("BAD_PARAMS", f"{key} needs a file name", hint=f"{key} result.json")
            path = tokens[i + 1]
            i += 2
            continue
        if summary and t == "--summary":
            want = True
            i += 1
            continue
        rest.append(t)
        i += 1
    return rest, path, want


def _write_out(path: str, env: dict) -> tuple[str, int]:
    """Write the envelope as indented JSON (a relative path is taken from the current folder)."""
    import os
    from pathlib import Path

    from .paths import atomic_write, jpath

    p = Path(os.path.abspath(Path(path).expanduser()))
    data = (envelope.dumps(env, pretty=True) + "\n").encode("utf-8")
    atomic_write(p, data)
    return jpath(p), len(data)


def _help_find(remaining: list[str]) -> dict:
    """``satk help --find <words> [--limit N]``: help topics and operations matching the words."""
    from satk.runtime.help import find as _find

    words: list[str] = []
    limit = 20
    i = 0
    while i < len(remaining):
        t = remaining[i]
        key, eq, val = t.partition("=")
        if t == "--find":
            i += 1
            continue
        if key == "--limit":
            raw = val if eq else (remaining[i + 1] if i + 1 < len(remaining) else "")
            try:
                limit = int(raw)
            except ValueError:
                raise SatkError("BAD_PARAMS", f"--limit: not a number: {raw!r}",
                                hint="satk help --find <words> --limit 50") from None
            i += 1 if eq else 2
            continue
        if t.startswith("-"):
            raise SatkError("BAD_PARAMS", f"satk help --find: unknown option {t}",
                            hint="satk help --find <words> [--limit N]")
        words.append(t)
        i += 1
    if not words:
        raise SatkError("BAD_PARAMS", "satk help --find needs words", hint="satk help --find make txd from png")
    return _find(" ".join(words), limit=limit)


def _setup_streams() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):  # pragma: no cover
            pass


def _guide_request(node: CommandTree, used: list[str], remaining: list[str], flags: set[str],
                   mode: str) -> list[str] | None:
    """Arguments for the help operation when the bare ``satk`` asks for the people's guide.

    ``satk`` in a terminal, ``satk -h``, ``satk --ru`` -> the guide; ``satk --all`` -> every command.
    Bare ``satk`` with JSON output keeps the machine listing of all commands.
    """
    if used or node.spec is not None or any(t not in _GUIDE_FLAGS for t in remaining):
        return None
    if not remaining and "help" not in flags and mode == "json":
        return None
    return list(remaining)


def main(argv: Sequence[str] | None = None, *, stdout: TextIO | None = None, stderr: TextIO | None = None) -> int:
    """Entry point of ``python -m satk``; returns the exit code."""
    if stdout is None and stderr is None:
        _setup_streams()
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    tokens = list(sys.argv[1:] if argv is None else argv)

    flags: set[str] = set()
    rest: list[str] = []
    for i, t in enumerate(tokens):
        if t == "--":
            rest.extend(tokens[i:])
            break
        if t in GLOBAL_FLAGS:
            flags.add(GLOBAL_FLAGS[t])
        else:
            rest.append(t)
    if "json" in flags:
        mode = "json"
    elif "table" in flags:
        mode = "table"
    else:
        mode = "table" if getattr(out, "isatty", lambda: False)() else "json"

    from .log import setup

    setup()
    t0 = time.perf_counter()
    code = EXIT_OK
    out_file: str | None = None
    want_summary = False
    token = _SURFACE.set("cli")
    try:
        tree = CommandTree.build(all_ops())
        if rest and rest[0] in ALIASES and rest[0] not in tree.children:
            rest = [*ALIASES[rest[0]], *rest[1:]]
        node, used, remaining = tree.walk(rest)
        guide = _guide_request(node, used, remaining, flags, mode)
        if guide is not None:  # satk / satk -h / satk --all / satk --ru -> the help operation
            node, used, _ = tree.walk(["help"])
            remaining = guide
            flags.discard("help")
        if node.spec is None:
            if remaining and not remaining[0].startswith("-"):
                import difflib

                word = remaining[0]
                where = " ".join(used)
                raise SatkError(
                    "BAD_PARAMS",
                    f"unknown command: satk {where + ' ' if where else ''}{word}",
                    did_you_mean=[f"satk {(where + ' ') if where else ''}{w}"
                                  for w in difflib.get_close_matches(word, list(node.children), n=3, cutoff=0.5)],
                    hint="satk " + (where + " " if where else "") + "-h",
                )
            if remaining:
                raise SatkError("BAD_PARAMS", f"unexpected arguments: {' '.join(remaining)}", hint="satk -h")
            _emit(_listing(node, used), mode, out, err)
            return EXIT_OK
        spec = node.spec
        if "help" in flags:
            if node.children:
                out.write(render(_listing(node, used)) + "\n\n")
            out.write(build_parser(spec).format_help())
            out.flush()
            return EXIT_OK
        post_fields: list[str] | None = None
        own = {p.name for p in spec.params}
        if "fields" not in own:
            remaining, post_fields = _take_fields(remaining)
        remaining, out_file, want_summary = _take_output(remaining, out="out" not in own,
                                                         summary="summary" not in own)
        if spec.name == "help" and "--find" in remaining:  # satk help --find <words>
            env = _help_find(remaining)
        else:
            if spec.name == "help" and "--all" in remaining:  # satk help --all = topic "all"
                remaining = ["all"] + [t for t in remaining if t != "--all"]
            args = parse_args(spec, remaining)
            env = invoke(spec, args)
        if post_fields and env.get("ok", True):
            env = envelope.select_fields(env, post_fields)
    except SatkError as e:
        env = e.to_dict()
    except KeyboardInterrupt:
        err.write("interrupted\n")
        return 130
    finally:
        _SURFACE.reset(token)
    if out_file:
        try:
            where, size = _write_out(out_file, env)
            env = _summary_env(env, where, size)
        except SatkError as e:
            env = e.to_dict()
    elif want_summary:
        env = _summary_env(env)
    if not env.get("ok", True):
        code = exit_code_for((env.get("error") or {}).get("code", "INTERNAL"))
    if not ("quiet" in flags and code == EXIT_OK):
        _emit(env, mode, out, err)
    if "timing" in flags:
        err.write(f"[timing] {(time.perf_counter() - t0) * 1000:.1f} ms\n")
        err.flush()
    return code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
