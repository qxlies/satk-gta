"""Fixtures for satk.lint tests (M2-06); builders live in ``lint_synth.py`` (importable by tests)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lint_synth import B, Mod  # noqa: E402


@pytest.fixture
def mod(tmp_path: Path) -> Mod:
    return Mod(tmp_path)


@pytest.fixture
def b() -> type[B]:
    return B


@pytest.fixture
def config_file(tmp_path: Path):
    """``config_file({"dff.tris_budget": {"params": ...}})`` -> path of a lint override file."""

    def make(rules: dict) -> str:
        p = tmp_path / "lint-config.json"
        p.write_text(json.dumps({"rules": rules}), encoding="utf-8")
        return str(p)

    return make
