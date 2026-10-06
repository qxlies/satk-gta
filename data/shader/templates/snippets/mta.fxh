// ---- Values MTA sets before every draw (by semantic or state annotation) ----
float4x4 gWorld : WORLD;
float4x4 gView : VIEW;
float4x4 gProjection : PROJECTION;
float4x4 gWorldViewProjection : WORLDVIEWPROJECTION;
float3 gCameraPosition : CAMERAPOSITION;
float gTime : TIME;

// The texture the game is drawing (stage 0): the world texture this shader replaces.
texture gTexture0 < string textureState = "0,Texture"; >;

// Direct3D 9 fixed-function state of the draw: material, lighting switch, material sources, sun light.
float4 gMaterialDiffuse < string materialState = "Diffuse"; >;
float4 gMaterialAmbient < string materialState = "Ambient"; >;
float4 gMaterialEmissive < string materialState = "Emissive"; >;
float4 gGlobalAmbient < string renderState = "AMBIENT"; >;
int gLighting < string renderState = "LIGHTING"; >;
int gDiffuseSource < string renderState = "DIFFUSEMATERIALSOURCE"; >;
int gAmbientSource < string renderState = "AMBIENTMATERIALSOURCE"; >;
int gEmissiveSource < string renderState = "EMISSIVEMATERIALSOURCE"; >;
int gHasColor0 < string vertexDeclState = "Color0"; >;
float4 gLight1Diffuse < string lightState = "1,Diffuse"; >;
float3 gLight1Direction < string lightState = "1,Direction"; >;

// Vertex colour the way Direct3D 9 lighting computes it: prelit vertices (buildings) keep their colour, lit
// geometry (vehicles, peds) gets global ambient + emissive + sun. A material source of 1 (COLOR1) reads the
// vertex colour when the vertex has one, else the material colour.
float4 SatkDiffuse(float3 worldNormal, float4 vertexColor)
{
    bool hasColor = gHasColor0 != 0;
    float4 diffuse = (gDiffuseSource == 1 && hasColor) ? vertexColor : gMaterialDiffuse;
    if (gLighting == 0)
        return hasColor ? vertexColor : gMaterialDiffuse;
    float4 ambient = (gAmbientSource == 1 && hasColor) ? vertexColor : gMaterialAmbient;
    float4 emissive = (gEmissiveSource == 1 && hasColor) ? vertexColor : gMaterialEmissive;
    float sun = saturate(dot(worldNormal, -gLight1Direction));
    float4 c;
    c.rgb = saturate(gGlobalAmbient.rgb * ambient.rgb + emissive.rgb + gLight1Diffuse.rgb * diffuse.rgb * sun);
    c.a = diffuse.a;
    return c;
}
