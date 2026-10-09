# Done: the inventory, close-up regions, defects and review (every asset kind)

<!-- Model-facing, English only. The definition of done for every asset kind: the inventory as the task list
     (design/inventory.json, scene.tag, asset.inventory), the close-up region sheets (blender preview --regions),
     the leak pass (look.leak), asset.check --strict and the review procedure. Worked construction guidance per
     kind: kinds.md. Help topic: `done` (data/style/topics/done.md). -->

Agent-built assets failed less on skill than on stopping early: a model built the silhouette, looked at one
lineup, saw that it was "fine" and declared itself finished, with parts missing, dummies unfitted, gaps you
could see through and no detail pass. This guide makes the task explicit and the finish line objective.

**An asset is done when all four hold, and not before:**

1. every required item of `<project>/design/inventory.json` is `built` (`asset.inventory` answers
   `complete: true`; an item a reviewer rejected stays open until it is rebuilt and accepted);
2. `asset.check <package> --strict` answers `done: true` (no engine error or warning, no form, fit or mesh
   defect, no warning beyond the vanilla range of the class, no required checklist item missing, inventory
   complete, and for EVERY DFF of the package - the model and its LOD - a full `look.leak` run that satk recorded,
   `<package>/checks/<stem>.leak.json`, without gaps; anything else is listed in `blocking`, warnings within the
   vanilla range in `advice`);
3. every close-up region of the kind was reviewed on its own sheet and passed;
4. the reviewer (the user, or the critic of a harness) passed the final sheets.

The builder never decides this alone. A builder reports the state (the numbers of the inventory, the
`blocking` list, the region table); the checks and the reviewer decide.

## 1. Rules for the builder

1. **Never declare yourself done.** Do not write "complete", "finished" or "ready" while `asset.check
   --strict` says `done: false` or an inventory item is `missing` or `unattached`. Report what is open instead.
2. **The inventory is the task.** Write `<project>/design/inventory.json` at G0, before the first mesh. Work
   from it at every stage: the stage is finished when every required item of that stage is built
   (`asset.inventory <project> --stage G<n>`).
3. **Itemise to the ambition.** The detail level of the commission (`hero` by default for new assets) sets
   how far the list goes (section 3). Start from the kind's starter list, add every feature of the
   references and the design; a list shorter than the starter list needs a reason per dropped item.
4. **Tag as you build.** Every object (or face set) you create gets its item id with `scene.tag` in the same
   batch. Untagged geometry is invisible to the report; an item without tagged geometry is `missing`.
5. **Built means finished in the style.** An item counts as built only when it has its final form in the SA
   language (rounded, crowned, joined), touches what it attaches to, and carries its material. A box standing
   in for a seat, a cylinder standing in for an engine, a recoloured face standing in for a lamp, or the
   template ghost are not built: leave such an item `missing` until it is. The report checks part of this: an
   item whose tagged faces have no vertex of their own (a patch of another item's surface) is `rejected` as a
   placeholder; a count counts only pieces with geometry of their own. Build a part as its own piece: extrude,
   inset and extrude (a recess), or a separate mesh. Texture-carried items (`category: decal`) are proven by an
   image texture on their faces, a paint surface (`verify: paint`) by the paint key.
6. **Drop honestly, at G0 only.** A starter item leaves the list only with a real reason, written as a waiver
   (`"waived": [{"key": "<starter key>", "why": "<reason>"}]`; a pair or a set with fewer pieces:
   `{"key", "count", "why"}`): the design does not have it (no rear doors on a coupe), the engine cannot show it,
   the user asked to drop it, or it duplicates another item. "Time", "budget", "too complex", "hard to see",
   "not needed at game distance" and "the count looks full" are not reasons: `inventory.validate` refuses them.
   Marking a starter item `required: false` drops it too. The inventory's kind and detail are the commission's
   (`asset.json`): a lower detail level or another kind is an error. After G0 you never remove an item, make it
   optional, move it to a later stage or loosen it (its `count`, `attaches_to`, `category`, a larger
   `max_gap_mm`). Rejections are the reviewer's (`inventory.mark --reject`); you never clear one yourself.
7. **Hand a gate in, do not close it.** `asset.status <dir> --record '{"gate": "G2", "state": "review"}'`;
   `done` is refused while the gate's items are open, `skipped` needs a `why`.
8. **Look at every region close up.** After G2, G3, G4 and at the end run `blender.preview --regions` and look
   at every region sheet, including the ones nobody "normally" sees: underside, inside the doors, the engine
   bay, behind the seats, the back of a sign, the roof of a building, the inside of a hull. Write one line per
   region into `notes.md` (region, sheet, verdict, what you fixed).
9. **Run the leak pass.** `look.leak` after G2 and at G5, on the model and on its LOD: every located gap is
   closed; a designed opening (a window, a grille hole, a doorway) is named in the notes.
10. **Polish until strict.** After G5 loop: `asset.check --strict`, fix the first items of `blocking`, re-run
   the regions that changed, repeat. Stop only at `done: true`, or when a blocking item needs a tool that does
   not exist: then report it as an open issue with its evidence, never hide it.
11. **Never trade detail for a pass.** Never delete, simplify or reject a built item to make a check pass;
    fix the item. Never change geometry to move a number.
12. **Do a second pass on purpose.** At the end of each stage, read the inventory, the features list and the
    region sheets once more and ask what is still missing or weak; the first acceptable look is the start of
    polishing, not the end.
13. **Answer every finding.** When resumed with a critique or the user's feedback, answer each point on its own
    line: fixed (how, which item), or not fixed (why, with evidence). Silence on a point is not allowed.

## 2. The inventory (`<project>/design/inventory.json`)

The asset project (`asset.init <dir>`) holds the inventory next to `asset.json`:

```json
{"kind": "automobile", "detail": "hero",
 "waived": [{"key": "doors_rear", "why": "two-door coupe: the design has no rear doors"}],
 "items": [
 {"id": "I01", "key": "body_shell", "name": "lower body shell", "region": "overall", "category": "shell",
  "construction": "mesh.loft half, 8 shape sections, parts bonnet/boot/doors", "attaches_to": null, "stage": "G1",
  "frame": "chassis"},
 {"id": "I14", "name": "headlamp bucket right", "region": "front", "category": "lamp",
  "construction": "mesh.inset with depth into the shell, lens faces on the lamp key", "attaches_to": "I01", "stage": "G3"},
 {"id": "I37", "name": "driver seat", "region": "interior", "category": "interior",
  "construction": "mesh.loft cushion + backrest + headrest, rounded", "attaches_to": "I30", "stage": "G3"},
 {"id": "I40", "name": "left and right wipers", "region": "front", "category": "trim", "count": 2,
  "construction": "mesh.sweep arm and blade on the scuttle", "attaches_to": "I01", "stage": "G3", "required": false}]}
```

| Name | Meaning |
|---|---|
| `kind` | the asset kind of `asset.init` (one of the 20 kit kinds: automobile, mtruck, quad, bike, bmx, boat, heli, plane, trailer, train, prop, breakable, animated_object, building, interior_shell, interior_prop, weapon, ped, pickup, vehicle_upgrade) |
| `detail` | `simple`, `standard` or `hero` (see "Detail levels") |
| `id` | `I` + 2-4 digits (`I01`, `I02`, ...); never reuse an id for another item |
| `key` | the starter item it came from (kept, so a dropped starter item is noticed: it needs a `waived` entry) |
| `name` | what the item is, in words a reviewer can find on a sheet (side matters: "left mirror") |
| `region` | a region of the kind's starter list (`overall`, `front`, `sides`, `interior`, ...); the starter's `regions` map each to the close-up sheets (`views`) where a reviewer looks for it |
| `category` | `shell`, `panel`, `glass`, `lamp`, `trim`, `wheel`, `running_gear`, `mechanism`, `engine`, `interior`, `control`, `underbody`, `structure`, `fixture`, `opening`, `clutter`, `sign`, `decal`, `body_part`, `clothing`, `accessory`, `weapon_part`, `effect`, `detail` or `other` (it sets the attach tolerance: panel 30 mm, glass 25, mechanism 60, running gear 20, others 10) |
| `construction` | how it is built (at least 12 characters): the methods, or `texture: <page or atlas region>` for a painted item |
| `attaches_to` | `null` for a root item (the main shell), an item id, or a list of ids (any one); the report calls an item farther than its tolerance from every built parent `unattached` |
| `stage` | the gate that builds it: `G1` form, `G2` compose, `G3` detail, `G4` surface |
| `required` | default `true`; `false` for optional richness (it never blocks) |
| `count`, `frame`, `max_gap_mm` | optional: separate pieces (a left/right pair = 2), the game frame(s) it ends up in, a tolerance override |

**Writing it (G0).** `asset.init <dir> --kind K --detail D` writes the starter list of the kind and detail
level (`data/kit/inventory/<kind>.json`) as `<project>/design/inventory.json`; `inventory.starter <kind> --detail D
--project <dir> --force` writes it again. Then edit it:

- add every identity and secondary feature of `<project>/refs/features.md` as its own item (one lamp shape,
  one grille, one moulding: one item each, left and right separately where both exist);
- add what the design adds (a roof rack, a cargo, a sign, an interior room);
- split anything a reviewer could find half-built: "interior" is not an item; "driver seat", "rear bench",
  "dashboard with gauge hood", "steering wheel", "door card left front" are;
- mark the finish parts (damage states, LOD, collision) as items only when they need modelling of their own
  (a building's LOD massing, a separately modelled broken part); `asset.check` checks the rest;
- remove what the design does not have and add a waiver with the reason for each removed starter item
  (`"waived": [{"key": "<its key>", "why": "<a design fact, 8+ characters>"}]`); `inventory.validate <dir>
  --strict` must answer without errors (it also checks: `attaches_to` is null only for a shell, wheel, structure
  or body_part root; `max_gap_mm` at most 150; a starter pair keeps its `count`; kind and detail of `asset.json`).
- `asset.init --detail none` makes no inventory: `asset.check --strict` never calls such an asset done (re-skins
  and texture jobs use `mod.check`).

**Tagging (every stage).** `scene.tag` {`objects`: [names] or `object` + `select`, `item`} writes the custom
property `satk_item` (one id, or a comma list for an object that carries several items) or, for faces of one
object, the integer face attribute `satk_item_idx` with the object property `satk_item_ids` mapping the index to
ids. Tag in the batch that creates the geometry; re-tag when you join, split or `kit.fill` pieces into a slot
(the tags of joined pieces must survive: tag the faces when several items end up in one object, for example
handles and mirrors joined into `chassis`).

**The report.** `asset.inventory <project> [--session <name>] [--dff <file>] [--stage G<n>] [--md]` answers
per item `built`, `missing` (no tagged geometry, fewer pieces of its own than its `count`, a decal without an
image texture), `unattached` (one of its pieces is farther from every built `attaches_to` item than its tolerance)
or `rejected` (a reviewer's reason, a placeholder under 5 mm, a face patch of another item, or tags only on hidden
or ghost objects), plus `counts`, `gates` (built per stage),
`done_through` and `complete`. `--plan --md` prints the checklist of a stage without measuring. Run it at the
end of every stage and before every report. `kit.export` writes the facts beside the DFF
(`<stem>.inventory.json`, always for a kit export: it marks the model as authored), so `asset.inventory <project>
--dff <file>` and `asset.check --strict` judge the exported model the same way. The sidecar is checked against
the DFF (its hash, the triangles per frame): an edited or stale sidecar is a problem, never a pass.

**New items found later** are added, never ignored: a reviewer or a region sheet that shows a missing part
adds an item (next free id) and the stage that builds it.

## 3. Detail levels

| Level | The list holds | Use it for |
|---|---|---|
| `simple` | the parts the engine needs and the silhouette items: every part a player sees at a glance, closed and joined | background props, a replacement that must blend into traffic or a district (tier `vanilla`) |
| `standard` | everything the vanilla peers of the class carry, built in the SA language: the class checklist (`coverage` rows) complete | a normal new asset |
| `hero` | `standard` plus the richness a close camera finds: a real interior, engine bay or mechanics, underbody, deeper lamp and grille housings, secondary details on every region, own soft textures | the default for new assets |

`hero` is not "more triangles": it is more ITEMS, each in the same soft language. The per-kind sections below
say what hero means for each kind. The tier (`sa_plus` or `vanilla`) is the style and lint preset; the detail
level is the size of the task.

## 4. Close-up regions

`blender.preview <subject> --regions all` (or `--regions front,wheels`; `--kind K` when the kind is not
found from `asset.json` or the model) writes one close-up sheet per region of the kind
(`preview-NNN-<region>.jpg`, one `NNN` per run) and an index sheet (`preview-NNN-index.jpg`); the answer
lists them in `regions.rows`. The regions and their cameras come from the kind's region file
(`data/kit/regions/<kind>.json`); section 9 names them per kind. A region can hide parts (the doors and the
windscreen for `interior`, the bonnet for `engine_bay`). A region the model has no surface in (the `details` band
of a short prop) is skipped with `preview-NNN-<region>.skipped.txt`; the `lod` region of a map model shows its LOD
model (the IPL LOD of a game model, the LOD DFF beside a file) or says NOT_FOUND; interior cameras stand in every
room (up to six, per storey). Map LODs and large flat ground pieces have region kinds of their own (`lod`,
`terrain`: no street-level cameras).

- When: after G2 (composition: gaps, floating parts, see-through), after G3 (detail: every item visible where
  the inventory says), after G4 (surface: seams, stretch, paint, materials) and at the end.
- How: open each region sheet once, next to the reference board and the inventory items of that region; tick
  every item of the region you can see; note every gap, float, crack, stripe, dark face, stretched texture
  and missing part. One line per region in `notes.md`.
- A region that shows nothing wrong is still reviewed and written down ("underside: ok, floor plate, exhaust
  run, tank, axles present"). A region that cannot be rendered is an open issue, not a pass.

## 5. The leak pass

`look.leak <subject> [--regions names] [--package <dff folder>]` (subject: the exported `.dff`, a SID or
`session:NAME`; in a session the method `look.leak` {`regions`, `views`, `package`}) casts a ray per pixel from
the review cameras of the kind: background seen inside the outline where the model should be closed is a gap.
Gap kinds: `gap` (see-through through a hollow shell, and slits: a lane of background at most 3.5 cm wide
between two parts at about the same depth - a seam, a wing root seen straight down, the gap under a roof slab;
road vehicles leave slits to the arch and inside checks, their rails and bars stand off by design),
`see_through` (a hole that shows the culled back of a shell; the back of a one-sided flat plane seen from behind
is information), `inside` (the inside of a shell seen deep through an opening: a wheel arch without a liner),
`escape` (an interior room open to the void). Each gap has an id, view, region, pixel box, `area_cm2`, an
approximate 3D `point` and the nearest part (`near`). With `--package` (the folder of the exported DFF) it
writes `<package>/checks/<stem>.leak.json` (one per DFF: run it on the model AND on its LOD) with the hash of
that DFF and its `coverage`; satk keeps its own record of every run. `asset.check --strict` blocks on its gaps,
on a file of an older export, on a run that did not cover every leak region of the kind at the default settings
(`--regions`, `--views`, `--cull`, a smaller `--size`), on a file that differs from satk's record, and on a missing
file for an authored asset and its LOD. `blender.preview --passes leak` shows the same as a sheet.

- Windows, grille holes, doorways, wheel openings seen through on purpose and gaps between the spokes of a
  wheel are designed openings: name them in the notes; everything else is fixed.
- Typical gaps: arches without liners, a door shut line that opens into the void, glass that does not meet its
  frame, a roof slab that does not meet the walls, a wing root that does not meet the fuselage, an open hull,
  an interior room whose wall stops short of the ceiling.

## 6. Defect classes

| Defect | How it shows | Found by | Fix |
|---|---|---|---|
| floating part | a gap and a shadow line between a part and its parent | `form.floating`, `unattached` items, region sheets | `mesh.attach` `snap`, move it onto the parent |
| gap, see-through | background or sky visible through the model | `form.see_through`, the leak pass | liner faces, close the opening, `mesh.attach` `weld` or `bridge`, `mesh.flare` |
| crack in a welded shell | a hairline that opens along a seam of one shell | `mesh.crack`, the leak pass | merge the coincident border vertices (`kit.fill` `weld`, `mesh.attach` `weld`) |
| interpenetration | a part buried in another, edges that cut through skin | `form.intersect` | move it out, or cut it to the contact |
| loose body | the body made of many separate loose pieces | `form.loose_share` | rebuild the region from the shell, cut panels from it |
| hard corners off seams | faceted folds inside a panel | `form.hard_corners` | `kit.shade` by seams and named creases |
| wasted density | a uniformly dense, subdivided-smooth mesh: fine triangles on flat or gently curved surface (modern CG, heavy on a map object) | `form.dense_flat` (a defect on map models, advice elsewhere), the wire view | rebuild the named regions with fewer sections, density only where the outline turns, smooth normals for the softness (`construction.md`, Density) |
| CG-clean texture | flat fills between crisp marks; the surface looks drawn, not photographed | `tex.look` rows of the `texture` section (advice: `tex.tone_mid`, `tex.flat_share`, `tex.crisp`), `style.texture --cls` | `texture.finish --photo 0.6` on the 4x paint (quiet variation, no grime); soften the marks (`textures.md`) |
| z-fighting | flickering stripes where two parts share a plane | `mesh.zfight`, region sheets | offset the overlay a few millimetres, or delete the hidden face |
| flipped or backface faces | dark or missing faces, a part that vanishes from one side | `mesh.flipped`, `mesh.inside_out`, `mesh.normals`, the leak pass (`see_through`) | recalculate normals outwards; solidify thin parts that are seen from both sides |
| degenerate or stray geometry | zero-area faces, loose vertices and edges, tiny pieces far from the model | `mesh.degenerate`, `mesh.unused`, `mesh.stray`, `mesh.far` | delete them; merge by distance |
| UV stretch, texel outliers | smeared or pixelated texture on one part | `mesh.uv_smear`, `mesh.texel`, region sheets | re-unwrap, match the texel density of the neighbours |
| texture seam | a colour step along a UV island border | region sheets | move the seam to a hard edge or a material border, paint across it |
| collision mismatch | collision far from the mesh, parts without collision | `col.outside`, `col.uncovered`, preview state `col` | `kit.col` or `col.gen` again from the final mesh |
| misplaced function | lamps, exhaust, hinges, rotors, steering axis or rider contacts off their parts | `fit.wheel_arch`, `fit.dummy`, `fit.hinge`, `fit.steer`, `fit.rider` | refit the frame (`kit.info` `frames: true`, `scene.transform`) |
| asymmetry | the left and right halves of a mirrored design differ | `sym.body`, `sym.part`, `sym.dummy` (blocking for an inventory item counted in pairs) | mirror again from the better side |
| LOD outline lost | the LOD has lost walls or its outline (slabs of a decimated box) | `lod.silhouette` | `kit.lod` again (it keeps the outline), or blocks per volume |
| missing detail | an item of the class or the inventory is not there | `asset.inventory`, `cov.*` rows | build it (the inventory names how) |
| placeholder | a primitive or a recoloured face standing in for a part | the reviewer only | build the real item; it was never `built` |
| engine error | the game will not load or show it correctly | `asset.check` `engine` section, lint | fix the structure (frames, keys, names, formats) |

## 7. The strict check

`asset.check <file, folder or SID> --like <SID> --strict [--project <dir>] [--session <name>]` adds the
verdict field `done` (`true` or `false`) and the list `blocking`: every `engine` error and warning (lint's
`ped.skin` and `weap.flash` included); every `form`, `fit` and `mesh` defect, and every warning beyond the vanilla
range of the class (`data/style/strict.json`; warnings within it go to `advice`: polish them); every required
checklist item `missing` in `coverage`; every required inventory item that is not `built`
(`<project>/design/inventory.json`, found from `--project` - which must exist -, from the folders above the DFF,
from the sidecar `kit.export` wrote, or from the project of `--session`); an authored model (a kit export or a DFF
of a project) without an inventory, or of a project made with `--detail none`; the asymmetry of an item counted in
pairs; and for an authored asset and its LOD the leak file rules of section 5. An authored map model also gets the
strict floating rules: a separate group stands on the ground (at the model's foot) or on the wall the model's main
group stands against, never beside the body. The answer also carries `inventory` (counts), `leak` (file, gaps),
`advice` and, for a LOD, `lod_of`. Reference numbers, `info` rows and optional checklist items never block.
`form.dense_flat` is a defect (blocking) on map models and advice on every other kind; the `texture` section
(`tex.look`: flat/CG-clean, too sharp) is always advice. Polish advice too: it is what a reviewer sees first.

The polish loop after G5: `kit.export` -> `look.leak <dff> --package <dff folder>` -> `asset.check <dff>
--strict` -> fix the first
items of `blocking` (the most visible first) -> regions of the parts you touched -> again. Record each round
(`asset.status --record`). A harness bounds the rounds; a builder without one keeps going until `done: true`
or a tool gap stops it (reported as an open issue).

## 8. The review procedure (critic)

A reviewer (the user, a critic agent or you in review mode) gets: the gate sheet, every region sheet, the
reference board and `<project>/refs/features.md`, the `asset.inventory` report, the `asset.check --strict`
answer and the leak result. In this order:

1. **Inventory.** Every `missing` and `unattached` item is a finding, listed in full (they are the task, not
   suggestions; the round limit of eight fixes does not apply to them). Read every waiver: refuse weak
   reasons (rule 6 of section 1) and turn them back into items. A built item that is a placeholder or
   unfinished is rejected with the reason (`inventory.mark <dir> <id> --reject "<reason>" --by critic`); accept
   it again (`--accept`) once it is fixed.
2. **Strict.** Every `blocking` row is a finding unless the builder named it as a tool gap with evidence.
3. **Regions.** Look at every region sheet: find each inventory item of that region on it (pick at least three
   `built` items at random and verify they are real, finished and attached; one placeholder fails the region),
   then look for gaps, floating parts, stripes, dark faces, stretched textures, seams, hard square forms.
   Verdict per region: pass or fix.
4. **Identity.** The features list against the sheets: each identity feature present, missing or wrong.
5. **Style.** Next to two vanilla peers: soft, rounded, joined, chunky; textures small and soft; clean paint.
   Read the wire view too: density where the outline turns, long triangles on flat and gently curved areas.
6. **Hero.** Does the asset meet the kind's hero definition below, region by region?

**Scores are anchored to vanilla, never to the builder's last round.** Score only what you saw next to the
like model and one more vanilla peer of the class, at the same size, in the same render (game look and wire,
the same camera and light), the textures at native size after DXT1 beside a vanilla texture of the same role:

- **9-10:** you could not pick the asset out of the vanilla lineup: same shape language, density placement
  and texture look.
- **7-8:** reads as SA at once; one or two things a player would notice side by side (a smoother barrel, a
  cleaner paint).
- **5-6:** clearly not vanilla next to the peers: modern CG smoothness, a flat CG-clean paint, boxy forms or
  loose parts.
- **1-4:** the wrong kind of object, a broken form or texture, or failing engine rows.

Caps: any `blocking` row caps `technical` at 6; a `form.dense_flat` row caps `sa_style` at 7 on any kind and
at 5 on a map model; `tex.look` advice on a texture that covers a large part of the model caps `sa_style` at
7; a score of 9 or 10 needs a sentence that names the vanilla peers it was compared with and what matched.
Example: the first atelier bin scored 9/10 everywhere with `geo.tris` 4,062 as a uniformly dense subdivided
barrel (vanilla bins about 100-350, 16 segments around) and a flat CG-clean paint; at the same size next to
`CJ_BIN1` both show at once, and both rows were in the checks this guide asks for.

The verdict is `pass` only when the inventory is complete, `done` is `true` and every region passed. Never pass
"for now", never pass on the builder's description without looking at the sheet. Write each finding as part,
problem, evidence (which sheet or row) and an instruction a modeller can carry out; besides the inventory list,
at most eight fixes per round, most visible first.

## 9. Per-kind definition of done

Each kind lists the items its inventory must cover (standard), the regions a reviewer opens, the kind's own
checks and what hero adds. Construction, frames and function per kind: `kinds.md`; cars and bikes in detail:
`vehicles.md`.

### Car, van, SUV, pickup, truck, bus (automobile)

- **Items:** lower body and glasshouse shell; bonnet, boot or tailgate, every door, both bumpers, windscreen
  (cut panels); side and rear glass; four arches with liners; the wheel (tyre, rim, hub); headlamp and tail
  lamp buckets with lenses on the lamp keys (each side); indicator and reverse lenses; grille with surround;
  plates on recesses; mirrors on stalks (each side); door handles; wipers; exhaust pipe; interior: front seats,
  rear bench, dashboard with gauge hood, steering wheel, door cards, floor, headliner; engine bay walls and
  engine block when the bonnet opens; floor plate, exhaust run and tank under the car; damage states, LOD,
  collision and shadow.
- **Regions:** `front`, `rear`, `left`, `right`, `wheels` (each wheel in its arch), `underside`, `interior`
  (doors and glass hidden), `engine_bay` (bonnet hidden), `roof`; open the boot in a `--states` or manual view.
- **Kind checks:** `fit.wheel_arch`, `fit.dummy` (lamps, exhaust, petrol cap), `fit.hinge` (doors, bonnet,
  boot), `sym.*`; leak: arches, door shut lines, glass edges, floor; open doors show jambs and an inner trim
  panel, never a void; `_dam` inner panels stay behind the skin.
- **Hero:** air cleaner, radiator, battery and hoses; console, gear lever, pedals; parcel shelf; spare tyre
  (SUV boot, pickup bed with liner and inner tailgate); fog lamps, intakes with bars, mouldings, roof rails on
  feet, mud flaps, antenna; suspension and brake parts seen through the arches; own soft interior page.

### Motorbike and scooter (bike)

- **Items:** body parts (tank, seat, tail, side panels; scooter: cowl, floor, leg shield) as joined rounded
  volumes; front forks and front fender (in `forks_front`); handlebars with grips, levers and mirrors; headset
  with speedo; headlamp, tail lamp, indicators; engine and drive (cylinder and cases, or the CVT cover);
  exhaust; swingarm and rear shock; footrests or floorboard; stand; plate; both wheels (tyre, rim, hub, disc).
- **Regions:** `front`, `rear`, `left`, `right`, `cockpit` (seen from the rider), `engine` (engine and drive),
  `wheels`.
- **Kind checks:** `fit.steer` (the steering axis through the headset inside the body), `fit.rider` (seat,
  footrest and grips at the contact points of the same anim group), `fit.dummy`; at full lock nothing on the
  steering leaves the body or cuts another part.
- **Hero:** brake calipers and discs, cables and hoses as sweeps, radiator, inner fairing panels, rack on legs,
  glovebox, horn grille, vents, passenger pegs and grab rail, chain and sprockets.

### Bicycle (bmx)

- **Items:** frame tubes (head, top, down, seat tube, chain and seat stays) meeting at closed joints; fork;
  stem, handlebar and grips (`bargrip`); seat and seat post; chainset (chainring and cranks) on its frame; both
  pedals; chain; both wheels (tyre, rim, spokes or a spoke texture, hub); brakes where the design has them.
- **Regions:** `front`, `rear`, `left`, `right` (the drive side), `cockpit`, `drivetrain` (chainset and
  pedals), `wheels`.
- **Kind checks:** no gaps at tube joints; the steering axis through the head tube (`forks_front`); the chainset
  centred on its frame origin (the bottom bracket) and the pedals clearing the frame and the ground through a
  whole turn; wheels centred in the fork and the dropouts; the rider's seat, grips and pedals at the like
  model's contact points.
- **Hero:** brake levers and cables, pegs, reflectors, valves, chain guard, kickstand, tyre tread and decals in
  the texture.

### Quad and monster truck (quad, mtruck)

- **Items:** as a car body (mtruck) or a bike body (quad), plus the moving suspension parts of the kind:
  quad `suspension_lf`/`suspension_rf` arms and `rear_axle`; mtruck `transmission_f`/`transmission_r` axles and
  the long-travel suspension; seat (and `ped_backseat` on the quad), handlebars or cab, lamps, exhaust.
- **Regions:** quad `front`, `rear`, `left`, `right`, `cockpit`, `wheels` (suspension and axles), `underside`;
  mtruck as a car (`front` ... `engine_bay`, `roof`).
- **Kind checks:** the suspension parts follow their wheels without cutting the body; wheels centred; rider or
  driver contact points as the like model.
- **Hero:** shocks and springs, steering links, skid plates, racks, roll cage, lamp guards.

### Boat

- **Items:** hull closed below the waterline; deck meeting the hull along the sheer line; cockpit with floor and
  sides; windscreen with its frame; seats; console with steering wheel and throttle; engine cover or outboard;
  propellers (`static_prop` and `moving_prop`, pairs for twin engines) with shafts and rudder; rails on
  stanchions; cleats; navigation lights (`boat_lights`); LOD (`boat_vlo`); collision.
- **Regions:** `bow`, `stern` (with the propellers), `left`, `right`, `deck` (from above), `cockpit`,
  `underside` (the hull bottom).
- **Kind checks:** the leak pass from below and from the sides shows no opening in the hull; the deck does not
  float over the hull; the propellers sit behind and below the hull on their frame origins; the driver at
  `ped_frontseat` behind the wheel.
- **Hero:** swim platform and ladder, hatches, fenders, anchor, antenna or radar arch, cushions, gauges, cabin
  interior.

### Helicopter (heli)

- **Items:** fuselage shell (cabin and tail boom in one); canopy glass meeting its frame; doors; main rotor
  (`static_rotor` blades and hub, `moving_rotor` blur disc) on the mast and rotor head; tail rotor
  (`static_rotor2`, `moving_rotor2`); tail fin and stabiliser; skids with struts or wheels; engine cowling with
  intakes and exhausts; cockpit (seats, instrument panel, sticks); lights (`Omni*` frames); LOD; collision.
- **Regions:** `front` (nose and canopy), `cockpit`, `left`, `right`, `tail` (boom and tail rotor), `rotor`
  (rotor head from above), `skids`, `underside`.
- **Kind checks:** each rotor centred on its frame origin and spinning in its plane (main about the vertical
  axis, tail about the side axis); blade tips clear the boom, the fin and the ground; the skids stand on the
  ground plane of the wheel dummies; the canopy has no gap to the frame; doors hinge on their edge.
- **Hero:** rotor head with swash plate and pitch links, antennas, steps, landing light, rear cabin seats, the
  inside of the doors.

### Plane

- **Items:** fuselage shell; both wings with tips; tailplane and fin; control surfaces as their own frames
  (`aileron_l`, `aileron_r`, `elevator` or `elevator_l`/`elevator_r`, `rudder`) each sitting in a matching
  notch of the fixed surface; engines (cowling or nacelle); propellers (`static_prop`, `moving_prop`) or jet intakes and exhausts; landing
  gear (struts and wheels on the wheel dummies); cockpit (seats, panel, yokes); canopy or windows; doors;
  navigation lights (`Omni*` frames at the wing tips and the tail); LOD; collision.
- **Regions:** `nose` (engine and propeller), `cockpit`, `left`, `right`, `wings` (above and below), `tail`,
  `gear`, `underside`.
- **Kind checks:** every control surface hinged at its frame origin on its leading edge, flush at neutral with
  no see-through gap and no overlap; the propeller centred on its frame origin and clear of the ground; the gear
  wheels on the ground; the wing roots closed to the fuselage (the leak pass looks for slits straight down
  and straight up: a wing root seen from an angle hides behind the fuselage).
- **Hero:** flap tracks, pitot, antennas, exhaust stacks, gear doors, wheel spats, steps, cabin seats, markings
  in the texture.

### Trailer

- **Items:** chassis frame; deck or box body; `hookup` at the coupling; landing legs (`misc_a`); axles and the
  wheel; mudguards and mud flaps; rear doors with hinges and locking bars; under-run bar; lamps and reflectors;
  side rails or skirts; cargo variants (`extra*`); LOD; collision.
- **Regions:** `front` (the coupling), `rear`, `left`, `right`, `wheels`, `underside` (the axles); look at
  the deck or cargo on the `left`/`right` sheets.
- **Kind checks:** `hookup` where the vanilla trailer of the same tractor has it; the wheels on the ground; cargo
  extras sit on the deck.
- **Hero:** lashing points, toolbox, spare wheel, air and electric lines, ladder, logos in the texture.

### Train (train)

- **Items:** body shell; cab with windows (locomotives); doors; bogies with their wheel sets on the wheel
  dummies; couplers and buffers at both ends; handrails and steps; underframe equipment; roof equipment (fans,
  horns, pantograph); head and tail lamps; LOD; collision.
- **Regions:** `front`, `rear`, `left`, `right`, `roof`, `bogies` (underframe and bogies); the cab interior
  on the `front` sheet.
- **Kind checks:** the wheel dummies on the rail gauge of the like model; the bogies under their pivots; the
  body clears the bogies; couplers at the like model's height.
- **Hero:** cab controls and seats, walkways, ladders, brake hoses, number boards.

### Street prop, small static object, breakable, animated object (prop, breakable, animated_object)

- **Items:** every closed piece of the object (body, cap, base or feet, lid or door, handles, hinges, signs) and
  its back side; the base meeting the ground; prelight; collision. Breakable: closed pieces that read when they
  fall. Animated: every moving part as its own frame named like the animation's bones.
- **Regions:** `sides` (front, back and both sides), `top`, `base` (the ground contact), `details`.
- **Kind checks:** no floating pieces, pieces touch or push into each other; the base reaches a little below
  the ground so no slope shows a gap; a closed bottom on anything that can fall over; animated parts turn about
  their frame origin without cutting the rest; no `form.dense_flat` (a prop is placed many times: density only
  where the outline turns); textures without look advice.
- **Hero:** bolts, seams, cable or pipe stubs, separate lids and hinges, labels and wear in the texture.

### Building with LOD (building)

- **Items:** the main shell with every wall corner closed; roof slab with overhang and fascia (or a parapet);
  roof equipment (vents, air conditioners, tanks); entrance (door recess, steps, canopy or porch); windows and
  doors (texture, or recessed with frames and sills at hero); ledges and cornices; gutters and downpipes; signs;
  balconies and railings; a foundation skirt into the ground; the LOD model; collision; prelight with night
  colours (lit windows); entrance lamps as 2D effects.
- **Regions:** `facade_north`, `facade_east`, `facade_south`, `facade_west`, `roof`, `entrance`, `lod` (the
  LOD model next to the HD); the base on the facade sheets; the night view in a `--time 22:00` preview.
- **Kind checks:** no gap at the wall corners or under the roof slab (leak pass: holes into the shell and slits
  between parts); no floating sills, signs or air conditioners; no z-fighting facade overlays; the LOD covers
  the HD silhouette (`lod.silhouette`; `kit.lod` keeps the outline) and has its own leak run
  (its own `<stem>.leak.json` in `checks`); draw at most 299 with collision.
- **Hero:** recessed windows with frames and sills, awnings, units on brackets, fire escapes, chimneys, roof
  access, a finished back and service side.

### Interior (interior_shell, interior_prop)

- **Items:** floor, walls and ceiling closed in every room; doorways with frames (or doors that stay shut);
  windows (blinds or an outside texture); skirting and ceiling trim; light fittings; the entry point with free
  space around it; furniture (interior props, each closed and touching the floor); collision with FLOORBOARD or
  CARPET floors.
- **Regions:** interior_shell `overview`, `walls`, `ceiling`, `floor` (cameras inside the rooms);
  interior_prop as a prop (`sides`, `top`, `base`, `details`).
- **Kind checks:** the leak pass from the cameras inside every room (they stand in up to six rooms, per storey)
  shows no opening into the void; no doorway
  into nothing; walk paths free of furniture collision.
- **Hero:** furnished rooms with their fittings (kitchen, bathroom, office), wall decor, lighting baked into the
  prelight.

### Weapon (weapon; melee and thrown: weapon_melee)

- **Items:** receiver or body; barrel; grip; stock (if any); magazine; trigger and guard; front and rear sights,
  or a scope on mounts; muzzle; the `gunflash` atomic at the muzzle; a texture on both sides. A knife, bat,
  katana, club, shovel or grenade is kind `weapon_melee` (modelled along +Z: blade or head, guard or collar, grip,
  pommel, texture; regions `sides`, `blade`, `tip`, `grip`, `ends`); `asset.init` warns when the kind and the
  `--like` model differ.
- **Regions:** `left`, `right`, `top` (the sights), `muzzle`, `grip`; in the ped's hand in a game-look
  preview.
- **Kind checks:** origin and orientation as the vanilla weapon of the slot (the hand holds it at the origin;
  the barrel along the same axis); `gunflash` at the muzzle tip; every part touches the body; length against
  the 1.84 m ped.
- **Hero:** ejection port, bolt or charging handle, rails, sling points, safety, muzzle device, screws in the
  texture.

### Ped skin (ped)

- **Re-skin only:** a ped keeps a vanilla ped's skinned mesh (Skin plugin, 32 bones): `kit.export` refuses peds
  (it cannot write a skin, and an unskinned ped crashes the game; `asset.check` reports `ped.skin`). The starter
  items are regions of the new atlas (face, hair, top garment, sleeves, hands, legs garment, shoes; details and
  shading at standard), each proven by the texture on the faces that show it: import the like ped with the new
  TXD into the session and tag those faces. The reviewer judges the painting.
- **Items:** face, hair, torso garment front and back, arms and hands, legs garment, shoes, painted accessories
  (belt, watch, jewellery), a consistent skin tone.
- **Regions:** `front`, `back`, `left`, `right`, `face`, `feet`.
- **Kind checks:** no texture seams at the UV borders (neck, wrists, waist, ankles), no stretched texels, the
  32-bone skin with at most 4 weights per vertex (lint `ped.skin`).
- **Hero:** photo-like face with painted light, folds and seams painted into the clothing, logos, layered
  garments.

### Pickup

- **Items:** the icon object, closed from every angle, centred on its origin (pickups spin), a small saturated
  texture, prelight, one collision sphere.
- **Regions:** `sides`, `top`, `details`.
- **Kind checks:** centred on the spin axis; no open faces seen while it turns.
- **Hero:** a second material or a modelled rim that reads at small size.

### Vehicle upgrade (vehicle_upgrade)

- **Items:** the part (spoiler, skirt, bonnet scoop, roof scoop, exhaust, bull bar, lamps, wheels), its mounting
  faces, its damage variant when the `ug_*` anchor has one, body colour on the car's body texture where the part
  is painted.
- **Regions:** `sides`, `top`, `mount` (the contact line); the part on the car in a lineup preview.
- **Kind checks:** seated on every car that takes it (`carmods.dat`) at its `ug_*` anchor, without a gap and
  without cutting into the body.
- **Hero:** mounting brackets, end plates, mesh inserts, a finished inside.

## 10. The final report

At the end (and at every pause) report, in this order: the stage reached; `asset.check --strict` (`done`, the
number and the first rows of `blocking`); `asset.inventory` counts (built, missing, unattached, rejected) with
every open item and its reason, and every waiver; the region table (region, sheet, verdict); the sheets and the deliverable
folder; the open issues, each with part, problem and evidence. A report that leads with "done" while any of
these is open is wrong.

Back to the guides: `README.md`; per-kind construction: `kinds.md`.
