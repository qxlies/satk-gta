"""``satk kb mta`` / ``satk kb native`` end to end: synthetic MTA and Pawn trees -> kb build hook -> CLI.

The trees are invented code in the MTA/Pawn style, written into ``tmp_path`` by the test (nothing from the real
sources). The KB is built with only the scripting API inputs (the other donors are absent).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from satk.re.gitsrc import DirTree

ENGINE_DEFS = r"""
#include "StdInc.h"

bool EngineSetThingFlags(std::uint32_t uiModelID, std::uint32_t uiFlags, std::optional<bool> bIdeFlags)
{
    return true;
}

void CLuaEngineDefs::LoadFunctions()
{
    constexpr static const std::pair<const char*, lua_CFunction> functions[]{
        {"engineRequestThing", EngineRequestThing},
        {"engineSetThingFlags", ArgumentParser<EngineSetThingFlags>},
    };
    for (const auto& [name, func] : functions)
        CLuaCFunctions::AddFunction(name, func);
}

void CLuaEngineDefs::AddClass(lua_State* luaVM)
{
    lua_newclass(luaVM);
    lua_classfunction(luaVM, "setThingFlags", "engineSetThingFlags");
    lua_registerstaticclass(luaVM, "Engine");
}

int CLuaEngineDefs::EngineRequestThing(lua_State* luaVM)
{
    //  int engineRequestThing ( string modelType [, int parentID ] )
    eThingModelType eModelType;
    CScriptArgReader argStream(luaVM);
    argStream.ReadEnumString(eModelType);
    if (!argStream.HasErrors())
    {
        ushort usParentID = 7;
        if (argStream.NextIsNumber())
            argStream.ReadNumber(usParentID);
        lua_pushinteger(luaVM, 400);
        return 1;
    }
    lua_pushboolean(luaVM, false);
    return 1;
}
"""

PARSE_HELPERS = r"""
IMPLEMENT_ENUM_CLASS_BEGIN(eThingModelType)
ADD_ENUM(eThingModelType::PED, "ped")
ADD_ENUM(eThingModelType::OBJECT, "object")
ADD_ENUM(eThingModelType::VEHICLE, "vehicle")
IMPLEMENT_ENUM_CLASS_END("thing-model-type")
"""

SERVER_HANDLING = r"""
void CLuaHandlingDefs::LoadFunctions()
{
    constexpr static const std::pair<const char*, lua_CFunction> functions[]{
        {"setVehicleThing", SetVehicleThing},
    };
    for (const auto& [name, func] : functions)
        CLuaCFunctions::AddFunction(name, func);
    CLuaCFunctions::AddFunction("restartThings", RestartThings, true);
}

int CLuaHandlingDefs::SetVehicleThing(lua_State* luaVM)
{
    //  bool setVehicleThing ( vehicle theVehicle, string property, var value )
    CVehicle* pVehicle;
    SString   strProperty;
    float     fValue;
    CScriptArgReader argStream(luaVM);
    argStream.ReadUserData(pVehicle);
    argStream.ReadString(strProperty);
    argStream.ReadNumber(fValue);
    lua_pushboolean(luaVM, true);
    return 1;
}

int CLuaHandlingDefs::RestartThings(lua_State* luaVM)
{
    lua_pushboolean(luaVM, true);
    return 1;
}

void CLuaVehicleDefs::AddClass(lua_State* luaVM)
{
    lua_newclass(luaVM);
    lua_classfunction(luaVM, "setThing", "setVehicleThing");
    lua_classvariable(luaVM, "thing", "setVehicleThing", nullptr);
    lua_registerclass(luaVM, "Vehicle", "Element");
}
"""

CLIENT_VEHICLE = r"""
void CLuaVehicleDefs::LoadFunctions()
{
    constexpr static const std::pair<const char*, lua_CFunction> functions[]{
        {"setVehicleThing", SetVehicleThing},
    };
}

int CLuaVehicleDefs::SetVehicleThing(lua_State* luaVM)
{
    CClientVehicle* pVehicle;
    SString         strProperty;
    CScriptArgReader argStream(luaVM);
    argStream.ReadUserData(pVehicle);
    argStream.ReadString(strProperty);
    lua_pushboolean(luaVM, true);
    return 1;
}
"""

CLIENT_GAME = 'void CClientGame::AddBuiltInEvents()\n{\n    m_Events.AddEvent("onClientThingStreamIn", "", NULL, false);\n}\n'
SERVER_GAME = 'void CGame::AddBuiltInEvents()\n{\n    m_Events.AddEvent("onPlayerThing", "thing, amount", NULL, false);\n}\n'

NEON_EXTRA = r"""
void CLuaNeonDefs::LoadFunctions()
{
    constexpr static const std::pair<const char*, lua_CFunction> functions[]{
        {"neonOnlyThing", ArgumentParser<NeonOnlyThing>},
    };
}

bool NeonOnlyThing(float fAmount) { return true; }
"""

A_THINGS = """#define MAX_THINGS (1000)
native SetThingMaterial(thingid, materialindex, modelid, const txdname[], const texturename[], materialcolor = 0);
native Float:GetThingDistance(thingid, &Float:x, &Float:y, &Float:z);
forward OnThingCreated(thingid);
"""
STREAMER = """native STREAMER_TAG_OBJECT:CreateDynamicThing(modelid, Float:x, Float:y, Float:z, worldid = -1);
native SetThingMaterial(thingid, materialindex, modelid, const txdname[], const texturename[], materialcolor = 0);
"""


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root


def _mta_tree(root: Path, extra: dict[str, str] | None = None) -> Path:
    files = {
        "Client/mods/deathmatch/logic/luadefs/CLuaEngineDefs.cpp": ENGINE_DEFS,
        "Client/mods/deathmatch/logic/luadefs/CLuaVehicleDefs.cpp": CLIENT_VEHICLE,
        "Client/mods/deathmatch/logic/lua/CLuaFunctionParseHelpers.cpp": PARSE_HELPERS,
        "Client/mods/deathmatch/logic/CClientGame.cpp": CLIENT_GAME,
        "Server/mods/deathmatch/logic/luadefs/CLuaHandlingDefs.cpp": SERVER_HANDLING,
        "Server/mods/deathmatch/logic/CGame.cpp": SERVER_GAME,
        "Client/game_sa/Unrelated.cpp": '{"notLua", Nothing},\n',
    }
    files.update(extra or {})
    return _write(root, files)


@pytest.fixture
def built(tmp_path, satk_home):
    from satk.kb.build import Inputs, build_kb
    from satk.kb.query import kb_path
    from satk.kb.scriptapi_build import ScriptApiInputs

    mta = _mta_tree(tmp_path / "mta")
    neon = _mta_tree(tmp_path / "neon", {"Client/mods/deathmatch/logic/luadefs/CLuaNeonDefs.cpp": NEON_EXTRA,
                                         "Client/mods/deathmatch/logic/CClientNeon.cpp":
                                             'void X()\n{\n    m_Events.AddEvent("onClientNeonThing", "a", NULL, false);\n}\n'})
    inc = _write(tmp_path / "inc", {"a_things.inc": A_THINGS, "plugins/streamer.inc": STREAMER,
                                    "shader.inc": "float4 main() : COLOR { return 0; }\n"})
    sa = ScriptApiInputs(mta=DirTree(mta), neon=DirTree(neon), pawn=[("inc", DirTree(inc))],
                         repos={"mta-lua": "mta", "mta-lua-neon": "neon"})
    out = kb_path()
    stats = build_kb(out, Inputs(scriptapi=sa))
    return out, stats


def _j(res):
    assert res.code == 0, res.out + res.err
    return json.loads(res.out)


def _err(res):
    assert res.code != 0, res.out
    return json.loads(res.out)["error"]


def test_build_counts_and_sources(built):
    out, st = built
    sa = st["scriptapi"]
    assert sa["mta-lua"]["functions"] == 5 and sa["mta-lua"]["names"] == 4
    assert sa["mta-lua"]["client"] == 3 and sa["mta-lua"]["server"] == 2 and sa["mta-lua"]["argparser"] == 1
    assert sa["mta-lua-neon"] == {"functions": 1, "events": 1, "oop": 0}
    assert sa["pawn"] == {"files": 2, "natives": 4, "callbacks": 1, "consts": 1}
    assert sa["counts"]["mta_func"] == 6 and sa["counts"]["mta_event"] == 3 and sa["counts"]["pawn_sym"] == 6
    with sqlite3.connect(out) as con:
        keys = [r[0] for r in con.execute("SELECT key FROM source ORDER BY id")]
        assert keys == ["facts", "mta-lua", "mta-lua-neon", "pawn"]
        assert con.execute("SELECT repo FROM source WHERE key = 'pawn'").fetchone()[0] == "inc"
        kinds = {r[0] for r in con.execute("SELECT DISTINCT kind FROM sym")}
        assert {"lua", "lua-event", "native", "callback", "const"} <= kinds
    assert "pawn" not in st["skipped"]


def test_kb_build_op_reports_counts(tmp_path, satk_home, run_cli, monkeypatch):
    """``satk kb build`` with only an MTA tree under paths.engine and an include folder from the environment."""
    import subprocess

    eng = _mta_tree(satk_home / "engine" / "mtasa")
    for args in (["init", "-q"], ["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"]):
        subprocess.run(["git", "-C", str(eng), *args], check=True, capture_output=True)
    inc = _write(tmp_path / "pawno" / "include", {"a_things.inc": A_THINGS})
    monkeypatch.setenv("SATK_KB_PAWN_INCLUDE", str(inc))
    env = _j(run_cli(["kb", "build", "--no-research", "--json"]))
    assert env["scriptapi"]["mta_func"] == 5 and env["scriptapi"]["pawn_sym"] == 4
    assert not any("SKIPPED: pawn:" in w for w in env.get("warn", []))
    ov = _j(run_cli(["kb", "mta", "--json"]))
    assert ov["functions"] == {"total": 4, "client": 2, "server": 1, "shared": 1, "neon_only": 0}
    assert ov["sources"]["mta-lua"] == "engine/mtasa" and ov["natives"] == 2


def test_function_lookup_side_enums_oop(built, run_cli):
    env = _j(run_cli(["kb", "mta", "engineRequestThing", "--json"]))
    assert env["side"] == "client" and env["sig"] == "int|false engineRequestThing(string modelType, [int parentID])"
    assert env["enums"] == {"thing-model-type": "ped|object|vehicle"}
    assert env["doc"] == "int engineRequestThing (string modelType [, int parentID ])"
    assert env["loc"] == "Client/mods/deathmatch/logic/luadefs/CLuaEngineDefs.cpp:26"
    assert env["impl"] == "EngineRequestThing (CScriptArgReader)" and env["repo"] == "mta"
    env = _j(run_cli(["kb", "mta", "engineSetThingFlags", "--json"]))
    assert env["sig"] == "bool engineSetThingFlags(int modelID, int flags, [bool ideFlags])"
    assert env["oop"] == ["Engine.setThingFlags"] and "notes" not in env


def test_shared_function_shows_both_sides(built, run_cli):
    env = _j(run_cli(["kb", "mta", "setvehiclething", "--json"]))         # case-insensitive
    assert env["name"] == "setVehicleThing" and env["side"] == "shared"
    assert env["sig"] == "bool setVehicleThing(vehicle vehicle, string property)"
    assert env["server"]["sig"] == "bool setVehicleThing(vehicle vehicle, string property, float value)"
    assert env["server"]["doc"].startswith("bool setVehicleThing (vehicle")
    assert env["oop"] == ["Vehicle:setThing", "Vehicle.thing (set)"]
    via = _j(run_cli(["kb", "mta", "Vehicle:setThing", "--json"]))
    assert via["name"] == "setVehicleThing" and via["via"] == "Vehicle.setThing (method)"
    r = _j(run_cli(["kb", "mta", "restartThings", "--json"]))
    assert r["side"] == "server" and "ACL-restricted by default" in r["notes"]


def test_neon_only_and_class_enum_event(built, run_cli):
    n = _j(run_cli(["kb", "mta", "neonOnlyThing", "--json"]))
    assert n["src"] == "mta-lua-neon" and n["notes"] == ["Neon fork only (not in MTA upstream)"]
    assert n["sig"] == "bool neonOnlyThing(float amount)"
    c = _j(run_cli(["kb", "mta", "Vehicle", "--json"]))
    assert c["kind"] == "class" and c["parent"] == "Element"
    assert c["members"]["rows"] == [["setThing", "method", "server", "setVehicleThing"],
                                    ["thing", "var", "server", "set setVehicleThing"]]
    e = _j(run_cli(["kb", "mta", "thing-model-type", "--json"]))
    assert e["values"] == ["ped", "object", "vehicle"] and e["ctype"] == "eThingModelType"
    ev = _j(run_cli(["kb", "mta", "--event", "onPlayerThing", "--json"]))
    assert ev["params"] == "thing, amount" and ev["side"] == "server"
    assert ev["hint"] == 'addEventHandler("onPlayerThing", root, function(thing, amount) ... end)'
    lst = _j(run_cli(["kb", "mta", "--event", "--json"]))
    assert [r[0] for r in lst["rows"]] == ["onClientNeonThing", "onClientThingStreamIn", "onPlayerThing"]


def test_words_typos_and_errors(built, run_cli):
    t = _j(run_cli(["kb", "mta", "vehicle thing", "--json"]))
    assert t["cols"] == ["kind", "name", "side", "sig"] and [r[1] for r in t["rows"]] == ["setVehicleThing"]
    t = _j(run_cli(["kb", "mta", "thing", "--side", "server", "--json"]))
    assert {r[1] for r in t["rows"]} == {"setVehicleThing", "restartThings", "onPlayerThing"}
    e = _err(run_cli(["kb", "mta", "engineRequestThnig", "--json"]))
    assert e["code"] == "NOT_FOUND" and e["did_you_mean"][0] == "engineRequestThing"
    e = _err(run_cli(["kb", "mta", "--event", "onPlayerThnig", "--json"]))
    assert e["did_you_mean"] == ["onPlayerThing"]
    e = _err(run_cli(["kb", "mta", "x", "--side", "both", "--json"]))
    assert e["code"] == "BAD_PARAMS"


def test_native_lookup_words_typos(built, run_cli):
    env = _j(run_cli(["kb", "native", "SetThingMaterial", "--json"]))
    assert env["sig"] == ("native SetThingMaterial(thingid, materialindex, modelid, const txdname[], "
                          "const texturename[], materialcolor = 0)")
    assert env["params"] == 6 and env["inc"] == "a_things" and env["loc"] == "inc/a_things.inc:2"
    assert env["also"] == ["streamer"]                    # stock includes first, plugins after
    d = _j(run_cli(["kb", "native", "GetThingDistance", "--json"]))
    assert d["ret"] == "Float" and d["sig"].endswith("(thingid, &Float:x, &Float:y, &Float:z)")
    cb = _j(run_cli(["kb", "native", "OnThingCreated", "--json"]))
    assert cb["kind"] == "callback" and cb["sig"] == "forward OnThingCreated(thingid)"
    k = _j(run_cli(["kb", "native", "MAX_THINGS", "--json"]))
    assert k["kind"] == "const" and k["value"] == "(1000)"
    w = _j(run_cli(["kb", "native", "thing material", "--json"]))
    assert [r[1] for r in w["rows"]] == ["SetThingMaterial"]
    e = _err(run_cli(["kb", "native", "SetThingMaterail", "--json"]))
    assert e["code"] == "NOT_FOUND" and e["did_you_mean"][0] == "SetThingMaterial"
    lst = _j(run_cli(["kb", "native", "--json"]))
    assert lst["rows"] == [["a_things", 2, 1, 1], ["streamer", 2, 0, 0]]
    only = _j(run_cli(["kb", "native", "dynamic", "--inc", "streamer.inc", "--json"]))
    assert [r[1] for r in only["rows"]] == ["CreateDynamicThing"]


def test_kb_search_finds_script_api(built, run_cli):
    env = _j(run_cli(["kb", "search", "engineRequestThing", "--json"]))
    first = dict(zip(env["cols"], env["rows"][0]))
    assert first["kind"] == "lua" and first["src"] == "mta-lua" and "engineRequestThing(string modelType" in first["info"]
    env = _j(run_cli(["kb", "search", "SetThingMaterial", "--kind", "sym", "--json"]))
    assert env["rows"][0][0] == "native"


def test_old_kb_without_tables_is_not_ready(tmp_path, satk_home, run_cli):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from kb_synth import make_inputs, make_world

    from satk.kb.build import build_kb
    from satk.kb.query import kb_path

    build_kb(kb_path(), make_inputs(make_world(tmp_path / "w")))     # a KB from before this feature
    e = _err(run_cli(["kb", "mta", "engineRequestModel", "--json"]))
    assert e["code"] == "NOT_READY" and e["hint"] == "satk kb build"


def test_builtin_natives_without_includes(tmp_path, satk_home, run_cli):
    from satk.kb.build import Inputs, build_kb
    from satk.kb.query import kb_path
    from satk.kb.scriptapi_build import ScriptApiInputs

    st = build_kb(kb_path(), Inputs(scriptapi=ScriptApiInputs(mta=DirTree(_mta_tree(tmp_path / "m")))))
    assert st["scriptapi"]["pawn"]["builtin"] >= 10 and "kb.pawn_include" in st["skipped"]["pawn"]
    env = _j(run_cli(["kb", "native", "SetObjectMaterial", "--json"]))
    assert env["inc"] == "satk-mapconv" and "loc" not in env and "kb.pawn_include" in env["notes"][0]
    assert env["sig"].startswith("native SetObjectMaterial(obj, slot, model, txd[], tex[], color = 0")


def test_no_sources_at_all_is_not_found(satk_home):
    from satk.core.errors import SatkError
    from satk.kb.build import Inputs, build_kb
    from satk.kb.query import kb_path
    from satk.kb.scriptapi_build import ScriptApiInputs

    with pytest.raises(SatkError) as ei:
        build_kb(kb_path(), Inputs(scriptapi=ScriptApiInputs()))
    assert ei.value.code == "NOT_FOUND"


def test_default_inputs_config_and_discovery(tmp_path, satk_home, monkeypatch):
    """kb.pawn_include (a missing folder is reported), SATK_KB_PAWN_INCLUDE and <src>/<clone>/pawno/include."""
    from satk.core import config as _config
    from satk.kb.scriptapi_build import default_inputs

    a = _write(tmp_path / "a", {"x.inc": A_THINGS})
    b = _write(tmp_path / "b", {"y.inc": STREAMER})
    _write(satk_home / "src" / "my-gamemode" / "pawno" / "include", {"a_things.inc": A_THINGS})
    toml = tmp_path / "satk.toml"
    toml.write_text(f"[kb]\npawn_include = ['{a.as_posix()}', '{(tmp_path / 'nope').as_posix()}']\n",
                    encoding="utf-8")
    monkeypatch.setenv("SATK_CONFIG", str(toml))
    _config.reset()
    inp = default_inputs()
    assert [lb for lb, _t in inp.pawn] == [a.as_posix(), "src/my-gamemode/pawno/include"]
    assert "nope" in inp.skipped["pawn:missing"]
    assert inp.mta is None and "mta-lua" in inp.skipped
    monkeypatch.setenv("SATK_KB_PAWN_INCLUDE", str(b))       # the environment overrides the file value
    _config.reset()
    assert [lb for lb, _t in default_inputs().pawn] == [b.as_posix(), "src/my-gamemode/pawno/include"]
    monkeypatch.setenv("SATK_CONFIG", "none")                 # and works without any file
    _config.reset()
    assert [lb for lb, _t in default_inputs().pawn] == [b.as_posix(), "src/my-gamemode/pawno/include"]
