"""IPL side of satk.mapconv: text/bnry reader with the engine rotation rule, text writer, tilt handling."""

from __future__ import annotations

import struct

from satk.mapconv.ipl import read_ipl, write_ipl
from satk.mapconv.mta import read_mta, write_mta
from satk.mapconv.rot import DONT_STREAM, ipl_heading_only, ipl_quat, same_rotation
from satk.mapconv.scene import MapObject, Scene

TEXT_IPL = """# synthetic
inst
17613, lae2_roads89, 0, 2489.296875, -1668.5, 12.296875, 0, 0, -0.7071068, 0.7071068, 1
17858, lodlae2_roads89, 0, 2489.296875, -1668.5, 12.296875, 0, 0, 0, 1, -1
1410, DYN_F_R_WOOD_1b, 3, 10.0, 20.0, 30.0, 0.01, -0.02, 0.3, 0.95, -1
1411, tilted, 512, 1.0, 2.0, 3.0, 0.2, 0.1, 0.0, 0.974679, -1
end
cull
1.0, 2.0, 3.0, 1, 1, 1, 0, 0, 0
end
enex
2.0, 3.0, 4.0, 0, 1, 1, 1, 2, 3, 4, 0, 1, "x", 0, 2, 0, 24
end
"""


def test_text_ipl_reader():
    sc = read_ipl(TEXT_IPL.encode("latin-1"), "x.ipl")
    a, b, c, d = sc.objects
    assert (a.model, a.name, a.pos, a.lod, a.interior, a.world) == (17613, "lae2_roads89", (2489.296875, -1668.5,
                                                                                          12.296875), 1, 0, 0)
    assert abs(a.rot[2] - 90.0) < 1e-3 and a.rot[:2] == (0.0, 0.0)  # file qz < 0 -> world heading +90
    assert c.rot[:2] == (0.0, 0.0) and c.interior == 3  # small tilt dropped like the engine does
    assert d.iflags == 2 and d.interior == 0
    assert d.rot[0] != 0.0  # a real tilt is kept
    assert sc.skipped == {"cull": 1, "enex": 1}
    assert [i.code for i in sc.issues] == ["TILT_IGNORED"]


def _bnry(insts, cars=0):
    hdr = struct.pack("<4s6I12I", b"bnry", len(insts), 0, 0, 0, cars, 0, 0x4C, 0, 0, 0, 0, 0, 0, 0,
                      0x4C + 40 * len(insts), 0, 0, 0)
    body = b"".join(struct.pack("<7f3i", *i) for i in insts)
    body += b"\0" * (48 * cars)
    return hdr + body


def test_binary_ipl_reader_uses_index_names_and_drops_parent_lods():
    data = _bnry([(1.0, 2.0, 3.0, 0.0, 0.0, -0.5, 0.8660254, 1410, 0, 7), (4.0, 5.0, 6.0, 0, 0, 0, 1, 1411, 3, -1)],
                 cars=2)
    sc = read_ipl(data, "x_stream0", names={1410: "DYN_F_R_WOOD_1b"})
    a, b = sc.objects
    assert (a.name, b.name, a.lod, b.interior) == ("DYN_F_R_WOOD_1b", None, -1, 3)
    assert abs(a.rot[2] - 60.0) < 1e-4
    assert sc.skipped == {"cars": 2} and [i.code for i in sc.issues] == ["LOD_EXTERNAL"]


def test_writer_conjugates_and_reader_returns_the_same_rotation():
    objs = [MapObject(model=1, pos=(1.0, 2.0, 3.0), rot=r, interior=i, iflags=f, lod=lod)
            for r, i, f, lod in (((0.0, 0.0, 90.0), 0, 0, 1), ((30.0, -45.0, 170.0), 5, 0, -1),
                                 ((0.0, 0.0, -135.0), 13, 4, -1))]
    sc = Scene(objects=objs)
    text = write_ipl(sc, names={1: "thing"}, header="test")
    assert text.startswith("# test\ninst\n") and text.endswith("end\n")
    assert "1, thing, 1037, 1, 2, 3, " in text  # area 13 + iflags 4 (underwater) << 8
    back = read_ipl(text.encode("latin-1"))
    for a, b in zip(objs, back.objects):
        assert same_rotation(a.rot, b.rot, 1e-4), (a.rot, b.rot)
        assert (b.pos, b.interior, b.iflags, b.lod) == (a.pos, a.interior, a.iflags, a.lod)
    assert sc.issues == []


def test_small_tilts_warn_or_keep_with_dont_stream():
    sc = Scene(objects=[MapObject(model=1, pos=(0.0, 0.0, 0.0), rot=(3.0, -2.0, 120.0)),
                        MapObject(model=2, pos=(0.0, 0.0, 0.0), rot=(3.0, 0.0, 0.0), interior=-1)])
    write_ipl(sc)
    assert [i.code for i in sc.issues] == ["TILT_LOST", "INTERIOR_ALL"]
    sc2 = Scene(objects=list(sc.objects))
    text = write_ipl(sc2, tilt="dontstream")
    back = read_ipl(text.encode("latin-1"))
    assert back.objects[0].iflags == DONT_STREAM
    assert same_rotation(back.objects[0].rot, (3.0, -2.0, 120.0), 1e-4)  # the loader keeps the full rotation now
    assert [i.code for i in sc2.issues] == ["TILT_LOST", "INTERIOR_ALL"]  # obj 1: qy == 0, cannot be kept
    assert not ipl_heading_only(ipl_quat(3.0, -2.0, 120.0), DONT_STREAM)


def test_ipl_map_ipl():
    sc = read_ipl(TEXT_IPL.encode("latin-1"))
    again = read_ipl(write_ipl(read_mta(write_mta(sc))).encode("latin-1"))
    for a, b in zip(sc.objects, again.objects):
        assert (a.model, a.name, a.pos, a.interior, a.iflags, a.lod) == (b.model, b.name, b.pos, b.interior, b.iflags,
                                                                        b.lod)
        assert same_rotation(a.rot, b.rot, 1e-3)


def test_ipl_names_are_single_tokens():
    sc = Scene(objects=[MapObject(model=5, pos=(0.0, 0.0, 0.0), rot=(0.0, 0.0, 0.0), name="my wall, #2")])
    text = write_ipl(sc)
    assert "5, my_wall_2, " in text
    assert read_ipl(text.encode("latin-1")).objects[0].name == "my_wall_2"
