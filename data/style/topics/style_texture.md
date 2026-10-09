# SA style: textures (small, soft, photo-like; clean car paint)

SA textures are SMALL (about 128 px, 64-256), PHOTO-sourced, SOFT (slightly blurry at native size: low
resolution plus DXT1), desaturated, with light, occlusion and folds painted in. Shape is geometry, surface is
texture: what has volume and reads at game distance is modelled; grille mesh, tread, badges, stitching,
instruments, bricks and window bays are painted. Numbers below are vanilla reference.

## How each class uses textures
- Map models TILE modular photo textures (one window bay, one storey, a wall or ground patch): `uv.span` p50 3-6
  on buildings and terrain; the dark baked prelight does the shading; moderate stains, mostly low on walls.
- Vehicles reuse `vehicle.txd`: body = clean paint KEY colour over `vehiclegrunge256` (the engine adds dirt low
  on the sides), trim/chrome/glass/grille/underbody = regions of `vehiclegeneric256`, lamps =
  `vehiclelights128`, gloss = env sheen `xvehicleenv128` on UV2. Own textures: dark interior atlas, rim, maybe a
  detail page (badges, grille mesh, gauges).
- Peds: one 128x256 photo atlas, folds painted in, photographic face. Weapons: a 64 px photo on both sides.
- Props, pickups: 64-128 px; signage bold, saturated, slightly blurry.

## Vehicle textures: clean and soft
- Paint is never painted: no body dirt, no painted shading, no panel lines in own textures.
- Grime and wear OFF by default (`texture.finish` `grime` 0, `wear` 0 for vehicle roles); wear only where use
  leaves it (seat edges, pedals, steering rim, tyre sidewall).
- Paint at 4x, then downscale with a soft filter (`texture.finish` `supersample`, or Lanczos/box), add only a
  faint grain: slightly out of focus at native size, like a downscaled photo.
- Interiors dark (value 0.1-0.2), desaturated; one atlas for seats, door cards, dash, headliner.

## Formats (32,878 vanilla textures)
DXT1 89 %, DXT3 6.9 %, X8R8G8B8 3.2 %, A8R8G8B8 0.9 %, DXT5 0. Powers of two; 128x128 33 %, 256x256 24 %,
64x64 16 %; 512+ only 2.9 %. Vehicles, peds, weapons: exactly ONE mip level (a mip warning there is wrong);
`sa_plus` own textures may be one size step larger. `texture.pack --asset-class vehicle|ped|weapon|map|lod`
writes these; `texture.audit` skips `no_mips` for one-level classes; never add mips to them.

## Vanilla role reference (p10 / p50 / p90; `style.texture <png|txd> --role auto` compares yours)
- Car interiors (84): exact colours 344 / 607 / 928; luminance 20.3 / 32.1 / 60.6 of 255; value 0.088 / 0.142
  / 0.243; saturation 0.09 / 0.22 / 0.35. Dark, low contrast, hundreds of colours.
- Vehicle own textures (574): luminance 23 / 59 / 161; saturation 0.02 / 0.12 / 0.37.
- Map classes: luminance p50 110-126 of 255, saturation p50 0.10-0.14 (vegetation 0.33, pickups 0.41).
- Failed agent textures: 19-77 colours (vector, "mobile game"); an interior at value 0.42 (glows through glass);
  crisp grain and grime everywhere (reads dirty and sharp, not SA).

## Photo-like, not CG-clean (look advice; quiet, never dirt)
A photo varies softly inside every region (tone, light from above, a little hue). `style.texture <png|txd>
--cls <class>` and the asset.check `texture` section judge the role's vanilla textures (the class picks the
role): flat/CG-clean = `tex.tone_mid` (fine variation) under the role's p5 (prop 1.3, p50 7.3) or
`tex.flat_share` (dead-flat 5x5) over p95 (prop 0.27); too sharp = `tex.crisp` (edge contrast / variation)
over p95 (prop 43). About 6 % / 5 % of vanilla get it too: advice, never blocking. The first atelier bin paint
(one green, crisp dots): 0.81, 0.66, 72 = both. Fix: `texture.finish <4x paint> --supersample 4 --photo 0.6`
(soft drift, mottling and grain, hue drift, light from above (not on wall/ground), grime 0; lands tone_mid at
the role's p34; keeps the paint): the bin became 4.1, 0.0003, 11. Loud blotches are camouflage, not a photo.

## Photo-like without photos (recipe)
1. Work at 4x: a mid-dark base with soft blotches (8-16 px at final size), only a faint grain; never a flat fill.
2. Paint the form: folds, pleats, panel edges as soft light/dark pairs; darken edges and corners (baked AO,
   `kit.bake`); faint top-light gradient.
3. Desaturate (0.1-0.3), push to warm grey/brown; interiors value 0.1-0.2.
4. Wear only where use leaves it; map textures: moderate stains at the base.
5. Text: blocky, slightly blurred; never anti-aliased vector type.
6. Downscale softly; judge AFTER DXT1 at native size (lossless crops, not a JPEG).
Tools: `texture.new` (flat base), `texture.finish <png> --preset photo_like|interior|wheel|wall` (`photo`,
`grime`, `wear`, `grain`, `soft`, `supersample`; `--mask` AO, `--edge` EDGE), session `kit.bake`;
`style.texture --cls`.

## Shared vehicle atlases (measured on 144 cars)
- Glass on `vehiclegeneric256`: u 0.59-0.66, v 0.94-1.0 holds 51 %; dark glass spans u 0.25-0.75, v 0.88-1.0.
- Colour-coded paint (jambs, inner panels): u 0-0.25, v 0.31-0.375 on `vehiclegeneric256`.
- Hand-labelled: chrome bands v 0.31-0.37 and 0.50-0.75; black plastic u 0.25-0.50, v 0.50-0.75; grille mesh
  u 0.75-1.0, v 0.88-1.0; underbody photo strips on the right half.
- `vehiclegrunge256`: clean upper zone for up-facing panels, grime band rising from the bottom for sides.
- `kit.uv_region` projects faces into a named region at its measured place.

## Anti-patterns
One flat colour per mesh; CG-clean flat fills with crisp marks; vector fills and modern fonts; crisp 1-px
grain; loud noise; grime painted over vehicle textures;
textures > 256 px on props/vehicles; DXT5; mips on vehicle/ped/weapon textures; painted panel lines; bright
saturated interiors; white un-baked prelight instead of texture shading.

Full guide: docs/agent/style/textures.md
