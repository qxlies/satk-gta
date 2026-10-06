"""Small generated game files; no stock assets are stored in this test package."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from satk.core import config


def write(root: Path, rel: str, content: str | bytes) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode("latin-1") if isinstance(content, str) else content)
    return path


def chunk(kind: int, payload: bytes = b"") -> bytes:
    return struct.pack("<III", kind, len(payload), 0x1803FFFF) + payload


def collision(name: str = "crate") -> bytes:
    from satk.rw.col import ColModel, encode_model

    return encode_model(ColModel(name=name, bmin=(-1, -1, -1), bmax=(1, 1, 1), radius=2))


def dff(effects: int = 0, embedded: bool = False) -> bytes:
    from satk.rw.codecs import Fx2dData, encode_2dfx

    payload = b""
    if effects:
        fx = encode_2dfx(Fx2dData([(struct.pack("<3f", 0, 0, 0), 1, b"smoke\0") for _ in range(effects)]))
        payload += chunk(0x1A, chunk(1, struct.pack("<I", 1)) + chunk(0xF, chunk(3, chunk(0x253F2F8, fx))))
    if embedded:
        payload += chunk(3, chunk(0x253F2FA, collision("basecar_col")))
    return chunk(0x10, payload)


def txd() -> bytes:
    return chunk(0x16, chunk(1, struct.pack("<HH", 0, 2)))


def img(entries: list[tuple[str, bytes]]) -> bytes:
    start = max(1, (8 + len(entries) * 32 + 2047) // 2048)
    directory = bytearray(b"VER2" + struct.pack("<I", len(entries)))
    data = bytearray()
    for name, raw in entries:
        sectors = max(1, (len(raw) + 2047) // 2048)
        directory.extend(struct.pack("<IHH24s", start, sectors, 0, name.encode("ascii")))
        data.extend(raw.ljust(sectors * 2048, b"\0"))
        start += sectors
    return bytes(directory).ljust(max(1, (len(directory) + 2047) // 2048) * 2048, b"\0") + data


def binary_ipl(instances: list[tuple[int, float, float]], cars: int = 0) -> bytes:
    body = b"".join(struct.pack("<7f3i", x, y, 0, 0, 0, 0, 1, mid, 0, -1) for mid, x, y in instances)
    head = struct.pack("<4s6I12I", b"bnry", len(instances), 0, 0, 0, cars, 0,
                       76, 0, 0, 0, 0, 0, 0, 0, 76 + len(body), 0, 0, 0)
    return head + body + b"".join(struct.pack("<4f8i", 0, 0, 0, 0, 400, 1, 1, 0, 0, 0, 0, 0) for _ in range(cars))


def car(mid: int, name: str) -> str:
    return f"{mid}, {name}, {name}, car, BASECAR, BASECAR, null, normal, 10, 0, 0, -1, 0.7, 0.7, 0"


CORE = ("cars\n" + car(400, "basecar") + "\nend\n"
        "peds\n7, baseped, baseped, CIVMALE, STAT_STREET_GUY, man, 1, 0, null, 9, 9\nend\n"
        "weap\n331, basegun, basegun, null, 1, 100, 0\nend\n"
        "objs\n1000, crate, shared, 100, 0\n1001, fence, shared, 100, 4096\nend\n"
        "tobj\n1002, lamp, shared, 100, 0, 6, 18\nend\n"
        "anim\n1003, moving, shared, null, 100, 0\nend\n"
        "hier\n1004, hierarchy, shared\nend\n"
        "2dfx\n1000, 0, 0, 0, 1, 255, 255, 255, 255\nend\n")
GTA_DAT = "IPL data/maps/map.ipl\n"
MAP = ("inst\n1000, crate, 0, 0, 0, 0, 0, 0, 0, 1, -1\n"
       "1001, fence, 0, 1, 0, 0, 0, 0, 0, 1, -1\nend\n"
       'enex\n0, 0, 0, 0, 1, 1, 1, 2, 2, 2, 0, 0, "door", 0, 0, 0, 0\nend\n')


def make_game(root: Path) -> Path:
    write(root, "data/default.dat", "IDE data/core.ide\n")
    write(root, "data/gta.dat", GTA_DAT)
    write(root, "data/core.ide", CORE)
    write(root, "data/maps/map.ipl", MAP)
    entries = [(name + ".dff", dff(2 if name == "lamp" else 0, name == "basecar"))
               for name in ("basecar", "baseped", "basegun", "crate", "fence", "lamp", "moving", "hierarchy")]
    entries += [(name + ".txd", txd()) for name in ("basecar", "baseped", "basegun", "shared")]
    entries += [("base.col", collision("crate") + collision("fence")),
                ("map_stream0.ipl", binary_ipl([(1001, 10, 0)], cars=2)),
                ("nodes0.dat", struct.pack("<5I", 0, 0, 0, 0, 0))]
    write(root, "models/gta3.img", img(entries))
    for rel in ("models/gta_int.img", "models/player.img", "anim/anim.img", "anim/cuts.img"):
        write(root, rel, img([]))
    write(root, "data/object.dat", "crate 1 1 1 1 1 1 1 1 1 1 1 1\n* end\n")
    write(root, "data/handling.cfg", ";the end\n")
    write(root, "data/cargrp.dat", "basecar\n")
    write(root, "data/pedgrp.dat", "baseped\n")
    write(root, "data/carmods.dat", "mods\nbasecar, upgrade1, upgrade2\nend\n")
    write(root, "data/weapon.dat", "ENDWEAPONDATA\n")
    write(root, "data/timecyc.dat", "// generated\n" + " ".join(["0"] * 52) + "\n")
    return root


@pytest.fixture
def games(satk_home, monkeypatch):
    clean = make_game(satk_home / "clean")
    installed = make_game(satk_home / "installed")
    monkeypatch.setenv("SATK_PATHS_GAME", str(clean))
    monkeypatch.setenv("SATK_PATHS_INSTALLED", str(installed))
    config.reset()
    return clean, installed


def addon(root: Path, name: str, ide: str = "", entries: dict | None = None) -> Path:
    write(root, "data/gta.dat", GTA_DAT + (f"IDE data/{name}.ide\n" if ide else ""))
    if ide:
        write(root, f"data/{name}.ide", ide)
    for rel, raw in (entries or {}).items():
        write(root, rel, raw)
    return root
