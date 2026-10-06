"""The effect reader (``satk.shader.fx``): MTA include rules, parameters, samplers, techniques."""

from __future__ import annotations

from satk.shader import fx

EFFECT = """// header comment with a fake technique { pass } inside
/* block
   comment */
float4x4 gWorld : WORLD;
float3 gCameraPosition : CAMERAPOSITION;
texture gTexture0 < string textureState = "0,Texture"; >;
texture gReplace;
int CUSTOMFLAGS < string createNormals = "yes"; >;
static const float PI = 3.14159;
float4 gColor = float4(1, 0.5, 0.25, 1);
float gArr[4] : MYARR;
row_major float4x4 gTr < string transformState = "WORLD"; >;
sampler Sampler0 = sampler_state
{
    Texture = (gTexture0);
    MinFilter = Linear;
};
sampler2D Sampler1 = sampler_state { Texture = <gReplace>; };
struct PSInput { float4 Position : POSITION0; float2 TexCoord : TEXCOORD0; };
float4 Helper(float2 uv) { return tex2D(Sampler1, uv); }
float4 PS(PSInput i) : COLOR0
{
    if (i.TexCoord.x > 0.5) { return Helper(i.TexCoord) * gColor; }
    return tex2D(Sampler0, i.TexCoord);
}
technique tec0 < string Group = "x"; >
{
    pass P0
    {
        AlphaBlendEnable = true;
        Texture[0] = gReplace;
        PixelShader = compile ps_2_0 PS();
    }
    pass P1 { VertexShader = null; }
}
technique fallback { pass P0 { } }
"""


def test_parse_parameters_samplers_functions_techniques(write):
    eff = fx.load(write("a.fx", EFFECT))
    by = {p.name: p for p in eff.params}
    assert set(by) >= {"gWorld", "gCameraPosition", "gTexture0", "gReplace", "CUSTOMFLAGS", "PI", "gColor", "gArr",
                       "gTr", "Sampler0", "Sampler1"}
    assert by["gWorld"].semantic == "WORLD" and by["gWorld"].key == "WORLD" and by["gWorld"].type == "float4x4"
    assert by["gTexture0"].annotations == {"textureState": "0,Texture"} and by["gTexture0"].is_texture
    assert by["CUSTOMFLAGS"].annotations == {"createNormals": "yes"}
    assert by["gColor"].init.startswith("float4") and by["gColor"].key == "GCOLOR"
    assert by["gArr"].array and by["gArr"].semantic == "MYARR"
    assert by["gTr"].row_major
    assert by["Sampler0"].is_sampler and "gTexture0" in by["Sampler0"].init
    assert "gReplace" in by["Sampler1"].init
    assert set(eff.functions) == {"Helper", "PS"}
    assert [t.name for t in eff.techniques] == ["tec0", "fallback"]
    p0 = eff.techniques[0].passes[0]
    assert p0.ps == ("ps_2_0", "PS") and p0.vs is None
    assert ("Texture[0]", "gReplace", p0.states[1][2]) in p0.states
    assert eff.techniques[0].passes[1].states[0][0] == "VertexShader"
    assert eff.techniques[1].passes[0].states == []
    assert not [i for i in eff.issues if i.sev == "error"]


def test_include_rules_follow_mta(write):
    root = write("meta.xml", "<meta/>").parent
    write("shaders/common/helper.fxh", "float gHelper;\n")
    write("shaders/inner.fxh", '#include "common/helper.fxh"\nfloat gInner;\n')   # relative to the FIRST .fx
    write("lib/rooted.fxh", "float gRooted;\n")
    main = write("shaders/main.fx", '#include "inner.fxh"\n#include "/lib/rooted.fxh"\n#include "../lib/rooted.fxh"\n'
                                   '#include "missing.fxh"\n// #include "commented.fxh"\n'
                                   "technique t { pass P0 { } }\n")
    eff = fx.load(main)
    assert eff.root == root.resolve()
    names = {p.name for p in eff.params}
    assert {"gHelper", "gInner", "gRooted"} <= names
    codes = {(i.code, i.line) for i in eff.issues}
    assert ("INCLUDE_ILLEGAL", 3) in codes and ("INCLUDE_MISSING", 4) in codes
    assert not any("commented" in i.text for i in eff.includes)
    # #line markers keep the original files
    assert '#line 1 "shaders/inner.fxh"' in eff.source and '#line 2 "shaders/main.fx"' in eff.source


def test_include_loop_and_syntax_problems(write):
    write("x.fxh", '#include "x.fxh"\n')
    eff = fx.load(write("loop.fx", '#include "x.fxh"\ntechnique t { pass P0 { } }\n'))
    assert any(i.code == "INCLUDE_LOOP" for i in eff.issues)
    bad = fx.load(write("bad.fx", "float4 PS() : COLOR0 { return 1;\n/* never closed\n"))
    msgs = [i.msg for i in bad.issues if i.code == "SYNTAX"]
    assert any("never closed" in m or "unterminated" in m for m in msgs)


def test_resource_root_without_meta_is_the_fx_folder(tmp_path):
    p = tmp_path / "solo" / "a.fx"
    p.parent.mkdir()
    p.write_text("technique t { pass P0 { } }", encoding="utf-8")
    assert fx.resource_root(p) == p.parent


def test_strip_comments_keeps_lines_and_strings():
    text = 'a // x\n"// not a comment" /* y\nz */ b'
    out, probs = fx.strip_comments(text)
    assert out.count("\n") == text.count("\n") and '"// not a comment"' in out and "b" in out and not probs
