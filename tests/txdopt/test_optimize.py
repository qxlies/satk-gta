"""``satk texture optimize`` on synthetic data: a bloated mod, game TXDs of a synthetic index, IMG mods, share."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from satk.formats.dxt import decode_rgba
from satk.formats.img import ImgArchive
from satk.formats.txd import mip0_bytes, parse_txd
from satk.texmod.encode import psnr, to_array
from satk.txdopt.analyze import fmt_label
from satk.txdopt.txdedit import level_list, native_spans


def _textures(p: Path) -> dict[str, str]:
    data = p.read_bytes()
    return {t.name: fmt_label(t.d3dfmt, t.alpha, t.w, t.h, t.levels) for t in parse_txd(data).textures}


def _opt(run_cli, *argv: str) -> dict:
    r = run_cli(["texture", "optimize", *argv, "--json"])
    env = r.json
    assert r.code == 0 and env["ok"], r.out
    return env


def test_bloated_mod_shrinks_reparses_and_passes_lint(ws, mod, run_cli, synth):
    mod_before = synth.tree_digest(mod)
    ws_before = synth.tree_digest(ws)
    env = _opt(run_cli, str(mod), "--max", "512", "--drop-unused", "--dedupe")
    s = env["summary"]
    assert s["bytes_before"] > 25_000_000 and s["saved_pct"] >= 95, s
    assert s["bytes_after"] < 1_000_000 and s["stream_after"] < s["stream_before"]
    assert s["psnr_min"] >= 35, s
    assert (s["txds"], s["textures"], s["dropped"], s["deduped"]) == (3, 10, 2, 1)
    assert all(r[6] is None or r[6] >= 35 for r in env["rows"])
    assert env["lint"] == {"info": 7} or not ({"fatal", "error"} & set(env["lint"])), env["lint"]
    out = Path(env["out"])
    assert out == ws / "work" / "out" / "txdopt" / "bigmap"
    assert _textures(out / "models" / "bigtxd.txd") == {
        "wall": "DXT1 512x512", "floor": "DXT5 512x512", "roof": "DXT1+a 512x512"}
    assert _textures(out / "models" / "smalltxd.txd") == {"wall": "DXT1 512x512", "sign": "DXT1 256x256"}
    assert _textures(out / "models" / "smalltxd2.txd") == {"wall": "DXT1 512x512", "lamp": "DXT1 512x256"}
    # the input is untouched; the workspace changed only under work/out/txdopt/bigmap (+ logs)
    assert synth.tree_digest(mod) == mod_before
    after = synth.tree_digest(ws)
    changed = {k for k in set(ws_before) | set(after) if ws_before.get(k) != after.get(k)}
    assert changed and all(k.startswith("work/out/txdopt/bigmap/") or k.startswith("work/logs/") for k in changed), \
        sorted(changed)
    rep = json.loads((out / "txdopt.json").read_text(encoding="utf-8"))
    assert rep["summary"] == s and len(rep["rows"]) == env["total"] and rep["settings"]["max"] == 512
    assert "bigmap" in (out / "README.txt").read_text(encoding="utf-8")
    # asset lint of the result: no error or fatal finding
    r = run_cli(["asset", "lint", str(out), "--json"])
    assert r.code == 0 and r.json["summary"]["fatal"] == 0 and r.json["summary"]["error"] == 0, r.out


def test_psnr_is_measured_against_the_resized_source(ws, mod, run_cli, synth):
    env = _opt(run_cli, str(mod), "--max", "512", "--out", "psnrcheck")
    row = next(r for r in env["rows"] if r[0] == "models/bigtxd.txd" and r[1] == "wall")
    data = (Path(env["out"]) / "models" / "bigtxd.txd").read_bytes()
    t = next(x for x in parse_txd(data).textures if x.name == "wall")
    got = to_array(t.w, t.h, decode_rgba(t, mip0_bytes(data, t)))
    from satk.txdopt.optimize import resample

    src = resample(synth.smooth(1024, 1024, 1), 512, 512)
    assert abs(psnr(src, got, alpha=False) - row[6]) < 0.01


def test_repeat_is_deterministic_and_reports_stale_files(ws, mod, run_cli):
    a = _opt(run_cli, str(mod), "--max", "256", "--out", "det")
    out = Path(a["out"])
    first = {p.name: p.read_bytes() for p in out.rglob("*") if p.is_file()}
    b = _opt(run_cli, str(mod), "--max", "256", "--out", "det")
    assert {p.name: p.read_bytes() for p in out.rglob("*") if p.is_file()} == first
    assert a["rows"] == b["rows"] and not any(w.startswith("STALE") for w in b.get("warn", []))
    (out / "leftover.txd").write_bytes(b"x")
    c = _opt(run_cli, str(mod), "--max", "256", "--out", "det")
    assert any(w.startswith("STALE: 1 file") for w in c["warn"])


def test_keep_formats_drop_only_copies_natives_byte_for_byte(ws, mod, run_cli):
    env = _opt(run_cli, str(mod), "--drop-unused", "--dxt", "keep", "--no-pot", "--out", "droponly")
    assert {r[2] for r in env["rows"]} == {"drop"} and env["summary"]["dropped"] == 2
    src = (mod / "models" / "smalltxd.txd").read_bytes()
    dst = (Path(env["out"]) / "models" / "smalltxd.txd").read_bytes()
    s_sp, d_sp = native_spans(src), native_spans(dst)
    assert [src[a:b] for a, b in s_sp[:2]] == [dst[a:b] for a, b in d_sp]
    assert not (Path(env["out"]) / "models" / "smalltxd2.txd").exists()     # nothing to do there
    assert any(w.startswith("SHAREABLE: 1 texture") for w in env["warn"])


def test_mips_keep_level_zero_and_lossless_dxt1(ws, mod, run_cli):
    env = _opt(run_cli, str(mod), "--mips", "--out", "mips")
    sign = next(r for r in env["rows"] if r[1] == "sign")
    assert sign[2] == "dxt1+mips" and sign[4] == "DXT1 256x256 m9" and sign[6] == 100.0
    src = (mod / "models" / "smalltxd.txd").read_bytes()
    t5 = next(t for t in parse_txd(src).textures if t.name == "sign")
    dst = (Path(env["out"]) / "models" / "smalltxd.txd").read_bytes()
    t1 = next(t for t in parse_txd(dst).textures if t.name == "sign")
    a = np.frombuffer(decode_rgba(t5, mip0_bytes(src, t5)), np.uint8).reshape(256, 256, 4)
    b = np.frombuffer(decode_rgba(t1, mip0_bytes(dst, t1)), np.uint8).reshape(256, 256, 4)
    assert (a[:, :, :3] == b[:, :, :3]).all() and t1.filter == 6
    assert len(level_list(dst, t1)) == 9


def test_share_moves_identical_textures_to_a_txdp_parent(ws, mod, run_cli):
    env = _opt(run_cli, str(mod), "--max", "512", "--drop-unused", "--share", "--out", "shared")
    out = Path(env["out"])
    sh = env["share"]
    assert sh["txd"] == "models/shared_shared.txd" and sh["textures"] == 1
    assert sh["ide"] == {"bigmap.ide": [["bigtxd", "shared_shared"], ["smalltxd", "shared_shared"],
                                        ["smalltxd2", "shared_shared"]]}
    assert _textures(out / "models" / "shared_shared.txd") == {"wall": "DXT1 512x512"}
    for stem in ("bigtxd", "smalltxd", "smalltxd2"):
        assert "wall" not in _textures(out / "models" / f"{stem}.txd")
    ide = (out / "bigmap.ide").read_text(encoding="latin-1")
    assert ide.startswith((mod / "bigmap.ide").read_text(encoding="latin-1"))
    assert ide.rstrip().endswith("txdp\nbigtxd, shared_shared\nsmalltxd, shared_shared\nsmalltxd2, shared_shared\nend")
    rows = env["rows"]
    assert sum(r[5] for r in rows) == env["summary"]["saved"], "row savings add up to the total"
    assert env["summary"]["shared"] == 3 and env["summary"]["saved_pct"] > 97


def test_img_mod_is_rebuilt_with_the_shared_txd_inside(ws, mod, run_cli, synth, tmp_path):
    tx = synth.mod_textures()
    d = tmp_path / "imgmod"
    d.mkdir()
    (d / "bigmap.img").write_bytes(synth.img_v2([("big1.dff", (mod / "models" / "big1.dff").read_bytes()),
                                                 ("bigtxd.txd", tx["bigtxd"]), ("smalltxd.txd", tx["smalltxd"])]))
    (d / "bigmap.ide").write_bytes((mod / "bigmap.ide").read_bytes())
    env = _opt(run_cli, str(d), "--max", "256", "--share", "--out", "imgmod")
    out = Path(env["out"])
    with ImgArchive.open(out / "bigmap.img") as a:
        names = [e.name for e in a.entries]
        assert names == ["big1.dff", "bigtxd.txd", "smalltxd.txd", "imgmod_shared.txd"]
        assert a.read(a.find("big1.dff")).startswith((mod / "models" / "big1.dff").read_bytes())
        shared = a.read(a.find("imgmod_shared.txd"))
        assert [t.name for t in parse_txd(shared).textures] == ["wall"]
    assert env["share"]["txd"] == "imgmod_shared.txd" and (out / "bigmap.ide").is_file()
    # the .img alone: rebuilt archive, no IDE (share needs the IDE)
    env = _opt(run_cli, str(d / "bigmap.img"), "--max", "256", "--share", "--out", "imgonly")
    assert "share" not in env and any(w.startswith("SHARE:") for w in env["warn"])
    assert sorted(p.name for p in Path(env["out"]).iterdir()) == ["README.txt", "bigmap.img", "txdopt.json"]


def test_full_copy_makes_a_complete_mod(ws, mod, run_cli, synth):
    env = _opt(run_cli, str(mod), "--max", "512", "--full", "--out", "full")
    out = Path(env["out"])
    got = synth.tree_digest(out)
    src = synth.tree_digest(mod)
    for rel in ("bigmap.ide", "bigmap.ipl", "models/big1.dff", "models/small2.dff"):
        assert got[rel] == src[rel]
    assert got["models/bigtxd.txd"] != src["models/bigtxd.txd"]


def test_game_txds_through_the_index(ws, run_cli):
    env = _opt(run_cli, "txd:boxes", "--drop-unused", "--out", "boxes")
    assert [r[:3] for r in env["rows"]] == [["boxes.txd", "oldtex", "drop"]]
    assert _textures(Path(env["out"]) / "boxes.txd") == {"boxtex": "DXT1 16x16"}
    env = _opt(run_cli, "txd:boxes", "--drop-unused", "--keep", "old*", "--out", "boxes2")
    assert env["rows"] == [] and env["warn"][0].startswith("NOTHING")
    env = _opt(run_cli, "txd:rifle1", "--drop-unused", "--out", "rifle")
    assert env["rows"] == [] and any("weap models" in w for w in env["warn"])
    env = _opt(run_cli, "models/gta3.img/testcar.txd", "--drop-unused", "--out", "car")
    assert env["rows"] == [] and env["source"] == "file:models/gta3.img/testcar.txd"
    env = _opt(run_cli, "txd:vehicle", "--drop-unused", "--out", "veh")
    assert any("parent of every vehicle" in w for w in env["warn"])


def test_errors_have_hints(ws, run_cli, tmp_path):
    r = run_cli(["texture", "optimize", str(tmp_path / "nope"), "--json"])
    assert r.code != 0 and r.json["error"]["code"] == "NOT_FOUND" and "mod folder" in r.json["error"]["hint"]
    r = run_cli(["texture", "optimize", "txd:boxes", "--out", "../escape", "--json"])
    assert r.json["error"]["code"] == "BAD_PARAMS" and "--out" in r.json["error"]["hint"]
    r = run_cli(["texture", "optimize", "txd:boxes", "--max", "9000", "--json"])
    assert r.json["error"]["code"] == "BAD_PARAMS"
    (tmp_path / "empty").mkdir()
    (tmp_path / "empty" / "readme.txt").write_text("x", encoding="utf-8")
    r = run_cli(["texture", "optimize", str(tmp_path / "empty"), "--json"])
    assert r.json["error"]["code"] == "NOT_FOUND" and "no TXD" in r.json["error"]["msg"]


def test_unreadable_and_odd_textures_are_kept(ws, run_cli, synth, tmp_path):
    d = tmp_path / "odd"
    d.mkdir()
    (d / "odd.txd").write_bytes(synth.txd([synth.ps2_native("ps2tex"), synth.dxt1_holes_native("holes"),
                                           synth.native("tiny", synth.smooth(2, 2, 1), "A8R8G8B8")]))
    (d / "broken.txd").write_bytes(b"not a txd at all")
    env = _opt(run_cli, str(d), "--max", "4", "--mips", "--out", "odd")
    assert any(w.startswith("UNSUPPORTED: broken.txd") for w in env["warn"])
    names = {r[1]: r for r in env["rows"]}
    assert "ps2tex" not in names and names["holes"][4] == "DXT1 4x4 m3"
    assert names["tiny"][4] == "A8R8G8B8 2x2 m2"
    txs = _textures(Path(env["out"]) / "odd.txd")
    assert txs["ps2tex"] == "PLATFORM_0x325350 0x0" or txs["ps2tex"].startswith("PLATFORM")


@pytest.mark.parametrize("mode,want", [("auto", "DXT5 512x512"), ("dxt1", "DXT1+a 512x512"), ("dxt5", "DXT5 512x512")])
def test_dxt_modes(ws, mod, run_cli, mode, want):
    env = _opt(run_cli, str(mod), "--max", "512", "--dxt", mode, "--out", f"mode_{mode}")
    floor = next(r for r in env["rows"] if r[1] == "floor")
    assert floor[4] == want
    roof = next(r for r in env["rows"] if r[1] == "roof")
    assert roof[4] == ("DXT5 512x512" if mode == "dxt5" else "DXT1+a 512x512")


def test_share_only_for_map_objects_and_zip_inputs(ws, run_cli, synth, tmp_path):
    import zipfile

    img = synth.smooth(64, 64, 3)
    d = tmp_path / "guns"
    d.mkdir()
    (d / "guns.ide").write_text("weap\n3100, gun1, gun1, null, 1, 50, 0\n3101, gun2, gun2, null, 1, 50, 0\nend\n",
                                encoding="latin-1")
    for g in ("gun1", "gun2"):
        (d / f"{g}.dff").write_bytes(synth.dff(["metal"]))
        (d / f"{g}.txd").write_bytes(synth.txd([synth.native("metal", img, "DXT1"),
                                                synth.native(f"{g}icon", synth.smooth(32, 32, 1), "DXT1")]))
    env = _opt(run_cli, str(d), "--share", "--drop-unused", "--out", "guns")
    assert "share" not in env and any(w.startswith("SHARE: fewer than two") for w in env["warn"])
    assert env["summary"]["dropped"] == 0                     # gun icons are found by name, never dropped
    z = tmp_path / "guns.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for p in sorted(d.iterdir()):
            zf.write(p, f"Guns/{p.name}")
    env = _opt(run_cli, str(z), "--max", "32", "--out", "gunzip")
    assert env["source"].endswith("guns.zip") and env["summary"]["resized"] == 2
    assert sorted(p.name for p in Path(env["out"]).iterdir()) == ["README.txt", "gun1.txd", "gun2.txd", "txdopt.json"]
