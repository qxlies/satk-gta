// {{NAME}}: animated water for MTA:SA, made by satk (template water). MIT licence.
// Two scrolling samples of the water texture make ripples; a Fresnel term blends in the sky colour and gWaterColor
// tints the water. The Lua glue sets gSkyTop/gSkyBottom from getSkyGradient().

//@satk:snippet mta

int CUSTOMFLAGS < string skipUnusedParameters = "yes"; >;

float4 gWaterColor = float4(0.10, 0.30, 0.35, 0.35);  // tint colour (rgb) and its strength (a)
float gFlowSpeed = 0.03;                              // texture scroll speed
float gReflection = 0.5;                              // sky reflection strength
float3 gSkyTop = float3(0.35, 0.55, 0.85);
float3 gSkyBottom = float3(0.75, 0.80, 0.85);

sampler Sampler0 = sampler_state
{
    Texture = (gTexture0);
    AddressU = Wrap;
    AddressV = Wrap;
};

struct VSInput
{
    float3 Position : POSITION0;
    float4 Diffuse : COLOR0;
    float2 TexCoord : TEXCOORD0;
};

struct PSInput
{
    float4 Position : POSITION0;
    float4 Diffuse : COLOR0;
    float2 TexCoord : TEXCOORD0;
    float3 View : TEXCOORD1;
};

PSInput VertexShaderFunction(VSInput VS)
{
    PSInput PS = (PSInput)0;
    float4 worldPos = mul(float4(VS.Position, 1.0), gWorld);
    PS.Position = mul(float4(VS.Position, 1.0), gWorldViewProjection);
    PS.View = gCameraPosition - worldPos.xyz;
    PS.Diffuse = VS.Diffuse;
    PS.TexCoord = VS.TexCoord;
    return PS;
}

float4 PixelShaderFunction(PSInput PS) : COLOR0
{
    float t = frac(gTime * gFlowSpeed);
    float2 uv1 = PS.TexCoord + float2(t, t * 0.7);
    float2 uv2 = PS.TexCoord * 1.7 - float2(t * 0.8, -t * 0.5);
    float4 tex = (tex2D(Sampler0, uv1) + tex2D(Sampler0, uv2)) * 0.5;
    float3 v = normalize(PS.View);
    float3 n = normalize(float3((tex.rg - 0.5) * 0.3, 1.0));
    float fresnel = 0.2 + 0.8 * pow(1.0 - saturate(dot(n, v)), 4.0);
    float3 sky = lerp(gSkyBottom, gSkyTop, saturate(reflect(-v, n).z));
    float3 water = lerp(tex.rgb * PS.Diffuse.rgb, gWaterColor.rgb, gWaterColor.a);
    float4 color;
    color.rgb = lerp(water, sky, fresnel * gReflection);
    color.a = PS.Diffuse.a * tex.a;
    return color;
}

technique water
{
    pass P0
    {
        VertexShader = compile vs_2_0 VertexShaderFunction();
        PixelShader = compile ps_2_0 PixelShaderFunction();
    }
}

technique fallback
{
    pass P0
    {
    }
}
