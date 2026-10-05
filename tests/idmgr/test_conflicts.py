"""``satk id conflicts`` and the definition scanners (synthetic profiles of tests/idmgr/conftest.py)."""

from __future__ import annotations

from satk.idmgr.conflicts import conflicts
from satk.idmgr.scan import Def, readme_defs, scan_mod


def _codes(rows):
    return sorted((r[1], r[2]) for r in rows)


def test_readme_lines():
    text = ("Install: copy the files, then add\n"
            "15000, mycar, mycar, car, MYCAR, MYCAR, null, executive, 10, 0, 0, -1, 0.7, 0.7, -1\n"
            "15001 skin1 skin1 CIVMALE STAT_STREET_GUY man 1983 1 null 9 9 PED_TYPE_GEN V1 V2\n"
            "15002, not, enough, fields\n"
            "# 15003, mycar, mycar, car, MYCAR, MYCAR, null, executive, 10, 0, 0, -1, 0.7, 0.7, -1\n")
    defs = readme_defs(text, "mod:x", "x/readme.txt")
    assert [(d.id, d.name, d.sec, d.where, d.via) for d in defs] == [
        (15000, "mycar", "cars", "x/readme.txt:2", "readme"), (15001, "skin1", "peds", "x/readme.txt:3", "readme")]


def test_rules():
    d = [Def(400, "testcar", "cars", "vanilla", "data/vehicles.ide:1"),
         Def(300, "cutobj01", "hier", "vanilla", "data/default.ide:1"),
         Def(300, "lapdna", "peds", "samp", "samp/samp.ide:1"),
         Def(15000, "a", "cars", "modloader:a", "modloader/a/v.ide:1"),
         Def(15000, "b", "cars", "modloader:b", "modloader/b/v.ide:1"),
         Def(15001, "c", "cars", "modloader:a", "modloader/a/v.ide:2"),
         Def(15001, "c", "cars", "modloader:b", "modloader/b/v.ide:2"),
         Def(400, "testcar", "cars", "modloader:a", "modloader/a/v.ide:3"),
         Def(15005, "d1", "cars", "modloader:a", "modloader/a/v.ide:4"),
         Def(15005, "d2", "cars", "modloader:a", "modloader/a/v.ide:5"),
         Def(15010, "testcar", "cars", "modloader:b", "modloader/b/v.ide:3"),
         Def(15020, "ghost", "cars", "samp", "samp/samp.ide:2", loaded=False)]
    rows = conflicts(d)
    assert _codes(r for r in rows if r[0] == "error") == [("CLASH", 15000)]
    assert _codes(r for r in rows if r[0] == "warn") == [
        ("DUPLICATE", 15001), ("DUP_IN_MOD", 15005), ("NAME_TWICE", 15010)]
    assert _codes(r for r in rows if r[0] == "info") == [("BASE", 300), ("REDEFINES", 400)]
    base = next(r for r in rows if r[1] == "BASE")
    assert base[3] == "lapdna" and "cutobj01" in base[5]  # the later layer first
    assert [r[0] for r in rows] == sorted((r[0] for r in rows), key=["error", "warn", "info"].index)


def test_cli_profile_mods(world, run_cli):
    r = run_cli(["id", "conflicts", "--profile", "installed"])
    assert r.code == 0, r.err
    env = r.json
    assert env["profile"] == "installed" and env["errors"] == 1
    assert _codes(env["rows"]) == [("CLASH", 15000), ("REPLACES", 401)]
    clash = env["rows"][0]
    assert clash[0] == "error" and {clash[3], clash[5].split(" @ ")[0]} == {"mycar", "othercar"}
    assert "modloader/" in clash[4]
    full = run_cli(["id", "conflicts", "--profile", "installed", "--all"]).json
    assert ("REDEFINES", 400) in _codes(full["rows"])
    assert run_cli(["id", "conflicts", "--profile", "vanilla"]).json["total"] == 0


def test_cli_given_mods(world, run_cli, tmp_path, builders):
    a = tmp_path / "packA"
    b = tmp_path / "dl" / "packA"  # same folder name: owners stay apart
    builders.w(a, "data/vehicles.ide", f"cars\n{builders.car(15500, 'newcar')}\nend\n")
    builders.w(b, "vehicles.ide", f"cars\n{builders.car(15500, 'other')}\n{builders.car(15002, 'mycar3')}\nend\n")
    env = run_cli(["id", "conflicts", str(a), str(b), "--no-use-profile"]).json
    assert env["profile"] is None and _codes(env["rows"]) == [("CLASH", 15500)]
    assert {env["rows"][0][4], env["rows"][0][5].split(" @ ")[1]} == {"packA/data/vehicles.ide:2",
                                                                      "packA#2/vehicles.ide:2"}
    env = run_cli(["id", "conflicts", str(b)]).json  # default profile: installed
    assert env["profile"] == "installed"
    assert ("DUPLICATE", 15002) in _codes(env["rows"])  # the same add-on is already installed (carpack)
    r = run_cli(["id", "conflicts", str(tmp_path / "missing")])
    assert r.json["error"]["code"] == "NOT_FOUND"


def test_scan_mod_file_and_folder(tmp_path, builders):
    builders.w(tmp_path / "m", "x.ide", "objs\n18000, ramp, ramp, 100, 0\nend\n")
    builders.w(tmp_path / "m", "sub/readme.txt", builders.car(15000, "c") + "\n")
    defs = scan_mod(tmp_path / "m")
    assert [(d.id, d.owner, d.where) for d in defs] == [(15000, "mod:m", "m/sub/readme.txt:1"),
                                                       (18000, "mod:m", "m/x.ide:2")] or \
        [(d.id, d.owner, d.where) for d in defs] == [(18000, "mod:m", "m/x.ide:2"),
                                                    (15000, "mod:m", "m/sub/readme.txt:1")]
    one = scan_mod(tmp_path / "m" / "x.ide")
    assert [(d.id, d.owner, d.where) for d in one] == [(18000, "mod:x.ide", "x.ide:2")]
