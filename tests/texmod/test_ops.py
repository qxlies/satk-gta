"""texture pack / replace / extract through the CLI on synthetic TXDs (M2-03).

Everything runs in the isolated ``satk_home`` workspace: outputs land in ``<ws>/work/out/{mods,texmod}``.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from satk.formats.txd import parse_txd, texture_hash


@pytest.fixture
def txd_file(tmp_path, tm) -> Path:
    p = tmp_path / "in" / "sample.txd"
    p.parent.mkdir(parents=True)
    p.write_bytes(tm.sample_txd())
    return p


def _ok(res):
    assert res.code == 0, res.out + res.err
    env = res.json
    assert env["ok"], env
    return env


def _err(res, code: str):
    env = json.loads(res.out)
    assert res.code != 0 and env["ok"] is False and env["error"]["code"] == code, env
    return env["error"]


def test_replace_two_textures(run_cli, satk_home, tmp_path, tm, txd_file):
    a = tm.write_png(tmp_path / "img" / "a.png", tm.image("shapes", 64, 64))
    b = tm.write_png(tmp_path / "img" / "b.png", tm.image("shapes", 32, 32))
    env = _ok(run_cli(["texture", "replace", str(txd_file), f"wall={a}", f"floor_tiles={b}", "--format", "dxt1",
                       "--name", "demo", "--json"]))
    out = Path(env["file"])
    assert out == satk_home / "work" / "out" / "mods" / "demo" / "sample.txd"
    assert env["cols"] == ["name", "fmt", "size", "mips", "psnr", "source"]
    rows = {r[0]: r for r in env["rows"]}
    assert set(rows) == {"wall", "Floor_Tiles"}                    # the TXD's own spelling is kept
    assert rows["wall"][1:4] == ["DXT1", "64x64", 1] and rows["Floor_Tiles"][1:4] == ["DXT1", "32x32", 6]
    assert all(r[4] >= 35 for r in env["rows"]), env["rows"]
    old, new = parse_txd(txd_file.read_bytes()), parse_txd(out.read_bytes())
    assert [t.name for t in new.textures] == [t.name for t in old.textures]
    assert texture_hash(out.read_bytes(), new.textures[2]) == texture_hash(txd_file.read_bytes(), old.textures[2])
    dump = _ok(run_cli(["formats", "dump", str(out), "--level", "full", "--json"]))
    assert dump["kind"] == "txd" and dump["textures"] == 3 and dump["by_fmt"] == {"DXT1": 2, "A8R8G8B8": 1}
    readme = (out.parent / "README.txt").read_text(encoding="utf-8")
    assert "== sample.txd ==" in readme and "modloader" in readme and a.as_posix() in readme


def test_replace_auto_keeps_family_and_mips(run_cli, satk_home, tmp_path, tm, txd_file):
    img = tm.write_png(tmp_path / "w.png", tm.image("noise", 64, 64))
    alpha = tm.write_png(tmp_path / "f.png", tm.image("smooth_alpha", 32, 32))
    env = _ok(run_cli(["texture", "replace", str(txd_file), f"WALL={img}", f"Floor_Tiles={alpha}", "--json"]))
    rows = {r[0]: r for r in env["rows"]}
    assert rows["wall"][1] == "X8R8G8B8" and rows["wall"][4] == 100.0       # raw stays raw: lossless
    assert rows["Floor_Tiles"][1:4] == ["DXT5", "32x32", 6]                  # DXT1 + smooth alpha -> DXT5, mips kept
    assert Path(env["file"]).parent.name == "sample"                        # default mod name = TXD name
    env = _ok(run_cli(["texture", "replace", str(txd_file), f"wall={img}", "--mips", "3", "--name", "m3", "--json"]))
    assert env["rows"][0][3] == 3
    t = parse_txd(Path(env["file"]).read_bytes()).textures[0]
    assert (t.levels, t.filter) == (3, 6)


def test_replace_errors(run_cli, satk_home, tmp_path, tm, txd_file):
    a = tm.write_png(tmp_path / "a.png", tm.image("smooth", 16, 16))
    err = _err(run_cli(["texture", "replace", str(txd_file), f"wal={a}", "--json"]), "NOT_FOUND")
    assert "wall" in err.get("did_you_mean", [])
    _err(run_cli(["texture", "replace", str(txd_file), "wall", "--json"]), "BAD_PARAMS")
    _err(run_cli(["texture", "replace", str(txd_file), f"wall={a}", f"WALL={a}", "--json"]), "BAD_PARAMS")
    _err(run_cli(["texture", "replace", str(txd_file), "wall=missing.png", "--json"]), "NOT_FOUND")
    _err(run_cli(["texture", "replace", str(txd_file), f"wall={a}", "--name", "../x", "--json"]), "BAD_PARAMS")
    _err(run_cli(["texture", "replace", str(tmp_path / "nope.txd"), f"wall={a}", "--json"]), "NOT_FOUND")
    bad = tmp_path / "bad.txd"
    bad.write_bytes(b"\x10\0\0\0" + b"\0" * 20)
    _err(run_cli(["texture", "replace", str(bad), f"wall={a}", "--json"]), "UNSUPPORTED")
    odd = tm.write_png(tmp_path / "odd.png", tm.image("smooth", 6, 6))
    _err(run_cli(["texture", "replace", str(txd_file), f"wall={odd}", "--no-pot", "--format", "dxt1", "--json"]),
         "BAD_PARAMS")
    long = tm.write_png(tmp_path / "l.png", tm.image("smooth", 8, 8))
    _err(run_cli(["texture", "replace", str(txd_file), f"{'x' * 32}={long}", "--add", "--json"]), "BAD_PARAMS")


def test_replace_add_and_resize(run_cli, satk_home, tmp_path, tm, txd_file):
    big = tm.write_png(tmp_path / "big.png", tm.image("shapes", 100, 60))
    env = _ok(run_cli(["texture", "replace", str(txd_file), f"decal={big}", "--add", "--json"]))
    assert env["rows"][0][:4] == ["decal", "DXT1", "128x64", 8]
    assert any(w.startswith("RESIZED: decal: 100x60 -> 128x64") for w in env["warn"])
    t = parse_txd(Path(env["file"]).read_bytes())
    assert t.count == 4 and t.textures[-1].name == "decal"
    env = _ok(run_cli(["texture", "replace", str(txd_file), f"decal={big}", "--add", "--no-pot", "--max-size", "50",
                       "--name", "np", "--json"]))
    assert env["rows"][0][1:3] == ["X8R8G8B8", "50x30"]                    # not whole blocks -> raw


def test_replace_from_img_entry(run_cli, satk_home, tmp_path, tm):
    arc = tm.make_img(tmp_path / "test.img", {"other.dff": b"\x10\0\0\0" + b"\0" * 40,
                                              "sample.txd": tm.sample_txd()})
    a = tm.write_png(tmp_path / "a.png", tm.image("smooth", 16, 16))
    env = _ok(run_cli(["texture", "replace", f"{arc.as_posix()}/SAMPLE.TXD", f"glass={a}", "--json"]))
    assert Path(env["file"]).name == "sample.txd"
    assert env["rows"][0][1] == "A8R8G8B8"


def test_pack_folder(run_cli, satk_home, tmp_path, tm):
    d = tmp_path / "signs"
    tm.write_png(d / "Sign_A.png", tm.image("shapes", 64, 32))
    tm.write_png(d / "sign_b.png", tm.image("binary_alpha", 32, 32))
    tm.write_png(d / "glow.png", tm.image("smooth_alpha", 16, 16))
    (d / "notes.txt").write_text("ignored", encoding="utf-8")
    env = _ok(run_cli(["texture", "pack", str(d), "--json"]))
    assert Path(env["file"]) == satk_home / "work" / "out" / "mods" / "signs" / "signs.txd"
    rows = [r[:4] for r in env["rows"]]
    assert rows == [["glow", "DXT5", "16x16", 5], ["Sign_A", "DXT1", "64x32", 7], ["sign_b", "DXT1+a", "32x32", 6]]
    data = Path(env["file"]).read_bytes()
    t = parse_txd(data)
    assert [x.name for x in t.textures] == ["glow", "Sign_A", "sign_b"] and t.textures[2].alpha
    dec = tm.decode(data, t.textures[2])
    assert np.array_equal(dec[..., 3] == 255, tm.image("binary_alpha", 32, 32)[..., 3] == 255)
    env = _ok(run_cli(["texture", "pack", str(d), "--name", "signs2", "--file", "custom", "--mips", "0",
                       "--format", "a8r8g8b8", "--json"]))
    assert Path(env["file"]).name == "custom.txd" and {r[3] for r in env["rows"]} == {1}
    assert all(r[4] == 100.0 for r in env["rows"])


def test_pack_errors(run_cli, satk_home, tmp_path, tm):
    empty = tmp_path / "empty"
    empty.mkdir()
    _err(run_cli(["texture", "pack", str(empty), "--json"]), "NOT_FOUND")
    _err(run_cli(["texture", "pack", str(tmp_path / "missing"), "--json"]), "NOT_FOUND")
    dup = tmp_path / "dup"
    tm.write_png(dup / "a.png", tm.image("smooth", 8, 8))
    tm.write_png(dup / "A.bmp", tm.image("smooth", 8, 8))
    _err(run_cli(["texture", "pack", str(dup), "--json"]), "BAD_PARAMS")
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "broken.png").write_bytes(b"\x89PNG\r\n\x1a\nnot really")
    _err(run_cli(["texture", "pack", str(bad), "--json"]), "UNSUPPORTED")
    _err(run_cli(["texture", "pack", str(dup), "--mips", "-1", "--json"]), "BAD_PARAMS")


def test_extract_edit_pack_round_trip(run_cli, satk_home, tmp_path, tm, txd_file):
    env = _ok(run_cli(["texture", "extract", str(txd_file), "--json"]))
    d = Path(env["dir"])
    assert d == satk_home / "work" / "out" / "texmod" / "sample"
    assert [r[1] for r in env["rows"]] == ["wall.png", "Floor_Tiles.png", "glass.png"]
    assert {r[5] for r in env["rows"]} == {"written"}
    man = json.loads((d / "texmod.json").read_text(encoding="utf-8"))
    assert [t["format"] for t in man["textures"]] == ["X8R8G8B8", "DXT1", "A8R8G8B8"]
    assert {r[5] for r in _ok(run_cli(["texture", "extract", str(txd_file), "--json"]))["rows"]} == {"same"}
    tm.write_png(d / "wall.png", tm.image("noise", 64, 64))           # the user edits one texture
    env = _ok(run_cli(["texture", "extract", str(txd_file), "--json"]))
    assert [r[5] for r in env["rows"]][0] == "kept" and any(w.startswith("KEPT: 1") for w in env["warn"])
    # pack the edited folder by its name under work/out/texmod: formats and mips follow texmod.json
    env = _ok(run_cli(["texture", "pack", "sample", "--name", "sample_hd", "--file", "sample", "--json"]))
    rows = {r[0]: r for r in env["rows"]}
    assert [r[0] for r in env["rows"]] == ["wall", "Floor_Tiles", "glass"]
    assert rows["wall"][1] == "X8R8G8B8" and rows["wall"][4] == 100.0
    assert rows["Floor_Tiles"][1:4] == ["DXT1", "32x32", 6] and rows["glass"][1] == "A8R8G8B8"
    assert "extracted from:" in (Path(env["mod"]) / "README.txt").read_text(encoding="utf-8")
    # a relative image path resolves under work/out/texmod as well
    env = _ok(run_cli(["texture", "replace", str(txd_file), "glass=sample/wall.png", "--name", "rel", "--json"]))
    assert env["rows"][0][5].endswith("/out/texmod/sample/wall.png")
    env = _ok(run_cli(["texture", "extract", str(txd_file), "--force", "--json"]))
    assert env["rows"][0][5] == "written"


def test_readme_sections_per_txd(run_cli, satk_home, tmp_path, tm, txd_file):
    other = tmp_path / "other.txd"
    other.write_bytes(tm.sample_txd())
    a = tm.write_png(tmp_path / "a.png", tm.image("smooth", 16, 16))
    _ok(run_cli(["texture", "replace", str(txd_file), f"wall={a}", "--name", "pack1", "--json"]))
    env = _ok(run_cli(["texture", "replace", str(other), f"glass={a}", "--name", "pack1", "--json"]))
    assert any(w.startswith("OTHER_FILES: pack1 also holds sample.txd") for w in env["warn"])
    text = Path(env["readme"]).read_text(encoding="utf-8")
    assert text.index("== other.txd ==") < text.index("== sample.txd ==")
    _ok(run_cli(["texture", "replace", str(txd_file), f"glass={a}", "--name", "pack1", "--json"]))
    text2 = Path(env["readme"]).read_text(encoding="utf-8")
    assert text2.count("== sample.txd ==") == 1 and text2.count("== other.txd ==") == 1
    sample = text2.split("== sample.txd ==", 1)[1]
    assert "  glass " in sample and "  wall " not in sample


def test_output_is_deterministic(run_cli, satk_home, tmp_path, tm, txd_file):
    a = tm.write_png(tmp_path / "a.png", tm.image("smooth_alpha", 32, 32))
    e1 = _ok(run_cli(["texture", "replace", str(txd_file), f"wall={a}", "--name", "d1", "--json"]))
    e2 = _ok(run_cli(["texture", "replace", str(txd_file), f"wall={a}", "--name", "d1", "--json"]))
    assert Path(e1["file"]).read_bytes() == Path(e2["file"]).read_bytes()
    assert e1["rows"] == e2["rows"]


def test_ops_are_cli_only():
    from satk.core.registry import get_op

    for name in ("texture.pack", "texture.replace", "texture.extract"):
        spec = get_op(name)
        assert spec.mcp_name is None, name
