"""SAAP/1 server, client, discovery and conformance against the mock endpoint (WP-07 acceptance 2)."""

from __future__ import annotations

import json
import os
import secrets
import socket
import struct
import subprocess
import sys
import threading
import time

import pytest

from satk.core.errors import SatkError
from satk.saap import client as C
from satk.saap import conformance as CF
from satk.saap import frame as F
from satk.saap.mock import MockWorld, serve
from satk.saap.server import SaapServer


@pytest.fixture
def server(satk_home):
    tok = secrets.token_hex(32)
    srv = SaapServer(MockWorld(), token=tok).start()
    yield srv, tok
    srv.stop()


def _raw(port: int) -> socket.socket:
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    return s


def _hello(s, tok):
    F.write_json(s, {"saap": 1, "id": "h", "method": "hello", "params": {"token": tok, "client": {"name": "t"}}})
    return F.read_json(s, F.MAX_RESPONSE)


def _closed(s) -> bool:
    s.settimeout(2)
    try:
        return s.recv(1) == b""
    except OSError:
        return True


def test_auth_and_errors(server):
    srv, tok = server
    s = _raw(srv.port)
    F.write_json(s, {"saap": 1, "id": "p", "method": "ping"})
    r = F.read_json(s, F.MAX_RESPONSE)
    assert r["ok"] is False and r["error"]["code"] == "AUTH" and _closed(s)
    s = _raw(srv.port)
    r = _hello(s, "x" * 64)
    assert r["error"]["code"] == "AUTH" and _closed(s)
    s = _raw(srv.port)
    r = _hello(s, tok)
    assert r["ok"] and r["result"]["saap"] == 1 and "core" in r["result"]["caps"]
    assert r["meta"]["frame"] >= 1 and "ms" in r["meta"]
    F.write_json(s, {"saap": 1, "id": "u", "method": "nope.nope"})
    assert F.read_json(s, F.MAX_RESPONSE)["error"]["code"] == "UNKNOWN_METHOD"
    F.write_json(s, {"saap": 1, "id": "b", "method": "camera.set", "params": {}})
    r = F.read_json(s, F.MAX_RESPONSE)
    assert r["error"]["code"] == "BAD_PARAMS" and r["id"] == "b" and r["error"]["retryable"] is False
    F.write_json(s, {"saap": 1, "id": "p2", "method": "ping"})
    assert F.read_json(s, F.MAX_RESPONSE)["ok"]  # connection survived
    s.sendall(struct.pack("!I", F.MAX_REQUEST + 1))
    r = F.read_json(s, F.MAX_RESPONSE)
    assert r["error"]["code"] == "PROTOCOL" and _closed(s)


@pytest.mark.parametrize("rounds", [5])  # the old close() lost the reply ~3 times in 5
def test_oversize_frame_with_body_still_delivers_protocol(server, rounds):
    """A whole oversize frame (header + 1 MiB body): the reply must not be lost to a reset."""
    srv, tok = server
    for _ in range(rounds):
        s = _raw(srv.port)
        assert _hello(s, tok)["ok"]
        s.sendall(struct.pack("!I", F.MAX_REQUEST + 1) + b" " * (F.MAX_REQUEST + 1))
        r = F.read_json(s, F.MAX_RESPONSE)
        assert r["error"]["code"] == "PROTOCOL" and r["error"]["data"]["length"] == F.MAX_REQUEST + 1
        assert _closed(s)
        s.close()
    s = _raw(srv.port)  # the server is still fine
    assert _hello(s, tok)["ok"]
    s.close()


@pytest.mark.parametrize("payload", [
    b'{"x":' + b'1' * 5000 + b'}',
    b'{"x":' + b'[' * 10000 + b'0' + b']' * 10000 + b'}',
    b'{"saap":1,"id":"h","method":"hello","params":{"token":"' + br'\ud800' * 32 + b'"}}',
    br'{"saap":1,"id":"\ud800","method":"hello"}',
    b'{"x":NaN}', b'{"x":Infinity}', b'{"x":-Infinity}',
], ids=["integer", "depth", "surrogate-token", "surrogate-id", "nan", "infinity", "negative-infinity"])
def test_malformed_request_returns_error_envelope(server, monkeypatch, payload):
    srv, tok = server
    exceptions = []
    monkeypatch.setattr(threading, "excepthook", exceptions.append)
    with _raw(srv.port) as s:
        s.sendall(F.encode(payload, F.MAX_REQUEST))
        response = F.read_json(s, F.MAX_RESPONSE)
        assert response["ok"] is False
        codes = {"PROTOCOL", "AUTH"} if b'"token"' in payload else {"PROTOCOL"}
        assert response["error"]["code"] in codes
        assert _closed(s)
    assert not exceptions
    with _raw(srv.port) as s:
        assert _hello(s, tok)["ok"]  # one bad client does not stop the endpoint


def test_unsupported_capability(satk_home):
    tok = secrets.token_hex(32)
    with SaapServer(MockWorld(caps=["core", "camera"]), token=tok) as srv:
        c = C.SaapClient("127.0.0.1", srv.port, tok)
        c.connect()
        assert c.caps == ["core", "camera"]
        with pytest.raises(SatkError) as e:
            c.call("capture", {"path_prefix": str(satk_home / "work" / "x")})
        assert e.value.code == "UNSUPPORTED" and e.value.data["capability"] == "capture"
        assert c.call("camera.get")["rev"] == 0
        c.close()


def test_server_requires_token(satk_home):
    with pytest.raises(SatkError) as e:
        SaapServer(MockWorld(), token=None)
    assert e.value.code == "AUTH"
    with SaapServer(MockWorld(), token=None, insecure=True) as srv:
        c = C.SaapClient("127.0.0.1", srv.port, "z" * 40)
        assert c.connect()["impl"] == "satk-mock"
        c.close()


def test_client_errors_and_paths(server, satk_home):
    srv, tok = server
    c = C.SaapClient("127.0.0.1", srv.port, tok, timeout=10)
    c.connect()
    with pytest.raises(SatkError) as e:
        c.call("camera.set", {"pose": {"pos": [0, 0, 50], "look": [0, 10, 0]}, "expect_rev": 99})
    assert e.value.code == "REVISION" and e.value.data["rev"] == 0
    r = c.call("capture", {"path_prefix": str(satk_home / "work" / "out" / "t1"), "w": 64, "h": 48})
    assert r["w"] == 64 and os.path.isfile(r["files"]["color"])
    with pytest.raises(SatkError) as e:
        c.call("capture", {"path_prefix": str(satk_home / "elsewhere" / "t2")})
    assert e.value.code == "BAD_PARAMS"
    with pytest.raises(SatkError) as e:
        C.check_paths({"files": {"color": "C:/Windows/evil.png"}}, "capture")
    assert e.value.code == "PROTOCOL"
    c.close()
    with pytest.raises(SatkError) as e:
        C.SaapClient("10.0.0.1", 1, tok)
    assert e.value.code == "BAD_PARAMS"


def _directory_link(target, link):
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


@pytest.mark.parametrize("as_list", [False, True])
def test_response_path_cannot_escape_work_via_junction(satk_home, as_list):
    work = satk_home / "work"
    outside = satk_home / "outside_work"
    outside.mkdir()
    (outside / "capture.png").write_bytes(b"synthetic capture")
    junction = work / "capture_link"
    _directory_link(outside, junction)
    candidate = junction / "capture.png"
    try:
        assert candidate.resolve() == (outside / "capture.png").resolve()
        files = [str(candidate)] if as_list else {"color": str(candidate)}
        with pytest.raises(SatkError) as e:
            C.check_paths({"files": files}, "capture")
        assert e.value.code == "PROTOCOL"
    finally:
        if os.name == "nt":
            junction.rmdir()  # remove the junction itself, never its target
        else:
            junction.unlink()


def test_response_path_accepts_resolved_work_alias(satk_home, monkeypatch):
    from satk.core import config

    work = satk_home / "work"
    capture = work / "capture.png"
    capture.write_bytes(b"synthetic capture")
    alias = satk_home / "work_alias"
    _directory_link(work, alias)
    monkeypatch.setenv("SATK_PATHS_WORK", str(alias))
    config.reset()
    try:
        C.check_paths({"files": {"color": str(capture)}}, "capture")
        C.check_paths({"files": {"color": str(alias / capture.name)}}, "capture")
    finally:
        if os.name == "nt":
            alias.rmdir()
        else:
            alias.unlink()


def test_response_path_resolution_fails_closed(satk_home, monkeypatch):
    capture = satk_home / "work" / "capture.png"
    capture.write_bytes(b"synthetic capture")

    def denied(*args, **kwargs):
        raise OSError("cannot resolve path")

    monkeypatch.setattr(os.path, "realpath", denied)
    with pytest.raises(SatkError) as e:
        C.check_paths({"files": {"color": str(capture)}}, "capture")
    assert e.value.code == "PROTOCOL"


def test_response_path_missing_file_fails_closed(satk_home):
    with pytest.raises(SatkError) as e:
        C.check_paths({"files": {"color": str(satk_home / "work" / "missing.png")}}, "capture")
    assert e.value.code == "PROTOCOL"


def test_pid_alive():
    assert C.pid_alive(os.getpid())
    assert not C.pid_alive(0) and not C.pid_alive(None)
    assert not C.pid_alive(4)  # System: not a process of this user -> never "our" endpoint


@pytest.mark.skipif(os.name != "nt", reason="Windows process tokens")
def test_pid_alive_checks_the_process_owner(monkeypatch):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert C.pid_alive(child.pid)  # the same user
        # An elevated caller can open other users' processes: the token owner decides.
        sids = iter([b"me", b"system"])
        monkeypatch.setattr(C, "_token_user_sid", lambda h: next(sids))
        assert not C.pid_alive(child.pid)
    finally:
        child.kill()
        child.wait()
    monkeypatch.undo()
    assert not C.pid_alive(child.pid)


def test_verify_pid_detects_reused_pids():
    me = os.getpid()
    info = C.process_info(me)
    assert info["exe"] and os.path.basename(info["exe"]).lower().startswith("python")
    assert time.time() - 3600 < info["created"] <= time.time()
    created = C.pid_created(me)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    ok = [{"pid": me, "pid_created": created}, {"pid": me, "exe": info["exe"], "pid_created": created},
          {"pid": me, "started_at": now}, {"pid": me, "exe": info["exe"].upper(), "started_at": now}]
    for d in ok:
        assert C.verify_pid(d) == ("ok", ""), d
    bad = [{"pid": me, "exe": "C:/nowhere/ariane.exe", "pid_created": created},       # another program
           {"pid": me, "pid_created": created - 100.0},                                # started later/earlier
           {"pid": me, "started_at": "2020-01-01T00:00:00Z"}]                          # files older than the pid
    for d in bad:
        state, why = C.verify_pid(d)
        assert state == "mismatch" and "stale" in why, d
        assert not C.endpoint_alive(d)
    assert C.verify_pid({"pid": me})[0] == "unknown" and C.endpoint_alive({"pid": me})
    assert C.verify_pid({"pid": 4})[0] == "dead" and C.verify_pid(None, {"pid": 0})[0] == "dead"
    # endpoint + session: fields are combined, the endpoint's pid counts
    assert C.verify_pid({"pid": me}, {"pid": me, "pid_created": created - 100.0})[0] == "mismatch"


def test_connect_and_list_reject_a_reused_pid(satk_home):
    C.write_endpoint("mock", {"protocol": "saap/1", "pid": os.getpid(), "port": 1,
                              "started_at": "2020-01-01T00:00:00Z"})
    C.write_session("mock", {"role": "mock", "pid": os.getpid(), "token": "t" * 64})
    with pytest.raises(SatkError) as e:
        C.connect("mock")
    assert e.value.code == "NOT_READY" and e.value.data["stale"] is True and "replaces" in e.value.hint
    (ep,) = C.list_endpoints()
    assert ep["alive"] is False and "pid was reused" in ep["stale"]


def test_discovery_serve_connect_quit(satk_home):
    tok = secrets.token_hex(32)
    ready = threading.Event()
    box = {}

    def run():
        box["result"] = serve(0, token=tok, on_ready=lambda s: ready.set())

    t = threading.Thread(target=run, daemon=True)
    t.start()
    assert ready.wait(10)
    ep = C.read_endpoint("mock")
    sess = C.read_session("mock")
    assert ep["protocol"] == "saap/1" and ep["pid"] == os.getpid() and "token" not in ep
    assert sess["token"] == tok and sess["pid"] == os.getpid()
    assert ep["pid_created"] == sess["pid_created"] == C.pid_created(os.getpid()) and ep["exe"]
    assert C.verify_pid(ep, sess) == ("ok", "")
    assert [(e["role"], e["alive"]) for e in C.list_endpoints()] == [("mock", True)]
    with C.connect("mock") as c:
        assert c.call("ping")["t_ms"] >= 0
        assert c.call("quit") == {}
    t.join(10)
    assert box["result"]["requests"] >= 2
    assert C.read_endpoint("mock") is None and C.read_session("mock") is None  # cleaned up


def test_connect_errors(satk_home):
    with pytest.raises(SatkError) as e:
        C.connect("mock")
    assert e.value.code == "NOT_READY"
    C.write_endpoint("mock", {"protocol": "saap/1", "pid": 4, "port": 1})
    with pytest.raises(SatkError) as e:
        C.connect("mock")
    assert e.value.code == "NOT_READY"  # pid not ours / not alive
    C.write_endpoint("mock", {"protocol": "ariane-ipc/1", "pid": os.getpid(), "port": 1})
    with pytest.raises(SatkError) as e:
        C.connect("mock")
    assert e.value.code == "UNSUPPORTED"
    C.write_endpoint("mock", {"protocol": "saap/1", "pid": os.getpid(), "port": 1})
    with pytest.raises(SatkError) as e:
        C.connect("mock")
    assert e.value.code == "AUTH"  # no session file
    C.remove_discovery("mock")
    assert C.read_endpoint("mock") is None


# --------------------------------------------------------------------------- conformance


def test_conformance_files_validate():
    r = CF.validate_cases()
    assert r["errors"] == []
    assert r["cases"] >= 45 and r["steps"] >= 60


def test_conformance_validation_catches_mistakes(tmp_path):
    bad = [
        {"id": "a", "caps": ["camera"], "steps": [{"call": "camera.set", "params": {}}]},          # invalid params
        {"id": "a", "caps": ["core"], "steps": [{"call": "ping"}]},                                # duplicate id
        {"id": "b", "caps": ["warp"], "steps": [{"call": "ping"}]},                                # unknown cap
        {"id": "c", "caps": ["core"], "steps": [{"call": "ping", "invalid": True}]},              # valid but marked
        {"id": "d", "caps": ["core"], "steps": [{"call": "camera.get"}]},                          # cap not listed
        {"id": "e", "caps": ["entity.inspect"], "steps": [{"call": "entity.inspect", "params": {"ref": "${x}"}}]},
        {"id": "f", "caps": ["core"], "steps": [{"raw": {"length": 5}}]},                          # raw w/o transport
        {"id": "g", "caps": ["core"], "steps": [{"call": "ping", "expect": {"error": "BAD_ID"}}]},  # not a SAAP code
    ]
    p = tmp_path / "bad.jsonl"
    p.write_text("\n".join(json.dumps(c) for c in bad) + "\nnot json\n", encoding="utf-8")
    errs = " | ".join(CF.validate_cases([p])["errors"])
    for needle in ("missing required field 'pose'", "duplicate id", "unknown capability 'warp'",
                   "marked invalid", "needs capability 'camera'", "${x}", "need transport", "not a SAAP error code",
                   "not JSON"):
        assert needle in errs, needle


def test_match_operators(tmp_path):
    m = CF.match
    assert m({"a": 1, "b": 2}, {"a": 1}) == []
    assert m({"a": 1.0}, {"a": 1}) == []
    assert m({"a": True}, {"a": 1})
    assert m([1, 2], [1]) and m({"a": [0.1, 0.2]}, {"a": {"$approx": [0.1, 0.21], "tol": 0.02}}) == []
    assert m(5, {"$gte": 5, "$lte": 5}) == [] and m(4, {"$gte": 5})
    assert m([1, {"x": "core"}], {"$contains": {"x": "core"}}) == []
    assert m("abc", {"$match": "^a.c$"}) == [] and m([1, 2], {"$len": 2}) == [] and m([], {"$minlen": 1})
    assert m("x", {"$type": "string"}) == [] and m(1, {"$type": "string"})
    png = tmp_path / "x.png"
    from satk.saap import png as P

    png.write_bytes(P.encode(3, 2, bytes(18)))
    ctx = {"work": tmp_path, "result": {"w": 3, "h": 2}}
    assert m(str(png), {"$file": {"png": "result"}}, ctx=ctx) == []
    assert m(str(png), {"$file": {"png": [2, 2]}}, ctx=ctx)
    assert m("C:/Windows/notepad.exe", {"$file": {}}, ctx=ctx)  # outside work


def test_conformance_mock_saap_driver(server):
    srv, tok = server
    rep = CF.run(CF.SaapDriver("127.0.0.1", srv.port, tok))
    bad = [r for r in rep["results"] if r[2] != "pass"]
    assert bad == [], bad
    assert rep["pass"] == rep["total"] >= 45 and rep["percent"] == 100.0
    ids = {r[0] for r in rep["results"]}
    for neg in ("auth.bad_token", "proto.oversize", "core.unknown_method", "camera.revision",
                "capture.unsupported_layer"):
        assert neg in ids


def test_conformance_backend_driver_skips_transport(satk_home):
    from satk.viewer.backends.mock import InProcessMockBackend

    rep = CF.run(CF.BackendDriver(InProcessMockBackend(MockWorld())))
    assert rep["fail"] == 0
    skipped = {r[0] for r in rep["results"] if r[2] == "skip"}
    assert "proto.oversize" in skipped and "auth.bad_token" in skipped
    assert rep["pass"] >= 35


def test_server_stop_removes_descriptor(satk_home):
    tok = secrets.token_hex(32)
    srv = SaapServer(MockWorld(), token=tok).start()
    p = srv.write_descriptor(satk_home / "work" / "run" / "endpoints")
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["port"] == srv.port and d["caps"][0] == "core"
    srv.stop()
    time.sleep(0.05)
    assert not p.exists()
