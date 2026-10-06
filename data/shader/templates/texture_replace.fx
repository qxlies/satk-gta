// {{NAME}}: world texture replacement for MTA:SA, made by satk (template texture_replace). MIT licence.
// Draws gTexture instead of every texture the shader is applied to (fixed-function pass, no shader code).
// The Lua glue loads {{IMAGE}} with dxCreateTexture and sets it with dxSetShaderValue(shader, "gTexture", ...).

texture gTexture;

technique texture_replace
{
    pass P0
    {
        Texture[0] = gTexture;
    }
}
