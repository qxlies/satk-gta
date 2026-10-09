# `satk style`: checks against vanilla SA, and the vanilla numbers as reference

[Русская версия](../ru/style.md)

Package: `satk.style`.

## What it is

The San Andreas look is described (soft, rounded, simplified forms joined into one body, small soft photo-like
textures, the shared vehicle textures) in the style guides; `satk style` checks what can be checked on a model
file and keeps the vanilla numbers as reference. `satk asset check` reads a model (a `.dff`, a mod folder or a
game model) and answers in eight sections:

- **engine**: the hard rules the engine needs (frames and parents, parts, normals, prelight, collision, TXD
  formats, paint and lamp colour keys, the second UV set for the sheen, wheel size, dummy sides);
- **form**: composition defects with their location: pieces that float, a body made of loose pieces, hard box
  corners, wheel arches you can look through, and wasted density (`form.dense_flat`: fine triangles on flat or
  gently curved surface, the uniformly dense look of a subdivided mesh; a defect on map models, which the game
  places many times, and advice on every other kind; rich detail that turns the outline is never counted);
- **mesh**: bugs of the mesh with their location: cracks (open seams the background shows through), z-fighting
  (coplanar overlapping faces of different pieces or materials), flipped faces and pieces turned inside out,
  zero-area triangles and unused vertices, tiny islands and stray pieces far away, smeared textures and texel
  outliers, collision far outside the mesh or missing near it;
- **fit**: wheels centred in their arches, lamp, exhaust and petrol-cap dummies on their geometry, doors and
  lids hinged at an edge; for bikes the steering parts around the steering axis and the rider's seat, feet and
  grips where the like model has them;
- **symmetry**: left and right parts or dummies that do not mirror each other (information);
- **coverage**: the class checklist of details, the definition of done of the class (lamps, glass, mirrors,
  handles, interior, engine bay, plates, exhaust, lined arches for cars; rotors for helicopters; propeller, control
  surfaces and gear for planes; hookup for trailers; head, hands and feet for peds; prelight and collision for map
  models ...): present or missing;
- **texture**: the look of the model's own textures against the vanilla textures of their role (advice that
  never blocks): `flat/CG-clean` (no fine tonal variation, dead-flat patches) or `too sharp` (crisp marks on a
  flat ground); `satk texture finish --photo` gives a clean paint the quiet variation of a photo, without dirt;
- **reference**: the vanilla numbers of the class (triangles, shading, UVs, sizes) next to the model's. They are
  never a target, a gate or a warning.

The verdict is `fail` on an engine error, `review` on a form, fit or mesh defect (a finding beyond what the vanilla
models of the class show), else `pass`; a warning is a real flaw at the place given that vanilla also has.
`--strict` is the definition of done: it answers `done` (true or false) and `blocking`, the list of everything
still to fix: engine errors and warnings (lint's `ped.skin` and `weap.flash` too), every form, fit and mesh
defect, every warning beyond the vanilla range of the class (`data/style/strict.json`: about nine in ten vanilla
models of a family stay below it, and every kit kind's exemplar passes; warnings within it are listed as `advice`),
required checklist items that are missing, inventory items not built (`<project>/design/inventory.json` of the
asset project, found from `--project` - which must exist -, the folders above the model, the
`<stem>.inventory.json` that `kit export` writes next to the DFF, or the project of `--session`), an authored model
(a kit export or a DFF of a project) without an inventory, the asymmetry of an inventory item counted in pairs, and
light-leak gaps (`<package>/checks/<stem>.leak.json` of a full `look leak --package` run on this very export,
matching satk's own record of the run; required for an authored asset and for its LOD).
An agent is finished only when `done` is true. `satk style texture`
tells whether a texture looks like a vanilla one of its role, `satk style brief-check` finds wording in a task
brief that leads to known mistakes. The game is only read; the cache is written to `<workspace>\work\style\`.

Two detail tiers: `vanilla` (replacements that must blend in) and `sa_plus` (the default for new assets: the
same language at a free detail level). A tier changes only the texture density reference; no tier has a
triangle band.

## Quick example

```powershell
satk asset check model:426
satk asset check model:426 --strict
satk style profile car.sedan --metrics veh.hd_tris shade.normal_bend dims.L
satk asset anatomy model:426 --md
```

What comes back (shortened):

```json
{"ok":true,"cols":["check","part","value","p10","p50","p90","verdict","hint","ref","section"],"rows":[],"cls":"car.sedan","verdict":"pass","sections":{"engine":{"info":1},"form":{},"mesh":{},"fit":{"info":4},"symmetry":{"info":1},"coverage":{"present":13},"reference":{"info":13}},"defects":0,"form":{"pieces":53,"floating_pieces":0,"loose_share":0.191,"crease_share":0.103,"soft_share":0.24,"arches":4}}
{"ok":true,"cols":["check","part","value","p10","p50","p90","verdict","hint","ref","section"],"rows":[],"verdict":"pass","done":true,"blocking":[]}
{"ok":true,"cols":["metric","p10","p50","p90","n","lo","hi"],"rows":[["veh.hd_tris",2032,2207,2338,24,2032,2338],["shade.normal_bend",7.7012,9.9785,13.9871,24,7.7012,13.9871],["dims.L",5.4961,5.815,6.1527,24,5.4961,6.1527]],"peer_set":"car.sedan (24 vanilla models)"}
```

The first call builds the cache (about 10-20 s); later calls answer in a fraction of a second. The default
answer lists only findings (`error`, `warn`, `defect`, `missing`); `--full` adds the information, the checklist
items present and the reference rows.

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk asset check <dff\|folder\|SID> [--like SID] [--cls auto] [--tier] [--md] [--full] [--strict] [--project P] [--session S]` | — (through `satk_op`) | engine, form, mesh, fit, symmetry, coverage and reference sections; `verdict` pass, review or fail; `--strict`: `done` and `blocking` |
| `satk style profile <class or model> [--like SID] [--tier vanilla\|sa_plus] [--metrics ...]` | — | vanilla p10/p50/p90 of the peer set (reference); exemplars; scale anchors; `--classes` lists the classes |
| `satk asset anatomy <dff\|SID> [--md]` | — | frame tree with positions, parts, materials by role, collision and TXD summary |
| `satk style texture <png\|txd\|folder\|tex:SID> [--role auto] [--cls CLASS]` | — | a texture against the vanilla textures of its role (interior, wheel, decal, body, ped, weapon, wall, ground, prop; `--cls` lets the model's class pick it), with look advice: `flat/CG-clean` or `too sharp` |
| `satk style brief-check <brief.md>` | — | wording in a task brief that leads to known mistakes: box words ("boxy", "slab", "squared-off"), "style = numbers", triangle numbers, "low-poly", pixel-measured photos, painted grime, "crisp", "flat colour" |
| `satk style card <class> [--md]`, `satk style card --lint-preset` | — | a Markdown table of the vanilla numbers of a class; a lint config with the shading hints |
| `satk style build [--force] [--validate] [--textures]` | — | build the cache now; `--validate` measures how tight each peer set is |

Classes: vehicles by type (`car`, `bike`, `bmx`, `quad`, `mtruck`, `boat`, `plane`, `heli`, `trailer`, `train`)
and, for cars, by body (`car.sedan`, `car.coupe_muscle`, `car.sports`, `car.suv_pickup`, `car.van`, ...); map
models by kind and size (`prop@1-2m`, `building@16-32m`, `terrain`, `vegetation`, `interior_prop`, `lod`, ...);
`ped`, `weapon`, `pickup`, `upgrade`. A model id (`model:426`) or name (`premier`) means "the class of this model".

## How it works

- **form** (`satk.style.form`): the model is cut into welded pieces. Pieces touch when a vertex lies within
  6 mm of another piece's face or their faces cross; the body shell is the ground. A group of pieces with no
  path to the ground is `form.floating` (part, gap in mm, centre, size). Vanilla hovers that are allowed: flat
  cards (plates, gauges, the steering card), glass behind its frame, small heads (a mirror head on a stalk),
  tiny bits, opening panels at their shut line, parts the game animates, and fill hidden from all six axis
  directions (an air cleaner under the bonnet). `form.loose_share`: more than 60 % of the body outside its
  largest welded piece. `form.hard_corners`: folds of 60-100° whose normals are split without a material or UV
  seam, when they are more than 20 % of the folds and fewer than 8 % of the folds stay smooth. `form.see_through`:
  rays from the side into an arch above the tyre that find no surface facing them in the near half of the car.
  `form.intersect` (pieces buried deep in another) is information: vanilla buries engines and arms too.
- **fit** (`satk.style.fit`): the arch outline from the side is centred on the wheel within a quarter of its
  radius; a lamp dummy seen head-on sits on a face of its lamp key colour (within 100 mm); a door, bonnet or boot
  dummy sits on an edge of its panel; for bikes `handlebars` and `forks_front` sit on the steering axis (the local
  Z of `forks_front`) no more than 40 mm farther off than on the like model, and the rider's seat, foot rest
  and grips are within 60 mm of the like model's (the rider is a fixed animation at `ped_frontseat`).
- **Every class has its own rules** (`data/style/form.json` `classes`, merged from the family to the class): road
  cars and motorbikes are strict (every floating piece, loose body, box corner or see-through arch is a defect;
  vanilla has none). For the other classes the vanilla construction patterns are allowed instead of the check
  being switched off: map models stand on the terrain (a group with nothing of the model under it) and props and
  interiors hang on walls and ceilings of other models (a group at the model's outer box); alpha cards (foliage,
  fences, wires) and decal cards; upgrades, pickups and LODs are separate components (only near misses float);
  extras and static/moving rotors are shown one at a time; aircraft and train doors do not hinge at an edge;
  flaps, rudders and props are moving parts wherever the word is in the name. A finding beyond the class's vanilla
  range is a defect, a smaller one a warning. An authored map model gets stricter rules (`authored` in
  `data/style/form.json`): a separate group stands on the ground only at the model's foot, and hangs on a wall only
  where the model's main group stands against the same side.
- **LOD outline** (`lod.silhouette`, form section): a map model with a LOD (the IPL LOD, or the LOD DFF beside the
  file) is compared with it from the side, the front and above; a LOD that covers less than 65 % of the HD outline
  is a warning (calibrated on 56 vanilla buildings), and `--strict` blocks one under 40 % (walls lost); flat ground
  pieces are not compared.
- **mesh** (`satk.style.defects`): a crack is an open seam between two borders of one part (1-25 mm across,
  in one surface) with no face looking back behind it; z-fighting is an overlap of faces closer
  than 1 mm that show different textures, colours or UVs (alpha decals are drawn after the opaque faces and do
  not fight); a face wound against its neighbours across a smooth edge is flipped (the IDE flag that turns
  backface culling off exempts the model); a closed piece with a negative volume is inside out (interior shells
  are seen from inside); UV triangles under 2 pixels sample one colour and are not judged.
- Calibration: every vanilla model of SA 1.0 US (14,790, every class) passes with 0 defects; warnings (real flaws
  vanilla has, counted per class in `data/style/defects.json` `calibration`) are listed for the polish. The
  checklists `data/style/coverage/<class>.json` are generated from the vanilla models of each class: an item
  every one of them has (95 % for classes of 20 and more) is required, one at least a quarter of them have is
  optional. Limits: `data/style/form.json`, `data/style/defects.json`.
- The studio step stats (`blender call`) run the form checks and the cheap mesh checks (zero-area triangles, tiny
  islands, flipped faces) on the changed objects after every step: `stats.form` and `stats.mesh`; counts and
  sizes there are plain numbers.
- `asset check` of a file named like a vanilla model (`premier.dff`) compares it with that model (a
  replacement; fit uses its rider pose and steering); `--like` and `--cls` choose explicitly; `asset.json` next to
  the file can set `tier` and `like`.

## Limitations and known issues

- A floating group of a vanilla map model with nothing of the model under it counts as standing on the terrain
  (vanilla pieces rest on neighbouring models): a lamp head 30 cm above its own pole is found, a sign floating
  beside a wall is not. An authored model is held to the stricter rules above. Vanilla near misses are warnings;
  `--strict` blocks the ones beyond the vanilla range.
- The crack and leak checks see open seams in one part; gaps between parts are the light-leak render's job
  (`look.leak`).
- `form.intersect` and the `symmetry` section never change the verdict (vanilla does both on purpose).
- The coverage detectors are simple (texture names, key colours, regions, frames, pieces): a detail modelled in an
  unusual way may be reported missing; mirrors and glass are found anywhere (a separate part, the doors or the
  body).
- The class of a model without an IDE line or a vanilla name is a guess (frames and size); pass `--cls` or
  `--like` for an exact comparison.

## Python API (if other packages use it)

```python
from satk.style import api
api.check("mymod/premier.dff")[0]["verdict"]                # "pass" | "review" | "fail"
api.profile("car.sedan", "vanilla")["shade.normal_bend"]   # {'p10': 7.7, 'p50': 9.98, 'p90': 14.0, ...}
from satk.style.form import analyse                        # composition checks, also inside Blender
from satk.style.defects import analyse                     # mesh defects (cracks, z-fighting, flipped, ...)
from satk.style.metrics import mesh_metrics                # canonical mesh metrics, also inside Blender
```
