"""What goes into a bug report, as Markdown sections (``satk bug-report``).

Collected: satk and Python versions, the operating system, how satk runs, the network policy, the
``satk doctor`` results, the configuration (path sources, profiles), the game edition and executable
variant (hash and size only), operation import errors and the last entries of ``work/logs/errors.log``.
Never collected: game files or their names, images, the index, notes, tokens of running endpoints, the
environment beyond ``SATK_*`` variables. Everything passes through :class:`~satk.bugreport.redact.Redactor`.
"""

from __future__ import annotations

import datetime as _dt
import locale
import os
import platform
import sys
import traceback
from pathlib import Path
from typing import Callable

from satk import __version__
from satk.core import detect
from satk.core.config import REPO_ROOT
from satk.core.paths import cfg, jpath

from .redact import Redactor

__all__ = ["Section", "collect", "render", "redactor_for", "error_entries", "LOG_ENTRY_LINES"]

#: At most this many lines of one errors.log entry are kept (the end of the traceback).
LOG_ENTRY_LINES = 40

Section = tuple[str, list[str]]


def redactor_for(env: dict[str, str] | None = None) -> Redactor:
    """A :class:`Redactor` for this configuration (game folders, workspace, satk checkout)."""
    c = cfg()
    p = c.paths
    games = [p.get(k) for k in ("game_root", "installed", "game", "samp")]
    return Redactor(env, games=[g for g in games if g], workspace=p.workspace, satk=REPO_ROOT)


def _kv(rows: list[tuple[str, object]]) -> list[str]:
    return [f"- {k}: {v}" for k, v in rows if v not in (None, "")]


def _environment() -> list[str]:
    from satk.core import resources

    try:
        data = resources.data_source()
    except Exception:  # noqa: BLE001 - the report must not fail on one probe
        data = "?"
    return _kv([
        ("satk", __version__),
        ("python", f"{platform.python_implementation()} {platform.python_version()} ({sys.executable})"),
        ("os", platform.platform()),
        ("machine", platform.machine()),
        ("encoding", f"preferred {locale.getpreferredencoding(False)}, stdout "
                     f"{getattr(sys.stdout, 'encoding', None)}, utf8_mode {sys.flags.utf8_mode}"),
        ("satk from", f"{REPO_ROOT} (data: {data})"),
    ])


def _network() -> list[str]:
    from satk.runtime.network import POLICY

    return [f"- {POLICY}"]


def _doctor() -> list[str]:
    from satk.runtime.doctor import run

    res = run()
    out = [f"- overall: {res['status']} ({', '.join(f'{k} {v}' for k, v in res['counts'].items())})"]
    for c in res["checks"]:
        line = f"- {c['status']:4} {c['name']}: {c.get('msg', '')}"
        if c.get("fix"):
            line += f" (fix: {c['fix']})"
        out.append(line)
    return out


def _config() -> list[str]:
    c = cfg()
    p = c.paths
    rows: list[tuple[str, object]] = [("workspace", f"{jpath(p.workspace)} ({c.workspace_source})"),
                                      ("default profile", c.default_profile)]
    for name in p:
        rows.append((f"path {name}", f"{jpath(p.get(name))} [{c.path_source(name)}]"))
    rows.append(("profiles", ", ".join(f"{n}={'ok' if pr.configured else 'not configured'}"
                                       for n, pr in c.profiles.items())))
    if c.aliases:
        rows.append(("aliases", ", ".join(f"{a}->{t}" for a, t in c.aliases.items())))
    env = sorted((k, v) for k, v in os.environ.items() if k.startswith("SATK_"))
    rows += [(f"env {k}", v) for k, v in env]
    return _kv(rows)


def _game() -> list[str]:
    c = cfg()
    out: list[str] = []
    roots: dict[str, Path] = {}
    for key in ("game_root", "installed", "game"):
        v = c.paths.get(key)
        if v is not None and Path(v).is_dir() and all(os.path.normcase(str(v)) != os.path.normcase(str(r))
                                                       for r in roots.values()):
            roots[key] = Path(v)
    if not roots:
        return ["- no game folder configured or found (satk init)"]
    for key, root in roots.items():
        ed = detect.edition(root)
        problems = detect.check_game(root)
        line = f"- {key}: edition {ed or 'not a game folder'}"
        exe = next((root / n for n in detect.EXE_NAMES if (root / n).is_file()), None)
        if exe is not None:
            try:
                info = detect.identify_exe(exe)
                line += (f", {exe.name} {info['variant']} ({info['name']}, match {info['match']}, "
                         f"{info['size']} bytes, sha256 {info['sha256']})")
            except OSError as e:
                line += f", {exe.name} unreadable ({type(e).__name__})"
        if problems:
            line += f"; problems: {', '.join(problems)}"
        out.append(line)
    return out


def _ops() -> list[str]:
    from satk.core.registry import all_ops, import_errors

    errs = import_errors()
    out = [f"- operations registered: {len(all_ops())}; packages that failed to import: {len(errs)}"]
    for mod, err in sorted(errs.items()):
        tail = str(err).strip().splitlines()[-LOG_ENTRY_LINES:]
        out += [f"- {mod}:", "```text", *tail, "```"]
    return out


def error_entries(path: Path, n: int) -> list[list[str]]:
    """The last ``n`` entries (``--- <stamp> ...`` blocks) of ``errors.log``, each at most
    :data:`LOG_ENTRY_LINES` lines (head line + end of the traceback)."""
    if n <= 0 or not path.is_file():
        return []
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 512 * 1024))
            text = f.read().decode("utf-8", "replace")
    except OSError:
        return []
    entries: list[list[str]] = []
    for line in text.splitlines():
        if line.startswith("--- "):
            entries.append([line])
        elif entries:
            entries[-1].append(line)
    out = []
    for e in entries[-n:]:
        while e and not e[-1].strip():
            e.pop()
        out.append(e if len(e) <= LOG_ENTRY_LINES else [e[0], "...", *e[-(LOG_ENTRY_LINES - 2):]])
    return out


def _errors(n: int) -> list[str]:
    path = Path(cfg().paths.work) / "logs" / "errors.log"
    entries = error_entries(path, n)
    if not entries:
        return [f"- no entries in {jpath(path)}" if n > 0 else "- not included (--logs 0)"]
    out = [f"- last {len(entries)} of {jpath(path)}:"]
    for e in entries:
        out += ["```text", *e, "```"]
    return out


def _safe(fn: Callable[[], list[str]]) -> list[str]:
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 - one broken probe must not lose the whole report
        tb = traceback.format_exception(type(e), e, e.__traceback__)[-6:]
        return [f"- this section failed: {type(e).__name__}: {e}", "```text", *"".join(tb).splitlines(), "```"]


def collect(*, what: str | None = None, logs: int = 3) -> list[Section]:
    """All sections, not yet redacted (:func:`render` redacts)."""
    return [
        ("What happened", [what.strip() if what and what.strip() else
                           "(describe what you did, what you expected and what happened instead)"]),
        ("Environment", _safe(_environment)),
        ("Network", _safe(_network)),
        ("satk doctor", _safe(_doctor)),
        ("Configuration", _safe(_config)),
        ("Game", _safe(_game)),
        ("Operations", _safe(_ops)),
        ("Recent errors", _safe(lambda: _errors(logs))),
    ]


def render(sections: list[Section], redact: Redactor, *, now: _dt.datetime | None = None) -> str:
    """The Markdown report, redacted."""
    stamp = (now or _dt.datetime.now(_dt.timezone.utc)).strftime("%Y-%m-%d %H:%M UTC")
    lines = ["# satk bug report", "",
             f"Created {stamp}. Paths, user and computer names, e-mail addresses and tokens are replaced "
             "(<game>, <workspace>, <satk>, %USERPROFILE%, <user>, <host>, <email>, <redacted>). No game files "
             "are included. Read the report before you share it.", ""]
    for title, body in sections:
        lines += [f"## {title}", "", *body, ""]
    return redact("\n".join(lines).rstrip("\n") + "\n")
