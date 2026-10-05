"""M2-03 acceptance on the real bistro.txd (read-only; marker ``game``).

A copy of ``models/gta3.img/bistro.txd`` goes to ``tmp_path``; two of its textures are replaced with two other
textures of the same TXD (extracted to PNG first). The result must parse with ``formats dump``, decode within
PSNR >= 35 dB of the PNGs as DXT1, keep sizes and mip counts, and leave the other 24 textures byte-identical.
Outputs go to the isolated ``satk_home`` workspace, never the real ``work``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from satk.core import config as _config
from satk.formats.img import ImgArchive
from satk.formats.rw import rw_payload_size
from satk.formats.txd import parse_txd, texture_hash

pytestmark = pytest.mark.game


def _skip(reason: str):
    if os.environ.get("SATK_TEST_NO_SKIP") == "1":
        pytest.fail(reason, pytrace=False)
    pytest.skip(reason)


@pytest.fixture(scope="module")
def real_paths() -> tuple[Path, Path]:
    """(gta3.img of gta-sa-clean, vanilla index) from the real configuration, before any isolation."""
    c = _config.build()
    return Path(c.paths.game) / "models" / "gta3.img", Path(c.paths.work) / "index" / "vanilla.sqlite"


@pytest.fixture(scope="module")
def bistro(real_paths) -> bytes:
    img = real_paths[0]
    if not img.is_file():
        _skip(f"game copy not found: {img}")
    with ImgArchive.open(img) as a:
        data = a.read(a.find("bistro.txd"))
    return data[:rw_payload_size(data)]


def _ok(res):
    assert res.code == 0, res.out + res.err
    env = res.json
    assert env["ok"], env
    return env


def test_replace_two_textures_in_bistro(run_cli, satk_home, tmp_path, bistro):
    src = tmp_path / "bistro.txd"
    src.write_bytes(bistro)
    ex = _ok(run_cli(["texture", "extract", str(src), "--out", str(tmp_path / "png"), "--json"]))
    assert len(ex["rows"]) == 26
    marble, floor = tmp_path / "png" / "Marble.png", tmp_path / "png" / "DinerFloor.png"
    env = _ok(run_cli(["texture", "replace", str(src), f"Plate={marble}", f"Panel={floor}", "--format", "dxt1",
                       "--name", "bistro_m2", "--json"]))
    out = Path(env["file"])
    assert out == satk_home / "work" / "out" / "mods" / "bistro_m2" / "bistro.txd"
    assert [r[:4] for r in env["rows"]] == [["Plate", "DXT1", "128x128", 1], ["Panel", "DXT1", "128x128", 1]]
    assert all(r[4] >= 35.0 for r in env["rows"]), env["rows"]
    dump = _ok(run_cli(["formats", "dump", str(out), "--level", "full", "--limit", "50", "--json"]))
    assert dump["kind"] == "txd" and dump["count"] == 26 and dump["rw_version"] == "0x36003"
    assert dump["by_fmt"] == {"X8R8G8B8": 23, "DXT1": 2, "A8R8G8B8": 1}
    data = out.read_bytes()
    old, new = parse_txd(bistro), parse_txd(data)
    assert [t.name for t in new.textures] == [t.name for t in old.textures]
    same = [texture_hash(data, n) == texture_hash(bistro, o) for n, o in zip(new.textures, old.textures)]
    assert same.count(False) == 2 and not same[7] and not same[8]          # only Plate and Panel changed
    assert (out.parent / "README.txt").is_file()


def test_full_mip_chain_and_auto(run_cli, satk_home, tmp_path, bistro):
    src = tmp_path / "bistro.txd"
    src.write_bytes(bistro)
    _ok(run_cli(["texture", "extract", str(src), "--out", str(tmp_path / "png"), "--json"]))
    env = _ok(run_cli(["texture", "replace", str(src), f"Plate={tmp_path / 'png' / 'Marble.png'}",
                       f"vent_64={tmp_path / 'png' / 'Cutlery.png'}", "--mips", "99", "--name", "bistro_mips",
                       "--json"]))
    rows = {r[0]: r for r in env["rows"]}
    assert rows["Plate"][1:5] == ["X8R8G8B8", "128x128", 8, 100.0]          # auto: raw stays raw, lossless
    assert rows["vent_64"][1:5] == ["A8R8G8B8", "128x128", 8, 100.0]        # alpha image -> A8R8G8B8
    t = {x.name: x for x in parse_txd(Path(env["file"]).read_bytes()).textures}
    base = {x.name: x for x in parse_txd(bistro).textures}["Plate"]
    assert (t["Plate"].levels, t["Plate"].raster_fmt) == (8, 0x8600)
    assert t["Plate"].filter == {1: 3, 2: 6}.get(base.filter, base.filter)   # mips need a mip filter
    assert t["vent_64"].alpha and (t["vent_64"].w, t["vent_64"].h) == (128, 128)


def test_replace_by_sid_with_the_real_index(run_cli, satk_home, tmp_path, real_paths, bistro, tm):
    from satk.index.api import IndexDB, override_index

    if not real_paths[1].is_file():
        _skip(f"no vanilla index: {real_paths[1]} (satk index build)")
    png = tm.write_png(tmp_path / "x.png", tm.image("shapes", 64, 64))
    db = IndexDB("vanilla", path=real_paths[1])
    try:
        with override_index(db):
            env = _ok(run_cli(["texture", "replace", "txd:bistro", f"vent_64={png}", "--json"]))
    finally:
        db.close()
    assert Path(env["file"]).name == "bistro.txd" and Path(env["mod"]).name == "bistro"
    assert env["rows"][0][:4] == ["vent_64", "X8R8G8B8", "64x64", 1]
    assert "base TXD: txd:bistro" in Path(env["readme"]).read_text(encoding="utf-8")
