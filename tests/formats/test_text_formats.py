"""satk.formats.dat / ide / ipl on synthetic text and bnry data (pitfalls #9-#12)."""

from __future__ import annotations

import math

import pytest

from satk.formats.dat import canon_relpath, parse_dat, read_text, resolve_ci, split_dos_path
from satk.formats.ide import parse_ide
from satk.formats.ipl import (area, iflags, parse_ipl_binary, parse_ipl_text, rz_deg, stream_base, world_quat)
from satk.formats.rw import FormatError


# ----------------------------------------------------------------------------- dat
def test_parse_dat():
    text = "# comment\n\nIMG DATA\\PATHS\\CARREC.IMG  \ncolfile 0 MODELS\\COLL\\WEAPONS.COL\nIDE DATA\\X.IDE # tail\nSPLASH\n"
    lines = parse_dat(text)
    assert [(x.key, x.arg, x.line) for x in lines] == [
        ("IMG", "DATA\\PATHS\\CARREC.IMG", 3), ("COLFILE", "0 MODELS\\COLL\\WEAPONS.COL", 4),
        ("IDE", "DATA\\X.IDE", 5), ("SPLASH", "", 6)]
    assert lines[1].path == "MODELS\\COLL\\WEAPONS.COL" and lines[0].path == lines[0].arg


def test_resolve_ci(tmp_path):
    d = tmp_path / "Data" / "Maps" / "LA"
    d.mkdir(parents=True)
    (d / "LaWn.ide").write_text("x", encoding="latin-1")
    hit = resolve_ci(tmp_path, "DATA\\MAPS\\LA\\LAwn.IDE")
    assert hit is not None and hit.is_file() and hit.read_text(encoding="latin-1") == "x"
    assert resolve_ci(tmp_path, "data/maps/la/lawn.ide").samefile(hit)
    assert resolve_ci(tmp_path, "DATA\\MAPS\\LA\\NOPE.IDE") is None
    # a file created after the listing was cached is still found
    (d / "Late.IPL").write_text("y", encoding="latin-1")
    assert resolve_ci(tmp_path, "data\\maps\\la\\LATE.ipl") is not None


@pytest.mark.parametrize("path", [
    "", ".", "../secret", "a/../b", "/Windows", r"\Windows", r"\\server\share\file.ide",
    "C:/Windows/win.ini", "C:Windows", "ignored/C:/Windows", r"ignored\C:Windows",
    r"ignored\more\D:\asset.ide", "./C:/Windows", '"ignored/C:/Windows"',
])
def test_resolve_ci_rejects_unsafe_paths(tmp_path, path):
    # resolve_ci treats every path that could leave the root as "not found" (satk.formats.dat).
    assert split_dos_path(path) is None
    assert resolve_ci(tmp_path, path) is None


def test_resolve_ci_allows_directory_links_within_root(tmp_path, directory_link):
    root = tmp_path / "game"
    data = root / "Data"
    data.mkdir(parents=True)
    asset = data / "asset.ide"
    asset.write_text("objs\nend\n", encoding="ascii")
    directory_link(root / "Alias", data)
    directory_link(tmp_path / "root-alias", root)
    assert resolve_ci(root, "alias/ASSET.IDE").samefile(asset)
    assert resolve_ci(tmp_path / "root-alias", "data/asset.ide").samefile(asset)


def test_path_helpers():
    assert split_dos_path(" DATA\\MAPS\\.\\x.ide ") == ["DATA", "MAPS", "x.ide"]
    assert split_dos_path("/abs") is None and split_dos_path("a/../b") is None
    assert canon_relpath("MODELS\\GTA3.IMG") == "models/gta3.img"


@pytest.mark.parametrize("dos", [
    "data\\C:\\Windows\\win.ini",      # drive in the middle: Path.joinpath would jump to C:
    "data\\C:Windows\\win.ini", "C:x.ide", "D:\\x", "\\\\srv\\share\\x", "//srv/share/x", "\\x",
    "models\\..\\..\\x", "data\\...\\x", "data\\. .\\x", "data\\..\\x",
    "data\\NUL", "data\\con.txt", "data\\COM1.img", "LPT9", "data\\CONIN$",
    "a\\b:c", "a\\b\"c", "a\\b*c", "a\\b?c", "a\\b<c", "a\\b|c", "a\\b\x00c", "a\\b\tc", "", "  ", ".",
])
def test_split_dos_path_rejects_escapes(dos):
    assert split_dos_path(dos) is None


@pytest.mark.parametrize("dos, parts", [
    ("MODELS\\GTA3.IMG", ["MODELS", "GTA3.IMG"]), ("models/gta3.img", ["models", "gta3.img"]),
    ("data\\consul.ide", ["data", "consul.ide"]), ("data\\nul_x.txd", ["data", "nul_x.txd"]),
    ("data\\x...y.ide", ["data", "x...y.ide"]), ('"DATA\\GTA.DAT"', ["DATA", "GTA.DAT"]),
])
def test_split_dos_path_accepts_plain_names(dos, parts):
    assert split_dos_path(dos) == parts


def test_resolve_ci_never_leaves_root(tmp_path):
    """Defect of the review: ``data\\C:\\Windows\\win.ini`` resolved to ``C:Windows/win.ini`` outside the root."""
    root = tmp_path / "root"
    (root / "data").mkdir(parents=True)
    (tmp_path / "secret.txt").write_text("s", encoding="latin-1")
    drive = tmp_path.drive or "C:"
    rest = str(tmp_path / "secret.txt")[len(drive):].lstrip("\\/")
    for dos in (f"data\\{drive}\\{rest}", f"data\\{drive}{rest}", f"data\\..\\..\\secret.txt",
                "data\\C:\\Windows\\win.ini", f"data\\...\\..\\secret.txt", "data\\NUL"):
        assert resolve_ci(root, dos) is None, dos


def test_read_text_latin1(tmp_path):
    p = tmp_path / "t.ide"
    p.write_bytes(b"objs\n1, caf\xe9, generic, 10, 0\nend\n")
    assert "caf\u00e9" in read_text(p)


# ----------------------------------------------------------------------------- ide
IDE = """# test
objs
615, veg_tree3, gta_tree_boak, 300, 2097152
320, airtrain_vlo, generic, 1, 2000, 0
700 sm_veg_tree4 gta_tree_bevhills 2 120 80 0
701,a,b,3,10,20,30,4
end
tobj
1001, nightlight, lights, 100, 0, 20, 6
1002, nl2, lights, 1, 50, 4, 22, 5
end
anim
1500, bigflag, flags, flag_anim, 120, 0
end
cars
400, landstal, landstal, car, LANDSTAL, LANDSTK, null, richfamily, 10, 7, 0, 0, 0.7, 0.7, -1
end
peds
7, male01, male01, CIVMALE, STAT_STREET_GUY, man, 1FF, 3, null, 9, 9, PED_TYPE_GEN, VOICE_GEN_ANDRE, VOICE_GEN_ANDRE
end
weap
321, gun_dildo1, gun_dildo1, null, 1, 50, 0
end
hier
384, clothes01, generic
385, clothes01, generic
end
txdp
csbravura, bravura
end
2dfx
1300, 0.0, 1.5, 2.0, 1, 2
end
path
group, 1
end
"""


def test_parse_ide_sections():
    defs, txdp, fx = parse_ide(IDE)
    by = {d.id: d for d in defs}
    assert [d.sec for d in defs].count("objs") == 4 and len(defs) == 12
    t = by[615]
    assert (t.sec, t.name, t.txd, t.draw, t.flags, t.line, t.extra) == ("objs", "veg_tree3", "gta_tree_boak", 300.0,
                                                                        2097152, 3, {})
    old = by[320]  # the 6-field line of default.ide
    assert (old.draw, old.flags, old.extra) == (2000.0, 0, {"meshes": 1})
    assert (by[700].draw, by[700].flags, by[700].extra) == (120.0, 0, {"meshes": 2, "draws": [120.0, 80.0]})
    assert by[701].extra["meshes"] == 3 and by[701].flags == 4
    assert (by[1001].time_on, by[1001].time_off, by[1001].flags) == (20, 6, 0)
    assert (by[1002].draw, by[1002].extra, by[1002].time_on) == (50.0, {"meshes": 1}, 22)
    assert (by[1500].anim, by[1500].draw) == ("flag_anim", 120.0)
    car = by[400]
    assert car.sec == "cars" and car.flags == 7 and car.extra["type"] == "car" and car.extra["freq"] == 10
    assert car.extra["comprules"] == "0" and car.extra["wheel_id"] == 0 and car.extra["wheel_scale_f"] == 0.7
    ped = by[7]
    assert ped.flags == 3 and ped.extra["cars_mask"] == "1FF" and ped.anim is None and ped.extra["pedtype"] == "CIVMALE"
    assert (by[321].anim, by[321].draw, by[321].extra) == ("null", 50.0, {"meshes": 1})
    assert [d.name for d in defs if d.sec == "hier"] == ["clothes01", "clothes01"]  # duplicate names are kept
    assert txdp == [("csbravura", "bravura")]
    assert fx == [{"line": 32, "id": 1300, "pos": (0.0, 1.5, 2.0), "fields": ["1", "2"]}]


def test_parse_ide_bad_lines():
    text = "objs\n1, ok, txd, 10, 0\nx, bad, txd, 10, 0\n2, short\nend\n"
    errs: list = []
    defs, _, _ = parse_ide(text, errors=errs)
    assert [d.id for d in defs] == [1] and [n for n, _ in errs] == [3, 4]
    with pytest.raises(FormatError) as ei:
        parse_ide(text, strict=True)
    assert ei.value.kind == "ide" and ei.value.offset == 3


def test_unknown_section_ignored():
    defs, _, _ = parse_ide("junk line\nmzonx\n5, a, b, 1, 0\nend\nobjs\n6, c, d, 1, 0\nend\n")
    assert [d.id for d in defs] == [6]


# ----------------------------------------------------------------------------- ipl
IPL = """inst
17613, lae2_roads89, 0, 2489.3, -1668.5, 12.3, 0, 0, 0, 1, 198
1226, lamp, 1280, 1, 2, 3, 0, 0, 0.7071068, 0.7071068, -1
1227, tilted, 0, 1, 2, 3, 0.5, 0, 0, 0.8660254, -1
end
cull
2500, -1700, 10, 1, 0, 0, 1, 0, 20, 0, 0
end
enex
2000, 1000, 10, 90, 2, 2, 8, 2010, 1010, 11, 270, 3, 4, "BURG", 0, 2, 0, 24
end
auzo
radio, 1, 0, 100, 200, 10, 110, 210, 20
end
path
0, -1
end
"""


def test_parse_ipl_text():
    insts, items = parse_ipl_text(IPL)
    assert [i.idx for i in insts] == [0, 1, 2]
    r = insts[0]
    assert (r.model_id, r.name, r.interior, r.pos, r.q, r.lod) == (17613, "lae2_roads89", 0, (2489.3, -1668.5, 12.3),
                                                                  (0.0, 0.0, 0.0, 1.0), 198)
    assert area(insts[1].interior) == 0 and iflags(insts[1].interior) == 5
    assert set(items) == {"cull", "enex", "auzo", "path"}
    assert items["enex"][0]["pos"] == (2000.0, 1000.0, 10.0) and items["enex"][0]["fields"][13] == '"BURG"'
    assert items["auzo"][0]["pos"] == (100.0, 200.0, 10.0)
    assert "pos" not in items["path"][0] and items["cull"][0]["line"] == 7


def test_quaternion_is_conjugated_for_world():
    """V9: the file quaternion is conjugated by the engine; +90 deg in the file is -90 deg in the world."""
    s = math.sqrt(0.5)
    q_file = (0.0, 0.0, s, s)
    assert world_quat(q_file) == (-0.0, -0.0, -s, s)
    assert rz_deg(q_file) == pytest.approx(-90.0)
    assert rz_deg((0.0, 0.0, -s, s)) == pytest.approx(90.0)
    assert rz_deg((0.0, 0.0, 0.0, 1.0)) == 0.0
    assert rz_deg((0.0, 0.0, 1.0, 0.0)) == pytest.approx(180.0)
    assert rz_deg((0.5, 0.0, 0.0, 0.8660254)) is None  # tilted -> full matrix, no single heading
    # dont_stream objects with qx, qy != 0 also use the full matrix
    assert rz_deg((0.01, 0.01, 0.0, 0.9999), interior=0x200) is None
    assert rz_deg((0.01, 0.01, 0.0, 0.9999), interior=0) is not None
    insts, _ = parse_ipl_text(IPL)
    assert insts[1].q == pytest.approx((0, 0, 0.7071068, 0.7071068))  # stored as in the file


def test_parse_ipl_binary(b):
    data = b.bnry([(2489.3, -1668.5, 12.3, 0, 0, 0, 1, 17613, 0, 198),
                   (1, 2, 3, 0, 0, 0.5, 0.8660254, 1226, 0x105, -1)],
                  [(1.0, 2.0, 3.0, 90.0, 400, -1, -1, 0, 0, 0, 0, 0)])
    insts, cars = parse_ipl_binary(data)
    assert len(insts) == 2 and insts[0].name is None
    i0 = insts[0]
    assert i0.model_id == 17613 and i0.lod == 198 and i0.pos == pytest.approx((2489.3, -1668.5, 12.3))
    assert area(insts[1].interior) == 5 and iflags(insts[1].interior) == 1
    assert cars == [{"x": 1.0, "y": 2.0, "z": 3.0, "angle": 90.0, "model_id": 400, "col1": -1, "col2": -1,
                     "flags": 0, "alarm": 0, "door_lock": 0, "min_delay": 0, "max_delay": 0}]


@pytest.mark.parametrize("data", [b"", b"bnry", b"xxxx" + b"\0" * 80])
def test_parse_ipl_binary_bad(data):
    with pytest.raises(FormatError):
        parse_ipl_binary(data)


def test_parse_ipl_binary_truncated_tables(b):
    data = b.bnry([(0, 0, 0, 0, 0, 0, 1, 1, 0, -1)] * 3)
    with pytest.raises(FormatError, match="inst table"):
        parse_ipl_binary(data[:-1])


def test_stream_base():
    assert stream_base("lae2_stream0") == "lae2"
    assert stream_base("LAe2_STREAM12.ipl") == "lae2"
    assert stream_base("barriers1") is None


def test_ipl_bad_line():
    errs: list = []
    insts, _ = parse_ipl_text("inst\n1, a, 0, 1, 2\n2, b, 0, 1, 2, 3, 0, 0, 0, 1\nend\n", errors=errs)
    assert [i.model_id for i in insts] == [2] and insts[0].lod == -1 and errs[0][0] == 2
    with pytest.raises(FormatError):
        parse_ipl_text("inst\n1, a, 0, x, 2, 3, 0, 0, 0, 1, -1\nend\n", strict=True)
