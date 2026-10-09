# SA style guides: the San Andreas look, the engine rules, and vanilla reference numbers

<!-- Model-facing, English only. The look is DESCRIBED here and in construction.md; the hard rules are what the
     engine needs; every number was measured on the clean 1.0 US copy (index profile `vanilla`) by the satk style
     analysis and is reference, never a target. Tables follow "How to read the numbers";
     tests/docs/test_style_tables.py checks them. -->

These guides tell an agent how to build a new asset of ANY kind (car, bike, bicycle, boat, helicopter, plane,
trailer, train, prop, building with LOD, interior, weapon, ped, pickup, vehicle upgrade) so that it sits next to
the stock game as if it shipped with it, complete and without gaps. Read the help topic of your class
(`satk_help("style_vehicle")`, ...), `satk_help("style_construction")`, `satk_help("style_kinds")` and
`satk_help("done")`; open a file here when you need the detail.

| File | Read it when |
|---|---|
| `README.md` (this file) | always: the look, the hard rules, tiers, metric names, anti-patterns, checklist, operations |
| `done.md` | always: the definition of done, the inventory (the task list), close-up regions, the leak pass, defect classes, the strict check, the critic's review, per-kind items, regions and hero detail |
| `kinds.md` | any kind that is not a road car: helicopter, plane, boat, bicycle, motorbike, quad, trailer, train, building with LOD, interior, weapon, ped skin, props, pickups, upgrades, sets of models |
| `construction.md` | before modelling anything: how vanilla models are built (shell, sections, arches, bumpers, lamps, details, props, buildings) and how to check it |
| `references.md` | the asset depicts a real object: spec sheet, features list, which photos may be measured, the reference board, how a critic compares |
| `modelling.md` | how to model in the live Blender session: form, composition, detail pass, recipes per kind, modifiers |
| `vehicles.md` | any vehicle type: look, detail, frames and dummies (refitting), bikes, materials, paint, damage, LOD, COL; vanilla numbers in its appendix |
| `shading.md` | any dynamically lit asset (vehicles, peds, weapons, upgrades): seams hard, corners soft |
| `textures.md` | any texture: soft, small, photo-like; clean car paint; formats; the shared vehicle atlases |
| `world.md` | props, buildings, LODs, terrain, interiors, vegetation, pickups: prelight, flags, draw distance |
| `peds-weapons.md` | peds (skinned) and weapons |
| `limits.md` | the hard engine limits: capacity, names, ids, formats and the construction mistakes that crash the game |

## The San Andreas look

**Shape language.** Soft, rounded, "pillowy", simplified forms. Every surface is a little crowned (roofs,
bonnets, door skins, walls of props), every corner turns in two or three smooth steps, glasshouses lean in,
lower bodies bulge like a barrel. Nothing is a hard box, and nothing is a sharp modern crease line except where
a material or a panel changes. The model is ONE coherent whole: a welded shell carries the body, panels are cut
from it, arches have liners, bumpers wrap into the arches, details sit in recesses or touch the skin they belong
to, characteristic lines run across the cuts. The silhouette carries the design; secondary detail is modelled
when it reads at game distance (lamp buckets, grille surround, bumpers, mirrors, handles, interior, engine bay)
and painted when it is flat or fine (grille mesh, tread, badges, stitching, bricks).

**Proportions.** Chunky and planted: SA cars are about 1.1-1.2 times the real car and relatively wider, with
thick bumpers and a pinched greenhouse. The donor's lines are kept (roof line, glass rake, overhangs, beltline,
lamp and grille arrangement), its millimetres are not. Props and buildings are measured against the 1.84 m ped.

**Shading.** Soft normals on a rounded form; hard edges only on seams (material and UV borders) and on creases
you can name. Peds fully smooth. Map models have no normals: a dark, baked prelight with occlusion gives them
their soft look (`shading.md`, `world.md`).

**Textures.** Small (about 128 px, 64-256), photo-sourced, soft and slightly blurry at native size, desaturated,
with light and occlusion painted in; never vector fills or one flat colour per mesh. Car paint is a clean key
colour on the shared dirt texture: the engine adds the dirt, own textures never paint it. Wear appears only
where use leaves it (`textures.md`).

**Detail.** Free. A model may be much richer than vanilla (a real interior, an engine bay, underbody parts,
deeper lamps) as long as it is built in this language. Triangle counts are never a target, a limit or a reason
to simplify.

How vanilla builds all this, rule by rule and with evidence: `construction.md`.

## Hard rules: what the engine needs

These are rules; everything else is style judged by eye. Details and checks in `limits.md` and the class guides.

- Frame names, parents and order of the model type (`veh.frames`); every `ug_*` frame of a replaced car; dummies
  on the right side (`headlights`, `taillights`, `ped_frontseat` on the right; `ped_arm` on the left); hinges at
  the dummy origins.
- Colour keys: paint 60,255,0 / 255,0,175 on `vehiclegrunge256`; lamp keys only on `vehiclelights128`; glass
  material alpha 128; UV2 on every geometry whose material uses `xvehicleenv128`.
- The wheel mesh diameter equals the IDE `wheel_scale`; one wheel mesh, cloned to the wheel dummies.
- Vertex normals on dynamically lit models; prelight (day and night) on map models.
- Collision: embedded COL3 with a valid shadow mesh for vehicles, valid surfaces, vertices within 256 m of the
  origin; one `.col` archive per map pack.
- Names: model, TXD and frame at most 23 characters (21 for a model with collision), texture at most 31, IMG and
  Mod Loader file names at most 23 including the extension; LOD naming.
- TXD: power-of-two sides, DXT1/DXT3 (or 32-bit), never DXT5, exactly one mip level for vehicles, peds, weapons.
- At most 65,535 vertices per geometry (16-bit indices).
- Map models with collision draw at most 299 m (the big-building rule).
- Ped skin: 32 bones in the vanilla order and ids, at most 4 weights per vertex, one material.
- Ids and capacity of the target (vehicle, ped, weapon and object slots, TXD and COL slots, 2DFX in the DFF).

## How to read the numbers

All numbers in these guides describe vanilla. They help you see what "rounded", "small" or "dark" means and
where vanilla puts its detail. They are never targets, gates or warnings: do not add or remove geometry to move
a count, and never judge a model by a count. Counts appear in tool answers as plain information.

- **Band tables** start with the column `Metric`. Each row names one metric (glossary below), optionally with a
  qualifier in brackets (`part.tris[wheel]` = the metric for the wheel part), the **peer set** it was measured on
  (class and number of models), and `p10`, `p50`, `p90`: linear-interpolated percentiles over the models of the
  peer set. A note says when a row is different (`pooled` = one share over all items; `min/mean/max`).
- **Class tables** put the qualifier (a class or a size bucket) in the first column, `n` in the second, and one
  metric per column header (cells are p50 unless the header says `p10 / p50 / p90`): `geo.tris` in the row
  `street_prop` is the band `geo.tris[street_prop]`.
- **Fact tables** start with the column `Fact` and carry a `Source` column: engine constants, colour keys,
  capacities, construction evidence. They are not bands.
- One metric (with its qualifier) has exactly one band table in this folder; other places refer to it.
- Earlier revisions carried `sa_plus (proposal)` columns with higher triangle ranges; they are withdrawn. No tier
  has triangle numbers.

## Tiers

| Tier | Meaning | Use it for | Lint preset |
|---|---|---|---|
| `sa_plus` | the same visual language at a free detail level: richer silhouette and curvature, a real interior, an engine bay, deeper lamp and grille housings, own textures one size step larger | every NEW asset (default) | `sa_plus` |
| `vanilla` | the same language with about the detail of the stock models of the class (compare by eye with the peers) | a replacement that must blend into traffic or a district unnoticed | `vanilla` |

Record the tier and the reason in the project manifest (`asset.init ... --tier`). Neither tier changes the hard
rules, the shared textures and colour keys, paint on the dirt texture with the UV2 sheen, the shading rule, the
soft photo-like texture look, scale and proportions, frame trees and dummies, the vehicle LOD and the map LOD,
collision and shadow, draw distance, prelight and night colours, data lines.

## The ten rules

1. **Describe, then build.** Before modelling write a design description: proportions in words and ratios from
   the spec sheet, the signature features from every photo, how the parts meet (`references.md`). Take the
   anatomy of the model you replace (`asset.anatomy <SID>`): frames, parts, materials.
2. **Form first.** Build the main forms in the live Blender session from authored rounded sections and sweeps
   (`mesh.loft` with section shapes, `mesh.sweep`, `mesh.lathe`), refined with soft moves. Never a cube with a
   chamfer as a body part; never vertex-by-vertex meshes; the blank is an optional quick start (`modelling.md`).
3. **Compose one whole.** One welded shell, panels cut from it, details touching their parent, arches with
   liners, lines running across the cuts; every floating or gapped piece is a defect (`construction.md`).
4. **Scale from anchors.** Vehicles: the wheel mesh diameter equals the IDE `wheel_scale`, and the body follows
   the spec ratios at about 1.1-1.2 times the real object. Everything else: the 1.84 m ped.
5. **Then the detail pass, item by item.** The inventory (`<project>/design/inventory.json`, written at G0 from the
   kind's starter list and the features list) is the task: build every item in the same soft language (lamps
   in buckets, grille surround, mirrors on stalks, handles, interior, engine bay, underbody; rotors, gear,
   hull fittings, facades, sights), tag it, and check it off with `asset.inventory`; flat and fine detail is
   texture (`done.md`).
6. **Shade and light like the class.** Dynamically lit assets: weld, smooth every face, hard only on seams and
   named creases, rounded corners soft (`shading.md`). Map objects: no normals, a dark day prelight with baked
   occlusion and warmer, darker night colours (`world.md`).
7. **Reuse the shared textures and colour keys.** Vehicle paint is a key colour on `vehiclegrunge256` (that is
   where the engine's dirt lives), lamps are key colours on `vehiclelights128`, glass is `vehiclegeneric256` with
   material alpha 128. Map models tile district textures.
8. **Textures are small, soft and photo-like.** About 128 px, slightly blurred at native size, desaturated,
   light and folds painted in; car paint stays clean; grime and wear off unless use leaves them (`textures.md`).
9. **Fit the function.** Refit every dummy to your geometry (lamps, exhaust, petrol cap, hinges, seats); bikes:
   the steering axis runs through the headset inside the body and the rider's contact points stay where the
   animation puts them (`vehicles.md`). Ship every part: damage states, LOD, collision (and the shadow mesh for
   vehicles), data lines (`limits.md`).
10. **Judge by eye, region by region; never declare yourself done.** At every gate one sheet: the lineup next
    to two vanilla peers (style), the reference board and the features list (likeness), clay views (form and
    composition), and from G2 one close-up sheet per region (`blender.preview --regions`). Done means the
    inventory is complete, `asset.check --strict` answers `done: true` (no engine errors, no form, fit or
    defect rows, no leak gaps) and the reviewer passed every region (`done.md`); reference numbers are
    information. Show the sheets to the user (workflow S25).

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
| `tex.tone_mid` | luma 0..255 | median absolute difference of the luma and the luma blurred at 1/64 of the side: fine tonal variation | textures.md |
| `tex.tone_lo` | luma 0..255 | median absolute difference of the luma blurred at 1/64 and at 1/8 of the side: soft drift and light | textures.md |
| `tex.chroma_lo` | luma 0..255 | the band of `tex.tone_lo` on colour minus luma (largest channel, median): hue drift | textures.md |
| `tex.flat_share` | share | pixels whose 5x5 window spans at most 2 luma levels (dead-flat patches) | textures.md |
| `tex.crisp` | ratio | median 5x5 contrast at strong edges / `tex.tone_mid`: crisp marks on a flat ground | textures.md |
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


## Reconciled rules (earlier wording disagreed; these are final)

- **Style is described, not counted.** The SA look is the shape language, shading and texture look above, with
  the vanilla numbers as reference. Triangle, density and shading numbers are never gates; an asset is judged
  by eye next to vanilla and its references, plus the engine rules and the `form`/`fit` defects of `asset.check`.
- **Density is placed, not capped.** Rich detail is welcome and never counted. What is a finding is *wasted*
  density: fine triangles on flat or gently curved surface (`form.dense_flat`, a uniformly dense subdivided
  mesh): a defect on map models (placed many times), advice on vehicles and other kinds (`construction.md`).
- **Photo-like is quiet, not dirty.** The texture look advice (flat/CG-clean, too sharp) asks for the soft tonal,
  light and hue variation of a photo, never for grime (`textures.md`).
- **Shading:** decided per line, not by one angle: hard on material and UV seams and on named creases (beltline,
  moulding, bonnet creases, shut lines), smooth on every rounded corner even where the fold is steep; bumpers and
  tyres fully smooth. The old wording "smooth below 30 deg, hard above 60 deg" is withdrawn: vanilla keeps a
  quarter to almost half of its 60-90 deg folds smooth (`shading.md`).
- **Detail:** "detail is texture, not geometry" is withdrawn for vehicles. Model what reads at game distance in
  the same soft language; keep flat and fine detail in the texture. Map models keep windows, bricks and bolts in
  tiling textures.
- **Damage parts are not lighter:** `_dam` is the `_ok` panel re-cut and visibly dented (`kit.damage`); vanilla
  has heavier `_dam` parts on most cars. The rule "`_dam` never has more triangles than `_ok`" is wrong.
- **Sedan dimensions:** one peer set (the 24 curated sedans of `vehicles.md`) and one definition (DFF bounding
  box; `W` of the chassis, mirrors on doors excluded).
- **Scale:** about 1.1-1.2x the real car, the wheel mesh equal to `wheel_scale`; never "close to real scale".
- **Paint:** key colour x `vehiclegrunge256`, up-facing faces in the clean upper zone, the UV V coordinate
  following the panel height on the sides so the engine's dirt rises from the sills; never paint a body on
  `vehiclegeneric256` alone (it never gets dirty) and never paint dirt into it.
- **Glass alpha:** 128. The value 242 belongs to the damage overlays (`vehiclescratch64`, `vehicleshatter128`)
  and the cut-out textures (`vehicledash32`, `vehiclesteering128`), never to glass (`vehicles.md`).
- **Texel density:** per size bucket of map models (`world.md`), not one global value; a uniform 32 px/m is right
  for buildings and terrain and 3-7x too low for props.
- **Prop edges:** props are kits of closed primitives with chamfered caps and chamfered vertical edges (hydrant,
  phone booth, dumpster lip); flat building walls get no bevels (`construction.md`).

## Anti-patterns (each one seen in agent-built assets)

| Seen | Vanilla instead | Caught by |
|---|---|---|
| voxel or box look: bodies and parts made of cubes with one chamfer, flat sides, flat roof, square plan | rounded sections, crowned surfaces, corners in smooth steps | the low three-quarter clay view; `form` row `hard_corners` |
| parts not joined: flares, bumpers, rails, pillars and fascia standing off the body | one welded shell, panels cut from it, details touching | `form` rows `floating`, `loose_share`, `see_through` |
| dummies left where the template put them; bike steering axis outside the body; seat and bars off the rider pose | dummies on their lamps, pipe and hinges; steering column around the axis | `fit` rows; the in-game check |
| too few details: no lamp buckets, no interior beyond boxes, no engine | every class item present, richer welcome | `coverage` rows; the detail pass |
| stopped at the first acceptable lineup and declared the model finished: parts missing, regions never looked at, gaps left | an itemised inventory built to the end, every region reviewed close up, polished until the strict check passes | `asset.inventory`, `asset.check --strict`, the region sheets (`done.md`) |
| placeholders: a box for a seat, a cylinder for an engine, a recoloured face for a lamp | the real item in the soft language, joined to its parent | the critic's region review |
| moving parts off their pivots: rotors, propellers, control surfaces, pedals swinging off the body | each moving part modelled in its frame's space around the frame origin | `fit` rows, the in-game check (`kinds.md`) |
| hard faceted shading, or one smoothing angle for everything | soft corners, hard only on seams | clay view; `shading.md` |
| one flat colour per mesh, single-texel UVs | real UVs on the shared atlases | `uv.zero_area_share` (reference) |
| vector-art textures; or crisp, grimy textures | soft photo-like textures, clean car paint | `style.texture` (reference), a look at native size |
| bright interior behind glass | dark interiors | `tex.val_mean` (reference) |
| real-world scale | about 1.1-1.2x real | the lineup; `dims.L_rel` (reference) |
| paint off the dirt texture | paint key on `vehiclegrunge256` | `asset.check` engine row `veh.paint_dirt` |
| photos measured with grids, solved cameras or back-projection | spec sheet, features list, true views only | `references.md` |
| geometry changed to move a number (dissolve, decimate, subdivide for a count) | counts are a result of the form | never do it |

## Checklist before showing an asset to the user

1. Likeness: the reference board and the features list next to the model; every identity feature present.
2. Style: the game-look lineup next to two vanilla peers of the body type (same camera, light, paint at dirt 2):
   silhouette, stance, size, soft forms.
3. Form and composition: clay views including a low three-quarter one; no flat walls, no hard boxes, no floating
   or gapped parts, arches closed; `asset.check` `form` rows clean.
4. Fit: dummies on their surfaces, hinges on the part edges, wheels centred in the arches; bikes: steering and
   rider (`fit` rows).
5. Detail: every inventory item and the class checklist (`coverage` rows); interior and engine bay seen
   through the glass or with the bonnet open (other kinds: their regions, `done.md`).
6. Surface: seams hard, corners soft; shared textures and keys (paint on `vehiclegrunge256`, lamps on
   `vehiclelights128`, glass alpha 128); own textures small and soft, looked at at native size after DXT.
7. `asset.check` and `asset.lint --preset <tier>`: no engine (structure or semantic) errors; damage visibly
   dented; LOD and collision present.
8. Done: `asset.inventory` complete (every required item built; starter items dropped only with a waiver),
   every region sheet read, `look.leak` without gaps, `asset.check --strict` `done: true` (`done.md`).

## Which operations to run

All are `satk_op` operations (CLI: `satk <group> <command>`); find them with `satk_ops("<words>")`.

| Step | Operation | What it gives |
|---|---|---|
| what the replaced model is made of | `asset.anatomy <SID or dff> --md` | frames with positions, parts, materials, COL, TXD |
| vanilla numbers of a class (reference) | `style.profile <class or --like SID>` | percentiles with peer set, exemplars, anchors |
| the same as a Markdown table | `style.card <class> --tier vanilla --md` | the reference tables of these guides |
| a brief without anti-pattern wording | `style.brief_check <brief.md>` | flagged phrases with the better wording |
| references | `ref.import <photo> --project <asset> --view <view>`, `ref.board --project <asset>`; session methods `ref.plane` {`view`, `image`, `length`} and `look.silhouette` (true views only) | prepared photos, one board, a scaled plane, an outline overlap (information) |
| project and resume card | `asset.init <dir> --kind --intent --like --tier --target`, `asset.status` | `asset.json`, gates, checkpoints |
| live modelling | `blender.session start`, `blender.methods`, `blender.call` | counts, `form` defects and a snapshot per step |
| templates, shading, generators, export | `kit.template`, session methods `kit.*`, `kit.export` | frames, slots, shading, wheel, vlo, damage, LOD, COL, package |
| SA-look preview and lineup | `blender.preview <SID, dff, folder or session:NAME> --lineup class` | one JPEG sheet + counts |
| texture look | `style.texture <png or txd> --role auto` | colours, luminance, saturation, fine grain against vanilla |
| conformance | `asset.check <dff, folder or SID> --like <SID> --tier <tier> --md` | engine errors, `form`, `fit`, `symmetry`, `coverage`, reference numbers |
| the task list and the finish line | `asset.inventory <project>`, session method `scene.tag`, `asset.check ... --strict`, `look.leak`, `blender.preview ... --regions` | built, missing, unattached and rejected items; `done` and `blocking`; located gaps; one close-up sheet per region |
| lint with vanilla baseline | `asset.lint <target> --preset sa_plus --baseline vanilla` | rules the stock game also breaks are suppressed |
| fix shading of a finished DFF | `rw.patch <dff> --smooth-normals --recalc-bsphere` | welded smooth normals, frame-local spheres |
| TXD in vanilla formats | `texture.pack <folder> --asset-class vehicle --out <mod folder>` | DXT1/DXT3, one level, no DXT5 |
| photo-like texture from a flat fill | `texture.finish <png> --preset interior` | soft noise, painted occlusion, optional wear, DXT preview |

All of these are registered (CLI-only, `mcp=False`): run them with `satk_op`, find them with `satk_ops("<words>")`
or `satk help --find <words>`. When Blender cannot start, use the fallback named in workflow S25 and say so.
