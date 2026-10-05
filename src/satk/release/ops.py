"""``satk dev release``, ``satk dev export-public`` and the doctor checks ``portable`` and ``app_control``."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Literal

from ..core import config as _config
from ..core.envelope import with_warn
from ..core.errors import SatkError
from ..core.paths import atomic_write, cfg, ensure_removable, ensure_writable, jpath, tmp
from ..core.registry import doctor_check, op
from ..mcp.generic import cli_only


@cli_only("it builds a release for minutes, may download from python.org and PyPI, and --relock rewrites "
          "packaging/ of the checkout")
@op("dev.release",
    summary="Build the portable Windows release from a git ref: satk-<ver>-win64.zip (embeddable CPython 3.12 + "
            "pinned wheels + satk), the satk-gta wheel and sdist, SHA256SUMS.txt; then smoke-test the unpacked zip "
            "in a path with Cyrillic letters and spaces.",
    summary_ru="Собрать переносимый релиз из git-ревизии: zip с embeddable CPython 3.12, колёсами и satk, wheel и "
               "sdist satk-gta, SHA256SUMS.txt; затем проверить распакованный zip в папке с кириллицей и пробелом.",
    mcp=False, group="dev", long_running=True,
    examples=("satk dev release", "satk dev release --smoke game", "satk dev release --offline --smoke none",
              "satk dev release --relock", "satk dev release --sandbox prepare",
              "satk dev release --public --repo <work>/out/public/satk-gta-<version>"))
def release(ref: str = "HEAD", out: str | None = None, smoke: Literal["none", "quick", "game"] = "quick",
            game: str | None = None, offline: bool = False, relock: bool = False,
            sandbox: Literal["none", "prepare", "run"] = "none", public: bool = False,
            repo: str | None = None) -> dict:
    """Build the release files into <work>/out/release/satk-<ver>/ (or --out).

    Files listed in data/public-exclude.txt of the ref are never packed; the built zip, wheel and sdist are
    read back to check it.

    Args:
        ref: git commit, branch or tag to package; uncommitted changes are never packed.
        out: output directory (default: <work>/out/release/satk-<version>).
        smoke: after the build, unpack the zip into <work>/tmp/release/ and run it with a clean
            environment. quick = version, config, doctor, mcp selftest; game = also init, index build and a
            first asset find on the game folder (--game); none = skip.
        game: game folder for --smoke game and the sandbox (default: the clean copy, paths.game).
        offline: never download; every file of the lock must already be in <work>/cache/release.
        relock: rebuild packaging/release-lock.json first (dependency closure from this venv, hashes from
            PyPI and python.org; needs the network unless cached) and write it into this checkout.
        sandbox: prepare = write a Windows Sandbox config (.wsb) that unpacks the zip and builds the index
            on a read-only game copy; run = also start it and wait for the result (needs Windows Sandbox).
        public: a public release: the files of the ref must pass the audit of satk dev export-public, and
            the built files must hold no path of the development machine.
        repo: git checkout to build from (default: this checkout), e.g. the public snapshot written by
            satk dev export-public, so that RELEASE.json names the public commit.
    """
    from ..core.config import REPO_ROOT
    from ..core.log import get_logger
    from . import build as B
    from . import lock as L
    from . import public as P
    from .tree import dirty_files, export, read_version

    log = get_logger("release")
    warns: list[str] = []
    t0 = time.perf_counter()
    work = Path(os.path.abspath(cfg().paths.work))
    cache = ensure_writable(work / "cache" / "release")
    cache.mkdir(parents=True, exist_ok=True)
    source = Path(os.path.abspath(repo)) if repo else REPO_ROOT
    if repo and relock:
        raise SatkError("BAD_PARAMS", "--relock writes packaging/ of this checkout; it cannot be combined with --repo",
                        hint="relock in the development checkout, commit, export again")
    lock_file = REPO_ROOT / L.LOCK_PATH
    if relock:
        data = L.relock(REPO_ROOT, cache, tmp("release"), offline=offline)
        atomic_write(lock_file, L.render(data))
        warns.append(f"RELOCKED: wrote {L.LOCK_PATH} and built with it; commit it so that builds of the ref "
                     "use it too")
    tree = export(source, ref)
    version = read_version(tree)
    dirty = dirty_files(source) if ref == "HEAD" else []
    if dirty:
        warns.append(f"DIRTY: {len(dirty)} uncommitted change(s) are not in the release (packed {tree.commit[:9]})")
    rules = P.load_rules(tree)
    tree, dropped = P.apply_excludes(tree, rules)
    if public:
        findings = P.audit(tree, rules, dropped=[d[0] for d in dropped])
        if findings:
            rows = P.group(findings)
            raise SatkError("CHECK_FAILED", f"{len(findings)} audit finding(s) in {len({r[0] for r in rows})} file(s) "
                            f"of {tree.commit[:9]}: not publishable",
                            hint="satk dev export-public --check-only lists them; fix them or extend "
                                 f"{P.EXCLUDE_FILE}",
                            data={"cols": ["path", "rule", "hits", "line", "text"], "rows": rows[:50],
                                  "total": len(rows)})
    lock = L.parse(tree.text(L.LOCK_PATH)) if L.LOCK_PATH in tree.files and not relock else L.load(lock_file)
    dest = Path(out) if out else work / "out" / "release" / f"satk-{version}"
    dest = ensure_writable(Path(os.path.abspath(dest)))
    dest.mkdir(parents=True, exist_ok=True)
    zip_path, manifest = B.build_zip(tree, lock, cache, dest, offline=offline, ref=ref, log=log.info)
    dists = B.build_dists(tree, dest)
    sums = B.sha256sums([zip_path, *dists], dest / "SHA256SUMS.txt")
    res: dict = {
        "ok": True, "version": version, "commit": tree.commit[:9], "out": jpath(dest),
        "files": [{"name": n, "mb": round((dest / n).stat().st_size / 1e6, 1), "sha256": h} for n, h in sums.items()],
        "zip": {"entries": manifest["files"], "unpacked_mb": manifest["unpacked_mb"], "python": manifest["python"],
                "wheels": len(manifest["wheels"])},
    }
    if repo:
        res["repo"] = jpath(source)
    leaks = P.check_release_files([zip_path, *dists], rules, strict=public)
    res["excluded"] = {"files": [d[0] for d in dropped], "checked": [zip_path.name, *[d.name for d in dists]],
                       "public": public}
    if leaks:
        raise SatkError("CHECK_FAILED", f"{len(leaks)} problem(s) in the built files: "
                        f"{', '.join(sorted({f.rule for f in leaks}))}",
                        hint=f"excluded files come from {P.EXCLUDE_FILE}; machine paths must leave the repository",
                        data={**res, "cols": ["entry", "line", "rule", "text"], "rows": [f.row() for f in leaks[:50]],
                              "total": len(leaks)})
    game_dir = Path(game) if game else cfg().paths.get("game")
    if smoke != "none":
        if smoke == "game" and (game_dir is None or not Path(game_dir).is_dir()):
            raise SatkError("NOT_FOUND", f"no game folder for --smoke game: {jpath(game_dir) if game_dir else '-'}",
                            hint="pass --game <folder with gta_sa.exe>")
        place = tmp("release") / "smoke" / "\u0422\u0435\u0441\u0442 satk"  # "Test satk" in Russian
        steps = B.smoke(zip_path, place, version=version, game=Path(game_dir) if smoke == "game" else None,
                        log=log.info)
        res["smoke"] = {"cols": ["step", "ok", "seconds", "summary"], "rows": [s.row() for s in steps]}
        failed = [s.name for s in steps if not s.ok]
        if failed:
            raise SatkError("CHECK_FAILED", f"release smoke failed: {', '.join(failed)}",
                            hint="rerun the step with the unpacked satk.cmd to see the error", data=res)
    if sandbox != "none":
        from . import sandbox as S

        if game_dir is None or not Path(game_dir).is_dir():
            raise SatkError("NOT_FOUND", "the sandbox check needs a game folder", hint="pass --game <folder>")
        res["sandbox"] = S.prepare_and_run(zip_path, Path(game_dir), dest / "sandbox", tree.files[S.SCRIPT],
                                           run=sandbox == "run", log=log.info)
        warns += res["sandbox"].pop("warn", [])
    res["seconds"] = round(time.perf_counter() - t0, 1)
    return with_warn(res, *warns) if warns else res


@cli_only("it writes a git repository under work/out/public, may clone the public repository and runs the test "
          "suite for minutes")
@op("dev.export_public",
    summary="Export a commit as the public snapshot: drop the files of data/public-exclude.txt, audit the rest "
            "(machine paths, internal plan references, emails, secrets, game assets, big files), write a git "
            "repository with one snapshot commit and tag v<version>, run its non-game tests in a clean environment.",
    summary_ru="Выгрузить коммит как публичный снимок: убрать файлы из data/public-exclude.txt, проверить остальное "
               "(пути машины, внутренние планы, адреса, секреты, ассеты игры, большие файлы), записать git-репозиторий "
               "с одним коммитом и тегом v<версия>, прогнать его тесты без игры в чистом окружении.",
    mcp=False, group="dev", long_running=True,
    examples=("satk dev export-public --check-only", "satk dev export-public",
              "satk dev export-public --base https://github.com/qxlies/satk-gta.git"))
def export_public(ref: str = "HEAD", out: str | None = None, base: str | None = None, check_only: bool = False,
                  tests: bool = True, limit: int = 20) -> dict:
    """Write the public snapshot of a commit into <work>/out/public/satk-gta-<version>/ (or --out).

    Args:
        ref: git commit, branch or tag of this checkout to export; uncommitted changes are never exported.
        out: folder of the snapshot repository (default: <work>/out/public/satk-gta-<version>); an existing
            folder is replaced only when it holds a snapshot written by this command.
        base: the public repository (a local clone or its URL): the snapshot starts from its history and adds one
            commit that replaces the whole tree; without it the snapshot is a fresh repository with one root commit.
        check_only: only filter and audit the commit (nothing but the audit report is written).
        tests: run the snapshot's own test suite (pytest -m "not game") with an empty configuration.
        limit: audit rows (one per file and rule) to return; every finding is in the audit report file.
    """
    from ..core.config import REPO_ROOT
    from ..core.log import get_logger
    from . import public as P
    from .tree import dirty_files, export, read_version

    log = get_logger("release")
    warns: list[str] = []
    t0 = time.perf_counter()
    tree = export(REPO_ROOT, ref)
    version = read_version(tree)
    if ref == "HEAD":
        dirty = dirty_files(REPO_ROOT)
        if dirty:
            warns.append(f"DIRTY: {len(dirty)} uncommitted change(s) are not exported (exported {tree.commit[:9]})")
    rules = P.load_rules(tree)
    kept, dropped = P.apply_excludes(tree, rules)
    warns += [f"UNUSED_EXCLUDE: {p} matches no file of {tree.commit[:9]}" for p in P.unused_patterns(tree, rules)]
    findings = P.audit(kept, rules, dropped=[d[0] for d in dropped])
    work = Path(os.path.abspath(cfg().paths.work))
    report = P.report(findings, work / "out" / "public" / f"satk-gta-{version}.audit.json", version=version,
                      commit=tree.commit, rules=rules.source, files=len(kept.files), dropped=[d[0] for d in dropped])
    res: dict = {
        "ok": True, "version": version, "commit": tree.commit[:9], "rules": rules.source,
        "dropped": {"cols": ["path", "pattern"], "rows": [list(d) for d in dropped]},
        "audit": {"files": len(kept.files), "findings": len(findings), "report": jpath(report)},
    }
    if findings:
        rows = P.group(findings)
        by_rule: dict[str, int] = {}
        for f in findings:
            by_rule[f.rule] = by_rule.get(f.rule, 0) + 1
        res["audit"]["by_rule"] = dict(sorted(by_rule.items()))
        raise SatkError("CHECK_FAILED", f"{len(findings)} audit finding(s) in {len({r[0] for r in rows})} file(s) of "
                        f"{tree.commit[:9]}: the commit is not publishable",
                        hint=f"fix the files (or exclude/allow them in {P.EXCLUDE_FILE}); every line is in "
                             f"{jpath(report)}",
                        data={**res, "cols": ["path", "rule", "hits", "line", "text"], "rows": rows[:max(limit, 0)],
                              "total": len(rows)})
    if check_only:
        res["seconds"] = round(time.perf_counter() - t0, 1)
        return with_warn(res, *warns) if warns else res
    dest = Path(os.path.abspath(out)) if out else work / "out" / "public" / f"satk-gta-{version}"
    res["snapshot"] = P.write_snapshot(kept, REPO_ROOT, dest, version=version, base=base, log=log.info)
    if tests:
        log.info("running the snapshot's test suite")
        from ..core.paths import HARD_PROTECTED

        protect = {*cfg().protected_roots, *(Path(r) for r in HARD_PROTECTED.values())}
        res["tests"] = P.run_tests(dest, tmp("export-public") / "tests", protect=sorted(protect))
        if not res["tests"]["ok"]:
            res["seconds"] = round(time.perf_counter() - t0, 1)
            what = ("wrote into protected folders: " + ", ".join(res["tests"]["touched_protected"][:3])
                    if res["tests"].get("touched_protected") else res["tests"]["summary"])
            raise SatkError("CHECK_FAILED", f"the snapshot's test suite failed: {what}",
                            hint=f"the snapshot stays in {jpath(dest)}; the full output is {res['tests']['log']}",
                            data=res)
    res["next"] = (f"satk dev release --public --repo {jpath(dest)}; push {jpath(dest)} "
                   f"(branch {res['snapshot']['branch']} and tag {res['snapshot']['tag']}) to the public repository")
    res["seconds"] = round(time.perf_counter() - t0, 1)
    return with_warn(res, *warns) if warns else res


# --------------------------------------------------------------------------- doctor


def _blocked(path: Path) -> bool:
    """The file carries the Mark of the Web (an NTFS ``Zone.Identifier`` stream from a download)."""
    if os.name != "nt":
        return False
    try:
        with open(str(path) + ":Zone.Identifier", "rb"):
            return True
    except OSError:
        return False


@doctor_check("portable")
def _check_portable() -> dict:
    """Portable installation (release zip): writable folder, not blocked by Windows, consistent version."""
    root = _config.PORTABLE_ROOT
    if root is None:
        return {"status": "ok", "msg": "not a portable installation (checkout or package)", "fix": None}
    from .. import __version__

    probe = root / "work" / ".write-test"
    try:
        ensure_writable(probe)
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_bytes(b"")
        ensure_removable(probe)
        probe.unlink()
    except (OSError, SatkError) as e:
        return {"status": "fail", "msg": f"the satk folder is not writable ({e}): {jpath(root)}",
                "fix": "unpack satk into a folder you own, e.g. D:\\satk (not Program Files, not the game folder)"}
    blocked = [p.name for p in (root / "satk.cmd", root / "python" / "python.exe") if _blocked(p)]
    if blocked:
        return {"status": "warn", "msg": f"Windows marks {', '.join(blocked)} as downloaded from the internet "
                                         "(security prompts on start)",
                "fix": f"powershell -Command \"Get-ChildItem -LiteralPath '{root}' -Recurse | Unblock-File\""}
    try:
        import json

        rel = json.loads((root / "RELEASE.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        rel = {}
    if rel.get("version") and rel["version"] != __version__:
        return {"status": "warn", "msg": f"RELEASE.json says {rel['version']} but the code is {__version__} "
                                         "(unpacked over another version?)",
                "fix": "unpack the new zip into an empty folder and move work/ and satk.toml there"}
    return {"status": "ok", "msg": f"portable satk {__version__} ({rel.get('commit', '?')[:9]}): {jpath(root)}",
            "fix": None}


#: Compiled extension modules of the runtime wheels (unsigned, as every wheel on PyPI).
EXTENSIONS = (("numpy", "numpy._core._multiarray_umath"), ("Pillow", "PIL._imaging"),
              ("pydantic-core (MCP server)", "pydantic_core._pydantic_core"))
#: ``VerifiedAndReputablePolicyState`` values of Smart App Control.
SAC_STATES = {0: "off", 1: "on", 2: "evaluation"}


def smart_app_control() -> str | None:
    """Smart App Control state (``off``/``on``/``evaluation``), ``None`` when unknown or not Windows."""
    if os.name != "nt":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\CI\Policy") as k:
            v, _ = winreg.QueryValueEx(k, "VerifiedAndReputablePolicyState")
    except OSError:
        return None
    return SAC_STATES.get(int(v), str(v))


def blocked_extensions() -> list[str]:
    """Names of :data:`EXTENSIONS` whose DLL Windows refuses to load (App Control / Smart App Control)."""
    import importlib

    out = []
    for name, mod in EXTENSIONS:
        try:
            importlib.import_module(mod)
        except ImportError as e:
            if "Application Control" in str(e) or "policy has blocked" in str(e):
                out.append(name)
    return out


@doctor_check("app_control")
def _check_app_control() -> dict:
    """Smart App Control can block the unsigned extension modules of numpy, Pillow and pydantic-core."""
    state = smart_app_control()
    if state in (None, "off"):
        return {"status": "ok", "msg": f"Smart App Control {state or 'not present'}", "fix": None}
    blocked = blocked_extensions()
    if not blocked:
        return {"status": "ok", "msg": f"Smart App Control {state}; the extension modules load", "fix": None}
    return {"status": "warn",
            "msg": f"Smart App Control ({state}) blocks the unsigned Python extensions of {', '.join(blocked)}: "
                   "the index, search and exports work; previews, PNG/DXT speed-ups or the MCP server do not",
            "fix": "Windows Security > App & browser control > Smart App Control decides this; changing it is "
                   "your choice (some Windows versions cannot turn it back on without a reinstall); satk never "
                   "changes it"}
