"""satk obs operations on generated files in an isolated workspace (no game needed)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from satk.core import paths
from satk.core.errors import SatkError
from satk.core.registry import get_op
from satk.obs import container as C
from satk.obs import sample
from satk.obs import stream as S

FIX = Path(__file__).resolve().parent / "fixtures"

def invoke(name, args):
    """Run an operation; unlike registry.invoke this raises SatkError, which the tests inspect."""
    return get_op(name).call(args)


OPS = ["obs.validate", "obs.read", "obs.summarize", "obs.convert", "obs.merge", "obs.pack", "obs.unpack", "obs.schema", "obs.sample"]


@pytest.fixture
def home(satk_home):
    """The isolated workspace with the example files written by obs.sample."""
    invoke("obs.sample", {})
    return paths.work("out", "obs", "sample")


def test_operations_are_registered_and_documented():
    for name in OPS:
        o = get_op(name)
        assert not o.mcp and o.summary_ru.strip() and 0 < len(o.summary) <= 300 and o.examples
        assert o.cli_path[0] == "obs"
        for ex in o.examples:
            assert ex.startswith("satk obs ")


def test_sample_writes_deterministic_files_and_all_validate(home):
    names = sorted(p.name for p in home.iterdir())
    assert names == ["client-classic.jsonl", "client-fixed.bench.json", "client-fixed.jsonl", "merged.jsonl", "server.jsonl",
                     "session.saerec", "trace.saenet"]
    before = {p.name: p.read_bytes() for p in home.iterdir() if p.suffix != ".saerec" and p.suffix != ".saenet"}
    for n in names:
        r = invoke("obs.validate", {"path": f"sample/{n}", "strict": True})
        assert r["verdict"] == "pass" and r["n"] == 0, (n, r)
    invoke("obs.sample", {})
    assert {p.name: p.read_bytes() for p in home.iterdir() if p.name in before} == before
    other = invoke("obs.sample", {"dir": "second"})
    assert other["n"] == 7 and (paths.work("out", "obs") / "second" / "server.jsonl").is_file()


def test_validate_reports_problems_as_a_table(home, tmp_path):
    recs = S.read_jsonl(home / "client-fixed.jsonl")
    frames = [i for i, x in enumerate(recs) if x["rec"] == "frame"]
    recs[frames[10]]["frame_ms"] = -3
    recs[frames[20]]["t_us"] = 5
    bad = tmp_path / "bad.jsonl"
    S.write_jsonl(bad, recs)
    r = invoke("obs.validate", {"path": str(bad), "limit": 2})
    assert r["verdict"] == "FAIL" and r["errors"] >= 2 and r["n"] == 2 and r["total"] >= 2 and r["cols"] == ["where", "severity", "code", "message"]
    assert r["by_code"]["E:SCHEMA"] == 1 and r["by_code"]["E:ORDER"] >= 1 and r["kind"] == "jsonl" and r["schema"] == "sae-obs/1"
    assert r["total"] > 2 and r["next"] == "2"
    r2 = invoke("obs.validate", {"path": str(bad), "limit": 2, "cursor": r["next"]})
    assert r2["rows"] != r["rows"] and r2["total"] == r["total"]
    cut = home / "client-fixed.jsonl"
    short = tmp_path / "short.jsonl"
    short.write_text("".join(cut.read_text(encoding="utf-8").splitlines(True)[:-1]), encoding="utf-8")
    r = invoke("obs.validate", {"path": str(short)})
    assert r["verdict"] == "pass" and r["warnings"] == 1 and r["rows"][0][2] == "NO_END"
    with pytest.raises(SatkError) as e:
        invoke("obs.validate", {"path": "no-such-file"})
    assert e.value.code == "NOT_FOUND"
    junk = tmp_path / "junk.txt"
    junk.write_text("hello\n", encoding="utf-8")
    with pytest.raises(SatkError) as e:
        invoke("obs.validate", {"path": str(junk)})
    assert e.value.code == "BAD_PARAMS" and "cannot tell" in e.value.msg
    forced = invoke("obs.validate", {"path": str(junk), "kind": "jsonl"})
    assert forced["verdict"] == "FAIL" and forced["rows"][0][2] == "JSON"
    with pytest.raises(SatkError):
        invoke("obs.validate", {"path": str(junk), "kind": "weird"})


def test_validate_containers_and_bench(home, tmp_path):
    r = invoke("obs.validate", {"path": "sample/session.saerec"})
    assert r["verdict"] == "pass" and r["container"]["kind"] == "SREC" and r["container"]["table"] == "used" and r["container"]["chunks"] == 7
    damaged = tmp_path / "d.saerec"
    raw = bytearray((home / "session.saerec").read_bytes())
    raw[200] ^= 0xFF
    damaged.write_bytes(bytes(raw))
    r = invoke("obs.validate", {"path": str(damaged)})
    assert r["verdict"] == "FAIL" and any(row[2] == "CHUNK_CRC" for row in r["rows"])
    shallow = invoke("obs.validate", {"path": str(damaged), "deep": False})
    assert shallow["verdict"] == "pass"
    not_one = tmp_path / "n.saerec"
    not_one.write_bytes(b"nope")
    assert invoke("obs.validate", {"path": str(not_one)})["rows"][0][2] in ("TRUNC", "MAGIC")
    # bench: current schema, then a legacy file checked after in-memory conversion
    legacy = FIX / "satk-bench" / "S2-mta.json"
    r = invoke("obs.validate", {"path": str(legacy)})
    assert r["verdict"] == "pass" and r["schema"] == "satk-bench/1" and any("LEGACY" in w for w in r["warn"])
    doc = json.loads(legacy.read_text(encoding="utf-8"))
    doc["summary"]["frames"] += 1
    broken = tmp_path / "b.json"
    broken.write_text(json.dumps(doc), encoding="utf-8")
    assert invoke("obs.validate", {"path": str(broken)})["verdict"] == "FAIL"


def test_read_records_with_filters_and_fields(home):
    r = invoke("obs.read", {"path": "sample/client-fixed.jsonl", "rec": ["frame"], "limit": 3})
    assert r["cols"][:3] == ["seq", "rec", "t_us"] and r["n"] == 3 and r["total"] == 180 and r["next"] == "3"
    assert all(row[1] == "frame" for row in r["rows"]) and r["kind"] == "jsonl"
    nxt = invoke("obs.read", {"path": "sample/client-fixed.jsonl", "rec": ["frame"], "limit": 3, "cursor": r["next"]})
    assert nxt["rows"][0][0] > r["rows"][-1][0]
    f = invoke("obs.read", {"path": "sample/client-fixed.jsonl", "rec": ["frame"], "fields": ["frame", "frame_ms", "fence_ms.F5", "gauge.visible_entities"], "limit": 2})
    assert f["cols"] == ["seq", "frame", "frame_ms", "fence_ms.F5", "gauge.visible_entities"] and f["rows"][0][1] == 100
    assert isinstance(f["rows"][0][3], float)
    t0 = S.read_jsonl(home / "client-fixed.jsonl")[1]["t_us"]
    win = invoke("obs.read", {"path": "sample/client-fixed.jsonl", "t_from": t0 + 500_000, "t_to": t0 + 600_000, "limit": 100})
    assert 0 < win["total"] < 40 and all(t0 + 500_000 <= row[2] <= t0 + 600_000 for row in win["rows"])
    ticks = invoke("obs.read", {"path": "sample/client-fixed.jsonl", "rec": ["tick"], "tick_from": 18030, "tick_to": 18030, "limit": 100})
    assert ticks["total"] >= 1 and all(row[3] == 18030 for row in ticks["rows"])
    hdr = invoke("obs.read", {"path": "sample/client-fixed.jsonl", "rec": ["hdr", "end"]})
    assert [row[1] for row in hdr["rows"]] == ["hdr", "end"] and hdr["rows"][0][3] is None
    merged = invoke("obs.read", {"path": "sample/merged.jsonl", "node": "server", "rec": ["tick"], "limit": 1})
    assert merged["total"] == 90
    out = invoke("obs.read", {"path": "sample/client-fixed.jsonl", "rec": ["stats", "event"], "out": "sel.jsonl", "limit": 1})
    wrote = S.read_jsonl(Path(out["out"]))
    assert out["written"] == len(wrote) == 4 and {r["rec"] for r in wrote} == {"stats", "event"}
    with pytest.raises(SatkError):
        invoke("obs.read", {"path": "sample/client-fixed.jsonl", "cursor": "x"})


def test_read_containers_and_bench(home):
    c = invoke("obs.read", {"path": "sample/session.saerec", "chunks": True})
    assert c["cols"][:3] == ["chunk", "type", "codec"] and c["total"] == 7 and c["rows"][0][1] == "META" and c["container"]["kind"] == "SREC"
    assert c["rows"][1][2] == "zlib" and c["rows"][1][4] > c["rows"][1][3]
    recs = invoke("obs.read", {"path": "sample/session.saerec", "rec": ["frame"], "limit": 2})
    assert recs["total"] == 180 and recs["container"]["chunks"] == 7
    one = invoke("obs.read", {"path": "sample/session.saerec", "chunk": 0})
    assert one["total"] == 1 and one["rows"][0][1] == "hdr"
    net = invoke("obs.read", {"path": "sample/trace.saenet", "rec": ["net"], "fields": ["dir", "element_id", "packet_id", "subtype", "bytes"], "limit": 3})
    assert net["total"] == 40 and net["rows"][0][1] in ("tx", "rx")
    b = invoke("obs.read", {"path": "sample/client-fixed.bench.json"})
    assert b["cols"][:3] == ["stage", "n", "p50_ms"] and b["rows"][0][0] == "client-1" and b["schema"] == "sae-bench/1"
    bf = invoke("obs.read", {"path": "sample/client-fixed.bench.json", "fields": ["frames.p99_ms", "ticks.n", "fences.F5.p99_ms"]})
    assert bf["cols"] == ["stage", "frames.p99_ms", "ticks.n", "fences.F5.p99_ms"] and bf["rows"][0][2] > 0
    legacy = invoke("obs.read", {"path": str(FIX / "satk-bench" / "S2-mta.json")})
    assert legacy["schema"] == "satk-bench/1" and legacy["rows"][0][1] == 1446
    with pytest.raises(SatkError):
        invoke("obs.read", {"path": "sample/client-fixed.bench.json", "kind": "container"})


def test_summarize(home):
    s = invoke("obs.summarize", {"path": "sample/client-fixed.jsonl"})
    n = s["nodes"][0]
    assert s["kind"] == "jsonl" and n["node"] == "client-1" and n["frames"]["n"] == 180 and n["ticks"]["n"] == 182
    c = invoke("obs.summarize", {"path": "sample/session.saerec"})
    assert c["container"]["kind"] == "SREC" and c["chunk_types"]["OBSJ"]["records"] == 367 and c["compression_ratio"] > 1.5
    assert c["nodes"][0]["frames"]["n"] == 180
    m = invoke("obs.summarize", {"path": "sample/merged.jsonl"})
    assert {x["node"] for x in m["nodes"]} == {"client-1", "server"}
    only = invoke("obs.summarize", {"path": "sample/merged.jsonl", "node": "server"})
    assert [x["node"] for x in only["nodes"]] == ["server"] and "frames" not in only["nodes"][0]
    b = invoke("obs.summarize", {"path": "sample/client-fixed.bench.json"})
    assert b["cols"][0] == "stage" and b["summary"]["frames"] == 180 and b["env"]["sim"] == "fixed" and b["source"]["tool"] == "obs-convert"
    lg = invoke("obs.summarize", {"path": str(FIX / "satk-bench" / "S2-mta.json")})
    assert lg["summary"]["p99_ms"] == 20 and lg["source"]["converted_from"] == "satk-bench/1"


def test_convert(home):
    r = invoke("obs.convert", {"src": str(FIX / "satk-bench" / "S2-sae-va-over.json")})
    out = Path(r["path"])
    assert out.name == "S2-sae-va-over.sae-bench.json" and r["verdict"] == "pass" and r["converted_from"] == "satk-bench/1"
    assert json.loads(out.read_text(encoding="utf-8"))["schema"] == "sae-bench/1" and out.read_text(encoding="utf-8").endswith("\n")
    assert invoke("obs.validate", {"path": str(out), "strict": True})["verdict"] == "pass"
    with pytest.raises(SatkError) as e:
        invoke("obs.convert", {"src": str(out)})
    assert "already" in e.value.msg
    s = invoke("obs.convert", {"src": "sample/session.saerec", "scene": "S0", "label": "rec", "skip_s": 1.0, "out": "rec.bench.json"})
    doc = json.loads(Path(s["path"]).read_text(encoding="utf-8"))
    assert s["converted_from"] == "sae-obs/1" and doc["scene"] == "S0" and doc["label"] == "rec" and doc["stages"][0]["warmup_s"] == 1.0
    assert doc["run"] == "session" and s["verdict"] == "pass" and doc["stages"][0]["frames"]["n"] < 180
    with pytest.raises(SatkError):
        invoke("obs.convert", {"src": "sample/server.jsonl"})


def test_merge_pack_unpack(home, tmp_path):
    r = invoke("obs.merge", {"paths": ["sample/client-fixed.jsonl", "sample/server.jsonl"], "out": "mm.jsonl"})
    assert r["records"] == 368 + 92 and r["inputs"] == ["client-fixed.jsonl", "server.jsonl"]
    assert invoke("obs.validate", {"path": r["path"], "strict": True})["verdict"] == "pass"
    assert S.read_jsonl(Path(r["path"])) == S.read_jsonl(home / "merged.jsonl")
    con = invoke("obs.merge", {"paths": ["sample/session.saerec", "sample/server.jsonl"], "out": "mc.jsonl"})
    assert con["inputs"] == ["session.saerec", "server.jsonl"] and con["records"] == 368 + 92
    with pytest.raises(SatkError):
        invoke("obs.merge", {"paths": ["sample/server.jsonl"]})
    with pytest.raises(SatkError) as e:
        invoke("obs.merge", {"paths": ["sample/server.jsonl", "sample/server.jsonl"]})
    assert "two inputs" in e.value.msg
    with pytest.raises(SatkError):
        invoke("obs.merge", {"paths": ["sample/server.jsonl", "sample/client-fixed.bench.json"]})
    local = [r for r in sample.client_stream(seconds=0.4)]
    for x in local:
        if "tsrc" in x:
            x["tsrc"] = "local"
    lp = tmp_path / "local.jsonl"
    S.write_jsonl(lp, local)
    w = invoke("obs.merge", {"paths": [str(lp), "sample/server.jsonl"], "out": "ml.jsonl"})
    assert w["warn"] and "tsrc=local" in w["warn"][0]
    # pack and unpack
    p = invoke("obs.pack", {"src": "sample/client-fixed.jsonl", "out": "x.saerec", "chunk_records": 50})
    assert p["verdict"] == "pass" and p["kind"] == "SREC" and p["records"] == 367
    assert "FINALIZED" in p["flags"]
    u = invoke("obs.unpack", {"src": p["path"], "out": "x.jsonl"})
    assert S.read_jsonl(Path(u["path"])) == S.read_jsonl(home / "client-fixed.jsonl")
    plain = invoke("obs.pack", {"src": "sample/client-fixed.jsonl", "out": "plain.saenet", "compress": False})
    assert plain["kind"] == "SNET" and "COMPRESSED" not in plain["flags"]
    assert Path(plain["path"]).stat().st_size > Path(p["path"]).stat().st_size
    with pytest.raises(SatkError):
        invoke("obs.pack", {"src": "sample/session.saerec", "out": "y.saerec"})
    with pytest.raises(SatkError):
        invoke("obs.unpack", {"src": "sample/server.jsonl"})
    with pytest.raises(SatkError) as e:
        invoke("obs.pack", {"src": "sample/server.jsonl", "out": "weird.bin"})
    assert e.value.code == "BAD_PARAMS"


def test_outputs_stay_under_work_and_the_write_guard_applies(home):
    with pytest.raises(SatkError) as e:
        invoke("obs.merge", {"paths": ["sample/client-fixed.jsonl", "sample/server.jsonl"], "out": str(paths.cfg().paths.game / "x.jsonl")})
    assert e.value.code == "PROTECTED_PATH"


def test_schema_operation(home):
    lst = invoke("obs.schema", {})
    assert [r[0] for r in lst["rows"]] == ["common", "record", "bench", "layout"] and lst["rows"][1][4] == 10
    one = invoke("obs.schema", {"name": "record", "definition": "frame"})
    assert one["schema"]["required"] == ["rec", "frame_ms", "ticks"] and one["path"].endswith("sae-obs-record.schema.json")
    whole = invoke("obs.schema", {"name": "bench"})
    assert whole["schema_id"] == "sae-bench.schema.json" and "stage" in whole["defs"]
    lay = invoke("obs.schema", {"name": "layout"})
    assert lay["schema"]["header"]["size"] == 64
    with pytest.raises(SatkError) as e:
        invoke("obs.schema", {"name": "record", "definition": "frme"})
    assert e.value.code == "NOT_FOUND" and "frame" in (e.value.did_you_mean or []) + [e.value.hint]
    with pytest.raises(SatkError):
        invoke("obs.schema", {"name": "nope"})


def test_cli_surface(home, run_cli):
    r = run_cli(["obs", "validate", "sample/client-fixed.jsonl"])
    assert r.code == 0 and r.json["verdict"] == "pass"
    r = run_cli(["obs", "read", "sample/client-fixed.jsonl", "--rec", "event", "--limit", "5"])
    assert r.code == 0 and r.json["total"] == 1
    r = run_cli(["obs", "summarize", "sample/server.jsonl"])
    assert r.code == 0 and r.json["nodes"][0]["role"] == "server"
    r = run_cli(["obs", "merge", "sample/client-fixed.jsonl", "sample/server.jsonl", "--out", "cli-merge.jsonl"])
    assert r.code == 0 and r.json["records"] == 460
    r = run_cli(["obs", "validate", "missing.jsonl"])
    assert r.code == 1 and r.json["error"]["code"] == "NOT_FOUND"


def test_packages_stay_stdlib_and_fast_to_import():
    import ast

    root = Path(__file__).resolve().parents[2] / "src" / "satk" / "obs"
    std = __import__("sys").stdlib_module_names
    for f in sorted(root.glob("*.py")):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module] if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module else []
            for m in mods:
                assert m.split(".")[0] in std or m.split(".")[0] == "satk", (f.name, m)
    ops_src = (root / "ops.py").read_text(encoding="utf-8")
    top = [ln for ln in ops_src.splitlines() if ln.startswith(("import ", "from "))]
    assert not any("container" in ln or "bench" in ln or "stream" in ln for ln in top)   # the work is imported inside functions
