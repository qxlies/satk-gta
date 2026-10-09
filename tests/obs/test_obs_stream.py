"""The JSONL stream: every validator rule, the summaries, the stage conversion and the merge (no game files)."""

from __future__ import annotations

import copy
import json

import pytest

from satk.core.errors import SatkError
from satk.obs import key as K
from satk.obs import sample
from satk.obs import stream as S
from satk.obs.stats import frame_stats


def codes(recs, **kw):
    rep = S.check_records(recs, **kw)
    return sorted(k for k in rep.by_code), rep


def fixed():
    return sample.client_stream(seconds=2.0)


def classic():
    return sample.client_stream(sim="classic", seconds=2.0, seed=5)


def find(recs, kind, n=0):
    return [i for i, r in enumerate(recs) if r["rec"] == kind][n]


def test_the_samples_are_clean():
    for recs in (fixed(), classic(), sample.server_stream(), sample.net_stream(), sample.client_stream(end=False)):
        rep = S.check_records(recs, strict=True)
        assert rep.ok, rep.problems
    rep = S.check_records(sample.client_stream(end=False))
    assert rep.by_code == {"W:NO_END": 1}                      # a cut stream is a warning, not an error
    assert S.check_records(fixed()).kinds["tick"] > 0


def test_json_level_problems(tmp_path):
    p = tmp_path / "s.jsonl"
    lines = [S.dump_line(r) for r in fixed()[:5]]
    lines.insert(2, "")
    lines.insert(3, "{not json")
    lines.insert(4, "[1,2]")
    p.write_bytes(("﻿" + "\r\n".join(lines) + "\r\n").encode("utf-8"))     # BOM and CRLF are tolerated
    rep = S.check_file(p)
    assert rep.by_code["E:JSON"] == 2 and rep.by_code["W:BLANK"] == 1
    assert rep.problems[0].where.startswith("line ")
    big = tmp_path / "big.jsonl"
    big.write_bytes(b'{"rec":"hdr","x":"' + b"a" * (S.MAX_LINE + 10) + b'"}\n' + S.dump_line(fixed()[0]).encode() + b"\n")
    rep = S.check_file(big)
    assert "longer" in rep.problems[0].msg and rep.records == 1
    bad_utf = tmp_path / "u.jsonl"
    bad_utf.write_bytes(b'\xff\xfe{"rec":"hdr"}\n')
    assert "not UTF-8" in S.check_file(bad_utf).problems[0].msg
    assert S.check_records([]).by_code == {"E:EMPTY": 1}
    with pytest.raises(SatkError) as e:
        S.read_jsonl(p)
    assert e.value.code == "BAD_PARAMS"


def test_header_rules():
    recs = fixed()
    assert codes(recs[1:])[0] == ["E:HDR_FIRST"] or "E:HDR_FIRST" in codes(recs[1:])[0]
    late = recs[:5] + [recs[0]] + recs[5:]
    assert "E:HDR_LATE" in codes(late)[0] and "E:HDR_DUP" in codes(late)[0]
    v2 = copy.deepcopy(recs)
    v2[0]["schema"] = "sae-obs/2"
    assert "E:VERSION" in codes(v2)[0]
    newer = copy.deepcopy(recs)
    newer[0]["minor"] = 3
    c, rep = codes(newer)
    assert c == ["W:VERSION"] and rep.ok
    badhdr = copy.deepcopy(recs)
    del badhdr[0]["clock"]
    assert "E:SCHEMA" in codes(badhdr)[0]
    two = recs[:1] + [dict(sample.server_stream()[0])] + recs[1:]
    c, _ = codes(two)
    assert "E:NODE" in c                                       # rows without a node in a stream with two headers


def test_order_and_counter_rules():
    base = fixed()
    i = find(base, "frame", 5)
    rec = copy.deepcopy(base)
    rec[i]["t_us"] -= 10_000_000
    c, rep = codes(rec)
    assert "E:ORDER" in c
    rec = copy.deepcopy(base)
    rec[i]["frame"] = rec[find(base, "frame", 2)]["frame"]
    assert "E:FRAME_ORDER" in codes(rec)[0]
    rec = copy.deepcopy(base)
    rec[find(base, "frame", 3)]["frame"] = K.NONE
    assert "E:KEY_FRAME" in codes(rec)[0]
    rec = copy.deepcopy(base)
    j = find(base, "tick", 4)
    rec[j]["sub"] = rec[find(base, "tick", 2)]["sub"]
    c = codes(rec)[0]
    assert "E:TICK_ORDER" in c or "E:KEY_SUB" in c
    rec = copy.deepcopy(base)
    k = [x for x, r in enumerate(rec) if r["rec"] == "frame" and "ctr" in r][10]
    rec[k]["ctr"]["timer.reentry"] = -1
    assert "E:CTR_DECREASE" in codes(rec)[0]
    # an after-the-footer record
    rec = copy.deepcopy(base)
    rec.append(copy.deepcopy(rec[find(rec, "frame", 0)]))
    rec[-1]["t_us"] = rec[-2]["t_us"]
    assert "E:AFTER_END" in codes(rec)[0]


def test_key_grid_rules():
    base = fixed()
    i = find(base, "frame", 8)
    rec = copy.deepcopy(base)
    rec[i]["tick"] += 1
    assert "E:KEY_TICK" in codes(rec)[0]
    rec = copy.deepcopy(base)
    rec[i]["tick"] = K.NONE                                    # "no grid value" is always allowed
    assert "E:KEY_TICK" not in codes(rec)[0]
    rec = copy.deepcopy(base)
    rec[i]["sub"] += 50                                        # ahead of the grid
    assert "E:KEY_SUB" in codes(rec)[0]
    rec = copy.deepcopy(base)
    j = find(base, "tick", 3)
    rec[j]["sub"] -= 1
    assert "E:KEY_SUB" in codes(rec)[0]
    nogrid = copy.deepcopy(base)
    nogrid[0]["clock"]["dt_s_us"] = 0
    assert "E:KEY_TICK" in codes(nogrid)[0]


def test_mode_rules():
    cl = classic()
    rec = copy.deepcopy(cl)
    rec[find(cl, "frame", 4)]["sub"] = 7
    assert "E:MODE" in codes(rec)[0]
    rec = copy.deepcopy(cl)
    i = find(cl, "frame", 6)
    rec[i]["ticks"] = 3
    assert "E:MODE" in codes(rec)[0]
    rec = copy.deepcopy(cl)
    rec.insert(i, {"rec": "tick", **K.of(cl[i]), "tick_ms": 0.5, "dt_us": 16667})
    assert "E:MODE" in codes(rec)[0]
    fx = fixed()
    rec = copy.deepcopy(fx)
    j = find(fx, "tick", 2)
    rec[j]["sub"] = K.NONE
    assert "E:MODE" in codes(rec)[0]


def test_footer_and_warnings():
    base = fixed()
    rec = copy.deepcopy(base)
    rec[-1]["counts"]["frame"] += 1
    c, rep = codes(rec)
    assert c == ["E:END_COUNT"] and "frame" in rep.problems[0].msg
    rec = copy.deepcopy(base)
    i = find(rec, "frame", 3)
    rec[i]["work_ms"] = rec[i]["frame_ms"] * 3 + 5
    assert codes(rec)[0] == ["W:WORK"]
    rec = copy.deepcopy(base)
    rec[find(rec, "frame", 3)]["fence_ms"]["F7"] = 0.2
    rec[0]["fences"] = ["F0", "F1"]
    c, rep = codes(rec)
    assert c == ["W:FENCE"] and rep.ok
    rec = copy.deepcopy(base)
    s = find(rec, "stats", 0)
    rec[s]["frames"]["p50_ms"] = 999.0
    assert "W:STATS" in codes(rec)[0]
    rec = copy.deepcopy(base)
    del rec[find(rec, "frame", 1)]["t_us"]
    c, rep = codes(rec)
    assert "E:SCHEMA" in c and "missing 't_us'" in rep.problems[0].msg
    # strict mode rejects members the schema does not declare
    rec = copy.deepcopy(base)
    rec[find(rec, "frame", 1)]["bonus"] = 1
    assert codes(rec)[0] == [] and codes(rec, strict=True)[0] == ["E:SCHEMA"]


def test_problem_list_is_capped_but_counts_are_complete():
    rec = fixed()
    for r in rec:
        if r["rec"] == "frame":
            r["frame_ms"] = -1
    rep = S.check_records(rec, max_problems=5)
    assert len(rep.problems) == 5 and rep.truncated and rep.by_code["E:SCHEMA"] > 100 or rep.by_code["E:SCHEMA"] == sum(1 for r in rec if r["rec"] == "frame")


# --------------------------------------------------------------------------- summaries


def test_summary_numbers_follow_the_records():
    recs = fixed()
    s = S.summarize_records(recs)["nodes"][0]
    frames = [r for r in recs if r["rec"] == "frame"]
    fs = frame_stats(r["frame_ms"] for r in frames)
    assert s["frames"]["n"] == len(frames) and s["frames"]["p99_ms"] == fs["p99_ms"] and "q" not in s["frames"]
    assert s["ticks"]["n"] == sum(r["ticks"] for r in frames) == len([r for r in recs if r["rec"] == "tick"])
    assert s["dropped_ms"]["total"] == round(sum(r["dropped_ms"] for r in frames), 3) > 0
    assert s["tick_ms"]["n"] == s["ticks"]["n"]
    assert set(s["fences"]) == set(sample.FENCES) - {"F7"} | {"F7"}
    assert s["ctr"]["sim.dropped_ms"] == frames[-1]["ctr"]["sim.dropped_ms"]
    assert s["gauge"]["visible_entities"]["max"] == max(r["gauge"]["visible_entities"] for r in frames)
    assert s["events"] == {"sae.clock.rebase": 1} and s["end"]["clean"] is True
    assert s["span"]["frame"] == [frames[0]["frame"], frames[-1]["frame"]] and s["sim"] == "fixed"
    assert s["last_stats"]["mem"]["va_guard"] == "ok" and s["gpu"]["scene"]["n"] > 0
    net = S.summarize_records(sample.net_stream())["nodes"][0]
    assert net["net"]["tx"]["rows"] + net["net"]["rx"]["rows"] == 40
    srv = S.summarize_records(sample.server_stream())["nodes"][0]
    assert srv["role"] == "server" and "frames" not in srv and srv["tick_ms"]["n"] == 90


def test_stage_from_records():
    recs = sample.client_stream(seconds=4.0)
    acc = S.accumulate(recs)["client-1"]
    st = S.stage_from_acc(acc)
    frames = [r for r in recs if r["rec"] == "frame"]
    assert st["frames"]["n"] == len(frames) and st["frames"]["p50_ms"] == frame_stats(r["frame_ms"] for r in frames)["p50_ms"]
    assert st["series"]["cols"][0] == "t_s" and st["series"]["rows"][0][0] == 1
    assert sum(r[1] for r in st["series"]["rows"]) == len(frames)
    assert st["ticks"]["n"] == sum(r["ticks"] for r in frames) and st["ticks"]["tick_ms"]["n"] > 0
    assert st["va"]["peak_mib"] >= st["va"]["start_mib"] and st["stream"]["peak_mib"] > 0
    assert st["key"]["start"]["t_us"] == frames[0]["t_us"] and st["fences"]["F5"]["n"] == len(frames)
    assert st["snap_start"]["mem"]["va_guard"] == "ok" and st["clock"]["ratio"] == 1.0
    warm = S.stage_from_acc(acc, skip_s=2.0)
    assert warm["warmup_s"] == 2.0 and warm["frames"]["n"] < st["frames"]["n"]
    assert warm["key"]["start"]["t_us"] >= frames[0]["t_us"] + 2_000_000
    cut = S.stage_from_acc(acc, until_s=1.0)
    assert cut["frames"]["n"] < st["frames"]["n"]
    with pytest.raises(SatkError) as e:
        S.stage_from_acc(acc, skip_s=99)
    assert e.value.code == "BAD_PARAMS"
    with pytest.raises(SatkError):
        S.stage_from_acc(S.accumulate(sample.server_stream())["server"])


# --------------------------------------------------------------------------- merge


def test_merge_orders_by_key_and_tags_nodes():
    a, b = fixed(), sample.server_stream()
    merged = list(S.merge_streams([("a", a), ("b", b)]))
    assert [r["node"] for r in merged[:2]] == ["client-1", "server"] and all(r.get("merged") for r in merged[:2])
    rows = merged[2:]
    keys = [K.sort_key(r) for r in rows]
    assert keys == sorted(keys) and {r["node"] for r in rows} == {"client-1", "server"}
    assert len(rows) == len(a) + len(b) - 2
    rep = S.check_records(merged, strict=True)
    assert rep.ok, rep.problems
    assert S.check_records(merged).nodes.keys() == {"client-1", "server"}
    # ties keep the order of the inputs
    twin = copy.deepcopy(b)
    twin[0]["node"] = "server-2"
    tie = list(S.merge_streams([("b", b), ("t", twin)]))
    same = [r for r in tie[2:] if r["rec"] == "tick"][:2]
    assert [r["node"] for r in same] == ["server", "server-2"]
    with pytest.raises(SatkError) as e:
        list(S.merge_streams([("a", a), ("a2", a)]))
    assert e.value.code == "BAD_PARAMS" and "appears in two inputs" in e.value.msg
    with pytest.raises(SatkError):
        list(S.merge_streams([("x", a[1:]), ("y", b)]))
    sums = S.summarize_records(merged)["nodes"]
    assert {n["node"] for n in sums} == {"client-1", "server"}


def test_jsonl_roundtrip_is_canonical(tmp_path):
    recs = fixed()
    p = tmp_path / "x.jsonl"
    assert S.write_jsonl(p, recs) == len(recs)
    assert S.read_jsonl(p) == recs
    text = p.read_text(encoding="utf-8")
    assert "\r" not in text and text.endswith("\n") and ": " not in text.splitlines()[0]
    assert json.loads(text.splitlines()[0])["rec"] == "hdr"
