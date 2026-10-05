"""satk.viewer.backends.mta_lua without MTA: configs, resource files, HTTP transport, discovery."""

from __future__ import annotations

import json
import re
import threading
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from satk.core.errors import SatkError
from satk.saap import schema as S
from satk.viewer.backends import mta_lua as M

TEMPLATE = """<config>
    <servername>Default MTA Server</servername>
    <serverip>auto</serverip>
    <serverport>22003</serverport>
    <httpport>22005</httpport>
    <ase>1</ase>
    <password/>
    <module src="sample_win32.dll"/>
    <resource src="admin" startup="1" protected="0"/>
    <resource src="play" startup="1" protected="0"/>
</config>"""


def test_server_config_is_loopback_with_only_the_agent():
    root = ET.fromstring(M.server_config(TEMPLATE, 40001, 40002).split("\n", 1)[1])
    assert root.findtext("serverip") == "127.0.0.1" and root.findtext("serverport") == "40001"
    assert root.findtext("httpport") == "40002" and root.findtext("ase") == "0"
    assert root.findtext("donotbroadcastlan") == "1" and root.findtext("http_dos_exclude") == "127.0.0.1"
    assert root.findtext("acl") == "satk-acl.xml" and root.findall("module") == []
    assert [r.get("src") for r in root.findall("resource")] == ["satk-agent"]


def test_acl_and_settings():
    acl = ET.fromstring(M.acl_xml().split("\n", 1)[1])
    rights = {(a.get("name"), r.get("name")): r.get("access") for a in acl.findall("acl") for r in a.findall("right")}
    assert rights[("Default", "resource.satk-agent.http")] == "true"
    assert rights[("Default", "general.http")] == "false"
    assert rights[("SatkAgent", "function.shutdown")] == "true"
    tok = "ab" * 32
    s = ET.fromstring(M.settings_xml(tok))
    assert s.find("setting").get("name") == "@satk-agent.token" and s.find("setting").get("value") == tok
    with pytest.raises(SatkError):
        M.settings_xml("short")


def test_resource_files():
    src = M.resource_source()
    meta = ET.parse(src / "meta.xml").getroot()
    scripts = [(s.get("src"), s.get("type")) for s in meta.findall("script")]
    assert scripts == [("shared.lua", "shared"), ("server.lua", "server"), ("client.lua", "client")]
    for name, _ in scripts:
        text = (src / name).read_text(encoding="utf-8")
        assert "SPDX-License-Identifier: MIT" in text.splitlines()[1]
    ex = meta.find("export")
    assert ex.get("function") == "rpc" and ex.get("http") == "true"
    assert meta.find("settings/setting").get("name") == "@token"
    # every server method the backend relies on exists in server.lua / client.lua
    server = (src / "server.lua").read_text(encoding="utf-8")
    client = (src / "client.lua").read_text(encoding="utf-8")
    for m in ("ping", "hello", "status", "env.get", "env.set", "log.poll", "console.exec", "lua.exec", "quit",
              "_poll", "_cancel"):
        assert re.search(rf"S(\.{re.escape(m)}\b|\[\"{re.escape(m)}\"\])", server), m
    for m in ("status", "camera.get", "camera.set", "camera.release", "world.settle", "capture", "pick", "_rays",
              "_lua"):
        assert f'H["{m}"]' in client, m


def test_normalizers_follow_the_schemas():
    e = {"ref": "b17700@1.00,2.00,3.00", "kind": "building", "model_id": 17700.0, "model_name": "x",
         "pos": [1, 2, 3], "src": {"kind": "model_pos"}}
    h = M._hit({"hit": True, "pos": [1, 2, 3], "normal": [0, 0, 1], "dist": 5.123456, "entity": e}, 3.0, 4.0)
    assert h["entity"]["model_id"] == 17700 and h["dist"] == 5.1235
    assert S.validate_result("pick", {"hits": [h, M._hit({}, 0, 0)]}) == []
    el = M._entity({"ref": "el:object/1a", "kind": "object", "src": {"kind": "runtime", "type": "object", "id": "1a"}})
    assert el["src"] == {"kind": "runtime", "type": "object", "id": "1a"}
    assert M._pose({"pos": [1, 2, 3], "look": [1, 3, 3], "fov_h_deg": 70.0001}) == \
        {"pos": [1.0, 2.0, 3.0], "look": [1.0, 3.0, 3.0], "fov_h_deg": 70.0}
    assert M._pose({"pos": [1, 2]}) is None and M._obj([]) == {}


class _Fake:
    """HTTP server answering like MTA: canned replies per method, records requests."""

    def __init__(self, replies):
        self.replies, self.seen = replies, []
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                req = body[0]
                fake.seen.append(req)
                r = fake.replies[req["method"]]
                r = r.pop(0) if isinstance(r, list) else r
                status, text = (r if isinstance(r, tuple) else (200, json.dumps([r])))
                data = text.encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def fake():
    made = []

    def make(replies):
        f = _Fake(replies)
        made.append(f)
        return f

    yield make
    for f in made:
        f.close()


def test_transport_polls_pending_and_maps_errors(fake):
    f = fake({
        "camera.get": {"ok": True, "pending": "q1"},
        "_poll": [{"ok": True, "pending": "q1"}, {"ok": True, "result": {"pose": {"pos": [1, 2, 3], "look": [1, 3, 3]},
                                                                         "fov_h_deg": 70, "rev": 2}}],
        "ping": {"ok": False, "error": {"code": "NOT_READY", "message": "no client", "data": {"hint": "start it"}}},
        "env.get": (200, "error: resource not running"),
        "status": (401, "login"),
        "log.poll": (200, "not json"),
    })
    b = M.MtaLuaBackend("game", "game", f.port, "t" * 64)
    r = b.call("camera.get", {})
    assert r == {"pose": {"pos": [1.0, 2.0, 3.0], "look": [1.0, 3.0, 3.0]}, "fov_h_deg": 70.0, "rev": 2}
    assert [q["method"] for q in f.seen] == ["camera.get", "_poll", "_poll"] and f.seen[1]["params"] == {"rid": "q1"}
    assert all(q["token"] == "t" * 64 for q in f.seen)
    for method, code in (("ping", "NOT_READY"), ("env.get", "NOT_READY"), ("status", "AUTH"), ("log.poll", "PROTOCOL")):
        with pytest.raises(SatkError) as e:
            b.call(method, {})
        assert e.value.code == code, method
    with pytest.raises(SatkError) as e:
        b.call("ping", {})
    assert e.value.hint == "start it"
    with pytest.raises(SatkError) as e:
        b.call("nope.method", {})
    assert e.value.code == "UNKNOWN_METHOD"
    with pytest.raises(SatkError) as e:
        b.call("capture", {"layers": ["color", "ids"], "path_prefix": "C:/x/y"})
    assert e.value.code == "UNSUPPORTED"


def test_pending_timeout_cancels(fake):
    f = fake({"world.settle": {"ok": True, "pending": "q9"}, "_poll": {"ok": True, "pending": "q9"},
              "_cancel": {"ok": True, "result": {"cancelled": True}}})
    b = M.MtaLuaBackend("game", "game", f.port, "t" * 64, timeout=0.3)
    with pytest.raises(SatkError) as e:
        b.rpc("world.settle")
    assert e.value.code == "TIMEOUT" and f.seen[-1]["method"] == "_cancel"


def test_get_backend_picks_mta_lua_from_discovery(satk_home, fake):
    import os

    from satk.saap import client as C
    from satk.viewer.backends import get_backend

    f = fake({"hello": {"ok": True, "result": {"agent_version": "1.0.0", "server": {"version": "1.7"},
                                                 "client": {"w": 800, "h": 600, "name": "satk"}, "players": []}}})
    pid = os.getpid()
    C.write_endpoint("game", {"role": "game", "protocol": "mta-lua/1", "port": f.port, "pid": pid,
                              "exe": (C.process_info(pid) or {}).get("exe"), "pid_created": C.pid_created(pid)})
    C.write_session("game", {"token": "k" * 64, "pid": pid, "gta_path": "C:\\games\\sa"})
    b = get_backend("game")
    assert isinstance(b, M.MtaLuaBackend) and b.token == "k" * 64
    h = b.hello
    assert h["viewport"] == {"w": 800, "h": 600} and h["game"]["root"] == "C:/games/sa"
    assert S.validate_result("hello", h) == []


def test_preflight_reports_checks():
    r = M.client_preflight()
    names = [c[0] for c in r["checks"]]
    assert names[:2] == ["client_exe", "registry_gta_path"] and "registry_writable" in names
    assert r["ready"] == (not r["blockers"]) and all(c[1] in ("ok", "blocker") for c in r["checks"])


def test_prepare_server_from_a_fake_build(satk_home, monkeypatch):
    bin_ = satk_home / "engine" / "Bin"
    srv = bin_ / "server"
    (srv / "x64").mkdir(parents=True)
    (srv / "mods" / "deathmatch").mkdir(parents=True)
    (srv / "MTA Server64.exe").write_bytes(b"MZ fake")
    for n in ("core.dll", "net.dll", "deathmatch.dll"):
        (srv / "x64" / n).write_bytes(b"MZ " + n.encode())
    (srv / "x64" / "core.pdb").write_bytes(b"pdb")
    (srv / "mods" / "deathmatch" / "mtaserver.conf").write_text(TEMPLATE, encoding="utf-8")
    monkeypatch.setattr(M, "_engine_bin", lambda: bin_)
    tok = "cd" * 32
    root = M.server_root()
    r = M.prepare_server(40001, 40002, tok)
    assert r["copied"] == 4 and Path(r["root"]) == root
    assert not (root / "x64" / "core.pdb").exists()
    dm = root / "mods" / "deathmatch"
    assert (dm / "resources" / "satk-agent" / "meta.xml").is_file() and (dm / "resources" / "satk-agent" / "captures").is_dir()
    assert tok in (dm / "settings.xml").read_text(encoding="utf-8")
    (dm / "resources" / "satk-agent" / "captures" / "old.png").write_bytes(b"x")
    assert M.prepare_server(40001, 40002, tok)["copied"] == 0
    assert not (dm / "resources" / "satk-agent" / "captures" / "old.png").exists()
    monkeypatch.setattr(M, "_engine_bin", lambda: satk_home / "nothing")
    with pytest.raises(SatkError) as e:
        M.prepare_server(1, 2, tok)
    assert e.value.code == "NOT_READY"


def test_status_and_stop_without_a_server(satk_home):
    assert M.status() == {"target": "game", "up": False}
    assert M.stop()["stopped"] is False
    with pytest.raises(SatkError) as e:
        M.start_client()
    assert e.value.code == "NOT_READY"


def test_cli_main(satk_home, capsys):
    assert M.main(["status"]) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "target": "game", "up": False}
    assert M.main(["client"]) == 3
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "NOT_READY"
