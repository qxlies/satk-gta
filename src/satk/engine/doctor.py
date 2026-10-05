"""``satk engine doctor`` / ``status``: checks of the MTA fork toolchain (SPEC §4.12, WP-11).

Every check returns ``{"status": "ok"|"warn"|"fail"|"skip", "msg": str, "fix": str|None}``.
Quick checks only look at files and git config; ``deep=True`` also re-hashes pinned
dependencies and asks MSBuild for its version. Registered for ``satk doctor`` as check
``engine`` and for ``satk_status`` as section ``engine``.
"""

from __future__ import annotations

import os
import subprocess
from typing import Callable

from ..core.paths import jpath
from ..core.registry import doctor_check, status_provider
from .common import (
    EXPECTED_BASE,
    Layout,
    disk_free_gb,
    donor_git,
    find_msbuild,
    find_premake,
    find_rc,
    layout,
    read_json,
    sha256_file,
)

__all__ = ["CHECKS", "run_checks", "summary", "status"]

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _r(status: str, msg: str, fix: str | None = None) -> dict:
    return {"status": status, "msg": msg, "fix": fix}


def _fork(L: Layout, deep: bool) -> dict:
    from .setup import fork_info

    if not (L.fork / ".git").exists():
        return _r("fail", f"no fork checkout at {jpath(L.fork)}", "satk engine setup")
    info = fork_info(L)
    msg = f"branch {info['branch']} @ {info['head']}, base {info['base']}, {info['ahead_of_base']} commit(s) ahead"
    if info["dirty"]:
        msg += f", {info['dirty']} uncommitted change(s)"
    if info.get("base") is None:
        return _r("fail", msg + "; upstream-local/master missing", "satk engine setup")
    if not EXPECTED_BASE.startswith(info["base"] or "-"):
        msg += f" (setup base was {EXPECTED_BASE[:9]})"
    return _r("ok", msg)


def _remotes(L: Layout, deep: bool) -> dict:
    from .setup import fork_info

    if not (L.fork / ".git").exists():
        return _r("skip", "no fork")
    info = fork_info(L)
    r = info["remotes"]
    problems = []
    neon = r.get("neon", {})
    if not neon.get("fetch", "").lower().startswith("file:///"):
        problems.append("neon is not the local donor")
    for name in ("neon", "upstream"):
        if name in r and r[name].get("push") != "DISABLED":
            problems.append(f"{name} push is not DISABLED")
    if "upstream" not in r:
        problems.append("no upstream remote")
    extra = sorted(set(r) - {"neon", "upstream", "origin"})
    if "origin" in r and r["origin"].get("push") not in ("DISABLED", None):
        msg_origin = f"origin = {r['origin'].get('push')} (publishing needs consent D5)"
    else:
        msg_origin = "no origin (D5 pending)"
    if not info["rerere"]:
        problems.append("rerere disabled")
    msg = (f"neon={neon.get('fetch')} upstream={r.get('upstream', {}).get('fetch')} "
           f"(fetched={info['upstream_fetched']}); {msg_origin}")
    if extra:
        msg += f"; extra remotes: {', '.join(extra)}"
    if problems:
        return _r("fail", "; ".join(problems) + " — " + msg, "satk engine setup")
    return _r("ok", msg)


def _donor(L: Layout, deep: bool) -> dict:
    if not (L.donor / ".git").exists():
        return _r("warn", f"donor clone missing: {jpath(L.donor)}", None)
    st = donor_git("status", "--porcelain", check=False)
    n = len([x for x in st.splitlines() if x.strip()])
    if n:
        return _r("fail", f"src/mtasa-neon has {n} changed file(s); src is read-only",
                  f"inspect with git -C {L.donor} status (do not commit there)")
    return _r("ok", "src/mtasa-neon clean (read-only donor)")


def _no_dbuild_in_fork(L: Layout, deep: bool) -> dict:
    if not L.fork.is_dir():
        return _r("skip", "no fork")
    found = []
    skip = {".git", "Bin", "Build"}
    for dirpath, dirnames, filenames in os.walk(L.fork):
        if dirpath == str(L.fork):
            dirnames[:] = [d for d in dirnames if d not in skip]
        for f in filenames:
            if f.lower().startswith("directory.build."):
                found.append(os.path.join(dirpath, f))
    if found:
        return _r("warn", f"Directory.Build.* inside the fork shadow engine\\Directory.Build.targets: {found[:3]}",
                  "remove them or merge the shim into them")
    return _r("ok", "no Directory.Build.* inside the fork")


def _shims(L: Layout, deep: bool) -> dict:
    from .setup import template_status

    st = template_status(L)
    bad = [t["name"] + ":" + t["status"] for t in st if t["status"] != "ok"]
    if bad:
        return _r("fail", "engine files not as generated: " + ", ".join(bad), "satk engine setup")
    return _r("ok", "Directory.Build.targets, shims/afxres.h, satk-premake.lua, bootstrap.ps1 up to date")


def _msbuild(L: Layout, deep: bool) -> dict:
    m = find_msbuild()
    if m is None:
        return _r("fail", "MSBuild.exe not found", "set [paths] msbuild in tools/satk.toml")
    msg = jpath(m)
    if deep:
        try:
            out = subprocess.run([str(m), "-version", "-nologo"], capture_output=True, text=True, timeout=60,
                                 creationflags=_NO_WINDOW).stdout.strip().splitlines()
            msg += f" ({out[-1].strip() if out else '?'})"
        except (OSError, subprocess.SubprocessError) as e:
            return _r("warn", f"{msg}: cannot run: {e}")
    return _r("ok", msg)


def _toolset(L: Layout, deep: bool) -> dict:
    m = find_msbuild()
    if m is None:
        return _r("skip", "no MSBuild")
    vs = m.parents[3] if len(m.parents) > 3 else m.parent  # ...\VS2026\MSBuild\Current\Bin\MSBuild.exe
    msvc = vs / "VC" / "Tools" / "MSVC"
    vers = sorted((d.name for d in msvc.iterdir() if d.is_dir()), reverse=True) if msvc.is_dir() else []
    ts = []
    for v in ("v180", "v170"):
        for name in ("v145", "v143"):
            if (vs / "MSBuild" / "Microsoft" / "VC" / v / "Platforms" / "Win32" / "PlatformToolsets" / name).is_dir():
                ts.append(name)
    if "v145" not in ts:
        return _r("fail", f"toolset v145 not found (have: {', '.join(ts) or 'none'}; MSVC {', '.join(vers)})",
                  "install MSVC v145 or build with --toolset v143")
    return _r("ok", f"toolsets {', '.join(sorted(set(ts)))}; MSVC {', '.join(vers)}")


def _rc(L: Layout, deep: bool) -> dict:
    rc = find_rc()
    if rc is None:
        return _r("fail", "rc.exe not found (Windows SDK)", "install the Windows SDK")
    return _r("ok", f"{jpath(rc.exe)} (SDK {rc.version})")


def _premake(L: Layout, deep: bool) -> dict:
    p = find_premake(L.fork)
    if p is None:
        return _r("fail", "premake5.exe not found", "satk engine setup")
    return _r("ok", jpath(p))


def _dxfiles(L: Layout, deep: bool) -> dict:
    h = L.dxfiles / "Include" / "d3dx9.h"
    lib = L.dxfiles / "Lib" / "x86" / "d3dx9.lib"
    if not h.is_file():
        return _r("warn", f"{jpath(h)} missing (needed for the client)", "satk engine setup --deps")
    if not lib.is_file():
        return _r("warn", f"{jpath(lib)} missing", "satk engine setup --deps")
    return _r("ok", f"DXSDK_DIR={jpath(L.dxfiles)}/")


def _deps_lock(L: Layout, deep: bool) -> dict:
    from .setup import manifest

    data = read_json(L.lock, None)
    if not isinstance(data, dict) or not data.get("items"):
        return _r("warn", "engine/deps-lock.json missing", "satk engine setup --deps")
    items = {it["id"]: it for it in data["items"] if isinstance(it, dict) and "id" in it}
    try:
        want = {d.id: d for d in manifest(L)}
    except Exception as e:  # noqa: BLE001
        return _r("warn", f"cannot read dependency manifest: {e}")
    missing = sorted(set(want) - set(items))
    stale = sorted(i for i, d in want.items() if i in items and d.sha256 and items[i].get("sha256") != d.sha256)
    bad = []
    for i, it in items.items():
        p = L.deps / it.get("file", "")
        if not p.is_file():
            bad.append(f"{i}:missing")
        elif deep and sha256_file(p) != it.get("sha256"):
            bad.append(f"{i}:sha256")
        elif not deep and it.get("size") is not None and p.stat().st_size != it["size"]:
            bad.append(f"{i}:size")
    if bad:
        return _r("fail", "pinned files damaged: " + ", ".join(bad), "satk engine setup --deps")
    if missing or stale:
        return _r("warn", f"not pinned: {', '.join(missing) or '-'}; upstream bumped: {', '.join(stale) or '-'}",
                  "satk engine setup --deps")
    return _r("ok", f"{len(items)} pinned ({'sha256 verified' if deep else 'sizes checked'})")


def _installed(L: Layout, deep: bool) -> dict:
    from .setup import verify_installed

    if not L.fork.is_dir():
        return _r("skip", "no fork")
    v = verify_installed(L)
    bad = [x["id"] + ":" + x["status"] for x in v if x["status"] != "ok"]
    if bad:
        sev = "fail" if any(s.endswith(":mismatch") for s in bad) else "warn"
        return _r(sev, "not installed/verified: " + ", ".join(bad), "satk engine setup --deps")
    return _r("ok", "CEF, discord-rpc, rapidjson, unifont installed; net.dll/net_64/netc match the pins")


def _sln(L: Layout, deep: bool) -> dict:
    from .build import gen_fingerprint, gen_state

    if not L.sln.is_file():
        return _r("warn", "Build/MTASA.sln missing", "satk engine gen")
    st = gen_state(L)
    try:
        fresh = st.get("fingerprint") == gen_fingerprint(L)
    except Exception:  # noqa: BLE001
        fresh = False
    n = len(list((L.fork / "Build").glob("*.vcxproj")))
    if not fresh:
        return _r("warn", f"MTASA.sln ({n} projects) is older than the tree/deps; build regenerates it",
                  "satk engine gen")
    return _r("ok", f"MTASA.sln, {n} projects, generated {st.get('time')}")


def _bins(L: Layout, deep: bool) -> dict:
    from .build import artifacts

    rows = artifacts(L)
    missing = [r[1] for r in rows if not r[2]]
    if missing:
        return _r("warn", f"{len(rows) - len(missing)}/{len(rows)} key outputs built; missing e.g. {missing[0]}",
                  "satk engine build --project all")
    return _r("ok", f"all {len(rows)} key outputs present (client Win32 + server x64)")


def _disk(L: Layout, deep: bool) -> dict:
    free = disk_free_gb(L.root)
    if free < 5:
        return _r("fail", f"{free} GB free on {L.root.drive}", "free disk space (Build/obj ~5 GB per config)")
    if free < 12:
        return _r("warn", f"{free} GB free on {L.root.drive} (< 12 GB)", "free disk space before big builds")
    return _r("ok", f"{free} GB free on {L.root.drive}")


CHECKS: dict[str, Callable[[Layout, bool], dict]] = {
    "fork": _fork,
    "remotes": _remotes,
    "donor_clean": _donor,
    "no_dbuild_in_fork": _no_dbuild_in_fork,
    "shims": _shims,
    "msbuild": _msbuild,
    "toolset": _toolset,
    "rc": _rc,
    "premake": _premake,
    "dxfiles": _dxfiles,
    "deps_lock": _deps_lock,
    "deps_installed": _installed,
    "sln": _sln,
    "outputs": _bins,
    "disk": _disk,
}


def run_checks(deep: bool = False) -> list[dict]:
    L = layout()
    out = []
    for name, fn in CHECKS.items():
        try:
            r = fn(L, deep)
        except Exception as e:  # noqa: BLE001 - a broken check must not break doctor
            r = _r("fail", f"check crashed: {type(e).__name__}: {e}")
        out.append({"check": name, **r})
    return out


_ORDER = {"ok": 0, "skip": 0, "warn": 1, "fail": 2}


def summary(results: list[dict]) -> dict:
    worst = max((_ORDER.get(r["status"], 2) for r in results), default=0)
    status = {0: "ok", 1: "warn", 2: "fail"}[worst]
    bad = [r for r in results if _ORDER.get(r["status"], 2) == worst and worst > 0]
    msg = f"{sum(r['status'] == 'ok' for r in results)}/{len(results)} engine checks ok"
    if bad:
        msg += f"; {bad[0]['check']}: {bad[0]['msg']}"
    return {"status": status, "msg": msg[:400], "fix": bad[0]["fix"] if bad else None}


@doctor_check("engine")
def _doctor_engine() -> dict:
    """MTA fork: checkout, remotes, shims, toolchain, pinned deps, outputs (``satk engine doctor``)."""
    return summary(run_checks(deep=False))


def status(deep: bool = False) -> dict:
    """Compact engine state for ``satk_status`` / ``satk engine status``."""
    from .build import artifacts, gen_state
    from .setup import fork_info, load_lock

    L = layout()
    out: dict = {"fork": fork_info(L) if (L.fork / ".git").exists() else {"exists": False, "path": jpath(L.fork)}}
    state = read_json(L.state, {}) or {}
    out["builds"] = state.get("builds", {})
    g = gen_state(L)
    if g:
        out["gen"] = {"time": g.get("time"), "projects": g.get("projects"), "cef": g.get("cef")}
    lock = load_lock(L)
    out["deps_pinned"] = [it.get("id") for it in lock.get("items", [])]
    rows = artifacts(L)
    out["outputs"] = f"{sum(1 for r in rows if r[2])}/{len(rows)}"
    out["disk_free_gb"] = disk_free_gb(L.root)
    if deep:
        out["checks"] = summary(run_checks(deep=True))
    return out


@status_provider("engine")
def _status_engine(deep: bool) -> dict:
    return status(deep)
