"""satk ingame bench: scenes as data, statistics, the A/B rules on recorded results, the run loop and the operations
(no game needed: the client is replaced by fakes and by recorded JSON)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from satk.core import paths
from satk.core.errors import SatkError
from satk.core.registry import get_op, invoke
from satk.ingame import bench as B
from satk.ingame import bench_scenes as BS
from satk.ingame import ops as O
from satk.ingame import resource as R
from satk.ingame import session as S
from satk.ingame import spots as SP

FIX = Path(__file__).resolve().parent / "fixtures" / "bench"


def fx(name: str) -> dict:
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- scenes


def test_nine_scenes_exist_as_json_data():
    assert BS.scene_ids() == [f"S{i}" for i in range(1, 10)]
    for sid in BS.scene_ids():
        sc = BS.scene(sid)
        assert sc["title"] and sc["purpose"] and sc["stages"]
        json.dumps(sc)                                     # plain data: sent to the client as a Lua literal
        ids = [s["id"] for s in sc["stages"]]
        assert len(ids) == len(set(ids))
        for st in BS.resolve(sid):
            assert st["warmup"] >= 0 and st["sample"] >= 1 and st["env"]["freeze"] is True
            cam = st["camera"]
            assert cam["kind"] in ("fixed", "path", "player")


def test_scene_contents_follow_the_design():
    s1 = BS.resolve("S1")
    assert [s["fps_limit"] for s in s1] == [60, 144, 165, 240] and all(len(s["peds"]) == 5 for s in s1)
    assert [s["server_fps"] for s in s1] == [60, 144, 165, 240] and all("console" not in s for s in s1)
    gx, gy, _ = SP.SPOTS["grove"]["pos"]
    assert all(abs(p["x"] - gx) < 12 and abs(p["y"] - gy) < 12 for p in s1[0]["peds"])
    s2 = BS.resolve("S2")[0]
    assert s2["sample"] == 180 and s2["camera"]["kind"] == "path" and {p["z"] for p in s2["camera"]["points"]} == {120.0}
    assert BS.resolve("S2", duration=300)[0]["sample"] == 300
    us = [p["u"] for p in s2["camera"]["points"]]
    assert us[0] == 0 and us[-1] == 1 and us == sorted(us)
    s3 = BS.resolve("S3")[0]
    assert s3["sample"] == 300 and {p["z"] for p in s3["camera"]["points"]} == {60.0}
    s4 = BS.resolve("S4")[0]
    assert s4["env"]["timelapse"] == {"from": "18:00", "to": "19:30"} and s4["env"]["time"] == "18:00"
    s5 = BS.resolve("S5")[0]
    assert len(s5["vehicles"]) == 64 and len({(v["x"], v["y"]) for v in s5["vehicles"]}) == 64
    xs = sorted({v["x"] for v in s5["vehicles"]})
    assert xs[1] - xs[0] == pytest.approx(4.8) and SP.SPOTS["pad"]["pos"][0] - 25 < xs[0] < SP.SPOTS["pad"]["pos"][0]
    s6 = BS.resolve("S6")[0]
    assert len(s6["peds"]) == 110 and s6["walk"] is True
    s7 = BS.resolve("S7")
    assert [s["id"] for s in s7] == ["v1", "v2", "v3", "v4", "v5", "v6"] and all(s["sample"] == 10 for s in s7)
    assert s7[0]["camera"]["pos"] == [-2324.0, -1636.0, 500.0] and s7[0]["camera"]["look"] == [1500.0, -1500.0, 40.0]
    assert s7[3]["camera"]["pos"] == s7[5]["camera"]["pos"] and s7[5]["env"]["weather"] == 9 and s7[3]["env"]["weather"] == 1
    s8 = BS.resolve("S8")[0]
    assert s8["probe"]["kind"] == "weapon" and s8["probe"]["weapon"] == 31 and s8["sample"] == 20
    s9 = BS.resolve("S9")[0]["probe"]
    assert (s9["kind"], s9["tex_mib"], s9["dff_mib"], s9["yellow_mib"]) == ("loader", 4, 2, 3072.0)


def test_resolve_overrides_and_validation():
    st = BS.resolve("s7", duration=3, warmup=0)
    assert all(s["sample"] == 3 and s["warmup"] == 0 for s in st) and BS.total_seconds(st) == 18
    assert BS.SCENES["S7"]["stages"][0]["sample"] == 10                  # the data itself is never mutated
    for bad in ({"duration": 0}, {"duration": 99999}, {"warmup": -1}):
        with pytest.raises(SatkError) as e:
            BS.resolve("S1", **bad)
        assert e.value.code == "BAD_PARAMS"
    with pytest.raises(SatkError) as e:
        BS.scene("S12")
    assert e.value.code == "BAD_PARAMS" and e.value.did_you_mean
    rows = BS.rows()
    assert [r[0] for r in rows] == BS.scene_ids() and rows[1][2] == 185.0
    assert invoke("ingame.bench_scenes", {})["n"] == 9


def test_make_path():
    p = BS.make_path([(0, 0), (30, 40), (30, 140)], 50)
    assert [q["u"] for q in p] == [0, 0.33333, 1.0] and p[1]["x"] == 30 and p[2]["z"] == 50
    with pytest.raises(ValueError):
        BS.make_path([(0, 0)], 1)


# --------------------------------------------------------------------------- statistics


def test_frame_stats_and_pooling():
    f = [10.0] * 98 + [20.0, 40.0]
    s = B.frame_stats(f, 1100)
    assert s["n"] == 100 and s["p50_ms"] == 10 and s["p99_ms"] == 20 and s["max_ms"] == 40
    assert s["low1_fps"] == 25.0 and s["fps_avg"] == pytest.approx(1000 / 10.4, abs=0.01) and s["fps_wall"] == 90.91
    assert s["integer_share"] == 1.0 and len(s["q"]) == 101 and B.frame_stats([])["n"] == 0
    a = {"frames": B.frame_stats([5.0] * 100)}
    b = {"frames": B.frame_stats([15.0] * 100)}
    assert B.pooled_percentile([a, b], 25) == 5 and B.pooled_percentile([a, b], 75) == 15
    assert B.pooled_percentile([{"frames": {}}], 50) is None
    sm = B.summarize([dict(a, va={"peak_mib": 1000, "growth_mib": 5}), dict(b, va={"peak_mib": 1200, "growth_mib": 7})])
    assert sm["stages"] == 2 and sm["frames"] == 200 and sm["pooled"] is True and sm["va_peak_mib"] == 1200
    assert sm["va_growth_mib"] == 12 and sm["max_ms"] == 15


def test_fixtures_are_recorded_results():
    d = fx("S2-mta")
    assert d["schema"] == B.SCHEMA and d["scene"] == "S2" and d["client"]["sae"] is True
    st = d["stages"][0]
    assert st["frames"]["q"][50] == st["frames"]["p50_ms"] and st["series"] and st["clock"]["ratio"] == 0.998
    assert d["summary"]["frames"] == st["frames"]["n"]


# --------------------------------------------------------------------------- compare rules


def test_compare_passes_a_small_change():
    r = B.compare(fx("S2-mta"), fx("S2-sae-good"))
    assert r["verdict"] == "pass" and r["failed"] == []
    byk = {(row[0], row[1]): row for row in r["rows"]}
    assert byk[("flyover", "p50_ms")][4:] == ["+2.5 %", "<= +5 %", "pass"]
    assert byk[("flyover", "p99_ms")][4:] == ["+4.0 %", "<= +10 %", "pass"]
    assert byk[("flyover", "va_peak_mib")][6] == "pass" and byk[("flyover", "clock_ratio")][6] == "info"


def test_compare_fails_on_p50_only():
    r = B.compare(fx("S2-mta"), fx("S2-sae-slow-p50"))
    assert r["verdict"] == "FAIL" and r["failed"] == ["flyover/p50_ms"]


def test_compare_fails_on_p99_only():
    r = B.compare(fx("S2-mta"), fx("S2-sae-slow-p99"))
    assert r["verdict"] == "FAIL" and r["failed"] == ["flyover/p99_ms"]
    byk = {row[1]: row for row in r["rows"]}
    assert byk["p50_ms"][6] == "pass" and byk["p99_ms"][4] == "+20.0 %"


def test_compare_fails_when_va_reaches_yellow():
    r = B.compare(fx("S2-mta"), fx("S2-sae-va-over"))
    assert r["verdict"] == "FAIL" and r["failed"] == ["flyover/va_peak_mib"]
    ok = B.compare(fx("S2-mta"), fx("S2-sae-va-over"), yellow_mib=3200)
    assert ok["verdict"] == "pass"


def test_compare_tolerance_boundaries():
    a = fx("S2-mta")
    for factor, verdict in ((1.05, "pass"), (1.0501, "FAIL")):
        b = copy.deepcopy(a)
        f = b["stages"][0]["frames"]
        f["p50_ms"] = round(a["stages"][0]["frames"]["p50_ms"] * factor, 4)
        r = B.compare(a, b)
        assert {row[1]: row[6] for row in r["rows"]}["p50_ms"] == verdict
    b = copy.deepcopy(a)
    b["stages"][0]["frames"]["p99_ms"] = a["stages"][0]["frames"]["p99_ms"] * 1.10
    assert B.compare(a, b)["verdict"] == "pass"
    assert B.compare(a, b, p99_tol=5.0)["verdict"] == "FAIL"
    # a faster candidate always passes
    c = copy.deepcopy(a)
    c["stages"][0]["frames"].update(p50_ms=4.0, p99_ms=10.0)
    assert B.compare(a, c)["verdict"] == "pass"


def test_compare_edge_cases():
    a = fx("S2-mta")
    tiny = copy.deepcopy(a)
    tiny["stages"][0]["frames"]["n"] = 10
    r = B.compare(a, tiny)
    assert r["verdict"] == "FAIL" and r["rows"][0][1] == "frames"
    novas = copy.deepcopy(a)
    del novas["stages"][0]["va"]
    r = B.compare(a, novas)
    assert r["verdict"] == "pass" and {row[1]: row[6] for row in r["rows"]}["va_peak_mib"] == "n/a"
    other = copy.deepcopy(a)
    other["stages"][0]["id"] = "elsewhere"
    with pytest.raises(SatkError) as e:
        B.compare(a, other)
    assert e.value.code == "BAD_PARAMS"
    two = copy.deepcopy(a)
    two["stages"].append(dict(copy.deepcopy(a["stages"][0]), id="extra"))
    r = B.compare(a, two)
    assert any(w.startswith("STAGES: B has stages") for w in r["warn"])
    s7 = copy.deepcopy(a)
    s7["scene"] = "S7"
    assert any(w.startswith("SCENE:") for w in B.compare(a, s7)["warn"])


# --------------------------------------------------------------------------- files


@pytest.fixture
def bench_home(satk_home):
    return satk_home


def test_result_files_and_lookup(bench_home):
    d = fx("S2-mta")
    p = B.result_path("run 1", "s2", "my label")
    assert p.parent.name == "run_1" and p.name == "S2-my_label.json"
    paths.atomic_write(p, json.dumps(d))
    older = B.result_path("20200101-000000", "S2", "mta")
    paths.atomic_write(older, json.dumps(dict(d, label="old")))
    assert B.load_result(str(p))[1] == p
    assert B.load_result("run_1/S2-my_label")[0]["label"] == "mta"
    assert B.load_result("S2-mta")[0]["label"] == "old"                  # newest run that has it
    assert B.load_result("S2-my_label")[1] == p
    with pytest.raises(SatkError) as e:
        B.load_result("S2-nothing")
    assert e.value.code == "NOT_FOUND"
    bad = bench_home / "work" / "bad.json"
    bad.write_text("[1]", encoding="utf-8")
    with pytest.raises(SatkError) as e:
        B.load_result(str(bad))
    assert e.value.code == "BAD_PARAMS"


def test_bench_compare_op(bench_home):
    a, b, c = B.result_path("r", "S2", "mta"), B.result_path("r", "S2", "sae"), B.result_path("r", "S2", "slow")
    paths.atomic_write(a, json.dumps(fx("S2-mta")))
    paths.atomic_write(b, json.dumps(fx("S2-sae-good")))
    paths.atomic_write(c, json.dumps(fx("S2-sae-slow-p99")))
    ok = invoke("ingame.bench_compare", {"a": "r/S2-mta", "b": "r/S2-sae"})
    assert ok["cols"] == ["stage", "metric", "A", "B", "delta", "limit", "verdict"] and ok["verdict"] == "pass"
    assert ok["A"].endswith("r/S2-mta.json") and "failed" not in ok
    bad = invoke("ingame.bench_compare", {"a": "r/S2-mta", "b": "r/S2-slow"})
    assert bad["verdict"] == "FAIL" and bad["failed"] == ["flyover/p99_ms"]
    loose = invoke("ingame.bench_compare", {"a": "r/S2-mta", "b": "r/S2-slow", "p99_tol": 25})
    assert loose["verdict"] == "pass"


# --------------------------------------------------------------------------- the run loop with a fake client


class FakeClient:
    """Scripted ``satk-bench`` client: results come from the recorded fixture."""

    def __init__(self, busy_polls: int = 1, fail_at: str | None = None, hello: dict | None = None, hang: bool = False):
        self.log: list[tuple[str, dict]] = []
        self.busy_polls, self.fail_at, self.hang = busy_polls, fail_at, hang
        self.polls = 0
        self.stage: dict = {}
        self._hello = hello or {"version": 1, "features": {"getEngineStats": False}, "screen": [1280, 720], "sae": False}

    def call(self, cmd, args=None):
        args = args or {}
        self.log.append((cmd, args))
        if cmd == "hello":
            return dict(self._hello)
        if cmd == "stage":
            self.stage, self.polls = args["stage"], 0
            return {"ok": True}
        if cmd == "status":
            self.polls += 1
            if self.fail_at == self.stage.get("id"):
                return {"state": "error", "busy": False, "err": "boom"}
            return {"state": "running", "busy": self.hang or self.polls <= self.busy_polls}
        if cmd == "result":
            r = copy.deepcopy(fx("S2-mta")["stages"][0])
            r["id"] = self.stage["id"]
            r["warn"] = ["sample warning"] if self.stage["id"].endswith("144") else []
            return r
        return {"ok": True}


def run(client, console=lambda line: True, **kw):
    kw.setdefault("scene", "S1")
    return B.run_scene(client, console, label="t", run="r", poll_s=0, sleep=lambda s: None, **kw)


def test_run_scene_sequence_and_document():
    c = FakeClient()
    lines, fps = [], []
    doc = run(c, lambda line: lines.append(line) or True, preset="classic", profile="mta", sets=[("sae_va_guard", "0")],
              duration=4, warmup=1, server_fps=lambda n: fps.append(n) or True)
    cmds = [x[0] for x in c.log]
    assert cmds[0] == "hello" and cmds[1] == "hello" and cmds[2] == "begin" and cmds[-1] == "finish"
    assert cmds.count("stage") == 4 and cmds.count("result") == 4
    assert lines == ["sae_preset classic", "sae_set sae_limits mta", "sae_set sae_va_guard 0"]
    assert fps == [60, 144, 165, 240, None]                       # per stage on the server, put back after the scene
    sent = [a for cmd, a in c.log if cmd == "stage"]
    assert sent[0]["warmup"] == 1 and sent[0]["sample"] == 4 and "console" not in sent[0]["stage"]
    assert "server_fps" not in sent[0]["stage"] and sent[1]["stage"]["fps_limit"] == 144
    assert doc["stages"][1]["console"] == [{"line": "setFPSLimit(144) on the server", "accepted": True}]
    assert doc["schema"] == B.SCHEMA and doc["scene"] == "S1" and [s["id"] for s in doc["stages"]] == [
        "fps60", "fps144", "fps165", "fps240"]
    assert doc["request"]["preset"] == "classic" and doc["request"]["set"] == ["sae_va_guard=0"]
    assert doc["summary"]["stages"] == 4 and any(w == "fps144: sample warning" for w in doc["warn"])
    assert any("UNVERIFIED: the client does not report its preset" in w for w in doc["warn"])


def test_run_scene_verifies_the_applied_preset_and_profile():
    h = {"version": 1, "features": {}, "sae": True, "preset": "balanced", "profile": "sae"}
    doc = run(FakeClient(hello=h), preset="classic", profile="mta", scene="S5", duration=2)
    assert any(w.startswith("NOT_APPLIED: preset is 'balanced'") for w in doc["warn"])
    assert any(w.startswith("NOT_APPLIED: profile is 'sae'") and "restart" in w for w in doc["warn"])


def test_run_scene_refuses_a_console_line_the_game_does_not_take():
    c = FakeClient()
    with pytest.raises(SatkError) as e:
        run(c, lambda line: False, preset="high", scene="S5")
    assert e.value.code == "EXTERNAL_TOOL" and "sae_preset high" in e.value.msg and not any(x[0] == "begin" for x in c.log)
    with pytest.raises(SatkError) as e:
        run(FakeClient(), preset="high; quit", scene="S5")
    assert e.value.code == "BAD_PARAMS"
    doc = run(FakeClient(), lambda line: False, scene="S1", duration=2, server_fps=lambda n: False)   # stages only warn
    assert sum(w.startswith("SERVER_FPS:") for w in doc["warn"]) == 4 and doc["summary"]["stages"] == 4
    doc = run(FakeClient(), scene="S1", duration=2)                           # no server hook at all
    assert sum(w.startswith("SERVER_FPS:") for w in doc["warn"]) == 4


def test_run_scene_with_launch_cvars_sends_no_console_lines():
    lines = []
    h = {"version": 1, "features": {}, "sae": True, "preset": "classic", "profile": "sae"}
    c = FakeClient(hello=h)
    doc = run(c, lambda line: lines.append(line) or True, preset="classic", profile="mta", scene="S5", duration=2,
              launch={"sae_preset": "classic", "sae_limits": "mta"})
    assert lines == [] and [x[0] for x in c.log].count("hello") == 1
    assert doc["request"]["launch"] == {"sae_preset": "classic", "sae_limits": "mta"}
    assert doc["request"]["console"] == [{"line": "sae_preset=classic", "accepted": True, "via": "launch"},
                                         {"line": "sae_limits=mta", "accepted": True, "via": "launch"}]
    assert any(w.startswith("NOT_APPLIED: profile is 'sae'") and "restart" not in w for w in doc["warn"])


def test_run_scene_failures_keep_the_partial_result_and_finish():
    c = FakeClient(fail_at="fps165")
    with pytest.raises(B.IncompleteRun) as e:
        run(c, scene="S1", duration=2)
    assert e.value.error.code == "EXTERNAL_TOOL" and "boom" in e.value.error.msg
    d = e.value.doc
    assert d["incomplete"] is True and [s["id"] for s in d["stages"]] == ["fps60", "fps144"] and d["error"]["code"] == "EXTERNAL_TOOL"
    assert c.log[-1][0] == "finish"
    t = [0.0]

    def clock():
        t[0] += 50.0
        return t[0]

    with pytest.raises(B.IncompleteRun) as e:
        B.run_scene(FakeClient(hang=True), lambda line: True, scene="S5", label="t", run="r", duration=2, warmup=0, poll_s=0,
                    sleep=lambda s: None, clock=clock)
    assert e.value.error.code == "TIMEOUT" and e.value.doc["stages"] == []


def test_parse_set():
    assert B.parse_set(["a=1", "sae_x = 2"]) == [("a", "1"), ("sae_x", "2")]
    for bad in ("a", "=1", "a=", "a b=1"):
        with pytest.raises(SatkError):
            B.parse_set([bad])


# --------------------------------------------------------------------------- operations


def test_ops_are_registered_for_agents_through_satk_ops():
    from satk.mcp.generic import denial

    for n in ("ingame.bench", "ingame.bench_compare", "ingame.bench_scenes"):
        spec = get_op(n)
        assert spec.mcp is False and len(spec.summary) <= 300 and spec.summary_ru and denial(spec) is None
    assert get_op("ingame.bench_compare").params[0].name == "a"                  # A and B are positional


class OpBridge:
    def __init__(self):
        self.lines = []
        self.execs = []
        self.b = self

    def console(self, line):
        self.lines.append(line)
        return True

    def client_joined(self):
        return True

    def exec(self, code, side="server", timeout_ms=8000):
        self.execs.append(code)
        return [100] if "getFPSLimit" in code else [True]

    def rcall(self, side, resource, fn, cmd, args=None):
        return self.client.call(cmd, args)


@pytest.fixture
def bench_ops(bench_home, monkeypatch):
    br = OpBridge()
    br.client = FakeClient()
    monkeypatch.setattr(O, "_bridge", lambda need_client=False: br)
    monkeypatch.setattr(S, "ensure_bench", lambda bridge, timeout=60.0: {"installed": ["client.lua"], "server": "running",
                                                                        "hello": {}})
    from satk.ingame import cvars as CV

    calls = []
    monkeypatch.setattr(CV, "relaunch", lambda run, *, joined, timeout=180.0: calls.append((dict(run), joined)) or {
        "restarted": False, "cvars": dict(run)})
    br.relaunch = calls
    return br


def test_bench_op_writes_the_result_and_a_table(bench_ops):
    env = O.ingame_bench(scene="s5", label="sae 1", run="r1", preset="classic", cvar=["sae_va_guard=1"], duration=3, warmup=1)
    assert env["cols"][:3] == ["stage", "frames", "fps_avg"] and env["rows"][0][0] == "cars64"
    f = Path(env["file"])
    assert f.name == "S5-sae_1.json" and f.parent.name == "r1" and env["installed"] == ["client.lua"]
    doc = json.loads(f.read_text(encoding="utf-8"))
    assert doc["schema"] == B.SCHEMA and doc["request"]["set"] == [] and doc["label"] == "sae 1"
    assert bench_ops.lines == [] and bench_ops.relaunch == [({"sae_preset": "classic", "sae_va_guard": "1"}, True)]
    assert doc["request"]["launch"] == {"sae_preset": "classic", "sae_va_guard": "1"}
    assert env["summary"]["frames"] == doc["summary"]["frames"]
    # the file feeds bench-compare directly
    again = invoke("ingame.bench_compare", {"a": str(f), "b": str(f)})
    assert again["verdict"] == "pass"


def test_bench_op_s1_sets_the_server_fps_limit_and_puts_it_back(bench_ops):
    env = O.ingame_bench(scene="S1", label="fps", run="r3", duration=2, warmup=0)
    sets = [c for c in bench_ops.execs if "setFPSLimit(" in c]
    assert [c.rsplit("setFPSLimit(", 1)[1].split(")")[0] for c in sets] == ["60", "144", "165", "240", "100"]
    assert bench_ops.relaunch == [] and not any(w.startswith("SERVER_FPS") for w in env.get("warn", []))
    # a server without setFPSLimit: every step warns, the scene still runs
    bench_ops.exec = lambda code, side="server", timeout_ms=8000: [False]
    env = O.ingame_bench(scene="S1", label="fps2", run="r3", duration=2, warmup=0)
    assert sum(w.startswith("SERVER_FPS:") for w in env["warn"]) == 4


def test_bench_op_saves_a_partial_result_on_failure(bench_ops):
    bench_ops.client = FakeClient(fail_at="fps144")
    with pytest.raises(SatkError) as e:
        O.ingame_bench(scene="S1", label="x", run="r2", duration=2)
    assert e.value.code == "EXTERNAL_TOOL" and "partial result saved" in e.value.msg
    doc = json.loads(Path(e.value.data["file"]).read_text(encoding="utf-8"))
    assert doc["incomplete"] is True and len(doc["stages"]) == 1


def test_bench_op_validates_before_it_touches_the_game(bench_home, monkeypatch):
    monkeypatch.setattr(O, "_bridge", lambda need_client=False: pytest.fail("the game must not be contacted"))
    for kw, code in (({}, "BAD_PARAMS"), ({"scene": "S99"}, "BAD_PARAMS"), ({"scene": "S1", "duration": 0}, "BAD_PARAMS"),
                     ({"scene": "S1", "cvar": ["nonsense"]}, "BAD_PARAMS")):
        with pytest.raises(SatkError) as e:
            O.ingame_bench(**kw)
        assert e.value.code == code


def test_bench_op_refuses_clearly_without_the_game(bench_home, monkeypatch):
    from satk.viewer.backends import mta_lua

    monkeypatch.setattr(mta_lua, "status", lambda probe=True: {"up": False})
    with pytest.raises(SatkError) as e:
        O.ingame_bench(scene="S1", label="x")
    assert e.value.code == "NOT_READY" and "real game" in e.value.msg and "satk ingame start" in e.value.hint


# --------------------------------------------------------------------------- the resource on the server


def test_install_bench_adds_the_generated_dff_only_to_the_installed_copy(bench_home, tmp_path):
    from satk.rw.dff import DffDoc

    src_meta = (R.bench_source() / "meta.xml").read_text(encoding="utf-8")
    assert "probe.dff" not in src_meta and not list(R.bench_source().rglob("*.dff"))      # no model in the repository
    res = tmp_path / "resources"
    first = R.install_bench(res)
    assert sorted(first["changed"]) == ["client.lua", "gen/probe.dff", "meta.xml", "stats.lua"]
    meta = (res / "satk-bench" / "meta.xml").read_text(encoding="utf-8")
    assert '<file src="gen/probe.dff"/>' in meta and meta.rstrip().endswith("</meta>")
    dff = (res / "satk-bench" / "gen" / "probe.dff").read_bytes()
    assert 1.9 * 2**20 < len(dff) < 2.2 * 2**20 and len(DffDoc.parse(dff).geometries()) == 1
    assert R.install_bench(res)["changed"] == []                                        # idempotent


def test_synthetic_dff_is_deterministic_and_sized():
    from satk.ingame.benchdff import synthetic_dff

    a = synthetic_dff(300_000)
    assert a == synthetic_dff(300_000) and 250_000 < len(a) < 350_000
    with pytest.raises(SatkError):
        synthetic_dff(10)


class EnsureBridge:
    def __init__(self, states, hello_after=1):
        self.states, self.calls, self.hello_after, self.n = list(states), [], hello_after, 0

    def exec(self, code, side="server", timeout_ms=8000):
        self.calls.append(code)
        if "getResourceState(r) or 'missing'" in code:
            return [self.states.pop(0) if len(self.states) > 1 else self.states[0]]
        return [True]

    def rcall(self, side, resource, fn, cmd, args=None):
        self.n += 1
        if self.n <= self.hello_after:
            raise SatkError("NOT_READY", "not loaded yet")
        return {"version": 1}


def test_ensure_bench_starts_the_resource_and_waits_for_the_client(bench_home, monkeypatch):
    monkeypatch.setattr(S.time, "sleep", lambda s: None)
    b = EnsureBridge(["missing", "missing", "loaded", "running"])
    out = S.ensure_bench(b, timeout=5)
    assert out["hello"] == {"version": 1} and "gen/probe.dff" in out["installed"]
    assert any("refreshResources" in c for c in b.calls) and any("startResource" in c for c in b.calls)
    b2 = EnsureBridge(["running"])
    assert S.ensure_bench(b2, timeout=5)["installed"] == []
    assert not any("startResource" in c or "restartResource" in c for c in b2.calls)
    b3 = EnsureBridge(["running"], hello_after=10**6)
    monkeypatch.setattr(S.time, "monotonic", iter(range(0, 10**6)).__next__)
    with pytest.raises(SatkError) as e:
        S.ensure_bench(b3, timeout=3)
    assert e.value.code == "NOT_READY"


def test_the_resource_lints_clean_without_the_knowledge_base():
    env = invoke("mta.lint", {"path": str(R.bench_source()), "ref": "none", "severity": "warn"})
    assert env["clean"] is True and env["rows"] == [], env["rows"]
