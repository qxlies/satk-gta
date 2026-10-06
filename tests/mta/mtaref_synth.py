"""A small synthetic MTA reference for the ``satk mta`` tests (names and shapes like MTA's, no MTA text)."""

from __future__ import annotations

import json
from pathlib import Path

E = "element"


def _f(*args, x=1, o="mta", e=(), r=None, a=0):
    """One side: args are (name, type, optional)."""
    return {"v": [[list(a_) for a_ in args]], "x": x, "o": o, "e": list(e), "r": r, "a": a}


SHARED = {
    "outputChatBox": _f(("text", "string", 0), ("visibleTo", E, 1), ("r", "int", 1), ("g", "int", 1),
                        ("b", "int", 1), ("colorCoded", "bool", 1)),
    "outputDebugString": _f(("text", "string", 0), ("level", "int", 1)),
    "addEvent": _f(("eventName", "string", 0), ("allowRemoteTrigger", "bool", 1)),
    "addEventHandler": _f(("eventName", "string", 0), ("attachedTo", E, 0), ("handler", "function", 0),
                          ("propagate", "bool", 1), ("priority", "string", 1)),
    "addCommandHandler": _f(("commandName", "string", 0), ("handler", "function", 0)),
    "triggerEvent": _f(("eventName", "string", 0), ("baseElement", E, 0), ("...", "...", 1)),
    "setTimer": _f(("callback", "function", 0), ("interval", "int", 0), ("times", "int", 0), ("...", "...", 1)),
    "setElementPosition": _f(("entity", E, 0), ("position", "Vector3", 0), ("warp", "bool", 1)),
    "getElementPosition": _f(("entity", E, 0)),
    "getElementType": _f(("entity", E, 0), r="string|false"),
    "getElementsByType": _f(("type", "string", 0), ("startAt", E, 1), ("streamedIn", "bool", 1), x=0, a=1),
    "createElement": _f(("elementType", "string", 0), ("id", "string", 1)),
    "getRootElement": _f(),
    "getThisResource": _f(),
    "isElement": _f(("value", "var", 0)),
    "setElementData": _f(("entity", E, 0), ("key", "string", 0), ("value", "var", 0), ("sync", "bool", 1)),
    "getElementData": _f(("entity", E, 0), ("key", "string", 0), ("inherit", "bool", 1)),
    "getPedOccupiedVehicle": _f(("ped", "ped", 0)),
    "getPlayerName": _f(("player", "player", 0)),
    "setElementModel": _f(("entity", E, 0), ("model", "int", 0)),
    "getElementModel": _f(("entity", E, 0)),
    "loadstring": _f(("code", "string", 0), ("name", "string", 1), r="function|false"),
    "print": _f(("...", "...", 1)),
    "tostring": _f(("value", "var", 0)),
    "approxThing": _f(("a", "string", 0), ("b", "int", 0), ("c", "int", 0), x=0, a=1),
}
CLIENT = {
    "dxDrawText": _f(("text", "string", 0), ("topLeft", "Vector2", 0), ("bottomRight", "Vector2|int", 1),
                     ("color", "color", 1), ("scale", "Vector2|float", 1)),
    "dxDrawRectangle": _f(("position", "Vector2", 0), ("size", "Vector2", 0), ("color", "color", 1)),
    "dxCreateFont": _f(("filepath", "string", 0), ("size", "int", 1)),
    "tocolor": _f(("r", "int", 0), ("g", "int", 0), ("b", "int", 0), ("a", "int", 1)),
    "guiGetScreenSize": _f(),
    "triggerServerEvent": _f(("event", "string", 0), ("theElement", E, 0), ("...", "...", 1)),
    "engineRequestModel": _f(("modelType", "string", 0), ("parentID", "int", 1), x=0, e=("client-model-type",)),
    "isElementStreamedIn": _f(("entity", E, 0)),
    "setSoundVolume": _f(("sound", "sound", 0), ("volume", "float", 0), x=1),
}
SERVER = {
    "kickPlayer": _f(("player", "player", 0), ("reason", "string", 1)),
    "triggerClientEvent": _f(("sendTo", "var", 1), ("name", "string", 0), ("sourceElement", E, 0),
                             ("...", "...", 1), x=0, a=1),
    "neonOnlyThing": _f(("x", "int", 0), o="neon"),
    "getPlayerAccount": _f(("player", "player", 0)),
}


def reference() -> dict:
    funcs: dict = {}
    for n, v in SHARED.items():
        funcs[n] = {"client": v, "server": v}
    for n, v in CLIENT.items():
        funcs.setdefault(n, {})["client"] = v
    for n, v in SERVER.items():
        funcs.setdefault(n, {})["server"] = v
    return {
        "version": 1, "source": "file", "mta": "1.7.0",
        "funcs": funcs,
        "events": {"onClientRender": {"client": ""}, "onClientResourceStart": {"client": "startedResource"},
                   "onResourceStart": {"server": "startedResource"}, "onPlayerJoin": {"server": ""},
                   "onClientElementStreamIn": {"client": ""}, "onClientElementDataChange": {"client": "key"}},
        "classes": {"Vector3": ["client", "server"], "Vehicle": ["client", "server"],
                    "Browser": ["client"]},
        "enums": {"client-model-type": ["ped", "object", "vehicle"]},
        "deprecated": {
            "client": {"getPlayerOccupiedVehicle": [False, "getPedOccupiedVehicle", ""],
                       "getComponentPosition": [False, "will return 3 floats instead of a Vector3", "1.5.5-9.11710"]},
            "server": {"getPlayerOccupiedVehicle": [False, "getPedOccupiedVehicle", ""],
                       "givePlayerJetPack": [True, "Replaced with setPedWearingJetpack", ""],
                       "onClientLogin": [False, "onPlayerLogin", ""]},
        },
    }


def write_reference(path: Path) -> Path:
    path.write_text(json.dumps(reference()), encoding="utf-8")
    return path


def load():
    from satk.mta.api import Reference

    return Reference.from_json(reference())
