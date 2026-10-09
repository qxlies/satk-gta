"""engine test: googletest JSON parsing and the run wrapper, with a stand-in test binary (no build)."""

from __future__ import annotations

import os
import sys
import textwrap
from pathlib import Path

import pytest

from satk.core.errors import SatkError
from satk.core.registry import get_op
from satk.engine import gtest

FAILING = {
    "tests": 4, "failures": 2, "disabled": 1, "errors": 0, "name": "AllTests",
    "testsuites": [
        {"name": "Satk_PatchPlan", "tests": 3, "failures": 1, "testsuite": [
            {"name": "VerifyOk", "file": "Tests/client/Satk_PatchPlan_Tests.cpp", "line": 10, "status": "RUN",
             "result": "COMPLETED", "classname": "Satk_PatchPlan"},
            {"name": "VerifyMismatch", "file": "Tests/client/Satk_PatchPlan_Tests.cpp", "line": 22, "status": "RUN",
             "result": "COMPLETED", "classname": "Satk_PatchPlan",
             "failures": [{"failure": "D:\\fork\\Tests\\client\\Satk_PatchPlan_Tests.cpp:27\nExpected equality of "
                                      "these values:\n  state\n    Which is: 3\n  GroupState::Rejected", "type": ""}]},
            {"name": "Later", "file": "Tests/client/Satk_PatchPlan_Tests.cpp", "line": 40, "status": "NOTRUN",
             "result": "SUPPRESSED", "classname": "Satk_PatchPlan"},
        ]},
        {"name": "Satk_Settings", "tests": 1, "failures": 1, "testsuite": [
            {"name": "Clamp", "file": "Tests/client/Satk_Settings_Tests.cpp", "line": 5, "status": "RUN",
             "result": "COMPLETED", "classname": "Satk_Settings",
             "failures": [{"failure": "first problem without a location line", "type": ""},
                          {"failure": "Tests/client/Satk_Settings_Tests.cpp:9\nsecond", "type": ""}]},
        ]},
    ],
}
PASSING = {"tests": 1, "failures": 0, "disabled": 0, "errors": 0, "name": "AllTests",
           "testsuites": [{"name": "S", "tests": 1, "failures": 0, "testsuite": [
               {"name": "T", "file": "a.cpp", "line": 1, "status": "RUN", "result": "COMPLETED"}]}]}


def test_parse_failures_into_rows():
    counts, rows = gtest.parse_gtest_json(FAILING)
    assert counts["tests"] == 4 and counts["failed"] == 2 and counts["disabled"] == 1 and counts["skipped"] == 1
    assert rows[0] == ["Satk_PatchPlan", "VerifyMismatch", "D:/fork/Tests/client/Satk_PatchPlan_Tests.cpp", 27,
                       "Expected equality of these values:\n  state\n    Which is: 3\n  GroupState::Rejected"]
    # a failure without a location line falls back to the test case's own file and line
    assert rows[1][:4] == ["Satk_Settings", "Clamp", "Tests/client/Satk_Settings_Tests.cpp", 5]
    assert rows[2][:4] == ["Satk_Settings", "Clamp", "Tests/client/Satk_Settings_Tests.cpp", 9]
    assert len(rows) == 3
    assert gtest.FAIL_COLS == ["suite", "test", "file", "line", "message"]


def test_parse_clean_run():
    counts, rows = gtest.parse_gtest_json(PASSING)
    assert rows == [] and counts["tests"] == 1 and counts["failed"] == 0


def _fake_exe(tmp_path: Path, report: dict | None, exit_code: int, *, hang: bool = False) -> list[str]:
    script = tmp_path / "fake_tests.py"
    script.write_text(textwrap.dedent(f"""
        import json, sys, time
        out = None
        for a in sys.argv[1:]:
            if a.startswith("--gtest_output=json:"):
                out = a.split(":", 1)[1]
        if {hang!r}:
            time.sleep(60)
        report = {report!r}
        if report is not None and out:
            open(out, "w", encoding="utf-8").write(json.dumps(report))
        print("[  FAILED  ] something" if {exit_code} else "[  PASSED  ]")
        sys.exit({exit_code})
    """), encoding="utf-8")
    return [sys.executable, str(script)]


def test_run_gtest_failing_fixture(satk_home, tmp_path):
    cmd = _fake_exe(tmp_path, FAILING, 1)
    res = gtest.run_gtest(cmd, satk_home / "work" / "engine" / "test" / "a.json", test_filter="Satk_*")
    assert res["ok"] is False and res["exit"] == 1
    assert res["cols"] == gtest.FAIL_COLS and res["n"] == 3 and res["total"] == 3
    assert res["rows"][0][0] == "Satk_PatchPlan" and res["rows"][0][3] == 27
    assert res["tests"] == 4 and res["failed"] == 2
    assert Path(res["json"]).is_file()


def test_run_gtest_green_fixture(satk_home, tmp_path):
    res = gtest.run_gtest(_fake_exe(tmp_path, PASSING, 0), satk_home / "work" / "engine" / "test" / "b.json")
    assert res["ok"] is True and res["rows"] == [] and res["tests"] == 1 and res["exit"] == 0


def test_run_gtest_crash_without_report(satk_home, tmp_path):
    res = gtest.run_gtest(_fake_exe(tmp_path, None, 3), satk_home / "work" / "engine" / "test" / "c.json")
    assert res["ok"] is False and res["rows"][0][0] == "(process)" and "no test report" in res["rows"][0][4]


def test_run_gtest_nonzero_exit_with_clean_report_is_a_failure(satk_home, tmp_path):
    res = gtest.run_gtest(_fake_exe(tmp_path, PASSING, 5), satk_home / "work" / "engine" / "test" / "d.json")
    assert res["ok"] is False and "exit code 5" in res["rows"][0][4]


def test_run_gtest_timeout(satk_home, tmp_path):
    res = gtest.run_gtest(_fake_exe(tmp_path, PASSING, 0, hang=True), satk_home / "work" / "engine" / "test" / "e.json",
                          timeout=2)
    assert res["ok"] is False and "killed after" in res["rows"][0][4]


@pytest.fixture
def fake_binary(satk_home, tmp_path, monkeypatch):
    """Route ``engine test`` to a stand-in ``Tests_Client.exe`` (a .cmd shim around the python script)."""
    import satk.engine.build as B

    built = []

    def fake_build(**kw):
        built.append(kw)
        return {"ok": True, "cols": ["step", "platform", "config", "exit", "seconds"], "rows": [["Tests_Client", "Win32", "Release", 0, 1.5]]}

    monkeypatch.setattr(B, "build", fake_build)

    def make(report, code):
        built.clear()
        py = _fake_exe(tmp_path, report, code)
        shim = tmp_path / "Tests_Client.cmd"
        shim.write_text(f'@echo off\r\n"{py[0]}" "{py[1]}" %*\r\n', encoding="utf-8")
        monkeypatch.setattr(gtest, "test_exe_path", lambda config="Release": shim)
        return built

    return make


@pytest.mark.skipif(os.name != "nt", reason="uses a .cmd shim")
def test_engine_test_op_green_and_failing(fake_binary):
    built = fake_binary(PASSING, 0)
    out = get_op("engine.test").call({})
    assert out["ok"] is True and out["tests"] == 1 and out["n"] == 0 and out["json"].endswith(".json")
    assert built == [{"project": "Tests_Client", "platform": "Win32", "config": "Release"}]
    built = fake_binary(FAILING, 1)
    with pytest.raises(SatkError) as e:
        get_op("engine.test").call({"build": False})
    env = e.value.to_dict()
    assert env["ok"] is False and env["error"]["code"] == "CHECK_FAILED"
    assert "2 of 4 tests failed" in env["error"]["msg"]
    data = env["error"]["data"]
    assert data["cols"] == gtest.FAIL_COLS and len(data["rows"]) == 3 and data["rows"][0][1] == "VerifyMismatch"
    assert built == []  # --no-build did not build


def test_engine_test_without_binary(satk_home, monkeypatch):
    monkeypatch.setattr(gtest, "test_exe_path", lambda config="Release": satk_home / "nope.exe")
    with pytest.raises(SatkError) as e:
        gtest.engine_test_run(build_first=False)
    assert e.value.code == "NOT_READY" and "Tests_Client" in e.value.hint


# --------------------------------------------------------------------------- suites: Tests_Client and Tests_Satk


def _fork_with_satk(satk_home, with_satk: bool = True):
    """A second fork checkout (a folder under work/wt), optionally with ``Tests/satk/premake5.lua``."""
    from satk.engine.common import layout

    wt = satk_home / "work" / "wt" / "sae2-srv"
    (wt / "Tests" / "satk").mkdir(parents=True, exist_ok=True)
    if with_satk:
        (wt / "Tests" / "satk" / "premake5.lua").write_text("-- fixture\n", encoding="utf-8")
    return wt, layout(wt)


def test_test_exe_path_per_project_and_platform(satk_home):
    wt, L = _fork_with_satk(satk_home)
    assert gtest.test_exe_path("Release", L) == wt / "Bin" / "tests" / "Tests_Client.exe"
    assert gtest.test_exe_path("Release", L, "Tests_Satk", "Win32") == wt / "Bin" / "tests" / "Tests_Satk.exe"
    assert gtest.test_exe_path("Release", L, "Tests_Satk", "x64") == wt / "Bin" / "tests" / "x64" / "Tests_Satk.exe"
    assert gtest.test_exe_path("Debug", L, "Tests_Satk", "x64").name == "Tests_Satk_d.exe"


def test_plan_runs(satk_home):
    wt, L = _fork_with_satk(satk_home)
    C, S = "Tests_Client", "Tests_Satk"
    assert gtest.plan_runs(L) == [(C, "Win32"), (S, "Win32"), (S, "x64")]  # auto, Tests/satk present
    assert gtest.plan_runs(L, "client") == [(C, "Win32")]
    assert gtest.plan_runs(L, "satk") == [(S, "Win32"), (S, "x64")]
    assert gtest.plan_runs(L, "satk", "x64") == [(S, "x64")]
    assert gtest.plan_runs(L, "auto", "x64") == [(S, "x64")]
    assert gtest.plan_runs(L, "all", "Win32") == [(C, "Win32"), (S, "Win32")]
    assert gtest.plan_runs(L, "all", "both") == [(C, "Win32"), (S, "Win32"), (S, "x64")]
    with pytest.raises(SatkError) as e:
        gtest.plan_runs(L, "client", "x64")
    assert e.value.code == "BAD_PARAMS"
    with pytest.raises(SatkError) as e:
        gtest.plan_runs(L, "everything")
    assert e.value.code == "BAD_PARAMS" and "auto" in e.value.did_you_mean
    with pytest.raises(SatkError) as e:
        gtest.plan_runs(L, "auto", "arm64")
    assert e.value.code == "BAD_PARAMS"


def test_plan_runs_without_tests_satk_keeps_the_old_behaviour(satk_home):
    wt, L = _fork_with_satk(satk_home, with_satk=False)
    assert gtest.plan_runs(L) == [("Tests_Client", "Win32")]
    assert gtest.plan_runs(L, "client") == [("Tests_Client", "Win32")]
    for suite in ("satk", "all"):
        with pytest.raises(SatkError) as e:
            gtest.plan_runs(L, suite)
        assert e.value.code == "NOT_READY" and "Tests/satk" in e.value.msg


def test_engine_test_runs_every_planned_project(satk_home, monkeypatch):
    import satk.engine.build as B

    wt, L = _fork_with_satk(satk_home)
    for plat, rel in (("Win32", "Tests_Client.exe"), ("Win32", "Tests_Satk.exe"), ("x64", "x64/Tests_Satk.exe")):
        exe = wt / "Bin" / "tests" / rel
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_bytes(b"MZ")
    built, ran = [], []
    monkeypatch.setattr(B, "build", lambda **kw: built.append((kw["project"], kw["platform"], kw["fork"])) or
                        {"rows": [[kw["project"], kw["platform"], "Release", 0, 2.0]]})

    def fake_run_gtest(cmd, out_json, **kw):
        ran.append((Path(cmd[0]).parent.name, Path(cmd[0]).name, out_json.name, kw.get("env_extra")))
        return {"ok": True, "exit": 0, "seconds": 0.5, "json": str(out_json), "tests": 10, "failed": 0, "errors": 0,
                "disabled": 0, "skipped": 1, "cols": gtest.FAIL_COLS, "rows": [], "n": 0, "total": 0, "next": None}

    monkeypatch.setattr(gtest, "run_gtest", fake_run_gtest)
    res = gtest.engine_test_run(fork=str(wt))
    assert built == [("Tests_Client", "Win32", str(wt)), ("Tests_Satk", "Win32", str(wt)), ("Tests_Satk", "x64", str(wt))]
    assert [r[1] for r in ran] == ["Tests_Client.exe", "Tests_Satk.exe", "Tests_Satk.exe"]
    assert len({r[2] for r in ran}) == 3 and all(r[2].endswith(".json") for r in ran)  # one report per run
    assert res["ok"] and res["tests"] == 30 and res["skipped"] == 3 and res["build_seconds"] == 6.0
    assert [(r["project"], r["platform"]) for r in res["runs"]] == [("Tests_Client", "Win32"), ("Tests_Satk", "Win32"), ("Tests_Satk", "x64")]
    # --suite client is the old single run, without the "runs" list
    built.clear()
    ran.clear()
    res = gtest.engine_test_run(fork=str(wt), suite="client")
    assert built == [("Tests_Client", "Win32", str(wt))] and "runs" not in res and res["project"] == "Tests_Client"


def test_engine_test_failure_names_the_architecture(satk_home, monkeypatch):
    import satk.engine.build as B

    wt, L = _fork_with_satk(satk_home)
    for rel in ("Tests_Satk.exe", "x64/Tests_Satk.exe"):
        exe = wt / "Bin" / "tests" / rel
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_bytes(b"MZ")
    monkeypatch.setattr(B, "build", lambda **kw: {"rows": []})

    def fake_run_gtest(cmd, out_json, **kw):
        x64 = "x64" in Path(cmd[0]).parts
        rows = [["Satk_PodRule", "SourceTree", "Tests/satk/PodRule_Tests.cpp", 179, "R5 ..."]] if x64 else []
        return {"ok": not rows, "exit": 1 if rows else 0, "seconds": 0.1, "json": str(out_json), "tests": 5, "failed": len(rows),
                "errors": 0, "disabled": 0, "skipped": 0, "cols": gtest.FAIL_COLS, "rows": rows, "n": len(rows), "total": len(rows), "next": None}

    monkeypatch.setattr(gtest, "run_gtest", fake_run_gtest)
    with pytest.raises(SatkError) as e:
        gtest.engine_test_run(fork=str(wt), suite="satk")
    err = e.value.to_dict()["error"]
    assert err["code"] == "CHECK_FAILED" and "1 of 10 tests failed in Tests_Satk x64" in err["msg"]
    assert err["data"]["rows"][0][0] == "Tests_Satk x64: Satk_PodRule" and len(err["data"]["runs"]) == 2


def test_engine_test_op_accepts_suite_and_platform(satk_home, monkeypatch):
    seen = {}
    monkeypatch.setattr(gtest, "engine_test_run", lambda **kw: seen.update(kw) or {"ok": True})
    get_op("engine.test").call({"suite": "satk", "platform": "x64", "build": False})
    assert seen["suite"] == "satk" and seen["platform"] == "x64" and seen["build_first"] is False
