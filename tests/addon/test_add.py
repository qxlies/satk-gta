"""``satk mod add`` on the synthetic game of conftest.py: files, lines, checks and what Mod Loader would read."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from satk.addon.game import tok
from satk.modinspect.traits import trim_config_line

from .conftest import CARGRP, OBJECT_DAT, VEHICLES_IDE


def _ok(r):
    assert r.code == 0, r.out + r.err
    return r.json


def _add(run_cli, game, kind, dff, *args, txd=None):
    argv = ["mod", "add", kind, "--dff", str(game.inp / dff)]
    if txd:
        argv += ["--txd", str(game.inp / txd)]
    return run_cli(argv + ["--profile", "vanilla", *args])


def _readme(env) -> list[str]:
    p = Path(env["out"]) / f"{env['name']}.txt"
    return [ln for ln in p.read_bytes().decode("latin-1").splitlines() if ln and not ln.startswith("#")]


def _same_but(new: str, old: str, changed: dict[int, str]) -> None:
    """``new`` has the tokens of ``old`` except ``changed`` (token index -> value)."""
    a, b = tok(new).toks, tok(old).toks
    assert len(a) == len(b)
    for i, (x, y) in enumerate(zip(a, b)):
        assert x == changed.get(i, y), (i, x, y)


def test_vehicle(game, run_cli):
    env = _ok(_add(run_cli, game, "vehicle", "car.dff", "--like", "testcar", "--name", "NewCar",
                   "--game-name", "New Car", txd="car.txd"))
    assert env["id"] == "model:404" and env["name"] == "newcar" and env["donor"] == "model:400 testcar"
    out = Path(env["out"])
    assert out == game.work / "out" / "addon" / "newcar"
    assert env["files"] == ["newcar.dff", "newcar.fxt", "newcar.txd", "newcar.txt"]
    assert (out / "newcar.dff").read_bytes() == (game.inp / "car.dff").read_bytes()
    assert any(w.startswith("NO_INDEX") for w in env["warn"])
    assert any(w.startswith("NEW_HANDLING_ID") for w in env["warn"])
    lines = _readme(env)
    donor_ide = VEHICLES_IDE.splitlines()[2]
    _same_but(lines[0], donor_ide, {0: "404", 1: "newcar", 2: "newcar", 4: "NEWCAR", 5: "NEWCAR"})
    from .conftest import car
    assert lines[1] == car("NEWCAR")                        # every byte of the donor line, new id only
    assert lines[2] == "newcar, 1,0, 2,1" and lines[3] == "newcar, nto_b_l, nto_b_s, nto_b_tw"
    assert (out / "newcar.fxt").read_bytes().endswith(b"NEWCAR New Car\r\n")
    # what Mod Loader makes of the folder
    ins = _ok(run_cli(["mod", "inspect", str(out), "--profile", "vanilla"]))
    got = {(r[0], r[1], r[2]) for r in ins["rows"]}
    assert ("ide", "model:404", "new-id") in got and ("data", "handling:NEWCAR", "add") in got
    assert not ins.get("warn")
    lint = _ok(run_cli(["asset", "lint", str(out)]))
    assert lint["summary"]["fatal"] == 0 and lint["summary"]["error"] == 0


def test_vehicle_extra_handling_lines_and_car4(game, run_cli):
    bike = _ok(_add(run_cli, game, "vehicle", "car.dff", "--like", "401", "--name", "bike2", "--dry-run",
                    txd="car.txd"))
    rows = [r for r in bike["rows"] if r[0] == "handling.cfg"]
    assert len(rows) == 2 and rows[1][1].startswith("!\tBIKE2\t\t0.35")
    boat = _ok(_add(run_cli, game, "vehicle", "car.dff", "--like", "tboat", "--name", "boat2", txd="car.txd"))
    lines = _readme(boat)
    assert lines[2].startswith("%\tBOAT2\t\t0.50") and lines[3] == "boat2, 0,0,0,1, 1,2,1,1"
    plane = _ok(_add(run_cli, game, "vehicle", "car.dff", "--like", "tplane", "--name", "plane2", txd="car.txd"))
    assert any(ln.startswith("$\tPLANE2") and "0.1s" in ln for ln in _readme(plane))
    assert any(w.startswith("NO_COLOURS") for w in plane["warn"])
    ins = _ok(run_cli(["mod", "inspect", plane["out"], "--profile", "vanilla"]))
    assert ("data", "handling:PLANE2", "add") in {(r[0], r[1], r[2]) for r in ins["rows"]}
    donor = _ok(_add(run_cli, game, "vehicle", "car.dff", "--like", "tboat", "--name", "boat3", "--handling", "donor",
                     "--dry-run", txd="car.txd"))
    assert [r[0] for r in donor["rows"]] == ["vehicles.ide", "carcols.dat", "boat3.fxt"]
    assert tok(donor["rows"][0][1]).toks[4] == "TBOAT"


def test_vehicle_names_keys_and_groups(game, run_cli):
    env = _ok(_add(run_cli, game, "vehicle", "car.dff", "--like", "testcar", "--name", "testcarx", "--dry-run",
                   txd="car.txd"))
    assert tok(env["rows"][0][1]).toks[5] == "TESTCA1" and any(w.startswith("GXT_KEY") for w in env["warn"])
    env = _ok(_add(run_cli, game, "vehicle", "car.dff", "--like", "testcar", "--name", "intro", "--dry-run",
                   txd="car.txd"))
    assert tok(env["rows"][0][1]).toks[5] == "INTRO1"                     # INTRO is a key of american.gxt
    r = _add(run_cli, game, "vehicle", "car.dff", "--like", "testcar", "--name", "averylongname1", txd="car.txd")
    assert r.json["error"]["code"] == "BAD_PARAMS" and "--handling donor" in r.json["error"]["hint"]
    env = _ok(_add(run_cli, game, "vehicle", "car.dff", "--like", "testcar", "--name", "grpcar", "--cargrp", "donor",
                   "boats", txd="car.txd"))
    new = (Path(env["out"]) / "data" / "cargrp.dat").read_bytes().decode("latin-1")
    assert new == CARGRP.replace("testcar, tbike\t#", "testcar, tbike, grpcar\t#").replace(
        "tboat, tplane\t\t#", "tboat, tplane, grpcar\t\t#")
    full = ", ".join(f"car{i}" for i in range(23))
    (game.vanilla / "data" / "cargrp.dat").write_bytes(f"{full}, testcar\t# big\r\n".encode("latin-1"))
    env = _ok(_add(run_cli, game, "vehicle", "car.dff", "--like", "testcar", "--name", "late", "--cargrp", "big",
                   "--dry-run", txd="car.txd"))
    assert any(w.startswith("CARGRP_FULL: car group 0 already lists 24 models") for w in env["warn"])
    (game.vanilla / "data" / "cargrp.dat").write_bytes(CARGRP.encode("latin-1"))
    for bad, code in ((["nosuch"], "NOT_FOUND"), (["7"], "NOT_FOUND"), (["popcycle"], "AMBIGUOUS")):
        r = _add(run_cli, game, "vehicle", "car.dff", "--like", "testcar", "--name", "grpcar2", "--cargrp", *bad,
                 txd="car.txd")
        assert r.json["error"]["code"] == code, bad


def test_checks(game, run_cli):
    cases = [
        (["--like", "testcar", "--name", "testcar"], "EXISTS"),          # model name of the game
        (["--like", "testcar", "--name", "taken"], "EXISTS"),            # taken.txd in gta3.img
        (["--like", "testcar", "--name", "x1", "--id", "400"], "EXISTS"),
        (["--like", "testcar", "--name", "x1", "--id", "abc"], "BAD_PARAMS"),
        (["--like", "male01", "--name", "x1"], "BAD_PARAMS"),            # donor of another kind
        (["--like", "nosuch", "--name", "x1"], "NOT_FOUND"),
        (["--like", "testcar", "--name", "bad-name"], "BAD_PARAMS"),
        (["--like", "testcar", "--name", "a" * 20], "BAD_PARAMS"),
        (["--name", "x1"], "BAD_PARAMS"),                               # no donor
    ]
    for args, code in cases:
        r = _add(run_cli, game, "vehicle", "car.dff", *args, txd="car.txd")
        assert r.code != 0 and r.json["error"]["code"] == code, (args, r.json)
    r = _add(run_cli, game, "vehicle", "bad.dff", "--like", "testcar", "--name", "x1", txd="car.txd")
    assert r.json["error"]["code"] == "BAD_PARAMS" and "DFF" in r.json["error"]["msg"]
    r = run_cli(["mod", "add", "vehicle", "--dff", "nosuch.dff", "--txd", "x.txd", "--like", "400", "--name", "x1",
                 "--profile", "vanilla"])
    assert r.json["error"]["code"] == "NOT_FOUND"
    env = _ok(_add(run_cli, game, "vehicle", "nocol.dff", "--like", "testcar", "--name", "x2", "--id", "15000",
                   "--dry-run", txd="ped.txd"))
    assert env["id"] == "model:15000" and env["dry_run"] is True
    codes = {w.split(":")[0] for w in env["warn"]}
    assert {"NO_EMBEDDED_COL", "MISSING_TEXTURES", "ADDON_VEHICLE"} <= codes
    assert not (game.work / "out" / "addon" / "x2").exists()


def test_output_folder_and_game_files(game, run_cli):
    before = {p: p.read_bytes() for p in game.vanilla.rglob("*") if p.is_file()}
    _ok(_add(run_cli, game, "vehicle", "car.dff", "--like", "testcar", "--name", "again", txd="car.txd"))
    r = _add(run_cli, game, "vehicle", "car.dff", "--like", "testcar", "--name", "again", txd="car.txd")
    assert r.json["error"]["code"] == "EXISTS" and "--force" in r.json["error"]["hint"]
    stray = game.work / "out" / "addon" / "again" / "stray.txt"
    stray.write_text("old", encoding="utf-8")
    env = _ok(_add(run_cli, game, "vehicle", "car.dff", "--like", "testcar", "--name", "again", "--out", "again",
                   "--force", txd="car.txd"))
    assert not stray.exists() and sorted(p.name for p in Path(env["out"]).iterdir()) == \
        ["again.dff", "again.fxt", "again.txd", "again.txt"]
    # from the game's own archives
    env = _ok(run_cli(["mod", "add", "vehicle", "--dff", "dff:testcar", "--txd", "txd:testcar", "--like", "400",
                       "--name", "copy1", "--profile", "vanilla"]))
    from .conftest import dff, txd
    assert (Path(env["out"]) / "copy1.dff").read_bytes() == dff("body", embedded_col=True)
    assert (Path(env["out"]) / "copy1.txd").read_bytes() == txd(["body"])
    assert run_cli(["mod", "add", "vehicle", "--dff", "dff:nosuch", "--txd", "txd:testcar", "--like", "400",
                    "--name", "copy2", "--profile", "vanilla"]).json["error"]["code"] == "NOT_FOUND"
    assert {p: p.read_bytes() for p in game.vanilla.rglob("*") if p.is_file()} == before


def test_ped(game, run_cli):
    env = _ok(_add(run_cli, game, "ped", "ped.dff", "--like", "male01", "--name", "newped", txd="ped.txd"))
    line = _readme(env)[0]
    from .conftest import PEDS_IDE
    mid = env["id"].split(":")[1]
    _same_but(line, PEDS_IDE.splitlines()[2], {0: mid, 1: "newped", 2: "newped"})
    ins = _ok(run_cli(["mod", "inspect", env["out"], "--profile", "vanilla"]))
    assert ("ide", env["id"], "new-id") in {(r[0], r[1], r[2]) for r in ins["rows"]}
    env = _ok(_add(run_cli, game, "ped", "car.dff", "--like", "7", "--name", "noskin", "--dry-run", txd="car.txd"))
    assert any(w.startswith("NO_SKIN") for w in env["warn"])


def test_weapon(game, run_cli):
    env = _ok(_add(run_cli, game, "weapon", "gun.dff", "--like", "colt45", "--name", "newgun", "--col",
                   str(game.inp / "one.col"), txd="gun.txd"))
    out = Path(env["out"])
    mid = int(env["id"].split(":")[1])
    assert (out / "data" / "maps" / "newgun.ide").read_text(encoding="latin-1").splitlines()[1:] == \
        ["weap", f"{mid}, newgun, newgun, colt45, 1, 30, 0", "end"]
    lines = _readme(env)
    assert lines[0] == "IDE DATA\\MAPS\\newgun.ide" and len(lines) == 5
    assert all(ln.startswith("$ PISTOL") and f"\t{mid}\t" in ln for ln in lines[1:])
    assert {w.split(":")[0] for w in env["warn"]} >= {"WEAPON_TYPE", "COL_RENAMED"}
    from satk.formats.col import iter_col
    assert [m.name for m in iter_col((out / "newgun.col").read_bytes())] == ["newgun"]
    ins = _ok(run_cli(["mod", "inspect", str(out), "--profile", "vanilla"]))
    got = {(r[0], r[1], r[2]) for r in ins["rows"]}
    assert ("ide", f"model:{mid}", "new-id") in got and ("data", "gta.dat:IDE data/maps/newgun.ide", "add") in got
    # melee donor: the 0xA3 mark survives in the readme
    env = _ok(_add(run_cli, game, "weapon", "gun.dff", "--like", "brass", "--name", "knuckle2", txd="gun.txd"))
    raw = (Path(env["out"]) / "knuckle2.txt").read_bytes()
    assert b"\r\n\xa3 BRASSKNUCKLE\tMELEE" in raw
    env = _ok(_add(run_cli, game, "weapon", "gun.dff", "--like", "colt45", "--name", "laser", "--weapon-type", "LASER",
                   "--dry-run", txd="gun.txd"))
    assert all("LASER" in r[1] for r in env["rows"] if r[0] == "weapon.dat")
    assert any(w.startswith("NEW_WEAPON_TYPE") for w in env["warn"])
    env = _ok(_add(run_cli, game, "weapon", "gun.dff", "--like", "colt45", "--name", "plain", "--weapon-type", "none",
                   "--dry-run", txd="gun.txd"))
    assert [r[0] for r in env["rows"]] == ["data/maps/plain.ide", "gta.dat"]
    r = _add(run_cli, game, "weapon", "gun.dff", "--like", "colt45", "--name", "dup", "--weapon-type", "rocket",
             txd="gun.txd")
    assert r.json["error"]["code"] == "EXISTS"


def test_object(game, run_cli):
    env = _ok(run_cli(["mod", "add", "object", "--dff", str(game.inp / "box.dff"), "--col",
                       str(game.inp / "multi.col"), "--like", "barrel", "--name", "newbarrel", "--profile", "vanilla"]))
    out = Path(env["out"])
    assert env["txd"] == "dynbarrels" and "newbarrel.txd" not in env["files"]
    mid = env["id"].split(":")[1]
    assert (out / "data" / "maps" / "newbarrel.ide").read_text(encoding="latin-1").splitlines()[2] == \
        f"{mid}, newbarrel, dynbarrels, 40, 2162816"
    obj = (out / "data" / "object.dat").read_bytes().decode("latin-1")
    first = OBJECT_DAT.splitlines(keepends=True)[1]
    assert obj == OBJECT_DAT.replace(first, first + first.replace("barrel,", "newbarrel,", 1))
    from satk.formats.col import iter_col
    assert [m.name for m in iter_col((out / "newbarrel.col").read_bytes())] == ["newbarrel"]
    assert any("extracted" in w for w in env["warn"])
    ins = _ok(run_cli(["mod", "inspect", str(out), "--profile", "vanilla"]))
    got = {(r[0], r[1], r[2]) for r in ins["rows"]}
    assert {("ide", f"model:{mid}", "new-id"), ("data", "object.dat:newbarrel", "add"), ("col", "col:newbarrel", "add")
            } <= got
    lint = _ok(run_cli(["asset", "lint", str(out), "--no-index"]))
    assert lint["summary"]["fatal"] == 0
    r = run_cli(["mod", "add", "object", "--dff", str(game.inp / "box.dff"), "--col", str(game.inp / "multi.col"),
                 "--like", "testcar", "--name", "nb2", "--profile", "vanilla"])
    assert r.json["error"]["code"] == "BAD_PARAMS"
    env = _ok(run_cli(["mod", "add", "object", "--dff", str(game.inp / "box.dff"), "--like", "1000", "--name", "nb3",
                       "--profile", "vanilla", "--dry-run"]))
    assert any(w.startswith("NO_COL") for w in env["warn"])


def _lod_args(game, *extra):
    lod = game.inp / "lodnewbarrel.dff"
    lod.write_bytes((game.inp / "box.dff").read_bytes())
    return ["--like", "barrel", "--name", "newbarrel", "--lod-dff", str(lod), *extra]


def test_object_lod_gets_its_own_ide_line_and_files(game, run_cli):
    env = _ok(_add(run_cli, game, "object", "box.dff", *_lod_args(game)))
    out = Path(env["out"])
    mid = int(env["id"].split(":")[1])
    lod = env["lod"]
    assert lod["name"] == "lodnewbarrel" and lod["draw"] == 800 and lod["txd"] == "dynbarrels"
    lod_id = int(lod["id"].split(":")[1])
    assert lod_id == mid + 1
    ide = (out / "data" / "maps" / "newbarrel.ide").read_text(encoding="latin-1").splitlines()
    assert ide == ["# satk add-on newbarrel", "objs", f"{mid}, newbarrel, dynbarrels, 40, 2162816",
                   f"{lod_id}, lodnewbarrel, dynbarrels, 800, 0", "end"]
    assert (out / "lodnewbarrel.dff").read_bytes() == (game.inp / "box.dff").read_bytes()
    assert "lodnewbarrel.txd" not in env["files"] and "data/maps/newbarrel.ipl" not in env["files"]
    ins = _ok(run_cli(["mod", "inspect", str(out), "--profile", "vanilla"]))
    got = {(r[0], r[1]) for r in ins["rows"]}
    assert ("ide", f"model:{mid}") in got and ("ide", f"model:{lod_id}") in got


def test_object_lod_txd_name_and_explicit_id(game, run_cli):
    lod_txd = game.inp / "lodtex.txd"
    lod_txd.write_bytes((game.inp / "car.txd").read_bytes())
    env = _ok(_add(run_cli, game, "object", "box.dff", *_lod_args(game, "--lod-txd", str(lod_txd), "--lod-name",
                                                                  "lodbarrel2", "--id", "20000")))
    assert env["id"] == "model:20000" and env["lod"] == {"id": "model:20001", "name": "lodbarrel2",
                                                          "txd": "lodbarrel2", "draw": 800}
    out = Path(env["out"])
    assert (out / "lodbarrel2.txd").is_file() and (out / "lodbarrel2.dff").is_file()
    ide = (out / "data" / "maps" / "newbarrel.ide").read_text(encoding="latin-1").splitlines()
    assert ide[3] == "20001, lodbarrel2, lodbarrel2, 800, 0"


def test_object_place_writes_an_ipl_with_the_lod_link(game, run_cli):
    env = _ok(_add(run_cli, game, "object", "box.dff", *_lod_args(game, "--place", "2495,-1687,13.5")))
    out = Path(env["out"])
    mid = int(env["id"].split(":")[1])
    ipl = (out / "data" / "maps" / "newbarrel.ipl").read_text(encoding="latin-1").splitlines()
    assert ipl[1] == "inst" and ipl[-1] == "end"
    assert ipl[2] == f"{mid}, newbarrel, 0, 2495, -1687, 13.5, 0, 0, 0, 1, 1"          # the LOD is the next inst
    assert ipl[3] == f"{mid + 1}, lodnewbarrel, 0, 2495, -1687, 13.5, 0, 0, 0, 1, -1"
    readme = [ln for ln in (out / "newbarrel.txt").read_bytes().decode("latin-1").splitlines() if ln.startswith(
        ("IDE", "IPL"))]
    assert readme == [r"IDE DATA\MAPS\newbarrel.ide", r"IPL DATA\MAPS\newbarrel.ipl"]
    solo = _ok(_add(run_cli, game, "object", "box.dff", "--like", "barrel", "--name", "solo", "--place", "1,2,3"))
    ipl = (Path(solo["out"]) / "data" / "maps" / "solo.ipl").read_text(encoding="latin-1").splitlines()
    assert ipl[2].endswith(", 1, -1") and len(ipl) == 4


def test_object_lod_errors(game, run_cli):
    r = _add(run_cli, game, "vehicle", "car.dff", "--like", "testcar", "--name", "nv", "--lod-dff",
             str(game.inp / "box.dff"), txd="car.txd")
    assert r.json["error"]["code"] == "BAD_PARAMS"
    r = _add(run_cli, game, "object", "box.dff", "--like", "barrel", "--name", "newbarrel", "--lod-txd",
             str(game.inp / "car.txd"))
    assert r.json["error"]["code"] == "BAD_PARAMS"
    same = game.inp / "newbarrel.dff"
    same.write_bytes((game.inp / "box.dff").read_bytes())
    r = _add(run_cli, game, "object", "box.dff", "--like", "barrel", "--name", "newbarrel", "--lod-dff", str(same))
    assert r.json["error"]["code"] == "BAD_PARAMS" and "own" in r.json["error"]["msg"]
    r = _add(run_cli, game, "object", "box.dff", "--like", "barrel", "--name", "nb9", "--place", "1,2")
    assert r.json["error"]["code"] == "BAD_PARAMS"


def _manifest(root: Path) -> None:
    files = sorted(p for p in root.rglob("*") if p.is_file())
    (root / "MANIFEST.sha256").write_text("".join(
        f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {os.path.relpath(p, root).replace('/', chr(92))}\n"
        for p in files), encoding="latin-1")


def test_ids_and_names_from_the_profile_index(game, run_cli, builders, tmp_path):
    """With a built index the modloader mods of the profile count: their ids, model names and files."""
    from satk.core.errors import SatkError
    from satk.index.api import IndexDB, override_index
    from satk.index.build import build
    from satk.index.layers import HashCache

    root = game.installed
    _manifest(root)
    builders.write(root, "modloader/pack/vehicles.ide", "cars\n404, modcar, modcar, car, MODCAR, MODCAR, null, normal, "
                                                         "10, 0, 0, -1, 0.7, 0.7, 0\nend\n")
    builders.write(root, "modloader/pack/modfile.txd", builders.txd(["a"]))
    out = tmp_path / "idx" / "installed.sqlite"
    build("installed", jobs=1, out=out, root=root, dat_files=["data/default.dat", "data/gta.dat"], img_order="engine",
          vanilla_manifest=root / "MANIFEST.sha256", hashcache=HashCache(tmp_path / "hc.sqlite"))
    db = IndexDB("installed", out)

    def factory(profile: str):
        if profile == "installed":
            return db
        raise SatkError("INDEX_MISSING", f"no index for {profile}")

    try:
        with override_index(factory):
            env = _ok(run_cli(["mod", "add", "vehicle", "--dff", str(game.inp / "car.dff"), "--txd",
                               str(game.inp / "car.txd"), "--like", "testcar", "--name", "fresh", "--dry-run"]))
            assert env["id"] == "model:405" and env["profile"] == "installed"
            assert not any(w.startswith("NO_INDEX") for w in env.get("warn", []))
            for name in ("modcar", "modfile"):
                r = run_cli(["mod", "add", "vehicle", "--dff", str(game.inp / "car.dff"), "--txd",
                             str(game.inp / "car.txd"), "--like", "testcar", "--name", name, "--dry-run"])
                assert r.json["error"]["code"] == "EXISTS" and "modloader" in r.json["error"]["msg"], name
    finally:
        db.close()


def test_readme_lines_match_mod_loader_patterns(game, run_cli):
    """Every generated readme line is one of the lines Mod Loader's readme readers take."""
    from satk.addon.add import readme_carmods_ok, readme_weapon_ok
    from satk.modinspect.traits import CarcolsTrait, GtaDatTrait, HandlingTrait, IdeTrait

    env = _ok(_add(run_cli, game, "vehicle", "car.dff", "--like", "testcar", "--name", "pat", txd="car.txd"))
    section = None
    for raw in (Path(env["out"]) / "pat.txt").read_bytes().decode("latin-1").splitlines():
        if raw.startswith("# "):
            section = raw[2:]
            continue
        line = trim_config_line(raw)
        if not line:
            continue
        ok = {"vehicles.ide": lambda s: IdeTrait().readme_section(s) == "cars",
              "handling.cfg": lambda s: HandlingTrait().readme_rec(s, 0, frozenset()) is not None,
              "carcols.dat": lambda s: CarcolsTrait().readme_rec(s, 0, frozenset({"pat"})) is not None,
              "carmods.dat": readme_carmods_ok, "gta.dat": lambda s: GtaDatTrait().readme_rec(s, 0, frozenset()),
              "weapon.dat": readme_weapon_ok}[section]
        assert ok(line), (section, raw)


@pytest.mark.parametrize("kind,donor,dff,txd", [("ped", "male01", "ped.dff", "ped.txd"),
                                                ("weapon", "colt45", "gun.dff", "gun.txd")])
def test_dry_run_is_deterministic(game, run_cli, kind, donor, dff, txd):
    a = _add(run_cli, game, kind, dff, "--like", donor, "--name", "det", "--dry-run", txd=txd).out
    b = _add(run_cli, game, kind, dff, "--like", donor, "--name", "det", "--dry-run", txd=txd).out
    assert a == b
