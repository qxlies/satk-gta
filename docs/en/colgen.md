# `satk col gen`: collision from models, checks, surfaces

[Русская версия](../ru/colgen.md)

Package: `satk.colgen`. The COL writer itself (`satk col write`, `satk col export`) is on the [rw page](rw.md).

## What it is

Every model the game draws needs a collision model, and making one by hand in a 3D editor is the slowest part
of a prop, building or car mod. `satk col gen` builds it from the DFF: a box, a few boxes, an outer convex hull
that always contains the model, the render mesh simplified to a face budget, or a vehicle-style set of spheres
with a closed shadow mesh. Surface materials (footsteps, tyre grip, bullet effects) come from the textures
through a table derived from the vanilla game itself, and the lighting byte of every face from the model's
prelit colours. `satk col check` checks any collision file (generated, from a mod or from the game). The game
is only read; output goes to `<workspace>\work\out\colgen\`.

## Quick example

```powershell
satk col gen model:1337 --mode hull
satk col gen infernus --out examples
satk col check BinNt07_LA.col
satk col surface ap_tarmac blendrock2grgrass my_wood_planks
```

What comes back (shortened):

```json
{"ok":true,"cols":["name","mode","spheres","boxes","faces","shadow","surfaces","fit","file"],"rows":[["BinNt07_LA","hull",0,0,64,0,"PLASTIC_DUMPSTER:64","out 0.00 dev 0.04","<workspace>/work/out/colgen/BinNt07_LA.col"]],"dir":"<workspace>/work/out/colgen","models":1,"verified":true,"surface_from":{"table":"100%"}}
{"ok":true,"rows":[["infernus","spheres",20,0,0,300,"CAR:20","cover 55%","<workspace>/work/out/colgen/examples/infernus.col"]],"models":1,"verified":true,"surface_from":{"vehicle":"100%"}}
{"ok":true,"cols":["model","sev","check","msg"],"rows":[],"files":1,"models":1,"summary":{"ok":1}}
{"ok":true,"cols":["texture","id","surface","how","share","votes"],"rows":[["ap_tarmac",1,"TARMAC","table","54%",199],["blendrock2grgrass",123,"DIRT_ROCKY","table","100%",108],["my_wood_planks",43,"WOOD_SOLID","keyword:plank","-",0]]}
```

`fit`: `out` = how far a render vertex sticks out of the collision (hull, box, boxes: 0 = the model is inside),
`dev` = how far the collision surface may be from the render mesh, `cover` = share of the body the spheres fill.

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk col gen <dff\|folder\|img\|SID> [--mode auto\|box\|boxes\|hull\|mesh\|spheres\|bounds]` | — (through `satk_op`) | one COL3 per model under `<work>/out/colgen/` (or one archive with `--archive`), re-parsed with `satk.formats` (`verified`) |
| `satk col check <col\|folder\|img\|dff\|model:ID> [--sev info] [--fail-on error]` | — | table `model/sev/check/msg`, `summary` by worst finding, `by_check` |
| `satk col surface [TEXTURE ...] [--txd NAME]` | — | what `--surface auto` picks and why; without textures the 179 surface ids and names |
| `satk col derive [--holdout 10] [--profile vanilla]` | — | rebuild the texture -> surface table from a profile's own collisions with an accuracy report |

Modes of `col gen` (`auto` is the default):

| Mode | Collision | Budget |
|---|---|---|
| `box` | the bounding box of the colliding parts | — |
| `boxes` | boxes from a voxel decomposition, merged and shrunk onto the mesh | `--max-prims 8` |
| `hull` | an outer convex hull: every render vertex is inside | `--max-faces 64` |
| `mesh` | the render mesh welded to the COL grid (1/128 m) and decimated (quadric error) | `--max-faces 1000` |
| `spheres` | inscribed spheres, mirrored left/right on vehicles, `CAR` surface and damage pieces | `--max-prims 20` |
| `bounds` | no primitives, only the bounding box and sphere (like vanilla LOD models) | — |
| `auto` | vehicles: `spheres` + shadow; > 25 m or > 2000 triangles: `mesh`; otherwise `hull` | |

Other options: `--shadow` / `--no-shadow` (closed COL3 shadow mesh, `--shadow-faces 300`), `--surface TARMAC`
(one surface for everything), `--surface-rule "*grass*=GRASS_SHORT_DRY"` (texture globs first),
`--exclude "*leaf*"` (part or texture names to leave out), `--txd NAME` (TXD context for surfaces),
`--version 2` (COL2), `--no-lighting` (face light 255), `--resolution N` (voxel cells), `--archive mymod.col`,
`--out folder|file.col`.

`col check` codes: `parse`, `name`, `index`, `surface`, `box_inverted`, `sphere_radius`, `bounds`/`bsphere`
(error above 0.5 m, warning above 1 cm), `face_groups` (errors); `limit` (above the largest vanilla model:
5191 faces / 3408 vertices), `degenerate`, `no_face_groups`, `flags`, `shadow_open`, `shadow_winding`
(warnings); `duplicate`, `unused_vertices`, `empty` (info). On the whole vanilla `gta3.img` (8255 models) it
reports 2 errors, both real defects of the shipped files.

## How it works

- **Render mesh.** The DFF is decoded with frame matrices applied (model space, like the game). Damage and LOD
  states (`*_dam`, `*_vlo`), geometries no atomic uses and, on vehicles, the wheels (the game builds wheel
  collision from the wheel dummies) never collide.
- **Hull.** Support planes of the vertices (the render face normals, a Fibonacci sphere, the axes) clip the
  bounding box: the plane cutting the deepest remaining corner goes first, until the corners are within 4 mm
  of the exact hull or the face budget is reached. Vertices are rounded outward to the COL grid, so the model
  stays inside.
- **Mesh.** Garland-Heckbert edge collapse; open borders and borders between surfaces are kept by penalty
  quadrics, folds and needles are refused, every face keeps the surface of the render face it came from.
- **Voxels.** Triangles are sampled densely, the outside is flood-filled; the rest is the solid body (a car's
  seats do not matter). Boxes, spheres and the shadow mesh (the closed boundary of the solid, smoothed, then
  decimated) are built from it.
- **Winding.** The engine computes a face normal as `(C - A) x (B - A)` (`CColTrianglePlane`), the opposite of
  RenderWare's render winding; faces are written that way, and `col check` warns about inside-out shadows.
- **Bounds** cover every primitive and the colliding render geometry: in vanilla files the COL box is also the
  model's culling box (99 % of vanilla bounds contain the DFF).
- **Surfaces.** `data/colgen/tex_surface.json` was derived from the vanilla game: each of its 1.18 million
  collision faces was matched to the nearest render triangle, giving 6181 textures with their majority surface
  plus 2277 (TXD, texture) pairs that differ (the same grass is lush in one area and dry in another). Lookup
  order: `--surface-rule`, (TXD, texture), texture, keyword fallbacks (`data/colgen/keywords.json`), `DEFAULT`.
  Measured on models held out of the table: 82 % of the faces get the vanilla surface (76 % without the TXD
  context); generated mesh collisions of a model sample match the nearest vanilla face on 93 % of the faces.
- **Lighting.** The low nibble (day) and high nibble (night) of the face lighting byte follow a linear fit to
  the prelit and night vertex colours (fitted on 1.18 million vanilla faces, mean error 1.7 of 15).
- Deterministic: the same input gives the same bytes.

## Limitations and known issues

- COL2/3 vertices are `int16 / 128`: a mesh or hull vertex farther than 256 m from the model origin is refused
  (boxes and spheres are floats and work).
- Spheres fit closed bodies; thin or open models (a bike frame, a fence) get a low `cover` and a note: use
  `hull` or `boxes` there.
- Vehicle damage pieces follow the bumper and door parts under each sphere (72 % agree with vanilla).
- The surface table knows vanilla texture names; new textures fall back to keywords, then `DEFAULT`. Check with
  `satk col surface` and override with `--surface-rule`.

## Python API

```python
from satk.colgen.build import GenOptions, generate
from satk.rw import col as COL

res = generate(dff_bytes, "mybarrel", opts=GenOptions(mode="hull", max_faces=48))
blob = COL.encode_model(res.model)
```
