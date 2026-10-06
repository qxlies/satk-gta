"""satk.ingame.modset: synthetic mod folders -> model specs (no game files, a fake vanilla lookup)."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from satk.core.errors import SatkError
from satk.ingame import modset as MS

PREMIER_LINE = ("PREMIER     1600.0    3921.3   1.8    0.0 -0.2 -0.1 75  0.75 0.85 0.52    5 220.0 24.0 10.0 R P "
                "8.0  0.5 0 35.0     1.8  0.1  0.0   0.31 -0.15 0.5  0.0     0.26 0.5 18000 40000000 10200002 1 1 0")


def _files(root: Path, **files: bytes | str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        p = root / name.replace("__", "/")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data if isinstance(data, bytes) else data.encode("latin-1"))
    return root


def _col2(name: str) -> bytes:
    """A minimal COL2 record (no spheres, boxes or faces)."""
    body = struct.pack("<10f3HBxI6I", -1, -1, -1, 1, 1, 1, 0, 0, 0, 1.7, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    head = name.encode("latin-1").ljust(22, b"\0") + struct.pack("<H", 0)
    return b"COL2" + struct.pack("<I", len(head) + len(body)) + head + body


def test_replace_by_name_with_handling(tmp_path, vanilla):
    mod = _files(tmp_path / "mod", **{"premier.dff": b"d", "premier.txd": b"t", "data__handling.cfg": PREMIER_LINE,
                                      "carcols.dat": "premier, 3,3, 5,5\n"})
    specs, warn = MS.scan([str(mod)], vanilla=vanilla)
    assert warn == [] and len(specs) == 1
    s = specs[0]
    assert (s.key, s.kind, s.mode, s.base, s.ref) == ("premier", "vehicle", "replace", 426, True)
    assert set(s.files) == {"dff", "txd"} and s.colors == [3, 3, 0, 0]
    h = s.handling
    assert h["engineAcceleration"] == pytest.approx(9.6) and h["maxVelocity"] == 220.0
    assert h["driveType"] == "rwd" and h["engineType"] == "petrol" and h["ABS"] is False
    assert h["centerOfMass"] == [0.0, -0.2, -0.1] and h["modelFlags"] == 0x40000000 and h["handlingFlags"] == 0x10200002
    assert "monetary" not in h and "headLight" not in h
    assert s.expect == {"source": "mod", "mass": 1600.0, "max_vel": 220.0, "accel": 24.0, "drive": "R",
                        "vanilla_max_vel": 200.0, "vanilla_accel": 22.0}
    m = s.manifest({"dff": "m/premier.dff"})
    assert m["files"] == {"dff": "m/premier.dff"} and m["handling"]["maxVelocity"] == 220.0 and "weapon" not in m


def test_new_and_base(tmp_path, vanilla):
    mod = _files(tmp_path / "mod", **{"premier.dff": b"d", "premier.txd": b"t"})
    s = MS.scan([str(mod)], new=True, vanilla=vanilla)[0][0]
    assert (s.mode, s.base, s.ref) == ("new", 426, True) and s.expect["source"] == "vanilla"
    s = MS.scan([str(mod)], new=True, base="model:411", vanilla=vanilla)[0][0]
    assert (s.mode, s.base, s.base_name) == ("new", 411, "infernus")
    s = MS.scan([str(mod)], replace="infernus", vanilla=vanilla)[0][0]
    assert (s.mode, s.base) == ("replace", 411)
    with pytest.raises(SatkError) as e:
        MS.scan([str(mod)], replace="411", new=True, vanilla=vanilla)
    assert e.value.code == "BAD_PARAMS"


def test_unknown_names_need_a_kind(tmp_path, vanilla):
    mod = _files(tmp_path / "mod", **{"mycar.dff": b"d", "mycar.txd": b"t"})
    with pytest.raises(SatkError) as e:
        MS.scan([str(mod)], vanilla=vanilla)
    assert e.value.code == "BAD_PARAMS" and "--kind" in e.value.hint
    specs, warn = MS.scan([str(mod)], kind="vehicle", vanilla=vanilla)
    assert (specs[0].mode, specs[0].base, specs[0].ref) == ("new", 426, False)
    assert any(w.startswith("NO_REFERENCE:") for w in warn)


def test_ide_line_gives_kind_wheels_and_handling(tmp_path, vanilla):
    ide = ("cars\n400, mycar, mycar, car, MYCAR, MYCAR, null, richfamily, 10, 0, 0, -1, 0.72, 0.7, -1\nend\n")
    hl = PREMIER_LINE.replace("PREMIER", "MYCAR")
    mod = _files(tmp_path / "mod", **{"mycar.dff": b"d", "mycar.txd": b"t", "mycar.ide": ide, "handling.cfg": hl})
    s = MS.scan([str(mod)], vanilla=vanilla)[0][0]
    assert (s.kind, s.mode) == ("vehicle", "new") and s.wheels == [0.72, 0.7]
    assert s.handling["maxVelocity"] == 220.0 and s.handling_line.startswith("MYCAR")


def test_txd_named_in_the_ide_and_loose_ide_line(tmp_path, vanilla):
    readme = "Add to vehicles.ide:\n400, mycar, mytex, car, MYCAR, MYCAR, null, normal, 10, 0, 0, -1, 0.7, 0.7, -1\n"
    mod = _files(tmp_path / "mod", **{"mycar.dff": b"d", "mytex.txd": b"t", "other.txd": b"o", "readme.txt": readme})
    specs, warn = MS.scan([str(mod)], vanilla=vanilla)
    assert specs[0].kind == "vehicle" and specs[0].files["txd"].name == "mytex.txd"
    assert any(w.startswith("UNUSED_TXD:") for w in warn)


def test_weapon_replaces(tmp_path, vanilla):
    mod = _files(tmp_path / "mod", **{"desert_eagle.dff": b"d", "desert_eagle.txd": b"t"})
    s, warn = MS.scan([str(mod)], new=True, vanilla=vanilla)
    assert (s[0].kind, s[0].mode, s[0].base, s[0].weapon) == ("weapon", "replace", 348, 24)
    assert any(w.startswith("WEAPON_REPLACE:") for w in warn)


def test_object_with_a_col_archive(tmp_path, vanilla):
    archive = _col2("other") + _col2("BinNt07_LA") + _col2("third")
    mod = _files(tmp_path / "mod", **{"BinNt07_LA.dff": b"d", "BinNt07_LA.txd": b"t", "pack.col": archive})
    s = MS.scan([str(mod)], vanilla=vanilla)[0][0]
    assert (s.kind, s.mode, s.base) == ("object", "replace", 1337)
    assert s.files["col"].name == "pack.col" and s.col_entry == "BinNt07_LA"
    one = MS.col_bytes(s.files["col"], s.col_entry)
    assert one == _col2("BinNt07_LA")
    assert MS.col_bytes(s.files["col"], None) == archive


def test_texture_only_mod(tmp_path, vanilla):
    mod = _files(tmp_path / "mod", **{"landjump.txd": b"t"})
    s = MS.scan([str(mod)], vanilla=vanilla)[0]
    assert len(s) == 1 and (s[0].kind, s[0].mode, s[0].base) == ("object", "replace", 1633)
    assert list(s[0].files) == ["txd"] and "shared by 2" in s[0].notes[0]


def test_inputs_and_keys(tmp_path, vanilla):
    with pytest.raises(SatkError) as e:
        MS.scan([str(tmp_path / "missing")], vanilla=vanilla)
    assert e.value.code == "NOT_FOUND"
    empty = _files(tmp_path / "empty", **{"readme.txt": "nothing"})
    with pytest.raises(SatkError) as e:
        MS.scan([str(empty)], vanilla=vanilla)
    assert e.value.code == "NOT_FOUND"
    two = _files(tmp_path / "two", **{"premier.dff": b"d", "infernus.dff": b"d"})
    with pytest.raises(SatkError) as e:
        MS.scan([str(two)], replace="426", vanilla=vanilla)
    assert e.value.code == "BAD_PARAMS"
    specs, warn = MS.scan([str(two / "premier.dff"), str(two / "infernus.dff")], vanilla=vanilla)
    assert [s.key for s in specs] == ["infernus", "premier"] and all(w.startswith("NO_TXD:") for w in warn)
    assert MS.key_of("My Car-v2.DFF") == "my_car_v2_dff" and MS.key_of("!!!") == "model"
