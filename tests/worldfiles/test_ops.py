"""CLI of satk.worldfiles in an isolated workspace with a synthetic game (no game files)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from satk.core.registry import all_ops

OPS = ("gxt.get", "gxt.export", "gxt.write", "gxt.patch", "gxt.keys", "fxt.write", "zone.list", "zone.check",
       "zone.add", "zone.remove", "zone.export", "zone.write", "water.list", "water.add", "water.remove",
       "water.export", "water.write", "timecyc.get", "timecyc.patch", "timecyc.diff", "popcycle.get",
       "popcycle.patch", "radar.export", "radar.build")


def test_operations_are_cli_only():
    ops = {o.name: o for o in all_ops()}
    for name in OPS:
        assert ops[name].mcp_name is None, name
        assert ops[name].module == "satk.worldfiles.ops"


def ok(r):
    assert r.code == 0, r.out + r.err
    return r.json


# --------------------------------------------------------------------------- GXT / FXT


def test_gxt_get_export_write_round_trip(game, run_cli, out_dir):
    env = ok(run_cli(["gxt", "get", "town", "INT1_AA", "NOPE"]))
    assert [r[1:] for r in env["rows"]] == [["MAIN", "Town"], ["INTRO1", "~z~Hello."]] and env["missing"] == ["NOPE"]
    env = ok(run_cli(["gxt", "get", "--search", "big"]))
    assert env["rows"][0][2] == "Big City"
    env = ok(run_cli(["gxt", "get"]))
    assert env["rows"] == [["MAIN", 6, env["rows"][0][2]], ["INTRO1", 2, env["rows"][1][2]]] and env["entries"] == 8
    src = (game.root / "text" / "american.gxt").read_bytes()
    env = ok(run_cli(["gxt", "export", "--out", "am.json"]))
    assert env["round_trip"] is True and env["entries"] == 8
    doc = json.loads((out_dir / "am.json").read_text(encoding="utf-8"))
    assert doc["format"] == "satk-gxt/1" and any(e[1] == "Café über" for e in doc["tables"][0]["entries"])
    env = ok(run_cli(["gxt", "write", str(out_dir / "am.json"), "--out", "back.gxt"]))
    assert env["verified"] and (out_dir / "back.gxt").read_bytes() == src
    ok(run_cli(["gxt", "export", "american", "--format", "dir", "--out", "amdir"]))
    assert sorted(p.name for p in (out_dir / "amdir").iterdir()) == ["INTRO1.txt", "MAIN.txt", "gxt.json"]
    main = out_dir / "amdir" / "MAIN.txt"
    main.write_text(main.read_text(encoding="utf-8") + "NEWKEY Brand new é\n", encoding="utf-8")
    ok(run_cli(["gxt", "write", str(out_dir / "amdir"), "--out", "edited.gxt"]))
    from satk.formats.gxt import load_gxt

    g = load_gxt(out_dir / "edited.gxt")
    assert g.text("NEWKEY") == "Brand new \x9e" and g.text("INT1_AB", "INTRO1") == "~z~Bye."


def test_gxt_patch_fxt_and_full(game, run_cli, out_dir):
    env = ok(run_cli(["gxt", "patch", "american", "TOWN=Old Town, west", "NEWZ=Zone é"]))
    assert env["rows"] == [["TOWN", "MAIN", "Town", "Old Town, west"], ["NEWZ", "MAIN", "-", "Zone é"]]
    fxt = (Path(env["out"]) / env["files"][0]).read_bytes()
    assert fxt.endswith(b"TOWN Old Town, west\r\nNEWZ Zone \x9e\r\n")
    env = ok(run_cli(["gxt", "patch", "american", "TOWN=Village", "0x00ABCDEF=renamed", "--target", "full",
                      "--out", "full"]))
    from satk.formats.gxt import load_gxt

    g = load_gxt(out_dir / "full" / "text" / "american.gxt")
    assert g.text("TOWN") == "Village" and g.tables["MAIN"][0xABCDEF] == "renamed" and g.text("PARK") == "Park"
    r = run_cli(["gxt", "patch", "american", "0x00ABCDEF=x"])
    assert r.code != 0 and "hash" in r.json["error"]["msg"]
    r = run_cli(["gxt", "patch", "american", "TOWN=Привет"])
    assert r.code != 0 and "cp1251" in r.json["error"]["hint"]
    env = ok(run_cli(["gxt", "patch", "american", "TOWN=Привет", "--charset", "cp1251", "--dry-run"]))
    assert env["dry_run"] is True and not (out_dir / "gxt-american-fxt" / "x").exists()


def test_fxt_write_from_pairs_and_files(game, run_cli, out_dir, tmp_path):
    src = tmp_path / "t.txt"
    src.write_text("# comment\nA1 First\nB2 Second ça\n", encoding="utf-8")
    env = ok(run_cli(["fxt", "write", str(src), "A1=Override", "--out", "mine"]))
    assert env["entries"] == 2
    assert (out_dir / "mine.fxt").read_bytes() == b"# satk fxt write\r\nA1 Override\r\nB2 Second \x9ca\r\n"
    r = run_cli(["fxt", "write", "nofile.json"])
    assert r.code != 0 and r.json["error"]["code"] == "NOT_FOUND"


def test_gxt_keys(game, run_cli):
    env = ok(run_cli(["gxt", "keys"]))
    assert env["keys"] == 8
    env = ok(run_cli(["gxt", "keys", "--scan"]))
    assert env["scanned"] >= 0 and Path(env["cache"]).is_file()


# --------------------------------------------------------------------------- zones


def test_zone_list_check_add_remove(game, run_cli, out_dir):
    env = ok(run_cli(["zone", "list", "--at", "0,0"]))
    assert [r[1] for r in env["rows"]] == ["PARK", "TOWN1", "BIG"] and env["shown"] == "PARK"
    assert env["shown_text"] == "Park"
    env = ok(run_cli(["zone", "list", "--name", "TOWN*"]))
    assert [r[1] for r in env["rows"]] == ["TOWN1", "TOWN2"] and env["rows"][0][3] == "Town"
    env = ok(run_cli(["zone", "check"]))
    assert env["counts"] == {"navigation": 6, "map": 3, "zone_info": 6}
    assert any("overlap partly" in r[2] for r in env["rows"])
    env = ok(run_cli(["zone", "add", "MYTOWN", "--box", "-20,-20,0,20,20,100", "--text", "My Town"]))
    assert env["added"]["line"] == "MYTOWN, 0, -20.0, -20.0, 0.0, 20.0, 20.0, 100.0, 1, MYTOWN"
    assert {r[0]: r[1] for r in env["rows"]} == {"#2 PARK": "inside", "#0 TOWN1": "inside", "#4 BIG": "inside"}
    folder = Path(env["out"])
    assert sorted(env["files"]) == ["data/info.zon", "zone-mytown.fxt"]
    assert (folder / "zone-mytown.fxt").read_bytes().endswith(b"MYTOWN My Town\r\n")
    zon = folder / "data" / "info.zon"
    assert zon.read_bytes() == game.root.joinpath("data", "info.zon").read_bytes().replace(
        b"end\r\n", b"MYTOWN, 0, -20.0, -20.0, 0.0, 20.0, 20.0, 100.0, 1, MYTOWN\r\nend\r\n")
    env = ok(run_cli(["zone", "check", "--file", str(zon)]))
    assert not any("MYTOWN is not a key" in r[2] for r in env["rows"])     # the mod's FXT names it
    env = ok(run_cli(["zone", "list", "--at", "0,0", "--file", str(zon)]))
    assert env["shown"] == "MYTOWN"
    env = ok(run_cli(["zone", "remove", "MYTOWN", "--file", str(zon), "--out", "back"]))
    assert (out_dir / "back" / "data" / "info.zon").read_bytes() == game.root.joinpath("data", "info.zon").read_bytes()
    r = run_cli(["zone", "add", "WAYTOOLONGNAME", "--box", "0,0,1,1"])
    assert r.code != 0 and "7" in r.json["error"]["msg"]
    env = ok(run_cli(["zone", "add", "NOTEXT", "--box", "0,0,10,10", "--dry-run"]))
    assert env["dry_run"] and any(w.startswith("NO_TEXT") for w in env["warn"])


def test_zone_limits_warn(game, run_cli):
    lines = "".join(f"Z{i}, 0, {i}.0, 0.0, 0.0, {i}.5, 1.0, 1.0, 1, Z{i}\r\n" for i in range(375))
    (game.root / "data" / "info.zon").write_bytes(("zone\r\n" + lines + "end\r\n").encode())
    env = ok(run_cli(["zone", "add", "LAST", "--box", "0,0,10,10", "--dry-run"]))
    assert not any(w.startswith("LIMIT") for w in env.get("warn", []))       # 1 + 375 + 1 = 377 <= 380
    lines += "".join(f"Y{i}, 0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1, Y{i}\r\n" for i in range(4))
    (game.root / "data" / "info.zon").write_bytes(("zone\r\n" + lines + "end\r\n").encode())
    env = ok(run_cli(["zone", "add", "LAST", "--box", "0,0,10,10", "--dry-run"]))
    assert any(w.startswith("LIMIT: 381 navigation zones") for w in env["warn"])


def test_zone_export_write_round_trip(game, run_cli, out_dir):
    for f in ("info", "map"):
        env = ok(run_cli(["zone", "export", f]))
        assert env["round_trip"] is True
        env = ok(run_cli(["zone", "write", env["path"], "--out", f"{f}2.zon"]))
        assert (out_dir / f"{f}2.zon").read_bytes() == (game.root / "data" / f"{f}.zon").read_bytes()


# --------------------------------------------------------------------------- water


def test_water_list_add_remove_export(game, run_cli, out_dir):
    env = ok(run_cli(["water", "list"]))
    assert env["counts"] == {"quads": 3, "tris": 1, "verts": 13} and env["rows"][1][7] == "visible,shallow"
    env = ok(run_cli(["water", "list", "--at", "150,150,10"]))
    assert [r[0] for r in env["rows"]] == [2]
    env = ok(run_cli(["water", "add", "--rect", "500,500,600,650", "--z", "3", "--shallow"]))
    wat = Path(env["out"]) / "data" / "water.dat"
    assert wat.read_text().splitlines()[-1] == ("500.0 500.0 3.00000 0.00000 0.00000 0.00000 0.00000    "
                                                "600.0 500.0 3.00000 0.00000 0.00000 0.00000 0.00000    "
                                                "500.0 650.0 3.00000 0.00000 0.00000 0.00000 0.00000    "
                                                "600.0 650.0 3.00000 0.00000 0.00000 0.00000 0.00000  3")
    env = ok(run_cli(["water", "add", "--rect", "150,150,300,300", "--z", "3", "--target", "mta"]))
    assert any(w.startswith("OVERLAP") for w in env["warn"])
    lua = (Path(env["out"]) / "water.lua").read_text()
    assert "createWater(150.00, 150.00, 3.00, 300.00, 150.00, 3.00, 150.00, 300.00, 3.00, 300.00, 300.00, 3.00, " \
           "false)" in lua
    env = ok(run_cli(["water", "remove", "4", "--file", str(wat), "--out", "wback"]))
    assert (out_dir / "wback" / "data" / "water.dat").read_bytes() == (game.root / "data" / "water.dat").read_bytes()
    r = run_cli(["water", "add", "--rect", "5,5,5,9"])
    assert r.code != 0
    for f in ("water", "water1"):
        env = ok(run_cli(["water", "export", f]))
        assert env["round_trip"] is True
        ok(run_cli(["water", "write", env["path"], "--out", f"{f}2.dat"]))
        assert (out_dir / f"{f}2.dat").read_bytes() == (game.root / "data" / f"{f}.dat").read_bytes()


def test_water_add_warns_at_the_pool_limit(game, run_cli):
    from .conftest import water_line

    lines = [water_line(-3000 + 10 * i, 0, -2995 + 10 * i, 5) for i in range(301)]
    (game.root / "data" / "water.dat").write_text("processed\n" + "\n".join(lines) + "\n")
    env = ok(run_cli(["water", "add", "--rect", "0,1000,10,1010", "--dry-run"]))
    assert any(w.startswith("LIMIT: 302 quads") for w in env["warn"])
    env = ok(run_cli(["water", "list", "--limit", "1"]))
    assert env["free"]["quads"] == 0


# --------------------------------------------------------------------------- timecyc / popcycle


def test_timecyc_get_patch_diff(game, run_cli, out_dir):
    env = ok(run_cli(["timecyc", "get"]))
    assert env["n"] == 23 and env["hours"] == [0, 5, 6, 7, 12, 19, 20, 22]
    env = ok(run_cli(["timecyc", "get", "SUNNY_LA", "12"]))
    assert env["values"]["sky_top"] == [40, 1, 200] and env["values"]["far_clip"] == 410.0
    env = ok(run_cli(["timecyc", "get", "*_LA", "12", "--field", "far_clip", "sky_top.g"]))
    assert env["cols"] == ["weather", "hour", "far_clip", "sky_top.g"] and env["total"] == 5
    r = run_cli(["timecyc", "get", "SUNNY_LA", "13"])
    assert r.code != 0 and "12" in r.json["error"]["did_you_mean"]
    r = run_cli(["timecyc", "get", "SUNNY_LA", "12", "--field", "farclip"])
    assert r.code != 0 and r.json["error"]["did_you_mean"] == ["far_clip"]
    env = ok(run_cli(["timecyc", "patch", "SUNNY_LA", "12", "sky_top=1,2,3", "far_clip*=2"]))
    assert env["verified"] and env["lines"] == 1 and len(env["rows"]) == 4
    patched = Path(env["out"]) / "data" / "timecyc.dat"
    env = ok(run_cli(["timecyc", "diff", str(patched)]))
    assert env["total"] == 4 and env["by_field"] == {"sky_top": 3, "far_clip": 1}
    env = ok(run_cli(["timecyc", "patch", "SUNNY_LA", "12", "sky_top=40,1,200", "far_clip=410", "--file",
                      str(patched), "--out", "tback"]))
    assert (out_dir / "tback" / "data" / "timecyc.dat").read_bytes() == \
        (game.root / "data" / "timecyc.dat").read_bytes()
    env = ok(run_cli(["timecyc", "patch", "all", "all", "sky_top.r+=500", "--dry-run"]))
    assert env["dry_run"] and any(w.startswith("RANGE") for w in env["warn"])
    assert any(w.startswith("REWRITTEN") for w in env["warn"])
    env = ok(run_cli(["timecyc", "diff", str(game.root / "data" / "timecyc.dat")]))
    assert env["same"] is True


def test_popcycle_get_patch(game, run_cli, out_dir):
    env = ok(run_cli(["popcycle", "get"]))
    assert env["n"] == 20 and env["rows"][7] == [7, "GANGLAND"]
    env = ok(run_cli(["popcycle", "get", "GANGLAND", "weekday", "20"]))
    assert env["hours"] == "20-22" and env["values"]["max_cars"] == 12
    env = ok(run_cli(["popcycle", "patch", "GANGLAND", "all", "all", "max_peds*=2", "gang=80"]))
    assert env["verified"] and env["lines"] == 24 and env["total"] == 48
    r = run_cli(["popcycle", "patch", "GANGLAND", "all", "all", "gnag=1"])
    assert r.code != 0 and r.json["error"]["did_you_mean"] == ["gang"]
    env = ok(run_cli(["popcycle", "patch", "BEACH", "weekend", "8", "max_peds=999", "--dry-run"]))
    assert env["rows"][0][-1] == 255 and env["warn"][0].startswith("CLAMPED")


# --------------------------------------------------------------------------- radar


def test_radar_export_and_build(game, run_cli, out_dir):
    pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    env = ok(run_cli(["radar", "export", "--tile", "32"]))
    assert env["tiles"] == 144 and env["size"] == 384
    env = ok(run_cli(["radar", "build", env["path"], "--out", "myradar", "--tile", "32"]))
    assert env["verified"] and env["psnr"] >= 30 and env["tiles"] == 144
    folder = Path(env["out"])
    names = sorted(p.name for p in (folder / "radar").iterdir())
    assert len(names) == 144 and "radar00.txd" in names and "radar143.txd" in names
    from satk.worldfiles.radar import decode_tile

    name, px = decode_tile((folder / "radar" / "radar143.txd").read_bytes())
    assert name == "radar143" and px.shape == (32, 32, 4)
    r = run_cli(["radar", "build"])
    assert r.code != 0 and r.json["error"]["code"] == "BAD_PARAMS"
    env = ok(run_cli(["radar", "build", "--from-map", "--layers", "water", "--tile", "32", "--out", "schem"]))
    assert env["drawn"]["water"] == 4 and env["verified"]
