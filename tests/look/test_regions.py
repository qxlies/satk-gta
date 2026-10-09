"""Review regions (contract K8) and the leak report, without Blender: data files, kind rules, sheets, leak.json."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from satk.core.errors import SatkError
from satk.core.registry import get_op
from satk.look import regions as RG
from satk.look import review


def test_every_kit_kind_has_a_valid_region_file():
    from satk.kit import kinds as K

    assert set(K.names()) <= set(RG.kinds()), sorted(set(K.names()) - set(RG.kinds()))
    for k in RG.kinds():
        d = RG.load(k)
        assert d["kind"] == k and d["version"] == 1
        assert RG.validate(d) == [], (k, RG.validate(d))
        assert d.get("summary") and all(r.get("summary") for r in d["regions"]), k


@pytest.mark.parametrize("kind, names", [
    ("automobile", {"front", "rear", "left", "right", "wheels", "underside", "interior", "engine_bay", "roof"}),
    ("bike", {"front", "rear", "left", "right", "cockpit", "engine", "wheels"}),
    ("building", {"facade_north", "facade_east", "facade_south", "facade_west", "roof", "entrance", "lod"}),
    ("prop", {"sides", "top", "base", "details"}),
    ("weapon", {"left", "right", "muzzle", "grip", "top"}),
    ("interior_shell", {"overview", "walls", "ceiling", "floor"}),
    ("heli", {"front", "tail", "left", "right", "rotor", "skids", "cockpit", "underside"}),
    ("boat", {"bow", "stern", "left", "right", "deck", "cockpit", "underside"}),
])
def test_region_sets_per_kind(kind, names):
    assert {r["name"] for r in RG.load(kind)["regions"]} == names


def test_load_aliases_and_errors():
    assert RG.load("car")["kind"] == "automobile"
    with pytest.raises(SatkError) as e:
        RG.load("spaceship")
    assert e.value.code == "NOT_FOUND"


def test_select_names_all_and_typos():
    d = RG.load("automobile")
    assert [r["name"] for r in RG.select(d, None)] == [r["name"] for r in d["regions"]]
    assert [r["name"] for r in RG.select(d, ["all"])] == [r["name"] for r in d["regions"]]
    assert [r["name"] for r in RG.select(d, ["wheels", "front"])] == ["front", "wheels"]  # file order
    with pytest.raises(SatkError) as e:
        RG.select(d, ["wheel"])
    assert e.value.code == "BAD_PARAMS" and "wheels" in e.value.did_you_mean


def test_leak_rules_follow_the_engine():
    car = RG.leak_config(RG.load("automobile"))
    assert car["cull"] is False and car["gap"] and car["inside"] and car["inside_regions"] == ["wheels"]
    assert car["inside_depth"] == RG.INSIDE_DEPTH and car["inside_cm2"] == RG.INSIDE_CM2
    prop = RG.leak_config(RG.load("prop"))
    assert prop["cull"] is True and prop["see_through_block"] and not prop["inside"]
    assert RG.leak_config(RG.load("prop"), ide_flags=RG.NO_CULL_FLAG | 4)["cull"] is False  # IDE no-cull flag
    weapon = RG.leak_config(RG.load("weapon"))          # holes block; a one-sided card from behind is information
    assert weapon["see_through_block"] is True and not weapon["gap"] and not weapon["slit"]   # guns: open by design
    assert RG.leak_config(car_def := RG.load("automobile"))["slit"] is False and car_def["kind"] == "automobile"
    assert RG.leak_config(RG.load("plane"))["inside_regions"] == ["left", "right", "wings", "tail"]
    assert RG.leak_config(RG.load("interior_shell"))["escape_max_m"] == 0.12
    for k in RG.VEHICLE_KINDS:
        assert RG.leak_config(RG.load(k))["cull"] is False, k


def test_validate_finds_mistakes():
    bad = {"kind": "x", "regions": [
        {"name": "a", "cameras": [{"az": "out", "el": 5}]},                 # out needs frames
        {"name": "a", "cameras": [{"az": 0, "el": 95}], "box": {"y": [0.5, 0.2]}},
        {"name": "c", "cameras": [], "glass": "show", "states": ["broken"], "leak": False},
    ]}
    probs = RG.validate(bad)
    assert any("needs frames" in p for p in probs) and any("repeated" in p for p in probs)
    assert any("el 95" in p for p in probs) and any("box y" in p for p in probs)
    assert any("no cameras" in p for p in probs) and any("glass" in p for p in probs)
    assert any("state" in p for p in probs)


def test_guess_kind_from_frames(m):
    assert review.guess_kind(m.car_dff()) == "automobile"
    assert review.guess_kind(m.box_dff()) == "prop"
    assert review.guess_kind(b"not a dff") is None


def _cells(tmp_path, png, n_rows: int, n_cols: int) -> list:
    return [[r, c, str(png(tmp_path / f"c{r}{c}.png", 64, 64, (40 * r + 30, 60, 90 + 20 * c)))]
            for r in range(n_rows) for c in range(n_cols)]


def test_region_sheets_share_one_run_number(tmp_path, png, satk_home):
    out = satk_home / "work" / "out" / "preview" / "x"
    out.mkdir(parents=True)
    regs = [{"name": "front", "summary": "nose"}, {"name": "wheels", "summary": "arches"},
            {"name": "roof", "summary": "top"}]
    views = [{"label": "front.1", "region": "front"}, {"label": "front.2", "region": "front"},
             {"label": "wheels.wheel_lf_dummy.1", "region": "wheels"}]  # roof framed nothing
    res = {"cells": _cells(tmp_path, png, 2, 3), "rows": ["ok/game", "ok/leak"], "cols": [v["label"] for v in views],
           "views": views}
    r1 = review.region_sheets(res, regs, title="t", out_dir=out)
    assert r1["n"] == 1 and [row[0] for row in r1["regions"]] == ["front", "wheels"]
    assert Path(r1["index"]).name == "preview-001-index.jpg"
    assert Path(r1["regions"][0][1]).name == "preview-001-front.jpg" and r1["regions"][0][2] == 2
    r2 = review.region_sheets(res, regs, title="t", out_dir=out)
    assert r2["n"] == 2 and Path(r2["regions"][1][1]).name == "preview-002-wheels.jpg"
    with pytest.raises(SatkError):
        review.region_sheets({"cells": [], "views": []}, regs, title="t", out_dir=out)


def test_leak_report_and_package_file(satk_home, tmp_path):
    pkg = satk_home / "work" / "out" / "kit" / "mycar" / "modloader" / "mycar"
    pkg.mkdir(parents=True)
    dff = pkg / "mycar.dff"
    dff.write_bytes(b"dff bytes")
    gap = {"id": "L1", "kind": "inside", "blocking": True, "region": "wheels", "view": "wheels.wheel_lf_dummy.1",
           "views": ["wheels.wheel_lf_dummy.1"], "px": 80, "area_cm2": 72.5, "point": [-0.92, 1.47, -0.1],
           "near": "chassis", "deep_m": 1.92}
    info = dict(gap, id="I1", blocking=False, area_cm2=30.0)
    lk = {"gaps": [gap], "info": [info], "params": {"kind": "automobile", "cull": False},
          "views": [{"view": "wheels.wheel_lf_dummy.1", "region": "wheels", "state": "ok", "gaps": [gap, info]}]}
    doc = review.leak_report(lk, subject=str(dff), kind="automobile", dff=str(dff), sheet="s.jpg")
    assert doc["leak"] == 1 and doc["clean"] is False and doc["blocking"] == 1 and doc["counts"] == {"inside": 1}
    assert doc["dff_sha256"] and doc["views"] == [{"view": "wheels.wheel_lf_dummy.1", "region": "wheels",
                                                    "state": "ok", "gaps": 2}]
    assert doc["info"][0]["id"] == "I1" and doc["sheet"] == "s.jpg"
    path = review.write_leak(pkg, doc)
    assert path.endswith("/checks/mycar.leak.json")
    back = json.loads((pkg / "checks" / "mycar.leak.json").read_text(encoding="utf-8"))
    assert back["gaps"][0]["point"] == [-0.92, 1.47, -0.1]
    clean = review.leak_report({"gaps": [], "views": []}, subject="s", kind="prop")
    assert clean["clean"] is True and clean["blocking"] == 0 and "info" not in clean
    with pytest.raises(SatkError) as e:
        review.write_leak(tmp_path / "nope", doc)
    assert e.value.code == "NOT_FOUND"


def test_ops_registered_with_their_parameters():
    leak = get_op("look.leak")
    assert leak.mcp in (None, False) and leak.long_running
    names = {p.name for p in leak.params}
    assert {"subject", "kind", "regions", "views", "package", "size", "cull"} <= names
    prev = {p.name for p in get_op("blender.preview").params}
    assert {"regions", "kind"} <= prev


def test_leak_rejects_bad_size():
    with pytest.raises(SatkError) as e:
        get_op("look.leak").call({"subject": "model:400", "size": 20})
    assert e.value.code == "BAD_PARAMS"


def test_the_kind_of_a_kit_export_comes_from_its_inventory_sidecar(tmp_path):
    from satk.look.inputs import Entry

    dff = tmp_path / "mybin.dff"
    dff.write_bytes(b"x")
    ent = Entry(key="e0", label="mybin.dff", kind="file", path=str(dff))
    (tmp_path / "mybin.inventory.json").write_text(json.dumps({"inventory": {"kind": "boat", "items": []}}),
                                                    encoding="utf-8")
    assert review.subject_kind(ent) == ("boat", "inventory sidecar")
    assert review.subject_kind(ent, asset={"kind": "prop"}) == ("prop", "asset.json")      # the project wins
    (tmp_path / "mybin.inventory.json").write_text(json.dumps({"inventory": {"kind": "nope"}}), encoding="utf-8")
    assert review._sidecar_kind(str(dff)) is None


def test_leak_coverage_and_the_run_record(satk_home):
    defn = {"kind": "prop", "regions": [{"name": "sides"}, {"name": "top"}, {"name": "base"},
                                        {"name": "close", "leak": False}]}
    full = review.coverage(defn, ["sides", "top", "base"])
    assert full == {"full": True, "regions": ["sides", "top", "base"], "missing": []}
    part = review.coverage(defn, ["top"])
    assert part["full"] is False and part["missing"] == ["sides", "base"] and "regions not checked" in part["why"]
    assert review.coverage(defn, None, views=["top"])["full"] is False
    assert "cull" in review.coverage(defn, ["sides", "top", "base"], cull=True)["why"]
    assert "below the default" in review.coverage(defn, ["sides", "top", "base"], size=128)["why"]
    pkg = satk_home / "work" / "out" / "kit" / "box" / "files" / "box"
    pkg.mkdir(parents=True)
    dff = pkg / "box.dff"
    dff.write_bytes(b"box bytes")
    doc = review.leak_report({"gaps": [], "views": []}, subject=str(dff), kind="prop", dff=str(dff), cover=full)
    assert review.record_mismatch(doc) == "satk has no record of a look.leak run on this export"
    rec = review.write_record(doc, job="j1")
    assert rec.endswith(f"/out/leak/records/{doc['dff_sha256']}.json") and review.record_mismatch(doc) is None
    forged = dict(doc, coverage=part)
    assert "its full (False) differs" in review.record_mismatch(forged)
    (pkg / "box.inventory.json").write_text(json.dumps({"stem": "box", "lod": "lodbox"}), encoding="utf-8")
    assert review.lod_sibling_of(pkg / "lodbox.dff") == "box" and review.lod_sibling_of(dff) is None
