"""satk ingame operations with the server and the game replaced by fakes (no MTA process is started)."""

from __future__ import annotations

from pathlib import Path

import pytest

from satk.core import paths
from satk.core.errors import SatkError
from satk.core.registry import get_op, invoke
from satk.ingame import modset as MS
from satk.ingame import ops as O
from satk.ingame import session as S


class FakeBridge:
    def __init__(self, manifest=None, joined=True):
        self.calls = []
        self.manifest = manifest or {"models": [{"key": "premier", "kind": "vehicle", "mode": "replace", "base": 426}]}
        self.joined = joined
        self.b = self

    def server(self, cmd, args=None):
        self.calls.append(("server", cmd, args))
        if cmd == "manifest":
            return self.manifest
        if cmd == "spawn":
            return {"kind": args.get("kind", "vehicle"), "model": args["model"], "element": "satk.td.1"}
        if cmd == "status":
            return {"players": [], "content": "running", "rev": "r"}
        return {}

    def client(self, cmd, args=None):
        self.calls.append(("client", cmd, args))
        if cmd == "pose":
            return {"pos": [1, 2, 3], "look": [4, 5, 6], "fov_h_deg": 60, "target": "vehicle"}
        return {}

    def client_joined(self):
        return self.joined

    def call(self, method, params):
        self.calls.append(("rpc", method, params))
        return {"files": {"color": params["path_prefix"] + ".png"}, "w": params["w"], "h": params["h"],
                "pose": params.get("pose")}


@pytest.fixture
def fakes(satk_home, monkeypatch, vanilla):
    state = {"started": [], "bridge": FakeBridge()}
    monkeypatch.setattr(MS, "Vanilla", lambda profile="vanilla": vanilla)
    monkeypatch.setattr(S, "ensure_server", lambda port, httpport, timeout, conf=None: (
        {"pid": 4242, "server_port": port, "reused": bool(state["started"])}, []))
    monkeypatch.setattr(S, "Bridge", lambda backend=None: state["bridge"])

    def start_resources(bridge, *, logic_changed, content_changed, timeout=20.0):
        state["started"].append((logic_changed, content_changed))
        return {"satk-testdrive-mod": "restarted" if content_changed else "running", "satk-testdrive": "running"}

    monkeypatch.setattr(S, "start_resources", start_resources)
    monkeypatch.setattr(S, "wait_applied", lambda bridge, rev, timeout: {"players": [], "no_client": True})
    monkeypatch.setattr(S, "preflight", lambda: {"ready": False, "setup_done": False, "game_running": False,
                                                 "blockers": ["registry_gta_path: missing"]})
    from satk.viewer.backends import mta_lua

    monkeypatch.setattr(mta_lua, "status", lambda probe=True: {"up": True, "server_port": 22040, "pid": 4242})
    return state


def _mod(tmp_path: Path) -> Path:
    d = tmp_path / "mod"
    d.mkdir()
    (d / "premier.dff").write_bytes(b"dff")
    (d / "premier.txd").write_bytes(b"txd")
    return d


def test_ops_are_registered_cli_only_where_needed():
    from satk.mcp.generic import denial

    names = ["ingame.start", "ingame.reload", "ingame.status", "ingame.stop", "ingame.spawn", "ingame.drive",
             "ingame.check", "ingame.shot", "ingame.logs", "ingame.spots", "ingame.suites", "ingame.play"]
    for n in names:
        spec = get_op(n)
        assert spec.mcp is False and len(spec.summary) <= 300 and spec.summary_ru
        assert (denial(spec) is not None) == (n == "ingame.play")
    assert denial(get_op("ingame.play"))[0] == "CONSENT_REQUIRED"


def test_spots_and_suites():
    env = invoke("ingame.spots", {})
    names = {r[0] for r in env["rows"] if r[1] == "spot"}
    assert {"grove", "runway", "wall", "ramp", "night", "pad"} <= names
    assert all(c is not None for r in env["rows"] for c in r)
    s = invoke("ingame.suites", {})
    assert ["vehicle", "speed"] == s["rows"][2][:2] and len(s["rows"]) == 16


def test_start_builds_resources_scripts_and_state(fakes, tmp_path):
    mod = _mod(tmp_path)
    r = O.ingame_start(mod=[str(mod)])
    assert r["server"]["connect"] == "mtasa://127.0.0.1:22040" and r["server"]["reused"] is False
    assert r["models"] == [{"key": "premier", "kind": "vehicle", "mode": "replace", "base": "426 premier",
                            "files": ["dff", "txd"]}]
    assert r["client"] == {"setup_done": False, "game_running": False, "joined": False}
    assert any("admin-setup.ps1" in n for n in r["next"]) and any(n.startswith("join: satk ingame play") for n in r["next"])
    res = S.resources_dir()
    assert (res / "satk-testdrive-mod" / "m" / "premier.dff").read_bytes() == b"dff"
    assert (res / "satk-testdrive" / "checks.lua").is_file()
    st = S.load_state()
    assert st["inputs"] == [str(mod)] and st["rev"] == r["rev"] and st["port"] == 22040 and st["models"] == ["premier"]
    d = S.ingame_dir()
    setup = (d / "admin-setup.ps1").read_bytes()
    assert setup.startswith(b"\xef\xbb\xbf") and b"\r\n" in setup and b"GTA:SA Path" in setup
    assert b"-m satk ingame play" in (d / "play.cmd").read_bytes()
    assert r["play"]["console"] == "connect 127.0.0.1 22040" and r["play"]["file"].endswith("play.cmd")
    # a second start without --mod keeps the set and restarts nothing that did not change
    r2 = O.ingame_start()
    assert r2["rev"] == r["rev"] and "changed" not in r2 and fakes["started"][-1] == (False, False)


def test_reload(fakes, tmp_path):
    with pytest.raises(SatkError) as e:
        O.ingame_reload()
    assert e.value.code == "NOT_READY"
    mod = _mod(tmp_path)
    first = O.ingame_start(mod=[str(mod)])
    r = O.ingame_reload()
    assert r["note"] == "nothing changed" and r["rev"] == first["rev"]
    (mod / "premier.txd").write_bytes(b"txd2")
    r = O.ingame_reload()
    assert r["changed"] == ["m/premier.txd", "manifest.lua"] and r["old_rev"] == first["rev"] != r["rev"]
    assert fakes["started"][-1] == (False, True) and r["resources"]["satk-testdrive-mod"] == "restarted"
    assert r["warn"][0].startswith("NOT_READY: no client")
    r = O.ingame_reload(force=True)
    assert fakes["started"][-1] == (False, True) and "changed" not in r


def test_spawn_and_drive(fakes):
    b = fakes["bridge"]
    r = O.ingame_spawn(at="wall", time="21:30", weather=8)
    assert r["element"] == "satk.td.1"
    args = b.calls[-1][2]
    assert args == {"camera": "three_quarter", "drive": False, "spot": "wall", "time": "21:30", "weather": 8,
                    "model": "mod"}
    O.ingame_spawn(model="model:411", at="1,2,3", heading=90)
    args = b.calls[-1][2]
    assert args["model"] == 411 and args["kind"] == "vehicle" and args["pos"] == [1.0, 2.0, 3.0] and args["h"] == 90
    O.ingame_spawn(model="desert_eagle")
    assert b.calls[-1][2]["kind"] == "weapon" and b.calls[-1][2]["weapon"] == 24
    O.ingame_drive()
    args = b.calls[-1][2]
    assert args["drive"] is True and args["spot"] == "runway" and args["kind"] == "vehicle" and args["camera"] == "chase"
    with pytest.raises(SatkError):
        O.ingame_spawn(at="nowhere")
    with pytest.raises(SatkError):
        O.ingame_spawn(time="25:00")
    b.joined = False
    with pytest.raises(SatkError) as e:
        O.ingame_drive()
    assert e.value.code == "NOT_READY" and "connect 127.0.0.1 22040" in e.value.hint


def test_shot(fakes):
    r = O.ingame_shot(camera="side", time="00:00")
    rpc = fakes["bridge"].calls[-1]
    assert rpc[1] == "capture" and rpc[2]["pose"] == {"pos": [1, 2, 3], "look": [4, 5, 6], "fov_h_deg": 60}
    assert rpc[2]["env"] == {"time": "00:00"} and r["file"].endswith("/out/ingame/shots/side-auto.png")
    O.ingame_shot(camera="current", name="my shot")
    rpc = fakes["bridge"].calls[-1]
    assert "pose" not in rpc[2] and rpc[2]["path_prefix"].endswith("/shots/my_shot")


def test_check_needs_a_loaded_model(fakes):
    b = fakes["bridge"]
    with pytest.raises(SatkError) as e:
        O.ingame_check(suite="ped")
    assert e.value.code == "NOT_FOUND"
    with pytest.raises(SatkError) as e:
        O.ingame_check(only=["nope"])
    assert e.value.code == "BAD_PARAMS" and "speed" in e.value.data["checks"]
    with pytest.raises(SatkError) as e:
        O.ingame_check()
    assert e.value.code == "NOT_READY" and "has not loaded premier" in e.value.msg
    b.joined = False
    with pytest.raises(SatkError) as e:
        O.ingame_check()
    assert e.value.code == "NOT_READY"


def test_status_and_logs_without_a_server(satk_home, monkeypatch):
    from satk.viewer.backends import mta_lua

    monkeypatch.setattr(S, "preflight", lambda: {"ready": True, "setup_done": True, "game_running": False})
    r = O.ingame_status()
    assert r["server"] == {"up": False} and r["preflight"]["setup_done"] is True and "warn" not in r
    assert mta_lua.status()["up"] is False
    env = O.ingame_logs(source="server")
    assert env["rows"] == []
    with pytest.raises(SatkError):
        O.ingame_check()
    with pytest.raises(SatkError) as e:
        O.ingame_logs(grep="(")
    assert e.value.code == "BAD_PARAMS"


def test_play_refuses_without_setup(fakes):
    with pytest.raises(SatkError) as e:
        O.ingame_play()
    assert e.value.code == "NOT_READY" and "admin-setup.ps1" in e.value.hint


def test_write_scripts_names_the_clean_copy(satk_home):
    s = S.write_scripts(22041)
    text = Path(s["admin_setup"]).read_text(encoding="utf-8-sig")
    assert str(Path(paths.cfg().paths.game)) in text
    assert "connect 127.0.0.1 22041" in Path(s["play"]).read_text(encoding="utf-8")
    assert S.play_info(22041, s)["client"].endswith("mtasa://127.0.0.1:22041")


# --------------------------------------------------------------------------- --conf / --cvar / --windowed / cvar-restore


COREFG = "<mainconfig>\n    <settings>\n        <fps_limit>100</fps_limit>\n        <vsync>1</vsync>\n    </settings>\n</mainconfig>\n"


@pytest.fixture
def cvfx(fakes, satk_home, monkeypatch):
    from satk.ingame import cvars as CV

    cfg = satk_home / "client" / "coreconfig.xml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(COREFG, encoding="utf-8")
    running: list[int] = []
    monkeypatch.setattr(CV, "config_path", lambda: cfg)
    monkeypatch.setattr(CV, "gta_running", lambda: list(running))
    seen = {}

    def ensure_server(port, httpport, timeout, conf=None):
        seen["conf"] = conf
        return {"pid": 4242, "server_port": port, "reused": False, "template": "fork-source",
                "conf": conf or None}, ["STALE_TEMPLATE: the Bin copy is stale"]

    monkeypatch.setattr(S, "ensure_server", ensure_server)
    return {"cfg": cfg, "running": running, "seen": seen, "CV": CV}


def test_start_with_conf_cvar_and_windowed(cvfx):
    r = O.ingame_start(clear=True, conf=["sae_policy=0", "fpslimit=0"], cvar=["vsync=0", "fps_limit=0"], windowed=True)
    assert cvfx["seen"]["conf"] == {"sae_policy": "0", "fpslimit": "0"}
    assert r["server"]["conf"] == {"sae_policy": "0", "fpslimit": "0"} and r["server"]["template"] == "fork-source"
    assert r["warn"] == ["STALE_TEMPLATE: the Bin copy is stale"]
    assert r["cvars"]["set"] == {"display_windowed": "1", "display_fullscreen_style": "0", "vsync": "0", "fps_limit": "0"}
    assert any(c.startswith("vsync: 1 -> 0") for c in r["cvars"]["changed"]) and r["cvars"]["backup"]
    text = cvfx["cfg"].read_text(encoding="utf-8")
    assert "<vsync>0</vsync>" in text and "<fps_limit>0</fps_limit>" in text and "<display_windowed>1</display_windowed>" in text
    assert O.ingame_status()["cvars"]["cvars"]["vsync"] == "0"
    # the same values again are a no-op even while the game runs; new values are refused
    cvfx["running"].append(4)
    O.ingame_start(cvar=["vsync=0"])
    with pytest.raises(SatkError) as e:
        O.ingame_start(cvar=["vsync=1"])
    assert e.value.code == "NOT_READY" and "gta_sa.exe is running" in e.value.msg
    assert "<vsync>0</vsync>" in cvfx["cfg"].read_text(encoding="utf-8")
    with pytest.raises(SatkError) as e:
        O.ingame_cvar_restore()
    assert e.value.code == "NOT_READY"
    cvfx["running"].clear()
    out = O.ingame_cvar_restore()
    assert out["restored"] is True and cvfx["cfg"].read_text(encoding="utf-8") == COREFG
    assert O.ingame_cvar_restore()["restored"] is False
    assert O.ingame_status().get("cvars") is None


def test_start_validates_conf_and_cvar_before_anything_starts(cvfx):
    for kw in ({"conf": ["serverip=0.0.0.0"]}, {"conf": ["ase=1"]}, {"conf": ["nonsense"]}, {"cvar": ["nonsense"]},
               {"cvar": ["a b=1"]}):
        with pytest.raises(SatkError) as e:
            O.ingame_start(clear=True, **kw)
        assert e.value.code == "BAD_PARAMS"
    assert "conf" not in cvfx["seen"] and cvfx["cfg"].read_text(encoding="utf-8") == COREFG


def test_stop_restores_the_client_config(cvfx, monkeypatch):
    from satk.viewer.backends import mta_lua

    O.ingame_start(clear=True, cvar=["vsync=0"])
    monkeypatch.setattr(mta_lua, "stop", lambda: {"target": "game", "up": False, "stopped": True, "how": "quit"})
    out = O.ingame_stop()
    assert out["stopped"] is True and out["cvars"]["restored"] is True
    assert cvfx["cfg"].read_text(encoding="utf-8") == COREFG
    assert "cvars" not in O.ingame_stop()                          # nothing recorded the second time


def test_play_writes_cvars_before_the_client_starts(cvfx, monkeypatch):
    from satk.viewer.backends import mta_lua

    monkeypatch.setattr(S, "preflight", lambda: {"ready": True, "setup_done": True, "game_running": False})
    seen = {}

    def start_client(*, timeout=180.0, force=False):
        seen["cfg"] = cvfx["cfg"].read_text(encoding="utf-8")
        return {"target": "game", "client": {"w": 1}, "client_pid": 55}

    monkeypatch.setattr(mta_lua, "start_client", start_client)
    r = O.ingame_play(cvar=["vsync=0"], windowed=True)
    assert "<vsync>0</vsync>" in seen["cfg"] and "<display_windowed>1</display_windowed>" in seen["cfg"]
    assert r["client_pid"] == 55 and cvfx["CV"].load_state()["launched"]["pid"] == 55


def test_cvar_ops_are_registered():
    from satk.mcp.generic import denial

    for n in ("ingame.cvar_restore", "ingame.stop", "ingame.bench"):
        spec = get_op(n)
        assert spec.mcp is False and len(spec.summary) <= 300 and spec.summary_ru and denial(spec) is None
    for n, names in (("ingame.start", {"conf", "cvar", "windowed"}), ("ingame.play", {"cvar", "windowed"}),
                     ("ingame.bench", {"cvar", "restore_cvars", "settle"})):
        assert names <= {p.name for p in get_op(n).params}
