"""satk for everyone (docs/en/install.md): no machine-specific paths in the code.

Every file under ``src/satk`` (Python, SQL, engine templates ...), the shims and the setup
scripts must not name the maintainers' layout (a ``Games`` or ``files`` folder at the root of drive
``D:``, any slash style). Hints and examples use ``<workspace>`` or the configured value at run time;
logic uses the configuration (``satk.core.config``) and discovery (``satk.core.detect``).
Data files with examples (``data/*.json``) and the docs are exempt. The samples below are spelled at run
time, so this file names no such path either.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src" / "satk"
#: ``Games`` or ``files`` at the root of drive ``D:``, with a backslash, an escaped one or a slash, any case.
LITERAL = re.compile(r"(?i)\b[d]:(?:\\|/)+(?:games|files)\b")
EXTRA = ("satk.cmd", "satk.sh", "scripts/bootstrap.ps1", "scripts/deps.py", "scripts/hooks/pre-commit",
         "pyproject.toml", "satk.toml.example")


def _files() -> list[Path]:
    out = [p for p in SRC.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"]
    out += [REPO / x for x in EXTRA if (REPO / x).is_file()]
    return sorted(out)


def test_no_machine_paths_in_code():
    hits = []
    for f in _files():
        text = f.read_text(encoding="utf-8", errors="replace")
        for n, line in enumerate(text.splitlines(), 1):
            if LITERAL.search(line):
                hits.append(f"{f.relative_to(REPO).as_posix()}:{n}: {line.strip()[:100]}")
    assert hits == [], "machine-specific paths:\n" + "\n".join(hits)


def test_no_machine_paths_in_python_strings():
    """The same through the AST, so string escapes and implicit concatenation cannot hide one."""
    hits = []
    for f in sorted(SRC.rglob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and LITERAL.search(node.value):
                hits.append(f"{f.relative_to(REPO).as_posix()}:{node.lineno}")
    assert hits == []


def test_pattern_catches_the_spellings():
    d, games, files = "D:", "Games", "files"
    for s in (rf"{d}\{games}\GTA", rf"{d}\\{games}\\GTA", f"{d}/{games}/GTA", f"{d}/{games}".lower(),
              rf"{d}\{files}\tg\tools"):
        assert LITERAL.search(s), s
    for s in ("<workspace>/work", r"C:\Program Files", "ID:/Gamesx", "D:/Gamesroom"):
        assert not LITERAL.search(s), s


def test_defaults_have_no_tool_literals():
    from satk.core import config as C

    d = C.defaults()
    for k in C.DETECTED_PATHS:
        assert k not in d["paths"], k  # discovered (satk.core.detect) unless configured
