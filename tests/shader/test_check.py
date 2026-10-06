"""MTA checks, the Lua cross-check, fxc discovery and compiling (``satk.shader.check``)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from satk.core.errors import SatkError
from satk.shader import check as C
from satk.shader import fx

BROKEN = """
texture gTex;
float4 gColor : MYCOLOR = float4(1, 1, 1, 1);
float4 gMat < string MaterialState = "Diffuse"; >;
float gStart < string renderState = "FOGSTRAT"; >;
bool gLit < string renderState = "LIGHTING"; >;
float4 gLightDir < string lightState = "9,Direction"; >;
float gNoStage < string renderState = "1,FOGSTART"; >;
float4x4 gW < string transformState = "WORLD"; >;
float4x4 gWVP : WORLDVIEWPROJECTON;
texture gDepth : DEPTHBUFFER;
int CUSTOMFLAGS < string createNormal = "yes"; >;
sampler S0 = sampler_state { Texture = (gTex); };
sampler S1 = sampler_state { Texture = (gMissing); };
float4 PS(float2 uv : TEXCOORD0) : COLOR0 { return tex2D(S0, uv) * gColor; }
technique t1 { pass P0 { PixelShader = compile ps_3_0 PS(); VertexShader = compile vs_4_0 VSX(); Texture[1] = gNope; } }
technique10 t2 { pass P0 { } }
technique t3 { }
"""

GOOD = """
float4x4 gWorldViewProjection : WORLDVIEWPROJECTION;
texture gTexture0 < string textureState = "0,Texture"; >;
float4 gFogColor < string renderState = "FOGCOLOR"; >;
float4 gMaterialDiffuse < string materialState = "Diffuse"; >;
float3 gLight1Direction < string lightState = "1,Direction"; >;
int gHasNormal < string vertexDeclState = "Normal"; >;
texture gExtra;
float gStrength = 0.5;
static const float K = 2.0;
sampler S0 = sampler_state { Texture = (gTexture0); };
sampler S1 = sampler_state { Texture = (gExtra); };
float4 PS(float2 uv : TEXCOORD0) : COLOR0 { return (tex2D(S0, uv) + tex2D(S1, uv)) * gMaterialDiffuse * gStrength; }
technique main { pass P0 { PixelShader = compile ps_2_0 PS(); } }
technique fallback { pass P0 { } }
"""


def _codes(issues) -> dict[str, list]:
    out: dict[str, list] = {}
    for i in issues:
        out.setdefault(i.code, []).append(i)
    return out


def test_mta_checks_find_what_mta_silently_ignores(write):
    eff = fx.load(write("broken.fx", BROKEN))
    issues, summary = C.mta_checks(eff)
    c = _codes(issues)
    assert {"STATE_GROUP", "STATE_NAME", "STATE_TYPE", "STATE_STAGE", "SEMANTIC", "DEPTHBUFFER", "CUSTOMFLAGS",
            "TEXTURE_UNDECLARED", "PROFILE", "UNKNOWN_FUNCTION", "D3D10", "NO_PASS", "SM3_FOG"} <= set(c)
    assert "FOGSTART" in c["STATE_NAME"][0].msg                          # did you mean
    assert any("materialState" in i.msg for i in c["STATE_GROUP"])
    assert {i.sev for i in c["STATE_TYPE"]} == {"error", "warn"}         # bool LIGHTING; column-major matrix
    assert len(c["STATE_STAGE"]) == 2                                    # stage 9; stage on renderState
    assert len(c["TEXTURE_UNDECLARED"]) == 2                             # sampler S1, pass Texture[1]
    assert "gStart" not in summary["mta"] and "gLit" not in summary["mta"]
    assert "gTex" in summary["lua_textures"]


def test_mta_checks_clean_effect(write):
    eff = fx.load(write("good.fx", GOOD))
    issues, summary = C.mta_checks(eff)
    assert [i for i in issues if i.sev != "info"] == []
    assert set(summary["mta"]) == {"gWorldViewProjection", "gTexture0", "gFogColor", "gMaterialDiffuse",
                                   "gLight1Direction", "gHasNormal"}
    assert summary["lua_textures"] == ["gExtra"] and summary["values"] == ["gStrength"]


def test_syntax_errors_do_not_add_structure_noise(write):
    eff = fx.load(write("syn.fx", "float4 PS() : COLOR0 { return 1;\ntechnique t { pass P0 { } }\n"))
    issues, _summary = C.mta_checks(eff)
    assert any(i.code == "SYNTAX" for i in eff.issues) and not any(i.code == "NO_TECHNIQUE" for i in issues)


def test_mta_facts_cover_groups_semantics_and_types():
    f = C.mta_facts()
    assert set(f["state_groups"]) == {"renderState", "stageState", "samplerState", "materialState", "transformState",
                                      "textureState", "lightState", "lightEnableState", "deviceCaps", "vertexDeclState"}
    assert f["state_groups"]["textureState"]["registers"] == {"Texture": "texture"}
    assert f["state_groups"]["lightState"]["stage"] and not f["state_groups"]["renderState"]["stage"]
    assert {"WORLD", "TIME", "CAMERAPOSITION", "DEPTHBUFFER"} <= set(f["semantics"])
    assert "ped" in f["element_types"]


LUA = """
-- local s = dxCreateShader("commented.fx")
local shader = dxCreateShader("good.fx", 0, 0, false, "world,vehicles")
local other = dxCreateShader("other.fx")
dxSetShaderValue(shader, "gStrength", 1)
dxSetShaderValue(shader, "GSTRENGTH", 2)
dxSetShaderValue(shader, "gStrenght", 1)
dxSetShaderValue(shader, "gFogColor", 1, 0, 0, 1)
dxSetShaderValue(other, "whatever", 1)
shader:setValue("gExtra", tex)
dxSetShaderValue(shader, "WORLDVIEWPROJECTION", m)
local LIST = { "*road*", "nosuchname*" }
for _, p in ipairs(LIST) do
    engineApplyShaderToWorldTexture(shader, p)
end
engineRemoveShaderFromWorldTexture(shader, "*sign*")
other:applyToWorldTexture("*glass*")
"""


def test_lua_cross_check(write):
    eff = fx.load(write("good.fx", GOOD))
    lua = write("client.lua", LUA)
    refs = C.lua_refs([lua])
    assert set(refs.shaders) == {"shader", "other"}
    issues, info = C.lua_checks(eff, refs, universe=["sf_road5", "dt_road", "roadsign01"])
    c = _codes(issues)
    unknown = sorted(c["LUA_UNKNOWN_PARAM"], key=lambda i: i.line)
    assert len(unknown) == 2 and "did you mean gStrength" in unknown[0].msg
    assert "renderState='FOGCOLOR'" in unknown[1].msg and "not settable" in unknown[1].msg
    assert "WORLDVIEWPROJECTION" in c["LUA_OVERRIDES_MTA"][0].msg
    assert "LUA_ELEMENT_TYPES" in c and "vehicles" in c["LUA_ELEMENT_TYPES"][0].msg
    assert "LUA_TEXTURE_NOT_SET" not in c                 # gExtra is set through the OOP call
    assert [i.msg for i in c["NO_MATCH"]] == ['apply "nosuchname*": matches no texture name of the profile (typo?)']
    assert info["patterns"] == [["apply", "*road*", 3], ["apply", "nosuchname*", 0], ["remove", "*sign*", 1]]
    assert info["linked"].startswith("shader")


def test_lua_semantic_lookup_and_texture_not_set(write):
    eff = fx.load(write("sem.fx", "float4 gTint : TINT;\ntexture gT;\nsampler S = sampler_state { Texture = (gT); };\n"
                                  "float4 PS(float2 uv : TEXCOORD0) : COLOR0 { return tex2D(S, uv) * gTint; }\n"
                                  "technique t { pass P0 { PixelShader = compile ps_2_0 PS(); } }\n"))
    lua = write("c.lua", 'local s = dxCreateShader(fxPath)\ndxSetShaderValue(s, "gTint", 1, 1, 1, 1)\n'
                         'dxSetShaderValue(s, "TINT", 1, 1, 1, 1)\n')
    issues, info = C.lua_checks(eff, C.lua_refs([lua]))
    c = _codes(issues)
    assert "semantic TINT" in c["LUA_UNKNOWN_PARAM"][0].msg and len(c["LUA_UNKNOWN_PARAM"]) == 1
    assert "LUA_TEXTURE_NOT_SET" in c
    assert info["linked"].endswith("(the only dxCreateShader)")


def test_client_scripts_from_meta(write):
    root = write("meta.xml", '<meta><script src="c.lua" type="client"/><script src="s.lua"/>'
                             '<script src="sh.lua" type="shared"/><file src="a.fx"/></meta>').parent
    for n in ("c.lua", "s.lua", "sh.lua"):
        write(n, "")
    fxp = write("a.fx", "technique t { pass P0 { } }")
    assert [p.name for p in C.client_scripts(root, fxp)] == ["c.lua", "sh.lua"]


def test_find_fxc_explicit(tmp_path):
    assert C.find_fxc("none").path is None
    with pytest.raises(SatkError) as e:
        C.find_fxc(str(tmp_path / "nope.exe"))
    assert e.value.code == "NOT_FOUND"
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "fxc.exe").write_bytes(b"")
    assert C.find_fxc(str(fake)).path == fake / "fxc.exe"


def test_kits_layout_newest_sdk_first(tmp_path):
    for v in ("10.0.17134.0", "10.0.26100.0"):
        (tmp_path / "bin" / v / "x64").mkdir(parents=True)
    got = [p.relative_to(tmp_path).as_posix() for p in C._kits_bins(tmp_path)]
    assert got[0] == "bin/10.0.26100.0/x64/fxc.exe" and got[2] == "bin/10.0.17134.0/x64/fxc.exe"


@pytest.mark.engine
def test_compile_reports_errors_with_original_files(write, fxc, satk_home):
    write("meta.xml", "<meta/>")
    write("inc/lib.fxh", "float4 Broken() { return undefinedThing; }\n")
    eff = fx.load(write("main.fx", '#include "inc/lib.fxh"\n' + GOOD))
    r = C.compile_fx(eff, fxc)
    assert not r["ok"]
    e = [i for i in r["issues"] if i.sev == "error"]
    assert e and e[0].code.startswith("X") and e[0].file.lower() == "inc/lib.fxh" and e[0].line == 1
    ok = C.compile_fx(fx.load(write("good.fx", GOOD)), fxc)
    assert ok["ok"] and not ok["issues"] and ok["variants"] == 1
    raw = fx.load(write("rawz.fx", "#if IS_DEPTHBUFFER_RAWZ\nfloat a;\n#endif\n" + GOOD))
    assert C.compile_fx(raw, fxc)["variants"] == 2
    leftovers = list((satk_home / "work" / "tmp" / "shader").glob("*"))
    assert leftovers == []


@pytest.mark.engine
def test_mta_json_matches_the_mta_source():
    import sys

    from satk.core.paths import cfg

    root = Path(cfg().paths.get("engine") or "")
    if not (root / "Client" / "core" / "Graphics" / "CRenderItem.EffectParameters.cpp").is_file():
        pytest.skip("MTA client source not found")
    sys.path.insert(0, str(Path(__file__).parent))
    import mta_source

    got = mta_source.extract(root)
    assert got == C.mta_facts(), "data/shader/mta.json is stale: python tests/shader/mta_source.py <engine> > it"
    json.dumps(got)


def test_lua_element_types_from_a_local_constant(write):
    eff = fx.load(write("good.fx", GOOD))
    lua = write("c.lua", 'local FX = "good.fx"\nlocal TYPES = "world,peds"\n'
                         'local shader = dxCreateShader(FX, 1, 0, false, TYPES)\n')
    issues, info = C.lua_checks(eff, C.lua_refs([lua]))
    assert info["linked"] == "shader (file name)"
    assert [i.code for i in issues if i.sev == "error"] == ["LUA_ELEMENT_TYPES"] and "peds" in issues[0].msg
