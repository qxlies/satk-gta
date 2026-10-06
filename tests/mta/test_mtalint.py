"""satk.mta.lint: the checks of ``satk mta lint`` on synthetic scripts and resources (synthetic reference)."""

from __future__ import annotations

from pathlib import Path

import pytest

import mtaref_synth
from satk.mta.lint import lint_path, lint_source


@pytest.fixture(scope="module")
def ref():
    return mtaref_synth.load()


def codes(res) -> list[str]:
    return [f.code for f in res.findings]


def _write(root: Path, files: dict[str, str | bytes]) -> Path:
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            p.write_bytes(content)
        else:
            p.write_text(content, encoding="utf-8")
    return root


META = """<meta>
    <script src="client.lua" type="client"/>
    <script src="server.lua" type="server"/>
</meta>
"""


# --------------------------------------------------------------------------- single scripts


@pytest.mark.parametrize("side,code,expected", [
    ("client", "outputChatBoxx('x')", "UNKNOWN_FUNCTION"),
    ("server", "dxDrawText('x', 0, 0)", "WRONG_SIDE"),
    ("client", "kickPlayer(source)", "WRONG_SIDE"),
    ("client", "local v = getPlayerOccupiedVehicle(localPlayer)", "DEPRECATED"),
    ("server", "givePlayerJetPack(source)", "REMOVED"),
    ("client", "local x, y = getComponentPosition(source)", "MIN_VERSION"),
    ("server", "neonOnlyThing(1)", "NEON_ONLY"),
    ("client", "setElementPosition(localPlayer)", "ARG_COUNT"),
    ("client", "outputChatBox('a', root, 1, 2, 3, true, 'extra')", "ARG_COUNT"),
    ("client", "setSoundVolume(source, 'loud')", "ARG_TYPE"),
    ("client", "outputChatBox('a', root, 1, 2, 3, 'yes')", "ARG_TYPE"),
    ("client", "local id = engineRequestModel('vehical')", "ENUM_VALUE"),
    ("client", "local y = notDefinedAnywhere + 1", "UNDEFINED_GLOBAL"),
    ("client", "local r = math.round(1.5)", "LUA_FIELD"),
    ("client", "local u = table.unpack({})", "LUA_FIELD"),
    ("client", "local f = io.open('x')", "MISSING_LIBRARY"),
    ("client", "dofile('x.lua')", "DISABLED_FUNCTION"),
    ("client", "os.execute('x')", "DISABLED_FUNCTION"),
    ("client", "if not a == 1 then end", "NOT_EQ"),
    ("client", "local s = '\\x41'", "ESCAPE_52"),
    ("client", "addEventHandler('onPlayerJoin', root, function() end)", "EVENT_WRONG_SIDE"),
    ("server", "addEventHandler('onCustomThing', root, function() end)", "EVENT_UNKNOWN"),
    ("client", "addEventHandler('onClientRender', root, function() dxCreateFont('a.ttf') end)", "RENDER_HEAVY"),
    ("client", "function f() end\nsetTimer(f(), 50, 1)", "HANDLER_CALLED"),
    ("client", "addEventHandler('onClientResourceStart', root, function() outputChatBox('x') end)",
     "RESOURCE_START_ROOT"),
    ("client", "dxDrawRectangle(0, 0, 10, 10)", "DRAW_OUTSIDE_RENDER"),
    ("client", "if getElementType(source) == 'Player' then end", "ELEMENT_TYPE"),
    ("client", "local list = getElementsByType('vehicels')", "ELEMENT_TYPE"),
    ("client", "function a() end\nfunction a() end", "DUPLICATE_FUNCTION"),
    ("client", "function f()\n  counter = 1\n  return counter\nend", "IMPLICIT_GLOBAL"),
    ("client", "function f( end", "SYNTAX"),
    ("server", "local p = localPlayer", "WRONG_SIDE"),
])
def test_each_check_fires(ref, side, code, expected):
    res = lint_source(code, side=side, ref=ref)
    assert expected in codes(res), [(f.code, f.msg) for f in res.findings]


def test_messages_carry_positions_and_suggestions(ref):
    res = lint_source("local a = 1\n  outputChatBoxx('x')\n", side="client", ref=ref)
    f = next(f for f in res.findings if f.code == "UNKNOWN_FUNCTION")
    assert (f.line, f.col, f.sev) == (2, 3, "error") and "did you mean outputChatBox" in f.msg
    res = lint_source("x = 1\nfunction f()\n  goto skip\nend\n", side="client", ref=ref)
    f = res.findings[0]
    assert f.code == "SYNTAX" and f.line == 3 and "no goto" in f.msg
    res = lint_source("local id = engineRequestModel('Vehicle')", side="client", ref=ref)
    assert "ENUM_VALUE" not in codes(res)                       # enum strings are case-insensitive


CLEAN = """
local screenW, screenH = guiGetScreenSize()
local font
local cache = {}
local function draw()
    if not font then
        font = dxCreateFont("font.ttf", 12)      -- lazy creation under an if: fine
    end
    dxDrawText("hi", screenW / 2, screenH / 2, 0, 0, tocolor(255, 255, 255))
    for _, v in ipairs(getElementsByType("vehicle", root, true)) do cache[v] = true end
end
addEventHandler("onClientRender", root, draw)
addEventHandler("onClientResourceStart", root, function(res)
    if res == getThisResource() then outputChatBox("mine") end
end)
local function maker(x) return function() outputChatBox(x) end end
setTimer(maker("later"), 1000, 1)
local t = setmetatable({}, {__index = function(_, k) return k end})
local v = Vector3(1, 2, 3)
local function va(...) return select("#", ...), arg end
if setfenv and loadstring then local fn = loadstring("return 1") end
local s = ("%d"):format(1) .. string.rep("x", 2) .. utf8.len("x")
local e = createElement("myType")
for _, x in ipairs(getElementsByType("myType")) do outputChatBox(tostring(x)) end
if getElementType(e) == "vehicle" then end
addEvent("my:event", true)
addEventHandler("my:event", resourceRoot, function() iprint(source, eventName) end)
triggerEvent("my:event", resourceRoot)
setElementPosition(localPlayer, 0, 0, 3)
local x, y, z = getElementPosition(localPlayer)
"""


def test_no_findings_on_idiomatic_code(ref):
    res = lint_source(CLEAN, side="client", ref=ref)
    assert res.findings == [], [(f.at, f.code, f.msg) for f in res.findings]


@pytest.mark.parametrize("body", [
    "if visible then dxCreateFont('font.ttf') end",
    "local font = visible and dxCreateFont('font.ttf')",
    "if font then else dxCreateFont('font.ttf') end",
    "local font; if not font then font = dxCreateFont('font.ttf') end",
    "local font; font = font or dxCreateFont('font.ttf')",
])
def test_render_calls_inside_conditions_are_still_heavy(ref, body):
    text = ("local visible = true; local font\n"
            "addEventHandler('onClientRender', root, function()\n" + body + "\nend)")
    heavy = [f for f in lint_source(text, side="client", ref=ref).findings if f.code == "RENDER_HEAVY"]
    assert len(heavy) == 1 and heavy[0].line == 3


@pytest.mark.parametrize("body", [
    "if not font then font = dxCreateFont('font.ttf') end",
    "if not isElement(font) then font = dxCreateFont('font.ttf') end",
    "font = font or dxCreateFont('font.ttf')",
    "if cache.font == nil then cache.font = dxCreateFont('font.ttf') end",
])
def test_render_persistent_lazy_cache_is_not_flagged(ref, body):
    text = "local font; local cache = {}\naddEventHandler('onClientRender', root, function()\n" + body + "\nend)"
    assert "RENDER_HEAVY" not in codes(lint_source(text, side="client", ref=ref))


def test_render_follows_helpers_across_scripts(tmp_path, ref):
    root = _write(tmp_path / "res", {
        "meta.xml": '<meta><script src="util.lua" type="client"/><script src="client.lua" type="client"/></meta>',
        "util.lua": "local function deep() dxCreateFont('font.ttf') end\nfunction helper() deep() end\n",
        "client.lua": "addEventHandler('onClientRender', root, function() helper() end)\n",
    })
    heavy = [f for f in lint_path(root, ref).findings if f.code == "RENDER_HEAVY"]
    assert [(f.file, f.line) for f in heavy] == [("util.lua", 1)]


def test_parenthesized_and_signed_argument_literals(ref):
    res = lint_source("setSoundVolume(source, ('loud'))\nsetElementPosition(-1, 0, 0, 0)", side="client", ref=ref)
    assert [(f.code, f.line) for f in res.findings] == [("ARG_TYPE", 1), ("ARG_TYPE", 2)]
    assert lint_source("setSoundVolume(source, (-0.5))", side="client", ref=ref).findings == []


def test_unknown_side_accepts_either_mta_signature():
    r = mtaref_synth.load()
    r.side("outputChatBox", "client").variants[0].pop(1)  # no recipient on the client
    text = "outputChatBox('hello', root, 255, 255, 255, true)"
    assert lint_source(text, side="any", ref=r).findings == []
    assert "ARG_COUNT" in codes(lint_source(text, side="client", ref=r))
    assert "ARG_TYPE" in codes(lint_source("outputChatBox('hello', true)", side="any", ref=r))


@pytest.mark.parametrize("sides", [("client",), ("server",), ("client", "server")])
@pytest.mark.parametrize("removed", [False, True])
def test_unknown_side_checks_registered_deprecations(sides, removed):
    r = mtaref_synth.load()
    name = "getPlayerOccupiedVehicle"
    r.funcs[name] = {side: r.side("getPedOccupiedVehicle", side) for side in sides}
    for side in sides:
        r.deprecated[side][name] = (removed, "getPedOccupiedVehicle", "")
    res = lint_source(f"{name}(source)", ref=r)
    assert codes(res) == ["REMOVED" if removed else "DEPRECATED"]


def test_unknown_side_does_not_assume_a_side_specific_deprecation():
    r = mtaref_synth.load()
    name = "getPlayerOccupiedVehicle"
    r.funcs[name] = dict(r.funcs["getPedOccupiedVehicle"])
    del r.deprecated["server"][name]
    assert lint_source(f"{name}(source)", ref=r).findings == []
    assert codes(lint_source(f"{name}(source)", side="client", ref=r)) == ["DEPRECATED"]


def test_feature_tests_are_not_uses(ref):
    res = lint_source("if io then end\nif getfenv and setfenv then end\nlocal ok = bit32 == nil\n", side="client",
                      ref=ref)
    assert res.findings == [], [(f.code, f.msg) for f in res.findings]
    res = lint_source("if getfenv then local e = getfenv(1) end\n", side="client", ref=ref)
    assert [(f.code, f.line, f.col) for f in res.findings] == [("DISABLED_FUNCTION", 1, 27)]


def test_ignore_comments(ref):
    res = lint_source("outputChatBoxx('x') -- satk:ignore UNKNOWN_FUNCTION\nfooBar() -- satk:ignore\n",
                      side="client", ref=ref)
    assert res.findings == []
    res = lint_source("-- satk:ignore-file UNKNOWN_FUNCTION\nfooBar()\n", side="client", ref=ref)
    assert res.findings == []


def test_shared_scripts_are_checked_for_both_sides(ref):
    res = lint_source("function g() dxDrawText('x', 0, 0) end\nkickPlayer(source)\n", side="shared", ref=ref)
    sev = {(f.code, f.line): f.sev for f in res.findings}
    assert sev[("WRONG_SIDE", 1)] == "info"           # in a function: may only run on the client
    assert sev[("WRONG_SIDE", 2)] == "error"          # top level: runs on the client too


def test_without_reference_only_lua_checks_run():
    from satk.mta.api import Reference

    res = lint_source("outputChatBoxx('x')\nlocal r = math.round(1)\nfunction f(\n", side="client", ref=Reference())
    assert codes(res) == ["SYNTAX"]
    res = lint_source("outputChatBoxx('x')\nlocal r = math.round(1)\n", side="client", ref=Reference())
    assert codes(res) == ["LUA_FIELD"]


# --------------------------------------------------------------------------- resources


def test_resource_meta_checks(tmp_path, ref):
    root = _write(tmp_path / "res", {
        "meta.xml": """<meta>
    <script src="client.lua" type="client"/>
    <script src="server.lua" type="sever"/>
    <script src="client.lua" type="client"/>
    <script src="gone.lua" type="client"/>
    <file src="../escape.txt"/>
    <file src="img/logo.png"/>
    <file src="car.txd"/>
    <file src="extra.lua"/>
    <Script src="x.lua"/>
    <min_mta_version client="1.5.x"/>
    <export function="exported" type="server"/>
    <export function="nowhere" type="client"/>
</meta>
""",
        "client.lua": "outputChatBox('hi')\n",
        "server.lua": "function exported() return 1 end\n",
        "extra.lua": "return 1\n",
        "unlisted.lua": "x = 1\n",
        "img/logo.png": b"GIF89a....",
        "car.txd": b"\x16\x00\x00\x00\x40\x00\x00\x00\xff\xff\x03\x18" + b"\x00" * 8,
    })
    res = lint_path(root, ref)
    got = {(f.code, f.file) for f in res.findings}
    for want in [("SCRIPT_TYPE", "meta.xml"), ("DUPLICATE_FILE", "meta.xml"), ("FILE_MISSING", "meta.xml"),
                 ("BAD_PATH", "meta.xml"), ("UNKNOWN_TAG", "meta.xml"), ("MIN_VERSION", "meta.xml"),
                 ("EXPORT_MISSING", "meta.xml"), ("LUA_AS_FILE", "meta.xml"), ("NOT_LISTED", "unlisted.lua"),
                 ("FILE_INVALID", "img/logo.png"), ("FILE_INVALID", "car.txd")]:
        assert want in got, (want, sorted(got))
    assert sum(1 for f in res.findings if f.code == "DUPLICATE_FUNCTION") == 0      # the duplicate is skipped
    assert res.findings[0].sev == "error" and res.findings[0].file == "meta.xml"


def test_globals_are_per_side_and_events_cross_sides(tmp_path, ref):
    root = _write(tmp_path / "res", {
        "meta.xml": META.replace('<script src="server.lua" type="server"/>',
                                 '<script src="server.lua" type="server"/>\n    <script src="util.lua" type="client"/>'),
        "util.lua": "function helper() return 1 end\n",
        "client.lua": ("helper()\n"
                       "triggerServerEvent('srv:ok', resourceRoot)\n"
                       "triggerServerEvent('srv:local', resourceRoot)\n"
                       "triggerServerEvent('srv:none', resourceRoot)\n"
                       "addEventHandler('cl:fromServer', resourceRoot, function() end)\n"),
        "server.lua": ("helper()\n"
                       "addEvent('srv:ok', true)\n"
                       "addEvent('srv:local')\n"
                       "addEvent('cl:fromServer', true)\n"),
    })
    res = lint_path(root, ref)
    by = {(f.file, f.line, f.code) for f in res.findings}
    assert ("client.lua", 1, "UNKNOWN_FUNCTION") not in by
    assert ("server.lua", 1, "UNKNOWN_FUNCTION") in by                  # helper is a client global
    assert ("client.lua", 3, "EVENT_NOT_REMOTE") in by
    assert ("client.lua", 4, "EVENT_NOT_REMOTE") in by
    assert ("client.lua", 2, "EVENT_NOT_REMOTE") not in by
    assert ("client.lua", 5, "EVENT_UNKNOWN") in by                     # added only on the server


def test_oop_flag(tmp_path, ref):
    files = {"meta.xml": META, "client.lua": "local p = localPlayer.position\nlocal v = Vector3(1, 2, 3)\n"
                                             "local b = Browser(1, 1, false)\n",
             "server.lua": ""}
    root = _write(tmp_path / "a", files)
    oop = [f for f in lint_path(root, ref).findings if f.code == "OOP_DISABLED"]
    assert {f.line for f in oop} == {1, 3}                              # vectors work without OOP
    root = _write(tmp_path / "b", dict(files, **{"meta.xml": META.replace("</meta>", "    <oop>true</oop>\n</meta>")}))
    assert "OOP_DISABLED" not in codes(lint_path(root, ref))


def test_download_budget_and_encoding(tmp_path, ref):
    root = _write(tmp_path / "res", {
        "meta.xml": META.replace("</meta>", '    <file src="big.bin"/>\n    <file src="opt.bin" download="false"/>\n'
                                            "</meta>"),
        "client.lua": "outputChatBox('caf\xe9')\n".encode("cp1252"),
        "server.lua": "\ufeffoutputChatBox('ok')\n",
        "big.bin": b"\x00" * 300_000,
        "opt.bin": b"\x00" * 900_000,
    })
    res = lint_path(root, ref, budget_mb=0.25)
    assert "DOWNLOAD_SIZE" in codes(res) and "ENCODING" in codes(res)
    assert {r[0] for r in res.downloads} == {"client.lua", "big.bin"}
    assert "SYNTAX" not in codes(res)                                   # the BOM is skipped like MTA does


def test_compiled_scripts_are_skipped(tmp_path, ref):
    root = _write(tmp_path / "res", {"meta.xml": META, "client.lua": b"\x1bLuaQ\x00\x01", "server.lua": ""})
    assert codes(lint_path(root, ref)) == ["COMPILED"]


def test_folder_without_meta_and_single_files(tmp_path, ref):
    root = _write(tmp_path / "loose", {"c_hud.lua": "kickPlayer(source)\n", "s_main.lua": "dxDrawText('x', 0, 0)\n",
                                       "util.lua": "kickPlayer(source)\n"})
    res = lint_path(root, ref)
    by = {(f.file, f.code) for f in res.findings}
    assert ("meta.xml", "META_MISSING") in by
    assert ("c_hud.lua", "WRONG_SIDE") in by and ("s_main.lua", "WRONG_SIDE") in by
    assert ("util.lua", "WRONG_SIDE") not in by                         # side unknown: checked against both
    res = lint_path(root / "util.lua", ref, side="client")
    assert "WRONG_SIDE" in codes(res)


def test_reference_json_round_trip(tmp_path):
    from satk.mta.api import Reference, load_reference

    p = mtaref_synth.write_reference(tmp_path / "ref.json")
    r, warn = load_reference(str(p))
    assert warn == [] and r.side("dxDrawText", "client").exact and r.sides("kickPlayer") == {"server"}
    again = Reference.from_json(r.to_json())
    assert again.to_json() == r.to_json()


def test_deprecated_list_parser():
    from satk.mta.api import parse_deprecated, parse_version

    text = """
    SDeprecatedItem clientDeprecatedList[] = {
        // Client functions
        {false, "getPlayerRotation", "getPedRotation"},
        {false, "getComponentPosition", "will return 3 floats instead of a Vector3", "1.5.5-9.11710"},
        //{false, "commented", "out"},
    };
    SDeprecatedItem serverDeprecatedList[] = {
        {true, "randInt", "math.random"}};
    """
    d = parse_deprecated(text)
    assert d["client"]["getPlayerRotation"] == (False, "getPedRotation", "")
    assert d["client"]["getComponentPosition"][2] == "1.5.5-9.11710" and "commented" not in d["client"]
    assert d["server"] == {"randInt": (True, "math.random", "")}
    assert parse_version("#define MTASA_VERSION_MAJOR 1\n#define MTASA_VERSION_MINOR 6\n"
                         "#define MTASA_VERSION_MAINTENANCE 0\n") == "1.6.0"


def test_meta_version_and_path_rules():
    from satk.mta.resource import valid_path, valid_version

    assert valid_version("1.6.0") and valid_version("1.5.9-9.21000.0") and valid_version("1.6")
    assert not valid_version("1.10.0") and not valid_version("v1.6") and not valid_version("1.5.x")
    assert valid_path("models/car.dff") and valid_path("a b.txt")
    assert not valid_path("../x") and not valid_path("c:/x") and not valid_path("caf\u00e9.png")
