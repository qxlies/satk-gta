"""CLI of satk.colgen (col gen/check/surface/derive) in an isolated workspace with synthetic DFFs."""

from __future__ import annotations

from pathlib import Path

import pytest

from satk.core.registry import all_ops

pytest.importorskip("numpy")


def test_operations_are_cli_only_and_grouped():
    ops = {o.name: o for o in all_ops()}
    for name in ("col.gen", "col.check", "col.surface", "col.derive"):
        assert ops[name].mcp_name is None and ops[name].group == "formats", name
        assert ops[name].module == "satk.colgen.ops"


def test_gen_one_file_then_check_lint_and_formats(satk_home, run_cli, dffs, tmp_path):
    src = tmp_path / "barrel.dff"
    src.write_bytes(dffs.barrel())
    r = run_cli(["col", "gen", str(src), "--mode", "hull", "--max-faces", "32"])
    assert r.code == 0, r.out + r.err
    env = r.json
    assert env["verified"] is True and env["models"] == 1
    name, mode, sph, box, faces, shadow, surf, fit, path = env["rows"][0]
    assert (name, mode, sph, box, shadow) == ("barrel", "hull", 0, 0, 0) and 12 <= faces <= 32
    assert surf.startswith("THICK_METAL_PLATE:") and fit.startswith("out 0.00")
    out = Path(path)
    assert out == satk_home / "work" / "out" / "colgen" / "barrel.col" and out.is_file()
    assert env["surface_from"] == {"keyword": "100%"}
    c = run_cli(["col", "check", str(out), "--sev", "info"]).json
    assert c["models"] == 1 and c["rows"] == [] and c["summary"] == {"ok": 1}
    lint = run_cli(["asset", "lint", str(out), "--sev", "info", "--no-index"]).json
    assert lint["summary"] == {"fatal": 0, "error": 0, "warn": 0, "info": 0}, lint
    d = run_cli(["formats", "dump", str(out)]).json
    assert d["ok"] is True


def test_gen_folder_into_one_archive(satk_home, run_cli, dffs, tmp_path):
    mod = tmp_path / "mod"
    mod.mkdir()
    (mod / "crate.dff").write_bytes(dffs.crate())
    (mod / "ground.dff").write_bytes(dffs.ground())
    (mod / "car.dff").write_bytes(dffs.car())
    (mod / "readme.txt").write_text("x", encoding="utf-8")
    r = run_cli(["col", "gen", str(mod), "--archive", "mymod"])
    assert r.code == 0, r.out + r.err
    env = r.json
    assert env["models"] == 3 and env["verified"] and env["path"].endswith("/mymod.col")
    rows = {row[0]: row for row in env["rows"]}
    assert rows["car"][1] == "spheres" and rows["car"][5] > 0          # auto: vehicle spheres + shadow
    assert rows["crate"][1] == "hull" and rows["crate"][4] == 12
    assert all(row[8] == "-" for row in env["rows"])                   # one archive, no per-model files
    c = run_cli(["col", "check", env["path"]]).json
    assert c["models"] == 3 and c["rows"] == []


def test_gen_options_and_single_named_output(satk_home, run_cli, dffs, tmp_path):
    src = tmp_path / "g.dff"
    src.write_bytes(dffs.ground(20))
    r = run_cli(["col", "gen", str(src), "--mode", "mesh", "--max-faces", "100", "--surface-rule",
                 "*lawn*=GRASS_SHORT_DRY", "--out", "ground_test.col", "--version", "2", "--no-lighting"])
    assert r.code == 0, r.out + r.err
    env = r.json
    assert env["rows"][0][8].endswith("/out/colgen/ground_test.col")
    assert "GRASS_SHORT_DRY" in env["rows"][0][6] and "TARMAC" in env["rows"][0][6]
    assert env["surface_from"].keys() == {"rule", "keyword"}
    assert env["rows"][0][4] <= 100
    r = run_cli(["col", "gen", str(src), "--mode", "boxes", "--max-prims", "3", "--surface", "concrete_damaged"])
    row = r.json["rows"][0]
    assert 1 <= row[3] <= 3 and row[6].startswith("CONCRETE_DAMAGED:")


@pytest.mark.parametrize("argv,code", [
    (["col", "gen", "nowhere/x.dff"], "NOT_FOUND"),
    (["col", "gen", "BAD"], "NOT_FOUND"),
    (["col", "gen", "{src}", "--surface", "tarmak"], "BAD_PARAMS"),
    (["col", "gen", "{src}", "--surface-rule", "nope"], "BAD_PARAMS"),
    (["col", "gen", "{src}", "--max-faces", "2"], "BAD_PARAMS"),
    (["col", "gen", "{src}", "--resolution", "500"], "BAD_PARAMS"),
    (["col", "check", "nowhere.col"], "NOT_FOUND"),
])
def test_errors(satk_home, run_cli, dffs, tmp_path, argv, code):
    src = tmp_path / "c.dff"
    src.write_bytes(dffs.crate())
    r = run_cli([a.replace("{src}", str(src)) for a in argv])
    assert r.code != 0
    assert r.json["error"]["code"] in (code, "INDEX_MISSING"), r.out


def test_gen_rejects_a_mode_typo(satk_home, run_cli, dffs, tmp_path):
    src = tmp_path / "c.dff"
    src.write_bytes(dffs.crate())
    r = run_cli(["col", "gen", str(src), "--mode", "convex"])
    assert r.code == 2


def test_check_fail_on_and_paging(satk_home, run_cli, tmp_path):
    from satk.rw import col as COL

    ms = []
    for i in range(3):
        m = COL.ColModel(version=3, name=f"bad{i}", boxes=[((1, 0, 0), (0, 1, 1), (250, 0, 0, 0))])
        COL.compute_bounds(m)
        m.flags = COL.canonical_flags(m)
        ms.append(m)
    p = tmp_path / "bad.col"
    p.write_bytes(COL.join_models(ms))
    env = run_cli(["col", "check", str(p), "--limit", "2"]).json
    assert env["total"] == 6 and env["n"] == 2 and env["next"] == "2"
    assert env["summary"] == {"error": 3} and env["by_check"] == {"box_inverted": 3, "surface": 3}
    env2 = run_cli(["col", "check", str(p), "--limit", "2", "--cursor", env["next"]]).json
    assert env2["rows"] != env["rows"]
    r = run_cli(["col", "check", str(p), "--fail-on", "error"])
    assert r.code != 0 and r.json["error"]["code"] == "CHECK_FAILED"


def test_surface_lookup_and_list(satk_home, run_cli):
    env = run_cli(["col", "surface", "my_grass", "zz_unknown", "--txd", "whatever"]).json
    rows = {r[0]: r for r in env["rows"]}
    assert rows["my_grass"][2] == "GRASS_MEDIUM_LUSH" and rows["my_grass"][3] == "keyword:grass"
    assert rows["zz_unknown"][1:4] == [0, "DEFAULT", "default"]
    env = run_cli(["col", "surface", "--limit", "500"]).json
    assert env["total"] == 179 and env["rows"][178] == [178, "RAIL_TRACK"]
