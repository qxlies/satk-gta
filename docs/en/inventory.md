# Inventory: the task list of an asset, checked against the model

[Русская версия](../ru/inventory.md)

Package: `satk.inventory` (MIT); the tagging methods live in `blender/satk_blender/studio/methods/tag.py` (GPL).

## What it is

A model is only finished when every part it should have is there and joined to the rest. The *inventory* of an
asset project (`<project>/design/inventory.json`) lists those parts as items: an id (`I01`), a name, the review region, a
category, how the part is built, what it attaches to and the gate that builds it (G1 form, G2 compose, G3 detail,
G4 surface). While modelling, the builder tags the geometry with the item ids (`scene.tag`); `satk asset
inventory` then says for every item whether it is **built**, **missing**, **unattached** (built, but standing off
its parent) or **rejected** (by a reviewer, or tagged on nothing real), measured in the live Blender session or
read from an exported model. Every asset kind has a starter list at three levels: `simple` (what every stock
model of the kind has), `standard` (a full stock-class model) and `hero` (richer than stock: engine bay, full
interior and underbody for a car; roof clutter, signage and finished back facades for a building; separate sights
and rails for a weapon).

## Quick example

```powershell
satk asset init docs-bin --kind prop --detail simple --force
satk inventory validate docs-bin
satk asset inventory docs-bin --plan --md
satk blender session start --project docs-bin --no-resume
satk blender call scene.clear --session docs-bin
satk blender call mesh.primitive --session docs-bin --params '{"kind":"cylinder","segments":8,"radius":0.3,"depth":0.9,"location":[0,0,0.45],"name":"bin"}'
satk blender call scene.tag --session docs-bin --params '{"object":"bin","item":"I01"}'
satk asset inventory docs-bin --session docs-bin
satk blender session stop --name docs-bin
```

What the eighth command returns (shortened):

```json
{"ok":true,"cols":["id","status","name","stage","region","note"],
 "rows":[["I02","missing","Base or feet","G1","base","no geometry carries this id (build it, then scene.tag)"],
         ["I03","missing","Cap or top","G2","top","no geometry carries this id (build it, then scene.tag)"],
         ["I04","missing","Secondary parts","G2","details","no geometry carries this id (build it, then scene.tag)"]],
 "complete":false,"counts":{"built":1,"missing":3,"unattached":0,"rejected":0,"total":4,"required_open":3},
 "source":"session:docs-bin","kind":"prop","detail":"simple"}
```

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk asset init <name> --kind K [--detail simple\|standard\|hero\|none]` | — (`satk_op`) | a new project gets the starter inventory of its kind (`hero` by default) |
| `satk inventory starter <kind>\|all [--detail D] [--project P] [--force]` | — (`satk_op`) | the starter list of a kind; `--project` writes it as the project's inventory; `all` lists the kinds |
| `satk inventory validate <project\|file.json> [--strict]` | — (`satk_op`) | readable errors: fields, ids, `attaches_to` targets and cycles, categories, stages, regions, starter items dropped without a waiver |
| `satk asset inventory <project> [--session S\|--dff F\|--blend F] [--status open\|all\|built\|missing\|unattached\|rejected] [--stage G1..G4] [--plan] [--md] [--strict]` | — (`satk_op`) | the report (`complete` = every required item built); `--plan` lists the items without measuring; `--md` a checklist |
| `satk inventory mark <project> <id> --reject "<reason>"\|--accept [--by critic]` | — (`satk_op`) | a reviewer rejects an item (it stays rejected until accepted) or accepts it |
| `satk blender call scene.tag --params '{"objects":[...],"item":"I05"}'` | — (`satk_op`) | tag whole objects; with `select` only those faces; `clear: true` removes tags |

## How it works

- **Tags.** `scene.tag` writes the integer face attribute `satk_item_idx` (0 = no item) and the object properties
  `satk_item_ids` (the id list the values index; one append-only list per scene, so every object agrees) and
  `satk_item` (the ids on the object). Face attributes travel with the faces, so tags survive `scene.join`,
  `kit.fill`, `mesh.attach weld` and MIRROR. A `satk_item` property set by hand still counts for an object's
  untagged faces. Mesh edits can select tagged faces: `{"select": {"item": "I05"}}`; `{"linked": true}` widens a
  selection to its whole welded pieces.
- **Measuring.** `scene.items` (read-only) collects every tagged item from the evaluated meshes (modifiers
  applied): objects, frames, triangles, size, connected pieces, and the gap to each item it attaches to (nearest
  points both ways with BVH trees, up to 0.3 m). Hidden, ghost and reference objects, `*_dam`/`*_vlo` copies and
  collision, shadow and LOD slots do not count. The facts go to a file; the report is judged on the MIT side.
- **Verdicts.** `rejected`: a reviewer rejected it, it is tagged only on hidden or ghost objects, it shares an
  object with other items without face tags, it is a placeholder under 5 mm, or its faces have no vertex of their
  own (a patch of another item's surface: a recoloured face is not a part). `missing`: no geometry, fewer pieces
  with geometry of their own than its `count` (a left/right pair = 2), or - for a texture item (category `decal`,
  `verify: texture`) - no image texture on its faces (`verify: paint`: not on the paint key). `unattached`: one of
  its pieces is farther from every built parent than the item's `max_gap_mm` or its category's tolerance (panel
  30 mm, glass 25, mechanism 60, running gear 20, others 10).
- **Without a session.** The report reads the project's running session, else its newest checkpoint in a one-shot
  Blender run. An exported model carries a sidecar `<stem>.inventory.json` next to its DFF (the inventory and the
  facts of the exported frames, the DFF's hash; written for every kit export); `--dff` judges from it and checks it
  against the DFF (another hash, changed frames, more tagged triangles than a frame has: a problem, `complete`
  false). A broken sidecar is a `BAD_PARAMS` error with a hint, never a crash.
- **The inventory is the plan.** Starter items carry a `key`; dropping one (or marking it `required: false`)
  needs a `waived` entry with a design reason (`--strict` blocks otherwise); reasons about time, budget,
  difficulty, distance or visibility are refused. A pair keeps its starter `count` unless a waiver gives the
  design's (`{"key", "count", "why"}`). Only a shell, wheel, structure or body part may have `attaches_to: null`;
  `max_gap_mm` is at most 150. The project's `asset.json` binds the kind and the detail level. The design stage
  renames items to the real design and adds what the design has. `satk asset status` shows the inventory size;
  `asset check --strict` uses the report.

## Limitations and known issues

- The `satk_item` summary property is refreshed by `scene.tag`; after a join it names the target's ids until the
  next tag (the report reads the faces, not the summary).
- Attachment is measured between surfaces: two parts that touch only through a third item read as unattached;
  attach them to that item instead.
- Kinds without a vehicle-like anatomy (props, interiors, breakables, animated objects) have generic starters:
  rename and split the items to the real parts.

## Python API (if other packages use it)

```python
from satk.inventory import api
rep = api.report("mycar")                       # session, else newest checkpoint
rep = api.report(None, dff="<work>/out/kit/premier/modloader/premier/premier.dff")   # the sidecar's inventory
rep["complete"], rep["counts"], [i for i in rep["items"] if i["status"] != "built"]
api.has_inventory("mycar")                      # cheap: does the project (or a DFF) have one
from satk.inventory.sidecar import export_sidecar   # kit export writes <stem>.inventory.json with it
```
