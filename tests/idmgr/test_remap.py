"""``satk id remap``: plan, byte-preserving rewrite of IDE / text and binary IPL / readme, CLI."""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from satk.core.errors import SatkError
from satk.idmgr.remap import parse_pairs, plan, rewrite_bytes
from satk.idmgr.scan import Def
from satk.mapconv.bnry import encode_bnry, parse_bnry


def _mod(root: Path, builders) -> Path:
    w = builders.w
    w(root, "data/vehicles.ide", "cars\r\n" + builders.car(15000, "mycar") + "\r\n"
      + "15001,  mycar2 ,mycar2, car, MYCAR2, MYCAR2, null, executive, 10, 0, 0, -1, 0.7, 0.7, -1 # was 15001\r\n"
      + builders.car(400, "testcar") + "\r\nend\r\n")
    w(root, "maps/ramps.ide", "objs\n1100, myramp, myramps, 100, 0\nend\n2dfx\n1100, 0, 0, 1, 255 255 255 200, 0\nend\n")
    w(root, "maps/ramps.ipl", "inst\n1100, myramp, 0, 1, 2, 3, 0, 0, 0, 1, -1\n1000, box1, 0, 1, 2, 3, 0, 0, 0, 1, -1\n"
                              "end\ncars\n10, 20, 5, 90, 15000, -1, -1, 0, 0, 0, 0, 0\nend\n")
    w(root, "maps/ramps_stream0.ipl", encode_bnry([(1.0, 2.0, 3.0, 0.0, 0.0, 0.0, 1.0, 1100, 0, -1),
                                                   (4.0, 5.0, 6.0, 0.0, 0.0, 0.0, 1.0, 1000, 0, -1)],
                                                  [(7.0, 8.0, 9.0, 0.0, 15001, -1, -1, 0, 0, 0, 0, 0)]))
    w(root, "readme.txt", "Add to vehicles.ide:\n" + builders.car(15000, "mycar") + "\nID 15000 is mycar\n")
    w(root, "models/mycar.dff", b"\x10\x00\x00\x00synthetic")
    w(root, "cleo/spawn.cs", b"\x00\x01")
    return root


def test_parse_pairs():
    assert parse_pairs("15000=16000, 15001:16001") == {15000: 16000, 15001: 16001}
    with pytest.raises(SatkError):
        parse_pairs("15000-16000")


def test_plan_moves_additions_keeps_replacements():
    defs = [Def(15001, "b", "cars", "mod:m", "m:1"), Def(15000, "a", "cars", "mod:m", "m:2"),
            Def(400, "testcar", "cars", "mod:m", "m:3"), Def(401, "renamed", "cars", "mod:m", "m:4")]
    p = plan(defs, {16000: "x", 16002: "y"}, [(16000, 16010)], base={400: {"testcar"}, 401: {"testcar2"}})
    assert p.mapping == {401: 16001, 15000: 16003, 15001: 16004} and list(p.kept) == [400]
    p = plan(defs, {}, [(16000, 16010)], base={}, explicit={15000: 17000}, only={15001})
    assert p.mapping == {15000: 17000, 15001: 16000} and set(p.kept) == {400, 401}
    p = plan(defs, {17000: "taken"}, [], base={}, explicit={15000: 17000, 999: 5})
    assert len(p.problems) == 2 and p.mapping == {15000: 17000} and set(p.kept.values()) == {"not in --map"}
    with pytest.raises(SatkError) as e:
        plan(defs, {}, [(16000, 16001)], base={})
    assert "2 free ids, the mod needs 4" in e.value.msg


def test_rewrite_bytes_keeps_everything_else():
    m = {15000: 16000, 1100: 16100}
    ide = b"cars\r\n15000 ,mycar, x\t# 15000 stays in the comment\r\n150000, big, x\r\nend\r\n"
    out, n, kind = rewrite_bytes(ide, ".IDE", m, {15000: "mycar"})
    assert (out, n, kind) == (b"cars\r\n16000 ,mycar, x\t# 15000 stays in the comment\r\n150000, big, x\r\nend\r\n",
                              1, "ide")
    data = encode_bnry([(1.0, 2.0, 3.0, 0.0, 0.0, 0.0, 1.0, 1100, 0, -1)], [(7.0, 8.0, 9.0, 0.0, 15000, -1, -1, 0, 0, 0,
                                                                              0, 0)])
    out, n, kind = rewrite_bytes(data, ".ipl", m, {})
    info = parse_bnry(out)
    assert kind == "bnry" and n == 2 and info.insts[0][7] == 16100 and info.cars[0][4] == 16000
    assert len(out) == len(data) and sum(a != b for a, b in zip(out, data)) <= 4  # only the two id fields
    assert rewrite_bytes(b"\x10\x00", ".dff", m, {}) == (b"\x10\x00", 0, "copy")


def test_cli_remap(world, run_cli, satk_home, tmp_path, builders):
    mod = _mod(tmp_path / "carpack", builders)
    r = run_cli(["id", "remap", str(mod), "--to", "1002-1010"])
    assert r.code == 0, r.err
    env = r.json
    out = Path(env["out"])
    assert out == satk_home / "work" / "out" / "idmgr" / "carpack"
    assert env["map"]["rows"] == [[1100, 1002, "myramp"], [15000, 1005, "mycar"], [15001, 1006, "mycar2"]]
    assert env["kept_ids"]["rows"] == [[400, "testcar", "replaces the base-game model of the same name"]]
    assert {r[0]: r[2] for r in env["changes"]["rows"]} == {
        "data/vehicles.ide": 2, "maps/ramps.ide": 2, "maps/ramps.ipl": 2, "maps/ramps_stream0.ipl": 2, "readme.txt": 1}
    assert any(w.startswith("SCRIPTS") for w in env["warn"])
    ide = (out / "data" / "vehicles.ide").read_bytes()
    assert ide.startswith(b"cars\r\n1005, mycar,") and b"1006,  mycar2 ,mycar2" in ide and b"# was 15001\r\n" in ide
    assert b"\r\n400, testcar" in ide
    ipl = (out / "maps" / "ramps.ipl").read_text(encoding="latin-1")
    assert "1002, myramp" in ipl and "1000, box1" in ipl and "10, 20, 5, 90, 1005, -1" in ipl
    b = parse_bnry((out / "maps" / "ramps_stream0.ipl").read_bytes())
    assert [r[7] for r in b.insts] == [1002, 1000] and b.cars[0][4] == 1006
    assert (out / "readme.txt").read_text(encoding="latin-1").endswith("ID 15000 is mycar\n")
    assert (out / "models" / "mycar.dff").read_bytes() == (mod / "models" / "mycar.dff").read_bytes()
    rep = json.loads(Path(env["report"]).read_text(encoding="utf-8"))
    assert rep["map"]["rows"][0] == [1100, 1002, "myramp"]
    # the copy adds no clash: the only error is the installed carpack/other clash on 15000
    c = run_cli(["id", "conflicts", str(out)]).json
    assert [(r[1], r[2]) for r in c["rows"] if r[0] == "error"] == [("CLASH", 15000)]
    assert not any("carpack/data" in r[4] or "carpack/data" in r[5] for r in c["rows"] if r[0] == "error")
    # again: refuse to overwrite, then --force; --dry-run writes nothing
    assert run_cli(["id", "remap", str(mod), "--to", "1002-1010"]).json["error"]["code"] == "EXISTS"
    assert run_cli(["id", "remap", str(mod), "--to", "1002-1010", "--force", "--changed-only"]).code == 0
    assert not (out / "models" / "mycar.dff").exists()
    again = run_cli(["id", "remap", str(out), "--to", "1007-1010", "--force"]).json
    assert again["error"]["code"] == "BAD_PARAMS" and "overlaps" in again["error"]["msg"]
    dry = run_cli(["id", "remap", str(mod), "--map", "15000=1007", "--name", "dry", "--dry-run"]).json
    assert dry["dry_run"] and dry["map"]["rows"] == [[15000, 1007, "mycar"]] and "out" not in dry
    assert not (satk_home / "work" / "out" / "idmgr" / "dry").exists()


def test_cli_remap_errors(world, run_cli, tmp_path, builders):
    mod = _mod(tmp_path / "m", builders)
    assert run_cli(["id", "remap", str(mod)]).json["error"]["code"] == "BAD_PARAMS"
    assert run_cli(["id", "remap", str(tmp_path / "nothing")]).json["error"]["code"] == "NOT_FOUND"
    empty = tmp_path / "empty"
    builders.w(empty, "readme.txt", "no ids here\n")
    assert run_cli(["id", "remap", str(empty), "--to", "1002"]).json["error"]["code"] == "NOT_FOUND"
    r = run_cli(["id", "remap", str(mod), "--to", "1002"])
    assert r.json["error"]["code"] == "BAD_PARAMS" and "1 free ids" in r.json["error"]["msg"]
    r = run_cli(["id", "remap", str(mod), "--map", "15000=1000", "--dry-run"]).json
    assert any(w.startswith("MAP:") and "1000 is taken" in w for w in r["warn"])


def test_remap_byte_layout_of_bnry_header():
    data = encode_bnry([(1.0, 2.0, 3.0, 0.0, 0.0, 0.0, 1.0, 5, 0, -1)], [], style="sizes", pad="none")
    out, n, _ = rewrite_bytes(data, ".ipl", {5: 6}, {})
    assert n == 1 and struct.unpack_from("<i", out, 0x4C + 28)[0] == 6 and out[:0x4C] == data[:0x4C]
