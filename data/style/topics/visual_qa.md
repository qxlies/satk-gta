# Visual QA round (workflow S26): judge an asset against vanilla and its references

Use it at every gate of S25 and for any finished DFF, mod folder or session. One round = one sheet + the
region sheets + the reference board + the checks; never a stream of full-size images. Judge by eye; numbers
are information. Pass only when the inventory is complete, `asset.check --strict` says `done: true` and
every region passed (`done`); never pass "for now" or on the builder's word.

## The round (about 4-6 calls)
1. `blender.preview <SID | dff path | mod folder | session:NAME> --like <SID>` (or `--lineup class`)
   `--passes game,clay,wire --states ok,dam,vlo,col --dirt 2 --time 12:00`: ONE JPEG sheet (<= 1,024 px) with
   your asset and two peers of the same body type at the same scale, camera, light and paint (session models
   get the paint swap and the wheel on every dummy). True side view: `--ref <photo> --ref-view side`. Add a LOW
   three-quarter camera when you judge composition (`camera.add`, then `blender.call ... --snapshot <camera>`).
2. Read the sheet ONCE next to the reference board and `refs/features.md`, in this order:
   - identity: does it read as the object from about 10 m; each listed feature present, missing or wrong;
   - proportions and stance (wheel size and position, ride height, roof line, glass height, overhangs);
   - form: rounded sections, crowned surfaces, corners in smooth steps; no flat walls, no hard boxes;
   - composition: one joined whole; no floating, gapped or see-through parts; arches lined; lines across cuts;
   - detail: the inventory items and the class checklist (lamps, grille, mirrors, interior, bay ...);
   - shading (seams hard, corners soft), materials (clean paint, white lamps, dark interior behind glass);
   - textures (small, soft, photo-like; no vector art, no grime everywhere).
3. `asset.check <file> --like <SID> --tier <tier> --md --strict`: `engine` errors must be fixed; `form`,
   `fit`, `symmetry` and defect rows are located defects; `coverage` lists what is missing; `reference` is
   information; `done`/`blocking` is the verdict. Project: `asset.inventory <project>` (missing, unattached,
   rejected items: list every one; refuse weak rejections such as time, budget, "not visible").
4. Regions: `blender.preview <subject> --regions` = one close-up sheet per region of the kind (front, rear,
   sides, wheels, underside, interior, bay, roof; facades, roof, entrance, base; ...). On each sheet find the
   region's items (check at least 3 `built` ones are real, finished and attached: a placeholder fails the
   region), then gaps, floats, z-fighting stripes, dark (flipped) faces, stretch, seams. `look.leak`
   (`--passes leak`): background inside the outline that is not a window or opening = a gap.
5. Texture pass when textures changed: native-scale lossless crops (never a downscaled JPEG) and
   `style.texture <png|txd> --role auto`.
6. World assets: add `--time 23:00` (night colours, lit windows, light pools) and look at the model in its map
   context.
7. Verdict: every missing item, then at most 8 fixes, most visible first, each with part, problem, evidence
   (view, region, feature or row) and an instruction a modeller can carry out (`asset.status --record`); pass
   or fix per region; show the sheets to the user at the gates. Never "N pixels off" or "count below the band".

## What "game look" means (the preview models it)
- Glass blended at its material alpha (the interior is visible), lamp materials white, paint key swapped for a
  real colour, dirt level 2 (raw `vehiclegrunge256` is dirtier than the game ever shows), env sheen, specular.
- Prelit models: object ambient from the time cycle on top of the prelight; day and night colours.
- A raw import (opaque black glass, black lamp codes, full grime, neon paint key) is NOT the game look: never
  judge or calibrate on it.

## Image rules (any task)
- Never open more than 1 MP; one sheet per cycle; JPEG for previews, lossless only for texture crops.
- Reference photos at most 1,600 px (`ref.import`); the board at most 1 MP (`ref.board`).

## Common findings and fixes
- Square or voxel look -> re-section with rounded shapes, crown, soft moves (`style_construction`).
- Parts float or gap -> `mesh.attach`, `mesh.flare`, cut panels from the shell; `form` rows clean.
- Faceted or bevelled-box shading -> `kit.shade` by seams and creases (`style_shading`).
- Parts placed wrong -> refit dummies; `fit` rows; check in the game.
- Too few details -> the detail pass (`coverage` rows).
- Dirty or crisp textures -> clean paint, `texture.finish` with grime 0 and a soft downscale.
- Bright interior through glass -> darken to value 0.1-0.2.
- Damage that reads as undamaged -> deeper dents, scratch and shatter overlays.
- White or blue night prelight -> `blender.game_ready --asset-class <class>` (dark grey day, warm night).

Full workflow: docs/agent/workflows.md
