# Convert: bring a rigid 3D model into SA style

[Русская версия](../ru/convert.md)

Package: `satk.convert` (MIT) and `blender/satk_blender/convert` (GPL-3.0-or-later).

## What it is

`asset convert` imports an author's model, fits the measured scale, cleans the mesh (no triangle target), bakes
its materials into small SA textures and builds a [kit](kit.md) package. It returns checks on the exported DFF/TXD and a
game, clay and wire lineup beside class peers. Inputs are read-only; results stay under `<workspace>/work/`.

## Quick example

```powershell
satk asset convert -h
satk convert prepare -h
satk convert finish -h
```

With a local source model, Blender, DragonFF and the vanilla index available:

<!-- docs-smoke: skip requires the author's model and Blender -->
```powershell
satk asset convert model.glb --kind prop --dims 1.2,1.2,1.8
satk asset convert car.fbx --kind vehicle --like model:426 --tier sa_plus
```

The response includes `out`, `blend`, `tris` (a plain number), `limit`, `shading` (class reference, info),
`textures`, `check`, `package.files`, `mta` and `preview.files.sheet`. `ok` means the operation finished: inspect the checks and
any `CHECK_FAILED` warnings before using the package. `conversion.json` keeps the complete result.

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk asset convert MODEL --kind KIND [--like SID] [--tier sa_plus\|vanilla] [--dims L,W,H] [--out DIR] [--name NAME] [--session S]` | `satk_op` | complete conversion, kit export, checks and lineup |
| `satk convert prepare MODEL --kind KIND [--session S] [--out DIR]` | `satk_op` | write the immutable plan; install the six conversion methods in a running session |
| `satk convert finish PLAN` | `satk_op` | finish the Cycles bakes against measured texture-role targets |

`vehicle` means `automobile`; rigid kit kinds also include `prop`, `building`, `interior_prop`,
`interior_shell`, `terrain`, `weapon` and `pickup`. Dimensions are length, width, height in metres
(Blender Y, X, Z). Omitted dimensions use class medians or the `--like` model. A body-only automobile
reserves ground clearance for the generated kit wheels. `--timeout` is the limit per Blender call.

Without `--session`, the converter starts and stops its own headless studio. To work step by step:

<!-- docs-smoke: skip requires the author's model and a running Blender session -->
```powershell
satk blender session start --name converting
satk convert prepare model.glb --kind prop --name example --session converting --out "<workspace>/work/out/convert/example"
satk blender call convert.import --session converting --params '{"plan":"<workspace>/work/out/convert/example/plan.json"}'
satk blender call convert.normalize --session converting --params '{"plan":"<workspace>/work/out/convert/example/plan.json"}'
satk blender call convert.clean --session converting --params '{"plan":"<workspace>/work/out/convert/example/plan.json"}'
satk blender call convert.reduce --session converting --params '{"plan":"<workspace>/work/out/convert/example/plan.json"}'
satk blender call convert.bake --session converting --params '{"plan":"<workspace>/work/out/convert/example/plan.json"}'
satk convert finish "<workspace>/work/out/convert/example/plan.json"
satk blender call convert.assemble --session converting --params '{"plan":"<workspace>/work/out/convert/example/plan.json"}' --save "<workspace>/work/out/convert/example/converted.blend"
satk kit export --model example --session converting --out "<workspace>/work/out/convert/example/package"
```

The usual [studio](studio.md) journal, checkpoints and snapshots apply. Methods require the preceding
stage; repeated import is refused. Continue an interrupted manual conversion from its last completed
stage, or run the complete operation in a new private session. Other session objects are preserved.

## How it works

1. Blender's built-in GLB/glTF, FBX, OBJ and available Collada importers read the source; `.blend` objects
   are appended. Evaluated transforms and axes are normalized, hidden and degenerate geometry is removed,
   and coincident vertices are merged.
2. Triangle counts are never a target: a planar dissolve removes redundant edges of flat areas and the
   source keeps its detail. Only a source above the engine safety cap (`limit.max_tris` = 21,845: 65,535
   vertices per geometry, 3 per triangle at worst) is collapsed, each role by its share. Material/UV seams
   delimit the dissolve; spaced seam samples and silhouette extrema constrain the collapse. `stages.json`
   reports preserved samples and sampled surface deviation; no subdivision is ever added.
3. The unreduced meshes keep the source materials. Cycles bakes base colour and alpha onto unique UV
   atlases, 128–256 pixels per role. `texture.finish` supplies the SA finish; measured palette, blur and
   grain adjustments are checked after DXT1/DXT3 compression. Alpha is preserved.
4. Kit slots receive the result and `sa_shade` supplies normals. World kinds receive day and warm night
   prelight, without exported normals. Kit generates wheels, vehicle VLO and building LOD; `kit.export`
   generates collision through `col.gen`, packs textures and writes the Mod Loader folder. The existing
   MTA resource writer also produces `meta.xml` and `client.lua` beside copies of the exported files.
5. `asset.check`, actual TXD pixel measurements and `blender.preview` inspect the exported result.
   Inputs, discovered texture dependencies and artifacts are hashed. An unchanged repeat returns the
   cached result without Blender. A different plan cannot overwrite an occupied output folder; concurrent
   complete conversions to the same folder return `BUSY`.

## Limitations and known issues

- Skinning, skeleton retargeting and animation are not automatic. Export a static pose or use the kit's
  authored rig workflow. Missing importers return `UNSUPPORTED`; Blender builds without Collada need a
  source exported to glTF/FBX/OBJ. No import plug-ins or remote resources are downloaded.
- Principled, Diffuse and Emission surface shaders with image/procedural colour inputs are supported.
  Mixed/custom shaders, movie textures and tiled image sets need a source-side bake first.
- A vehicle conversion is a rigid body in a kit scaffold with generated wheels/VLO. Doors, damage,
  wheel arches and functional lamps need authoring. Source paint is baked as a fixed livery with SA
  sheen; automatic car-colour keys and dirt remapping are not inferred.
- A dense source stays dense: simplify it in its own editor or in a studio session when the SA look needs
  fewer, softer panels. A uniform high-resolution surface can miss the class shading reference; review
  panel density and seams in the saved blend; all measured failures remain visible in the response.
- `--like` supplies a reference, not replacement registration. New-name Mod Loader files need a later
  `kit export --replace SID` or `--add`; MTA resources load assets but do not spawn vehicles or place a map.

## Python API

```python
from satk.convert.pipeline import run
result = run("model.glb", kind="prop", dims=[1.2, 1.2, 1.8])
```
