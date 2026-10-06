"""Fixtures for satk.re tests (WP-09); builders live in ``re_synth.py`` (importable by tests)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

# make_sources lives in re_synth: tests import it from there, never from ``conftest`` (a bare ``conftest`` is
# whichever package's conftest pytest imported last, so such an import depends on the order of the test folders)
from re_synth import World, make_sources, make_world  # noqa: E402,F401


@pytest.fixture
def world(tmp_path: Path) -> World:
    return make_world(tmp_path / "w")


@pytest.fixture
def built(world: World, satk_home: Path):
    """A symdb built from the synthetic world at ``<satk_home>/work/re/symdb.sqlite``."""
    from satk.re import db as dbm
    from satk.re.build import build_db

    dbm.reset_cache()
    out = satk_home / "work" / "re" / "symdb.sqlite"
    stats = build_db(out, make_sources(world))
    yield out, stats
    dbm.reset_cache()
