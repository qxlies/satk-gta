"""satk.colgen.build on synthetic DFFs: every mode, winding, bounds, surfaces, lighting, vehicles, limits."""

from __future__ import annotations

import pytest

np = pytest.importorskip("numpy")

from satk.colgen.build import GenOptions, generate  # noqa: E402
from satk.colgen.check import check_model  # noqa: E402
from satk.colgen.geom import render_mesh  # noqa: E402
from satk.colgen.nearest import outside_distance  # noqa: E402
from satk.colgen.surface import SurfaceTable  # noqa: E402
from satk.core.errors import SatkError  # noqa: E402
from satk.formats.col import iter_col  # noqa: E402
from satk.rw import col as COL  # noqa: E402

ST = SurfaceTable.load()


def _gen(dff: bytes, name: str = "thing", **kw):
    return generate(dff, name, opts=GenOptions(**kw))


def _faces(m):
    V = np.asarray(m.vertices, float) / 128.0
    F = np.asarray([f[:3] for f in m.faces], dtype=np.int64)
    return V, F


def _roundtrip(m) -> COL.ColModel:
    rec = COL.encode_model(m)
    back = list(iter_col(rec))                                      # the independent reader agrees
    assert len(back) == 1 and back[0].faces == len(m.faces) and back[0].spheres == len(m.spheres)
    m2 = COL.decode_model(rec)
    assert COL.encode_model(m2) == rec                              # bit-exact through the codec
    return m2


@pytest.mark.parametrize("mode", ["box", "boxes", "hull", "mesh", "spheres", "bounds"])
def test_every_mode_writes_a_clean_model(dffs, mode):
    r = _gen(dffs.barrel(), "barrel", mode=mode)
    m = _roundtrip(r.model)
    assert r.mode == mode and m.name == "barrel" and m.version == 3
    rep = check_model(m)
    assert [f for f in rep.findings if f.sev != "info"] == [], [f.row() for f in rep.findings]
    # bounds cover the render mesh (vanilla COL bounds double as the culling box)
    rm = render_mesh(dffs.barrel(), "barrel")
    assert (rm.P.min(axis=0) >= np.asarray(m.bmin) - 1e-6).all()
    assert (rm.P.max(axis=0) <= np.asarray(m.bmax) + 1e-6).all()
    assert (np.linalg.norm(rm.P - np.asarray(m.center), axis=1) <= m.radius + 1e-6).all()


def test_hull_is_exact_for_a_low_poly_prop_and_uses_the_engine_winding(dffs):
    r = _gen(dffs.crate(), "crate", mode="hull")
    V, F = _faces(r.model)
    assert len(F) == 12 and r.fit["exact"] and r.fit["outside"] == 0.0
    # the engine's normal is (C - A) x (B - A): in RW's (B - A) x (C - A) terms the closed hull is inside out
    a, b, c = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    assert float((np.einsum("ij,ij->i", a, np.cross(b, c))).sum()) < 0
    rm = render_mesh(dffs.crate(), "crate")
    assert outside_distance(rm.P, V, F[:, [0, 2, 1]]) <= 1 / 128


def test_hull_budget_and_auto_mode(dffs):
    r = _gen(dffs.barrel(32), "barrel", mode="hull", max_faces=24)
    assert len(r.model.faces) <= 24 and not r.fit["exact"] and r.fit["outside"] <= 1 / 128
    assert _gen(dffs.crate(), "crate").mode == "hull"
    assert _gen(dffs.ground(), "ground", mode="auto").mode == "hull"           # small and simple


def test_mesh_mode_decimates_to_budget_and_keeps_surfaces(dffs):
    dff = dffs.ground(30)                                            # 1800 triangles, two textures
    r = _gen(dff, "ground", mode="mesh", max_faces=200)
    m = r.model
    assert len(m.faces) <= 200 and m.groups                          # > 80 faces: face groups like vanilla
    grass, road = ST.id_of("GRASS_SHORT_LUSH"), ST.id_of("TARMAC")
    assert set(r.surfaces) == {grass, road}
    V, F = _faces(m)
    cx = V[F].mean(axis=1)[:, 0]
    mats = np.asarray([f[3] for f in m.faces])
    assert (cx[mats == grass] < 1).all() and (cx[mats == road] > -1).all()  # materials stay on their side
    _roundtrip(m)


def test_box_and_boxes_contain_the_render_mesh(dffs):
    for mode in ("box", "boxes"):
        r = _gen(dffs.barrel(), "barrel", mode=mode, max_prims=4)
        assert 1 <= len(r.model.boxes) <= 4 and r.fit["outside"] == 0.0


def test_surface_auto_rules_and_fixed(dffs):
    r = _gen(dffs.crate("wood_crate01"), "crate", mode="box")
    assert r.model.boxes[0][2] == (ST.id_of("WOOD_CRATES"), 0, 187, 0) and r.how == {"keyword": 1}
    r = _gen(dffs.crate("zzunknown"), "crate", mode="box")
    assert r.model.boxes[0][2][0] == 0 and r.how == {"default": 1}
    r = _gen(dffs.crate("zzunknown"), "crate", mode="hull", rules=[("zz*", ST.id_of("GRAVEL"))])
    assert set(r.surfaces) == {ST.id_of("GRAVEL")} and set(r.how) == {"rule"}
    r = _gen(dffs.crate(), "crate", mode="hull", surface="sand_deep")
    assert set(r.surfaces) == {ST.id_of("SAND_DEEP")} and set(r.how) == {"fixed"}
    with pytest.raises(SatkError) as e:
        _gen(dffs.crate(), "crate", surface="tarmak")
    assert e.value.code == "BAD_PARAMS" and "TARMAC" in e.value.did_you_mean


def test_lighting_comes_from_prelit_colours(dffs):
    bright = dffs.build_model([dffs.Part("a", *dffs.box_mesh(), ["wall"], prelit=(250, 250, 250, 255))])
    dark = dffs.build_model([dffs.Part("a", *dffs.box_mesh(), ["wall"], prelit=(10, 10, 10, 255))])
    lb = {f[4] & 15 for f in _gen(bright, mode="hull").model.faces}
    ld = {f[4] & 15 for f in _gen(dark, mode="hull").model.faces}
    assert min(lb) > max(ld)
    assert {f[4] for f in _gen(bright, mode="hull", lighting=False).model.faces} == {255}
    unlit = dffs.build_model([dffs.Part("a", *dffs.box_mesh(), ["wall"], prelit=None)])
    assert {f[4] for f in _gen(unlit, mode="hull").model.faces} == {255}


def test_vehicle_auto_gives_car_spheres_pieces_and_a_closed_shadow(dffs):
    r = _gen(dffs.car(), "boxcar")
    m = _roundtrip(r.model)
    assert r.mode == "spheres" and m.spheres and m.shadow_faces
    assert {s[2][0] for s in m.spheres} == {63} and {s[2][2] for s in m.spheres} == {187}
    pieces = {s[2][1] for s in m.spheres}
    assert pieces & {3, 4, 5, 6}                                     # bumpers / doors under some spheres
    assert r.fit["coverage"] >= 0.5                                  # inscribed spheres leave the corners
    xs = sorted(round(s[0][0], 3) for s in m.spheres if abs(s[0][0]) > 0.1)  # cell / 2 = 0.1
    assert xs == sorted(-x for x in xs)                              # mirrored left/right pairs
    rep = check_model(m)
    assert not [f for f in rep.findings if f.check.startswith("shadow")]   # closed, engine winding
    # wheels and damage states never collide; the shadow reaches the wheels
    rm = render_mesh(dffs.car(), "boxcar")
    assert "wheel" not in rm.parts and "door_lf_dam" not in rm.parts
    sv = np.asarray(m.shadow_vertices, float) / 128.0
    assert sv[:, 2].min() < -0.35                                    # the wheels (z -0.4) hang below the body (-0.3)
    cell = 4.8 / 24                                                  # spheres reach the solid voxels' boundary
    assert m.bmin[2] >= -0.3 - cell - 1e-6 and min(s[0][2] - s[1] for s in m.spheres) >= -0.3 - cell - 1e-6


def test_version_2_has_no_shadow(dffs):
    r = _gen(dffs.car(), "boxcar", version=2)
    assert r.model.version == 2 and not r.model.shadow_faces
    assert any("COL2" in n for n in r.notes)
    _roundtrip(r.model)


def test_exclude_parts_and_textures(dffs):
    v1, t1 = dffs.box_mesh((-1, -1, 0), (1, 1, 1))
    v2, t2 = dffs.box_mesh((-0.2, -0.2, 1), (0.2, 0.2, 5))
    dff = dffs.build_model([dffs.Part("base", v1, t1, ["stone"]), dffs.Part("tree_leaves", v2, t2, ["leaf01"])])
    full = _gen(dff, "tree", mode="box")
    assert full.model.boxes[0][1][2] == pytest.approx(5.0)
    cut = _gen(dff, "tree", mode="box", exclude=["*leaves*"])
    assert cut.model.boxes[0][1][2] == pytest.approx(1.0)
    assert any("excluded" in n for n in cut.notes)
    assert _gen(dff, "tree", mode="box", exclude=["leaf*"]).model.boxes[0][1][2] == pytest.approx(1.0)


def test_frames_are_applied(dffs):
    v, t = dffs.box_mesh((-0.5, -0.5, -0.5), (0.5, 0.5, 0.5))
    dff = dffs.build_model([dffs.Part("moved", v, t, ["wall"], pos=(10.0, 0.0, 3.0))])
    m = _gen(dff, mode="box").model
    assert m.boxes[0][0] == pytest.approx((9.5, -0.5, 2.5)) and m.boxes[0][1] == pytest.approx((10.5, 0.5, 3.5))


def test_far_vertices_are_refused_for_meshes_but_not_for_boxes(dffs):
    v, t = dffs.box_mesh((300.0, 0, 0), (301.0, 1, 1))
    dff = dffs.build_model([dffs.Part("far", v, t, ["wall"])])
    with pytest.raises(SatkError) as e:
        _gen(dff, mode="hull")
    assert e.value.code == "BAD_PARAMS" and "256" in str(e.value)
    assert _gen(dff, mode="box").model.boxes                          # primitives are floats


def test_long_names_are_cut_and_generation_is_deterministic(dffs):
    a = generate(dffs.barrel(), "a_really_long_model_name_x", opts=GenOptions(mode="spheres"))
    b = generate(dffs.barrel(), "a_really_long_model_name_x", opts=GenOptions(mode="spheres"))
    assert a.model.name == "a_really_long_model_n" and any("21" in n for n in a.notes)
    assert COL.encode_model(a.model) == COL.encode_model(b.model)


def test_bad_mode_and_version(dffs):
    with pytest.raises(SatkError):
        _gen(dffs.crate(), mode="convex")
    with pytest.raises(SatkError):
        _gen(dffs.crate(), version=1)
