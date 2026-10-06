// {{NAME}}: sky and fog tint for MTA:SA, made by satk (template sky_fog). MIT licence.
// Tints the world (gTint) and blends a distance haze (gHazeColor from gHazeStart to gHazeEnd metres) on top of the
// game's own fog. The Lua glue sets a matching sky gradient with setSkyGradient and restores it on stop.

//@satk:snippet mta

int CUSTOMFLAGS < string createNormals = "yes"; string skipUnusedParameters = "yes"; >;

float4 gTint = float4(1.00, 0.92, 0.85, 0.35);       // colour multiplier (rgb) and its strength (a)
float4 gHazeColor = float4(0.85, 0.62, 0.50, 1.0);   // haze colour (rgb)
float gHazeStart = 120.0;
float gHazeEnd = 650.0;
float gHazeMax = 0.55;                               // strongest haze (0..1)

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
    float Haze : TEXCOORD1;
};

PSInput VertexShaderFunction(VSInput VS)
{
    PSInput PS = (PSInput)0;
    float4 worldPos = mul(float4(VS.Position, 1.0), gWorld);
    PS.Position = mul(float4(VS.Position, 1.0), gWorldViewProjection);
    float3 normal = normalize(mul(VS.Normal, (float3x3)gWorld));
    PS.Diffuse = SatkDiffuse(normal, VS.Diffuse);
    PS.TexCoord = VS.TexCoord;
    float dist = distance(worldPos.xyz, gCameraPosition);
    PS.Haze = saturate((dist - gHazeStart) / max(gHazeEnd - gHazeStart, 1.0)) * gHazeMax;
    return PS;
}

float4 PixelShaderFunction(PSInput PS) : COLOR0
{
    float4 color = tex2D(Sampler0, PS.TexCoord) * PS.Diffuse;
    color.rgb = lerp(color.rgb, color.rgb * gTint.rgb, gTint.a);
    color.rgb = lerp(color.rgb, gHazeColor.rgb, PS.Haze);
    return color;
}

technique sky_fog
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
