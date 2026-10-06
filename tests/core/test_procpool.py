"""``satk.core.procpool``: a worker pool that runs in-process when worker processes cannot start."""

from __future__ import annotations

import concurrent.futures as cf
import logging

import pytest

from satk.core import procpool as P


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch):
    monkeypatch.setattr(P, "_reason", None)
    monkeypatch.delenv(P.ENV_JOBS, raising=False)


def _boom(*_a, **_k):
    raise PermissionError(5, "Access is denied")


def test_real_pool_gives_the_same_results_as_inline():
    with P.pool(2) as ex:
        assert list(ex.map(abs, [-3, -1, 2])) == [3, 1, 2]
        assert [f.result() for f in [ex.submit(abs, -7), ex.submit(abs, 8)]] == [7, 8]
    if P.unavailable_reason() is None:  # a restricted sandbox legitimately falls back
        assert P.fallback_reason() is None


def test_env_single_process_runs_inline_without_a_pool(monkeypatch):
    monkeypatch.setenv(P.ENV_JOBS, "1")
    monkeypatch.setattr(cf, "ProcessPoolExecutor", _boom)  # would fail if a pool were created
    assert P.single_process_requested()
    with P.pool(8) as ex:
        assert list(ex.map(abs, [-2, -4])) == [2, 4]
    assert P.fallback_reason() is None  # explicit choice, not a fallback


def test_jobs_from_env(monkeypatch):
    assert P.jobs_from_env() is None
    for raw, want in (("3", 3), (" 1 ", 1), ("0", None), ("x", None), ("", None)):
        monkeypatch.setenv(P.ENV_JOBS, raw)
        assert P.jobs_from_env() == want


def test_pool_creation_failure_falls_back_and_warns_once(monkeypatch, caplog):
    monkeypatch.setattr(cf, "ProcessPoolExecutor", _boom)
    logger = logging.getLogger("satk.procpool")  # the "satk" parent does not propagate to caplog's root handler
    logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.WARNING, logger="satk.procpool"):
            for _ in range(2):
                with P.pool(4, spawn=True) as ex:
                    assert [f.result() for f in [ex.submit(abs, -5)]] == [5]
                    assert list(ex.map(pow, [2, 3], [3, 2])) == [8, 9]
    finally:
        logger.removeHandler(caplog.handler)
    assert "pool creation" in (P.fallback_reason() or "") and "Access is denied" in P.fallback_reason()
    # one record (the handler may see it twice when the "satk" logger also propagates to the root)
    assert len({id(r) for r in caplog.records if "in-process" in r.getMessage()}) == 1


def test_first_submit_failure_falls_back(monkeypatch):
    class Broken(cf.Executor):
        def submit(self, fn, /, *a, **k):
            raise PermissionError(5, "Access is denied")

        def shutdown(self, wait=True, *, cancel_futures=False):
            pass

    monkeypatch.setattr(cf, "ProcessPoolExecutor", lambda **_kw: Broken())
    with P.pool(2) as ex:
        assert list(ex.map(abs, [-1, -2, -3])) == [1, 2, 3]
        assert ex.submit(abs, -9).result() == 9
    assert "first submit" in (P.fallback_reason() or "")


def test_inline_executor_delivers_worker_exceptions_through_the_future():
    ex = P._InlineExecutor()
    f = ex.submit(int, "x")
    assert isinstance(f.exception(), ValueError)
    with pytest.raises(ValueError):
        f.result()


def test_inline_map_is_lazy():
    seen: list[int] = []

    def work(n):
        seen.append(n)
        return n * n

    it = P._InlineExecutor().map(work, [1, 2, 3])
    assert seen == []
    assert next(it) == 1 and seen == [1]


def test_unavailable_reason_on_a_working_machine_or_a_sandbox():
    why = P.unavailable_reason()
    assert why is None or "Error" in why


def test_index_default_jobs_honours_the_env(monkeypatch):
    from satk.index.scan import default_jobs

    assert default_jobs() >= 1
    monkeypatch.setenv(P.ENV_JOBS, "1")
    assert default_jobs() == 1
    monkeypatch.setenv(P.ENV_JOBS, "5")
    assert default_jobs() == 5
