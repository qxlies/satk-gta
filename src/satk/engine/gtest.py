"""``satk engine test``: build the fork's googletest projects and run them, failures come back as rows.

Two projects: ``Tests_Client`` (Win32, ``Tests/client``) and ``Tests_Satk`` (``Tests/satk``, x86 and x64: every
sa-engine header compiled per architecture, the ``Satk_*`` tests, the POD/handle interface rule). ``suite=auto``
(the default) runs ``Tests_Client`` and, when the fork has ``Tests/satk``, ``Tests_Satk`` on both architectures.
The output is JSON (``--gtest_output=json:<file>``); reports are kept in ``work/engine/test/<timestamp>.json``.
Stdlib only.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..core.errors import SatkError
from ..core.paths import cfg, ensure_writable, jpath
from .common import Layout, build_env, layout, run, stamp

__all__ = ["parse_gtest_json", "run_gtest", "test_exe_path", "plan_runs", "engine_test_run", "FAIL_COLS", "SUITES"]

FAIL_COLS = ["suite", "test", "file", "line", "message"]
_LOC = re.compile(r"^(?P<file>.+?):(?P<line>\d+)\s*$")
_MAX_ROWS = 50
_MSG_CHARS = 300
SUITES = ("auto", "client", "satk", "all")
_SATK_PREMAKE = ("Tests", "satk", "premake5.lua")


def test_exe_path(config: str = "Release", L: Layout | None = None, project: str = "Tests_Client",
                  platform: str = "Win32") -> Path:
    """``Bin/tests/<project>.exe`` of the fork (x64: ``Bin/tests/x64/``; Debug adds ``_d``)."""
    name = f"{project}_d.exe" if config == "Debug" else f"{project}.exe"
    folder = (L or layout()).bin / "tests"
    return folder / "x64" / name if platform == "x64" else folder / name


def plan_runs(L: Layout, suite: str = "auto", platform: str | None = None) -> list[tuple[str, str]]:
    """``[(project, platform), ...]`` for ``suite`` (auto|client|satk|all) in the checkout ``L``.

    ``Tests_Client`` exists for Win32 only. ``Tests_Satk`` runs on the platforms asked for (``None`` = both).
    ``auto`` includes ``Tests_Satk`` when the checkout has ``Tests/satk/premake5.lua``; ``satk`` and ``all``
    require it."""
    if suite not in SUITES:
        raise SatkError("BAD_PARAMS", f"unknown suite {suite!r}", did_you_mean=list(SUITES))
    plats = ["Win32", "x64"] if platform in (None, "", "both") else [platform]
    for p in plats:
        if p not in ("Win32", "x64"):
            raise SatkError("BAD_PARAMS", f"unknown platform {platform!r}", did_you_mean=["Win32", "x64", "both"])
    has_satk = L.fork.joinpath(*_SATK_PREMAKE).is_file()
    if suite in ("satk", "all") and not has_satk:
        raise SatkError("NOT_READY", f"{jpath(L.fork)} has no Tests/satk (project Tests_Satk)",
                        hint="merge the branch feat/sae2-core-x64 of the fork, or use --suite client")
    runs: list[tuple[str, str]] = []
    if suite in ("auto", "client", "all"):
        if platform in (None, "", "both", "Win32"):
            runs.append(("Tests_Client", "Win32"))
        elif suite == "client":
            raise SatkError("BAD_PARAMS", "Tests_Client is Win32 only", hint="--suite satk --platform x64")
    if suite in ("satk", "all") or (suite == "auto" and has_satk):
        runs += [("Tests_Satk", p) for p in plats]
    return runs


def _split_failure(text: str, default_file: str, default_line) -> tuple[str, int | None, str]:
    """``("file", line, "message")`` from a gtest failure text (first line is ``file:line``)."""
    first, _, rest = text.partition("\n")
    m = _LOC.match(first.strip())
    if m:
        return m.group("file").replace("\\", "/"), int(m.group("line")), rest.strip()
    return (default_file or ""), (int(default_line) if isinstance(default_line, int) else None), text.strip()


def parse_gtest_json(doc: dict) -> tuple[dict, list[list]]:
    """``(counts, failure rows)`` from a googletest JSON report.

    Rows: ``[suite, test, file, line, message]``, one per failed assertion; a failed test without an
    assertion text (crash, timeout recorded as failure) gets one row with its own file and line.
    """
    rows: list[list] = []
    run_n = 0
    skipped = 0
    for suite in doc.get("testsuites", []) or []:
        for case in suite.get("testsuite", []) or []:
            if case.get("status") == "NOTRUN" or case.get("result") == "SUPPRESSED":
                skipped += 1
                continue
            if case.get("result") == "SKIPPED":
                skipped += 1
                continue
            run_n += 1
            fails = case.get("failures") or []
            for f in fails:
                file, line, msg = _split_failure(str(f.get("failure", "")), str(case.get("file", "")), case.get("line"))
                rows.append([str(suite.get("name", "")), str(case.get("name", "")), file, line,
                             msg[:_MSG_CHARS]])
    counts = {"tests": int(doc.get("tests", run_n)), "failed": int(doc.get("failures", 0)),
              "errors": int(doc.get("errors", 0)), "disabled": int(doc.get("disabled", 0)), "skipped": skipped}
    return counts, rows


def _stock_exe_env() -> dict:
    """``SAE_STOCK_EXE`` for the exe-identity test: the clean copy's ``gta_sa.exe`` (read only), when it exists."""
    try:
        exe = Path(cfg().paths.workspace) / "gta-sa-clean" / "gta_sa.exe"
    except Exception:  # noqa: BLE001 - optional convenience
        return {}
    return {"SAE_STOCK_EXE": str(exe)} if exe.is_file() else {}


def run_gtest(cmd_prefix: list[str], out_json: Path, *, test_filter: str | None = None, timeout: float = 900,
              cwd: Path | None = None, L: Layout | None = None, env_extra: dict | None = None) -> dict:
    """Run a googletest binary (``cmd_prefix``) and return the parsed result.

    Returns ``{"ok", "exit", "seconds", "json", "tests", "failed", ..., "cols", "rows", "n", "total"}``;
    ``ok`` is False when any test failed, the process crashed or no report was written.
    """
    ensure_writable(out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.unlink(missing_ok=True)
    cmd = list(cmd_prefix) + [f"--gtest_output=json:{out_json}"]
    if test_filter:
        cmd.append(f"--gtest_filter={test_filter}")
    r = run(cmd, cwd=cwd, env=(build_env(env_extra) if L is None or L.is_default else build_env(env_extra, L=L)), timeout=timeout)
    rows: list[list] = []
    counts = {"tests": 0, "failed": 0, "errors": 0, "disabled": 0, "skipped": 0}
    problem = None
    if r.timed_out:
        problem = f"test run killed after {timeout:.0f}s"
    elif not out_json.is_file():
        problem = f"no test report written (exit code {r.code}); last output: {r.out[-300:].strip()}"
    else:
        try:
            doc = json.loads(out_json.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as e:
            problem = f"unreadable test report: {e}"
        else:
            counts, rows = parse_gtest_json(doc)
    total = len(rows)
    if problem:
        rows.insert(0, ["(process)", "(run)", "", None, problem[:_MSG_CHARS]])
        total += 1
    elif r.code != 0 and not rows:
        rows.append(["(process)", "(run)", "", None, f"exit code {r.code} but no failed assertion in the report"])
        total += 1
    ok = not rows
    return {"ok": ok, "exit": r.code, "seconds": r.seconds, "json": jpath(out_json), **counts,
            "cols": FAIL_COLS, "rows": rows[:_MAX_ROWS], "n": min(len(rows), _MAX_ROWS), "total": total, "next": None}


def _run_one(project: str, plat: str, *, L: Layout, fork: str | None, build_first: bool, config: str,
             test_filter: str | None, timeout: float, multi: bool) -> dict:
    """Build and run one test project on one platform; the result of :func:`run_gtest` plus ``project``/``platform``."""
    from . import build as _build

    built = None
    fork_kw = {} if fork is None else {"fork": fork}
    if build_first:
        built = _build.build(project=project, platform=plat, config=config, **fork_kw)
    if project == "Tests_Client":
        exe = test_exe_path(config) if fork is None else test_exe_path(config, L)
    else:
        exe = test_exe_path(config, L, project, plat)
    if not exe.is_file():
        raise SatkError("NOT_READY", f"test binary not found: {jpath(exe)}",
                        hint=f"satk engine build --project {project} --platform {plat}"
                        + ("" if fork is None else f" --fork {jpath(L.fork)}"))
    report_dir = L.build_dir.parent / "test"
    base = report_dir if L.is_default else report_dir / L.fork_id
    tag = "" if (project == "Tests_Client" and not multi) else f"-{project.split('_')[-1].lower()}-{plat.lower()}"
    out_json = base / f"{stamp()}{tag}.json"
    res = run_gtest([str(exe)], out_json, test_filter=test_filter, timeout=timeout, cwd=exe.parent,
                    env_extra=_stock_exe_env() or None, **({} if L.is_default else {"L": L}))
    res["exe"] = jpath(exe)
    res["project"] = project
    res["platform"] = plat
    if built is not None:
        res["build_seconds"] = round(sum(float(r[4]) for r in built.get("rows", []) if len(r) > 4), 1)
    return res


def engine_test_run(build_first: bool = True, config: str = "Release", test_filter: str | None = None,
                    timeout: float = 900, fork: str | None = None, suite: str = "auto",
                    platform: str | None = None) -> dict:
    """Build the test projects with the engine build op and run them; raise ``CHECK_FAILED`` on failures.

    ``suite``: ``client`` = ``Tests_Client`` (Win32, the original behaviour), ``satk`` = ``Tests_Satk`` (x86 and x64),
    ``all`` = both, ``auto`` = ``client`` plus ``satk`` when the checkout has ``Tests/satk``. ``platform`` limits
    ``Tests_Satk`` to ``Win32`` or ``x64`` (default both).
    ``fork``: another checkout of the fork (``--fork PATH``); its reports go to ``work/engine/test/<fork-id>/``."""
    L = layout() if fork is None else layout(fork)
    runs = plan_runs(L, suite, platform)
    results = [_run_one(project, plat, L=L, fork=fork, build_first=build_first, config=config, test_filter=test_filter,
                        timeout=timeout, multi=len(runs) > 1) for project, plat in runs]
    if len(results) == 1:
        res = results[0]
    else:
        rows: list[list] = []
        for r in results:
            label = f"{r['project']} {r['platform']}: "
            rows += [[label + str(row[0])] + list(row[1:]) for row in r["rows"]]
        total = sum(int(r.get("total", len(r["rows"]))) for r in results)
        res = {"ok": all(r["ok"] for r in results), "exit": next((r["exit"] for r in results if r["exit"]), 0),
               "seconds": round(sum(float(r["seconds"]) for r in results), 2), "json": results[0]["json"],
               **{k: sum(int(r.get(k, 0)) for r in results) for k in ("tests", "failed", "errors", "disabled", "skipped")},
               "cols": FAIL_COLS, "rows": rows[:_MAX_ROWS], "n": min(len(rows), _MAX_ROWS), "total": total, "next": None,
               "exe": results[0]["exe"],
               "runs": [{k: r.get(k) for k in ("project", "platform", "ok", "tests", "failed", "seconds", "json", "exe")}
                        for r in results]}
        if any("build_seconds" in r for r in results):
            res["build_seconds"] = round(sum(float(r.get("build_seconds", 0)) for r in results), 1)
    if not res["ok"]:
        bad = [f"{r['project']} {r['platform']}" for r in results if not r["ok"]]
        where = f" in {', '.join(bad)}" if len(results) > 1 else ""
        msg = (f"{res['failed']} of {res['tests']} tests failed{where}" if res["tests"] and res["failed"]
               else (res["rows"][0][4] if res["rows"] else "tests failed"))
        data = {k: res[k] for k in ("cols", "rows", "tests", "failed", "total", "exit", "json")}
        if "runs" in res:
            data["runs"] = res["runs"]
        raise SatkError("CHECK_FAILED", msg, hint=f"full report: {res['json']}", data=data)
    return res
