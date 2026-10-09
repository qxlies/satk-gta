# Shading: seams hard, corners soft

<!-- Model-facing, English only. Conventions: README.md "How to read the numbers". Measured on the clean 1.0 US
     copy (profile vanilla); the Blender facts were verified on Blender 5.1.1 headless with DragonFF. -->

Vanilla dynamically lit models (vehicles, peds, weapons, upgrades) look SOFT: rounded forms whose normals blend
the corners, with crisp lines only where a material or a panel changes. Soft normals make a rounded form read
smoothly; they cannot round a box. Build the form round first (`construction.md`), then shade it by this rule.

## The rule: decide per line, not by one angle

- **Hard:** every material or UV seam (glass and body, lamp and body, chrome or rubber trim, a panel cut with its
  jamb) and the creases you can name: the beltline or side moulding, the two bonnet creases, shut lines, a
  bumper groove, the sharp edge of a firearm receiver.
- **Smooth:** everything else, including steep folds that are rounded corners: roof edge, fender tops, nose and
  tail corners, bumper ends, lamp buckets, mirror heads, seat cushions. A big turn built from two or three steps
  stays smooth across all of them.
- Bumpers and tyres are fully smooth. Peds are fully smooth (one skinned mesh, one material). Firearms are
  harder-edged on receivers and slides; melee weapons and round items are smooth.
- **Map objects** (props, buildings, terrain, LODs): no normals at all. Their shape comes from the prelight: bake
  soft occlusion into it (`world.md`). Interior props are the exception: 42 % carry normals.

Why not one angle: vanilla keeps a quarter to almost half of its 60-90 deg folds smooth (rounded corners) and
puts 80-93 % of its chassis hard edges on seams. Smoothing everything under 30 deg and splitting everything above
it turns the same rounded body into a bevelled box: the corners go hard and the chamfers between them read as
flat bands (`construction.md`, rules 7-8).

## Vanilla reference

| Metric | Peer set (n) | p10 | p50 | p90 | Note |
|---|---|---|---|---|---|
| `shade.normal_bend[car]` | cars, HD parts (144) | 7.3 | 10.6 | 15.4 | area-weighted; premier 10.64 |
| `shade.normal_bend[vehicle]` | every vehicle of the `cars` section (204) | 9 | 13 | 20 | bikes, boats, aircraft included |
| `shade.normal_bend[weapon]` | weapons (50) | 7.2 | 23 | 34 | |
| `shade.normal_bend[ped]` | peds (265) | 26 | 29 | 35 | |
| `shade.flat_share[car]` | cars, HD parts (144) | 0.092 | 0.144 | 0.333 | premier 0.103 (HD) |
| `shade.flat_share[sedan]` | sedan, HD parts (24) | 0.084 | 0.103 | 0.143 | |
| `shade.flat_share[wheel]` | car wheel meshes (143) | 0.0 | 0.015 | 0.228 | tyres are smooth |
| `shade.flat_share[ped]` | peds (265) | 0.00 | 0.00 | 0.0054 | fully smooth |
| `shade.flat_share[firearm]` | m4, micro_uzi, sniper, colt45 | 0.39 | - | 0.69 | min/max |
| `shade.hard_at_seam[sedan]` | sedan (24) | 0.77 | 0.84 | 0.90 | |
| `dff.verts_per_tri[chassis]` | car chassis geometries (144) | 1.18 | 1.33 | 1.48 | premier 1.37 |

How often an edge is hard, by the angle between its two faces (all edges of the HD parts of 143 cars, 372,736
edges, pooled): even the steepest folds are far from always hard, because rounded corners stay smooth.

| Metric | Peer set (n) | p10 | p50 | p90 | Note |
|---|---|---|---|---|---|
| `shade.hard_by_dihedral[0-10]` | car HD edges (143 cars) | - | 0.02 | - | pooled |
| `shade.hard_by_dihedral[10-20]` | car HD edges (143 cars) | - | 0.10 | - | pooled |
| `shade.hard_by_dihedral[20-30]` | car HD edges (143 cars) | - | 0.19 | - | pooled |
| `shade.hard_by_dihedral[30-45]` | car HD edges (143 cars) | - | 0.32 | - | pooled |
| `shade.hard_by_dihedral[45-60]` | car HD edges (143 cars) | - | 0.47 | - | pooled |
| `shade.hard_by_dihedral[60-90]` | car HD edges (143 cars) | - | 0.65 | - | pooled |
| `shade.hard_by_dihedral[90-180]` | car HD edges (143 cars) | - | 0.81 | - | pooled |

Most of the hard edges at small angles are seams. Two agent-built cars for comparison: one split every edge above
45 deg and kept 11 % of its hard edges on seams (it looked blocky); the other was over-split below 20 deg and
looked sharp. These numbers explain the rule; they are not values to reach.

## Blender 5.1 recipe (the order matters)

Run it as the session method `kit.shade` or by hand with session methods, in this order:

1. **Weld** the shell (merge by distance, about 0.1 mm). Skip glass and double-sided parts. Parts that must stay
   separate (doors, bonnet, bumpers) are separate objects anyway.
2. **Smooth every face** (`use_smooth` on all polygons). Shape lofts, sweeps and rounded primitives come
   smooth-shaded; lofts of raw points need `smooth: true` and lathes `shade.basic`. Keep them smooth from the
   first step, so every review sheet shows the real form.
3. **Mark sharp** the material borders, the UV-island borders and the named creases. `kit.shade` (`mode`
   seams, the default) makes hard: material borders, UV seams that fold more than `seam_angle` (vehicles 20
   deg), named creases (edges marked sharp with `mesh.mark`, Blender edge creases, the edge attribute
   `satk_crease`) and fold-backs above `hard` (vehicles 110 deg); edges in the attribute `satk_soft` stay smooth;
   bumpers and wheels keep only material borders. `mode: "angle"` is the old dihedral rule. Do not mark by one
   angle; if you start from an angle, un-mark every rounded corner afterwards.
4. **Weighted Normal** modifier (Keep Sharp on, face influence) or custom normals LAST. Modifiers may stay live:
   the exporter writes the evaluated mesh.
5. **Export** with split normals (DragonFF `export_split_normals`; `kit.export` does it) and look at the re-read
   model in clay: highlights roll around corners, lines appear only on seams and named creases.

Facts behind the order (Blender 5.1.1, headless, DragonFF; a 128-face test sphere):
- Custom normals on FLAT faces are kept, but each flat face becomes its own fan: the DFF gets one vertex per corner
  (480 vertices instead of 114).
- Toggling `use_smooth` after setting custom normals moves them by about 15 deg on average: smooth first.
- A Weighted Normal modifier on flat faces exports fully flat (bend 0.0 deg, 2.14 vertices per triangle).
- The Smooth by Angle modifier and `shade_smooth_by_angle` work headless under `--factory-startup`.
- DragonFF exports the evaluated modifier stack; Weighted Normal normals reach the DFF within 0.001.
- Background mode has no undo: the session checkpoints are `.blend` saves.

## Fixing a finished DFF without Blender

- `rw.patch <dff> --smooth-normals --angle 45 --weld 0.001 --split-at material,uv` welds and smooths, splitting
  at material and UV seams (a rescue for imported models; a model built in the session uses `kit.shade`).
- Never use `--recalc-normals` for this: it does not weld, so it makes faceting worse.

## Anti-patterns

- Flat shading anywhere on a dynamically lit model: reads as a 1998 game.
- One smoothing angle for everything (smooth below, hard above): rounded corners go hard, the model reads as a
  bevelled box.
- Smoothing groups per part that never weld (each piece its own island): looks like a voxel model.
- Hard edges inside one material at gentle folds: looks "sharp".
- Fully smooth bodies with no seam splits: glass and trim smear into the paint, doors look melted.
- Trying to fix a boxy form with normals: round the form instead.
