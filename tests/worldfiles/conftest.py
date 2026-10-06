"""Synthetic world data files for satk.worldfiles (invented values in the stock layouts; no game files).

``make_game(root)`` writes ``text/american.gxt``, ``data/info.zon``, ``data/map.zon``, ``data/water.dat``,
``data/water1.dat``, ``data/timecyc.dat``, ``data/popcycle.dat`` and ``models/gta3.img`` (144 radar tiles of
16 x 16 pixels) under ``root``.
"""

from __future__ import annotations

import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

SECTOR = 2048

INFO_ZON = (
    "zone\r\n"
    "TOWN1, 0, -100.0, -100.0, 0.0, 100.0, 100.0, 200.0, 1, TOWN\r\n"
    "TOWN2\t, 0, 100.0, -100.0, -4.57764e-005, 300.0, 100.0, 200.0, 1, TOWN\r\n"
    "PARK, 0, -50.0, -50.0, 0.0, 50.0, 50.0, 200.0, 1, PARK\r\n"
    "EDGE, 0, 250.0, 50.0, 0.0, 400.0, 150.0, 200.0, 1, EDGE\r\n"
    "BIG, 0, -1000.0, -1000.0, -100.0, 1000.0, 1000.0, 900.0, 1, BIGCITY\r\n"
    "end\r\n"
)
MAP_ZON = (
    "zone\r\n"
    "LA01, 3, 0.0, -3000.0, -500.0, 3000.0, 0.0, 500.0, 1, UNUSED\r\n"
    "SF01, 3, -3000.0, -3000.0, -500.0, 0.0, 3000.0, 500.0, 2, UNUSED\r\n"
    "end\r\n"
)


def water_line(x0: float, y0: float, x1: float, y1: float, z: float = 0.0, flags: int | None = 1) -> str:
    v = [(x0, y0), (x1, y0), (x0, y1), (x1, y1)]
    s = "    ".join(f"{x:.1f} {y:.1f} {z:.5f} 0.00000 0.00000 0.05100 0.10200" for x, y in v)
    return s + (f"  {flags}" if flags is not None else "")


WATER_DAT = "processed\n" + "\n".join([
    water_line(-3000, -3000, -2000, -2000),
    water_line(-2000, -3000, -1000, -2000, 0.0, 3),
    water_line(100, 100, 200, 200, 12.5),
    "-1722.0 -62.0 0.00000 0.00000 0.00000 0.19900 0.24100    -1574.0 -62.0 0.00000 0.00000 0.00000 0.19900 "
    "0.24100    -1574.0 86.0 0.00000 0.00000 0.00000 0.19900 0.24100  1",
]) + "\n"
WATER1_DAT = "processed\n" + "\n".join([water_line(-3000, -3000, -2000, -2000, 0.0, None)]) + "\n"

WEATHERS = (
    "EXTRASUNNY_LA", "SUNNY_LA", "EXTRASUNNY_SMOG_LA", "SUNNY_SMOG_LA", "CLOUDY_LA", "SUNNY_SF", "EXTRASUNNY_SF",
    "CLOUDY_SF", "RAINY_SF", "FOGGY_SF", "SUNNY_VEGAS", "EXTRASUNNY_VEGAS", "CLOUDY_VEGAS", "EXTRASUNNY_COUNTRYSIDE",
    "SUNNY_COUNTRYSIDE", "CLOUDY_COUNTRYSIDE", "RAINY_COUNTRYSIDE", "EXTRASUNNY_DESERT", "SUNNY_DESERT",
    "SANDSTORM_DESERT", "UNDERWATER", "EXTRACOLOURS_1", "EXTRACOLOURS_2",
)
HOUR_NAMES = ("Midnight", "5AM", "6AM", "7AM", "Midday", "7PM", "8PM", "10PM")


def tc_line(w: int, h: int) -> str:
    a = (w * 7 + h * 3) % 200
    return (f"{a} {a} {a}\t210 194 182\t255 255 255\t{h * 10} {w} 200\t0 31 32\t255 128 0\t5 0 0\t"
            f"1.00 1.00 0.30 200 100 0\t{400 + w * 10}.00 100.00 1.00 30 20 0\t3 3 3\t85 85 65 240\t"
            f"255 87 87 87\t255 60 121 122\t0 90 0")


def timecyc_text(short_line: bool = True) -> str:
    lines = []
    for w, name in enumerate(WEATHERS):
        lines += [f"//////////// {name}", "//Amb\t\t\tAmb_Obj\tDir ..."]
        for h, hn in enumerate(HOUR_NAMES):
            lines.append(f"//{hn}")
            if short_line and w == 16 and h == 6:
                lines.append("255\t167 198 223\t255 255 255\t40 40 40")       # read shifted, like vanilla line 320
            else:
                lines.append(tc_line(w, h))
        lines.append("//")
    # mixed line ends, like the stock file
    return "\r\n".join(lines[:40]) + "\r\n" + "\n".join(lines[40:]) + "\n"


def popcycle_text() -> str:
    out = ["// synthetic popcycle", ""]
    for z in range(20):
        out.append(f"// ZONE {z}")
        for day in ("Weekday", "Weekend"):
            out.append(f"// {day}")
            for s in range(12):
                vals = [z % 10 + 1, s + 2, 100, 100, 100, 100] + [0] * 6 + [50, 30, 20] + [0] * 9 + [0, 0]
                out.append("    " + "     ".join(map(str, vals)) + "      // slot")
    out.append("   ")
    return "\r\n".join(out) + "\r\n"


def gxt_bytes() -> bytes:
    from satk.worldfiles.gxt import GxtDoc, GxtTable, write_gxt

    doc = GxtDoc(tables=[
        GxtTable("MAIN", [("TOWN", "Town"), ("PARK", "Park"), ("BIGCITY", "Big City"), ("CRED001", "Producer"),
                          ("ACCENT", "Café über"), ("0x00ABCDEF", "hashed")]),
        GxtTable("INTRO1", [("INT1_AA", "~z~Hello."), ("INT1_AB", "~z~Bye.")]),
    ])
    return write_gxt(doc)


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


def gradient(side: int):
    """A smooth synthetic picture (numpy RGBA)."""
    np = pytest.importorskip("numpy")
    y, x = np.mgrid[0:side, 0:side].astype(np.float64)
    r = 128 + 100 * np.sin(x / side * 6.0)
    g = 128 + 100 * np.cos(y / side * 5.0)
    b = 128 + 60 * np.sin((x + y) / side * 4.0)
    img = np.stack([r, g, b, np.full_like(r, 255)], axis=2)
    return np.clip(np.rint(img), 0, 255).astype(np.uint8)


def radar_img(tile: int = 16) -> bytes:
    from satk.worldfiles.radar import GRID, tile_name, tile_txd

    pic = gradient(GRID * tile)
    entries = []
    for i in range(GRID * GRID):
        r, c = divmod(i, GRID)
        entries.append((f"{tile_name(i)}.txd", tile_txd(tile_name(i), pic[r * tile:(r + 1) * tile,
                                                                        c * tile:(c + 1) * tile])))
    return img_v2(entries)


def make_game(root: Path, radar: bool = True) -> SimpleNamespace:
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "text").mkdir(exist_ok=True)
    (root / "models").mkdir(exist_ok=True)
    (root / "gta_sa.exe").write_bytes(b"MZ synthetic")
    (root / "text" / "american.gxt").write_bytes(gxt_bytes())
    (root / "data" / "info.zon").write_bytes(INFO_ZON.encode("latin-1"))
    (root / "data" / "map.zon").write_bytes(MAP_ZON.encode("latin-1"))
    (root / "data" / "water.dat").write_bytes(WATER_DAT.encode("latin-1"))
    (root / "data" / "water1.dat").write_bytes(WATER1_DAT.encode("latin-1"))
    (root / "data" / "timecyc.dat").write_bytes(timecyc_text().encode("latin-1"))
    (root / "data" / "popcycle.dat").write_bytes(popcycle_text().encode("latin-1"))
    if radar:
        (root / "models" / "gta3.img").write_bytes(radar_img())
    return SimpleNamespace(root=root)


def _have(mod: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(mod) is not None


@pytest.fixture
def game(satk_home: Path) -> SimpleNamespace:
    """A synthetic vanilla game at ``<ws>/gta-sa-clean`` (the vanilla profile of the isolated workspace);
    the radar IMG only when numpy is installed (the DXT encoder needs it)."""
    return make_game(satk_home / "gta-sa-clean", radar=_have("numpy"))


@pytest.fixture
def out_dir(satk_home: Path) -> Path:
    return satk_home / "work" / "out" / "worldfiles"
