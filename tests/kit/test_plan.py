"""Template plan helpers without game data: roles, slots, names, matrices, HAnim ids."""

from __future__ import annotations

import pytest

from satk.core.errors import SatkError
from satk.kit import plan as P


def _rgba(r, g, b, a=255) -> int:
    return (r << 24) | (g << 16) | (b << 8) | a


def test_role_of_vehicle_materials():
    v = "vehicle"
    assert P.role_of(_rgba(60, 255, 0), "vehiclegrunge256", v) == "paint1"
    assert P.role_of(_rgba(255, 0, 175), "vehiclegeneric256", v) == "paint2"
    assert P.role_of(_rgba(255, 175, 0), "vehiclelights128", v) == "lamp_fl"
    assert P.role_of(_rgba(255, 175, 0), "vehiclegeneric256", v) == "lens"   # a lamp key off the lights texture
    assert P.role_of(_rgba(255, 255, 255, 128), "vehiclegeneric256", v) == "glass"
    assert P.role_of(_rgba(255, 255, 255), "vehiclegeneric256", v) == "chrome"
    assert P.role_of(_rgba(133, 133, 133), "vehiclegeneric256", v) == "trim"
    assert P.role_of(_rgba(0, 0, 0), None, v) == "black"
    assert P.role_of(_rgba(255, 255, 255), "vehicletyres128", v) == "tyre"
    assert P.role_of(_rgba(255, 255, 255), "premier92wheel64", v) == "rim"
    assert P.role_of(_rgba(255, 255, 255), "premier92interior128", v) == "interior"
    assert P.role_of(_rgba(255, 255, 255), "carplate", v) == "plate"
    assert P.role_of(_rgba(255, 255, 255, 242), "vehiclescratch64", v) == "scratch"
    assert P.role_of(_rgba(255, 255, 255), "ambulan92decal128", v) == "decal"
    assert P.role_of(_rgba(255, 255, 255), "wall", "world") == "map"
    assert P.role_of(_rgba(255, 255, 255, 100), "fence", "world") == "map_alpha"
    assert P.role_of(_rgba(255, 255, 255), "muzzle_texture4", "character", part="gunflash") == "gunflash"


def test_slot_of():
    assert P.slot_of("door_lf_ok", "vehicle") == ("door_lf", "ok")
    assert P.slot_of("bonnet_dam", "vehicle") == ("bonnet", "dam")
    assert P.slot_of("chassis_vlo", "vehicle") == ("chassis", "vlo")
    assert P.slot_of("chassis", "vehicle") == ("chassis", "hd")
    assert P.slot_of("gunflash", "character") == ("gunflash", "flash")


def test_check_name():
    assert P.check_name("MyCar_2", "vehicle") == "mycar_2"
    for bad in ("", "my car", "x" * 18, "имя"):
        with pytest.raises(SatkError) as e:
            P.check_name(bad, "vehicle")
        assert e.value.code == "BAD_PARAMS"
    assert P.check_name("x" * 19, "world") == "x" * 19


def test_matrix_helpers():
    m = (0.0, 1.0, 0.0, -1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 2.0, 3.0)  # 90 deg about Z
    r = P._rot(m)
    inv = P._inv3(r)
    v = P._mv(r, [1.0, 0.0, 0.0])
    assert v == pytest.approx([0.0, 1.0, 0.0])
    assert P._mv(inv, v) == pytest.approx([1.0, 0.0, 0.0])
    assert P._inv3([[0, 0, 0], [0, 0, 0], [0, 0, 0]]) == [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]


def test_bones_reads_hanim_ids(rw):
    dff = rw.clump([rw.box(0, 0, 0)], [(-1, "", (0, 0, 0)), (0, "Root", (0, 0, 0)), (1, "Pelvis", (0, 0, 1))],
                   [(2, 0)], hanim={1: 0, 2: 1})
    assert P._bones(dff) == {1: 0, 2: 1}
    assert P._bones(b"junk") == {}


def test_template_plan_needs_like_or_kind():
    with pytest.raises(SatkError) as e:
        P.template_plan()
    assert e.value.code == "BAD_PARAMS"
    with pytest.raises(SatkError) as e:
        P.template_plan(kind="automobile", tier="ultra")
    assert e.value.code == "BAD_PARAMS"
