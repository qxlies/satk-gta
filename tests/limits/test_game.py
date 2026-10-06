"""Read-only acceptance on the configured games; derived files use a private work directory."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from satk.core import config
from satk.limits.ops import limits_plan

from .conftest import car, write
from .test_plan import ini, rows, saved

pytestmark = pytest.mark.game


@pytest.fixture
def private_work(tmp_path, monkeypatch):
    monkeypatch.setenv("SATK_PATHS_WORK", str(tmp_path / "work"))
    config.reset()
    try:
        yield
    finally:
        config.reset()


def test_vanilla_capacity_matches_stock_content(clean_root, private_work, repo_root):
    env = limits_plan(profile=["vanilla"], limit=500)
    report = saved(env)
    r = rows(report)
    golden = json.loads((repo_root / "tests/golden/index_vanilla.json").read_text(encoding="utf-8"))["metrics"]
    assert env["summary"]["exceeded"] == []
    for key, need, stock in (
        ("model.vehicle", 212, 212), ("model.ped", 276, 278), ("model.weapon", 50, 51),
        ("model.object", 14045, 14070), ("model.atomic", 13976, 14000),
        ("model.damageable", 69, 70), ("store.col", 252, 255), ("fx.ide", 97, 100),
        ("ipl.enex", 376, 400), ("model.ids", 14832, 20000), ("model.id_span", 18631, 20000),
    ):
        assert (r[key]["need"], r[key]["stock"], r[key]["headroom"]) == (need, stock, stock - need), key
    assert r["img.files"]["need"] == golden["img.archives"] == 8
    assert r["img.archives"]["need"] == 6  # cuts/anim archives are opened directly
    assert r["img.entries"]["need"] == golden["img.entries"] == 21058
    assert r["ipl.instances"]["need"] == 50935
    assert r["fx.dff"]["need"] == 18795
    assert report["exe"]["checks"]
    assert all(c["status"] == "matches" for c in report["exe"]["checks"].values())
    assert report["summary"]["runtime_verified"] is False
    assert r["pool.vehicle_structs"]["need"] == "runtime"
    assert not ini(env, "fastman92").sections()
    assert not ini(env, "open_limit_adjuster").sections()


def test_real_installed_profile_and_synthetic_vehicle_overflow(installed_root, private_work, tmp_path):
    before = saved(limits_plan(profile=["installed"], limit=1))
    mod = tmp_path / "limits_probe"
    # Mod Loader's existing readme reader merges this line into vehicles.ide.
    write(mod, "readme.txt", car(19699, "satklimitsprobe") + "\n")
    env = limits_plan(profile=["installed", str(mod)], limit=1)
    after = saved(env)
    assert Path(after["inputs"]["root"]) == installed_root
    assert rows(after)["model.vehicle"]["need"] == rows(before)["model.vehicle"]["need"] + 1
    assert any(m["name"] == "limits_probe" for m in after["inputs"]["mods"])
    # The configured stock install already has all 212 vehicle model slots in use.
    assert rows(after)["model.vehicle"]["risk"] == "exceeded"
    assert any(c["n"] == 244 for c in after["details"]["model.vehicle"]["known_crashes"])
    assert ini(env, "fastman92").getint("IDE LIMITS", "Vehicle Models") >= 213
    ola = ini(env, "open_limit_adjuster")
    if ola.has_option("SALIMITS", "VehicleModels"):
        assert ola.getint("SALIMITS", "VehicleModels") >= 213
    else:
        declared = after["details"]["model.vehicle"]["configured"]
        assert any(d["adjuster"] == "open_limit_adjuster" and d["comparison"] == "dynamic" for d in declared)
