"""sae-bench/1: validation rules and the converter from the recorded satk-bench/1 results of the in-game bench (no game)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from satk.core.errors import SatkError
from satk.ingame import bench as LB
from satk.obs import bench as B
from satk.obs import sample, schemas
from satk.obs import stream as S

FIX = Path(__file__).resolve().parent / "fixtures" / "satk-bench"
NAMES = ["S2-mta", "S2-sae-slow-p99", "S2-sae-va-over"]


def fx(name):
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


def multi():
    d = fx("S2-mta")
    s2 = copy.deepcopy(d["stages"][0])
    s2["id"], s2["index"] = "second", 2
    s2["frames"] = LB.frame_stats([16.0, 16.5, 17.0, 33.0] * 40, 3000)
    s2["frames"]["sum_ms"] = round(sum([16.0, 16.5, 17.0, 33.0] * 40), 1)
    s2["va"] = {"start_mib": 1500, "end_mib": 1800, "peak_mib": 1850, "growth_mib": 300}
    s2["probe"] = {"kind": "weapon", "shots_per_s": 11.5}
    d["stages"].append(s2)
    d["summary"] = LB.summarize(d["stages"])
    d["planned_stages"] = 2
    return d


@pytest.mark.parametrize("name", NAMES)
def test_converted_fixtures_validate_and_keep_the_numbers(name):
    old = fx(name)
    new = B.convert_satk_bench(old)
    assert new["schema"] == "sae-bench/1" and new["source"] == {"tool": "satk-ingame-bench", "converted_from": "satk-bench/1"}
    rep = B.validate(new, strict=True)
    assert rep.ok and not rep.problems, rep.problems
    assert schemas.check_bench(new, strict=True) == []
    assert new["summary"] == old["summary"]                       # the same pooling arithmetic
    for mine, theirs in zip((B.stage_metrics(s) for s in new["stages"]), (LB.stage_metrics(s) for s in old["stages"])):
        assert mine == {k: theirs[k] for k in mine}                # the numbers bench-compare reads are the same
    st, ost = new["stages"][0], old["stages"][0]
    assert st["frames"] == ost["frames"] and st["va"] == ost["va"] and st["stream"] == ost["stream"]
    assert st["series"]["cols"] == ["t_s", "frames", "avg_ms", "max_ms", "va_mib", "stream_mib"] and st["series"]["rows"] == ost["series"]
    assert st["clock"]["tick_count_ms"] == ost["clock"]["tick_ms"] and "tick_ms" not in st["clock"]
    assert st["raw"] == {"start": ost["start"], "finish": ost["finish"]}      # the client tables stay verbatim
    assert st["snap_start"]["mem"]["va_used_mib"] == ost["start"]["va_mib"] and st["snap_end"]["game"]["timer_mode"] == "real"
    assert st["snap_end"]["stream"]["used_mib"] == ost["finish"]["stream_mib"]
    assert st["gauge"]["capacity.visibleEntities"] == {"last": ost["limits_last"]["capacity"]["visibleEntities"],
                                                       "max": ost["limits_max"]["capacity.visibleEntities"]}
    assert st["ext"]["features"] == ost["features"] and new["env"]["features"] == old["client"]["features"]
    assert new["env"]["role"] == "client" and new["env"]["sae"] is True and new["env"]["preset"] == "classic"
    assert new["request"] == old["request"] and new["run"] == old["run"] and new["label"] == old["label"]


def test_converter_covers_every_member_of_the_legacy_stage():
    old = fx("S2-mta")
    new = B.convert_satk_bench(old)
    stage_keys = set(old["stages"][0])
    mapped = {"id", "index", "title", "warmup_s", "sample_s", "frames", "va", "stream", "warn", "counts", "probe", "camera", "fps_limit",
              "console", "clock", "series", "limits_max", "limits_last", "start", "finish"}
    assert set(new["stages"][0]["ext"]) == stage_keys - mapped          # everything else is kept in ext, nothing dropped
    old2 = copy.deepcopy(old)
    old2["stages"][0]["something_new"] = {"a": 1}
    old2["stages"][0]["limits_max"]["weird key!"] = 5
    new2 = B.convert_satk_bench(old2)
    assert new2["stages"][0]["ext"]["something_new"] == {"a": 1}
    assert new2["stages"][0]["ext"]["limits"] == {"weird key!": {"max": 5, "last": None}}
    assert B.validate(new2).ok


def test_the_legacy_comparison_gives_the_same_verdicts_on_converted_files():
    for a, b in (("S2-mta", "S2-sae-slow-p99"), ("S2-mta", "S2-sae-va-over"), ("S2-mta", "S2-mta")):
        ra = LB.compare(fx(a), fx(b))
        rb = LB.compare(B.convert_satk_bench(fx(a)), B.convert_satk_bench(fx(b)))
        assert ra["rows"] == rb["rows"] and ra["verdict"] == rb["verdict"]
    assert LB.compare(fx("S2-mta"), fx("S2-sae-va-over"))["verdict"] == "FAIL"


def test_multi_stage_summary_matches_the_bench_arithmetic():
    old = multi()
    new = B.convert_satk_bench(old)
    assert new["summary"] == old["summary"] and new["summary"]["pooled"] is True and new["summary"]["shots_per_s"] == 11.5
    assert B.validate(new).ok
    rows = B.table_rows(new)
    assert [r[0] for r in rows] == ["flyover", "second", "(all)"] and len(rows[0]) == len(B.TABLE_COLS)
    assert rows[1][1] == 160 and rows[2][1] == new["summary"]["frames"]


def test_incomplete_runs_and_errors_survive():
    old = multi()
    old["incomplete"] = True
    old["error"] = {"code": "TIMEOUT", "msg": "stage did not finish"}
    old["warn"] = ["S2: something"]
    old["stages"].pop()
    old["summary"] = LB.summarize(old["stages"])
    new = B.convert_satk_bench(old)
    assert new["incomplete"] is True and new["error"]["code"] == "TIMEOUT" and new["warn"] == ["S2: something"]
    assert B.validate(new).ok                                     # planned 2, present 1, incomplete set: consistent
    new["incomplete"] = False
    rep = B.validate(new)
    assert rep.by_code == {"W:INCOMPLETE": 1} or "W:INCOMPLETE" in rep.by_code


def test_conversion_input_checks():
    with pytest.raises(SatkError) as e:
        B.convert_satk_bench({"schema": "other/1"})
    assert e.value.code == "BAD_PARAMS"
    with pytest.raises(SatkError):
        B.convert_satk_bench(B.convert_satk_bench(fx("S2-mta")))


# --------------------------------------------------------------------------- the validator


def doc():
    return B.convert_satk_bench(fx("S2-mta"))


def codes(d, **kw):
    return B.validate(d, **kw).by_code


def test_validation_rules():
    assert codes(doc()) == {}
    d = doc()
    d["stages"].append(copy.deepcopy(d["stages"][0]))
    d["summary"] = B.summarize(d["stages"])
    d["planned_stages"] = 2
    assert codes(d) == {"E:STAGE_ID": 1}
    d = doc()
    d["stages"][0]["frames"]["p50_ms"] = 999.0
    assert "E:STATS" in codes(d)
    d = doc()
    d["stages"][0]["series"]["rows"][3] = [4, 1]
    assert "E:SERIES" in codes(d)
    d = doc()
    d["stages"][0]["series"]["cols"][0] = "time"
    assert "E:SERIES" in codes(d)
    d = doc()
    d["stages"][0]["series"]["rows"][5][0] = 0
    assert "E:SERIES" in codes(d)
    d = doc()
    d["summary"]["frames"] += 1
    d["summary"]["va_peak_mib"] += 10
    d["summary"]["p99_ms"] += 1
    c = codes(d)
    assert c["E:SUMMARY"] == 2 and c["W:SUMMARY"] == 1
    d = doc()
    d["stages"][0]["va"]["peak_mib"] = 10
    d["summary"] = B.summarize(d["stages"])
    assert codes(d) == {"W:VA": 1}
    d = doc()
    d["stages"][0]["frames"] = {"n": 5}
    d["summary"] = B.summarize(d["stages"])
    assert codes(d) == {"W:FEW_FRAMES": 1}
    d = doc()
    d["planned_stages"] = 3
    assert codes(d) == {"W:INCOMPLETE": 1}
    d = doc()
    d["schema"] = "satk-bench/1"
    rep = B.validate(d)
    assert rep.by_code == {"E:SCHEMA": 1} and "satk obs convert" in rep.problems[0].msg
    assert B.validate([1]).by_code == {"E:SCHEMA": 1}
    d = doc()
    del d["source"]
    d["stages"][0]["frames"]["q"] = [1, 2]
    rep = B.validate(d)
    assert rep.by_code["E:SCHEMA"] >= 2
    d = doc()
    d["surprise"] = 1
    d["stages"][0]["surprise"] = 1
    assert codes(d) == {} and codes(d, strict=True) == {"E:SCHEMA": 2}


def test_stage_ids_must_be_strings_and_cells_numbers():
    d = doc()
    d["stages"][0]["id"] = 5
    assert "E:SCHEMA" in codes(d)
    d = doc()
    d["stages"][0]["series"]["rows"][0][2] = "x"
    assert "E:SCHEMA" in codes(d)
    d = doc()
    d["stages"][0]["series"]["rows"][0][2] = None            # a missing cell is allowed
    assert codes(d) == {}


# --------------------------------------------------------------------------- stream -> bench


def test_bench_from_a_stream_validates_and_has_the_tick_data():
    recs = sample.client_stream(seconds=3.0)
    d = B.doc_from_records(recs, run="r1", scene="S0", label="fixed", skip_s=0.5)
    assert B.validate(d, strict=True).ok
    assert d["source"] == {"tool": "obs-convert", "converted_from": "sae-obs/1"}
    assert d["env"]["sim"] == "fixed" and d["env"]["pulse"] == "standard" and d["env"]["role"] == "client"
    assert d["summary"]["ticks_per_frame"] > 0.4 and d["summary"]["dropped_ms"] >= 0
    assert d["key"]["first"]["t_us"] <= d["key"]["last"]["t_us"]
    assert d["stages"][0]["warmup_s"] == 0.5 and d["planned_stages"] == 1 and d["label"] == "fixed"
    two = B.doc_from_records(list(S.merge_streams([("a", recs), ("b", sample.client_stream(node="client-9", seed=9))])), run="m")
    assert [s["id"] for s in two["stages"]] == ["client-1", "client-9"] and two["summary"]["pooled"] is True and B.validate(two).ok
    with pytest.raises(SatkError):
        B.doc_from_records(sample.server_stream(), run="x")


def test_load_json_and_detect(tmp_path):
    p = tmp_path / "b.json"
    p.write_bytes("﻿".encode("utf-8") + json.dumps(doc()).encode("utf-8"))
    assert B.detect_schema(B.load_json(p)) == "sae-bench/1"
    p.write_text("{oops", encoding="utf-8")
    with pytest.raises(SatkError) as e:
        B.load_json(p)
    assert e.value.code == "BAD_PARAMS"
    assert B.detect_schema([]) is None and B.detect_schema({"schema": 5}) is None
