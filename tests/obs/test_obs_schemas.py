"""The schema files of data/obs, the key helpers, the statistics and the example data (no game files)."""

from __future__ import annotations

import json
import re
import struct

import pytest

from satk.core.errors import SatkError
from satk.obs import container as C
from satk.obs import key as K
from satk.obs import sample, schemas
from satk.obs import stats as ST


def refs(node, out):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "$ref":
                out.add(v)
            else:
                refs(v, out)
    elif isinstance(node, list):
        for v in node:
            refs(v, out)
    return out


def test_every_ref_resolves_and_every_definition_is_used():
    reg = schemas.registry()
    assert set(reg) == {"sae-obs-common.schema.json", "sae-obs-record.schema.json", "sae-bench.schema.json"}
    used: set[tuple[str, str]] = set()
    for sid, doc in reg.items():
        for ref in refs(doc, set()):
            target, _, frag = ref.partition("#")
            target = target or sid
            node = reg[target]
            parts = [p for p in frag.split("/") if p]
            for p in parts:
                node = node[p]
            if len(parts) == 2 and parts[0] == "$defs":
                used.add((target, parts[1]))
    for sid, doc in reg.items():
        for name in doc.get("$defs", {}):
            if sid == "sae-obs-record.schema.json" and name in schemas.RECORD_KINDS:
                continue   # reached through the if/then dispatch below
            assert (sid, name) in used or sid == "sae-obs-common.schema.json" and name in {"open_object"}, f"unused definition {sid}#{name}"


def test_the_record_dispatch_matches_the_kinds():
    doc = schemas.load("record")
    assert tuple(doc["properties"]["rec"]["enum"]) == schemas.RECORD_KINDS
    dispatched = [b["if"]["properties"]["rec"]["const"] for b in doc["allOf"]]
    assert tuple(dispatched) == schemas.RECORD_KINDS
    assert set(doc["$defs"]) == set(schemas.RECORD_KINDS)
    for kind, d in doc["$defs"].items():
        assert d["properties"]["rec"] == {"const": kind}
        assert "rec" in d["required"]


def test_unknown_schema_and_record_kinds():
    with pytest.raises(SatkError) as e:
        schemas.load("recrod")
    assert e.value.code == "NOT_FOUND" and "record" in e.value.did_you_mean
    assert schemas.check_record(5)[0].keyword == "type"
    assert "not a record kind" in schemas.check_record({"rec": "zzz"})[0].msg
    assert schemas.check_record({}) [0].path == "rec"
    names = [r["name"] for r in schemas.describe()]
    assert names == ["common", "record", "bench", "layout"]


def test_the_container_layout_file_matches_the_reference_implementation():
    lay = schemas.layout()
    assert lay["header"]["size"] == C.HEADER_SIZE == 64
    assert lay["chunk_header"]["size"] == C.CHUNK_HEADER_SIZE == 40
    assert lay["table"]["entry"]["size"] == C.ENTRY_SIZE == 48
    assert bytes.fromhex(lay["magic"].replace(" ", "")) == C.MAGIC
    assert lay["version"] == {"major": C.VERSION[0], "minor": C.VERSION[1]}
    assert set(lay["kinds"]) == set(C.KINDS)
    assert {k: v["extension"] for k, v in lay["kinds"].items()} == C.KINDS
    sizes = {"u8": 1, "u16": 2, "u32": 4, "u64": 8, "i64": 8, "fourcc": 4}

    def width(t):
        m = re.fullmatch(r"bytes\[(\d+)\]", t)
        return int(m.group(1)) if m else sizes[t]

    for sect, fmt in (("header", C._HEADER), ("chunk_header", C._CHUNK)):  # noqa: SLF001
        fields = lay[sect]["fields"]
        end = 0
        for f in fields:
            assert f["offset"] == end, (sect, f["name"])
            end += width(f["type"])
        assert end == lay[sect]["size"] == fmt.size
        # little-endian struct format with the same field widths
        codes = re.findall(r"\d*[A-Za-z]", fmt.format.lstrip("<"))
        assert [struct.calcsize("<" + c) for c in codes] == [width(f["type"]) for f in fields]
        assert fmt.format.startswith("<")
    assert lay["flags"]["FINALIZED"]["bit"] == 0 and C.FLAG_FINALIZED == 1
    assert lay["flags"]["COMPRESSED"]["bit"] == 1 and C.FLAG_COMPRESSED == 2
    assert lay["flags"]["SORTED"]["bit"] == 2 and C.FLAG_SORTED == 4
    assert lay["flags"]["MERGED"]["bit"] == 3 and C.FLAG_MERGED == 8
    assert {int(k): v["name"] for k, v in lay["codecs"].items()} == {0: "none", 1: "zlib", 2: "zstd"}
    assert lay["limits"]["max_raw_size"] == C.MAX_RAW_DEFAULT
    assert struct.calcsize("<8sHH4sIIQIIqqII") == 64


# --------------------------------------------------------------------------- key


def test_key_grid_arithmetic():
    dt = 33333
    assert K.tick_of(0, dt) == 0 and K.tick_of(33332, dt) == 0 and K.tick_of(33333, dt) == 1
    assert K.tick_of(10, 0) == K.NONE
    assert K.sub_of(16666, dt, 2) == 0 and K.sub_of(16667, dt, 2) == 1 and K.sub_of(33333, dt, 2) == 2
    assert K.sub_of(5, 0, 2) == K.NONE
    for n in (1, 2, 3, 4):
        for k in range(0, 500, 7):
            t = K.tick_time_us(k, dt, n)
            assert K.sub_of(t, dt, n) == k and K.sub_of(t - 1, dt, n) == k - 1 if k else K.sub_of(t, dt, n) == 0
            assert K.sub_of(t, dt, n) // n == K.tick_of(t, dt)
    key = K.make(100_000, dt_s_us=dt, sub_n=2, frame=7, fixed=True, tsrc="est")
    assert key == {"t_us": 100000, "tick": 3, "sub": 6, "frame": 7, "tsrc": "est"}
    assert K.make(100_000, dt_s_us=dt)["sub"] == K.NONE and K.make(1)["tick"] == K.NONE
    assert K.of({"rec": "x", **key, "other": 1}) == key
    assert K.sort_key({"t_us": 5}) == (5, K.NONE, K.NONE, K.NONE)
    assert K.fmt(key) == "t=0.100000s T=3 k=6 f=7" and K.fmt({"t_us": 1, "tick": K.NONE}).startswith("t=0.000001s T=-")


# --------------------------------------------------------------------------- statistics


def test_frame_stats_equal_the_bench_arithmetic():
    from satk.ingame import bench as B

    data = [8.0] * 90 + [16.7, 16.7, 33.4, 50.1, 8.3, 8.4, 120.0, 9.0, 9.1, 7.9, 8.25, 8.5]
    mine, theirs = ST.frame_stats(data, 1100), B.frame_stats(data, 1100)
    for k, v in theirs.items():
        assert mine[k] == v, k
    assert set(mine) - set(theirs) == {"sum_ms", "wall_ms"}
    assert ST.frame_stats([]) == {"n": 0}
    a, b = ST.frame_stats([5.0] * 100), ST.frame_stats([15.0] * 100)
    assert ST.pooled_percentile([a, b], 25) == B.pooled_percentile([{"frames": a}, {"frames": b}], 25) == 5
    assert ST.pooled_percentile([{}], 50) is None
    ms = ST.ms_stats([1, 2, 3, 4, 100])
    assert ms["n"] == 5 and ms["p50_ms"] == 3 and ms["max_ms"] == 100 and ms["sum_ms"] == 110 and ST.ms_stats([]) == {"n": 0}


def test_check_frame_stats_finds_inconsistencies():
    fs = ST.frame_stats([8.0, 9.0, 10.0] * 40)
    assert ST.check_frame_stats(fs) == []
    assert ST.check_frame_stats({"n": 0}) == []
    bad = dict(fs, p50_ms=99.0)
    assert any("p50_ms <= p95_ms" in m for m in ST.check_frame_stats(bad))
    bad = dict(fs, q=[*fs["q"][:50], fs["q"][50] + 1, *fs["q"][51:]])
    assert any("q[50]" in m or "ascending" in m for m in ST.check_frame_stats(bad))
    assert any("fps_avg" in m for m in ST.check_frame_stats(dict(fs, fps_avg=500.0)))
    assert any("avg_ms is above" in m for m in ST.check_frame_stats(dict(fs, avg_ms=50.0, p50_ms=1, p95_ms=2, p99_ms=3, max_ms=4, q=None)))


# --------------------------------------------------------------------------- the examples are deterministic and valid


def test_sample_streams_are_deterministic_and_obey_every_record_schema():
    a = sample.client_stream()
    b = sample.client_stream()
    assert a == b and json.dumps(a) == json.dumps(b)
    kinds = {r["rec"] for r in a + sample.server_stream() + sample.net_stream()}
    assert {"hdr", "frame", "tick", "stats", "event", "net", "end"} <= kinds
    for r in a + sample.server_stream() + sample.net_stream() + sample.client_stream(sim="classic"):
        assert schemas.check_record(r, strict=True) == [], r
    fixed = [r for r in a if r["rec"] == "tick"]
    assert fixed and all(r["sub"] != K.NONE for r in fixed)
    assert not [r for r in sample.client_stream(sim="classic") if r["rec"] == "tick"]
    assert sample.client_stream(seed=1) != sample.client_stream(seed=2)
    # the spiral guard fires in the sample (every 97th frame is five times longer than the others)
    assert any(r.get("dropped_ms", 0) > 0 for r in a if r["rec"] == "frame")
