"""satk.style.classes: taxonomy, aliases, size buckets and the map classifier (synthetic facts only)."""

from __future__ import annotations

import pytest

from satk.core.errors import SatkError
from satk.style import classes as C


def test_taxonomy_is_complete_and_english():
    t = C.taxonomy()
    fams = {v["family"] for v in t["classes"].values()}
    assert fams == {"vehicle", "map", "ped", "weapon", "pickup", "upgrade"}
    for name, v in t["classes"].items():
        assert v["what"].isascii() and v["kind"], name
        if "parent" in v:
            assert v["parent"] in t["classes"], name
    for vt in C.VEHICLE_TYPES:
        assert C.family(vt) == "vehicle"
    # every curated body name is unique and every alias points at a class
    names = [n for b in t["car_bodies"].values() for n in b.split()]
    assert len(names) == len(set(names))
    assert all(v in t["classes"] for v in t["aliases"].values())


@pytest.mark.parametrize("given,want", [("sedan", "car.sedan"), ("automobile", "car"), ("Car.Sedan", "car.sedan"),
                                        ("vehicle_upgrade", "upgrade"), ("prop@1-2m", "prop@1-2m"),
                                        ("street_prop@0.5-1m", "prop@0.5-1m"), ("motorbike", "bike")])
def test_resolve_aliases(given, want):
    assert C.resolve(given) == want


def test_resolve_errors():
    with pytest.raises(SatkError) as e:
        C.resolve("sedna")
    assert e.value.code == "BAD_PARAMS" and "sedan" in e.value.did_you_mean
    with pytest.raises(SatkError):
        C.resolve("prop@9-10m")
    with pytest.raises(SatkError):
        C.resolve("car@1-2m")                       # buckets are for map classes only


def test_buckets_and_fallback_chain():
    assert C.size_bucket(0.2) == "0-0.5m" and C.size_bucket(1.0) == "1-2m" and C.size_bucket(5000) == "256m+"
    assert C.size_bucket(None) is None
    assert C.fallback_chain("prop@1-2m") == ["prop@1-2m", "prop"]
    assert C.fallback_chain("car.sedan") == ["car.sedan", "car"]
    assert C.peer_key("prop", "1-2m") == "prop@1-2m" and C.peer_key("car.sedan", "1-2m") == "car.sedan"


@pytest.mark.parametrize("facts,want", [
    ({"sec": "cars", "type": "car", "name": "premier"}, ("car.sedan", None)),
    ({"sec": "cars", "type": "car", "name": "mycar"}, ("car", None)),
    ({"sec": "cars", "type": "bike", "name": "pcj600"}, ("bike", None)),
    ({"sec": "peds", "name": "wfyclot"}, ("ped", None)),
    ({"sec": "weap", "name": "colt45"}, ("weapon", None)),
    ({"sec": "objs", "id": 1240, "name": "health"}, ("pickup", None)),
    ({"sec": "objs", "ide": "data/maps/veh_mods/veh_mods.ide", "name": "spl_a"}, ("upgrade", None)),
    ({"sec": "objs", "name": "lodblock", "draw": 1000, "dims": (50, 50, 10)}, ("lod", "32-64m")),
    ({"sec": "objs", "name": "x", "as_lod": 3, "dims": (50, 50, 10)}, ("lod", "32-64m")),
    ({"sec": "objs", "ide": "data/maps/interior/int_la.ide", "name": "room", "dims": (12, 9, 4)},
     ("interior_shell", "8-16m")),
    ({"sec": "objs", "name": "chair", "n_int": 4, "n_ext": 0, "dims": (0.6, 0.6, 1.0)}, ("interior_prop", "1-2m")),
    ({"sec": "objs", "name": "sm_palm", "flags": 16384, "dims": (4, 4, 9)}, ("vegetation", "8-16m")),
    ({"sec": "objs", "ide": "data/maps/generic/dynamic.ide", "name": "barrel", "dims": (0.6, 0.6, 1.1)},
     ("prop", "1-2m")),
    ({"sec": "objs", "name": "decal", "flags": 4, "dims": (30, 30, 0.1)}, ("overlay", "16-32m")),
    ({"sec": "objs", "name": "roadsec", "flags": 1, "dims": (60, 60, 2)}, ("terrain", "32-64m")),
    ({"sec": "objs", "name": "block", "dims": (40, 30, 25), "col": {"faces": 10}}, ("building", "32-64m")),
    ({"sec": "tobj", "name": "neon", "dims": (10, 1, 3), "col": {"faces": 2}}, ("time_object", "8-16m")),
])
def test_classify(facts, want):
    assert C.classify(facts) == want
