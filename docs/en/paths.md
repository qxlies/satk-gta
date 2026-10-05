# `satk paths`: car, boat and pedestrian paths (NODES*.DAT)

[Русская версия](../ru/paths.md)

<!-- Checked 2026-10-05: the quick example was run on the vanilla profile; the answers
     below are real and shortened. -->

Package: `satk.paths` on top of the vendored gta-flow core (`vendor/gtaflow`, MIT).

## What it is

The San Andreas path network is 64 regions `nodes0.dat` … `nodes63.dat` inside `models\gta3.img`: car, boat and
pedestrian nodes, navigation nodes (navis: lanes and direction) and links between nodes. `satk paths` reads them
from the profile's IMG archives **read-only**, stores them in a separate database
`work\index\paths-<profile>.sqlite` (the asset index is not touched) and answers "which paths are near this point"
and "where does this node lead". `export` writes a JSON overlay of an area for the viewer, Blender and plots;
`compile` rebuilds the regions with the gta-flow compiler and proves that an unchanged network comes out
**byte for byte** identical, while an edited node changes only its own region. Everything is written under
`work\`: the database into `work\index\`, files into `work\out\paths\<profile>\`.

## Quick example

```powershell
satk paths import
satk paths near 2495 -1687
satk paths node 15:6
satk paths export --area 2400,-1750,2600,-1600 --kind car
satk paths compile
```

What comes back (shortened):

```json
{"ok":true,"profile":"vanilla","db":"<workspace>/work/index/paths-vanilla.sqlite","reused":false,"areas":64,"nodes":68237,"vehicle":30587,"car":28991,"boat":1596,"ped":37650,"navis":31466,"links":143622,"sources":{"models/gta3.img":64},"check":"valid: 0 errors, 4 warnings","network_sha256":"7a0b8988…","seconds":0.94}
{"ok":true,"cols":["node","kind","pos","d","width","flood","links"],"rows":[["15:668","ped",[2496.0,-1686.12,12.38],1.33,0.25,5,["15:667","15:669","15:671"]],["…"]],"n":20,"total":43}
{"ok":true,"id":"15:6","kind":"car","pos":[2502.0,-1669.75,13.0],"width":0.0,"flood":1,"spawn":15,"behaviour":0,"flags":["switched_off","switched_off_orig","not_highway"],"links":[{"to":"15:5","dist":4,"navi":"15:17","lanes":[0,1]},{"to":"15:7","dist":6,"navi":"15:19","lanes":[1,0]}],"navis":["15:19"],"region":15}
{"ok":true,"path":"<workspace>/work/out/paths/vanilla/car_2400_-1750_2600_-1600.json","nodes":64,"segments":74,"navis":74,"bytes":27709}
{"ok":true,"profile":"vanilla","areas":64,"identical":64,"byte_identical":true,"oracle":"valid: 0 errors, 4 warnings","out":"<workspace>/work/out/paths/vanilla/compiled","written":0,"seconds":5.6}
```

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk paths import [--profile vanilla] [--force]` | — | the 64 regions from the profile's IMG into `work\index\paths-<profile>.sqlite`; a repeat with unchanged archives answers `reused: true` in milliseconds |
| `satk paths near X Y [--r 30] [--z Z] [--kind all\|vehicle\|car\|boat\|ped] [--limit 20]` | — | nodes within the radius, nearest first: address `area:idx`, kind, position, distance, width, `flood`, linked nodes |
| `satk paths node AREA:IDX` | — | one node: flags, `spawn`/`behaviour`, links with length, navi and lanes in both directions, navis attached to it |
| `satk paths export [--area minx,miny,maxx,maxy] [--kind ...] [--name N] [--no-navis]` | — | JSON overlay `satk.paths.overlay/1` in `work\out\paths\<profile>\<name>.json` |
| `satk paths compile [--edits JSON\|@file] [--write changed\|all\|none] [--name compiled]` | — | rebuild through gta-flow, compare with the source bytes and with an independent oracle; `manifest.json` and the regions in `work\out\paths\<profile>\<name>\` |

Parameters and answers:

- **Node address** `15:6` = region 15, index 6 inside it (as in the file). Regions form an 8×8 grid of squares of
  750 units starting at (−3000, −3000); a node moved into another square changes its region on `compile`.
- **Kinds:** `car` = cars, `boat` = car nodes with the water flag, `ped` = pedestrians; `vehicle` = `car` + `boat`.
- **Lanes** `lanes: [a→b, b→a]` in `node` and in overlay segments: the number of lanes in each direction from the
  navi of that segment (`[1,0]` is one-way traffic). `flood` is the number of the connected component: a car in
  component 1 cannot drive to a node of component 2.
- `near`: `--z` switches to 3D distance; nothing found gives `warn: EMPTY`. The first call of any command without
  a database imports it (`warn: IMPORTED`); when the IMG archives changed after the import you get
  `warn: INDEX_STALE` and the hint `satk paths import`.
- `export --area`: four comma-separated numbers. When the first one is negative, write it with `=`:
  `--area=-100,-200,100,200`. Without `--area` the whole map is exported (about 20 MB, about 3.5 s). The file has
  `nodes` (exact coordinates), `segments` (both ends, their positions, length, navi, lanes, the junction byte
  `inter`) and `navis`.
- `compile --edits`: a JSON object `{"area:idx": [x, y, z]}` (move a node; `[x, y]` keeps the height) or
  `{"area:idx": {"pos": [...], "width": 2.0, "spawn": 0..15, "behaviour": 0..15}}`. In PowerShell it is easier to
  put the JSON into a file and pass `--edits @edits.json`. `--write changed` (the default) writes only the changed
  regions, `all` all 64, `none` only `manifest.json`. Old `nodes*.dat` in the `<name>` folder are deleted before
  writing.

An example edit (node 15:6 moved by 2 units along X): region 15 changes, the other 63 match byte for byte,
the oracle reports no errors:

<!-- docs-smoke: skip the quoted JSON depends on the shell -->
```powershell
satk paths compile --edits '{"15:6":[2500,-1669.75,13]}' --name moved
```

The resulting `nodes15.dat` replaces the file of the same name from `gta3.img` (for example as a Mod Loader mod
file). satk does not change the game.

## How it works

- **Source.** The profile's archives are taken in the engine's registration order
  (`satk.formats.layout.archives`, namespace `main`): the first archive that has `nodes<N>.dat` wins. In the
  vanilla game all 64 regions are in `models\gta3.img`; the engine never reads the copies in `data\Paths\`, and
  satk leaves them alone too.
- **Parsing** is the vendored gta-flow `codec`: lossless, including the reserved sections and the IMG sector
  padding at the end. On import an independent gta-flow oracle checks the bytes of every region (vanilla: 0
  errors and 4 known warnings `exceptional attached navi` in regions 6 and 14).
- **Database** (`PRAGMA user_version` = 1): tables `node`, `navi` and `link` with decoded fields and `area` with the
  source bytes of the regions; `compile` builds from them. A fingerprint of the archives (size and mtime) decides
  whether a new import is needed. `near`/`node` queries take milliseconds; importing vanilla takes about 1 s,
  `compile` 5–7 s.
- **Compiling** goes the same way as in gta-flow: `document.import_files` → edits (`document.apply`) →
  `compiler.compile_document` → `oracle.check`. Every region is compared with its source; `manifest.json` lists
  `identical`/`changed` and the sha256 sums. Everything is deterministic: the same input gives the same files.
- **Vendoring.** `vendor/gtaflow` is the gta-flow core at revision `c3d9ad4` without changes (the author's
  `LICENSE` and `NOTICE.md` are kept, hashes in [`VENDORED.md`](../../vendor/gtaflow/VENDORED.md)).
  `satk.paths.vendor` loads it under the name `satk_vendor_gtaflow` without touching `sys.path`.

## Limitations and known issues

- Only the PC "compact" format (as in gta-flow): coordinates within ±4096, up to 1024 navis per region,
  64 map regions. The engine builds the interior regions itself; they are not in the files.
- Edits are limited to existing nodes (position, width, `spawn`, `behaviour`). gta-flow can make new roads,
  junctions and traffic lights, but `satk paths` does not expose that yet; see the gta-flow documentation.
- Mod layers (Mod Loader) are not taken into account: only the IMG archives from the profile's DAT files are read.
- Not checked in the game: `compile` proves the format and the topology, not that cars will drive as intended.
- `vendor/gtaflow` exists only in a checkout of the repository and in the portable zip; a `pip` installation and
  a checkout without it answer `DEPENDENCY`.

## Python API

```python
from satk.paths.db import import_profile, open_paths
from satk.paths.build import compile_network, export_overlay

db, warnings = open_paths("vanilla")
with db:
    row = db.node(15, 6)            # sqlite3.Row: x, y, z, kind, flags ...
res = compile_network("vanilla", edits={"15:6": [2500, -1669.75, 13]}, name="moved")
```
