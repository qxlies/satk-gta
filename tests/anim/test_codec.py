"""satk.anim.ifp: key frame codec on hand-packed bytes (ANP3, ANP2, ANPK), conversions, error paths."""

from __future__ import annotations

import struct

import pytest

from satk.anim.ifp import (Anim, Ifp, Seq, convert_anim, dequantize_seq, frame_type, quantize_seq, read_ifp,
                           write_ifp)
from satk.formats.ifp import parse_ifp
from satk.formats.rw import FormatError

from .conftest import anp3_bytes, anpk_bytes, walk_ifp


def test_anp3_hand_packed_values_and_bit_exact():
    blob = anp3_bytes("ped", [
        ("walk", [("Root", 0, 4, [(0, 0, 2896, 2896, 0, 0, 0, -70), (0, 0, 2896, 2896, 30, 0, 1024, -70)]),
                  (" Pelvis", 1, 3, [(-2048, -2048, -2048, 2048, 0), (-2048, -2048, -2048, 2048, 30)])]),
        ("float", [("Root", -1, 2, [(0.0, 0.0, 0.0, 1.0, 0.0, 1.0, 2.0, 3.0)]),
                   ("x", 5, 1, [(0.5, 0.5, 0.5, 0.5, 0.25)])]),
    ], flags=None)
    ifp = read_ifp(blob)
    assert (ifp.format, ifp.pack, [a.name for a in ifp.anims]) == ("ANP3", "ped", ["walk", "float"])
    walk, flt = ifp.anims
    assert walk.flags == 1 and walk.compressed and not flt.compressed
    root = walk.seqs[0]
    assert (root.tag, root.trans, root.compressed, frame_type(root)) == (0, True, True, 4)
    t, q, tr = root.frames()[1]
    assert t == 0.5 and q == (0.0, 0.0, 2896 / 4096, 2896 / 4096) and tr == (0.0, 1.0, -70 / 1024)
    assert walk.duration == 0.5 and walk.keys == 2
    assert flt.seqs[0].frames()[0] == (0.0, (0.0, 0.0, 0.0, 1.0), (1.0, 2.0, 3.0))
    assert frame_type(flt.seqs[1]) == 1 and flt.seqs[1].tag == 5
    assert write_ifp(ifp) == blob
    assert ifp.end == len(blob)
    # the structural reader of satk.formats agrees
    assert parse_ifp(blob) == ("ANP3", "ped", [("walk", 2, 2), ("float", 2, 1)])


def test_anp3_keeps_name_garbage_and_ignores_trailing_padding():
    blob = anp3_bytes(b"ped\0junk", [(b"run\0=C:\\3dsmax", [(b"Root\0xx", 0, 3, [(0, 0, 0, 4096, 0)])])])
    padded = blob + b"\0" * 300                                   # an IMG entry is padded to 2048-byte sectors
    ifp = read_ifp(padded)
    a = ifp.anims[0]
    assert ifp.pack == "ped" and a.name == "run" and a.seqs[0].name == "Root"
    assert ifp.raw_pack is not None and a.raw_name is not None and a.seqs[0].raw_name is not None
    assert ifp.end == len(blob) and write_ifp(ifp) == blob


def test_anp2_round_trip():
    blob = anp3_bytes("old", [("a", [("Root", -1, 1, [(0.0, 0.0, 0.0, 1.0, 0.0), (0.0, 0.0, 0.0, 1.0, 1.0)])])],
                      anp2=True)
    ifp = read_ifp(blob)
    assert ifp.format == "ANP2" and ifp.anims[0].flags == 0
    assert write_ifp(ifp) == blob


def test_anpk_conjugates_quaternion_time_last_and_round_trip():
    stored = [(0.1, 0.2, 0.3, 0.9, 1.0, 2.0, 3.0, 0.5)]           # q (stored, conjugated), tr, t
    blob = anpk_bytes("cut", [("door", [("DOOR", -1, b"KRT0", stored), ("lock", 7, b"KR00", [(0.0, 0.0, 0.0, 1.0, 0.0)]),
                                        ("empty", -1, b"KR00", [])]),
                              ("scale", [("box", -1, b"KRTS", [(0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0, 2.0, 2.0, 2.0, 0.0)])])])
    ifp = read_ifp(blob)
    assert ifp.format == "ANPK" and ifp.pack == "cut"
    door = ifp.anims[0].seqs[0]
    t, q, tr = door.frames()[0]
    f32 = lambda v: struct.unpack("<f", struct.pack("<f", v))[0]  # noqa: E731
    assert t == f32(0.5) and q == (-f32(0.1), -f32(0.2), -f32(0.3), f32(0.9)) and tr == (1.0, 2.0, 3.0)
    assert ifp.anims[0].seqs[1].tag == 7 and ifp.anims[0].seqs[2].keys == []
    box = ifp.anims[1].seqs[0]
    assert box.scale and box.trans and len(box.keys[0]) == 11
    assert write_ifp(ifp) == blob
    assert parse_ifp(blob)[2] == [("door", 3, 1), ("scale", 1, 1)]


def test_anpk_nonzero_name_padding_is_kept():
    blob = bytearray(anpk_bytes("las", [("sjmove", [("Prism", -1, b"KR00", [(0.0, 0.0, 0.0, 1.0, 0.0)])])]))
    i = blob.index(b"NAME") + 8 + len(b"sjmove\0")
    blob[i] = 0xDC                                                   # garbage in the pad byte (vanilla las.ifp)
    ifp = read_ifp(bytes(blob))
    assert ifp.anims[0].name_pad == b"\xdc"
    assert write_ifp(ifp) == bytes(blob)


def test_write_model_api_and_reread():
    ifp = walk_ifp()
    blob = write_ifp(ifp)
    back = read_ifp(blob)
    assert [a.name for a in back.anims] == ["walk", "idle"]
    assert [[s.keys for s in a.seqs] for a in back.anims] == [[s.keys for s in a.seqs] for a in ifp.anims]
    assert write_ifp(back) == blob


def test_quantize_and_ranges():
    s = Seq("Root", 0, True, False, [(0.0, 0.0, 0.70710677, 0.70710677, 0.5, 1.0, -2.0, 0.25)])
    c = quantize_seq(s)
    assert c.compressed and c.keys == [(0, 0, 2896, 2896, 30, 1024, -2048, 256)]
    assert dequantize_seq(c).keys == [(0.0, 0.0, 2896 / 4096, 2896 / 4096, 0.5, 1.0, -2.0, 0.25)]
    with pytest.raises(ValueError, match="translation"):
        quantize_seq(Seq("Root", 0, True, False, [(0.0, 0.0, 0.0, 1.0, 0.0, 40.0, 0.0, 0.0)]))
    with pytest.raises(ValueError, match="time"):
        quantize_seq(Seq("Root", 0, False, False, [(0.0, 0.0, 0.0, 1.0, 600.0)]))


def test_convert_between_formats():
    anpk = read_ifp(anpk_bytes("cut", [("a", [("box", 3, b"KRTS", [(0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0, 2.0, 2.0, 2.0, 0.5)])])]))
    a, notes = convert_anim(anpk.anims[0], "ANPK", "ANP3")
    assert notes and "scale" in notes[0]
    s = a.seqs[0]
    assert not s.scale and len(s.keys[0]) == 8 and s.extra is None and a.flags == 0
    blob = write_ifp(Ifp("ANP3", "cut", [a]))
    assert read_ifp(blob).anims[0].seqs[0].frames() == s.frames()
    c, _ = convert_anim(a, "ANP3", "ANP3", compress=True)
    assert c.flags == 1 and c.seqs[0].compressed
    # compressed -> ANPK: floats, the values are exact
    w = walk_ifp().anims[0]
    k, _ = convert_anim(w, "ANP3", "ANPK")
    assert not any(x.compressed for x in k.seqs)
    back = read_ifp(write_ifp(Ifp("ANPK", "w", [k]))).anims[0]
    for x, y in zip(back.seqs, w.seqs):                             # rotation/translation exact, time to float32
        assert [(q, tr) for _t, q, tr in x.frames()] == [(q, tr) for _t, q, tr in y.frames()]
        assert max(abs(t0 - t1) for (t0, _q, _r), (t1, _q2, _r2) in zip(x.frames(), y.frames())) < 1e-6
    same, _ = convert_anim(w, "ANP3", "ANP3")
    assert same is w


def test_write_errors_and_broken_input():
    with pytest.raises(ValueError, match="longer than 23"):
        write_ifp(Ifp("ANP3", "p", [Anim("x" * 30, [Seq("Root", 0, False, True, [(0, 0, 0, 4096, 0)])], 1)]))
    with pytest.raises(ValueError, match="does not match frame type"):
        write_ifp(Ifp("ANP3", "p", [Anim("x", [Seq("Root", 0, True, True, [(0, 0, 0, 4096, 0)])], 1)]))
    with pytest.raises(ValueError, match="unknown IFP format"):
        write_ifp(walk_ifp(), "ANP9")
    with pytest.raises(FormatError):
        read_ifp(b"ANP3" + b"\0" * 10)
    with pytest.raises(FormatError):
        read_ifp(b"RIFF" + b"\0" * 64)
    blob = anp3_bytes("p", [("a", [("Root", 0, 3, [(0, 0, 0, 4096, 0)] * 4)])])
    with pytest.raises(FormatError):
        read_ifp(blob[:-5])
