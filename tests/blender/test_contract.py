"""Request/response contract ``satk-blender/1`` (SPEC §4.11). No Blender needed."""

from __future__ import annotations

import json

import pytest

from satk.blender import contract as C


def test_cmds_and_defaults_cover_each_other():
    assert set(C.CMDS) == set(C.ARG_DEFAULTS)
    assert C.CONTRACT == "satk-blender/1"


def test_spec_example_request_roundtrip(tmp_path):
    req = C.make_request("import_area", {"center": [2495, -1687], "box": 100, "match": "center", "area": 0,
                                         "lod": "hd", "col": False},
                         profile="vanilla", out_dir=str(tmp_path / "job"), satk_src=r"D:\ws\tools\src")
    assert req["cmd"] == "import_area" and req["profile"] == "vanilla"
    assert req["satk_src"] == "D:/ws/tools/src"
    assert "\\" not in req["out_dir"]
    a = req["args"]
    assert a["box"] == [2445.0, -1737.0, 2545.0, -1637.0]
    assert a["center"] == [2495.0, -1687.0] and a["area"] == 0 and a["lod"] == "hd" and a["r"] is None
    p = tmp_path / "request.json"
    C.write_json(p, req)
    assert json.loads(p.read_text(encoding="utf-8")) == req


@pytest.mark.parametrize("area,want", [("any", None), (None, None), ("3", 3), (0, 0)])
def test_area_any(area, want):
    a = C.normalize_args("import_area", {"center": "1,2", "r": 50, "area": area})
    assert a["area"] == want and a["r"] == 50.0 and a["box"] is None


@pytest.mark.parametrize("args", [
    {"center": [0, 0]},                                   # neither r nor box
    {"center": [0, 0], "r": 10, "box": 10},               # both
    {"center": [0, 0], "r": -1},
    {"center": [0, 0], "box": 0},
    {"center": [0, 0], "r": 10, "lod": "nope"},
    {"center": [0, 0], "r": 10, "area": 300},
    {"center": [0, 0], "r": 10, "bogus": 1},              # unknown key
    {"center": [0], "r": 10},
])
def test_import_area_bad(args):
    with pytest.raises(C.ContractError) as e:
        C.normalize_args("import_area", args)
    assert e.value.code == "BAD_PARAMS"


def test_box_list_is_sorted():
    a = C.normalize_args("import_area", {"center": [0, 0], "box": [10, 20, -10, -20]})
    assert a["box"] == [-10.0, -20.0, 10.0, 20.0]


def test_import_model_args():
    a = C.normalize_args("import_model", {"id": "model:411", "render": "true", "views": 4})
    assert a == {"id": "model:411", "render": True, "views": 4, "size": 768, "engine": "workbench",
                 "col": False, "time": "12:00", "save": True}
    with pytest.raises(C.ContractError):
        C.normalize_args("import_model", {})
    with pytest.raises(C.ContractError):
        C.normalize_args("import_model", {"id": 411, "views": 3})


def test_render_args():
    a = C.normalize_args("render", {"blend": "x.blend", "pose": {"pos": [1, 2, 3], "look": "4,5,6"},
                                    "size": "640x360", "objindex": 1})
    assert a["pose"] == {"pos": [1.0, 2.0, 3.0], "look": [4.0, 5.0, 6.0]}
    assert a["size"] == [640, 360] and a["objindex"] is True and a["engine"] == "workbench"
    with pytest.raises(C.ContractError):
        C.normalize_args("render", {"blend": "x", "pose": {"pos": [0, 0, 0], "look": [0, 0, 0]}})
    with pytest.raises(C.ContractError):
        C.normalize_args("render", {"blend": "x", "pose": {"pos": [0, 0, 0], "look": [1, 0, 0]}, "bm": "a"})
    with pytest.raises(C.ContractError):
        C.normalize_args("render", {"blend": "x", "engine": "cycles"})


def test_export_args():
    a = C.normalize_args("export", {"blend": "x.blend", "objects": "a, b"})
    assert a["objects"] == ["a", "b"] and a["target"] == "mta-resource"
    with pytest.raises(C.ContractError):
        C.normalize_args("export", {"blend": "x.blend", "objects": []})
    with pytest.raises(C.ContractError):
        C.normalize_args("export", {"blend": "x.blend", "objects": ["a"], "name": "bad name!"})


@pytest.mark.parametrize("v,want", [("960x540", (960, 540)), (768, (768, 768)), ([10 * 10, 50], (100, 50)),
                                    ("256", (256, 256))])
def test_parse_size(v, want):
    assert C.parse_size(v) == want


@pytest.mark.parametrize("v", ["0x0", "abc", 8, 5000, True])
def test_parse_size_bad(v):
    with pytest.raises(C.ContractError):
        C.parse_size(v)


@pytest.mark.parametrize("t,want", [("00:00", 1.0), ("05:59", 1.0), ("06:30", 0.5), ("07:00", 0.0),
                                    ("12:00", 0.0), ("20:00", 0.0), ("20:15", 0.25), ("21:00", 1.0),
                                    ("23:59", 1.0)])
def test_day_night_balance_curve(t, want):
    h, m = C.parse_time(t)
    assert C.day_night_balance(h, m) == pytest.approx(want)


def test_parse_time_bad():
    for v in ("25:00", "12:60", "noon"):
        with pytest.raises(C.ContractError):
            C.parse_time(v)


def test_responses():
    ok = C.ok_response("doctor", stats={"x": 1}, log="L")
    assert ok["ok"] is True and ok["files"] == {} and ok["warnings"] == [] and ok["log"] == "L"
    err = C.error_response("render", "EXTERNAL_TOOL", "boom", hint="see log")
    assert err == {"ok": False, "cmd": "render", "error": {"code": "EXTERNAL_TOOL", "msg": "boom", "hint": "see log"},
                   "warnings": []}


def test_contract_is_stdlib_only():
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(C))
    mods = {n.names[0].name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)}
    mods |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert mods <= {"__future__", "json", "os", "re", "typing"}
