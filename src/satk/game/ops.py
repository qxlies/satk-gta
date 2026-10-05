"""Operations of satk.game (owner WP-01, SPEC §4.1): verify, info, clone, protect, unprotect, exe.

All are CLI-only (SPEC §4.6: no MCP tools for ``game``). Also registers the ``game`` section
of ``satk status`` and the ``game_copy`` check of ``satk doctor``. Standard library only.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from satk.core.envelope import clamp_limit
from satk.core.errors import SatkError
from satk.core.paths import atomic_write, cfg, jpath, work
from satk.core.registry import doctor_check, op, report_progress, status_provider

from . import manifests as M

_STOCK_OK = ("hoodlum-stock", "mta-canonical")


def _abs(p: str | os.PathLike) -> Path:
    return Path(os.path.abspath(os.path.expandvars(os.fspath(p))))


def _arg(p: str) -> str:
    """A path argument with variables expanded but otherwise untouched: write targets are
    canonicalized and checked by :mod:`satk.game.guard`, which needs the original spelling."""
    return os.path.expandvars(p)


def _game_root(root: str | None) -> Path:
    return _abs(root) if root else cfg().paths.game


def _progress(done: int, total: int, msg: str | None = None) -> None:
    report_progress(done, total, msg)


def _problem_rows(problems, limit: int) -> list[list]:
    return [p.row() for p in problems[:limit]]


# --------------------------------------------------------------------------- verify


@op("game.verify",
    summary="Verify a game copy: stock manifest (default) or the original install vs its audit. "
            "Fast by default (IMG/audio by size+mtime, the rest by SHA-256); --deep hashes everything.",
    summary_ru="Проверить копию игры по стоковому манифесту (или оригинал по аудиту). "
               "По умолчанию быстро (IMG/аудио по size+mtime), --deep — sha256 всех файлов.",
    mcp=False, long_running=True,
    examples=("satk game verify", "satk game verify --deep",
              'satk game verify --root "<workspace>/GTA San Andreas" --against install --scope stock'))
def game_verify(root: str | None = None, deep: bool = False,
                against: Literal["stock", "install"] = "stock",
                scope: Literal["stock", "all"] = "stock",
                jobs: int = 0, limit: int = 20, manifest: str | None = None) -> dict:
    """Compare a game directory with a manifest; error REVISION lists the differences.

    Args:
        root: game directory (default: paths.game = gta-sa-clean; with --against install:
            paths.installed).
        deep: hash every file instead of trusting size+mtime of IMG and audio files.
        against: stock = the 416-file clean set (data/manifests/stock-1.0us-hoodlum.json) plus a
            cross-check of <root>/MANIFEST.sha256; install = the audit of the original install
            (development checkouts only; elsewhere pass --manifest).
        scope: stock = only the 416 stock files (with --against stock extra files are only a
            warning); all = every file of the manifest and extra files are errors (with
            --against install: logs of SA-MP/sanext change while playing).
        jobs: hashing threads (0 = default 4).
        limit: max rows of differences in the error report.
        manifest: alternative manifest JSON (tests, other game versions).
    """
    from .verify import check_clean_manifest, check_tree

    limit = clamp_limit(limit)
    if against == "stock":
        ref = M.load_stock(manifest)
        r = _game_root(root)
        rep = check_tree(r, ref, deep=deep, jobs=jobs or None, scan_extra=True, progress=_progress)
        mstatus, warns = check_clean_manifest(r, ref)
        out: dict = {"root": jpath(r), "files": rep.checked, "bytes": rep.bytes, "mismatch": rep.mismatch,
                     "missing": rep.missing, "extra": len(rep.extra)}
        exe_sha = rep.hashes.get(M.key("gta_sa.exe"))
        if exe_sha:
            from .exe import KNOWN_EXE

            v = KNOWN_EXE.get(exe_sha)
            out["exe"] = v.name if v else "unknown"
        out["manifest"] = mstatus
        try:
            from .protect import summary

            out["protected"] = summary(r)
        except SatkError:  # pragma: no cover
            pass
        failing = [p.row() for p in rep.problems]
        if rep.extra:
            if scope == "all":
                failing += [[x, "extra", None, None] for x in rep.extra]
            else:
                shown = ", ".join(rep.extra[:5]) + (", ..." if len(rep.extra) > 5 else "")
                warns.append(f"EXTRA_FILES: {len(rep.extra)} file(s) not in the stock manifest: {shown}")
    else:
        inst = M.load_install(manifest)
        r = _abs(root) if root else cfg().paths.installed
        ref = inst if scope == "all" else inst.subset(lambda e: M.classify(e.path) in ("stock", "restore"))
        rep = check_tree(r, ref, deep=deep, jobs=jobs or None, scan_extra=(scope == "all"), ignore=(),
                         progress=_progress)
        out = {"root": jpath(r), "checked": rep.checked, "changed": rep.mismatch, "missing": rep.missing}
        if scope == "all":
            out["extra"] = len(rep.extra)
        warns = []
        failing = [p.row() for p in rep.problems] + [[x, "extra", None, None] for x in rep.extra]
    out.update(mode="deep" if deep else "fast", hashed=rep.hashed, fast=rep.fast, seconds=round(rep.seconds, 1))
    if failing:
        data = dict(out)
        if warns:
            data["warn"] = warns
        data.update(cols=["path", "problem", "expected", "actual"], rows=failing[:limit])
        raise SatkError(
            "REVISION",
            f"{jpath(r)} differs from the {against} manifest: {len(failing)} problem(s)"
            f" ({rep.mismatch} changed, {rep.missing} missing"
            + (f", {len(failing) - len(rep.problems)} extra" if len(failing) > len(rep.problems) else "") + ")",
            hint=("compare with: satk game verify --deep; a fresh copy: satk game clone --dst <new dir>"
                  if against == "stock" else "the original install changed since the audit (2026-10-04)"),
            data=data,
        )
    if warns:
        out["warn"] = warns
    return out


# --------------------------------------------------------------------------- info


@op("game.info",
    summary="Describe a game directory: gta_sa.exe variant (stock/no-intro/MTA/compact), vorbisFile, ASI count, "
            "non-stock files, IMG protection.",
    summary_ru="Что за каталог игры: вариант gta_sa.exe, vorbisFile, число ASI, нестоковые файлы, защита IMG.",
    mcp=False,
    examples=("satk game info", 'satk game info --root "<game folder>" --table'))
def game_info(root: str | None = None, manifest: str | None = None) -> dict:
    """Identify the executable and list what is not part of the stock 1.0 US file set.

    Args:
        root: game directory (default: paths.game = gta-sa-clean; without it paths.game_root).
        manifest: alternative stock manifest JSON.
    """
    from .verify import info

    if root is None and not cfg().paths.game.is_dir() and (gr := cfg().paths.get("game_root")) is not None             and gr.is_dir():
        root = str(gr)  # no clean copy: describe the user's game folder
    return info(_game_root(root), M.load_stock(manifest))


# --------------------------------------------------------------------------- clone


@op("game.clone",
    summary="Recreate the clean copy from the original install into a NEW directory (EXISTS otherwise); "
            "source read-only, every file hash-checked, exe restored in memory.",
    summary_ru="Воспроизвести чистую копию из оригинала в НОВЫЙ каталог: источник только читается, "
               "каждый файл сверяется по хэшу, exe восстанавливается в памяти.",
    mcp=False, long_running=True,
    examples=("satk game clone --dst <workspace>/work/tmp/wp-01/clone --dry-run",
              "satk game clone --dst <workspace>/work/copies/gta-sa-mta --exe mta --protect"))
def game_clone(src: str | None = None, dst: str | None = None, exe: Literal["stock", "mta"] = "stock",
               dry_run: bool = False, protect: bool = False, jobs: int = 2,
               manifest: str | None = None) -> dict:
    """Copy the 416 stock files to dst via dst.partial, write MANIFEST.sha256, rename atomically.

    Args:
        src: original install (default: paths.installed) or a verified clean copy; opened
            read-only only.
        dst: new directory under work/ (default: paths.game, which already exists -> EXISTS);
            the clean copy root itself may be recreated, anything inside it, the install, src,
            a git working tree, a network path or a place outside work/ is PROTECTED_PATH.
        exe: gta_sa.exe variant to write: stock HOODLUM or MTA-canonical (PE checksum fixed).
        dry_run: only validate and count; nothing is written.
        protect: make the IMG archives of the new copy read-only afterwards.
        jobs: parallel file copies.
        manifest: alternative stock manifest JSON.
    """
    from .clone import clone

    s = _arg(src) if src else cfg().paths.installed
    d = _arg(dst) if dst else cfg().paths.game
    res = clone(s, d, M.load_stock(manifest), exe_variant=exe, dry_run=dry_run, jobs=max(1, jobs),
                progress=_progress)
    if protect and not dry_run:
        from .protect import set_protected

        res["protected"] = set_protected(res["dst"], True)["protected"]
    return res


# --------------------------------------------------------------------------- protect


@op("game.protect",
    summary="Make the 8 IMG archives of the clean copy read-only (Ariane/game read them with 'rb').",
    summary_ru="Сделать 8 IMG-архивов чистой копии только для чтения (Ariane и игра читают их через 'rb').",
    mcp=False, examples=("satk game protect",))
def game_protect(root: str | None = None) -> dict:
    """Set FILE_ATTRIBUTE_READONLY on models/{gta3,gta_int,player,cutscene}.img, anim/{anim,cuts}.img,
    data/paths/carrec.img, data/script/script.img.

    Args:
        root: game directory: paths.game (default) or a copy under work/; the original
            install, src and their aliases are refused.
    """
    from .protect import set_protected

    return set_protected(_arg(root) if root else cfg().paths.game, True)


@op("game.unprotect",
    summary="Clear the read-only attribute of the 8 IMG archives of the clean copy.",
    summary_ru="Снять атрибут «только чтение» с 8 IMG-архивов чистой копии.",
    mcp=False, examples=("satk game unprotect",))
def game_unprotect(root: str | None = None) -> dict:
    """Clear FILE_ATTRIBUTE_READONLY on the 8 IMG archives (see game protect).

    Args:
        root: game directory: paths.game (default) or a copy under work/; the original
            install, src and their aliases are refused.
    """
    from .protect import set_protected

    return set_protected(_arg(root) if root else cfg().paths.game, False)


# --------------------------------------------------------------------------- exe


@op("game.exe",
    summary="Write the stock or MTA-canonical gta_sa.exe (restored in memory from the clean copy or the "
            "install) to --out, outside any game folder.",
    summary_ru="Записать стоковый или MTA-канонический gta_sa.exe (восстановленный в памяти) в --out "
               "вне каталогов игры.",
    mcp=False,
    examples=("satk game exe --variant mta --out <workspace>/work/re/bin/gta_sa_mta.exe",
              "satk game exe --variant stock"))
def game_exe(variant: Literal["stock", "mta"] = "mta", out: str | None = None, src: str | None = None,
             force: bool = False) -> dict:
    """Restore an executable variant; the result is hash-checked before it is written.

    Args:
        variant: stock = 1.0 US HOODLUM (a559aa77...); mta = MTA canonical (f63fa623..., PE checksum fixed).
        out: output file under work/ (default: work/re/bin/gta_sa_<variant>.exe); game
            folders, git working trees, network paths and places outside work/ are refused.
        src: input executable (default: gta-sa-clean/gta_sa.exe, else the original install's).
        force: overwrite an existing different file.
    """
    from . import exe as X
    from .guard import write_target

    # the target first: a refused --out costs nothing and reads no game file
    op_ = write_target(_arg(out) if out else work("re", "bin", f"gta_sa_{variant}.exe"), what="--out").path
    if src:
        sp = _abs(src)
    else:
        sp = cfg().paths.game / "gta_sa.exe"
        if not sp.is_file():
            sp = cfg().paths.installed / "gta_sa.exe"
    data = X.read_exe(sp)
    src_variant = X.identify(data)["variant"]
    img = X.build(data, variant)
    sha = X.sha256(img)
    written = True
    if op_.exists():
        if op_.is_dir():
            raise SatkError("EXISTS", f"{jpath(op_)} is a directory", hint="--out <file path>")
        with open(op_, "rb") as f:
            cur = X.sha256(f.read())
        if cur == sha:
            written = False
        elif not force:
            raise SatkError("EXISTS", f"{jpath(op_)} exists with other content ({cur[:12]}...)",
                            hint="--force to overwrite, or another --out")
    if written:
        atomic_write(op_, img)
    return {"out": jpath(op_), "variant": X.KNOWN_EXE[sha].name, "sha256": sha, "size": len(img),
            "src": jpath(sp), "src_variant": src_variant, "written": written}


# --------------------------------------------------------------------------- status / doctor


def _quick(root: Path) -> dict:
    """Cheap state of the clean copy: stat sizes of all stock files, hash only the exe."""
    from . import exe as X
    from .protect import summary

    stock = M.load_stock()
    bad = 0
    for e in stock:
        try:
            if os.stat(M.to_path(root, e.path)).st_size != e.size:
                bad += 1
        except OSError:
            bad += 1
    try:
        variant = X.identify(X.read_exe(root / "gta_sa.exe"))["variant"]
    except SatkError:
        variant = "missing"
    return {"files": len(stock) - bad, "bad": bad, "exe": variant, "protected": summary(root)}


def _user_game() -> Path | None:
    """``paths.game_root`` when it is a game folder (used when there is no clean copy)."""
    gr = cfg().paths.get("game_root")
    return gr if gr is not None and any((gr / n).is_file() for n in ("gta_sa.exe", "gta-sa.exe")) else None


@status_provider("game")
def _status(deep: bool = False) -> dict:
    root = cfg().paths.game
    if not root.is_dir():
        gr = _user_game()
        if gr is not None:  # no clean copy: the user's game is what the tools read (profile game)
            return {"root": jpath(gr), "ok": True, "clean_copy": False, "profile": "game"}
        return {"root": jpath(root), "ok": False, "error": "game copy not found"}
    q = _quick(root)
    out = {"root": jpath(root), "ok": q["bad"] == 0 and q["exe"] in _STOCK_OK, "exe": q["exe"],
           "files": q["files"], "protected": q["protected"]}
    if deep:
        from .verify import check_tree

        rep = check_tree(root, M.load_stock(), deep=False, scan_extra=True)
        out["ok"] = out["ok"] and not rep.problems
        out["verify"] = {"mismatch": rep.mismatch, "missing": rep.missing, "extra": len(rep.extra),
                         "seconds": round(rep.seconds, 1)}
    return out


@doctor_check("game_copy")
def _doctor() -> dict:
    root = cfg().paths.game
    if not root.is_dir():
        gr = _user_game()
        if gr is not None:
            return {"status": "warn", "msg": f"no clean copy at {jpath(root)} (not configured): the tools read "
                                             f"the game folder {jpath(gr)} (profile vanilla = game)",
                    "fix": "satk init --clean-copy (stock 1.0 US only)"}
        return {"status": "fail", "msg": f"clean game copy not found: {jpath(root)}",
                "fix": f"satk game clone --dst {jpath(root)} (or satk init)"}
    try:
        q = _quick(root)
    except SatkError as e:  # pragma: no cover - e.g. broken manifest data
        return {"status": "fail", "msg": f"{e.code}: {e.msg}", "fix": "satk game verify"}
    if q["bad"]:
        return {"status": "fail", "msg": f"{q['bad']} stock file(s) missing or with a wrong size in {jpath(root)}",
                "fix": "satk game verify --deep"}
    if q["exe"] not in _STOCK_OK:
        return {"status": "warn", "msg": f"gta_sa.exe is {q['exe']}, expected hoodlum-stock or mta-canonical",
                "fix": "satk game exe --variant stock --out <file>, then replace it manually"}
    if not q["protected"].startswith("8/"):
        return {"status": "warn", "msg": f"IMG archives read-only: {q['protected']}", "fix": "satk game protect"}
    return {"status": "ok", "msg": f"{jpath(root)}: {q['files']} stock files, exe {q['exe']}, "
                                   f"IMG read-only {q['protected']}", "fix": None}
