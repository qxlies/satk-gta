# Textures: how SA assets are textured

<!-- Model-facing, English only. Conventions: README.md "How to read the numbers". Measured on the 32,878 textures
     of the clean 1.0 US copy (profile vanilla); the role bands come from the texture statistics of the style
     analysis (vanilla car interiors, vehicle own textures, map classes). -->

San Andreas looks like San Andreas mostly because of its textures: **small** (64-256 px), **photo-sourced**,
colour-graded towards dusty, desaturated tones, with shading, folds, grime and edge wear **painted in**, and
blurred by DXT1 compression. Detail is in the texture, not in the geometry. How each class uses them:

- **Map models** tile modular photo textures (one window bay, one storey, a wall or ground patch) many times over
  the surface (`uv.span` 3-6 on buildings and terrain), and the dark baked prelight does the shading. A whole city
  block can be 300 triangles with a 256 px facade repeated across it.
- **Vehicles** reuse the shared `vehicle.txd`: the body is a flat **paint key colour** over `vehiclegrunge256`
  (the engine swaps in one of 16 **dirt** levels), trim, chrome, glass, grille and underbody are regions of
  `vehiclegeneric256`, lamps are `vehiclelights128`, and the **environment sheen** (`xvehicleenv128` on UV2) makes
  the paint glossy. Own textures are only a dark interior atlas (128 px) and a rim (64 px), sometimes decals.
- **Peds** are one 128x256 photo atlas with folds, seams and shadows painted in and a photographic face.
- **Weapons** are one 64 px photo of the real weapon unwrapped onto both sides, plus an icon and a muzzle flash.
- **Props and pickups** are 64-128 px; signage and branding are bold, saturated, slightly blurry graphics.

## Formats and sizes

| Fact | Value | Source |
|---|---|---|
| formats, all 32,878 textures | DXT1 89 % (29,260; 484 of them with 1-bit alpha), DXT3 6.9 %, X8R8G8B8 3.2 %, A8R8G8B8 0.9 %, PAL8 1, DXT5 0 | vanilla index |
| sizes | all powers of two; 128x128 33 %, 256x256 24 %, 64x64 16 %, 32x32 7 %; 512 and more only 2.9 % (terrain, big buildings); 2:1 textures are common | vanilla index |
| mip levels | 21 % of textures have mips: terrain 43 %, buildings 32 %, props 14 %, LODs 0.2 %, vehicles, peds and weapons 0 % | vanilla index |
| vehicles | own textures DXT1 (487 of 573) or DXT3 for alpha (86), one level; `vehicle.txd` uncompressed 32-bit, one level | vanilla index |
| peds | 128x256 X8R8G8B8 uncompressed (253 of 289), one level | vanilla index |
| weapons | 64x64 or 32x32, DXT1 (51) or DXT3 (16), one level | vanilla index |

| Metric | Peer set (n) | p10 | p50 | p90 | sa_plus (proposal) | Note |
|---|---|---|---|---|---|---|
| `tex.side_px[all]` | all vanilla textures (32,878) | 64 | 128 | 256 | one size step, 512 max (terrain, landmark) | larger side |

Rules: DXT1 for opaque, DXT3 for alpha (DXT1 1-bit for cut-outs), never DXT5; one level for vehicles, peds and
weapons (a mip warning on those classes is wrong); power-of-two sides. `texture.pack --asset-class
vehicle|ped|weapon|map|lod` writes these formats. `texture.audit` finds oversized, uncompressed and duplicate
textures; it skips `no_mips` for vehicle, ped, weapon and upgrade TXDs (field `one_level`) and still reports it for
map TXDs. Never run `texture.optimize --mips` on the one-level classes.

## The look, by role

Role bands (`style.texture <png or txd> --role auto` measures a texture against them):

| Metric | Peer set (n) | p10 | p50 | p90 | sa_plus (proposal) | Note |
|---|---|---|---|---|---|---|
| `tex.colours[interior]` | car interiors, `style.texture` role (84) | 344 | 607 | 928 | same | exact colours |
| `tex.colours15[interior]` | car interiors, `style.texture` role (84) | 66 | 113 | 170 | same | 5 bits per channel |
| `tex.lum_mean[interior]` | car interiors, `style.texture` role (84) | 20.3 | 32.1 | 60.6 | same | of 255: dark |
| `tex.lum_std[interior]` | car interiors, `style.texture` role (84) | 18.7 | 24.4 | 45.5 | same | low contrast |
| `tex.val_mean[interior]` | car interiors, `style.texture` role (84) | 0.088 | 0.142 | 0.243 | same | |
| `tex.sat_mean[interior]` | car interiors, `style.texture` role (84) | 0.093 | 0.217 | 0.354 | same | |
| `tex.hf_energy[interior]` | car interiors, `style.texture` role (84) | 18.3 | 22.2 | 27.2 | same | fine grain everywhere |
| `tex.colours15[vehicle own]` | own textures of vehicles (574) | 12.6 | 88 | 219 | same | includes tiny decals |
| `tex.lum_mean[vehicle own]` | own textures of vehicles (574) | 23 | 59 | 161 | same | of 255 |
| `tex.lum_std[vehicle own]` | own textures of vehicles (574) | 17.9 | 40.8 | 66.3 | same | |
| `tex.sat_mean[vehicle own]` | own textures of vehicles (574) | 0.02 | 0.12 | 0.37 | same | |
| `tex.hf_energy[vehicle own]` | own textures of vehicles (574) | 7.7 | 23.0 | 53.6 | same | |

Map classes, peds and weapons (texture means per model; luminance of 255):

| Class | n | `tex.lum_mean` p10 / p50 / p90 | `tex.sat_mean` p10 / p50 / p90 |
|---|---|---|---|
| street_prop | 699 | 51 / 110 / 165 | 0.015 / 0.14 / 0.61 |
| building_small | 734 | 71 / 120 / 174 | 0.022 / 0.11 / 0.43 |
| building_medium | 1,117 | 72 / 124 / 175 | 0.022 / 0.11 / 0.40 |
| building_large | 1,371 | 69 / 124 / 171 | 0.019 / 0.10 / 0.40 |
| terrain_road | 2,179 | 84 / 122 / 164 | 0.032 / 0.14 / 0.40 |
| vegetation | 314 | 15 / 74 / 143 | 0.067 / 0.33 / 0.57 |
| interior_prop | 1,413 | 47 / 119 / 184 | 0.018 / 0.19 / 0.60 |
| lod | 4,349 | 89 / 126 / 164 | 0.024 / 0.11 / 0.34 |
| pickup | 28 | 61 / 117 / 170 | 0.012 / 0.41 / 0.74 |
| ped | 265 | 46 / 80 / 133 | 0.081 / 0.25 / 0.45 |
| weapon | 50 | 68 / 115 / 178 | 0.036 / 0.15 / 0.85 |

What these numbers say:
- Map textures are mid-grey and desaturated (saturation p50 0.10-0.14); the prelight darkens them in game.
- Car interiors are very dark (value about 0.14) and low-contrast, yet have hundreds of colours and fine grain:
  photo leather and plastic, not flat fills. A bright interior glows through the alpha-128 glass.
- Luminance spread alone does not separate vector art from photo-like textures; the colour count and the
  high-frequency energy do. Agent textures that failed: 19-77 exact colours (vector art, "mobile game" look);
  an interior with 936 colours but value 0.42 (too bright).
- Vehicle rims and tyres are greyscale with high local contrast; lamp lenses are photographed with chrome
  reflectors; decals and stickers are DXT3 white shapes on alpha with chunky, slightly blurry text.

## Photo-like textures without photos (the recipe)

When photographs cannot be used, reproduce the look of a photo source, never a vector drawing:

1. Start from a mid-dark base with **noise at two scales** (blotches of 8-16 px and a 1-2 px grain), never a flat
   fill. Aim for at least 300 distinct colours in a 128 px texture.
2. Paint the form: folds, pleats and panel edges as soft light and dark pairs; **darken every edge and corner**
   (baked occlusion); a faint top-light gradient.
3. Desaturate (saturation 0.1-0.3) and push towards warm grey or brown. Interiors stay at value 0.1-0.2.
4. Add sparse grime: streaks below edges, specks, worn highlights on raised edges. On cars keep it subtle: body
   dirt comes from the dirt system.
5. Text and logos: blocky, slightly blurred; never crisp anti-aliased vector type.
6. Work at 4x the size, downsample with a box or Lanczos filter, then look at the result **after DXT1
   compression at native size** (lossless crops, never a downscaled JPEG).

Tools: `texture.new` makes the flat base (size, `color`, `gradient`, `rect` fills); `texture.finish <png> --preset
photo_like|interior|wheel|wall` does steps 1-6 on it (`--role` lands the result in the `style.texture` band of that
role, up to 4 rounds; `--mask` and `--edge` take the baked masks, a mask of another size is resized, warn
`RESIZED_MASK`); the session bakes ambient occlusion and an edge mask from your mesh (`kit.bake`, Cycles, about 0.1 s
headless, `size` N, [w, h] or "WxH") as masks for steps 2 and 4; `style.texture` checks the result against the role
band.

## Shared vehicle atlases: where things are

Use regions of the shared textures instead of new textures (`kit.uv_region` projects faces into a named region
at the tier's texel density). Measured on the UV usage of 144 vanilla cars; the "support" is the share of vanilla
usage inside the region.

| Fact | Value | Source |
|---|---|---|
| glass on `vehiclegeneric256` | u 0.59-0.66, v 0.94-1.0 holds 51 % of vanilla glass triangles; the dark glass area spans u 0.25-0.75, v 0.88-1.0; two measuring methods agree (r 0.97) | UV usage, 144 cars |
| colour-coded paint on `vehiclegeneric256` | core at u 0-0.25, v 0.31-0.375: door jambs, inner panels, mirror bodies (paint without dirt); methods agree (r 0.83) | UV usage, 144 cars |
| chrome bands on `vehiclegeneric256` | v 0.31-0.37 and v 0.50-0.75 (hand-labelled, support not measured) | atlas sheet |
| black plastic, grille, underbody | black noisy plastic u 0.25-0.50, v 0.50-0.75; grille mesh u 0.75-1.0, v 0.88-1.0; engine and underbody photo strips on the right half (hand-labelled) | atlas sheet |
| `vehiclegrunge256` | clean upper area for up-facing panels, grime band rising from the bottom edge for sides | atlas sheet, dirt renders |
| `vehiclelights128` | photographed lamp lenses: headlamps with chrome reflectors, dark red tail lamps with vertical ribs | atlas sheet |
| `vehicletyres128` | tread and sidewall strips (64x128) | atlas sheet |

## Anti-patterns

- One flat colour per mesh or single-texel UVs (`uv.zero_area_share` far above the class band).
- Vector-art fills, gradients and clean UI-like drawings; modern anti-aliased fonts.
- Textures above 256 px on props and vehicles; a 1024 px body texture; DXT5; mip chains on vehicle, ped or
  weapon textures.
- Painted panel lines on a vehicle body (vanilla panel lines are real cuts with dark jambs).
- Bright, saturated interiors; white or un-baked prelight standing in for texture shading on map models.

| Metric | Peer set (n) | p10 | p50 | p90 | sa_plus (proposal) | Note |
|---|---|---|---|---|---|---|
| `uv.zero_area_share[car]` | cars, HD parts (144) | 0.014 | 0.046 | 0.105 | same | an agent car had 0.95 |
| `uv.zero_area_share[vehicle]` | every vehicle of the `cars` section (204) | 0.02 | 0.05 | 0.13 | same | |
| `uv.zero_area_share[weapon]` | weapons (50) | 0.0 | 0.065 | 0.26 | same | |
| `uv.zero_area_share[ped]` | peds (265) | 0.013 | 0.044 | 0.10 | same | |
| `uv.zero_area_share[map object]` | 600 sampled map objects (no LODs) | 0.0 | 0.0 | 0.19 | same | |
