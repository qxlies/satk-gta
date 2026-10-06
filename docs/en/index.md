# `satk index`: SQLite asset index, profiles and queries

[Русская версия](../ru/index.md)

<!-- Checked 2026-10-05: vanilla index (schema v3) built in a private work dir; the quick
     example and the Python API were run, answers below are real and shortened. -->

Package: `satk.index`. Schema: `src/satk/index/schema.sql`; golden numbers: `tests/golden/index_vanilla.json`.

## What it is

One SQLite file per load profile (`work\index\<profile>.sqlite`) with everything the game loads: all
archives and loose files, TXDs and textures, DFFs with materials, COL, IDE definitions, IPL placements with
LODs and bounding boxes, zones (with their in-game names from `text/american.gxt`:
`asset find ganton --kind zone` → `zone:gan1` "Ganton"; `view goto --id zone:gan1` flies to the zone centre),
animations and full-text search. Since schema v3 it also holds the data files that `gta_sa.exe` loads by name:
water (`water.dat`, `water1.dat`), `timecyc.dat`, car colours (`carcols.dat`), `handling.cfg`, ped type relations
(`ped.dat`) and object physics (`object.dat`); see "Game data (schema v3)" below.
An index of an older schema (v1, v2) is not opened (`INDEX_MISSING: … has schema v2, this satk needs v3`):
rebuild it with `satk index build`.

The index answers "what does the game see": which file wins when names collide, which TXD a model uses and
through which parent a texture was found, what overrides what (SA-MP, mods). Game files are only read; the
package writes only into `work\index\` (`<profile>.sqlite`, `hashcache.sqlite`). The vanilla build takes
about 8 s.

## Quick example

```powershell
satk index build
satk asset get model:411
satk asset refs model:17613 --rel inst
satk asset find ws_rooftarmac1 --kind tex --limit 3
satk world near 2495 -1687 --r 30 --limit 5
satk index query "SELECT sid, name, n_inst FROM v_model ORDER BY n_inst DESC LIMIT 3"
satk asset get tcyc:extrasunny_la/12
satk world near -1500 -1700 --r 50 --kinds water
satk index verify
```

What comes back (shortened, the second command):

```json
{"ok":true,"id":"model:411","name":"infernus","sec":"cars","txd":"infernus","layer":"vanilla","n_inst":0,
 "tex":{"total":13,"missing":0},"geo":{"verts":3573,"tris":3072,"bs":[-0.03,0.09,-0.06,3.13]},
 "handling":{"mass":1400.0,"max_vel":240.0,"accel":30.0,"gears":5,"drive":"4","engine":"P","brake":11.0,
 "steer_lock":30.0,"value":95000},
 "colors":[[12,1],[64,1],[123,1],[116,1],[112,1],[106,1],[80,1],[75,1]],"color_rgb":{"1":"#f5f5f5","12":"#5d7e8d",…},
 "links":{"ide":"ide:data/vehicles.ide","dff":"dff:infernus","txd_chain":["txd:infernus","txd:vehicle"],
 "col":"col:infernus_col","col_via":"embedded","handling":"handling:infernus"}}
```

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk index build [--profile P \| --all] [--jobs N]` | — | build the index of a profile (`vanilla`, `installed`, `samp`, `game`; without `--profile`: `[index] default_profile`); the file is replaced atomically |
| `satk index status [--deep]` | — | per profile: built or not, fresh or not, size, numbers of models, textures and placements; `state` is `not configured` for a profile without a game folder and `alias of game` for `vanilla` without a clean copy |
| `satk index verify [--profile P] [--golden F] [--metrics]` | — | compare with `tests/golden/index_<profile>.json` (Appendix A) |
| `satk index hash [--profile P] [--recompute]` | — | content hash; equal for two builds of the same files |
| `satk index bench [--profile P] [--n N]` | — | p50/p95 of `get`, `find`, `refs`, `near r=100`; `ok` when every p95 < 50 ms |
| `satk index query "<SELECT…>" [--params …] [--db index\|re\|notes]` | `index_query` | read-only SQL: writes, `ATTACH`, `PRAGMA x=…` → `READ_ONLY` |
| `satk index diff A B [--kind model\|txd\|tex\|file]` | `index_diff` | what was added, overridden, changed or removed between two profiles |
| `satk asset find <query> [--kind K]` | `asset_find` | search by name, exact matches first; `'*grove*'` is a wildcard pattern |
| `satk asset get <sid> [--fields a,b]` | `asset_get` | object by SID with `links` to related SIDs (SIDs of other packages go to their providers) |
| `satk asset refs <sid> [--rel R]` | `asset_refs` | without `--rel`: how many relations of each kind; with `--rel`: a table |
| `satk world near X Y [Z] [--r R \| --box x0,y0,x1,y1] [--match aabb\|center] [--area N\|any] [--lod hd\|lod\|all] [--kinds inst,item,zone,water]` | `world_near` | placements nearby, nearest first; `water` = `water.dat` polygons (v3) |

The scan runs in worker processes (`--jobs N`, or the environment variable `SATK_JOBS=N`; `1` scans in-process).
When the machine forbids worker processes (a restricted sandbox, `WinError 5`) the build scans in-process by
itself and says so in `warn`; the index and its content hash are identical.

Every query takes `--profile` (default `vanilla`; without a clean copy that is an alias of the `game` profile,
your game folder, see [install.md](install.md)). When DAT/IDE/IPL/IMG files changed after the build, the answer
carries `warn: ["INDEX_STALE: … (satk index build)"]`.

Relations for `--rel`: `model` → `inst tex dff txd col ide overrides`; `tex` → `models same_pixels txd`;
`txd` → `textures models parent children file`; `inst` → `model lod hd_children ipl near`;
`file` → `parsed shadowed_by shadows`; `ipl` → `inst`; `ide` → `models`; `zone` → `inst`; `ifp` → `anims`;
`handling` → `models`; `tcyc` → `hours`.

## Game data (schema v3)

`gta_sa.exe` loads these files by name, not through `gta.dat`. The parsers live in `satk.formats` (see
[formats.md](formats.md)); loading into the index is `src/satk/index/gamedata.py`. Their `source` rows are as
before (`kind = other`), but they take part in the freshness check: editing one gives `INDEX_STALE`.

| File | Tables | SID | What you see |
|---|---|---|---|
| `data/water.dat`, `data/water1.dat` | `water_quad`, `water_rtree` | `water:<n>`, `water:water1/<n>` | water quads and triangles, flags (1 visible, 2 shallow), waves; `world near --kinds water` |
| `data/timecyc.dat` | `timecyc` | `tcyc:<weather>/<hour>`, `tcyc:<weather>` | 23 weathers × 8 hours (0, 5, 6, 7, 12, 19, 20, 22): sky and light colours, draw distance, fog, water, post effects |
| `data/handling.cfg` | `handling` | `handling:<id>` | car lines, `!` bikes, `%` boats, `$` aircraft; flags decoded |
| `data/carcols.dat` | `carcol`, `car_color` | — | palette (index → RGB) and the colour variations of each model |
| `data/ped.dat` | `ped_rel` | — | who hates, dislikes, likes or respects whom (`hate dislike like respect`) |
| `data/object.dat` | `object_data` | — | mass, breaking, collision response, effect |

How this shows up in answers:

- `asset get model:<car>`: `handling` (the main fields), `colors` (pairs or quadruples of palette indexes) and
  `color_rgb`, the link `links.handling`; for a ped, `pedtype_rel` (the relations of its type from `ped.dat`);
  for an object listed in `object.dat`, `physics` (`dmg_effect`, `col_response`, `fx`; `loaded: false` marks a
  line after `*` that the engine never reads).
- `asset get handling:infernus`: every field of the line in the file's units (km/h, m/s²), `model_flags` and
  `handling_flags` by name, `models` = the cars that use this `handling` in `vehicles.ide`.
- `asset get tcyc:extrasunny_la/12`: colours as `#rrggbb`, `water` as `#rrggbbaa`; `tcyc:extrasunny_la` lists the
  hours and draw distances. A weather can be given by number: `tcyc:0/12`.
- `asset find infernus` also finds `handling:infernus`; `asset find sunny` finds the `tcyc:*` weathers.
- SQL views: `v_vehicle` (the `vehicles.ide` fields + `handling` + number of colour variations) and `v_ped`
  (the `peds.ide` fields: type, stat, voices, radio).

The index reproduces how the engine reads these files, including its mistakes (they are listed in `meta.notes`):

- `timecyc.dat`, line 320 (`RAINY_COUNTRYSIDE`, 8 PM) starts with a single `255` instead of three numbers: the
  engine reads 20 values shifted, the rest stay from 7 PM; the row has `nread = 20`.
- `carcols.dat`: colour 98 is written as `77.93,96` (read as 77, 93, 96); `moonbeam` names colour 227, which is
  not in the palette (0–126).
- `ped.dat`: a second `Respect` line of a type replaces the first, so `CIVMALE` respects only `CIVFEMALE`.
- `object.dat`: the line `*********melee weapons` stops reading, so the last record (`flowera`) is not loaded.
- `handling.cfg`: everything after `;the end` is ignored; `$ RCRAIDER` contains `0.1s` (the stray `s` is skipped).
- The profile on SA-MP's `.two` files (`samp`) reads `HANDLING.two` and `timecyc.two`, as the SA-MP client does.

Vanilla numbers (`tests/golden/index_vanilla.json`): 301 water quads + 6 triangles (`water1.dat`: 267),
184 `timecyc.dat` rows, 210 + 13 + 12 + 24 `handling.cfg` records (all 212 cars find theirs), a palette of 127 colours,
199 models with 1 157 colour variations, 113 `ped_rel` pairs, 995 `object.dat` lines (994 loaded).

The SIDs `water:`, `tcyc:` and `handling:` are understood by `asset get/refs/find` and the index API. Other
packages (notes, viewer) will accept them once they are added to the kinds of `satk.core.ids` (the core owner
changes that list).

## How it works

Pipeline (`build.py`): find the profile's files and the IMG registration order (`formats.layout`) → attribute
each file to a layer → parse the blobs in 12 processes (`scan.py`, parsers from `satk.formats`) → pick the winners
(`resolve.py`) → links, LODs, bounding boxes, R-tree (`link.py`) → data files (`gamedata.py`, v3) →
FTS5 trigram search (`search.py`) → `meta` + `content_hash` + atomic replace.

- **Layers.** A file is `vanilla` when the pair (path, sha256) is in `gta-sa-clean\MANIFEST.sha256`; otherwise
  the rules `[layers].rules` apply (`SAMP/**` → `samp`, `modloader/<mod>/**` → `modloader:<mod>`); otherwise it
  is `modded`. SHA-256 sums are cached in `work\index\hashcache.sqlite` by (root, path, size, mtime); the cache is
  first filled from the manifests in `tools\data\manifests`, so 5 GB never need hashing.
- **Winners.** Blobs: "the first registered archive wins" for a pair (namespace, name); loose files compete
  inside `main` after all IMGs. IDE: for one ID the higher layer priority wins (`vanilla` 0 < `samp` 10 <
  `modded` 20 < `modloader` 30), then the later file and line. COL: the first occurrence of a name (DAT `COLFILE`,
  then loose files, then IMG).
- **Model textures** (`model_tex`): own TXD → `txdp` parents → `vehicle`. The implicit parent `vehicle` is set for
  the TXD of every car, so textures of tuning parts that share the TXD are found through it too.
- **Placements.** The quaternion is stored as in the IPL; the world rotation is its conjugate; `rz` in the
  answers is already the world value. The bounding box comes from the COL (else from the DFF sphere, else it is a point) →
  `inst_rtree`. The LOD of a binary `xxx_streamN` is an index into the text `xxx`; `is_lod` means "someone points
  at me".
- **SIDs.** `dff:`/`txd:`/`tex:` name the active version; a version overridden by another layer is `@<layer>`; one
  overridden inside its own layer is reachable only as `file:<archive>/<entry>`. `model:<id>@<layer>` is an
  inactive definition.
- **Reading.** `IndexDB` opens the file with `mode=ro` and `PRAGMA query_only`, one connection per thread;
  `open_index()` caches it and reopens the file after a rebuild (by mtime). On Windows an open file cannot be
  replaced by a rename; then the new build is copied into it with the SQLite backup API.
- **Determinism.** Rows are inserted in a fixed order; `content_hash` is the sha256 of a canonical dump of the key
  tables (no mtimes). Two builds of the same files give one hash (tested).

Vanilla numbers: 21 147 blobs, 4 046 TXDs, 32 878 textures, 15 356 DFFs, 14 832 definitions, 50 935
placements, 6 103 LOD links (all resolved), a file of about 58 MB; `get` takes about 0.4 ms, `find` and
`near r=100` about 1.5 ms.

## Limitations and known issues

- No incremental build: every build is a full one (about 8 s).
- The R-trees (`inst_rtree`, `item_rtree`, `water_rtree`) cannot be read through `index query`: the rtree module
  prepares internal statements that the "SELECT only" authorizer forbids. Use `world near` for spatial queries.
- `--kinds` of `world near` is checked against `api.NEAR_KINDS`; the MCP tool description does not mention `water`
  (the `tools/list` budget), but it works.
- `asset find` without a pattern returns only the exact matches when there are any (`warn: MORE: N …` tells how
  many other names contain the string); for a substring use `'*text*'`.
- The search parameter is called `query`, not `q`: `q` is taken by the global flag `-q`.
- The `path` sections of text IPLs (a Vice City leftover, 165 152 lines) are not stored in `ipl_item`.
- Profile `samp`: the position of `GTA_INT.IMG` in `default.two` is an assumption, recorded in
  `meta.assumptions`.
- `tests/golden/index_vanilla.json` explains two differences from the first design in `_comment`: the prototype's
  `atomic 46 108` counted the light Structs of the sources (the right number is 19 556); 5 of the 467 "unresolved"
  are materials with an empty texture name, not cutscene.img.

## Python API (if other packages use it)

```python
from satk.index.api import open_index, override_index, FakeIndexDB

db = open_index("vanilla")
mf = db.model_files(411)                 # dff, txd_chain [infernus.txd, vehicle.txd], col
data = db.read_blob(mf.dff)              # bytes of the IMG entry (open_ro)
tex = db.texture_ref("tex:bistro/vent_64")   # where the mip0 pixels are
db.match_runtime(17613, (2489.3, -1668.5, 12.3))  # ['inst:lae2_stream0#4']
db.get("handling:infernus")["car"]["max_vel"]      # 240.0 (v3; api.parse_sid parses the SID)
db.near(-1500, -1700, r=10, kinds=("water",))      # water:0

with override_index(FakeIndexDB()):      # in other packages' tests
    ...
```
