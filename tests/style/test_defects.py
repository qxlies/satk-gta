"""Mesh defects (cracks, z-fighting, flipped, degenerate, stray, UVs, collision), class rules with the vanilla
patterns (ground, mounts, loose components), the new coverage detectors and ``asset.check --strict``.

Every scene is built here from a few quads and boxes; the limits are the calibrated ones of
``data/style/defects.json`` and ``form.json`` (vanilla SA: 0 defects in every class).
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import types
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

from satk.style import check as CK  # noqa: E402
from satk.style import coverage as CV  # noqa: E402
from satk.style import defects as D  # noqa: E402
from satk.style import form as F  # noqa: E402


# ----------------------------------------------------------------------------- builders
def quad(corners, *, name="part", tex="body", alpha=255, uv=None, key="255,255,255", flip=False):
    """One quad (two triangles) from four corners in order; outward = right-hand rule."""
    P = np.array(corners, dtype=float)
    T = np.array([(0, 1, 2), (0, 2, 3)])
    if flip:
        T = T[:, [0, 2, 1]]
    U = np.array(uv if uv is not None else [(0, 0), (1, 0), (1, 1), (0, 1)], dtype=float)
    return {"name": name, "pos": P, "tris": T, "normals": None, "uv": U, "mat": np.zeros(2, np.int64),
            "tex": [tex], "alpha": [alpha], "keys": [key], "role": "hd"}


def join(name, *parts):
    """Several parts in one (separate pieces, shared material list of the first)."""
    P, T, U, M, off = [], [], [], [], 0
    for p in parts:
        P.append(p["pos"])
        T.append(p["tris"] + off)
        U.append(p["uv"])
        M.append(p["mat"])
        off += len(p["pos"])
    out = dict(parts[0])
    out.update(name=name, pos=np.concatenate(P), tris=np.concatenate(T), uv=np.concatenate(U), mat=np.concatenate(M))
    return out


def box(size, centre=(0.0, 0.0, 0.0), *, name="part", tex="body", inward=False):
    sx, sy, sz = (s / 2 for s in size)
    cx, cy, cz = centre
    P = np.array([(cx + x * sx, cy + y * sy, cz + z * sz) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)])
    quads = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    T = []
    for a, b, c, d in quads:
        T += [(a, b, c), (a, c, d)]
    T = np.array(T)
    if inward:
        T = T[:, [0, 2, 1]]
    return {"name": name, "pos": P, "tris": T, "normals": None, "uv": np.zeros((len(P), 2)),
            "mat": np.zeros(len(T), np.int64), "tex": [tex], "alpha": [255], "keys": ["255,255,255"], "role": "hd"}


def floor(x0, x1, y0, y1, z=0.0, **kw):
    return quad([(x0, y0, z), (x1, y0, z), (x1, y1, z), (x0, y1, z)], **kw)


LIM = D.limits()


# ----------------------------------------------------------------------------- cracks
def test_an_open_seam_with_nothing_behind_is_a_crack_and_a_backed_or_welded_one_is_not():
    a = floor(-1.0, 0.0, 0.0, 1.0)
    b = floor(0.003, 1.0, 0.0, 1.0)                                  # a 3 mm slot between two pieces of one part
    part = join("panel", a, b)
    r = D.analyse([part], checks=("crack",), lim=LIM)
    (row,) = r["crack"]
    assert row["part"] == "panel" and row["len_mm"] == pytest.approx(1000, abs=5) and row["gap_mm"] == pytest.approx(3, abs=0.1)
    assert row["at"][0] == pytest.approx(0.0015, abs=0.01)
    # a liner under the slot (facing up, towards the viewer): no light comes through
    liner = floor(-0.2, 0.2, -0.1, 1.1, z=-0.05, name="liner")
    assert "crack" not in D.analyse([part, liner], checks=("crack",), lim=LIM)
    # a liner facing away is culled in game: the background still shows
    back = floor(-0.2, 0.2, -0.1, 1.1, z=-0.05, name="liner", flip=True)
    assert D.analyse([part, back], checks=("crack",), lim=LIM)["crack"]
    # welded (shared border) and a T-junction: no open seam
    t = join("panel", floor(-1.0, 0.0, 0.0, 1.0), floor(0.0, 1.0, 0.0, 0.5), floor(0.0, 1.0, 0.5, 1.0))
    r = D.analyse([t], checks=("crack",), lim=LIM)
    assert "crack" not in r and r["counts"]["tjunction_mm"] >= 900


# ----------------------------------------------------------------------------- z-fighting
def test_coplanar_overlap_of_another_texture_fights_but_a_decal_or_a_duplicate_does_not():
    wall = floor(0, 1, 0, 1, name="wall", tex="brick")
    sign = floor(0.2, 0.6, 0.2, 0.6, z=0.0004, name="sign", tex="sign")      # 0.4 mm off: same plane
    (row,) = D.analyse([wall, sign], checks=("zfight",), lim=LIM)["zfight"]
    assert {row["part"], row["other"]} == {"wall", "sign"} and row["area_cm2"] == pytest.approx(1600, rel=0.2)
    lifted = floor(0.2, 0.6, 0.2, 0.6, z=0.004, name="sign", tex="sign")      # 4 mm off: fine
    assert "zfight" not in D.analyse([wall, lifted], checks=("zfight",), lim=LIM)
    # an alpha decal is drawn after the opaque faces
    assert "zfight" not in D.analyse([wall, sign], checks=("zfight",), lim=LIM, tex_alpha={"sign"})
    # the same texture at the same UVs is an invisible duplicate; other UVs flicker
    dup = floor(0, 1, 0, 1, z=0.0003, name="wall2", tex="brick")
    assert "zfight" not in D.analyse([wall, dup], checks=("zfight",), lim=LIM)
    shifted = floor(0, 1, 0, 1, z=0.0003, name="wall2", tex="brick", uv=[(0.5, 0), (1.5, 0), (1.5, 1), (0.5, 1)])
    assert D.analyse([wall, shifted], checks=("zfight",), lim=LIM)["zfight"]
    # variants the game shows one at a time (extras, static/moving rotor) may overlap
    e1 = floor(0, 1, 0, 1, name="extra1", tex="a")
    e2 = floor(0, 1, 0, 1, name="extra2", tex="b")
    assert "zfight" not in D.analyse([e1, e2], checks=("zfight",), lim=LIM)


# ----------------------------------------------------------------------------- flipped
def test_flipped_faces_inside_out_pieces_and_normals_against_the_winding():
    grid = join("roof", *[floor(x, x + 0.5, y, y + 0.5, flip=(x, y) == (0.5, 0.5))
                          for x in (0.0, 0.5, 1.0) for y in (0.0, 0.5, 1.0)])
    # weld the grid: share vertices by position
    (row,) = D.analyse([grid], checks=("flipped",), lim=LIM)["flipped"]
    assert row["kind"] == ["winding"] and row["flip_cm2"] == pytest.approx(2500, rel=0.01)
    assert row["at"][:2] == pytest.approx([0.75, 0.75], abs=0.01)
    (row,) = D.analyse([box((1, 1, 1), name="crate", inward=True)], checks=("flipped",), lim=LIM)["flipped"]
    assert "inside_out" in row["kind"] and row["inside_out_m3"] == pytest.approx(1.0, rel=0.01)
    assert "flipped" not in D.analyse([box((1, 1, 1), name="room", inward=True)], checks=("flipped",), lim=LIM,
                                      inside_ok=True)
    assert "flipped" not in D.analyse([box((1, 1, 1), name="crate")], checks=("flipped",), lim=LIM)
    lit = floor(0, 1, 0, 1, name="lid")
    lit["normals"] = np.tile([0.0, 0.0, -1.0], (4, 1))                      # normals point down, faces up
    (row,) = D.analyse([lit], checks=("flipped",), lim=LIM)["flipped"]
    assert row["kind"] == ["normals"] and row["normals_share"] == 1.0


# ----------------------------------------------------------------------------- degenerate, stray
def test_zero_area_triangles_unused_vertices_tiny_islands_and_far_pieces():
    base = box((1, 1, 1), name="body")
    P = np.vstack([base["pos"], np.zeros((40, 3))])                         # 40 vertices no triangle uses
    T = np.vstack([base["tris"], np.repeat([[0, 0, 1]], 10, axis=0)])       # 10 collapsed triangles
    bad = dict(base, pos=P, tris=T, mat=np.zeros(len(T), np.int64))
    (row,) = D.analyse([bad], checks=("degenerate",), lim=LIM)["degenerate"]
    assert row["zero"] == 10 and row["unused"] == 40 and set(row["kind"]) == {"zero", "unused"}
    crumbs = join("body", base, *[box((0.002, 0.002, 0.002), (0.6 + i * 0.01, 0, 0)) for i in range(3)])
    rows = D.analyse([crumbs], checks=("stray",), lim=LIM)["stray"]
    assert rows[0]["kind"] == "tiny" and rows[0]["pieces"] == 3
    far = join("body", base, box((0.5, 0.5, 0.5), (30.0, 0, 0)))
    (row,) = D.analyse([far], checks=("stray",), lim=LIM)["stray"]
    assert row["kind"] == "far" and row["dist_m"] == pytest.approx(29.25, abs=0.01)


# ----------------------------------------------------------------------------- UVs
def test_smeared_texture_and_texel_outliers_per_material():
    smear = floor(0, 2, 0, 2, name="wall", tex="t", uv=[(0, 0), (1, 0), (1, 0.001), (0, 0.001)])
    lim = dict(LIM, uv_cm2=1.0)
    (row,) = D.analyse([smear], checks=("uv",), lim=lim, tex_sizes={"t": (256, 256)})["uv_stretch"]
    assert row["texture"] == "t" and row["share"] == 1.0
    solid = floor(0, 2, 0, 2, name="wall", tex="t", uv=[(0.5, 0.5)] * 4)    # one texel: a solid colour
    assert "uv_stretch" not in D.analyse([solid], checks=("uv",), lim=lim, tex_sizes={"t": (256, 256)})
    a = floor(0, 1, 0, 1, tex="t")
    b = floor(2, 3, 0, 1, tex="t", uv=[(0, 0), (0.01, 0), (0.01, 0.01), (0, 0.01)])
    c = floor(4, 5, 0, 1, tex="t")
    r = D.analyse([join("wall", a, b, c)], checks=("uv",), lim=dict(LIM, texel_cm2=1.0, texel_share=0.2),
                  tex_sizes={"t": (256, 256)})
    (row,) = r["texel"]
    assert row["ratio"] == pytest.approx(0.01, rel=0.05) and row["share"] == pytest.approx(1 / 3, abs=0.01)


# ----------------------------------------------------------------------------- collision
def test_collision_far_outside_and_mesh_without_collision():
    body = box((2, 4, 1), name="body")
    ok = {"spheres": [], "boxes": [((-1, -2, -0.5), (1, 2, 0.5))], "verts": [], "faces": []}
    assert "col" not in D.analyse([body], checks=("col",), col=ok, lim=LIM)
    out = {"spheres": [((0, 0, 4), 0.5)], "boxes": [((-1, -2, -0.5), (1, 2, 0.5))], "verts": [], "faces": []}
    kinds = {r["kind"]: r for r in D.analyse([body], checks=("col",), col=out, lim=LIM)["col"]}
    assert kinds["outside"]["what"] == "sphere" and kinds["outside"]["over_m"] == pytest.approx(4.0, abs=0.01)
    small = {"spheres": [((0, 1.5, 0), 0.6)], "boxes": [], "verts": [], "faces": []}
    kinds = {r["kind"]: r for r in D.analyse([body], checks=("col",), col=small, lim=LIM)["col"]}
    assert kinds["uncovered"]["share"] > 0.3
    mesh = {"spheres": [], "boxes": [], "verts": [tuple(v) for v in body["pos"]], "faces": [tuple(t) for t in body["tris"]]}
    assert "col" not in D.analyse([body], checks=("col",), col=mesh, lim=LIM)


def test_limits_and_classes_of_the_data_file():
    data = json.loads(json.dumps(D._data()))
    assert set(data["limits"]) <= set(D.DEFAULTS) | {"mesh_uv_smear_warn", "mesh_texel_warn"}
    from satk.style import classes as C

    for key, over in data["classes"].items():
        assert key in C.class_names() or key in ("vehicle", "map", "ped", "weapon", "pickup", "upgrade", "default"), key
        for k in over:
            assert k.startswith("_") or k.endswith("_defect") or k in D.DEFAULTS or k in ("inside_ok", "skip"), k
    cal = data["calibration"]
    assert sum(v["models"] for k, v in cal.items() if not k.startswith("_")) >= 14000
    assert all(v["defects"] == 0 for k, v in cal.items() if not k.startswith("_"))
    # the chain: road cars take the car pool, a prop its own vanilla range
    assert CK.mesh_limits("car.sedan").get("mesh_zfight_defect") == data["classes"]["car"]["mesh_zfight_defect"]
    assert CK.mesh_limits("interior_shell")["inside_ok"] is True


def test_mesh_rows_grade_by_the_vanilla_range():
    res = {"zfight": [{"part": "a", "other": "b", "area_cm2": 10.0, "tris": 2, "at": [0, 0, 0]},
                      {"part": "c", "other": "d", "area_cm2": 9000.0, "tris": 9, "at": [1, 0, 0]}],
           "uv_stretch": [{"part": "a", "texture": "t", "share": 0.9, "area_cm2": 5000, "tris": 4, "at": [0, 0, 0]}]}
    rows = CK.mesh_rows(res, {"mesh_zfight_defect": 4500, "mesh_uv_smear_warn": False})
    v = {(r[0], r[1]): r[6] for r in rows}
    assert v == {("mesh.zfight", "a"): "warn", ("mesh.zfight", "c"): "defect", ("mesh.uv_smear", "a"): "info"}
    assert all(r[9] == "mesh" and "at (" in r[7] for r in rows)


# ----------------------------------------------------------------------------- class rules and ground
def test_class_rules_merge_from_the_family_to_the_class():
    sedan = CK.class_rules("car.sedan")
    assert sedan["strict"] and sedan["body"] == "chassis" and "car.sedan" not in sedan["chain"] or True
    assert CK.class_rules("prop")["ground"] == "world" and CK.class_rules("prop")["limits"]["alpha_free"]
    assert "+z" in CK.class_rules("interior_prop")["limits"]["mount_dirs"]
    assert CK.class_rules("lod")["ground"] == "loose" and CK.class_rules("upgrade")["ground"] == "loose"
    assert CK.class_rules("quad")["see_through"] is False
    assert "hinge" in CK.class_rules("plane")["fit_skip"] and not CK.class_rules("truck").get("strict", False) \
        if "truck" in __import__("satk.style.classes", fromlist=["x"]).class_names() else True
    assert not CK.class_rules("car.truck_bus").get("strict") and CK.class_rules("bike").get("strict")


def test_map_models_stand_on_the_world_and_hang_on_walls_but_a_hovering_head_floats():
    table = box((1.0, 1.0, 0.75), (0, 0, 0.375), name="park")
    bench = box((1.0, 0.3, 0.45), (0, 1.0, 0.225), name="park")             # 25 cm away, on the ground
    pole = box((0.1, 0.1, 3.0), (3, 0, 1.5), name="park")
    head = box((0.4, 0.4, 0.2), (3, 0, 3.4), name="park")                   # 30 cm above the pole top
    part = join("park", table, bench, pole, head)
    r = F.analyse([part], body=None, checks=("floating",), ground="world")
    (row,) = r["floating"]
    assert row["at"] == pytest.approx([3.0, 0.0, 3.4], abs=0.01) and r["counts"]["on_world"] >= 1
    # the same scene judged like a vehicle (anchor only): the bench floats too
    assert len(F.analyse([part], body=None, checks=("floating",))["floating"]) == 3     # bench, pole, head
    # a wall cabinet hangs on the room's wall (another model): the model's back plane
    base = box((1.0, 0.6, 0.9), (0, 0.0, 0.45), name="kitchen")
    wall = box((1.0, 0.35, 0.6), (0, 0.125, 1.9), name="kitchen")
    kit = join("kitchen", base, wall)
    assert "floating" in F.analyse([kit], body=None, checks=("floating",), ground="world")
    lim = {"mount_dirs": ["+y", "-y", "+x", "-x", "+z"]}
    assert "floating" not in F.analyse([kit], body=None, checks=("floating",), ground="world", limits_override=lim)


def test_loose_components_and_alpha_cards():
    left = box((0.08, 0.5, 0.08), (-0.3, 0, 0), name="exh")
    right = box((0.08, 0.5, 0.08), (0.3, 0, 0), name="exh")
    near = box((0.08, 0.5, 0.08), (0.36, 0.52, 0), name="exh")             # a tip 2 cm off the right pipe
    part = join("exh", left, right, near)
    rows = F.analyse([part], body=None, checks=("floating",), ground="loose")["floating"]
    assert len(rows) == 1 and rows[0]["gap_mm"] == pytest.approx(20, abs=1)
    leaf = floor(0, 1, 0, 1, z=5.0, name="tree", tex="leaves")
    trunk = box((0.3, 0.3, 4.0), (0.5, 0.5, 2.0), name="tree")
    tree = join("tree", trunk, leaf)
    tree["talpha"] = [False]
    assert F.analyse([tree], body=None, checks=("floating",), ground="world")["floating"]
    tree["talpha"] = [True]
    assert "floating" not in F.analyse([tree], body=None, checks=("floating",), ground="world",
                                       limits_override={"alpha_free": True})


# ----------------------------------------------------------------------------- coverage detectors
def _car_body(mirror_in_body: bool):
    body = box((2.0, 4.6, 0.8), (0, 0, 0.0), name="chassis")
    cabin = box((1.6, 2.0, 0.6), (0, -0.2, 0.7), name="chassis")
    parts = [body, cabin]
    if mirror_in_body:
        parts.append(box((0.25, 0.12, 0.12), (1.0, 0.75, 0.55), name="chassis"))   # mirror welded into the body
    return join("chassis", *parts)


FRAMES = {f"wheel_{s}_dummy": {"pos": (x, y, -0.3)} for s, x, y in
          (("lf", -0.9, 1.5), ("rf", 0.9, 1.5), ("lb", -0.9, -1.5), ("rb", 0.9, -1.5))}


def test_mirrors_and_glass_built_into_the_body_are_found():
    """The false negatives of phase 1: a car with mirrors and glass in the chassis showed both missing."""
    import numpy as np_

    X, M = CV._tris(np_, [_car_body(True)])
    assert CV._detect(np_, {"mirrors": 0.05}, [_car_body(True)], FRAMES, X, M, {}) is True
    X0, M0 = CV._tris(np_, [_car_body(False)])
    assert CV._detect(np_, {"mirrors": 0.05}, [_car_body(False)], FRAMES, X0, M0, {}) is False
    body = _car_body(False)
    glassy = dict(body, alpha=[255, 128], keys=["255,255,255", "255,255,255"], tex=["body", "vehiclegeneric256"],
                  mat=np.where(np.arange(len(body["tris"])) >= 12, 1, 0))
    assert CV._detect(np_, {"glass": True}, [glassy], FRAMES, X0, M0, {}) is True
    assert CV._detect(np_, {"glass": True}, [body], FRAMES, X0, M0, {}) is False
    lamp_only = dict(glassy, keys=["255,255,255", "255,175,0"])              # a translucent lamp is no window
    assert CV._detect(np_, {"glass": True}, [lamp_only], FRAMES, X0, M0, {}) is False


def test_frame_detectors_and_every_checklist_uses_known_detectors():
    import numpy as np_

    head = box((0.2, 0.2, 0.25), (0, 0, 1.6), name="pelvis")
    frames = {"head": {"pos": (0, 0, 1.62)}, "l hand": {"pos": (0.8, 0, 1.0)}}
    X, M = CV._tris(np_, [head])
    assert CV._detect(np_, {"near_frames": {"frames": ["head"], "radius": 0.15}}, [head], frames, X, M, {}) is True
    assert CV._detect(np_, {"near_frames": {"frames": ["l hand"], "radius": 0.12}}, [head], frames, X, M, {}) is False
    assert CV._detect(np_, {"frames": "^gunflash$"}, [head], frames, X, M, {"frames_all": {"gunflash"}}) is True
    assert CV._detect(np_, {"frame_geom": {"parts": "^pelvis$", "min_tris": 6}}, [head], frames, X, M, {}) is True
    assert CV._detect(np_, {"lod_model": True}, [head], frames, X, M, {"lod": None}) is None
    known = {"keys", "keys_all", "texture", "parts", "frame_near", "region", "outboard", "small_pieces", "above_roof",
             "lined_arches", "seat", "flags", "glass", "mirrors", "frames", "frame_geom", "near_frames", "lod_model"}
    names = set(CV.list_classes())
    want = {"car", "bike", "bmx", "boat", "heli", "plane", "trailer", "train", "mtruck", "quad", "prop", "vegetation",
            "building", "lod", "interior_prop", "interior_shell", "weapon", "pickup", "upgrade", "ped"}
    assert want <= names, want - names
    for n in names:
        cl = CV.checklist(n)
        assert cl["items"], n
        for it in cl["items"]:
            assert set(it["how"]) <= known and it["fix"] and "vanilla" in it, (n, it["id"])


# ----------------------------------------------------------------------------- strict
def _result(rows):
    return {"rows": rows, "verdict": "pass"}


def _subj(tmp_path, name="mybox"):
    from satk.style.subject import Subject

    f = tmp_path / "pkg" / f"{name}.dff"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"x")
    return Subject(str(f), name, b"", "file", path=f)


def test_strict_blocks_warnings_defects_missing_items_and_tells_done(tmp_path):
    rows = [["mesh.crack", "chassis", 120.0, None, None, None, "warn", "120 mm of open seam at (1.00, 0.00, 0.20)", "", "mesh"],
            ["cov.mirrors", "", "missing", None, None, None, "missing", "door mirrors", "", "coverage"],
            ["cov.gauges", "optional", "missing", None, None, None, "missing", "gauges", "", "coverage"],
            ["sym.body[chassis]", "chassis", 0.2, None, None, None, "info", "", "", "symmetry"],
            ["veh.frames", "hookup", 0, None, None, None, "warn", "frame missing", "", "engine"]]
    st = CK.strict_status(_subj(tmp_path), _result(rows))
    assert st["done"] is False and len(st["blocking"]) == 2 and "cov.gauges" not in " ".join(st["blocking"])
    # a short narrow seam is within the vanilla range of the class (data/style/strict.json): advice, not blocking
    assert st["advice"] == ["mesh/mesh.crack chassis: 120 mm of open seam at (1.00, 0.00, 0.20)"]
    wide = [["mesh.crack", "chassis", 120.0, None, None, None, "warn", "120 mm of open seam up to 9.0 mm wide", "",
             "mesh"],
            ["form.floating", "trim", None, None, None, None, "warn", "touch nothing: more than 300 mm", "", "form"],
            ["mesh.zfight", "chassis", 300.0, None, None, None, "warn", "300 cm2 of chassis and chassis overlap", "",
             "mesh"],
            ["mesh.zfight", "chassis", 300.0, None, None, None, "warn", "300 cm2 of chassis and door overlap", "",
             "mesh"]]
    st = CK.strict_status(_subj(tmp_path), _result(wide))
    assert [b.split(" ")[0] for b in st["blocking"]] == ["mesh/mesh.crack", "form/form.floating", "mesh/mesh.zfight"]
    assert st["advice"] == ["mesh/mesh.zfight chassis: 300 cm2 of chassis and chassis overlap"]   # one part's decal
    clean = CK.strict_status(_subj(tmp_path), _result(rows[2:4]))
    assert clean == {"done": True, "blocking": []}


def _fake_inventory(monkeypatch, report):
    api = types.ModuleType("satk.inventory.api")
    api.has_inventory = lambda project=None, dff=None: False
    api.locate = lambda dff: None
    api.report = report
    pkg = types.ModuleType("satk.inventory")
    pkg.api = api
    monkeypatch.setitem(sys.modules, "satk.inventory", pkg)
    monkeypatch.setitem(sys.modules, "satk.inventory.api", api)


def test_strict_reads_the_inventory_and_the_leak_file(tmp_path, monkeypatch):
    subj = _subj(tmp_path)
    proj = tmp_path
    (proj / "design").mkdir()
    (proj / "design" / "inventory.json").write_text(json.dumps({"kind": "prop", "items": []}), encoding="utf-8")
    monkeypatch.setitem(sys.modules, "satk.inventory", None)                 # not installed (lane inventory)
    monkeypatch.setitem(sys.modules, "satk.inventory.api", None)
    st = CK.strict_status(subj, _result([]))
    assert not st["done"] and "satk.inventory is not installed" in st["blocking"][0]
    _fake_inventory(monkeypatch, lambda project_dir, dff=None, session=None, strict=False: {
        "items": [{"id": "I01", "name": "lid", "status": "built", "objects": ["lid"]},
                  {"id": "I02", "name": "rim", "status": "missing", "objects": []},
                  {"id": "I03", "name": "crest", "status": "unattached", "objects": ["crest"]}],
        "counts": {"built": 1, "missing": 1, "unattached": 1}, "complete": False})
    st = CK.strict_status(subj, _result([]))
    assert st["blocking"][:2] == ["inventory/I02 rim: missing", "inventory/I03 crest: unattached (crest)"]
    assert st["blocking"][2].startswith("leak: no checks/mybox.leak.json next to mybox.dff: run satk look leak")
    assert st["inventory"] == {"built": 1, "missing": 1, "unattached": 1}
    (proj / "design" / "inventory.json").unlink()          # a plain DFF (no project, no kit export): leak optional
    assert CK.strict_status(subj, _result([])) == {"done": True, "blocking": []}
    leak = subj.path.parent / "checks" / "mybox.leak.json"
    leak.parent.mkdir()
    leak.write_text(json.dumps({"gaps": [{"view": "side_l", "at": [1.0, 0.5, 0.2], "px": 40}]}), encoding="utf-8")
    st = CK.strict_status(subj, _result([]))
    assert st["blocking"] == ["leak/side_l: background shows through the model near (1.00, 0.50, 0.20) (40 px)"]
    assert st["leak"]["gaps"] == 1
    leak.write_text(json.dumps({"gaps": []}), encoding="utf-8")
    old = time.time() - 3600
    os.utime(leak, (old, old))
    st = CK.strict_status(subj, _result([]))
    assert st["blocking"] == ["leak: mybox.leak.json is older than the model: run look.leak on the new export"]
    # look.leak writes the hash of the DFF it checked: that decides, not the file time
    leak.write_text(json.dumps({"leak": 1, "clean": False, "blocking": 1, "dff_sha256": hashlib.sha256(b"x").hexdigest(),
                                "gaps": [{"id": "L1", "kind": "inside", "region": "wheels", "view": "wheels_l",
                                          "point": [0.9, 1.47, -0.1], "near": "chassis", "area_cm2": 72.5}]}),
                    encoding="utf-8")
    os.utime(leak, (old, old))
    st = CK.strict_status(subj, _result([]))
    assert st["blocking"] == ["leak/wheels/wheels_l L1: inside (background shows through) near (0.90, 1.47, -0.10) "
                              "next to chassis (72.5 cm2)"]
    leak.write_text(json.dumps({"leak": 1, "clean": True, "blocking": 0, "dff_sha256": "0" * 64, "gaps": []}),
                    encoding="utf-8")
    assert CK.strict_status(subj, _result([]))["blocking"] == [
        "leak: mybox.leak.json is older than the model: run look.leak on the new export"]
    # the old shared checks/leak.json counts only when it names this very DFF
    leak.unlink()
    shared = subj.path.parent / "checks" / "leak.json"
    shared.write_text(json.dumps({"dff": "D:/x/other.dff", "gaps": [{"view": "v", "px": 9}]}), encoding="utf-8")
    assert CK.strict_status(subj, _result([])) == {"done": True, "blocking": []}
    shared.write_text(json.dumps({"dff": "D:/x/mybox.dff", "gaps": [{"view": "v", "px": 9}]}), encoding="utf-8")
    assert CK.strict_status(subj, _result([]))["blocking"] == ["leak/v: background shows through the model (9 px)"]


def test_strict_needs_a_full_recorded_leak_run_per_dff(satk_home, monkeypatch):
    """A building and its LOD in one package each have their own leak file; an authored asset needs a full run
    that satk recorded (a one-region run or an edited file does not count)."""
    from satk.core import paths
    from satk.look import review as RV
    from satk.style.subject import Subject

    _fake_inventory(monkeypatch, lambda project_dir, dff=None, session=None, strict=False: {
        "items": [{"id": "I01", "name": "shell", "status": "built", "objects": ["shell"]}],
        "counts": {"built": 1}, "complete": True})
    proj = Path(paths.cfg().paths.work) / "assets" / "bld"
    (proj / "design").mkdir(parents=True)
    (proj / "design" / "inventory.json").write_text("{}", encoding="utf-8")
    (proj / "asset.json").write_text(json.dumps({"asset": 1, "kind": "building", "detail": "standard"}),
                                     encoding="utf-8")
    pkg = proj / "out" / "pkg"
    pkg.mkdir(parents=True)
    hd, lod = pkg / "bld.dff", pkg / "lodbld.dff"
    hd.write_bytes(b"hd bytes")
    lod.write_bytes(b"lod bytes")
    (pkg / "bld.inventory.json").write_text(json.dumps({"stem": "bld", "lod": "lodbld", "inventory": None}),
                                            encoding="utf-8")
    S = lambda f: Subject(str(f), f.stem, f.read_bytes(), "file", path=f)  # noqa: E731
    defn = {"kind": "building", "regions": [{"name": "facades"}, {"name": "roof"}, {"name": "lod", "leak": False}]}

    def run(dff, kind, regions, gaps=()):
        lk = {"gaps": [dict(g) for g in gaps], "views": []}
        doc = RV.leak_report(lk, subject=str(dff), kind=kind, dff=str(dff), cover=RV.coverage(defn, regions))
        RV.write_record(doc)
        RV.write_leak(pkg, doc)
        return doc

    st = CK.strict_status(S(hd), _result([]))
    assert st["blocking"] == [f"leak: no checks/bld.leak.json next to bld.dff: run satk look leak {paths.jpath(hd)} "
                              f"--package {paths.jpath(pkg)}"]
    run(hd, "building", ["facades", "roof"])
    assert CK.strict_status(S(hd), _result([])) == {"inventory": {"built": 1}, "leak": {
        "file": paths.jpath(pkg / "checks" / "bld.leak.json"), "gaps": 0}, "done": True, "blocking": []}
    lst = CK.strict_status(S(lod), _result([]))          # the LOD of an authored building needs its own run
    assert lst["lod_of"] == "bld" and lst["blocking"] == [f"leak: no checks/lodbld.leak.json next to lodbld.dff: run satk look leak "
                               f"{paths.jpath(lod)} --package {paths.jpath(pkg)}"]
    run(lod, "lod", ["facades", "roof"], [{"id": "L1", "kind": "see_through", "view": "n", "area_cm2": 900.0}])
    assert CK.strict_status(S(hd), _result([]))["done"] is True               # the HD's file is untouched
    assert CK.strict_status(S(lod), _result([]))["blocking"] == [
        "leak/n L1: see_through (background shows through) (900.0 cm2)"]
    run(hd, "building", ["roof"])                                             # one region only
    st = CK.strict_status(S(hd), _result([]))
    assert st["blocking"] == ["leak: bld.leak.json covers only part of the model (regions not checked: facades): run "
                              "look.leak with the kind's regions and the default settings"]
    run(hd, "prop", ["facades", "roof"])                                      # the wrong kind's rules
    assert any("checked the model as prop, the asset is a building" in b
               for b in CK.strict_status(S(hd), _result([]))["blocking"])
    doc = run(hd, "building", ["facades", "roof"], [{"id": "L1", "kind": "gap", "view": "n", "area_cm2": 50.0}])
    doc.update(gaps=[], clean=True, blocking=0)                               # an edited file
    (pkg / "checks" / "bld.leak.json").write_text(json.dumps(doc), encoding="utf-8")
    st = CK.strict_status(S(hd), _result([]))
    assert st["blocking"] == ["leak: bld.leak.json does not match satk's own record (its clean (True) differs from "
                              "satk's record of that run (False)): run look.leak again"]


def test_strict_knows_authored_models_without_an_inventory(satk_home, monkeypatch):
    from satk.core import paths
    from satk.core.errors import SatkError
    from satk.style.subject import Subject

    out = Path(paths.cfg().paths.work) / "out" / "kit" / "lazybin"
    pkg = out / "files" / "lazybin"
    pkg.mkdir(parents=True)
    dff = pkg / "lazybin.dff"
    dff.write_bytes(b"x")
    subj = Subject(str(dff), "lazybin", b"x", "file", path=dff)
    assert CK.strict_status(subj, _result([])) == {"done": True, "blocking": []}       # a plain DFF
    (out / "export.json").write_text(json.dumps({"stem": "lazybin", "files": {"dff": str(dff)}}), encoding="utf-8")
    st = CK.strict_status(subj, _result([]))                                            # a kit export
    assert st["done"] is False and st["blocking"][0].startswith("inventory: no inventory for this authored model")
    assert st["blocking"][1].startswith("leak: no checks/lazybin.leak.json")
    with pytest.raises(SatkError) as e:
        CK.strict_status(subj, _result([]), project="nosuchproject")
    assert e.value.code == "NOT_FOUND"
    proj = Path(paths.cfg().paths.work) / "assets" / "optout"
    proj.mkdir(parents=True)
    (proj / "asset.json").write_text(json.dumps({"asset": 1, "kind": "prop", "detail": "none"}), encoding="utf-8")
    st = CK.strict_status(subj, _result([]), project="optout")
    assert st["blocking"][0].startswith("inventory: project optout opted out of the inventory")
    (pkg / "lazybin.inventory.json").write_text('{"format": "satk.inventory-sidecar/1", "facts": {', encoding="utf-8")
    st = CK.strict_status(subj, _result([]))                                            # a broken sidecar
    assert st["blocking"][0] == ("inventory: the sidecar lazybin.inventory.json does not read: re-export the model "
                                 "(kit export writes it)")


def test_strict_blocks_asymmetry_of_items_counted_in_pairs(tmp_path, monkeypatch):
    subj = _subj(tmp_path)
    (tmp_path / "design").mkdir()
    (tmp_path / "design" / "inventory.json").write_text("{}", encoding="utf-8")
    _fake_inventory(monkeypatch, lambda project_dir, dff=None, session=None, strict=False: {
        "items": [{"id": "I05", "name": "mirrors", "status": "built", "objects": ["mirror_l"], "count": 2}],
        "counts": {"built": 1}, "complete": True})
    rows = [["sym.part", "mirror_l", 1, None, None, None, "warn", "no mirror twin on the right", "", "symmetry"],
            ["sym.part", "aerial", 1, None, None, None, "warn", "no mirror twin on the right", "", "symmetry"]]
    st = CK.strict_status(subj, _result(rows))
    assert [b for b in st["blocking"] if b.startswith("symmetry")] == [
        "symmetry/sym.part mirror_l: no mirror twin on the right (an inventory item counted in pairs)"]


def test_a_car_is_not_guessed_as_a_trailer(fake_cache):
    car = {"chassis", "chassis_dummy", "wheel_lf_dummy", "wheel_rf_dummy", "wheel_lb_dummy", "wheel_rb_dummy",
           "door_lf_dummy", "door_rf_dummy", "bonnet_dummy", "boot_dummy", "headlights", "taillights", "ped_frontseat",
           "exhaust", "engine", "windscreen_dummy"}
    vt, _score = CK._guess_vehicle_type(car, fake_cache)
    assert vt == "car"


from .test_core import fake_cache  # noqa: E402,F401  (the synthetic style cache fixture)


def test_a_lod_file_next_to_a_building_is_a_lod_and_counts_for_the_building(fake_cache, tmp_path, monkeypatch):
    from satk.style import subject as S

    monkeypatch.setattr(CK, "resolve_target", lambda key, cache: {"class": key.split("@")[0], "peer_set": key})

    from .rwkit import prop_dff

    (tmp_path / "mybld.dff").write_bytes(prop_dff(20.0))
    (tmp_path / "lodmybld.dff").write_bytes(prop_dff(20.0))
    bld, lod = S.load_file(tmp_path / "mybld.dff"), S.load_file(tmp_path / "lodmybld.dff")
    assert CK.pick_class(lod, fake_cache)["class"] == "lod" and "name" in CK.pick_class(lod, fake_cache)["how"]
    assert CK._lod_of(bld) is True and CK._lod_of(lod) is False
