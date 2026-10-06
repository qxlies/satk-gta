"""``satk mta resource new``: starter MTA:SA resources that follow the usual conventions.

Kinds: ``script`` (client + server + shared scripts, a remote event both ways, a command and a render
handler), ``map`` (a ``.map`` with one object), ``shader`` (a texture-replace shader with a generated texture),
``vehicle-pack`` / ``skin-pack`` / ``object-pack`` (the model loader of :mod:`.pack` with an empty model list:
add files with ``satk mta pack``). Every generated resource passes ``satk mta lint`` without findings.
"""

from __future__ import annotations

import math
import re
import struct
import zlib

__all__ = ["KINDS", "render", "png_checker"]

KINDS = ("script", "map", "vehicle-pack", "skin-pack", "object-pack", "shader")

_SCRIPT_META = '''<meta>
    <info author="{author}" name="{name}" type="script" version="1.0.0"
          description="{name}: a starter resource made by satk mta resource new"/>
{oop}    <script src="shared.lua" type="shared"/>
    <script src="server.lua" type="server"/>
    <script src="client.lua" type="client"/>
</meta>
'''

_SHARED = '''-- {name}: settings both sides read (a shared script runs on the server and on every client)
CONFIG = {{
    greeting = "Hello from {name}",
    command = "{cmd}",
}}
'''

_SERVER = '''-- {name}: the server side (runs once, on the server)

-- true = clients may trigger it with triggerServerEvent; check the sender ('client') in the handler
addEvent("{name}:hello", true)

addEventHandler("{name}:hello", resourceRoot, function(message)
    if not client then
        return
    end
    outputChatBox(("%s: %s"):format(getPlayerName(client), tostring(message)), root, 0, 200, 255)
end)

-- /{cmd}: show or hide this resource's panel for the player who typed it
addCommandHandler(CONFIG.command, function(player)
    outputChatBox(CONFIG.greeting, player, 255, 200, 0)
    triggerClientEvent(player, "{name}:toggle", resourceRoot)
end)

addEventHandler("onResourceStart", resourceRoot, function()
    outputDebugString("{name} started: type /" .. CONFIG.command)
end)
'''

_CLIENT = '''-- {name}: the client side (runs on every player's PC)
local visible = false
local screenW, screenH = guiGetScreenSize()

addEvent("{name}:toggle", true)
addEventHandler("{name}:toggle", resourceRoot, function()
    visible = not visible
    if visible then
        triggerServerEvent("{name}:hello", resourceRoot, "opened the panel")
    end
end)

-- drawing happens every frame: keep this function cheap (no dxCreate*, no getElementsByType)
local function render()
    if not visible then
        return
    end
    local x, y = screenW / 2 - 160, screenH - 120
    dxDrawRectangle(x, y, 320, 60, tocolor(0, 0, 0, 160))
    dxDrawText(CONFIG.greeting, x, y, x + 320, y + 60, tocolor(255, 255, 255), 1.2, "default-bold", "center",
        "center")
end
addEventHandler("onClientRender", root, render)
'''

_MAP_META = '''<meta>
    <info author="{author}" name="{name}" type="map" version="1.0.0"
          description="{name}: a starter map made by satk mta resource new"/>
    <map src="{name}.map" dimension="0"/>
</meta>
'''

_MAP = '''<map>
    <object id="{name} object 1" model="{model}" posX="{x}" posY="{y}" posZ="{z}" rotX="0" rotY="0" rotZ="0"
            interior="0" dimension="0" doublesided="false" collisions="true" frozen="true"/>
</map>
'''

_SHADER_META = '''<meta>
    <info author="{author}" name="{name}" type="script" version="1.0.0"
          description="{name}: replaces the world texture '{texture}' with texture.png (satk mta resource new)"/>
    <script src="client.lua" type="client"/>
    <file src="shader.fx"/>
    <file src="texture.png"/>
</meta>
'''

_SHADER_FX = '''// {name}: texture replacement shader (the texture comes from client.lua with dxSetShaderValue)
texture gTexture;

technique TexReplace
{{
    pass P0
    {{
        Texture[0] = gTexture;
    }}
}}
'''

_SHADER_CLIENT = '''-- {name}: replaces the configured world texture on this client
local shader, texture

addEventHandler("onClientResourceStart", resourceRoot, function()
    shader = dxCreateShader("shader.fx")
    texture = dxCreateTexture("texture.png")
    if not shader or not texture then
        outputDebugString("{name}: cannot create the shader or the texture (see the client debug log)", 1)
        return
    end
    dxSetShaderValue(shader, "gTexture", texture)
    engineApplyShaderToWorldTexture(shader, {texture_lua})
end)
'''


def png_checker(size: int = 64, cell: int = 8, a: tuple = (230, 60, 160), b: tuple = (40, 40, 40)) -> bytes:
    """A small RGB checkerboard PNG made by satk (a placeholder texture, not a game asset)."""
    rows = []
    for y in range(size):
        row = bytearray(b"\x00")
        for x in range(size):
            row += bytes(a if ((x // cell) + (y // cell)) % 2 == 0 else b)
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def _cmd(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())[:24] or "starter"


def render(kind: str, name: str, *, author: str = "satk", oop: bool = False, model: int = 1337,
           pos: list[float] | None = None, texture: str = "vehiclegrunge256") -> dict[str, str | bytes]:
    """File name -> content of a new resource of ``kind``."""
    from ..core.errors import SatkError
    from .pack import _lua_str, _xml, render_pack, validate_name

    validate_name(name)
    if kind not in KINDS:
        raise SatkError("BAD_PARAMS", f"unknown resource kind {kind!r}")
    fmt = {"name": name, "author": _xml(author), "cmd": _cmd(name), "texture": _xml(texture),
           "texture_lua": _lua_str(texture),
           "oop": "    <oop>true</oop>\n" if oop else ""}
    if kind == "script":
        return {"meta.xml": _SCRIPT_META.format(**fmt), "shared.lua": _SHARED.format(**fmt),
                "server.lua": _SERVER.format(**fmt), "client.lua": _CLIENT.format(**fmt)}
    if kind == "map":
        if pos is not None and (len(pos) != 3 or not all(math.isfinite(v) for v in pos)):
            raise SatkError("BAD_PARAMS", "pos must contain three finite coordinates: x y z")
        x, y, z = pos if pos is not None else (0.0, 0.0, 3.0)
        return {"meta.xml": _MAP_META.format(**fmt),
                f"{name}.map": _MAP.format(model=int(model), x=_num(x), y=_num(y), z=_num(z), **fmt)}
    if kind == "shader":
        return {"meta.xml": _SHADER_META.format(**fmt), "shader.fx": _SHADER_FX.format(**fmt),
                "client.lua": _SHADER_CLIENT.format(**fmt), "texture.png": png_checker()}
    pk = kind.split("-", 1)[0]
    files = render_pack(name, pk, [], new_id=True, author=author)
    return dict(files)


def _num(v: float) -> str:
    return f"{float(v):.2f}".rstrip("0").rstrip(".") if not float(v).is_integer() else str(int(v))
