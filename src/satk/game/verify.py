"""Integrity checks of a game copy against a manifest, and ``game info`` (SPEC §4.1).

``check_tree`` compares a directory with a :class:`~satk.game.manifests.Manifest`:

* every expected file must exist with the expected size;
* default (fast) mode: IMG archives and ``audio/`` (4.9 of the 5.0 GB) pass on size + mtime
  equal to the manifest; when the mtime differs they are hashed instead, so a mere ``touch``
  is not reported as damage; every other file is hashed (SHA-256);
* ``deep`` mode hashes everything;
* files present but not in the manifest are reported as ``extra``.

All game files are opened with ``open_ro`` only. Standard library only.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from satk.core.errors import SatkError
from satk.core.paths import jpath

from . import exe as _exe
from .hashing import hash_files
from .manifests import (CLEAN_MANIFEST_NAME, Manifest, classify, is_fast, key, norm_rel,
                        read_sha256_file, to_path)

__all__ = ["Problem", "Report", "check_tree", "walk_files", "check_clean_manifest", "info"]

#: mtime tolerance of the fast check, seconds (FAT 2-second granularity, float rounding).
MTIME_SLACK = 2


@dataclass(slots=True)
class Problem:
    path: str
    problem: str  # missing | size | sha256 | type | unreadable | extra
    expected: str | int | None = None
    actual: str | int | None = None

    def row(self) -> list:
        return [self.path, self.problem, self.expected, self.actual]


@dataclass
class Report:
    root: Path
    checked: int = 0
    bytes: int = 0
    fast: int = 0
    hashed: int = 0
    hashed_bytes: int = 0
    problems: list[Problem] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)
    hashes: dict[str, str] = field(default_factory=dict)  # key -> sha256 of hashed files
    seconds: float = 0.0

    @property
    def missing(self) -> int:
        return sum(1 for p in self.problems if p.problem == "missing")

    @property
    def mismatch(self) -> int:
        return sum(1 for p in self.problems if p.problem != "missing")


def walk_files(root: Path) -> list[str]:
    """All regular files under ``root`` as canonical relative paths (``/``), sorted."""
    out: list[str] = []
    base = os.path.abspath(root)
    for dp, dns, fns in os.walk(base):
        dns.sort()
        rel_dir = os.path.relpath(dp, base)
        for fn in sorted(fns):
            rel = fn if rel_dir in (".", "") else os.path.join(rel_dir, fn)
            out.append(norm_rel(rel))
    return out


def check_tree(root: str | os.PathLike, ref: Manifest, *, deep: bool = False, jobs: int | None = None,
               scan_extra: bool = True, ignore: tuple[str, ...] = (CLEAN_MANIFEST_NAME,),
               progress: Callable[[int, int], None] | None = None) -> Report:
    """Compare ``root`` with ``ref`` (see module docstring). Never raises for content problems."""
    t0 = time.perf_counter()
    r = Path(os.path.abspath(os.fspath(root)))
    if not r.is_dir():
        raise SatkError("NOT_FOUND", f"game root not found: {jpath(r)}", hint="--root <game dir>")
    rep = Report(root=r)
    to_hash: list[tuple[str, Path, int]] = []
    for e in ref:
        rep.checked += 1
        rep.bytes += e.size
        p = to_path(r, e.path)
        try:
            st = os.stat(p)
        except (FileNotFoundError, NotADirectoryError):
            rep.problems.append(Problem(e.path, "missing", e.size, None))
            continue
        except OSError as ex:
            rep.problems.append(Problem(e.path, "unreadable", None, str(ex)))
            continue
        if not os.path.isfile(p):
            rep.problems.append(Problem(e.path, "type", "file", "directory"))
            continue
        if st.st_size != e.size:
            rep.problems.append(Problem(e.path, "size", e.size, st.st_size))
            continue
        if (not deep and e.mtime is not None and is_fast(e.path)
                and abs(int(st.st_mtime) - e.mtime) <= MTIME_SLACK):
            rep.fast += 1
            continue
        to_hash.append((key(e.path), p, e.size))
    results = hash_files(to_hash, jobs=jobs, progress=progress)
    for k, p, size in to_hash:
        e = ref.entries[k]
        res = results[k]
        if isinstance(res, OSError):
            rep.problems.append(Problem(e.path, "unreadable", None, str(res)))
            continue
        rep.hashed += 1
        rep.hashed_bytes += size
        rep.hashes[k] = res
        if not e.accepts(res):
            rep.problems.append(Problem(e.path, "sha256", e.sha256, res))
    if scan_extra:
        ign = {key(x) for x in ignore}
        rep.extra = [rel for rel in walk_files(r) if key(rel) not in ref.entries and key(rel) not in ign]
    rep.problems.sort(key=lambda p: key(p.path))
    rep.seconds = time.perf_counter() - t0
    return rep


def check_clean_manifest(root: Path, ref: Manifest) -> tuple[str, list[str]]:
    """Cross-check ``<root>/MANIFEST.sha256`` with ``ref``: (``ok|differs|missing``, warnings)."""
    p = root / CLEAN_MANIFEST_NAME
    if not p.is_file():
        return "missing", [f"NO_MANIFEST: {CLEAN_MANIFEST_NAME} not found in the copy (satk game clone writes it)"]
    try:
        listed = read_sha256_file(p)
    except (OSError, SatkError) as e:
        return "differs", [f"MANIFEST_UNREADABLE: {e}"]
    bad = sorted(rel for k, (rel, sha) in listed.items() if k in ref.entries and not ref.entries[k].accepts(sha))
    unknown = sorted(rel for k, (rel, _) in listed.items() if k not in ref.entries)
    absent = sorted(e.path for k, e in ref.entries.items() if k not in listed)
    if not (bad or unknown or absent):
        return "ok", []
    parts = []
    if bad:
        parts.append(f"{len(bad)} hash(es) differ ({', '.join(bad[:3])})")
    if unknown:
        parts.append(f"{len(unknown)} path(s) not in the stock manifest ({', '.join(unknown[:3])})")
    if absent:
        parts.append(f"{len(absent)} stock path(s) not listed ({', '.join(absent[:3])})")
    return "differs", [f"MANIFEST_DIFFERS: {CLEAN_MANIFEST_NAME}: " + "; ".join(parts)]


def _sha_or_none(p: Path) -> str | None:
    from .hashing import sha256_file

    try:
        return sha256_file(p)
    except FileNotFoundError:
        return None


def info(root: str | os.PathLike, stock: Manifest, *, list_limit: int = 20) -> dict:
    """What is this game directory: exe variant, vorbisFile, ASI count, non-stock files, protection."""
    from . import protect as _protect

    r = Path(os.path.abspath(os.fspath(root)))
    if not r.is_dir():
        raise SatkError("NOT_FOUND", f"game root not found: {jpath(r)}", hint="--root <game dir>")
    out: dict = {"root": jpath(r)}
    exe_path = next((r / n for n in ("gta_sa.exe", "gta-sa.exe") if (r / n).is_file()), r / "gta_sa.exe")
    if exe_path.is_file():
        from satk.core.detect import classify_exe

        data = _exe.read_exe(exe_path)
        ident = _exe.identify(data)
        out["exe"] = ident["variant"]
        if exe_path.name != "gta_sa.exe":
            out["exe_file"] = exe_path.name
        # data/exe_versions.json: verified hashes, else plugin-sdk signatures / sizes (heuristic)
        ver = classify_exe(data)
        out["variant"] = ver["variant"]
        out["layout"] = ver["layout"]
        out["re_supported"] = ver["supported"]
        out["variant_match"] = ver["match"]
        out["exe_sha256"] = ident["sha256"]
        if ident.get("desc"):
            out["exe_desc"] = ident["desc"]
        if ident.get("pe"):
            out["exe_pe"] = ident["pe"]
        if not ver["supported"]:
            out["warn"] = [f"UNSUPPORTED_EXE: {exe_path.name} is {ver['variant']}: assets work with any version, "
                           "satk re needs the 1.0 US address layout"]
    else:
        out["exe"] = "missing"
    vf = _sha_or_none(r / "vorbisFile.dll")
    out["vorbisfile"] = _exe.identify_vorbisfile(vf)
    if vf:
        out["vorbisfile_sha256"] = vf
    files = walk_files(r)
    ign = key(CLEAN_MANIFEST_NAME)
    present = {key(f) for f in files}
    nonstock = [f for f in files if key(f) not in stock.entries and key(f) != ign]
    out["asi"] = sum(1 for f in files if f.lower().endswith(".asi"))
    out["nonstock"] = len(nonstock)
    if nonstock:
        out["nonstock_files"] = nonstock[:list_limit]
        roles: dict[str, int] = {}
        for f in nonstock:
            role = classify(f)
            roles[role] = roles.get(role, 0) + 1
        out["nonstock_roles"] = roles
    out["stock_present"] = sum(1 for k in stock.entries if k in present)
    out["stock_missing"] = len(stock) - out["stock_present"]
    out["files"] = len(files)
    out["manifest"] = (r / CLEAN_MANIFEST_NAME).is_file()
    try:
        out["protected"] = _protect.summary(r)
    except SatkError:  # pragma: no cover - root vanished meanwhile
        pass
    return out
