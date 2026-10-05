"""Streaming SHA-256 of game files (``open_ro`` only) with a small thread pool.

``hashlib`` releases the GIL while hashing large buffers, so a few threads keep an NVMe
drive busy: the full 5 GB clean copy hashes in well under the 90 s budget of
``satk game verify --deep``. Standard library only.
"""

from __future__ import annotations

import contextvars
import hashlib
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Hashable, Iterable, TypeVar

from satk.core.paths import open_ro

__all__ = ["BUFSIZE", "sha256_file", "hash_files", "default_jobs"]

BUFSIZE = 1 << 20
K = TypeVar("K", bound=Hashable)


def default_jobs() -> int:
    """Hashing threads by default: 4, fewer on small machines."""
    return max(1, min(4, os.cpu_count() or 1))


def sha256_file(path: str | os.PathLike, *, on_bytes: Callable[[int], None] | None = None,
                bufsize: int = BUFSIZE) -> str:
    """SHA-256 hex digest of a file read with ``open_ro`` in ``bufsize`` chunks."""
    h = hashlib.sha256()
    buf = bytearray(bufsize)
    mv = memoryview(buf)
    with open_ro(path) as f:
        while True:
            n = f.readinto(buf)
            if not n:
                break
            h.update(mv[:n])
            if on_bytes is not None:
                on_bytes(n)
    return h.hexdigest()


def hash_files(items: Iterable[tuple[K, Path, int]], *, jobs: int | None = None,
               progress: Callable[[int, int], None] | None = None) -> dict[K, str | OSError]:
    """Hash many files: ``[(key, path, size)] -> {key: sha256 | OSError}``.

    Largest files start first (better balance). ``progress(done_bytes, total_bytes)`` is
    called from worker threads, at most about every 64 MiB.
    """
    work = sorted(items, key=lambda t: -t[2])
    total = sum(t[2] for t in work)
    done = 0
    last = 0
    lock = threading.Lock()

    def tick(n: int) -> None:
        nonlocal done, last
        if progress is None:
            return
        with lock:
            done += n
            if done - last >= (64 << 20) or done >= total:
                last = done
                progress(done, total)

    def one(path: Path) -> str | OSError:
        try:
            return sha256_file(path, on_bytes=tick)
        except OSError as e:
            return e

    out: dict[K, str | OSError] = {}
    n = max(1, int(jobs or default_jobs()))
    if n == 1 or len(work) <= 1:
        for k, p, _ in work:
            out[k] = one(p)
        return out
    with ThreadPoolExecutor(max_workers=n, thread_name_prefix="satk-hash") as ex:
        # copy_context: the progress handler (a contextvar) reaches the worker threads
        futs = {ex.submit(contextvars.copy_context().run, one, p): k for k, p, _ in work}
        for fut in as_completed(futs):
            out[futs[fut]] = fut.result()
    return out
