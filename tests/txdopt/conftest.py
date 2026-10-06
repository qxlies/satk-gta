"""Fixtures for satk.txdopt tests; builders live in ``txdopt_synth.py`` (no game files)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parent))   # appended: other packages import their own "conftest"

import txdopt_synth as S  # noqa: E402


@pytest.fixture
def ws(satk_home: Path, run_cli):
    """Isolated workspace whose vanilla game is the synthetic one, with its index built."""
    from satk.index import api

    S.make_game(satk_home / "gta-sa-clean")
    api.clear_cache()
    r = run_cli(["index", "build", "--jobs", "1", "--json"])
    assert r.code == 0, r.out
    yield satk_home
    api.clear_cache()


@pytest.fixture
def mod(tmp_path: Path) -> Path:
    """The bloated map mod (outside the workspace, so nothing may be written there)."""
    return S.make_mod(tmp_path / "mods" / "bigmap")


@pytest.fixture
def synth():
    return S
