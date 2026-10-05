"""Synthetic path networks for the satk.paths tests (M2-05; no game files, SPEC §5.1 rule 5).

``network()`` builds two regions in the compact NODES layout from the format description:

* region 0 (x < -2250): cars ``0:0`` (-2300, -2900, 10) - ``0:1`` (-2280, ...) - ``0:2`` (-2260, ...),
  an isolated boat node ``0:3`` (water flag), pedestrians ``0:4`` - ``0:5``; navis ``0:0`` (0:0-0:1)
  and ``0:1`` (0:1-0:2) with one lane each way;
* region 1: cars ``1:0`` (-2240, -2900, 10) - ``1:1`` (-2220, ...); the cross-region road ``0:2 - 1:0``
  has navi ``1:0`` attached to ``0:2`` with 0 lanes westbound and 2 lanes eastbound (one way east);
  navi ``1:1`` (1:0-1:1).

Navis are attached to the lower address of their two endpoints and point toward it, as in the
vanilla files, so the gta-flow compiler reproduces the regions byte for byte. Region files are padded
to 2048-byte IMG sectors. ``make_root(root, ...)`` writes a minimal game root (DAT files and VER2
archives) that holds them.

With ``--import-mode=importlib`` test modules cannot import this file: helpers come through the
``pn`` fixture.
"""

from __future__ import annotations

import struct
import types
from pathlib import Path

import pytest

SECTOR = 2048
WATER = 0x80
NOT_HIGHWAY = 0x1000

#: (area, idx) -> (x, y, z, kind, flood, extra flags, width16, spawn, behaviour)
NODES = {
    0: [(-2300, -2900, 10, "car", 1, NOT_HIGHWAY, 0, 15, 0),
        (-2280, -2900, 10, "car", 1, NOT_HIGHWAY, 16, 15, 0),
        (-2260, -2900, 10.5, "car", 1, NOT_HIGHWAY, 0, 7, 2),
        (-2290, -2950, 0, "boat", 2, WATER, 0, 15, 0),
        (-2300, -2890, 10, "ped", 3, 0, 4, 15, 0),
        (-2290, -2890, 10, "ped", 3, 0, 4, 15, 0)],
    1: [(-2240, -2900, 10, "car", 1, NOT_HIGHWAY, 0, 15, 0),
        (-2220, -2900, 10, "car", 1, NOT_HIGHWAY, 0, 15, 0)],
}
#: node -> [(target, navi (area, idx) or packed int for peds, distance, intersection)]
LINKS = {
    (0, 0): [((0, 1), (0, 0), 20, 0)],
    (0, 1): [((0, 0), (0, 0), 20, 0), ((0, 2), (0, 1), 20, 1)],
    (0, 2): [((0, 1), (0, 1), 20, 1), ((1, 0), (1, 0), 20, 0)],
    (0, 3): [],
    (0, 4): [((0, 5), 0, 10, 0)],
    (0, 5): [((0, 4), 0, 10, 0)],
    (1, 0): [((0, 2), (1, 0), 20, 0), ((1, 1), (1, 1), 20, 0)],
    (1, 1): [((1, 0), (1, 1), 20, 0)],
}
#: area -> [(x, y, attached, dir100, width16, lanes toward attached, lanes away, light)]
NAVIS = {
    0: [(-2290, -2900, (0, 0), (-100, 0), 0, 1, 1, 0),
        (-2270, -2900, (0, 1), (-100, 0), 16, 1, 1, 0)],
    1: [(-2250, -2900, (0, 2), (-100, 0), 0, 0, 2, 1),
        (-2230, -2900, (1, 0), (-100, 0), 0, 1, 1, 0)],
}


def node_record(x, y, z, base, area, idx, nlinks, flood=1, flags=0, width16=0, spawn=15, behaviour=0) -> bytes:
    rec = bytearray(28)
    word = (nlinks & 15) | flags | (spawn << 16) | (behaviour << 20)
    struct.pack_into("<3hhhHHBBI", rec, 8, round(x * 8), round(y * 8), round(z * 8), 32766, base, area, idx,
                     width16, flood, word)
    return bytes(rec)


def navi_record(x, y, attached, d, width16, lanes_to, lanes_away, light=0) -> bytes:
    return struct.pack("<hhHHbbBBH", round(x * 8), round(y * 8), attached[0], attached[1], d[0], d[1], width16,
                       lanes_to | (lanes_away << 3), light)


def region_bytes(area: int, nodes=None, links=None, navis=None, pad: bool = True) -> bytes:
    """One nodes<area>.dat from the tables above (or the given ones)."""
    nodes = NODES[area] if nodes is None else nodes
    links = LINKS if links is None else links
    navis = NAVIS.get(area, []) if navis is None else navis
    vehicles = sum(1 for n in nodes if n[3] != "ped")
    recs, flat = [], []
    for i, (x, y, z, _kind, flood, flags, width16, spawn, beh) in enumerate(nodes):
        mine = links.get((area, i), [])
        recs.append(node_record(x, y, z, len(flat), area, i, len(mine), flood, flags, width16, spawn, beh))
        flat.extend(mine)
    out = bytearray(struct.pack("<5I", len(nodes), vehicles, len(nodes) - vehicles, len(navis), len(flat)))
    out += b"".join(recs)
    out += b"".join(navi_record(*nv) for nv in navis)
    if flat:
        out += b"".join(struct.pack("<HH", *t) for t, _n, _d, _i in flat)
        out += bytes(768)
        out += b"".join(struct.pack("<H", (n[0] << 10 | n[1]) if isinstance(n, tuple) else n)
                        for _t, n, _d, _i in flat)
        out += bytes(d for _t, _n, d, _i in flat) + bytes(192)
        out += bytes(i for _t, _n, _d, i in flat) + bytes(192)
    if pad:
        out += bytes(-len(out) % SECTOR)
    return bytes(out)


def network() -> dict[int, bytes]:
    return {a: region_bytes(a) for a in NODES}


def make_img(path: Path, entries: list[tuple[str, bytes]]) -> Path:
    """A VER2 archive with sector-aligned entries."""
    head = 8 + 32 * len(entries)
    off = -(-head // SECTOR)
    dir_, blobs = b"", b""
    for name, data in entries:
        data = data + bytes(-len(data) % SECTOR)
        n = len(data) // SECTOR
        dir_ += struct.pack("<IHH24s", off, n, 0, name.encode("latin-1"))
        blobs += data
        off += n
    body = b"VER2" + struct.pack("<I", len(entries)) + dir_
    body += bytes(-len(body) % SECTOR)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body + blobs)
    return path


def make_root(root: Path, regions: dict[int, bytes] | None = None, extra: dict[int, bytes] | None = None) -> Path:
    """Minimal game root: DAT files, models/gta3.img (+ models/extra.img from gta.dat when ``extra``)."""
    regions = network() if regions is None else regions
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "gta_sa.exe").write_bytes(b"MZ synthetic")
    (root / "data" / "default.dat").write_text("# synthetic\n", encoding="ascii")
    lines = ["# synthetic"]
    if extra is not None:
        lines.append("IMG MODELS\\EXTRA.IMG")
        make_img(root / "models" / "extra.img", [(f"nodes{a}.dat", b) for a, b in sorted(extra.items())])
    (root / "data" / "gta.dat").write_text("\n".join(lines) + "\n", encoding="ascii")
    entries = [("dummy.txd", b"\0" * 100)] + [(f"nodes{a}.dat", b) for a, b in sorted(regions.items())]
    make_img(root / "models" / "gta3.img", entries)
    make_img(root / "models" / "gta_int.img", [("other.dff", b"\1" * 10)])
    return root


@pytest.fixture
def pn():
    """Helper functions and tables of this conftest."""
    return types.SimpleNamespace(NODES=NODES, LINKS=LINKS, NAVIS=NAVIS, SECTOR=SECTOR, node_record=node_record,
                                 navi_record=navi_record, region_bytes=region_bytes, network=network,
                                 make_img=make_img, make_root=make_root)


@pytest.fixture
def game(satk_home):
    """Isolated workspace whose vanilla profile root (``<ws>/gta-sa-clean``) holds the synthetic network."""
    from satk.core import config as _config

    root = make_root(satk_home / "gta-sa-clean")
    _config.reset()
    return root
