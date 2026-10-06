# SA style: shading (normals) of dynamically lit assets

Vanilla vehicles, peds, weapons and upgrades are SOFT. Faceted shading is the main reason agent models look
"blocky" or "sharp". Map objects carry no normals: bake soft occlusion into the prelight instead.

## Bands (p10 / p50 / p90, peer set)
- `shade.normal_bend` (area-weighted corner-normal bend, deg): cars HD (144) 7.3 / 10.6 / 15.4 (premier 10.64);
  all vehicles (204) 9 / 13 / 20; weapons (50) 7.2 / 23 / 34; peds (265) 26 / 29 / 35.
- `shade.flat_share` (triangles rendered flat): cars HD (144) 0.092 / 0.144 / 0.333; sedan (24) 0.084 /
  0.103 / 0.143; car wheels (143) 0.0 / 0.015 / 0.228; peds 0.00 / 0.00 / 0.005; firearms 0.39-0.69 (samples).
- `shade.hard_at_seam` (hard edges on a material/UV border): sedan (24) 0.77 / 0.84 / 0.90.
- `dff.verts_per_tri[chassis]` (144 cars): 1.18 / 1.33 / 1.48; above p90 = unwelded or over-split.
- `shade.hard_by_dihedral` (pooled, 372,736 HD edges of 143 cars), share of hard edges by fold angle:
  0-10 deg 0.02, 10-20 0.10, 20-30 0.19, 30-45 0.32, 45-60 0.47, 60-90 0.65, > 90 0.81.
  Agent car that looked blocky: 1.00 above 45 deg and only 11 % of hard edges on seams.

## The rule
- Cars and all vehicle types: weld the shell; smooth below 30 deg; decide 30-60 deg by design (beltline, shut
  lines, arch lip, roof edge hard; rounded body corners smooth); hard above 60 deg unless the fold is a rounded
  corner; ALWAYS hard at material and UV seams. Bumpers and tyres fully smooth.
- Peds fully smooth. Firearms harder-edged; melee and round items smooth.
- Map objects: no normals; shape comes from the prelight (`style_world`). Interior props: 42 % carry normals.

## Blender 5.1 recipe (order matters; session method `kit.shade` does it)
1. Weld the shell (merge by distance ~0.1 mm); skip glass and double-sided parts.
2. Smooth EVERY face (`use_smooth`); never leave flat faces.
3. Mark sharp: material borders, UV-island borders, then designed creases (Smooth by Angle 30 deg or mark sharp
   by angle, then un-mark rounded corners).
4. Weighted Normal modifier (Keep Sharp) or custom normals LAST; modifiers may stay live (export writes the
   evaluated mesh).
5. Export with split normals; verify on the RE-READ DFF: `shade.normal_bend`, `shade.flat_share` in band,
   `dff.verts_per_tri` <= class p90 (`kit.export` and `asset.check` do it).

Verified facts (Blender 5.1.1 headless + DragonFF, 128-face test sphere):
- Custom normals on flat faces are kept but each flat face becomes its own fan: 480 DFF vertices instead of 114.
- Toggling `use_smooth` after custom normals moves them by ~15 deg: smooth first.
- Weighted Normal on flat faces exports fully flat (bend 0.0, 2.14 verts/tri).
- Smooth by Angle modifier and `shade_smooth_by_angle` work headless (`--factory-startup`).
- No undo in background mode: the session checkpoints are `.blend` saves.

## Fixing a finished DFF
`rw.patch <dff> --smooth-normals --angle 45 --weld 0.001 --split-at material,uv`. Never `--recalc-normals` for
this: it does not weld and makes faceting worse (agent cars 0.81 -> 0.71 and 2.42 -> 1.70 deg).

## Anti-patterns
- Flat shading or per-part smoothing groups that never weld (voxel look).
- Hard edges inside one material at 20-45 deg folds ("sharp").
- Fully smooth body with no seam splits (glass and trim smear into the paint; reads modern).

Full guide: docs/agent/style/shading.md
