# SA style guides: what "looks like San Andreas" means, in numbers

<!-- Model-facing, English only. Every number was measured on the clean 1.0 US copy (index profile `vanilla`,
     2026-10-05) by the satk style analysis; nothing is recalled from memory. Tables follow the conventions of
     "How to read the numbers"; tests/docs/test_style_tables.py checks them. When `style.card` exists, the band
     tables are regenerated from it with the same metric names and peer sets. -->

These guides tell an agent how to build a new asset of ANY kind (vehicle, prop, building with LOD, interior,
weapon, ped, pickup, vehicle upgrade) so that it sits next to the stock game without looking foreign. They replace
style research: read the help topic of your class (`satk_help("style_vehicle")`, ...) and, only when you need
the detail, one file here.

| File | Read it when |
|---|---|
| `README.md` (this file) | always: rules, tiers, metric names, anti-patterns, checklist, operations |
| `shading.md` | any dynamically lit asset (vehicles, peds, weapons, upgrades): normals, the Blender recipe |
| `vehicles.md` | any vehicle type: budgets per class and part, scale, frames, materials, dirt, damage, LOD, COL |
| `world.md` | props, buildings, LODs, terrain, interiors, vegetation, pickups: budgets, texel density, prelight, flags |
| `peds-weapons.md` | peds (skinned) and weapons |
| `textures.md` | any texture: look by role, the photo-like recipe without photos, formats, atlas regions |
| `modelling.md` | how to model in the live Blender session: recipes per kind, segment counts per tier, modifiers |
| `limits.md` | engine capacity, names, ids and the construction mistakes that crash the game |

## How to read the numbers

- **Band tables** start with the column `Metric`. Each row names one metric (glossary below), optionally with a
  qualifier in brackets (`part.tris[wheel]` = the metric for the wheel part), the **peer set** it was measured on
  (class and number of models), and `p10`, `p50`, `p90`: linear-interpolated percentiles over the models of the
  peer set. A note says when a row is different (`pooled` = one share over all items; `min/mean/max`).
- The column `sa_plus (proposal)` is the higher-detail tier. Its numbers are a **proposal**: they are not
  validated in game or by the user yet; `vanilla` numbers are measured. `same` / `unchanged` = the vanilla band
  holds in `sa_plus` too; `-` = no separate proposal: stay near the vanilla band, and let triangle counts grow only
  as far as the class total of your tier allows (`veh.hd_tris`, `geo.tris`).
- **Class tables** put the qualifier (a class or a size bucket) in the first column, `n` in the second, and one
  metric per column header (cells are p50 unless the header says `p10 / p50 / p90`): `geo.tris` in the row
  `street_prop` is the band `geo.tris[street_prop]`.
- **Fact tables** start with the column `Fact` and carry a `Source` column: engine constants, colour keys,
  capacities. They are not bands.
- One metric (with its qualifier) has exactly one band table in this folder; other places refer to it.
- Bands are advisory: being inside p10..p90 does not make an asset good, and a landmark may leave a band with a
  written reason. Structure and semantics (frames, keys, textures, names) are not advisory.

## The ten rules

1. **Measure, do not recall.** Before modelling take the class profile (`style.profile <class> --tier <tier>`)
   and the anatomy of the model you replace or resemble (`asset.anatomy <SID>`). Design inside the band of every
   metric of your tier.
2. **Model in Blender, live.** Use the session (`blender.session start`, `blender.call`): primitives with explicit
   segment counts, modifiers, reference planes, a snapshot and stats after every step. Never type vertex lists
   and never write a private mesh library (`modelling.md`).
3. **Scale from anchors.** Vehicles: the wheel mesh diameter equals the IDE `wheel_scale` and every other
   dimension follows from it (`vehicles.md`); SA cars are about 1.1-1.2x the real car, wheels stay real size.
   Everything else: the ped is 1.84 m tall.
4. **Silhouette first.** Triangles go where the outline turns (wheel arches, bumper corners, lamp and grille
   surrounds, roof and pillar edges, cornices). Flat areas are a few long triangles; windows, grilles, bolts,
   brickwork and panel detail are texture.
5. **Smooth shading.** Weld, smooth every face, split normals only at material and UV seams and at designed
   creases. Cars: smooth below 30 deg, decide 30-60 deg by design, hard above 60 deg. Peds are fully smooth. Map
   objects carry no normals: their shading is baked into the prelight (`shading.md`).
6. **Reuse the shared textures and colour keys.** Vehicle paint is a key colour on `vehiclegrunge256` (that is
   where the dirt lives), lamps are key colours on `vehiclelights128`, glass is `vehiclegeneric256` with material
   alpha 128. Map models tile district textures.
7. **Textures are small, photo-like and dirty.** 64-256 px, DXT1 (DXT3 for alpha), low saturation, shading,
   folds and grime painted in. Never vector fills, never one flat colour per mesh (`textures.md`).
8. **Light like the class.** Vehicles, peds and weapons: normals, no prelight. Map objects: dark grey day prelight,
   darker warm night colours with light pools; collision face light derived from the prelight.
9. **Ship every part.** Damage parts at 0.8-1.1x the triangles of their `_ok` part, the LOD, the collision (and the
   shadow mesh for vehicles), frames and dummies in vanilla order and side, names within the limits, the data
   lines (`limits.md`).
10. **Judge against vanilla, not against taste.** `asset.check` (structure, metrics, semantics), the game-look
    lineup next to two class peers (`blender.preview --lineup class`), the lint preset of your tier. Show the
    lineup sheet to the user at gates G1 and G2 (workflow S25).

## Tiers

| Tier | Meaning | Use it for | Lint preset |
|---|---|---|---|
| `sa_plus` | the same visual language with higher budgets only where silhouette and curvature live | every NEW asset (default) | `sa_plus` |
| `vanilla` | every metric inside the measured band of its class | a replacement that must blend into traffic or a district unnoticed | `vanilla` |

Record the tier and the reason in the project manifest (`asset.init ... --tier`). `sa_plus` raises:
triangles of the chassis, interior, wheels, arches, bumpers and nose profiles, round props and poles; the size
of own textures (one step, for example interior 128 -> 256). It does NOT change: shared textures and colour keys,
paint on the dirt texture with the UV2 sheen, the shading rule, the photo-like texture look, formats (DXT1/DXT3,
one mip level for vehicles, peds and weapons, never DXT5), scale and proportions, frame trees and dummies, LOD
budgets, collision and shadow budgets, draw distance, prelight and night rules, data lines. A map LOD keeps the
vanilla LOD budget; it is not a ratio of an `sa_plus` HD model.

## Metric names

One name per quantity; the definitions follow `satk.style.registry` (the metric code that `style.profile`,
`asset.check`, the session stats and lint use). Names the registry does not compute yet (derived ratios,
data-line fields, class shares such as `dims.L_rel`, `mat.refl`, `light.night_models_share`) were measured by the
style analysis and are used only by these guides until the registry adds them. Ranges for a class come from the
file in the last column.

| Name | Unit | Definition | Bands in |
|---|---|---|---|
| `veh.hd_tris` | tris | chassis + every atomic that is not `_dam`/`_vlo` + the wheel mesh counted once | vehicles.md |
| `veh.hi_tris` | tris | what renders undamaged: like `veh.hd_tris`, the wheel counted once per wheel dummy, plus the largest `extra` | vehicles.md |
| `part.tris` | tris | triangles of one frame's geometry; qualifier = the frame (`part.tris[chassis]`) | vehicles.md |
| `mat.count` | count | unique materials of a model; `mat.per_geom` per geometry | vehicles.md, world.md, peds-weapons.md |
| `mat.per_geom` | count | materials of one geometry; qualifier = the frame | vehicles.md |
| `mat.refl` | 0..1 | reflection-plugin intensity (the car "shininess") per material slot; qualifier = material kind | vehicles.md |
| `mat.spec` | 0..1 | specular level per material slot (texture `vehiclespecdot64`) | vehicles.md |
| `dam.ok_ratio` | ratio | tris(`<part>_dam`) / tris(`<part>_ok`) | vehicles.md |
| `dam.disp_cm` | cm | per-vertex displacement of a `_dam` part against its `_ok` part: p50 or p90 per part | vehicles.md |
| `dims.L` | m | bounding box length along Y of the HD selection (no `_dam`/`_vlo` atomics) | vehicles.md |
| `dims.W`, `dims.H` | m | bounding box width (X, vehicles: of the chassis, mirrors on doors excluded) and height (Z) | vehicles.md |
| `dims.wheelbase`, `dims.track`, `dims.wheel_d` | m | front-rear wheel dummy distance, 2 x the right wheel dummy X, IDE `wheel_scale` | vehicles.md |
| `dims.L_rel` | ratio | a dimension divided by the anchor: `wheel_d` for vehicles (also `W_rel`, `H_rel`, `wheelbase_rel`, `track_rel`); the code's `dims.L_over_wheel` anchors | vehicles.md |
| `dims.W_rel`, `dims.H_rel`, `dims.wheelbase_rel`, `dims.track_rel` | ratio | see `dims.L_rel` | vehicles.md |
| `dims.wheel_ratio` | ratio | wheel mesh diameter / IDE `wheel_scale` | vehicles.md |
| `frame.pos` | 0..1 | frame position normalised to the bounding box per axis (0 = min, 1 = max); qualifier = frame | vehicles.md |
| `dims.origin_z` | m | model origin above the ground (ground = wheel dummy z - wheel_d / 2) | vehicles.md |
| `dims.real_ratio` | ratio | game length / length of the real counterpart (indicative: few known counterparts) | vehicles.md |
| `dims.size` | m | largest bounding box side: the size bucket of map models, the length of a weapon | world.md, peds-weapons.md |
| `shade.normal_bend` | deg | area-weighted mean angle between corner normal and face normal | shading.md |
| `shade.flat_share` | share | triangles whose three corner normals are within 1 deg of the face normal | shading.md |
| `shade.hard_by_dihedral` | share | edges with split normals per dihedral bin (qualifier = bin in degrees) | shading.md |
| `shade.hard_at_seam` | share | hard edges that lie on a material or UV-island border | shading.md |
| `geo.tris` | tris | triangles of a single-geometry model (map objects, peds, weapons, pickups, upgrades) | world.md, peds-weapons.md |
| `geo.tris_per_m2` | 1/m2 | triangles per square metre of the model's own surface | vehicles.md, world.md |
| `geo.median_edge_m` | m | median edge length | vehicles.md, world.md |
| `geo.median_dihedral` | deg | median angle between neighbouring triangles | vehicles.md |
| `geo.thirds` | share | triangles in the front / middle / rear third of the length (vehicles) | vehicles.md |
| `geo.pieces` | count | connected pieces of one geometry | vehicles.md |
| `geo.largest_piece_share` | share | triangles in the largest connected piece | vehicles.md |
| `geo.sliver_share` | share | triangles with a smallest angle below 8 deg | vehicles.md |
| `geo.open_edge_share` | share | boundary (open) edges among all edges | vehicles.md |
| `geo.round_sides` | count | segments of a round silhouette (wheel rim, arch, hydrant, pole); qualifier = the feature | modelling.md |
| `dff.verts_per_tri` | ratio | DFF vertices per triangle (vertex sharing; split normals and seams raise it) | shading.md |
| `uv.zero_area_share` | share | triangles whose UV triangle has zero area (single-texel mapping) | textures.md |
| `uv.texel_px_m` | px/m | sqrt(UV area x texture w x h / world area), area-weighted; qualifier = texture, class or size bucket | vehicles.md, world.md, peds-weapons.md |
| `uv.area_share` | share | share of the visible surface mapped to a texture; qualifier = texture | vehicles.md |
| `uv.span` | uv | UV range of UV set 0 (above 1 = tiling); the bands take the largest range of a material | world.md |
| `tex.side_px` | px | larger side of a texture | textures.md, world.md |
| `tex.own_count` | count | textures in a model's own TXD | vehicles.md |
| `tex.txd_kb` | KB | size of a TXD | vehicles.md, world.md, peds-weapons.md |
| `txd.models` | count | models that share one TXD | world.md |
| `txd.textures` | count | textures in one TXD | world.md |
| `tex.colours` | count | exact distinct RGB colours of a texture | textures.md |
| `tex.colours15` | count | distinct colours after reducing each channel to 5 bits | textures.md |
| `tex.lum_mean` | luma 0..255 | mean luma | textures.md |
| `tex.lum_std` | luma 0..255 | standard deviation of the luma | textures.md |
| `tex.sat_mean` | 0..1 | mean HSV saturation | textures.md |
| `tex.val_mean` | 0..1 | mean HSV value | textures.md |
| `tex.hf_energy` | luma 0..255 | mean absolute 3x3 Laplacian of the luma (fine grain; vector art is near 0 inside its fills) | textures.md |
| `light.prelit_lum_p50` | 0..255 | median luminance of a model's day prelight | world.md |
| `light.prelit_lum_std` | 0..255 | standard deviation of the day prelight luminance | world.md |
| `light.dark_vertex_share` | share | prelit vertices below luminance 16 | world.md |
| `light.white_vertex_share` | share | prelit vertices above luminance 240 | world.md |
| `light.night_ratio` | ratio | night luma / day luma of a model's vertices (the bands use the means) | world.md |
| `light.night_tint` | 0..255 | mean R - mean B of the night colours | world.md |
| `light.lit_vertex_share` | share | vertices brighter at night than by day (lit windows, lamp pools) | world.md |
| `light.night_models_share` | share | models of a class that carry night colours | world.md |
| `light.normal_models_share` | share | models of a class that carry normals | world.md |
| `handling.mass`, `handling.max_vel` | kg, game units | `handling.cfg` fields per body class (other fields: `style.profile`) | vehicles.md |
| `lod.ratio` | ratio | LOD triangles / HD triangles | world.md |
| `ide.draw` | m | IDE draw distance | world.md |
| `col.spheres`, `col.boxes` | count | collision primitives | vehicles.md |
| `col.mesh_faces` | count | collision mesh faces | vehicles.md, world.md |
| `col.shadow_faces` | count | COL3 shadow mesh faces | vehicles.md |
| `col.shadow_ratio` | ratio | shadow mesh faces / `veh.hi_tris` | vehicles.md |
| `col.mesh_ratio` | ratio | collision mesh faces / render triangles | world.md |

## Reconciled rules (earlier measurements disagreed; these are final)

- **Shading:** one dihedral rule for cars: smooth below 30 deg, decide 30-60 deg by design (beltline, shut lines,
  arch lip and roof edge hard; rounded body corners smooth), hard above 60 deg, always hard at material and UV
  seams; bumpers and tyres fully smooth. The old wording "many hard creases" is withdrawn: vanilla bodies have
  many split normals, but they sit on seams (`shading.md`).
- **Triangle budgets:** always name the definition. `veh.hd_tris` counts the wheel once, `veh.hi_tris` once per
  wheel dummy; earlier "sedan budgets" mixed both.
- **Damage parts are not lighter:** `dam.ok_ratio` is 0.8-1.1 for the build target; vanilla has heavier `_dam`
  parts on most cars. The rule "`_dam` never has more triangles than `_ok`" is wrong.
- **Sedan dimensions:** one peer set (the 24 curated sedans of `vehicles.md`) and one definition (DFF bounding
  box; `W` includes mirrors). Earlier sets (25 "sedans", 47 "4-door cars") are retired.
- **Scale:** about 1.1-1.2x the real car, wheels at real size (0.70 m for cars); never "close to real scale".
- **Paint:** key colour x `vehiclegrunge256`, the UV V coordinate follows the panel height so dirt rises from the
  sills; never paint a body on `vehiclegeneric256` alone (it never gets dirty).
- **Glass alpha:** 128. The value 242 belongs to the damage overlays (`vehiclescratch64`, `vehicleshatter128`)
  and the cut-out textures (`vehicledash32`, `vehiclesteering128`), never to glass (`vehicles.md`).
- **Texel density:** per size bucket of map models (`world.md`), not one global value; a uniform 32 px/m is right
  for buildings and terrain and 3-7x too low for props.
- **Prop bevels: unmeasured.** Clay renders show single chamfers on round props (hydrant and bin caps), while map
  models in general put no bevels, window frames or panel gaps into geometry. Until a measurement exists: one
  chamfer on a prop's silhouette edge is allowed, flat walls and buildings get none.

## Anti-patterns (each one seen in agent-built assets)

| Seen | Vanilla instead | Caught by |
|---|---|---|
| hard faceted shading (an agent car: 0.81 deg bend, 0.47 flat) | soft normals, hard only at seams | `shade.normal_bend`, `shade.flat_share` |
| one flat colour per mesh, single-texel UVs (95 % zero-area) | real UVs on the shared atlases, 1-10 % zero-area | `uv.zero_area_share` |
| voxel or box look: loose slabs and floating details (89 pieces, largest 6.5 %) | one welded shell, largest piece about 74 % | `geo.largest_piece_share`, `geo.pieces` |
| vector-art textures with 19-77 colours | photo-like noise, 344+ colours in an interior texture | `tex.colours`, `tex.hf_energy` |
| bright interior behind glass (value 0.42) | dark interiors, value about 0.14 | `tex.val_mean` |
| real-world scale (4.98 m sedan) | about 1.1-1.2x real | `dims.L_rel`, `dims.L` |
| triangles spent on flat skin (median dihedral 2 deg) | few long triangles on flats, density at the ends | `geo.median_dihedral`, `geo.thirds` |
| paint off the dirt texture | paint key on `vehiclegrunge256` | `asset.check` semantic row `veh.paint_dirt` |
| hand-typed geometry in scripts | session methods, modifiers, reference planes | session journal (`blender.session`) |

## Checklist before showing an asset to the user

1. Game-look lineup next to two class peers (same camera, light and paint): silhouette, stance, size, density.
2. Clay + wire: density at ends and edges, long triangles on flats, the round segment counts of your tier.
3. Normals: `shade.*` inside the class band on the re-read DFF; seams hard, panels soft.
4. Budgets per part inside the tier band; `_dam` 0.8-1.1x `_ok`; LOD and collision at vanilla budgets.
5. Shared textures and keys used (paint on `vehiclegrunge256`, lamps on `vehiclelights128`, glass alpha 128,
   scratch and shatter overlays on `_dam`); map models tile district textures.
6. Own textures: few, small, photo-like, dark interiors, DXT1/DXT3, one mip level for vehicles, peds, weapons;
   `style.texture` in band; looked at at native size after DXT compression.
7. `asset.check` and `asset.lint --preset <tier>`: no structure or semantic errors; every out-of-band row
   explained.

## Which operations to run

All are `satk_op` operations (CLI: `satk <group> <command>`); find them with `satk_ops("<words>")`.

| Step | Operation | What it gives |
|---|---|---|
| style numbers for a class | `style.profile <class or --like SID> --tier sa_plus` | bands with peer set, exemplars, anchors |
| the same as a Markdown table | `style.card <class> --tier vanilla --md` | the band tables of these guides |
| what the replaced model is made of | `asset.anatomy <SID or dff> --md` | frames with positions, parts, materials, COL, TXD |
| a brief without anti-pattern wording | `style.brief_check <brief.md>` | flagged phrases with the measured alternative |
| project and resume card | `asset.init <dir> --kind --intent --like --tier --target`, `asset.status` | `asset.json`, gates, checkpoints |
| live modelling | `blender.session start`, `blender.methods`, `blender.call` | stats and a snapshot per step |
| templates, shading, generators, export | `kit.kinds`, `kit.template`, session methods `kit.*`, `kit.export` | scaffold, `sa_shade`, wheel, vlo, damage, LOD, COL, package |
| SA-look preview and lineup | `blender.preview <SID, dff, folder or session:NAME> --lineup class` | one JPEG sheet + stats |
| texture look | `style.texture <png or txd> --role auto` | colours, luminance, saturation, HF vs the role band |
| conformance | `asset.check <dff, folder or SID> --like <SID> --tier <tier> --md` | structure, metrics and semantic rows |
| lint with vanilla baseline | `asset.lint <target> --preset sa_plus --baseline vanilla` | rules the stock game also breaks are suppressed |
| fix shading of a finished DFF | `rw.patch <dff> --smooth-normals --recalc-bsphere` | welded smooth normals, frame-local spheres |
| TXD in vanilla formats | `texture.pack <folder> --asset-class vehicle --out <mod folder>` | DXT1/DXT3, one level, no DXT5 |
| photo-like texture from a flat fill | `texture.finish <png> --preset interior` | two-scale noise, AO, grime, DXT preview |

All of these are registered (CLI-only, `mcp=False`): run them with `satk_op`, find them with `satk_ops("<words>")`
or `satk help --find <words>`. When Blender cannot start, use the fallback named in workflow S25 and say so.
