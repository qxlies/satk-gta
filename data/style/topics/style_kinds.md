# SA style per kind: structure, moving parts, items, regions (every kind, not only cars)

Same language for all (soft, rounded, joined; `style_construction`); per kind the frames, the moving parts
and where to look differ. Moving parts are frames turned about their FRAME ORIGIN: model each in its
frame's space, pivot at the origin, centred on its axis, clear of neighbours through the whole motion.
Frames: `kit.template --like <SID>`, `asset.anatomy <SID> --md`. Done per kind: `satk_help("done")`.
Regions below are the `blender.preview --regions` names (`data/kit/regions/<kind>.json`).

## Vehicles
- Car/van/SUV/pickup/truck/bus: cut panels, lined arches, lamp buckets, grille, mirrors, handles, wipers,
  exhaust, full interior, bay, underbody. Regions: front, rear, left, right, wheels, underside, interior,
  engine_bay, roof. Fit: `fit.wheel_arch|dummy|hinge`. Hero: bay parts, console, spare, rails, flaps.
- Motorbike/scooter: joined rounded lofts; `forks_front`/`handlebars` steer about their axis inside the
  headset; seat, footrest, grips at the like model's rider contacts (`style_vehicle`). Regions: front, rear,
  left, right, cockpit, engine, wheels. Hero: calipers, cables, radiator, rack, grab rail.
- Bicycle (bmx, `model:481`): tube sweeps meeting at closed joints; `forks_front` (+ `wheel_front`) and
  `handlebars` steer; `chainset` turns about its origin (bottom bracket) with `pedal_l/r` as children;
  `bargrip` = hands. Pedals clear frame and ground through a full turn. Regions: front, rear, left,
  right, cockpit, drivetrain, wheels. Hero: levers, cables, pegs, reflectors, chain guard.
- Quad (`model:471`): `suspension_lf/rf` and `rear_axle` follow the wheels; mtruck (`model:444`):
  `transmission_f/r` axles, long suspension. Regions as bike (quad: + underside) or car (mtruck).
- Boat (`model:452`): hull closed below the waterline (vee sections, chines); deck welded on the sheer line;
  cockpit cut in with a floor; `static_prop`/`moving_prop` (+`*2`) behind and below the hull; `boat_vlo`
  LOD; `boat_lights`. Regions: bow, stern, left, right, deck, cockpit, underside. Hero: platform,
  ladder, hatches, cleats, fenders, console gauges, radar arch.
- Helicopter (`model:487`): one shell (cabin + boom), canopy glass in the shell; `static_rotor` (blades) and
  `moving_rotor` (blur disc on alpha) at the hub, main about the vertical axis; `static_rotor2`/
  `moving_rotor2` tail about the side axis; skids on the wheel-dummy ground plane; `Omni*` lights. Regions:
  front, cockpit, left, right, tail, rotor, skids, underside. Hero: rotor head
  links, steps, antennas, landing light, rear seats. Paint on an own body page (`maverick92body128`).
- Plane (`model:593`, `model:519`): fuselage loft; wings = sweeps of a rounded aerofoil with taper and
  dihedral, filleted to the body; `aileron_l/r`, `elevator` (or `elevator_l/r`), `rudder` hinged at the
  origin on the leading edge, flush in a notch (no gap, no overlap); `static_prop`/`moving_prop`; gear on the
  wheel dummies; `Omni*` at tips and tail. Regions: nose, cockpit, left, right, wings, tail, gear,
  underside. Hero: exhaust stacks, pitot, gear doors, spats, steps, cabin.
- Trailer (`model:435`): rounded rail frame under the body; `hookup` where the like trailer couples;
  `misc_a` legs to the ground; `extra*` cargo on the deck. Regions: front, rear, left, right, wheels, underside.
- Train (`model:537`): body loft; `bogie_front/rear` carry the wheel dummies on the like model's gauge;
  couplers at the like height. Regions: front, rear, left, right, roof, bogies. Hero: rails, steps, hoses.

## World
- Prop/breakable/animated: closed primitives touching or pushed in; base a little below ground; back
  finished; breakables closed pieces; animated parts = frames named like the IFP bones. Regions: sides,
  top, base, details.
- Building + LOD (`model:3639`): one shell, corners closed; roof slab meeting the walls (overhang, fascia);
  ledges, cornices, entrance, roof equipment, gutters, signs ON the shell; foundation skirt; facades in tiling
  textures (hero: recessed windows with sills). LOD (`kit.lod`) keeps the silhouette, no hole on swap. Draw
  <= 299 with COL. Regions: facade_north|east|south|west, roof, entrance, lod.
- Interior (`model:14746`): shell faces inward, floor-wall-ceiling closed in every room (leak pass from
  inside), doorways with frames, entry with free floor, props on the floor; FLOORBOARD/CARPET collision.
  Regions: overview, walls, ceiling, floor (cameras inside every room, up to six, per storey).
- Pickup: centred on its origin (it spins), closed all round, saturated texture, one sphere. Regions:
  sides, top, details.

## Character and parts
- Weapon (`model:356`): side-profile build; origin and axis of the vanilla weapon of the slot (barrel along
  +X, hand at the vanilla grip); `gunflash` at the muzzle; parts pushed into the body. Regions: left, right,
  top, muzzle, grip. Hero: ejection port, bolt, rails, sling points. Melee and thrown (`weapon_melee`,
  `model:339`): along +Z, blade/head, guard, grip, pommel. Regions: sides, blade, tip, grip, ends.
- Ped skin (re-skin only: kit export cannot write a skin): keep the 32-bone skin, <= 4 weights, one material; paint regions soft and photo-like, carry
  garments across the UV borders (neck, wrists, waist, ankles: no seams). Regions: front, back, left,
  right, face, feet.
- Vehicle upgrade (`model:1039`): seated at its `ug_*` anchor on EVERY car of its `carmods.dat` list, no gap
  and no cut; painted parts carry the paint key on the car's body texture. Regions: sides, top, mount.
- Sets (station + canopy + interior + props): one project per model, one shared design note, each project
  done on its own, then the set reviewed together in context (`view.place`).

Full guide: docs/agent/style/kinds.md
