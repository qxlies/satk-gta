"""Lane C2 acceptance through the CLI: exact report of a synthetic mod, handling conflict with a winner,
zip read without extracting, effective line, mod check (satk and an external INU Check stand-in)."""

from __future__ import annotations

import os
import sys
import zipfile
from pathlib import Path

import pytest

from .conftest import anim_line, boat_line, car_line, make_mod, rw_stub, zip_of

HANDLING_MOD = "\n".join([
    car_line("TESTCAR"),                       # unchanged
    car_line("FASTCAR", 1250.0, 260.0),        # mass and max_vel changed
    car_line("TESTBOAT", 2000.0, 150.0),       # unchanged standard line ...
    boat_line("TESTBOAT", 0.9),                # ... with a changed boat line
    car_line("NEWCAR", 1600.0),                # new id
]) + "\n"                                      # no '^ 0' line: the anim record is dropped


def _mod(tmp: Path) -> Path:
    """The acceptance mod: 3 files, 5 data lines + 1 readme line."""
    return make_mod(tmp / "fastmod", {
        "cars/fastcar.dff": rw_stub(tag=b"mod"),
        "data/handling.cfg": HANDLING_MOD,
        "readme.txt": "Fast cars pack.\r\nPut the folder into modloader.\r\ntestcar, 3,3, 0,0\r\n",
    })


def test_inspect_synthetic_mod_exact(games, tmp_path, run_cli):
    r = run_cli(["mod", "inspect", str(_mod(tmp_path)), "--json"])
    assert r.code == 0, r.out + r.err
    env = r.json
    assert env["cols"] == ["kind", "target", "change", "by", "detail"]
    rows = [r[:4] for r in env["rows"]]
    assert rows == [
        ["dff", "dff:fastcar", "replace", "cars/fastcar.dff"],
        ["data", "handling:FASTCAR", "modify", "data/handling.cfg:2"],
        ["data", "handling:TESTBOAT", "modify", "data/handling.cfg:3"],
        ["data", "handling:NEWCAR", "add", "data/handling.cfg:5"],
        ["data", "handling:^0", "remove", "data/handling.cfg"],
        ["data", "carcols:testcar", "modify", "readme.txt:3"],
    ]
    det = {r[1]: r[4] for r in env["rows"]}
    assert det["dff:fastcar"].startswith("model 401 fastcar in models/gta3.img")
    assert det["handling:FASTCAR"] == "mass 1200->1250, max_vel 250->260"
    assert det["handling:TESTBOAT"] == "boat.thrust_y 0.6->0.9"
    assert det["carcols:testcar"] == "colours 1,2,2,1->3,3,0,0"
    assert env["changes"] == {"replace": 1, "add": 1, "modify": 3, "remove": 1}
    assert env["mod"] == "fastmod" and env["files"] == 3


def test_inspect_zip_without_extracting(games, tmp_path, run_cli, monkeypatch):
    folder = _mod(tmp_path)
    z = zip_of(folder, tmp_path / "fastmod.zip", top="Fast Mod")

    def boom(*a, **k):
        raise AssertionError("the inspector must not extract")

    monkeypatch.setattr(zipfile.ZipFile, "extract", boom)
    monkeypatch.setattr(zipfile.ZipFile, "extractall", boom)
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    rz = run_cli(["mod", "inspect", str(z), "--json"])
    rd = run_cli(["mod", "inspect", str(folder), "--json"])
    assert rz.code == 0, rz.out + rz.err
    assert rz.json["rows"] == rd.json["rows"]
    assert rz.json["mod"] == "Fast Mod" and rz.json["source"].startswith("zip ")
    assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")) == before


def _installed_mod(inst: Path, name: str, mass: float) -> None:
    text = "\n".join([car_line("TESTCAR"), car_line("FASTCAR", mass, 250.0), car_line("TESTBOAT", 2000.0, 150.0),
                      boat_line("TESTBOAT"), anim_line(0)]) + "\n"
    make_mod(inst / "modloader" / name, {"data/handling.cfg": text})


def test_handling_conflict_two_mods_tie(games, run_cli):
    _van, inst = games
    _installed_mod(inst, "aaa", 1300.0)
    _installed_mod(inst, "bbb", 1400.0)
    r = run_cli(["mod", "conflicts", "--json"])
    assert r.code == 0, r.out + r.err
    assert r.json["rows"] == [["handling:FASTCAR", "data", "aaa", "bbb",
                               "tie: the value of the mod installed first wins (counts 1/1)"]]
    assert r.json["mods"] == ["aaa(50)", "bbb(50)"]
    e = run_cli(["mod", "effective", "handling", "401", "--json"]).json
    assert e["key"] == "FASTCAR" and e["from"] == "aaa/data/handling.cfg" and e["mode"] == "merge"
    assert e["line"].startswith("FASTCAR 1300.0 ")
    assert e["changes"] == "mass 1200->1300"


def test_handling_conflict_less_common_and_priority_order(games, run_cli):
    _van, inst = games
    _installed_mod(inst, "aaa", 1300.0)
    _installed_mod(inst, "bbb", 1300.0)
    _installed_mod(inst, "ccc", 1400.0)
    # priority puts ccc first in install order; it would also win a tie, but 1400 is less common anyway
    (inst / "modloader" / "modloader.ini").write_text(
        "[Folder.Config]\nProfile=Default\n[Profiles.Default.Priority]\nccc=10\n", encoding="utf-8")
    r = run_cli(["mod", "conflicts", "--json"]).json
    assert r["mods"] == ["ccc(10)", "aaa(50)", "bbb(50)"]
    assert r["rows"] == [["handling:FASTCAR", "data", "ccc", "aaa,bbb",
                          "the least common value wins (counts 1/2)"]]


def test_replaced_file_winner_by_priority_then_name(games, run_cli):
    _van, inst = games
    for name in ("zz", "longname", "mid"):
        make_mod(inst / "modloader" / name, {"fastcar.dff": rw_stub(tag=name.encode())})
    r = run_cli(["mod", "conflicts", "--json"]).json
    # equal priority: shorter names install first, so the longest name wins
    assert r["rows"][0][:4] == ["dff:fastcar", "dff", "longname", "zz,mid"]
    (inst / "modloader" / "modloader.ini").write_text("[Profiles.Default.Priority]\nzz=70\n", encoding="utf-8")
    r = run_cli(["mod", "conflicts", "--json"]).json
    assert r["rows"][0][:5] == ["dff:fastcar", "dff", "zz", "mid,longname", "priority 70 > 50"]


def test_partial_data_file_removes_records(games, tmp_path, run_cli):
    _van, inst = games
    _installed_mod(inst, "full", 1300.0)
    make_mod(inst / "modloader" / "part", {"handling.cfg": car_line("FASTCAR", 1400.0, 250.0) + "\n"})
    r = run_cli(["mod", "conflicts", "--json"]).json
    # FASTCAR differs in both (a tie: 'full' and 'part' have equal length, 'full' sorts first and installs first);
    # TESTCAR/TESTBOAT/^0 are only dropped by 'part' - one mod, so a DROPS warning instead of a conflict row
    assert r["rows"] == [["handling:FASTCAR", "data", "full", "part",
                          "tie: the value of the mod installed first wins (counts 1/1)"]]
    assert any(w.startswith("DROPS: part/handling.cfg lacks 3 game record(s)") for w in r.get("warn", []))
    e = run_cli(["mod", "effective", "handling", "TESTCAR", "--json"]).json
    assert e["result"] == "removed" and "part/handling.cfg" in e["why"]


def test_new_ids_and_override(games, tmp_path, run_cli):
    mod = make_mod(tmp_path / "newveh", {
        "data/vehicles.ide": "cars\n" + "\n".join([
            "400, testcar, testcar, car, TESTCAR, TESTCAR, null, normal, 10, 0, 0, -1, 0.7, 0.7, 0",
            "401, othercar, othercar, car, FASTCAR, FASTCAR, null, executive, 5, 0, 0, -1, 0.7, 0.7, 0",
            "402, testboat, testboat, boat, TESTBOAT, TESTBOA, null, ignore, 10, 0, 0",
            "20001, bigcar, bigcar, car, TESTCAR, BIGCAR, null, normal, 10, 0, 0, -1, 0.7, 0.7, 0"]) + "\nend\n",
        "bigcar.dff": rw_stub(), "bigcar.txd": rw_stub(0x16), "notes.pdf": b"%PDF",
        "data/maps/extra.ide": "objs\n15000, newobj, newobj, 100, 0\nend\n",
    })
    env = run_cli(["mod", "inspect", str(mod), "--json"]).json
    got = {(r[1], r[2]) for r in env["rows"]}
    assert ("model:401", "override-id") in got
    assert ("model:20001", "new-id") in got
    assert ("dff:bigcar", "add") in got and ("txd:bigcar", "add") in got
    assert ("file:extra.ide", "ignored") in got          # no gta.dat line loads it
    assert ("*.pdf", "ignored") in got
    assert env["new_ids"] == [20001]
    big = next(r for r in env["rows"] if r[1] == "model:20001")
    assert "limit adjuster" in big[4]
    dff = next(r for r in env["rows"] if r[1] == "dff:bigcar")
    assert "IDE ID 20001" in dff[4]
    chk = run_cli(["mod", "check", str(mod), "--no-lint", "--json"]).json
    rules = {r[0] for r in chk["rows"]}
    assert {"mod.id_collision", "mod.id_limit", "mod.not_loaded"} <= rules


def test_inspect_single_img(games, tmp_path, run_cli):
    from .conftest import img_v2

    img = tmp_path / "cars.img"
    img.write_bytes(img_v2([("testcar.dff", rw_stub(tag=b"x")), ("brandnew.txd", rw_stub(0x16)),
                            ("fastcar.dff", rw_stub())]))      # fastcar.dff: the same bytes as the game's
    env = run_cli(["mod", "inspect", str(img), "--json"]).json
    assert [r[:4] for r in env["rows"]] == [["txd", "txd:brandnew", "add", "brandnew.txd"],
                                            ["dff", "dff:testcar", "replace", "testcar.dff"]]
    assert env["unchanged"] == 1 and env["files"] == 3


def test_check_inu_missing_and_fake(games, tmp_path, run_cli, monkeypatch):
    mod = _mod(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path / "nothing"))
    r = run_cli(["mod", "check", str(mod), "--engine", "inu", "--json"])
    assert r.code == 3 and r.json["error"]["code"] == "NOT_READY"
    fake = tmp_path / "fake_inu.py"
    fake.write_text(
        "import json, sys\n"
        "print(json.dumps({'version': 3, 'issues': [\n"
        " {'file': 'cars/fastcar.dff', 'severity': 'error', 'message': 'bad normals', 'code': 'geom.normals'},\n"
        " {'path': 'cars/fastcar.txd', 'level': 'warning', 'text': 'no mipmaps'}]}))\n", encoding="utf-8")
    monkeypatch.setenv("SATK_INU_CHECK", f'"{sys.executable}"')
    monkeypatch.setenv("SATK_INU_CHECK_ARGS", f'"{fake}" --json {{path}}')
    r = run_cli(["mod", "check", str(mod), "--engine", "inu", "--json"])
    assert r.code == 0, r.out + r.err
    assert r.json["rows"] == [["inu.geom.normals", "error", "cars/fastcar.dff", "bad normals", "dff:fastcar"],
                              ["inu", "warn", "cars/fastcar.txd", "no mipmaps", "txd:fastcar"]]
    assert r.json["adapter"] == "inu-adapter/1" and r.json["format"] == "json v3"


def test_effective_save_writes_merged_file(games, run_cli):
    _van, inst = games
    _installed_mod(inst, "aaa", 1300.0)
    _installed_mod(inst, "bbb", 1400.0)
    env = run_cli(["mod", "effective", "handling", "--save", "--json"]).json
    assert env["rows"] == [["data/handling.cfg", "merge", 3, 1, 0, 0, "aaa/data/handling.cfg, bbb/data/handling.cfg"]]
    out = Path(env["saved"][0])
    text = out.read_text(encoding="latin-1")
    assert text.splitlines()[1].startswith("FASTCAR 1300.0") and text.rstrip().endswith(";the end")
    assert os.path.commonpath([out, Path(os.environ["SATK_HOME"])]) == str(Path(os.environ["SATK_HOME"]))


@pytest.mark.parametrize("bad", [["mod", "inspect", "nope_missing_dir"], ["mod", "effective", "ide"]])
def test_errors(games, run_cli, bad):
    r = run_cli(bad + ["--json"])
    assert r.code != 0 and r.json["ok"] is False


def test_override_mode_removed_record(games, run_cli):
    _van, inst = games
    make_mod(inst / "modloader" / "part", {"handling.cfg": car_line("FASTCAR", 1400.0, 250.0) + "\n"})
    e = run_cli(["mod", "effective", "handling", "TESTCAR", "--json"]).json
    assert e["mode"] == "override" and e["result"] == "removed"
    assert e["why"] == "part/handling.cfg replaces the game's file and lacks it"


def test_readme_line_loses_tie_to_a_file(games, run_cli):
    _van, inst = games
    _installed_mod(inst, "file", 1400.0)
    make_mod(inst / "modloader" / "readme", {"readme.txt": "Handling line:\n" + car_line("FASTCAR", 1300.0, 250.0)})
    r = run_cli(["mod", "conflicts", "--json"]).json
    assert r["rows"] == [["handling:FASTCAR", "data", "file", "readme",
                          "tie: the value of the mod installed first wins (counts 1/1)"]]
    e = run_cli(["mod", "effective", "handling", "fastcar", "--json"]).json
    assert e["from"] == "file/data/handling.cfg"
    assert [c[0] for c in e["candidates"]["rows"]] == ["game", "file/data/handling.cfg", "readme/readme.txt:2"]
