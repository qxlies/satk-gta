"""Body blanks (``kit.blank``): plans, generated geometry, the op and the agent docs. Fast, no game data, no Blender.

The geometry is OUR parametric construction (``satk.kit.blanks``): the tests check it is valid (outward quads, no
degenerate faces), symmetric where it claims to be, sized as asked, deterministic and of the documented density.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from satk.core.errors import SatkError
from satk.core.registry import get_op
from satk.kit import blanks as B
from satk.kit import kinds as K

KINDS = B.KINDS
TIERS = B.TIERS
BODIES = ("sedan", "coupe", "sports", "suv", "van", "hatchback", "wagon", "suv_boxy", "pickup")
GOLDEN = json.loads((Path(__file__).resolve().parents[1] / "golden" / "blanks.json").read_text(encoding="utf-8"))


def _spec(kind: str, tier: str = "sa_plus", **kw) -> tuple[dict, dict]:
    plan = B.blank_plan(kind, name="tst", tier=tier, **kw)
    return plan, B.mesh_spec(plan)


def _bbox(spec: dict) -> tuple[list[float], list[float]]:
    pts = [v for p in spec["pieces"] for v in p["verts"]]
    return [min(v[i] for v in pts) for i in range(3)], [max(v[i] for v in pts) for i in range(3)]


# --------------------------------------------------------------------------- catalog and plans


def test_catalog_lists_every_kind():
    cat = B.kinds()["kinds"]
    assert tuple(cat) == KINDS
    for name, k in cat.items():
        assert k["summary"].isascii() and len(k["summary"]) > 40, name
        assert set(k["dims"]) == {"L", "W", "H"} and all(v > 0 for v in k["dims"].values())
        if name != "automobile":
            assert set(k["tiers"]) == set(TIERS) and k["parts"], name
        assert k["group"] in ("vehicle", "world") and k["kit_kind"] in K.names(), name


def test_plan_defaults_and_overrides():
    plan = B.blank_plan("automobile", name="mycar")
    assert plan["tier"] == "sa_plus" and plan["body"] == "sedan" and plan["kind"] == "automobile"
    assert plan["dims"] == {"L": 5.82, "W": 2.27, "H": 1.53}
    a = plan["anchors"]
    assert a["wheel_d"] == 0.7 and 3.4 < a["wheelbase"] < 3.7 and 1.8 < a["track"] < 1.95
    assert abs(sum(a["axle_y"]) / 2) < 0.01 and a["ground_z"] < 0 < a["ground_z"] + plan["dims"]["H"]
    big = B.blank_plan("automobile", name="mycar", dims="7.0,2.6,2.0", tier="vanilla", body="suv", wheel_d=0.8)
    assert big["dims"] == {"L": 7.0, "W": 2.6, "H": 2.0} and big["body"] == "suv" and big["anchors"]["wheel_d"] == 0.8
    assert B.blank_plan("automobile", dims=[5.0, 2.0, 1.4])["dims"] == {"L": 5.0, "W": 2.0, "H": 1.4}
    assert plan["slots"]["door_f"] == "door_rf_ok" and plan["slots"]["chassis"] == "chassis"
    assert B.blank_plan("prop_cyl", name="mybin")["slots"] == {"prop": "mybin"}
    assert B.blank_plan("bike")["anchors"]["wheelbase"] > 1.0 and "wheel_d" in B.blank_plan("bike")["anchors"]


@pytest.mark.parametrize("bad", ["boxx", "", "autmobile"])
def test_unknown_kind_has_did_you_mean(bad):
    with pytest.raises(SatkError) as e:
        B.blank_plan(bad)
    assert e.value.code == "NOT_FOUND" and "satk kit blank" in e.value.hint


@pytest.mark.parametrize("kw", [{"dims": "1,2"}, {"dims": [1, 0, 2]}, {"dims": "a,b,c"}, {"tier": "ultra"},
                                {"name": "Bad Name"}, {"name": "x" * 18}, {"body": "limo"}])
def test_bad_arguments_are_rejected(kw):
    with pytest.raises(SatkError) as e:
        B.blank_plan("automobile", **kw)
    assert e.value.code in ("BAD_PARAMS",)


def test_aliases_resolve():
    assert B.blank_plan("car")["kind"] == "automobile" and B.blank_plan("box")["kind"] == "prop_box"
    assert B.blank_plan("automobile", body="coupe_muscle")["body"] == "coupe"
    assert B.blank_plan("automobile", tier="sa-plus")["tier"] == "sa_plus"


def test_compose_frame_positions():
    fr = [{"i": 0, "parent": -1, "name": "root", "matrix": [1, 0, 0, 0, 1, 0, 0, 0, 1, 1.0, 2.0, 3.0]},
          {"i": 1, "parent": 0, "name": "child", "matrix": [1, 0, 0, 0, 1, 0, 0, 0, 1, 0.5, 0.0, -1.0]},
          {"i": 2, "parent": 1, "name": "rot", "matrix": [0, 1, 0, -1, 0, 0, 0, 0, 1, 1.0, 0.0, 0.0]}]
    pos = B._compose(fr)
    assert pos["child"] == pytest.approx((1.5, 2.0, 2.0)) and pos["rot"] == pytest.approx((2.5, 2.0, 2.0))


# --------------------------------------------------------------------------- geometry


@pytest.mark.parametrize("tier", TIERS)
@pytest.mark.parametrize("kind", KINDS)
def test_every_blank_is_valid(kind, tier):
    plan, spec = _spec(kind, tier)
    assert spec["kind"] == kind and spec["tier"] == tier and spec["name"] == "tst"
    json.dumps(spec)                                            # a plain JSON document
    check = B.check_spec(spec)
    roles = set(K.materials()["roles"])
    for piece in spec["pieces"]:
        n, nf = len(piece["verts"]), len(piece["faces"])
        assert nf and all(len(f) >= 3 and all(0 <= i < n for i in f) for f in piece["faces"])
        assert len(piece["role"]) == len(piece["part"]) == len(piece["uv"]) == nf
        assert all(len(uv) == len(f) for uv, f in zip(piece["uv"], piece["faces"]))
        assert set(piece["role"]) <= roles, set(piece["role"]) - roles
        assert all(0 <= p < len(piece["part_names"]) for p in piece["part"])
        assert all(0 <= a < n and 0 <= b < n for a, b in piece["seam"])
        c = check[piece["name"]]
        assert c["degenerate"] == 0 and c["flipped_edges"] == 0, (piece["name"], c)
        if not piece["mirror"]:
            assert c["nonmanifold"] == 0
    parts = {p["name"] for p in spec["parts"]}
    assert {pn for piece in spec["pieces"] for pn in piece["part_names"]} <= parts, (kind, parts)


@pytest.mark.parametrize("tier", TIERS)
@pytest.mark.parametrize("kind", ("prop_box", "prop_cyl", "building_box"))
def test_world_blanks_are_closed_or_documented_open(kind, tier):
    _plan, spec = _spec(kind, tier)
    c = B.check_spec(spec)[spec["pieces"][0]["name"]]
    assert c["nonmanifold"] == 0
    if kind != "building_box":                                  # the building has no floor
        assert c["open_edges"] == 0


@pytest.mark.parametrize("kind,dims", [("prop_box", (0.8, 1.4, 0.9)), ("prop_cyl", (0.6, 0.6, 1.1)),
                                       ("building_box", (14.0, 10.0, 12.0))])
def test_world_blank_fills_its_dimensions(kind, dims):
    plan, spec = _spec(kind, dims=list(dims))
    lo, hi = _bbox(spec)
    L, W, H = dims
    assert hi[2] - lo[2] == pytest.approx(H, abs=0.02) and lo[2] == pytest.approx(0.0, abs=1e-6)
    assert hi[0] - lo[0] == pytest.approx(W, abs=0.5 if kind == "building_box" else 0.02)
    assert hi[1] - lo[1] == pytest.approx(L, abs=0.5 if kind == "building_box" else 0.02)


@pytest.mark.parametrize("body", BODIES)
@pytest.mark.parametrize("tier", TIERS)
def test_automobile_half_body_is_symmetric_and_sized(body, tier):
    plan, spec = _spec("automobile", tier, body=body)
    d, a = plan["dims"], plan["anchors"]
    hull = spec["pieces"][0]
    assert hull["mirror"] and hull["name"] == "body"
    xs = [v[0] for v in hull["verts"]]
    assert min(xs) >= -1e-6 and min(abs(x) for x in xs) == 0.0               # the half x >= 0, with a mirror line
    lo, hi = _bbox({"pieces": [hull]})
    assert hi[1] - lo[1] == pytest.approx(d["L"], abs=0.02)                  # the shell spans the length
    hw = d["W"] / 2
    shell = [v[0] for v in hull["verts"] if v[0] <= hw + 0.01]
    assert 2 * max(shell) == pytest.approx(d["W"], abs=0.02)                  # the body without the mirrors
    assert hw + 0.15 < hi[0] < hw + 0.35                                      # a mirror head outside the door
    assert lo[2] >= a["ground_z"] and hi[2] - a["ground_z"] == pytest.approx(d["H"], abs=0.06)
    # the arch of each wheel is centred on its axle: a vertex of the opening sits on the axle line
    clear = plan["profile"]["wheel_clear"]
    for ya in a["axle_y"]:
        assert any(abs(v[1] - ya) < 0.03 and abs(v[2] - (a["wheel_z"] + a["wheel_d"] / 2 + clear)) < 0.06
                   for v in hull["verts"]), (body, tier, ya)


def test_automobile_parts_cover_the_vanilla_part_list():
    for body, absent in (("sedan", ()), ("coupe", ("door_r",)), ("van", ("door_r",)), ("hatchback", ()),
                         ("wagon", ()), ("suv_boxy", ()), ("pickup", ("door_r",))):
        _plan, spec = _spec("automobile", body=body)
        faces = {}
        for piece in spec["pieces"]:
            for p in piece["part"]:
                faces[piece["part_names"][p]] = faces.get(piece["part_names"][p], 0) + 1
        want = {"chassis", "bonnet", "boot", "windscreen", "door_f", "bump_front", "bump_rear", "exhaust", "door_r"}
        assert set(faces) == want - set(absent), (body, sorted(faces))
    slots = {p["name"]: B.part_slot(p, "l") for p in spec["parts"]}
    assert slots["door_f"] == "door_lf_ok" and slots["bump_rear"] == "bump_rear_ok" and slots["exhaust"] == "exhaust_ok"


def test_automobile_part_tris_are_in_the_vanilla_neighbourhood():
    """Per-part triangles of the sa_plus sedan (the part table of the vehicle guide; wide on purpose)."""
    _plan, spec = _spec("automobile", "sa_plus")
    tris = {}
    for piece in spec["pieces"]:
        mult = 2 if piece["mirror"] else 1
        for f, p in zip(piece["faces"], piece["part"]):
            n = piece["part_names"][p]
            tris[n] = tris.get(n, 0) + mult * (len(f) - 2)
    assert 1500 <= tris["chassis"] <= 3000 and 60 <= tris["bonnet"] <= 260 and 30 <= tris["boot"] <= 160
    assert 10 <= tris["windscreen"] <= 40 and 20 <= tris["bump_front"] <= 320 and 12 <= tris["exhaust"] <= 48
    v = _spec("automobile", "vanilla")[1]
    chassis = sum((2 if p["mirror"] else 1) * (len(f) - 2) for p in v["pieces"] for f, k in zip(p["faces"], p["part"])
                  if p["part_names"][k] == "chassis")
    assert 1000 <= chassis <= 1700 and chassis < tris["chassis"]             # the vanilla tier is the lighter one


def test_automobile_follows_the_tier_and_options():
    sa = _spec("automobile", "sa_plus")[1]
    va = _spec("automobile", "vanilla")[1]
    assert sum(B.tri_count(p["faces"]) for p in sa["pieces"]) > 1.4 * sum(B.tri_count(p["faces"]) for p in va["pieces"])
    none = B.mesh_spec(B.blank_plan("automobile", name="tst", interior=False))
    assert [p["name"] for p in none["pieces"]] == ["body", "exhaust"]
    assert [p["name"] for p in sa["pieces"]] == ["body", "interior", "exhaust"]


def _unit_normal(verts, f):
    n = [0.0, 0.0, 0.0]
    pts = [verts[i] for i in f]
    for k in range(len(f)):
        p, q = pts[k], pts[(k + 1) % len(f)]
        n[0] += (p[1] - q[1]) * (p[2] + q[2])
        n[1] += (p[2] - q[2]) * (p[0] + q[0])
        n[2] += (p[0] - q[0]) * (p[1] + q[1])
    ln = (n[0] ** 2 + n[1] ** 2 + n[2] ** 2) ** 0.5 or 1.0
    return [c / ln for c in n]


@pytest.mark.parametrize("body", BODIES)
def test_uv_maps_follow_the_paint_rule(body):
    """Paint is ONE continuous island in the clean left strip of vehiclegrunge256 (u 0.03-0.23, never the drip band):
    up-facing panels high in the clean band, sides lower in the grime band, the belly at the bottom."""
    plan, spec = _spec("automobile", body=body)
    hull = spec["pieces"][0]
    hw = plan["dims"]["W"] / 2
    up, side, under = [], [], []
    corner: dict = {}
    for f, uv, role in zip(hull["faces"], hull["uv"], hull["role"]):
        if role != "paint1":
            continue
        assert all(0.03 - 1e-6 <= u <= 0.23 + 1e-6 for u, _v in uv), (body, uv)
        for vi, c in zip(f, uv):
            corner.setdefault(vi, set()).add((round(c[0], 4), round(c[1], 4)))
        if max(hull["verts"][i][0] for i in f) > hw + 0.01:
            continue                                               # the mirror heads use their own clean rectangle
        n = _unit_normal(hull["verts"], f)
        v = sum(c[1] for c in uv) / len(uv)
        if n[2] > 0.985:
            up.append(v)
        elif abs(n[0]) > 0.85:
            side.append(v)
    for f, uv, role in zip(hull["faces"], hull["uv"], hull["role"]):
        if role == "black" and _unit_normal(hull["verts"], f)[2] < -0.85:
            under.append(sum(c[1] for c in uv) / len(uv))
    assert up and side and under
    assert min(up) >= 0.85 and sorted(side)[len(side) // 2] < 0.7     # door panels: the grime band
    shell = {vi for f, r in zip(hull["faces"], hull["role"]) if r == "paint1" for vi in f
             if hull["verts"][vi][0] <= hw + 0.01}
    split = [vi for vi in shell if len(corner[vi]) > 1]
    assert not split, f"{body}: {len(split)} paint vertices carry two UVs (a seam inside the paint)"
    assert hull["seam"], "the sill line and the shoulder carry unwrap seams"


@pytest.mark.parametrize("body", ("sedan", "hatchback", "suv_boxy", "pickup"))
def test_shading_is_soft_with_hard_material_borders(body):
    """Every face is smooth in Blender; the spec's sharp edges are exactly role borders and real folds (> 85 deg)."""
    _plan, spec = _spec("automobile", body=body)
    hull = spec["pieces"][0]
    faces_of: dict = {}
    for fi, f in enumerate(hull["faces"]):
        for k in range(len(f)):
            a, b = f[k], f[(k + 1) % len(f)]
            faces_of.setdefault((min(a, b), max(a, b)), []).append(fi)
    sharp = {tuple(e) for e in hull["sharp"]}
    assert sharp
    for e in sharp:
        fs = faces_of[e]
        n1, n2 = (_unit_normal(hull["verts"], hull["faces"][i]) for i in fs)
        dot = sum(x * y for x, y in zip(n1, n2))
        assert hull["role"][fs[0]] != hull["role"][fs[1]] or dot < 0.0872, e
    soft_corners = sum(1 for e, fs in faces_of.items() if len(fs) == 2 and e not in sharp
                       and hull["role"][fs[0]] == hull["role"][fs[1]] == "paint1"
                       and sum(x * y for x, y in zip(*(_unit_normal(hull["verts"], hull["faces"][i]) for i in fs))) < 0.94)
    assert soft_corners > 20, "paint corners of 20-85 deg stay smooth (vanilla rule)"


@pytest.mark.parametrize("body", ("sedan", "suv_boxy"))
def test_arches_are_round_with_liners_and_the_mirror_touches_the_door(body):
    plan, spec = _spec("automobile", body=body)
    a, hull = plan["anchors"], spec["pieces"][0]
    clear = plan["profile"]["wheel_clear"]
    ra = a["wheel_d"] / 2 + clear
    zc = a["wheel_z"]
    for ya in a["axle_y"]:
        # the opening: vertices on a circle around the wheel centre, at least 5 segments over the top
        arc = {round(v[1], 3) for v in hull["verts"] if abs(v[1] - ya) < ra and v[2] > zc + 0.2 * ra
               and abs(((v[1] - ya) ** 2 + (v[2] - zc) ** 2) ** 0.5 - ra) < 0.02 and v[0] > a["track"] / 2}
        assert len(arc) >= 6, (body, ya, sorted(arc))
        # a vertical liner plate inside the arch: a black face whose vertices share one x (the liner plane)
        liners = [f for f, r in zip(hull["faces"], hull["role"]) if r == "black" and len(f) >= 6
                  and max(hull["verts"][i][0] for i in f) - min(hull["verts"][i][0] for i in f) < 1e-3
                  and all(abs(hull["verts"][i][1] - ya) <= ra + 0.03 for i in f)]
        assert liners, (body, ya)
        assert all(hull["verts"][f[0]][0] < a["track"] / 2 - a["wheel_d"] * 0.15 for f in liners)   # inside the tyre
    hw = plan["dims"]["W"] / 2
    mirror = [v for v in hull["verts"] if v[0] > hw + 0.01]
    assert mirror and min(v[0] for v in hull["verts"] if v[0] > hw * 0.9 and v[2] > max(m[2] for m in mirror) - 0.2
                          and abs(v[1] - mirror[0][1]) < 0.15) < hw              # the stalk starts inside the skin


def test_rear_glass_and_hatch_parts():
    _plan, sedan = _spec("automobile", body="sedan")
    hull = sedan["pieces"][0]
    names = hull["part_names"]
    back_glass = [f for f, r, pt in zip(hull["faces"], hull["role"], hull["part"]) if r == "glass"
                  and names[pt] == "chassis" and _unit_normal(hull["verts"], f)[1] < -0.3]
    assert back_glass, "the sedan has a rear window (fixed glass in the chassis)"
    _plan, hatch = _spec("automobile", body="hatchback")
    hull = hatch["pieces"][0]
    names = hull["part_names"]
    assert any(r == "glass" and names[pt] == "boot" for r, pt in zip(hull["role"], hull["part"])), \
        "the hatch carries its rear glass"
    plan, pick = _spec("automobile", body="pickup")
    hull = pick["pieces"][0]
    top = plan["anchors"]["ground_z"] + 0.6 * plan["dims"]["H"]
    bed = [f for f, r in zip(hull["faces"], hull["role"]) if r == "trim" and _unit_normal(hull["verts"], f)[2] > 0.9
           and max(hull["verts"][i][2] for i in f) < top]
    assert bed, "the pickup has an open bed (a floor sunk under the rails)"


def test_wheel_diameter_and_dims_order():
    plan = B.blank_plan("automobile", body="hatchback", dims="4.4,2.0,1.45", wheel_d=0.62)
    assert plan["dims"] == {"L": 4.4, "W": 2.0, "H": 1.45} and plan["anchors"]["wheel_d"] == 0.62
    scoot = B.blank_plan("bike", body="scooter", wheel_d=0.5)
    assert scoot["anchors"]["wheel_d"] == 0.5 and scoot["body"] == "scooter"
    with pytest.raises(SatkError):
        B.blank_plan("bike", body="chopper")
    with pytest.raises(SatkError):
        B.blank_plan("boat", body="sedan")


def test_scooter_is_built_around_its_frames():
    """Steering axis inside the leg shield, seat 10 cm under the rider dummy, muffler tip at the exhaust dummy."""
    plan, spec = _spec("bike", body="scooter")
    a = plan["anchors"]
    assert [p["name"] for p in spec["parts"]] == ["chassis", "forks_front", "handlebars", "mudguard", "wheel_front",
                                                 "wheel_rear"]
    assert set(plan["slots"]) >= {"mudguard", "forks_front"}
    body = next(p for p in spec["pieces"] if p["name"] == "body")
    pv, ax = a["pivot"], a["axis"]
    tan = -ax[1] / ax[2]
    for z in (a["ground_z"] + 0.35, a["ground_z"] + 0.6, a["headlights"][2] - 0.1):
        ay = pv[1] - (z - pv[2]) * tan
        ring = [v for v in body["verts"] if abs(v[2] - z) < 0.12 and abs(v[1] - ay) < 0.3 and v[0] < 0.02]
        assert ring and min(v[1] for v in ring) < ay < max(v[1] for v in ring), (z, ay)
    seat = next(p for p in spec["pieces"] if p["name"] == "seat")
    assert max(v[2] for v in seat["verts"]) == pytest.approx(a["seat"][2] - 0.10, abs=0.01)
    eng = next(p for p in spec["pieces"] if p["name"] == "engine")
    ex = a["exhaust"]
    assert min(((v[0] - ex[0]) ** 2 + (v[1] - ex[1]) ** 2 + (v[2] - ex[2]) ** 2) ** 0.5 for v in eng["verts"]) < 0.08
    for nm in ("wheel_front", "wheel_rear"):
        w = next(p for p in spec["pieces"] if p["name"] == nm)
        assert {"tyre", "rim"} <= set(w["role"])


def test_blanks_are_deterministic_and_match_the_golden_numbers():
    variants = [(kind, None) for kind in KINDS] + [("automobile", b) for b in BODIES[1:]] + [("bike", "scooter")]
    for kind, body in variants:
        for tier in TIERS:
            kw = {"body": body} if body else {}
            _plan, a = _spec(kind, tier, **kw)
            _plan, b = _spec(kind, tier, **kw)
            assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
            tris = {p["name"]: B.tri_count(p["faces"]) for p in a["pieces"]}
            key = f"{kind}/{tier}" + (f"/{body}" if body else "")
            assert tris == GOLDEN["tris"][key], (key, tris)


def test_world_blank_origin_follows_the_like_numbers(monkeypatch):
    """With ``like`` a world blank sits where the like model sits (origin of its footprint, its lowest point)."""
    fake = {"sid": "model:1", "model": "colt45", "kind": "weapon", "dims": {"L": 0.2, "W": 0.5, "H": 0.3},
            "bbox": [[0.0, -0.1, -0.05], [0.5, 0.1, 0.25]], "frames": {}, "wheel_scale": None, "warn": []}
    monkeypatch.setattr(B, "_like_numbers", lambda *a, **k: fake)
    plan = B.blank_plan("prop_box", name="gun", like="model:1")
    assert plan["dims"] == {"L": 0.2, "W": 0.5, "H": 0.3} and plan["anchors"]["origin"] == [0.25, 0.0, -0.05]
    lo, hi = _bbox(B.mesh_spec(plan))
    assert lo == pytest.approx([0.0, -0.1, -0.05], abs=1e-4) and hi == pytest.approx([0.5, 0.1, 0.25], abs=1e-4)


# --------------------------------------------------------------------------- the op


def test_op_is_cli_only_with_an_english_summary():
    spec = get_op("kit.blank")
    assert spec.mcp is False and spec.group == "blender" and len(spec.summary) <= 300 and spec.summary.isascii()
    assert spec.param("kind").default is None and spec.param("tier").default == "sa_plus"


def test_op_lists_the_blanks_and_writes_a_plan(run_cli, satk_home):
    r = run_cli(["kit", "blank"])
    env = json.loads(r.out)
    assert r.code == 0 and env["total"] == 8 and [row[0] for row in env["rows"]] == list(KINDS)
    r = run_cli(["kit", "blank", "--kind", "prop_cyl", "--dims", "0.6,0.6,1.1", "--name", "mybin", "--plan-only"])
    env = json.loads(r.out)
    assert r.code == 0 and env["kind"] == "prop_cyl" and env["tris"]["prop"] > 100
    plan = json.loads(Path(env["plan"]).read_text(encoding="utf-8"))
    assert plan["name"] == "mybin" and plan["dims"] == {"L": 0.6, "W": 0.6, "H": 1.1} and Path(env["plan"]).is_relative_to(
        satk_home / "work" / "out" / "kit" / "mybin")


def test_op_errors_say_how_to_fix_them(run_cli, satk_home):
    r = run_cli(["kit", "blank", "--kind", "boxx", "--plan-only"])
    env = json.loads(r.out)
    assert r.code != 0 and env["error"]["code"] == "NOT_FOUND" and "prop_box" in env["error"].get("did_you_mean", [])
    r = run_cli(["kit", "blank", "--kind", "bike", "--dims", "1,2", "--plan-only"])
    assert json.loads(r.out)["error"]["code"] == "BAD_PARAMS"
    r = run_cli(["kit", "blank", "--kind", "automobile", "--out", str(satk_home.parent / "elsewhere"), "--plan-only"])
    assert json.loads(r.out)["error"]["code"] == "PROTECTED_PATH"


def test_blender_side_registers_the_methods():
    text = (Path(__file__).resolve().parents[2] / "blender" / "satk_blender" / "kit" / "methods.py").read_text(encoding="utf-8")
    assert '"kit.blank": _dff(blank_method)' in text and '"kit.blank_split": _dff(split_method)' in text


# --------------------------------------------------------------------------- the agent docs

ROOT = Path(__file__).resolve().parents[2]
QUICK = ROOT / "docs" / "agent" / "creation-quickstart.md"
TOPIC = ROOT / "data" / "style" / "topics" / "creation.md"


def test_quickstart_is_short_english_and_the_help_topic():
    text = QUICK.read_text(encoding="utf-8")
    assert len(text.encode("utf-8")) <= 6144 and text.isascii()
    assert TOPIC.read_text(encoding="utf-8") == text, "refresh: copy docs/agent/creation-quickstart.md to data/style/topics/creation.md"
    from satk.runtime import help as H
    from satk.style.ops import register_topics

    register_topics()                                     # the style package registers its help topics
    env = H.render("creation")
    assert env["topic"] == "creation" and env["text"].strip() == text.strip()
    for kind in ("Car", "Prop", "Building", "Weapon", "Ped"):
        assert re.search(rf"^## {kind}", text, re.M), kind


def _commands(text: str):
    """``(line number, tokens)`` of every ``satk ...`` line of the fenced blocks."""
    from satk.docs.smoke import tokenize

    out, fence = [], False
    for i, line in enumerate(text.splitlines(), 1):
        if line.startswith("```"):
            fence = not fence
            continue
        if fence and line.startswith("satk "):
            out.append((i, tokenize(line)[1:]))
    return out


def test_every_quickstart_command_names_a_real_operation_and_flags():
    from satk.core.cli import GLOBAL_FLAGS, CommandTree
    from satk.core.registry import all_ops

    tree = CommandTree.build(all_ops())
    cmds = _commands(QUICK.read_text(encoding="utf-8"))
    assert len(cmds) >= 25
    for line, argv in cmds:
        node, used, rest = tree.walk([a for a in argv if a not in GLOBAL_FLAGS])
        assert node.spec is not None, (line, argv)
        flags = {"--" + p.name.replace("_", "-") for p in node.spec.params} | {"--no-" + p.name.replace("_", "-")
                                                                               for p in node.spec.params}
        for tok in rest:
            if tok.startswith("--"):
                assert tok in flags, (line, tok, sorted(flags))


def test_every_blender_call_in_the_quickstart_names_a_kit_or_studio_method():
    text = QUICK.read_text(encoding="utf-8")
    methods = set(re.findall(r"satk blender call ([a-z_.]+)", text))
    assert {"kit.blank_split", "kit.wheel", "kit.shade", "kit.vlo", "kit.damage", "kit.lod",
            "mesh.transform"} <= methods
    src = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "blender" / "satk_blender").rglob("*.py"))
    for m in methods:
        if m.startswith(("mesh.", "kit.")):
            assert f'"{m}"' in src, m                      # registered in a METHODS table
    params = re.findall(r"--params '(\{.*?\})'", text)
    assert len(params) >= 8
    for param in params:
        json.loads(param)                                 # the JSON of every example parses


def test_skill_points_to_the_quickstart_first():
    skill = (ROOT / "docs" / "agent" / "SKILL.md").read_text(encoding="utf-8")
    assert len(skill.splitlines()) <= 400
    sec = skill.split("## 13. Creating assets", 1)[1]
    first = sec.split("\n- ", 2)[1]
    assert 'satk_help("creation")' in first and "kit.blank" in first
    assert 'satk_help("creation")' in skill.split("| S25-S26 |", 1)[1].split("\n", 1)[0]
    wf = (ROOT / "docs" / "agent" / "workflows.md").read_text(encoding="utf-8")
    assert 'satk_help("creation")' in wf and "kit.blank_split" in wf and "kit.blank" in wf
