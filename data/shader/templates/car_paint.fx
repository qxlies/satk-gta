// {{NAME}}: car paint reflection for MTA:SA, made by satk (template car_paint). MIT licence.
// Sky reflection with a Fresnel falloff plus a sun highlight on the vehicle paint textures.
// The Lua glue sets gSkyTop/gSkyBottom from getSkyGradient(); tune the other values in client.lua.

//@satk:snippet mta

int CUSTOMFLAGS < string skipUnusedParameters = "yes"; >;

float gReflection = 0.45;        // sky reflection strength (0..1)
float gSpecular = 0.8;           // sun highlight strength
float gSpecularPower = 24.0;     // sun highlight sharpness
float3 gSkyTop = float3(0.35, 0.55, 0.85);
float3 gSkyBottom = float3(0.75, 0.80, 0.85);

sampler Sampler0 = sampler_state
{
    Texture = (gTexture0);
};

struct VSInput
{
    float3 Position : POSITION0;
    float3 Normal : NORMAL0;
    float4 Diffuse : COLOR0;
    float2 TexCoord : TEXCOORD0;
};

struct PSInput
{
    float4 Position : POSITION0;
    float4 Diffuse : COLOR0;
    float2 TexCoord : TEXCOORD0;
    float3 Normal : TEXCOORD1;
    float3 View : TEXCOORD2;
};

PSInput VertexShaderFunction(VSInput VS)
{
    PSInput PS = (PSInput)0;
    float4 worldPos = mul(float4(VS.Position, 1.0), gWorld);
    PS.Position = mul(float4(VS.Position, 1.0), gWorldViewProjection);
    PS.Normal = normalize(mul(VS.Normal, (float3x3)gWorld));
    PS.View = gCameraPosition - worldPos.xyz;
    PS.Diffuse = SatkDiffuse(PS.Normal, VS.Diffuse);
    PS.TexCoord = VS.TexCoord;
    return PS;
}

float4 PixelShaderFunction(PSInput PS) : COLOR0
{
    float4 color = tex2D(Sampler0, PS.TexCoord) * PS.Diffuse;
    float3 n = normalize(PS.Normal);
    float3 v = normalize(PS.View);
    float3 r = reflect(-v, n);
    float3 sky = lerp(gSkyBottom, gSkyTop, saturate(r.z));
    float fresnel = 0.25 + 0.75 * pow(1.0 - saturate(dot(n, v)), 3.0);
    float3 sun = gLight1Diffuse.rgb * pow(saturate(dot(r, -gLight1Direction)), gSpecularPower) * gSpecular;
    color.rgb += sky * (gReflection * fresnel) + sun;
    return color;
}

technique car_paint
{
    pass P0
    {
        VertexShader = compile vs_2_0 VertexShaderFunction();
        PixelShader = compile ps_2_0 PixelShaderFunction();
    }
}

// Old GPUs: draw the vehicle unchanged instead of failing.
technique fallback
{
    pass P0
    {
    }
}
