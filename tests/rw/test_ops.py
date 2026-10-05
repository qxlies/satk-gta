"""CLI of satk.rw (rw patch/roundtrip, col write/export, img build/diff) in an isolated workspace."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from satk.core.registry import all_ops


def test_operations_are_cli_only_and_grouped():
    ops = {o.name: o for o in all_ops()}
    for name in ("rw.patch", "rw.roundtrip", "col.write", "col.export", "img.build", "img.diff"):
        assert name in ops
        assert ops[name].mcp_name is None and ops[name].group == "formats"


def test_rw_patch_then_formats_dump_sees_the_new_name(satk_home, run_cli, make_dff, tmp_path):
    src = tmp_path / "box.dff"
    src.write_bytes(make_dff(tex="wall", night=True))
    r = run_cli(["rw", "patch", str(src), "--rename-tex", "WALL=brick", "--night-colors", "remove"])
    assert r.code == 0, r.out + r.err
    env = r.json
    assert env["written"] == 1 and env["rows"][0][1] == "written"
    assert "texture:1" in env["rows"][0][2] and "night_removed:1" in env["rows"][0][2]
    out = Path(env["rows"][0][3])
    assert out == satk_home / "work" / "out" / "rw" / "patch" / "box.dff"
    assert src.read_bytes() == make_dff(tex="wall", night=True)          # input untouched
    d = run_cli(["formats", "dump", str(out)]).json
    assert d["textures"] == ["brick"]


def test_rw_patch_batch_folder_dry_run_and_unchanged(satk_home, run_cli, make_dff, tmp_path):
    d = tmp_path / "mod"
    d.mkdir()
    (d / "a.dff").write_bytes(make_dff(tex="wall"))
    (d / "b.dff").write_bytes(make_dff(tex="other"))
    (d / "notes.txt").write_text("x", encoding="utf-8")
    env = run_cli(["rw", "patch", str(d), "--rename-tex", "wall=brick", "--dry-run"]).json
    assert env["written"] == 1 and env["unchanged"] == 1 and "out_dir" not in env
    assert [r[:2] for r in env["rows"]] == [["a.dff", "would-write"], ["b.dff", "unchanged"]]
    assert not (satk_home / "work" / "out" / "rw" / "patch").exists()
    env = run_cli(["rw", "patch", str(d), "--material-color", "0=255,0,0", "--recalc-normals", "--rw-version", "vc",
                   "--out", "fixed"]).json
    assert env["written"] == 2 and env["out_dir"].endswith("/out/rw/fixed")
    info = run_cli(["formats", "dump", str(satk_home / "work/out/rw/fixed/a.dff"), "--level", "full"]).json
    assert info["rw_version"] == "0x33002" and info["rows"][0][4] == "ff0000ff" and "normals" in info["flags"]


def test_rw_patch_errors(satk_home, run_cli, make_dff, tmp_path):
    src = tmp_path / "box.dff"
    src.write_bytes(make_dff())
    r = run_cli(["rw", "patch", str(src)])
    assert r.code == 2 and r.json["error"]["code"] == "BAD_PARAMS"
    r = run_cli(["rw", "patch", str(src), "--rename-tex", "wall"])
    assert r.json["error"]["code"] == "BAD_PARAMS"
    r = run_cli(["rw", "patch", str(src), "--material-color", "9=1,2,3"])
    assert r.json["error"]["code"] == "UNSUPPORTED" and "out of range" in r.json["error"]["msg"]
    r = run_cli(["rw", "patch", str(tmp_path / "missing.dff"), "--recalc-normals"])
    assert r.json["error"]["code"] == "NOT_FOUND"
    bad = tmp_path / "bad.dff"
    bad.write_bytes(b"COL3" + b"\0" * 40)
    r = run_cli(["rw", "patch", str(bad), "--recalc-normals"])
    assert r.json["error"]["code"] == "UNSUPPORTED"


def test_img_build_100_files_is_readable_by_formats_ls(satk_home, run_cli, make_dff, tmp_path):
    d = tmp_path / "mod"
    d.mkdir()
    for i in range(100):
        (d / f"m{i:03d}.dff").write_bytes(make_dff(tex=f"t{i}"))
    env = run_cli(["img", "build", str(d), "--out", "mod100"]).json
    assert env["ok"] and env["entries"] == 100 and env["added"] == 100 and env["version"] == 2
    img = Path(env["path"])
    assert img == satk_home / "work" / "out" / "rw" / "mod100.img"
    ls = run_cli(["formats", "ls", str(img), "--limit", "500"]).json
    assert ls["total"] == 100 and ls["version"] == 2
    assert [r[1] for r in ls["rows"]] == [f"m{i:03d}.dff" for i in range(100)]
    dump = run_cli(["formats", "dump", f"{img}/m042.dff"]).json
    assert dump["textures"] == ["t42"]
    # the new archive is never overwritten silently, and a base archive is never the output
    r = run_cli(["img", "build", str(d), "--out", "mod100"])
    assert r.json["error"]["code"] == "EXISTS"
    r = run_cli(["img", "build", "--base", str(img), "--out", str(img), "--overwrite"])
    assert r.json["error"]["code"] == "BAD_PARAMS"


def test_img_build_with_base_and_diff(satk_home, run_cli, make_dff, tmp_path):
    d = tmp_path / "mod"
    d.mkdir()
    for i in range(5):
        (d / f"m{i}.dff").write_bytes(make_dff(tex=f"t{i}"))
    base = Path(run_cli(["img", "build", str(d), "--out", "base"]).json["path"])
    patch = tmp_path / "patch"
    patch.mkdir()
    (patch / "M1.DFF").write_bytes(make_dff(tex="new1"))
    (patch / "extra.dff").write_bytes(make_dff(tex="extra"))
    env = run_cli(["img", "build", str(patch), "--base", "base.img", "--remove", "m4.*", "--out", "mod",
                   "--version", "1"]).json
    assert (env["copied"], env["replaced"], env["added"], env["removed"], env["version"]) == (3, 1, 1, 1, 1)
    assert Path(env["dir"]).is_file()
    diff = run_cli(["img", "diff", str(base), "mod.img"]).json
    assert (diff["same"], diff["changed"], diff["added"], diff["removed"]) == (3, 1, 1, 1)
    assert diff["version_a"] == 2 and diff["version_b"] == 1 and not diff["identical"]
    assert run_cli(["img", "diff", "base.img", "base.img"]).json["identical"] is True
    only = run_cli(["img", "diff", "base.img", "mod.img", "--change", "added"]).json
    assert [r[0] for r in only["rows"]] == ["extra.dff"]


def test_col_write_inline_and_file_then_export(satk_home, run_cli, tmp_path):
    inline = json.dumps({"models": [{"name": "cube", "boxes": [{"min": [-1, -1, 0], "max": [1, 1, 2],
                                                                 "surface": "CONCRETE"}]}]})
    env = run_cli(["col", "write", inline, "--out", "cube"]).json
    assert env["ok"] and env["verified"] and env["models"] == 1 and env["rows"][0][:4] == ["cube", 3, 0, 1]
    col = Path(env["path"])
    assert col == satk_home / "work" / "out" / "rw" / "cube.col"
    dump = run_cli(["formats", "dump", str(col), "--level", "full"]).json
    assert dump["models"] == 1 and dump["by_version"] == {"COL3": 1}
    exp = run_cli(["col", "export", str(col)]).json
    js = Path(exp["path"])
    assert js.name == "cube.json" and exp["models"] == 1
    env2 = run_cli(["col", "write", str(js), "--out", "cube2"]).json
    assert Path(env2["path"]).read_bytes() == col.read_bytes()
    spec = tmp_path / "two.json"
    spec.write_text(json.dumps({"version": 2, "models": [
        {"name": "a", "spheres": [{"center": [0, 0, 0], "radius": 1, "surface": 3}]},
        {"name": "b", "version": 3, "vertices": [[0, 0, 0], [1, 0, 0], [0, 1, 0]], "faces": [[0, 1, 2, 4, 0]]}]}),
        encoding="utf-8")
    env3 = run_cli(["col", "write", str(spec)]).json
    assert [r[:2] for r in env3["rows"]] == [["a", 2], ["b", 3]] and env3["path"].endswith("/two.col")


def test_col_write_errors(satk_home, run_cli):
    r = run_cli(["col", "write", "{not json"])
    assert r.json["error"]["code"] == "BAD_PARAMS"
    r = run_cli(["col", "write", json.dumps({"name": "a", "vertices": [[0, 0, 0]], "faces": [[0, 0, 5]]})])
    assert r.json["error"]["code"] == "BAD_PARAMS" and "vertex index" in r.json["error"]["msg"]
    r = run_cli(["col", "write", json.dumps({"name": "a", "spheres": "x"})])
    assert r.json["error"]["code"] == "BAD_PARAMS"


def test_roundtrip_op_on_a_synthetic_archive(satk_home, run_cli, make_dff, tmp_path):
    d = tmp_path / "mod"
    d.mkdir()
    (d / "a.dff").write_bytes(make_dff(skin=True, matfx="env"))
    (d / "b.dff").write_bytes(make_dff(short_clump=True))
    img = Path(run_cli(["img", "build", str(d), "--out", "rt"]).json["path"])
    env = run_cli(["rw", "roundtrip", str(img), "--kind", "dff"]).json
    assert env["dff"]["files"] == 2 and env["dff"]["tree_exact"] == 2 and env["dff"]["typed_exact"] == 2
    assert env["dff"]["restamp_exact"] == 1                                # the skin's used-bone order is lost
    assert env["dff"]["classes"] == {"restamp:skin-used-bone-order": 1}
    r = run_cli(["rw", "roundtrip", str(img), "--step", "0"])
    assert r.json["error"]["code"] == "BAD_PARAMS"
    fury = run_cli(["rw", "roundtrip", str(img), "--kind", "dff", "--engine", "rwfury"]).json
    if fury["ok"]:
        assert fury["dff"]["files"] == 2 and fury["dff"]["exact"] == 0


@pytest.mark.parametrize("argv", [["img", "build", "--out", "x"], ["img", "build", "somewhere"]])
def test_img_build_needs_sources_and_out(satk_home, run_cli, argv, tmp_path):
    r = run_cli(argv)
    assert r.json["error"]["code"] in ("BAD_PARAMS", "NOT_FOUND")
