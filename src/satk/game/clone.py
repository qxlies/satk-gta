"""Reproduce the clean dev copy from the original install (SPEC §4.1 ``game.clone``).

The copy already exists and is verified (SPEC §0.2 V1); this module makes it reproducible:

1. the source (default: the original install; a verified clean copy works too) is only read,
   with ``open_ro`` (``"rb"``);
2. the destination must be new: ``EXISTS`` if it is a non-empty directory (or a file). It is
   checked by :func:`satk.game.guard.write_target` (``PROTECTED_PATH``): under ``work/`` or the
   clean copy root itself, never inside the clean copy, the install, ``src``, a git working
   tree or behind an alias of them (extended-length, UNC, junction); it must not overlap the source;
3. every file of the stock manifest is streamed into ``<dst>.partial`` while hashing and
   must match the manifest; ``gta_sa.exe`` is restored in memory (:mod:`satk.game.exe`),
   ``vorbisFile.dll`` comes from ``vorbisHooked.dll`` (or from ``vorbisFile.dll`` itself when
   the source is a clean copy); mtimes are preserved so the fast ``verify`` works on the result;
4. ``MANIFEST.sha256`` is written, then ``<dst>.partial`` is renamed to ``<dst>`` (atomic).

The first failed file (or Ctrl+C) stops all copies; the partial directory created by this run
is then removed. Standard library only.
"""

from __future__ import annotations

import contextvars
import hashlib
import os
import shutil
import threading
import time
from concurrent.futures import FIRST_EXCEPTION, Future, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Callable

from satk.core.errors import SatkError
from satk.core.paths import atomic_write, cfg, ensure_removable, ensure_writable, jpath, open_ro

from . import exe as _exe
from .guard import Target, canonical, inside, write_target
from .hashing import BUFSIZE
from .manifests import CLEAN_MANIFEST_NAME, Entry, Manifest, format_sha256_file, key, to_path
from .verify import walk_files

__all__ = ["plan", "clone", "FREE_MARGIN"]

#: Extra free space required on the destination drive besides the copy itself.
FREE_MARGIN = 256 << 20
EXE_VARIANT_NAMES = {"stock": "hoodlum-stock", "mta": "mta-canonical"}


def _existing_ancestor(p: Path) -> Path:
    cur = Path(os.path.abspath(p))
    while not cur.exists() and cur.parent != cur:
        cur = cur.parent
    return cur


def _partial(dst: Path) -> Path:
    return dst.with_name(dst.name + ".partial")


def _exe_target(e: Entry, variant: str, stock: Manifest) -> str:
    """Expected SHA-256 of ``gta_sa.exe`` for the requested variant."""
    if variant == "stock":
        return e.sha256
    targets = (stock.meta.get("targets") or {}).get("gta_sa.exe") or {}
    sha = targets.get(EXE_VARIANT_NAMES[variant])
    if not sha and e.alt:
        sha = e.alt[0]
    if not sha:
        raise SatkError("UNSUPPORTED", f"the manifest has no target hash for the {variant} executable")
    return str(sha)


def _source_file(s: Path, e: Entry) -> Path | None:
    """The file of ``s`` that ``e`` is produced from, or ``None`` (missing / another size).

    ``e.source`` first (the install keeps the original ``vorbisFile.dll`` as
    ``vorbisHooked.dll``). A clean copy has no such file but already holds the target under
    its own name, so for restored entries ``e.path`` with the right size is accepted too;
    the hash check while copying decides either way.
    """
    for rel in dict.fromkeys((e.source, e.path)):
        p = to_path(s, rel)
        try:
            if p.is_file() and p.stat().st_size == e.size:
                return p
        except OSError:
            continue
    return None


def _prepare(src: str | os.PathLike, dst: str | os.PathLike, stock: Manifest,
             exe_variant: str) -> tuple[dict, Path, Target, Target, dict[str, Path]]:
    """Checks of :func:`plan`; also returns the canonical paths and the source file of each entry."""
    if exe_variant not in EXE_VARIANT_NAMES:
        raise SatkError("BAD_PARAMS", f"unknown exe variant {exe_variant!r}", did_you_mean=list(EXE_VARIANT_NAMES))
    s = canonical(src, what="clone source")
    if not s.is_dir():
        raise SatkError("NOT_FOUND", f"source game root not found: {jpath(s)}", hint="--src <original install>")
    # the clean copy root may be recreated; anything inside it, the install, src, network paths,
    # places outside work/ and git working trees never (satk.game.guard)
    d = write_target(dst, what="clone destination", allow_game_root=True)
    part = write_target(_partial(d.path), what="clone staging directory", location=False)
    for a, b in ((d.path, s), (part.path, s)):
        if inside(a, b) or inside(b, a):
            raise SatkError("PROTECTED_PATH", f"destination {jpath(a)} overlaps the source {jpath(s)}",
                            hint=f"clone into a separate directory, e.g. {jpath(cfg().paths.work)}/tmp/wp-01/clone",
                            data={"path": jpath(a), "root": jpath(s)})
    if d.path.exists() and (not d.path.is_dir() or any(d.path.iterdir())):
        raise SatkError("EXISTS", f"destination exists and is not empty: {jpath(d.path)}",
                        hint="choose a new --dst (satk game verify checks an existing copy)")
    if part.path.exists():
        raise SatkError("EXISTS", f"{jpath(part.path)} exists (left by an interrupted clone)",
                        hint="delete it manually or choose another --dst")
    sources: dict[str, Path] = {}
    problems: list[list] = []
    for e in stock:
        f = _source_file(s, e)
        if f is not None:
            sources[e.path] = f
            continue
        p = to_path(s, e.source)
        if not p.is_file():
            problems.append([e.source, "missing", e.size, None])
        else:
            problems.append([e.source, "size", e.size, p.stat().st_size])
    if problems:
        missing = sum(1 for r in problems if r[1] == "missing")
        raise SatkError(
            "NOT_FOUND" if missing else "REVISION",
            f"source is neither the audited install nor a clean copy: {missing} missing, "
            f"{len(problems) - missing} with another size",
            hint="satk game verify --root <src> --against install (or --against stock for a copy)",
            data={"cols": ["path", "problem", "expected", "actual"], "rows": problems[:50]},
        )
    skip = [f for f in walk_files(s) if key(f) not in stock.entries]
    free = shutil.disk_usage(_existing_ancestor(d.path)).free
    need = stock.total_bytes + FREE_MARGIN
    if free < need:
        raise SatkError("BAD_PARAMS", f"not enough free space for {jpath(d.path)}: {free} bytes free, {need} needed",
                        hint="free some space or clone to another drive")
    report = {
        "src": jpath(s),
        "dst": jpath(d.path),
        "files": len(stock),
        "bytes": stock.total_bytes,
        "skip_nonstock": len(skip),
        "restore": [e.path for e in stock if e.src or e.via],
        "exe": EXE_VARIANT_NAMES[exe_variant],
        "free_bytes": free,
    }
    return report, s, d, part, sources


def plan(src: str | os.PathLike, dst: str | os.PathLike, stock: Manifest, *, exe_variant: str = "stock") -> dict:
    """Validate a clone without writing anything; returns the dry-run report.

    Raises ``NOT_FOUND`` (no source / source files missing), ``PROTECTED_PATH`` (see
    :func:`satk.game.guard.write_target`; also when source and destination overlap), ``EXISTS``,
    ``REVISION`` (a source file has the wrong size), ``BAD_PARAMS`` (not enough free space,
    ambiguous path spelling).
    """
    return _prepare(src, dst, stock, exe_variant)[0]


class _Aborted(Exception):
    """A copy stopped because another one failed or the clone was interrupted (never escapes)."""


def _copy_verified(src: Path, dst: Path, expected: str, on_bytes: Callable[[int], None]) -> str:
    """Stream ``src`` (``open_ro``) into a new file ``dst`` while hashing; keep src mtime.

    ``on_bytes`` is called after every buffer and may raise to stop the copy early.
    """
    h = hashlib.sha256()
    buf = bytearray(BUFSIZE)
    mv = memoryview(buf)
    ensure_writable(dst)
    with open_ro(src) as fi, open(dst, "xb") as fo:
        while True:
            n = fi.readinto(buf)
            if not n:
                break
            h.update(mv[:n])
            fo.write(mv[:n])
            on_bytes(n)
    st = os.stat(src)
    os.utime(dst, ns=(st.st_atime_ns, st.st_mtime_ns))
    got = h.hexdigest()
    if got != expected:
        raise SatkError("REVISION", f"{src} hashes to {got}, expected {expected}",
                        hint="the source differs from the audited install (satk game verify --against install)",
                        data={"path": jpath(src), "expected": expected, "actual": got})
    return got


def _copy_all(jobs: list[tuple[str, Path, Path, str]], workers: int, tick: Callable[[int], None],
              stop: threading.Event) -> dict[str, str]:
    """Copy ``(rel, src, dst, sha256)`` items in parallel; the first error stops everything.

    On an error (or ``KeyboardInterrupt`` in the waiting thread) ``stop`` is set, queued copies
    are cancelled and running ones end after their current buffer, so no further gigabytes are
    written before the caller cleans up.
    """
    def job(sp: Path, dp: Path, sha: str) -> str:
        if stop.is_set():  # a worker may pick the next item before the waiting thread reacts
            raise _Aborted()
        try:
            return _copy_verified(sp, dp, sha, tick)
        except BaseException:
            stop.set()
            raise

    ex = ThreadPoolExecutor(max_workers=max(1, int(workers)), thread_name_prefix="satk-clone")
    try:
        # copy_context: worker threads keep the progress handler of the operation
        futs: dict[Future, str] = {ex.submit(contextvars.copy_context().run, job, sp, dp, sha): rel
                                   for rel, sp, dp, sha in jobs}
        pending = set(futs)
        sums: dict[str, str] = {}
        while pending and not stop.is_set():
            # a timeout keeps the wait interruptible by Ctrl+C on Windows
            done, pending = wait(pending, timeout=0.25, return_when=FIRST_EXCEPTION)
            for f in done:
                if f.exception() is None:
                    sums[futs[f]] = f.result()
        if stop.is_set():  # a copy failed: cancel the queue, let running copies end, report the cause
            ex.shutdown(wait=True, cancel_futures=True)
            errors = [e for f in futs if f.done() and not f.cancelled() and (e := f.exception()) is not None]
            causes = [e for e in errors if not isinstance(e, _Aborted)]
            raise (causes or errors)[0]
        return sums
    except BaseException:
        stop.set()
        ex.shutdown(wait=True, cancel_futures=True)
        raise
    finally:
        ex.shutdown(wait=True)


def clone(src: str | os.PathLike, dst: str | os.PathLike, stock: Manifest, *, exe_variant: str = "stock",
          dry_run: bool = False, jobs: int = 2, keep_partial: bool = False,
          progress: Callable[[int, int, str | None], None] | None = None) -> dict:
    """Create a new clean copy at ``dst`` (see the module docstring)."""
    t0 = time.perf_counter()
    report, _src, d, part, sources = _prepare(src, dst, stock, exe_variant)
    if dry_run:
        report["dry_run"] = True
        return report
    p = part.path
    total = stock.total_bytes
    done = 0
    lock = threading.Lock()
    stop = threading.Event()

    def tick(n: int) -> None:
        nonlocal done
        if stop.is_set():
            raise _Aborted()
        with lock:
            done += n
            if progress is not None:
                progress(done, total, None)

    sums: dict[str, str] = {}
    created = False
    try:
        ensure_writable(p)
        p.mkdir(parents=True)
        created = True
        entries = list(stock)
        for e in entries:
            ensure_writable(to_path(p, e.path)).parent.mkdir(parents=True, exist_ok=True)
        for e in (x for x in entries if x.via):
            if e.via != "exe:stock":
                raise SatkError("UNSUPPORTED", f"unknown transform {e.via!r} for {e.path}")
            expected = _exe_target(e, exe_variant, stock)
            data = _exe.build(_exe.read_exe(sources[e.path]), exe_variant, strict=False)
            got = _exe.sha256(data)
            if got != expected:
                raise SatkError("REVISION", f"restored {e.path} hashes to {got}, expected {expected}",
                                hint="the source executable is not a derivable variant (satk game info --root <src>)")
            atomic_write(to_path(p, e.path), data)
            sums[e.path] = got
            tick(len(data))
        plain = sorted((x for x in entries if not x.via), key=lambda x: -x.size)  # largest first
        sums.update(_copy_all([(e.path, sources[e.path], to_path(p, e.path), e.sha256) for e in plain],
                              jobs, tick, stop))
        atomic_write(p / CLEAN_MANIFEST_NAME, format_sha256_file(sums.items()))
        ensure_removable(p)
        with d.unlock():  # game_writer() only when dst is the clean copy root itself
            ensure_removable(d.path)
            if d.path.exists():
                d.path.rmdir()  # empty (checked by plan)
            os.replace(p, d.path)
        created = False
    except BaseException:
        if created and not keep_partial and p.exists():
            ensure_removable(p)
            shutil.rmtree(p, ignore_errors=True)
        raise
    report["manifest"] = jpath(d.path / CLEAN_MANIFEST_NAME)
    report["seconds"] = round(time.perf_counter() - t0, 1)
    report.pop("free_bytes", None)
    return report
