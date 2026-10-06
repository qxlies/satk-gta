"""Shader resource templates (``satk.shader.templates``): rendering, MTA checks of every output, compiling."""

from __future__ import annotations

import struct
import typing
import zlib
from pathlib import Path

import pytest

from satk.core.errors import SatkError
from satk.shader import check as C
from satk.shader import fx
from satk.shader import templates as T


def _render_all(tmp_path: Path) -> dict[str, Path]:
    out = {}
    for name in T.TEMPLATES:
        files = T.render(name, f"test_{name}", apply=["*road*", 'we"ird\\name'], remove=["*sign*"],
                         selection="unit test", image="texture.png" if name == "texture_replace" else None)
        d = tmp_path / name
        d.mkdir()
        for fname, text in files.items():
            (d / fname).write_text(text, encoding="utf-8")
        out[name] = d
    return out


def test_template_list_matches_index_and_literal():
    from satk.shader.ops import Template

    assert set(T.TEMPLATES) == set(T.templates()) == set(typing.get_args(Template))
    for name, t in T.templates().items():
        assert t["about"] and t["fx"].endswith(".fx") and t["lua"] in ("world.lua", "post.lua"), name


def test_every_template_renders_without_placeholders(tmp_path):
    for name, d in _render_all(tmp_path).items():
        files = sorted(p.name for p in d.iterdir())
        assert files == sorted(["meta.xml", "client.lua", T.templates()[name]["fx"]]), name
        for p in d.iterdir():
            text = p.read_text(encoding="utf-8")
            assert "{{" not in text and "@satk:snippet" not in text, (name, p.name)
        lua = (d / "client.lua").read_text(encoding="utf-8")
        assert lua.count("function(") == lua.count("end)") or "end)" in lua
        assert f'"{T.templates()[name]["fx"]}"' in lua
        meta = (d / "meta.xml").read_text(encoding="utf-8")
        assert f'<file src="{T.templates()[name]["fx"]}" />' in meta and 'type="client"' in meta
        if name == "texture_replace":
            assert '<file src="texture.png" />' in meta and 'local IMAGE = "texture.png"' in lua


def test_lua_glue_escapes_and_wraps_patterns(tmp_path):
    lua = T.render("wet_roads", "x", apply=[f"name{i:03d}_road*" for i in range(30)] + ['we"ird\\name'],
                   remove=[], selection="s")["client.lua"]
    assert '"we\\"ird\\\\name"' in lua
    assert all(len(line) <= 100 for line in lua.splitlines() if line.startswith('    "'))
    assert "local REMOVE = {}" in lua
    assert T.lua_table([]) == "{}"


def test_every_template_passes_the_mta_and_lua_checks(tmp_path):
    for name, d in _render_all(tmp_path).items():
        fxp = d / T.templates()[name]["fx"]
        eff = fx.load(fxp)
        issues, summary = C.mta_checks(eff)
        assert [i.row() for i in eff.issues + issues if i.sev != "info"] == [], name
        refs = C.lua_refs(C.client_scripts(eff.root, fxp))
        li, info = C.lua_checks(eff, refs)
        assert [i.row() for i in li if i.sev != "info"] == [], name
        assert info["linked"].startswith("shader"), name
        for v in info["values_set"]:
            assert eff.param(v) is not None, (name, v)
        if name != "post_process":
            assert [p[1] for p in info["patterns"]][:1] == ["*road*"] or name in ("sky_fog", "ped_shading"), name


def test_bad_names_and_unknown_templates():
    with pytest.raises(SatkError) as e:
        T.render("wet_roads", "bad name!", apply=[], remove=[], selection="")
    assert e.value.code == "BAD_PARAMS"
    with pytest.raises(SatkError) as e:
        T.render("wet_road", "x", apply=[], remove=[], selection="")
    assert "wet_roads" in e.value.did_you_mean


def test_checker_png_is_a_valid_png():
    data = T.checker_png(16, 4)
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos = 8
    chunks = []
    while pos < len(data):
        n = struct.unpack(">I", data[pos:pos + 4])[0]
        tag, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + n]
        crc = struct.unpack(">I", data[pos + 8 + n:pos + 12 + n])[0]
        assert zlib.crc32(tag + body) & 0xFFFFFFFF == crc
        chunks.append(tag)
        pos += 12 + n
    assert chunks == [b"IHDR", b"IDAT", b"IEND"]
    assert len(zlib.decompress(data[data.index(b"IDAT") + 4:])) >= 0
    assert T.checker_png() == T.checker_png()


@pytest.mark.engine
def test_every_template_compiles_with_fxc(tmp_path, fxc, satk_home):
    for name, d in _render_all(tmp_path).items():
        eff = fx.load(d / T.templates()[name]["fx"])
        r = C.compile_fx(eff, fxc)
        assert r["ok"] and r["issues"] == [], (name, [i.row() for i in r["issues"]])


LUA_BUILTINS = {"ipairs", "pairs", "math", "max", "min", "function", "if", "and", "or", "not", "return", "local",
                "type", "tostring", "tonumber"}


@pytest.mark.engine
def test_glue_calls_only_known_mta_client_functions_and_events(tmp_path):
    """Every function and event the generated Lua uses exists on the MTA client (kb built from the MTA source)."""
    import os
    import re

    from satk.core.registry import invoke

    probe = invoke("kb.mta", {"query": "dxCreateShader"})
    if not probe.get("ok") and probe["error"]["code"] in ("NOT_READY", "INDEX_MISSING", "DEPENDENCY"):
        reason = f"MTA kb not available: {probe.get('error', {}).get('msg')}"
        if os.environ.get("SATK_TEST_NO_SKIP") == "1":
            pytest.fail(reason, pytrace=False)
        pytest.skip(reason)
    calls: set[str] = set()
    events: set[str] = set()
    defined: set[str] = set()
    for d in _render_all(tmp_path).values():
        lua = C.strip_lua_comments((d / "client.lua").read_text(encoding="utf-8"))
        defined |= set(re.findall(r"\bfunction\s+([A-Za-z_]\w*)\s*\(", lua))
        calls |= set(re.findall(r"(?<![.:\w])([A-Za-z_]\w*)\s*\(", lua))
        events |= set(re.findall(r'(?:add|remove)EventHandler\(\s*"(\w+)"', lua))
    for fn in sorted(calls - defined - LUA_BUILTINS):
        r = invoke("kb.mta", {"query": fn})
        assert r.get("ok") and r.get("name") == fn, (fn, r)
        assert r.get("side") in ("client", "shared"), (fn, r.get("side"))
    for ev in sorted(events):
        r = invoke("kb.mta", {"query": ev, "event": True})
        assert r.get("ok") and r.get("side") == "client", (ev, r)
