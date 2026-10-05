"""Fixtures for the satk-agent tests (M2-14).

* ``lua_dll`` — the fork's ``Bin/server/x64/lua5.1.dll`` (the Lua MTA runs), found through the
  real configuration at collection time; tests that need it are marked ``engine``;
* ``sim`` / ``sim_noclient`` — :class:`simhost.SimHost`: the real resource scripts in a simulated
  MTA behind a local HTTP server, plus discovery files for target ``game`` in the isolated
  workspace, so ``satk view ... --target game`` talks to it through :class:`MtaLuaBackend`.
"""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from luart import dll_path  # noqa: E402

try:
    LUA_DLL = dll_path()  # at collection, before satk_home points paths.engine at a temp workspace
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


def _write_discovery(port: int, token: str, root: Path) -> None:
    from satk.saap import client as C

    pid = os.getpid()
    info = C.process_info(pid) or {}
    C.write_endpoint("game", {"role": "game", "protocol": "mta-lua/1", "impl": "mta-lua", "port": port, "pid": pid,
                              "exe": info.get("exe"), "pid_created": C.pid_created(pid), "server_root": str(root)})
    C.write_session("game", {"role": "game", "token": token, "pid": pid, "profile": "vanilla", "server_root": str(root)})


def _make_sim(satk_home: Path, lua_dll: Path, *, client: bool, **kw):
    from satk.viewer.backends.mta_lua import resource_source
    from simhost import SimHost

    token = secrets.token_hex(32)
    root = satk_home / "work" / "mta" / "server"
    host = SimHost(lua_dll, resource_source(), root, token, client=client, **kw)
    host.token, host.root = token, root
    _write_discovery(host.port, token, root)
    return host


@pytest.fixture
def make_sim(satk_home: Path, lua_dll: Path):
    hosts = []

    def make(*, client: bool = True, **kw):
        h = _make_sim(satk_home, lua_dll, client=client, **kw)
        hosts.append(h)
        return h

    yield make
    for h in hosts:
        h.close()


@pytest.fixture
def sim(make_sim):
    return make_sim()


@pytest.fixture
def sim_noclient(make_sim):
    return make_sim(client=False)
