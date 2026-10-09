"""``--fork PATH`` of the engine operations: layout, ids, per-fork logs/state/lock, premake wrapper, MSBuild shim.

Everything here is synthetic: no premake, no MSBuild, no real fork. The default behaviour (no ``--fork``) is
pinned next to the new one, because engine lanes build through the same code.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from satk.core.errors import SatkError
from satk.core.registry import get_op
from satk.engine import build as B
from satk.engine import common as C
from satk.engine import gtest
from satk.engine.common import RunResult, fork_id_of, layout, same_path

FORK_OPS = ("engine.doctor", "engine.status", "engine.gen", "engine.build", "engine.server_smoke",
            "engine.sites_check", "engine.sites_gen", "engine.sites_scan", "engine.test")


def _other(satk_home: Path, name: str = "sae2-srv") -> Path:
    p = satk_home / "work" / "wt" / name
    p.mkdir(parents=True, exist_ok=True)
    return p


# --------------------------------------------------------------------------- layout and ids


def test_default_layout_is_unchanged(satk_home):
    L = layout()
    assert L.is_default and L.fork_id == ""
    assert L.fork == satk_home / "engine" / "mtasa" and L.root == satk_home / "engine"
    assert L.logs == satk_home / "work" / "engine" / "build" / "logs"
    assert L.state == satk_home / "work" / "engine" / "build" / "state.json"
    assert L.lock_name == "build" and L.tmp_id == "engine-build"
    assert L.sln == L.fork / "Build" / "MTASA.sln" and L.bin == L.fork / "Bin"


@pytest.mark.parametrize("spelling", [
    lambda L: str(L.fork), lambda L: str(L.fork).upper(), lambda L: str(L.fork) + "\\", lambda L: str(L.fork).replace("\\", "/"),
    lambda L: None, lambda L: "",
])
def test_configured_fork_in_any_spelling_is_the_default(satk_home, spelling):
    default = layout()
    assert layout(spelling(default)) == default


def test_second_fork_layout(satk_home):
    wt = _other(satk_home)
    L = layout(wt)
    d = layout()
    assert not L.is_default and L.fork_id == "sae2-srv" and L.fork == wt
    # shared with the configured fork: the wrapper, the deps store, the shims, the donor
    assert (L.root, L.donor, L.build_dir) == (d.root, d.donor, d.build_dir)
    assert L.wrapper == d.wrapper and L.deps == d.deps and L.lock == d.lock
    # its own: outputs, logs, state, lock, temp, registry entry
    assert L.sln == wt / "Build" / "MTASA.sln" and L.bin == wt / "Bin"
    assert L.logs == d.build_dir / "logs" / "sae2-srv"
    assert L.state == d.build_dir / "state.sae2-srv.json"
    assert L.lock_name == "build-sae2-srv" and L.tmp_id == "engine-build-sae2-srv"
    assert L.meta == satk_home / "work" / "engine" / "forks" / "sae2-srv.json"


def test_layout_creates_nothing(satk_home):
    layout(satk_home / "work" / "wt" / "ghost")
    assert not (satk_home / "work" / "wt").exists() and not (satk_home / "work" / "engine").exists()


def test_fork_id_rules(satk_home):
    wt = satk_home / "work" / "wt"
    assert fork_id_of(wt / "Sae2-Srv", wt) == "sae2-srv"
    other = fork_id_of(satk_home / "elsewhere" / "sae2-srv", wt)
    assert other.startswith("sae2-srv-") and len(other) == len("sae2-srv-") + 6
    assert other != fork_id_of(satk_home / "else" / "sae2-srv", wt)  # same name, other folder: other id
    assert other == fork_id_of(satk_home / "elsewhere" / "sae2-srv", wt)  # stable
    assert fork_id_of(wt / "we ird!", wt) == "we-ird"
    assert same_path("C:/A/b/", "c:\\a\\B") and not same_path("C:/A", "C:/A/b")


def test_every_fork_operation_takes_fork():
    for name in FORK_OPS:
        p = get_op(name).param("fork")
        assert p is not None and p.default is None, name
    assert get_op("engine.worktree").param("action").choices == ("create", "list", "remove", "refresh")
    assert get_op("engine.worktree.refresh").param("fork").default is None


def test_build_env_of_a_second_fork_has_its_own_temp(satk_home):
    wt = _other(satk_home)
    a, b = C.build_env(), C.build_env(L=layout(wt))
    assert a["TEMP"].endswith("engine-build") and b["TEMP"].endswith("engine-build-sae2-srv")
    assert a["DXSDK_DIR"] == b["DXSDK_DIR"]


# --------------------------------------------------------------------------- gen


def _fake_tree(wt: Path) -> None:
    (wt / "utils").mkdir(parents=True, exist_ok=True)
    (wt / "utils" / "premake5.exe").write_bytes(b"MZ")
    (wt / "premake5.lua").write_text("-- synthetic\n", encoding="utf-8")


@pytest.fixture
def fake_tools(monkeypatch):
    """Capture premake/MSBuild command lines instead of running them."""
    calls: list[dict] = []

    def fake_run(cmd, *, cwd=None, env=None, log=None, timeout=None, input=None, keep=4000):  # noqa: A002
        calls.append({"cmd": [str(c) for c in cmd], "cwd": cwd, "env": env, "log": log})
        if "vs2026" in [str(c) for c in cmd]:
            Path(cwd, "Build").mkdir(exist_ok=True)
            Path(cwd, "Build", "MTASA.sln").write_text("synthetic\n", encoding="utf-8")
            Path(cwd, "Build", "Core.vcxproj").write_text("<Project/>", encoding="utf-8")
        return RunResult(cmd=[str(c) for c in cmd], code=0, seconds=0.1, log=log)

    monkeypatch.setattr(B, "run", fake_run)
    monkeypatch.setattr(B, "find_msbuild", lambda: Path("MSBuild.exe"))
    monkeypatch.setattr(B, "gen_fingerprint", lambda L=None: "fp")
    monkeypatch.setattr(B, "git", lambda *a, **k: "0123456789abcdef")
    return calls


def test_gen_of_a_second_fork_goes_through_the_wrapper(satk_home, fake_tools):
    L = layout()
    L.root.mkdir(parents=True)
    L.wrapper.write_text("-- wrapper\n", encoding="utf-8")
    wt = _other(satk_home)
    _fake_tree(wt)
    out = B.gen(layout(wt))
    cmd = fake_tools[0]["cmd"]
    assert cmd[0].endswith("premake5.exe") and cmd[-1] == "vs2026" and cmd[1] == f"--file={L.wrapper}"
    env = fake_tools[0]["env"]
    assert env["SATK_OFFLINE"] == "1" and env["SATK_FORK"] == str(wt) and env["SATK_DEPS_LOCK"] == str(L.lock)
    assert env["TEMP"].endswith("engine-build-sae2-srv")
    assert fake_tools[0]["cwd"] == wt
    assert Path(out["log"]).parent == L.logs / "sae2-srv" and out["fork"].endswith("work/wt/sae2-srv")
    state = json.loads((L.build_dir / "state.sae2-srv.json").read_text(encoding="utf-8"))
    assert state["gen"]["fingerprint"] == "fp"
    assert not (L.build_dir / "state.json").exists()  # the configured fork's state is not touched


def test_gen_of_the_configured_fork_still_runs_premake_directly(satk_home, fake_tools):
    L = layout()
    L.fork.mkdir(parents=True)
    _fake_tree(L.fork)
    out = B.gen()
    cmd = fake_tools[0]["cmd"]
    assert [Path(cmd[0]).name, cmd[1]] == ["premake5.exe", "vs2026"] and len(cmd) == 2
    assert "SATK_FORK" not in fake_tools[0]["env"] and fake_tools[0]["env"]["TEMP"].endswith("engine-build")
    assert Path(out["log"]).parent == L.logs and "fork" not in out
    assert (L.build_dir / "state.json").is_file()


def test_gen_without_a_checkout_names_the_worktree_command(satk_home):
    wt = _other(satk_home)
    with pytest.raises(SatkError) as e:
        B.gen(layout(wt))
    assert e.value.code == "NOT_READY" and "worktree" in (e.value.hint or "")


# --------------------------------------------------------------------------- build


def _solution(wt: Path) -> None:
    (wt / "Build").mkdir(parents=True, exist_ok=True)
    (wt / "Build" / "MTASA.sln").write_text(
        'Project("{8BC9CEB8-8B4A-11D0-8D11-00A0C91BC942}") = "Core", "Core.vcxproj", "{AAAA0000-0000-0000-0000-000000000002}"\n'
        "EndProject\nGlobal\n\tGlobalSection(ProjectConfigurationPlatforms) = postSolution\n"
        "\t\t{AAAA0000-0000-0000-0000-000000000002}.Release|x64.Build.0 = Release|x64\n\tEndGlobalSection\nEndGlobal\n",
        encoding="utf-8")


def test_build_of_a_second_fork_is_separate_in_every_way(satk_home, fake_tools, monkeypatch):
    L0 = layout()
    L0.targets.parent.mkdir(parents=True, exist_ok=True)
    L0.targets.write_text("<Project/>", encoding="utf-8")
    wt = _other(satk_home)
    _fake_tree(wt)
    _solution(wt)
    seen_locks: list[str] = []
    real = C.exclusive

    def spy(name):
        seen_locks.append(name)
        return real(name)

    monkeypatch.setattr(B, "exclusive", spy)
    out = B.build(project="server", platform="x64", regen="never", fork=str(wt))
    cmd = fake_tools[-1]["cmd"]
    assert f"-p:DirectoryBuildTargetsPath={L0.targets}" in cmd
    assert f"-p:SatkForkRoot={wt}\\" in cmd
    assert cmd[1] == str(wt / "Build" / "MTASA.sln") and "-p:Platform=x64" in cmd
    assert seen_locks == ["build-sae2-srv"]
    assert fake_tools[-1]["env"]["TEMP"].endswith("engine-build-sae2-srv")
    assert out["fork"].endswith("work/wt/sae2-srv") and out["ok"]
    log = Path(out["rows"][0][-1])
    assert log.parent == L0.logs / "sae2-srv" and "-sln-Release-x64" in log.name
    # change tracking is per fork: the build recorded its revision in the fork's own state file
    assert json.loads((L0.build_dir / "state.sae2-srv.json").read_text(encoding="utf-8"))["builds"]["Release|x64"]["rev"]
    assert not (L0.build_dir / "state.json").exists()
    assert out["artifacts"][0][1].startswith("Bin/server/")


def test_build_of_the_configured_fork_keeps_its_command_line(satk_home, fake_tools, monkeypatch):
    L0 = layout()
    L0.targets.parent.mkdir(parents=True, exist_ok=True)
    L0.targets.write_text("<Project/>", encoding="utf-8")
    L0.fork.mkdir(parents=True)
    _fake_tree(L0.fork)
    _solution(L0.fork)
    locks: list[str] = []
    real = C.exclusive
    monkeypatch.setattr(B, "exclusive", lambda n: (locks.append(n), real(n))[1])
    out = B.build(project="server", platform="x64", regen="never")
    cmd = " ".join(fake_tools[-1]["cmd"])
    assert "DirectoryBuildTargetsPath" not in cmd and "SatkForkRoot" not in cmd
    assert locks == ["build"] and "fork" not in out
    assert Path(out["rows"][0][-1]).parent == L0.logs
    assert (L0.build_dir / "state.json").is_file()


def test_changed_tracking_is_per_fork(satk_home):
    L0 = layout()
    a, b = layout(_other(satk_home, "one")), layout(_other(satk_home, "two"))
    B._update_state(a, "builds", {"Release|x64": {"rev": "aaa", "ts": 1.0}})
    B._update_state(b, "builds", {"Release|x64": {"rev": "bbb", "ts": 2.0}})
    assert C.read_json(a.state)["builds"]["Release|x64"]["rev"] == "aaa"
    assert C.read_json(b.state)["builds"]["Release|x64"]["rev"] == "bbb"
    assert C.read_json(L0.state) is None


def test_protected_fork_path_never_reaches_a_tool(satk_home, monkeypatch):
    protected = satk_home / "src" / "checkout"
    protected.mkdir(parents=True)
    (protected / "premake5.lua").write_text("--\n", encoding="utf-8")
    monkeypatch.setattr(B, "run", lambda *a, **k: pytest.fail("a tool was reached"))
    monkeypatch.setattr(B, "find_msbuild", lambda: Path("MSBuild.exe"))
    with pytest.raises(SatkError) as e:
        B.build(project="server", regen="never", fork=str(protected))
    assert e.value.code == "PROTECTED_PATH"
    with pytest.raises(SatkError) as e:
        B.gen(replace(layout(protected)))
    assert e.value.code == "PROTECTED_PATH"


# --------------------------------------------------------------------------- tests, doctor, status


def test_gtest_paths_of_a_second_fork(satk_home, monkeypatch):
    wt = _other(satk_home)
    exe = wt / "Bin" / "tests" / "Tests_Client.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    assert gtest.test_exe_path("Release", layout(wt)) == exe
    assert gtest.test_exe_path("Debug", layout(wt)).name == "Tests_Client_d.exe"
    built: dict = {}
    monkeypatch.setattr(B, "build", lambda **kw: built.update(kw) or {"rows": [["Tests_Client", "Win32", "Release", 0, 1.5]]})
    got: dict = {}

    def fake_run_gtest(cmd, out_json, **kw):
        got.update(cmd=cmd, out=out_json, kw=kw)
        return {"ok": True, "json": str(out_json), "tests": 3, "failed": 0, "rows": []}

    monkeypatch.setattr(gtest, "run_gtest", fake_run_gtest)
    res = gtest.engine_test_run(fork=str(wt))
    assert built["fork"] == str(wt) and built["project"] == "Tests_Client"
    assert got["cmd"] == [str(exe)] and got["out"].parent.name == "sae2-srv" and got["out"].parent.parent.name == "test"
    assert got["kw"]["L"].fork_id == "sae2-srv" and res["exe"].endswith("Bin/tests/Tests_Client.exe")


def test_doctor_and_status_of_a_second_fork(satk_home):
    from satk.engine import doctor as D

    wt = _other(satk_home)
    L = layout(wt)
    res = {r["check"]: r for r in D.run_checks(L=L)}
    assert res["fork"]["status"] == "fail" and "no fork checkout" in res["fork"]["msg"]
    assert D.status(L=L)["fork"]["exists"] is False
    st = D.status(L=L)
    assert st["fork_id"] == "sae2-srv" and st["profile"] == "full"
    # the server profile expects the server and the test binary only, no client dependencies
    L.meta.parent.mkdir(parents=True)
    L.meta.write_text(json.dumps({"profile": "server"}), encoding="utf-8")
    res = {r["check"]: r for r in D.run_checks(L=L)}
    assert res["dxfiles"]["status"] == "skip"
    assert res["outputs"]["msg"].startswith("0/4") or "key outputs built" in res["outputs"]["msg"]
    assert [r[1] for r in B.artifacts(L)] == ["Bin/tests/Tests_Client.exe", "Bin/server/MTA Server64.exe",
                                              "Bin/server/x64/core.dll", "Bin/server/x64/deathmatch.dll"]
    assert len(B.artifacts(layout())) == 10  # the configured fork: unchanged list (client + server)


def test_sites_scan_accepts_fork_and_echoes_it(satk_home, monkeypatch):
    import satk.engine.sites_run as SR

    monkeypatch.setattr(SR, "run_scan", lambda *a, **k: {"ok": True, "rows": []})
    wt = _other(satk_home)
    out = get_op("engine.sites_scan").call({"base": 1, "size": 4, "stride": 4, "fork": str(wt)})
    assert out["fork"].endswith("work/wt/sae2-srv")
    assert "fork" not in get_op("engine.sites_scan").call({"base": 1, "size": 4, "stride": 4})


# --------------------------------------------------------------------------- sparse (server profile) planning

_SLN3 = """
Microsoft Visual Studio Solution File, Format Version 12.00
Project("{8BC9CEB8-8B4A-11D0-8D11-00A0C91BC942}") = "Launcher", "Launcher.vcxproj", "{AAAA0000-0000-0000-0000-000000000001}"
EndProject
Project("{8BC9CEB8-8B4A-11D0-8D11-00A0C91BC942}") = "Core", "Core.vcxproj", "{AAAA0000-0000-0000-0000-000000000002}"
EndProject
Project("{8BC9CEB8-8B4A-11D0-8D11-00A0C91BC942}") = "zlib", "zlib.vcxproj", "{AAAA0000-0000-0000-0000-000000000003}"
EndProject
Project("{8BC9CEB8-8B4A-11D0-8D11-00A0C91BC942}") = "jpeg", "jpeg.vcxproj", "{AAAA0000-0000-0000-0000-000000000004}"
EndProject
Project("{8BC9CEB8-8B4A-11D0-8D11-00A0C91BC942}") = "Tests_Client", "Tests_Client.vcxproj", "{AAAA0000-0000-0000-0000-000000000005}"
EndProject
Global
	GlobalSection(ProjectConfigurationPlatforms) = postSolution
		{AAAA0000-0000-0000-0000-000000000001}.Release|x64.Build.0 = Release|x64
		{AAAA0000-0000-0000-0000-000000000002}.Release|x64.Build.0 = Release|x64
		{AAAA0000-0000-0000-0000-000000000003}.Release|x64.Build.0 = Release|x64
		{AAAA0000-0000-0000-0000-000000000003}.Release|Win32.Build.0 = Release|Win32
		{AAAA0000-0000-0000-0000-000000000004}.Release|x64.Build.0 = Release|x64
		{AAAA0000-0000-0000-0000-000000000005}.Release|Win32.Build.0 = Release|Win32
	EndGlobalSection
EndGlobal
"""


def _sparse_fork(satk_home: Path, projects=("Launcher", "Core", "zlib", "Tests_Client")) -> "C.Layout":
    wt = _other(satk_home)
    (wt / "Build").mkdir(exist_ok=True)
    (wt / "Build" / "MTASA.sln").write_text(_SLN3, encoding="utf-8")
    kinds = {"Launcher": "Application", "Core": "DynamicLibrary", "zlib": "StaticLibrary", "jpeg": "StaticLibrary",
             "Tests_Client": "Application"}
    for n, k in kinds.items():
        (wt / "Build" / f"{n}.vcxproj").write_text(
            '<Project xmlns="http://schemas.microsoft.com/developer/msbuild/2003"><PropertyGroup>'
            f"<ConfigurationType>{k}</ConfigurationType></PropertyGroup></Project>", encoding="utf-8")
    L = layout(wt)
    L.meta.parent.mkdir(parents=True, exist_ok=True)
    L.meta.write_text(json.dumps({"profile": "server", "projects": list(projects)}), encoding="utf-8")
    return L


def test_sparse_fork_builds_its_own_executables_not_the_whole_solution(satk_home):
    L = _sparse_fork(satk_home)
    steps, notes = B.plan("server", None, "Release", L)
    # the solution also holds jpeg (no sources in this checkout); the roots of the present projects are built,
    # their static libraries (zlib) follow through the project references
    assert [(s.label, s.platform, s.covers) for s in steps] == [("Core", "x64", True), ("Launcher", "x64", True)]
    assert all(s.target.suffix == ".vcxproj" for s in steps)
    steps, _ = B.plan("all", None, "Release", L)
    assert [(s.label, s.platform) for s in steps] == [("Tests_Client", "Win32"), ("Core", "x64"), ("Launcher", "x64")]
    steps, _ = B.plan("Tests_Client", None, "Release", L)
    assert [(s.label, s.platform) for s in steps] == [("Tests_Client", "Win32")]
    with pytest.raises(SatkError) as e:
        B.plan("client", None, "Release", L)
    assert e.value.code == "BAD_PARAMS" and "profile" in e.value.msg


def test_full_checkout_still_builds_the_solution(satk_home):
    L = _sparse_fork(satk_home)
    L.meta.write_text(json.dumps({"profile": "full"}), encoding="utf-8")
    steps, _ = B.plan("server", None, "Release", L)
    assert [(s.label, s.platform, s.covers) for s in steps] == [("sln", "x64", False)]
    L.meta.write_text(json.dumps({"profile": "server"}), encoding="utf-8")  # no project list recorded: solution
    steps, _ = B.plan("server", None, "Release", L)
    assert [s.label for s in steps] == ["sln"]
    d = layout()
    d.sln.parent.mkdir(parents=True)
    d.sln.write_text(_SLN3, encoding="utf-8")
    assert [(s.label, s.platform) for s in B.plan("server", None, "Release", d)[0]] == [("sln", "x64")]


def test_changed_in_a_sparse_fork_records_the_covered_platform(satk_home, fake_tools):
    L = _sparse_fork(satk_home)
    (L.fork / "premake5.lua").write_text("--\n", encoding="utf-8")
    out = B.build(project="server", platform="x64", regen="never", fork=str(L.fork))
    assert out["ok"] and [r[0] for r in out["rows"]] == ["Core", "Launcher"]
    assert C.read_json(L.state)["builds"]["Release|x64"]["rev"]
