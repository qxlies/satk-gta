"""The real MTA server of the fork runs satk-testdrive headless (loopback, private work dir). SATK_TEST_LIVE=1.

``satk ingame start`` with a synthetic mod starts ``MTA Server64.exe`` with satk-agent, satk-testdrive and the
generated content resource; the test talks to them through the satk-agent bridge (td status/manifest/spawn),
hot-reloads a changed file and reads the logs. No game client is started.
"""

from __future__ import annotations

import os
import socket

import pytest

from satk.core.registry import invoke

try:
    from satk.core import config as _config

    REAL_ENGINE = _config.build().paths.get("engine")  # before satk_home points it at a temp workspace
except Exception:  # noqa: BLE001
    REAL_ENGINE = None

pytestmark = [pytest.mark.engine, pytest.mark.slow,
              pytest.mark.skipif(os.environ.get("SATK_TEST_LIVE") != "1", reason="live MTA server: SATK_TEST_LIVE=1")]

HANDLING = ("PREMIER 1600.0 3921.3 1.8 0.0 -0.2 -0.1 75 0.75 0.85 0.52 5 220.0 24.0 10.0 R P 8.0 0.5 0 35.0 1.8 0.1 "
            "0.0 0.31 -0.15 0.5 0.0 0.26 0.5 18000 40000000 10200002 1 1 0\n")


def _free(kind: int) -> int:
    s = socket.socket(socket.AF_INET, kind)
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


@pytest.fixture
def live(satk_home, monkeypatch):
    if not REAL_ENGINE or not (REAL_ENGINE / "Bin" / "server" / "MTA Server64.exe").is_file():
        pytest.skip("the fork's server is not built")
    monkeypatch.setenv("SATK_PATHS_ENGINE", str(REAL_ENGINE))
    _config.reset()
    from satk.viewer.backends import mta_lua

    try:
        yield
    finally:
        st = mta_lua.stop()
        assert st["stopped"] is True or st.get("note") == "not running", st


def test_live_testdrive_server(live, satk_home, tmp_path):
    from satk.engine.smoke import listening_sockets
    from satk.ingame import session as S

    mod = tmp_path / "mod"
    mod.mkdir()
    (mod / "premier.dff").write_bytes(b"synthetic dff")
    (mod / "premier.txd").write_bytes(b"synthetic txd 1")
    (mod / "handling.cfg").write_text(HANDLING, encoding="utf-8")
    r = invoke("ingame.start", {"mod": [str(mod)], "kind": "vehicle", "replace": "426",
                                "port": _free(socket.SOCK_DGRAM), "httpport": _free(socket.SOCK_STREAM)})
    assert r["server"]["resources"] == {"satk-testdrive-mod": "running", "satk-testdrive": "running"}
    assert r["models"][0]["mode"] == "replace" and r["models"][0]["handling"] == "from the mod"
    socks = listening_sockets(r["server"]["pid"])
    assert socks and all("127.0.0.1:" in s for s in socks), socks
    b = S.Bridge()
    st = b.server("status")
    assert st["content"] == "running" and st["rev"] == r["rev"] and st["players"] == []
    m = b.server("manifest")
    model = m["models"][0]
    assert model["files"] == {"dff": "m/premier.dff", "txd": "m/premier.txd"}
    assert model["handling"]["engineAcceleration"] == pytest.approx(9.6) and "runway" in m["spots"]
    sp = b.server("spawn", {"model": "mod", "kind": "vehicle", "spot": "wall"})
    assert sp["element"] == "satk.td.1" and sp["model"] == 426
    walls = b.exec("local n = 0 for _, o in ipairs(getElementsByType('object')) do "
                   "if getElementModel(o) == 8650 then n = n + 1 end end return n", "server")
    assert walls == [1]
    assert b.server("clear")["removed"] == 1
    (mod / "premier.txd").write_bytes(b"synthetic txd 2")
    rl = invoke("ingame.reload", {"timeout": 5})
    assert rl["changed"] == ["m/premier.txd", "manifest.lua"] and rl["resources"]["satk-testdrive-mod"] == "restarted"
    assert b.server("status")["rev"] == rl["rev"] != r["rev"]
    # debug output of the startup resources goes to the server log, later output to the script log too
    logs = invoke("ingame.logs", {"source": "all", "level": "debug", "since": "start", "limit": 500})
    rows = [row for row in logs["rows"] if row[1] in ("server", "scripts", "agent:server")]
    msgs = [row[3] for row in rows]
    assert any(x.startswith("satk-testdrive: started") for x in msgs), msgs
    assert any(f"content {rl['rev']} loaded" in x for x in msgs), msgs
    assert not [row for row in rows if row[2] == "error"], rows
    assert not [x for x in msgs if "deprecated" in x and "satk-testdrive" in x], msgs
