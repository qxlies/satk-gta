"""Synthetic GTA:SA game roots for index-builder tests (no game files; SPEC §5.1 rule 5).

``make_game(root)`` writes a tiny but structurally real game: DAT files, IDE/IPL/ZON text files,
``models/gta3.img`` + ``models/gta_int.img`` (VER2) holding TXD/DFF/COL/binary IPL blobs, a loose
``models/generic/vehicle.txd`` and ``MANIFEST.sha256``. The RenderWare/COL/IPL bytes are built
here from the format specs (RW chunks, D3D9 texture natives, COL3, ``bnry``).

Contents (numbers the tests rely on):

* models: 400 ``testcar`` (cars, TXD ``testcar`` -> implicit ``vehicle``, embedded COL3),
  1000 ``box1`` (objs, TXD ``boxes``, COL ``box1`` in ``test.col``; uses ``boxtex`` + the missing
  ``nosuchtex``), 1001 ``lodbox1`` (objs, LOD model of box1, no COL -> DFF sphere);
* placements: ``test.ipl`` #0 lodbox1 @ (100, 200, 10), #1 box1 @ (100, 200, 10) rotated +90 deg
  about Z with LOD #0, an ``enex`` item; ``test_stream0.ipl`` (bnry in gta3.img) #0 box1 @ (150, 250, 5)
  with LOD -> ``test.ipl#0`` and one car generator; ``orphan_stream0.ipl`` (no text parent);
* ``models/gta_int.img/boxes.txd`` is shadowed by ``models/gta3.img/boxes.txd``;
* zones: ``zone`` section of ``data/map.zon`` (TESTZONE around the boxes);
* data files (schema v3, :data:`DATA_FILES`): ``water.dat`` 1 quad + 1 triangle, ``water1.dat`` 1 quad,
  ``timecyc.dat`` 23 x 8 lines (:func:`timecyc_text`), ``carcols.dat`` 3 colours + ``testcar`` 2 variations,
  ``handling.cfg`` ``TESTCAR`` (then ``;the end``), ``ped.dat`` CIVMALE/COP, ``object.dat`` ``box1`` (+ ``lodbox1``
  after the ``*`` terminator);
* layers: every file is in MANIFEST.sha256 (vanilla) except ``models/extra.txd`` (modded) and
  ``SAMP/samp.ide`` (rule SAMP/** -> samp, not loaded).
"""

from __future__ import annotations

import hashlib
import math
import os
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

LIBID = 0x1803FFFF  # RW 3.6.0.3 (SA PC)
SECTOR = 2048


# --------------------------------------------------------------------------- RW chunks


def chunk(ctype: int, payload: bytes, libid: int = LIBID) -> bytes:
    return struct.pack("<III", ctype, len(payload), libid) + payload


def rw_string(s: str) -> bytes:
    b = s.encode("latin-1") + b"\0"
    b += b"\0" * (-len(b) % 4)
    return chunk(0x02, b)


def texture_native(name: str, w: int = 4, h: int = 4, seed: int = 0) -> bytes:
    data = bytes((seed * 31 + i * 7) & 0xFF for i in range(max(1, w // 4) * max(1, h // 4) * 8))
    hdr = struct.pack("<IBBH32s32sII", 9, 0x06, 0x11, 0, name.encode(), b"", 0x200, int.from_bytes(b"DXT1", "little"))
    hdr += struct.pack("<HHBBBB", w, h, 16, 1, 4, 8)
    body = hdr + struct.pack("<I", len(data)) + data
    return chunk(0x15, chunk(0x01, body) + chunk(0x03, b""))


def txd(textures: list[tuple[str, int]]) -> bytes:
    kids = chunk(0x01, struct.pack("<HH", len(textures), 2))
    for name, seed in textures:
        kids += texture_native(name, seed=seed)
    return chunk(0x16, kids + chunk(0x03, b""))


def col3(name: str, bmin, bmax) -> bytes:
    cx, cy, cz = ((a + b) / 2 for a, b in zip(bmin, bmax))
    r = math.dist(bmin, bmax) / 2
    body = name.encode()[:21].ljust(22, b"\0") + struct.pack("<H", 0)
    body += struct.pack("<10f", *bmin, *bmax, cx, cy, cz, r)
    body += struct.pack("<HHHBBI6I", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    body += struct.pack("<3I", 0, 0, 0)
    return b"COL3" + struct.pack("<I", len(body)) + body


def dff(textures: list[str], verts: list[tuple[float, float, float]], tris: list[tuple[int, int, int]],
        embedded_col: bytes | None = None, frame_name: str = "root") -> bytes:
    nv, nt = len(verts), len(tris)
    fmt = 0x02 | 0x04 | (1 << 16)  # positions, textured, 1 UV set
    g = struct.pack("<IIII", fmt, nt, nv, 1)
    g += b"\0" * (8 * nv)  # UVs
    for i, (a, b, c) in enumerate(tris):
        g += struct.pack("<HHHH", b, a, i % max(1, len(textures)), c)
    xs = [v[0] for v in verts]
    ys = [v[1] for v in verts]
    zs = [v[2] for v in verts]
    cen = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, (min(zs) + max(zs)) / 2)
    rad = max(math.dist(cen, v) for v in verts)
    g += struct.pack("<4fII", *cen, rad, 1, 0)
    for v in verts:
        g += struct.pack("<3f", *v)
    mats = b""
    for t in textures:
        tex = chunk(0x06, chunk(0x01, struct.pack("<I", 0x1106)) + rw_string(t) + rw_string("") + chunk(0x03, b""))
        mats += chunk(0x07, chunk(0x01, struct.pack("<I4BII3f", 0, 255, 255, 255, 255, 0, 1, 1.0, 1.0, 1.0)) + tex
                      + chunk(0x03, b""))
    matlist = chunk(0x08, chunk(0x01, struct.pack(f"<I{len(textures)}i", len(textures), *([-1] * len(textures))))
                    + mats)
    idx = []
    for a, b, c in tris:
        idx += [a, b, c]
    binmesh = struct.pack("<III", 0, 1, len(idx)) + struct.pack("<II", len(idx), 0) + struct.pack(f"<{len(idx)}I", *idx)
    geom = chunk(0x0F, chunk(0x01, g) + matlist + chunk(0x03, chunk(0x50E, binmesh)))
    frame = struct.pack("<9f3fiI", 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, -1, 0)
    flist = chunk(0x0E, chunk(0x01, struct.pack("<I", 1) + frame) + chunk(0x03, chunk(0x253F2FE, frame_name.encode())))
    glist = chunk(0x1A, chunk(0x01, struct.pack("<I", 1)) + geom)
    atomic = chunk(0x14, chunk(0x01, struct.pack("<4I", 0, 0, 5, 0)) + chunk(0x03, b""))
    ext = chunk(0x03, chunk(0x253F2FA, embedded_col) if embedded_col else b"")
    return chunk(0x10, chunk(0x01, struct.pack("<III", 1, 0, 0)) + flist + glist + atomic + ext)


BOX_VERTS = [(-1, -2, 0), (1, -2, 0), (1, 2, 0), (-1, 2, 1)]
BOX_TRIS = [(0, 1, 2), (0, 2, 3)]


def bnry(insts: list[tuple], cars: list[tuple] = ()) -> bytes:
    """insts: (x, y, z, qx, qy, qz, qw, model, interior, lod); cars: (x, y, z, angle, model, ...8 ints)."""
    inst_off = 0x4C
    cars_off = inst_off + 40 * len(insts)
    hdr = struct.pack("<4s6I12I", b"bnry", len(insts), 0, 0, 0, len(cars), 0,
                      inst_off, 40 * len(insts), 0, 0, 0, 0, 0, 0, cars_off, 48 * len(cars), 0, 0)
    body = b"".join(struct.pack("<7f3i", *i) for i in insts)
    body += b"".join(struct.pack("<4f8i", *c) for c in cars)
    return hdr + body


def img_v2(entries: list[tuple[str, bytes]]) -> bytes:
    n = len(entries)
    dir_sectors = -(-(8 + 32 * n) // SECTOR)
    out_dir = b"VER2" + struct.pack("<I", n)
    data = b""
    off = dir_sectors
    for name, payload in entries:
        sectors = -(-len(payload) // SECTOR)
        out_dir += struct.pack("<IHH24s", off, sectors, 0, name.encode())
        data += payload.ljust(sectors * SECTOR, b"\0")
        off += sectors
    return out_dir.ljust(dir_sectors * SECTOR, b"\0") + data


def _w(root: Path, rel: str, data: bytes | str) -> Path:
    p = root.joinpath(*rel.split("/"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data.encode("latin-1") if isinstance(data, str) else data)
    return p


TCYC_LINE = ("{a} {a} {a}\t210 194 182\t255 255 255\t68 117 210\t36 117 199\t255 255 255\t255 255 255\t1.10 1.00 0.00 "
             "236 0 190\t{far:.2f} 10.00 0.00 44 34 23\t145 164 183\t90 170 170 240\t255 66 66 48\t255 166 129 60\t"
             "25 180 0")


def timecyc_text(weathers: int = 23, hours: int = 8, short: tuple[int, int] | None = None) -> str:
    """A synthetic ``timecyc.dat``: ``weathers`` blocks (``//////////// W<n>`` headers) x ``hours`` lines.

    Line values: ambient = weather number, far clip = 100 * (hour index + 1). ``short=(w, h)`` makes that
    line start with a lone ``255`` like vanilla line 320.
    """
    out = ["//////////// FIRST_W", "//Amb Amb_Obj Dir ..."]
    for w in range(weathers):
        if w:
            out += ["//", f"//////////// W{w}"]
        for h in range(hours):
            out.append(f"//{h}")
            line = TCYC_LINE.format(a=w, far=100.0 * (h + 1))
            if short == (w, h):
                line = "255\t" + line.split("\t", 1)[1]
            out.append(line)
    return "\r\n".join(out) + "\r\n//\r\n"


DATA_FILES = {
    "data/water.dat": "processed\n"
                      "100.0 200.0 0.0 0.0 0.0 0.05 0.1   140.0 200.0 0.0 0.0 0.0 0.05 0.1   "
                      "100.0 240.0 0.0 0.0 0.0 0.05 0.1   140.0 240.0 0.5 0.0 0.0 0.05 0.1   1\n"
                      "0 0 1.5 1 1 0.4 0.4   10 0 1.5 1 1 0.4 0.4   0 10 1.5 1 1 0.4 0.4   3\n",
    "data/water1.dat": "processed\n"
                       "-10 -10 2 0 0 0 0   10 -10 2 0 0 0 0   -10 10 2 0 0 0 0   10 10 2 0 0 0 0\n",
    "data/carcols.dat": "# synthetic\ncol\n0,0,0\t\t# 0 black\t\tblack\n245,245,245\t# 1 white\t\twhite\n"
                        "42,119,161\t# 2 police car blue\tblue\nend\ncar\ntestcar, 1,2, 2,1\nend\ncar4\nend\n",
    "data/handling.cfg": "; synthetic\n"
                         "TESTCAR 1500.0 3900.0 2.1 0.0 0.0 -0.12 80 0.72 0.88 0.52 5 200.0 28.0 6.0 R P 11.0 0.46 0 30.0 "
                         "1.25 0.11 0.0 0.27 -0.23 0.5 0.35 0.26 0.55 35000 2800 10200000 1 1 0\n"
                         "^ 0 0 0\n;the end\nIGNORED 1 2 3\n",
    "data/ped.dat": "# synthetic\nCIVMALE\n    Respect CIVMALE\n    Hate COP\nCOP\n    Respect COP\n",
    "data/object.dat": "; synthetic\nbox1, 20.0, 20.0 0.99, 0.03, 50.0, 0.0, 2.5, 20, 2, 1, 0, 2, 0.0, 0.0, 0.0, "
                       "explosion_crate\n* end\nlodbox1, 1.0, 1.0 0.99, 0.03, 50.0, 0.0, 2.5, 0, 0, 0, 0, 0\n",
}


def quat_z(deg: float) -> tuple[float, float, float, float]:
    """IPL quaternion (conjugate!) for a WORLD rotation of ``deg`` about Z."""
    h = math.radians(deg) / 2
    return (0.0, 0.0, -math.sin(h), math.cos(h))


def make_game(root: Path, *, variant: int = 0) -> Path:
    """Write the synthetic game into ``root`` (see module docstring). ``variant`` changes one texture."""
    root.mkdir(parents=True, exist_ok=True)
    _w(root, "data/default.dat", "# synthetic\nIDE DATA\\DEFAULT.IDE\nIDE DATA\\VEHICLES.IDE\n")
    _w(root, "data/gta.dat", "IDE DATA\\MAPS\\TEST\\TEST.IDE\nIPL DATA\\MAP.ZON\nIPL DATA\\MAPS\\TEST\\TEST.IPL\n"
                             "SPLASH loadsc1\n")
    _w(root, "data/default.ide", "objs\n# nothing\nend\nhier\n300, cutobj01, generic\nend\n")
    _w(root, "data/vehicles.ide", "cars\n400, testcar, testcar, car, TESTCAR, TESTCAR, null, executive, 10, 0, 0, "
                                  "-1, 0.7, 0.7, -1\nend\n")
    _w(root, "data/maps/test/test.ide", "objs\n1000, box1, boxes, 150, 0\n1001, lodbox1, boxes, 1000, 0\nend\n"
                                        "txdp\ntestcarparts, testcar\nend\n")
    s45 = quat_z(90)
    _w(root, "data/maps/test/test.ipl",
       "inst\n1001, lodbox1, 0, 100, 200, 10, 0, 0, 0, 1, -1\n"
       f"1000, box1, 0, 100, 200, 10, {s45[0]}, {s45[1]}, {s45[2]}, {s45[3]}, 0\nend\n"
       "enex\n101, 201, 10, 0, 1, 1, 0, 0, 0, 0, 0, 1, \"door\", 0, 2, 0, 24\nend\n"
       "path\nped, 1\n0, -1, 0, 0, 0, 0, 0\nend\n")
    _w(root, "data/map.zon", "zone\nTESTZONE, 0, 50, 150, -10, 200, 300, 100, 1, TESTZ\nend\n")
    for rel, text in DATA_FILES.items():  # schema v3 data files (water, carcols, handling, ped.dat, object.dat)
        _w(root, rel, text)
    _w(root, "data/timecyc.dat", timecyc_text())
    seed = 7 + variant
    gta3 = img_v2([
        ("testcar.dff", dff(["carbody", "vehiclegeneric256"], BOX_VERTS, BOX_TRIS,
                            embedded_col=col3("testcar", (-1, -2, -0.5), (1, 2, 1)))),
        ("testcar.txd", txd([("carbody", seed)])),
        ("box1.dff", dff(["boxtex", "nosuchtex"], BOX_VERTS, BOX_TRIS)),
        ("lodbox1.dff", dff(["boxtex"], [(-5, -5, 0), (5, -5, 0), (5, 5, 0), (-5, 5, 2)], BOX_TRIS)),
        ("boxes.txd", txd([("boxtex", 3), ("grove_tag", 4)])),
        ("test.col", col3("box1", (-1, -2, 0), (1, 2, 1))),
        ("test_stream0.ipl", bnry([(150, 250, 5, 0, 0, 0, 1, 1000, 0, 0)],
                                  [(160, 260, 5, 90.0, 400, -1, -1, 0, 0, 0, 0, 0)])),
        ("orphan_stream0.ipl", bnry([(500, 500, 5, 0, 0, 0, 1, 1000, 0, -1)])),
    ])
    _w(root, "models/gta3.img", gta3)
    _w(root, "models/gta_int.img", img_v2([("boxes.txd", txd([("boxtex", 9)])),
                                           ("intbox.dff", dff(["boxtex"], BOX_VERTS, BOX_TRIS))]))
    _w(root, "models/generic/vehicle.txd", txd([("vehiclegeneric256", 5)]))
    _w(root, "gta_sa.exe", b"MZ synthetic")
    # manifest of the "clean" files, then the extras that are not in it
    files = sorted(p for p in root.rglob("*") if p.is_file() and p.name != "MANIFEST.sha256")
    lines = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {os.path.relpath(p, root).replace('/', chr(92))}"
             for p in files]
    _w(root, "MANIFEST.sha256", "\n".join(lines) + "\n")
    _w(root, "models/extra.txd", txd([("extratex", 1)]))
    _w(root, "SAMP/samp.ide", "objs\n19000, sampobj, samp, 100, 0\nend\n")
    return root


@pytest.fixture
def make_game_fn():
    """The :func:`make_game` writer itself (test modules cannot import conftest)."""
    return make_game


@pytest.fixture
def asset_builders():
    """Synthetic writers for tests that add archives or loose assets to a game root."""
    return SimpleNamespace(img_v2=img_v2, txd=txd, col3=col3)


@pytest.fixture
def data_builders():
    """The synthetic data-file texts (``DATA_FILES``) and the ``timecyc_text`` generator."""
    return SimpleNamespace(files=dict(DATA_FILES), timecyc_text=timecyc_text)


@pytest.fixture
def game_root(tmp_path: Path) -> Path:
    """A synthetic game root (outside any satk workspace)."""
    return make_game(tmp_path / "game")


@pytest.fixture
def synth_ws(satk_home: Path) -> Path:
    """Isolated workspace whose default ``vanilla`` root (``<ws>/gta-sa-clean``) is the synthetic game."""
    make_game(satk_home / "gta-sa-clean")
    return satk_home
