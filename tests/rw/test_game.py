"""M3-B1 acceptance on the vanilla game (READ-ONLY; gta-sa-clean). Golden numbers: tests/golden/rw_roundtrip.json.

* every vanilla DFF (15 356) and COL record (10 169 + 212 embedded) goes through the satk writers;
* the vendored rwfury 0.6.1 is the baseline (0 bit-exact) and an independent reader of our output;
* ``rw patch`` -> ``formats dump`` shows the new texture name; ``img build`` of 100 entries -> ``formats ls``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from satk.core import config as _config
from satk.formats.dff import scan_dff
from satk.formats.img import ImgArchive
from satk.formats.rw import rw_payload_size
from satk.rw import roundtrip as RT
from satk.rw.chunk import GAME_VERSIONS
from satk.rw.dff import DffDoc
from satk.rw.vendor import load

pytestmark = pytest.mark.game

GOLDEN = json.loads((Path(__file__).resolve().parents[1] / "golden" / "rw_roundtrip.json").read_text(encoding="utf-8"))


def _strip(d: dict) -> dict:
    return {k: v for k, v in d.items() if k != "examples" and not k.startswith("_")}


@pytest.fixture
def game_work(clean_root, tmp_path, monkeypatch):
    """The real configuration (game roots) with a private work directory."""
    monkeypatch.setenv("SATK_PATHS_WORK", str(tmp_path / "work"))
    _config.reset()
    yield tmp_path / "work"
    _config.reset()


@pytest.mark.slow
def test_full_round_trip_matches_golden(clean_root):
    res = RT.run(clean_root, "all", restamp=True)
    want_dff = {k: v for k, v in GOLDEN["dff"].items() if not k.startswith("_")}
    want_col = {k: v for k, v in GOLDEN["col"].items() if not k.startswith("_")}
    assert _strip(res["dff"]) == {**want_dff, "opaque": {}}
    assert _strip(res["col"]) == want_col
    # the acceptance figures: every DFF bit for bit at both levels, every COL file bit for bit
    assert res["dff"]["tree_exact"] == res["dff"]["typed_exact"] == res["dff"]["files"] == 15356
    assert res["col"]["files_exact"] == res["col"]["files"] == 254


def test_rwfury_baseline_and_golden(clean_root):
    if load() is None:
        pytest.skip("vendor/rwfury missing")
    sample = RT.measure_rwfury(lambda k: (x for i, x in enumerate(RT.iter_game(clean_root, k)) if i % 25 == 0))
    assert sample["dff"]["files"] == 615 and sample["dff"]["exact"] == 0
    assert sample["col"]["models"] > 0 and sample["col"]["models_exact"] == 0
    g = GOLDEN["rwfury_0_6_1"]
    assert g["dff"]["exact"] == 0 and g["col"]["models_exact"] == 0 and g["dff"]["files"] == GOLDEN["dff"]["files"]


def test_our_reading_agrees_with_rwfury(clean_root):
    """Independent cross-check on every 20th vanilla DFF: materials, texture names, vertex counts."""
    rwf = load()
    if rwf is None:
        pytest.skip("vendor/rwfury missing")
    n = 0
    for i, (label, data) in enumerate(RT.iter_game(clean_root, "dff")):
        if i % 20:
            continue
        doc = DffDoc.parse(data)
        if len(doc.clumps()) != 1:                      # rwfury keeps only the last clump (player.img)
            continue
        fury = rwf.Dff.from_bytes(data[:rw_payload_size(data)])
        ours = doc.geometries()
        assert len(ours) == len(fury.geometries), label
        for g, fg in zip(ours, fury.geometries):
            assert g.data().num_verts == len(fg.vertices) or not fg.vertices, label
        names = [m.texture_name for g in fury.geometries for m in g.materials if m.texture_name]
        assert sorted(set(names), key=names.index) == doc.texture_names(), label
        n += 1
    assert n > 700


def test_patch_then_dump_shows_new_texture(game_work, run_cli):
    before = run_cli(["formats", "dump", "models/gta3.img/infernus.dff"]).json
    assert "vehiclelights128" in before["textures"]
    env = run_cli(["rw", "patch", "models/gta3.img/infernus.dff", "--rename-tex", "vehiclelights128=mylights128"]).json
    assert env["written"] == 1, env
    out = Path(env["rows"][0][3])
    assert out.parent == game_work / "out" / "rw" / "patch"
    after = run_cli(["formats", "dump", str(out)]).json
    assert "mylights128" in after["textures"] and "vehiclelights128" not in after["textures"]
    assert sorted(set(before["textures"]) - {"vehiclelights128"}) == sorted(set(after["textures"]) - {"mylights128"})
    for k in ("atomics", "frames", "geoms", "materials", "verts", "tris", "embedded_col", "plugins"):
        assert after[k] == before[k], k
    rwf = load()
    if rwf is not None:
        names = {m.texture_name for g in rwf.Dff.from_file(str(out)).geometries for m in g.materials}
        assert "mylights128" in names


def test_restamp_vanilla_models_stay_readable(clean_root):
    rwf = load()
    with ImgArchive.open(clean_root / "models" / "gta3.img") as a:
        for name in ("infernus.dff", "bmyst.dff", "lae2_roads04.dff", "countn2_1_lod.dff" if a.find(
                "countn2_1_lod.dff") else "cj_bag_reclaim.dff"):
            data = a.read(a.find(name))
            info = scan_dff(data)
            for target in ("vc", "iii"):
                doc = DffDoc.parse(data)
                doc.restamp(GAME_VERSIONS[target])
                out = doc.to_bytes()
                got = scan_dff(out)
                assert got.rw_version == GAME_VERSIONS[target], name
                assert (len(got.geoms), got.verts, got.tris, len(got.materials)) == \
                    (len(info.geoms), info.verts, info.tris, len(info.materials)), name
                if rwf is not None:
                    assert len(rwf.Dff.from_bytes(out).geometries) == len(info.geoms)


def test_img_build_of_100_vanilla_entries(game_work, run_cli, clean_root, tmp_path):
    src = tmp_path / "pick"
    src.mkdir()
    with ImgArchive.open(clean_root / "models" / "gta3.img") as a:
        picked = [e for e in a.entries if e.ext in ("dff", "txd", "col")][::150][:100]
        for e in picked:
            (src / e.name).write_bytes(a.read(e))
    assert len(picked) == 100
    env = run_cli(["img", "build", str(src), "--out", "pick100"]).json
    assert env["entries"] == 100, env
    ls = run_cli(["formats", "ls", env["path"], "--limit", "500"]).json
    assert ls["total"] == 100 and {r[1] for r in ls["rows"]} == {e.name for e in picked}
    diff = run_cli(["img", "diff", "models/gta3.img", "pick100.img"]).json
    assert diff["changed"] == 0 and diff["added"] == 0 and diff["same"] == 100
    assert diff["removed"] == 16316 - 100
    sub = run_cli(["img", "build", "--base", "models/gta3.img", "--include", "infernus.*", "--out", "inf"]).json
    assert sub["copied"] == 2
    assert run_cli(["formats", "dump", f"{sub['path']}/infernus.dff"]).json["embedded_col"] == "infernus_col"


def test_col_export_write_vanilla_file_is_bit_exact(game_work, run_cli, clean_root):
    exp = run_cli(["col", "export", "models/gta3.img/countn2_1.col"]).json
    assert exp["models"] > 10
    env = run_cli(["col", "write", exp["path"], "--out", "countn2_1_copy"]).json
    assert env["verified"]
    with ImgArchive.open(clean_root / "models" / "gta3.img") as a:
        orig = a.read(a.find("countn2_1.col"))
    out = Path(env["path"]).read_bytes()
    assert orig[:len(out)] == out and not orig[len(out):].strip(b"\0")
    veh = run_cli(["col", "export", "models/gta3.img/infernus.dff"]).json
    assert veh["rows"][0][0] == "infernus_col"
