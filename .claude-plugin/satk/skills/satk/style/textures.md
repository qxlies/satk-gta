# Textures: small, soft, photo-like

<!-- Model-facing, English only. Conventions: README.md "How to read the numbers". Measured on the 32,878 textures
     of the clean 1.0 US copy (profile vanilla); the role bands come from the texture statistics of the style
     analysis (vanilla car interiors, vehicle own textures, map classes). All numbers are vanilla reference. -->

San Andreas textures are **small** (about 128 px, 64-256), **photo-sourced**, **soft** (slightly blurry at native
size: low resolution plus DXT1), colour-graded towards dusty, desaturated tones, with light, occlusion and folds
painted in. Shape is geometry, surface is texture: what has volume and reads at game distance is modelled
(`construction.md`), what is flat or fine is painted (grille mesh, tread, badges, stitching, instruments, bricks
and window bays on map models). How each class uses them:

- **Map models** tile modular photo textures (one window bay, one storey, a wall or ground patch) many times over
  the surface (`uv.span` 3-6 on buildings and terrain), and the dark baked prelight does the shading. Map
  textures may carry moderate stains, mostly at bases and below windows.
- **Vehicles** reuse the shared `vehicle.txd`: the body is a clean **paint key colour** over `vehiclegrunge256`
  (the engine swaps in one of 16 **dirt** levels and keeps tops clean), trim, chrome, glass, grille and underbody
  are regions of `vehiclegeneric256`, lamps are `vehiclelights128`, and the **environment sheen**
  (`xvehicleenv128` on UV2) makes the paint glossy. Own textures: a dark interior atlas, a rim, maybe a detail
  page (badges, grille mesh, gauges).
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

| Metric | Peer set (n) | p10 | p50 | p90 | Note |
|---|---|---|---|---|---|
| `tex.side_px[all]` | all vanilla textures (32,878) | 64 | 128 | 256 | larger side; `sa_plus` own textures one step larger |

Rules: DXT1 for opaque, DXT3 for alpha (DXT1 1-bit for cut-outs), never DXT5; one level for vehicles, peds and
weapons (a mip warning on those classes is wrong); power-of-two sides. `sa_plus` may use one size step more on
own textures (interior 256, wheel 128); 512 only for terrain or a landmark. `texture.pack --asset-class
vehicle|ped|weapon|map|lod` writes these formats. `texture.audit` finds oversized, uncompressed and duplicate
textures; it skips `no_mips` for vehicle, ped, weapon and upgrade TXDs (field `one_level`) and still reports it for
map TXDs. Never run `texture.optimize --mips` on the one-level classes.

## The look, by role (vanilla reference)

`style.texture <png or txd> --role auto` measures a texture against these:

| Metric | Peer set (n) | p10 | p50 | p90 | Note |
|---|---|---|---|---|---|
| `tex.colours[interior]` | car interiors, `style.texture` role (84) | 344 | 607 | 928 | exact colours |
| `tex.colours15[interior]` | car interiors, `style.texture` role (84) | 66 | 113 | 170 | 5 bits per channel |
| `tex.lum_mean[interior]` | car interiors, `style.texture` role (84) | 20.3 | 32.1 | 60.6 | of 255: dark |
| `tex.lum_std[interior]` | car interiors, `style.texture` role (84) | 18.7 | 24.4 | 45.5 | low contrast |
| `tex.val_mean[interior]` | car interiors, `style.texture` role (84) | 0.088 | 0.142 | 0.243 |  |
| `tex.sat_mean[interior]` | car interiors, `style.texture` role (84) | 0.093 | 0.217 | 0.354 |  |
| `tex.hf_energy[interior]` | car interiors, `style.texture` role (84) | 18.3 | 22.2 | 27.2 | fine grain everywhere |
| `tex.colours15[vehicle own]` | own textures of vehicles (574) | 12.6 | 88 | 219 | includes tiny decals |
| `tex.lum_mean[vehicle own]` | own textures of vehicles (574) | 23 | 59 | 161 | of 255 |
| `tex.lum_std[vehicle own]` | own textures of vehicles (574) | 17.9 | 40.8 | 66.3 |  |
| `tex.sat_mean[vehicle own]` | own textures of vehicles (574) | 0.02 | 0.12 | 0.37 |  |
| `tex.hf_energy[vehicle own]` | own textures of vehicles (574) | 7.7 | 23.0 | 53.6 |  |

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
- Car interiors are very dark (value about 0.14) and low-contrast, yet have hundreds of colours: photo leather
  and plastic, not flat fills. A bright interior glows through the alpha-128 glass.
- Luminance spread alone does not separate vector art from photo-like textures; the colour count does. Agent
  textures that failed: 19-77 exact colours (vector art, "mobile game" look); an interior with 936 colours but
  value 0.42 (too bright).
- Vehicle rims and tyres are greyscale with local contrast; lamp lenses are photographed with chrome
  reflectors; decals and stickers are DXT3 white shapes on alpha with chunky, slightly blurry text.

## Vehicle textures: clean and soft

- **Paint is never painted.** The body is the key colour on `vehiclegrunge256`; the engine adds dirt low on the
  sides. Own textures carry no body dirt, no painted shading and no panel lines.
- **Grime and wear are off by default.** Wear only where use leaves it: seat edges and bolsters, pedals, the
  steering rim, the tyre sidewall, a step plate. `texture.finish` has `grime` and `wear` set to 0 for vehicle
  roles; raise them only for a deliberately worn vehicle and say so.
- **Paint at 4x, then downscale.** Paint or compose each own texture at four times its size (seat pleats, door
  card, gauges, rim spokes, badge), with soft light from above and darkened edges, then reduce it with a soft
  filter (`texture.finish` `supersample`, or a Lanczos/box reduction) and add only a faint grain. The result
  should look slightly out of focus at native size, like a downscaled photo. Judge it after DXT1 at native size
  (lossless crops), next to a vanilla interior.
- **Dark interiors** (value about 0.1-0.2), desaturated; one atlas for seats, door cards, dash and headliner.

## Photo-like, not CG-clean (the look advice)

A vanilla texture is a photograph: inside every region the tone drifts softly, the light that fell on the object
is baked in (lighter at the top), the colour wanders a little, and nothing is razor-sharp at 40-80 px/m. That
variation is **quiet and is not dirt**: no stains, streaks or grime are needed to look photo-like. A clean CG
paint is the opposite: one flat colour per region with crisp painted marks on it. `style.texture <png or txd>
--cls <class>` and the `texture` section of `asset.check` measure the look against the vanilla textures of the
same role (the class picks the role: a prop's `ls_bin_paint` is a prop texture, not car paint):

| Metric | Peer set (n) | p10 | p50 | p90 | Note |
|---|---|---|---|---|---|
| `tex.tone_mid[prop]` | street and interior props, `style.texture` role (160) | 1.82 | 7.31 | 20.4 | fine tonal variation; advice below p5 1.3 |
| `tex.tone_mid[wall]` | buildings and interior shells, `style.texture` role (160) | 2.16 | 6.95 | 17.8 | p5 1.5 |
| `tex.tone_mid[ground]` | terrain, `style.texture` role (160) | 2.35 | 6.76 | 16.2 |  |
| `tex.tone_mid[interior]` | car interiors, `style.texture` role (84) | 4.38 | 6.12 | 8.09 | photo leather and plastic |
| `tex.tone_mid[body]` | vehicle body textures, `style.texture` role (50) | 0 | 4.99 | 9.65 | paint keys are flat by design |
| `tex.tone_lo[prop]` | street and interior props, `style.texture` role (160) | 3.54 | 11.7 | 32.8 | soft drift and light inside regions |
| `tex.chroma_lo[prop]` | street and interior props, `style.texture` role (160) | 0.65 | 2.64 | 16.0 | slight hue drift |
| `tex.flat_share[prop]` | street and interior props, `style.texture` role (160) | 0 | 0.002 | 0.11 | dead-flat patches; advice above p95 0.27 |
| `tex.flat_share[wall]` | buildings and interior shells, `style.texture` role (160) | 0 | 0.0004 | 0.05 | p95 0.15 |
| `tex.crisp[prop]` | street and interior props, `style.texture` role (155) | 4.65 | 8.57 | 23.1 | crisp marks; advice above p95 42.7 |
| `tex.crisp[wall]` | buildings and interior shells, `style.texture` role (156) | 4.62 | 8.19 | 28.0 | p95 41.3 |

The advice: **flat/CG-clean** when `tex.tone_mid` is below the role's p5 or `tex.flat_share` is above its p95
(and at least 0.2); **too sharp** when `tex.crisp` is above its p95. About 6 % (flat) and 5 % (sharp) of the
vanilla sample get it too, so it is advice, never a gate, and it never blocks `asset.check --strict` (it is
listed under `advice`). Decals and car paint keys are flat by design: their own roles allow it.

Example: the bin of the first atelier run (`ls_bin_paint`, 256 px, role prop) was one green with two crisp
rows of dots and a small crest: `tex.tone_mid` 0.81, 66 % of the pixels dead flat, `tex.crisp` 72. Both
advices; the critic had scored the surface 9/10. The old noise finish on the same paint chased a contrast
target with camouflage blotches (luma spread 0.06 -> 0.21) and was rightly rejected. `texture.finish
--supersample 4 --photo 0.6 --role prop` on its 4x master gives `tex.tone_mid` 4.1, flat share 0.0003,
`tex.crisp` 11: photo-like, the same green, the crest and dots still there.

**The fix: `texture.finish --photo`** (0..1, 0.6 is a good start) adds the variation of a photo and nothing
else: a soft low-frequency tonal drift, mottling with soft vertical runs and a soft grain, a slight hue drift,
and a soft light gradient from above (none on the tiling roles wall and ground: it would seam at every repeat).
It keeps the painted structure and colour, sets grime to 0 for every role (raise `grime` only for a
deliberately dirty object), and lands the fine variation of the DXT1 preview at the role's vanilla percentile
10 + 40 x photo (p34 at 0.6). Soften crisp marks with `--soft 1` and paint them at 4x. Judge the DXT1 preview
at native size next to a vanilla texture of the role; `style.texture --cls <class>` must show no look advice.

## Photo-like textures without photos (the recipe)

When photographs cannot be used, reproduce the look of a photo source, never a vector drawing:

1. Start at 4x the final size from a mid-dark base with **soft blotches** (8-16 px at the final size) and only a
   faint grain; never a flat fill.
2. Paint the form: folds, pleats and panel edges as soft light and dark pairs; **darken edges and corners**
   (baked occlusion, `kit.bake` masks); a faint top-light gradient.
3. Desaturate (saturation about 0.1-0.3) and push towards warm grey or brown. Interiors stay at value 0.1-0.2.
4. Wear only where use leaves it (see above). Map textures: moderate stains, mostly at the base.
5. Text and logos: blocky, slightly blurred; never anti-aliased vector type.
6. Downscale with a soft filter, then look at the result **after DXT1 compression at native size** (lossless
   crops, never a downscaled JPEG). On a clean paint, `texture.finish --photo 0.6` does steps 1-2 and 6 for you
   (variation, light, soft downscale) without dirt.

Tools: `texture.new` makes the flat base (size, `color`, `gradient`, `rect` fills); `texture.finish <png> --preset
photo_like|interior|wheel|wall` does the variation, occlusion and DXT preview (`photo` for the quiet variation of a
photo, else a noise; `grime`, `wear`, `grain`, `soft` and `supersample` tune it; `--mask` and `--edge` take the
baked masks, a mask of another size is resized, warn `RESIZED_MASK`); the session bakes ambient occlusion and an
edge mask from your mesh (`kit.bake`, `size` N, [w, h] or "WxH"); `style.texture --cls <class>` compares the
result with the vanilla role and gives the look advice.

## Shared vehicle atlases: where things are

Use regions of the shared textures instead of new textures (`kit.uv_region` projects faces into a named region
at its measured place). Measured on the UV usage of 144 vanilla cars; the "support" is the share of vanilla
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

- One flat colour per mesh or single-texel UVs.
- Vector-art fills, gradients and clean UI-like drawings; modern anti-aliased fonts.
- CG-clean paint: one flat colour per region with crisp marks (`style.texture`: flat/CG-clean, too sharp).
- Crisp textures: a sharp 1-px grain or hard painted lines that survive the downscale.
- Loud noise to look "photo-like": big blotches and a doubled contrast read as camouflage; a photo's variation
  is a few luma levels.
- Dirt and grime painted over a vehicle texture; streaks and specks everywhere.
- Textures above 256 px on props and vehicles; a 1024 px body texture; DXT5; mip chains on vehicle, ped or
  weapon textures.
- Painted panel lines on a vehicle body (vanilla panel lines are real cuts with dark jambs).
- Bright, saturated interiors; white or un-baked prelight standing in for texture shading on map models.

Single-texel mapping in vanilla (reference):

| Metric | Peer set (n) | p10 | p50 | p90 | Note |
|---|---|---|---|---|---|
| `uv.zero_area_share[car]` | cars, HD parts (144) | 0.014 | 0.046 | 0.105 | an agent car with one colour per mesh had 0.95 |
| `uv.zero_area_share[vehicle]` | every vehicle of the `cars` section (204) | 0.02 | 0.05 | 0.13 |  |
| `uv.zero_area_share[weapon]` | weapons (50) | 0.0 | 0.065 | 0.26 |  |
| `uv.zero_area_share[ped]` | peds (265) | 0.013 | 0.044 | 0.10 |  |
| `uv.zero_area_share[map object]` | 600 sampled map objects (no LODs) | 0.0 | 0.0 | 0.19 |  |

