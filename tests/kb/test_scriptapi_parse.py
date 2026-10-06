"""satk.kb.scriptapi parsers on small synthetic snippets (invented code in the MTA/Pawn style; no real sources)."""

from __future__ import annotations

import pytest

from satk.kb import scriptapi as S

CLIENT_DEFS = r"""
#include "StdInc.h"

// A free function bound with ArgumentParser
bool SetThingFlags(std::uint32_t uiModelID, std::uint32_t uiFlags, std::optional<bool> bIdeFlags)
{
    return true;
}

std::variant<bool, CLuaMultiReturn<float, float, float>> GetThingPosition(CClientVehicle* pVehicle)
{
    return false;
}

void CLuaThingDefs::LoadFunctions()
{
    constexpr static const std::pair<const char*, lua_CFunction> functions[]{
        {"thingRequestModel", ThingRequestModel},
        {"thingSetFlags", ArgumentParser<SetThingFlags>},
        {"getThingPosition", ArgumentParserWarn<false, GetThingPosition>},
        {"thingPick", ArgumentParser<PickByName, PickById>},
        // {"thingDisabled", ThingDisabled},
    };
    /*
    CLuaCFunctions::AddFunction("thingCommented", ThingCommented);
    */
    for (const auto& [name, func] : functions)
        CLuaCFunctions::AddFunction(name, func);
}

void CLuaThingDefs::AddClass(lua_State* luaVM)
{
    lua_newclass(luaVM);
    lua_classfunction(luaVM, "setFlags", "thingSetFlags");
    lua_classfunction(luaVM, "getPosition", OOP_GetThingPosition);
    lua_classvariable(luaVM, "flags", "thingSetFlags", nullptr);
    lua_registerstaticclass(luaVM, "Thing");
}

bool PickByName(std::string strName) { return true; }
bool PickById(int iId, std::optional<float> fScale) { return true; }

int CLuaThingDefs::ThingRequestModel(lua_State* luaVM)
{
    //  int thingRequestModel ( string type [, int parent ] )
    eThingType eType;
    CScriptArgReader argStream(luaVM);
    argStream.ReadEnumString(eType);
    ushort usParentID = 7;
    if (argStream.NextIsNumber())
        argStream.ReadNumber(usParentID);
    if (!argStream.HasErrors())
    {
        lua_pushinteger(luaVM, 400);
        return 1;
    }
    lua_pushboolean(luaVM, false);
    return 1;
}

IMPLEMENT_ENUM_CLASS_BEGIN(eThingType)
ADD_ENUM(eThingType::PED, "ped")
ADD_ENUM(eThingType::OBJECT, "object")
IMPLEMENT_ENUM_CLASS_END("thing-type")
"""

SHARED_DEFS = r"""
void CLuaSharedThing::LoadFunctions()
{
    constexpr static const std::pair<const char*, lua_CFunction> functions[]{
        {"sharedThing", SharedThing},
#ifdef MTA_CLIENT
        {"clientOnlyThing", SharedThing},
#else
        {"serverOnlyThing", SharedThing},
#endif
#if 0
        {"deadThing", SharedThing},
#endif
    };
}

int CLuaSharedThing::SharedThing(lua_State* luaVM)
{
    SString strText;
    float   fX, fY, fZ = 1.0f;
    CScriptArgReader argStream(luaVM);
    argStream.ReadString(strText, "a,b");
    argStream.ReadNumber(fX);
    argStream.ReadNumber(fY, false);
    if (argStream.NextIsTable())
        argStream.ReadTable(fZ);
    else
        argStream.Skip(1);
    lua_pushnumber(luaVM, 1);
    lua_pushnumber(luaVM, 2);
    return 2;
}
"""


@pytest.fixture(scope="module")
def api():
    files = {"Client/mods/deathmatch/logic/luadefs/CLuaThingDefs.cpp": CLIENT_DEFS,
             "Shared/mods/deathmatch/logic/luadefs/CLuaSharedThing.cpp": SHARED_DEFS,
             "Client/mods/deathmatch/logic/CClientGame.cpp":
                 'void CClientGame::AddBuiltInEvents()\n{\n    m_Events.AddEvent("onClientThing", "thing, count", NULL, false);\n}\n',
             "Server/mods/deathmatch/logic/CGame.cpp":
                 'void CGame::AddBuiltInEvents()\n{\n    m_Events.AddEvent("onThing", "", NULL, false);\n}\n'}
    return S.parse_mta(files)


def _fn(api, name, side="client"):
    return next(f for f in api.funcs if f.name == name and f.side == side)


def test_registrations_sides_and_comments(api):
    names = {(f.name, f.side) for f in api.funcs}
    assert ("thingRequestModel", "client") in names
    assert ("sharedThing", "client") in names and ("sharedThing", "server") in names
    assert ("clientOnlyThing", "client") in names and ("clientOnlyThing", "server") not in names
    assert ("serverOnlyThing", "server") in names and ("serverOnlyThing", "client") not in names
    assert not any(n in {"thingDisabled", "thingCommented", "deadThing"} for n, _s in names)


def test_argument_parser_types(api):
    f = _fn(api, "thingSetFlags")
    assert f.parser == "argparser" and f.ret == "bool"
    assert S.render_sig(f.name, f.variants[0], f.ret) == "bool thingSetFlags(int modelID, int flags, [bool ideFlags])"
    g = _fn(api, "getThingPosition")
    assert g.ret == "float, float, float|bool"            # variant<bool, CLuaMultiReturn<...>>
    assert [a.type for a in g.variants[0]] == ["vehicle"]
    p = _fn(api, "thingPick")
    assert len(p.variants) == 2 and p.flags["overloads"] == 2
    assert [a.render() for a in p.variants[1]] == ["int id", "[float scale]"]


def test_argreader_enum_optional_and_doc(api):
    f = _fn(api, "thingRequestModel")
    assert f.parser == "argreader" and f.flags.get("approx")
    assert S.render_sig(f.name, f.variants[0], f.ret) == "int|false thingRequestModel(string type, [int parentID])"
    assert f.enums == ["eThingType"]
    assert f.doc_sig == "int thingRequestModel (string type [, int parent ])"


def test_argreader_defaults_quotes_skip_and_multi_return(api):
    f = _fn(api, "sharedThing", "server")
    rendered = [a.render() for a in f.variants[0]]
    # a comma inside a string default stays one argument; ReadNumber(x, false) is no default;
    # Skip(1) in the else branch is "no value" for the same position
    assert rendered == ['[string text = "a,b"]', "float x", "float y", "[table z]"]
    assert f.ret == "number, number"


def test_oop_events_enums(api):
    oop = {(o.cls, o.member, o.kind): o for o in api.oop}
    assert oop[("Thing", "setFlags", "method")].func == "thingSetFlags"
    gp = oop[("Thing", "getPosition", "method")]
    assert gp.func == "getThingPosition" and gp.impl == "OOP_GetThingPosition"   # OOP_X -> x by convention
    assert oop[("Thing", "flags", "var")].setter == "thingSetFlags"
    assert [c.name for c in api.classes] == ["Thing"] and api.classes[0].static
    ev = {(e.name, e.side): e.params for e in api.events}
    assert ev == {("onClientThing", "client"): "thing, count", ("onThing", "server"): ""}
    assert [(e.name, e.ctype, e.values) for e in api.enums] == [("thing-type", "eThingType", ["ped", "object"])]
    assert api.stats["functions"] == len(api.funcs) and api.stats["events"] == 2


@pytest.mark.parametrize("cpp,lua", [
    ("const std::string&", ("string", False)), ("std::optional<CVector>", ("Vector3", True)),
    ("std::variant<CClientPed*, CClientVehicle*>", ("ped|vehicle", False)), ("std::vector<int>", ("table", False)),
    ("CLuaFunctionRef", ("function", False)), ("lua_State*", ("", False)), ("CResource*", ("resource", False)),
    ("CClientSomething*", ("something", False)), ("CLuaMultiReturn<int, bool>", ("int, bool", False)),
    ("std::uint8_t", ("int", False)), ("double", ("float", False)),
])
def test_lua_type(cpp, lua):
    assert S.lua_type(cpp) == lua


def test_lua_type_known_enum_is_string():
    assert S.lua_type("eThingType", {"eThingType": "thing-type"}) == ("string", False)


@pytest.mark.parametrize("cpp,lua", [("uiModelID", "modelID"), ("pVehicle", "vehicle"), ("strName", "name"),
                                     ("bEnabled", "enabled"), ("fX", "x"), ("m_iCount", "count"), ("theElement",
                                                                                                    "theElement"),
                                     ("strID", "ID"), ("vecPosition", "position")])
def test_lua_name(cpp, lua):
    assert S.lua_name(cpp) == lua


INC = """
/*
native FakeDocNative(a, b);
*/
#define MAX_THINGS (1000)
#define THING_SIZE_256x128 90
#define THING_NAME "thing"
#define NOT_A_CONST(%0) (%0 + 1)
native SetThingMaterial(thingid, materialindex, modelid, const txdname[], const texturename[], materialcolor = 0);
native Float:GetThingDistance(thingid, &Float:x, &Float:y = 0.0, &Float:z = 0.0); // a comment
native bool:IsThing({Float, _}:value, ...);
#pragma deprecated Use SetThingMaterial
native BAD_SetThingMaterial(thingid) = SetThingMaterial;
native PrintThings(const format[], {Float,_}:...);
forward OnThingCreated(thingid, const name[]);
"""


def test_parse_pawn():
    syms = {(s.kind, s.name): s for s in S.parse_pawn(INC, "a_things.inc")}
    assert ("native", "FakeDocNative") not in syms                           # commented out
    m = syms[("native", "SetThingMaterial")]
    assert S.render_pawn(m) == ("native SetThingMaterial(thingid, materialindex, modelid, const txdname[], "
                                "const texturename[], materialcolor = 0)")
    assert m.line == 9 and m.path == "a_things.inc"
    d = syms[("native", "GetThingDistance")]
    assert d.tag == "Float" and [(p.name, p.tag, p.ref, p.default) for p in d.params] == [
        ("thingid", "", False, None), ("x", "Float", True, None), ("y", "Float", True, "0.0"), ("z", "Float", True, "0.0")]
    assert syms[("native", "IsThing")].params[0].tag == "{Float,_}"
    assert syms[("native", "IsThing")].params[1].name == "..."
    bad = syms[("native", "BAD_SetThingMaterial")]
    assert bad.alias == "SetThingMaterial" and bad.deprecated
    assert syms[("native", "PrintThings")].params[1].render() == "{Float,_}:..."
    cb = syms[("callback", "OnThingCreated")]
    assert S.render_pawn(cb) == "forward OnThingCreated(thingid, const name[])"
    consts = {s.name: s.value for s in syms.values() if s.kind == "const"}
    assert consts == {"MAX_THINGS": "(1000)", "THING_SIZE_256x128": "90", "THING_NAME": '"thing"'}
