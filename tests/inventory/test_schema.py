"""The inventory file (K6): validation with readable errors, the JSON schema and the starter lists of every kind."""

from __future__ import annotations

import copy
import json

import pytest

from satk.core import resources
from satk.core.errors import SatkError
from satk.inventory import schema as SC
from satk.inventory import starter as ST

GOOD = {
    "kind": "prop", "detail": "simple",
    "items": [
        {"id": "I01", "name": "Bin body", "region": "body", "category": "shell", "stage": "G1", "key": "main_body",
         "construction": "8-sided lathe body with a chamfered rim", "attaches_to": None},
        {"id": "I02", "name": "Feet", "region": "base", "category": "structure", "stage": "G1", "key": "base",
         "construction": "four rounded_box feet flat on the ground", "attaches_to": "I01", "count": 4},
        {"id": "I03", "name": "Lid", "region": "top", "category": "shell", "stage": "G2", "key": "top",
         "construction": "lofted lid with a lip pushed into the rim", "attaches_to": ["I01"]},
        {"id": "I04", "name": "Wheel pair", "region": "details", "category": "detail", "stage": "G2",
         "key": "secondary_parts", "construction": "two lathe wheels on an axle stub", "attaches_to": "I01"},
    ],
}


def _errs(data, **kw) -> list[str]:
    return SC.validate(data, **kw)["errors"]


def test_a_good_inventory_is_clean():
    r = SC.validate(GOOD, strict=True)
    assert r["errors"] == [] and r["warnings"] == [] and r["items"] == 4 and r["required"] == 4


@pytest.mark.parametrize("change, needle", [
    (lambda d: d["items"][1].update(id="I1"), "items[1].id: must be like I01"),
    (lambda d: d["items"][1].update(id="I01"), "I01: duplicate id"),
    (lambda d: d["items"][2].update(attaches_to="I09"), "I03.attaches_to: no item 'I09'"),
    (lambda d: d["items"][2].update(attaches_to="I03"), "I03.attaches_to: an item cannot attach to itself"),
    (lambda d: d["items"][0].update(attaches_to="I03"), "attaches_to forms a cycle: I01 -> I03 -> I01"),
    (lambda d: d["items"][2].update(category="glas"), "did you mean glass"),
    (lambda d: d["items"][2].update(stage="G5"), "I03.stage: must be one of G1, G2, G3, G4"),
    (lambda d: d["items"][2].update(construction="box"), "I03.construction: say how it is built"),
    (lambda d: d["items"][2].pop("attaches_to"), "I03: 'attaches_to' is missing (null for a root item)"),
    (lambda d: d["items"][1].update(count=0), "I02.count: an integer 1-64"),
    (lambda d: d.update(detail="ultra"), "detail: must be one of simple, standard, hero"),
    (lambda d: d.update(items=[]), "items: a non-empty list"),
])
def test_errors_name_the_item_and_the_fix(change, needle):
    data = copy.deepcopy(GOOD)
    change(data)
    errs = _errs(data)
    assert any(needle in e for e in errs), errs


def test_regions_unknown_fields_and_dropped_starter_items():
    data = copy.deepcopy(GOOD)
    data["items"][2]["region"] = "lid"
    data["items"][2]["colour"] = "red"
    r = SC.validate(data)
    assert any("region 'lid' is not a review region of prop" in w for w in r["warnings"])
    assert any("unknown field(s) ['colour']" in w for w in r["warnings"])
    del data["items"][3]                                   # secondary_parts dropped
    r = SC.validate(data)
    assert r["dropped"] == ["secondary_parts"] and any("not in the inventory or not required: secondary_parts"
                                                       in w for w in r["warnings"])
    assert any("secondary_parts" in e for e in _errs(data, strict=True))
    data["waived"] = [{"key": "secondary_parts", "why": "a plain bin has no wheels or doors"}]
    assert _errs(data, strict=True) == []
    data["waived"] = [{"key": "secondary_parts", "why": "no"}]
    assert any(e.startswith("waived[0]") for e in _errs(data))
    data["detail"] = "hero"                                # hero wants more starter items
    assert len(SC.validate(data, strict=True)["dropped"]) == len(ST.instantiate("prop", "hero")["items"]) - 3


def test_check_raises_with_the_first_errors_and_load_reads_utf8_bom(tmp_path):
    bad = copy.deepcopy(GOOD)
    bad["items"][0]["stage"] = "G9"
    with pytest.raises(SatkError) as e:
        SC.check(bad)
    assert e.value.code == "BAD_PARAMS" and "I01.stage" in e.value.msg and e.value.data["errors"]
    f = tmp_path / "inv.json"
    f.write_text("﻿" + json.dumps(GOOD), encoding="utf-8")
    assert SC.load_file(f)["kind"] == "prop"
    f.write_text("[1]", encoding="utf-8")
    with pytest.raises(SatkError):
        SC.load_file(f)


def test_helpers():
    assert SC.parse_ids("I05, I06,I05") == ["I05", "I06"] and SC.parse_ids(["I01"]) == ["I01"]
    assert SC.parse_ids(None) == [] and SC.detail_rank("standard") == 1
    assert SC.gap_mm({"category": "panel"}) == 30.0 and SC.gap_mm({"category": "trim"}) == 10.0
    assert SC.gap_mm({"category": "panel", "max_gap_mm": 5}) == 5.0


def test_json_schema_matches_the_validator():
    doc = resources.read_json("kit", "inventory", "schema.json")
    item = doc["$defs"]["item"]
    assert set(doc["required"]) == {"kind", "detail", "items"}
    assert item["properties"]["category"]["enum"] == list(SC.CATEGORIES)
    assert item["properties"]["stage"]["enum"] == list(SC.STAGES)
    assert doc["properties"]["detail"]["enum"] == list(SC.DETAILS)
    assert item["properties"]["id"]["pattern"] == SC.ID_RE.pattern
    assert set(item["required"]) == set(SC._REQUIRED)            # noqa: SLF001
    assert set(item["properties"]) == set(SC.FIELDS)


# --------------------------------------------------------------------------- starters


def test_every_kit_kind_has_a_starter():
    kit = resources.read_json("kit", "kinds.json")["kinds"]
    assert set(ST.kinds()) == set(kit), set(kit) ^ set(ST.kinds())


@pytest.mark.parametrize("kind", ST.kinds())
def test_starters_are_valid_at_every_level(kind):
    st = ST.load(kind)
    assert st["format"] == ST.FORMAT and st["kind"] == kind and st["regions"] and st["summary"]
    keys = [s["key"] for s in st["items"]]
    assert len(keys) == len(set(keys))
    sizes = []
    for d in SC.DETAILS:
        inv = ST.instantiate(kind, d)
        r = SC.validate(inv, strict=True)
        assert r["errors"] == [] and r["warnings"] == [], (kind, d, r)
        sizes.append(len(inv["items"]))
        ids = [i["id"] for i in inv["items"]]
        assert ids == [f"I{n:02d}" for n in range(1, len(ids) + 1)]
        assert all(i["region"] in st["regions"] for i in inv["items"])
        assert inv["items"][0]["attaches_to"] is None and inv["items"][0]["stage"] == "G1"
    assert sizes[0] <= sizes[1] <= sizes[2] and sizes[0] >= 3, sizes


def test_hero_is_richer_than_vanilla_where_it_matters():
    car = {i["key"]: i for i in ST.instantiate("automobile", "hero")["items"]}
    for k in ("engine_block", "engine_bay_walls", "door_cards", "seat_belts", "exhaust_system", "driveline",
              "headliner", "brake_discs"):
        assert k in car, k
    simple = {i["key"] for i in ST.instantiate("automobile", "simple")["items"]}
    assert "engine_block" not in simple and {"body_shell", "wheel", "wheel_arches", "mirrors"} <= simple
    assert car["wheel_arches"]["count"] == 4 and car["doors_front"]["frame"] == ["door_lf_ok", "door_rf_ok"]
    bld = {i["key"] for i in ST.instantiate("building", "hero")["items"]}
    assert {"roof_clutter", "signage", "entrance", "back_facades"} <= bld
    gun = {i["key"] for i in ST.instantiate("weapon", "hero")["items"]}
    assert {"sights", "rails", "muzzle_flash", "magazine"} <= gun


def test_starter_errors():
    with pytest.raises(SatkError) as e:
        ST.load("automobil")
    assert e.value.code == "NOT_FOUND" and "automobile" in e.value.did_you_mean
    with pytest.raises(SatkError):
        ST.instantiate("prop", "ultra")
    with pytest.raises(SatkError):
        ST.load("../x")
    s = ST.summary("bike")
    assert s["items"]["simple"] < s["items"]["hero"] and "cockpit" in s["regions"]


@pytest.mark.parametrize("kind", ST.kinds())
def test_regions_name_their_close_up_views(kind):
    vs = ST.views(kind)
    assert "overall" in vs and all(isinstance(v, list) for v in vs.values())
    assert all(vs[r] for r in vs if r != "overall" and kind != "interior_shell"), vs      # every area has a close-up
    try:                                     # the close-up regions of the look package, once they are installed
        look = resources.read_json("kit", "regions", f"{kind}.json")
    except SatkError:
        return
    names = {r["name"] for r in look.get("regions") or []}
    missing = sorted({v for vv in vs.values() for v in vv} - names)
    assert not missing, (kind, missing)


def test_the_builder_cannot_loosen_its_own_task_list():
    """Effort is no waiver, a starter item made optional is dropped, non-root items name their parent, a count stays,
    the tolerance is capped, and the commission (asset.json) binds the kind and the detail level."""
    data = copy.deepcopy(GOOD)
    data["waived"] = [{"key": "secondary_parts", "why": "not needed at game distance"}]
    del data["items"][3]
    assert any("'not needed' is not a reason to drop an item" in e for e in _errs(data))
    assert SC.banned_reason("two-door coupe") is None and SC.banned_reason("the hard top replaces it") is None
    assert SC.banned_reason("too complex for the budget") and SC.banned_reason("hard to see")
    data = copy.deepcopy(GOOD)
    data["items"][3]["required"] = False                     # secondary_parts made optional = dropped
    assert any("not required: secondary_parts" in e for e in _errs(data, strict=True))
    data = copy.deepcopy(GOOD)
    data["items"][3]["attaches_to"] = None                   # a detail is no root
    assert any(e.startswith(f"{data['items'][3]['id']}.attaches_to: a ") for e in _errs(data))
    data = copy.deepcopy(GOOD)
    data["items"][3]["max_gap_mm"] = 1000
    assert any("max_gap_mm: a number 0-150" in e for e in _errs(data))
    data["items"][3]["max_gap_mm"] = 90
    assert any("over 3x" in w for w in SC.validate(data)["warnings"])
    car = ST.instantiate("automobile", "simple")
    arches = next(i for i in car["items"] if i["key"] == "wheel_arches")
    arches.pop("count")
    assert any("(wheel_arches): count None is below the starter's 4" in e for e in _errs(car, strict=True))
    arches["count"] = 2
    car["waived"] = [{"key": "wheel_arches", "count": 2, "why": "a three-wheeler with two rear arches"}]
    assert _errs(car, strict=True) == []
    ok = ST.instantiate("prop", "standard")
    assert SC.validate(ok, commission={"kind": "prop", "detail": "standard"})["errors"] == []
    errs = SC.validate(ok, commission={"kind": "prop", "detail": "hero"})["errors"]
    assert any("commissioned at hero" in e for e in errs)
    errs = SC.validate(ok, commission={"kind": "building", "detail": "standard"})["errors"]
    assert any("commissioned as building" in e for e in errs)
    assert SC.verify_of({"category": "decal"}) == "texture" and SC.verify_of({"category": "trim"}) == "geometry"
    assert SC.verify_of({"category": "decal", "verify": "paint"}) == "paint"
