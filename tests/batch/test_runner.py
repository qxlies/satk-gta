"""``satk batch`` / ``batch report``: counts, exit codes, JSONL, resume, dry run, threads, policy."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

import synth
from synth import BROKEN
from satk.batch.runner import item_key, parse_kv, read_results, run_batch
from satk.core.errors import SatkError
from satk.core.paths import jpath


def _noop(target: str) -> dict:
    return {}


def _lines(p: Path) -> list[dict]:
    return [json.loads(s) for s in p.read_text(encoding="utf-8").splitlines() if s.strip()]


# --------------------------------------------------------------------------- the acceptance case


@pytest.mark.parametrize("jobs", [1, 4])
def test_lint_50_dffs_with_3_broken(dff_dir, satk_home, run_cli, tmp_path, jobs):
    out = tmp_path / "res.jsonl"
    r = run_cli(["batch", "asset.lint", "--over", str(dff_dir / "*.dff"), "--arg", "fail_on=error",
                 "--jobs", str(jobs), "--out", str(out)])
    assert r.code == 1, r.out
    env = r.json
    err = env["error"]
    assert err["code"] == "CHECK_FAILED"
    assert "3 of 50 input(s) failed, 47 ok" in err["msg"]
    data = err["data"]
    assert data["counts"] == {"ok": 47, "fail": 3} and data["by_code"] == {"CHECK_FAILED": 3}
    assert data["totals"]["fatal"] == 3 and data["totals"]["error"] == 0
    assert [row[0] for row in data["rows"]] == [f"car{i:02d}.dff" for i in BROKEN]
    assert data["base"] == jpath(dff_dir) and data["jobs"] == jobs and data["out"] == jpath(out)
    recs = _lines(out)
    assert len(recs) == 50 and [x["n"] for x in recs] == list(range(1, 51))  # input order after the rewrite
    assert sorted(x["n"] for x in recs if x["status"] == "fail") == list(BROKEN)
    assert all(x["op"] == "asset.lint" and x["args"]["fail_on"] == "error" for x in recs)
    assert "hint" in err and "batch report" in err["hint"]


def test_all_ok_exit_zero_and_table_mode(dff_dir, satk_home, run_cli, tmp_path):
    for i in BROKEN:
        (dff_dir / f"car{i:02d}.dff").unlink()
    r = run_cli(["batch", "asset", "lint", "--over", str(dff_dir / "*.dff"), "--arg", "fail_on=error"])
    assert r.code == 0, r.out
    env = r.json
    assert env["ok"] is True and env["counts"] == {"ok": 47, "fail": 0} and env["rows"] == []
    assert env["out"].startswith(jpath(satk_home / "work" / "out" / "batch" / "asset.lint-"))
    t = run_cli(["batch", "asset.lint", "--over", str(dff_dir / "*.dff"), "--arg", "fail_on=error"], tty=True)
    assert t.code == 0 and "counts.ok: 47" in t.out


def test_resume_skips_done_inputs(dff_dir, satk_home, tmp_path):
    out = tmp_path / "res.jsonl"
    kw = dict(arg=["fail_on=error"], out=str(out))
    with pytest.raises(SatkError):
        run_batch("asset.lint", str(dff_dir / "*.dff"), **kw)
    first = {x["key"]: x for x in _lines(out)}
    # resume: the 47 ok inputs are skipped, the 3 failures run again (and fail again)
    with pytest.raises(SatkError) as ei:
        run_batch("asset.lint", str(dff_dir / "*.dff"), resume=True, **kw)
    d = ei.value.data
    assert d["counts"] == {"ok": 0, "fail": 3, "resumed": 47}
    assert "47 done earlier" in ei.value.msg
    # fix the broken files: a resumed run runs only those 3 and ends green with 50 records
    good = synth.dff()
    for i in BROKEN:
        (dff_dir / f"car{i:02d}.dff").write_bytes(good)
    env = run_batch("asset.lint", str(dff_dir / "*.dff"), resume=True, **kw)
    assert env["counts"] == {"ok": 3, "fail": 0, "resumed": 47}
    recs = _lines(out)
    assert len(recs) == 50 and all(x["status"] == "ok" for x in recs)
    assert {x["key"] for x in recs} == set(first)  # stable keys
    # without --resume the file is rewritten from scratch
    env = run_batch("asset.lint", str(dff_dir / "*.dff"), **kw)
    assert env["counts"] == {"ok": 50, "fail": 0}


def test_interrupted_run_keeps_records(dff_dir, satk_home, tmp_path, fake_ops):
    calls = []

    def flaky(target: str) -> dict:
        calls.append(target)
        if len(calls) == 5:
            raise KeyboardInterrupt
        return {"seen": Path(target).name}

    fake_ops("t.flaky", flaky)
    out = tmp_path / "r.jsonl"
    with pytest.raises(KeyboardInterrupt):
        run_batch("t.flaky", str(dff_dir / "*.dff"), out=str(out))
    assert len(_lines(out)) == 4  # appended as they finished
    env = run_batch("t.flaky", str(dff_dir / "*.dff"), out=str(out), resume=True)
    assert env["counts"] == {"ok": 46, "fail": 0, "resumed": 4}
    assert len(read_results(out)) == 50 and len(_lines(out)) == 50


def test_dry_run_writes_nothing(dff_dir, satk_home, work_files, run_cli, tmp_path):
    before = work_files()
    out = tmp_path / "never.jsonl"
    r = run_cli(["batch", "asset.lint", "--over", str(dff_dir / "*.dff"), "--arg", "sev=info", "--dry-run",
                 "--limit", "5", "--out", str(out)])
    assert r.code == 0, r.out
    env = r.json
    assert env["dry_run"] is True and env["items"] == 50 and env["n"] == 5 and env["total"] == 50
    assert env["rows"][0][:3] == [1, "car01.dff", "run"]
    assert json.loads(env["rows"][0][3]) == {"sev": "info", "target": jpath(dff_dir / "car01.dff")}
    r2 = run_cli(["batch", "asset.lint", "--over", str(dff_dir / "*.dff"), "--dry-run"])
    assert r2.code == 0
    assert work_files() == before and not out.exists()


def test_dry_run_shows_what_resume_skips_and_bad_inputs(dff_dir, satk_home, tmp_path, fake_ops):
    def need_n(target: str, n: int) -> dict:
        return {"n": n}

    fake_ops("t.n", need_n)
    lst = tmp_path / "l.txt"
    lst.write_text('{"target": "a", "n": 1}\n{"target": "b", "n": "x"}\n', encoding="utf-8")
    env = run_batch("t.n", f"@{lst}", dry_run=True)
    assert env["invalid"] == 1 and [r[2] for r in env["rows"]] == ["run", "invalid"]
    assert "b: n: expected an integer" in env["first_invalid"]


def test_max_fail_stops_and_resume_finishes(dff_dir, satk_home, tmp_path):
    out = tmp_path / "r.jsonl"
    with pytest.raises(SatkError) as ei:
        run_batch("asset.lint", str(dff_dir / "*.dff"), arg=["fail_on=error"], out=str(out), max_fail=2)
    d = ei.value.data
    assert d["counts"]["fail"] == 2 and d["counts"]["not_run"] == 50 - 23
    assert any(w.startswith("MAX_FAIL") for w in d["warn"])
    with pytest.raises(SatkError) as ei:
        run_batch("asset.lint", str(dff_dir / "*.dff"), arg=["fail_on=error"], out=str(out), resume=True)
    assert ei.value.data["counts"] == {"ok": 26, "fail": 3, "resumed": 21}  # 21 ok before input 23 stopped it


def test_templates_param_and_keep(tmp_path, satk_home, fake_ops):
    def echo(target: str, label: str = "", extra: int = 0) -> dict:
        return {"target": target, "label": label, "extra": extra}

    fake_ops("t.echo", echo)
    for name in ("x.dff", "y.txd"):
        (tmp_path / name).write_bytes(b"1")
    out = tmp_path / "r.jsonl"
    env = run_batch("t.echo", str(tmp_path / "*.*"), arg=["label={n}:{stem}.{ext}@{name}", "extra=7"],
                    out=str(out), keep="brief")
    assert env["counts"] == {"ok": 2, "fail": 0}
    recs = _lines(out)
    assert [x["result"]["label"] for x in recs] == ["1:x.dff@x.dff", "2:y.txd@y.txd"]
    assert recs[0]["result"]["extra"] == 7
    run_batch("t.echo", str(tmp_path / "*.*"), arg=["extra=1"], out=str(out), keep="none")
    assert all("result" not in x for x in _lines(out))
    # --param chooses the parameter of the inputs; the inputs override the same --arg
    env = run_batch("t.echo", "a,b", param="label", arg=["target=T", "label=ignored"], out=str(out))
    assert [x["result"]["label"] for x in _lines(out)] == ["a", "b"]
    # --param none: the input only feeds the templates
    env = run_batch("t.echo", "a,b", param="none", arg=["target=T-{item}", "label=L{n}"], out=str(out))
    assert [(x["result"]["target"], x["result"]["label"]) for x in _lines(out)] == [("T-a", "L1"), ("T-b", "L2")]


def test_parse_kv_merges_comma_fragments():
    assert parse_kv(["sev=info", "rule=dff.parse", "txd.pow2", "--fail-on=error"]) == {
        "sev": "info", "rule": "dff.parse,txd.pow2", "fail_on": "error"}
    with pytest.raises(SatkError):
        parse_kv(["novalue"])
    with pytest.raises(SatkError):
        parse_kv(["=x"])


def test_errors_before_anything_runs(dff_dir, satk_home, run_cli):
    r = run_cli(["batch", "asset.lnt", "--over", str(dff_dir / "*.dff")])
    assert r.code == 1 and r.json["error"]["code"] == "NOT_FOUND"
    assert "asset.lint" in r.json["error"]["did_you_mean"]
    r = run_cli(["batch", "asset.lint", "--over", str(dff_dir / "*.dff"), "--arg", "sevv=info"])
    assert r.code == 2 and "sev" in r.json["error"]["did_you_mean"]
    r = run_cli(["batch", "asset.lint", "--over", str(dff_dir / "*.dff"), "--arg", "sev=loud"])
    assert r.code == 2 and "car01.dff" in r.json["error"]["msg"]
    r = run_cli(["batch", "asset.lint"])
    assert r.code == 2 and "--over" in r.json["error"]["msg"]
    r = run_cli(["batch", "asset.lint", "--over", str(dff_dir / "*.nope")])
    assert r.code == 1 and r.json["error"]["code"] == "NOT_FOUND"
    r = run_cli(["batch", "asset.lint", "--over", str(dff_dir / "*.nope"), "--allow-empty"])
    assert r.code == 0 and r.json["items"] == 0
    r = run_cli(["batch", "batch", "--over", "a"])
    assert r.code == 2


def test_threads_only_for_thread_safe_ops(tmp_path, satk_home, fake_ops):
    seen: set[str] = set()

    def who(target: str) -> dict:
        seen.add(threading.current_thread().name)
        return {}

    fake_ops("t.who", who)
    env = run_batch("t.who", ",".join(f"i{k}" for k in range(20)), jobs=4, out=str(tmp_path / "a.jsonl"))
    assert env["jobs"] == 1 and any(w.startswith("JOBS:") for w in env["warn"])
    assert not any(n.startswith("satk-batch") for n in seen)
    from satk.core.registry import get_op

    get_op("t.who").extra["thread_safe"] = True
    seen.clear()
    env = run_batch("t.who", ",".join(f"i{k}" for k in range(20)), jobs=4, out=str(tmp_path / "b.jsonl"))
    assert env["jobs"] == 4 and "warn" not in env
    assert seen and all(n.startswith("satk-batch") for n in seen)


def test_duplicates_run_once(tmp_path, satk_home, fake_ops):
    fake_ops("t.one", _noop)
    env = run_batch("t.one", "a,b,a", out=str(tmp_path / "r.jsonl"))
    assert env["items"] == 2 and any(w.startswith("DUPLICATE: 1") for w in env["warn"])


def test_item_key_is_stable():
    assert item_key("asset.lint", {"target": "a", "sev": "info"}) == item_key("asset.lint", {"sev": "info",
                                                                                              "target": "a"})
    assert item_key("asset.lint", {"target": "a"}) != item_key("asset.lint", {"target": "b"})


# --------------------------------------------------------------------------- policy


def test_consent_and_cli_only_ops_need_yes_on_the_command_line(tmp_path, satk_home, fake_ops, run_cli):
    ran: list[str] = []

    def wipe(target: str) -> dict:
        ran.append(target)
        return {}

    fake_ops("t.wipe", wipe, consent=True)
    fake_ops("t.cli", wipe, consent=False)
    out = str(tmp_path / "r.jsonl")
    with pytest.raises(SatkError) as ei:
        run_batch("t.wipe", "a,b", out=out)
    assert ei.value.code == "CONSENT_REQUIRED" and "--yes" in ei.value.hint
    with pytest.raises(SatkError) as ei:  # an API/MCP caller cannot give consent
        run_batch("t.wipe", "a,b", out=out, yes=True)
    assert ei.value.code == "CONSENT_REQUIRED" and "command line" in ei.value.msg
    with pytest.raises(SatkError) as ei:
        run_batch("t.cli", "a", out=out)
    assert ei.value.code == "UNSUPPORTED"
    assert ran == []
    r = run_cli(["batch", "t.wipe", "--over", "a,b", "--out", out])
    assert r.code == 1 and r.json["error"]["code"] == "CONSENT_REQUIRED" and ran == []
    r = run_cli(["batch", "t.wipe", "--over", "a,b", "--out", out, "--yes"])
    assert r.code == 0 and ran == ["a", "b"]


def test_never_ops_are_refused_even_with_yes(satk_home, run_cli, fake_ops):
    fake_ops("view.mock", _noop)
    r = run_cli(["batch", "view.mock", "--over", "1", "--yes"])
    assert r.code == 1 and r.json["error"]["code"] == "UNSUPPORTED"


def test_group_restriction_outside_the_cli(satk_home, monkeypatch, fake_ops, tmp_path):
    fake_ops("re.thing", _noop)
    monkeypatch.setenv("SATK_MCP_GROUPS", "index,core")
    with pytest.raises(SatkError) as ei:
        run_batch("re.thing", "a", out=str(tmp_path / "x.jsonl"))
    assert ei.value.code == "UNSUPPORTED" and "SATK_MCP_GROUPS" in ei.value.msg


# --------------------------------------------------------------------------- report


def test_report(dff_dir, satk_home, run_cli, tmp_path):
    out = tmp_path / "r.jsonl"
    run_cli(["batch", "asset.lint", "--over", str(dff_dir / "*.dff"), "--arg", "fail_on=error", "--out", str(out)])
    r = run_cli(["batch", "report", str(out), "--status", "fail"])
    env = r.json
    assert r.code == 0 and env["total"] == 3 and [row[0] for row in env["rows"]] == [f"car{i:02d}.dff" for i in BROKEN]
    assert env["counts"] == {"fail": 3, "ok": 47} and env["totals"]["fatal"] == 3 and env["op"] == "asset.lint"
    r = run_cli(["batch", "report", str(out), "--field", "summary.fatal", "--field", "files", "--limit", "10"])
    env = r.json
    assert env["cols"] == ["item", "status", "summary.fatal", "files"] and env["next"] == "10"
    assert env["rows"][0] == ["car01.dff", "ok", 0, 1]
    assert env["rows"][6] == ["car07.dff", "fail", 1, None]  # a failed answer: summary from error.data
    page2 = run_cli(["batch", "report", str(out), "--cursor", "10", "--limit", "10"]).json
    assert page2["rows"][0][0] == "car11.dff"
    r = run_cli(["batch", "report", str(tmp_path / "none.jsonl")])
    assert r.code == 1 and r.json["error"]["code"] == "NOT_FOUND"
