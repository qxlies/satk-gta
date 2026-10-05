"""satk.engine.build: MSBuild log parsing, solution/project parsing, planning (synthetic data)."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from satk.core.errors import SatkError
from satk.engine import build as B
from satk.engine.common import layout

FORK = Path(r"D:\ws\engine\mtasa")


# --------------------------------------------------------------------------- log parsing


def test_parse_compile_link_and_msbuild_errors():
    text = "\n".join([
        r"     1>D:\ws\engine\mtasa\Client\game_sa\CPedSA.cpp(42,7): error C2065: 'foo': undeclared identifier [D:\ws\engine\mtasa\Build\Game SA.vcxproj]",
        r"D:\ws\engine\mtasa\Client\game_sa\CPedSA.cpp(42,7): error C2065: 'foo': undeclared identifier [D:\ws\engine\mtasa\Build\Game SA.vcxproj]",
        r"..\Client\core\CCore.cpp(10): fatal error C1083: Cannot open include file: 'x.h': No such file or directory [D:\x\Build\Client Core.vcxproj]",
        r"LINK : fatal error LNK1104: cannot open file 'core.lib' [D:\x\Build\Client Core.vcxproj]",
        r"CPedSA.obj : error LNK2019: unresolved external symbol _bar referenced in function _baz [D:\x\Build\Game SA.vcxproj]",
        r"C:\Program Files\MSBuild\Microsoft.Common.targets(5,5): error MSB3073: The command exited with code 1. [D:\x\Build\Loader.vcxproj]",
        r"D:\ws\engine\mtasa\Server\core\CServerImpl.cpp(77,5): warning C4267: 'argument': conversion from 'size_t' to 'int' [D:\x\Build\Core.vcxproj]",
        "Build succeeded.",
        "    0 Warning(s)",
    ])
    errs, warns = B.parse_msbuild_log(text, FORK)
    assert [e.code for e in errs] == ["C2065", "C1083", "LNK1104", "LNK2019", "MSB3073"]
    first = errs[0]
    assert first.file == "Client/game_sa/CPedSA.cpp" and first.line == 42 and first.project == "Game SA"
    assert errs[1].file == "Client/core/CCore.cpp" and errs[1].line == 10
    assert errs[2].file == "LINK" and errs[2].line is None
    assert len(warns) == 1 and warns[0].code == "C4267" and warns[0].file == "Server/core/CServerImpl.cpp"
    assert first.row()[:3] == ["Client/game_sa/CPedSA.cpp", 42, "C2065"]


def test_parse_ignores_noise():
    errs, warns = B.parse_msbuild_log("Time Elapsed 00:00:01.00\nerror count: 0\n  0 Error(s)\n")
    assert errs == [] and warns == []


# --------------------------------------------------------------------------- solution / vcxproj

_SLN = """
Microsoft Visual Studio Solution File, Format Version 12.00
Project("{8BC9CEB8-8B4A-11D0-8D11-00A0C91BC942}") = "Game SA", "Game SA.vcxproj", "{AAAA0000-0000-0000-0000-000000000001}"
EndProject
Project("{8BC9CEB8-8B4A-11D0-8D11-00A0C91BC942}") = "Core", "Core.vcxproj", "{AAAA0000-0000-0000-0000-000000000002}"
EndProject
Project("{8BC9CEB8-8B4A-11D0-8D11-00A0C91BC942}") = "Shared", "Shared.vcxproj", "{AAAA0000-0000-0000-0000-000000000003}"
EndProject
Project("{2150E333-8FDC-42A3-9474-1A3956D46DE8}") = "Client", "Client", "{BBBB0000-0000-0000-0000-000000000009}"
EndProject
Global
	GlobalSection(ProjectConfigurationPlatforms) = postSolution
		{AAAA0000-0000-0000-0000-000000000001}.Release|Win32.ActiveCfg = Release|Win32
		{AAAA0000-0000-0000-0000-000000000001}.Release|Win32.Build.0 = Release|Win32
		{AAAA0000-0000-0000-0000-000000000001}.Release|x64.ActiveCfg = Release|x64
		{AAAA0000-0000-0000-0000-000000000002}.Release|x64.ActiveCfg = Release|x64
		{AAAA0000-0000-0000-0000-000000000002}.Release|x64.Build.0 = Release|x64
		{AAAA0000-0000-0000-0000-000000000003}.Release|Win32.Build.0 = Release|Win32
		{AAAA0000-0000-0000-0000-000000000003}.Release|x64.Build.0 = Release|x64
	EndGlobalSection
EndGlobal
"""

_VCX = """<?xml version="1.0" encoding="utf-8"?>
<Project DefaultTargets="Build" ToolsVersion="4.0" xmlns="http://schemas.microsoft.com/developer/msbuild/2003">
  <PropertyGroup Label="Configuration"><ConfigurationType>{kind}</ConfigurationType></PropertyGroup>
  <ItemGroup>
    {items}
  </ItemGroup>
</Project>
"""


def _write_build(fork: Path) -> None:
    b = fork / "Build"
    b.mkdir(parents=True, exist_ok=True)
    (b / "MTASA.sln").write_text(_SLN, encoding="utf-8")
    (b / "Game SA.vcxproj").write_text(_VCX.format(kind="DynamicLibrary", items=(
        r'<ClCompile Include="..\Client\game_sa\CPedSA.cpp" /><ClInclude Include="..\Client\game_sa\CPedSA.h" />')),
        encoding="utf-8")
    (b / "Core.vcxproj").write_text(_VCX.format(kind="DynamicLibrary", items=(
        r'<ClCompile Include="..\Server\core\CServerImpl.cpp" /><ResourceCompile Include="..\Server\core\core.rc" />')),
        encoding="utf-8")
    (b / "Shared.vcxproj").write_text(_VCX.format(kind="StaticLibrary", items=(
        r'<ClCompile Include="..\Shared\sdk\SharedUtil.cpp" />')), encoding="utf-8")


def test_parse_sln(tmp_path):
    p = tmp_path / "MTASA.sln"
    p.write_text(_SLN, encoding="utf-8")
    sln = B.parse_sln(p)
    assert set(sln) == {"Game SA", "Core", "Shared"}  # solution folders are skipped
    assert sln["Game SA"]["builds"] == {"Release|Win32"}
    assert sln["Core"]["builds"] == {"Release|x64"}
    assert sln["Shared"]["path"] == "Shared.vcxproj"


def test_project_index(tmp_path):
    fork = tmp_path / "mtasa"
    _write_build(fork)
    files, kinds = B.project_index(fork / "Build")
    assert files["client/game_sa/cpedsa.cpp"] == {"Game SA"}
    assert files["server/core/core.rc"] == {"Core"}
    assert "client/game_sa/cpedsa.h" not in files  # headers are not compile items
    assert kinds == {"Game SA": "DynamicLibrary", "Core": "DynamicLibrary", "Shared": "StaticLibrary"}


# --------------------------------------------------------------------------- planning


@pytest.fixture
def fake_fork(satk_home):
    """A tiny git repository laid out like the fork, at the configured engine path."""
    L = layout()
    fork = L.fork
    for rel in ("Client/game_sa/CPedSA.cpp", "Client/game_sa/CPedSA.h", "Server/core/CServerImpl.cpp",
                "Shared/sdk/SharedUtil.cpp", "README.md"):
        f = fork / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(f"// {rel}\n", encoding="utf-8")
    (fork / ".gitignore").write_text("Build/\nBin/\n", encoding="utf-8")
    _write_build(fork)

    def g(*a):
        subprocess.run(["git", *a], cwd=fork, check=True, capture_output=True)

    g("init", "-q", "-b", "main")
    g("config", "user.email", "t@example.invalid")
    g("config", "user.name", "t")
    g("config", "core.autocrlf", "false")
    g("add", "-A")
    g("commit", "-q", "-m", "init")
    return L


def test_plan_aliases(fake_fork):
    L = fake_fork
    steps, _ = B.plan("all", None, "Release", L)
    assert [(s.label, s.platform) for s in steps] == [("sln", "Win32"), ("sln", "x64")]
    steps, _ = B.plan("server", None, "Release", L)
    assert [(s.label, s.platform) for s in steps] == [("sln", "x64")]
    steps, _ = B.plan("client", None, "Release", L)
    assert [(s.label, s.platform) for s in steps] == [("sln", "Win32")]
    with pytest.raises(SatkError) as e:
        B.plan("client", "x64", "Release", L)
    assert e.value.code == "BAD_PARAMS"
    steps, notes = B.plan("server", "Win32", "Release", L)
    assert steps[0].platform == "Win32" and notes["warn"]


def test_plan_named_project(fake_fork):
    L = fake_fork
    steps, _ = B.plan("game sa", None, "Release", L)  # case-insensitive
    assert [(s.label, s.platform, s.target.name) for s in steps] == [("Game SA", "Win32", "Game SA.vcxproj")]
    steps, _ = B.plan("Game_SA", None, "Release", L)  # underscores/spaces ignored
    assert steps[0].label == "Game SA"
    steps, _ = B.plan("Core", None, "Release", L)
    assert [s.platform for s in steps] == ["x64"]
    steps, notes = B.plan("Core", "Win32", "Release", L)
    assert steps[0].platform == "Win32" and "not built" in notes["warn"][0]
    with pytest.raises(SatkError) as e:
        B.plan("Game SAA", None, "Release", L)
    assert e.value.code == "NOT_FOUND" and "Game SA" in e.value.did_you_mean


def test_plan_changed(fake_fork):
    L = fake_fork
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=L.fork, capture_output=True, text=True).stdout.strip()
    # no previous build -> whole solution
    steps, notes = B.plan("changed", None, "Release", L, state={})
    assert {(s.label, s.platform) for s in steps} == {("sln", "Win32"), ("sln", "x64")}
    ts = time.time() - 5
    state = {"builds": {"Release|Win32": {"rev": head, "ts": ts}, "Release|x64": {"rev": head, "ts": ts}}}
    steps, notes = B.plan("changed", None, "Release", L, state=state)
    assert steps == [] and "nothing changed" in notes["changed"]["Win32"]
    # a client source -> only that project, Win32
    (L.fork / "Client/game_sa/CPedSA.cpp").write_text("// changed\n", encoding="utf-8")
    steps, notes = B.plan("changed", None, "Release", L, state=state)
    assert [(s.label, s.platform) for s in steps] == [("Game SA", "Win32")]
    assert "nothing changed" in notes["changed"]["x64"]
    # a header -> whole solution of its platform
    (L.fork / "Client/game_sa/CPedSA.h").write_text("// changed\n", encoding="utf-8")
    steps, _ = B.plan("changed", None, "Release", L, state=state)
    assert [(s.label, s.platform) for s in steps] == [("sln", "Win32")]
    subprocess.run(["git", "checkout", "--", "."], cwd=L.fork, check=True)
    # a static library source -> whole solutions (dependents relink)
    (L.fork / "Shared/sdk/SharedUtil.cpp").write_text("// changed\n", encoding="utf-8")
    steps, _ = B.plan("changed", None, "Release", L, state=state)
    assert {(s.label, s.platform) for s in steps} == {("sln", "Win32"), ("sln", "x64")}
    subprocess.run(["git", "checkout", "--", "."], cwd=L.fork, check=True)
    # docs never trigger a build
    (L.fork / "README.md").write_text("changed\n", encoding="utf-8")
    steps, _ = B.plan("changed", None, "Release", L, state=state)
    assert steps == []
    # files untouched since the last build are skipped by mtime even if they differ from rev
    later = {"builds": {k: {"rev": head, "ts": time.time() + 60} for k in ("Release|Win32", "Release|x64")}}
    (L.fork / "Client/game_sa/CPedSA.cpp").write_text("// changed again\n", encoding="utf-8")
    steps, _ = B.plan("changed", None, "Release", L, state=later)
    assert steps == []
    # regenerated projects are rebuilt even without content changes
    steps, notes = B.plan("changed", "Win32", "Release", L, state=later, touched=["Game SA"])
    assert [(s.label, s.platform) for s in steps] == [("Game SA", "Win32")]
    assert "regenerated Game SA" in notes["changed"]["Win32"]


def test_changed_files_lists_untracked(fake_fork):
    L = fake_fork
    (L.fork / "Client/game_sa/new.cpp").write_text("// new\n", encoding="utf-8")
    assert "Client/game_sa/new.cpp" in B.changed_files(L, None)


def test_canon_platform():
    assert B._canon_platform("x86") == "Win32"
    assert B._canon_platform("WIN32") == "Win32"
    assert B._canon_platform("amd64") == "x64"
    assert B._canon_platform(None) is None
    with pytest.raises(SatkError):
        B._canon_platform("arm")


def test_path_platforms():
    assert B._path_platforms("Client/core/CCore.cpp") == ["Win32"]
    assert B._path_platforms("Server/core/CServerImpl.cpp") == ["x64"]
    assert B._path_platforms("Shared/sdk/SharedUtil.cpp") == ["Win32", "x64"]
    assert B._path_platforms("docs/limits.toml") == []
    assert B._path_platforms("CLAUDE.md") == []


def test_artifacts_debug_suffix(satk_home):
    rows = B.artifacts(layout(), "Debug", ("x64",))
    assert rows[0][1] == "Bin/server/MTA Server64_d.exe" and rows[0][2] is False


def test_build_requires_msbuild(satk_home, monkeypatch):
    monkeypatch.setattr(B, "find_msbuild", lambda: None)
    with pytest.raises(SatkError) as e:
        B.build()
    assert e.value.code == "DEPENDENCY"


def test_msbuild_cmd_shape(satk_home):
    L = layout()
    step = B.Step("Game SA", L.fork / "Build" / "Game SA.vcxproj", "Win32")
    log = L.logs / "x.log"
    cmd = B._msbuild_cmd(Path("MSBuild.exe"), step, "Release", "ResourceCompile", 4, "v143", True, log, L)
    assert "-p:Platform=Win32" in cmd and "-t:ResourceCompile" in cmd and "-m:4" in cmd
    assert "-nodeReuse:false" in cmd and "-p:PlatformToolset=v143" in cmd and "-p:BuildProjectReferences=false" in cmd
    assert any(c.startswith("-p:SolutionDir=") and c.endswith("\\") for c in cmd)
    assert any(c.startswith("-flp1:") and "errorsonly" in c for c in cmd)
    sln = B.Step("sln", L.sln, "x64")
    cmd = B._msbuild_cmd(Path("MSBuild.exe"), sln, "Release", None, None, None, False, log, L)
    assert "-m" in cmd and not any(c.startswith("-p:SolutionDir") for c in cmd)


def test_log_names_keep_dotted_project_names(tmp_path):
    lg = B._logs(tmp_path / "20261004-120000-lua5.1-Release-x64")
    assert lg["log"].name == "20261004-120000-lua5.1-Release-x64.log"
    assert lg["errors"].name == "20261004-120000-lua5.1-Release-x64.errors.log"
    assert lg["console"].name == "20261004-120000-lua5.1-Release-x64.console.log"


def test_env_has_temp_on_work(satk_home):
    from satk.engine.common import build_env

    env = build_env()
    assert os.path.normcase(env["TEMP"]).startswith(os.path.normcase(str(satk_home)))
    assert env["MSBUILDDISABLENODEREUSE"] == "1"
    assert env["DXSDK_DIR"].endswith("DXFiles\\")
