"""Worker pool that degrades to in-process work when worker processes cannot be set up.

``ProcessPoolExecutor`` needs a named pipe between the parent and its workers. A restricted Windows
token (the Codex sandbox, some CI sandboxes) denies that with ``PermissionError: [WinError 5]``, either
when the pool is created or when the first job is submitted. :func:`pool` then returns an executor that
runs every job in the calling thread, in submission order, and logs one warning. The jobs of satk are pure
functions of their arguments, so the result is identical to a multi-process run; only the speed differs.

``SATK_JOBS=1`` asks for single-process work explicitly (same as ``--jobs 1``): no process is started.
Worker functions must not rely on running in a separate process (they are the same ones that run when
``jobs == 1``).
"""

from __future__ import annotations

import os
import threading
from concurrent.futures import Executor, Future
from typing import Any, Callable, Iterator

__all__ = ["ENV_JOBS", "pool", "single_process_requested", "jobs_from_env", "fallback_reason", "unavailable_reason"]

#: ``SATK_JOBS=<n>``: default worker count of the parallel steps; ``1`` = single-process, no pool.
ENV_JOBS = "SATK_JOBS"

#: Errors that mean "this environment cannot start worker processes".
_NO_PROCESSES = (OSError, ImportError, NotImplementedError)

_lock = threading.Lock()
_reason: str | None = None


def single_process_requested() -> bool:
    """True when ``SATK_JOBS=1`` (an explicit single-process run)."""
    return os.environ.get(ENV_JOBS, "").strip() == "1"


def jobs_from_env() -> int | None:
    """``SATK_JOBS`` as a worker count (``None``: unset or invalid)."""
    try:
        n = int(os.environ.get(ENV_JOBS, "").strip())
    except ValueError:
        return None
    return n if n >= 1 else None


def fallback_reason() -> str | None:
    """Why this process fell back to in-process work (``None``: it did not)."""
    return _reason


def unavailable_reason() -> str | None:
    """Why worker processes cannot be set up here, found by creating the pipe a pool needs (``None``: they can)."""
    try:
        import multiprocessing as mp

        a, b = mp.Pipe()
        a.close()
        b.close()
    except _NO_PROCESSES as e:
        return f"{type(e).__name__}: {e}"[:200]
    return None


def _note_fallback(exc: BaseException, where: str) -> None:
    global _reason
    with _lock:
        first = _reason is None
        _reason = f"{where}: {type(exc).__name__}: {exc}"[:300]
    if first:
        from .log import get_logger

        get_logger("procpool").warning("worker processes are unavailable (%s); running the jobs in-process", _reason)


class _InlineExecutor(Executor):
    """Runs each job in the calling thread when it is submitted (the pool of a ``jobs == 1`` run)."""

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future:
        f: Future = Future()
        try:
            f.set_result(fn(*args, **kwargs))
        except Exception as e:  # noqa: BLE001 - delivered by f.result(), like a worker's exception
            f.set_exception(e)
        return f

    def map(self, fn: Callable[..., Any], *iterables: Any, timeout: float | None = None,
            chunksize: int = 1) -> Iterator[Any]:
        """Lazy: one job per ``next()``, so progress reporting between results keeps working."""
        for args in zip(*iterables):
            yield fn(*args)


class _GuardedPool(Executor):
    """A ``ProcessPoolExecutor`` that switches to :class:`_InlineExecutor` if the first submit cannot start workers."""

    def __init__(self, real: Executor) -> None:
        self._real: Executor | None = real
        self._inline = _InlineExecutor()
        self._started = False

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future:
        if self._real is None:
            return self._inline.submit(fn, *args, **kwargs)
        try:
            fut = self._real.submit(fn, *args, **kwargs)
        except _NO_PROCESSES as e:
            if self._started:
                raise
            _note_fallback(e, "first submit")
            self._drop_real()
            return self._inline.submit(fn, *args, **kwargs)
        except RuntimeError as e:  # BrokenProcessPool / "cannot schedule new futures after shutdown"
            from concurrent.futures.process import BrokenProcessPool

            if self._started or not isinstance(e, BrokenProcessPool):
                raise
            _note_fallback(e, "first submit")
            self._drop_real()
            return self._inline.submit(fn, *args, **kwargs)
        self._started = True
        return fut

    def map(self, fn: Callable[..., Any], *iterables: Any, timeout: float | None = None,
            chunksize: int = 1) -> Iterator[Any]:
        if self._real is None:
            return self._inline.map(fn, *iterables)
        return super().map(fn, *iterables, timeout=timeout)

    def _drop_real(self) -> None:
        real, self._real = self._real, None
        if real is not None:
            real.shutdown(wait=False, cancel_futures=True)

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        if self._real is not None:
            self._real.shutdown(wait=wait, cancel_futures=cancel_futures)
        self._inline.shutdown(wait=wait)


def pool(max_workers: int, *, spawn: bool = False) -> Executor:
    """A process pool of ``max_workers`` workers, or an in-process executor when processes are not possible.

    Use it like ``ProcessPoolExecutor`` (``with pool(n) as ex: ex.submit(...)`` / ``ex.map(...)``). Falls back to
    in-process work for ``SATK_JOBS=1`` and when the pool cannot be created or started (one warning is logged).

    Args:
        max_workers: worker processes (Windows allows at most 61).
        spawn: use the ``spawn`` start method explicitly (render and measure workers import their own modules).
    """
    if single_process_requested():
        return _InlineExecutor()
    try:
        from concurrent.futures import ProcessPoolExecutor

        if spawn:
            import multiprocessing as mp

            real: Executor = ProcessPoolExecutor(max_workers=max_workers, mp_context=mp.get_context("spawn"))
        else:
            real = ProcessPoolExecutor(max_workers=max_workers)
    except _NO_PROCESSES as e:
        _note_fallback(e, "pool creation")
        return _InlineExecutor()
    return _GuardedPool(real)
