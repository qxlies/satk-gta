# Visual QA round (workflow S26): judge an asset against vanilla in one sheet

Use it at every gate of S25 (G1, G2, G5) and for any finished DFF, mod folder or session. One round = one sheet +
one stats answer + one check; never a stream of full-size images.

## The round (about 3-4 calls)
1. `blender.preview <SID | dff path | mod folder | session:NAME> --like <SID>` (or `--lineup class`)
   `--passes game,clay,wire --states ok,dam,vlo,col --dirt 2 --time 12:00`: ONE JPEG sheet (<= 1,024 px,
   <= 300 KB) + stats JSON. The lineup puts your asset and two class peers at a fixed distance with identical
   cameras, light and paint.
2. Read the sheet ONCE. Compare with the peers, in this order:
   - silhouette and stance (wheel size and position, ride height, roof line, overhangs; building massing);
   - size (the lineup is to scale);
   - density (clay + wire: triangles where the outline turns, long triangles on flats, round segment counts);
   - shading (game look: soft panels, crisp seams; no faceting, no smeared seams);
   - materials (paint dirt band, lamps white in daylight, glass shows a dark interior, chrome sheen);
   - textures (photo-like noise, dark interiors, no flat fills, no vector art).
3. `asset.check <file> --like <SID> --tier <tier> --md`: structure, metric and semantic rows. Every out-of-band
   row gets a fix or a written reason; structure and semantic rows must be fixed.
4. Texture pass when textures changed: native-scale lossless crops (never a downscaled JPEG) and
   `style.texture <png|txd> --role auto`.
5. World assets: add `--time 23:00` (night colours, lit windows, light pools) and look at the model in its map
   context (the map preset of `blender.preview`).
6. Record the verdict as at most 10 fixes, most visible first (`asset.status --record`); show the sheet to the
   user at G1, G2 and G5 and wait for the answer.

## What "game look" means (the preview models it)
- Glass blended at its material alpha (the interior is visible), lamp materials white, dirt level 2 (raw
  `vehiclegrunge256` is dirtier than the game ever shows), env sheen scaled like the game, specular.
- Prelit models: object ambient from the time cycle on top of the prelight; day and night colours.
- Peds upright; muzzle-flash atomics hidden.
- A raw import (opaque black glass, black lamp codes, full grime) is NOT the game look: never calibrate on it.

## Image rules (any task)
- Never open more than 1 MP; one sheet per cycle; JPEG for previews, lossless only for texture crops.
- Reference photos at most 1,600 px (`ref.import`).
- Stats before pictures: read the numbers first, open the sheet only to judge what numbers cannot.

## Common findings and fixes
- Faceted panels -> `kit.shade`; finished DFF -> `rw.patch --smooth-normals` (`satk_help("style_shading")`).
- Too small or too real-scale -> rescale from the wheel anchor / ped height (`dims.L_rel`).
- Flat colours or one-texel UVs -> `kit.uv_region`, real UVs on the shared atlases.
- Bright interior through glass -> darken to value 0.1-0.2.
- Vector-looking textures -> `texture.finish` (`satk_help("style_texture")`).
- Damage that reads as undamaged -> deeper dents (p90 >= 12 cm), scratch and shatter overlays.
- White or blue night prelight -> `blender.game_ready --asset-class <class>` (dark grey day, warm night).

Full workflow: docs/agent/workflows.md
