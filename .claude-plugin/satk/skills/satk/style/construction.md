# Construction: how San Andreas models are built

<!-- Model-facing, English only. The vanilla construction grammar, written as instructions to a modeller. Evidence:
     the clean 1.0 US copy (profile vanilla) read with satk's DFF reader: landstal 400, huntley 579, rancher 489
     (SUVs), premier 426, sentinel 405, elegant 507 (sedans), faggio 462, pcj600 461 (bikes), seven street props
     and two houses. Numbers are what vanilla does, never targets. Table conventions: README.md. -->

Read this before you build anything. Each rule says what vanilla does, how to build it with the session methods
and how to see or check it. Numbers in brackets are vanilla reference: they tell you what "crowned" or "thick"
means, they are never goals, gates or warnings. Detail may be far richer than vanilla (interior, engine bay,
underbody); these rules are about the language the detail is built in, not about its amount.

The look in one sentence: **soft, rounded, simplified forms that are composed into ONE coherent, joined whole,
with crisp lines only where a material or a panel changes.** A model fails the style when it is a set of hard
boxes (the "voxel" look), when its parts float next to each other, or when it is under-detailed or unfinished
(items of its inventory missing, regions nobody looked at: `done.md`).

The rules below are written for road cars, where the evidence is richest, but they hold for every kind: one
welded shell (body, hull, fuselage, building walls), parts cut from it or touching it, nothing floating, nothing
see-through except designed openings, corners turned in smooth steps, crisp lines only on seams, every moving
part on its frame origin. How each other kind applies them: `kinds.md`.

## How to look at a model

- Look at a vanilla peer of the same body type and at your model in the same views: `blender.preview
  session:<name> --lineup class --passes game,clay,wire` (or `--like <SID>`), and the side, front and top
  views of the session (`camera.views`, `look.render`); a low three-quarter camera is `camera.add` with
  `location` and `target`, shown with `--snapshot <camera>`.
- Clay shows the form and the normals; wire shows where the density goes; game shows materials and paint.
- A low three-quarter view (camera at wheel height) shows floating parts, see-through arches and steps at cuts.
- `asset.check` adds a `form` section (floating, interpenetrating and loose pieces, hard corners, see-through
  openings) and a `fit` section (wheels in arches, dummies on their surfaces, hinges, bike steering and rider).
  Every row there is a located defect to fix, not a number to chase.

## Vehicles: the rules

### Shell and panels

1. **One welded shell carries the body.** Roof, pillars, fenders, quarters, rear panel, floor, dash, fixed
   glass and lamp lenses are one welded surface (the largest welded piece holds 71-84 % of a vanilla car
   chassis). The other pieces of a vanilla chassis are deliberate loose items: seats, engine block, roof rails,
   grille teeth, wipers, exhaust. The shell is single-sided and open where nobody looks (underside, inside the
   openings); it shows thickness only where the eye sees one (door jambs, lamp buckets, window frames).
   Build: the body is a loft (or the blank) with a MIRROR modifier; features are cut, inset, extruded and grown
   from the shell's own faces; a part that belongs to the body is joined with `mesh.attach` (`weld` or
   `bridge`). Check: the `form` rows `loose_share` and `floating`; a clay view shows one continuous body.
2. **Panels are cut from the shell, never modelled on top of it.** Bonnet, boot, doors, bumpers and
   windscreen are separate atomics because the game needs them, but their outline lies on the shell (median
   distance of their border to the chassis surface 0-6 mm). Build: name the part regions when you loft
   (`parts`) or use the blank's regions, then `kit.blank_split` cuts along them. Never replace a cut panel with
   a new primitive. Check: no step and no gap along a cut line in the low three-quarter clay view.
3. **Lines run across the cuts.** The beltline, the side moulding and the bonnet creases continue at the same
   height over fender, doors and quarter; door edge loops continue into the fender within about a centimetre.
   Build: author each line once on the shell (a station row or an edge loop), then cut. Check: in the side view
   the line is unbroken through every cut.

### Sections: nothing is flat

4. **Every surface is crowned.** The roof is a gentle arc (crown 3-10 cm across the car, 7-15 section points
   including the rounded edges), the bonnet is crowned 2-7 cm and often carries two long creases, the lower side
   is a barrel that bulges 3-9 cm between sill and belt with 3-5 points. Build: section `crown`, a superellipse
   `exp` that keeps the corners round, soft moves (`mesh.transform` with `falloff`), `mesh.deform` `cast` for a
   pillow. Check: the front view of a cut or of the clay: no straight wall from sill to belt, no flat roof plate.
5. **The greenhouse leans in.** Side glass 16-27 deg from vertical (sedans about 25), windscreen 25-35 deg from
   horizontal on cars and 42-51 deg on upright SUVs. Build: `tumble` on the glasshouse sections. Check: the
   front view: the glass tucks in above the belt.
6. **Corners turn in two or three smooth steps.** Roof edge, fender tops, nose and tail corners and bumper ends
   carry 2-4 section points per corner; in plan the nose is a shallow arc and the four corners are rounded with
   3-4 segments. Never one vertex on a 90-degree corner. Build: section `exp`, plan rounding through the section
   widths along the length (`interp` smooth). Check: highlights roll around the corners in clay; the top view
   has no square corner.

### Shading

7. **Hard edges live on seams.** Most vanilla hard edges (80-93 % on the chassis) lie on a material or UV border;
   the others are creases you can name (moulding, bonnet creases, shut lines). Build: `kit.shade` (`mode` seams,
   the default; name a crease by marking it sharp with `mesh.mark`), not one angle threshold (`shading.md`).
   Check: clay shows no hard line inside a panel except a named crease.
8. **Soft corners survive.** Even among 60-90 deg folds a quarter to almost half stay smooth in vanilla (rounded
   corners, lamp buckets, mirror heads). Smoothing everything below one angle and splitting everything above it
   turns a rounded body into a bevelled box.

### Arches

9. **Arches are holes with a liner.** The cut has 5-8 segments over the top (often flat-topped or trapezoidal),
   a radius of about 1.15-1.3 times the wheel radius (4-9 cm clearance), a short return face and a flat liner
   plate inside: you never see through the body. Build: `mesh.flare` with `arch` {`center`, `radius`} and
   `segments` cuts the arch into the shell with a return (`lip`) and a liner plate (`depth`); the blank comes with
   lined arches; or an inward `mesh.extrude` of the rim. Check: the side view without the wheel; `form` row
   `see_through`; `fit` row for the wheel centred in its arch.
10. **SUV flares grow out of the fender.** The flare is a raised band 5-8 cm wide inside the welded fender
    surface, its outer edge chamfered and smooth-shaded into the body. It is never a separate horseshoe standing
    off the side. Build: `mesh.flare` on the shell's arch (`out` 0.02-0.05 and `width` 0.05-0.08: welded, with a
    lip and a liner). Check: low three-quarter clay: no gap and no shadow line between flare and body, no
    separate piece near the arch.

### Bumpers

11. **Bumpers wrap from arch to arch.** A wrap-around volume with 2-4 profile segments, smooth-shaded: soft
    rubber tubes on sedans, a chunkier block with rounded ends and a groove on trucks. Its top meets the body
    along a shared line; the plate is a card on it; fog lamps and intakes are set into it. Build: `mesh.sweep` a
    rounded profile along the plan curve of the nose or tail and `mesh.attach` it (`snap`), or reshape the
    bumper region of the blank with soft moves. Check: three-quarter views: no gap at the top line, the ends tuck
    into the arches.

### Doors, glass, pillars

12. **Doors are thick shells that carry their frame and glass.** Outer skin, a flat inner trim panel (interior
    atlas) and jamb faces; the lower door is 6-13 cm thick. The door carries its window frame (thin dark trim
    strips) and its glass: one flat pane, flush with the frame or up to about 1 cm behind it. Pillars belong to
    the shell (A, C) or to the door frame (B): dark trim material on shell faces, never extra bars stuck on.
    Build: `mesh.solidify` the cut door skin, inset the window, assign trim and glass. Check: open the door in
    the preview: inner panel and jambs are there; no black bars floating on the glasshouse.

### Lamps and grille

13. **Lamps sit in recesses.** The lens is a flat face of a few triangles on the lamp key, 3-8 cm behind its
    surround on most cars (flush on some trucks), the bucket walls angle in. A lamp is never a box stuck on the
    surface. The grille is a recessed opening with a surround; its mesh is texture, teeth or bars are geometry.
    Build: `mesh.inset` with depth on the lamp faces, lens faces on the lamp key; the grille surround from the
    shell, bars as `mesh.primitive` `rounded_box` or a sweep. Check: a nose and a tail close-up: bucket, lip and
    lens read at game distance.

### Small details

14. **Small details touch what they sit on.** Handles, rails, mirror stalks, seats and the engine are in
    contact with their parent or slightly embedded (0-1 mm). Only interior cards, plates and mirror heads on
    their stalks may hover. A mirror is a head (18-32 cm) on a short stalk that touches the door or the A-pillar
    sail. Build: `mesh.attach` mode `snap`; rails as a `mesh.sweep` on feet that sit on the roof. Check: `form`
    row `floating` (name, gap, centre of every floating piece).

### Inside

15. **The car is closed from every angle.** Interior (front seats, rear bench, a dash that is part of the shell,
    steering wheel, gauges), engine bay (inner fender walls, an engine block, an air cleaner), a floor plate
    with rails and the exhaust under it, a spare tyre in an SUV boot. Vanilla keeps them simple (seat wedges,
    one generic engine block). Richer is welcome in the same soft language: cushions and backrests with rounded
    edges and headrests, a dash with a gauge hood, door cards, a console, radiator, battery and hoses. Check: the
    `coverage` rows of `asset.check`; a view through the glass and one with the bonnet open.

### Proportions

16. **Chunky and planted.** SA cars are about 1.1-1.2 times the real car and relatively wider than it (width to
    length 3-23 % above the donor); sedans are a little lower and longer; the wheel stays about real against the
    height. Keep the donor's lines (roof line, glass rake, overhangs, beltline, arch shape, lamp and grille
    arrangement, signature items), not its millimetres. Recurring exaggerations: a pinched greenhouse, thick
    wrap-around bumpers, rounded corners everywhere, deep lamp recesses on trucks, chunky flares on SUVs.
    Check: the lineup next to two vanilla peers and the features list (`references.md`).

### Surface

17. **Clean key-colour paint.** Paint is the key colour on `vehiclegrunge256`: up-facing faces map to the clean
    zone of the texture, side faces map V to height, so the engine's dirt only reaches the lower sides. No dirt,
    shading or panel lines in the paint. Check: the game look at dirt 0 and 2; once at the dirtiest spawn level
    (14) only the lower third of the sides may show grime.
18. **Small, blurry photo textures.** About 50 px/m on paint and trim, 60-80 on the interior, 130-200 on lamps
    and tyres; one small own interior atlas; trim, glass, lamps and tyres from the shared `vehicle.txd`.
    (`textures.md`)

### Density

19. **Density where the outline turns.** Long thin triangles run across panels (a door can be a few strips),
    the vertices crowd at corners, arches and lamps; there is no even grid. Build: put loft stations where the
    form changes and let `steps` follow the curvature. Check: the wire side view. This is about WHERE vertices
    go, never about how many: rich detail that turns the outline (lamp buckets, handles, a shaped interior) is
    welcome and never counted.
    **The soft look comes from the normals (and on map models from the prelight), not from density.** A
    subdivision surface, or loops added everywhere "to make it smooth", gives a uniformly dense mesh with folds of
    a few degrees: it reads as modern CG, not SA, and costs a lot on a map object the game places hundreds of
    times. `asset.check` finds it as `form.dense_flat`: a vertex is wasted when it could collapse into a
    neighbour with its faces within 8 deg and its edges are under 10 cm; seams, open edges and double-sided
    cards never count. A finding = at least 100 wasted triangles and a third of the model (vanilla, all 14,790
    models: none; the highest share 0.26). It names the regions (triangles, area, centre, size, edge length,
    fold) and says: move density to where the outline turns; let normals make it soft. It is a **defect on map
    models** (vanilla map models placed 10 or more times waste at most 168 triangles, those placed 50 or more
    times at most 20) and **advice on vehicles** and every other kind. Fix: rebuild the part with fewer
    sections (12-16 segments around a 0.5 m barrel, no rings on its straight part, a ring only at each foot,
    bead and rim), dissolve the flat loops, keep the smooth normals; never decimate blindly over the detail.

### Bikes and other vehicle types

- Bikes follow the same language: every body part is a closed, rounded volume (a scooter's cowl, floor, leg
  shield and seat are lofts of rounded sections), joined to its neighbours. The faggio chassis is one welded
  piece holding almost all of its triangles.
- Function decides placement: the steering axis, the suspension nodes and the rider's contact points are fixed
  by the frames and the animation (`vehicles.md`, "Bikes: steering and rider").
- Bicycles: tubes are sweeps of a round profile that meet at closed joints (no gap where two tubes meet); the
  fork and the handlebar turn about the head tube, the chainset about the bottom bracket.
- Boats: one welded hull, closed below the waterline; the deck meets it along the sheer line; the cockpit is cut
  down into the deck with a floor; propellers sit on their frames behind and below the hull.
- Helicopters and planes: one welded fuselage with the canopy glass in the shell; wings, fins and tailplanes are
  sweeps of a rounded profile joined at closed roots; rotors, propellers and control surfaces are separate
  frames with their hinge or hub at the frame origin, flush in their notches at neutral.
- Trailers and trains: a frame of rounded rails under the body, nothing floating between them; bogies and axles
  under their pivots; couplers at the like model's height.

## Props and buildings

20. **Props are kits of closed, interpenetrating primitives with baked occlusion.** A hydrant is about 18 closed
    pieces (8-sided cylinders with chamfered caps), a bench 9, a bin 11; pieces push into each other and never
    leave a gap. A round prop is 8-16 segments around with long strips between the rings that shape it (the
    vanilla bins use 16 segments: 22.5 deg folds), never a subdivided barrel. Chamfered vertical box edges (phone booth), boxes with a lip (dumpster), perforations as alpha
    texture (bin). Map models carry no normals: the soft look comes from a dark prelight that is darker at the
    base (`world.md`). Build: `mesh.primitive` (`cylinder`, `rounded_box`, `capsule`) with `mesh.attach`
    `snap` or a slight overlap; `kit.bake` and `kit.export` prelight.

- **Buildings:** one main shell holding most of the triangles (about 70 % on vanilla houses), flat walls, a
  thin roof slab with overhang and fascia, small closed volumes for chimney, porch posts and steps. Windows,
  doors, bricks and railings live in tiling textures. Richer massing (setbacks, ledges, cornices, balconies) is
  welcome when the pieces sit on the shell.
- **Weapons:** build from the side profile (a sweep or an extruded outline, rounded where the hand goes), never
  from a box; firearms keep harder edges on receivers and slides; magazine, sights and scope mounts push into the
  body; origin and axis as the vanilla weapon of the slot.
- **Interiors:** a shell that faces inwards, every room closed from floor to ceiling, doorways with frames,
  furniture as closed pieces standing on the floor (`kinds.md`).

## What made agent models look wrong, and the fix

| Complaint | What happened | What vanilla does | Fix |
|---|---|---|---|
| everything is square, like voxel art | bodies and parts built from cubes with one chamfer; plan and sections flattened; a brief that asked for a "boxy" car | rounded sections, crowned surfaces, corners turned in smooth steps | Sections: rounded sections and sweeps, soft moves, never a cube as a body part |
| parts are not joined, together it is mush | flares, bumpers, rails, pillars and fascia added as separate pieces next to the body | one welded shell, panels cut from it, details touching | Shell and panels, Arches, Bumpers, Small details: `mesh.attach`, `mesh.flare`, `kit.blank_split`; fix every `form` row |
| pillars and window frames as black bars | frames added as cuboids and recoloured | frames are dark trim on the shell and door faces | Doors, glass, pillars |
| arches look like stadium cut-outs or horseshoes | super-ellipse cut without liner, or a lathe ring stuck on | polygonal arch with liner; SUV flare welded into the fender | Arches |
| too few details | stopped at the first acceptable lineup; triangle counts read as a ceiling | every class item present: lamps in buckets, grille, mirrors, handles, interior, engine block | the detail pass at gate G3 (`modelling.md`); counts are information |
| smooth like modern CG, heavy for a map object | a subdivided smooth barrel: `geo.tris` 4,062 on a 0.9 m bin, folds of 3-8 deg and 5 cm edges everywhere | 16 segments around, long strips, density at the rims; soft from the prelight | Density (rule 19): the `form.dense_flat` rows name the regions; rebuild them coarser, keep the normals smooth |
| unfinished: parts missing, gaps left, hidden sides empty | stopped at the first acceptable lineup and declared the model done; no item list; underside, interior and back never looked at | every part of the class present, closed from every angle | the inventory as the task, the region sheets, the leak pass, `asset.check --strict` (`done.md`) |
| parts placed wrong | template dummies never refitted; seat and bars ignore the rider pose; steering axis outside the body | dummies on their lamps, pipe and hinges; steering column around the axis; rider contacts fixed | refit dummies (`vehicles.md`), `fit` rows, the in-game check |
| dirt everywhere, neon green body | preview showed the raw paint key and raw grunge; textures painted with grime | clean key colour; the engine adds a little dirt low on the sides | Surface; judge paint at the low dirt levels |
| wrong local proportions from photos | a pixel grid, a solved camera and back-projected points from a three-quarter photo | simplified silhouette with the donor's lines | `references.md`: spec sheet, features list, true views only |
| a template shape instead of the car | the blank's generic profile kept, then flattened by numbers | the design of the real car, simplified | the blank is a quick start only; build from the description |
| numbers gamed | coplanar faces dissolved to move a metric into a band | counts are a result of the form | never add or remove geometry to move a count |

## Evidence (vanilla reference)

| Fact | Value | Source |
|---|---|---|
| welded shell share of the chassis | landstal 72 %, huntley 84 %, rancher 71 %, premier 81 %, sentinel 84 %, elegant 77 %; faggio 95 %, pcj600 62 % (plus a separate engine); an agent SUV that looked like voxel art: 30 % with 56 pieces | welded components of the chassis DFF |
| panel border to chassis surface (median) | windscreen 0-4.5 mm, bonnet 0-6.3 mm, front bumper 0.2-3.9 mm, rear bumper within 2 cm everywhere, boot 0-12.7 mm | point-to-surface distance of open boundary vertices |
| door lines continued in the fender | door edge heights matched by a chassis vertex within 12 mm: landstal 3 of 3, sentinel 5 of 6, huntley 6 of 6, rancher 7 of 7 | z-level match |
| roof and bonnet crown | roof 3-10 cm (landstal 7, huntley 5, premier 3, sentinel 10, elegant 3); bonnet 2-7 cm with 8-16 section points | transverse sections |
| lower body barrel | outer half-width at the B-pillar from sill to belt: premier 1.08 / 1.11 / 1.10 / 1.09 / 1.06 m, landstal 0.99 / 1.02 / 1.02 / 1.00 / 0.95 m | transverse section at the B-pillar |
| glass lean | side glass from vertical: premier 25, sentinel 27, elegant 27, landstal 23, rancher 19, huntley 16.5 deg; windscreen from horizontal: elegant 25, premier 31, sentinel 32, landstal 35, rancher 48, huntley 51 deg | glass face normals |
| hard edges on seams | 80-93 % of chassis hard edges on a material or UV border (agent SUV 55 %) | edge classes by dihedral |
| smooth among 60-90 deg folds | 22-45 % stay smooth (agent SUV 4 %); 30-60 deg chamfer edges 12-17 % of all edges (agent SUV 35 %) | edge classes by dihedral |
| chassis normal bend | 9.1-15.8 deg (agent SUV 5.5) | area-weighted vertex-to-face normal angle |
| door thickness | lower door 6-13 cm (median 12 cm on landstal, premier, elegant); agent SUV 3 cm | door section |
| lamp recess | lens 7 cm behind the surround on landstal, 5.5 premier, 7.8 sentinel, 2.9 elegant; flush on huntley and rancher | first vertex ring around the lens |
| detail contact | handles 0-1 mm, roof rails 0.1-0.2 mm, seats 0 mm, mirror stalks 0.4-0.9 mm, engine block 5-10 mm into the bay; agent SUV: 38 pieces up to 44 mm off the body | nearest-surface distance |
| mirror and handle sizes | mirror head about 18-32 x 8-25 x 10-18 cm on a stalk; handle about 15-21 cm long | piece bounding boxes |
| SA against the real car | length 0.98-1.22, width 1.14-1.29, height 1.04-1.16 times the donor | six cars with approximate donors |
| prop construction | hydrant 18 closed pieces, bench 9, bin 11; day prelight darker at the base (lamppost 28 at the bottom against 86 at the top) | prop DFFs and prelight |
| wasted density | all 14,790 vanilla models: no `form.dense_flat` finding; the highest share 0.26 (a gym dumbbell set by script), the most wasted triangles 614 at a share of 0.08 (an interior shell); vanilla bins 16 segments around; the first atelier bin 1,774 of 4,062 triangles (0.44) | `form.dense_flat` over the vanilla index |
| house construction | main shell holds about 70 % of the triangles (ganghous05 362 of 522, carlshou1 378 of 531) | building DFFs |

Full style guide: `README.md`; references: `references.md`; methods: `modelling.md`.
