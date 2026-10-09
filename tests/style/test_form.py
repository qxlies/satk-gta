"""Composition (form), fit, symmetry and coverage checks of satk.style on synthetic meshes (no game data).

The limits were calibrated on the vanilla vehicles (data/style/form.json); these tests pin the behaviour of each
rule on small hand-built scenes: a body, pieces that float, touch, cross or hide, hard and soft corners, an arch
with and without a liner, dummies on and off their geometry, a bike rider pose.
"""

from __future__ import annotations

import math

import pytest

np = pytest.importorskip("numpy")

from satk.style import coverage as CV  # noqa: E402
from satk.style import fit as FT  # noqa: E402
from satk.style import form as F  # noqa: E402
from satk.style import symmetry as SY  # noqa: E402


# ----------------------------------------------------------------------------- builders
def box(size, centre=(0.0, 0.0, 0.0), *, name="part", smooth=True, mats=1, role="hd", tex=None, alpha=None):
    """A closed box part (8 shared vertices, outward winding); smooth = vertex normals, else face normals."""
    sx, sy, sz = (s / 2 for s in size)
    cx, cy, cz = centre
    P = np.array([(cx + x * sx, cy + y * sy, cz + z * sz) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)])
    quads = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    T = []
    for a, b, c, d in quads:
        T += [(a, b, c), (a, c, d)]
    T = np.array(T)
    A, B, C = P[T[:, 0]], P[T[:, 1]], P[T[:, 2]]
    out = np.einsum("ij,ij->i", np.cross(B - A, C - A), (A + B + C) / 3 - np.array(centre)) < 0
    T[out] = T[out][:, [0, 2, 1]]
    if smooth:
        N = (P - np.array(centre)) / np.linalg.norm(P - np.array(centre), axis=1, keepdims=True)
    else:
        A, B, C = P[T[:, 0]], P[T[:, 1]], P[T[:, 2]]
        n = np.cross(B - A, C - A)
        n /= np.linalg.norm(n, axis=1, keepdims=True)
        N = np.repeat(n[:, None, :], 3, axis=1)
    M = np.arange(len(T)) // 2 % mats
    return {"name": name, "pos": P, "tris": T, "normals": N, "mat": M, "role": role,
            "tex": tex or ["body"] * mats, "alpha": alpha or [255] * mats}


def merge(name, *parts, role="hd"):
    """Several pieces in one part (separate welded components)."""
    P, T, N, M, off = [], [], [], [], 0
    for p in parts:
        P.append(p["pos"])
        T.append(p["tris"] + off)
        n = p["normals"]
        N.append(n[p["tris"]] if n.ndim == 2 else n)
        M.append(p["mat"])
        off += len(p["pos"])
    return {"name": name, "pos": np.concatenate(P), "tris": np.concatenate(T), "normals": np.concatenate(N),
            "mat": np.concatenate(M), "role": role, "tex": parts[0]["tex"], "alpha": parts[0]["alpha"]}


def card(centre, size=(0.0, 0.3, 0.1), name="plate"):
    """A two-triangle flat card in the YZ plane."""
    cx, cy, cz = centre
    _sx, sy, sz = (s / 2 for s in size)
    P = np.array([(cx, cy - sy, cz - sz), (cx, cy + sy, cz - sz), (cx, cy + sy, cz + sz), (cx, cy - sy, cz + sz)])
    T = np.array([(0, 1, 2), (0, 2, 3)])
    N = np.repeat(np.array([[[1.0, 0.0, 0.0]] * 3]), 2, axis=0)
    return {"name": name, "pos": P, "tris": T, "normals": N, "mat": np.zeros(2, np.int64), "role": "hd",
            "tex": ["carplate"], "alpha": [255]}


BODY = dict(size=(2.0, 4.0, 1.0), centre=(0.0, 0.0, 0.5))


# ----------------------------------------------------------------------------- floating, intersect, loose
def test_touching_and_crossing_pieces_are_joined_and_a_gap_floats():
    body = box(**BODY, name="chassis")
    sitting = box((0.2, 0.2, 0.2), (0.0, 0.0, 1.1), name="rail")              # its bottom lies on the roof
    crossing = box((0.2, 0.2, 0.2), (1.0, 0.5, 0.5), name="flare")            # straddles the side wall
    r = F.analyse([body, sitting, crossing], body="chassis", checks=("floating",))
    assert "floating" not in r and r["counts"]["floating_pieces"] == 0
    off = box((0.4, 0.4, 0.4), (0.0, 1.0, 1.25), name="rack")                 # 5 cm above the roof
    r = F.analyse([body, off], body="chassis", checks=("floating",))
    (row,) = r["floating"]
    assert row["part"] == "rack" and row["pieces"] == 1 and row["tris"] == 12
    assert row["gap_mm"] == pytest.approx(50.0, abs=0.5) and row["at"] == [0.0, 1.0, 1.25]


def test_a_group_of_touching_pieces_floats_as_one():
    body = box(**BODY, name="chassis")
    rail = box((0.1, 2.0, 0.1), (0.5, 0.0, 1.2), name="rack")
    bar = box((1.0, 0.1, 0.1), (0.0, 0.0, 1.2), name="rack2")                 # crosses the rail
    r = F.analyse([body, rail, bar], body="chassis", checks=("floating",))
    (row,) = r["floating"]
    assert row["pieces"] == 2 and row["parts"] == ["rack", "rack2"] and row["gap_mm"] == pytest.approx(150, abs=1)


def test_vanilla_hovers_are_allowed():
    body = box(**BODY, name="chassis")
    plate = card((1.01, 0.0, 0.3))                                           # a plate card 10 mm off
    head = box((0.02, 0.02, 0.02), (0.0, 0.0, 1.025), name="mirror_in")      # a small head 15 mm off
    hidden = box((0.3, 0.3, 0.3), (0.0, 0.0, 0.5), name="engine")             # inside the closed body
    r = F.analyse([body, plate, head, hidden], body="chassis", checks=("floating",))
    assert "floating" not in r and r["counts"]["hover_allowed"] == 3
    far_card = card((1.06, 0.0, 0.3))                                        # 60 mm: too far for a card
    r = F.analyse([body, far_card], body="chassis", checks=("floating",))
    assert r["floating"][0]["part"] == "plate"


def test_a_panel_may_stand_at_its_shut_line_but_its_details_must_touch_it():
    body = box(**BODY, name="chassis")
    door = box((0.05, 1.0, 0.6), (1.04, 0.0, 0.5), name="door_lf_ok")          # 15 mm gap to the side
    r = F.analyse([body, door], body="chassis", checks=("floating",))
    assert "floating" not in r
    strip = box((0.02, 0.8, 0.05), (1.10, 0.0, 0.3), name="x")                # 2.5 cm off the door skin
    r = F.analyse([body, merge("door_lf_ok", door, strip)], body="chassis", checks=("floating",))
    (row,) = r["floating"]
    assert row["part"] == "door_lf_ok" and row["tris"] == 12 and row["gap_mm"] == pytest.approx(25, abs=1)


def test_focus_judges_only_the_changed_parts():
    body = box(**BODY, name="chassis")
    a = box((0.2, 0.2, 0.2), (0.0, 1.0, 1.3), name="a")
    b = box((0.2, 0.2, 0.2), (0.0, -1.0, 1.3), name="b")
    r = F.analyse([body, a, b], body="chassis", focus=["b"], checks=("floating",))
    assert [x["part"] for x in r["floating"]] == ["b"]


def test_intersect_reports_buried_pieces():
    body = box(**BODY, name="chassis")
    sunk = merge("axle", box((0.1, 0.1, 0.1), (0.95, 1.5, 0.5)), box((0.1, 0.1, 0.1), (0.95, 1.55, 0.5)))
    jammed = box((0.3, 0.2, 0.2), (0.9, 0.0, 0.5), name="jam")                # half inside, crosses the wall
    r = F.analyse([body, jammed], body="chassis", checks=("intersect",),
                  limits_override={"deep_share": 0.4, "deep_radius_mm": 300.0})
    (row,) = r["intersect"]
    assert row["part"] == "jam" and row["into"] == "chassis" and row["depth_mm"] >= 30 and row["share"] >= 0.5
    assert sunk["tris"].shape[0] == 24


def test_loose_share_of_the_body():
    shell = box(**BODY)
    bits = [box((0.1, 0.1, 0.1), (0.0, -1.5 + 0.15 * i, 1.2)) for i in range(20)]
    body = merge("chassis", shell, *bits)
    r = F.analyse([body], body="chassis", checks=("loose_share",), limits_override={"loose_min_tris": 10})
    ls = r["loose_share"]
    assert ls["part"] == "chassis" and ls["pieces"] == 21 and ls["share"] == pytest.approx(240 / 252, abs=0.001)
    r = F.analyse([merge("chassis", shell, bits[0])], body="chassis", checks=("loose_share",),
                  limits_override={"loose_min_tris": 10})
    assert "loose_share" not in r and r["counts"]["loose_share"] == 0.5


# ----------------------------------------------------------------------------- hard corners
def test_hard_corners_split_without_a_seam_are_the_box_look():
    hard = box((1.0, 1.0, 1.0), name="hard", smooth=False)
    hc = F.hard_corners(F.pieces_of([hard])[0][0])
    assert hc["folds"] == 12 and hc["creases"] == 12 and hc["soft"] == 0 and len(hc["at"]) == 4
    soft = box((1.0, 1.0, 1.0), name="soft", smooth=True)
    assert F.hard_corners(F.pieces_of([soft])[0][0])["soft"] == 12
    seams = box((1.0, 1.0, 1.0), name="seams", smooth=False, mats=6)          # every face its own material
    hs = F.hard_corners(F.pieces_of([seams])[0][0])
    assert hs["creases"] == 0 and hs["seams"] == 12
    boxes = [box((0.5, 0.5, 0.5), (i * 1.0, 0.0, 0.0), name=f"b{i}", smooth=False) for i in range(6)]
    r = F.analyse(boxes, body=None, checks=("hard_corners",))
    assert r["counts"]["crease_share"] == 1.0 and r["hard_corners"]
    r = F.analyse([box((0.5, 0.5, 0.5), (i * 1.0, 0.0, 0.0), name=f"b{i}") for i in range(6)], body=None,
                  checks=("hard_corners",))
    assert "hard_corners" not in r and r["counts"]["soft_share"] == 1.0


# ----------------------------------------------------------------------------- see-through arches
def side_panel(x: float, wheel, hole: float, step: float = 0.05, name="chassis"):
    """A vertical panel at ``x`` (facing outward) from y -1.2..1.2, z 0..1.2 with a round hole around ``wheel``."""
    cy, cz = wheel
    P, T = [], []
    side = 1.0 if x > 0 else -1.0
    for iy in range(int(2.4 / step)):
        for iz in range(int(1.2 / step)):
            y0, z0 = -1.2 + iy * step, iz * step
            if math.hypot(y0 + step / 2 - cy, z0 + step / 2 - cz) < hole:
                continue
            k = len(P)
            P += [(x, y0, z0), (x, y0 + step, z0), (x, y0 + step, z0 + step), (x, y0, z0 + step)]
            a, b, c, d = k, k + 1, k + 2, k + 3
            T += [(a, b, c), (a, c, d)] if side > 0 else [(a, c, b), (a, d, c)]
    P, T = np.array(P), np.array(T)
    A, B, C = P[T[:, 0]], P[T[:, 1]], P[T[:, 2]]
    assert (np.cross(B - A, C - A)[:, 0] * side > 0).all()
    N = np.repeat(np.array([[[side, 0.0, 0.0]] * 3]), len(T), axis=0)
    return {"name": name, "pos": P, "tris": T, "normals": N, "mat": np.zeros(len(T), np.int64), "role": "hd",
            "tex": ["body"], "alpha": [255]}


WHEEL = {"name": "wheel_rf_dummy", "center": (0.85, 0.0, 0.35), "radius": 0.3}


def test_see_through_arch_without_and_with_a_liner():
    hole = 0.3 * 1.25
    right, left = side_panel(1.0, (0.0, 0.35), hole), side_panel(-1.0, (0.0, 0.35), hole)
    body = merge("chassis", right, left)
    r = F.analyse([body], body="chassis", wheels=[WHEEL], checks=("see_through",))
    (st,) = r["see_through"]
    assert st["wheel"] == "wheel_rf_dummy" and st["share"] == 1.0 and r["counts"]["arches"] == 1
    liner = side_panel(0.6, (5.0, 5.0), 0.0, step=0.1, name="liner")         # a closed plate inside, facing out
    r = F.analyse([body, liner], body="chassis", wheels=[WHEEL], checks=("see_through",))
    assert "see_through" not in r
    # no body around the wheel at all (a kart): nothing to close
    r = F.analyse([box((0.5, 0.5, 0.5), (0.0, 0.0, 2.0), name="seat")], body=None, wheels=[WHEEL],
                  checks=("see_through",))
    assert "see_through" not in r and r["counts"].get("arches", 0) == 0


# ----------------------------------------------------------------------------- fit
def test_fit_arch_centre_lamps_and_hinges():
    body = merge("chassis", side_panel(1.0, (0.15, 0.35), 0.38), side_panel(-1.0, (0.15, 0.35), 0.38))
    lamp = box((0.05, 0.1, 0.1), (0.7, 1.2, 0.6), name="lamp")
    lamp["keys"] = ["0,255,200"]
    door = box((0.05, 0.6, 0.8), (1.0, -0.8, 0.6), name="door_rf_ok")
    frames = {"headlights": {"pos": (0.7, 1.2, 0.6)}, "taillights": {"pos": (0.7, 1.2, 0.0)},
              "door_rf_dummy": {"pos": (1.0, -0.8, 0.6)}, "exhaust": {"pos": (0.3, -3.0, 0.0)}}
    rows = FT.fit_check([body, lamp, door], frames, [WHEEL], vtype="car")
    got = {r["check"]: r for r in rows if r["verdict"] == "defect"}
    arch = got["fit.wheel_arch[wheel_rf_dummy]"]
    assert arch["value"] == pytest.approx(150, abs=25)                       # the arch is 15 cm ahead
    assert "fit.dummy[headlights]" not in got                               # on its lamp
    assert "fit.hinge[door_rf]" in got and got["fit.hinge[door_rf]"]["value"] == 0.5   # hinge in the middle
    assert "fit.dummy[exhaust]" in got                                     # smoke out of thin air
    frames["door_rf_dummy"] = {"pos": (1.0, -0.5, 0.6)}
    frames["exhaust"] = {"pos": (0.9, -1.1, 0.05)}
    got = {r["check"] for r in FT.fit_check([body, lamp, door], frames, [WHEEL], vtype="car")
           if r["verdict"] == "defect"}
    assert "fit.hinge[door_rf]" not in got and "fit.dummy[exhaust]" not in got


def _bike(seat_z: float, grip_x: float, bar_y: float):
    """A minimal bike: a seat slab, a floor, handlebars, frames (forks_front axis = local Z)."""
    seat = box((0.3, 0.6, 0.1), (0.0, -0.3, seat_z - 0.05), name="chassis")
    floor = box((0.4, 0.4, 0.05), (0.0, 0.25, -0.3), name="floor")
    bars = box((2 * grip_x, 0.05, 0.05), (0.0, bar_y, 0.6), name="handlebars")
    frames = {"ped_frontseat": {"pos": (0.0, -0.3, 0.4)},
              "forks_front": {"pos": (0.0, 0.5, 0.1), "at": (0.0, 0.0, 1.0)}}
    return [seat, floor, bars], frames


def test_fit_bike_rider_and_steering_against_the_like_model():
    like, lf = _bike(0.32, 0.47, 0.45)
    same, sf = _bike(0.32, 0.47, 0.45)
    assert [r for r in FT.fit_check(same, sf, [], vtype="bike", like_parts=like, like_frames=lf)
            if r["verdict"] == "defect"] == []
    mine, mf = _bike(0.41, 0.36, 0.30)                                       # seat +9 cm, bars 11 cm in, 15 cm back
    got = {r["check"]: r for r in FT.fit_check(mine, mf, [], vtype="bike", like_parts=like, like_frames=lf,
                                                like_name="faggio") if r["verdict"] == "defect"}
    assert got["fit.rider[seat]"]["value"] == pytest.approx(90, abs=2)
    assert got["fit.rider[grip]"]["value"] > 100 and "faggio" in got["fit.rider[grip]"]["hint"]
    assert got["fit.steer[handlebars]"]["value"] == pytest.approx(200, abs=2)
    assert "fit.rider[foot]" not in got                                    # the floor stayed where it was


# ----------------------------------------------------------------------------- symmetry, coverage
def test_symmetry_mirrors_parts_and_dummies():
    left = box((0.05, 1.0, 0.6), (-1.0, 0.0, 0.5), name="door_lf_ok")
    right = box((0.05, 1.0, 0.6), (1.0, 0.0, 0.5), name="door_rf_ok")
    frames = {"wheel_lf_dummy": {"pos": (-0.8, 1.4, 0.0)}, "wheel_rf_dummy": {"pos": (0.8, 1.4, 0.0)}}
    assert [r for r in SY.symmetry_check([left, right], frames) if r["verdict"] != "info" or
            not r["check"].startswith("sym.body")] == []
    right2 = box((0.05, 1.0, 0.6), (1.0, 0.3, 0.5), name="door_rf_ok")
    frames["wheel_rf_dummy"] = {"pos": (0.8, 1.5, 0.0)}
    got = {r["check"]: r for r in SY.symmetry_check([left, right2], frames)}
    assert got["sym.part[door_lf_ok]"]["value"] > 100 and got["sym.dummy[wheel_lf_dummy]"]["value"] == 100.0
    assert SY.twin("door_lf_ok") == "door_rf_ok" and SY.twin("wheel_lb_dummy") == "wheel_rb_dummy"
    assert SY.twin("door_rf_ok") is None and SY.twin("chassis") is None


def test_coverage_checklists_exist_and_detect():
    assert {"car", "car.suv_pickup", "bike", "prop"} <= set(CV.list_classes())
    assert CV.checklist("car.sedan")["class"] == "car.sedan" and CV.checklist("car.suv_pickup")["class"] == "car.suv_pickup"
    assert CV.checklist("car.offroad")["class"] == "car"                 # no list of its own: the road-car list
    body = box(**BODY, name="chassis")
    lamp = box((0.05, 0.1, 0.1), (0.7, 2.0, 0.6), name="lamps")
    lamp["keys"] = ["255,175,0"]
    lamp["tex"] = ["vehiclelights128"]
    rows = {r["check"]: r for r in CV.coverage_rows("car.sedan", [body, lamp], {}, {"see_through": [], "arches": 0})}
    assert rows["cov.headlamps"]["verdict"] == "present" and rows["cov.tail_lamps"]["verdict"] == "missing"
    assert "vehiclelights128" in rows["cov.tail_lamps"]["hint"] and "cov.arch_liners" in rows
    flags = {"prelit": True, "night": False, "collision": True, "textured": True}
    rows = {r["check"]: r["verdict"] for r in CV.coverage_rows("prop@1-2m", [body], {}, {"flags": flags})}
    assert rows == {"cov.prelight": "present", "cov.night": "missing", "cov.collision": "present",
                    "cov.textured": "present"}


def test_limits_come_from_the_data_file():
    import json

    from satk.core import resources

    data = resources.read_json("style", "form.json")
    assert data["limits"] == F.DEFAULTS and data["fit"] == FT.FIT_DEFAULTS
    assert F.limits()["touch_mm"] == data["limits"]["touch_mm"] and json.dumps(data)
    assert "car.sedan" in data["strict_classes"] and "car.truck_bus" not in data["strict_classes"]
