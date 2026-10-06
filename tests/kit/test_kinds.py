"""satk.kit catalog data: kinds, material presets, segments, game-ready classes, atlas regions (no game data)."""

from __future__ import annotations

import pytest

from satk.core.errors import SatkError
from satk.kit import atlas as A
from satk.kit import kinds as K

#: Every kind of PLAN L3 task 1.
ALL_KINDS = {"automobile", "mtruck", "quad", "bike", "bmx", "boat", "plane", "heli", "trailer", "train", "prop",
             "building", "interior_shell", "interior_prop", "breakable", "animated_object", "weapon", "ped",
             "pickup", "vehicle_upgrade"}


def test_catalog_covers_every_kind_with_its_recipes():
    ks = K.catalog()["kinds"]
    assert set(ks) == ALL_KINDS
    roles = set(K.materials()["roles"])
    for name, k in ks.items():
        assert k["group"] in ("vehicle", "world", "character"), name
        assert str(k["like"]).startswith("model:") and k.get("like_name"), name
        assert isinstance(k["frames"]["required"], list) and k["frames"]["required"], name
        assert set(k["material_roles"]) <= roles, (name, set(k["material_roles"]) - roles)
        for key in ("col", "lod", "txd", "summary", "ide", "slots"):
            assert key in k, (name, key)
    assert ks["automobile"]["peers"] == 144 and ks["automobile"]["anchors"]["wheel_scale"][1] == 0.7
    assert {"chassis", "chassis_dummy", "wheel_rf_dummy", "door_lf_dummy"} <= set(ks["automobile"]["frames"]["required"])
    assert ks["building"]["lod"]["ratio_tris"][1] == pytest.approx(0.208, abs=0.01)
    assert ks["ped"]["anchors"]["height_m"] == 1.84
    bones = {b["name"]: b["id"] for b in ks["ped"]["bones"] if b["name"]}
    assert bones["Pelvis"] == 1 and bones["Head"] == 5 and bones["R Hand"] == 24 and len(bones) == 32
    assert "gunflash" in ks["weapon"]["frames"]["common"]


def test_get_resolves_aliases_and_suggests():
    assert K.get("car") is K.get("automobile")
    assert K.canonical("upgrade") == "vehicle_upgrade"
    with pytest.raises(SatkError) as e:
        K.get("bikee")
    assert e.value.code == "NOT_FOUND" and "bike" in e.value.did_you_mean


def test_kind_for_maps_ide_sections():
    assert K.kind_for("cars", "car") == "automobile"
    assert K.kind_for("cars", "bike") == "bike" and K.kind_for("cars", "heli") == "heli"
    assert K.kind_for("peds") == "ped" and K.kind_for("weap") == "weapon" and K.kind_for("anim") == "animated_object"
    assert K.kind_for("objs", model_id=1010) == "vehicle_upgrade"
    assert K.kind_for("objs", size_m=2.0) == "prop" and K.kind_for("objs", size_m=40) == "building"
    assert K.kind_for("objs", interior=True, size_m=20) == "interior_shell"
    assert K.kind_for("objs", interior=True, size_m=1.5) == "interior_prop"


def test_presets_carry_measured_vanilla_values():
    p1 = K.preset("paint1")
    assert p1["rgba"] == [60, 255, 0, 255] and p1["texture"] == "vehiclegrunge256" and p1["shared"]
    assert p1["env"]["texture"] == "xvehicleenv128" and p1["specular"]["texture"] == "vehiclespecdot64"
    assert p1["uv_sets"] == 2 and "measured" not in p1
    g = K.preset("glass")
    assert g["rgba"][3] == 128 and g["texture"] == "vehiclegeneric256"
    lamps = {r: K.preset(r)["rgba"][:3] for r in ("lamp_fl", "lamp_fr", "lamp_rl", "lamp_rr")}
    assert lamps == {"lamp_fl": [255, 175, 0], "lamp_fr": [0, 255, 200], "lamp_rl": [185, 255, 0], "lamp_rr": [255, 60, 0]}
    assert all(K.preset(r)["texture"] == "vehiclelights128" for r in lamps)
    assert K.preset("interior", "mycar")["texture"] == "mycar92interior128"
    assert K.preset("map")["surface"] == [1.0, 0.0, 1.0] and K.preset("ped")["surface"] == [1.0, 0.0, 1.0]
    assert len(K.presets_for("automobile", "x")) >= 8
    with pytest.raises(SatkError):
        K.preset("glas")


def test_segments_and_texel_density():
    seg = K.segments()
    assert seg["default_tier"] == "sa_plus"
    assert seg["round"]["wheel"]["vanilla"] == 12 and 16 <= seg["round"]["wheel"]["sa_plus"] <= 24
    assert seg["tris"]["vlo"]["cap"] == 130 and seg["damage"]["dam_over_ok_tris"] == [0.8, 1.1]
    assert K.texel_density("prop", 1.0) == 228 and K.texel_density("prop", 0.75) == 228
    assert K.texel_density("prop", 1.0, "sa_plus") == 342
    assert K.texel_density("building", 300) == 16 and K.texel_density("building", 50) == 35
    assert K.texel_density("vegetation", 12) == 49.4
    assert K.game_ready_class("overlay")["flags"] == 132
    with pytest.raises(SatkError) as e:
        K.game_ready_class("props")
    assert e.value.code == "BAD_PARAMS" and "prop" in e.value.did_you_mean
    night = K.classes()["prelight"]["night_over_day"]
    assert night[0] > night[2]  # warm, never blue


def test_shared_textures():
    assert K.is_shared("vehiclegeneric256") and K.is_shared("VehicleLights128") and not K.is_shared("mycar92wheel64")
    assert not K.is_shared(None)


def test_scene_spec():
    s = K.scene_spec()
    assert {"clump", "frames", "tags", "dragonff", "collision", "export"} <= set(s["contract"])
    b = K.scene_spec("boat")
    assert b["kind"] == "boat" and b["frames"]["required"] and b["col"]["name"] == "<model>_col"


def test_atlas_regions_are_valid_and_flip_to_blender():
    rs = A.regions()
    assert len(rs) >= 20
    for name, r in rs.items():
        u0, v0, u1, v1 = r["rect"]
        assert 0.0 <= u0 < u1 <= 1.0 and 0.0 <= v0 < v1 <= 1.0, name
        assert 0.0 <= r["support"] <= 1.0 and r["kind"] in ("measured", "labelled"), name
    assert rs["generic.glass_core"]["support"] > 0.5 and rs["lights.front_measured"]["support"] > 0.7
    assert A.to_blender([0.5, 0.9375, 0.6875, 1.0]) == (0.5, 0.0, 0.6875, 0.0625)
    assert A.by_texture("vehicletyres128") and A.region("Grunge.Paint")["texture"] == "vehiclegrunge256"
    with pytest.raises(SatkError):
        A.region("generic.glas")


def test_atlas_fit_maps_a_box_inside_the_region():
    rect = [0.5, 0.875, 0.75, 1.0]
    su, sv, ou, ov = A.fit((0.0, 0.0, 2.0, 1.0), rect)
    bu0, bv0, bu1, bv1 = A.to_blender(rect)
    for u, v in ((0.0, 0.0), (2.0, 1.0), (1.0, 0.5)):
        x, y = u * su + ou, v * sv + ov
        assert bu0 - 1e-9 <= x <= bu1 + 1e-9 and bv0 - 1e-9 <= y <= bv1 + 1e-9
    assert su == sv  # aspect kept
