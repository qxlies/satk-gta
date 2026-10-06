// {{NAME}}: wet, shiny roads for MTA:SA, made by satk (template wet_roads). MIT licence.
// Darkens the road texture and adds a sky reflection and a sun highlight scaled by gWetness (0 dry .. 1 soaked).
// The Lua glue sets gWetness from the rain level and gSkyTop/gSkyBottom from getSkyGradient().

//@satk:snippet mta

// Road meshes carry no normals: let MTA generate them.
int CUSTOMFLAGS < string createNormals = "yes"; string skipUnusedParameters = "yes"; >;

float gWetness = 0.0;
float gReflection = 0.6;         // sky reflection strength at full wetness
float gSpecularPower = 40.0;     // sun highlight sharpness
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
    float fresnel = pow(1.0 - saturate(dot(n, v)), 4.0);
    float3 sky = lerp(gSkyBottom, gSkyTop, saturate(r.z));
    float3 sun = gLight1Diffuse.rgb * pow(saturate(dot(r, -gLight1Direction)), gSpecularPower);
    // Wet surfaces look darker; the shine follows the prelit brightness so tunnels and nights stay dark.
    color.rgb *= 1.0 - 0.35 * gWetness;
    color.rgb += (sky * (fresnel * gReflection) + sun * 0.8) * (gWetness * PS.Diffuse.rgb);
    return color;
}

technique wet_roads
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
