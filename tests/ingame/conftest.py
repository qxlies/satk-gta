"""Fixtures of the satk.ingame tests.

* ``lua_dll`` -- the fork's ``Bin/server/x64/lua5.1.dll`` found through the real configuration at
  collection time; tests that need it are marked ``engine`` and skip without the build;
* ``make_sim`` -- :class:`tdsim.TdSim` factory (closed after the test);
* ``vanilla`` -- a :class:`satk.ingame.modset.Vanilla` stand-in with a few vanilla models, so the mod
  scanner runs without an index.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from lua51 import dll_path  # noqa: E402

from satk.ingame.modset import Vanilla as _REAL_VANILLA  # noqa: E402 - tests monkeypatch modset.Vanilla

try:
    LUA_DLL = dll_path()  # before satk_home points paths.engine at a temp workspace
except Exception:  # noqa: BLE001 - a broken config must not break collection
    LUA_DLL = None


@pytest.fixture(scope="session")
def lua_dll() -> Path:
    if LUA_DLL is None:
        reason = "the fork's Bin/server/x64/lua5.1.dll is not built (satk engine build --project server)"
        if os.environ.get("SATK_TEST_NO_SKIP") == "1":
            pytest.fail(reason, pytrace=False)
        pytest.skip(reason)
    return LUA_DLL


@pytest.fixture
def make_sim(lua_dll):
    from tdsim import TdSim

    sims = []

    def make(manifest: dict, knobs: str = ""):
        s = TdSim(lua_dll, manifest, knobs=knobs)
        sims.append(s)
        return s

    yield make
    for s in sims:
        s.close()


class FakeVanilla:
    """Vanilla lookups without an index (the models the tests use)."""

    MODELS = {
        426: ("premier", "cars", "premier"), 411: ("infernus", "cars", "infernus"), 7: ("male01", "peds", "male01"),
        1337: ("BinNt07_LA", "objs", "binnt07_la"), 348: ("desert_eagle", "weap", "desert_eagle"),
        1634: ("landjump2", "objs", "landjump"), 1633: ("landjump", "objs", "landjump"),
    }
    VEHICLES = {426: {"handling": "PREMIER", "mass": 1600.0, "max_vel": 200.0, "accel": 22.0, "drive": "R"},
                411: {"handling": "INFERNUS", "mass": 1400.0, "max_vel": 240.0, "accel": 30.0, "drive": "4"}}

    profile = "vanilla"

    def _row(self, mid: int) -> dict:
        name, sec, txd = self.MODELS[mid]
        return {"id": mid, "name": name, "sec": sec, "txd": txd}

    def by_name(self, name: str):
        for mid, (n, _s, _t) in self.MODELS.items():
            if n.lower() == name.lower():
                return self._row(mid)
        return None

    def by_id(self, mid: int):
        return self._row(mid) if mid in self.MODELS else None

    def by_txd(self, txd: str):
        return [self._row(m) for m, (_n, _s, t) in sorted(self.MODELS.items()) if t == txd.lower()]

    def vehicle(self, mid: int):
        return self.VEHICLES.get(mid)

    def resolve(self, sid: str):
        return _REAL_VANILLA.resolve(self, sid)  # same parsing, our lookups

    def weapon_id(self, mid: int):
        return {348: 24}.get(mid)


@pytest.fixture
def vanilla() -> FakeVanilla:
    return FakeVanilla()
