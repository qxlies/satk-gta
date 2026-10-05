# `satk map` and `satk ipl`: map converters, binary IPL, area cleaning

[Русская версия](../ru/mapconv.md)

Package: `satk.mapconv`. Basis: our survey of the mapping ecosystem (SA-MP Pawn maps, MTA `.map`, IPL, map
cleaners).

## What it is

Converts maps between four formats: SA-MP/open.mp Pawn (`CreateObject`, `CreateDynamicObject(Ex)`,
materials, `RemoveBuildingForPlayer`), MTA `.map`, the game's text IPL and the canonical satk JSON. It also
checks a map against the index: do the models exist, are the coordinates inside the world, what do the
removals remove. Pawn is read **as literals only**: no code is executed. Everything is written to
`work\out\mapconv\`, next to a JSON report of what was lost.

Two services for the game's own map: `satk ipl decompile|compile` turns a binary IPL (`*_streamN.ipl` in
`gta3.img`) into text and back, byte for byte; `satk map clean` removes the placements of an area together with
their LODs, as modloader IPL copies, an MTA Lua script and SA-MP `RemoveBuildingForPlayer` lines. Model ids
(free ids, conflicts, remapping a mod) are on the [`satk id` page](idmgr.md).

## Quick example

```powershell
satk map convert ipl:lae2 --to mta
satk map validate ipl:lae2
satk map convert tests/mapconv/data/grove_sample.pwn --to mta
satk map validate tests/mapconv/data/grove_sample.pwn
satk ipl decompile ipl:lae2_stream0
satk map clean Ganton --dry-run
```

What comes back (shortened; the `grove_sample.pwn` sample is in the repository, run the commands from the
checkout root):

```json
{"ok":true,"file":"<workspace>/work/out/mapconv/lae2.map","report":"<workspace>/work/out/mapconv/lae2.mta.report.json","src":"ipl:lae2","from":"ipl","to":"mta","objects":377,"removals":0,"materials":0,"texts":0,"models":0,"errors":0,"warnings":0,"skipped":{"enex":10,"grge":1},"issues":[["info","TILT_IGNORED","file","1 inst have a small tilt …"]]}
{"ok":true,"cols":["sev","code","where","msg"],"rows":[["info","TILT_IGNORED","file","…"]],"n":1,"total":1,"next":null,"format":"ipl","profile":"vanilla","objects":377,"errors":0,"warnings":0}
{"ok":true,"file":"<workspace>/work/out/mapconv/grove_sample.map","from":"pawn","to":"mta","objects":5,"removals":1,"materials":1,"texts":1,"errors":0,"warnings":0}
{"ok":true,"cols":["sev","code","where","msg"],"rows":[["error","MODEL_UNKNOWN","obj 2 (line 7)","model 19379 is not in profile 'vanilla' (an SA-MP object id: validate with --profile samp)"]],"n":1,"total":1,"errors":1}
{"ok":true,"file":"<workspace>/work/out/mapconv/decompiled/lae2_stream0.ipl","src":"ipl:lae2_stream0","inst":377,"cars":5,"bytes":16384,"style":"vanilla","pad":"sector","exact":true}
{"ok":true,"cols":["sid","model","name","role","pos","area"],"rows":[["inst:lae2#7",714,"veg_bevtree2","obj",[2490.62,-1806.57,14.38],0],…],"total":524,"area":"ganton","removed":524,"hd":66,"lods":66,"objs":392,"calls":176,"kept_shared_lods":0,"ipl_files":5,"lod_check":{"files":5,"rows":1445,"kept":921,"hidden":524,"hidden_lods":0,"bad":0},"dry_run":true}
```

To check the round trip, compile the `file` that `ipl decompile` printed and compare it with the original:
`satk ipl compile <file> --compare ipl:lae2_stream0` answers `"same":true`.

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk map convert SRC --to mta\|pawn\|ipl\|json [--out PATH]` | — (`satk_op`) | reads `.pwn/.inc/.txt`, `.map`, `.ipl` (text or `bnry`), `.json` or an `ipl:<name>` SID from the index and writes another format; `file`, `report`, counters, `issues [sev, code, where, msg]` |
| `satk map validate SRC [--profile vanilla] [--strict]` | — | table `sev/code/where/msg` + `objects removals materials errors warnings`; `--strict` fails with `CHECK_FAILED` when there is an `error` |
| `satk ipl decompile SRC [--out PATH] [--no-names]` | — | a `bnry` file or `ipl:<name>` SID -> text IPL (`inst` + `cars`) in `work\out\mapconv\decompiled\`; `exact` = compiling it back gives the same bytes |
| `satk ipl compile SRC [--out PATH] [--pad auto\|sector\|none] [--compare FILE\|ipl:<name>]` | — | text IPL -> `bnry` in `work\out\mapconv\compiled\`; `same` and `first_difference` with `--compare` |
| `satk map clean AREA [--to ipl lua pawn] [--interior 0] [--models …] [--shared-lods] [--dry-run]` | — | removes the placements of a box `x0,y0,x1,y1`, a circle `x,y,r` or a zone (`Ganton`, `GAN1`) with their LODs; table `sid/model/name/role/pos/area`, `lod_check`, files in `work\out\mapconv\clean\<name>\` |

`convert` parameters:

- `--out`: a relative path and the default (`<source name>.<extension>`) go under `work\out\mapconv\`;
  an absolute path is used as is (an existing file outside `work\` only with `--force`). Without `--to` the
  format comes from the `--out` extension.
- `--pawn-func auto|CreateObject|CreateDynamicObject|CreateDynamicObjectEx`: `auto` uses the same function as
  the source, otherwise `CreateDynamicObject`.
- `--materials embed|drop`: in `.map`, materials are written as `<material>`/`<materialText>` children (a satk
  extension) or dropped. The report always has them.
- `--tilt warn|dontstream`: for IPL, see "Rotations".
- `--no-names`: do not look model names up in the index (for MTA object `id`s, IPL names and Pawn comments).

`validate` checks: coordinates are finite, `|x|,|y| ≤ 3000` (`OUT_OF_MAP`), `z` within `-200…2500` (`Z_RANGE`);
the model exists in the profile (`MODEL_UNKNOWN`; for SA-MP ids `11682…11753`, `18631…19999` it hints
`--profile samp`); duplicate objects and removals (`DUP_OBJECT`, `REMOVE_DUP`: a double removal crashes the
SA-MP client); material slots `0…15`, the material's model and texture exist in the index (`MAT_*`); a removal
hits something (`REMOVE_NOTHING`), and if it removes an HD object that has a LOD, it hints the LOD model id
(`REMOVE_NO_LOD`); SA-MP limits (1000 `CreateObject`, about 1000 removals). Without an index only the
structural checks remain, plus `warn: NO_INDEX`.

## How it works

- **Common model.** Every format is read into one scene (`satk.mapconv.scene`): objects (model, position,
  SA-MP Euler angles, interior/world with `-1` = "all", draw/stream distance, MTA fields, IPL LOD and flags,
  materials per slot), removals, DL custom models, diagnostics with a line number. JSON is the scene as is.
- **Pawn.** The tokenizer understands comments, strings with escapes, `0x…`, `Float:` tags, `{…}` arrays and
  constants (`OBJECT_MATERIAL_SIZE_*`, `STREAMER_OBJECT_SD`, `true`). A call with a non-literal argument
  (`x + 1.0`, `GetX()`) is skipped with `NOT_LITERAL` and the line. Handles are tracked through assignments
  (`tmpobjid = …`, `objs[3] = …`). Argument order differs: `SetObjectMaterialText(obj, text, index…)`, but
  `SetDynamicObjectMaterialText(obj, index, text…)`. The encoding is UTF-8, otherwise cp1251.
- **MTA `.map`.** Attributes as in the MTA map editor; `dimension="-1"` means all dimensions. What MTA lacks is
  kept in extensions MTA ignores: `allInteriors`, `drawDistance`, `streamDistance`, `lodIndex`, `iplFlags`.
  So Pawn → `.map` → Pawn is lossless (the test checks to 0.01).
- **Rotations.** SA-MP and MTA pass the angles to `CMatrix::SetRotate(x, y, z)` = `Rz·Rx·Ry`; they are copied
  as is. IPL stores the **conjugate** quaternion, and if `|qx|,|qy| ≤ 0.05` the game uses the heading only.
  Reading IPL repeats this rule (`TILT_IGNORED`), so the angles in `.map` are what the game shows. When
  writing IPL, tilts below ≈5.73° would be lost silently: `TILT_LOST`; `--tilt dontstream` sets the
  `dont_stream` flag on such objects, and the loader then takes the full rotation (when `qx` and `qy` are both
  non-zero; the flag's other effects are not studied).
- **Losses** on write are `LOST_*` warnings in `issues` and in the report: IPL has no materials, removals,
  dimensions or distances; Pawn has no MTA scale/alpha and no IPL LOD; `.map` has no DL custom models.
- **Checked against the game.** The test `tests/mapconv/test_game.py` converts all 220 vanilla IPLs (50,935
  placements) IPL → `.map` and compares them with the index: model, position (≤ 1e-6 m), rotation
  (≤ 1e-4°). About 2.5 s.

## Binary IPL

- **Format.** A 0x4C-byte header (magic, six counts, six offset/size pairs), `inst` records of 40 bytes
  (position, quaternion as stored, model, interior, LOD) and car generators of 48 bytes. Only `inst` and `cars`
  exist in binary form; `ipl compile` skips other sections with `LOST_SECTIONS`.
- **Exact text.** Floats are written as the shortest decimal that parses back to the same float32 (`-0` stays
  `-0`), interiors as unsigned numbers. The header layout and the padding are kept in a comment line
  `# satk-bnry: style=vanilla pad=sector`: every vanilla file has the size fields at 0 and zero padding to
  2048 bytes, like an IMG entry. A file that follows no known layout decompiles with `exact: false` and
  `NOT_EXACT`.
- **Checked against the game.** `tests/mapconv/test_game.py` decompiles and compiles all 190 binary IPLs of the
  clean copy (41,667 placements, 1,045 car generators): every file comes back bit for bit, in about a second.
- **LOD indexes** of a binary IPL point into the `inst` section of the parent text IPL (`lae2_stream0` ->
  `lae2.ipl`); they are copied as is. Compiling a text IPL with LOD links that did not come from `ipl decompile`
  warns `LOD_SPACE`.

## Cleaning an area

- **Selection.** Every placement whose position is inside the area, in interior `--interior` (`-1` = all),
  optionally within `--z z0 z1` and only `--models` (ids or names with `*` and `?`). Their LOD parents are
  followed wherever they stand. An LOD goes only when all its HD children go; one that also serves a placement
  outside stays (`LOD_SHARED`; `--shared-lods` removes it anyway, then `LOD_HIDDEN` counts the placements that
  lose their LOD). An LOD inside the area whose children are all outside stays too.
- **modloader copies** (`modloader\<file>.ipl`): full copies of every text and binary IPL that holds a removed
  placement, with the same lines in the same order. A removed line keeps its model, rotation, flags and LOD
  index and is moved underground (`z = -1000`): deleting lines would shift the LOD indexes of the rest of the
  file and of the `*_streamN` files that point into it. Only the `z` token (text) or the `z` float (binary)
  changes. Copy the folder into a modloader mod folder.
- **MTA** (`<name>.lua`): a server script with `removeWorldModel` on resource start and `restoreWorldModel` on
  stop. MTA matches model, 3D distance and interior and does not follow LOD links, so LODs get their own entries.
- **SA-MP** (`<name>.pwn`): a `stock` with `RemoveBuildingForPlayer` lines to call from `OnPlayerConnect` (no
  interior parameter; SA-MP keeps about 1000 removals per player: `REMOVE_LIMIT`).
- **Fewer calls.** Placements of one model and area code are merged into one sphere when no placement of that
  model that stays is inside it (checked in 2D against the whole profile, so 2D and 3D matching are both safe);
  otherwise the group is split, down to single placements with a radius of at most 0.25 m (`--no-merge` keeps
  one call per placement). Ganton: 524 placements, 176 calls.
- **LOD check** (`lod_check`): the copies are parsed back and compared row by row with the originals and the
  index (model, LOD index, position; removed rows differ only in `z`); `bad` must be 0.

## Limitations and known issues

- Pawn is literals only: loops, `#define` macros, arithmetic and coordinate arrays are not parsed
  (running AMX in a sandbox is not planned yet). Preprocessor lines are skipped.
- Texture Studio `.db` and `artconfig.txt` are not read yet; `map convert --to ipl` writes a text IPL (binary: `ipl compile`).
- MTA has no "all interiors": SA-MP objects with interior `-1` get `interior="0"` and `allInteriors="true"`;
  in IPL they get area 0 (`INTERIOR_ALL`; 13 would mean "visible in all").
- Removals are written to Pawn as a pair of lines (model and LOD) and to `.map` as one element with
  `lodModel`; the pair is not merged back on read.
- `bnry` LOD references point into the parent text IPL and are dropped on read (`LOD_EXTERNAL`).
- Non-uniform `scaleX/Y/Z` in `.map` is not supported (`BAD_ELEMENT`).
- `map clean` hides removed placements underground instead of deleting them; their collision and 2D effects
  (lights) stay there, out of sight. Scripts that look a building up by its position (a mission swapping a door
  model) no longer find it.

## Python API (if other packages use it)

```python
from satk.mapconv.pawn import read_pawn, write_pawn
from satk.mapconv.mta import read_mta, write_mta
from satk.mapconv.ipl import read_ipl, write_ipl
from satk.mapconv.rot import euler_quat, quat_euler, ipl_quat, ipl_euler
from satk.mapconv.validate import validate_scene
from satk.mapconv.bnry import decompile, compile_ipl, parse_bnry, encode_bnry, f32_text
from satk.mapconv.clean import parse_area, select, clean, removal_calls
```
