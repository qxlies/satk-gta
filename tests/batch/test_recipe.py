"""Recipes: loading and validation, references and filters, run rules, dry run, policy, list/show."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from satk.batch.recipe import load_recipe, parse_recipe, run_recipe
from satk.batch.yamlite import loads
from satk.core.errors import SatkError
from satk.core.paths import jpath

SHIPPED = ("check-mod-before-release", "crash-triage", "export-all-textures-of-a-mod", "many-cars-lint-and-pack")


def _write(tmp_path: Path, text: str, name: str = "r.yaml") -> str:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


@pytest.fixture
def ops(fake_ops):
    """Fake operations that record their calls."""
    calls: list[tuple[str, dict]] = []

    def files(folder: str, limit: int = 20) -> dict:
        calls.append(("t.files", {"folder": folder, "limit": limit}))
        rows = [[f"{folder}/a.dff", 10], [f"{folder}/b.txd", 20]]
        return {"ok": True, "cols": ["path", "size"], "rows": rows, "n": 2, "total": 2, "next": None,
                "summary": {"error": 0, "warn": 1}}

    def stat(path: str, mode: str = "stats", tags: list[str] | None = None) -> dict:
        calls.append(("t.stat", {"path": path, "mode": mode, "tags": tags}))
        if path.endswith("boom"):
            raise SatkError("NOT_FOUND", f"no {path}")
        return {"ok": True, "path": path, "mode": mode, "size": len(path), "tags": tags or []}

    def many(items: list[str]) -> dict:
        calls.append(("t.many", {"items": items}))
        return {"ok": True, "count": len(items)}

    def danger(target: str) -> dict:
        calls.append(("t.danger", {"target": target}))
        return {"ok": True}

    fake_ops("t.files", files)
    fake_ops("t.stat", stat)
    fake_ops("t.many", many)
    fake_ops("t.danger", danger, consent=True)
    return calls


# --------------------------------------------------------------------------- shipped recipes


@pytest.mark.parametrize("name", SHIPPED)
def test_shipped_recipes_load(name, satk_home):
    r = load_recipe(name)
    assert r.name == name and r.source == "shipped" and r.summary and r.steps
    assert len(r.summary) <= 300
    assert any(v.required for v in r.vars.values()) or name == "crash-triage"


def test_list_and_show(satk_home, run_cli):
    env = run_cli(["recipe", "list"]).json
    assert [r[0] for r in env["rows"]] == sorted(SHIPPED)
    assert {r[1] for r in env["rows"]} == {"shipped"}
    show = run_cli(["recipe", "show", "check-mod-before-release"]).json
    assert show["vars"]["mod"]["required"] is True and show["run"].endswith("--var mod=...")
    steps = {r[0]: r for r in show["rows"]}
    assert steps["inspect"][2] == "mod.inspect"
    assert steps["textures"][1] == "texture.audit"
    r = run_cli(["recipe", "show", "check-mod-before-relase"])
    assert r.code == 1 and r.json["error"]["did_you_mean"] == ["check-mod-before-release"]


def test_user_recipes_win_over_shipped(satk_home, run_cli):
    d = satk_home / "work" / "recipes"
    d.mkdir(parents=True)
    (d / "crash-triage.yaml").write_text("summary: mine\nsteps:\n  - op: version\n", encoding="utf-8")
    (d / "extra.json").write_text(json.dumps({"summary": "json one", "steps": [{"op": "version"}]}), encoding="utf-8")
    (d / "broken.yaml").write_text("steps: [\n", encoding="utf-8")
    env = run_cli(["recipe", "list"]).json
    rows = {r[0]: r for r in env["rows"]}
    assert rows["crash-triage"][1:3] == ["user", "mine"] and rows["extra"][1:3] == ["user", "json one"]
    assert rows["broken"][2].startswith("(invalid") and any(w.startswith("BAD_RECIPE: broken") for w in env["warn"])
    assert run_cli(["recipe", "run", "extra"]).json["counts"] == {"ok": 1}


# --------------------------------------------------------------------------- validation


@pytest.mark.parametrize("text,words", [
    ("steps: []", "non-empty list"),
    ("stepz: [{op: a}]", "unknown key 'stepz'"),
    ("steps: [{op: a, args: 3}]", "'args' must be a mapping"),
    ("steps: [{opp: a}]", "unknown key 'opp'"),
    ("steps: [{id: x, op: a}, {id: x, op: b}]", "duplicate id"),
    ("steps: [{op: a, args: {p: '${nope}'}}]", "unknown variable"),
    ("steps: [{id: a, op: a, args: {p: '${steps.b.x}'}}, {id: b, op: b}]", "names no earlier step"),
    ("steps: [{op: a, args: {p: '${work|upper}'}}]", "unknown filter"),
    ("steps: [{op: a, on_error: ignore}]", "on_error"),
    ("vars: {steps: 1}\nsteps: [{op: a}]", "reserved"),
    ("vars: {a: {defualt: 1}}\nsteps: [{op: a}]", "unknown key 'defualt'"),
    ("vars: {a: '${b}', b: 1}\nsteps: [{op: a}]", "unknown variable"),
    ("[1, 2]", "a recipe is a mapping"),
])
def test_validation_errors(text, words):
    with pytest.raises(SatkError) as ei:
        parse_recipe(loads(text), default_name="t")
    assert ei.value.code == "BAD_PARAMS" and words in ei.value.msg, ei.value.msg


def test_formats_json_toml_yaml(tmp_path, satk_home, ops):
    y = _write(tmp_path, "vars: {p: x}\nsteps:\n  - op: t.stat\n    input: ${p}\n")
    j = _write(tmp_path, json.dumps({"vars": {"p": "x"}, "steps": [{"op": "t.stat", "input": "${p}"}]}), "r.json")
    t = _write(tmp_path, 'vars = {p = "x"}\n[[steps]]\nop = "t.stat"\ninput = "${p}"\n', "r.toml")
    for f in (y, j, t):
        env = run_recipe(f, out=str(tmp_path / "o.json"))
        assert env["counts"] == {"ok": 1}, f
    with pytest.raises(SatkError) as ei:
        run_recipe(str(tmp_path / "missing.yaml"))
    assert ei.value.code == "NOT_FOUND"


# --------------------------------------------------------------------------- running


RECIPE = """
name: demo
vars:
  dir: {required: true, help: a folder}
  mode: stats
  tag: null
  derived: "${dir}/sub"
steps:
  - id: list
    op: t.files
    input: ${dir}
    args: {limit: 5}
  - id: first
    op: t.stat
    input: ${steps.list.col.path.0}
    args: {mode: "${mode}", tags: ["${steps.list.summary.warn}", "${steps.list.col.path|len}", "${tag}"]}
  - id: whole
    op: t.many
    input: ${steps.list.col.path}
  - id: text
    op: t.stat
    input: "n=${steps.list.total} last=${steps.list.rows.-1.0|name} stem=${steps.list.rows.1.0|stem} $${x} ${derived}"
  - id: cond
    op: t.stat
    input: x
    when: "${steps.list.summary.error}"
  - id: cond2
    op: t.stat
    input: "${steps.list.rows.0.0|ext}"
    unless: "${steps.list.summary.error}"
  - id: eq
    op: t.stat
    input: yes
    when: "${steps.first.mode|eq:STATS}"
"""


def test_run_references_filters_and_conditions(tmp_path, satk_home, ops, run_cli):
    f = _write(tmp_path, RECIPE)
    out = tmp_path / "out.json"
    r = run_cli(["recipe", "run", f, "--var", "dir=D:/m", "--out", str(out)])
    assert r.code == 0, r.out
    env = r.json
    assert [row[:3] for row in env["rows"]] == [
        ["list", "t.files", "ok"], ["first", "t.stat", "ok"], ["whole", "t.many", "ok"], ["text", "t.stat", "ok"],
        ["cond", "t.stat", "skipped"], ["cond2", "t.stat", "ok"], ["eq", "t.stat", "ok"]]
    assert env["rows"][4][3] == "when: false"
    assert env["vars"] == {"dir": "D:/m", "mode": "stats", "derived": "D:/m/sub"}
    calls = dict((k, v) for k, v in ops if k != "t.stat")
    assert calls["t.files"] == {"folder": "D:/m", "limit": 5}
    assert calls["t.many"] == {"items": ["D:/m/a.dff", "D:/m/b.txd"]}
    stats = [v for k, v in ops if k == "t.stat"]
    assert stats[0] == {"path": "D:/m/a.dff", "mode": "stats", "tags": ["1", "2"]}  # the null var is left out
    assert stats[1]["path"] == "n=2 last=b.txd stem=b ${x} D:/m/sub"
    assert stats[2]["path"] == "dff" and stats[3]["path"] == "yes"
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["recipe"] == "demo" and [s["status"] for s in doc["steps"]][:2] == ["ok", "ok"]
    assert doc["steps"][0]["result"]["rows"][0] == ["D:/m/a.dff", 10]
    # the same run gives the same file (no time in the data)
    first = out.read_bytes()
    run_cli(["recipe", "run", f, "--var", "dir=D:/m", "--out", str(out)])
    assert out.read_bytes() == first


def test_missing_ops_are_skipped_with_their_dependents(tmp_path, satk_home, ops):
    f = _write(tmp_path, """
steps:
  - id: audit
    op: [texture.audit, txd.audit]
    input: a
  - id: uses
    op: t.stat
    input: ${steps.audit.count}
  - id: rescued
    op: t.stat
    input: "${steps.audit.count|default:none}"
  - id: after
    op: t.stat
    input: b
""")
    env = run_recipe(f, out=str(tmp_path / "o.json"))
    assert [r[2] for r in env["rows"]] == ["skipped", "skipped", "ok", "ok"]
    assert env["rows"][0][3] == "op not available (texture.audit, txd.audit)"
    assert env["rows"][1][3] == "needs step 'audit' (skipped)"
    assert [v["path"] for k, v in ops] == ["none", "b"]
    assert env["counts"] == {"skipped": 2, "ok": 2}


def test_requires_skips_a_step_whose_inner_op_is_missing(tmp_path, satk_home, ops, run_cli):
    f = _write(tmp_path, """
steps:
  - id: inner
    op: batch
    requires: [t.stat, no.such.op]
    args: {op: no.such.op, over: "a,b"}
  - id: ok
    op: t.stat
    requires: t.files
    input: a
""")
    env = run_recipe(f, out=str(tmp_path / "o.json"))
    assert [r[2] for r in env["rows"]] == ["skipped", "ok"]
    assert env["rows"][0][3] == "op not available (no.such.op)"
    show = run_cli(["recipe", "show", f]).json
    assert show["rows"][0][2] == "not available (no.such.op)" and show["rows"][1][2] == "t.stat"


def test_first_available_candidate_is_used(tmp_path, satk_home, ops):
    f = _write(tmp_path, "steps:\n  - op: [no.such, t.stat]\n    input: a\n")
    env = run_recipe(f, out=str(tmp_path / "o.json"))
    assert env["rows"][0][1:3] == ["t.stat", "ok"]


def test_on_error_stop_continue_and_fail_if(tmp_path, satk_home, ops, run_cli):
    f = _write(tmp_path, """
steps:
  - id: soft
    op: t.stat
    input: soft-boom
    on_error: continue
  - id: judged
    op: t.files
    input: d
    on_error: continue
    fail_if: ["${steps.judged.summary.error}", "${steps.judged.summary.warn}"]
  - id: hard
    op: t.stat
    input: hard-boom
  - id: never
    op: t.stat
    input: z
""")
    r = run_cli(["recipe", "run", f, "--out", str(tmp_path / "o.json")])
    assert r.code == 1
    err = r.json["error"]
    assert err["code"] == "CHECK_FAILED" and "3 step(s) failed (soft, judged, hard); stopped at hard" in err["msg"]
    rows = {row[0]: row for row in err["data"]["rows"]}
    assert rows["soft"][2] == "fail" and rows["soft"][3].startswith("NOT_FOUND: no soft-boom")
    assert rows["judged"][2] == "fail" and rows["judged"][3].startswith("fail_if ${steps.judged.summary.warn} = 1")
    assert rows["never"][2:] == ["not run", "stopped after step hard"]
    assert err["data"]["counts"] == {"fail": 3, "not run": 1}
    assert [v.get("path") or v.get("folder") for k, v in ops] == ["soft-boom", "d", "hard-boom"]


def test_bad_reference_fails_the_step(tmp_path, satk_home, ops):
    f = _write(tmp_path, "steps:\n  - id: a\n    op: t.files\n    input: d\n"
                         "  - op: t.stat\n    input: ${steps.a.nope}\n")
    with pytest.raises(SatkError) as ei:
        run_recipe(f, out=str(tmp_path / "o.json"))
    row = ei.value.data["rows"][1]
    assert row[2] == "fail" and "no field 'nope'" in row[3] and "fields: ok, cols, rows" in row[3]


def test_dry_run_writes_and_runs_nothing(tmp_path, satk_home, ops, work_files, run_cli):
    f = _write(tmp_path, RECIPE)
    before = work_files()
    r = run_cli(["recipe", "run", f, "--var", "dir=D:/m", "--dry-run"])
    assert r.code == 0, r.out
    env = r.json
    assert env["dry_run"] is True and ops == []
    states = {row[0]: row[2] for row in env["rows"]}
    assert states["list"] == "run" and states["cond"].startswith("run (when/unless")
    assert json.loads(env["rows"][0][3]) == {"limit": 5, "folder": "D:/m"}
    assert work_files() == before
    assert env["out"] == jpath(satk_home / "work" / "out" / "recipes" / "demo.json")
    assert not Path(env["out"]).exists()


def test_static_argument_errors_stop_before_running(tmp_path, satk_home, ops):
    f = _write(tmp_path, "steps:\n  - op: t.files\n    input: d\n"
                         "  - op: t.stat\n    args: {path: x, mode: 3, bogus: 1}\n")
    with pytest.raises(SatkError) as ei:
        run_recipe(f)
    assert ei.value.code == "BAD_PARAMS" and "step step2" in ei.value.msg and "bogus" in ei.value.msg
    assert ops == []


def test_variables(tmp_path, satk_home, ops, run_cli):
    f = _write(tmp_path, RECIPE)
    r = run_cli(["recipe", "run", f])
    assert r.code == 2 and "missing variable(s): dir" in r.json["error"]["msg"]
    r = run_cli(["recipe", "run", f, "--var", "dri=x"])
    assert r.code == 2 and r.json["error"]["did_you_mean"][0] == "dir"
    env = run_recipe(f, vars={"dir": "E:/x", "mode": "full"}, out=str(tmp_path / "o.json"))
    assert env["vars"]["mode"] == "full"


def test_consent_is_checked_before_any_step(tmp_path, satk_home, ops, run_cli):
    f = _write(tmp_path, "steps:\n  - op: t.stat\n    input: a\n  - op: t.danger\n    input: b\n")
    with pytest.raises(SatkError) as ei:
        run_recipe(f, out=str(tmp_path / "o.json"))
    assert ei.value.code == "CONSENT_REQUIRED" and "step step2" in ei.value.msg
    with pytest.raises(SatkError):
        run_recipe(f, yes=True, out=str(tmp_path / "o.json"))  # not from the command line
    assert ops == []
    r = run_cli(["recipe", "run", f, "--yes", "--out", str(tmp_path / "o.json")])
    assert r.code == 0 and [k for k, _ in ops] == ["t.stat", "t.danger"]


def test_recursion_is_bounded(tmp_path, satk_home, ops):
    p = tmp_path / "self.yaml"
    p.write_text(f"steps:\n  - op: recipe.run\n    input: '{p.as_posix()}'\n", encoding="utf-8")
    with pytest.raises(SatkError) as ei:
        run_recipe(str(p), out=str(tmp_path / "o.json"))
    assert ei.value.code == "CHECK_FAILED"
    assert "BAD_PARAMS: recipe self runs itself: self -> self" in ei.value.data["rows"][0][3]


def test_nesting_depth_is_bounded(tmp_path, satk_home, ops):
    for i in range(6):  # r0 -> r1 -> ... -> r5: different recipes, too deep
        child = (tmp_path / f"r{i + 1}.yaml").as_posix()
        step = f"  - op: recipe.run\n    input: '{child}'\n" if i < 5 else "  - op: t.stat\n    input: x\n"
        (tmp_path / f"r{i}.yaml").write_text("steps:\n" + step, encoding="utf-8")
    with pytest.raises(SatkError) as ei:
        run_recipe(str(tmp_path / "r0.yaml"), out=str(tmp_path / "o.json"))
    assert ei.value.code == "CHECK_FAILED" and ops == []
    inner = (satk_home / "work" / "out" / "recipes" / "r3.json").read_text(encoding="utf-8")
    assert "batches and recipes nest 4 levels at most" in inner


def test_batch_as_a_step(tmp_path, satk_home, dff_dir):
    f = _write(tmp_path, """
vars: {dir: {required: true}}
steps:
  - id: lint
    op: batch
    args: {op: asset.lint, over: "${dir}/*.dff", arg: ["fail_on=error"], out: "${work}/out/b.jsonl", keep: none}
    on_error: continue
  - id: count
    op: batch.report
    input: ${steps.lint.error.data.out}
    args: {status: fail}
""")
    with pytest.raises(SatkError) as ei:
        run_recipe(f, var=[f"dir={dff_dir.as_posix()}"], out=str(tmp_path / "o.json"))
    rows = {r[0]: r for r in ei.value.data["rows"]}
    assert rows["lint"][2] == "fail" and "3 of 50 input(s) failed" in rows["lint"][3]
    assert rows["count"][2] == "ok" and "fail=3" in rows["count"][3] and "ok=47" in rows["count"][3]


def test_crash_triage_on_a_generated_sample(satk_home, run_cli):
    from satk.core.registry import invoke

    sample = invoke("crash.sample", {"kind": "sp"})
    assert sample["ok"], sample
    r = run_cli(["recipe", "run", "crash-triage", "--var", f"game={sample['game']}"])
    assert r.code == 0, r.out
    rows = {row[0]: row for row in r.json["rows"]}
    assert [rows[s][2] for s in ("recent", "analyze", "modules", "logs", "bisect")] == ["ok"] * 5
    assert "ACCESS_VIOLATION" in rows["analyze"][3]
    doc = json.loads(Path(r.json["out"]).read_text(encoding="utf-8"))
    analyze = next(s for s in doc["steps"] if s["id"] == "analyze")
    assert analyze["args"] == {"last": True, "game": sample["game"]}
    assert "0x00456809" in json.dumps(analyze["result"]["known"])
    dry = run_cli(["recipe", "run", "crash-triage", "--dry-run"]).json
    assert {row[0]: row[2] for row in dry["rows"]}["logs"] == "when: false"
