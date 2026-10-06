"""Execute generated loaders with MTA's Lua 5.1 DLL and small engine stubs; no client/server process."""

from __future__ import annotations

import pytest

from luart import Lua
from satk.mta.pack import render_pack

pytestmark = pytest.mark.engine


STUBS = r'''
resourceName = "pack"
root, resourceRoot = {}, {}
calls, handlers, commands, elements = {}, {}, {}, {}
localPlayer = {kind = "player", model = 7, data = {}, streamed = false}
elements[1] = localPlayer
nextId = 20000

local function record(name) calls[#calls + 1] = name end
function isElement(e) return type(e) == "table" and e.kind ~= nil end
function getElementType(e) assert(isElement(e)); return e.kind end
function getElementModel(e) assert(isElement(e)); return e.model end
function setElementModel(e, id)
    assert(isElement(e))
    if failSetModel then return false end
    e.model = id
    return true
end
function getElementData(e, key, inherit)
    assert(inherit == false, "pack data must not be inherited from a parent")
    return e.data[key]
end
function setElementData(e, key, value) e.data[key] = value; return true end
function isElementStreamedIn(e) return e.streamed end
function getElementsByType(kind, parent, streamed)
    assert(parent == root and streamed == true)
    local out = {}
    for _, e in ipairs(elements) do
        if e.kind == kind and e.streamed then out[#out + 1] = e end
    end
    return out
end
function addEventHandler(event, parent, fn) handlers[event] = fn end
function addCommandHandler(name, fn) commands[name] = fn end
function outputDebugString(message, level) lastError = message end
function outputChatBox() end
function getPlayerAccount(player) assert(isElement(player)); return "account" end
function isGuestAccount() return false end
function getAccountName() return "admin" end
function aclGetGroup() return false end
function isObjectInACLGroup(_, group) assert(group); return true end
function createVehicle(parent) return {kind = "vehicle", model = parent, data = {}} end
function createObject(parent) return {kind = "object", model = parent, data = {}} end
function engineRequestModel(kind, parent)
    record("request:" .. kind .. ":" .. parent)
    if failRequest then return false end
    allocated = nextId
    nextId = nextId + 1
    return allocated
end
function engineFreeModel(id) record("free:" .. id); freed = id; return true end
function engineLoadCOL(path) record("col:" .. path); return {kind = "col"} end
function engineReplaceCOL(col, id) assert(col.kind == "col"); record("replace-col:" .. id); return true end
function engineLoadTXD(path) record("txd:" .. path); return {kind = "txd"} end
function engineImportTXD(txd, id) assert(txd.kind == "txd"); record("import-txd:" .. id); return true end
function engineLoadDFF(path)
    record("dff:" .. path)
    return {kind = "dff"}
end
function engineReplaceModel(dff, id)
    assert(dff.kind == "dff")
    record("replace-model:" .. id)
    return not failReplace
end
function engineSetModelLODDistance(id, distance) record("lod:" .. id .. ":" .. distance); return true end
function destroyElement(e) e.destroyed = true; record("destroy:" .. e.kind); return true end
'''


@pytest.fixture
def lua(lua_dll):
    rt = Lua(lua_dll)
    rt.run(STUBS)
    try:
        yield rt
    finally:
        rt.close()


def _scripts(kind, new_id=True):
    parent = {"vehicle": 400, "skin": 7, "object": 1337}[kind]
    rows = [{"name": "custom", "col": "models/custom.col", "txd": "models/custom.txd",
             "dff": "models/custom.dff", "lod": 250,
             **({"parent": parent} if new_id else {"replace": parent})}]
    return render_pack("pack", kind, rows, new_id)


@pytest.mark.parametrize("kind", ["vehicle", "skin", "object"])
@pytest.mark.parametrize("new_id", [False, True])
def test_generated_loader_calls_engine_in_order(lua, kind, new_id):
    files = _scripts(kind, new_id)
    lua.run(files["models.lua"], "models.lua")
    lua.run(files["client.lua"], "client.lua")
    lua.run("handlers.onClientResourceStart()")
    parent = {"vehicle": 400, "skin": 7, "object": 1337}[kind]
    mid = 20000 if new_id else parent
    expected = ([f"request:{'ped' if kind == 'skin' else kind}:{parent}"] if new_id else []) + [
        "col:models/custom.col", f"replace-col:{mid}", "txd:models/custom.txd", f"import-txd:{mid}",
        "dff:models/custom.dff", f"replace-model:{mid}", f"lod:{mid}:250",
    ]
    assert lua.run("return table.concat(calls, '|')") == "|".join(expected)


@pytest.mark.parametrize("kind", ["vehicle", "skin", "object"])
def test_new_ids_apply_only_to_this_pack_and_matching_element_types(lua, kind):
    files = _scripts(kind)
    element_kind = "ped" if kind == "skin" else kind
    lua.run(files["models.lua"], "models.lua")
    lua.run(f'''
        existing = {{kind = "{element_kind}", model = 1, data = {{[MODEL_DATA_KEY] = "custom"}}, streamed = true}}
        otherPack = {{kind = "{element_kind}", model = 2, data = {{["satk:other:model"] = "custom"}}, streamed = true}}
        wrongType = {{kind = "marker", model = 3, data = {{[MODEL_DATA_KEY] = "custom"}}, streamed = true}}
        pending = {{kind = "{element_kind}", model = 4, data = {{[MODEL_DATA_KEY] = "custom"}}, streamed = false}}
        elements = {{localPlayer, existing, otherPack, wrongType, pending}}
    ''')
    lua.run(files["client.lua"], "client.lua")
    lua.run("handlers.onClientResourceStart()")
    assert lua.run("return existing.model .. ',' .. otherPack.model .. ',' .. wrongType.model .. ',' .. pending.model") == \
        "20000,2,3,4"
    lua.run('source = pending; handlers.onClientElementDataChange(MODEL_DATA_KEY)')
    assert lua.run("return pending.model") == "4"
    lua.run('pending.streamed = true; handlers.onClientElementStreamIn()')
    assert lua.run("return pending.model") == "20000"
    lua.run('source = wrongType; handlers.onClientElementStreamIn()')
    assert lua.run("return wrongType.model") == "3"
    lua.run('source = otherPack; handlers.onClientElementDataChange("satk:other:model")')
    assert lua.run("return otherPack.model") == "2"


def test_skin_pack_applies_to_existing_remote_and_local_players(lua):
    files = _scripts("skin")
    lua.run(files["models.lua"], "models.lua")
    lua.run('''
        localPlayer.data[MODEL_DATA_KEY] = "custom"
        remote = {kind = "player", model = 7, data = {[MODEL_DATA_KEY] = "custom"}, streamed = true}
        elements[#elements + 1] = remote
    ''')
    lua.run(files["client.lua"], "client.lua")
    lua.run("handlers.onClientResourceStart()")
    assert lua.run("return localPlayer.model .. ',' .. remote.model") == "20000,20000"
    lua.run('localPlayer.model = 7; source = localPlayer; handlers.onClientElementDataChange(MODEL_DATA_KEY)')
    assert lua.run("return localPlayer.model") == "20000"


@pytest.mark.parametrize("new_id", [False, True])
def test_failed_dff_replacement_releases_only_allocated_ids(lua, new_id):
    files = _scripts("object", new_id)
    lua.run("failReplace = true")
    lua.run(files["models.lua"], "models.lua")
    lua.run(files["client.lua"], "client.lua")
    lua.run("handlers.onClientResourceStart()")
    assert lua.run("return tostring(freed)") == ("20000" if new_id else "nil")
    assert "cannot load models/custom.dff" in lua.run("return lastError")
    assert "lod:" not in lua.run("return table.concat(calls, '|')")


def test_model_allocation_failure_does_not_load_assets(lua):
    files = _scripts("vehicle")
    lua.run("failRequest = true")
    lua.run(files["models.lua"], "models.lua")
    lua.run(files["client.lua"], "client.lua")
    lua.run("handlers.onClientResourceStart()")
    assert lua.run("return table.concat(calls, '|')") == "request:vehicle:400"


@pytest.mark.parametrize("kind", ["vehicle", "skin", "object"])
def test_server_helpers_keep_the_parent_model_and_tag_the_pack(lua, kind):
    files = _scripts(kind)
    lua.run(files["models.lua"], "models.lua")
    lua.run(files["server.lua"], "server.lua")
    if kind == "skin":
        lua.run('assert(setPackSkin(localPlayer, "custom")); created = localPlayer')
        lua.run('assert(not setPackSkin({kind = "vehicle", data = {}}, "custom"))')
        lua.run('failSetModel = true; assert(not setPackSkin(localPlayer, "custom"))')
    else:
        fn = "createPackVehicle" if kind == "vehicle" else "createPackObject"
        lua.run(f'created = {fn}("custom", 1, 2, 3); assert({fn}("missing", 0, 0, 0) == false)')
    parent = {"vehicle": 400, "skin": 7, "object": 1337}[kind]
    assert lua.run('return created.model .. ":" .. created.data["satk:pack:model"]') == f"{parent}:custom"
    lua.run("commands.pack(false); commands.pack(localPlayer)")  # console and an absent Admin ACL group
