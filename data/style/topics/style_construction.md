# SA style: construction (how vanilla builds a model, and how to check yours)

Soft, rounded, simplified forms composed into ONE joined whole, with crisp lines only where a material or a
panel changes. Numbers in brackets are vanilla reference, never targets. Look at a vanilla peer and your model in
the same views: `blender.preview session:<name> --lineup class --passes game,clay,wire` plus a LOW
three-quarter camera (wheel height): it shows floating parts, see-through arches and steps at cuts.
`asset.check` `form` and `fit` rows are located defects to fix.

## Vehicles
1. One welded shell carries the body (largest welded piece 71-84 % of a vanilla chassis); loose items are only
   seats, engine, rails, grille teeth, wipers, exhaust. Build features from the shell's own faces.
2. Panels are cut from the shell (border within a few mm of it): loft `parts` or blank regions, then
   `kit.blank_split`. Never replace a cut panel with a primitive.
3. Lines run across the cuts: beltline, moulding, bonnet creases at one height over fender, doors, quarter.
4. Nothing is flat: roof crown 3-10 cm, bonnet 2-7 cm (often two creases), lower side a barrel bulging 3-9 cm.
5. The greenhouse leans in: side glass 16-27 deg from vertical; windscreen 25-35 deg from horizontal on cars,
   42-51 on upright SUVs (`tumble`).
6. Corners turn in 2-3 smooth steps; plan corners rounded, the nose a shallow arc. Never one vertex on a
   90-degree corner.
7. Hard edges live on seams (80-93 % of vanilla chassis hard edges) and on named creases.
8. Soft corners survive: a quarter to half of 60-90 deg folds stay smooth. One smoothing angle = bevelled box.
9. Arches are holes with a liner: 5-8 segments over the top, radius about 1.15-1.3x the wheel radius, a return
   face and a flat liner; never see-through.
10. SUV flares grow out of the fender: a raised welded band 5-8 cm wide, chamfered and smooth into the body
    (`mesh.flare`); never a separate horseshoe.
11. Bumpers wrap from arch to arch: 2-4 smooth profile segments, top on a shared line with the body
    (`mesh.sweep` + `mesh.attach`); plate and lamps set into them.
12. Doors are thick shells (6-13 cm) with inner trim, jambs, their window frame and glass (a flat pane flush or
    up to about 1 cm behind the frame). Pillars are shell or door-frame faces in dark trim, never added bars.
13. Lamps sit in recesses: a flat lens on the lamp key 3-8 cm behind its surround (flush on some trucks); the
    grille is a recessed opening with a surround, its mesh is texture.
14. Small details touch their parent (0-1 mm): handles, rails on feet, mirror stalks, seats, engine
    (`mesh.attach` snap). Mirror = head (18-32 cm) on a stalk touching the door.
15. Closed from every angle: interior, engine bay, floor, exhaust; vanilla keeps them simple, richer is welcome
    in the same soft language (shaped seats, gauge hood, door cards, radiator).
16. Chunky proportions: about 1.1-1.2x the real car, relatively wider; keep the donor's lines, not millimetres.
17. Clean key-colour paint: up faces in the clean zone of `vehiclegrunge256`, V follows height on the sides.
18. Small blurry photo textures (about 50 px/m on paint and trim); shared `vehicle.txd` for trim, glass, lamps.
19. Density where the outline turns: long strips across panels, vertices at corners, arches, lamps; no grid.
    Soft comes from normals (map models: prelight), not density. Rich detail is never counted; wasted density
    is: `form.dense_flat` = fine triangles (edges < 10 cm) on surface turning < 8 deg, >= 100 and a third of
    the model (vanilla: none, max 0.26). Defect on map models (placed many times), advice elsewhere. The
    first atelier bin: 1,774 of 4,062 wasted (subdivided barrel); vanilla bins: 16 segments, long strips.
20. Props: closed primitives that touch or push into each other (hydrant 18 pieces, chamfered caps), baked
    occlusion in a prelight darker at the base. Buildings: one main shell (about 70 % of the triangles), roof
    slab with overhang, small volumes on it, windows and bricks in tiling textures.

## Bikes and every other kind
Every body part is a closed rounded loft, joined to its neighbours. The steering axis (`forks_front`) runs
through the headset inside the body; the rider is a fixed pose: seat, footrest and grips at the like model's
contact points (`style_vehicle`). Moving parts of any kind (rotors, propellers, control surfaces, pedals,
bogies) turn about their frame origin; hulls and fuselages are one welded shell; bicycles are tube sweeps
with closed joints; buildings and interiors are closed shells (`style_kinds`).

## Agent anti-patterns -> fix
- Square, voxel look (cubes with one chamfer, flat sides) -> rounded sections, crown, smooth corner steps.
- Parts not joined (flares, bumpers, rails, pillars, fascia beside the body) -> shell, cuts, `mesh.attach`,
  `mesh.flare`; clean `form` rows.
- Black bars for pillars -> trim faces of the shell. Stadium or horseshoe arches -> lined polygonal arches.
- Too few details -> the detail pass and `coverage` rows; counts are information.
- Smooth like modern CG (subdivided, uniformly dense) -> fewer sections where it is flat, `form.dense_flat`.
- Parts placed wrong -> refit dummies; `fit` rows; the in-game check.
- Dirt everywhere -> clean paint, judge at dirt 0-2. Template look -> build from the description.

Full guide: docs/agent/style/construction.md
