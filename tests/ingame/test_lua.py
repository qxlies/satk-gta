"""The satk-testdrive scripts compile with the server's own Lua 5.1, and the pure helpers of shared.lua work."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET

import pytest

from satk.ingame import checks as CH
from satk.ingame import resource as R

pytestmark = pytest.mark.engine

SCRIPTS = ("shared.lua", "server.lua", "client.lua", "checks.lua")


@pytest.fixture
def lua(lua_dll):
    from lua51 import Lua

    L = Lua(lua_dll)
    L.run((R.logic_source() / "shared.lua").read_text(encoding="utf-8"), "shared.lua")
    L.run("function J(v) if type(v) == 'table' then local p = {} for k, x in pairs(v) do "
          "p[#p + 1] = string.format('%q:%s', tostring(k), J(x)) end return '{' .. table.concat(p, ',') .. '}' "
          "elseif v == nil then return 'null' elseif type(v) == 'string' then return string.format('%q', v) "
          "else return tostring(v) end end")
    yield L
    L.close()


def test_meta_lists_the_scripts_and_exports():
    meta = ET.parse(R.logic_source() / "meta.xml").getroot()
    scripts = [(s.get("src"), s.get("type")) for s in meta.findall("script")]
    assert scripts == [("shared.lua", "shared"), ("server.lua", "server"), ("client.lua", "client"),
                       ("checks.lua", "client")]
    assert {(e.get("function"), e.get("type")) for e in meta.findall("export")} == {("td", "server"),
                                                                                   ("tdc", "client")}
    for name in SCRIPTS:
        head = (R.logic_source() / name).read_text(encoding="utf-8").splitlines()[:5]
        assert "-- SPDX-License-Identifier: MIT" in head, name


@pytest.mark.parametrize("name", SCRIPTS)
def test_scripts_compile_with_the_servers_lua(lua_dll, name):
    from lua51 import Lua

    L = Lua(lua_dll)
    try:
        assert L.compile((R.logic_source() / name).read_text(encoding="utf-8"), name) is None
    finally:
        L.close()


def test_generated_manifest_compiles(lua_dll):
    from lua51 import Lua
    from satk.ingame import spots as SP

    m = {"rev": "abc", "models": [{"key": "a\"b", "kind": "vehicle", "files": {"dff": "m/a.dff"}, "handling": {"ABS": True,
         "centerOfMass": [0.0, -0.2, -0.1]}}], "spots": SP.SPOTS, "geom": SP.GEOM, "label": "Tëst ü"}
    L = Lua(lua_dll)
    try:
        code = R._manifest_lua(m).decode("utf-8")
        assert L.compile(code, "manifest.lua") is None
        L.run(code)
        assert L.run("return manifest().models[1].key") == 'a"b'
        assert L.run("return manifest().label") == "Tëst ü"
        assert L.run("return tostring(manifest().models[1].handling.ABS)") == "true"
        assert L.run("return tostring(manifest().geom.runway.loop_at)") == "600"
    finally:
        L.close()


def test_geometry(lua):
    out = json.loads(lua.run("return '[' .. table.concat({TD.offset(0, 0, 0, 10, 0)}, ',') .. ',' .. "
                             "table.concat({TD.offset(0, 0, 270, 10, 2)}, ',') .. ']'"))
    assert out[0] == pytest.approx(0, abs=1e-9) and out[1] == pytest.approx(10)
    assert out[2] == pytest.approx(10) and out[3] == pytest.approx(-2)  # heading 270 = east, right = south
    along, side = json.loads(lua.run("return '[' .. table.concat({TD.project(100, 50, 270, 110, 47)}, ',') .. ']'"))
    assert along == pytest.approx(10) and side == pytest.approx(3)
    assert float(lua.run("return TD.angleDiff(10, 350)")) == pytest.approx(20)
    assert float(lua.run("return TD.angleDiff(350, 10)")) == pytest.approx(-20)
    assert float(lua.run("return TD.headingOf(1, 0)")) == pytest.approx(270)
    assert lua.run("return TD.round(1/0)") is None and lua.run("return TD.round(-0.0001, 2)") == "0"
    assert lua.run("return TD.formatTime(TD.parseTime('7:05'))") == "07:05" and lua.run("return TD.parseTime('25:00')") is None


def test_series_statistics(lua):
    lua.run("S = {} for i = 0, 40 do S[#S + 1] = {i * 0.1, math.min(i * 5, 120)} end")
    assert float(lua.run("return TD.timeTo(S, 100)")) == pytest.approx(2.0)
    assert lua.run("return TD.timeTo(S, 500)") is None
    assert lua.run("return tostring(TD.plateau(S, 1.0, 1.0))") == "true"
    assert lua.run("return tostring(TD.plateau(S, 3.5, 1.0))") == "false"
    lua.run("Z = {{0, 2}, {0.5, 1.2}, {1.0, 0.95}, {1.5, 1.004}, {2.0, 1.0}, {2.5, 1.0}}")
    assert float(lua.run("return TD.settleTime(Z, 0.01)")) == pytest.approx(1.5)
    assert float(lua.run("return TD.amplitude(Z, 0.9)")) == pytest.approx(0.05)


def test_bone_segments_and_stats(lua):
    lua.run("P = {} for i = 1, 3 do local s = (i == 2) and 1.2 or 1.0 "
            "P[i] = TD.segments(function(b) return 0, 0, (b == 23) and 0.5 * s or b * 0.01 end) end "
            "ST = TD.segmentStats(P)")
    st = json.loads(lua.run("return J(ST)"))
    assert set(st) == {s for s in ("spine", "spine_upper", "neck", "r_upper_arm", "r_forearm", "l_upper_arm",
                                   "l_forearm", "l_thigh", "l_shin", "r_thigh", "r_shin")}
    assert st["spine"]["dev"] == 0 and st["r_upper_arm"]["dev"] > 0.1 and st["r_upper_arm"]["n"] == 3


def test_model_lookup(lua):
    lua.run('M = {models = {{key = "a", kind = "object"}, {key = "b", kind = "vehicle"}}}')
    assert lua.run("return TD.modelOf(M, 'mod').key") == "a"
    assert lua.run("return TD.modelOf(M, 'mod', 'vehicle').key") == "b"
    assert lua.run("return TD.modelOf(M, 'b').key") == "b"
    assert lua.run("return tostring(TD.modelOf(M, 'zz'))") == "nil"


def test_python_and_lua_suites_agree():
    text = (R.logic_source() / "checks.lua").read_text(encoding="utf-8")
    block = text.split("TDC.SUITES = {", 1)[1].split("\n}", 1)[0]
    for kind, names in CH.SUITES.items():
        line = next(ln for ln in block.splitlines() if ln.strip().startswith(kind + " ="))
        assert [n.strip(' "') for n in line.split("{", 1)[1].split("}", 1)[0].split(",")] == names
        for n in names:
            assert f"{ {'vehicle': 'V', 'object': 'O', 'ped': 'P', 'weapon': 'W'}[kind]}.{n} = " in text
            assert (kind, n) in CH.VERDICTS and (kind, n) in CH.ABOUT
