"""``satk data get|patch|explain`` on the synthetic game of conftest.py."""

from __future__ import annotations

from pathlib import Path

from .conftest import HANDLING, WEAPON


def _ok(r):
    assert r.code == 0, r.out + r.err
    return r.json


def test_get_handling_by_model_name_id_and_handling_id(game, run_cli):
    for key in ("testcar", "model:400", "400", "TESTCAR", "handling:testcar"):
        env = _ok(run_cli(["data", "get", "handling", key]))
        assert env["id"] == "handling:TESTCAR" and env["models"] == ["model:400 testcar"], key
    env = _ok(run_cli(["data", "get", "handling", "testcar"]))
    rows = {r[0]: r for r in env["rows"]}
    assert len(env["rows"]) == 35 and env["file"] == "data/handling.cfg" and env["lines"] == [3]
    assert rows["fMass"] == ["fMass", 1400.0, "kg"]
    assert rows["nDriveType"][2] == "four-wheel drive"
    assert rows["modelFlags"][1:] == ["40002004", "IS_LOW|DOUBLE_EXHAUST|FORCE_GROUND_CLEARANCE"]
    assert rows["frontLights"][2] == "small"


def test_get_handling_extra_lines_and_fields(game, run_cli):
    env = _ok(run_cli(["data", "get", "handling", "tbike", "--field", "fMass", "bike.fMaxLean", "maxVelocity"]))
    assert env["cols"] == ["field", "value", "unit", "meaning"] and env["kinds"] == ["car", "bike"]
    assert [r[0] for r in env["rows"]] == ["fMass", "fMaxVelocity", "bike.fMaxLean"]
    env = _ok(run_cli(["data", "get", "handling", "tplane"]))
    assert env["total"] == 35 + 21 and env["rows"][-1][0] == "flying.vecSpeedRes.z"
    r = run_cli(["data", "get", "handling", "tplane", "--field", "bike.fMaxLean"])
    assert r.code != 0 and r.json["error"]["code"] in ("NOT_FOUND", "BAD_PARAMS")
    r = run_cli(["data", "get", "handling", "testcr"])
    assert r.json["error"]["code"] == "NOT_FOUND" and "TESTCAR" in r.json["error"]["did_you_mean"]
    assert run_cli(["data", "get", "handling", "male01"]).json["error"]["code"] == "BAD_PARAMS"


def test_get_weapon_carcols_peds(game, run_cli):
    env = _ok(run_cli(["data", "get", "weapon", "colt45"]))
    assert env["id"] == "weapon:PISTOL" and env["weapon_id"] == 22 and env["cols"] == ["field", "poor", "std", "pro",
                                                                                         "cop", "unit"]
    flags = next(r for r in env["rows"] if r[0] == "flags")
    assert flags[1:5] == ["3033", "3033", "3833", "7031"] and "pro: +TWIN_PISTOL" in flags[5]
    for key in ("PISTOL", "weapon:22", "22", "model:346", "weapon:pistol"):
        assert _ok(run_cli(["data", "get", "weapon", key]))["id"] == "weapon:PISTOL", key
    melee = _ok(run_cli(["data", "get", "weapon", "brass"]))
    assert melee["cols"] == ["field", "value", "unit"] and melee["kind"] == "melee"
    aim = _ok(run_cli(["data", "get", "weapon", "aim:colt45"]))
    assert aim["id"] == "aim:colt45" and aim["rows"][0][:2] == ["aimX", 0.2]
    assert run_cli(["data", "get", "weapon", "99"]).json["error"]["code"] == "NOT_FOUND"
    cc = _ok(run_cli(["data", "get", "carcols", "testcar"]))
    assert cc["rows"] == [[0, "1,0", "#f5f5f5 #000000", "white, black"], [1, "2,1", "#2a77a1 #f5f5f5", "blue, white"]]
    assert _ok(run_cli(["data", "get", "carcols", "tboat"]))["section"] == "car4"
    assert run_cli(["data", "get", "carcols", "tplane"]).json["error"]["code"] == "NOT_FOUND"
    peds = _ok(run_cli(["data", "get", "peds", "male01"]))
    rows = {r[0]: r[1:] for r in peds["rows"]}
    assert rows["pedStats"][0] == "STAT_SENSIBLE_GUY" and rows["radio1"][0] == 1
    assert rows["pedstats.flee_distance"] == [17.0, "m"] and peds["pedstats"] == "data/pedstats.dat:3"


def test_explain(game, run_cli):
    env = _ok(run_cli(["data", "explain", "handling", "tractionBias"]))
    assert env["name"] == "fTractionBias" and env["mta"] == "tractionBias" and env["col"] == "L"
    env = _ok(run_cli(["data", "explain", "handling", "modelFlags"]))
    assert len(env["bits"]) == 32 and env["bits"][2][:2] == ["0x4", "IS_LOW"]
    assert _ok(run_cli(["data", "explain", "handling", "is_low"]))["value"] == "0x4"
    assert _ok(run_cli(["data", "explain", "handling", "boat.fThrustY"]))["kind"] == "boat"
    assert _ok(run_cli(["data", "explain", "handling", "nMonetaryValue"]))["mta"] == "monetary (read-only in MTA)"
    lst = _ok(run_cli(["data", "explain", "handling"]))
    assert lst["n"] == 20 and lst["total"] == 35 + 15 + 14 + 21 and lst["next"] == "20"
    assert _ok(run_cli(["data", "explain", "weapon", "animLoopEnd"]))["mta_unit"].startswith("seconds")
    assert _ok(run_cli(["data", "explain", "peds", "pedstats.fear"]))["key"] == "fear"
    r = run_cli(["data", "explain", "handling", "fMas"])
    assert r.json["error"]["code"] == "NOT_FOUND" and "fMass" in r.json["error"]["did_you_mean"]


def test_patch_handling_modloader_full_mta(game, run_cli):
    env = _ok(run_cli(["data", "patch", "handling", "testcar", "fMass=1500", "fTractionMultiplier=0.8"]))
    assert env["rows"] == [["fMass", "1400.0", "1500.0"], ["fTractionMultiplier", "0.70", "0.80"]]
    out = Path(env["out"])
    assert out.name == "testcar-handling-modloader" and env["files"] == ["testcar-handling-modloader.txt"]
    text = (out / "testcar-handling-modloader.txt").read_bytes().decode("latin-1")
    data = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
    assert data == [env["new"][0]] and env["new"][0].startswith("TESTCAR     1500.0")
    # Mod Loader would read that line and change exactly these two fields
    ins = _ok(run_cli(["mod", "inspect", str(out), "--profile", "vanilla"]))
    assert [r[:3] for r in ins["rows"]] == [["data", "handling:TESTCAR", "modify"]]
    assert "mass" in ins["rows"][0][4] and "traction_mult" in ins["rows"][0][4]
    # full copy: only that line differs
    env = _ok(run_cli(["data", "patch", "handling", "tbike", "bike.fMaxLean=40", "--target", "full"]))
    new = (Path(env["out"]) / "data" / "handling.cfg").read_bytes().decode("latin-1")
    assert new.replace(env["new"][0], env["old"][0]) == HANDLING and new != HANDLING
    # mta
    env = _ok(run_cli(["data", "patch", "handling", "400", "maxVelocity=260", "nDriveType=R", "--target", "mta"]))
    lua = (Path(env["out"]) / "handling.lua").read_text(encoding="latin-1")
    assert 'setModelHandling(400, "maxVelocity", 260) -- testcar' in lua and '"driveType", "rwd"' in lua
    assert env["lua"] == [ln for ln in lua.splitlines() if not ln.startswith("--")]


def test_patch_errors_and_dry_run(game, run_cli):
    work = game.work
    env = _ok(run_cli(["data", "patch", "handling", "testcar", "fMass=1500", "--dry-run"]))
    assert env["dry_run"] is True and not (work / "out" / "addon").exists()
    for args, code in ((["fMass"], "BAD_PARAMS"), (["fMas=1"], "NOT_FOUND"), (["nDriveType=X"], "BAD_PARAMS"),
                       (["bike.fMaxLean=1"], "NOT_FOUND"), (["fMass=abc"], "BAD_PARAMS")):
        r = run_cli(["data", "patch", "handling", "testcar", *args])
        assert r.code != 0 and r.json["error"]["code"] == code, (args, r.json)
    r = run_cli(["data", "patch", "handling", "testcar", "fTractionBias=1.5", "--dry-run"])
    assert any(w.startswith("OUT_OF_RANGE") for w in r.json["warn"])
    r = run_cli(["data", "patch", "handling", "testcar", "fMass=1", "--name", "../x"])
    assert r.json["error"]["code"] == "BAD_PARAMS"


def test_patch_weapon(game, run_cli):
    env = _ok(run_cli(["data", "patch", "weapon", "PISTOL", "damage=30", "--skill", "pro"]))
    assert env["rows"] == [["damage", "25", "30"]] and len(env["new"]) == 1
    text = (Path(env["out"]) / env["files"][0]).read_bytes().decode("latin-1")
    assert env["new"][0] in text.splitlines()
    env = _ok(run_cli(["data", "patch", "weapon", "colt45", "damage=40", "--target", "full"]))
    assert len(env["new"]) == 4 and [r[0] for r in env["rows"]][:2] == ["damage[poor]", "damage[std]"]
    new = (Path(env["out"]) / "data" / "weapon.dat").read_bytes().decode("latin-1")
    for o, n in zip(env["old"], env["new"]):
        new = new.replace(n, o)
    assert new == WEAPON
    env = _ok(run_cli(["data", "patch", "weapon", "brass", "weaponRange=2.0", "--target", "mta"]))
    assert env["lua"] == ['setWeaponProperty(1, "std", "weapon_range", 2.0) -- BRASSKNUCKLE weaponRange']
    melee = (Path(_ok(run_cli(["data", "patch", "weapon", "brass", "numCombos=2"]))["out"]))
    raw = next(melee.glob("*.txt")).read_bytes()
    assert b"\n\xa3 BRASSKNUCKLE" in raw                    # the melee mark stays the 0xA3 byte
    env = _ok(run_cli(["data", "patch", "weapon", "aim:colt45", "aimX=0.3"]))
    assert env["files"] == ["data/weapon.dat"] and env["warn"][0].startswith("README_UNSUPPORTED")
    assert run_cli(["data", "patch", "weapon", "PISTOL", "damage=1", "--skill", "ultra"]).json["error"]["code"] == \
        "BAD_PARAMS"
    assert run_cli(["data", "patch", "weapon", "PISTOL", "weaponType=X"]).json["error"]["code"] == "BAD_PARAMS"
