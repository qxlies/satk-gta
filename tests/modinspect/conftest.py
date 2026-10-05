"""Synthetic game roots and mods for satk.modinspect (no game files; invented values).

``make_game(root)`` writes a tiny game that the inspector can read:

* ``data/default.dat`` (IDE vehicles.ide, peds.ide) and ``data/gta.dat`` (IMG, IDE/IPL of ``data/maps/test``);
* ``models/gta3.img`` (VER2): ``testcar.dff``, ``testcar.txd``, ``fastcar.dff``, ``box1.dff``, ``boxes.txd``;
  ``models/gta_int.img`` (VER2, empty);
* IDE: cars 400 ``testcar`` (handling ``TESTCAR``), 401 ``fastcar`` (``FASTCAR``), 402 ``testboat``
  (``TESTBOAT``); peds 7 ``male01``; objs 1000 ``box1`` (``boxes``);
* ``data/handling.cfg``: TESTCAR, FASTCAR, TESTBOAT + ``%`` boat line, ``^ 0`` anim line, ``;the end``;
* ``data/carcols.dat``: 3 colours, ``testcar`` and ``fastcar`` variations;
* ``data/object.dat`` (box1, ``*`` end), ``data/timecyc.dat`` (2 lines), ``data/maps/test.ipl``.
"""

from __future__ import annotations

import io
import struct
import zipfile
from pathlib import Path

import pytest

SECTOR = 2048


def car_line(name: str, mass: float = 1500.0, max_vel: float = 200.0, flags: str = "40000000") -> str:
    """A standard handling line with 35 values (invented numbers)."""
    vals = [f"{mass:.1f}", "3000.0", "2.0", "0.0", "0.0", "-0.1", "75", "0.75", "0.85", "0.5", "5",
            f"{max_vel:.1f}", "25.0", "10.0", "4", "P", "8.0", "0.5", "0", "30.0", "1.2", "0.1", "0.0", "0.3",
            "-0.15", "0.5", "0.3", "0.25", "0.5", "20000", flags, "0", "1", "1", "0"]
    return f"{name} " + " ".join(vals)


def boat_line(name: str, thrust: float = 0.6) -> str:
    vals = [f"{thrust:.2f}", "1.0", "0.2", "9.0", "0.8", "1.2", "4.0", "0.9", "0.99", "0.99", "0.9", "0.97",
            "0.97", "4.0"]
    return f"%\t{name}\t" + "\t".join(vals)


def anim_line(gid: int = 0) -> str:
    return "^ " + " ".join([str(gid)] + ["0"] * 20 + ["0.5"] * 13 + ["0"])


HANDLING = "\n".join([
    "; synthetic handling",
    car_line("TESTCAR"),
    car_line("FASTCAR", 1200.0, 250.0),
    car_line("TESTBOAT", 2000.0, 150.0),
    boat_line("TESTBOAT"),
    anim_line(0),
    ";the end",
]) + "\n"

VEHICLES_IDE = """cars
400, testcar, testcar, car, TESTCAR, TESTCAR, null, normal, 10, 0, 0, -1, 0.7, 0.7, 0
401, fastcar, fastcar, car, FASTCAR, FASTCAR, null, executive, 5, 0, 0, -1, 0.7, 0.7, 0
402, testboat, testboat, boat, TESTBOAT, TESTBOA, null, ignore, 10, 0, 0
end
"""
PEDS_IDE = """peds
7, male01, male01, CIVMALE, STAT_STREET_GUY, man, 1983, 0, null, 9, 9, PED_TYPE_GEN, VOICE_GEN_BMOST, VOICE_GEN_BMOST
end
"""
TEST_IDE = """objs
1000, box1, boxes, 100, 0
end
"""
CARCOLS = """# synthetic
col
0,0,0\t# black
245,245,245\t# white
42.119,161\t# blue (dot quirk)
end
car
testcar, 1,2, 2,1
fastcar, 0,1
end
car4
end
"""
OBJECT_DAT = "; synthetic\nbox1, 20.0, 20.0 0.99, 0.03, 50.0, 0.0, 2.5, 20, 2, 1, 0, 2\n* end\n"
TIMECYC = "// synthetic\n1 2 3\n4 5 6\n"
TEST_IPL = "inst\n1000, box1, 0, 10.0, 20.0, 5.0, 0, 0, 0, 1, -1\nend\n"


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


def rw_stub(kind: int = 0x10, tag: bytes = b"") -> bytes:
    """Bytes that only start like an RW chunk (not parsed by the inspector); ``tag`` makes them differ."""
    return struct.pack("<III", kind, 4 + len(tag), 0x1803FFFF) + bytes(4) + tag


def write(root: Path, rel: str, data: bytes | str) -> Path:
    p = root.joinpath(*rel.split("/"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data.encode("latin-1") if isinstance(data, str) else data)
    return p


def make_game(root: Path) -> Path:
    write(root, "gta_sa.exe", b"MZ")
    write(root, "data/default.dat", "IDE DATA\\VEHICLES.IDE\nIDE DATA\\PEDS.IDE\n")
    write(root, "data/gta.dat", "IMG MODELS\\GTA_INT.IMG\nIDE DATA\\MAPS\\TEST.IDE\nIPL DATA\\MAPS\\TEST.IPL\n")
    write(root, "data/vehicles.ide", VEHICLES_IDE)
    write(root, "data/peds.ide", PEDS_IDE)
    write(root, "data/maps/test.ide", TEST_IDE)
    write(root, "data/maps/test.ipl", TEST_IPL)
    write(root, "data/handling.cfg", HANDLING)
    write(root, "data/carcols.dat", CARCOLS)
    write(root, "data/object.dat", OBJECT_DAT)
    write(root, "data/timecyc.dat", TIMECYC)
    write(root, "models/gta3.img", img_v2([("testcar.dff", rw_stub()), ("testcar.txd", rw_stub(0x16)),
                                           ("fastcar.dff", rw_stub()), ("box1.dff", rw_stub()),
                                           ("boxes.txd", rw_stub(0x16))]))
    write(root, "models/gta_int.img", img_v2([]))
    return root


def make_mod(root: Path, files: dict[str, bytes | str]) -> Path:
    for rel, data in files.items():
        write(root, rel, data)
    return root


def zip_of(folder: Path, dest: Path, top: str | None = None) -> Path:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(folder.rglob("*")):
            if p.is_file():
                rel = p.relative_to(folder).as_posix()
                zf.writestr(f"{top}/{rel}" if top else rel, p.read_bytes())
    dest.write_bytes(buf.getvalue())
    return dest


@pytest.fixture
def games(satk_home: Path):
    """``(vanilla_root, installed_root)`` synthetic games in the isolated workspace (installed has modloader/)."""
    van = make_game(satk_home / "gta-sa-clean")
    inst = make_game(satk_home / "GTA San Andreas")
    (inst / "modloader").mkdir()
    return van, inst
