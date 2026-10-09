# Studio: a live Blender session for step-by-step modelling

[Русская версия](../ru/studio.md)

Package: `satk.studio` (MIT) and `blender/satk_blender/studio` (GPL-3.0-or-later).

## What it is

An AI assistant models inside Blender one step at a time instead of typing coordinates into a script. A
session is a headless Blender 5.1 that stays open; every step is one *method* (`mesh.loft`, `modifier.add`,
`ref.plane`, ...) and its answer comes back in milliseconds with the numbers that matter: what changed,
triangles and vertices, sizes, the composition defects of the changed objects (floating or buried pieces,
see-through arches, ...) and, on request, a small JPEG snapshot. The same methods work for every asset kind:
vehicles, props, buildings, interiors, weapons, peds, pickups and upgrades. An *asset project* keeps the work
resumable: its journal, checkpoints, snapshots, reference photos and decisions live in one folder under
`<workspace>\work\assets\`. The game and your own Blender profile are never touched.

## Quick example

```powershell
satk asset init docs-bench --kind prop --dims 1.8,0.6,0.9 --force
satk blender session start --project docs-bench --no-resume
satk blender call scene.clear --session docs-bench
satk blender call mesh.primitive --session docs-bench --params '{"kind":"cube","size":[1.6,0.12,0.04],"name":"slat"}'
satk blender call modifier.add --session docs-bench --params '{"object":"slat","type":"ARRAY","settings":{"count":4,"relative":[0,1.25,0]}}' --snapshot sheet
satk asset status docs-bench
satk blender session stop --name docs-bench
```

What the fifth command returns (shortened):

```json
{"ok":true,"method":"modifier.add","n":4,"result":{"added":["slat:array"]},"changed":["slat"],
 "stats":{"scene":{"objects":1,"tris":48,"verts":32,"dims":[1.6,0.57,0.04],"vs_target":[0.889,0.95,0.044]},
          "objects":{"slat":{"tris":48,"verts":32,"dims":[1.6,0.57,0.04],"geo.pieces":4,"geo.open_edges":0}}},
 "snapshot":".../work/assets/docs-bench/snaps/0004-sheet.jpg","ms":61.3,"session":"docs-bench"}
```

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk asset init <name> --kind K [--intent replace\|add] [--like SID] [--tier vanilla\|sa_plus] [--target sp\|mta\|samp] [--dims W,L,H] [--detail simple\|standard\|hero\|none]` | — (`satk_op`) | start an asset project (`asset.json`; `sa_plus` is the default tier) with the starter inventory of its kind (`<project>/design/inventory.json`, [inventory.md](inventory.md)) |
| `satk asset status <name> [--record JSON] [--sheet]` | — (`satk_op`) | resume card (at most 1 KB); record a decision, gate, issue or result |
| `satk ref import <photo> [--project P] [--view side\|front\|rear\|top\|3q\|detail] [--max-px N] [--grid] [--grid-step N]` | — (`satk_op`) | reference photo: EXIF rotation applied and metadata stripped, at most 1,600 px; its view is kept next to it (side, front, rear, top = a true elevation for `ref.plane`; 3q and detail are described, never measured); a labelled pixel-grid copy only with `--grid` |
| `satk ref board [--project P] [--images F …] [--labels L …] [--cols N]` | — (`satk_op`) | one labelled JPEG (about 1 megapixel) of 1-12 reference photos (default: every reference of the project) for a review round |
| `satk blender session start [--name N] [--project P] [--blend F] [--gui] [--threads N] [--no-resume]` | — (`satk_op`) | start a session (or reuse the running one) |
| `satk blender session status\|list\|stop\|restore\|replay\|prune [--name N] [--ref R] [--source S]` | — (`satk_op`) | state; load a checkpoint; rebuild a journal; clean up |
| `satk blender call <method> [--params JSON] [--params-file F] [--session N] [--snapshot VIEW\|sheet] [--look L] [--checkpoint\|--no-checkpoint] [--save F] [--timeout S]` | — (`satk_op`) | one step; `batch` runs a list of steps; `--params-file` (`params_file` in `satk_op`) reads the parameters from a JSON file |
| `satk blender methods [--query WORD]` | — (`satk_op`) | the methods of a session; an exact name (`mesh.lathe`) gives its parameter table and full help |

Every method with its parameters and defaults, one line each: [studio-methods.md](../agent/studio-methods.md)
(generated from the plug-in sources by `satk dev gen-docs`). A parameter needed only in some cases says when
(`segments (required if kind not plane|grid|cube|ico_sphere)`, `distance (required if with points)`).

## Methods

| Family | Methods |
|---|---|
| `scene.*` | `info`, `object`, `stats` (numbers now; `file` for every row), `clear`, `delete`, `rename`, `parent`, `transform` (`apply`), `duplicate` (`mirror` x/y/z), `join`, `empty`, `collection`, `origin`, `props`, `visible`, `tag` (inventory item ids on objects or faces) and `items` (their facts for `asset inventory`, [inventory.md](inventory.md)) |
| `mesh.*` | `primitive` (round shapes need `segments`; `rounded_box` and `capsule` are smooth), `loft` (sections along an axis: points or a rounded `shape`, `steps` between them, `half` with a MIRROR modifier, `parts` for the kit), `sweep` (a profile along a path), `lathe`, `curve`, `convert`, `extrude`, `inset`, `transform` (soft `falloff`), `relax`, `deform` (bend, taper, twist, cast, lattice), `attach` (snap, weld or bridge a part onto its parent), `flare` (a wheel arch with a band, a return and a liner), `bevel`, `subdivide`, `delete`, `normals`, `loopcut` (cuts at exact positions), `bisect`, `symmetrize`, `bridge`, `merge`, `dissolve` (planar by default), `mark` (sharp or seam by angle, material or UV borders), `group`, `info` |
| `modifier.*` | `add`, `set`, `apply`, `remove`, `list` for MIRROR, SUBSURF, BEVEL, SOLIDIFY, SHRINKWRAP, WEIGHTED_NORMAL, DECIMATE, TRIANGULATE, ARRAY, CURVE, WELD and SMOOTH_BY_ANGLE (whitelisted settings, angles in degrees) |
| `material.*` | `create` (colour 0-255, alpha, texture image; `preset` from the kit), `assign` (whole object or a face selection), `set`, `list` |
| `uv.*` | `unwrap` (smart, seams, cube, cylinder, sphere, planar), `fit` (into an atlas rectangle), `layers` (a second UV map), `texel` (px/m) |
| `camera.*`, `ref.*` | named cameras (`add` by view or `location` + `target`, `views`, `list`); `ref.plane` puts a TRUE elevation photo (`view` front, rear, side/left, right or top) behind the model with a matched camera `ref_cam_<view>`, scaled by `length` (the object's real length: the object is found against the photo's border colour, or `span` [x0, x1] px), by `width` (the whole photo) or by two `points` and their `distance`; `ref.list` |
| `kit.*`, `look.*` | the kit plug-ins ([kit.md](kit.md)): `template`, `blank`, `blank_split`, `fill`, `wheel`, `shade`, `vlo`, `damage`, `col`, `export`, ...; the look plug-ins ([look.md](look.md)): `apply`, `restore`, `render` (views in a look; `colors` = paint colours), `preview`, `silhouette` (the outline against a true side, front or rear photo: information, never a gate) |
| `io.*`, `shade.basic`, `python` | import a DFF; `io.ghost` brings a vanilla model as a reference that is never counted or exported; quick smooth/flat shading; Python as a journaled escape hatch |
| `session.*` | `checkpoint` (`tag` such as G1: never pruned), `restore`, `checkpoints`, `prune`, `journal` |

Mesh edits take a face selector `select`: `{"side": "+z"}`, `{"normal": [0, 0.7, 0.7], "within": 20}`,
`{"where": ["z>0.4", "y<-1.2"]}`, `{"box": [[x0, y0, z0], [x1, y1, z1]]}`, `{"group": "roof"}`,
`{"material": "glass"}`, `{"near": {"point": [x, y, z], "radius": 0.2}}` (face centres within the radius),
`{"loop": {"point": [x, y, z], "dir": "z"}}` (the edge loop through the edge nearest the point; `"ring": true`
takes the strip of quads across it), `{"grow": 1}` (add rings of neighbours), `{"linked": true}` (then the whole
welded pieces), `{"item": "I05"}` (faces tagged with an inventory item); the keys combine and work in object
space. Vertex edits (`mesh.transform`, `mesh.relax`) move only the loop's own vertices. `save_group` names the new
faces, so the next step can select them by name instead of by coordinates. Vehicles face +Y, Z is up.

## Soft forms

Bodies are built from authored rounded sections and profiles, not from boxes moved as rigid slabs.

- **`mesh.loft` with shapes.** A section is `{"at": y, "shape": {...}}`: `w` half width, `h` height, `z` bottom,
  `exp` (or `exp_top`/`exp_bottom`) superellipse exponent (2 round, 3-5 a soft box), `mid` belt height as a share
  of `h`, `crown` (m, a crowned roof), `tumble` (roof width / belt width: the greenhouse leans in), `shoulder`
  (m, a beltline ledge under the glass), `flat_bottom`. Corners get the vertices (`spacing: turn`); the belt line
  and both centre lines are always vertices. `steps` adds rings between sections through a monotone spline of the
  shape (`interp: smooth`); a section with `"interp": "linear"` keeps the gap after it planar (a windscreen).
  `half: true` builds only x >= 0 with a MIRROR modifier. `parts` writes the face attribute `satk_part` that
  `kit.blank_split` cuts along (`angle`: 0 = faces looking up, 90 = sideways, 180 = down; `model` names the kit
  model). Shape lofts are smooth-shaded; lofts of raw `points` stay flat unless `smooth: true`, and `mesh.lathe`
  is flat until `shade.basic` or `kit.shade`.
- **`mesh.sweep`** runs a profile (points or a shape) along a path (points or a curve object): wrap-around
  bumpers (`half: true` starts on the mirror plane), mouldings, sills, rails, pipes; `scale` and `twist` change
  along the path.
- **`mesh.transform` + `falloff`** bends the surface around a selection (`radius`, `curve`, `connected`);
  `mesh.relax` evens out lumps; `mesh.deform` bends, tapers, twists or lattice-deforms.
- **`mesh.attach`** puts a part on its parent: `snap` (contact vertices onto the surface; `rigid` moves the whole
  part first), `weld` (join the part into `to` and merge the open borders: two shells become one), `bridge` (join it
  into `to` and fill the gap between the open borders); after `weld` and `bridge` the part object is gone. Code that
  joins meshes itself can call `weld_boundaries(obj, dist)` from `satk_blender.studio.methods.attach`.
- **`mesh.flare`** cuts a round wheel arch into the side faces (`arch` {`center`, `radius`} with `segments`
  over the top, which it then needs, or `select` = the opening), raises a welded band (`width`, `out`, `chamfer`;
  `out: 0` is a plain sedan cut), rolls the rim inward (`lip` = the depth of the return) and closes the wheel
  house with a liner (`depth`, `liner_material`).

A car body half (shortened: sections at the tail, the roof and the nose; an arch; a bumper; a mirror):

```json
{"steps": [
 {"method": "mesh.loft", "params": {"name": "body", "samples": 32, "half": true, "steps": 2, "sections": [
   {"at": -2.6, "shape": {"w": 0.92, "h": 0.56, "z": 0.34, "exp": 3.5, "mid": 0.75}},
   {"at": -1.55, "interp": "linear", "shape": {"w": 1.03, "h": 0.74, "z": 0.28, "exp_top": 4, "exp_bottom": 4.5, "mid": 0.91, "crown": 0.03, "flat_bottom": true}},
   {"at": -0.95, "shape": {"w": 1.03, "h": 1.22, "z": 0.28, "exp_top": 5, "exp_bottom": 4.5, "mid": 0.565, "crown": 0.06, "tumble": 0.78, "shoulder": 0.05, "flat_bottom": true}},
   {"at": 0.25, "interp": "linear", "shape": {"w": 1.03, "h": 1.22, "z": 0.28, "exp_top": 5, "exp_bottom": 4.5, "mid": 0.565, "crown": 0.06, "tumble": 0.78, "shoulder": 0.05, "flat_bottom": true}},
   {"at": 0.95, "shape": {"w": 1.03, "h": 0.75, "z": 0.28, "exp_top": 4, "exp_bottom": 4.5, "mid": 0.9, "crown": 0.035, "flat_bottom": true}},
   {"at": 2.62, "shape": {"w": 0.91, "h": 0.52, "z": 0.34, "exp": 3.5, "mid": 0.72}}]}},
 {"method": "mesh.flare", "params": {"object": "body", "arch": {"center": [1.0, 1.65, 0.35], "radius": 0.44},
   "segments": 7, "width": 0.05, "out": 0.02, "liner_material": "liner"}},
 {"method": "mesh.sweep", "params": {"name": "bumper", "half": true, "samples": 16, "profile": {"w": 0.09, "h": 0.2, "exp": 3},
   "path": [[0, 2.66, 0.42], [0.72, 2.62, 0.42], [0.93, 2.32, 0.42], [0.95, 2.13, 0.42]]}},
 {"method": "mesh.primitive", "params": {"kind": "rounded_box", "name": "mirror", "size": [0.14, 0.05, 0.04],
   "radius": 0.015, "segments": 2, "location": [1.13, 0.8, 1.03]}},
 {"method": "mesh.attach", "params": {"object": "mirror", "to": "body", "select": {"where": ["x<-0.06"]}, "rigid": true,
   "max_dist": 0.12}}]}
```

Save it as `car.json` and run `satk blender call batch --params-file car.json --snapshot 3q`.

## Snapshots

`--snapshot 3q|front|rear|left|right|top`, a camera name, or `sheet` (a 2x2 sheet of `3q`, `front`, `left`,
`top`); a JSON object (or `--look`) adds `look` (`clay` with a thin wire overlay by default, `raw` for
materials and textures, `wire`, and `game`: the SA game look of [look.md](look.md) with a ground and noon
light, showing what the game draws up close, so no damage parts, low LODs, collision or ghosts; about
0.3-1 s), `ghost` (`overlay`, `lineup`, `hide`), `refs` and `ref_alpha` (how strongly the model is drawn over
a reference photo). Snapshots are JPEG, 512 px by default, typically 10-40 KB; open them only when you need
to look.

## How it works

- `session start` launches Blender with satk's isolated profile and no window (`--gui` opens one); Blender
  opens a local SAAP endpoint (role `blender`, capabilities `core` + `author`, [saap.md](saap.md)) and writes
  `work\run\endpoints\blender-<name>.json`. Several named sessions can run side by side.
- Requests run on Blender's main thread, one at a time. Numbers are taken from the evaluated meshes
  (modifiers applied), as an export would see them; modifiers stay live until export. A reply stays under
  1.5 KB (a batch gets about 100 bytes more per step, at most 3 KB: every step keeps its short result
  values, such as the object name and triangle count; `python` returns up to 4,000 characters of its `result`
  and writes a longer one, or any one with `out`, to a JSON file); a warm step takes about 5-20 ms, a 512 px
  snapshot about 50-100 ms.
- The step stats are plain numbers, never judged against a band: counts and sizes of the changed objects,
  topology (`geo.pieces`, `geo.open_edges`, ...), `scene.dims` [W, L, H] measured like the vanilla reference
  (high-detail geometry only, the width from the body without mirrors) and, with a project, `vs_target` (the ratio
  to its target size). When the changed objects have composition defects a `form` block lists each with its
  place: `floating` (part, gap in mm, centre), `intersect` (pieces deep inside others), `loose_share` (body faces
  outside its largest welded piece), `hard_corners` (60-100 deg folds marked hard off any seam) and
  `see_through` (arches without a liner), plus one `FORM:` warning. The shading numbers (`shade.normal_bend`,
  `shade.flat_share`, `dff.verts_per_tri`) come from `scene.stats` on request and from the `reference` section of
  `asset check`: information, never a target.
- Every step has a time budget (`--timeout`, 90 s by default for `blender call`): a method that runs longer is
  stopped and answered `TIMEOUT`, and the session stays usable. When Blender is stuck inside its own code and
  cannot be stopped, the session still answers `TIMEOUT` a few seconds after the budget, then `BUSY` to new
  steps until it is free; `session status` shows the step under `busy`, and `session stop` ends a stuck
  session in about 3 s. A session that died is reported as `EXTERNAL_TOOL` with the path of Blender's crash
  report and the command that resumes it.
- After every method the session puts back the original materials wherever a method stored a depsgraph copy
  (a mesh built from an evaluated mesh); such a pointer used to crash or hang the next preview.
- With `--project` the journal (`journal.jsonl`, every step with its parameters, failed ones too),
  `checkpoints` (a `.blend` copy every 5 mutating steps, once after every `batch` with two or more mutating
  steps, after every `python` step and for every gate tag; `--checkpoint` forces one, `--no-checkpoint`
  skips it; the last 10 untagged ones are kept), `snaps` and `refs` live in the project folder. A new session
  of the project opens its newest checkpoint; a session saves one when it stops.
- `session replay --source <project> --name <other>` rebuilds the scene from the journal in another session
  (checkpoint loads where the journal restored one or a person edited the scene in the Blender window).
- `asset.json` records the kind, intent, like model, platform, tier, target size, gates G0-G5, decisions,
  issues and the last check and export; `asset status` turns it into a short resume card. A gate's state is
  `open`, `review` (handed in by the builder), `done` (refused while the gate's required inventory items are not
  built) or `skipped` (with a `why`, shown on the card). `asset init --like` must name a model of the kind's family
  (a pickup truck is not the collectible `pickup`); for a ped the target size stands on +Z.
  `<project>/design/inventory.json` is the asset's task list (`--detail` picks the starter level; `none` makes no
  inventory, and `asset check --strict` never calls such an asset done; [inventory.md](inventory.md)).
- A session ends with `stop`, after `--idle` seconds without requests (one hour by default) or when its
  owner process exits; `stop` only ever terminates the Blender process it started itself.
- New methods are plug-ins: a module with a `METHODS` table under `satk_blender.studio.methods` (or the `kit`
  and `look` packages) is found when a session starts.

## Limitations and known issues

- `--save`, snapshots and checkpoints write only inside the work folder; projects live there too.
- Undo is not available in a headless Blender; use checkpoints (`session restore --ref <step|tag|last>`).
- The `form` block needs the style package (`satk.style`); scenes above 150,000 triangles skip it (run
  `asset check` on the export instead).
- A method stuck inside Blender's own code (a render, a modifier evaluation) cannot be interrupted; the session
  answers and reports it, but only `session stop` + `session start` ends it.
- Edits made by hand in a `--gui` window are journaled as one `external` step with a checkpoint; a replay
  loads that checkpoint instead of repeating the edit.

## Python API (if other packages use it)

```python
from satk.studio import api
api.call("mesh.primitive", {"kind": "cube", "size": [1, 2, 0.5], "name": "crate"}, session="docs", snapshot="3q")
api.call("batch", {"steps": [{"method": "scene.empty", "params": {"name": "wheel_lf_dummy"}}]}, session="docs")
```
