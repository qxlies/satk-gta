# Models: GPU-free previews and export to glTF, OBJ and "as in the game"

[Русская версия](../ru/models.md)

Package: `satk.model3d`.

## What it is

Shows a game model as a picture and exports it to formats that Blender and any 3D viewer open. Previews come
from satk's own software rasteriser (`soft`, numpy): no video card, no window and no viewer, byte-identical on a
repeat run. This makes thumbnails of **every** model of the game a matter of minutes. DFF, TXD and COL are read
from the IMG archives read-only (through the `satk.index` asset index). Everything is written under `<work>`:
previews to `<work>/cache/model/`, exports to `<work>/out/models/<name>/`, "as in the game" files to
`<work>/cache/raw/<profile>/`.

## Quick example

```powershell
satk model image model:411
satk model image lae2_roads89 --views 1 --size 256
satk asset export model:411 --format glb
satk asset export model:411 --format raw
```

What comes back (shortened):

```json
{"ok":true,"id":"model:411","file":"<work>/cache/model/de2d9659f2e6-a8a588ec0813-soft-4-384.png",
 "backend":"soft","name":"infernus","w":768,"h":768,
 "legend":[[1,45.0,25.0],[2,135.0,25.0],[3,225.0,25.0],[4,315.0,25.0]],
 "stats":{"tris":3072,"drawn":3222,"tex_missing":0,"cover":[0.262,0.251,0.251,0.262]},"cached":false}
{"ok":true,"id":"model:411","name":"infernus","format":"glb","dir":"<work>/out/models/infernus",
 "files":["<work>/out/models/infernus/infernus.glb"],
 "stats":{"tris":3072,"verts":3573,"meshes":15,"materials":19,"textures":13,"tex_missing":0}}
```

`legend` gives the cell number of the sheet (left to right, top to bottom), the camera azimuth and the elevation
in degrees; `cover` is the share of model pixels in each view (close to 0 means the model is barely visible).

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk model image ID [--views 4] [--size 384] [--backend auto\|soft\|ariane\|blender] [--force]` | `model_image` | a preview: `views` views around the model, a 2×2 sheet for 4 views (768×768 with `--size 384`) |
| `satk model image --all --size 128 --views 1 [--jobs 12] [--force]` | `model_image` (`all`; long-running) | thumbnails of every model with a DFF; finished ones are skipped; manifest `<work>/out/models/thumbs-<profile>-<size>x<views>.json` |
| `satk asset export ID --format glb\|obj\|png\|raw [--out DIR]` | `asset_export` | exports a model; `png` = its textures, `raw` = DFF + TXD chain + COL as in the game |

`ID` is `model:411`, `model:infernus`, plain `411` or `infernus`, `dff:infernus` (the model with that DFF name),
`inst:lae2_stream0#4` (the model of that placement) or the path of your own `.dff` file (the `.txd` of the same
name next to it is used; a vehicle also gets the game's `vehicle.txd` and satk's default paint; no index is
needed except for that `vehicle.txd`). Every command takes `--profile` (default `vanilla`).
`--out` is accepted only inside `<work>`.

## How it works

**The `soft` preview.**
- The camera sits on a sphere around the model: azimuth 0 puts the camera on +Y looking at −Y (the front of a
  car); the azimuth grows counter-clockwise seen from above. Four views are 45/135/225/315° at 25° elevation.
  Each view is framed to the model with a 5% margin.
- Colour = texture (nearest texel, mip level chosen by the triangle's size on screen) × material colour ×
  light, with the numbers of the game look ([look.md](look.md)): lit models (vehicles, peds, objects) get the
  object ambient plus the directional light of noon in the profile's `timecyc.dat` (one fixed light direction,
  shared with the Blender previews), prelit models get their day prelight plus the ambient; then the game's
  colour filter and display gamma. Car bodies on `vehiclegrunge256` show dirt level 2.
- Alpha: textures with alpha are clipped at 0.5; a translucent material colour (vehicle glass) is blended as one
  layer over the nearest opaque surface.
- Up to 256 px per view, 2× supersampling is used. The background is `#b2bac4`.
- As in the game: vehicles hide `*_dam` and `*_vlo`, the `wheel` is copied onto the empty `wheel_??_dummy`
  frames, the paint colour keys are replaced with the first colour pair from `data\carcols.dat`, and the light
  lamp materials on `vehiclelights128` are drawn white (the engine's rule; a lamp key on another texture stays as
  it is). Peds (skinned, `Pelvis` bone) are stood upright facing +Y.
- A model without a DFF (for example `model:300` `cutobj01`, `hier`) gives a grey placeholder and the warning
  `NO_DFF`, not an error.

**Cache.** `<work>/cache/model/<DFF hash>-<hash of the TXD chain and settings>-<backend>-<views>-<size>.png` with
a `.json` of statistics next to it. A repeat returns the finished file (`"cached":true`), `--force` redraws it
(the bytes are the same). The rasteriser version is part of the hash, so the cache refreshes itself after an
algorithm change.

**Backends.** `auto` = `soft`. `ariane` is the running viewer (`satk view start`), SAAP method `asset.render`,
one view per call. `blender` is the Blender runner ([blender.md](blender.md), `import_model` with a render,
1 or 4 views). If the backend is not running or fails to draw, `soft` draws the preview and `warn` gets
`FALLBACK: backend ariane failed (...)`.

**Export.**
- `glb`: glTF 2.0 in one file, written without dependencies. The `satk_root` node turns the game's Z-up into
  glTF's Y-up; below it are the DFF frame nodes with local matrices and the meshes: `POSITION`, `NORMAL`
  (recomputed if the DFF has none), `TEXCOORD_0/1`, `COLOR_0` = prelit (day), `COLOR_1` = night colours.
  Textures are embedded PNGs; `alphaMode` is `MASK` for textures with alpha and `BLEND` for a translucent colour.
  The `*_dam`/`*_vlo` states are kept with `extras.satk_state`.
- `obj`: OBJ + MTL + PNGs next to it. Also Y-up (Blender's default import places the model correctly), frame
  matrices "baked", `vt` with the origin at the bottom.
- `png`: every texture of the model that was found, as PNG (RGBA).
- `raw`: DFF and TXD as separate files (without the IMG sector padding) and the model's `.col` archive; vehicles
  keep their embedded collision inside the DFF. For DragonFF and Blender.
- Export adds no wheel copies and hides no parts: the file holds exactly what the DFF has (3,072 triangles for
  `model:411`).

**Thumbnails of all models** (`--all`). Models are sorted by TXD and split into batches over processes (`--jobs`,
default 12), so a shared TXD (`vehicle` and the like) is decoded once per process. Errors of single models do
not stop the run: they are listed in the answer (`errors`, the first 50) and in the manifest.

## Limitations and known issues

- No shadows, reflections or night mode in `soft`; the full game look (glass, env, day/night, lineups) is
  `satk blender preview` ([look.md](look.md)).
- Animations are not applied: peds and `anim` objects stay in the pose of the DFF.
- `--backend ariane` draws one view per call; the viewer may fail to load vehicles and peds, and then the
  fallback to `soft` kicks in.
- `--backend blender` draws 1 or 4 views (any other number falls back to `soft`); its elevation is always 25°,
  and it counts the azimuth its own way.
- Model files come from the index (`satk.index`); without it the commands answer `INDEX_MISSING`
  (`satk index build`). Solutions are in [troubleshooting.md](troubleshooting.md).

## Python API (if other packages use it)

```python
from satk.model3d import api
api.image("model:411", views=4, size=384)            # envelope: file, backend, legend, stats
api.export("model:411", "glb")                        # envelope: files, stats
m = api.load("model:426")                             # the decoded model, no rendering
m.src.txd_chain                                       # [BlobRef] own TXD, txdp parents, vehicle.txd
for part in m.scene.parts:                            # one per atomic: name, geometry, model-space matrix
    mesh = m.scene.meshes[part.geom]                  # positions, tris, normals, uv sets, prelit, mat_ids
for f in m.scene.frames:                              # frame tree: idx, parent, name, local and model matrices
    print(f.name, f.model[9:12])                      # matrices are (right, up, at, pos) 12-float tuples
m.mats[0][0].rgba, m.mats[0][0].tex                   # per geometry and slot: colour (paint applied), texture
from satk.model3d.batch import thumbnails
thumbnails(size=128, views=1, jobs=12)                # thumbnails of every model
```
