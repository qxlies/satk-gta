"""M2-06 acceptance on the real game (read-only; markers ``game``, ``slow``): the vanilla copy lints with 0 fatal."""

from __future__ import annotations

from pathlib import Path

import pytest

from satk.lint.runner import lint

pytestmark = [pytest.mark.game, pytest.mark.slow]


def test_vanilla_game_root_has_no_fatal(clean_root: Path):
    rep = lint(str(clean_root), use_index=False)
    assert rep.summary["fatal"] == 0, rep.at_least("fatal")[:10]
    assert rep.files > 19000                       # gta3/gta_int/player/cutscene entries + loose files + IDEs
    # the only error is a real bug of the shipped data: dynamic2.ide line 164, draw distance 1
    errors = [f.row() for f in rep.at_least("error")]
    assert [e[0] for e in errors] == ["ide.draw_min"] and "DYN_SALE_POST" in errors[0][3]
    assert {"link.texture_missing", "dff.prelight_missing"} <= set(rep.by_rule)
    assert not any("peds.col" in f.file for f in rep.findings)      # never loaded (no COLFILE)


def test_vanilla_files_by_relative_path(clean_root: Path):
    rep = lint("models/gta3.img/infernus.dff", use_index=False)
    assert rep.files == 1 and rep.summary == {"fatal": 0, "error": 0, "warn": 0, "info": 0}
    col = lint("models/gta3.img/lae2_4.col", use_index=False, only=["col"])
    assert col.summary["fatal"] == col.summary["error"] == 0


def test_lone_replacement_dff_is_link_checked_through_the_index(tmp_path: Path):
    """A mod that only replaces infernus.dff (no IDE line) still gets link.texture_missing via the index."""
    from satk.core.errors import SatkError
    from satk.index.api import IndexDB, read_blob_bytes

    try:
        data = read_blob_bytes(IndexDB("vanilla").blob_ref("dff:infernus"))
    except SatkError as e:                         # no (current) vanilla index in this work dir
        pytest.skip(f"vanilla index unavailable: {e}")
    assert b"vehiclelights128" in data
    mod = tmp_path / "mod"
    mod.mkdir()
    (mod / "infernus.dff").write_bytes(data.replace(b"vehiclelights128", b"zzzzzzzzzzzzz128", 1))
    from satk.index.api import clear_cache

    try:
        rep = lint(str(mod), use_index=True, only=["link.texture_missing"])
    finally:
        clear_cache()                              # never leak the real index into isolated tests
    hits = [f for f in rep.findings if f.rule == "link.texture_missing"]
    assert len(hits) == 1 and "zzzzzzzzzzzzz128" in hits[0].row()[3] and "infernus+vehicle" in hits[0].row()[3]


#: Rules added by wave A1 with what they examine on vanilla; each fires on at most 2 % of its subjects.
NEW_RULES = ("dff.flat_shading", "dff.vert_sharing", "veh.frames", "veh.dummy_side", "veh.wheel_scale",
             "veh.shadow_mesh", "veh.env_uv2", "veh.light_key_tex", "veh.paint_dirt", "veh.hd_tris",
             "veh.part_tris", "veh.dam_ratio", "ped.skin", "weap.flash", "col.face_light_zero",
             "ide.draw_bigbuilding", "dff.clump_ext_dup")


def test_vanilla_preset_quiet_on_vehicle_txds_and_new_rules_rare(clean_root: Path):
    from satk.formats.dat import read_text, resolve_ci
    from satk.formats.ide import parse_ide

    rep = lint(str(clean_root), use_index=False, preset="vanilla")
    defs, _txdp, _fx = parse_ide(read_text(resolve_ci(clean_root, "data/vehicles.ide")))
    car_txds = {f"models/gta3.img/{d.txd.lower()}.txd" for d in defs if d.sec == "cars" and d.txd}
    car_txds.add("models/generic/vehicle.txd")
    assert len(car_txds) > 200
    loud = [f.row() for f in rep.at_least("warn") if f.file.lower() in car_txds]
    assert loud == [], loud[:5]                    # strict used to warn on every vehicle TXD (mipmaps)
    assert rep.checked["txd.mips_missing"] > 25000
    for rid in NEW_RULES:
        n, fired = rep.checked.get(rid, 0), rep.fired_files.get(rid, 0)
        assert n > 0, rid
        assert fired <= 0.02 * n, (rid, fired, n)


def test_vanilla_premier_triggers_no_semantic_rule():
    from satk.core.errors import SatkError
    from satk.index.api import IndexDB, clear_cache

    try:
        IndexDB("vanilla").blob_ref("dff:premier")
    except SatkError as e:
        pytest.skip(f"vanilla index unavailable: {e}")
    try:
        rep = lint("model:426", preset="sa_plus")
    finally:
        clear_cache()
    assert rep.summary["warn"] == rep.summary["error"] == rep.summary["fatal"] == 0, rep.at_least("warn")[:5]
    assert rep.checked.get("veh.frames") == rep.checked.get("dff.flat_shading") == 1
