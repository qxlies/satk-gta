"""satk dev gate: step runner and summaries (no real suites are run here)."""

from __future__ import annotations

import sys

from satk.docs import gate as G


def _step(name, code, out=""):
    return G.Step(name, [sys.executable, "-c", f"import sys; print({out!r}); sys.exit({code})"])


def test_runs_all_steps_and_reports_failures():
    res = G.run([_step("a", 0, "fine"), _step("b", 1, "broken"), _step("c", 0, "after")])
    assert [(r.name, r.ok) for r in res] == [("a", True), ("b", False), ("c", True)]
    assert res[1].summary == "broken"


def test_stop_on_fail():
    res = G.run([_step("a", 1, "x"), _step("b", 0, "y")], keep_going=False)
    assert [r.name for r in res] == ["a"]


def test_pytest_summary_picks_counts_and_failures():
    out = ("FAILED tests/x.py::test_a - boom\n"
           "===== 1 failed, 20 passed, 2 skipped in 3.10s =====\n")
    s = G._pytest_summary(1, out)
    assert s.startswith("1 failed, 20 passed, 2 skipped") and "tests/x.py::test_a" in s


def test_json_summary_ok_and_error():
    ok = G._json_summary("tools")(0, '{"ok": true, "tools": 25}\n')
    err = G._json_summary()(1, '{"ok": false, "error": {"code": "REVISION", "msg": "stale"}}\n')
    assert ok == "tools=25" and err == "REVISION: stale"


def test_plan_quick_skips_game_steps(tmp_path):
    names_quick = [s.name for s in G.plan(quick=True, private_work=tmp_path)]
    names_full = [s.name for s in G.plan(quick=False, private_work=tmp_path)]
    assert "tests-game" not in names_quick and "index-verify" in names_full
    assert all(s.env.get("SATK_PATHS_WORK") == str(tmp_path) for s in G.plan(quick=False, private_work=tmp_path)
               if s.name.startswith(("tests-game", "index")))


def test_skipped_step_passes_without_running():
    st = G.Step("s", [sys.executable, "-c", "import sys; sys.exit(1)"], skip=lambda: "skipped: not here")
    assert [(r.name, r.ok, r.summary) for r in G.run([st])] == [("s", True, "skipped: not here")]


def test_agent_docs_sync_is_skipped_where_nothing_was_published(satk_home, run_cli):
    """Outside a development workspace (a fresh clone) no copy was ever published: nothing to compare."""
    step = next(s for s in G.plan(quick=True, private_work=satk_home / "w") if s.name == "agent-docs-sync")
    assert step.skip() and step.skip().startswith("skipped:")
    assert run_cli(["dev", "sync-agent-docs"]).code == 0
    assert step.skip() is None
