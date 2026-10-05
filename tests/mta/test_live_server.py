"""The real MTA server of the fork with satk-agent (loopback, private work dir). Needs SATK_TEST_LIVE=1.

Starts ``MTA Server64.exe`` from a copy under the test's workspace, talks to the resource over
HTTP, checks it listens on 127.0.0.1 only, compiles client.lua with the server's Lua and quits.
No game client is started (see docs/ru/mta-agent.md for the client).
"""

from __future__ import annotations

import os

import pytest

from satk.core.errors import SatkError

try:
    from satk.core import config as _config

    REAL_ENGINE = _config.build().paths.get("engine")  # before satk_home points it at a temp workspace
except Exception:  # noqa: BLE001
    REAL_ENGINE = None

pytestmark = [pytest.mark.engine, pytest.mark.slow,
              pytest.mark.skipif(os.environ.get("SATK_TEST_LIVE") != "1", reason="live MTA server: SATK_TEST_LIVE=1")]


@pytest.fixture
def live(satk_home, monkeypatch):
    if not REAL_ENGINE or not (REAL_ENGINE / "Bin" / "server" / "MTA Server64.exe").is_file():
        pytest.skip("the fork's server is not built")
    monkeypatch.setenv("SATK_PATHS_ENGINE", str(REAL_ENGINE))
    _config.reset()
    from satk.viewer.backends import mta_lua as M

    up = M.start_server(timeout=90)
    try:
        yield M, up
    finally:
        st = M.stop()
        assert st["stopped"] is True or st.get("note") == "not running", st


def test_live_server_rpc(live):
    from satk.engine.smoke import listening_sockets
    from satk.saap import schema as S
    from satk.viewer.backends import get_backend

    M, up = live
    assert up["up"] and up["ready_seconds"] < 60
    socks = listening_sockets(up["pid"])
    assert socks and all("127.0.0.1:" in s for s in socks), socks
    b = get_backend("game")
    h = b.call("hello", {})
    assert h["impl"] == "mta-lua" and h["mta"]["client"] is None and S.validate_result("hello", h) == []
    assert b.call("lua.exec", {"code": "return 1 + 2", "side": "server"})["values"] == [3]
    code = ("local f = fileOpen('client.lua', true) local s = fileRead(f, fileGetSize(f)) fileClose(f) "
            "local fn, err = loadstring(s) return fn ~= nil, err")
    assert b.call("lua.exec", {"code": code, "side": "server"})["values"] == [True, None]
    assert b.call("env.set", {"time": "21:30", "weather": 8}) == {"time": "21:30", "weather": 8, "freeze": False}
    st = b.call("status", {})
    assert st["frame"] == 0 and st["env"]["time"] == "21:30"
    logs = b.call("log.poll", {"since": 0})
    assert any("satk-agent 1.0.0 started" in it["msg"] for it in logs["items"])
    with pytest.raises(SatkError) as e:
        b.call("camera.get", {})
    assert e.value.code == "NOT_READY"
