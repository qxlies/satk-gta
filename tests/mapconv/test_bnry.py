"""Binary IPL <-> text (``satk ipl decompile|compile``): exact float32 text, layouts, padding, CLI."""

from __future__ import annotations

import random
import struct
from pathlib import Path

import pytest

from satk.mapconv.bnry import (compile_ipl, decompile, encode_bnry, f32_text, first_difference, is_bnry,
                               parse_bnry)

INSTS = [(2487.3984, -1688.1016, 13.28125, 0.00021211553, -0.0024252036, -0.99939084, -0.034814548, 1410, 0, -1),
         (-0.0, 1e-8, 3.4028234663852886e38, 0.0, 0.0, 0.7071069, 0.7071067, 17613, 256 | 13, 198),
         (1.1754943508222875e-38, 1.401298464324817e-45, -123456.7, 0.1, -0.1, 0.3, 0.9, 1, -1, 5)]
CARS = [(2500.5, -1700.25, 13.5, 270.0, 411, -1, -1, 1, 0, 0, 0, 0)]


def _bits(v: float) -> bytes:
    return struct.pack("<f", v)


def test_f32_text_is_shortest_and_exact():
    assert f32_text(struct.unpack("<f", _bits(0.1))[0]) == "0.1"
    assert f32_text(-0.0) == "-0" and f32_text(0.0) == "0" and f32_text(13.28125) == "13.28125"
    assert "e" not in f32_text(1e-8) and "e" not in f32_text(3.4028234663852886e38)
    rnd = random.Random(1234)
    for _ in range(20000):
        raw = struct.pack("<I", rnd.getrandbits(32))
        v = struct.unpack("<f", raw)[0]
        if v != v or v in (float("inf"), float("-inf")):
            continue
        t = f32_text(v)
        assert _bits(float(t)) == raw, (raw.hex(), t)


@pytest.mark.parametrize("style", ["vanilla", "sizes"])
@pytest.mark.parametrize("pad", ["sector", "none"])
@pytest.mark.parametrize("cars", [[], CARS])
def test_roundtrip_layouts(style, pad, cars):
    insts = [tuple(struct.unpack("<7f", struct.pack("<7f", *r[:7]))) + r[7:] for r in INSTS]
    data = encode_bnry(insts, cars, style, pad)
    assert is_bnry(data) and (len(data) % 2048 == 0) == (pad == "sector")
    info = parse_bnry(data)
    assert (info.style, info.pad) == (style, pad)
    text, info = decompile(data, names={1410: "DYN_F_R_WOOD_1b"}, label="x_stream0.ipl", parent="x")
    assert f"# satk-bnry: style={style}" in text and "1410, DYN_F_R_WOOD_1b, 0," in text and "model17613" in text
    assert "lod indexes the inst section of the parent text IPL x.ipl" in text
    back, rep = compile_ipl(text)
    assert back == data and rep["inst"] == 3 and rep["cars"] == len(cars) and rep["directive"]


def test_detects_what_it_cannot_keep():
    data = encode_bnry([INSTS[0]], [], "vanilla", "none") + b"\x01\x02"
    info = parse_bnry(data)
    assert info.pad is None and info.why
    text, _ = decompile(data)
    assert compile_ipl(text)[0] != data
    odd = bytearray(encode_bnry([INSTS[0]], [], "vanilla", "none"))
    struct.pack_into("<I", odd, 8, 7)  # an unused count
    assert parse_bnry(bytes(odd)).style is None
    for bad in (b"bnry", b"xxxx" + b"\0" * 0x48, encode_bnry([INSTS[0]], [], "vanilla", "none")[:0x60]):
        with pytest.raises(ValueError):
            parse_bnry(bad)


def test_compile_plain_text_ipl_and_errors():
    text = ("# my placements\ninst\n1000, box, 0, 1.5, 2, 3, 0, 0, 0, 1\n1001 lod 4294967295 1 2 3 0 0 0 1 -1\nend\n"
            "enex\n1, 2, 3\nend\ncars\n1, 2, 3, 90, 411, -1, -1, 0, 0, 0, 0, 0\nend\n")
    data, rep = compile_ipl(text)
    info = parse_bnry(data)
    assert (rep["inst"], rep["cars"], rep["skipped"], rep["style"], rep["pad"], rep["directive"]) == (
        2, 1, {"enex": 1}, "vanilla", "sector", False)
    assert info.insts[0][9] == -1 and info.insts[1][8] == -1 and len(data) == 2048
    assert compile_ipl(text, pad="none")[0] == data[:0x4C + 80 + 48]
    for bad, where in (("inst\n1000, box, 0, 1, 2\nend\n", "line 2"), ("inst\n1000, box, 0, x, 2, 3, 0, 0, 0, 1\nend\n",
                                                                      "line 2"),
                       ("cars\n1, 2, 3\nend\n", "line 2"), ("inst\n99999999999, b, 0, 1, 2, 3, 0, 0, 0, 1\nend\n",
                                                            "line 2")):
        with pytest.raises(ValueError) as e:
            compile_ipl(bad)
        assert str(e.value).startswith(where)
    with pytest.raises(ValueError):
        compile_ipl("inst\nend\n", pad="weird")
    assert first_difference(b"abc", b"abd") == 2 and first_difference(b"ab", b"abc") == 2
    assert first_difference(b"x", b"x") is None


def test_cli_decompile_compile_compare(satk_home, run_cli, tmp_path):
    src = tmp_path / "my_stream0.ipl"
    src.write_bytes(encode_bnry(INSTS, CARS))
    r = run_cli(["ipl", "decompile", str(src), "--no-names"])
    assert r.code == 0, r.err
    env = r.json
    txt = Path(env["file"])
    assert txt == satk_home / "work" / "out" / "mapconv" / "decompiled" / "my_stream0.ipl"
    assert (env["inst"], env["cars"], env["style"], env["pad"], env["exact"]) == (3, 1, "vanilla", "sector", True)
    r = run_cli(["ipl", "compile", str(txt), "--compare", str(src)])
    assert r.code == 0, r.err
    assert r.json["same"] is True and Path(r.json["file"]).read_bytes() == src.read_bytes()
    assert Path(r.json["file"]) == satk_home / "work" / "out" / "mapconv" / "compiled" / "my_stream0.ipl"
    # an edit: one more object -> differs, reported
    txt.write_text(txt.read_text(encoding="latin-1").replace("\nend\n", "\n1, a, 0, 1, 2, 3, 0, 0, 0, 1, -1\nend\n", 1),
                   encoding="latin-1")
    r = run_cli(["ipl", "compile", str(txt), "--out", "edited.ipl", "--compare", str(src)]).json
    assert r["inst"] == 4 and r["same"] is False and any(w.startswith("DIFFERENT") for w in r["warn"])
    assert r["file"].endswith("/compiled/edited.ipl")
    # wrong directions and plain text IPLs
    assert run_cli(["ipl", "decompile", str(txt)]).json["error"]["code"] == "BAD_PARAMS"
    assert run_cli(["ipl", "compile", str(src)]).json["error"]["code"] == "BAD_PARAMS"
    plain = tmp_path / "plain.ipl"
    plain.write_text("inst\n1000, box, 0, 1, 2, 3, 0, 0, 0, 1, 0\nend\ngrge\n1, 2, 3\nend\n", encoding="latin-1")
    w = run_cli(["ipl", "compile", str(plain)]).json["warn"]
    assert [x.split(":")[0] for x in w] == ["LOST_SECTIONS", "LOD_SPACE"]
    bad = tmp_path / "bad.ipl"
    bad.write_text("inst\n1000, box\nend\n", encoding="latin-1")
    r = run_cli(["ipl", "compile", str(bad)])
    assert r.code == 2 and "line 2" in r.json["error"]["msg"]


def test_cli_decompile_sid_with_names(world, run_cli):
    r = run_cli(["ipl", "decompile", "ipl:t_stream0"])
    assert r.code == 0, r.err
    env = r.json
    assert (env["inst"], env["cars"], env["exact"]) == (2, 1, True)
    text = Path(env["file"]).read_text(encoding="latin-1")
    assert "\n1000, box, 0, 105, 100, 5, 0, 0, 0, 1, 0\n" in text and "parent text IPL t.ipl" in text
    r = run_cli(["ipl", "compile", env["file"], "--compare", "ipl:t_stream0"]).json
    assert r["same"] is True
    assert run_cli(["ipl", "decompile", "ipl:t"]).json["error"]["code"] == "BAD_PARAMS"  # a text IPL
