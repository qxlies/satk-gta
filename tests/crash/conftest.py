"""Fixtures for satk.crash tests (M2-07): a synthetic symbol DB from WP-09's synthetic world.

The symbol DB is built from ``tests/re/re_synth.py`` (invented gta_sa.exe and source trees), so frames
symbolize to ``CFoo::Bar`` etc.; nothing here comes from the game or a real dump. Dump builders live in
``crash_helpers.py``.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
for _p in (_HERE, _HERE.parent / "re"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import re_synth  # noqa: E402  (WP-09 test helper: synthetic gta_sa.exe + sources)


@dataclass
class Synth:
    root: Path
    exe: Path
    symdb: Path


@pytest.fixture
def synth(tmp_path: Path, satk_home: Path):
    """Synthetic world + symbol DB at ``<satk_home>/work/re/symdb.sqlite``."""
    from satk.re import db as dbm
    from satk.re.build import Sources, build_db
    from satk.re.gitsrc import DirTree

    w = re_synth.make_world(tmp_path / "w")
    src = Sources(exe=w.exe, gtarev=DirTree(w.gtarev), pluginsdk=DirTree(w.psdk), upstream=DirTree(w.mta),
                  neon=DirTree(w.mta), trunk=DirTree(w.trunk), limits=DirTree(w.trunk),
                  repo_names={"gta-reversed": w.gtarev.as_posix(), "plugin-sdk": w.psdk.as_posix(),
                              "mta-upstream": w.mta.as_posix(), "neon": w.mta.as_posix(),
                              "mta-trunk": w.trunk.as_posix(), "exe": w.exe.as_posix()})
    dbm.reset_cache()
    out = satk_home / "work" / "re" / "symdb.sqlite"
    build_db(out, src)
    yield Synth(tmp_path, w.exe, out)
    dbm.reset_cache()
