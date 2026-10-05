"""Network policy: satk opens no internet connections by default (M3 A2).

A few modules need sockets: loopback endpoints (viewer, game, SAAP; 127.0.0.1 only) and commands
the user runs on purpose to download pinned files. Each one is listed in :data:`ALLOWED` with the
network modules it may import and why. Every other source file of the product must not import a
network module at all.

* ``tests/core/test_network_allowlist.py`` scans the sources (:func:`scan`) and fails on any new
  network import outside :data:`ALLOWED` and on stale entries.
* ``satk doctor`` shows the policy (check ``network``); ``satk doctor --deep`` also runs the scan.

How to add a module that really needs the network: add ``"<path>": (modules, reason)`` to
:data:`ALLOWED` in the same change, with a reason a user can read in ``satk doctor --deep``.
stdlib only; nothing here opens a connection.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

__all__ = ["NETWORK_MODULES", "ALLOWED", "SCAN_DIRS", "POLICY", "Finding", "network_module", "imports_of",
           "scan", "violations", "stale_entries"]

#: Modules that can open connections or fetch URLs (stdlib and common third-party clients).
#: ``urllib.parse`` and ``http`` (``HTTPStatus``) are pure helpers and allowed everywhere.
NETWORK_MODULES: frozenset[str] = frozenset({
    "socket", "ssl", "socketserver", "http.client", "http.server", "http.cookiejar",
    "urllib", "urllib.request", "urllib.error", "urllib.response", "urllib.robotparser",
    "ftplib", "poplib", "imaplib", "nntplib", "smtplib", "telnetlib", "xmlrpc", "xmlrpc.client",
    "xmlrpc.server", "webbrowser",
    "requests", "httpx", "aiohttp", "urllib3", "websocket", "websockets",
})

#: Source file (POSIX path relative to the checkout) -> (network modules it may import, reason).
ALLOWED: dict[str, tuple[frozenset[str], str]] = {
    "src/satk/saap/frame.py": (frozenset({"socket"}), "SAAP/1 framing over loopback sockets"),
    "src/satk/saap/client.py": (frozenset({"socket"}), "SAAP/1 client to local endpoints (127.0.0.1)"),
    "src/satk/saap/server.py": (frozenset({"socket"}), "SAAP/1 server of the mock endpoint, bound to 127.0.0.1"),
    "src/satk/saap/conformance.py": (frozenset({"socket"}), "SAAP/1 conformance cases against a local endpoint"),
    "src/satk/viewer/launcher.py": (frozenset({"socket"}), "finds a free loopback port for the viewer"),
    "src/satk/viewer/backends/ariane_legacy.py": (frozenset({"socket"}), "loopback link to the Ariane viewer"),
    "src/satk/viewer/backends/mta_lua.py": (frozenset({"socket", "http.client"}),
                                            "loopback HTTP to the local MTA server (target=game)"),
    "src/satk/engine/smoke.py": (frozenset({"socket"}), "probes the local MTA server ports on 127.0.0.1"),
    "src/satk/engine/setup.py": (frozenset({"urllib.request"}),
                                 "satk engine setup downloads pinned MTA build dependencies (explicit command)"),
    "src/satk/release/fetch.py": (frozenset({"urllib.request"}),
                                  "satk dev release downloads pinned files from python.org/PyPI (explicit command)"),
    "src/satk/notes/db.py": (frozenset({"urllib.request"}),
                             "pathname2url only (file: URI for SQLite); no connection"),
}

#: Product source roots scanned (relative to the checkout); tests may use sockets for fakes.
SCAN_DIRS: tuple[str, ...] = ("src", "vendor", "blender", "scripts", "packaging")

#: One line for ``satk doctor`` and the bug report.
POLICY = ("network: none by default (no internet access, no telemetry); loopback 127.0.0.1 only for the "
          "viewer, the game and SAAP; downloads only on explicit commands "
          "(satk engine setup, satk dev release, bootstrap -Deps)")


@dataclass(frozen=True)
class Finding:
    """One network import: ``path`` (relative, POSIX), ``line``, ``module`` (dotted)."""

    path: str
    line: int
    module: str


def network_module(name: str) -> str | None:
    """The :data:`NETWORK_MODULES` entry ``name`` falls under (``"urllib.request"``), or ``None``."""
    parts = name.split(".")
    for i in range(len(parts), 0, -1):
        cand = ".".join(parts[:i])
        if cand in NETWORK_MODULES:
            # "urllib" itself only counts when nothing more specific was imported ("urllib.parse" is fine)
            if cand in ("urllib", "xmlrpc") and i < len(parts):
                return None if parts[1] == "parse" else ".".join(parts[:2])
            return cand
    return None


def _literal_import(node: ast.Call) -> str | None:
    """``importlib.import_module("x")`` / ``__import__("x")`` with a constant name."""
    f = node.func
    name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else None)
    if name not in ("import_module", "__import__") or not node.args:
        return None
    a = node.args[0]
    return a.value if isinstance(a, ast.Constant) and isinstance(a.value, str) else None


def imports_of(source: str, filename: str = "<src>") -> Iterator[tuple[int, str]]:
    """``(line, module)`` of every absolute import in ``source`` (also inside functions).

    ``from a import b`` yields ``a.b`` (``b`` may be a submodule or a name; ``*`` yields ``a``), plain
    ``import a.b`` yields ``a.b``; literal ``importlib.import_module("a")`` and ``__import__("a")`` count too.
    """
    tree = ast.parse(source, filename=filename)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield node.lineno, a.name
        elif isinstance(node, ast.ImportFrom):
            if node.level or not node.module:
                continue
            for a in node.names:  # "from urllib import parse" is urllib.parse, not urllib
                yield node.lineno, node.module if a.name == "*" else f"{node.module}.{a.name}"
        elif isinstance(node, ast.Call):
            lit = _literal_import(node)
            if lit:
                yield node.lineno, lit


def _files(root: Path, dirs: Iterable[str]) -> Iterator[Path]:
    for d in dirs:
        base = root / d
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(n for n in dirnames if n not in ("__pycache__", ".venv", "node_modules"))
            for fn in sorted(filenames):
                if fn.endswith(".py"):
                    yield Path(dirpath) / fn


def scan(root: str | os.PathLike, dirs: Iterable[str] = SCAN_DIRS) -> list[Finding]:
    """Every network import under ``root``/``dirs`` (sorted, one finding per module and line)."""
    root = Path(root)
    out: set[Finding] = set()
    for f in _files(root, dirs):
        rel = f.relative_to(root).as_posix()
        try:
            src = f.read_text(encoding="utf-8")
            found = list(imports_of(src, rel))
        except (OSError, SyntaxError, ValueError):
            continue  # not ours to judge here (a broken file fails its own tests)
        for line, mod in found:
            net = network_module(mod)
            if net is not None:
                out.add(Finding(rel, line, net))
    return sorted(out, key=lambda x: (x.path, x.line, x.module))


def violations(findings: Iterable[Finding], allowed: dict[str, tuple[frozenset[str], str]] = ALLOWED) -> list[Finding]:
    """Findings not covered by ``allowed``."""
    return [f for f in findings if f.module not in allowed.get(f.path, (frozenset(), ""))[0]]


def stale_entries(findings: Iterable[Finding], root: str | os.PathLike,
                  allowed: dict[str, tuple[frozenset[str], str]] = ALLOWED) -> list[str]:
    """``"path: module"`` entries of ``allowed`` that no import uses any more (keeps the list tight).

    Files that do not exist under ``root`` are not reported (a package may be missing from a build).
    """
    used = {(f.path, f.module) for f in findings}
    out = []
    for path, (mods, _) in sorted(allowed.items()):
        if not (Path(root) / path).is_file():
            continue
        out += [f"{path}: {m}" for m in sorted(mods) if (path, m) not in used]
    return out
