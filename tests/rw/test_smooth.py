"""Seam-aware smooth normals, frame-local bounding spheres (``rw patch --smooth-normals/--recalc-bsphere``)
and the COL face lighting fallback of ``col write`` (wave A1, lane rules). Synthetic meshes only."""

from __future__ import annotations

import math
import struct
from array import array

import pytest

from satk.formats.dff import decode_geometries, scan_dff
from satk.rw import codecs as C
from satk.rw import col as COL
from satk.rw.chunk import STRUCT
from satk.rw.dff import DffDoc, PatchError, _bend, smooth_geometry, tight_sphere


def _cylinder(sides: int = 12, flat: bool = True):
    """An open cylinder of ``sides`` quads; ``flat``: every triangle has its own three vertices."""
    pos: list[float] = []
    tris: list[tuple[int, int, int, int]] = []
    ring = [(math.cos(2 * math.pi * i / sides), math.sin(2 * math.pi * i / sides)) for i in range(sides)]
    for i in range(sides):
        (x0, y0), (x1, y1) = ring[i], ring[(i + 1) % sides]
        quad = [(x0, y0, 0.0), (x1, y1, 0.0), (x0, y0, 1.0), (x1, y1, 1.0)]
        if flat:
            for a, b, c in ((0, 1, 2), (2, 1, 3)):
                base = len(pos) // 3
                for k in (a, b, c):
                    pos.extend(quad[k])
                tris.append((base, base + 1, base + 2, 0))
        else:
            base = len(pos) // 3
            for p in quad:
                pos.extend(p)
            tris += [(base, base + 1, base + 2, 0), (base + 2, base + 1, base + 3, 0)]
    return array("f", pos), tris


def _face_normals_of(pos, tris):
    out = array("f")
    nv = len(pos) // 3
    acc = [[0.0, 0.0, 0.0] for _ in range(nv)]
    for a, b, c, _m in tris:
        ux, uy, uz = (pos[3 * b + i] - pos[3 * a + i] for i in range(3))
        vx, vy, vz = (pos[3 * c + i] - pos[3 * a + i] for i in range(3))
        n = (uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx)
        ln = math.sqrt(sum(x * x for x in n))
        for v in (a, b, c):
            acc[v] = [x / ln for x in n]
    for n in acc:
        out.extend(n)
    return out


def test_flat_cylinder_becomes_smooth_and_radial():
    pos, tris = _cylinder(12)
    flat = _face_normals_of(pos, tris)
    assert _bend(pos, tris, flat) < 1e-3
    n = smooth_geometry(pos, tris, angle=45.0)
    assert len(n) == len(pos)                               # vertex records are never merged
    assert _bend(pos, tris, n) > 5.0                        # 30 deg between sides -> about 7.5 deg bend
    for v in range(len(pos) // 3):                          # radial: no z, along the position
        x, y, z = pos[3 * v], pos[3 * v + 1], pos[3 * v + 2]
        assert abs(n[3 * v + 2]) < 1e-6 and n[3 * v] * x + n[3 * v + 1] * y > 0.999


def test_hard_edges_material_and_uv_seams_stay_split():
    pos, tris = _cylinder(4)                                # a box: 90 deg between the sides
    flat = _face_normals_of(pos, tris)
    n = smooth_geometry(pos, tris, angle=45.0)
    assert _bend(pos, tris, n) == pytest.approx(_bend(pos, tris, flat), abs=1e-4)
    assert _bend(pos, tris, smooth_geometry(pos, tris, angle=120.0)) > 20.0   # above 90 deg it rounds the box
    pos, tris = _cylinder(12)
    mixed = [(a, b, c, (i // 4) % 2) for i, (a, b, c, _m) in enumerate(tris)]     # materials alternate per 2 sides
    n_mat = smooth_geometry(pos, mixed, angle=45.0)
    n_all = smooth_geometry(pos, mixed, angle=45.0, split_material=False)
    assert _bend(pos, mixed, n_mat) < _bend(pos, mixed, n_all)
    uv = array("f", [float(i) for i in range(2 * (len(pos) // 3))])             # every corner its own UV
    n_uv = smooth_geometry(pos, tris, angle=45.0, uv=uv)
    assert _bend(pos, tris, n_uv) < 1e-3


def _dff(pos, tris, *, normals=None, sphere=(0.0, 0.0, 0.0, 1.0)) -> bytes:
    from satk.rw.chunk import (ATOMIC, CLUMP, EXTENSION, FRAMELIST, GEOMETRY, GEOMLIST, MATERIAL, MATLIST, Chunk,
                               RwStream, pack_libid)

    lib = pack_libid(0x36003)
    nv = len(pos) // 3
    flags = C.GEO_POSITIONS | C.GEO_LIGHT | (C.GEO_NORMALS if normals is not None else 0)
    tri_words = b"".join(struct.pack("<4H", b, a, m, c) for a, b, c, m in tris)
    g = C.GeometryData(flags, 0, 0, len(tris), nv, None, None, [], tri_words,
                       [C.MorphData(struct.pack("<4f", *sphere), 1, 1 if normals is not None else 0,
                                    array("f", pos).tobytes(), array("f", normals).tobytes() if normals else b"")])
    mats = max(m for *_x, m in tris) + 1
    mat_struct = C.encode_material(C.MaterialData(0, b"\xff\xff\xff\xff", 0, 0, struct.pack("<3f", 1, 1, 1)), 0x36003)
    matlist = Chunk(MATLIST, lib, kids=[Chunk(STRUCT, lib, data=struct.pack(f"<i{mats}i", mats, *([-1] * mats)))] +
                    [Chunk(MATERIAL, lib, kids=[Chunk(STRUCT, lib, data=mat_struct), Chunk(EXTENSION, lib, kids=[])])
                     for _ in range(mats)])
    geo = Chunk(GEOMETRY, lib, kids=[Chunk(STRUCT, lib, data=C.encode_geometry(g, 0x36003)), matlist,
                                     Chunk(EXTENSION, lib, kids=[])])
    frames = C.encode_framelist(C.FrameListData([struct.pack("<9f3fiI", 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, -1, 0)]))
    clump = Chunk(CLUMP, lib, kids=[
        Chunk(STRUCT, lib, data=C.encode_clump(C.ClumpData(1, 0, 0), 0x36003)),
        Chunk(FRAMELIST, lib, kids=[Chunk(STRUCT, lib, data=frames), Chunk(EXTENSION, lib, kids=[])]),
        Chunk(GEOMLIST, lib, kids=[Chunk(STRUCT, lib, data=struct.pack("<i", 1)), geo]),
        Chunk(ATOMIC, lib, kids=[Chunk(STRUCT, lib, data=struct.pack("<4I", 0, 0, 5, 0)), Chunk(EXTENSION, lib, kids=[])]),
        Chunk(EXTENSION, lib, kids=[])])
    return RwStream([clump]).to_bytes()


def test_dffdoc_smooth_normals_keeps_topology_and_guards():
    pos, tris = _cylinder(12)
    blob = _dff(list(pos), tris)                             # no normals at all
    doc = DffDoc.parse(blob)
    assert dict(doc.smooth_normals()) == {"smooth_normals": 1}
    out = doc.to_bytes()
    (m,) = decode_geometries(out)
    assert m.normals is not None and len(m.positions) == len(pos) and list(m.tris)
    assert scan_dff(out).geoms[0].verts == len(pos) // 3
    smooth = list(m.normals)
    doc2 = DffDoc.parse(out)                                 # already smooth: the guard keeps it (no change)
    assert doc2.smooth_normals(angle=10.0) == [("kept_normals", 1)] and doc2.to_bytes() == out
    assert dict(DffDoc.parse(out).smooth_normals(angle=10.0, keep_worse=True))["smooth_normals"] == 1
    with pytest.raises(PatchError):
        DffDoc.parse(blob).smooth_normals(angle=0)
    assert smooth


def test_recalc_bsphere_fixes_only_wrong_spheres():
    pos, tris = _cylinder(6, flat=False)
    good = _dff(list(pos), tris, sphere=(*tight_sphere(pos)[:3], tight_sphere(pos)[3]))
    assert DffDoc.parse(good).recalc_bsphere() == []
    world = _dff(list(pos), tris, sphere=(5.0, 3.0, 0.5, 1.3))               # world-space centre
    doc = DffDoc.parse(world)
    assert doc.recalc_bsphere() == [("bsphere", 1)]
    x, y, z, r = scan_dff(doc.to_bytes()).geoms[0].bsphere
    assert abs(x) < 1e-5 and abs(y) < 1e-5 and abs(z - 0.5) < 1e-5 and r == pytest.approx(math.sqrt(1.25), abs=1e-3)
    huge = _dff(list(pos), tris, sphere=(0.0, 0.0, 0.5, 40.0))               # encloses, but 30x too big
    assert DffDoc.parse(huge).recalc_bsphere() == [("bsphere", 1)]


def test_rw_patch_cli_smooth_and_bsphere(satk_home, run_cli, tmp_path):
    pos, tris = _cylinder(12)
    src = tmp_path / "pipe.dff"
    src.write_bytes(_dff(list(pos), tris, normals=list(_face_normals_of(pos, tris)), sphere=(9.0, 9.0, 9.0, 1.0)))
    env = run_cli(["rw", "patch", str(src), "--smooth-normals", "--recalc-bsphere", "--json"]).json
    assert env["ok"] and env["rows"][0][2].startswith("smooth_normals:1, bsphere:1, bend:0.00->")
    assert float(env["rows"][0][2].rsplit("->", 1)[1]) > 5          # the faceted pipe is now round
    for bad in (["--smooth-normals", "--recalc-normals"], ["--smooth-normals", "--split-at", "edges"],
                ["--smooth-normals", "--angle", "0"]):
        r = run_cli(["rw", "patch", str(src), *bad, "--json"])
        assert r.json["error"]["code"] == "BAD_PARAMS", bad
    env = run_cli(["rw", "patch", str(src), "--smooth-normals", "--split-at", "none", "--angle", "60", "--dry-run",
                   "--json"]).json
    assert env["rows"][0][1] == "would-write"


def test_recalc_normals_warns_when_it_flattens_a_split_mesh(satk_home, run_cli, tmp_path):
    pos, tris = _cylinder(12)                               # split per face, but with round (radial) normals
    radial = smooth_geometry(pos, tris, angle=45.0)
    src = tmp_path / "round.dff"
    src.write_bytes(_dff(list(pos), tris, normals=list(radial)))
    env = run_cli(["rw", "patch", str(src), "--recalc-normals", "--dry-run", "--json"]).json
    assert env["ok"] and any(w.startswith("FLATTER:") for w in env.get("warn", []))
    env = run_cli(["rw", "patch", str(src), "--smooth-normals", "--dry-run", "--json"]).json
    assert not env.get("warn")                              # already smooth: smoothing keeps it so


# --------------------------------------------------------------------------- COL face lighting


def _tri_model(**extra) -> dict:
    return {"name": "slab", "vertices": [[0, 0, 0], [2, 0, 0], [0, 2, 0], [2, 2, 0]],
            "faces": [[0, 1, 2, 3], [2, 1, 3, 3, 0x21]], **extra}


def test_faces_without_light_get_the_fallback_with_a_warning():
    warn: list[str] = []
    m = COL.model_from_json(_tri_model(), warn=warn)
    assert [f[4] for f in m.faces] == [COL.FALLBACK_LIGHT, 0x21]           # an explicit light is kept
    assert len(warn) == 1 and warn[0].startswith("FACE_LIGHT_DEFAULT: slab: 1 face(s)")
    assert COL.FALLBACK_LIGHT & 15 == 15


def test_model_light_and_prelight():
    warn: list[str] = []
    m = COL.model_from_json(_tri_model(light={"day": 12, "night": 4}), warn=warn)
    assert m.faces[0][4] == 12 | (4 << 4) and warn == []
    assert COL.model_from_json(_tri_model(light=0x5F)).faces[0][4] == 0x5F
    bright = COL.model_from_json(_tri_model(prelight={"day": [250, 240, 230], "night": 40})).faces[0][4]
    dark = COL.model_from_json(_tri_model(prelight=30)).faces[0][4]
    assert bright & 15 == 15 and (bright >> 4) < 15 and (dark & 15) < (bright & 15)
    assert COL.face_light(None, None) == COL.FALLBACK_LIGHT
    with pytest.raises(COL.ColError):
        COL.model_from_json(_tri_model(light={"day": 16}))
    with pytest.raises(COL.ColError):
        COL.model_from_json(_tri_model(prelight="bright"))


def test_col_write_cli_reports_the_fallback(satk_home, run_cli, tmp_path):
    import json

    p = tmp_path / "slab.json"
    p.write_text(json.dumps(_tri_model()), encoding="utf-8")
    env = run_cli(["col", "write", str(p), "--json"]).json
    assert env["ok"] and env["verified"] and any(w.startswith("FACE_LIGHT_DEFAULT") for w in env["warn"])
    p.write_text(json.dumps(_tri_model(light=0x3F)), encoding="utf-8")
    env = run_cli(["col", "write", str(p), "--json"]).json
    assert not any(w.startswith("FACE_LIGHT_DEFAULT") for w in env.get("warn", []))
