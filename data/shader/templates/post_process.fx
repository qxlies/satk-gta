// {{NAME}}: simple post-process (colour grading and vignette) for MTA:SA, made by satk (template post_process).
// MIT licence. The Lua glue copies the screen into gScreenSource before the HUD is drawn and draws this shader over
// the whole screen.

texture gScreenSource;

float gBrightness = 1.0;
float gContrast = 1.08;
float gSaturation = 1.15;
float gVignette = 0.35;                       // darkening of the corners (0 = none)
float4 gTint = float4(1.0, 0.98, 0.95, 0.5);  // colour multiplier (rgb) and its strength (a)

sampler ScreenSampler = sampler_state
{
    Texture = (gScreenSource);
    MinFilter = Linear;
    MagFilter = Linear;
    AddressU = Clamp;
    AddressV = Clamp;
};

float4 PixelShaderFunction(float2 TexCoord : TEXCOORD0) : COLOR0
{
    float4 color = tex2D(ScreenSampler, TexCoord);
    float luma = dot(color.rgb, float3(0.299, 0.587, 0.114));
    color.rgb = lerp(float3(luma, luma, luma), color.rgb, gSaturation);
    color.rgb = (color.rgb - 0.5) * gContrast + 0.5;
    color.rgb = lerp(color.rgb, color.rgb * gTint.rgb, gTint.a) * gBrightness;
    float2 d = TexCoord - 0.5;
    color.rgb *= saturate(1.0 - dot(d, d) * gVignette * 2.0);
    color.a = 1.0;
    return color;
}

technique post_process
{
    pass P0
    {
        PixelShader = compile ps_2_0 PixelShaderFunction();
    }
}
