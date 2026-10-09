# Modelling in the live Blender session: form, composition, detail

<!-- Model-facing, English only. Conventions: README.md "How to read the numbers". Session and kit method names
     follow the authoring packages; `blender.methods --query <name>` gives the exact parameters of the running
     version (all methods: studio-methods.md). -->

Model IN Blender, step by step, and look after every few steps. Build the form from authored rounded sections
and profiles (a handful of numbers per station), then join the parts into one whole, then add detail, then the
surface. Never build meshes vertex by vertex (typed vertex lists, scripts that compute every vertex, a private
mesh library): agents who did that wrote thousands of lines, waited minutes per cycle and still got blocky shapes.
A script that only writes section shapes and method calls for a batch is fine.

## The session loop

1. `blender.session start --name <asset>` (isolated Blender profile, headless; `--gui` opens a window with the
   same profile for the user to watch). `blender.session status|list|stop`.
2. `blender.methods` lists the methods (generic `scene.*`, `mesh.*`, `modifier.*`, `material.*`, `uv.*`,
   `camera.*`, `ref.*`, `io.*`, `shade.basic`, `python`, plus the kit methods `kit.*` and `look.*`).
3. `blender.call <method> --params {...}` (or `--params-file <json>`) runs ONE step, or a list of steps
   (`batch`) with one snapshot and, for two or more mutating steps, one checkpoint (`--no-checkpoint` skips it).
   A step has a 90 s budget: `TIMEOUT` = split it, `BUSY` = Blender is stuck inside its own code
   (`blender.session stop`, then start again from the checkpoint). The reply is small: `{ok, method, changed,
   stats, snapshot?, checkpoint, ms}`. `stats` holds plain counts and dimensions (information) and a `form` block
   with real defects of the changed objects (floating pieces with their gap and centre, pieces buried in others,
   loose share of a body, hard corners off any seam, see-through openings). Fix the defects; never chase a count.
4. Snapshots are opt-in: a 512 px JPEG (Workbench clay + wire by default, `look=game` for the SA look) from a
   view (3q, front, rear, left, right, top), a camera of the scene (`camera.add` with `location` and `target`,
   e.g. a low three-quarter camera at wheel height), a 2x2 `sheet`, a reference overlay or the vanilla ghost.
5. Checkpoints are `.blend` saves (background Blender has no undo): `blender.session restore --ref <step|tag|last>`;
   gate tags are kept when old checkpoints are pruned. The journal replays the build (`blender.session replay`).
6. `python` is the last resort and read-only by preference (dumps of positions, a selection to target); its
   reply is capped (write longer results to a file with `out`). Code and hash are journaled.
7. Tag what you build: `scene.tag` {`objects` | `object` + `select`, `item`} in the same batch that creates a
   piece writes its inventory id (`satk_item`, or the face attribute `satk_item_idx` when several items share an
   object); `asset.inventory <project> --session <name>` then reports every item as built, missing, unattached
   or rejected (`done.md`); `kit.export` carries the tags beside the DFF (`<stem>.inventory.json`).

Request at most one snapshot per few steps and one sheet per review cycle (SKILL.md image rules).

## The order: form, compose, detail, surface

| Gate | Work | Passes when |
|---|---|---|
| G1 form | the main volumes from rounded sections and sweeps, wheels or the ped at scale, smooth shading from the start | the lineup and the clay views read as the object, soft and at the right size; the G1 items built |
| G2 compose | one welded shell, panels cut from it, flares and arches, bumpers, pillars and frames from the shell, details touching, dummies and moving parts refitted | the `form` and `fit` rows are clean, the low three-quarter view shows one joined whole, the leak pass finds no gap, the G2 items built, every region sheet read |
| G3 detail | every G3 item of the inventory, built in the same soft language | no G3 item missing or unattached; the region sheets show each item finished |
| G4 surface | UVs on the shared atlases, materials and keys, own soft textures, shading by seams | the game look at low dirt levels; textures at native size; region sheets without seams, stretch or stripes |

G0 (design and the inventory) comes before and G5 (finish: damage, LOD, collision, export, the polish loop
until `asset.check --strict` answers `done: true`, in-game) after; the workflow is S25. A gate is never passed
by the builder's own word: the sheets, the inventory report and the checks decide (`done.md`).

## Form: rounded sections and sweeps

- **`mesh.loft`** skins a body through cross-sections along an axis (`samples` = points around the loop,
  required). A section is a shape, not a point list: `shape` = {`w` half width, `h` height, `z` bottom, `exp`
  superellipse exponent (or `exp_top`/`exp_bottom`), `mid` belt height as a share of `h`, `crown` (m), `tumble`
  (top width / belt width: the glasshouse leans in), `shoulder` (m, a ledge under the glass), `flat_bottom`}.
  `interp` smooth (the default: a monotone spline through the shape numbers) or linear; a section's own
  `"interp": "linear"` keeps the gap after it planar (a windscreen, a flat door). `steps` sets the rings between
  stations, `half: true` builds x >= 0 with a MIRROR modifier, `parts` = [{`name`, `at`: [a, b], `angle`: [deg0,
  deg1]}] names regions as the face attribute `satk_part` that `kit.blank_split` cuts along (`angle` = which way
  the face looks across the loft: 0 up, 90 sideways, 180 down; later parts win; `model` names the kit model). The
  result is welded with cylindrical UVs; shape lofts are smooth-shaded, lofts of raw `points` stay flat unless you
  pass `smooth: true`.
- Put stations where the design changes: nose, front axle, cowl or windscreen base, B-pillar, C-pillar or hatch
  top, rear axle, tail. Typical car: one loft for the lower body (bumper to bumper, the top crowned as bonnet and
  deck) and one for the glasshouse (windscreen base to rear glass base, `tumble` for the lean), welded along the
  belt with `mesh.attach`; or one loft whose sections carry the shoulder.
- `exp` decides how square a section reads: about 2 is an ellipse, 3-4 a rounded box (the scooter body that
  looked right used 3-4); very high values give hard corners again. `crown` keeps tops and sides from going flat.
- Example (a half body; check the parameters with `blender.methods --query mesh.loft`; a whole car in tested
  batches closes this guide):

  ```json
  {"name": "body", "axis": "y", "samples": 28, "half": true, "steps": 3, "sections": [
    {"at": 2.55, "shape": {"w": 0.82, "h": 0.50, "z": 0.28, "exp": 2.6, "crown": 0.04}},
    {"at": 1.40, "shape": {"w": 1.04, "h": 0.66, "z": 0.22, "exp": 3.2, "crown": 0.07}},
    {"at": -1.60, "shape": {"w": 1.06, "h": 0.68, "z": 0.22, "exp": 3.2, "crown": 0.08}},
    {"at": -2.70, "shape": {"w": 0.86, "h": 0.55, "z": 0.30, "exp": 2.8, "crown": 0.05}}]}
  ```

- **`mesh.sweep`** runs a profile (points or a `shape`) along a path (points or a curve object) with `scale` and
  `twist` along it: bumpers along the plan curve of the nose (`half: true` starts on the mirror plane), sills,
  side mouldings, roof rails, window surrounds, pipes, handlebars, exhausts. Smooth-shaded.
- **`mesh.lathe`** spins a profile: wheels, hubs, lamp pods, posts, barrels, hydrants. It comes flat-shaded:
  `shade.basic` {`objects`, `mode`: `angle`, `angle`} or `kit.shade` later.
- **`mesh.primitive`** `rounded_box` {`size`, `radius`, `segments`} and `capsule` {`radius`, `depth`,
  `segments`, `rings`} give small smooth rounded volumes (seats, engine block, lamp housings, mirror heads,
  handles); `cylinder` with explicit `segments` for props.
- **Soft moves:** `mesh.transform` with `falloff` {`radius`, `curve` smooth|sphere|root|linear|sharp|constant,
  `connected`} moves a region smoothly instead of shifting a rigid slab; select by `near` {`point`, `radius`},
  edge `loop` {`point`, `ring`} and `grow`; `mesh.relax` evens out a lumpy region; `mesh.deform` (`cast`,
  `taper`, `bend`, `twist`, `lattice`) crowns, pillows and tapers whole parts.
- SUBSURF: one level on a curved patch (roof, bonnet, fender) is fine; apply it and keep long triangles on the
  flatter parts.

### The blank: an optional quick start

`kit.blank --kind automobile --body sedan|coupe|sports|suv|van|hatchback|wagon|suv_boxy|pickup` (and `bike`
with `--body scooter`, `boat`, `heli`, `plane`, `prop_box`, `prop_cyl`, `building_box`) builds a generic, smooth
half body with arches, part regions and UV seams from class dimensions (`--wheel-d` sets the wheel). It saves
time on a plain body of that type; it never decides the design. Reshape it with soft moves, re-section it, or
throw it away when the body type does not match (a scooter is not a sport bike). After G1, edit the mesh: do
not rebuild the blank from edited numbers again and again (the later steps are lost each time).

## Compose: join the parts into one whole

- Panels are cut from the shell along named regions: `kit.blank_split` (doors per side, bonnet, boot, bumpers,
  windscreen) with `fill: true` into the kit slots. Never replace a cut body panel with a new primitive.
- `mesh.attach` {`object`, `to`, `mode`, `select`, `max_dist`, `offset`, `rigid`, `direction`} joins a part to
  its parent: `snap` projects the contact vertices onto the parent's surface (handles, mirror stalks, rail feet,
  lamp pods; `rigid` first moves the whole part by the median gap), `weld` joins the part INTO `to` and merges
  its open border with the target's within `max_dist` (a glasshouse on the lower body, a sill), `bridge` joins it
  into `to` and fills the gap between the open borders. After `weld` or `bridge` the part object is gone.
- `mesh.flare` cuts a round wheel arch into the shell with a welded band and a liner: `arch` {`center`,
  `radius`} needs `segments` (over the top, vanilla 5-8), or `select` = the opening; `width` and `out` shape the
  band (`out` 0 = a plain sedan cut, 0.02-0.05 with `width` 0.05-0.08 = an SUV flare), `lip` = the depth of the
  return, `depth` = how far in the liner plate sits, `liner_material`; `both` cuts both sides of a full body.
- `kit.fill` {`slot`, `objects`, `append`, `weld`} moves pieces into a kit slot (bumper sweeps into
  `bump_front_ok`, mirrors appended to `chassis`); `weld` merges coincident boundary vertices where they meet.
- Refit every dummy after the form is settled: `kit.info` with `frames: true` writes every frame and dummy
  (world position, parent, part box) and the ghost's part sizes to `frames_file`; move lamps onto the lens
  faces, the exhaust onto the pipe tip, the petrol cap onto the surface, hinges onto the part edges
  (`scene.transform`), and keep the seat and the steering wheel where the seated pose expects them
  (`vehicles.md`).
- Moving parts of every kind (doors, rotors, propellers, ailerons, elevators, rudders, forks, handlebars,
  pedals and chainsets, suspension arms, bogies) are atomics that the engine turns about their frame origin:
  model each in its frame's space with the pivot at the origin, centred on its axis, and check it clears its
  neighbours through its whole motion (`kinds.md`).
- Check: the `form` rows in the step stats and `asset.check` (`form`, `fit`, `symmetry`); a low three-quarter
  clay view.

## The detail pass

After the composition holds, build every G3 item of the inventory in the same soft language, tag it, and check
it off (`asset.inventory`; the `coverage` rows of `asset.check` list the class items present and missing). More
than vanilla is welcome; a placeholder primitive is not a built item. Then a second pass: read the inventory,
the features list and the region sheets once more and add an item for anything still missing.

- **Car exterior:** lamp buckets (`mesh.inset` with depth, lens faces on the lamp key), grille surround and
  bars or teeth, bumper grooves, intakes and fog lamps, plate recess, mirrors (head on a stalk, `mesh.attach`),
  handles on the skin, wipers (cards), side mouldings, roof rails on feet, exhaust tip, mud flaps, badges and
  grille mesh in the texture.
- **Car interior:** shaped front seats (cushion, backrest, headrest, rounded edges: `rounded_box` or a loft),
  rear bench, dashboard with a gauge hood (part of the shell), steering wheel (lathe or card on
  `vehiclesteering128`), gauges on `vehicledash32`, door cards, centre console, headliner; dark interior atlas.
- **Engine bay and underbody** (when the bonnet opens or the view reaches them): inner fender walls, engine
  block, air cleaner, radiator, battery, hoses as sweeps; floor plate, exhaust run, tank, axles; spare tyre in an
  SUV or pickup bed.
- **Bike:** headset with speedo, levers and grips, horn grille, indicators, glovebox, floor runners, side-panel
  seams and vents, engine and CVT cover, shock, stand, rack on legs, plate and rear lamp housing, rimmed wheels.
- **Bicycle:** grips, stem, brake levers and cables, brakes, pegs, reflectors, valves, seat clamp, kickstand,
  chain and chain guard, spoke wheels.
- **Boat:** seats and cushions, console with wheel, throttle and gauges, engine cover or outboard, rails on
  stanchions, cleats, fenders, swim platform and ladder, hatches, navigation lights, antenna or radar arch.
- **Helicopter and plane:** cockpit (seats, panel with hood, sticks or yokes, pedals), rotor head or spinner,
  intakes and exhausts, antennas, pitot, landing light, steps, gear struts and doors or skids with cross tubes,
  navigation light housings at the frames.
- **Trailer and train:** couplers and buffers, steps and handrails on feet, doors with hinges and locking bars,
  under-run bar, underframe equipment, lamps and reflectors, roof equipment.
- **Weapon:** trigger and guard, sights or scope on mounts, ejection port, bolt or charging handle, rails, sling
  points, safety, muzzle device.
- **Prop and building:** caps, lips, ledges, cornices, posts and steps as closed pieces that touch; entrance,
  roof equipment, gutters, signs and (hero) recessed windows with sills on buildings; windows, doors and bricks
  in tiling textures.
- **Interior:** per room the furniture of its use, skirting and ceiling trim, door frames, light fittings, wall
  decor; every room closed.

## Round features: minimum segments (more is fine)

| Metric | Peer set (n) | p10 | p50 | p90 | Note |
|---|---|---|---|---|---|
| `geo.round_sides[wheel]` | road cars (129) | 12 | 12 | 16 | rim segments |
| `geo.round_sides[arch]` | 12 cars, counted on clay renders | 7 | - | 9 | segments over the half circle; min/max |
| `geo.round_sides[bumper profile]` | 12 cars, counted on clay renders | 2 | - | 4 | rounded nose and bumper profile; min/max |
| `geo.round_sides[round prop]` | hydrant, bin | 8 | 8 | 8 | cylinders with chamfered caps |
| `geo.round_sides[pole]` | street poles | 4 | - | 6 | min/max |

Vegetation: crossed alpha planes (a palm is 44 triangles) with 128-256 px DXT3 cut-out fronds.

## Rules for every kind

- **Template = structure:** `kit.template --like <SID> --tier <tier> --ghost` (or `--kind <kind>`) builds the
  collections, frame empties in vanilla order, dummies scaled to your dimensions (wheels keep `wheel_scale`),
  empty part slots, material presets, a collision skeleton and a vanilla ghost tagged `satk_ghost` that is never
  exported. It carries names and numbers only, no vertices and no shape; its dummy positions are a starting
  point to refit.
- **Scale first:** the anchor (wheel diameter for vehicles, the 1.84 m ped for everything else) and the spec
  ratios before any detail; check the lineup at G1.
- **Segments where the form needs them.** Place them when you create a loft, sweep or lathe. Do not subdivide and
  then decimate. Clean-up: merge by distance, limited (planar) dissolve on truly flat areas only.
- **Mirror** symmetric models (`half` lofts or a MIRROR modifier on X, clipping on) and apply it before cutting
  left/right parts.
- **Shade with `kit.shade`** at G4, when materials and UV seams exist (`shading.md`): `mode` seams (default; hard
  on material borders, UV seams folding more than `seam_angle`, named creases and fold-backs over `hard`) or
  `angle` (the old dihedral rule); keep modifiers live until export; the exporter writes the evaluated mesh.
- **UVs on purpose:** `kit.uv_region` into named atlas regions; paint up-facing faces into the clean zone of
  `vehiclegrunge256` and map V to height on the sides; `uv.unwrap` with `planar` or `cube` (`size` = metres per
  repeat) for tiling surfaces; `uv.texel` reports px/m; `uv.fit` keeps no aspect by default (`keep_aspect`
  true keeps it); lofts, sweeps and lathes come with real UVs (`kit.uv_region` with `project: "keep"` fits them
  into an atlas region without new seams). Never collapse a face onto one texel.

## Modifiers

| Modifier | Use it for | Watch out |
|---|---|---|
| MIRROR | symmetric bodies, props, weapons | apply before splitting left/right parts and before UV2 |
| SUBSURF | one level on curved patches (roof, bonnet crown, fenders), then apply | never as density padding on flat parts |
| BEVEL | rounding a silhouette edge, a prop cap, a bumper lip | more segments rather than one hard chamfer on a rounded form |
| SOLIDIFY | thin parts that read from both sides (door skins, spoilers, fins, fences); `mesh.solidify` closes a free sheet | glass stays single-sided |
| SHRINKWRAP | flush details on a curved body (lamps, badges, trim strips) | keep a small offset against z-fighting; `mesh.attach` snap does the same for one part |
| LATTICE, CAST, SIMPLE_DEFORM | crowning, pillowing, tapering whole parts (`mesh.deform`) | apply before cutting parts |
| WEIGHTED_NORMAL | the last shading step | only after smooth faces and sharp seams |
| ARRAY, CURVE | repeated or bent elements (rails, fence posts, cables, exhaust pipes) | apply before export if counts must be exact |
| DECIMATE | planar mode only, to remove coplanar splits | never to reach or move a number |

## Reference photos and planes

References are described, not measured: the rules are in `references.md`. In the session: `ref.plane` {`view`,
`image`, `length`} only for a true side, front, rear or top view, scaled by the SA length (the object is found
against the photo's border colour, or give `span` [x0, x1] px) or by two `points` and their `distance`;
`blender.preview --ref <photo> --ref-view side` puts the photo behind the matching view of the lineup;
`look.silhouette` {`ref`, `view`} reports the outline overlap as information. Never a grid, a solved camera or
back-projected pixels.

## Recipes by kind

**Car (automobile; other vehicle types the same with their own template).**
1. G0: design description, features list, ratio card (`references.md`); `kit.template --like <replaced SID>
   --tier sa_plus --ghost`; lineup peers of the same body type.
2. G1 form: lower body and glasshouse lofts from the description (or a matching blank, reshaped), `kit.wheel`
   at `wheel_scale` in the arches, smooth shading; lineup sheet.
3. G2 compose: arches with liners or flares (`mesh.flare`), bumpers (`mesh.sweep` + `mesh.attach`), pillars and
   window frames from the shell, `kit.blank_split` into the slots, doors thickened with jambs, mirrors on stalks;
   refit dummies (`kit.info` `frames: true`); `form`/`fit` clean.
4. G3 detail: the checklist above (lamps, grille, interior, engine bay, underbody).
5. G4 surface: `kit.material_preset` (paint on grunge with UV2 sheen, lamps, glass, chrome, trim, interior,
   plate), `kit.uv_region`, own soft textures (`textures.md`), `kit.shade`.
6. G5 finish: `kit.damage`, `kit.vlo` (`method` sections, the default, or decimate), `kit.col` (`contact` faces
   under the top), `kit.export --replace <SID>` (or `--add`), `asset.check`, lint, the in-game check.

**Bike.** Lofts per body part (cowl, floor, leg shield, seat, tank, fairing) of rounded sections, joined where
they meet; the steering column built around the `forks_front` axis inside the headset; seat, footrest and grips
at the rider's contact points of the like model (`vehicles.md`).

**Street prop.** Closed primitives (`cylinder` with explicit segments, `rounded_box`, `capsule`) that touch or
push into each other, chamfered caps, 1-2 materials, one 64-128 px soft texture; no normals in game: bake
occlusion into the prelight (`kit.bake` masks, `texture.finish --edge --role prop`, then `kit.export`, which
writes prelight and a primitive COL by the class rule; `blender.game_ready --asset-class prop` for a mesh made
outside the kit); draw 100 or less.

**Building with LOD.** One main shell (`kit.blank --kind building_box` or extruded footprints) with setbacks,
thin ledges and cornices, a roof slab with overhang and fascia, porch and chimney volumes that sit on it;
windows, doors and brickwork in tiling textures (`uv.span` 3-6); 4-7 materials; dark grey day prelight with
occlusion, warm night colours with lit windows; collision mesh at about a quarter of the render faces
(`col.gen`); draw at most 299; LOD with `kit.lod` (32-64 px textures in a shared LOD TXD, `lod` + name without
its first 3 characters, draw about 800).

**Interior.** Shells face inwards and are prelit, every room closed floor to ceiling (leak pass from inside);
interior props may carry normals and stand on the floor; floors get FLOORBOARD or CARPET surfaces; keep entry
markers in mind (`limits.md`, `kinds.md`).

**Boat, helicopter, plane, bicycle, quad, trailer, train.** One welded hull, fuselage or body shell from a loft;
wings, fins and tubes as sweeps of rounded profiles joined at closed roots and joints; every moving part on its
frame origin; the kind's items and regions in `done.md`, worked steps in `kinds.md`.

**Weapon.** From the side profile (sweep or an extruded outline, rounded grips), real length against the 1.84 m
ped; firearms keep harder edges on receivers and slides, melee smooth; one 64-128 px photo-like texture on both
sides; the `gunflash` atomic from the template.

**Ped.** Re-skin the vanilla mesh or edit vertices while keeping the 32-bone skin, one material and at most 4
weights per vertex; a new skeleton is out of scope.

**Pickup.** Small (0.2-1 m), one 32-64 px saturated texture, prelit, one collision sphere, draw 40-100.

**Vehicle upgrade.** Dynamically lit like the car (normals, no prelight), 1-2 materials, the same soft forms,
frames named for the `ug_*` anchors of the cars that take it, seated on the body where it mounts.

## A car in batches (a tested pattern)

The car of `creation-quickstart.md` step by step (`satk blender call batch --session mycar --params-file <file>`
after `kit.template --like model:426 --name mycar --ghost`). Coordinates are the template's model space (the like
model's: premier has its wheels at y +-1.64, z -0.35 and the ground at z -0.70; `kit.info` `frames: true` lists
yours). Replace the numbers with your design; keep the order.

`body.json` (G1): one half loft with the body's stations and the panel regions for the cut.

```json
{"steps": [
 {"method": "mesh.loft", "params": {"name": "mycar_body", "model": "mycar", "axis": "y", "samples": 32, "half": true, "steps": 2,
  "sections": [
   {"at": -2.70, "shape": {"w": 0.92, "h": 0.56, "z": -0.36, "exp": 3.5, "mid": 0.75, "crown": 0.02}},
   {"at": -2.35, "shape": {"w": 1.03, "h": 0.73, "z": -0.42, "exp_top": 4, "exp_bottom": 4.5, "mid": 0.92, "crown": 0.03, "flat_bottom": true}},
   {"at": -1.60, "interp": "linear", "shape": {"w": 1.03, "h": 0.74, "z": -0.42, "exp_top": 4, "exp_bottom": 4.5, "mid": 0.91, "crown": 0.03, "flat_bottom": true}},
   {"at": -1.00, "shape": {"w": 1.03, "h": 1.22, "z": -0.42, "exp_top": 5, "exp_bottom": 4.5, "mid": 0.565, "crown": 0.06, "tumble": 0.78, "shoulder": 0.05, "flat_bottom": true}},
   {"at": 0.20, "interp": "linear", "shape": {"w": 1.03, "h": 1.22, "z": -0.42, "exp_top": 5, "exp_bottom": 4.5, "mid": 0.565, "crown": 0.06, "tumble": 0.78, "shoulder": 0.05, "flat_bottom": true}},
   {"at": 0.90, "shape": {"w": 1.03, "h": 0.75, "z": -0.42, "exp_top": 4, "exp_bottom": 4.5, "mid": 0.9, "crown": 0.035, "flat_bottom": true}},
   {"at": 2.20, "shape": {"w": 1.02, "h": 0.67, "z": -0.42, "exp_top": 4, "exp_bottom": 4.5, "mid": 0.89, "crown": 0.03, "flat_bottom": true}},
   {"at": 2.55, "shape": {"w": 0.91, "h": 0.52, "z": -0.36, "exp": 3.5, "mid": 0.72, "crown": 0.02}}],
  "parts": [
   {"name": "bonnet", "at": [1.0, 2.45], "angle": [0, 62]},
   {"name": "boot", "at": [-2.6, -1.7], "angle": [0, 62]},
   {"name": "door_f", "at": [-0.35, 0.95], "angle": [62, 128]},
   {"name": "door_r", "at": [-1.15, -0.35], "angle": [62, 128]}]}}
]}
```

`compose.json` (G2): arches with a band, a return and a liner, wrap-around bumpers, a mirror on the door, a low
three-quarter camera for the composition view (`--snapshot cam_low`).

```json
{"steps": [
 {"method": "mesh.flare", "params": {"object": "mycar_body", "arch": {"center": [1.0, 1.64, -0.35], "radius": 0.44},
   "segments": 7, "width": 0.05, "out": 0.02, "lip": 0.04, "depth": 0.28}},
 {"method": "mesh.flare", "params": {"object": "mycar_body", "arch": {"center": [1.0, -1.64, -0.35], "radius": 0.44},
   "segments": 7, "width": 0.05, "out": 0.02, "lip": 0.04, "depth": 0.28}},
 {"method": "mesh.sweep", "params": {"name": "mycar_bump_f", "half": true, "samples": 16, "profile": {"w": 0.09, "h": 0.2, "exp": 3},
   "path": [[0, 2.60, -0.28], [0.45, 2.60, -0.28], [0.72, 2.56, -0.28], [0.93, 2.30, -0.28], [0.95, 2.12, -0.28]]}},
 {"method": "mesh.sweep", "params": {"name": "mycar_bump_r", "half": true, "samples": 16, "profile": {"w": 0.09, "h": 0.2, "exp": 3},
   "path": [[0, -2.75, -0.26], [0.45, -2.75, -0.26], [0.70, -2.69, -0.26], [0.90, -2.40, -0.26], [0.92, -2.12, -0.26]]}},
 {"method": "mesh.primitive", "params": {"kind": "rounded_box", "name": "mycar_mirror", "size": [0.14, 0.05, 0.04],
   "radius": 0.015, "segments": 2, "location": [1.13, 0.80, 0.33]}},
 {"method": "mesh.primitive", "params": {"kind": "rounded_box", "name": "mycar_mirror_head", "size": [0.09, 0.22, 0.14],
   "radius": 0.04, "segments": 2, "location": [1.22, 0.79, 0.37]}},
 {"method": "scene.join", "params": {"objects": ["mycar_mirror", "mycar_mirror_head"]}},
 {"method": "mesh.attach", "params": {"object": "mycar_mirror", "to": "mycar_body", "mode": "snap",
   "select": {"where": ["x<-0.06"]}, "rigid": true, "max_dist": 0.12}},
 {"method": "scene.duplicate", "params": {"object": "mycar_mirror", "name": "mycar_mirror_l", "mirror": "x"}},
 {"method": "camera.add", "params": {"name": "cam_low", "location": [3.4, 4.2, -0.2], "target": [0.6, 1.2, -0.3], "lens": 40}}
]}
```

Then `kit.blank_split` {`object`: `mycar_body`, `fill`: true} cuts the parts into their slots (`bonnet_ok`,
`boot_ok`, `door_lf_ok` ..., the rest into `chassis`), and `fill.json` puts the loose pieces in:

```json
{"steps": [
 {"method": "kit.fill", "params": {"slot": "bump_front_ok", "objects": ["mycar_bump_f"]}},
 {"method": "kit.fill", "params": {"slot": "bump_rear_ok", "objects": ["mycar_bump_r"]}},
 {"method": "kit.fill", "params": {"slot": "chassis", "objects": ["mycar_mirror", "mycar_mirror_l"], "append": true}},
 {"method": "kit.info", "params": {"frames": true}}
]}
```

`surface.json` (G4, the minimum): paint on the shared grunge texture, glass above the belt, the loft's own UVs
fitted into the atlas regions (`project: "keep"` adds no seams), then the shading. Lamps, trim, interior and own
textures follow the same way (`kit.material_preset`, `kit.uv_region`).

```json
{"steps": [
 {"method": "material.assign", "params": {"objects": ["chassis", "bonnet_ok", "boot_ok", "door_*_ok", "bump_*_ok"], "material": "mycar.paint1"}},
 {"method": "material.assign", "params": {"objects": ["chassis", "door_*_ok"], "material": "mycar.glass", "select": {"where": ["z>0.36", "z<0.74"]}}},
 {"method": "kit.uv_region", "params": {"object": "chassis", "region": "grunge.paint", "faces": "role:paint1", "project": "keep"}},
 {"method": "kit.uv_region", "params": {"object": "chassis", "region": "generic.glass_core", "faces": "role:glass", "project": "keep"}},
 {"method": "kit.shade", "params": {}}
]}
```

G5: `kit.damage`, `kit.vlo`, `kit.col` (each with `{}`), `satk kit export --replace model:426 --session mycar`;
its answer carries `files.dff`, the lint summary and the `asset.check` verdict with its rows (here a `fit.hinge`
row until the boot dummy is moved to the hinge edge).

## Anti-patterns

- Vertex-by-vertex meshes: typed vertex lists, scripts that compute every vertex, private mesh libraries.
- Body parts as cubes with one chamfer; flat sides, flat roofs, square plans; a brief word like "boxy" built
  literally.
- Shape by rigid slab moves (a box selection shifted or scaled) instead of falloff moves and re-sectioning.
- Replacing a cut body panel with a primitive; recolouring faces instead of building lamp buckets or a grille.
- Floating details, see-through arches, separate horseshoe flares, pillars as bars; boolean soup.
- Changing geometry to move a number (dissolve, decimate, subdivide); subdivide-then-decimate density; uniform
  grids on flat panels.
- Reverse-engineering satk sources to drive a generator instead of shaping the mesh.
