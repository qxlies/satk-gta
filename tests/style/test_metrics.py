"""Contract K1: satk.style.metrics.mesh_metrics on synthetic meshes (no game data)."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from array import array
from pathlib import Path

import numpy as np
import pytest

from satk.style import metrics as SM
from satk.style.metrics import KEYS, concat, is_hd_part, mesh_metrics

from .conftest import cube, face_normals, split_corners, uv_sphere

METRICS_PY = Path(SM.__file__)


def _rot_z(deg: float) -> np.ndarray:
    c, s = np.cos(np.radians(deg)), np.sin(np.radians(deg))
    return np.array([[c, -s, 0, 0], [s, c, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1.0]])


# ----------------------------------------------------------------------------- shading
def test_smooth_sphere_is_soft_and_closed():
    P, T = uv_sphere()
    m = mesh_metrics(P, T, normals=P)                      # unit sphere: normal = position
    assert m["geo.tris"] == len(T) == 16 * 2 + 16 * 2 * 6
    assert m["shade.normal_bend"] > 5 and m["shade.flat_share"] == 0
    assert m["shade.hard_edge_share"] == 0 and "shade.hard_at_seam" not in m   # no hard edge -> left out
    assert m["geo.pieces"] == 1 and m["geo.largest_piece_share"] == 1
    assert m["geo.open_edges"] == 0 and m["geo.nonmanifold_edges"] == 0
    assert m["dff.verts"] == len(P) and m["dff.verts_per_tri"] == round(len(P) / len(T), 4)
    assert m["bbox"] == [-1.0, -1.0, -1.0, 1.0, 1.0, 1.0] or np.allclose(m["bbox"], [-1, -1, -1, 1, 1, 1], atol=1e-3)
    assert 0 < m["geo.median_dihedral"] < 30
    assert abs(m["geo.area_m2"] - 4 * np.pi) < 0.6          # a 16x8 sphere is a bit smaller than the sphere


def test_flat_sphere_is_faceted_with_hard_edges():
    P, T = uv_sphere()
    m = mesh_metrics(P, T, corner_normals=face_normals(P, T))
    assert m["shade.normal_bend"] < 1e-3 and m["shade.flat_share"] == 1.0
    # every fold is hard; only the diagonals inside the 96 planar quads stay smooth (Euler: E = V + F - 2)
    edges = len(P) + len(T) - 2
    assert m["shade.hard_edge_share"] == round((edges - 16 * 6) / edges, 4)
    assert set(m["shade.hard_by_dihedral"].values()) - {1.0} == {m["shade.hard_by_dihedral"]["0-10"]}
    # one vertex per (vertex, normal): a flat-shaded sphere stores ~one vertex per corner
    assert m["dff.verts_per_tri"] > 2


def test_normal_bend_is_area_weighted_mean_of_corner_angles():
    # one triangle in the XY plane, corner normals tilted by 10, 20 and 30 deg
    P = np.array([(0, 0, 0), (1, 0, 0), (0, 1, 0)], float)
    T = np.array([(0, 1, 2)])
    tilt = [np.radians(a) for a in (10, 20, 30)]
    CN = np.array([[(np.sin(t), 0, np.cos(t)) for t in tilt]])
    m = mesh_metrics(P, T, corner_normals=CN)
    assert m["shade.normal_bend"] == pytest.approx(20.0, abs=1e-3)
    assert m["shade.flat_share"] == 0
    # a second, flat triangle with 3x the area pulls the area-weighted mean to 5 deg
    P2 = np.vstack([P, P * np.sqrt(3) + (5, 0, 0)])
    T2 = np.vstack([T, T + 3])
    CN2 = np.concatenate([CN, np.tile((0, 0, 1.0), (1, 3, 1))])
    m2 = mesh_metrics(P2, T2, corner_normals=CN2)
    assert m2["shade.normal_bend"] == pytest.approx(5.0, abs=1e-3)
    assert m2["shade.flat_share"] == 0.5


def test_flat_threshold_is_one_degree():
    P = np.array([(0, 0, 0), (1, 0, 0), (0, 1, 0)], float)
    T = np.array([(0, 1, 2)])
    for deg, flat in ((0.9, 1.0), (1.1, 0.0)):
        t = np.radians(deg)
        CN = np.array([[(np.sin(t), 0, np.cos(t))] * 3])
        assert mesh_metrics(P, T, corner_normals=CN)["shade.flat_share"] == flat


# ----------------------------------------------------------------------------- hard edges and seams
def test_cube_hard_edges_seams_and_dihedrals():
    P, T = cube()
    flat = face_normals(P, T)
    m = mesh_metrics(P, T, corner_normals=flat)
    # 18 welded edges: 12 cube edges at 90 deg (hard), 6 face diagonals at 0 deg (smooth)
    assert m["geo.open_edges"] == 0 and m["geo.pieces"] == 1
    assert m["shade.hard_edge_share"] == round(12 / 18, 4)
    assert m["geo.median_dihedral"] == 90.0
    hb = m["shade.hard_by_dihedral"]
    assert hb["0-10"] == 0.0 and sum(v for k, v in hb.items() if k != "0-10") == 1.0
    # one material and shared per-vertex UVs: no hard edge is on a seam
    uv = P[:, :2] * 0.5 + 0.5
    assert mesh_metrics(P, T, corner_normals=flat, uv=uv, mat=np.zeros(12))["shade.hard_at_seam"] == 0.0
    # one material per face: every cube edge is a material border
    mat = np.repeat(np.arange(6), 2)
    assert mesh_metrics(P, T, corner_normals=flat, mat=mat)["shade.hard_at_seam"] == 1.0
    # per-face UVs (every face its own island): every hard edge is a UV seam
    quad_uv = np.array([[(0, 0), (1, 0), (1, 1)], [(0, 0), (1, 1), (0, 1)]] * 6, float)
    m3 = mesh_metrics(P, T, corner_normals=flat, uv=quad_uv, mat=np.zeros(12))
    assert m3["shade.hard_at_seam"] == 1.0 and m3["uv.zero_area_share"] == 0 and m3["uv.span"] == [1.0, 1.0]


def test_split_vertices_weld_back():
    """A DFF stores split vertices at hard edges: topology must not change."""
    P, T = cube()
    Ps, Ts = split_corners(P, T)
    flat = face_normals(P, T)
    a = mesh_metrics(P, T, corner_normals=flat)
    b = mesh_metrics(Ps, Ts, normals=flat.reshape(-1, 3))
    for k in ("geo.pieces", "geo.open_edges", "geo.median_dihedral", "shade.hard_edge_share", "shade.normal_bend",
              "shade.flat_share", "geo.area_m2", "bbox"):
        assert a[k] == b[k], k
    assert a["dff.verts"] == 24                               # 8 corners x 3 face normals
    assert b["dff.verts"] == 36                               # a DFF stores what it is given


# ----------------------------------------------------------------------------- construction and UVs
def test_pieces_slivers_and_zero_uv():
    P, T = cube()
    P2 = np.vstack([P, P + (5, 0, 0)])
    T2 = np.vstack([T, T + 8])
    m = mesh_metrics(P2, T2)
    assert m["geo.pieces"] == 2 and m["geo.largest_piece_share"] == 0.5
    assert "shade.normal_bend" not in m and "uv.zero_area_share" not in m
    # a 1-1-178 deg triangle is a sliver, a right triangle is not
    s = np.array([(0, 0, 0), (1, 0, 0), (0.5, np.tan(np.radians(1)) * 0.5, 0), (0, 2, 0), (1, 2, 0), (0, 3, 0)])
    ms = mesh_metrics(s, [(0, 1, 2), (3, 4, 5)])
    assert ms["geo.sliver_share"] == 0.5 and ms["geo.pieces"] == 2
    # one texel smeared over every face
    mz = mesh_metrics(P, T, uv=np.full((8, 2), 0.25))
    assert mz["uv.zero_area_share"] == 1.0 and mz["uv.span"] == [0.0, 0.0]


def test_open_and_nonmanifold_edges():
    P = np.array([(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1), (0, -1, 0)], float)
    m = mesh_metrics(P, [(0, 1, 2)])
    assert m["geo.open_edges"] == 3 and "geo.median_dihedral" not in m
    fan = mesh_metrics(P, [(0, 1, 2), (0, 1, 3), (0, 1, 4)])     # three faces on edge 0-1
    assert fan["geo.nonmanifold_edges"] == 1


def test_degenerate_triangles_are_skipped_by_normal_metrics():
    P = np.array([(0, 0, 0), (1, 0, 0), (0, 1, 0), (2, 0, 0)], float)
    T = np.array([(0, 1, 2), (0, 1, 3)])                       # the second one is a line
    N = np.tile((0, 0, 1.0), (4, 1))
    m = mesh_metrics(P, T, normals=N)
    assert m["shade.normal_bend"] == 0 and m["shade.flat_share"] == 1.0   # only the real triangle counts
    assert m["geo.sliver_share"] == 0.5


# ----------------------------------------------------------------------------- input forms
def test_input_forms_agree():
    P, T = uv_sphere(12, 6)
    uv = P[:, :2] * 0.5 + 0.5
    mat = np.arange(len(T)) % 3
    ref = mesh_metrics(P, T, normals=P, uv=uv, mat=mat)
    cn = P[T]                                                 # (T, 3, 3)
    assert mesh_metrics(P, T, corner_normals=cn, uv=uv, mat=mat) == ref
    assert mesh_metrics(P, T, corner_normals=cn.reshape(-1, 3), uv=uv[T], mat=mat) == ref
    flat = mesh_metrics(array("f", P.astype(np.float32).ravel()), array("I", T.astype(np.uint32).ravel()),
                        normals=list(P.ravel()), uv=uv.ravel().tolist(), mat=list(mat))
    assert flat.keys() == ref.keys()
    assert flat["shade.normal_bend"] == pytest.approx(ref["shade.normal_bend"], abs=1e-3)
    assert mesh_metrics(P, T, normals=P, uv=uv, mat=mat) == ref     # deterministic
    assert list(ref) == [k for k in KEYS if k in ref]                 # canonical key order
    json.dumps(ref)                                                   # plain JSON types


def test_empty_and_bad_input():
    assert mesh_metrics(np.zeros((0, 3)), np.zeros((0, 3), int)) == {"geo.tris": 0}
    assert mesh_metrics([(0, 0, 0), (1, 2, 3)], [])["bbox"] == [0.0, 0.0, 0.0, 1.0, 2.0, 3.0]
    P, T = cube()
    with pytest.raises(ValueError, match="out of range"):
        mesh_metrics(P, T + 1)
    with pytest.raises(ValueError, match="not both"):
        mesh_metrics(P, T, corner_normals=face_normals(P, T), normals=P)
    with pytest.raises(ValueError, match="mat"):
        mesh_metrics(P, T, mat=[0, 1])
    with pytest.raises(ValueError, match="uv"):
        mesh_metrics(P, T, uv=np.zeros((5, 2)))
    with pytest.raises(ValueError, match="multiple of 3"):
        mesh_metrics([0.0, 1.0], T)


# ----------------------------------------------------------------------------- concat (models of parts)
def test_concat_moves_parts_and_keeps_shading():
    P, T = uv_sphere()
    one = mesh_metrics(P, T, normals=P)
    m4 = np.eye(4)
    m4[:3, 3] = (10, 0, 0)
    rot = _rot_z(90) @ m4
    parts = [{"pos": P, "tris": T, "normals": P}, {"pos": P, "tris": T, "normals": P, "matrix": rot}]
    m = mesh_metrics(**concat(parts))
    assert m["geo.tris"] == 2 * one["geo.tris"] and m["geo.pieces"] == 2
    assert m["shade.normal_bend"] == pytest.approx(one["shade.normal_bend"], abs=1e-3)
    assert m["bbox"][4] == pytest.approx(11.0, abs=1e-3)       # rotated to +Y, 10 m away
    # a mirroring matrix flips the winding back: shading is unchanged
    mirror = np.diag([-1.0, 1, 1, 1])
    mm = mesh_metrics(**concat([{"pos": P, "tris": T, "normals": P, "matrix": mirror}]))
    assert mm["shade.normal_bend"] == pytest.approx(one["shade.normal_bend"], abs=1e-3)
    assert mm["shade.flat_share"] == 0


def test_concat_never_welds_two_parts_and_fills_missing_normals():
    P, T = cube()
    flat = face_normals(P, T)
    touching = [{"pos": P, "tris": T, "corner_normals": flat, "mat": np.zeros(12)},
                {"pos": P + (2, 0, 0), "tris": T}]          # shares a face plane, no normals, no material
    c = concat(touching)
    assert c["corner_normals"].shape == (24, 3, 3) and set(c["mat"].tolist()) == {0, 1}
    m = mesh_metrics(**c)
    assert m["geo.pieces"] == 2 and m["geo.nonmanifold_edges"] == 0
    assert m["shade.flat_share"] == 1.0                         # the part without normals counts as flat
    assert mesh_metrics(**concat([])) == {"geo.tris": 0}


def test_is_hd_part():
    assert is_hd_part("chassis") and is_hd_part("wheel") and is_hd_part("door_lf_ok")
    assert not is_hd_part("door_lf_dam") and not is_hd_part("Chassis_VLO")


# ----------------------------------------------------------------------------- standalone (Blender's Python)
def test_metrics_module_is_standalone():
    tree = ast.parse(METRICS_PY.read_text(encoding="utf-8"))
    top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    names = {a.name for n in top if isinstance(n, ast.Import) for a in n.names}
    names |= {n.module or "" for n in top if isinstance(n, ast.ImportFrom)}
    assert names <= {"__future__"}, names                      # numpy only inside functions, no satk at all
    assert not any(isinstance(n, ast.ImportFrom) and n.level for n in ast.walk(tree))   # no relative import


def test_metrics_loads_by_file_path_without_satk():
    code = (
        "import importlib.util, json, sys\n"
        "sys.dont_write_bytecode = True\n"
        f"spec = importlib.util.spec_from_file_location('k1', {str(METRICS_PY)!r})\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "r = m.mesh_metrics([(0,0,0),(1,0,0),(0,1,0)], [(0,1,2)], normals=[(0,0,1)]*3)\n"
        "print(json.dumps({'r': r, 'satk': any(k == 'satk' or k.startswith('satk.') for k in sys.modules)}))\n"
    )
    p = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, encoding="utf-8",
                       timeout=60)
    assert p.returncode == 0, p.stderr[-2000:]
    out = json.loads(p.stdout.strip().splitlines()[-1])
    assert out["satk"] is False
    assert out["r"]["shade.normal_bend"] == 0 and out["r"]["geo.open_edges"] == 3
