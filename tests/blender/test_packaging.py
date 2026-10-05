"""Text files around a Blender export (IDE/IPL, meta.xml, client.lua). No Blender needed."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from satk.blender import packaging as P
from satk.core.errors import SatkError

MODELS = [
    {"name": "lae2_roads89", "sid": "model:17613", "id": 17613, "sec": "objs",
     "files": {"dff": "D:/x/lae2_roads89.dff", "txd": "D:/x/lae2_roads89.txd", "col": "D:/x/lae2_roads89.col"},
     "insts": [{"sid": "inst:lae2_stream0#4", "pos": [2489.296875, -1668.5, 12.296875], "q": [0.0, 0.0, 0.3826834, 0.9238795],
                "rot_zxy_deg": [0.0, 0.0, 45.0], "area": 0, "lod": False}]},
    {"name": "infernus", "sid": "model:411", "id": 411, "sec": "cars",
     "files": {"dff": "D:/x/infernus.dff", "txd": "D:/x/infernus.txd"}, "insts": []},
]


def test_ipl_conjugates_world_rotation():
    text = P.ipl_text(MODELS)
    line = next(ln for ln in text.splitlines() if ln.startswith("17613"))
    f = [x.strip() for x in line.split(",")]
    assert f[1] == "lae2_roads89" and f[2] == "0"
    assert [float(v) for v in f[3:6]] == pytest.approx([2489.2969, -1668.5, 12.2969], abs=1e-4)
    # world quaternion (0,0,0.38,0.92) is written as the IPL quaternion (conjugate, V9)
    assert [float(v) for v in f[6:10]] == pytest.approx([0.0, 0.0, -0.3826834, 0.9238795], abs=1e-6)
    assert f[10] == "-1"
    assert "infernus" not in text  # vehicles have no IPL placements
    assert text.splitlines()[1] == "inst" and text.rstrip().endswith("end")


def test_ide_objs_only():
    text = P.ide_text(MODELS)
    # no definition data in the manifest (a new object): defaults
    assert f"17613, lae2_roads89, lae2_roads89, {P.DEFAULT_DRAW}, 0" in text
    assert "infernus" not in text
    assert "20000, lae2_roads89" in P.ide_text(MODELS, first_id=20000)


def _with_defs():
    ms = json.loads(json.dumps(MODELS))
    ms[0]["ide"] = {"sec": "objs", "draw": 150.0, "flags": 1}                 # lae2.ide: lae2_roads89
    ms[0]["insts"].append({"sid": "inst:lae2_stream2#169", "pos": [2463.05, -1645.11, 12.75], "q": [0, 0, 0, 1],
                           "area": 0, "iflags": 2})                           # telgrphpole02-like: interior 512
    ms.append({"name": "htl_fan_rotate_nt", "sid": "model:1657", "id": 1657, "sec": "tobj",
               "ide": {"sec": "tobj", "draw": 30.0, "flags": 0, "time": [20, 6]}, "files": {}, "insts": []})
    ms.append({"name": "animobj", "sid": "model:3000", "id": 3000, "sec": "anim",
               "ide": {"sec": "anim", "draw": 100.0, "flags": 4, "anim": "ifp1"}, "files": {}, "insts": []})
    return ms


def test_ide_keeps_original_definition():
    text = P.ide_text(_with_defs())
    lines = text.splitlines()
    assert "17613, lae2_roads89, lae2_roads89, 150, 1" in lines                 # not 299, 0
    tobj = lines.index("tobj")
    assert lines[tobj + 1] == "1657, htl_fan_rotate_nt, htl_fan_rotate_nt, 30, 0, 20, 6" and lines[tobj + 2] == "end"
    anim = lines.index("anim")
    assert lines[anim + 1] == "3000, animobj, animobj, ifp1, 100, 4"
    assert lines.index("objs") < tobj < anim


def test_ipl_keeps_instance_flags():
    text = P.ipl_text(_with_defs())
    by_x = {r[3]: r for r in ([x.strip() for x in ln.split(",")] for ln in text.splitlines() if "," in ln)}
    assert by_x["2463.05"][2] == "512"          # area 0 + iflags 2 << 8, as in lae2_stream2.ipl
    assert by_x["2489.2969"][2] == "0"
    assert P.interior_field({"area": 3, "iflags": 1}) == 259 and P.interior_field({}) == 0


def test_meta_and_lua():
    meta = P.meta_xml("res", MODELS)
    for fn in ("lae2_roads89.dff", "lae2_roads89.txd", "lae2_roads89.col", "infernus.dff", "infernus.txd", "client.lua"):
        assert fn in meta
    assert "D:/x" not in meta  # file names only, relative to the resource
    lua = P.client_lua("res", MODELS)
    for fn in ("engineRequestModel", "engineLoadTXD", "engineImportTXD", "engineLoadDFF", "engineReplaceModel",
               "engineLoadCOL", "engineReplaceCOL", "createObject"):
        assert fn in lua, fn
    assert 'kind = "vehicle"' in lua and 'base = 411' in lua
    assert re.search(r"\{2489\.2969, -1668\.5, 12\.2969, 0, 0, 45, 0\}", lua)


def test_write_package(satk_home):
    from satk.core.paths import work

    d = work("out", "exports", "res")
    (d / "export.json").write_text(json.dumps({"target": "mta-resource", "name": "res", "models": MODELS}), encoding="utf-8")
    r = P.write_package(d, "mta-resource")
    names = sorted(p.rsplit("/", 1)[-1] for p in r["files"])
    assert names == ["client.lua", "meta.xml", "res.ide", "res.ipl"]
    assert "\r" not in (d / "client.lua").read_text(encoding="utf-8")
    assert r["warn"] and r["warn"][0].startswith("IDE_DEFAULTS: no IDE definition for lae2_roads89")
    r2 = P.write_package(d, "modloader", "ml")
    assert sorted(p.rsplit("/", 1)[-1] for p in r2["files"]) == ["README.txt", "ml.ide", "ml.ipl"]
    (d / "export.json").write_text(json.dumps({"models": _with_defs()}), encoding="utf-8")
    r3 = P.write_package(d, "mta-resource", "res")
    assert r3["warn"] == [] and "150, 1" in (d / "res.ide").read_text(encoding="utf-8")


def test_export_dir_checks_without_creating(satk_home):
    from satk.core.paths import cfg

    wk = Path(cfg().paths.work)
    d = P.export_dir("lae2_roads89")
    assert d == Path(os.path.abspath(wk)) / "out" / "exports" / "lae2_roads89"
    assert not d.exists()
    for bad in ("../../gta-sa-clean/x", "a/b", "", "x" * 41, "имя"):
        with pytest.raises(SatkError) as e:
            P.export_dir(bad)
        assert e.value.code == "BAD_PARAMS", bad
    assert not (wk / "gta-sa-clean").exists() and not (wk / "out" / "exports").exists()
    with pytest.raises(SatkError) as e:
        P.export_dir("x", str(wk.parent / "elsewhere"))
    assert e.value.code == "PROTECTED_PATH"


def test_default_name():
    assert P.default_name("lae2_roads89@lae2_stream0#4") == "lae2_roads89"
    assert P.default_name("wheel_lf.001") == "wheel_lf"
    assert P.default_name("inst:lae2_stream2#164") == "inst_lae2_stream2_164"
    assert P.default_name("кириллица") == "export" and P.NAME_RE.fullmatch(P.default_name("x" * 80))


def test_write_package_refuses_protected(satk_home, tmp_path, monkeypatch):
    prot = tmp_path / "prot"
    prot.mkdir()
    (prot / "export.json").write_text(json.dumps({"models": MODELS}), encoding="utf-8")
    monkeypatch.setenv("SATK_SAFETY_PROTECTED_ROOTS", json.dumps([str(prot)]))
    from satk.core import config

    config.reset()
    try:
        with pytest.raises(SatkError) as e:
            P.write_package(prot, "mta-resource", "x")
        assert e.value.code == "PROTECTED_PATH" and "prot" in e.value.msg
    finally:
        config.reset()
