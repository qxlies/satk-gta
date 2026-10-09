# Building every kind: worked guidance beyond the car

<!-- Model-facing, English only. Worked construction guidance for the kinds that are not a road car: helicopter,
     plane, boat, bicycle, motorbike, quad, monster truck, trailer, train, building with LOD, interior, weapon,
     ped skin, props, pickups, vehicle upgrades and sets of models. Frame names are the vanilla ones of the like
     model (`kit.template --like <SID>` copies them; `asset.anatomy <SID> --md` lists them with positions); the
     examples come from the clean 1.0 US copy. What each kind must contain to be done: done.md section 9. -->

Every kind is built the same way: a design description and an inventory (G0), rounded main forms at scale
(G1), one joined whole with every functional frame refitted (G2), the detail pass from the inventory (G3), the
surface (G4), the finish parts and the strict check (G5). The SA language is the same for all of them (soft,
rounded, simplified, joined; small soft textures; `construction.md`). What differs per kind is the structure
the engine expects, the parts that move, and where a reviewer has to look. Read the section of your kind, then
its definition of done (`done.md`).

**Moving parts are frames.** Rotors, propellers, control surfaces, forks, handlebars, pedals, suspension arms,
bogies and doors are separate atomics that the engine turns about their FRAME ORIGIN. Model each one in its
frame's space with the pivot exactly at the origin, centred on its axis, and check that it clears its
neighbours through its whole motion. A part whose pivot is off swings off the body in the game.

## Helicopter (heli; like `model:487` maverick)

- **Structure.** `chassis_dummy` -> `chassis`, `chassis_vlo`, `static_rotor` and `moving_rotor` at the main rotor
  hub (maverick: on the mast, 2.55 m above the model origin), `static_rotor2` and `moving_rotor2` at the tail
  rotor hub, doors (`door_*_dummy` -> `door_*_ok`), `wheel_*_dummy` frames that mark the ground contact of skids
  or wheels, `ped_frontseat` (right side, mirrored by the engine), light frames `Omni01`, `Omni02` with DFF 2D
  effects.
- **Rotors.** The engine shows the static rotor while the rotor turns slowly and swaps in the moving one at
  speed: the static rotor is the real blades and hub, the moving rotor a flat blurred disc on an alpha texture
  (vanilla: `cargobobrotorblack128`). The main rotor turns about the frame's vertical axis, the tail rotor about
  its side axis: centre both on their frame origins, blades level, tips clear of the boom, the fin and the
  ground.
- **Form.** One fuselage shell from a loft along the length: rounded cabin sections (`exp` about 2.5-3, a tall
  `crown`), tapering through the transition into small round boom sections; the canopy is part of the shell with
  glass faces, not a bubble stuck on. Fin and stabiliser as `mesh.sweep` of a rounded thin profile, welded or
  snapped to the boom; the engine cowling a rounded loft on the roof; skids as `mesh.sweep` tubes with cross
  tubes that touch the belly; the mast a lathe.
- **Fit.** Skids or wheels on the ground plane of the wheel dummies; doors hinged on their front edge; the pilot
  seat and the instrument panel where the seated pose at `ped_frontseat` expects them.
- **Detail pass.** Seats, instrument panel with its hood, cyclic and collective sticks, pedals, intakes and
  exhaust outlets, steps on the skids, antennas, landing light, rotor head (hub, pitch links), tail rotor
  guard, door handles, the inside of the doors.
- **Surface.** Vanilla helicopters paint the body with the paint keys on an own body page
  (`maverick92body128`) plus `vehiclegeneric256` for trim and glass (alpha 128); lights on their frames.
- **Mistakes.** A rotor offset from its frame origin (it wobbles), a moving rotor without alpha (a solid disc), a
  canopy with a gap to its frame, skids floating above or sunk under the ground, a tail boom welded with a crack.

## Plane (plane; like `model:593` dodo, `model:519` shamal)

- **Structure.** `chassis`, `chassis_vlo`; control surfaces as their own frames: `aileron_l` and `aileron_r` (turn
  about the side axis), `elevator` (side axis; shamal `elevator_l`/`elevator_r`), `rudder` (vertical axis;
  dodo puts it under `rudder_dummy`); `static_prop` and `moving_prop` at the propeller hub (turn about the length
  axis); `wheel_*_dummy` for the gear (dodo: both front dummies on the centre line for one nose wheel, the
  `wheel` mesh under `wheel_rf_dummy`); `gear_l`/`gear_r` on planes that retract the gear; effect points
  `wingtip_pos`, `aileron_pos`; navigation lights `Omni01`-`Omni03` (wing tips and tail) with 2D effects;
  `windscreen_dummy`, doors, `ped_frontseat`, `engine`.
- **Control surfaces.** Each one is modelled in its frame's space with the hinge line through the origin along
  its leading edge, and sits in a matching notch of the fixed wing or tail: at neutral it is flush, with no
  see-through gap and no overlap (overlapping faces z-fight). Rounded leading edge, thin trailing edge.
- **Form.** Fuselage: a loft along the length (round to oval sections, a tapering tail cone, the cockpit glass
  in the shell). Wings: `mesh.sweep` of a rounded aerofoil profile along the span with `scale` for taper and a
  path that carries the dihedral; the wing root meets the fuselage with a fillet (`mesh.attach` `bridge` or
  `weld`), never a plate pushed into the side. Engines: cowling and nacelles as lofts or lathes; struts as sweeps
  that touch both ends.
- **Fit.** The propeller centred on its frame origin, clear of the ground on the parked stance; gear wheels on
  the ground; doors on their hinge edge; lights at the wing tips and the tail.
- **Detail pass.** Cockpit (seats, panel, yokes), exhaust stacks, intakes, pitot, antennas, gear struts with
  oleo legs, wheel spats or gear doors, steps, flap tracks, registration and markings in the texture.
- **Surface.** Vanilla planes use an own body page (`dodo92body8bit256`) with `vehiclegeneric256` for trim and
  glass; propeller blur on an alpha texture.
- **Mistakes.** Ailerons as separate plates with a gap or buried in the wing, a hinge away from the frame origin,
  wings with a zero-thickness trailing edge (flipped and degenerate faces), an open wing root, a propeller off
  its axis.

## Boat (boat; like `model:452` speeder, `model:446` squalo)

- **Structure.** `chassis` (the speeder's sits at y 1.5, z 1.17: keep the like model's frame), `boat_vlo` (the
  LOD), `boat_lights`, `ped_frontseat` (the driver), `exhaust`, `extra`, propellers `static_prop`/`moving_prop`
  (and `*_prop2` for the second engine) at the stern below the hull; no wheels.
- **Form.** The hull is one closed shell below the waterline: a loft along the length whose sections change from
  a deep vee at the bow to a flatter vee at the transom (point sections or `exp_bottom` well below 2 for the vee,
  rounded chines), flare at the bow, a near-flat transom. The deck is the top of the same shell or a second loft
  welded along the sheer line; the cockpit is cut down into the deck (`mesh.inset` with depth, then a floor) so
  its sides show their thickness. Windscreen frame and rails as sweeps; rails stand on stanchions that touch the
  deck.
- **Fit.** Propellers on their frame origins behind and below the hull, with a shaft and a rudder that touch it;
  the driver seat and the wheel where the seated pose at `ped_frontseat` expects them; lamps on `boat_lights`.
- **Detail pass.** Seats and cushions, console with wheel, throttle and gauges, engine cover or outboard,
  cleats, fenders, swim platform and ladder, hatches, anchor, antenna or radar arch, navigation lights, cabin
  interior on a cabin cruiser.
- **Surface.** Paint keys on `vehiclegrunge256` like a car (speeder), trim and glass on `vehiclegeneric256`, an
  own interior page.
- **Mistakes.** A hull open underneath (the camera sees it from the water and from below), a deck floating over
  the hull, a cockpit without a floor, propellers cutting through the hull, glass z-fighting with its frame.

## Bicycle (bmx; like `model:481` bmx, `model:509` bike, `model:510` mtbike)

- **Structure.** `chassis_dummy` -> `chassis` (the frame), `forks_front` (steers; `wheel_front` is its child),
  `handlebars` (steers about the same axis), `wheel_rear`, `chainset` (turns with the pedalling; `pedal_l` and
  `pedal_r` are its children at the crank ends), `bargrip` (where the hands hold), `ped_frontseat`,
  `headlights`/`taillights` dummies; no damage parts and no doors.
- **Form.** Tubes as `mesh.sweep` with a round profile along their centre lines: head tube, top tube, down tube,
  seat tube, chain stays, seat stays. Joints are where the work is: run each tube a little into its neighbour,
  or weld the ends, so no joint shows a gap; add gussets at the head tube. Fork legs and the handlebar are sweeps;
  the seat is a small rounded loft on a lathe seat post; the chainring a lathe disc (teeth in the texture);
  cranks rounded boxes; the chain a flat loop sweep around the chainring and the rear sprocket.
- **Wheels.** Tyre and rim as lathes, a hub, spokes as thin sweeps or a spoke texture with alpha on a disc
  (vanilla `bmxwheel`); centred on `wheel_front` and `wheel_rear`.
- **Fit.** The steering axis through the head tube (`forks_front` origin); the chainset centred on its frame
  origin at the bottom bracket; the pedals clear the frame and the ground through a whole turn; seat, grips and
  pedals at the like model's contact points (the rider pose is fixed).
- **Detail pass.** Grips with flanges, brake levers and cables (sweeps), brakes, pegs, reflectors, valve stems,
  seat clamp, kickstand, chain guard, decals in the texture.
- **Mistakes.** Tubes that stop short of each other, a fork outside the steering axis, pedals that cut the frame
  when they turn, a chain floating beside the chainring, wheels off-centre in the dropouts.

## Motorbike, scooter, quad, monster truck

- **Motorbike and scooter (bike):** body parts as joined rounded lofts; steering (`forks_front`, `handlebars`)
  and the rider's contact points are in `vehicles.md` "Bikes: steering and rider"; `mudguard` and `wheel_front`
  follow the front suspension; lamps and exhaust dummies refitted. Close-ups of the cockpit from the rider and of
  the engine side.
- **Quad (`model:471`):** `handlebars` steer; `suspension_lf`/`suspension_rf` arms follow the front wheels and
  `rear_axle` the rear: model each about its frame origin so it never cuts the body as the wheels travel;
  `ped_frontseat` and `ped_backseat` on the seat.
- **Monster truck (mtruck; `model:444`):** a car body high on long-travel suspension, wheel scale 1.5;
  `transmission_f`/`transmission_r` axles follow the wheels; build the frame, shocks and the underside: on this
  kind the underside is a front view.

## Trailer (trailer; like `model:435` artict1, `model:450` artict2)

- **Structure.** `chassis_dummy` -> `chassis`, `chassis_vlo`, `hookup` (the coupling point at the front),
  `misc_a` (the landing legs), `extra1`-`extra6` (cargo or body variants), wheel dummies with the `wheel` mesh.
- **Form.** A ladder frame of rounded rails (sweeps) under a box, tank or deck body (lofts or rounded boxes
  with crowned roofs and rounded corners); the box sits on the frame without a gap.
- **Fit.** `hookup` where the vanilla trailer of the same tractor has it, so the coupling meets the tractor; the
  landing legs reach the ground; the wheels on the ground and inside the mudguards.
- **Detail pass.** Rear doors with hinges and locking bars, under-run bar, lamps and reflectors, mud flaps,
  side guards, lashing points, toolbox, spare wheel, air and electric lines, ladder; logos in the texture.

## Train (train; like `model:537` freight, `model:538` streak)

- **Structure.** `chassis_dummy` -> `chassis`, `chassis_vlo`, `bogie_front` and `bogie_rear` with the wheel
  dummies as their children (freight: three axles per bogie, `wheel_lf1_dummy` ... `wheel_rb3_dummy`; the bogie
  mesh under `bogie_front`), `headlights`, `taillights`, `exhaust`, cab doors.
- **Form.** A long body shell: a loft with rounded roof sections and soft corners; the cab windows inset into
  the shell; underframe equipment as rounded boxes that touch the frame; bogies as small kits of closed pieces.
- **Fit.** The wheel dummies on the like model's rail gauge (freight: 0.83 m each side of the centre line);
  bogies centred under their pivots; the body clear of the bogies when they turn; couplers and buffers at the
  like model's height.
- **Detail pass.** Couplers, buffers, handrails on feet, steps, horns, fans or pantograph, walkways, ladders,
  brake hoses, number boards; cab controls and seats seen through the windows.

## Building with LOD (building; like `model:3639` GlenPHouse01_LAx)

- **Structure.** One geometry, one atomic, prelit (no normals); a separate LOD model (`kit.lod`: name `lod` + the
  HD name from its fourth character, drawn to about 800 m, no collision, its own IDE line); collision (mesh or
  boxes, `col.gen`); draw distance at most 299 with collision.
- **Form.** One main shell from the footprint (extruded walls, or `kit.blank --kind building_box`), every wall
  corner closed; setbacks and wings as volumes welded or snapped onto it; a roof slab with overhang and fascia
  (or a parapet) that meets the walls; ledges and cornices as sweeps along the wall tops; a foundation skirt that
  reaches below the ground so no slope shows a gap.
- **Facades.** Windows, doors, bricks and railings live in tiling textures (one bay, one storey per tile;
  `uv.unwrap` planar or cube with `size` = metres per repeat). Hero: shallow window recesses (`mesh.inset` with
  depth) with frames and sills that touch the wall, a door recess with steps, awnings, balconies on brackets.
- **Detail pass.** Entrance (canopy or porch, steps, door frame, lamps as 2D effects), roof equipment (vents,
  air conditioners, tanks, access hatch), gutters and downpipes, signs, fire escapes, chimneys, a finished back
  and service side.
- **Surface and light.** 4-7 materials of the district; a dark grey day prelight with occlusion (dark at the base
  and under the eaves) and a warm, darker night set with lit windows (`kit.bake`, `kit.export` or
  `blender.game_ready --asset-class building`); night window planes as time objects when wanted.
- **LOD.** The same silhouette with the massing simplified, a baked 32-64 px texture in a shared LOD TXD; review
  it next to the HD at distance: no hole and no change of outline when it swaps.
- **Mistakes.** A roof slab hovering over the walls, open wall corners, window sills floating off the wall, facade
  overlays z-fighting with the wall, a white prelight, a LOD with a different outline.

## Interior (interior_shell, interior_prop; like `model:14746` rylounge)

- **Structure.** The shell is one prelit geometry whose faces look inwards (floor, walls, ceiling) and lives in
  an interior area away from the street, entered through an entry marker; furniture and fittings are separate
  interior props (normals allowed: 42 % of vanilla interior props carry them).
- **Form.** Rooms from the floor plan: extrude the walls up to the ceiling, close every corner and every
  wall-to-ceiling and wall-to-floor line; doorways as cut openings with frames; windows with blinds or an outside
  view texture. Skirting and ceiling trim as sweeps along the walls.
- **Fit.** The entry point with free floor around it; doorways at ped height; furniture standing on the floor and
  against the walls (snap), walk paths free of furniture collision.
- **Detail pass.** Per room: the furniture set of its use (kitchen, bathroom, office, lounge), light fittings,
  wall decor, radiators, switches; light and occlusion baked into the prelight.
- **Collision.** A closed mesh with FLOORBOARD or CARPET floors; no shadow meshes.
- **Mistakes.** A wall that stops short of the ceiling (the void shows: run the leak pass from inside), a doorway
  into nothing, furniture floating above the floor or sunk into it, rooms lit white.

## Weapon (weapon; like `model:356` m4, `model:346` colt45)

- **Structure.** One atomic named like the model plus the `gunflash` atomic at the muzzle (crossed planes on a
  32 px alpha muzzle texture). Keep the origin and the axis of the vanilla weapon of the slot (`kit.template
  --like`): the barrel points along the same axis (+X in vanilla: the m4 flash sits at x 0.76) and the hand holds
  the weapon where the vanilla one is held.
- **Form.** From the side profile: the receiver as an extruded outline with rounded edges (or a loft of
  rounded sections), the barrel a lathe or a sweep, the grip and the stock rounded lofts where the hand goes;
  magazine, sights, scope and mounts as closed pieces that push into the body. Firearms keep harder edges on
  receivers and slides; melee weapons are smooth.
- **Detail pass.** Trigger and guard (a sweep), sights or scope on mounts, ejection port, bolt or charging
  handle, rails, sling points, safety, muzzle device; screws and markings in the texture.
- **Surface.** One small photo-like texture unwrapped onto both sides (64-128 px), DXT1, one mip level; a HUD
  icon texture.
- **Mistakes.** A box stretched into a gun, a scope floating above its mounts, the flash away from the muzzle,
  the wrong origin (the gun sits beside the hand), flipped faces on thin parts.
- **Melee and thrown weapons** (kind `weapon_melee`; like `model:339` katana): vanilla models them along +Z, the
  blade or head up and the grip down, with no `gunflash`. Items: blade or head (lofted or lathed, a bevelled edge),
  grip (a soft section with a taper), guard or collar, pommel or end cap, the photo texture; edge bevel and grip
  wrap at standard. Regions: `sides`, `blade`, `tip`, `grip`, `ends`.

## Ped skin (ped; like `model:7` male01)

- **Structure.** The vanilla 32-bone skin, one material, one texture: a re-skin (a new atlas on the vanilla
  mesh). `kit.export` refuses peds (it cannot write the Skin plugin; an unskinned ped crashes the game and
  `asset.check` reports `ped.skin` as an error). The inventory items are regions of the atlas, proven by the
  texture on the faces that show them (import the like ped with the new TXD, tag those faces).
- **Work.** Paint the atlas region by region (face, hair, torso, arms and hands, legs, shoes) soft and
  photo-like, with folds, seams and light painted in; carry each garment across the UV borders so the neck,
  wrists, waist and ankles show no seam; keep the skin tone the same on face, neck and hands.
- **Review.** Front, back and both sides in the bind pose and one walking frame; close-ups of the face and the
  hands; the seams at every UV border.

## Props, breakables and animated objects (prop, breakable, animated_object)

- **Form.** A kit of closed primitives (`cylinder` with explicit segments, `rounded_box`, `capsule`, lathes,
  sweeps) that touch or push into each other; chamfered caps and vertical edges; a lip on boxes; the base a
  little below the ground; the back finished like the front.
- **Breakables** (an `object.dat` entry): every piece closed, so nothing is open when the object falls or
  breaks; collision primitives that match the pieces.
- **Animated objects** (an IDE anim line and an IFP): every moving part its own frame, named like the
  animation's bones, with its pivot at the frame origin; run the animation in the preview and check that no part
  cuts another.
- **Surface.** 1-2 materials, one 64-128 px soft texture, prelight darker at the base (`kit.bake`,
  `kit.export`).

## Pickup and vehicle upgrade

- **Pickup** (like `model:1240` health): a small icon object (0.2-1 m) centred on its origin, because the game
  spins it; closed from every angle; one saturated 32-64 px texture; prelit; one collision sphere.
- **Vehicle upgrade** (like `model:1039` wg_l_c_l): lit like the car (normals, no prelight), 1-2 materials; body
  coloured parts carry the paint key on the car's body texture (vanilla side skirts use the paint key on
  `remapelegybody128`). Model it seated on the car at its `ug_*` anchor; check it on every car of its
  `carmods.dat` list: no gap along the contact line, no cut into the body.

## Sets of models

A gas station, a house with its interior and garden props, a car with its own upgrades: one asset project per
exported model (`asset.init` each), one shared design note that lists the projects and how they meet (the
canopy on the forecourt, the interior's entry behind the front door, the upgrade on the car's anchor). Each
project has its own inventory and must reach done on its own; then review the set together in its map context
(`view.place`, workflow S29), where the meeting lines between the models are checked like parts of one model.

Back to the guides: `README.md`; the definition of done: `done.md`.
