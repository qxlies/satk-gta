"""``texture pack --asset-class/--out`` and ``texture finish`` (wave A1, lane rules) on synthetic images."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from satk.formats.txd import parse_txd


def _ok(res):
    assert res.code == 0, res.out + res.err
    env = res.json
    assert env["ok"], env
    return env


def _folder(tmp_path: Path, tm) -> Path:
    d = tmp_path / "tex"
    tm.write_png(d / "body.png", tm.image("shapes", 64, 64))
    tm.write_png(d / "glass.png", tm.image("smooth_alpha", 64, 64))
    tm.write_png(d / "grille.png", tm.image("binary_alpha", 64, 64))
    tm.write_png(d / "big.png", tm.image("smooth", 256, 256))
    return d


def _formats(path: Path) -> dict:
    return {t.name: (t.d3dfmt, t.levels, t.alpha) for t in parse_txd(path.read_bytes()).textures}


def test_vehicle_class_is_dxt1_dxt3_one_level_never_dxt5(run_cli, satk_home, tmp_path, tm):
    env = _ok(run_cli(["texture", "pack", str(_folder(tmp_path, tm)), "--asset-class", "vehicle", "--json"]))
    f = _formats(Path(env["file"]))
    assert f["body"] == ("DXT1", 1, False) and f["glass"] == ("DXT3", 1, True) and f["grille"] == ("DXT1", 1, True)
    assert f["big"][:2] == ("DXT1", 1) and env["asset_class"] == "vehicle"
    assert not any(v[0] == "DXT5" for v in f.values())
    plain = _formats(Path(_ok(run_cli(["texture", "pack", str(_folder(tmp_path, tm)), "--name", "plain",
                                        "--json"]))["file"]))
    assert plain["glass"][0] == "DXT5" and plain["body"][1] > 1      # the old defaults, for comparison


def test_ped_map_and_lod_classes(run_cli, satk_home, tmp_path, tm):
    d = _folder(tmp_path, tm)
    ped = _formats(Path(_ok(run_cli(["texture", "pack", str(d), "--asset-class", "ped", "--name", "p",
                                      "--json"]))["file"]))
    assert ped["body"] == ("X8R8G8B8", 1, False) and ped["glass"][:2] == ("A8R8G8B8", 1)
    mp = _formats(Path(_ok(run_cli(["texture", "pack", str(d), "--asset-class", "map", "--name", "m",
                                     "--json"]))["file"]))
    assert mp["big"][:2] == ("DXT1", 9) and mp["body"][1] == 1      # a full chain from 256 px on
    lod = _formats(Path(_ok(run_cli(["texture", "pack", str(d), "--asset-class", "lod", "--name", "l",
                                      "--json"]))["file"]))
    assert {v[1] for v in lod.values()} == {1}
    over = _ok(run_cli(["texture", "pack", str(d), "--asset-class", "vehicle", "--format", "dxt5", "--mips", "3",
                        "--name", "o", "--json"]))
    assert _formats(Path(over["file"]))["glass"][:2] == ("DXT5", 3)    # explicit arguments win
    wep = _ok(run_cli(["texture", "pack", str(d), "--asset-class", "weapon", "--name", "w", "--json"]))
    assert any(w.startswith("CLASS_SIZE: big") for w in wep["warn"])
    bad = run_cli(["texture", "pack", str(d), "--asset-class", "boat", "--json"])
    assert bad.code != 0


def test_out_writes_into_a_mod_folder_without_readme(run_cli, satk_home, tmp_path, tm):
    mod = tmp_path / "modloader" / "mycar"
    env = _ok(run_cli(["texture", "pack", str(_folder(tmp_path, tm)), "--asset-class", "vehicle", "--file",
                       "premier.txd", "--out", str(mod), "--json"]))
    assert Path(env["file"]) == mod / "premier.txd" and (mod / "premier.txd").is_file()
    assert sorted(p.name for p in mod.iterdir()) == ["premier.txd"]
    assert not (satk_home / "work" / "out" / "mods").exists()


def test_finish_interior_lands_in_the_vanilla_band_and_is_deterministic(run_cli, satk_home, tmp_path, tm):
    flat = np.zeros((128, 128, 4), dtype=np.uint8)
    flat[..., :3] = (92, 84, 76)
    flat[..., 3] = 255
    src = tm.write_png(tmp_path / "in" / "seat.png", flat)
    env = _ok(run_cli(["texture", "finish", str(src), "--preset", "interior", "--json"]))
    assert env["in_band"] is True, env["band"]
    rows = {r[0]: r for r in env["rows"]}
    assert rows["colours"][1] == 1 and rows["colours"][3] >= 364          # DXT1 preview: vanilla p10 is 364
    assert 0.085 <= rows["value"][3] <= 0.261
    out, prev = Path(env["file"]), Path(env["dxt_preview"])
    assert out.parent == satk_home / "work" / "out" / "texmod" / "finish" and prev.is_file()
    meta = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert meta["preset"] == "interior"
    first = out.read_bytes()
    _ok(run_cli(["texture", "finish", str(src), "--preset", "interior", "--json"]))
    assert out.read_bytes() == first                                   # same image + preset -> same file


def test_finish_mask_darkens_and_wall_keeps_value(run_cli, satk_home, tmp_path, tm):
    img = np.zeros((64, 64, 4), dtype=np.uint8)
    img[..., :3] = (170, 160, 150)
    img[..., 3] = 255
    src = tm.write_png(tmp_path / "in" / "wall.png", img)
    mask = np.full((64, 64, 4), 255, dtype=np.uint8)
    mask[:, :32, :3] = 0
    m = tm.write_png(tmp_path / "in" / "wall_ao.png", mask)
    plain = _ok(run_cli(["texture", "finish", str(src), "--preset", "wall", "--json"]))
    ao = _ok(run_cli(["texture", "finish", str(src), "--preset", "wall", "--mask", str(m), "--out",
                      str(tmp_path / "ao"), "--json"]))
    v_in = {r[0]: r for r in plain["rows"]}["value"][1]
    v_plain = {r[0]: r for r in plain["rows"]}["value"][2]
    v_ao = {r[0]: r for r in ao["rows"]}["value"][2]
    assert abs(v_plain - v_in) < 0.12 and v_ao < v_plain
    odd = tm.write_png(tmp_path / "in" / "odd.png", np.zeros((6, 6, 4), dtype=np.uint8))
    assert run_cli(["texture", "finish", str(odd), "--json"]).code != 0
