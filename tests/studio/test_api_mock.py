"""satk.studio.api / launcher / ops against an in-process mock session (SAAP over TCP, no Blender)."""

from __future__ import annotations

import os
import secrets
import statistics
import time

import pytest

from satk.core.errors import SatkError
from satk.saap import client as C
from satk.saap.server import SaapServer
from satk.studio import api
from satk.studio import launcher as L
from satk.studio.mock import MockStudioWorld


@pytest.fixture
def mock_session(satk_home):
    """A running mock session named ``mock1`` with real discovery files."""
    tok = secrets.token_hex(32)
    world = MockStudioWorld(journal=satk_home / "work" / "studio" / "mock1" / "journal.jsonl")
    srv = SaapServer(world, token=tok, role="blender").start()
    d = srv.descriptor()
    d["name"] = "mock1"
    C.write_endpoint("blender-mock1", d)
    C.write_session("blender-mock1", {"role": "blender", "name": "mock1", "pid": os.getpid(), "token": tok,
                                      "pid_created": C.pid_created(os.getpid())})
    yield srv, world
    api.close_all()
    srv.stop()
    C.remove_discovery("blender-mock1")


def test_warm_calls_and_latency(mock_session):
    srv, world = mock_session
    r = api.call("mesh.primitive", {"kind": "cube", "size": 2, "name": "crate"}, session="mock1")
    assert r["session"] == "mock1" and r["changed"] == ["crate"] and r["stats"]["objects"]["crate"]["tris"] == 12
    lat = []
    for i in range(30):
        t = time.perf_counter()
        api.call("scene.info", {"limit": 1}, session="mock1")
        lat.append(time.perf_counter() - t)
    assert statistics.median(lat) < 0.05  # one cached connection, no hello per call
    assert srv.requests == 32  # hello once + 31 calls
    assert world.core.journal.entries()[-1]["method"] == "scene.info"


def test_errors_are_lifted_to_satk_errors(mock_session):
    with pytest.raises(SatkError) as ei:
        api.call("mesh.primitiv", session="mock1")
    e = ei.value
    assert e.code == "NOT_FOUND" and e.did_you_mean == ["mesh.primitive"] and e.hint and e.data["session"] == "mock1"
    with pytest.raises(SatkError) as ei:
        api.call("python", {"code": "raise KeyError('k')"}, session="mock1")
    assert ei.value.code == "BAD_PARAMS"
    with pytest.raises(SatkError) as ei:  # schema validation by the server
        api.call("scene.info", session="mock1", snapshot="3q", size=8)
    assert ei.value.code == "BAD_PARAMS"
    assert api.call("scene.info", session="mock1")["method"] == "scene.info"  # still alive


def test_save_is_guarded_on_the_client(mock_session, satk_home):
    with pytest.raises(SatkError) as ei:
        api.call("scene.info", session="mock1", save=str(satk_home / "work" / "x.txt"))
    assert ei.value.code == "BAD_PARAMS"
    with pytest.raises(SatkError) as ei:  # the mock cannot save .blend files
        api.call("scene.info", session="mock1", save=str(satk_home / "work" / "x.blend"))
    assert ei.value.code == "UNSUPPORTED"


def test_methods_status_and_list(mock_session):
    m = api.methods("primitive", session="mock1")
    assert m["methods"][0]["name"] == "mesh.primitive" and m["session"] == "mock1"
    api.call("mesh.primitive", {"kind": "plane"}, session="mock1")
    st = L.status("mock1")
    assert st["up"] is True and st["calls"] == 1 and st["objects"] == 1 and st["journal"].endswith("journal.jsonl")
    rows = L.list_sessions()
    assert [r["name"] for r in rows] == ["mock1"] and rows[0]["up"] is True


def test_named_session_not_running_is_not_ready(satk_home):
    with pytest.raises(SatkError) as ei:
        api.call("scene.info", session="nope")
    assert ei.value.code == "NOT_READY" and "session start --name nope" in ei.value.hint
    with pytest.raises(SatkError) as ei:
        api.methods(session="nope")
    assert ei.value.code == "NOT_READY"


def test_no_session_falls_back_to_a_cold_run(satk_home, monkeypatch):
    seen = {}

    def fake_cold(saap_method, p, *, blend=None, timeout=0):
        seen.update(method=saap_method, p=p, blend=blend)
        return {"method": p.get("method", "?"), "n": 1, "ms": 1.0, "mode": "cold", "job": "j"}

    monkeypatch.setattr(api, "cold", fake_cold)
    blend = satk_home / "work" / "in.blend"
    blend.write_bytes(b"BLENDER")
    r = api.call("scene.info", {"limit": 2}, blend=str(blend), save=str(satk_home / "work" / "out" / "o.blend"))
    assert r["mode"] == "cold" and seen["method"] == "author.call" and seen["blend"] == str(blend)
    assert seen["p"]["params"] == {"limit": 2} and seen["p"]["save"].endswith("o.blend") and "open" not in seen["p"]
    with pytest.raises(SatkError) as ei:
        api.call("scene.info", blend=str(satk_home / "work" / "missing.blend"))
    assert ei.value.code == "NOT_FOUND"


def test_stale_discovery_files_are_cleaned_without_touching_the_pid(satk_home):
    # a live pid that is not a Blender session: this very process, but a start time far in the past
    C.write_endpoint("blender-old", {"protocol": "saap/1", "role": "blender", "name": "old", "pid": os.getpid(),
                                     "port": 1, "pid_created": 1000.0})
    C.write_session("blender-old", {"role": "blender", "pid": os.getpid(), "token": "t" * 64, "pid_created": 1000.0})
    st = L.status("old")
    assert st["up"] is False and st["stale"] is True
    out = L.stop("old")
    assert out["stale"] is True and out["stopped"] is False and "was not touched" in out["note"]
    assert C.read_endpoint("blender-old") is None and C.read_session("blender-old") is None
    assert L.stop("old") == {"name": "old", "up": False, "stopped": False, "note": "not running"}


def test_session_names_are_checked(satk_home):
    for bad in ("", "Has Space", "../x", "a" * 33):
        with pytest.raises(SatkError):
            L.check_name(bad or "!")
    assert L.role("car-1") == "blender-car-1" and L.check_name(None) == "default"


def test_request_and_argv(satk_home):
    d = satk_home / "work" / "studio" / "x"
    req = L.request("serve", d, name="x", owner_pid=42)
    assert req["mode"] == "serve" and req["owner_pid"] == 42 and "token" not in req
    assert req["endpoint"].replace("\\", "/").endswith("run/endpoints/blender-x.json")
    one = L.request("oneshot", d, call={"method": "scene.info"})
    assert one["call"] == {"method": "scene.info"} and one["response"].endswith("response.json")
    a = L.argv(d / "request.json")
    assert a[0] == "-b" and a[a.index("--python") + 1].replace("\\", "/").endswith("satk_blender/studio/main.py")
    assert L.argv(d / "request.json", gui=True)[0] == "--factory-startup"
