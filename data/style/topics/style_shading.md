# SA style: shading (normals) of dynamically lit assets

Seams hard, corners soft. Vanilla vehicles, peds, weapons and upgrades are SOFT: rounded forms whose normals
blend the corners, crisp lines only where a material or a panel changes. Soft normals cannot round a box: build
the form round first (`style_construction`). Map objects carry no normals: bake soft occlusion into the prelight.

## The rule: decide per line, not by one angle
- HARD: every material or UV seam (glass/body, lamp/body, chrome or rubber trim, a panel cut with its jamb) and
  the creases you can name (beltline or moulding, two bonnet creases, shut lines, a bumper groove, a firearm
  receiver edge).
- SMOOTH: everything else, including steep folds that are rounded corners (roof edge, fender tops, nose and tail
  corners, bumper ends, lamp buckets, mirror heads, seat cushions). A big turn built from 2-3 steps stays smooth.
- Bumpers and tyres fully smooth. Peds fully smooth. Firearms harder on receivers and slides; melee and round
  items smooth. Map objects: no normals (`style_world`); interior props: 42 % carry normals.
- Why not one angle: vanilla keeps a quarter to almost half of its 60-90 deg folds smooth and puts 80-93 % of
  its chassis hard edges on seams. "Smooth below 30, hard above" turns a rounded body into a bevelled box.

## Vanilla reference (p10 / p50 / p90, peer set)
- `shade.normal_bend` (area-weighted corner-normal bend, deg): cars HD (144) 7.3 / 10.6 / 15.4; all vehicles
  (204) 9 / 13 / 20; weapons (50) 7.2 / 23 / 34; peds (265) 26 / 29 / 35.
- `shade.flat_share`: cars HD 0.092 / 0.144 / 0.333; car wheels 0.0 / 0.015 / 0.228; peds 0.00; firearms
  0.39-0.69 (samples).
- `shade.hard_at_seam`: sedan (24) 0.77 / 0.84 / 0.90.
- `shade.hard_by_dihedral` (pooled, 372,736 HD edges of 143 cars), share of hard edges by fold angle:
  0-10 deg 0.02, 10-20 0.10, 20-30 0.19, 30-45 0.32, 45-60 0.47, 60-90 0.65, > 90 0.81.
These explain the rule; they are not values to reach.

## Blender 5.1 recipe (order matters; session method `kit.shade` does it)
1. Weld the shell (merge by distance ~0.1 mm); skip glass and double-sided parts.
2. Smooth EVERY face (`use_smooth`); shape lofts, sweeps and rounded primitives come smooth (point lofts:
   `smooth: true`; lathes: `shade.basic`): keep them so from the first step.
3. Mark sharp: material borders, UV-island borders, the named creases. `kit.shade` `mode` seams (default): hard on
   material borders, UV seams folding over `seam_angle` (20 deg), edges marked sharp (`mesh.mark`), creases or
   `satk_crease`, fold-backs over `hard` (110 deg); `satk_soft` stays smooth. If you start from an angle,
   un-mark every rounded corner.
4. Weighted Normal modifier (Keep Sharp) or custom normals LAST; modifiers may stay live (export writes the
   evaluated mesh).
5. Export with split normals (`kit.export`) and look at the re-read model in clay: highlights roll around the
   corners, lines only on seams and named creases.

Verified facts (Blender 5.1.1 headless + DragonFF, 128-face test sphere):
- Custom normals on flat faces are kept but each flat face becomes its own fan: 480 DFF vertices instead of 114.
- Toggling `use_smooth` after custom normals moves them by ~15 deg: smooth first.
- Weighted Normal on flat faces exports fully flat (bend 0.0, 2.14 verts/tri).
- No undo in background mode: the session checkpoints are `.blend` saves.

## Fixing a finished DFF
`rw.patch <dff> --smooth-normals --angle 45 --weld 0.001 --split-at material,uv` (a rescue for imported models).
Never `--recalc-normals` for this: it does not weld and makes faceting worse.

## Anti-patterns
- Flat shading anywhere on a dynamically lit model (a 1998 look).
- One smoothing angle for everything: rounded corners go hard, a bevelled-box look.
- Per-part smoothing groups that never weld (voxel look); hard edges inside one material at gentle folds.
- Fully smooth body with no seam splits (glass and trim smear into the paint).
- Fixing a boxy form with normals: round the form instead.

Full guide: docs/agent/style/shading.md
