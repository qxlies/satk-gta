"""Static checks of the WP-10 layout: MIT side without bpy, GPL add-on headers, manifest, licence."""

from __future__ import annotations

import ast
import os
import re
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
MIT = REPO / "src" / "satk" / "blender"
GPL = REPO / "blender" / "satk_blender"


def _imports(p: Path) -> set[str]:
    tree = ast.parse(p.read_text(encoding="utf-8"))
    out: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            out.add(n.module.split(".")[0])
    return out


@pytest.mark.parametrize("p", sorted(MIT.glob("*.py")), ids=lambda p: p.name)
def test_mit_side_has_no_bpy_or_dragonff(p):
    bad = _imports(p) & {"bpy", "mathutils", "bmesh", "dragonff", "satk_blender", "numpy", "PIL"}
    assert not bad, f"{p.name} imports {bad} at some level (MIT package must stay stdlib-only)"


@pytest.mark.parametrize("p", sorted(GPL.glob("*.py")), ids=lambda p: p.name)
def test_gpl_headers(p):
    head = p.read_text(encoding="utf-8").splitlines()[:5]
    assert head[0] == "# SPDX-License-Identifier: GPL-3.0-or-later"


def test_licence_and_manifest():
    lic = (GPL / "LICENSE").read_text(encoding="utf-8")
    assert "GNU GENERAL PUBLIC LICENSE" in lic and "Version 3, 29 June 2007" in lic
    m = tomllib.loads((GPL / "blender_manifest.toml").read_text(encoding="utf-8"))
    assert m["id"] == "satk_blender" and m["type"] == "add-on"
    assert m["license"] == ["SPDX:GPL-3.0-or-later"]
    assert len(m["tagline"]) <= 64 and not m["tagline"].endswith((".", "!", "?"))
    assert all(len(v) <= 64 for v in m.get("permissions", {}).values())


#: Directories that never hold repository sources (all gitignored); ``work`` only at the top level.
_NOT_SOURCES = {".venv", ".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "build", "dist"}
#: Class names of DragonFF's importer/exporter modules, assembled at run time so that this file
#: does not match itself.
_DRAGONFF_MARKERS = tuple("class " + n for n in ("dff_importer", "txd_importer", "dff_exporter", "col_exporter"))


def _repo_sources() -> list[Path]:
    out = []
    for root, dirs, files in os.walk(REPO):
        rel = Path(root).relative_to(REPO).parts
        dirs[:] = sorted(d for d in dirs if d not in _NOT_SOURCES and not (not rel and d == "work"))
        out += [Path(root, f) for f in files if f.endswith(".py")]
    return out


def test_no_dragonff_code_in_repo():
    """DragonFF is loaded at run time from work/blender/dragonff; no copy in the repository."""
    files = _repo_sources()
    rel = {p.relative_to(REPO).as_posix() for p in files}
    # the scan really covers this checkout (also inside a worktree under <workspace>/work/wt/...)
    assert {"blender/satk_blender/importer.py", "src/satk/blender/ops.py",
            "tests/blender/test_layout_static.py"} <= rel and len(files) > 100
    hits = [p.relative_to(REPO).as_posix() for p in files
            if any(m in p.read_text(encoding="utf-8", errors="replace") for m in _DRAGONFF_MARKERS)]
    assert hits == []


#: Paths of the development machine (``docs/ru/install.md``, rule 10 of CLAUDE.md), assembled at run time.
_MACHINE = re.compile("[A-Za-z]:" + r"[\\/]+" + "(games|files)" + r"([\\/]|$)", re.IGNORECASE)


def test_no_machine_paths_in_addon():
    """The add-on finds the satk sources and DragonFF itself: no machine-path defaults."""
    files = [p for p in (REPO / "blender").rglob("*")
             if p.is_file() and p.name != "LICENSE"
             and "__pycache__" not in p.parts and p.suffix != ".pyc"]
    assert any(p.name == "__init__.py" for p in files)
    hits = [f"{p.relative_to(REPO).as_posix()}:{i}" for p in files
            for i, ln in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1)
            if _MACHINE.search(ln)]
    assert hits == []
    assert _MACHINE.search("x = r'" + "D:" + "\\Games\\GTA'") and _MACHINE.search("D:" + "/files/x")


def test_dragonff_markers_detect_a_copy(tmp_path):
    """The marker check is not vacuous: a pasted DragonFF class is found."""
    p = tmp_path / "x.py"
    p.write_text("class " + "dff_importer:\n    pass\n", encoding="utf-8")
    assert any(m in p.read_text(encoding="utf-8") for m in _DRAGONFF_MARKERS)
