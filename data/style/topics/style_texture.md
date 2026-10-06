# SA style: textures (look, formats, the photo-free recipe, shared atlases)

SA looks like SA because of its textures: SMALL (64-256 px), PHOTO-sourced, colour-graded to dusty desaturated
tones, shading/folds/grime PAINTED IN, blurred by DXT1. Detail is texture, not geometry.

## How each class uses textures
- Map models TILE modular photo textures (one window bay, one storey, a wall or ground patch): `uv.span` p50 3-6
  on buildings and terrain; the dark baked prelight does the shading.
- Vehicles reuse `vehicle.txd`: body = flat paint KEY colour over `vehiclegrunge256` (the engine swaps 16 dirt
  levels), trim/chrome/glass/grille/underbody = regions of `vehiclegeneric256`, lamps = `vehiclelights128`,
  gloss = env sheen `xvehicleenv128` on UV2. Own textures: dark interior atlas 128 px, rim 64 px, decals.
- Peds: one 128x256 photo atlas, folds painted in, photographic face. Weapons: a 64 px photo on both sides.
- Props, pickups: 64-128 px; signage is bold, saturated, slightly blurry.

## Formats (32,878 vanilla textures)
DXT1 89 % (484 with 1-bit alpha), DXT3 6.9 %, X8R8G8B8 3.2 %, A8R8G8B8 0.9 %, DXT5 0. Powers of two; 128x128
33 %, 256x256 24 %, 64x64 16 %; 512+ only 2.9 %. Mips on 21 % (terrain 43 %, buildings 32 %); vehicles, peds,
weapons: exactly ONE level (a mip warning there is wrong). Vehicle own textures DXT1/DXT3; peds uncompressed
128x256. `texture.pack --asset-class vehicle|ped|weapon|map|lod` writes these. `texture.audit` skips `no_mips`
for vehicle/ped/weapon/upgrade TXDs (field `one_level`); never add mips to them.

## Role bands (p10 / p50 / p90; `style.texture <png|txd> --role auto` measures yours)
- Car interiors (84, the `interior` role): exact colours 344 / 607 / 928; 5-bit colours 66 / 113 / 170; luminance
  20.3 / 32.1 / 60.6 of 255; value 0.088 / 0.142 / 0.243; saturation 0.09 / 0.22 / 0.35; luma std 18.7 / 24.4 /
  45.5; HF energy 18.3 / 22.2 / 27.2. Dark, low contrast, yet hundreds of colours and fine grain.
- Vehicle own textures (574): luminance 23 / 59 / 161; saturation 0.02 / 0.12 / 0.37; HF 7.7 / 23.0 / 53.6.
- Map classes (texture means): luminance p50 110-126 of 255, saturation p50 0.10-0.14 (vegetation 0.33,
  pickups 0.41, peds 0.25); mid-grey, the prelight darkens them.
- Luminance spread alone does NOT separate vector art from photo-like; colour count and HF energy do.
- Failed agent textures: 19-77 colours (vector, "mobile game"); an interior at value 0.42 (glows through glass).

## Photo-like without photos (recipe)
1. Mid-dark base with noise at TWO scales (8-16 px blotches, 1-2 px grain); >= 300 colours in 128 px.
2. Paint the form: folds, pleats, panel edges as soft light/dark pairs; darken every edge and corner (baked AO);
   faint top-light gradient.
3. Desaturate (0.1-0.3), push to warm grey/brown; interiors value 0.1-0.2.
4. Sparse grime: streaks below edges, specks, worn highlights; subtle on car bodies (the dirt system does it).
5. Text: blocky, slightly blurred; never crisp vector type.
6. Work at 4x, downsample (box/Lanczos), judge AFTER DXT1 at native size (lossless crops, not a JPEG).
Tools: `texture.new` (flat base), `texture.finish <png> --preset photo_like|interior|wheel|wall --mask AO --edge EDGE
--role <role>` (lands the band); session `kit.bake` (Cycles AO + edge mask, ~0.1 s) for steps 2 and 4;
`style.texture` to check.

## Shared vehicle atlases (measured on 144 cars; support = share of vanilla use)
- Glass on `vehiclegeneric256`: u 0.59-0.66, v 0.94-1.0 holds 51 %; dark glass spans u 0.25-0.75, v 0.88-1.0.
- Colour-coded paint (jambs, inner panels): u 0-0.25, v 0.31-0.375 on `vehiclegeneric256`.
- Hand-labelled: chrome bands v 0.31-0.37 and 0.50-0.75; black plastic u 0.25-0.50, v 0.50-0.75; grille mesh
  u 0.75-1.0, v 0.88-1.0; underbody photo strips on the right half.
- `vehiclegrunge256`: clean upper area for up-facing panels, grime band rising from the bottom for sides.
- `kit.uv_region` projects faces into a named region at the tier's texel density.

## Anti-patterns
One flat colour per mesh (`uv.zero_area_share` cars p50 0.045; an agent car 0.95); vector fills and modern
fonts; textures > 256 px on props/vehicles; DXT5; mips on vehicle/ped/weapon textures; painted panel lines;
bright saturated interiors; white un-baked prelight instead of texture shading.

Full guide: docs/agent/style/textures.md
