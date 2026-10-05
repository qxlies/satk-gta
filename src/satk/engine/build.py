"""``satk engine gen | build | rc-test`` (SPEC §4.12.2).

* :func:`gen` — ``utils\\premake5.exe vs2026`` in the fork with ``DXSDK_DIR`` set first
  (premake bakes it into the projects); a fingerprint (file list, premake scripts, installed
  vendor deps) is stored so :func:`build` regenerates only when needed (globs are expanded at
  generation time, report 28 §6.4).
* :func:`build` — MSBuild directly (no vswhere, no ``win-build.bat``), ``-nodeReuse:false``,
  TEMP on D:. ``--project`` is a vcxproj name or an alias: ``server`` (x64 solution),
  ``client`` (Win32 solution), ``all`` (solution, both platforms unless ``--platform``),
  ``changed`` (projects touched since the last successful build of that platform).
  Errors come back as rows ``file, line, code, msg``; full logs stay in ``work\\engine\\build\\logs``.
* :func:`rc_test` — compiles the 4 client ``.rc`` with the Windows SDK ``rc.exe`` and the
  ``afxres.h`` shim (V5 inside the fork); ``control=True`` also proves the failure without it.
"""

from __future__ import annotations

import hashlib
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from ..core.errors import SatkError
from ..core.paths import ensure_writable, jpath
from ..core.registry import report_progress
from .common import (
    Layout,
    build_env,
    exclusive,
    find_msbuild,
    find_premake,
    find_rc,
    git,
    layout,
    read_json,
    run,
    safe_name,
    stamp,
    write_json,
)

__all__ = [
    "RC_FILES",
    "PLATFORMS",
    "ARTIFACTS",
    "parse_msbuild_log",
    "parse_sln",
    "project_index",
    "gen_fingerprint",
    "gen",
    "gen_state",
    "plan",
    "changed_files",
    "build",
    "rc_test",
    "artifacts",
]

#: The client resource scripts that include MFC's afxres.h (V5).
RC_FILES = ("Client/loader/loader.rc", "Client/launch/launch.rc", "Client/launch/Multi Theft Auto.rc",
            "Client/core/core.rc")
PLATFORMS = ("Win32", "x64")
#: Key outputs per platform (Release names; Debug adds ``_d`` before the extension).
ARTIFACTS = {
    "Win32": ("Multi Theft Auto.exe", "mta/loader.dll", "mta/core.dll", "mta/game_sa.dll", "mta/multiplayer_sa.dll",
              "mta/cefweb.dll", "mods/deathmatch/client.dll"),
    "x64": ("server/MTA Server64.exe", "server/x64/core.dll", "server/x64/deathmatch.dll"),
}
_SRC_EXT = (".c", ".cc", ".cpp", ".cxx", ".rc", ".asm")


# --------------------------------------------------------------------------- logs


_ERR_POS = re.compile(
    r"^\s*(?:\d+>)?(?P<file>[^\s(][^(]*?)\((?P<line>\d+)(?:,(?P<col>\d+))?(?:,\d+,\d+)?\)\s*:\s*"
    r"(?:fatal\s+)?(?P<kind>error|warning)\s+(?P<code>[A-Za-z]+\d+)\s*:\s*(?P<msg>.*?)(?:\s+\[(?P<proj>[^\]]+)\])?\s*$")
_ERR_NOPOS = re.compile(
    r"^\s*(?:\d+>)?(?P<file>[^:\[\]]*?)\s*:\s*(?:fatal\s+)?(?P<kind>error|warning)\s+(?P<code>[A-Za-z]+\d+)\s*:\s*"
    r"(?P<msg>.*?)(?:\s+\[(?P<proj>[^\]]+)\])?\s*$")


@dataclass(frozen=True)
class Diag:
    kind: str
    file: str
    line: int | None
    code: str
    msg: str
    project: str

    def row(self) -> list:
        return [self.file, self.line, self.code, self.msg[:300], self.project]


def _rel(p: str, fork: Path | None) -> str:
    s = p.strip().replace("\\", "/")
    if fork is not None:
        f = str(fork).replace("\\", "/").rstrip("/") + "/"
        if s.lower().startswith(f.lower()):
            s = s[len(f):]
        b = f + "Build/"
        if s.lower().startswith(b.lower()):
            s = s[len(b):]
    # "..\Client\x.cpp" relative to Build\
    while s.startswith("../"):
        s = s[3:]
    return s


def parse_msbuild_log(text: str, fork: Path | None = None) -> tuple[list[Diag], list[Diag]]:
    """(errors, warnings) from MSBuild output, de-duplicated, in order of appearance."""
    errs: list[Diag] = []
    warns: list[Diag] = []
    seen: set = set()
    for line in text.splitlines():
        m = _ERR_POS.match(line) or _ERR_NOPOS.match(line)
        if not m:
            continue
        d = Diag(
            kind=m.group("kind"),
            file=_rel(m.group("file") or "", fork),
            line=int(m.group("line")) if "line" in m.groupdict() and m.group("line") else None,
            code=m.group("code"),
            msg=m.group("msg").strip(),
            project=Path((m.group("proj") or "").replace("\\", "/")).stem,
        )
        key = (d.kind, d.file, d.line, d.code, d.msg)
        if key in seen:
            continue
        seen.add(key)
        (errs if d.kind == "error" else warns).append(d)
    return errs, warns


# --------------------------------------------------------------------------- solution / projects


_SLN_PROJ = re.compile(r'^Project\("\{[^}]+\}"\)\s*=\s*"(?P<name>[^"]+)",\s*"(?P<path>[^"]+)",\s*"\{(?P<guid>[^}]+)\}"')
_SLN_CFG = re.compile(r"^\s*\{(?P<guid>[^}]+)\}\.(?P<sc>[^|]+)\|(?P<sp>[^.]+)\.Build\.0\s*=")


def parse_sln(sln: Path) -> dict[str, dict]:
    """``{project name: {"path": rel vcxproj, "builds": {"Release|Win32", ...}}}`` from MTASA.sln."""
    text = sln.read_text(encoding="utf-8-sig", errors="replace")
    by_guid: dict[str, dict] = {}
    for line in text.splitlines():
        m = _SLN_PROJ.match(line)
        if m and m.group("path").lower().endswith(".vcxproj"):
            by_guid[m.group("guid").upper()] = {"name": m.group("name"), "path": m.group("path"), "builds": set()}
            continue
        m = _SLN_CFG.match(line)
        if m and m.group("guid").upper() in by_guid:
            by_guid[m.group("guid").upper()]["builds"].add(f"{m.group('sc')}|{m.group('sp')}")
    return {v["name"]: v for v in by_guid.values()}


_MSB_NS = "{http://schemas.microsoft.com/developer/msbuild/2003}"


def project_index(build_dir: Path) -> tuple[dict[str, set[str]], dict[str, str]]:
    """({source path relative to the fork (lower, forward slashes): {project}}, {project: ConfigurationType})."""
    files: dict[str, set[str]] = {}
    kinds: dict[str, str] = {}
    for vcx in sorted(build_dir.glob("*.vcxproj")):
        name = vcx.stem
        try:
            root = ET.parse(vcx).getroot()
        except ET.ParseError:
            continue
        for ct in root.iter(f"{_MSB_NS}ConfigurationType"):
            kinds.setdefault(name, (ct.text or "").strip())
        for tag in ("ClCompile", "ResourceCompile", "MASM"):
            for el in root.iter(f"{_MSB_NS}{tag}"):
                inc = el.get("Include")
                if not inc:
                    continue
                p = (build_dir / inc).resolve()
                try:
                    rel = p.relative_to(build_dir.parent.resolve())
                except ValueError:
                    continue
                files.setdefault(str(rel).replace("\\", "/").lower(), set()).add(name)
    return files, kinds


# --------------------------------------------------------------------------- generation


def _dep_presence(L: Layout) -> dict:
    return {
        "dxsdk": str(L.dxfiles),
        "dxfiles": (L.dxfiles / "Include" / "d3dx9.h").is_file(),
        "cef": (L.fork / "vendor" / "cef3" / "cef" / "include").is_dir(),
        "discord": (L.fork / "vendor" / "discord-rpc" / "discord" / "src").is_dir(),
    }


def gen_fingerprint(L: Layout | None = None) -> str:
    """Hash of everything premake generation depends on: file list (tracked + untracked, not
    ignored), contents of all premake5.lua, installed vendor deps and DXSDK_DIR."""
    L = L or layout()
    h = hashlib.sha256()
    listing = git("ls-files", "-co", "--exclude-standard", "-z", cwd=L.fork)
    names = sorted(x for x in listing.split("\0") if x)
    for n in names:
        h.update(n.encode("utf-8", "surrogateescape") + b"\n")
        if n.endswith("premake5.lua"):
            try:
                h.update((L.fork / n).read_bytes())
            except OSError:
                pass
    h.update(repr(sorted(_dep_presence(L).items())).encode())
    return h.hexdigest()


def gen_state(L: Layout | None = None) -> dict:
    L = L or layout()
    return (read_json(L.state, {}) or {}).get("gen", {})


def _update_state(L: Layout, key: str, value) -> None:
    st = read_json(L.state, {}) or {}
    if isinstance(value, dict) and isinstance(st.get(key), dict) and key == "builds":
        st[key].update(value)
    else:
        st[key] = value
    write_json(L.state, st)


def _guard_build_paths(L: Layout) -> None:
    """Check checkout and output aliases before premake, MSBuild or work-file creation."""
    for path in (L.fork, L.fork / "Build", L.sln, L.bin, L.build_dir, L.logs, L.state):
        ensure_writable(path)


def gen(L: Layout | None = None) -> dict:
    """Run ``premake5 vs2026`` (DXSDK_DIR set before premake) and record the fingerprint."""
    L = L or layout()
    _guard_build_paths(L)
    if not (L.fork / "premake5.lua").is_file():
        raise SatkError("NOT_READY", f"fork not set up: {jpath(L.fork)}", hint="satk engine setup")
    premake = find_premake(L.fork)
    if premake is None:
        raise SatkError("NOT_READY", "premake5.exe not found", hint="satk engine doctor")
    pres = _dep_presence(L)
    warn = []
    if not pres["dxfiles"]:
        warn.append("NO_DXFILES: engine/deps/DXFiles missing; client projects will not compile (satk engine setup --deps)")
    if not pres["cef"] or not pres["discord"]:
        warn.append("NO_CLIENT_DEPS: CEF/discord-rpc not installed; generated client projects lack their sources "
                    "(server builds are fine; satk engine setup --deps, then gen again)")
    log = L.logs / f"{stamp()}-premake-vs2026.log"
    ensure_writable(log)
    r = run([premake, "vs2026"], cwd=L.fork, env=build_env(), log=log, timeout=600)
    if r.code != 0 or not L.sln.is_file():
        raise SatkError("EXTERNAL_TOOL", f"premake vs2026 failed (exit {r.code})", hint=f"see {jpath(log)}",
                        data={"tail": r.out[-1500:]})
    nproj = len(list((L.fork / "Build").glob("*.vcxproj")))
    fp = gen_fingerprint(L)
    state = {"fingerprint": fp, "time": time.strftime("%Y-%m-%dT%H:%M:%S"), "projects": nproj, **pres}
    _update_state(L, "gen", state)
    out = {"sln": jpath(L.sln), "projects": nproj, "seconds": r.seconds, "dxsdk_dir": jpath(L.dxfiles) + "/",
           "cef": pres["cef"], "discord": pres["discord"], "log": jpath(log)}
    if warn:
        out["warn"] = warn
    return out


def _vcxproj_hashes(L: Layout) -> dict[str, str]:
    out = {}
    for p in (L.fork / "Build").glob("*.vcxproj"):
        try:
            out[p.stem] = hashlib.sha1(p.read_bytes()).hexdigest()  # noqa: S324 - change detection only
        except OSError:
            pass
    return out


def _ensure_generated(L: Layout, regen: str) -> dict | None:
    """Regenerate if the solution is missing or stale (``regen``: auto|always|never).

    The result has ``"touched"``: projects whose ``.vcxproj`` changed (files added/removed),
    which ``--project changed`` must rebuild even if no tracked source differs.
    """
    if regen == "never":
        if not L.sln.is_file():
            raise SatkError("NOT_READY", "Build/MTASA.sln missing", hint="satk engine gen")
        return None
    if regen != "always" and L.sln.is_file() and gen_state(L).get("fingerprint") == gen_fingerprint(L):
        return None
    before = _vcxproj_hashes(L)
    out = gen(L)
    after = _vcxproj_hashes(L)
    out["touched"] = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    return out


# --------------------------------------------------------------------------- planning


@dataclass(frozen=True)
class Step:
    label: str  # "sln" or a project name
    target: Path  # .sln or .vcxproj
    platform: str


def _canon_platform(p: str | None) -> str | None:
    if p is None:
        return None
    low = p.strip().lower()
    if low in ("win32", "x86"):
        return "Win32"
    if low in ("x64", "amd64", "win64"):
        return "x64"
    raise SatkError("BAD_PARAMS", f"unknown platform {p!r}", did_you_mean=["Win32", "x64"])


def _platforms_of(entry: dict, config: str) -> list[str]:
    return [p for p in PLATFORMS if f"{config}|{p}" in entry["builds"]]


def changed_files(L: Layout, since: str | None, after: float | None = None) -> list[str]:
    """Paths (relative, forward slashes) changed since commit ``since`` (working tree included)
    plus untracked, not ignored. With ``after`` (epoch seconds of the last build) files that
    still exist and were not modified after it are dropped: they were part of that build."""
    out: set[str] = set()
    for x in git("diff", "--name-only", since or "HEAD", cwd=L.fork).splitlines():
        if x.strip():
            out.add(x.strip())
    for x in git("ls-files", "-o", "--exclude-standard", cwd=L.fork).splitlines():
        if x.strip():
            out.add(x.strip())
    if after is not None:
        keep = set()
        for f in out:
            try:
                if (L.fork / f).stat().st_mtime <= after:
                    continue
            except OSError:
                pass  # deleted: keep
            keep.add(f)
        out = keep
    return sorted(out)


_NOT_BUILT = re.compile(r"(^|/)(docs|\.github|\.vscode)/|\.(md|txt|toml|json|yml|yaml)$|^(license|\.git\w*)$", re.I)


def _path_platforms(path: str) -> list[str]:
    """Platforms whose build a changed file can affect (none for docs, CI files and notes)."""
    low = path.lower()
    if _NOT_BUILT.search(low):
        return []
    if low.startswith("client/") or low.startswith("tests/client/"):
        return ["Win32"]
    if low.startswith("server/"):
        return ["x64"]
    return list(PLATFORMS)


def plan(project: str, platform: str | None, config: str, L: Layout | None = None,
         state: dict | None = None, touched: list[str] | None = None) -> tuple[list[Step], dict]:
    """Translate ``--project``/``--platform`` into MSBuild steps; second value = notes.

    ``touched`` = projects whose ``.vcxproj`` was just regenerated with different content
    (``changed`` rebuilds them: a source file was added or removed).
    """
    L = L or layout()
    plat = _canon_platform(platform)
    sln = L.sln
    notes: dict = {}
    key = project.strip()
    low = key.lower()
    if low in ("all", "sln", "solution"):
        plats = [plat] if plat else list(PLATFORMS)
        return [Step("sln", sln, p) for p in plats], notes
    if low == "server":
        if plat == "Win32":
            notes["warn"] = ["the Win32 solution builds only legacy x86 server libraries; the server is x64"]
        return [Step("sln", sln, plat or "x64")], notes
    if low == "client":
        if plat == "x64":
            raise SatkError("BAD_PARAMS", "the client is Win32 only", hint="--platform Win32")
        return [Step("sln", sln, "Win32")], notes
    projects = parse_sln(sln)
    if low == "changed":
        builds = (state or read_json(L.state, {}) or {}).get("builds", {})
        index, kinds = project_index(L.fork / "Build")
        plats = [plat] if plat else list(PLATFORMS)
        steps: list[Step] = []
        notes["changed"] = {}
        for p in plats:
            prev = builds.get(f"{config}|{p}", {})
            last = prev.get("rev")
            if not last:
                steps.append(Step("sln", sln, p))
                notes["changed"][p] = "no previous successful build: whole solution"
                continue
            files = [f for f in changed_files(L, last, prev.get("ts")) if p in _path_platforms(f)]
            regen = [t for t in (touched or []) if t in projects and f"{config}|{p}" in projects[t]["builds"]]
            if not files and not regen:
                notes["changed"][p] = "nothing changed since " + last[:9]
                continue
            projs: set[str] = set()
            whole = False
            for f in files:
                owners = index.get(f.lower())
                if not owners or not f.lower().endswith(_SRC_EXT):
                    whole = True  # header, script, data or unknown file: let MSBuild decide
                    break
                for o in owners:
                    entry = projects.get(o)
                    if entry is None or f"{config}|{p}" not in entry["builds"]:
                        continue  # not part of this platform
                    projs.add(o)
            projs |= set(regen)
            if any(kinds.get(o, "") == "StaticLibrary" for o in projs):
                whole = True  # dependents must relink
            what = f"{len(files)} file(s)" + (f", regenerated {', '.join(regen)}" if regen else "")
            if whole:
                steps.append(Step("sln", sln, p))
                notes["changed"][p] = f"{what}: headers/static libs/unknown files -> whole solution"
            elif not projs:
                notes["changed"][p] = f"{what}: none built for {p}"
            else:
                for o in sorted(projs):
                    steps.append(Step(o, L.fork / "Build" / projects[o]["path"], p))
                notes["changed"][p] = f"{what} -> {', '.join(sorted(projs))}"
        return steps, notes
    # a named project
    match = next((n for n in projects if n.lower() == low), None)
    if match is None:
        alt = {n.lower().replace(" ", "").replace("_", ""): n for n in projects}
        match = alt.get(low.replace(" ", "").replace("_", ""))
    if match is None:
        import difflib

        raise SatkError("NOT_FOUND", f"no project {project!r} in MTASA.sln",
                        did_you_mean=difflib.get_close_matches(key, list(projects), n=5, cutoff=0.4),
                        hint="aliases: server, client, all, changed")
    entry = projects[match]
    avail = _platforms_of(entry, config)
    if plat:
        if avail and plat not in avail:
            notes["warn"] = [f"{match} is not built for {plat} in the solution ({', '.join(avail) or 'none'})"]
        plats = [plat]
    else:
        plats = avail or ["Win32"]
    return [Step(match, L.fork / "Build" / entry["path"], p) for p in plats], notes


# --------------------------------------------------------------------------- build


def artifacts(L: Layout | None = None, config: str = "Release", platforms: tuple[str, ...] = PLATFORMS) -> list[list]:
    """Rows ``[platform, path, exists, size, mtime]`` for the key outputs."""
    L = L or layout()
    rows = []
    for p in platforms:
        for rel in ARTIFACTS[p]:
            if config == "Debug":
                stem, dot, ext = rel.rpartition(".")
                rel = f"{stem}_d.{ext}" if dot else rel
            f = L.bin / rel
            if f.is_file():
                st = f.stat()
                rows.append([p, "Bin/" + rel, True, st.st_size, time.strftime("%Y-%m-%dT%H:%M:%S",
                                                                              time.localtime(st.st_mtime))])
            else:
                rows.append([p, "Bin/" + rel, False, None, None])
    return rows


def _logs(base: Path) -> dict[str, Path]:
    """Log files of one MSBuild step: ``<base>.log``, ``.errors.log``, ``.warnings.log``, ``.console.log``.

    Built by appending (not ``with_suffix``): project names may contain dots (``lua5.1``)."""
    return {k: Path(f"{base}{ext}") for k, ext in (("log", ".log"), ("errors", ".errors.log"),
                                                   ("warnings", ".warnings.log"), ("console", ".console.log"))}


def _msbuild_cmd(msbuild: Path, step: Step, config: str, target: str | None, jobs: int | None,
                 toolset: str | None, no_deps: bool, base: Path, L: Layout) -> list[str]:
    lg = _logs(base)
    cmd = [str(msbuild), str(step.target), f"-p:Configuration={config}", f"-p:Platform={step.platform}",
           f"-m:{jobs}" if jobs else "-m", "-nologo", "-v:m", "-nodeReuse:false",
           f"-flp:logfile={lg['log']};verbosity=normal;encoding=utf-8",
           f"-flp1:logfile={lg['errors']};errorsonly;encoding=utf-8",
           f"-flp2:logfile={lg['warnings']};warningsonly;encoding=utf-8"]
    if step.target.suffix == ".vcxproj":
        cmd.append(f"-p:SolutionDir={L.fork / 'Build'}\\")
        if no_deps:
            cmd.append("-p:BuildProjectReferences=false")
    if target:
        cmd.append(f"-t:{target}")
    if toolset:
        cmd.append(f"-p:PlatformToolset={toolset}")
    if jobs:
        cmd.append(f"-p:CL_MPCount={jobs}")
    return cmd


def build(project: str = "all", platform: str | None = None, config: str = "Release", target: str | None = None,
          jobs: int | None = None, toolset: str | None = None, no_deps: bool = False, regen: str = "auto",
          max_errors: int = 30, timeout: float = 3600) -> dict:
    """Build with MSBuild; returns summary rows, errors and artifacts (see module doc)."""
    L = layout()
    _guard_build_paths(L)
    msbuild = find_msbuild()
    if msbuild is None:
        raise SatkError("DEPENDENCY", "MSBuild.exe not found", hint="set [paths] msbuild in satk.toml")
    if config not in ("Release", "Debug", "Nightly"):
        raise SatkError("BAD_PARAMS", f"unknown config {config!r}", did_you_mean=["Release", "Debug", "Nightly"])
    with exclusive("build"):
        regenerated = _ensure_generated(L, regen)
        steps, notes = plan(project, platform, config, L, touched=(regenerated or {}).get("touched"))
        head = git("rev-parse", "HEAD", cwd=L.fork)
        started = time.time()
        rows: list[list] = []
        errors: list[Diag] = []
        nwarn = 0
        ok = True
        for i, step in enumerate(steps):
            ensure_writable(step.target)
            ensure_writable(step.target.parent)
            report_progress(i, len(steps), f"{step.label} {config}|{step.platform}")
            base = L.logs / f"{stamp()}-{safe_name(step.label)}-{config}-{step.platform}"
            lg = _logs(base)
            log = lg["log"]
            for path in lg.values():
                ensure_writable(path)
            log.parent.mkdir(parents=True, exist_ok=True)
            cmd = _msbuild_cmd(msbuild, step, config, target, jobs, toolset, no_deps, base, L)
            r = run(cmd, cwd=L.fork, env=build_env(), log=lg["console"], timeout=timeout)
            try:
                text = lg["errors"].read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                text = ""
            errs, _ = parse_msbuild_log(text + "\n" + (r.out if r.code else ""), L.fork)
            try:
                wtext = lg["warnings"].read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                wtext = ""
            _, warns = parse_msbuild_log(wtext, L.fork)
            nwarn += len(warns)
            errors.extend(errs)
            step_ok = r.code == 0 and not r.timed_out
            ok = ok and step_ok
            rows.append([step.label, step.platform, config, r.code, r.seconds, len(errs), len(warns), jpath(log)])
            if not step_ok and not errs and r.timed_out:
                errors.append(Diag("error", "", None, "TIMEOUT", f"MSBuild killed after {timeout:.0f}s", step.label))
        report_progress(len(steps), len(steps), "build done")
        # Remember "everything of this platform is built as of <rev, started>" for --project changed:
        # after a whole-solution build, or after a 'changed' build that covered its platform.
        if ok and target in (None, "Build", "Rebuild"):
            covered = {s.platform for s in steps if s.label == "sln"}
            if project.strip().lower() == "changed":
                covered |= {s.platform for s in steps}
            upd = {f"{config}|{p}": {"rev": head, "ts": started,
                                     "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(started))}
                   for p in covered}
            if upd:
                _update_state(L, "builds", upd)
    plats = tuple(sorted({s.platform for s in steps}, key=PLATFORMS.index)) or PLATFORMS
    out: dict = {
        "ok": ok,
        "project": project,
        "cols": ["step", "platform", "config", "exit", "seconds", "errors", "warnings", "log"],
        "rows": rows,
        "n": len(rows),
        "total": len(rows),
        "next": None,
        "errors_cols": ["file", "line", "code", "msg", "project"],
        "errors": [d.row() for d in errors[:max_errors]],
        "errors_total": len(errors),
        "warnings_total": nwarn,
        "artifacts": [r for r in artifacts(L, config, plats)],
        "rev": head[:9],
    }
    if regenerated:
        out["regenerated"] = {"projects": regenerated["projects"], "seconds": regenerated["seconds"],
                              "touched": regenerated.get("touched", [])}
    if notes.get("changed"):
        out["changed"] = notes["changed"]
    warn = list(notes.get("warn", [])) + list((regenerated or {}).get("warn", []))
    if warn:
        out["warn"] = warn
    if not steps:
        out["note"] = "nothing to build"
    if not ok:
        first = errors[0] if errors else None
        msg = (f"build failed: {len(errors)} error(s); first: {first.file}({first.line}): {first.code}: {first.msg}"
               if first else "build failed (see log)")
        raise SatkError("EXTERNAL_TOOL", msg[:600], hint=f"full log: {rows[-1][-1] if rows else ''}",
                        data={k: out[k] for k in ("cols", "rows", "errors_cols", "errors", "errors_total")})
    return out


# --------------------------------------------------------------------------- rc test


def rc_test(control: bool = False) -> dict:
    """Compile the 4 client ``.rc`` files with ``rc.exe`` + the afxres shim (no MSBuild)."""
    L = layout()
    rc = find_rc()
    if rc is None:
        raise SatkError("DEPENDENCY", "rc.exe (Windows SDK) not found", hint="install the Windows 10/11 SDK")
    if not L.afxres.is_file():
        raise SatkError("NOT_READY", f"shim missing: {jpath(L.afxres)}", hint="satk engine setup")
    outdir = L.build_dir / "rc-test"
    ensure_writable(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    rows = []
    ok = True
    cases = [(f, True) for f in RC_FILES]
    if control:
        cases.append((RC_FILES[0], False))
    for rel, with_shim in cases:
        src = L.fork / rel
        res = outdir / (safe_name(Path(rel).stem) + ("" if with_shim else ".noshim") + ".res")
        res.unlink(missing_ok=True)
        cmd = [str(rc.exe), "/nologo"]
        if with_shim:
            cmd += ["/i", str(L.shims)]
        for inc in rc.includes:
            cmd += ["/i", str(inc)]
        cmd += ["/fo", str(res), str(src)]
        r = run(cmd, cwd=src.parent, timeout=120)
        compiled = r.code == 0 and res.is_file()
        err = ""
        if not compiled:
            m = re.search(r"(error RC\d+:.*)", r.out)
            err = (m.group(1) if m else r.out.strip()[-200:]).strip()
        if with_shim:
            ok = ok and compiled
            rows.append([rel, "shim", compiled, r.code, res.stat().st_size if compiled else None, err or None])
        else:
            expected_fail = (not compiled) and "afxres.h" in err
            rows.append([rel, "control(no shim)", compiled, r.code, None, err or None])
            if not expected_fail:
                ok = False
    out = {
        "ok": ok,
        "cols": ["rc", "mode", "compiled", "exit", "res_bytes", "error"],
        "rows": rows,
        "n": len(rows),
        "total": len(rows),
        "next": None,
        "rc": str(rc.exe).replace("\\", "/"),
        "sdk": rc.version,
        "shim": jpath(L.afxres),
        "out": jpath(outdir),
    }
    if not ok:
        raise SatkError("EXTERNAL_TOOL", "rc.exe could not compile all client .rc files with the shim",
                        data={"cols": out["cols"], "rows": rows})
    return out
