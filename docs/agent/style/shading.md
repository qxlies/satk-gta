# Shading: smooth like vanilla, hard only at seams

<!-- Model-facing, English only. Conventions: README.md "How to read the numbers". Measured on the clean 1.0 US
     copy (profile vanilla); the Blender facts were verified on Blender 5.1.1 headless with DragonFF. -->

The single biggest reason agent-built models look "blocky" or "sharp" is faceted shading. Vanilla dynamically lit
models (vehicles, peds, weapons, upgrades) are SOFT: their vertex normals bend about 13 deg away from the face
normals on average, and only about a fifth of the triangles render flat. A 1,400-triangle car body looks curved
because of these normals plus the environment sheen, not because of its polygons.

Vanilla bodies still have many split normals, but they sit on seams: where the material or the UV island changes
(glass/body, lamp/body, chrome or rubber trim, a panel cut with its jamb). Inside one material, folds stay smooth.

## The rule

- **Cars and every vehicle type:** weld the shell; smooth every edge below 30 deg; decide 30-60 deg by design
  (beltline, door and bonnet shut lines, wheel-arch lip, roof edge: hard; rounded body corners: smooth); hard
  above 60 deg unless the fold is a rounded corner; always hard at material and UV seams. Bumpers and tyres are
  fully smooth.
- **Peds:** fully smooth (one skinned mesh, one material).
- **Weapons:** firearms are harder-edged (receivers, slides); melee weapons and round items are smooth.
- **Map objects** (props, buildings, terrain, LODs): no normals at all. Their shape comes from the prelight; the
  rule becomes "bake soft occlusion into the prelight" (`world.md`). Interior props are the exception: 42 % carry
  normals.

## Bands

| Metric | Peer set (n) | p10 | p50 | p90 | sa_plus (proposal) | Note |
|---|---|---|---|---|---|---|
| `shade.normal_bend[car]` | cars, HD parts (144) | 7.3 | 10.6 | 15.4 | same band | area-weighted; premier 10.64 |
| `shade.normal_bend[vehicle]` | every vehicle of the `cars` section (204) | 9 | 13 | 20 | same band | bikes, boats, aircraft included |
| `shade.normal_bend[weapon]` | weapons (50) | 7.2 | 23 | 34 | same band | |
| `shade.normal_bend[ped]` | peds (265) | 26 | 29 | 35 | same band | |
| `shade.flat_share[car]` | cars, HD parts (144) | 0.092 | 0.144 | 0.333 | same band | premier 0.103 (HD) |
| `shade.flat_share[sedan]` | sedan, HD parts (24) | 0.084 | 0.103 | 0.143 | same band | |
| `shade.flat_share[wheel]` | car wheel meshes (143) | 0.0 | 0.015 | 0.228 | same band | tyres are smooth |
| `shade.flat_share[ped]` | peds (265) | 0.00 | 0.00 | 0.0054 | same band | fully smooth |
| `shade.flat_share[firearm]` | m4, micro_uzi, sniper, colt45 | 0.39 | - | 0.69 | same band | min/max |
| `shade.hard_at_seam[sedan]` | sedan (24) | 0.77 | 0.84 | 0.90 | same band | |
| `dff.verts_per_tri[chassis]` | car chassis geometries (144) | 1.18 | 1.33 | 1.48 | same band | premier 1.37 |

How often an edge is hard, by the angle between its two faces (all edges of the HD parts of 143 cars, 372,736
edges, pooled; this is the curve your model should follow):

| Metric | Peer set (n) | p10 | p50 | p90 | sa_plus (proposal) | Note |
|---|---|---|---|---|---|---|
| `shade.hard_by_dihedral[0-10]` | car HD edges (143 cars) | - | 0.02 | - | same | pooled |
| `shade.hard_by_dihedral[10-20]` | car HD edges (143 cars) | - | 0.10 | - | same | pooled |
| `shade.hard_by_dihedral[20-30]` | car HD edges (143 cars) | - | 0.19 | - | same | pooled |
| `shade.hard_by_dihedral[30-45]` | car HD edges (143 cars) | - | 0.32 | - | same | pooled |
| `shade.hard_by_dihedral[45-60]` | car HD edges (143 cars) | - | 0.47 | - | same | pooled |
| `shade.hard_by_dihedral[60-90]` | car HD edges (143 cars) | - | 0.65 | - | same | pooled |
| `shade.hard_by_dihedral[90-180]` | car HD edges (143 cars) | - | 0.81 | - | same | pooled |

Read the curve as: below 30 deg almost nothing is hard; even at 60-90 deg a third of the folds stay smooth; most
of the hard edges at small angles are seams. Two agent-built cars for comparison: one split 100 % of its edges
above 45 deg and kept 11 % of its hard edges on seams (it looked blocky); the other was over-split below 20 deg
and looked sharp.

## Blender 5.1 recipe (the order matters)

Run it as the session method `kit.shade` (`sa_shade`) or by hand with session methods, in this order:

1. **Weld** the shell (merge by distance, about 0.1 mm). Skip glass and double-sided parts. Parts that must stay
   separate (doors, bonnet, bumpers) are separate objects anyway.
2. **Smooth every face** (`use_smooth` on all polygons; Shade Smooth). Never leave flat faces.
3. **Mark sharp** edges: material borders, UV-island borders, then the designed creases by the dihedral rule
   above (Smooth by Angle modifier at 30 deg, or mark sharp by angle, then un-mark the rounded corners).
4. **Weighted Normal** modifier (Keep Sharp on, face influence) or custom normals LAST. Modifiers may stay live:
   the exporter writes the evaluated mesh.
5. **Export** with split normals (DragonFF `export_split_normals`), then **verify on the re-read DFF**:
   `shade.normal_bend` and `shade.flat_share` in the class band, `dff.verts_per_tri` not above the class p90.
   `kit.export` and `asset.check` do this for you.

Facts behind the order (Blender 5.1.1, headless, DragonFF; a 128-face test sphere):
- Custom normals on FLAT faces are kept, but each flat face becomes its own fan: the DFF gets one vertex per corner
  (480 vertices instead of 114).
- Toggling `use_smooth` after setting custom normals moves them by about 15 deg on average: smooth first.
- A Weighted Normal modifier on flat faces exports fully flat (bend 0.0 deg, 2.14 vertices per triangle).
- The Smooth by Angle modifier and `shade_smooth_by_angle` work headless under `--factory-startup`.
- DragonFF exports the evaluated modifier stack; Weighted Normal normals reach the DFF within 0.001.
- Background mode has no undo: the session checkpoints are `.blend` saves.

## Fixing a finished DFF without Blender

- `rw.patch <dff> --smooth-normals --angle 45 --weld 0.001 --split-at material,uv` welds and smooths by the rule.
- Never use `--recalc-normals` for this: it does not weld, so it makes faceting worse (one agent car went from
  0.81 to 0.71 deg bend, another from 2.42 to 1.70; vanilla premier is 10.64).

## Anti-patterns

- Flat shading everywhere, or auto-smooth with a 30 deg cut on a low-poly body: reads as a 1998 game.
- Smoothing groups per part that never weld (each slab its own island): looks like a voxel model.
- Hard edges inside one material at 20-45 deg folds: looks "sharp", the user's complaint about one agent car.
- Fully smooth bodies with no seam splits: reads as a modern render; glass and trim smear into the paint.
