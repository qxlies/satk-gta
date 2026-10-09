# SA style: vehicles (all types)

A San Andreas vehicle is a soft, rounded, simplified body composed as one whole: a welded shell with the opening
panels cut from it, lined arches, bumpers wrapping into the arches, lamps in recesses, a closed interior and
engine bay. Normals, no prelight; key colours the engine recolours over the shared `vehicle.txd`; a few small
own textures. How to build each part: `style_construction`. Numbers here are vanilla reference. Boats,
helicopters, planes, bicycles, quads, trailers, trains (frames, moving parts, items, regions): `style_kinds`.
Done = inventory complete + `asset.check --strict` `done: true` + every region reviewed (`done`).

## Detail: richer than vanilla, same style
Vanilla (2004) used about 2,200 triangles per car and seat wedges inside. Welcome now, in the same soft language
and joined to their parent: shaped seats (cushion, backrest, headrest), a dash with a gauge hood, steering
wheel, door cards, console, headliner; an engine bay (inner fender walls, block, air cleaner, radiator, hoses)
when the bonnet opens; floor, exhaust run, tank; lamp buckets, grille surround and bars, plate recess, mirrors
on stalks, handles, wipers, mouldings, rails on feet. Texture only: grille mesh, lamp internals, badges, tread,
stitching, instruments. The class checklist is the `coverage` section of `asset.check`; your task list is
the inventory (`done`), one item per part, left and right apart.

## Scale (anchor = wheel)
Wheel mesh diameter = IDE `wheel_scale` (0.70 m on most cars; the engine uses half as the physics radius).
The body follows the real vehicle's spec ratios at about 1.1-1.2x and a little wider (`style_references`).
Lineup peers: the same BODY TYPE (hatchback, wagon, upright SUV, pickup), not only the spawn class. Vanilla
sedans (24): L 5.50 / 5.82 / 6.15 m, H 1.48 / 1.53 / 1.70 m (p10 / p50 / p90), reference only.

## Frames and dummies
Root -> `chassis_dummy` -> `chassis`, `chassis_vlo`; `<part>_dummy` -> `<part>_ok` + `<part>_dam` (bonnet, boot,
bump_front/rear, door_lf/rf/lr/rr, windscreen); `wheel_lf/rf/lb/rb_dummy` (+ `wheel` under rf); `headlights`,
`taillights`, `ped_frontseat` on the RIGHT only (mirrored by the engine); `ped_arm` on the LEFT; hinges = dummy
origins (doors front edge, bonnet rear edge, boot front edge); keep every `ug_*` frame of the replaced model.
Never copy vanilla typos (`bonnet_ok `). Engine names per type: `re.nodes --type <type>`.
REFIT after the composition: `kit.template` copies the like model's positions. Move lamps onto the lens faces,
`exhaust` onto the pipe tip, `petrolcap` onto the surface, hinges onto the part edges; wheels centred in the
arches; seat, floor and steering wheel where the fixed seated pose expects them relative to `ped_frontseat`.
`kit.info` `frames: true` writes them to `frames_file`; `asset.check` `fit` rows report what is off.

## Bikes: steering and rider
- `forks_front` and `handlebars` rotate about the axis through their frame: build the steering column around it,
  hidden in the headset or shield; keep steering parts centred near the axis (else they swing off at lock).
- The front fender rides in `forks_front`; `mudguard` and `wheel_front` follow the suspension (put the axle link
  there).
- The rider is a fixed pose of the anim group (`bikev`, `bikes`, `bikeh`, `biked`) at `ped_frontseat`, no IK:
  keep seat top, footrest/floor and grip ends at the like model's contact points (faggio `bikev`: seat about
  0.32 m, floor about -0.29 m, grips about 0.47 m to each side).
- Check in game: rider seated, steering at full lock both ways, lights at night.

## Materials and keys (RGB, alpha ignored)
- Paint 1 (60,255,0) on `vehiclegrunge256`; also on the white of `vehiclegeneric256` for jambs and inner panels
  (no dirt). Paint 2 (255,0,175) two-tone and trim.
- Lamps FL (255,175,0), FR (0,255,200), RL (185,255,0), RR (255,60,0): ONLY on `vehiclelights128` (the engine
  turns them white and swaps `vehiclelightson128` when lit).
- Glass: `vehiclegeneric256` dark-glass area, alpha 128, single-sided. 242 is NOT glass (damage overlays).
- Sheen on paint, chrome, glass, lamps: env `xvehicleenv128` (reads UV2: give those geometries 2 UV sets) +
  reflection + specular `vehiclespecdot64`. None on interior and tyres.

## Paint and dirt
Paint is a clean key colour times `vehiclegrunge256`; the engine picks dirt level 0-14 per spawn (raw texture =
level 16, dirtier than the game shows). Up-facing faces map to the clean upper zone (vanilla about u 0.03-0.23),
sides map V to height, so grime only reaches the lower sides. Own textures carry NO body dirt, shading or panel
lines. Judge paint at dirt 0-2 (preview default 2), never on the raw key colour.

## Damage, LOD, collision
- `_dam` = `_ok` re-cut and visibly dented (`kit.damage`), not lighter; scratch (alpha 242) and shatter faces.
- `chassis_vlo`: simple body + cabin trapezoid + black glass top, 2 lamp quads, 4 diamond wheel quads (`kit.vlo`).
- COL3: spheres (surface CAR 63, piece byte = damage part), a few mesh faces, a closed shadow mesh; named
  `<model>_col` (`col.gen` or `kit.col`, `col.check`).
- Own TXD: DXT1/DXT3, ONE mip level, `<model>92interior128`, `<model>92wheel64`; never pack `vehicle.txd` copies.

Full guide: docs/agent/style/vehicles.md
