"""satk.rw.col: JSON -> COL, exact codec, checked with satk.formats.col (WP-02) and the vendored rwfury."""

from __future__ import annotations

import json
import struct

import pytest

from satk.formats.col import iter_col
from satk.rw import col as COL
from satk.rw.vendor import load, surface_names


def _grid(n: int) -> tuple[list, list]:
    """An n x n grid of quads at z=0 (2 * n * n faces)."""
    verts = [[x * 0.5, y * 0.5, 0.0] for y in range(n + 1) for x in range(n + 1)]
    faces = []
    for y in range(n):
        for x in range(n):
            a = y * (n + 1) + x
            faces += [[a, a + 1, a + n + 2, 1, 0], [a, a + n + 2, a + n + 1, 1, 0]]
    return verts, faces


def test_box_model_defaults():
    m = COL.model_from_json({"name": "cube", "boxes": [{"min": [-1, -1, 0], "max": [1, 1, 2], "surface": 4}]})
    assert m.version == 3 and m.flags == 0x02 and m.groups == []
    assert m.bmin == (-1.0, -1.0, 0.0) and m.bmax == (1.0, 1.0, 2.0) and m.center == (0.0, 0.0, 1.0)
    assert abs(m.radius - 3 ** 0.5) < 1e-9
    rec = COL.encode_model(m)
    assert rec[:4] == b"COL3" and struct.unpack_from("<I", rec, 4)[0] == len(rec) - 8
    (cm,) = list(iter_col(rec, strict=True))
    assert (cm.name, cm.version, cm.boxes, cm.surfaces) == ("cube", 3, 1, {4: 1})
    assert COL.encode_model(COL.decode_model(rec)) == rec


def test_mesh_with_face_groups_and_shadow():
    verts, faces = _grid(8)                              # 128 faces > 80 -> face groups
    js = {"name": "floor", "vertices": verts, "faces": faces, "spheres": [{"center": [1, 1, 1], "radius": 0.5}],
          "shadow": {"vertices": [[0, 0, 0], [4, 0, 0], [4, 4, 0]], "faces": [[0, 1, 2, 0, 0]]}}
    m = COL.model_from_json(js)
    assert m.flags == 0x02 | 0x08 | 0x10
    assert m.groups and max(e - s + 1 for _lo, _hi, s, e in m.groups) <= COL.FG_MAX_GROUP
    spans = sorted((s, e) for _lo, _hi, s, e in m.groups)
    assert spans[0][0] == 0 and spans[-1][1] == len(faces) - 1
    assert all(spans[i][1] + 1 == spans[i + 1][0] for i in range(len(spans) - 1))     # contiguous ranges
    for lo, hi, s, e in m.groups:                                                        # boxes hold their faces
        for f in m.faces[s:e + 1]:
            for v in f[:3]:
                p = [c / 128 for c in m.vertices[v]]
                assert all(lo[k] - 1e-6 <= p[k] <= hi[k] + 1e-6 for k in range(3))
    rec = COL.encode_model(m)
    (cm,) = list(iter_col(rec, strict=True))
    assert (cm.faces, cm.verts, cm.spheres, cm.shadow_faces) == (128, 81, 1, 1)
    back = COL.decode_model(rec)
    assert len(back.groups) == len(m.groups) and back.faces == m.faces
    assert COL.encode_model(back) == rec
    rwf = load()
    if rwf is not None:
        r = rwf.Col.from_bytes(rec).models[0]
        assert (len(r.faces), len(r.vertices), len(r.face_groups), len(r.shadow_faces)) == (128, 81, len(m.groups), 1)


def test_small_mesh_has_no_face_groups_and_odd_vertex_padding():
    verts, faces = _grid(2)                              # 9 vertices -> 54 bytes + 2 bytes padding
    m = COL.model_from_json({"name": "small", "vertices": verts, "faces": faces})
    assert m.groups == [] and m.flags == 0x02
    rec = COL.encode_model(m)
    off_v, off_f = struct.unpack_from("<II", rec, 4 + 4 + 22 + 2 + 40 + 12 + 12)
    assert off_f - off_v == 9 * 6 + 2


def test_surfaces_by_name_and_full_form():
    names = surface_names()
    if names is None:
        pytest.skip("vendor/rwfury missing")
    m = COL.model_from_json({"name": "s", "boxes": [{"min": [0, 0, 0], "max": [1, 1, 1], "surface": "tarmac"},
                                                    {"min": [0, 0, 0], "max": [1, 1, 1],
                                                     "surface": {"material": "GRASS_SHORT_LUSH", "light": 7}},
                                                    {"min": [0, 0, 0], "max": [1, 1, 1], "surface": [42, 1, 2, 3]}]},
                            surfaces=names)
    assert [b[2] for b in m.boxes] == [(1, 0, 0, 0), (9, 0, 0, 7), (42, 1, 2, 3)]
    with pytest.raises(COL.ColError, match="unknown surface"):
        COL.model_from_json({"name": "s", "boxes": [{"min": [0, 0, 0], "max": [1, 1, 1], "surface": "lava"}]},
                            surfaces=names)


def test_col1_and_col2():
    verts, faces = _grid(1)
    for v in (1, 2):
        m = COL.model_from_json({"name": f"v{v}", "version": v, "vertices": verts,
                                 "faces": [f[:3] + [5] for f in faces], "spheres": [{"center": [0, 0, 0], "radius": 1}]})
        rec = COL.encode_model(m)
        assert rec[:4] == COL.FOURCC[v]
        (cm,) = list(iter_col(rec, strict=True))
        assert (cm.version, cm.faces, cm.spheres) == (v, 2, 1)
        assert COL.encode_model(COL.decode_model(rec)) == rec


def test_export_import_is_exact_including_garbage():
    verts, faces = _grid(7)
    m = COL.model_from_json({"name": "junk", "vertices": verts, "faces": faces})
    rec = bytearray(COL.encode_model(m))
    rec[8 + 5:8 + 22] = b"\xab" * 17                       # garbage after "junk\0" like Rockstar's files
    rec = bytes(rec)
    d = COL.decode_model(rec)
    assert d.name == "junk" and d.name_raw is not None
    js = json.loads(json.dumps(COL.model_to_json(d)))
    assert "raw" in js and COL.encode_model(COL.model_from_json(js)) == rec
    canon = json.loads(json.dumps(COL.model_to_json(d, raw=False)))
    out = COL.encode_model(COL.model_from_json(canon))
    assert out[8:30] == b"junk".ljust(22, b"\0") and out[30:] == rec[30:]


def test_file_split_join_keeps_padding():
    a = COL.encode_model(COL.model_from_json({"name": "a", "spheres": [{"center": [0, 0, 0], "radius": 1}]}))
    b = COL.encode_model(COL.model_from_json({"name": "b", "boxes": [{"min": [0, 0, 0], "max": [1, 1, 1]}]}))
    blob = a + b + b"\0" * 100
    recs, tail = COL.split_models(blob)
    assert recs == [a, b] and tail == b"\0" * 100
    assert COL.join_models([COL.decode_model(r) for r in recs], tail) == blob


@pytest.mark.parametrize("bad,match", [
    ({"name": ""}, "name"),
    ({"name": "x" * 22}, "name"),
    ({"name": "a", "version": 5}, "version"),
    ({"name": "a", "vertices": [[0, 0, 0]], "faces": [[0, 1, 2]]}, "vertex index"),
    ({"name": "a", "vertices": [[300, 0, 0], [0, 0, 0], [0, 1, 0]], "faces": [[0, 1, 2]]}, "range"),
    ({"name": "a", "spheres": [{"center": [0, 0], "radius": 1}]}, r"\[x, y, z\]"),
    ({"name": "a", "version": 2, "shadow": {"vertices": [[0, 0, 0]], "faces": []}}, "version 3"),
    ({"name": "a", "vertices": [[0, 0, 0], [1, 0, 0], [0, 1, 0]], "faces": [[0, 1, 2]],
      "face_groups": [{"min": [0, 0, 0], "max": [1, 1, 1], "start": 0, "end": 4}]}, "face group"),
])
def test_validation(bad, match):
    with pytest.raises(COL.ColError, match=match):
        COL.model_from_json(bad)


def test_decode_rejects_garbage():
    with pytest.raises(COL.ColError):
        COL.split_models(b"RIFF" + b"\0" * 40)
    with pytest.raises(COL.ColError):
        COL.decode_model(b"COL3" + struct.pack("<I", 500) + b"\0" * 40)
