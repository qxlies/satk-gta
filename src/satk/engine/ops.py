"""Operations of satk.engine (owner WP-11, SPEC §4.6 group engine, §4.12).

CLI: ``satk engine doctor|status|setup|rc-test|gen|build|server-smoke``.
MCP: one tool ``engine`` (``satk engine run <cmd> --args '<json>'``, cmd = status|doctor|build;
WP-15 extends it). Module-level imports are stdlib only; the work lives in the sibling modules.

Every operation that works on the fork takes ``fork`` (``--fork PATH``): another checkout of the fork, usually a
worktree made by ``engine worktree create``. Without it the configured fork (``[paths] engine``) is used, exactly
as before.
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
def engine_doctor(deep: bool = False, fork: str | None = None) -> dict:
    """Run all engine checks.

    Args:
        deep: also re-hash pinned dependencies and ask MSBuild for its version (slower).
        fork: check another checkout of the fork (a worktree) instead of the configured one.
    """
    if fork is None:
        res = _doctor.run_checks(deep=deep)
    else:
        from .common import layout

        res = _doctor.run_checks(deep=deep, L=layout(fork))
    s = _doctor.summary(res)
    env = table(["check", "status", "msg", "fix"], [[r["check"], r["status"], r["msg"], r["fix"]] for r in res])
    env["status"] = s["status"]
    env["summary"] = s["msg"]
    return env


@op("engine.status", summary="Show the MTA fork state: branch/revision, last builds, pinned deps, outputs.",
    summary_ru="Состояние форка MTA: ветка/ревизия, последние сборки, зависимости, артефакты.",
    mcp=False, examples=("satk engine status",))
def engine_status(deep: bool = False, fork: str | None = None) -> dict:
    """Compact engine state.

    Args:
        deep: include the summary of the deep doctor checks.
        fork: another checkout of the fork (a worktree) instead of the configured one.
    """
    if fork is None:
        return _doctor.status(deep=deep)
    from .common import layout

    return _doctor.status(deep=deep, L=layout(fork))


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
def engine_gen(fork: str | None = None) -> dict:
    """Run premake (about 5 s); ``engine build`` also does it when the tree changed.

    Args:
        fork: generate in another checkout of the fork (a worktree): premake runs through the engine wrapper
            with the dependencies of ``engine/deps`` (offline), the solution goes to ``<fork>/Build``.
    """
    from .build import gen

    if fork is None:
        return gen()
    from .common import layout

    return gen(layout(fork))


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
                 regen: Literal["auto", "always", "never"] = "auto", max_errors: int = 30,
                 fork: str | None = None) -> dict:
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
        fork: build another checkout of the fork (a worktree): its own Build/Bin, logs in
            work/engine/build/logs/<fork-id>/, its own change tracking for `--project changed`.
    """
    from .build import build

    if max_errors < 1 or max_errors > 500:
        raise SatkError("BAD_PARAMS", "max_errors must be in 1..500")
    extra = {} if fork is None else {"fork": fork}
    return build(project=project, platform=platform, config=config, target=target, jobs=jobs, toolset=toolset,
                 no_deps=no_deps, regen=regen, max_errors=max_errors, **extra)


@op("engine.server_smoke",
    summary="Start the built x64 server on 127.0.0.1 (ports 22103/22105, ase 0, no LAN broadcast), "
            "wait for ready, shut it down.",
    summary_ru="Запустить собранный сервер x64 на 127.0.0.1 (порты 22103/22105), дождаться готовности, остановить.",
    mcp=False, examples=("satk engine server-smoke",))
def engine_server_smoke(timeout: float = 60, port: int = 22103, httpport: int = 22105,
                        fork: str | None = None) -> dict:
    """Loopback smoke run of ``MTA Server64.exe --child-process`` (start, wait until ready, shut down).

    Args:
        timeout: seconds to wait for "Server started and is ready to accept connections!".
        port: game port (UDP).
        httpport: HTTP port (TCP).
        fork: run the server built in another checkout of the fork (a worktree).
    """
    from .smoke import server_smoke

    if fork is None:
        return server_smoke(timeout=timeout, port=port, httpport=httpport)
    from .common import layout

    return server_smoke(timeout=timeout, port=port, httpport=httpport, L=layout(fork))


@op("engine.sites_check",
    summary="Check the sa-engine patch-site manifest (docs/sae/patch-sites.toml of the fork) against the stock exe: "
            "schema, ids, golden bytes, SecuROM keys, HOODLUM stolen sites, relocated functions, trunk patches, header.",
    summary_ru="Проверить манифест сайтов патчей sa-engine по стоковому exe: схема, golden-байты, ключи SecuROM, "
               "HOODLUM, релоцированные функции, патчи trunk, свежесть заголовка.",
    mcp=False, long_running=True,
    examples=("satk engine sites-check", "satk engine sites-check --manifest docs/sae/patch-sites.toml"))
def engine_sites_check(fork: str | None = None, manifest: str | None = None) -> dict:
    """Run every check of the patch-site manifest; a problem returns ``CHECK_FAILED`` with the findings as rows.

    Args:
        fork: the engine checkout (default: ``[paths] engine``).
        manifest: manifest file (default: ``<fork>/docs/sae/patch-sites.toml``).
    """
    from .sites_run import run_check

    return run_check(fork, manifest)


@op("engine.sites_gen",
    summary="Write Shared/sdk/satk/generated/SaeSites.gen.h in the engine fork from patch-sites.toml "
            "(deterministic; first line carries the TOML SHA-256, sites-check reports a stale header).",
    summary_ru="Сгенерировать SaeSites.gen.h форка из patch-sites.toml (детерминированно, в первой строке SHA-256 TOML).",
    mcp=False, examples=("satk engine sites-gen",))
def engine_sites_gen(fork: str | None = None, manifest: str | None = None) -> dict:
    """Regenerate the C++ site tables; run it in the same commit as every manifest change.

    Args:
        fork: the engine checkout (default: ``[paths] engine``).
        manifest: manifest file (default: ``<fork>/docs/sae/patch-sites.toml``).
    """
    from .sites_run import run_gen

    return run_gen(fork, manifest)


@op("engine.sites_scan",
    summary="List candidate exe operands of a static array (--base --size --stride) from Ghidra xrefs, classified as "
            "operand-in-array, true-end, end-plus-field or variable-after-array (never patch); flags static-init, in-hoodlum.",
    summary_ru="Найти операнды статического массива в exe (xref Ghidra) и классифицировать: operand-in-array, true-end, "
               "end-plus-field, variable-after-array (не патчить).",
    mcp=False, examples=("satk engine sites-scan --base 0xB748F8 --size 4000 --stride 4",
                         "satk engine sites-scan --base 0xC3E058 --size 0xF00 --stride 0x3C --limit 60 --only operand-in-array"))
def engine_sites_scan(base: int | None = None, size: int | None = None, stride: int | None = None,
                      only: str | None = None, flag: str | None = None, limit: int = 20, offset: int = 0,
                      fork: str | None = None) -> dict:
    """Candidate operands for relocating or growing a static array (window = array end plus one stride).

    Args:
        base: array address (VA), hex allowed.
        size: array size in bytes.
        stride: element size in bytes (the window is extended by one stride).
        only: keep one class: operand-in-array, true-end, end-plus-field, variable-after-array.
        flag: keep candidates carrying a flag: static-init, in-hoodlum, in-relocated-function, unclassified.
        limit: rows to return (default 20, max 500); counts of every class are always returned.
        offset: rows to skip.
        fork: accepted for symmetry with the other engine operations; the scan reads the stock exe and the
            Ghidra export only, so the answer does not depend on it (it is echoed back).
    """
    from .sites_run import run_scan

    out = run_scan(base, size, stride, only=only, limit=limit, offset=offset, flag=flag)
    if fork is not None:
        from ..core.paths import jpath
        from .common import layout

        out["fork"] = jpath(layout(fork).fork)
    return out


@op("engine.test",
    summary="Build the fork's googletest projects with the engine build op and run them with JSON reports in "
            "work/engine/test/: Tests_Client (Win32) and, when the fork has Tests/satk, Tests_Satk on x86 and x64 "
            "(--suite client|satk|all|auto, --platform); failures come back as rows suite,test,file,line,message.",
    summary_ru="Собрать и запустить Tests_Client (Win32) и Tests_Satk (x86 и x64, правило POD-интерфейсов); падения "
               "как строки suite,test,file,line,message.",
    mcp=False, long_running=True,
    examples=("satk engine test", "satk engine test --filter Satk_*", "satk engine test --no-build",
              "satk engine test --suite satk --platform x64"))
def engine_test(build: bool = True, filter: str | None = None, config: Literal["Release", "Debug"] = "Release",
                timeout: float = 900, fork: str | None = None,
                suite: Literal["auto", "client", "satk", "all"] = "auto",
                platform: Literal["Win32", "x64", "both"] | None = None) -> dict:
    """Run the fork's googletest suites.

    Args:
        build: build the test projects first (default); with false the existing binaries are run.
        filter: googletest filter, e.g. ``Satk_*``.
        config: build configuration.
        timeout: seconds before each test process is killed.
        fork: build and run the tests of another checkout of the fork (a worktree).
        suite: ``client`` = Tests_Client (Win32) only; ``satk`` = Tests_Satk (every sa-engine header per
            architecture, the Satk_* tests, the POD/handle interface rule); ``all`` = both; ``auto`` (default) =
            ``client`` plus ``satk`` when the checkout has ``Tests/satk``.
        platform: ``Win32``, ``x64`` or ``both`` (default) for Tests_Satk; Tests_Client is Win32 only.
    """
    from .gtest import engine_test_run

    extra = {} if fork is None else {"fork": fork}
    return engine_test_run(build_first=build, config=config, test_filter=filter, timeout=timeout, suite=suite,
                           platform=platform, **extra)


def _cli_only(reason: str, *, consent: bool = False):
    """``satk.mcp.generic.cli_only`` when the generic MCP access is present, else a no-op."""
    try:
        from satk.mcp.generic import cli_only
    except ImportError:  # pragma: no cover - the mcp package is optional
        return lambda fn: fn
    return cli_only(reason, consent=consent)


@_cli_only("it creates or deletes a checkout of the fork and changes the git metadata of the fork repository",
           consent=True)
@op("engine.worktree",
    summary="Light second checkouts of the MTA fork under work/wt: create (git worktree add + sparse 'server' profile + "
            "pinned net.dll), refresh, list, remove. Use --fork on gen/build/test/status/doctor/server-smoke.",
    summary_ru="Лёгкие вторые чекауты форка MTA в work/wt: create (git worktree add + sparse-профиль server), refresh, list, remove.",
    mcp=False, long_running=True,
    examples=("satk engine worktree create --path <workspace>/work/wt/sae2-srv --branch feat/sae2-srv-base",
              "satk engine worktree list --sizes", "satk engine worktree remove --path <workspace>/work/wt/sae2-srv"))
def engine_worktree(action: Literal["create", "list", "remove", "refresh"], path: str | None = None, branch: str | None = None,
                    base: str = "main", profile: Literal["server", "full"] = "server", fork: str | None = None,
                    force: bool = False, delete_branch: bool = False, sizes: bool = False) -> dict:
    """Manage worktrees of the fork. Only folders directly under ``<work>/wt`` are accepted.

    Args:
        action: create (new branch from `base`, sparse checkout of the profile, deps), refresh, list, or remove.
        path: the worktree folder, `<work>/wt/<name>` (create, refresh, remove).
        branch: new branch for create (`git worktree add -b`); it must not exist yet.
        base: commit or branch the new branch starts at (create).
        profile: `server` = Shared, Server, Tests, the vendor libraries the x64 server and included test projects
            need (computed from the premake files), premake scripts of the rest; `full` = a normal checkout.
        fork: the fork repository to add the worktree to (default: the configured fork).
        force: remove even with uncommitted changes (remove).
        delete_branch: also `git branch -d` the branch that create made (remove; only when it is merged).
        sizes: add file counts and megabytes to the list.
    """
    from . import worktree as _wt

    if action == "create":
        return _wt.create(path or "", branch, base=base, profile=profile, fork=fork)
    if action == "list":
        return _wt.listing(fork=fork, sizes=sizes)
    if action == "refresh":
        return _wt.refresh(path or "", fork=fork)
    return _wt.remove(path or "", force=force, delete_branch=delete_branch, fork=fork)


@_cli_only("it reapplies sparse checkout and changes the git metadata of the fork repository", consent=True)
@op("engine.worktree.refresh", mcp=False, long_running=True,
    summary="Recompute and reapply a managed MTA worktree's recorded sparse profile from its current HEAD. "
            "Preserves local changes; the next build regenerates premake projects.",
    summary_ru="Пересчитать и применить сохранённый разреженный профиль рабочего дерева MTA по текущему HEAD; "
               "сохранить локальные изменения, обновить проекты при следующей сборке.",
    examples=("satk engine worktree refresh <workspace>/work/wt/sae2-srv",))
def engine_worktree_refresh(path: str, fork: str | None = None) -> dict:
    """Refresh a worktree created by ``satk engine worktree create``.

    Args:
        path: existing worktree folder, `<work>/wt/<name>`.
        fork: fork repository owning the worktree (default: the configured fork).
    """
    from .worktree import refresh

    return refresh(path, fork=fork)


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
