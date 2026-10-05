"""``satk doctor``: environment checks (SPEC §4.7, WP-06 acceptance 4).

Checks are plain functions registered with :func:`satk.core.registry.doctor_check`; every
package adds its own (``game_copy`` WP-01, ``index_fresh`` WP-03, ``engine`` WP-11, ...).
This module registers the environment checks ``python venv deps utf8 paths disk network git blender
msbuild premake mcp_config mcp_groups index``. A name already taken by its owning package (registered
first) is skipped here, so the owner's version wins and nothing breaks at import.

:func:`run` collects all checks (``ops_import`` from the core included) in a stable order.
"""

from __future__ import annotations

import contextvars
import datetime as _dt
import importlib.metadata as _md
import json
import os
import platform
import sys
import time
from pathlib import Path
from typing import Callable

from satk.core.config import DETECTED_PATHS, MAIN_ROOT, REPO_ROOT
from satk.core.errors import SatkError
from satk.core.log import get_logger, record_exception
from satk.core.paths import cfg, jpath
from satk.core.registry import doctor_check, doctor_checks

from .sysinfo import drive_of, file_version, free_gb, run_version

__all__ = ["run", "ORDER", "STATUSES", "disk_verdict", "deps_verdict", "python_verdict", "is_deep",
           "DISK_MIN_WORK_GB", "DISK_MIN_SYSTEM_GB", "PYTHON_SUPPORTED"]

log = get_logger("doctor")

STATUSES = ("ok", "warn", "fail")
#: Display order; checks not listed follow alphabetically.
ORDER = ("python", "venv", "deps", "utf8", "paths", "disk", "network", "git", "blender", "msbuild", "premake",
         "mcp_config", "mcp_groups", "index", "ops_import")
DISK_MIN_WORK_GB = 10.0
DISK_MIN_SYSTEM_GB = 3.0
#: Python versions satk supports (3.12 is the reference; 3.13 and 3.14 are supported too).
PYTHON_SUPPORTED = ((3, 12), (3, 13), (3, 14))
#: Packages pinned in requirements.lock that satk uses directly (pytest only for development).
MAIN_DEPS = ("pillow", "numpy", "mcp", "pytest")
DEV_DEPS = ("pytest",)
_BOOTSTRAP = f"powershell -ExecutionPolicy Bypass -File {REPO_ROOT / 'scripts' / 'bootstrap.ps1'} -Deps"

_deep: contextvars.ContextVar[bool] = contextvars.ContextVar("satk_doctor_deep", default=False)


def is_deep() -> bool:
    """True inside ``satk doctor --deep`` (checks may do slower probes)."""
    return _deep.get()


def _res(status: str, msg: str, fix: str | None = None, **extra) -> dict:
    d = {"status": status, "msg": msg, "fix": fix}
    d.update({k: v for k, v in extra.items() if v is not None})
    return d


def _register(name: str) -> Callable[[Callable[[], dict]], Callable[[], dict]]:
    """``doctor_check(name)`` that yields to an existing registration by the owning package."""

    def deco(fn: Callable[[], dict]) -> Callable[[], dict]:
        try:
            doctor_check(name)(fn)
        except ValueError:
            log.info("doctor check %r is provided by another package; keeping that one", name)
        return fn

    return deco


# --------------------------------------------------------------------------- checks


def python_verdict(major: int, minor: int) -> tuple[str, str]:
    """(status, note): 3.12-3.14 ``ok``, newer ``warn`` (untested), older ``fail``."""
    lo, hi = PYTHON_SUPPORTED[0], PYTHON_SUPPORTED[-1]
    if (major, minor) in PYTHON_SUPPORTED:
        return "ok", f"supported {lo[0]}.{lo[1]}-{hi[0]}.{hi[1]}"
    if (major, minor) > hi:
        return "warn", f"newer than the supported {lo[0]}.{lo[1]}-{hi[0]}.{hi[1]}; untested"
    return "fail", f"satk needs Python {lo[0]}.{lo[1]}-{hi[0]}.{hi[1]}"


@_register("python")
def check_python() -> dict:
    v = sys.version_info
    ver = platform.python_version()
    status, note = python_verdict(v.major, v.minor)
    msg = f"{platform.python_implementation()} {ver} ({jpath(sys.executable)}); {note}"
    fix = None
    if status != "ok":
        fix = (f"install Python 3.12 (or 3.13/3.14), then powershell -ExecutionPolicy Bypass -File "
               f"{REPO_ROOT / 'scripts' / 'bootstrap.ps1'} -Deps")
    return _res(status, msg, fix, version=ver)


@_register("venv")
def check_venv() -> dict:
    if sys.prefix == sys.base_prefix:
        return _res("warn", f"not running in a virtualenv ({jpath(sys.prefix)})",
                    "run satk through satk.cmd / satk.sh of the checkout (uses its .venv)")
    expected = (MAIN_ROOT or REPO_ROOT) / ".venv"
    here = Path(sys.prefix)
    try:
        same = os.path.samefile(here, expected)
    except OSError:
        same = False
    if same:
        return _res("ok", jpath(here))
    return _res("ok", f"{jpath(here)} (not the shared {jpath(expected)})")


def deps_verdict(installed: dict[str, str | None], pinned: dict[str, str]) -> tuple[str, str]:
    """(status, message) for the main dependencies: ``warn`` when missing or not the pinned version.

    A missing development dependency (pytest) is only noted: a user install does not need it.
    """
    parts, bad = [], []
    for name in MAIN_DEPS:
        have = installed.get(name)
        want = pinned.get(name)
        if have is None and name in DEV_DEPS:
            parts.append(f"{name} not installed (development only: bootstrap.ps1 -Dev)")
        elif have is None:
            parts.append(f"{name} missing")
            bad.append(name)
        elif want and have != want:
            parts.append(f"{name} {have} (lock {want})")
            bad.append(name)
        else:
            parts.append(f"{name} {have}")
    return ("warn" if bad else "ok"), ", ".join(parts)


def _lock_pins() -> dict[str, str]:
    f = REPO_ROOT / "requirements.lock"
    pins: dict[str, str] = {}
    try:
        for line in f.read_text(encoding="ascii", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "==" in line:
                k, v = line.split("==", 1)
                pins[k.strip().lower().replace("_", "-")] = v.strip()
    except OSError:
        pass
    return pins


@_register("deps")
def check_deps() -> dict:
    installed: dict[str, str | None] = {}
    for name in MAIN_DEPS:
        try:
            installed[name] = _md.version(name)
        except _md.PackageNotFoundError:
            installed[name] = None
    status, msg = deps_verdict(installed, _lock_pins())
    return _res(status, msg, None if status == "ok" else _BOOTSTRAP,
                versions={k: v for k, v in installed.items() if v})


@_register("utf8")
def check_utf8() -> dict:
    enc = (getattr(sys.stdout, "encoding", None) or "?").lower()
    msg = f"utf8_mode={sys.flags.utf8_mode}, PYTHONUTF8={os.environ.get('PYTHONUTF8', '-')}, stdout={enc}"
    if sys.flags.utf8_mode:
        return _res("ok", msg)
    return _res("warn", msg + " (cp1251 console: Cyrillic may break)", "run via satk.cmd (python -X utf8)")


@_register("paths")
def check_paths() -> dict:
    c = cfg()
    p = c.paths
    problems: list[str] = []
    parts: list[str] = []
    work = Path(p.work)
    if work.is_dir() and os.access(work, os.W_OK):
        parts.append(f"work {jpath(work)}")
    elif not work.exists() and work.parent.is_dir() and os.access(work.parent, os.W_OK):
        problems.append(f"work {jpath(work)} does not exist yet (created on first use)")
    else:
        return _res("fail", f"work dir {jpath(work)} is missing or not writable", "check [paths].work in satk.toml")
    parts.insert(0, f"workspace {jpath(p.workspace)} ({c.workspace_source})")
    game = Path(p.game)
    root = p.get("game_root")
    if (game / "gta_sa.exe").is_file():
        parts.append(f"game {jpath(game)}")
    elif root is not None and Path(root).is_dir():
        parts.append(f"game_root {jpath(root)} (no clean copy: profile vanilla = game)")
    else:
        problems.append(f"no game: neither the clean copy {jpath(game)} nor game_root "
                        f"{jpath(root) if root else '(unset)'} exists")
    unconfigured = [n for n in ("installed", "src") if p.get(n) is not None and not Path(p.get(n)).exists()]
    if unconfigured:
        parts.append("not configured: " + ", ".join(unconfigured))
    roots = [jpath(r) for r in c.protected_roots]
    parts.append(f"{len(roots)} protected roots")
    names = [*p, *(k for k in DETECTED_PATHS if k not in set(p))]
    sources = {k: c.path_source(k) for k in names}
    profiles = {n: ("ok" if pr.configured else "not configured") for n, pr in c.profiles.items()}
    profiles.update({a: f"alias of {t}" for a, t in c.aliases.items()})
    from . import location

    bad = location.workspace_error(p.workspace)
    if bad is not None:
        return _res("fail", bad[0], bad[1], sources=sources, profiles=profiles)
    where = [w.split(":", 1)[1].strip() for w in location.workspace_warnings(p.workspace, min_free_gb=0)]
    for g in dict.fromkeys(x for x in (root, p.get("installed")) if x is not None and Path(x).is_dir()):
        where += [w.split(":", 1)[1].strip() for w in location.game_warnings(g)]
    if problems or where:
        fix = "satk init (or satk config show --section paths)" if problems else "satk init --workspace <folder>"
        return _res("warn", "; ".join(problems + where + parts), fix, sources=sources, profiles=profiles)
    return _res("ok", ", ".join(parts), sources=sources, profiles=profiles)


def disk_verdict(free: dict[str, float | None], work_drive: str, system_drive: str = "C:") -> tuple[str, str]:
    """(status, message): ``warn`` when the work drive has < 10 GB or the system drive < 3 GB free."""
    parts, warn = [], False
    for d in dict.fromkeys([work_drive, system_drive]):
        gb = free.get(d)
        if gb is None:
            parts.append(f"{d} ?")
            continue
        need = DISK_MIN_WORK_GB if d == work_drive else DISK_MIN_SYSTEM_GB
        if d == work_drive and d == system_drive:
            need = max(DISK_MIN_WORK_GB, DISK_MIN_SYSTEM_GB)
        low = gb < need
        warn = warn or low
        parts.append(f"{d} {gb:.1f} GB free" + (f" (< {need:g} GB)" if low else ""))
    return ("warn" if warn else "ok"), ", ".join(parts)


@_register("disk")
def check_disk() -> dict:
    work = cfg().paths.work
    wd = drive_of(work)
    sd = (os.environ.get("SystemDrive") or "C:").upper()
    free = {wd: free_gb(work), sd: free_gb(sd + os.sep)}
    status, msg = disk_verdict(free, wd, sd)
    return _res(status, msg, None if status == "ok" else "free space: work\\tmp, work\\cache, engine\\Build\\obj",
                free_gb={k: round(v, 1) for k, v in free.items() if v is not None})


def _tool_check(label: str, exe: Path | None, *, optional_for: str, version: Callable[[Path], str | None]) -> dict:
    source = cfg().path_source(label)
    if exe is None or not Path(exe).is_file():
        where = f"at {jpath(exe)}" if exe else "(not configured, not discovered)"
        return _res("warn", f"{label} not found {where} (needed for {optional_for})",
                    f"install it or set [paths].{label} in satk.toml", source=source)
    v = version(Path(exe))
    return _res("ok", f"{v or 'unknown version'} ({jpath(exe)}; {source})", version=v, source=source)


@_register("network")
def check_network() -> dict:
    """The network policy line; ``--deep`` also scans the sources for network imports outside the allowlist."""
    from . import network as N

    msg = N.POLICY.split(": ", 1)[1]
    if not is_deep():
        return _res("ok", msg, modules=len(N.ALLOWED))
    if (REPO_ROOT / "src" / "satk").is_dir():
        found = N.scan(REPO_ROOT)
    else:  # installed package: scan site-packages/satk, named like the checkout's src/satk
        from dataclasses import replace

        pkg = Path(N.__file__).resolve().parents[1]
        found = [replace(f, path="src/" + f.path) for f in N.scan(pkg.parent, ("satk",))]
    bad = N.violations(found)
    if bad:
        return _res("fail", msg + "; but these files import network modules outside the allowlist: "
                    + ", ".join(f"{f.path}:{f.line} {f.module}" for f in bad),
                    "remove the import or add the file to satk.runtime.network.ALLOWED with a reason")
    return _res("ok", msg + f"; scan: {len(found)} network imports, all allowed",
                allowed={k: v[1] for k, v in N.ALLOWED.items()})


@_register("git")
def check_git() -> dict:
    v = run_version(["git", "--version"])
    if not v:
        return _res("warn", "git not found on PATH", "install Git for Windows")
    return _res("ok", v.replace("git version ", ""), version=v.replace("git version ", ""))


@_register("blender")
def check_blender() -> dict:
    def ver(p: Path) -> str | None:
        v = file_version(p)
        if (v is None or is_deep()) and (line := run_version([str(p), "--version"], timeout=60)):
            v = line.replace("Blender", "").strip() or v
        return v

    return _tool_check("blender", cfg().paths.get("blender"), optional_for="satk blender", version=ver)


@_register("msbuild")
def check_msbuild() -> dict:
    res = _tool_check("msbuild", cfg().paths.get("msbuild"), optional_for="satk engine build", version=file_version)
    vc = cfg().paths.get("vcvars")
    res["vcvars"] = f"{jpath(vc)} ({cfg().path_source('vcvars')})" if vc else "not found"
    return res


@_register("premake")
def check_premake() -> dict:
    def ver(p: Path) -> str | None:
        line = run_version([str(p), "--version"])
        return line.split()[-1] if line else file_version(p)

    return _tool_check("premake", cfg().paths.get("premake"), optional_for="satk engine gen", version=ver)


@_register("mcp_config")
def check_mcp_config() -> dict:
    from satk.mcp.adapter import read_mcp_json

    f = Path(cfg().paths.workspace) / ".mcp.json"
    fix = "satk mcp config --write"
    data, problem, bom = read_mcp_json(f)
    if problem == "missing":
        return _res("warn", f"{jpath(f)} missing: Claude Code does not see the satk MCP server", fix)
    if data is None:
        return _res("warn", f"{jpath(f)}: {problem}; Claude Code cannot read it",
                    "fix or move the file, then satk mcp config --write")
    try:
        entry = data["mcpServers"]["satk"]
        exe = Path(entry["command"])
    except (KeyError, TypeError) as e:
        return _res("warn", f"{jpath(f)}: no valid mcpServers.satk entry ({type(e).__name__})", fix)
    if bom:
        return _res("warn", f"{jpath(f)} starts with a UTF-8 BOM; Claude Code's JSON parser rejects it", fix)
    if not exe.is_file():
        return _res("warn", f"{jpath(f)}: command {jpath(exe)} does not exist", fix)
    return _res("ok", f"{jpath(f)} -> {jpath(exe)} {' '.join(entry.get('args', []))}")


@_register("mcp_groups")
def check_mcp_groups() -> dict:
    from satk.mcp.adapter import resolve_groups

    raw = os.environ.get("SATK_MCP_GROUPS")
    try:
        groups = resolve_groups(raw)
    except SatkError as e:
        return _res("fail", e.msg + (f" (did you mean {', '.join(e.did_you_mean)}?)" if e.did_you_mean else ""),
                    "fix or unset SATK_MCP_GROUPS: the MCP server does not start with an unknown group")
    return _res("ok", "SATK_MCP_GROUPS not set: all tool groups" if groups is None
                else f"SATK_MCP_GROUPS={raw}: groups {','.join(sorted(groups))}")


@_register("index")
def check_index() -> dict:
    c = cfg()
    name = c.default_profile
    f = Path(c.paths.work) / "index" / f"{name}.sqlite"
    if not f.is_file():
        return _res("warn", f"no index for profile {name} ({jpath(f)})", "satk index build")
    st = f.stat()
    when = _dt.datetime.fromtimestamp(st.st_mtime, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return _res("ok", f"{name} index {st.st_size / 1e6:.1f} MB, modified {when}")


# --------------------------------------------------------------------------- runner


def _sort_key(name: str) -> tuple[int, str]:
    return (ORDER.index(name), "") if name in ORDER else (len(ORDER), name)


def _normalize(name: str, res: object) -> dict:
    if not isinstance(res, dict) or res.get("status") not in STATUSES:
        return {"name": name, "status": "fail", "msg": f"check returned {type(res).__name__}, not a check result",
                "fix": None}
    out = {"name": name, "status": res["status"], "msg": str(res.get("msg", "")), "fix": res.get("fix")}
    for k, v in res.items():
        if k not in out and v is not None:
            out[k] = v
    return out


def run(deep: bool = False, only: list[str] | None = None) -> dict:
    """Run all registered checks; returns ``{status, counts, checks:[{name,status,msg,fix,...}]}``."""
    token = _deep.set(bool(deep))
    try:
        checks = doctor_checks()
        names = sorted(checks, key=_sort_key)
        if only:
            unknown = [n for n in only if n not in checks]
            if unknown:
                import difflib

                raise SatkError("BAD_PARAMS", f"unknown check(s): {', '.join(unknown)}",
                                did_you_mean=difflib.get_close_matches(unknown[0], names, n=3, cutoff=0.4),
                                data={"checks": names})
            names = [n for n in names if n in only]
        results: list[dict] = []
        for name in names:
            t0 = time.perf_counter()
            try:
                res = _normalize(name, checks[name]())
            except SatkError as e:
                res = {"name": name, "status": "fail", "msg": f"{e.code}: {e.msg}", "fix": e.hint}
            except Exception as e:  # noqa: BLE001 - a broken check must not break doctor
                where = record_exception(e, context=f"doctor check {name}")
                res = {"name": name, "status": "fail", "msg": f"check crashed: {type(e).__name__}: {e}",
                       "fix": f"traceback in {jpath(where)}" if where else None}
            ms = (time.perf_counter() - t0) * 1000
            if ms >= 500:
                res["ms"] = round(ms)
            results.append({k: v for k, v in res.items() if v is not None})
    finally:
        _deep.reset(token)
    counts = {s: sum(1 for r in results if r["status"] == s) for s in STATUSES}
    worst = "fail" if counts["fail"] else ("warn" if counts["warn"] else "ok")
    return {"status": worst, "counts": counts, "checks": results}
