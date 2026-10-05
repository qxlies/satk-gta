"""Synthetic game roots and profile indexes for ``satk.idmgr`` (no game files).

``vanilla`` root: DAT files, ``vehicles.ide`` (400 testcar, 401 testcar2), ``peds.ide`` (7 male01),
``default.ide`` (hier 300 cutobj01, weap 321 dildo), ``maps/t/t.ide`` (objs 1000, 1001, 1003, tobj 1004),
``maps/t/t.ipl`` and ``MANIFEST.sha256`` (every file vanilla), plus ``SAMP/samp.ide`` (objs 19000, not loaded).

``installed`` root = vanilla + modloader mods + a fastman92 LA ini (``dff = 25000``):

* ``modloader/carpack/vehicles.ide`` - cars 15000 mycar, 15002 mycar3;
* ``modloader/other/data/cars.ide`` - cars 15000 othercar (CLASH with carpack);
* ``modloader/other/readme.txt`` - a vehicles.ide line 15001 readmecar;
* ``modloader/rep/vehicles.ide`` - 400 testcar (same name: REDEFINES), 401 newname (REPLACES).
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

CAR = "{id}, {name}, {name}, car, {up}, {up}, null, executive, 10, 0, 0, -1, 0.7, 0.7, -1"


def car(i: int, name: str) -> str:
    return CAR.format(id=i, name=name, up=name.upper())


def w(root: Path, rel: str, text: str | bytes) -> Path:
    p = root.joinpath(*rel.split("/"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(text if isinstance(text, bytes) else text.encode("latin-1"))
    return p


def make_vanilla(root: Path) -> Path:
    w(root, "data/default.dat", "IDE DATA\\VEHICLES.IDE\nIDE DATA\\PEDS.IDE\nIDE DATA\\DEFAULT.IDE\n")
    w(root, "data/gta.dat", "IDE DATA\\MAPS\\T\\T.IDE\nIPL DATA\\MAPS\\T\\T.IPL\n")
    w(root, "data/vehicles.ide", f"cars\n{car(400, 'testcar')}\n{car(401, 'testcar2')}\nend\n")
    w(root, "data/peds.ide", "peds\n7, male01, male01, CIVMALE, STAT_STREET_GUY, man, 1983, 1, null, 9,9, PED_TYPE_GEN, "
                             "VOICE_GEN_BBDYG1, VOICE_GEN_BBDYG2\nend\n")
    w(root, "data/default.ide", "hier\n300, cutobj01, generic\nend\nweap\n321, gun_dildo1, gun_dildo1, null, 1, 50, 0\n"
                                "end\n")
    w(root, "data/maps/t/t.ide", "objs\n1000, box1, boxes, 150, 0\n1001, box2, boxes, 150, 0\n1003, box4, boxes, 150, 0\n"
                                 "end\ntobj\n1004, nightbox, boxes, 150, 0, 20, 6\nend\n")
    w(root, "data/maps/t/t.ipl", "inst\n1000, box1, 0, 10, 20, 3, 0, 0, 0, 1, -1\nend\n")
    w(root, "gta_sa.exe", b"MZ synthetic")
    files = sorted(p for p in root.rglob("*") if p.is_file())
    w(root, "MANIFEST.sha256", "".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  "
                                       f"{os.path.relpath(p, root).replace('/', chr(92))}\n" for p in files))
    w(root, "SAMP/samp.ide", "objs\n19000, sampobj, samp, 100, 0\nend\n")
    return root


def add_mods(root: Path) -> Path:
    w(root, "modloader/carpack/vehicles.ide", f"cars\n{car(15000, 'mycar')}\n{car(15002, 'mycar3')}\nend\n")
    w(root, "modloader/other/data/cars.ide", f"cars\n{car(15000, 'othercar')}\nend\n")
    w(root, "modloader/other/readme.txt", f"Put this into vehicles.ide:\n{car(15001, 'readmecar')}\n")
    w(root, "modloader/rep/vehicles.ide", f"cars\n{car(400, 'testcar')}\n{car(401, 'newname')}\nend\n")
    w(root, "fastman92limitAdjust_GTASA.ini", "[ID LIMITS]\nApply ID limit patch = 1\ndff = 25000\ntxd = 15000\n")
    return root


def build_index(root: Path, out: Path, profile: str):
    from satk.index.api import IndexDB
    from satk.index.build import build
    from satk.index.layers import HashCache

    build(profile, jobs=1, out=out, root=root, dat_files=["data/default.dat", "data/gta.dat"], img_order="engine",
          vanilla_manifest=root / "MANIFEST.sha256", hashcache=HashCache(out.parent / "hc.sqlite"))
    return IndexDB(profile, out)


@pytest.fixture
def world(satk_home, tmp_path):
    """``SimpleNamespace(vanilla=root, installed=root, dbs={profile: IndexDB})`` with ``open_index`` overridden."""
    from satk.core.errors import SatkError
    from satk.index.api import override_index

    van = make_vanilla(tmp_path / "van")
    inst = add_mods(make_vanilla(tmp_path / "inst"))
    dbs = {"vanilla": build_index(van, tmp_path / "idx" / "vanilla.sqlite", "vanilla"),
           "installed": build_index(inst, tmp_path / "idx" / "installed.sqlite", "installed")}

    def factory(profile: str):
        if profile in dbs:
            return dbs[profile]
        raise SatkError("INDEX_MISSING", f"no index for profile {profile!r}")

    with override_index(factory):
        yield SimpleNamespace(vanilla=van, installed=inst, dbs=dbs)
    for db in dbs.values():
        db.close()


@pytest.fixture
def builders():
    return SimpleNamespace(w=w, car=car, make_vanilla=make_vanilla, add_mods=add_mods, build_index=build_index)
