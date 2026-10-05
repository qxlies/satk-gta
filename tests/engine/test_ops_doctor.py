"""satk.engine ops registration, doctor/status on an empty workspace, smoke config, locks."""

from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET

import pytest

from satk.core.errors import SatkError
from satk.core.registry import all_ops, doctor_checks, get_op, op_by_mcp, status_providers

ENGINE_OPS = {"engine.doctor", "engine.status", "engine.setup", "engine.rc_test", "engine.gen", "engine.build",
              "engine.server_smoke", "engine.run"}


def test_ops_registered():
    names = {o.name for o in all_ops()}
    assert ENGINE_OPS <= names
    run = get_op("engine.run")
    assert run.mcp_name == "engine" and run.mcp_group == "engine" and run.long_running
    assert run.cli == "engine run"
    assert op_by_mcp("engine").name == "engine.run"
    # only one MCP tool for the engine group (SPEC §4.6: 25 tools in total)
    assert [o.name for o in all_ops() if o.name.startswith("engine.") and o.mcp_name] == ["engine.run"]
    schema = run.input_schema()
    assert schema["properties"]["cmd"]["enum"] == ["status", "doctor", "build"]
    assert schema["required"] == ["cmd"]
    assert len(run.summary) <= 300
    assert get_op("engine.rc_test").cli == "engine rc-test"
    assert get_op("engine.server_smoke").cli == "engine server-smoke"
    b = get_op("engine.build")
    assert b.param("platform").choices == ("Win32", "x64")
    assert b.param("project").default == "all"


def test_doctor_and_status_registered():
    assert "engine" in doctor_checks()
    assert "engine" in status_providers()


def test_engine_run_dispatch(monkeypatch):
    import satk.engine.build as B

    seen = {}

    def fake_build(**kw):
        seen.update(kw)
        return {"ok": True, "cols": [], "rows": [], "n": 0, "total": 0, "next": None}

    monkeypatch.setattr(B, "build", fake_build)
    out = get_op("engine.run").call({"cmd": "build", "args": {"project": "Game SA", "platform": "Win32"}})
    assert out["ok"] and seen["project"] == "Game SA" and seen["platform"] == "Win32" and seen["config"] == "Release"
    with pytest.raises(SatkError) as e:
        get_op("engine.run").call({"cmd": "build", "args": {"projekt": "x"}})
    assert e.value.code == "BAD_PARAMS" and "project" in e.value.did_you_mean


def test_cli_build_parses_quoted_project(run_cli, monkeypatch):
    import satk.engine.build as B

    seen = {}
    monkeypatch.setattr(B, "build", lambda **kw: seen.update(kw) or {"ok": True, "rows": [], "cols": [], "n": 0,
                                                                     "total": 0, "next": None})
    r = run_cli(["engine", "build", "--project", "Game SA", "--platform", "x64", "--config", "Debug", "--jobs", "4"])
    assert r.code == 0, r.err
    assert seen == {"project": "Game SA", "platform": "x64", "config": "Debug", "target": None, "jobs": 4,
                    "toolset": None, "no_deps": False, "regen": "auto", "max_errors": 30}
    r = run_cli(["engine", "build", "--platform", "arm64"])
    assert r.code == 2


def test_doctor_on_empty_workspace(satk_home, run_cli):
    from satk.engine import doctor as D

    res = {r["check"]: r for r in D.run_checks()}
    assert set(res) == set(D.CHECKS)
    assert res["fork"]["status"] == "fail" and res["fork"]["fix"] == "satk engine setup"
    assert res["shims"]["status"] == "fail"
    assert all(r["status"] in ("ok", "warn", "fail", "skip") for r in res.values())
    s = D.summary(list(res.values()))
    assert s["status"] == "fail" and s["fix"]
    r = run_cli(["engine", "doctor"])
    env = r.json
    assert r.code == 0 and env["status"] == "fail" and env["cols"] == ["check", "status", "msg", "fix"]
    st = D.status()
    assert st["fork"]["exists"] is False and st["outputs"].startswith("0/")


def test_summary_levels():
    from satk.engine.doctor import summary

    assert summary([{"check": "a", "status": "ok", "msg": "", "fix": None}])["status"] == "ok"
    s = summary([{"check": "a", "status": "ok", "msg": "", "fix": None},
                 {"check": "b", "status": "warn", "msg": "w", "fix": "do"}])
    assert s == {"status": "warn", "msg": "1/2 engine checks ok; b: w", "fix": "do"}


_CONF = """<config>
    <!-- comment -->
    <servername>Default MTA Server</servername>
    <serverip>auto</serverip>
    <serverport>22003</serverport>
    <httpport>22005</httpport>
    <ase>1</ase>
    <donotbroadcastlan>0</donotbroadcastlan>
    <crash_dump_upload>1</crash_dump_upload>
    <minclientversion_auto_update>1</minclientversion_auto_update>
    <backup_interval>3</backup_interval>
    <password></password>
    <resource src="admin" startup="1" protected="0"/>
    <resource src="play" startup="1" protected="0"/>
</config>"""


def test_smoke_config_is_loopback_and_quiet():
    from satk.engine.smoke import smoke_config

    text = smoke_config(_CONF, 22103, 22105, "pw")
    root = ET.fromstring(text.split("\n", 1)[1])
    v = {el.tag: (el.text or "") for el in root}
    assert v["serverip"] == "127.0.0.1" and v["serverport"] == "22103" and v["httpport"] == "22105"
    assert v["ase"] == "0" and v["donotbroadcastlan"] == "1" and v["crash_dump_upload"] == "0"
    assert v["minclientversion_auto_update"] == "0" and v["backup_interval"] == "0" and v["password"] == "pw"
    assert root.find("resource") is None
    assert v["logfile"].startswith("logs/satk-smoke")


def test_server_smoke_not_built(satk_home):
    from satk.engine.smoke import server_smoke

    with pytest.raises(SatkError) as e:
        server_smoke()
    assert e.value.code == "NOT_READY"


def test_exclusive_lock(satk_home):
    from satk.engine.common import exclusive

    with exclusive("build") as p:
        assert p.is_file()
        assert int(p.read_text(encoding="utf-8").split()[0]) == os.getpid()
        # a stale lock of a dead process is taken over
    p.write_text("999999 stale\n", encoding="utf-8")
    with exclusive("build"):
        pass
    assert not p.exists()


def test_exclusive_lock_busy(satk_home):
    import subprocess
    import sys

    from satk.engine.common import exclusive

    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        from satk.core.paths import work

        lockf = work("engine", "build") / ".build.lock"
        lockf.write_text(f"{child.pid} test\n", encoding="utf-8")
        with pytest.raises(SatkError) as e:
            with exclusive("build"):
                pass
        assert e.value.code == "BUSY" and e.value.data["pid"] == child.pid
    finally:
        child.kill()
        child.wait()


def test_pid_alive():
    from satk.engine.common import pid_alive

    assert pid_alive(os.getpid())
    assert not pid_alive(0)


def test_ops_module_imports_stdlib_only():
    import ast
    from pathlib import Path

    import satk.engine as pkg

    heavy = {"numpy", "PIL", "mcp"}
    for f in Path(pkg.__file__).parent.glob("*.py"):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not any(a.name.split(".")[0] in heavy for a in node.names), f
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] not in heavy, f


def test_lock_json_written_atomically(satk_home):
    from satk.engine.common import read_json, write_json
    from satk.core.paths import work

    p = work("engine", "x.json")
    write_json(p, {"a": [1, "é"]})
    assert read_json(p) == {"a": [1, "é"]}
    assert json.loads(p.read_text(encoding="utf-8"))["a"][1] == "é"
    p.write_text("{broken", encoding="utf-8")
    with pytest.raises(SatkError):
        read_json(p)
