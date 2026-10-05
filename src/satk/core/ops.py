"""Core operations: ``version``, ``config show``, ``dev guard-test``, ``dev assetguard`` (WP-00);
doctor checks ``ops_import`` and ``package_data``."""

from __future__ import annotations

import platform
import sys
from pathlib import Path
from typing import Literal

from .. import __version__
from . import assetguard as _ag
from . import resources
from .config import REPO_ROOT, SRC_ROOT
from .errors import SatkError
from .paths import cfg, ensure_removable, ensure_writable, jpath
from .registry import doctor_check, import_errors, op


@doctor_check("ops_import")
def _check_ops_import() -> dict:
    """All ``satk.<pkg>.ops`` modules imported (SPEC §3.1 ``discover``)."""
    errs = import_errors()
    if not errs:
        return {"status": "ok", "msg": "all satk.<pkg>.ops modules imported", "fix": None}
    first = next(iter(sorted(errs)))
    return {
        "status": "fail",
        "msg": f"{len(errs)} ops module(s) failed to import; first: {first}: {errs[first]}",
        "fix": "run: python -X utf8 -c \"import " + first + "\" to see the traceback",
    }


@doctor_check("package_data")
def _check_package_data() -> dict:
    """Data files shipped with satk (``data/``: exe variants, game manifests) are readable."""
    inf = resources.info()
    where = f"{inf['root']} ({inf['source']})"
    try:
        db = resources.read_json("exe_versions.json")
        if not resources.list_files("manifests", pattern="*.json"):
            raise SatkError("NOT_FOUND", "no game manifests in data/manifests", hint=resources.HINT)
    except SatkError as e:
        return {"status": "fail", "msg": f"satk data incomplete: {e.msg} ({where})", "fix": e.hint}
    return {"status": "ok", "msg": f"{inf['files']} data files, {len(db.get('variants', []))} exe variants: {where}",
            "fix": None}


def _git_head(repo: Path) -> tuple[str | None, str | None]:
    """(branch, short commit) read from ``.git`` files directly (no subprocess)."""
    try:
        g = repo / ".git"
        if g.is_file():
            line = g.read_text(encoding="utf-8").strip()
            if not line.startswith("gitdir:"):
                return None, None
            gitdir = Path(line[7:].strip())
            if not gitdir.is_absolute():
                gitdir = (repo / gitdir).resolve()
        elif g.is_dir():
            gitdir = g
        else:
            return None, None
        common = gitdir
        cfile = gitdir / "commondir"
        if cfile.is_file():
            c = Path(cfile.read_text(encoding="utf-8").strip())
            common = c if c.is_absolute() else (gitdir / c).resolve()
        head = (gitdir / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref:"):
            return None, head[:9]
        ref = head[4:].strip()
        branch = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
        for d in (gitdir, common):
            f = d / ref
            if f.is_file():
                return branch, f.read_text(encoding="utf-8").strip()[:9]
        packed = common / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(encoding="utf-8").splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1] == ref:
                    return branch, parts[0][:9]
        return branch, None  # unborn branch
    except OSError:
        return None, None


@op("version", summary="Show satk, Python and checkout versions.",
    summary_ru="Версия satk, Python и рабочей копии.", mcp=False,
    examples=("satk version",))
def version() -> dict:
    """Version information (fast: no subprocesses, no optional imports)."""
    branch, commit = _git_head(REPO_ROOT)
    return {
        "satk": __version__,
        "python": platform.python_version(),
        "impl": platform.python_implementation(),
        "exe": jpath(sys.executable),
        "venv": sys.prefix != sys.base_prefix,
        "repo": jpath(REPO_ROOT),
        "src": jpath(SRC_ROOT),
        "branch": branch,
        "commit": commit,
        "data": jpath(resources.data_root()),
    }


@op("config.show", summary="Show the effective configuration (defaults + satk.toml + SATK_* env).",
    summary_ru="Показать действующую конфигурацию (умолчания + satk.toml + переменные SATK_*).",
    mcp=False, examples=("satk config show", "satk config show --section paths --table"))
def config_show(section: Literal["paths", "safety", "profiles", "index", "layers", "viewer"] | None = None) -> dict:
    """Effective configuration with absolute forward-slash paths.

    Args:
        section: show only this section.
    """
    c = cfg()
    d = c.as_dict()
    warn = {"warn": list(c.warnings)} if c.warnings else {}
    if section:
        return {"section": section, section: d.get(section, {}), "sources": c.sources, **warn}
    return {**d, "sources": c.sources, **warn}


@op("dev.guard_test", summary="Check whether satk may write to a path (PROTECTED_PATH if not). Creates nothing.",
    summary_ru="Проверить, можно ли satk писать по пути (иначе PROTECTED_PATH). Ничего не создаёт.",
    mcp=False, group="dev",
    examples=('satk dev guard-test "<workspace>/GTA San Andreas/x.tmp"',))
def guard_test(path: str, remove: bool = False) -> dict:
    """Run the write guard on ``path`` without touching the file system.

    Args:
        path: file or directory path to test.
        remove: test removal/rename safety too (refuse ancestors of protected roots).
    """
    p = ensure_removable(path) if remove else ensure_writable(path)
    result = {"path": jpath(p), "writable": True}
    if remove:
        result["removable"] = True
    return result


@op("dev.assetguard",
    summary="Reject files that look like game assets, images outside docs/img or reference code (pre-commit).",
    summary_ru="Отклонить файлы, похожие на ассеты игры, картинки вне docs/img и код-справочники (pre-commit).",
    mcp=False, group="dev",
    examples=("satk dev assetguard tests/core/data/fake_rw.bin", "satk dev assetguard --staged",
              "satk dev assetguard --all"))
def assetguard(files: list[str] | None, all: bool = False, staged: bool = False,  # noqa: A002
               allowlist: bool | None = None, repo: str | None = None) -> dict:
    """Scan files for game assets; exit 1 (ASSET_GUARD) with a table of violations.

    Args:
        files: files or directories to check (strict: allowlist off by default).
        all: check all tracked and untracked-not-ignored files of the repository.
        staged: check the staged (index) content, as the pre-commit hook does.
        allowlist: honor .assetguard-allow (default: on for --all/--staged, off for files).
        repo: repository root (default: this checkout).
    """
    root = Path(repo).resolve() if repo else REPO_ROOT
    modes = sum(bool(x) for x in (files, all, staged))
    if modes != 1:
        raise SatkError("BAD_PARAMS", "give files, or --all, or --staged (exactly one)",
                        hint="satk dev assetguard -h")
    if staged:
        mode = "staged"
        n, found = _ag.scan_staged(root, use_allowlist=True if allowlist is None else allowlist)
    elif all:
        mode = "all"
        n, found = _ag.scan_all(root, use_allowlist=True if allowlist is None else allowlist)
    else:
        mode = "files"
        n, found = _ag.scan_files(files or [], repo=root, use_allowlist=bool(allowlist))
    if found:
        files_bad = sorted({f.path for f in found})
        raise SatkError(
            "ASSET_GUARD",
            f"{len(files_bad)} file(s) look like game assets or forbidden content",
            hint="unstage them (git restore --staged <file>); synthetic fixtures go to .assetguard-allow with sha256",
            data={"mode": mode, "checked": n, "cols": ["path", "rule", "detail"],
                  "rows": [[f.path, f.rule, f.detail] for f in found]},
        )
    return {"mode": mode, "checked": n, "violations": 0}
