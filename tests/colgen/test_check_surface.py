"""satk.colgen.check on hand-made collision models, and the surface table / data files."""

from __future__ import annotations

import pytest

from satk.colgen.check import check_blob, check_model
from satk.colgen.surface import MAX_SURFACE, SurfaceTable
from satk.core import resources
from satk.core.errors import SatkError
from satk.rw import col as COL


def _model(**kw) -> COL.ColModel:
    m = COL.ColModel(version=3, name=kw.pop("name", "thing"))
    for k, v in kw.items():
        setattr(m, k, v)
    COL.compute_bounds(m)
    m.flags = COL.canonical_flags(m)
    return m


def _checks(m: COL.ColModel) -> dict[str, str]:
    return {f.check: f.sev for f in check_model(m).findings}


def _quad_faces(mat=0):
    # a 1 m square, engine winding does not matter for open meshes
    verts = [(0, 0, 0), (128, 0, 0), (128, 128, 0), (0, 128, 0)]
    return verts, [(0, 2, 1, mat, 255), (0, 3, 2, mat, 255)]


def test_clean_model_has_no_findings():
    v, f = _quad_faces()
    m = _model(vertices=v, faces=f, boxes=[((0, 0, 0), (1, 1, 1), (0, 0, 187, 0))])
    assert _checks(m) == {}


def test_bad_surface_inverted_box_and_bad_sphere():
    m = _model(boxes=[((1, 0, 0), (0, 1, 1), (200, 0, 0, 0))], spheres=[((0, 0, 0), 0.0, (0, 0, 0, 0))])
    got = _checks(m)
    assert got["surface"] == "error" and got["box_inverted"] == "error" and got["sphere_radius"] == "error"


def test_bounds_and_bsphere():
    v, f = _quad_faces()
    m = _model(vertices=v, faces=f)
    m.bmax = (0.9, 0.9, 0.0)
    assert _checks(m)["bounds"] == "warn"
    m.bmax = (0.3, 0.3, 0.0)
    assert _checks(m)["bounds"] == "error"
    m = _model(vertices=v, faces=f)
    m.radius = 0.1
    assert _checks(m)["bsphere"] == "error"


def test_degenerate_duplicate_unused_and_index():
    v, f = _quad_faces()
    v = v + [(500, 500, 500)]
    f = f + [(0, 0, 1, 0, 255), (0, 2, 1, 0, 255)]
    got = _checks(_model(vertices=v, faces=f))
    assert got["degenerate"] == "warn" and got["duplicate"] == "info" and got["unused_vertices"] == "info"
    m = _model(vertices=v[:2], faces=[(0, 1, 5, 0, 255)])
    assert _checks(m)["index"] == "error"


def test_face_groups_flags_and_empty():
    verts = [(i * 64, j * 64, 0) for j in range(11) for i in range(11)]
    faces = []
    for j in range(10):
        for i in range(10):
            a = j * 11 + i
            faces += [(a, a + 12, a + 1, 0, 255), (a, a + 11, a + 12, 0, 255)]
    m = _model(vertices=verts, faces=faces)
    assert _checks(m) == {"no_face_groups": "warn"}
    COL.face_groups(m)
    m.flags = COL.canonical_flags(m)
    assert _checks(m) == {}
    lo, hi, s, e = m.groups[0]
    m.groups[0] = ((lo[0] + 1, lo[1], lo[2]), hi, s, e)
    assert _checks(m)["face_groups"] == "error"
    m.flags = 0
    assert "flags" in _checks(m)
    assert _checks(_model()) == {"empty": "info"}


def test_shadow_open_and_winding():
    # a tetrahedron, outward counter-clockwise in RW terms
    sv = [(0, 0, 0), (128, 0, 0), (0, 128, 0), (0, 0, 128)]
    ccw = [(0, 2, 1), (0, 1, 3), (0, 3, 2), (1, 2, 3)]
    box = [((0, 0, 0), (1, 1, 1), (0, 0, 187, 0))]
    engine = _model(boxes=box, shadow_vertices=sv, shadow_faces=[(a, c, b, 0, 255) for a, b, c in ccw])
    assert _checks(engine) == {}
    inside_out = _model(boxes=box, shadow_vertices=sv, shadow_faces=[(a, b, c, 0, 255) for a, b, c in ccw])
    assert _checks(inside_out) == {"shadow_winding": "warn"}
    open_ = _model(boxes=box, shadow_vertices=sv, shadow_faces=[(a, c, b, 0, 255) for a, b, c in ccw[:3]])
    assert _checks(open_) == {"shadow_open": "warn"}


def test_check_blob_duplicates_trailing_and_garbage():
    v, f = _quad_faces()
    rec = COL.encode_model(_model(vertices=v, faces=f))
    reps, problems = check_blob(rec + rec + b"\0" * 16)
    assert len(reps) == 2 and problems == ["DUPLICATE_NAME: thing appears 2 times (the game takes the first)"]
    reps, problems = check_blob(rec + b"junk")
    assert any(p.startswith("TRAILING_DATA") for p in problems)
    reps, problems = check_blob(b"not a collision")
    assert reps == [] and problems


# ----------------------------------------------------------------------------- surfaces
def test_data_files_are_consistent():
    names = resources.read_json("colgen", "surfaces.json")["names"]
    assert len(names) == MAX_SURFACE + 1 and names[0] == "DEFAULT" and names[63] == "CAR"
    st = SurfaceTable.load()
    assert st.keywords and all(0 <= s <= MAX_SURFACE for _k, s in st.keywords)
    assert len(st.textures) > 1000
    assert all(0 <= row[0] <= MAX_SURFACE and 0 < row[1] <= 100 and row[2] >= 1 for row in st.textures.values())
    for txd, rows in st.txd.items():
        assert txd == txd.lower() and all(0 <= r[0] <= MAX_SURFACE for r in rows.values())
    doc = resources.read_json("colgen", "tex_surface.json")
    assert doc["format"] == "satk-colgen-tex-surface" and doc["derived"]["holdout_acc"] >= 80
    assert set(st.lighting) >= {"day", "night"}


def test_lookup_order_txd_table_keyword_default():
    st = SurfaceTable.load().with_textures({"grass_x": [10, 90, 50]}, {"desert_txd": {"grass_x": [13, 70, 20]}})
    assert st.lookup("GRASS_X") == (10, "table")
    assert st.lookup("grass_x", "Desert_TXD") == (13, "txd")
    assert st.lookup("grass_x", "other") == (10, "table")
    sid, how = st.lookup("my_new_grass2")
    assert how == "keyword:grass" and st.name(sid) == "GRASS_MEDIUM_LUSH"
    assert st.lookup("sandstone_wall") == (0, "keyword:sandstone")      # stops "sand" from matching
    assert st.lookup("zzz") == (0, "default") and st.lookup(None) == (0, "default")


def test_surface_names_and_ids():
    st = SurfaceTable.load()
    assert st.id_of("tarmac") == 1 and st.id_of("rail-track") == 178 and st.id_of("63") == 63
    assert st.name(45) == "GLASS" and st.name(999) == "#999"
    with pytest.raises(SatkError) as e:
        st.id_of("glas")
    assert e.value.code == "BAD_PARAMS" and "GLASS" in e.value.did_you_mean
    with pytest.raises(SatkError):
        st.id_of("179")


def test_lighting_byte():
    st = SurfaceTable.load()
    assert st.light(-1, -1) == 255
    lo, hi = st.light(10, 10), st.light(250, 250)
    assert (lo & 15) < (hi & 15) and (lo >> 4) <= (hi >> 4)
    assert 0 <= st.light(255, -1) <= 255
