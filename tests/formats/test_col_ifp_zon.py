"""satk.formats.col / ifp / zon on synthetic data (SPEC §4.2, pitfall #13; milestone F2)."""

from __future__ import annotations

import struct

import pytest

from satk.formats.col import iter_col
from satk.formats.ifp import parse_ifp
from satk.formats.rw import FormatError
from satk.formats.zon import parse_zon


# ----------------------------------------------------------------------------- COL
def test_col_v1(b):
    blob = b.col_v1("molotov", spheres=[(0.2, 0, 0, 0.1, 5)], boxes=[((-1, -1, -1), (1, 1, 1), 7)],
                    verts=[(0, 0, 0), (1, 0, 0), (0, 1, 0)], faces=[(0, 1, 2, 5)], mid=1234)
    (m,) = iter_col(blob)
    assert (m.idx, m.version, m.name, m.hdr_model_id, m.size) == (0, 1, "molotov", 1234, len(blob))
    assert (m.spheres, m.boxes, m.verts, m.faces, m.shadow_faces) == (1, 1, 3, 1, 0)
    assert m.surfaces == {5: 2, 7: 1}
    assert m.bbox == (-1.0, -2.0, -3.0, 1.0, 2.0, 3.0) and m.bsphere == (0.0, 0.0, 0.0, 5.0)


@pytest.mark.parametrize("version", [2, 3, 4])
def test_col_v234(b, version):
    blob = b.col_v23("lae2_bit", version, spheres=[(0, 0, 0, 1, 9)], boxes=[((0, 0, 0), (1, 1, 1), 9)],
                     verts=[(0, 0, 0), (128, 0, 0), (0, 128, 0), (0, 0, 128)], faces=[(0, 1, 2, 4), (1, 2, 3, 4)],
                     shadow=[(0, 1, 2, 4)] * 3)
    (m,) = iter_col(blob, strict=True)
    assert (m.version, m.name, m.spheres, m.boxes, m.faces) == (version, "lae2_bit", 1, 1, 2)
    assert m.verts == 4                                    # highest face index + 1 (not stored in COL2+)
    assert m.shadow_faces == (3 if version >= 3 else 0)
    assert m.surfaces == {4: 2, 9: 2}
    assert m.bbox == (-1.0, -2.0, -3.0, 1.0, 2.0, 3.0) and m.bsphere == (0.0, 0.0, 0.0, 5.0)


def test_col_many_models_padding_and_names_only(b):
    blob = b.col_v23("a", 3, mid=1) + b.col_v1("b", mid=1) + b.col_v23("c", 2, mid=1) + b"\0" * 1000
    ms = list(iter_col(blob))
    assert [(m.idx, m.name, m.version) for m in ms] == [(0, "a", 3), (1, "b", 1), (2, "c", 2)]
    assert {m.hdr_model_id for m in ms} == {1}             # kept, but never used for matching (pitfall #13)


def test_col_errors(b):
    with pytest.raises(FormatError, match="not a collision"):
        list(iter_col(b"\0" * 64))
    with pytest.raises(FormatError, match="empty"):
        list(iter_col(b""))
    good = b.col_v1("x", spheres=[(1, 0, 0, 0, 1)])
    with pytest.raises(FormatError, match="overruns the buffer"):
        list(iter_col(good[:-4]))
    # body claims 133 boxes and stores none (vanilla models/coll/peds.col): lenient by default
    bad = bytearray(good)
    nbox_at = 32 + 40 + 4 + 20 + 4
    struct.pack_into("<I", bad, nbox_at, 133)
    errs: list = []
    (m,) = iter_col(bytes(bad), errors=errs)
    assert (m.name, m.spheres, m.boxes, m.bbox) == ("x", 0, 0, (-1.0, -2.0, -3.0, 1.0, 2.0, 3.0))
    assert errs and errs[0][0] == 0 and "133 boxes" in errs[0][1]
    with pytest.raises(FormatError, match="133 boxes"):
        list(iter_col(bytes(bad), strict=True))


def test_col_offsets_outside_the_model(b):
    blob = bytearray(b.col_v23("x", 3, faces=[(0, 1, 2, 1)], verts=[(0, 0, 0)] * 3))
    struct.pack_into("<I", blob, 32 + 40 + 8 + 4 + 16, 0xFFFF0)   # offFaces
    with pytest.raises(FormatError, match="faces"):
        list(iter_col(bytes(blob), strict=True))


def test_col2_rejects_impossible_vertex_span():
    header = struct.pack("<4sI22sH", b"COL2", 108, b"broken", 0)
    body = struct.pack("<10f3HBxI6I", -1, -1, -1, 1, 1, 1, 0, 0, 0, 2,
                       0, 0, 1, 0, 0, 0, 0, 0, 0xFFFFFFF0, 104, 0)
    raw = header + body + struct.pack("<4H", 0, 1, 2, 0)
    with pytest.raises(FormatError, match="vertices"):
        list(iter_col(raw, strict=True))
    errors = []
    (model,) = iter_col(raw, errors=errors)
    assert model.name == "broken" and model.bbox == (-1, -1, -1, 1, 1, 1)
    assert (model.verts, model.faces, model.surfaces) == (0, 0, {})
    assert len(errors) == 1 and errors[0][0] == 0


@pytest.mark.parametrize("version", [2, 3, 4])
@pytest.mark.parametrize("offset", [0, 28, 0xFFFFFFF0])
def test_col_vertex_offset_must_address_record_data(b, version, offset):
    raw = bytearray(b.col_v23("bad_vertices", version, verts=[(0, 0, 0)] * 3, faces=[(0, 1, 2, 7)]))
    struct.pack_into("<I", raw, 32 + 52 + 3 * 4, offset)
    with pytest.raises(FormatError, match="vertices"):
        list(iter_col(raw, strict=True))


@pytest.mark.parametrize("version", [2, 3, 4])
def test_col_vertex_count_cannot_read_into_next_record(b, version):
    raw = b.col_v23("bad_count", version, verts=[(0, 0, 0)] * 3, faces=[(0, 1, 12, 7)])
    raw += b.col_v23("next_record", version, verts=[(0, 0, 0)] * 20)
    with pytest.raises(FormatError, match="vertices"):
        list(iter_col(raw, strict=True))


@pytest.mark.parametrize("version", [3, 4])
@pytest.mark.parametrize("field,value", [(0, 10000), (1, 0), (1, 0xFFFFFFF0), (2, 0), (2, 0xFFFFFFF0)])
def test_col_shadow_spans_must_fit_record(b, version, field, value):
    raw = bytearray(b.col_v23("bad_shadow", version, verts=[(0, 0, 0)] * 3, shadow=[(0, 1, 2, 7)]))
    struct.pack_into("<I", raw, 32 + 76 + field * 4, value)
    with pytest.raises(FormatError, match="shadow"):
        list(iter_col(raw, strict=True))


# ----------------------------------------------------------------------------- IFP
def test_anp3_and_anp2(b):
    anims = [("walk_civi", [("root", 4, 23), ("l_arm", 3, 20)]), ("idle", [("root", 2, 5)]), ("empty", [])]
    fmt, pack, out = parse_ifp(b.anp3("ped", anims))
    assert (fmt, pack, out) == ("ANP3", "ped", [("walk_civi", 2, 23), ("idle", 1, 5), ("empty", 0, 0)])
    fmt, pack, out = parse_ifp(b.anp3("old", [("a", [("x", 1, 3)])], anp2=True))
    assert (fmt, pack, out) == ("ANP2", "old", [("a", 1, 3)])


def test_anpk(b):
    anims = [("csbcard", [("root", 522, b"KRT0"), ("hand", 10, b"KR00")]), ("noframes", [("b", 0, b"KR00")]),
             ("scaled", [("s", 2, b"KRTS")])]
    fmt, pack, out = parse_ifp(b.anpk("BCESA4w", anims))
    assert (fmt, pack) == ("ANPK", "BCESA4w")
    assert out == [("csbcard", 2, 522), ("noframes", 1, 0), ("scaled", 1, 2)]


@pytest.mark.parametrize("cut", [0, 4, 30, 40, 70, 100])
def test_ifp_truncated(b, cut):
    blob = b.anp3("ped", [("walk", [("root", 4, 3)])])
    with pytest.raises(FormatError):
        parse_ifp(blob[:cut])


def test_ifp_bad_frame_type_and_section(b):
    blob = bytearray(b.anp3("ped", [("walk", [("root", 4, 3)])]))
    struct.pack_into("<I", blob, 36 + 36 + 24, 9)
    with pytest.raises(FormatError, match="frame type 9"):
        parse_ifp(bytes(blob))
    bad = bytearray(b.anpk("p", [("a", [("r", 1, b"KR00")])]))
    i = bad.find(b"DGAN")
    bad[i:i + 4] = b"XXXX"
    with pytest.raises(FormatError, match="DGAN"):
        parse_ifp(bytes(bad))
    with pytest.raises(FormatError, match="not an IFP"):
        parse_ifp(b"RIFF....")


# ----------------------------------------------------------------------------- ZON
ZON = """zone
# comment
GAN1, 0, 2222.56, -1722.33, -10.0, 2632.83, -1628.33, 200.0, 1, GAN1
Vegas 3 685.0 476.093 -500.0 3000.0 3000.0 500.0 3 UNUSED
BAD, 0, x, 1, 2
end
inst
1, a, 0, 1, 2, 3, 0, 0, 0, 1, -1
end
"""


def test_parse_zon():
    errs: list = []
    zs = parse_zon(ZON, errors=errs)
    assert zs[0] == {"name": "GAN1", "label": "GAN1", "type": 0, "level": 1, "min": (2222.56, -1722.33, -10.0),
                     "max": (2632.83, -1628.33, 200.0), "line": 3}
    assert (zs[1]["name"], zs[1]["level"], zs[1]["label"]) == ("Vegas", 3, "UNUSED")
    assert len(zs) == 2 and errs and errs[0][0] == 5
    with pytest.raises(FormatError) as ei:
        parse_zon(ZON, strict=True)
    assert ei.value.kind == "zon" and ei.value.offset == 5
