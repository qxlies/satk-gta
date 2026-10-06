"""Mesh and UV methods of lane a2-kit with the real Blender 5.1 (markers blender, slow; no game data).

Dogfood friction of wave A1: ``mesh.lathe`` wrote no UVs, ``uv.unwrap cylinder`` made faces that span the whole ``u``
range, ``uv.fit`` kept the aspect when it should fill a rectangle, ``mesh.inset`` and ``mesh.extrude`` left faces with
no UV area (and an inset that is too thick collapsed silently), a free sheet extruded to an open box, and
``kit.bake`` knew only square sizes.
"""

from __future__ import annotations

import math

import pytest

from satk.core.errors import SatkError

pytestmark = [pytest.mark.blender, pytest.mark.slow]

BIN_PROFILE = [[0, 0.03], [0.26, 0.03], [0.29, 0.0], [0.29, 0.09], [0.275, 0.12], [0.275, 0.86], [0.315, 0.89],
               [0.315, 0.96], [0.29, 1.03], [0.17, 1.09], [0.15, 1.02], [0, 1.02]]

_UV = """
me = bpy.data.objects[args['o']].data
uv = me.uv_layers.active.data
spans, zero, caps = [], 0, 0
for p in me.polygons:
    us = [uv[i].uv.x for i in p.loop_indices]
    vs = [uv[i].uv.y for i in p.loop_indices]
    uu, vv = max(us) - min(us), max(vs) - min(vs)
    n = len(us)
    area = abs(sum(us[i] * vs[(i + 1) % n] - us[(i + 1) % n] * vs[i] for i in range(n))) / 2
    spans.append(uu)
    if area < 1e-9:
        zero += 1
allu = [uv[i].uv.x for i in range(len(uv))]
allv = [uv[i].uv.y for i in range(len(uv))]
result['max_span'] = max(spans)
result['zero'] = zero
result['u'] = [min(allu), max(allu)]
result['v'] = [min(allv), max(allv)]
result['faces'] = len(me.polygons)
"""

_TOPO = """
import bmesh
bm = bmesh.new()
bm.from_mesh(bpy.data.objects[args['o']].data)
bm.normal_update()
vol = sum(f.calc_center_median().dot(f.normal) * f.calc_area() / 3 for f in bm.faces)
uvl = bm.loops.layers.uv.active
zero = 0
for f in bm.faces:
    uv = [l[uvl].uv.copy() for l in f.loops]
    if sum(abs((uv[i] - uv[0]).cross(uv[i + 1] - uv[0])) / 2 for i in range(1, len(uv) - 1)) < 1e-9:
        zero += 1
result['volume'] = round(vol, 5)
result['open'] = sum(1 for e in bm.edges if e.is_boundary)
result['nonman'] = sum(1 for e in bm.edges if len(e.link_faces) > 2)
result['faces'] = len(bm.faces)
result['uv_zero'] = zero
bm.free()
"""


@pytest.fixture(scope="module")
def session(studio_session, tmp_path_factory):
    with studio_session("a2auth", tmp_path_factory.mktemp("a2auth")) as s:
        yield s


def _fresh(session) -> None:
    session.call("scene.clear")


def test_lathe_writes_cylindrical_uvs(session):
    _fresh(session)
    r = session.call("mesh.lathe", {"name": "bin", "segments": 16, "axis": "z", "profile": BIN_PROFILE})["result"]
    assert r["faces"] == 176 and "caps" not in r
    uv = session.py(_UV, {"o": "bin"})
    assert uv["u"] == [pytest.approx(0.0), pytest.approx(1.0)] and uv["v"] == [pytest.approx(0.0), pytest.approx(1.0)]
    assert uv["zero"] == 0                                     # flat tops and rims have their own v extent
    assert uv["max_span"] <= 1 / 16 + 1e-4                   # no face spans the u range: the seam is on an edge


def test_partial_lathe_fills_u_over_the_sweep(session):
    _fresh(session)
    session.call("mesh.lathe", {"name": "arc", "segments": 8, "axis": "y", "angle": 180,
                                "profile": [[0.3, 0], [0.3, 1], [0.2, 1.2]]})
    uv = session.py(_UV, {"o": "arc"})
    assert uv["u"] == [pytest.approx(0.0, abs=1e-5), pytest.approx(1.0, abs=1e-5)] and uv["zero"] == 0


def test_lathe_cap_gets_a_planar_disc(session):
    _fresh(session)
    r = session.call("mesh.lathe", {"name": "pot", "segments": 12, "cap": True,
                                    "profile": [[0.3, 0], [0.3, 0.5]]})["result"]
    assert r["caps"] == 2
    uv = session.py(_UV, {"o": "pot"})
    assert uv["zero"] == 0 and uv["u"][0] >= -1e-6 and uv["u"][1] <= 1 + 1e-6


def test_unwrap_cylinder_has_no_face_across_the_seam(session):
    _fresh(session)
    session.call("mesh.primitive", {"kind": "cylinder", "name": "cyl", "segments": 12, "radius": 0.3, "depth": 1.0,
                                    "cap": "none"})
    r = session.call("uv.unwrap", {"object": "cyl", "method": "cylinder"})["result"]
    assert r["method"] == "cylinder" and "seam_deg" in r
    uv = session.py(_UV, {"o": "cyl"})
    assert uv["max_span"] <= 1 / 12 + 1e-4 and uv["zero"] == 0
    assert uv["u"] == [pytest.approx(0.0, abs=1e-5), pytest.approx(1.0, abs=1e-5)]
    # caps are planar discs, the poles of a sphere take the u of their face
    session.call("mesh.primitive", {"kind": "cylinder", "name": "capped", "segments": 12, "radius": 0.3, "depth": 1.0})
    r = session.call("uv.unwrap", {"object": "capped", "method": "cylinder"})["result"]
    assert r["caps"] == 2
    session.call("mesh.primitive", {"kind": "uv_sphere", "name": "ball", "segments": 12, "rings": 6, "radius": 0.4})
    session.call("uv.unwrap", {"object": "ball", "method": "cylinder"})
    uv = session.py(_UV, {"o": "ball"})
    assert uv["max_span"] <= 1 / 12 + 1e-4 and uv["zero"] == 0


def test_unwrap_cylinder_around_another_axis(session):
    _fresh(session)
    session.call("mesh.primitive", {"kind": "cylinder", "name": "pipe", "segments": 8, "radius": 0.2, "depth": 2.0,
                                    "cap": "none"})
    session.call("mesh.transform", {"object": "pipe", "rotate": [90, 0, 0], "pivot": "origin"})   # now along y
    r = session.call("uv.unwrap", {"object": "pipe", "method": "cylinder", "axis": "y"})["result"]
    assert r["faces"] == 8
    uv = session.py(_UV, {"o": "pipe"})
    assert uv["max_span"] <= 1 / 8 + 1e-4 and uv["zero"] == 0
    assert uv["u"] == [pytest.approx(0.0, abs=1e-5), pytest.approx(1.0, abs=1e-5)]


def test_uv_fit_fills_the_rectangle_unless_keep_aspect(session):
    _fresh(session)
    session.call("mesh.primitive", {"kind": "plane", "name": "strip", "size": [2.0, 0.5]})
    session.call("uv.unwrap", {"object": "strip", "method": "planar", "axis": "z", "size": 1.0})
    r = session.call("uv.fit", {"object": "strip", "rect": [0, 0, 1, 0.5]})["result"]
    uv = session.py(_UV, {"o": "strip"})
    assert uv["u"] == [pytest.approx(0.0), pytest.approx(1.0)] and uv["v"] == [pytest.approx(0.0), pytest.approx(0.5)]
    assert r["scale"][0] != r["scale"][1]                       # the island was stretched to fill the rectangle
    session.call("uv.unwrap", {"object": "strip", "method": "planar", "axis": "z", "size": 1.0})
    session.call("uv.fit", {"object": "strip", "rect": [0, 0, 1, 0.5], "keep_aspect": True})
    uv = session.py(_UV, {"o": "strip"})
    assert uv["u"][1] - uv["u"][0] == pytest.approx(1.0) and uv["v"][1] - uv["v"][0] == pytest.approx(0.25)  # 4:1 kept


def test_inset_and_extrude_give_new_faces_real_uvs(session):
    _fresh(session)
    session.call("mesh.primitive", {"kind": "cylinder", "name": "lid", "segments": 16, "radius": 0.3, "depth": 0.1})
    r = session.call("mesh.inset", {"object": "lid", "select": {"side": "+z", "within": 5}, "thickness": 0.05,
                                    "save_group": "ring"})["result"]
    assert r["uv_filled"] == 16
    r = session.call("mesh.extrude", {"object": "lid", "select": {"group": "ring"}, "distance": -0.04})["result"]
    assert r["uv_filled"] == 16
    t = session.py(_TOPO, {"o": "lid"})
    assert t["uv_zero"] == 0 and t["open"] == 0 and t["nonman"] == 0
    assert t["volume"] == pytest.approx(math.pi * 0.3 ** 2 * 0.1 - math.pi * 0.25 ** 2 * 0.04, rel=0.03)


def test_inset_that_would_collapse_is_an_error(session):
    _fresh(session)
    session.call("mesh.primitive", {"kind": "cube", "name": "box", "size": [1, 1, 1]})
    with pytest.raises(SatkError) as e:
        session.call("mesh.inset", {"object": "box", "select": {"side": "+z", "within": 5}, "thickness": 0.9})
    assert e.value.code == "BAD_PARAMS" and "collapse" in e.value.msg and "below" in e.value.hint
    t = session.py(_TOPO, {"o": "box"})
    assert t["faces"] == 6                                     # nothing was changed
    with pytest.raises(SatkError):
        session.call("mesh.extrude", {"object": "box", "select": {"side": "+z", "within": 5}, "distance": 0})


def test_extrude_of_a_free_sheet_is_a_closed_slab(session):
    _fresh(session)
    session.call("mesh.primitive", {"kind": "plane", "name": "sign", "size": [1.0, 0.5]})
    r = session.call("mesh.extrude", {"object": "sign", "distance": 0.05})["result"]
    assert r["slab"] is True
    t = session.py(_TOPO, {"o": "sign"})
    assert t["open"] == 0 and t["uv_zero"] == 0 and t["volume"] == pytest.approx(0.025, rel=1e-3)
    session.call("mesh.primitive", {"kind": "plane", "name": "sign2", "size": [1.0, 0.5]})
    session.call("mesh.extrude", {"object": "sign2", "distance": -0.05})        # against the normal: still outward
    t = session.py(_TOPO, {"o": "sign2"})
    assert t["open"] == 0 and t["volume"] == pytest.approx(0.025, rel=1e-3)
    session.call("mesh.primitive", {"kind": "cube", "name": "solid", "size": [1, 1, 1]})
    r = session.call("mesh.extrude", {"object": "solid", "select": {"side": "+z", "within": 5}, "distance": 0.2})["result"]
    assert "slab" not in r                                      # on a solid the old face is inside and goes
    assert session.py(_TOPO, {"o": "solid"})["open"] == 0


def test_solidify_makes_a_closed_shell_with_uvs(session):
    _fresh(session)
    session.call("mesh.primitive", {"kind": "plane", "name": "panel", "size": [1.0, 1.0]})
    r = session.call("mesh.solidify", {"object": "panel", "thickness": 0.05})["result"]
    assert r["rim"] == 4
    t = session.py(_TOPO, {"o": "panel"})
    assert t["open"] == 0 and t["uv_zero"] == 0 and t["volume"] == pytest.approx(0.05, rel=1e-3)
    session.call("mesh.primitive", {"kind": "cube", "name": "box", "size": [1, 1, 1]})
    session.call("mesh.solidify", {"object": "box", "thickness": 0.1})
    assert session.py(_TOPO, {"o": "box"})["volume"] == pytest.approx(1 - 0.8 ** 3, rel=1e-3)   # a hollow shell
    session.call("mesh.primitive", {"kind": "cube", "name": "solid", "size": [1, 1, 1]})
    with pytest.raises(SatkError) as e:
        session.call("mesh.solidify", {"object": "solid", "thickness": 0.1, "select": {"side": "+z", "within": 5}})
    assert e.value.code == "BAD_PARAMS" and "inset" in e.value.hint


def test_bake_accepts_non_square_sizes_and_texel_takes_wh(session, tmp_path):
    from satk.media.png import png_size

    _fresh(session)
    session.call("mesh.lathe", {"name": "bin", "segments": 12, "profile": BIN_PROFILE})
    for size in ([64, 32], "64x32"):
        b = session.call("kit.bake", {"object": "bin", "size": size, "samples": 2})["result"]
        assert b["size"] == [64, 32] and all(png_size(f) == (64, 32) for f in b["files"].values())
    b = session.call("kit.bake", {"object": "bin", "size": 32, "samples": 2, "edge": False})["result"]
    assert b["size"] == [32, 32] and set(b["files"]) == {"ao"}
    with pytest.raises(SatkError):
        session.call("kit.bake", {"object": "bin", "size": [64, 8]})
    a = session.call("uv.texel", {"object": "bin", "px": [256, 128]})["result"]["px_per_m"]
    b = session.call("uv.texel", {"object": "bin", "px": 181})["result"]["px_per_m"]
    assert a["p50"] == pytest.approx(b["p50"], rel=0.01)
