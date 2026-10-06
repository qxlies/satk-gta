# Studio: a live Blender session for step-by-step modelling

[Русская версия](../ru/studio.md)

Package: `satk.studio` (MIT) and `blender/satk_blender/studio` (GPL-3.0-or-later).

## What it is

An AI assistant models inside Blender one step at a time instead of typing coordinates into a script. A
session is a headless Blender 5.1 that stays open; every step is one *method* (`mesh.loft`, `modifier.add`,
`ref.plane`, ...) and its answer comes back in milliseconds with the numbers that matter: what changed,
triangles and vertices, sizes, the shading numbers of the style metrics (`shade.normal_bend`,
`shade.flat_share`, ...) and, on request, a small JPEG snapshot. The same methods work for every asset kind:
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
 "stats":{"scene":{"objects":1,"tris":48,"verts":32,"dims":[1.6,0.57,0.04]},
          "objects":{"slat":{"tris":48,"verts":32,"shade.normal_bend":0.0,"shade.flat_share":1.0,"geo.pieces":4}}},
 "snapshot":".../work/assets/docs-bench/snaps/0004-sheet.jpg","ms":61.3,"session":"docs-bench"}
```

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk asset init <name> --kind K [--intent replace\|add] [--like SID] [--tier vanilla\|sa_plus] [--target sp\|mta\|samp] [--dims W,L,H]` | — (`satk_op`) | start an asset project (`asset.json`; `sa_plus` is the default tier) |
| `satk asset status <name> [--record JSON] [--sheet]` | — (`satk_op`) | resume card (at most 1 KB); record a decision, gate, issue or result |
| `satk ref import <photo> [--project P]` | — (`satk_op`) | reference photo: no EXIF, at most 1,600 px, plus a pixel-grid copy |
| `satk blender session start [--name N] [--project P] [--blend F] [--gui] [--threads N] [--no-resume]` | — (`satk_op`) | start a session (or reuse the running one) |
| `satk blender session status\|list\|stop\|restore\|replay\|prune [--name N] [--ref R] [--source S]` | — (`satk_op`) | state; load a checkpoint; rebuild a journal; clean up |
| `satk blender call <method> [--params JSON] [--session N] [--snapshot VIEW\|sheet] [--look L] [--checkpoint\|--no-checkpoint] [--save F] [--timeout S]` | — (`satk_op`) | one step; `batch` runs a list of steps |
| `satk blender methods [--query WORD]` | — (`satk_op`) | the methods of a session; an exact name (`mesh.lathe`) gives its parameter table and full help |

Every method with its parameters and defaults, one line each: [studio-methods.md](../agent/studio-methods.md)
(generated from the plug-in sources by `satk dev gen-docs`).

## Methods

| Family | Methods |
|---|---|
| `scene.*` | `info`, `object`, `stats` (numbers now; `file` for every row), `clear`, `delete`, `rename`, `parent`, `transform` (`apply`), `duplicate` (`mirror` x/y/z), `join`, `empty`, `collection`, `origin`, `props`, `visible` |
| `mesh.*` | `primitive` (round shapes need `segments`), `loft` (sections along an axis, `mirror` halves), `lathe`, `curve`, `convert`, `extrude`, `inset`, `transform`, `bevel`, `subdivide`, `delete`, `normals`, `loopcut` (cuts at exact positions), `bisect`, `symmetrize`, `bridge`, `merge`, `dissolve` (planar by default), `mark` (sharp or seam by angle, material or UV borders), `group`, `info` |
| `modifier.*` | `add`, `set`, `apply`, `remove`, `list` for MIRROR, SUBSURF, BEVEL, SOLIDIFY, SHRINKWRAP, WEIGHTED_NORMAL, DECIMATE, TRIANGULATE, ARRAY, CURVE, WELD and SMOOTH_BY_ANGLE (whitelisted settings, angles in degrees) |
| `material.*` | `create` (colour 0-255, alpha, texture image; `preset` from the kit), `assign` (whole object or a face selection), `set`, `list` |
| `uv.*` | `unwrap` (smart, seams, cube, cylinder, sphere, planar), `fit` (into an atlas rectangle), `layers` (a second UV map), `texel` (px/m) |
| `camera.*`, `ref.*` | named cameras (`add`, `views`, `list`); `ref.plane` puts a photo behind the model with a matched camera `ref_cam_<view>`; `ref.list` |
| `io.*`, `shade.basic`, `python` | import a DFF; `io.ghost` brings a vanilla model as a reference that is never counted or exported; quick smooth/flat shading; Python as a journaled escape hatch |
| `session.*` | `checkpoint` (`tag` such as G1: never pruned), `restore`, `checkpoints`, `prune`, `journal` |

Mesh edits take a face selector `select`: `{"side": "+z"}`, `{"normal": [0, 0.7, 0.7], "within": 20}`,
`{"where": ["z>0.4", "y<-1.2"]}`, `{"box": [[x0, y0, z0], [x1, y1, z1]]}`, `{"group": "roof"}`,
`{"material": "glass"}`; the keys combine and work in object space. `save_group` names the new faces, so the
next step can select them by name instead of by coordinates. Vehicles face +Y, Z is up.

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
  values, such as the object name and triangle count); a warm step takes about 5-20 ms, a 512 px snapshot
  about 50-100 ms.
- With a project the step stats judge the numbers against the project's tier band (`sa_plus` or `vanilla`
  of its peer set) with the same function as `asset check`: a value between the band and its fence is listed
  under `edge`, one beyond it under `out`, for example `"edge": ["geo.tris>562"]` (the band edge it crossed).
  Low LODs (`*_vlo`, a building's LOD slot) are not judged against the bands of the HD model.
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
  issues and the last check and export; `asset status` turns it into a short resume card.
- A session ends with `stop`, after `--idle` seconds without requests (one hour by default) or when its
  owner process exits; `stop` only ever terminates the Blender process it started itself.
- New methods are plug-ins: a module with a `METHODS` table under `satk_blender.studio.methods` (or the `kit`
  and `look` packages) is found when a session starts.

## Limitations and known issues

- `--save`, snapshots and checkpoints write only inside the work folder; projects live there too.
- Undo is not available in a headless Blender; use checkpoints (`session restore --ref <step|tag|last>`).
- Class bands in the step stats need the vanilla index (the style cache is built from it); without one the
  stats give the raw numbers and the ratio to the project's target size.
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
