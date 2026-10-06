"""``satk mta`` operations in an isolated workspace (synthetic files and reference only)."""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

import mtaref_synth


def _ok(res):
    assert res.code == 0, res.out + res.err
    return res.json


def _rw(cid: int, payload: bytes = b"\x00" * 12) -> bytes:
    """A minimal RenderWare file: one chunk with a struct child (SA version stamp)."""
    child = struct.pack("<III", 1, len(payload), 0x1803FFFF) + payload
    return struct.pack("<III", cid, len(child), 0x1803FFFF) + child


@pytest.fixture
def ref_file(tmp_path) -> str:
    return str(mtaref_synth.write_reference(tmp_path / "ref.json"))


@pytest.mark.parametrize("kind", ["script", "map", "shader", "vehicle-pack", "skin-pack", "object-pack"])
def test_resource_new_every_kind_lints_clean(satk_home, run_cli, ref_file, kind):
    env = _ok(run_cli(["mta", "resource", "new", f"demo-{kind}", "--kind", kind]))
    d = Path(env["dir"])
    assert d == satk_home / "work" / "out" / "mta" / f"demo-{kind}" and (d / "meta.xml").is_file()
    assert env["lint"]["clean"] is True, env
    lint = _ok(run_cli(["mta", "lint", f"demo-{kind}", "--ref", ref_file, "--codes",
                        "SYNTAX,META_XML,FILE_MISSING,BAD_PATH,NOT_EQ,LUA_FIELD,DUPLICATE_FUNCTION"]))
    assert lint["rows"] == [] and lint["resource"] == f"demo-{kind}"
    if kind == "shader":
        assert (d / "texture.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_resource_new_guards(satk_home, run_cli):
    _ok(run_cli(["mta", "resource", "new", "dup"]))
    res = run_cli(["mta", "resource", "new", "dup"])
    assert res.code != 0 and res.json["error"]["code"] == "EXISTS"
    _ok(run_cli(["mta", "resource", "new", "dup", "--force", "--oop"]))
    assert "<oop>true</oop>" in (satk_home / "work" / "out" / "mta" / "dup" / "meta.xml").read_text(encoding="utf-8")
    res = run_cli(["mta", "resource", "new", "bad name!"])
    assert res.json["error"]["code"] == "BAD_PARAMS"


def test_lint_envelope_filters_and_pages(satk_home, run_cli, ref_file, tmp_path):
    src = tmp_path / "res"
    src.mkdir()
    (src / "meta.xml").write_text('<meta><script src="c.lua" type="client"/></meta>', encoding="utf-8")
    (src / "c.lua").write_text("fooA()\nfooB()\nfooC()\nlocal r = math.round(1)\n", encoding="utf-8")
    env = _ok(run_cli(["mta", "lint", str(src), "--ref", ref_file, "--limit", "2"]))
    assert env["cols"] == ["sev", "at", "code", "msg"] and env["total"] == 4 and env["n"] == 2 and env["next"] == "2"
    assert env["rows"][0][1] == "c.lua:1:1" and env["clean"] is False and env["counts"] == {"error": 4}
    assert env["ref"]["source"] == "file" and env["sides"] == ["client"]
    page2 = _ok(run_cli(["mta", "lint", str(src), "--ref", ref_file, "--limit", "2", "--cursor", "2"]))
    assert page2["n"] == 2 and page2["next"] is None
    only = _ok(run_cli(["mta", "lint", str(src), "--ref", ref_file, "--codes", "LUA_FIELD"]))
    assert [r[2] for r in only["rows"]] == ["LUA_FIELD"] and only["counts"] == {"error": 4}
    bad = run_cli(["mta", "lint", str(src), "--codes", "UNKOWN_FUNCTION"])
    assert bad.json["error"]["code"] == "BAD_PARAMS" and "UNKNOWN_FUNCTION" in bad.json["error"]["did_you_mean"]
    single = _ok(run_cli(["mta", "lint", str(src / "c.lua"), "--ref", "none"]))
    assert [r[2] for r in single["rows"]] == ["LUA_FIELD"]
    miss = run_cli(["mta", "lint", "no-such-resource"])
    assert miss.json["error"]["code"] == "NOT_FOUND"


def test_lint_resource_zip(satk_home, run_cli, ref_file, tmp_path):
    import zipfile

    z = tmp_path / "zipped.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("zipped/meta.xml", '<meta><script src="c.lua" type="client"/></meta>')
        zf.writestr("zipped/c.lua", "kickPlayer(source)\n")
    env = _ok(run_cli(["mta", "lint", str(z), "--ref", ref_file]))
    assert env["target"].endswith("zipped.zip") and [r[1:3] for r in env["rows"]] == [["c.lua:1:1", "WRONG_SIDE"]]
    bad = tmp_path / "slip.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("../evil.lua", "x = 1")
    res = run_cli(["mta", "lint", str(bad)])
    assert res.json["error"]["code"] == "BAD_PARAMS" and "unsafe" in res.json["error"]["msg"]


def test_lint_without_reference_warns(satk_home, run_cli, tmp_path):
    f = tmp_path / "x.lua"
    f.write_text("outputChatBoxx('x')\n", encoding="utf-8")
    env = _ok(run_cli(["mta", "lint", str(f)]))
    assert env["rows"] == [] and any(w.startswith("NOT_READY") for w in env["warn"])


def test_ref_export_and_reuse(satk_home, run_cli, ref_file):
    env = _ok(run_cli(["mta", "ref", "--ref", ref_file, "--out", "copy.json"]))
    out = Path(env["file"])
    assert out == satk_home / "work" / "out" / "mta" / "copy.json" and env["functions"] > 10
    assert json.loads(out.read_text(encoding="utf-8"))["funcs"]["kickPlayer"]
    assert _ok(run_cli(["mta", "ref", "--ref", str(out)]))["neon_only"] == 1
    res = run_cli(["mta", "ref"])
    assert res.json["error"]["code"] == "NOT_READY"


def _models(root: Path, names=("mycar",), col=False) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for n in names:
        (root / f"{n}.dff").write_bytes(_rw(0x10, b"\x01" * 40))
        (root / f"{n}.txd").write_bytes(_rw(0x16, b"\x02" * 60))
        if col:
            (root / f"{n}.col").write_bytes(b"COL3" + b"\x00" * 60)
    return root


def test_pack_replace_mode(satk_home, run_cli, tmp_path):
    src = _models(tmp_path / "cars", ("alpha", "beta"))
    env = _ok(run_cli(["mta", "pack", str(src), "--kind", "vehicle", "--replace", "411", "--replace", "model:415"]))
    d = Path(env["dir"])
    assert d.name == "cars" and env["mode"] == "replace"
    assert env["models"] == [{"name": "alpha", "replace": 411}, {"name": "beta", "replace": 415}]
    meta = (d / "meta.xml").read_text(encoding="utf-8")
    assert meta.index("models/alpha.txd") < meta.index("models/alpha.dff") and "server.lua" not in meta
    client = (d / "client.lua").read_text(encoding="utf-8")
    assert client.index("engineLoadCOL") < client.index("engineLoadTXD") < client.index("engineLoadDFF")
    assert "replace = 411" in (d / "models.lua").read_text(encoding="utf-8")
    assert env["download"]["files"] == 6 and env["lint"]["clean"]
    assert {r[1] for r in env["download"]["rows"]} == {"dff", "txd", "script"}
    res = run_cli(["mta", "pack", str(src), "--kind", "vehicle", "--replace", "411"])
    assert res.json["error"]["code"] == "BAD_PARAMS" and res.json["error"]["data"]["models"] == ["alpha", "beta"]


def test_pack_new_ids_json_and_lod(satk_home, run_cli, tmp_path):
    src = _models(tmp_path / "props", ("crate",), col=True)
    lst = tmp_path / "list.json"
    lst.write_text(json.dumps({"models": [{"dff": "props/crate.dff", "txd": "props/crate.txd",
                                           "col": "props/crate.col", "name": "Crate 1", "parent": 1337}]}),
                   encoding="utf-8")
    env = _ok(run_cli(["mta", "pack", str(lst), "--kind", "object", "--new-id", "--name", "my-props"]))
    d = Path(env["dir"])
    assert env["mode"] == "new-id" and env["models"] == [{"name": "Crate_1", "parent": 1337, "lod": 300.0}]
    meta = (d / "meta.xml").read_text(encoding="utf-8")
    assert 'min_mta_version client="1.6.0"' in meta and '<export function="createPackObject"' in meta
    assert meta.index("crate.col") < meta.index("crate.txd") < meta.index("crate.dff")
    assert "engineRequestModel(MODEL_TYPE, m.parent)" in (d / "client.lua").read_text(encoding="utf-8")
    assert "lod = 300" in (d / "models.lua").read_text(encoding="utf-8")
    assert env["lint"]["clean"] is True and "createPackObject" in env["install"]


def test_pack_errors_and_budget(satk_home, run_cli, tmp_path):
    src = _models(tmp_path / "skins", ("myskin",))
    res = run_cli(["mta", "pack", str(src), "--kind", "skin"])                 # no index: name lookup fails
    assert res.json["error"]["code"] == "NOT_FOUND" and "--replace" in res.json["error"]["hint"]
    res = run_cli(["mta", "pack", str(src)])
    assert res.json["error"]["code"] == "BAD_PARAMS" and "--kind" in res.json["error"]["hint"]
    res = run_cli(["mta", "pack", str(src), "--kind", "skin", "--new-id", "--replace", "7"])
    assert res.json["error"]["code"] == "BAD_PARAMS"
    env = _ok(run_cli(["mta", "pack", str(src), "--kind", "skin", "--new-id", "--budget-mb", "0.0001"]))
    assert any(w.startswith("DOWNLOAD_SIZE") for w in env["warn"]) and env["models"][0]["parent"] == 7
    empty = tmp_path / "empty"
    empty.mkdir()
    assert run_cli(["mta", "pack", str(empty), "--kind", "object"]).json["error"]["code"] == "NOT_FOUND"


LOG = """[2026-10-05 12:00:01] Server started and is ready to accept connections!
[2026-10-05 12:00:02] ERROR: race/race_server.lua:120: attempt to call global 'kickPlayr' (a nil value)
[2026-10-05 12:00:02] WARNING: race/race_server.lua:88: Bad argument @ 'setElementPosition' [Expected element at argument 1, got nil]  [DUP x12]
[2026-10-05 12:00:03] WARNING: freeroam/fr_client.lua(Line 5) [Client] getPlayerOccupiedVehicle is deprecated and may not work in future versions. Please replace with getPedOccupiedVehicle.
[2026-10-05 12:00:04] Couldn't find file(s) models/car.txd for resource mycars
[2026-10-05 12:00:04] Loading of resource 'mycars' failed
[12:00:05] SCRIPT ERROR: hud/client.lua:33: 'end' expected (to close 'function' at line 10) near '<eof>'
ERROR: hud/client.lua:50: attempt to call global 'dxDrawText' (a nil value)
INFO: hud started
[2026-10-05 12:00:08] ERROR: race/race_server.lua:120: attempt to call global 'kickPlayr' (a nil value)
"""


def test_logs_rows_groups_and_hints(satk_home, run_cli, ref_file, tmp_path):
    f = tmp_path / "server.log"
    f.write_text(LOG, encoding="utf-8")
    env = _ok(run_cli(["mta", "logs", str(f), "--ref", ref_file]))
    assert env["cols"] == ["line", "level", "res", "at", "msg", "n", "hint"]
    rows = {(r[2], r[3]): r for r in env["rows"]}
    call = rows[("race", "race_server.lua:120")]
    assert call[1] == "error" and call[5] == 2 and "did you mean kickPlayer" in call[6]
    bad = rows[("race", "race_server.lua:88")]
    assert bad[1] == "warning" and bad[5] == 12 and bad[6].startswith("setElementPosition(element entity")
    assert rows[("freeroam", "fr_client.lua:5")][1] == "warning"
    assert rows[("hud", "client.lua:50")][6].startswith("dxDrawText is a client-only function")
    assert rows[("hud", "client.lua:33")][1] == "error" and rows[("mycars", "")][1] == "error"
    assert env["counts"]["error"] == 6 and env["by_resource"]["race"] == 14
    assert env["rows"][0][1] == "error"                                       # errors first
    flat = _ok(run_cli(["mta", "logs", str(f), "--level", "all", "--no-group", "--resource", "race"]))
    assert flat["total"] == 3 and [r[0] for r in flat["rows"]] == [2, 3, 10]
    only = _ok(run_cli(["mta", "logs", str(f), "--level", "error", "--grep", "nil value", "--ref", "none"]))
    assert {r[2] for r in only["rows"]} == {"race", "hud"} and all(r[6] for r in only["rows"])


def test_logs_parser_details():
    from satk.mta.logs import parse_lines

    rows = parse_lines(['ERROR: [string "return 1 +"]:1: unexpected symbol near \'<eof>\'',
                        "WARNING: Loading script failed: res/a.lua:3: malformed number near '1..2'",
                        "[12:00:00] start: Resource 'res' started"])
    assert (rows[0].level, rows[0].at) == ("error", '[string "return 1 +"]:1')
    assert (rows[1].resource, rows[1].at, rows[1].level) == ("res", "a.lua:3", "error")
    assert rows[2].level == "log" and rows[2].time == "12:00:00"


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
def test_log_tail_preserves_complete_lines_at_the_read_boundary(tmp_path, newline):
    from satk.mta.logs import read_tail

    f = tmp_path / "tail.log"
    prefix = b"discarded" + newline
    tail = b"ERROR: res/a.lua:1: keep" + newline + b"WARNING: res/b.lua:2: keep" + newline
    f.write_bytes(prefix + tail)
    lines, first, truncated = read_tail(f, len(tail))
    assert lines == ["ERROR: res/a.lua:1: keep", "WARNING: res/b.lua:2: keep"]
    assert first == 0 and truncated
    assert read_tail(f, len(tail) - 3)[0] == ["WARNING: res/b.lua:2: keep"]
    assert read_tail(f, 3)[0] == []
    f.write_bytes(b"a long line without a newline")
    assert read_tail(f, 8)[0] == []


def test_logs_report_missing_reference_without_losing_messages(satk_home, run_cli, tmp_path):
    f = tmp_path / "server.log"
    f.write_text("ERROR: res/a.lua:1: attempt to call global 'foo' (a nil value)\n", encoding="utf-8")
    for ref, warning in ((str(tmp_path / "missing.json"), "NOT_FOUND"), ("auto", "NOT_READY")):
        env = _ok(run_cli(["mta", "logs", str(f), "--ref", ref]))
        assert env["total"] == 1 and env["rows"][0][6].startswith("foo is nil"), env
        assert any(w.startswith(warning) for w in env["warn"]), env
    assert not _ok(run_cli(["mta", "logs", str(f), "--ref", "none"])).get("warn")
