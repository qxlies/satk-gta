"""Repository layout, stub packages and import hygiene (SPEC §2.2, §2.3, §5.3 WP-00)."""

from __future__ import annotations

import satk
import ast
import importlib
import json
import sys
from pathlib import Path

import pytest

from satk.core import registry as R

SRC = Path(__file__).resolve().parents[2] / "src" / "satk"
REPO = SRC.parents[1]

#: Packages WP-00 creates as stubs (owned by their WPs afterwards).
STUBS = ("game", "formats", "index", "media", "model3d", "mcp", "runtime", "notes", "saap", "viewer", "re",
         "blender", "engine")
#: Packages that must import with the standard library only (also used from Blender's Python).
STDLIB_ONLY = ("core", "formats", "index", "re", "game", "saap")
#: Never imported at module level anywhere in satk (SPEC §2.3: only inside functions).
HEAVY = {"PIL", "numpy"}
STDLIB = set(sys.stdlib_module_names)


@pytest.mark.parametrize("pkg", STUBS)
def test_stub_package_and_ops_import(pkg):
    m = importlib.import_module(f"satk.{pkg}")
    assert m.__doc__
    importlib.import_module(f"satk.{pkg}.ops")


def _module_level_imports(tree: ast.Module) -> list[tuple[int, str]]:
    """Imports executed at import time (top level, inside if/try/with/class bodies)."""
    out: list[tuple[int, str]] = []

    def visit(stmts):
        for node in stmts:
            if isinstance(node, ast.Import):
                out.extend((node.lineno, a.name.split(".")[0]) for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module and node.module != "__future__":
                    out.append((node.lineno, node.module.split(".")[0]))
            elif isinstance(node, (ast.If, ast.Try, ast.With, ast.ClassDef, ast.For, ast.While)):
                for field in ("body", "orelse", "finalbody"):
                    visit(getattr(node, field, []) or [])
                for h in getattr(node, "handlers", []) or []:
                    visit(h.body)
            elif sys.version_info >= (3, 11) and isinstance(node, ast.TryStar):
                visit(node.body)
    visit(tree.body)
    return out


def _py_files():
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)


def test_import_hygiene():
    problems = []
    for f in _py_files():
        rel = f.relative_to(SRC).as_posix()
        pkg = rel.split("/")[0] if "/" in rel else ""
        tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        for line, mod in _module_level_imports(tree):
            if mod in ("satk",) or mod in STDLIB:
                if mod in HEAVY:  # pragma: no cover - not stdlib
                    problems.append(f"{rel}:{line}: {mod}")
                continue
            if mod in HEAVY:
                problems.append(f"{rel}:{line}: '{mod}' at module level (import inside functions)")
            elif pkg in STDLIB_ONLY:
                problems.append(f"{rel}:{line}: non-stdlib '{mod}' in stdlib-only package satk.{pkg}")
            elif f.name == "ops.py":
                problems.append(f"{rel}:{line}: non-stdlib '{mod}' at module level of an ops module")
    assert problems == []


def test_all_registered_operations_are_valid():
    R.discover(force=True)
    assert R.import_errors() == {}
    ops = R.all_ops()
    names = {o.name for o in ops}
    assert {"version", "config.show", "dev.guard_test", "dev.assetguard"} <= names
    mcp_names = [o.mcp_name for o in ops if o.mcp_name]
    assert len(mcp_names) == len(set(mcp_names))
    cli_paths = [o.cli_path for o in ops]
    assert len(cli_paths) == len(set(cli_paths))
    for o in ops:
        assert 0 < len(o.summary) <= R.MAX_SUMMARY, o.name
        assert o.summary_ru.strip(), o.name
        json.dumps(o.input_schema())


def test_repo_root_files():
    for name in ("pyproject.toml", "requirements.lock", "satk.cmd", "satk.sh", "satk.toml.example", ".gitignore",
                 "LICENSE", "NOTICE.md", "README.md", ".assetguard-allow", "scripts/bootstrap.ps1",
                 "scripts/hooks/pre-commit"):
        assert (REPO / name).is_file(), name
    lic = (REPO / "LICENSE").read_text(encoding="utf-8")
    assert lic.startswith("MIT License") and "WITHOUT WARRANTY OF ANY KIND" in lic
    ign = (REPO / ".gitignore").read_text(encoding="utf-8").split()
    for pat in (".venv/", "*.sqlite", "*.img", "*.dff", "*.txd", "*.col", "*.ifp", "*.png", "satk.toml", "work/"):
        assert pat in ign, pat


def test_requirements_lock_pins():
    lock = (REPO / "requirements.lock").read_text(encoding="ascii")  # ASCII only (pip on cp1251)
    pins = dict(line.split("==", 1) for line in lock.splitlines() if "==" in line and not line.startswith("#"))
    low = {k.lower(): v for k, v in pins.items()}
    assert low["pillow"] == "12.3.0" and low["mcp"] == "2.3.0"
    assert low["numpy"].startswith("2.") and low["pytest"].startswith("8.")


def test_version_is_single_sourced():
    import tomllib

    import satk

    meta = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    assert meta["project"]["version"] == satk.__version__
    assert meta["project"]["requires-python"] == ">=3.12"
