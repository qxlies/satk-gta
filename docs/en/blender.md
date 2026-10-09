# Blender: GTA:SA models and areas in Blender, game-like renders, export to MTA

[Русская версия](../ru/blender.md)

Package: `satk.blender` (MIT) and the add-on `blender/satk_blender` (GPL-3.0-or-later).

## What it is

Headless Blender 5.1 for agents and an N-panel "SATK" for people. A model or an area is read straight from the
IMG archives (DFF + TXD chain + COL), imported with DragonFF's functions and saved as a `.blend` with packed
images; that `.blend` can then be rendered from any camera pose (with the SIDs of the visible objects) or
exported as an MTA resource or a modloader folder. `game-ready` turns any mesh of your own (`.blend`, `.obj`,
`.glb`, `.fbx`…) into a game model: its triangles kept (the engine cap of 65,535 vertices per geometry holds),
UVs, day/night prelight, COL, LOD, DFF, TXD.
Everything is written only to `work\blender\` (jobs, cache, Blender profile, DragonFF copy),
`work\out\blender\` and `work\out\exports\` of the workspace. The game and your own Blender profile are not
changed.

## Quick example

```powershell
satk blender doctor --quick
satk blender import-model model:411 --render
```

What comes back (shortened):

```json
{"ok":true,"cmd":"import_model","job":"20261005-111052-b8de",
 "files":{"png":[".../jobs/20261005-111052-b8de/model.png"],"blend":".../jobs/20261005-111052-b8de/scene.blend"},
 "stats":{"model":"model:411","name":"infernus","sec":"cars","hidden_dam":true,"wheels":4,"paint":[12,1,12,1],
          "meshes":17,"images":13,"txd_images":22,"unused_images":9,"missing_tex":0,"frame_margin":0.04,
          "engine":"BLENDER_EEVEE","png_std":62.81,"seconds":2.78,"process_s":4.65},
 "source":"index","warn":["EEVEE_ALPHA: used EEVEE to composite blended textures/materials"],
 "log":".../jobs/20261005-111052-b8de/blender.log"}
```

`images` are the images stored in the saved `.blend` (the model's materials use them, all are packed);
`txd_images` is everything loaded from the TXD chain (`infernus`: 3 of its own + 19 from `vehicle.txd`); the
extra ones (`unused_images`) are removed before saving, as Blender would drop them anyway. `frame_margin` is the
smallest distance from a projected vertex to the frame edge (a share of the frame): above zero, the model is not
cut off. The default engine is Workbench, but it cannot composite semi-transparent textures (car glass), so such
a render uses EEVEE (`EEVEE_ALPHA`).

## Area → render → export

The `.blend` path is `files.blend` of the `import-area` answer (`<job>` below is the job folder name):

```powershell
satk blender import-area --center 2495,-1687 --box 100 --match center --lod all --area any
satk blender render --blend <workspace>/work/blender/jobs/<job>/scene.blend --pos 2495,-1740,40 --look 2495,-1687,13 --objindex
satk blender export --blend <workspace>/work/blender/jobs/<job>/scene.blend --objects lae2_roads89 --target mta-resource
```

Here: 187 placements (168 HD, 19 LOD) of 116 models; the render with `--objindex` sees 78 objects, the largest
is `inst:lae2_stream0#18` (`Pawnshp_lae2`, 15.6 % of the pixels); the export writes `lae2_roads89.dff`, `.txd`,
`.col`, `.ide`, `.ipl`, `meta.xml` and `client.lua` into `work\out\exports\lae2_roads89\`.

## A game model from any mesh (game-ready)

<!-- docs-smoke: skip needs your own source file -->
```powershell
satk blender game-ready <workspace>/work/tmp/crate.obj --col box
satk blender game-ready <workspace>/work/tmp/statue.blend --objects Statue,Plinth --budget 600 --height 3 --render
```

The result is the folder `work\out\blender\<name>\`: `<name>.dff` (RW 3.6.0.3, day and night prelight),
`<name>.col` (COL3, COL model name = model name), `lod<name>.dff`, `<name>.txd` (the format is chosen by alpha,
DXT1 for opaque textures, with mipmaps; the PNG sources stay in the `tex` subfolder), `<name>.ide` (`objs`
lines for the model and its LOD with ID `-1`) and `gameready.json` (arguments, statistics, checks). The answer
(shortened, a textured cube):

```json
{"ok":true,"cmd":"game_ready","name":"crate","out":".../work/out/blender/crate",
 "files":{"dff":".../crate.dff","col":".../crate.col","txd":".../crate.txd","png":[".../tex/crate.png"],"ide":".../crate.ide"},
 "stats":{"src_tris":12,"tris":12,"uv":"keep","baked":false,"prelight":"bake","day_mean":0.337,"col":"box",
          "txd_formats":{"crate":"DXT1 64x64 mips 7"}},
 "checks":{"cols":["check","status","detail"],"rows":[["dff","ok","RW 0x36003, 12 tris, 28 verts (cap 65535)"],
   ["prelight","ok","day + night"],["txd","ok","crate DXT1 64x64 mips 7"],["col","ok","COL3 'crate': 0 faces, 1 boxes, 0 spheres"],…]},
 "warn":["LOD_SKIPPED: 12 triangles are below 48, no LOD model needed"]}
```

Steps (the source objects and their materials are not changed, everything works on copies):

1. **Source:** a `.blend` is opened as the scene (read-only; it is saved into the job folder), other formats are
   imported. It takes `--objects` (with their children) or every visible mesh; modifiers are applied, the meshes
   are joined into one object. `--height` (m) or `--scale`; the origin is `--origin base` (bottom centre),
   `center` or `keep`; duplicate vertices are merged, loose edges and degenerate faces are removed.
2. **Triangles:** the mesh keeps them; triangle counts are never a target. Only an explicit `--budget N` reduces
   it (planar dissolve at 1°, material and UV seams stay, then `COLLAPSE` down to N). Either way the engine cap
   holds: a mesh that would export more than 65,535 vertices in one geometry is collapsed just below it
   (`stats.vertex_cap`). Vanilla map models (p50 216, p90 1,040 triangles) are reference numbers only.
3. **UV:** `auto` keeps the existing UVs and uses smart project when there are none; `keep` and `smart` force
   one of them; `box` is a cube projection at 32 px/m.
4. **Textures:** a material with an image keeps it (a power of two ≤ `--tex-size`, default 256; the material
   colour becomes white), a plain colour stays the material colour; a procedural shader, vertex colours or a
   texture on new UVs are **baked** (Cycles `DIFFUSE`/`COLOR`, selected-to-active) into one texture `<name>` on
   fresh smart UVs. `--bake never` forbids baking (warnings `TEX_FLAT`, `TEX_UV_MISMATCH`).
5. **Prelight:** two per-corner colour attributes (`satk_day`, `satk_night`; DragonFF writes them as the prelight
   and the night colours). `bake` is Cycles AO with a ground plane, day = AO × (sky + sun), night = AO × a
   dark-blue ambient; `simple` uses the normals only, without Cycles; `none` writes no colours. The brightness
   matches vanilla: the median day prelight over 400 random `gta3.img` models is 82/255, the night one 26/255.
6. **COL:** `hull` is the convex hull (at most 256 triangles), `box` an AABB primitive, `mesh` the simplified
   model itself, `none` nothing; the surface is `--surface` (eSurfaceType 0…178: 0 default, 4 pavement,
   43 solid wood, 51 metal plate…). A flat mesh gets a box instead of a hull (`COL_HULL_FLAT`).
7. **LOD:** a share `--lod` of the triangles (0.25, like vanilla) in the model `lod<name>` with the same textures,
   its own prelight and draw distance 800 (the HD model gets `--draw`, default 150); below 48 triangles no LOD is
   needed (`LOD_SKIPPED`).
8. **Export and checks:** DragonFF writes the DFF and the COL; satk packs the TXD, re-reads everything with its own
   `satk.formats` and fills `checks`: the vertex cap and RW version, prelight, texture references, TXD, COL (one model,
   name, ±256 m), name lengths (COL ≤ 21, DFF ≤ 23), power-of-two textures. A failed check is the warning
   `GAME_READY_CHECKS`.

The TXD is packed by `satk texmod`; a checkout without it leaves the PNGs in `tex` with a `TODO-txd.txt` and
the warning `TXD_PENDING`. `--render` adds `preview.png` to the job folder: EEVEE, the game-like view
(texture × material colour × day prelight); the job's saved `.blend` opens with the same shading.

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk blender doctor [--quick]` | — | Blender and Python versions, DragonFF `b3bd7aa`, the isolated profile; extracts DragonFF on the first run; `--quick` does not start Blender |
| `satk blender import-model <id> [--render] [--views 1\|4] [--size N] [--engine workbench\|eevee] [--col] [--time HH:MM]` | `blender_job` (`import_model`) | one model into a new `.blend`; vehicles get `_dam`/`_vlo` hidden, wheels on every `wheel_*_dummy`, colours from `carcols.dat` |
| `satk blender import-area --center X,Y (--box S \| --box x0,y0,x1,y1 \| --r R) [--match aabb\|center] [--area N\|any] [--lod hd\|lod\|all] [--col] [--limit N]` | `blender_job` (`import_area`) | an area: one mesh per model, linked duplicates per placement |
| `satk blender render --blend B (--pos X,Y,Z --look X,Y,Z \| --ypr Y,P,R \| --bm NAME) [--time HH:MM] [--size WxH] [--fov 70] [--engine workbench\|eevee] [--objindex] [--limit N]` | `blender_job` (`render`) | PNG; with `--objindex` also a table of the visible SIDs with their pixel share |
| `satk blender export --blend B --objects NAME… [--target mta-resource\|modloader] [--out DIR] [--name N]` | `blender_job` (`export`) | DFF/TXD/COL + IDE/IPL with the source definition and placement data (+ `meta.xml`, `client.lua`) |
| `satk blender game-ready <src> [--name N] [--objects A,B] [--budget N] [--height M \| --scale S] [--origin base\|center\|keep] [--uv auto\|keep\|smart\|box] [--bake auto\|always\|never] [--tex-size N] [--prelight bake\|simple\|none] [--col hull\|box\|mesh\|none] [--surface N] [--lod R] [--draw D] [--out DIR] [--render]` | — | mesh → game model: DFF + COL + LOD + TXD in `work\out\blender\<name>\`, with checks |
| `satk blender preview <subject>` | — | an SA-like preview sheet of a model, a DFF file, a mod folder or a session: [look.md](look.md) |
| `satk blender addon-build` | — | the `satk_blender` extension zip in `work\out\blender\` |
| `satk blender run <cmd> --args '<json>'` | `blender_job` | the same through JSON (`cmd` = `doctor`, `import_model`, `import_area`, `render`, `export`) |

The model `id` is `model:411`, `model:infernus`, `411`, a name or the path of a `.dff` file of your own (the
`.txd` of the same name next to it is used; a vehicle also gets the game's `vehicle.txd`). The import commands also take `--profile`
and `--source auto|index|direct`. `--objects` of `export` is an object name in the scene, a model name
(`lae2_roads89`) or a SID (`model:17613`, `inst:lae2_stream0#4`); a placement is exported as its model.

## How it works

- **A job** is `work\blender\jobs\<yyyymmdd-HHMMSS-xxxx>\`: `request.json` → Blender
  (`-b --factory-startup --python-exit-code 1 --python blender\satk_blender\agent_cli.py -- request.json`)
  → `response.json`, `blender.log`, `scene.blend`, PNGs. The result is read from the file, not from stdout.
  The contract `satk-blender/1` is `src/satk/blender/contract.py` (frozen: changes are compatible only).
- **Environment:** DragonFF is extracted with `git archive b3bd7aa` from the workspace's `src\DragonFF` into
  `work\blender\dragonff` (the clone is not touched); the Blender profile is `work\blender\profile`
  (`BLENDER_USER_CONFIG/SCRIPTS/EXTENSIONS/DATAFILES/RESOURCES`), `PYTHONDONTWRITEBYTECODE=1`, TEMP inside the job
  folder.
- **Where the data comes from:** the index `work\index\vanilla.sqlite`; while it is not built (or was
  built by an older satk), direct DAT/IDE/IPL/IMG parsing (`satk.blender.gamedata`, ~0.6 s, warning
  `INDEX_MISSING` in the answer). Archive order, "the first archive wins" and LODs by back references work as in
  the index; vanilla has 50,935 placements. Blobs are copied to `work\blender\cache\blobs\<hh>\<sha16>\<name>`
  (cut to the RW size, content-addressed).
- **Import:** DragonFF's `dff_importer`/`txd_importer` functions are called directly (the modal map operator loads
  nothing under `-b`), images are packed, back faces are kept (`create_backfaces`). An area: model prototypes in
  `SATK_models` (excluded from the view layer), placements in `SATK_area`, LODs in `SATK_lod` (hidden when there
  is an HD model), collisions in `SATK_col` (only with `--col`, materials are shared). The placement matrix is
  the position + the **conjugated** IPL quaternion. Objects carry the properties `satk_sid`
  (`inst:`/`model:`), `satk_model`, `satk_lod`, `satk_area`, `satk_iflags` (placement flags from the IPL interior
  field); a model's collection carries `satk_ide` (draw distance, flags, `tobj` time, `anim` IFP).
  Breakable meshes (`*_breakable`: lamp posts, hydrants, crates) are put by DragonFF into a `Breakable`
  collection at the scene root; the import moves them into their model's collection (in an area, into the
  hidden `SATK_models`) and hides them from renders (`hide_render`), as the game does until the object breaks.
  The answer has `stats.breakables`.
- **Game-like shading** (EEVEE): the group `SATK_SA_Building` = texture × material colour ×
  lerp(day prelight, night prelight, balance); the balance follows the time of day as in the engine
  (1 until 06:00 → 0 at 07:00, 0 until 20:00 → 1 at 21:00). Workbench shows the textures without prelight.
- **`--objindex`:** a second EEVEE render with one sample: every object emits its own colour, texture alpha
  < 0.5 is cut (foliage, fences, wires), the colours are mapped back to SIDs. The full table is
  `files.visible`; the answer has the first `--limit` rows.
- **Export:** DragonFF writes the DFF (RW 3.6.0.3) and COL3; the COL comes from the game collision when the area
  was imported with `--col`, otherwise from the render mesh (warning `COL_FROM_MESH`). The COL model name is the
  model name. `client.lua`: `engineRequestModel` → `engineLoadTXD`/`engineImportTXD` →
  `engineLoadDFF`/`engineReplaceModel` → `engineLoadCOL`/`engineReplaceCOL` → `createObject` (MTA's object
  rotation order is `ZXY`). The IDE fragment takes the draw distance and flags of the source definition
  (`lae2_roads89`: `150, 1`; `tobj` with its time, `anim` with its IFP), the IPL interior field = area +
  placement flags `<< 8` (`telgrphpole02`: `512`). For a `.blend` imported before this existed, the data is
  completed from the index or the game by the scene's `satk_profile`; a model without a definition gets
  `299, 0` and the warning `IDE_DEFAULTS`. The name (`--name`) and the folder are checked before Blender starts,
  and the folder is created only after the objects are found: `NOT_FOUND`/`BAD_PARAMS` leave nothing on disk.
  Writes go only under `work\`; `--out` into the game gives `PROTECTED_PATH`. Never into an IMG.
- **Model framing** (`--render`): the camera at azimuths 45/135/225/315° is fitted to the vertices of the
  visible meshes with an 8 % margin from the edge; the projection is centred with perspective taken into account.
- **Time:** Blender starts in ≈1.5–2 s; a car with a render ≈4–5 s; an area of 100×100 m (187 placements,
  116 models, 843 images from TXDs, 481 of them in the `.blend`) ≈7–8 s; a render with `--objindex` ≈4 s
  (1.5 s of it rendering); the first EEVEE frame compiles shaders.

## The add-on for people

`satk blender addon-build` builds `work\out\blender\satk_blender-0.1.0.zip`. Installing it into your own Blender
profile is your decision (Preferences → Get Extensions → Install from Disk). The "SATK" N-panel: index search,
Import Model, Import Area by coordinates / zone / viewer bookmark, "Game shading" and a time slider, Hide/Show
`_dam`/`_vlo`, Export of the selection. A zone is given by name (`GAN1`): the centre of its rectangle (`zone:` of
the index; without an index `data/info.zon`/`map.zon`), the size is the Box field. Export works in the open
scene and puts it back as it was afterwards (excluded collections, visibility, selection, shader links). The
"Game-ready" block does what `satk blender game-ready` does, from the selected meshes of the open scene: the
result goes into the `SATK_gameready` collection and into `work\out\blender\<name>\`; the selection and the
source objects do not change, and a repeated run with the same name replaces the previous result.

The add-on preferences hold the path to the satk checkout's `src` (`satk_src`) and to the folder with `dragonff`.
Empty fields mean the defaults: the `src` of the checkout the add-on runs from (or `SATK_SRC`) and the
`<work>\blender` of the satk workspace (or `SATK_DRAGONFF`); an installed DragonFF extension is used when there is
one. The add-on holds no paths of a particular machine. In a session with the add-on, DragonFF's own TXD import
(its operator and its map import) decodes textures through `satk.formats.dxt`: DragonFF `b3bd7aa` breaks DXT3
colours; TXDs that satk does not read (PS2, Xbox) and imports with mipmaps stay with DragonFF's
decoder.

Blender runs Python with `ignore_environment`, so `PYTHONDONTWRITEBYTECODE` alone has no effect: the add-on and
`agent_cli.py` set `sys.dont_write_bytecode` themselves (the add-on when the variable is set, as satk's runner
sets it), and no `__pycache__` appears in `tools` or in `work\blender\dragonff`.

## Blender 5.1 notes

- **Normals.** Since Blender 4.1 there is no "auto smooth": custom normals are kept even on flat faces, but every
  flat face is its own fan, so a DFF export writes one vertex per corner (a flat-shaded car gets about four
  times the vertices). Weld first, set every face smooth, mark the sharp edges, and only then apply a Weighted
  Normal modifier or custom normals; toggling smooth shading afterwards moves the custom normals.
- **Smooth by Angle** is a modifier (a geometry-nodes asset) since 4.1; it works headless with
  `--factory-startup`, and DragonFF exports the evaluated modifier stack.
- **`use_nodes`** of materials and worlds is deprecated in 5.x (always on); satk sets it only where older
  builds need it.
- **Thumbnails.** Blender on Windows writes `.blend` thumbnails into the user's `.thumbnails` folder whatever
  `XDG_CACHE_HOME` says. Every satk entry script sets `file_preview_type = 'NONE'` (and no `.blend1` backups,
  no autosave) before anything is saved: a cold job that saves a `.blend` adds no thumbnail.
- **Bytecode.** Blender ignores `PYTHONDONTWRITEBYTECODE` (it runs Python with `ignore_environment`); the entry
  scripts set `sys.dont_write_bytecode`, so no `__pycache__` appears in the add-on folder.
- **Threads.** Jobs pass `-t N` from `SATK_BLENDER_THREADS` or `[blender] threads` in `satk.toml` (unset or 0 =
  all cores), so several jobs can share a machine.
- **Background mode.** `bpy.app.timers` do not fire under `-b` and undo is unavailable; the live session
  ([studio.md](studio.md)) uses a blocking main-thread loop and `.blend` checkpoints instead.
- **EEVEE** (`BLENDER_EEVEE` in 5.x) compiles shaders on the first frame of a job; the SA game look of
  [look.md](look.md) shares one node group between all materials to keep that short.

## Limitations and known issues

- `export` writes the TXD as RGBA8888 without mipmaps (DragonFF's TXD writer has no DXT encoder;
  warning `TXD_RGBA8888`): files are 4–8 times bigger. `game-ready` already packs DXT through `satk texmod`.
- `match=aabb` without an index takes the AABB from the DFF bounding sphere (the index takes the COL box), so the
  numbers can differ slightly from `satk world near`.
- Workbench cannot multiply texture × material colour: cars are white in Workbench, the paint shows in EEVEE.
  Scenes with semi-transparent textures are rendered with EEVEE anyway (`EEVEE_ALPHA`).
- DragonFF loses a few degenerate or duplicate triangles (`lae2_roads89`: 219 of 225).
- The whole map does not fit into Blender (50,935 placements): a job is limited to 5,000 placements
  (`--limit`, default 2,000); an area is the sensible unit.
- Every job pays the 1.5 s start; step-by-step work goes to a live session ([studio.md](studio.md)).
- `game-ready`: UVs are smart project or a cube (xatlas and part-based UVs are not installed: no new
  dependencies); one COL surface for the whole model; the prelight is a lighting model, not `timecyc.dat`; the alpha of
  the source textures is lost when baking; game-ready does not fix the orientation of the source normals.

## Python API

```python
from satk.blender.resolve import plan_model, plan_area      # no bpy: what to import and where to place it
from satk.blender.runner import run_job                      # run a job of the satk-blender/1 contract
plan = plan_area(center=[2495, -1687], box=[2445, -1737, 2545, -1637], match="center", area=None, lod="all")
resp = run_job("import_area", {"center": [2495, -1687], "box": 100, "lod": "all", "area": "any"}, plan=plan)

from satk.blender.gameready import out_dir, finalize         # game-ready: the folder, then TXD/IDE/checks
resp = run_job("game_ready", {"src": "<workspace>/work/tmp/crate.obj", "out": str(out_dir("crate"))})
done = finalize(out_dir("crate"))                            # {"files", "checks", "stats", "warn"}
```
