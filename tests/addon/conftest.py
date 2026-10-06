"""Synthetic game roots and RW files for satk.addon (no game files; invented values in the vanilla layout).

``make_game(root)`` writes a tiny game with every data file ``satk.addon`` reads:

* ``data/default.dat`` (DEFAULT.IDE, VEHICLES.IDE, PEDS.IDE) and ``data/gta.dat`` (IMG, maps/test.ide);
* IDE: cars 400 ``testcar`` (handling TESTCAR, GXT TESTCAR), 401 ``tbike`` (bike, TBIKE), 402 ``tboat`` (boat,
  TBOAT), 403 ``tplane`` (plane, TPLANE); peds 7 ``male01``; weap 331 ``brass``, 346 ``colt45``, 345 ``missile``;
  objs 1000 ``barrel`` (TXD ``dynbarrels``);
* ``handling.cfg`` (tabs and spaces like the game's, ``!`` ``%`` ``$`` ``^`` lines, ``;the end``), ``carcols.dat``
  (``car`` and ``car4``), ``carmods.dat``, ``cargrp.dat``, ``pedstats.dat``, ``weapon.dat`` (melee line with the
  0xA3 byte, PISTOL with four skill lines, ROCKET, an aim line, ``ENDWEAPONDATA``), ``object.dat``;
* ``text/american.gxt`` whose MAIN table has the key ``TBIKE``; ``models/gta3.img`` with ``testcar.dff``,
  ``testcar.txd`` and ``taken.txd``; ``models/generic/vehicle.txd``.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path
from types import SimpleNamespace

import pytest

SA = 0x1803FFFF
SECTOR = 2048

VEHICLES_IDE = """# synthetic
cars
400,\ttestcar,\ttestcar,\tcar,\t\tTESTCAR,\tTESTCAR,\tnull,\texecutive,\t5,\t0,\t0,\t\t-1, 0.7, 0.7,\t\t0
401,\ttbike,\t\ttbike,\t\tbike,\t\tTBIKE,\t\tTBIKE,\t\tbikes,\tmotorbike,\t10,\t0,\t0,\t\t16, 0.67, 0.67,\t\t-1
402, \ttboat, \ttboat, \tboat,\t\tTBOAT,\t\tTBOAT, \tnull,\tignore,\t\t10,\t0, \t0,
403, \ttplane, \ttplane, \tplane, \tTPLANE, \tTPLANE,\t\ttplane,\tignore,\t\t5,\t0,\t0,\t\t-1, 0.62, 0.62,\t\t-1
end
"""
PEDS_IDE = """peds
0, null, generic, PLAYER1, STAT_PLAYER, player, 0, 0, null, 9,9\t, PED_TYPE_PLAYER, VOICE_PLY_CR, VOICE_PLY_CR
7, male01, male01, CIVMALE, STAT_SENSIBLE_GUY, man, 0, 0, man, 1,4, PED_TYPE_GEN, VOICE_GEN_MALE01, VOICE_GEN_MALE01 \t
end
"""
DEFAULT_IDE = """weap
331, brass, brass, null, 1, 50, 0
345, missile, missile, null, 1, 100, 0
346, colt45, colt45, colt45, 1, 30, 0
end
"""
TEST_IDE = "objs\n1000, barrel, dynbarrels, 40, 2162816\nend\n"


def car(hid: str, mass: str = "1400.0", vmax: str = "240.0", mflags: str = "40002004", hflags: str = "C04000") -> str:
    return (f"{hid}     {mass}    2725.3   1.5    0.0 0.0 -0.25 70  0.70 0.8  0.50 \t5 {vmax} 30.0 10.0 4 P \t"
            f"11.0  0.51 0 30.0  \t1.2  0.19  0.0   0.25 -0.10 0.5  0.4\t\t0.37 0.72 95000 \t{mflags}\t{hflags}\t\t1  1\t1")


HANDLING = "\r\n".join([
    "; synthetic handling.cfg",
    ";",
    car("TESTCAR"),
    car("TBIKE", "500.0", "190.0", "1002000", "0"),
    car("TBOAT", "2200.0", "160.0", "8000400", "0"),
    car("TPLANE", "5000.0", "200.0", "4000000", "0"),
    "%\tTBOAT\t\t0.50\t1.1 \t0.2\t\t9.0\t\t0.80\t1.2\t\t4.0\t\t\t0.90\t0.998\t0.999\t\t0.89\t0.975\t0.97\t4.0",
    "!\tTBIKE\t\t0.35\t0.15\t0.34\t0.10\t45.0\t38.0\t0.93\t0.70\t0.5\t\t0.1\t\t35.0\t-40.0\t-0.009\t0.7\t\t0.6",
    "$\tTPLANE\t\t0.25\t0.6\t\t-0.002\t  0.05\t 0.10\t0.009\t 5.0\t0.009\t5.0\t\t0.4\t\t0.008\t0.2\t 1.0  0.1s\t\t0.989"
    "\t0.880\t0.880\t0.998\t\t0.0\t\t0.0\t\t5.0",
    "^\t0\t\t0\t\t0\t\t" + "\t\t".join(["0"] * 18) + "\t\t" + "\t\t".join(["0.5"] * 13) + "\t\t0",
    ";the end",
    "",
])
CARCOLS = """# synthetic\r
col\r
0,0,0\t\t# 0 black\r
245,245,245\t# 1 white\r
42,119,161\t# 2 blue\r
end\r
car\r
testcar, 1,0, 2,1\r
tbike, 0,1\r
end\r
car4\r
tboat, 0,0,0,1, 1,2,1,1 \r
end\r
"""
CARMODS = "link\r\nbntl_b_ov, bntr_b_ov\r\nend\r\n\r\nmods\r\ntestcar, nto_b_l, nto_b_s, nto_b_tw\r\nend\r\n" \
          "wheel\r\n0, wheel_gn1, wheel_gn2\r\nend\r\n"
CARGRP = "# synthetic cargrp\r\n\r\ntestcar, tbike\t# POPCYCLE_GROUP_WORKERS\r\ntbike\t# POPCYCLE_GROUP_BUSINESS\r\n" \
         "tboat, tplane\t\t# Boats\r\n"
PEDSTATS = "# synthetic\r\nSTAT_PLAYER          0.0   9.0    50  50  50  50  3.0   0.4   40\t2\r\n" \
           "STAT_SENSIBLE_GUY    17.0  7.5    65  30  70  20  0.7   1.1   40 \t4\r\n"
MELEE = "£"
WEAPON = "\r\n".join([
    "# synthetic weapon.dat",
    f"{MELEE} UNARMED\t\tMELEE\t10.0  1.6\t-1\t-1\t\t0\t\tUNARMED\t\t4\t\t\t1\t\tnull",
    f"{MELEE} BRASSKNUCKLE\tMELEE\t10.0  1.6\t331\t-1\t\t0\t\tUNARMED\t\t4\t\t\t1\t\tnull",
    "$ ROCKET\t\t\tPROJECTILE\t30.0 40.0\t345\t-1\t\t8\tnull\t\t1\t 75\t\t0.0   0.0   0.0\t\t1  0\t1.0\t 1.0\t"
    " 0 99  6\t 0 99 10  99\t100\t\t\t0.25 -1.0 800.0\t 1.0",
    "$ PISTOL\t\t\tINSTANT_HIT\t25.0 30.0\t346\t-1\t\t2\tcolt45\t\t17\t 25\t\t0.25  0.05  0.09    0  0\t0.75 1.0 "
    "\t 6 17  6     6 16  6  99\t3033",
    "$ PISTOL\t\t\tINSTANT_HIT\t30.0 35.0\t346\t-1\t\t2\tcolt45\t\t17\t 25\t\t0.25  0.05  0.09\t1  40\t1.0  1.0 "
    "\t 6 15  6     6 15  6  99\t3033",
    "$ PISTOL\t\t\tINSTANT_HIT\t35.0 35.0\t346\t-1\t\t2\tcolt45pro\t34\t 25\t\t0.25  0.05  0.09\t2  999\t1.25 1.0 "
    "\t 6 18  6     6 17  6  99\t3833",
    "$ PISTOL\t\t\tINSTANT_HIT\t30.0 35.0\t346\t-1\t\t2\tcolt_cop\t17\t 25\t\t0.25  0.05  0.09\t3  5000\t1.0  1.0 "
    "\t 6 15  6     6 15  6  99\t7031",
    "#$ OLD\t\t\tINSTANT_HIT\t1.0 1.0\t1\t-1\t\t2\tnull\t1\t1\t0 0 0\t1 0\t1 1\t1 1 1 1 1 1 1\t0",
    "% colt45\t\t\t0.2\t\t0.6\t\t\t0.1\t\t0.1\t\t254\t633\t\t254\t633",
    "",
    "ENDWEAPONDATA",
    "",
])
OBJECT_DAT = "; synthetic\r\nbarrel,\t\t100.0,\t\t100.0  \t\t0.99,\t\t0.05,\t\t50.0,\t\t0.0,    10.0,\t20,\t0,\t1,\t1,\t2," \
             "\t0.0, 0.0, 0.0,\t\texplosion_medium\r\n*** end\r\nlate,\t1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0, 0, 0, 0, 0\r\n"


# ----------------------------------------------------------------------------- RW builders


def chunk(t: int, payload: bytes) -> bytes:
    return struct.pack("<III", t, len(payload), SA) + payload


def string(s: str) -> bytes:
    raw = s.encode("latin-1") + b"\0"
    return chunk(0x02, raw + b"\0" * (-len(raw) % 4))


def native(name: str) -> bytes:
    st = struct.pack("<IBBH32s32sII", 9, 6, 0x11, 0, name.encode(), b"", 0x200, struct.unpack("<I", b"DXT1")[0])
    st += struct.pack("<HHBBBB", 4, 4, 16, 1, 4, 8) + struct.pack("<I", 8) + bytes(range(8))
    return chunk(0x15, chunk(0x01, st) + chunk(0x03, b""))


def txd(names: list[str]) -> bytes:
    body = chunk(0x01, struct.pack("<HH", len(names), 2)) + b"".join(native(n) for n in names) + chunk(0x03, b"")
    return chunk(0x16, body)


def col3(name: str) -> bytes:
    h = struct.pack("<10f3HBxI6I", -1, -1, -1, 1, 1, 1, 0, 0, 0, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    h += struct.pack("<3I", 0, 0, 0)
    return b"COL3" + struct.pack("<I22sH", 24 + len(h), name.encode(), 0) + h


def dff(tex: str = "body", *, embedded_col: bool = False, skin: bool = False) -> bytes:
    """One clump, frame, atomic and geometry (3 vertices, 1 triangle, one textured material)."""
    pos = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)]
    flags = 0x02 | 0x20 | 0x04
    st = struct.pack("<IiiI", flags | (1 << 16), 1, 3, 1)
    st += b"".join(struct.pack("<2f", i * 0.5, 0.0) for i in range(3))
    st += struct.pack("<4H", 1, 0, 0, 2)
    st += struct.pack("<4fII", 0.0, 0.0, 0.0, 1.5, 1, 0)
    st += b"".join(struct.pack("<3f", *p) for p in pos)
    mat_st = struct.pack("<I4BII3f", 0, 255, 255, 255, 255, 0, 1, 1.0, 1.0, 1.0)
    tex_chunk = chunk(0x06, chunk(0x01, struct.pack("<HH", 0x1106, 0)) + string(tex) + string("") + chunk(0x03, b""))
    mat = chunk(0x07, chunk(0x01, mat_st) + tex_chunk + chunk(0x03, b""))
    ml = chunk(0x01, struct.pack("<Ii", 1, -1)) + mat
    gext = chunk(0x116, b"\0" * 4) if skin else b""
    geo = chunk(0x0F, chunk(0x01, st) + chunk(0x08, ml) + chunk(0x03, gext))
    cl_st = chunk(0x01, struct.pack("<III", 1, 0, 0))
    fl = struct.pack("<I", 1) + struct.pack("<12fiI", 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, -1, 0)
    frames = chunk(0x0E, chunk(0x01, fl) + chunk(0x03, chunk(0x253F2FE, b"root")))
    geos = chunk(0x1A, chunk(0x01, struct.pack("<I", 1)) + geo)
    atomic = chunk(0x14, chunk(0x01, struct.pack("<4I", 0, 0, 5, 0)) + chunk(0x03, b""))
    ext = chunk(0x253F2FA, col3("x")) if embedded_col else b""
    return chunk(0x10, cl_st + frames + geos + atomic + chunk(0x03, ext))


def img_v2(entries: list[tuple[str, bytes]]) -> bytes:
    n = len(entries)
    dir_sectors = max(1, -(-(8 + 32 * n) // SECTOR))
    out = b"VER2" + struct.pack("<I", n)
    data = b""
    off = dir_sectors
    for name, payload in entries:
        sectors = max(1, -(-len(payload) // SECTOR))
        out += struct.pack("<IHH24s", off, sectors, 0, name.encode())
        data += payload.ljust(sectors * SECTOR, b"\0")
        off += sectors
    return out.ljust(dir_sectors * SECTOR, b"\0") + data


def gxt(keys: dict[str, str]) -> bytes:
    """An 8-bit SA GXT with one MAIN table."""
    tdat = b""
    tkey = b""
    for k, v in keys.items():
        h = zlib.crc32(k.upper().encode()) ^ 0xFFFFFFFF
        tkey += struct.pack("<II", len(tdat), h)
        tdat += v.encode("latin-1") + b"\0"
    tabl = b"TABL" + struct.pack("<I", 12)
    main_off = 4 + len(tabl) + 12
    tabl += b"MAIN".ljust(8, b"\0") + struct.pack("<I", main_off)
    return struct.pack("<HH", 4, 8) + tabl + b"TKEY" + struct.pack("<I", len(tkey)) + tkey + b"TDAT" + \
        struct.pack("<I", len(tdat)) + tdat


def write(root: Path, rel: str, data: bytes | str) -> Path:
    p = root.joinpath(*rel.split("/"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data.encode("latin-1") if isinstance(data, str) else data)
    return p


def make_game(root: Path) -> Path:
    write(root, "gta_sa.exe", b"MZ synthetic")
    write(root, "data/default.dat", "IDE DATA\\DEFAULT.IDE\r\nIDE DATA\\VEHICLES.IDE\r\nIDE DATA\\PEDS.IDE\r\n")
    write(root, "data/gta.dat", "IMG MODELS\\GTA_INT.IMG\r\nIDE DATA\\MAPS\\TEST.IDE\r\n")
    write(root, "data/vehicles.ide", VEHICLES_IDE)
    write(root, "data/peds.ide", PEDS_IDE)
    write(root, "data/default.ide", DEFAULT_IDE)
    write(root, "data/maps/test.ide", TEST_IDE)
    write(root, "data/handling.cfg", HANDLING)
    write(root, "data/carcols.dat", CARCOLS)
    write(root, "data/carmods.dat", CARMODS)
    write(root, "data/cargrp.dat", CARGRP)
    write(root, "data/pedstats.dat", PEDSTATS)
    write(root, "data/weapon.dat", WEAPON)
    write(root, "data/object.dat", OBJECT_DAT)
    write(root, "text/american.gxt", gxt({"TBIKE": "Bike", "INTRO": "Hello"}))
    write(root, "models/gta3.img", img_v2([("testcar.dff", dff("body", embedded_col=True)),
                                           ("testcar.txd", txd(["body"])), ("taken.txd", txd(["x"]))]))
    write(root, "models/gta_int.img", img_v2([]))
    write(root, "models/generic/vehicle.txd", txd(["vehiclegrunge256"]))
    return root


@pytest.fixture
def game(satk_home: Path):
    """Synthetic ``vanilla`` (``<ws>/gta-sa-clean``) and ``installed`` (``<ws>/GTA San Andreas``) roots + input files."""
    van = make_game(satk_home / "gta-sa-clean")
    inst = make_game(satk_home / "GTA San Andreas")
    (inst / "modloader").mkdir()
    inp = satk_home / "inputs"
    files = {
        "car.dff": dff("body", embedded_col=True), "car.txd": txd(["body"]),
        "nocol.dff": dff("body"), "ped.dff": dff("skin", skin=True), "ped.txd": txd(["skin"]),
        "gun.dff": dff("gun"), "gun.txd": txd(["gun"]), "box.dff": dff("dynbarrel"),
        "one.col": col3("whatever"), "multi.col": col3("other") + col3("barrel") + col3("third"),
        "bad.dff": b"not a dff at all", "missing_tex.dff": dff("nosuchtexture"),
    }
    for name, data in files.items():
        write(inp, name, data)
    return SimpleNamespace(vanilla=van, installed=inst, inp=inp, work=satk_home / "work")


@pytest.fixture
def builders():
    return SimpleNamespace(dff=dff, txd=txd, col3=col3, img_v2=img_v2, gxt=gxt, write=write, make_game=make_game)
