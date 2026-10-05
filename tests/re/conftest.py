"""Fixtures for satk.re tests (WP-09); builders live in ``re_synth.py`` (importable by tests)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from re_synth import World, make_world  # noqa: E402


def make_sources(world: World):
    """Build inputs for the synthetic world (all trees are plain directories)."""
    from satk.re.build import Sources
    from satk.re.gitsrc import DirTree

    return Sources(exe=world.exe, gtarev=DirTree(world.gtarev), pluginsdk=DirTree(world.psdk),
                   upstream=DirTree(world.mta), neon=DirTree(world.mta), trunk=DirTree(world.trunk),
                   limits=DirTree(world.trunk),
                   repo_names={"gta-reversed": world.gtarev.as_posix(), "plugin-sdk": world.psdk.as_posix(),
                               "mta-upstream": world.mta.as_posix(), "neon": world.mta.as_posix(),
                               "mta-trunk": world.trunk.as_posix(), "exe": world.exe.as_posix()})


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
