"""Starter MTA shader resources (``satk shader new``). Stdlib only.

``data/shader/templates/index.json`` lists the templates: the ``.fx`` file (``//@satk:snippet <name>`` lines take
``snippets/<name>.fxh``, so every output is one self-contained file), the Lua glue (``world.lua`` for world-texture
shaders, ``post.lua`` for the screen post-process), the default world-texture selection, ``dxCreateShader``
arguments, the values the glue sets with ``dxSetShaderValue`` and extra Lua lines. :func:`render` fills the
``{{PLACEHOLDERS}}`` and returns ``{file name: text}`` for ``meta.xml``, ``client.lua`` and the effect.
"""

from __future__ import annotations

import re
import struct
import zlib

from ..core.errors import SatkError

__all__ = ["TEMPLATES", "templates", "render", "lua_table", "lua_string", "checker_png", "check_name"]

#: Template names (the ``Literal`` of ``satk shader new``; tests compare it with ``index.json``).
TEMPLATES = ("car_paint", "texture_replace", "wet_roads", "water", "sky_fog", "ped_shading", "post_process")
_NAME = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_SNIPPET = re.compile(r"^[ \t]*//@satk:snippet[ \t]+([a-z0-9_]+)[ \t]*$", re.M)


def templates() -> dict[str, dict]:
    from ..core import resources

    return resources.read_json("shader", "templates", "index.json")["templates"]


def check_name(name: str) -> str:
    if not _NAME.match(name or ""):
        raise SatkError("BAD_PARAMS", f"bad resource name {name!r}",
                        hint="letters, digits, _ and -, at most 40 characters (it is also the MTA resource name)")
    return name


def lua_string(s: str) -> str:
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def lua_table(items: list[str], indent: str = "    ", width: int = 100) -> str:
    """A Lua array of strings, wrapped at ``width`` columns."""
    if not items:
        return "{}"
    lines: list[str] = []
    cur = indent
    for it in items:
        piece = lua_string(it) + ","
        if len(cur) + len(piece) + 1 > width and cur.strip():
            lines.append(cur.rstrip())
            cur = indent
        cur += piece + " "
    lines.append(cur.rstrip())
    return "{\n" + "\n".join(lines) + "\n}"


def _lua_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (list, tuple)):
        return "{" + ", ".join(_lua_value(x) for x in v) + "}"
    if isinstance(v, float):
        return repr(round(v, 6))
    return str(v)


def _values_block(values: list) -> str:
    if not values:
        return "local function applyValues()\nend\n"
    rows = [f'    dxSetShaderValue(shader, {lua_string(n)}, {_lua_value(v)})' for n, v, _c in values]
    w = max(len(r) for r in rows)
    out = ["-- Shader values (defaults of the .fx file); change them here.", "local function applyValues()"]
    for r, (_n, _v, c) in zip(rows, values):
        out.append(f"{r.ljust(w)}  -- {c}")
    out.append("end")
    return "\n".join(out) + "\n"


def _fx_text(t: dict, fill: dict[str, str]) -> str:
    from ..core import resources

    text = resources.read_text("shader", "templates", t["fx"])

    def snip(m: re.Match) -> str:
        return resources.read_text("shader", "templates", "snippets", m.group(1) + ".fxh").rstrip("\n")

    text = _SNIPPET.sub(snip, text)
    return _fill(text, fill)


def _fill(text: str, fill: dict[str, str]) -> str:
    def rep(m: re.Match) -> str:
        k = m.group(1)
        if k not in fill:
            raise SatkError("INTERNAL", f"template placeholder {{{{{k}}}}} has no value")
        return fill[k]

    return re.sub(r"\{\{([A-Z_]+)\}\}", rep, text)


def render(template: str, name: str, *, apply: list[str], remove: list[str], selection: str,
           image: str | None = None) -> dict[str, str]:
    """``{file name: text}`` of the resource (``meta.xml``, ``client.lua``, ``<template>.fx``)."""
    from ..core import resources

    idx = templates()
    if template not in idx:
        raise SatkError("BAD_PARAMS", f"unknown template {template!r}", did_you_mean=sorted(idx))
    t = idx[template]
    check_name(name)
    fx_name = t["fx"]
    fill = {
        "NAME": name, "TEMPLATE": template, "ABOUT": t["about"], "FX": fx_name, "COMMAND": name.lower(),
        "IMAGE": image or "", "SELECTION": selection.replace("\n", " "),
        "APPLY": lua_table(apply), "REMOVE": lua_table(remove),
        "ELEMENT_TYPES": t.get("element_types", "world,vehicle,object,other"),
        "PRIORITY": _lua_value(t.get("priority", 0)), "MAX_DISTANCE": _lua_value(t.get("max_distance", 0)),
        "LAYERED": _lua_value(bool(t.get("layered", False))),
    }
    consts = "\n".join(_fill(c, fill) for c in t.get("consts", []))
    fill["VALUES"] = (consts + "\n\n" if consts else "") + _values_block(t.get("values", []))
    fill["UPDATE"] = "\n".join(t.get("update", ["local function update()", "end"])) + "\n"
    fill["SETUP"] = "\n".join(t.get("setup", []) + ["    applyValues()"])
    fill["TEARDOWN"] = "\n".join(t.get("teardown", [])) if t.get("teardown") else ""
    timer = int(t.get("timer", 0))
    fill["TIMER"] = f"    setTimer(update, {timer}, 0)\n" if timer > 0 else ""
    lua = _fill(resources.read_text("shader", "templates", t["lua"]), fill)
    lua = re.sub(r"\n{3,}", "\n\n", lua).replace("\n\nend\n", "\nend\n")
    files_xml = f'    <file src="{image}" />\n' if image else ""
    meta = _fill(resources.read_text("shader", "templates", "meta.xml"),
                 {**fill, "ABOUT": _xml(t["about"]), "FILES": files_xml})
    return {"meta.xml": meta, "client.lua": lua, fx_name: _fx_text(t, fill)}


def _xml(s: str) -> str:
    return s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


def checker_png(size: int = 64, cell: int = 8) -> bytes:
    """A deterministic magenta/grey checker PNG (placeholder image of ``texture_replace``)."""
    rows = bytearray()
    for y in range(size):
        rows.append(0)
        for x in range(size):
            on = ((x // cell) + (y // cell)) % 2 == 0
            rows += bytes((230, 40, 200)) if on else bytes((60, 60, 60))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + \
        chunk(b"IEND", b"")
