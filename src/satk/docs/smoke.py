"""Run the quick-example blocks of the user docs (``satk dev docs-smoke``).

Convention for ``docs/en/*.md`` pages and their Russian mirrors ``docs/ru/*.md`` (SPEC §5.3 WP-12,
acceptance 4):

* a heading whose text starts with ``Quick example`` (on a Russian page: its Russian translation, see
  :data:`QUICK_HEADING`) opens a section;
  every fenced shell block in it (``powershell``, ``cmd``, ``bash``, ``console`` or no language)
  is executed line by line until the next heading of the same or a higher level;
* only ``satk`` command lines run (``satk ...``, ``satk.cmd ...``, ``D:\\...\\satk.cmd ...``,
  ``& "...\\satk.cmd" ...``); comments (``#``, ``rem``, ``::``) and empty lines are skipped;
  other commands are reported as ``skip`` and not run;
* commands must be concrete: no ``<placeholders>``, pipes or redirection (they fail with
  ``not runnable``); viewer examples use ``--target mock``;
* ``<!-- docs-smoke: skip <reason> -->`` right before a fence skips that block.

Each command runs in a subprocess ``python -X utf8 -m satk <args>`` with ``PYTHONPATH`` = this
checkout's ``src`` (what the shim does). An unknown command group or command is a ``fail`` with a
"did you mean" (a typo in the docs: ``satk inedx build``). A page that documents a package whose
operations are not merged yet marks its block with the skip directive. ``strict`` also fails other
(non-satk) commands inside a quick example, which are otherwise ``skip``.

Pages whose quick example rewrites shared state under ``work/`` (:data:`SHARED_STATE_OPS`:
``satk index build``, ``satk re build``, ``satk kb build``, ``satk paths import``, note and bookmark
writes) run with their own
``SATK_PATHS_WORK`` (see ``satk dev docs-smoke --isolate``), so a smoke run never rebuilds the index
or the symbol DB that other sessions are querying.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

__all__ = ["Command", "extract_commands", "tokenize", "parse_line", "classify", "run_command", "default_pages",
           "shared_state_writer", "SHARED_STATE_OPS", "LANGS", "QUICK_HEADING"]

QUICK_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(?:Быстрый пример|Quick example)", re.I)
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+\S")
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})\s*([\w+-]*)")
_SKIP_DIRECTIVE = re.compile(r"<!--\s*docs-smoke:\s*skip\b(.*?)-->", re.I)
SHELL_LANGS = frozenset({"", "powershell", "ps1", "pwsh", "ps", "cmd", "bat", "batch", "bash", "sh", "shell",
                         "console", "shell-session"})
_PROMPTS = ("PS> ", "$ ", "> ")
_SATK_NAMES = frozenset({"satk", "satk.cmd", "satk.sh"})
#: Operations that rewrite shared, non-regenerable-in-place state under ``work/`` (the index, the
#: symbol DB, the knowledge base and the paths DB other sessions query, the notes DB). ``None`` = always;
#: a tuple = only with one of these words in the arguments (``satk view bookmark save x`` writes,
#: ``... list`` reads). Content-addressed outputs (``cache/``, ``out/``) are safe to share and are not listed.
SHARED_STATE_OPS: dict[str, tuple[str, ...] | None] = {
    "index.build": None, "re.build": None, "kb.build": None, "paths.import": None,
    "note.add": None, "note.rm": None, "note.import": None, "note": ("add", "rm"),
    "view.bookmark": ("save", "rm"),
}


@dataclass
class Command:
    """One command line of a quick-example block."""

    page: str
    line: int
    text: str
    argv: list[str] | None = None  # arguments after "satk"; None = not a satk command
    reason: str | None = None  # why it is not runnable / skipped
    block_skip: str | None = None  # <!-- docs-smoke: skip ... -->
    status: str = ""
    code: int | None = None
    ms: float | None = None
    detail: str = ""
    extra: dict = field(default_factory=dict)

    def row(self) -> list:
        return [self.page, self.line, self.text, self.status, self.code, self.ms, self.detail]


def tokenize(s: str) -> list[str]:
    """Split like cmd/PowerShell: whitespace, ``"..."`` and ``'...'`` group; backslashes are literal."""
    out: list[str] = []
    cur: list[str] = []
    quote: str | None = None
    started = False
    for ch in s:
        if quote:
            if ch == quote:
                quote = None
            else:
                cur.append(ch)
        elif ch in "\"'":
            quote = ch
            started = True
        elif ch.isspace():
            if cur or started:
                out.append("".join(cur))
                cur, started = [], False
        else:
            cur.append(ch)
    if quote:
        raise ValueError("unclosed quote")
    if cur or started:
        out.append("".join(cur))
    return out


def _outside_quotes(s: str) -> Iterable[tuple[int, str]]:
    quote = None
    for i, ch in enumerate(s):
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            continue
        yield i, ch


def _strip_comment(s: str) -> str:
    """Drop a trailing `` # comment`` (outside quotes)."""
    for i, ch in _outside_quotes(s):
        if ch == "#" and (i == 0 or s[i - 1].isspace()):
            return s[:i].rstrip()
    return s


def parse_line(line: str) -> tuple[list[str] | None, str | None]:
    """``(argv, reason)``: argv after ``satk`` for a satk command; reason when it cannot run.

    ``(None, None)`` = comment/empty; ``(None, "not a satk command")`` = some other command.
    """
    s = line.strip()
    for p in _PROMPTS:
        if s.startswith(p):
            s = s[len(p):].strip()
            break
    if not s or s.startswith("#") or s.lower().startswith(("rem ", "::")) or s.lower() == "rem":
        return None, None
    s = _strip_comment(s)
    if not s:
        return None, None
    try:
        toks = tokenize(s)
    except ValueError as e:
        return [], f"not runnable: {e}"
    if toks and toks[0] == "&":
        toks = toks[1:]
    if not toks:
        return None, None
    name = re.split(r"[\\/]", toks[0])[-1].lower()
    if name not in _SATK_NAMES:
        return None, "not a satk command"
    for i, ch in _outside_quotes(s):
        if ch in "|><;" or s.startswith("&&", i):
            what = "a <placeholder>" if ch in "<>" and re.search(r"<[^<>\s]+>", s) else "shell syntax"
            return toks[1:], f"not runnable: {what} ({ch!r})"
    return toks[1:], None


def extract_commands(text: str, page: str = "") -> list[Command]:
    """Commands of all quick-example sections of a Markdown page."""
    lines = text.splitlines()
    out: list[Command] = []
    level: int | None = None  # inside a quick-example section of this heading level
    fence: tuple[str, int, str] | None = None
    pending_skip: str | None = None
    buf: list[tuple[int, str]] = []
    for i, raw in enumerate(lines, start=1):
        if fence is not None:
            fm = _FENCE.match(raw)
            if fm and fm.group(1)[0] == fence[0] and len(fm.group(1)) >= fence[1] and not raw.strip()[len(fm.group(1)):].strip():
                if level is not None and fence[2] in SHELL_LANGS:
                    out.extend(_block_commands(buf, page, fence[2], pending_skip))
                fence, buf, pending_skip = None, [], None
            else:
                buf.append((i, raw))
            continue
        fm = _FENCE.match(raw)
        if fm:
            fence = (fm.group(1)[0], len(fm.group(1)), fm.group(2).lower())
            buf = []
            continue
        hm = _HEADING.match(raw)
        if hm:
            n = len(hm.group(1))
            if QUICK_HEADING.match(raw):
                level = n
            elif level is not None and n <= level:
                level = None
            pending_skip = None
            continue
        sm = _SKIP_DIRECTIVE.search(raw)
        if sm:
            pending_skip = sm.group(1).strip() or "skipped by directive"
        elif raw.strip():
            pending_skip = None if not raw.strip().startswith("<!--") else pending_skip
    return out


def _block_commands(buf: list[tuple[int, str]], page: str, lang: str, skip: str | None) -> list[Command]:
    cont = {"powershell": "`", "ps1": "`", "pwsh": "`", "ps": "`", "cmd": "^", "bat": "^", "batch": "^",
            "bash": "\\", "sh": "\\", "shell": "\\"}.get(lang)
    out: list[Command] = []
    acc, start = "", 0
    for ln, raw in buf:
        s = raw.rstrip()
        if not acc:
            start = ln
        if cont and s.endswith(" " + cont):
            acc += s[: -len(cont)].rstrip() + " "
            continue
        text = (acc + s.strip()).strip() if acc else s.strip()
        acc = ""
        argv, reason = parse_line(text)
        if argv is None and reason is None:
            continue
        out.append(Command(page, start, text, argv, reason, skip))
    return out


#: Languages of the user docs, primary first: ``docs/<lang>/*.md``.
LANGS = ("en", "ru")


def default_pages(repo: Path) -> list[Path]:
    """``<repo>/docs/en/*.md`` then ``<repo>/docs/ru/*.md``, without ``_*.md`` templates, sorted."""
    out: list[Path] = []
    for lang in LANGS:
        d = repo / "docs" / lang
        if d.is_dir():
            out += sorted(p for p in d.glob("*.md") if not p.name.startswith("_"))
    return out


def _did_you_mean(word: str, choices: Iterable[str]) -> str:
    import difflib

    near = difflib.get_close_matches(word, sorted(choices), n=2, cutoff=0.6)
    return f" (did you mean {' / '.join(repr(n) for n in near)}?)" if near else ""


def classify(cmd: Command, tree, *, strict: bool) -> tuple[str, str]:
    """``("run"|"skip"|"fail", reason)`` before execution.

    Unknown groups and commands always fail (with a "did you mean"); ``strict`` also fails non-satk
    command lines (otherwise ``skip``). Blocks under ``<!-- docs-smoke: skip ... -->`` are ``skip``.
    """
    if cmd.block_skip:
        return "skip", f"block skipped: {cmd.block_skip}"
    if cmd.reason == "not a satk command":
        if strict:
            return "fail", "not a satk command (a quick example runs only satk commands)"
        return "skip", "not a satk command (not run)"
    if cmd.reason:
        return "fail", cmd.reason
    from satk.core.cli import GLOBAL_FLAGS

    argv = [a for a in (cmd.argv or []) if a not in GLOBAL_FLAGS]
    node, used, remaining = tree.walk(argv)
    if node.spec is not None or not remaining or remaining[0].startswith("-"):
        return "run", ""
    if not used:
        return "fail", (f"unknown command group '{remaining[0]}'" + _did_you_mean(remaining[0], tree.children)
                        + "; package not merged yet? mark the block <!-- docs-smoke: skip ... -->")
    return "fail", (f"unknown command: satk {' '.join(used + remaining[:1])}"
                    + _did_you_mean(remaining[0], node.children))


def shared_state_writer(cmd: Command, tree) -> str | None:
    """Name of the operation when ``cmd`` rewrites shared state under ``work/`` (:data:`SHARED_STATE_OPS`)."""
    from satk.core.cli import GLOBAL_FLAGS

    argv = [a for a in (cmd.argv or []) if a not in GLOBAL_FLAGS]
    node, _used, remaining = tree.walk(argv)
    if node.spec is None or node.spec.name not in SHARED_STATE_OPS:
        return None
    words = SHARED_STATE_OPS[node.spec.name]
    if words is None or any(a in words for a in remaining):
        return node.spec.name
    return None


def run_command(argv: list[str], *, src: Path, cwd: Path, timeout: float,
                env_extra: dict[str, str] | None = None) -> tuple[int, dict | None, str, str, float]:
    """Run ``python -X utf8 -m satk <argv>`` like the shim; (exit, envelope or None, out, err, ms).

    ``env_extra`` is added to the environment (``SATK_PATHS_WORK`` of a page with its own work dir).
    """
    env = dict(os.environ)
    env.update(env_extra or {})
    env["PYTHONPATH"] = str(src)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    t0 = time.perf_counter()
    try:
        p = subprocess.run([sys.executable, "-X", "utf8", "-m", "satk", *argv], cwd=str(cwd), env=env,
                           capture_output=True, timeout=timeout, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired as e:
        ms = (time.perf_counter() - t0) * 1000
        return -1, None, (e.stdout or "") if isinstance(e.stdout, str) else "", f"timeout after {timeout:g} s", ms
    ms = (time.perf_counter() - t0) * 1000
    envelope = None
    out = p.stdout or ""
    try:
        envelope = json.loads(out) if out.strip().startswith("{") else None
    except json.JSONDecodeError:
        envelope = None
    return p.returncode, envelope, out, p.stderr or "", ms
