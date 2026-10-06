// {{NAME}}: ped shading tweak for MTA:SA, made by satk (template ped_shading). MIT licence.
// Pixel shader only: the game keeps its own vertex processing (ped skinning and lighting), the shader adjusts
// saturation, contrast, brightness and a tint of the lit colour. Tune the values in client.lua.

texture gTexture0 < string textureState = "0,Texture"; >;

float gBrightness = 1.05;
float gContrast = 1.05;
float gSaturation = 1.10;
float4 gTint = float4(1.00, 0.97, 0.94, 0.25);   // colour multiplier (rgb) and its strength (a)

sampler Sampler0 = sampler_state
{
    Texture = (gTexture0);
};

float4 PixelShaderFunction(float4 Diffuse : COLOR0, float2 TexCoord : TEXCOORD0) : COLOR0
{
    float4 color = tex2D(Sampler0, TexCoord) * Diffuse;
    float luma = dot(color.rgb, float3(0.299, 0.587, 0.114));
    color.rgb = lerp(float3(luma, luma, luma), color.rgb, gSaturation);
    color.rgb = (color.rgb - 0.5) * gContrast + 0.5;
    color.rgb *= gBrightness;
    color.rgb = lerp(color.rgb, color.rgb * gTint.rgb, gTint.a);
    return color;
}

technique ped_shading
{
    pass P0
    {
        PixelShader = compile ps_2_0 PixelShaderFunction();
    }
}

technique fallback
{
    pass P0
    {
    }
}
