from __future__ import annotations

import configparser
import hashlib
import json
import struct
import zipfile
from pathlib import Path

import pytest

from satk.core.errors import SatkError
from satk.limits.ops import limits_plan

from .conftest import CORE, GTA_DAT, MAP, addon, binary_ipl, car, chunk, collision, dff, img, write


def rows(env):
    return {r[0]: dict(zip(env["cols"], r)) for r in env["rows"]}


def saved(env):
    return json.loads(Path(env["files"]["report"]).read_text(encoding="utf-8"))


def ini(env, name):
    p = configparser.ConfigParser()
    p.read(env["files"][name], encoding="utf-8")
    return p


def test_counts_separate_model_stores_effects_archives_and_runtime(games):
    env = limits_plan(profile=["vanilla"], limit=500)
    r = rows(env)
    assert r["model.vehicle"]["need"] == 1
    assert r["model.object"]["need"] == 2
    assert r["model.atomic"]["need"] == 1
    assert r["model.damageable"]["need"] == 1
    assert r["model.clump"]["need"] == 2
    assert r["model.id_span"]["need"] == 1005
    assert r["fx.ide"]["need"] == 1 and r["fx.dff"]["need"] == 2
    assert r["store.col"]["need"] == 2
    assert r["store.ipl"]["need"] == 3
    assert r["store.txd"]["need"] == 4
    assert r["img.archives"]["need"] == 3
    assert r["img.files"]["need"] == 5
    assert r["img.entries"]["need"] == 15
    assert r["ipl.instances"]["need"] == 3
    assert r["ipl.instances"]["risk"] == "inventory"
    assert r["pool.vehicles"]["need"] == "runtime"
    assert r["pool.vehicles"]["headroom"] == "unknown"
    assert r["pool.vehicle_structs"]["need"] == "runtime"
    assert r["pool.colmodels"]["risk"] == "runtime"
    assert r["pool.buildings"]["need"] == 2
    assert r["pool.dummies"]["need"] == 1
    assert r["streaming.memory"]["basis"] == "lower_bound"
    assert not env["summary"]["exceeded"]


def test_real_overflow_suggestions_crashes_and_complete_paginated_artifacts(games, tmp_path, run_cli):
    ide = "cars\n" + "\n".join(car(20000 + n, f"newcar{n}") for n in range(212)) + "\nend\n"
    mod = addon(tmp_path / "overflow", "added", ide)
    # 254 additional archives plus the base archive and the generic slot exceed the 255 COL slots.
    for n in range(254):
        write(mod, f"added{n}.col", collision("fence"))
    result = run_cli(["limits", "plan", "--profile", "vanilla", str(mod), "--limit", "1", "--json"])
    assert result.code == 0
    env = result.json
    report = saved(env)
    r = rows(report)
    assert env["n"] == 1 and report["summary"]["limits"] == len(report["rows"])
    assert r["model.vehicle"]["need"] == 213
    assert r["model.vehicle"]["headroom"] == -1
    assert r["model.id_span"]["need"] == 20212
    assert r["store.col"]["need"] == 256
    assert r["store.col"]["risk"] == "exceeded"
    assert any(c["n"] == 244 and "0x004C678E" in c["addresses"]
               for c in report["details"]["model.vehicle"]["known_crashes"])
    assert any(c["n"] == 172 for c in report["details"]["store.col"]["known_crashes"])
    f92 = ini(env, "fastman92")
    ola = ini(env, "open_limit_adjuster")
    assert f92.getint("IDE LIMITS", "Vehicle Models") >= 213
    assert f92.getint("ID LIMITS", "FILE_TYPE_COL") >= 256
    assert f92.getint("ID LIMITS", "FILE_TYPE_DFF") >= 20212
    assert ola.getint("SALIMITS", "VehicleModels") >= 213
    assert not ola.has_option("SALIMITS", "FILE_TYPE_COL")
    assert not f92.has_option("DYNAMIC LIMITS", "Vehicles")
    assert "no verified setting" in Path(env["files"]["open_limit_adjuster"]).read_text(encoding="utf-8")
    assert not list(games[0].rglob("*.ini"))


def test_replacements_remove_old_definitions_and_do_not_add_resource_slots(games, tmp_path):
    mod = tmp_path / "replacement"
    write(mod, "core.ide", CORE.replace(car(400, "basecar") + "\n", ""))
    write(mod, "base.col", collision("crate"))
    write(mod, "shared.txd", chunk(0x16))
    write(mod, "map.ipl", "inst\n1000, crate, 0, 0, 0, 0, 0, 0, 0, 1, -1\nend\n")
    r = rows(limits_plan(profile=["vanilla", str(mod)], limit=500))
    assert r["model.vehicle"]["need"] == 0
    assert r["store.col"]["need"] == 2
    assert r["store.txd"]["need"] == 4
    assert r["ipl.instances"]["need"] == 2
    assert r["ipl.enex"]["need"] == 0


def test_two_mods_merge_registered_ides_and_are_order_independent(games, tmp_path):
    a = addon(tmp_path / "a", "a", "cars\n" + car(450, "acar") + "\nend\n")
    b = addon(tmp_path / "b", "b", "cars\n" + car(451, "bcar") + "\nend\n")
    first = limits_plan(profile=["vanilla", str(a), str(b)], limit=500)
    second = limits_plan(profile=["vanilla", str(b), str(a)], limit=500)
    assert rows(first)["model.vehicle"]["need"] == 3
    assert rows(first)["ipl.instances"]["need"] == 3
    assert first["plan"] == second["plan"]


def test_unregistered_ide_and_ipl_are_not_counted(games, tmp_path):
    mod = tmp_path / "unregistered"
    write(mod, "unused.ide", "cars\n" + car(450, "unused") + "\nend\n")
    write(mod, "unused.ipl", MAP)
    r = rows(limits_plan(profile=["vanilla", str(mod)], limit=500))
    assert r["model.vehicle"]["need"] == 1
    assert r["ipl.instances"]["need"] == 3


def test_installed_profile_applies_enabled_mods_and_deduplicates_given_path(games):
    clean, installed = games
    active = addon(installed / "modloader" / "active", "added", "cars\n" + car(450, "installedcar") + "\nend\n")
    addon(installed / "modloader" / ".disabled", "ignored", "cars\n" + car(451, "ignoredcar") + "\nend\n")
    env = limits_plan(profile=["installed", str(active)], limit=500)
    assert rows(env)["model.vehicle"]["need"] == 2
    assert len(saved(env)["inputs"]["mods"]) == 1
    assert rows(limits_plan(profile=["vanilla"], limit=500))["model.vehicle"]["need"] == 1


def test_mod_paths_without_profile_use_explicit_base(games, tmp_path, run_cli):
    mod = addon(tmp_path / "pack", "extra", "cars\n" + car(450, "addcar") + "\nend\n")
    env = run_cli(["limits", "plan", "--profile", str(mod), "--base", "vanilla", "--limit", "500"]).json
    assert env["ok"] and rows(env)["model.vehicle"]["need"] == 2


def test_more_img_archives_and_replacement_archive_entries(games, tmp_path):
    mod = tmp_path / "archives"
    for n in range(6):
        write(mod, f"extra{n}.img", img([(f"added{n}.txd", chunk(0x16))]))
    env = limits_plan(profile=["vanilla", str(mod)], limit=500)
    assert rows(env)["img.archives"]["need"] == 9
    assert rows(env)["img.archives"]["risk"] == "exceeded"
    assert rows(env)["store.txd"]["need"] == 10
    assert ini(env, "fastman92").getint("IMG LIMITS", "Max number of IMG archives") >= 9


def test_effective_archive_manifest_can_add_remove_and_order_archives(games, tmp_path):
    clean, _ = games
    write(clean, "data/old.img", img([("old.txd", chunk(0x16))]))
    write(clean, "data/new.img", img([("new.txd", chunk(0x16)), ("lamp.dff", dff(99))]))
    write(clean, "data/gta.dat", GTA_DAT + "IMG data/old.img\n")
    assert rows(limits_plan(profile=["vanilla"], limit=500))["store.txd"]["need"] == 5
    mod = tmp_path / "manifest"
    write(mod, "gta.dat", GTA_DAT + "IMG data/new.img\nIMG data/new.img\n")
    r = rows(limits_plan(profile=["vanilla", str(mod)], limit=500))
    assert r["store.txd"]["need"] == 5  # the unregistered old.img no longer contributes
    assert r["img.archives"]["need"] == 4  # repeated registration counts once
    assert r["img.files"]["need"] == 6  # anim/cuts archives do not occupy streaming IMG slots
    assert r["fx.dff"]["need"] == 2  # earlier gta3.img wins over the added lamp.dff


def test_replacement_img_replaces_entries_without_adding_a_slot(games, tmp_path):
    mod = tmp_path / "archive_replacement"
    write(mod, "gta3.img", img([("basecar.dff", dff(3))]))
    r = rows(limits_plan(profile=["vanilla", str(mod)], limit=500))
    assert r["img.archives"]["need"] == 3
    assert r["img.entries"]["need"] == 1
    assert r["fx.dff"]["need"] == 3
    assert r["ipl.instances"]["need"] == 2
    assert r["store.col"]["need"] == 1


def test_clothing_namespace_does_not_supply_ordinary_models_or_txd_slots(games, tmp_path):
    mod = tmp_path / "clothing"
    write(mod, "player.img/lamp.dff", dff(8))
    write(mod, "player.img/clothing.txd", chunk(0x16))
    env = limits_plan(profile=["vanilla", str(mod)], limit=500)
    r = rows(env)
    assert r["store.txd"]["need"] == 4
    assert r["txd.files"]["need"] == 5
    assert r["fx.dff"]["need"] == 2


def test_registered_loose_col_replacement_and_external_colfile(games, tmp_path):
    clean, _ = games
    write(clean, "models/coll/registered.col", collision("crate"))
    write(clean, "data/extra.col", collision("lamp"))
    write(clean, "data/default.dat", "IDE data/core.ide\nCOLFILE 0 models/coll/registered.col\nCOLFILE 0 data/extra.col\n")
    before = rows(limits_plan(profile=["vanilla"], limit=500))
    mod = tmp_path / "collision_replacement"
    write(mod, "registered.col", collision("crate") + collision("lamp"))
    after = rows(limits_plan(profile=["vanilla", str(mod)], limit=500))
    assert after["col.models"]["need"] == before["col.models"]["need"] + 1
    assert after["store.col"]["need"] == before["store.col"]["need"]


def test_override_only_data_uses_winner_of_multiple_mods(games, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    line = " ".join(["0"] * 52) + "\n"
    write(a, "timecyc.dat", line * 2)
    write(b, "timecyc.dat", line * 3)
    from satk.limits.planner import _world

    with _world(["vanilla", str(a), str(b)], None, "installed") as world:
        expected = 2 if world.mods[-1].name == "a" else 3
    env = limits_plan(profile=["vanilla", str(a), str(b)], limit=500)
    assert rows(env)["timecyc.entries"]["need"] == expected
    assert not any("timecyc.dat" in w and "unported" in w for w in env["warn"])


def test_duplicate_ids_count_allocations_and_keep_sparse_id_capacity(games, tmp_path):
    mod = addon(tmp_path / "duplicates", "extra", "cars\n" + car(400, "replacement") + "\nend\n")
    env = limits_plan(profile=["vanilla", str(mod)], limit=500)
    r = rows(env)
    assert r["model.vehicle"]["need"] == 2
    assert r["model.ids"]["need"] == 8
    assert r["model.id_span"]["stock"] == 20000
    assert any(w.startswith("DUPLICATE_ID:") for w in env["warn"])


def test_repeated_ide_registration_counts_each_allocation(games, tmp_path):
    mod = tmp_path / "repeated_manifest"
    write(mod, "default.dat", "IDE data/core.ide\nIDE data/core.ide\n")
    env = limits_plan(profile=["vanilla", str(mod)], limit=500)
    assert rows(env)["model.vehicle"]["need"] == 2
    assert rows(env)["model.ids"]["need"] == 8


@pytest.mark.parametrize("text,key", [
    ("\xa3 CUSTOM MELEE 1.0 1.0 333 -1 0 UNARMED 4 1 null\n", "weapons.infos"),
    ("basecar, crate, fence\n", "carmods.members"),
])
def test_unported_readme_data_does_not_silently_keep_the_base_count(games, tmp_path, text, key):
    mod = tmp_path / "readme_data"
    write(mod, "readme.txt", text)
    env = limits_plan(profile=["vanilla", str(mod)], limit=500)
    assert rows(env)[key]["risk"] == "unknown"
    assert any("unresolved readme" in w for w in env["warn"])


def test_missing_resources_and_undefined_placements_cannot_be_a_complete_scenario(games, tmp_path):
    mod = tmp_path / "missing"
    write(mod, "core.ide", CORE.replace("fence, shared", "fence, absent"))
    write(mod, "map_stream0.ipl", binary_ipl([(1001, 10, 0), (19999, 10, 0)]))
    env = limits_plan(profile=["vanilla", str(mod)], area=[10, 0, 1], limit=500)
    r = rows(env)
    assert r["streaming.memory"]["basis"] == "lower_bound"
    assert r["pool.buildings"]["risk"] == "runtime"
    detail = saved(env)["details"]["streaming.memory"]["count"]
    assert detail["missing_txds"] == ["absent"]
    assert detail["unresolved_placements"] == [19999]


def test_declared_adjuster_capacity_is_compared_but_not_claimed_active(games, tmp_path):
    clean, _ = games
    write(clean, "scripts/fastman92limitAdjuster_GTASA.ini", "[IDE LIMITS]\nVehicle Models = 5000 ; existing\n")
    write(clean, "III.VC.SA.LimitAdjuster.ini", "[SALIMITS]\nVehicleModels = unlimited\n")
    mod = addon(tmp_path / "many", "many", "cars\n" + "\n".join(car(2000 + i, f"car{i}") for i in range(212)) + "\nend\n")
    env = limits_plan(profile=["vanilla", str(mod)], limit=500)
    detail = saved(env)["details"]["model.vehicle"]["configured"]
    f92 = next(d for d in detail if d["adjuster"] == "fastman92")
    assert f92["capacity"] == 5000 and f92["headroom"] == 4787
    assert f92["runtime_verified"] is False
    assert ini(env, "fastman92").getint("IDE LIMITS", "Vehicle Models") == 5000
    assert not ini(env, "open_limit_adjuster").has_option("SALIMITS", "VehicleModels")


def test_ola_memory_suggestion_respects_its_2_gib_maximum():
    from satk.limits.advice import fragments
    from satk.limits.catalog import load
    from satk.limits.inventory import Measure

    limits = [l for l in load() if l.key == "streaming.memory"]
    p = configparser.ConfigParser()
    p.read_string(fragments(limits, {"streaming.memory": Measure(2000 * 1024 * 1024)})["open_limit_adjuster"])
    assert p.getint("SALIMITS", "MemoryAvailable") == 2048
    text = fragments(limits, {"streaming.memory": Measure(2100 * 1024 * 1024)})["open_limit_adjuster"]
    p = configparser.ConfigParser()
    p.read_string(text)
    assert not p.sections() and "no sufficient setting" in text


def test_zip_is_read_in_place(games, tmp_path):
    zpath = tmp_path / "pack.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("pack/data/gta.dat", GTA_DAT + "IDE data/added.ide\n")
        z.writestr("pack/data/added.ide", "cars\n" + car(450, "zipcar") + "\nend\n")
    before = zpath.read_bytes()
    env = limits_plan(profile=["vanilla", str(zpath)], limit=500)
    assert rows(env)["model.vehicle"]["need"] == 2
    assert zpath.read_bytes() == before and not (tmp_path / "pack").exists()


def test_streamed_car_generators_are_not_summed_as_simultaneously_live(games, tmp_path):
    mod = tmp_path / "generators"
    write(mod, "map_stream0.ipl", binary_ipl([(1001, 0, 0)], cars=300))
    write(mod, "map_stream1.ipl", binary_ipl([(1001, 1000, 0)], cars=300))
    r = rows(limits_plan(profile=["vanilla", str(mod)], limit=500))
    assert r["ipl.car_records"]["need"] == 600
    assert r["ipl.cars"]["need"] == 300
    assert r["ipl.cars"]["basis"] == "lower_bound"
    assert r["ipl.cars"]["risk"] != "exceeded"


def test_area_includes_whole_streamed_block_and_replacement_not_base_twice(games, tmp_path):
    mod = tmp_path / "blocks"
    write(mod, "map_stream0.ipl", binary_ipl([(1001, 10, 0), (1001, 500, 0)]))
    env = limits_plan(profile=["vanilla", str(mod)], area=[10, 0, 1], limit=500)
    r = rows(env)
    assert r["pool.buildings"]["need"] == 3  # one permanent + both instances in the replacement block
    assert r["streaming.memory"]["need"] == 4096
    assert r["streaming.memory"]["basis"] == "scenario"


def test_large_txd_uses_sector_budget_and_ini_memory_units(games, tmp_path):
    mod = tmp_path / "large"
    path = write(mod, "basecar.txd", chunk(0x16))
    with path.open("r+b") as f:
        f.truncate(51 * 1024 * 1024 + 1)
    env = limits_plan(profile=["vanilla", str(mod)], limit=500)
    r = rows(env)["streaming.memory"]
    assert r["need"] == 51 * 1024 * 1024 + 4096  # TXD rounded up + one DFF sector
    assert r["risk"] == "exceeded"
    assert 51 < ini(env, "fastman92").getint("STREAMING", "Memory available") < 100
    assert 51 < ini(env, "open_limit_adjuster").getint("SALIMITS", "MemoryAvailable") < 100


def test_path_grid_and_navi_overflows_are_counted_before_codec_capacity_rejection(games, tmp_path):
    mod = tmp_path / "paths"
    raw = struct.pack("<5I", 0, 0, 0, 1025, 0) + bytes(1025 * 14)
    write(mod, "gta3.img/nodes64.dat", raw)
    env = limits_plan(profile=["vanilla", str(mod)], limit=500)
    r = rows(env)
    assert r["paths.files"]["need"] == 65
    assert r["paths.navis_per_region"]["need"] == 1025
    assert r["paths.navis_per_region"]["risk"] == "exceeded"
    assert "No matching signature" in saved(env)["details"]["paths.files"]["known_crashes"]


def test_unported_multi_mod_merge_is_unknown(games, tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    write(a, "weapon.dat", "ENDWEAPONDATA\n")
    write(b, "weapon.dat", "ENDWEAPONDATA\n")
    env = limits_plan(profile=["vanilla", str(a), str(b)], limit=500)
    assert rows(env)["weapons.infos"]["risk"] == "unknown"
    assert any("unported merge" in w for w in env["warn"])


def test_ignored_cargrp_tail_is_visible_without_a_false_pool_overflow(games, tmp_path):
    mod = tmp_path / "groups"
    write(mod, "cargrp.dat", ",".join(f"car{n}" for n in range(29)))
    env = limits_plan(profile=["vanilla", str(mod)], limit=500)
    assert rows(env)["cargrp.members"]["need"] == 23
    assert rows(env)["cargrp.requested"]["need"] == 29
    assert any(w.startswith("TRUNCATED:") for w in env["warn"])


def test_outputs_are_deterministic_and_game_is_read_only(games):
    root = games[0]
    before = {p.relative_to(root): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()}
    first = limits_plan(profile=["vanilla"], limit=1)
    mtimes = {p: Path(p).stat().st_mtime_ns for p in first["files"].values()}
    second = limits_plan(profile=["vanilla"], limit=500)
    assert first["files"] == second["files"]
    assert mtimes == {p: Path(p).stat().st_mtime_ns for p in first["files"].values()}
    assert before == {p.relative_to(root): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()}
    assert not ini(first, "fastman92").sections()


def test_existing_modified_artifact_is_preserved(games):
    env = limits_plan(profile=["vanilla"])
    path = Path(env["files"]["fastman92"])
    path.write_text("user edit\n", encoding="utf-8")
    with pytest.raises(SatkError, match="refusing to replace") as err:
        limits_plan(profile=["vanilla"])
    assert err.value.code == "EXISTS" and path.read_text(encoding="utf-8") == "user edit\n"


@pytest.mark.parametrize("kwargs", [{"area": [0, 0]}, {"area": [0, 0, 0]}, {"area": [0, 0, float("nan")]},
                                    {"area": [float("inf"), 0, 1]}, {"cursor": "-1"}, {"cursor": "o1"},
                                    {"profile": [""]}])
def test_invalid_parameters_are_structured_errors(games, kwargs):
    with pytest.raises(SatkError) as err:
        limits_plan(**kwargs)
    assert err.value.code == "BAD_PARAMS"


@pytest.mark.parametrize("rel,raw", [("base.col", b"COL3"), ("map_stream0.ipl", b"bnry"),
                                    ("gta3.img/nodes1.dat", b"\0" * 4), ("basecar.dff", b"bad")])
def test_malformed_counted_input_is_not_reported_as_safe(games, tmp_path, rel, raw):
    mod = tmp_path / "broken"
    write(mod, rel, raw)
    with pytest.raises(SatkError) as err:
        limits_plan(profile=["vanilla", str(mod)])
    assert err.value.code == "BAD_PARAMS"


def test_missing_mod_and_broken_zip_fail(games, tmp_path):
    with pytest.raises(SatkError) as err:
        limits_plan(profile=["vanilla", str(tmp_path / "missing")])
    assert err.value.code == "NOT_FOUND"
    bad = write(tmp_path, "broken.zip", b"not a ZIP")
    with pytest.raises(SatkError) as err:
        limits_plan(profile=["vanilla", str(bad)])
    assert err.value.code == "BAD_PARAMS"


def test_dff_uv_animation_dictionary_can_precede_the_clump(games, tmp_path):
    mod = tmp_path / "uv_animation"
    write(mod, "lamp.dff", chunk(0x2B) + dff(7))
    assert rows(limits_plan(profile=["vanilla", str(mod)], limit=500))["fx.dff"]["need"] == 7
