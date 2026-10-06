"""satk dev gate: step runner and summaries (no real suites are run here)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

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


# --------------------------------------------------------------------------- restricted environments (Codex sandbox)


def test_pytest_summary_counts_sandbox_skips():
    out = ("SKIPPED [3] tests/sandbox_compat.py:57: sandbox: sh.EXE cannot start in this environment (x)\n"
           "SKIPPED [1] tests/x.py:3: no game copy\n"
           "===== 5 passed, 4 skipped in 1.00s =====\n")
    assert G._pytest_summary(0, out) == "5 passed, 4 skipped; 3 skipped for the sandbox"


def test_json_summary_skips_log_lines_after_the_json():
    out = '{"ok": true, "build_seconds": 12.5}\nWARNING satk.procpool: worker processes are unavailable\n'
    assert G._json_summary("build_seconds")(0, out) == "build_seconds=12.5"
    assert G._json_summary()(1, "Traceback (most recent call last):\nboom") == "boom"


def test_plan_without_restrictions_is_unchanged(tmp_path):
    for s in G.plan(quick=False, private_work=tmp_path):
        assert "sandbox_compat" not in s.argv and "PYTHONPATH" not in s.env


def test_plan_with_restrictions_touches_only_the_test_steps(tmp_path):
    r = G.Restrictions(notes=["x"], pytest_args=["-p", "sandbox_compat"], pytest_env={"PYTHONPATH": "a;b"})
    steps = {s.name: s for s in G.plan(quick=False, private_work=tmp_path, restrictions=r)}
    for name in ("tests", "tests-game"):
        assert steps[name].argv[1:3] == ["-m", "pytest"] and steps[name].argv[-4:-2] == ["-p", "sandbox_compat"]
        assert steps[name].env["PYTHONPATH"] == "a;b"
    assert steps["tests-game"].env["SATK_PATHS_WORK"] == str(tmp_path)
    assert all("PYTHONPATH" not in s.env for n, s in steps.items() if n not in ("tests", "tests-game"))


def test_detect_restrictions_describes_what_it_found():
    r = G.detect_restrictions()
    assert isinstance(r, G.Restrictions) and all(isinstance(n, str) for n in r.notes)
    if r.pytest_args:  # owner-only temp directories are unusable: the plugin must be loadable from tests/
        assert r.pytest_args == ["-p", "sandbox_compat"] and r.pytest_env["PYTHONPATH"].endswith("tests")


# --------------------------------------------------------------------------- progress, temp folder, private folders


def test_progress_lines_and_the_temp_folder_of_every_step(tmp_path):
    probe = ("import os, tempfile; print(os.environ['TEMP'], os.environ['TMP'], os.environ['TMPDIR'], "
             "tempfile.gettempdir(), sep='|')")
    st = G.Step("probe", [sys.executable, "-c", probe], summarize=lambda code, out: out.strip())
    said: list[str] = []
    temp = tmp_path / "gate" / "tmp"
    res = G.run([_step("a", 0, "fine"), st], log=said.append, temp_dir=temp)
    assert temp.is_dir() and res[1].summary == "|".join([str(temp)] * 4)
    assert said[0] == "gate: [1/2] a ..." and said[1].startswith("gate: [1/2] a ok in")
    assert said[2] == "gate: [2/2] probe ..." and "ok in" in said[3]


def test_failed_step_is_reported_as_failed_in_the_progress(tmp_path):
    said: list[str] = []
    G.run([_step("bad", 1, "boom")], log=said.append)
    assert said[-1].startswith("gate: [1/1] bad FAILED in") and said[-1].endswith(": boom")


def test_heartbeat_shows_the_pytest_percentage_of_a_long_step():
    # long enough after the progress line that a slow interpreter start (a loaded machine) still sees a beat
    code = "import time; print('.....  [ 40%]', flush=True); time.sleep(4); print('1 passed in 4.00s')"
    said: list[str] = []
    res = G.run([G.Step("tests", [sys.executable, "-c", code], summarize=G._pytest_summary)], log=said.append,
                heartbeat=0.5)
    beats = [m for m in said if "still running" in m]
    assert res[0].ok and any("40%" in m for m in beats) and res[0].summary == "1 passed"
    assert all("%" not in m or "40%" in m for m in beats)


def test_heartbeat_counts_failing_tests_so_far():
    code = "import time; print('..F.  [ 50%]', flush=True); time.sleep(4)"
    said: list[str] = []
    G.run([G.Step("tests", [sys.executable, "-c", code])], log=said.append, heartbeat=0.5)
    assert any("50%" in m and "1 failing so far" in m for m in said)


def test_a_step_that_overruns_is_killed_and_fails():
    st = G.Step("slow", [sys.executable, "-c", "import time; print('start', flush=True); time.sleep(60)"], timeout=1)
    t0 = time.time()
    res = G.run([st], heartbeat=0.2)
    assert not res[0].ok and res[0].summary == "timeout after 1 s" and time.time() - t0 < 30


def test_exit_code_five_passes_only_where_allowed():
    five = "import sys; sys.exit(5)"
    strict = G.Step("a", [sys.executable, "-c", five])
    lax = G.Step("b", [sys.executable, "-c", five], ok_codes=(0, 5))
    assert [(r.name, r.ok) for r in G.run([strict, lax])] == [("a", False), ("b", True)]


def test_workdirs_are_fresh_per_run_and_never_shared_with_a_live_gate(satk_home):
    d1 = G.claim_workdirs("abc12345")
    assert d1.temp == d1.root / "tmp" and d1.work == d1.root / "work" and d1.temp.is_dir()
    (d1.temp / "left-over.txt").write_text("x", encoding="utf-8")
    d2 = G.claim_workdirs("abc12345")  # same process: the old folder is wiped, not shared
    assert d2.root == d1.root and not (d2.temp / "left-over.txt").exists()
    d2.work.mkdir(parents=True, exist_ok=True)
    (d2.work / "vanilla.sqlite").write_text("x", encoding="utf-8")
    d2.release(clean=True)  # a green run frees the temp folder and the private work (the index: gigabytes for real)
    assert not d2.temp.exists() and not d2.work.exists() and not (d2.root / "owner.json").exists()
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:  # another live gate of the same checkout owns the folder
        (d2.root).mkdir(parents=True, exist_ok=True)
        (d2.root / "owner.json").write_text(json.dumps({"pid": sleeper.pid}), encoding="utf-8")
        (d2.root / "tmp").mkdir()
        (d2.root / "tmp" / "theirs.txt").write_text("x", encoding="utf-8")
        d3 = G.claim_workdirs("abc12345")
        assert d3.root != d2.root and d3.root.name == f"gate-abc12345-{os.getpid()}"
        assert (d2.root / "tmp" / "theirs.txt").exists()  # nothing of the live gate was touched
        assert any("another gate of this checkout runs" in n for n in d3.notes)
        d3.release(clean=True)
        assert not d3.root.exists()  # a sibling folder goes away whole
    finally:
        sleeper.kill()
        sleeper.wait()
    (d2.root / "owner.json").write_text(json.dumps({"pid": sleeper.pid}), encoding="utf-8")  # that gate is gone now
    d4 = G.claim_workdirs("abc12345")
    assert d4.root == d2.root and not (d4.temp / "theirs.txt").exists()
    d4.release(clean=False)
    assert d4.temp.exists() and not (d4.root / "owner.json").exists()  # a failed run keeps its folders for a look


def test_claiming_sweeps_the_folders_of_dead_gates_only(satk_home):
    from satk.core.paths import tmp

    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    try:
        gone, kept = tmp(f"gate-feed0001-{dead.pid}"), tmp(f"gate-feed0001-{sleeper.pid}")
        (gone / "work").mkdir()
        (gone / "work" / "x").write_text("x", encoding="utf-8")
        unrelated = tmp("gate-feed0002-4242")  # another checkout's folder is not ours to sweep
        G.claim_workdirs("feed0001").release(clean=True)
        assert not gone.exists() and kept.exists() and unrelated.exists()
    finally:
        sleeper.kill()
        sleeper.wait()


def test_every_step_output_is_saved_and_a_failed_step_names_its_file(tmp_path):
    said: list[str] = []
    res = G.run([_step("good", 0, "fine"), _step("bad", 1, "boom: details here")], log=said.append,
                log_dir=tmp_path / "logs")
    good, bad = res
    assert good.log.endswith("/logs/01-good.log") and bad.log.endswith("/logs/02-bad.log")
    text = (tmp_path / "logs" / "02-bad.log").read_text(encoding="utf-8")
    assert text.startswith("exit code 1\n") and "boom: details here" in text
    assert said[-1].endswith(f"boom: details here; output: {bad.log}")  # only a failed step is pointed at
    assert "output:" not in " ".join(m for m in said if "[1/2] good" in m)
    assert G.run([_step("x", 0)])[0].log == ""  # no log_dir: nothing written


def test_workdirs_keep_their_logs_after_a_green_run(satk_home):
    d = G.claim_workdirs("abc99999")
    (d.logs).mkdir(parents=True)
    (d.logs / "01-x.log").write_text("x", encoding="utf-8")
    d.release(clean=True)
    assert (d.logs / "01-x.log").exists() and not d.work.exists() and not d.temp.exists()
    again = G.claim_workdirs("abc99999")  # the next run starts with empty logs
    assert not (again.logs / "01-x.log").exists()
    again.release(clean=True)
