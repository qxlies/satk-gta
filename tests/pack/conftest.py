"""Fixtures for satk.pack tests; builders live in ``pack_synth.py`` (no game files)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parent))   # appended: other packages import their own "conftest"

import pack_synth as S  # noqa: E402


@pytest.fixture
def synth():
    return S


@pytest.fixture
def mod_dir(tmp_path: Path) -> Path:
    """A small mod folder: two DFFs with identical bytes, a TXD, a COL, a nested file and a non-asset file."""
    d = tmp_path / "mymod"
    S.write(d, "car.dff", S.dff(lights=True))
    S.write(d, "car_copy.dff", S.dff(lights=True))
    S.write(d, "car.txd", S.txd([("body", 1), ("glass", 2)]))
    S.write(d, "col/car.col", b"COL3" + bytes(range(256)) * 20)
    S.write(d, "sub/extra.dff", S.dff(seed=3))
    S.write(d, "readme.txt", "not an asset")
    return d


@pytest.fixture
def ws(satk_home: Path, run_cli):
    """Isolated workspace whose vanilla game is the synthetic census game, with its index built."""
    from satk.index import api

    S.make_game(satk_home / "gta-sa-clean")
    api.clear_cache()
    r = run_cli(["index", "build", "--jobs", "1", "--json"])
    assert r.code == 0, r.out
    yield satk_home
    api.clear_cache()
