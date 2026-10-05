"""A synthetic world for ``satk map clean`` and ``satk ipl`` (no game files).

``data/maps/t/t.ipl`` (text, CRLF, comments, an ``enex`` section after ``inst``):

====  ======  ==================  =====  ===  =========================================
idx   model   position            area   lod  role
====  ======  ==================  =====  ===  =========================================
0     1001    100, 100, 5         0      -1   LOD of t#2 and t_stream0#0 (both inside)
1     1003    150, 100, 5         0      -1   LOD shared by t#3 (inside) and t#4 (outside)
2     1000    100, 100, 5         0      0    HD inside
3     1000    150, 100, 5         0      1    HD inside
4     1000    300, 100, 5         0      1    HD outside
5     1002    110, 110, 5         0      -1   tree inside
6     1002    112, 110, 5         0      -1   tree inside
7     1002    400, 400, 5         0      -1   tree outside
8     1002    120, 120, 1005      5      -1   tree inside the box but in interior 5
9     1005    130, 130, 5         0      -1   LOD inside, of t_stream0#1 (outside)
====  ======  ==================  =====  ===  =========================================

``t_stream0.ipl`` (``bnry`` in ``models/gta3.img``): #0 1000 @ (105, 100, 5) LOD -> t#0 (inside),
#1 1000 @ (500, 500, 5) LOD -> t#9 (outside), one car generator. Zone ``TESTZONE`` (label ``TESTZ``) = the box
``90,90,200,200``.
"""

from __future__ import annotations

import hashlib
import os
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

SECTOR = 2048
BOX = "90,90,200,200"
T_IPL = ("# synthetic map\r\ninst\r\n"
         "1001, lodbox, 0, 100, 100, 5, 0, 0, 0, 1, -1\r\n"
         "1003, lodshared, 0, 150, 100, 5, 0, 0, 0, 1, -1\r\n"
         "1000, box, 0, 100, 100, 5, 0, 0, 0, 1, 0\r\n"
         "1000, box, 0, 150, 100, 5, 0, 0, -0.7071068, 0.7071068, 1  # turned\r\n"
         "1000, box, 0, 300, 100, 5, 0, 0, 0, 1, 1\r\n"
         "1002 tree 0 110 110 5 0 0 0 1 -1\r\n"
         "1002, tree, 0, 112, 110, 5, 0, 0, 0, 1, -1\r\n"
         "1002, tree, 0, 400, 400, 5, 0, 0, 0, 1, -1\r\n"
         "1002, tree, 5, 120, 120, 1005, 0, 0, 0, 1, -1\r\n"
         "1005, lodbig, 0, 130, 130, 5, 0, 0, 0, 1, -1\r\n"
         "end\r\nenex\r\n101, 201, 10, 0, 1, 1, 0, 0, 0, 0, 0, 1, \"door\", 0, 2, 0, 24\r\nend\r\n")


def bnry(insts: list[tuple], cars: list[tuple] = ()) -> bytes:
    """Vanilla-layout ``bnry`` (size fields 0) padded to a sector, like the IMG entries of the game."""
    after = 0x4C + 40 * len(insts)
    hdr = struct.pack("<4s6I12I", b"bnry", len(insts), 0, 0, 0, len(cars), 0, 0x4C, 0, 0, 0, 0, 0, 0, 0,
                      after if cars else 0, 0, 0, 0)
    body = hdr + b"".join(struct.pack("<7f3i", *i) for i in insts) + b"".join(struct.pack("<4f8i", *c) for c in cars)
    return body + b"\0" * (-len(body) % SECTOR)


def img_v2(entries: list[tuple[str, bytes]]) -> bytes:
    n = len(entries)
    dir_sectors = -(-(8 + 32 * n) // SECTOR)
    head = b"VER2" + struct.pack("<I", n)
    data = b""
    off = dir_sectors
    for name, payload in entries:
        sectors = -(-len(payload) // SECTOR)
        head += struct.pack("<IHH24s", off, sectors, 0, name.encode())
        data += payload.ljust(sectors * SECTOR, b"\0")
        off += sectors
    return head.ljust(dir_sectors * SECTOR, b"\0") + data


STREAM = bnry([(105.0, 100.0, 5.0, 0.0, 0.0, 0.0, 1.0, 1000, 0, 0),
               (500.0, 500.0, 5.0, 0.0, 0.0, 0.0, 1.0, 1000, 0, 9)],
              [(160.0, 160.0, 5.0, 90.0, 400, -1, -1, 0, 0, 0, 0, 0)])


def _w(root: Path, rel: str, data: str | bytes) -> None:
    p = root.joinpath(*rel.split("/"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data.encode("latin-1") if isinstance(data, str) else data)


def make_world(root: Path) -> Path:
    _w(root, "data/default.dat", "IDE DATA\\VEHICLES.IDE\n")
    _w(root, "data/gta.dat", "IDE DATA\\MAPS\\T\\T.IDE\nIPL DATA\\MAP.ZON\nIPL DATA\\MAPS\\T\\T.IPL\n")
    _w(root, "data/vehicles.ide", "cars\n400, testcar, testcar, car, TESTCAR, TESTCAR, null, executive, 10, 0, 0, -1, "
                                  "0.7, 0.7, -1\nend\n")
    _w(root, "data/maps/t/t.ide", "objs\n1000, box, boxes, 150, 0\n1001, lodbox, boxes, 1000, 0\n"
                                  "1002, tree, boxes, 150, 0\n1003, lodshared, boxes, 1000, 0\n"
                                  "1005, lodbig, boxes, 1000, 0\nend\n")
    _w(root, "data/maps/t/t.ipl", T_IPL)
    _w(root, "data/map.zon", "zone\nTESTZONE, 0, 90, 90, -100, 200, 200, 900, 1, TESTZ\nend\n")
    _w(root, "models/gta3.img", img_v2([("t_stream0.ipl", STREAM)]))
    _w(root, "models/gta_int.img", img_v2([]))
    _w(root, "gta_sa.exe", b"MZ synthetic")
    files = sorted(p for p in root.rglob("*") if p.is_file())
    _w(root, "MANIFEST.sha256", "".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  "
                                        f"{os.path.relpath(p, root).replace('/', chr(92))}\n" for p in files))
    return root


@pytest.fixture
def world(satk_home, tmp_path):
    """The synthetic world indexed as profile ``vanilla`` (``open_index`` overridden)."""
    from satk.index.api import IndexDB, override_index
    from satk.index.build import build
    from satk.index.layers import HashCache

    root = make_world(tmp_path / "game")
    out = tmp_path / "idx" / "vanilla.sqlite"
    build("vanilla", jobs=1, out=out, root=root, dat_files=["data/default.dat", "data/gta.dat"], img_order="engine",
          vanilla_manifest=root / "MANIFEST.sha256", hashcache=HashCache(out.parent / "hc.sqlite"))
    db = IndexDB("vanilla", out)
    with override_index(db):
        yield SimpleNamespace(root=root, db=db, stream=STREAM, t_ipl=T_IPL.encode("latin-1"))
    db.close()


@pytest.fixture
def bnry_builder():
    return bnry
