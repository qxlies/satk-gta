"""Operations of satk.engine (owner WP-11, SPEC §4.6 group engine, §4.12).

CLI: ``satk engine doctor|status|setup|rc-test|gen|build|server-smoke``.
MCP: one tool ``engine`` (``satk engine run <cmd> --args '<json>'``, cmd = status|doctor|build;
WP-15 extends it). Module-level imports are stdlib only; the work lives in the sibling modules.
"""

from __future__ import annotations

from typing import Any, Literal

from ..core.envelope import table
from ..core.errors import SatkError
from ..core.registry import get_op, op
from . import doctor as _doctor  # noqa: F401 - registers @doctor_check("engine") and @status_provider("engine")


@op("engine.doctor", summary="Check the MTA fork toolchain: checkout, remotes, shims, MSBuild/rc/premake, pinned deps.",
    summary_ru="Проверить тулчейн форка MTA: checkout, remotes, шимы, MSBuild/rc/premake, зафиксированные зависимости.",
    mcp=False, examples=("satk engine doctor", "satk engine doctor --deep --table"))
def engine_doctor(deep: bool = False) -> dict:
    """Run all engine checks.

    Args:
        deep: also re-hash pinned dependencies and ask MSBuild for its version (slower).
    """
    res = _doctor.run_checks(deep=deep)
    s = _doctor.summary(res)
    env = table(["check", "status", "msg", "fix"], [[r["check"], r["status"], r["msg"], r["fix"]] for r in res])
    env["status"] = s["status"]
    env["summary"] = s["msg"]
    return env


@op("engine.status", summary="Show the MTA fork state: branch/revision, last builds, pinned deps, outputs.",
    summary_ru="Состояние форка MTA: ветка/ревизия, последние сборки, зависимости, артефакты.",
    mcp=False, examples=("satk engine status",))
def engine_status(deep: bool = False) -> dict:
    """Compact engine state.

    Args:
        deep: include the summary of the deep doctor checks.
    """
    return _doctor.status(deep=deep)


@op("engine.setup",
    summary="Set up engine/mtasa offline from src/mtasa-neon (remotes push DISABLED), shims, premake wrapper; "
            "--deps also fetches client deps (consent D3), sha256-pinned in deps-lock.json.",
    summary_ru="Настроить engine/mtasa офлайн из src/mtasa-neon, шимы, обёртку premake; --deps — зависимости "
               "клиента (согласие D3) с sha256 в deps-lock.json.",
    mcp=False, long_running=True,
    examples=("satk engine setup", "satk engine setup --deps", "satk engine setup --deps --offline"))
def engine_setup(deps: bool = False, offline: bool = False, update_pins: bool = False,
                 reuse_from: list[str] | None = None) -> dict:
    """Idempotent setup of <workspace>/engine.

    Args:
        deps: fetch and install build/runtime dependencies (DXFiles, CEF, discord-rpc, rapidjson,
            Unifont, net.dll/net_64.dll/netc.dll); reuses verified local copies first.
        offline: never download; use only the store and reusable local copies.
        update_pins: accept new sha256 for trust-on-first-use files (net*.dll) that changed on the CDN.
        reuse_from: extra directories (mtasa-blue trees) to take verified copies from.
    """
    from .setup import setup

    return setup(deps=deps, offline=offline, update_pins=update_pins, reuse_from=reuse_from)


@op("engine.rc_test", summary="Compile the 4 client .rc files with rc.exe and the afxres.h shim (no MFC needed).",
    summary_ru="Скомпилировать 4 клиентских .rc через rc.exe с шимом afxres.h (MFC не нужен).",
    mcp=False, examples=("satk engine rc-test", "satk engine rc-test --control"))
def engine_rc_test(control: bool = False) -> dict:
    """V5 inside the fork.

    Args:
        control: also compile loader.rc without the shim and expect the afxres.h error.
    """
    from .build import rc_test

    return rc_test(control=control)


@op("engine.gen", summary="Generate Build/MTASA.sln with the fork's premake5 vs2026 (DXSDK_DIR = engine/deps/DXFiles).",
    summary_ru="Сгенерировать Build/MTASA.sln через premake5 vs2026 форка (DXSDK_DIR = engine/deps/DXFiles).",
    mcp=False, examples=("satk engine gen",))
def engine_gen() -> dict:
    """Run premake (about 5 s); ``engine build`` also does it when the tree changed."""
    from .build import gen

    return gen()


@op("engine.build",
    summary="Build the MTA fork with MSBuild. project: server|client|all|changed or a vcxproj name "
            "('Game SA'); returns per-step exit/time and errors as file,line,code,msg.",
    summary_ru="Собрать форк MTA через MSBuild: server|client|all|changed или имя проекта; ошибки — "
               "file,line,code,msg.",
    mcp=False, long_running=True,
    examples=("satk engine build --project server --platform x64", "satk engine build --project \"Game SA\"",
              "satk engine build --project changed", "satk engine build --project Loader --target ResourceCompile"))
def engine_build(project: str = "all", platform: Literal["Win32", "x64"] | None = None,
                 config: Literal["Release", "Debug", "Nightly"] = "Release", target: str | None = None,
                 jobs: int | None = None, toolset: str | None = None, no_deps: bool = False,
                 regen: Literal["auto", "always", "never"] = "auto", max_errors: int = 30) -> dict:
    """MSBuild without vswhere; logs in work/engine/build/logs.

    Args:
        project: alias (server = x64 solution, client = Win32 solution, all = solution for both
            platforms, changed = what changed since the last successful build) or a project name.
        platform: Win32 or x64 (default: derived from the project/alias).
        config: build configuration.
        target: MSBuild target (-t:), e.g. ResourceCompile, Rebuild, Clean.
        jobs: parallel MSBuild nodes and cl.exe processes (default: all cores).
        toolset: PlatformToolset override, e.g. v143 (fallback to MSVC 14.44).
        no_deps: with a project name, do not build referenced projects.
        regen: regenerate projects with premake when the tree changed (auto), always or never.
        max_errors: errors to return (the log has all).
    """
    from .build import build

    if max_errors < 1 or max_errors > 500:
        raise SatkError("BAD_PARAMS", "max_errors must be in 1..500")
    return build(project=project, platform=platform, config=config, target=target, jobs=jobs, toolset=toolset,
                 no_deps=no_deps, regen=regen, max_errors=max_errors)


@op("engine.server_smoke",
    summary="Start the built x64 server on 127.0.0.1 (ports 22103/22105, ase 0, no LAN broadcast), "
            "wait for ready, shut it down.",
    summary_ru="Запустить собранный сервер x64 на 127.0.0.1 (порты 22103/22105), дождаться готовности, остановить.",
    mcp=False, examples=("satk engine server-smoke",))
def engine_server_smoke(timeout: float = 60, port: int = 22103, httpport: int = 22105) -> dict:
    """Loopback smoke run of ``MTA Server64.exe --child-process`` (start, wait until ready, shut down).

    Args:
        timeout: seconds to wait for "Server started and is ready to accept connections!".
        port: game port (UDP).
        httpport: HTTP port (TCP).
    """
    from .smoke import server_smoke

    return server_smoke(timeout=timeout, port=port, httpport=httpport)


_RUN = {"status": "engine.status", "doctor": "engine.doctor", "build": "engine.build"}


@op("engine.run", mcp="engine", mcp_group="engine", long_running=True,
    summary="MTA fork engine tool. cmd=status (fork/builds/deps), doctor (toolchain checks), build "
            "(args: project=server|client|all|changed|<vcxproj>, platform, config, target).",
    summary_ru="Инструмент движка MTA: status, doctor, build (args: project, platform, config, target).",
    examples=("satk engine run status", "satk engine run build --args '{\"project\":\"changed\"}'"))
def engine_run(cmd: Literal["status", "doctor", "build"], args: dict[str, Any] | None = None) -> dict:
    """Dispatch to ``engine status|doctor|build`` with JSON arguments.

    Args:
        args: e.g. {"project": "Game SA", "platform": "Win32"}.
    """
    spec = get_op(_RUN[cmd])
    return spec.call(args or {})
