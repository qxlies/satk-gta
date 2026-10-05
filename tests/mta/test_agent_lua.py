"""satk-agent Lua: compiled and run with the fork's Lua 5.1, against a simulated MTA (no game needed).

The simulation (``sim.lua``) is not MTA: it checks our protocol and logic end to end — Python
backend -> HTTP -> server.lua -> events -> client.lua coroutines -> latent PNG -> file — with a
pinhole camera whose FOV mapping and aspect differ from the requested ones. What only the real
game can show (streaming, real FOV, D3D capture) is covered by the live run in docs/ru/mta-agent.md.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from satk.core.errors import SatkError
from satk.saap import png as P
from satk.saap import schema as S
from satk.viewer.backends import get_backend
from satk.viewer.backends.mta_lua import CAPS, MtaLuaBackend, resource_source

pytestmark = pytest.mark.engine

GROVE = {"pos": [2489.3, -1720.0, 60.0], "look": [2489.3, -1668.5, 12.3]}


def _lua(lua_dll):
    from luart import Lua

    L = Lua(lua_dll)
    L.run("function toJSON(v) if type(v) == 'function' then return nil end "
          "if type(v) == 'string' then return '[\"' .. v .. '\"]' end return '[' .. tostring(v) .. ']' end")
    L.run((resource_source() / "shared.lua").read_text(encoding="utf-8"), "shared.lua")
    return L


@pytest.mark.parametrize("name", ["shared.lua", "server.lua", "client.lua"])
def test_scripts_compile_with_mta_lua(lua_dll, name):
    from luart import Lua

    L = Lua(lua_dll)
    try:
        assert L.compile((resource_source() / name).read_text(encoding="utf-8"), name) is None
    finally:
        L.close()


def test_shared_helpers(lua_dll):
    L = _lua(lua_dll)
    try:
        assert L.run("local h, m = satk.parseTime('21:30') return h * 100 + m") == "2130"
        with pytest.raises(RuntimeError):
            L.run("satk.parseTime('25:00')")
        assert L.run("local ok, e = pcall(satk.parseTime, '7:00') return e.code") == "BAD_PARAMS"
        # fovFor: measured tan at fov 70 is K*tan(35); asking for 70 deg gives the fov that undoes K
        f = float(L.run("return (satk.fovFor(math.tan(math.rad(35)), 1.15 * math.tan(math.rad(35)), 70))"))
        assert 1.15 * math.tan(math.radians(f) / 2) == pytest.approx(math.tan(math.radians(35)), rel=1e-9)
        # cropFor: 4:3 out of 16:9 crops the sides; 21:9 crops top and bottom
        u, v, us, vs, c = json.loads(L.run("return '[' .. table.concat({satk.cropFor(1280, 720, 320, 240)}, ',') .. ']'"))
        assert (u, v, us, vs) == pytest.approx((160, 0, 960, 720)) and c == pytest.approx(0.75)
        u, v, us, vs, c = json.loads(L.run("return '[' .. table.concat({satk.cropFor(1280, 720, 2100, 900)}, ',') .. ']'"))
        assert (u, us) == (0, 1280) and vs == pytest.approx(1280 * 900 / 2100) and c == 1
        assert L.run("return satk.jsonValue(nil) .. satk.jsonValue(0/0) .. satk.jsonValue(3)") == "nullnull3"
        assert L.run("return satk.jsonValue(print)").startswith("\"function")
        r = L.run("local r = satk.execLua('print(1, 2) return 5, nil') return table.concat(r.values_json, '|') "
                  ".. '#' .. r.prints[1]")
        assert r == "5|null#1\t2"
        assert L.run("local r = satk.execLua('\\n error(\"x\")') return r.error.line .. r.error.msg") == "2x"
        assert L.run("local r = satk.execLua('return (') return r.error.msg").startswith("unexpected symbol")
    finally:
        L.close()


def test_conformance_against_the_simulation(sim):
    from satk.viewer import api

    rep = api.conformance("game", only=r"^(core|camera|capture|pick|raycast|env|world|log|console|lua)\b", show="all")
    assert rep["impl"] == "mta-lua" and rep["driver"] == "backend"
    failed = [r for r in rep["rows"] if r[2] == "fail"]
    assert failed == [] and rep["fail"] == 0, failed
    by = {r[0]: r[2] for r in rep["rows"]}
    for case in ("core.hello", "core.status", "camera.set.look", "camera.revision", "camera.set.ypr",
                 "capture.color", "capture.size", "capture.stateless", "capture.unsupported_layer", "pick.many",
                 "pick.capture_space", "raycast.down", "env.set_get", "world.settle", "log.poll", "lua.exec"):
        assert by.get(case) == "pass", (case, by.get(case))
    assert by["capture.ids"] == "skip" and by["capture.depth"] == "skip"


def test_view_capture_and_pick_through_the_backend(sim, satk_home):
    from satk.viewer import api

    out = api.capture("game", pose={**GROVE, "fov_h_deg": 60}, w=640, h=360)
    assert P.size(out["file"]) == (640, 360) and out["w"] == 640
    assert out["pose"]["fov_h_deg"] == pytest.approx(60, abs=0.05)
    side = json.loads(Path(out["sidecar"]).read_text(encoding="utf-8"))
    assert side["impl"] == "mta-lua" and side["proto"] == "mta-lua/1" and side["sha256"]
    res = api.pick([[640, 360]], target="game")
    row = dict(zip(res["cols"], res["rows"][0]))
    assert row["model"] == "model:17700" and row["name"] == "simbuilding" and row["dist"] > 0
    # the hud and chat are back, the camera is where it was
    assert sim.eval("Sim.hud") is True
    b = get_backend("game")
    assert isinstance(b, MtaLuaBackend)
    assert b.call("camera.get", {})["pose"]["pos"] != GROVE["pos"]


def test_fov_is_measured_not_assumed(make_sim):
    h = make_sim(W=1024, H=768, K=1.31)
    b = MtaLuaBackend("game", "game", h.port, h.token, server_root=h.root)
    r = b.call("camera.set", {"pose": GROVE, "fov_h_deg": 50})
    assert r["pose"]["fov_h_deg"] == pytest.approx(50, abs=0.05)
    fov_mta = float(h.run("return Sim.camApplied.fov"))
    assert 1.31 * math.tan(math.radians(fov_mta) / 2) == pytest.approx(math.tan(math.radians(25)), rel=1e-3)
    c = b.call("capture", {"path_prefix": str(h.root.parent / "c"), "w": 400, "h": 100, "pose": {**GROVE, "fov_h_deg": 40}})
    assert (c["w"], c["h"]) == (400, 100) and c["pose"]["fov_h_deg"] == pytest.approx(40, abs=0.05)
    sec = h.eval("Sim.lastSection")  # 4:1 out of 4:3: full width, a band of 256 px
    assert sec[6] == pytest.approx(1024) and sec[7] == pytest.approx(256)
    assert S.validate_result("capture", c) == []


def test_no_client(sim_noclient):
    b = get_backend("game")
    h = b.call("hello", {})
    assert h["caps"] == CAPS and h["viewport"] == {"w": 1, "h": 1} and h["mta"]["client"] is None
    assert any("no game client" in w for w in b.warnings)
    st = b.call("status", {})
    assert st["frame"] == 0 and st["client"] is False and S.validate_result("status", st) == []
    with pytest.raises(SatkError) as e:
        b.call("camera.get", {})
    assert e.value.code == "NOT_READY" and "mta_lua client" in (e.value.hint or "")
    assert b.call("lua.exec", {"code": "return 40 + 2"}) == {"values": [42], "prints": [], "side": "server"}
    assert b.call("env.set", {"time": "06:15", "freeze": True})["freeze"] is True


def test_errors_and_revision(sim):
    b = MtaLuaBackend("game", "game", sim.port, "0" * 64, server_root=sim.root)
    with pytest.raises(SatkError) as e:
        b.call("ping", {})
    assert e.value.code == "AUTH"
    b = get_backend("game")
    rev = b.call("camera.get", {})["rev"]
    with pytest.raises(SatkError) as e:
        b.call("camera.set", {"pose": GROVE, "expect_rev": rev + 5})
    assert e.value.code == "REVISION" and e.value.data == {"rev": rev}
    with pytest.raises(SatkError) as e:
        b.call("capture", {"path_prefix": "relative/x"})
    assert e.value.code == "BAD_PARAMS"
    with pytest.raises(SatkError) as e:
        b.call("pick", {"points": [[1, 1]], "mode": "visible"})
    assert e.value.code == "UNSUPPORTED"
    r = b.call("lua.exec", {"code": "error('boom')", "side": "client"})
    assert r["error"]["msg"] == "boom" and r["side"] == "client"
    sim.run("Sim.screenUpload = false")
    with pytest.raises(SatkError) as e:
        b.call("capture", {"path_prefix": str(sim.root.parent / "blocked")})
    assert e.value.code == "NOT_READY" and "screen upload" in e.value.msg


def test_quit_requests_shutdown(sim):
    b = get_backend("game")
    assert b.call("quit", {}) == {}
    sim.pump(20)
    assert sim.eval("Sim.shutdownRequested") == "satk-agent: quit requested over rpc"


def test_client_logs_reach_the_server_ring(sim):
    b = get_backend("game")
    b.call("lua.exec", {"code": "outputDebugString('x')", "side": "client"})
    sim.run("Sim.fire(Sim.client, 'onClientDebugMessage', Sim.root, nil, 'client says hi', 2, 'client.lua', 7)")
    sim.pump(40)
    items = b.call("log.poll", {"streams": ["script"], "min_level": "warn"})["items"]
    assert items and items[-1]["msg"] == "client says hi" and items[-1]["level"] == "warn"
    assert items[-1]["file"] == "client.lua" and items[-1]["line"] == 7 and items[-1]["resource"] == "satk"
