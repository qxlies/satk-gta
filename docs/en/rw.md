# `satk rw`, `col`, `img`: writing RenderWare without Blender

[Русская версия](../ru/rw.md)

Package: `satk.rw`.

## What it is

DFF, COL and IMG writers in pure Python (standard library only), with no Blender and no GPL code. They cover
the jobs where DragonFF is too slow or does not fit at all: "change a texture reference in 300 DFFs",
"restamp a VC model for SA", "build a collision from JSON", "build an IMG for SA-MP DL or a mod release".

The game is only read. Everything written is a **new** file under `<work>/out/rw/` (or at an explicit path
outside the game folders). Existing IMG archives are never opened for writing.

## Quick example

```powershell
satk rw patch models/gta3.img/infernus.dff --rename-tex vehiclelights128=mylights128 --out docs-demo
satk rw patch models/gta3.img/infernus.dff --smooth-normals --recalc-bsphere --dry-run
satk formats ls models/gta3.img --name infernus
satk img build docs-demo --base models/gta3.img --include infernus.txd --out docs-demo --overwrite
satk img diff models/gta3.img docs-demo.img --change changed
satk col export models/gta3.img/infernus.dff --out docs-demo-infernus
satk col write docs-demo-infernus.json --out docs-demo-infernus
satk rw roundtrip models/gta_int.img --kind col
```

What comes back (shortened, first and fourth commands):

```json
{"ok":true,"cols":["name","status","changes","out"],
 "rows":[["infernus.dff","written","texture:11","<work>/out/rw/docs-demo/infernus.dff"]],"written":1}
{"ok":true,"cols":["name","change","size_a","size_b"],"rows":[["infernus.dff","changed",215040,215040]],
 "entries_a":16316,"entries_b":2,"same":1,"changed":1,"added":0,"removed":16314,"identical":false}
```

After `rw patch`, `satk formats dump <path from out>` shows the new texture name.

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk rw patch DFF… [--rename-tex OLD=NEW…] [--rw-version iii\|vc\|sa] [--night-colors add\|remove] [--recalc-normals \| --smooth-normals [--angle 45] [--weld 0.001] [--split-at material,uv\|none]] [--recalc-bsphere] [--material-color N=R,G,B[,A]…] [--out DIR] [--dry-run]` | — | edits DFFs: files, folders (their `*.dff`), a whole IMG (all DFFs) or `<img>/<entry>`; copies of the changed files go to `<work>/out/rw/<DIR>/` (default `patch`) |
| `satk rw roundtrip [TARGET] [--kind dff\|col\|all] [--engine satk\|rwfury] [--no-restamp] [--step N]` | — | measures bit-for-bit round trips: the whole game, an IMG, a folder or a file; mismatch classes; `--no-restamp` skips the SA → VC → SA cycle |
| `satk col write SRC [--out NAME] [--version 1\|2\|3\|4]` | — | a COL from a JSON file or a JSON string; the result is re-read by an independent parser (`verified`) |
| `satk col export TARGET [--model NAME] [--out NAME] [--no-raw]` | — | a `.col`, an IMG entry or a vehicle's embedded COL → the JSON that `col write` accepts |
| `satk img build SRC… --out NAME [--base IMG] [--include PAT…] [--remove PAT…] [--version 2\|1] [--recursive] [--overwrite]` | — | a new IMG (VER2; `--version 1` = `.img` + `.dir` for III/VC) from folders and files and/or the entries of another archive |
| `satk img diff A B [--change added\|removed\|changed] [--no-content]` | — | compares two IMGs by entry name and content (zero sector padding is not a difference) |

None of these commands has an MCP tool of its own: an agent runs them through `satk_op`. Every command takes
`--profile` (default `vanilla`).

Input paths: absolute, relative to the profile's game root (case-insensitive, `<archive>.img/<entry>`),
relative to the current folder, or a name inside `<work>/out/rw/`. So `rw patch --out x` continues directly
with `img build x`. The sources of `img build` are positional arguments: write `satk img build FOLDER --out NEW.img`.

### `rw patch`

- `--rename-tex` compares names case-insensitively and renames the main texture, the mask, the MatFX effect
  textures (environment, bump, dual) and the specular texture. A name is up to 31 ASCII characters
  (23 for specular).
- `--rw-version` stamps 3.1.0.1 / 3.3.0.2 / 3.6.0.3 on every chunk and changes what librw writes
  differently: 12 bytes of surface properties in the geometry Struct below 3.4, the old Skin PLG format below
  3.4 (`0xDEADDEAD` before the matrices, no bone list), light and camera counters in Clump from 3.3 on. For III
  the clump's RW lights are removed (`lights_dropped`): format 3.1 does not count them.
- `--night-colors add` copies the day prelit colours into night colours where they are missing (no prelit →
  warning `NO_PRELIT`); `remove` deletes the plugin.
- `--recalc-normals` computes normals per vertex record (the sum of the normals of its own faces, weighted by
  area) and sets the NORMALS flag. It does **not** weld: in a flat-shaded export every face has its own
  vertices, so the result stays flat (the benchmark cars got flatter: normal bend 0.81 → 0.71° and 2.42 → 1.70°,
  and vanilla `premier` drops from 10.64 to 4.97°). Use `--smooth-normals` for such files.
- `--smooth-normals` is the seam-aware weld: vertices at one position (snapped to a `--weld` grid, default
  1 mm) share the normals of the faces around them that lie within `--angle` degrees (default 45, the vanilla
  rule) of each other and do not cross a `--split-at` seam (default `material`; `uv` also keeps UV seams hard;
  `none` lets only the angle decide). Faces are weighted by their corner angle. Vertex records, UVs, prelight and
  skin are untouched (only normals change), and a geometry whose shading would get flatter keeps its normals
  (`kept_normals`). On vanilla `premier` it moves the normal bend by 0.2° (10.64 → 10.86); on the benchmark cars
  it raises 0.81 → 1.50° and 2.42 → 3.47° and lowers the flat share 0.465 → 0.392 and 0.315 → 0.228. Most of
  their faceting is in the geometry (59 % of the chassis edges of one of them meet at less than 10°, 23 % at
  80-90°), which normals cannot round off; `--angle 89` rounds those folds too (3.47 → 7.97°), at the price of
  rounded box edges.
- With `--smooth-normals` or `--recalc-normals` the `changes` column also shows the shading of the undamaged
  high-detail parts before and after (`bend:0.81->1.50`, the area-weighted normal bend in degrees, the same number
  as lint `dff.flat_shading`); when `--recalc-normals` makes it flatter the answer warns `FLATTER`.
- `--recalc-bsphere` recomputes the bounding sphere of every geometry whose stored sphere does not enclose its
  vertices or is more than twice the tight one: centre = bounding-box centre, radius = the farthest vertex, in the
  frame's own space (atomics take their sphere from the geometry at run time). DragonFF writes these spheres in
  world space: a re-export of `premier` had 12 `dff.bsphere` lint warnings, 0 after the patch.
- `--material-color`: `N` is the row number from `satk formats dump … --level full`, `G:I` is geometry and
  slot; the colour is `R,G,B[,A]` or `#RRGGBB[AA]`. The geometry gets the MODULATE_MATERIAL_COLOR flag,
  otherwise the colour is not visible.
- Unchanged chunks are written byte for byte; changed ones get their sizes recomputed up the tree.
  A file in which nothing changed is not written (`unchanged`).

### JSON for `col write`

```json
{"models": [{
  "name": "mybox", "version": 3,
  "spheres": [{"center": [0, 0, 1], "radius": 1.5, "surface": "CONCRETE"}],
  "boxes": [{"min": [-1, -1, 0], "max": [1, 1, 2], "surface": 4}],
  "vertices": [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
  "faces": [[0, 1, 2, "TARMAC", 0]],
  "shadow": {"vertices": [[0, 0, 0], [1, 0, 0], [0, 1, 0]], "faces": [[0, 1, 2, 0, 0]]}
}]}
```

- A surface is a number, a name from the rwfury table (`TARMAC`, `GRASS_SHORT_LUSH`…, case-insensitive),
  `[material, flag, brightness, light]` or `{"material": …, "light": …}`. A COL2/3 face is `[a, b, c,
  material, light]`, a COLL face is `[a, b, c, material, flag, brightness, light]`.
- Face lighting: the last byte of a COL2/3 face is `light`, low nibble = day, high nibble = night (0..15 each;
  the game lights peds and cars standing on it with `light x 0.95 + 0.05`, and cars switch their headlights on
  below 0.05). A face without its own `light` takes the model's `"light"` (a byte, or `{"day": 15, "night": 3}`),
  else the value fitted from the model's `"prelight"` (the brightness 0..255 or `[r, g, b]` of the render mesh
  above it, or `{"day": …, "night": …}`), else `0x3F` (day 15, the dominant vanilla value; night 3) with the
  warning `FACE_LIGHT_DEFAULT`. A light of 0 makes peds dark and cars light up their headlights in daylight.
- In a sphere or box surface `[material, flag, brightness, light]` of a vehicle collision, `flag` is the car
  piece the primitive belongs to (`eCarPiece`: bumpers, doors, wheels...).
- When `bounds`, `flags` or `face_groups` are not given, they are computed the way Rockstar's files have them:
  flags `0x02` (spheres, boxes or faces present), `0x08` (face groups), `0x10` (shadow); face groups from
  81 faces on, contiguous ranges of at most 50 faces (faces are reordered); `"face_groups": "none"` means
  no groups.
- COL2/3 vertex coordinates are stored as `int16 / 128`: ±256 m range, 1/128 m step.
- `col export` writes the same plus `raw`: Rockstar's garbage bytes (the name tail after the NUL, vertex
  padding). With `raw`, `export` → `write` gives back the original file bit for bit; `--no-raw` drops them.

## How it works

| Module | What |
|---|---|
| `chunk` | lossless chunk tree: librw containers are split into children, everything else is kept as bytes; sizes are recomputed on write |
| `codecs` | typed codecs: geometry, material and clump Struct, frames, strings, Skin, night colours, MatFX, specular, BinMesh, 2dEffect |
| `dff` | `DffDoc`: texture renaming, RW version, night colours, normals (seam-aware smoothing), bounding spheres, material colour |
| `col` | exact COLL/COL2/COL3/COL4 codec, JSON, bounds, flags and face groups for new models |
| `img` | VER2/VER1 building and archive comparison |
| `roundtrip` | measurements on the game install (`rw roundtrip`, `tests/golden/rw_roundtrip.json`) |
| `vendor` | loads `vendor/rwfury` (MIT, 0.6.1, unmodified) |

**Why not rwfury.** rwfury's writers rebuild a file from a model, and on vanilla they give 0 of 15,356 DFFs and
0 of 10,169 COL records bit for bit. Lost on the way: the material's `unused` word, frame and texture filter
flags, the Breakable and atomic MatFX plugins, the garbage after the NUL in strings and COL names. So the writers
are satk's own, following the librw stream semantics (MIT). rwfury is vendored and used for surface names, as an
independent reader in tests and as the baseline of the golden numbers.

**Vanilla measurement** (`satk rw roundtrip`, about 40 s, numbers in `tests/golden/rw_roundtrip.json`):

| What | Bit for bit |
|---|---|
| DFF, chunk tree (all 15,356: gta3, gta_int, player, cutscene and loose files) | 15,356 (100%) |
| DFF, every Struct and plugin through the typed codecs | 15,356 (100%) |
| DFF, SA → VC → SA | 15,223 (99.1%): 117 are the Skin bone list (absent in the VC format), 16 are RW 3.5 files |
| COL, files (254) | 254 (100%) |
| COL, records through the codec (10,169) | 10,167: 2 broken records in `models/coll/peds.col`, which the game does not load |
| COL, `export` → `write` through JSON | 10,165 (+ 212 of 212 embedded in vehicles) |

**Known IMG-writer bugs (INU_Core `BUGS.md`).** A new archive is laid out before its first byte is written, so
the VER2 directory never overlaps the data. This is covered by `tests/rw/test_img.py`:

- 1, 63, 64, 65, 67, 100 and 3,000 entries;
- rebuilding a base of 64+ entries;
- a dense base plus 67 new entries;
- VER1 without a VER2 header;
- an `<IHH24s>` entry with a zero archive size;
- the 65,535-sector limit;
- names up to 23 characters with NUL, no duplicates.

## Limitations and known issues

- TXD is not written here: textures have `satk texture …` ([texmod.md](texmod.md)). 2DFX effects are not
  exported to JSON yet.
- Platform (PS2/Xbox) geometries are not restamped and get no normals.
- SA → VC → SA changes the order of the Skin bone list: librw writes it sorted. Semantically it is the same.
- `img build --base` copies entries as they are (with padding) and does not check their content.
- In an install without `vendor/rwfury` (a wheel without the `vendor` folder), surface names are unavailable:
  numbers work.

## Python API

```python
from satk.rw.dff import DffDoc
doc = DffDoc.parse(blob)
doc.rename_textures({"vehiclelights128": "mylights128"})
new_blob = doc.to_bytes()
```
