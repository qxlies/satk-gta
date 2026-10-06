# `satk fx2d`: 2dEffect (2DFX) entries of models as JSON

[Русская версия](../ru/fx2d.md)

Package: `satk.fx2d`.

## What it is

A model's DFF can carry a 2dEffect block per geometry: street-light coronas and their light shadows, particle
emitters (smoke, fountains, insects), ped attractors (benches, ATMs, shop browsing), entry/exit markers, road-sign
text, slot-machine trigger points, cover points and escalators. Editing them used to need a 3D editor plugin.
`satk fx2d` turns all entries of a DFF into one readable JSON document, writes a changed document back into a
copy of the DFF, copies entries between models (put the street light of `lamppost1` on your own lamp) and checks
them against the game's own resources (`effects.fxp`, `particle.txd`).

The round trip is exact: `dump` followed by `apply` of the unchanged JSON gives the identical DFF for all 1 851
vanilla models that have 2dEffect entries (18 795 entries). Game files are only read; everything is written to
`<workspace>\work\out\fx2d\`.

## Quick example

```powershell
satk fx2d dump model:lamppost1
satk fx2d check model:lamppost1
satk fx2d apply model:lamppost1 lamppost1.json --out docs-demo-lamp
satk fx2d copy model:lamppost1 models/gta3.img/vgseesc01.dff --filter light --offset 0,0,1 --out docs-demo-escalator
satk fx2d dump docs-demo-escalator.dff
satk fx2d roundtrip models/gta_int.img
```

What comes back (shortened: first, third, fourth and last command):

```json
{"ok":true,"cols":["fx","type","pos","info"],
 "rows":[["g0#0","light",[-0.44,0.05,3.2],"coronastar rgba 249,145,34,200 size 2.5 far 100 range 12 shadow 8 at_night"]],
 "file":"<workspace>/work/out/fx2d/lamppost1.json","source":"model:lamppost1 (models/gta3.img/lamppost1.dff)",
 "geometries":1,"geometry_count":1,"entries":1,"counts":{"light":1}}
{"ok":true,"file":"<workspace>/work/out/fx2d/docs-demo-lamp.dff","mode":"replace","geometries":[0],"entries":1,
 "identical":true,"bytes":6666,"check":{"errors":0,"warnings":0}}
{"ok":true,"file":"<workspace>/work/out/fx2d/docs-demo-escalator.dff","from":"model:lamppost1 (models/gta3.img/lamppost1.dff)",
 "mode":"add","copied":1,"types":{"light":1},"geometries":[0],"entries":2,"identical":false}
{"ok":true,"target":"models/gta_int.img","files":1901,"with_2dfx":170,"exact":170,"differ":0,
 "entries":{"attractor":123,"cover_point":99,"light":1165,"particle":10,"trigger_point":3},"with_keep":1175}
```

The file `lamppost1.json` (one entry per line):

```json
{"format": "satk.fx2d/1", "source": "model:lamppost1 (models/gta3.img/lamppost1.dff)", "geometry_count": 1,
 "geometries": [
  {"geometry": 0, "frame": "lamppost1_L0", "effects": [
   {"type": "light", "pos": [-0.43525124, 0.048412323, 3.202], "color": [249, 145, 34, 200], "corona": "coronastar",
    "shadow": "shad_exp", "corona_size": 2.5, "far_clip": 100.0, "range": 12.000001, "shadow_size": 8.0,
    "shadow_mult": 40, "shadow_z": 0, "flash": "default", "reflection": true, "flare": 0,
    "flags": ["fog1", "at_night", "update_height_above_ground"], "look_dir": [0, 0, 100],
    "keep": {"corona": "0001000100400000003100000094", "shadow": "00000021000000436f6c205370686572"}}
  ]}
 ]}
```

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk fx2d dump SRC [--out NAME] [--filter TYPE…] [--no-keep] [--full]` | — (`satk_op`) | all entries of a DFF → `work\out\fx2d\<name>.json`; table `fx/type/pos/info` (`g0#3` = geometry 0, entry 3), `counts` per type; `--full` also returns the entries as JSON |
| `satk fx2d apply DFF JSON [--mode replace\|add] [--geometry N] [--out NAME] [--no-check]` | — | writes the entries of a document (file or JSON text) into a copy of the DFF; `identical` says whether the result equals the input; the written geometries are checked |
| `satk fx2d copy SRC DST [--filter TYPE…] [--from-geometry N] [--to-geometry N] [--mode add\|replace] [--offset X,Y,Z] [--out NAME]` | — | copies entries from one model into a copy of another, optionally shifted |
| `satk fx2d check SRC [--fxp FILE] [--txd FILE] [--strict]` | — | a DFF or a JSON document: table `where/sev/code/msg`, `errors`, `warnings`; `--strict` fails with `CHECK_FAILED` on an error |
| `satk fx2d roundtrip [TARGET] [--no-keep]` | — | DFF → JSON text → DFF on the whole game, an IMG, a folder or a file: `exact`, `differ`, entries per type |

`SRC`/`DFF` is `model:<id|name>` or `dff:<name>` (through the index of `--profile`, default `vanilla`),
`models/gta3.img/<name>.dff`, or a path: absolute, relative to the game root, to the current folder or to
`work\out\fx2d\` (so `dump` → edit → `apply` → `dump` chains by file name). `--out` is a name under
`work\out\fx2d\` or an absolute path; the default is the input's name. Agents call every command through
`satk_op("fx2d.dump", {...})`.

## The JSON document

Top level: `format` (`satk.fx2d/1`), `source`, `geometry_count` and `geometries`: one object per geometry that
has a 2dEffect block, `{"geometry": N, "frame": "<frame name>", "effects": [...]}`. `apply` also accepts
`{"effects": [...], "geometry": N}` or a bare list of effects (geometry 0 or `--geometry`). With `--mode
replace` (default) the listed geometries get exactly the given entries (an empty list removes the block);
`--mode add` appends. Geometries that are not listed are not touched; a new block goes at the end of the
geometry's Extension, as in Rockstar's files.

Every entry has `type` and `pos` (`[x, y, z]`, relative to the object; road signs: world coordinates).
Entry keys by type:

| `type` (number) | Keys | Defaults for a hand-written entry |
|---|---|---|
| `light` (0) | `color` `[r,g,b,a]`, `corona`, `shadow` (texture names in `particle.txd`), `corona_size`, `far_clip`, `range` (point light), `shadow_size`, `shadow_mult`, `shadow_z`, `flash`, `reflection`, `flare` (0 none, 1 sun, 2 headlights), `flags`, `look_dir` `[x,y,z]` (×100) | the street light of `lamppost1` |
| `particle` (1) | `name` (an effect system of `effects.fxp`) | — |
| `attractor` (3) | `atype` (`atm seat stop pizza shelter trigger_script look_at scripted park step`), `queue_dir`, `use_dir`, `fwd_dir`, `script`, `probability`, `unk1`, `flags` | directions `[0,1,0]`, `script` `none`, `probability` 75 |
| `sun_glare` (4) | — (no data; the IDE `2dfx` section uses it, the DFF reader of the game does not) | — |
| `enex` (6) | `name` (interior, ≤ 7 characters), `enter_angle`, `radius` `[x,y]`, `exit` `[x,y,z]` (relative to `pos`), `exit_angle`, `interior`, `flags`, `sky`, `time_on`, `time_off` | `radius` `[2,2]`, `time_off` 24 |
| `roadsign` (7) | `text` (4 lines, `_` = space, trailing `_` omitted), `size` `[w,h]`, `rot` `[x,y,z]` (degrees), `lines` (1-4), `chars` (2, 4, 8, 16 per line), `color` (0-3) | `rot` 0, 4 lines of 16, colour 0 |
| `trigger_point` (8) | `id` (slot-machine wheel) | — |
| `cover_point` (9) | `dir` `[x,y]`, `usage` (`low_cover wall_to_left wall_to_right`) | `usage` `low_cover` |
| `escalator` (10) | `bottom`, `top`, `end` (`[x,y,z]`), `up` | `up` true |

Light `flags`: `check_obstacles fog1 fog2 without_corona only_long_distance at_day at_night blinking1
only_from_below blinking2 update_height_above_ground check_direction blinking3`. `flash`: `default random
random_when_wet anim_speed_4x anim_speed_2x anim_speed_1x unknown6 traffic_light train_crossing unused9
only_rain on5_off5 on6_off4 on4_off6`. Entry/exit `flags`: `unknown_interior unknown_pairing create_linked_pair
reward_interior used_reward_entrance cars_and_aircraft bikes_and_motorcycles disable_on_foot accept_npc_group
food_date_flag unknown_burglary disable_exit burglary_access entered_without_exit enable_access delete_enex`.
Enumerations also accept numbers, flag lists also accept an integer.

Exactness: floats are written with the fewest digits that give back the same 32-bit value (`12.000001` is
really stored); NaN and infinity appear as raw bits (`"0x7FC00000"`). `keep` holds the bytes the game ignores
(leftovers after a name's terminator, padding): 2 642 vanilla entries have them. Deleting `keep` (or
`dump --no-keep`) is always safe; the file is then rewritten with zero bytes there. `bytes` appears only on a
light of 76 instead of 80 bytes or an entry/exit of 40 instead of 44. Types 2 and 5, unknown numbers and odd sizes
are kept as raw hex in `data`. An unknown key, flag or value is an error that names the JSON path and suggests
the closest name (`colour` → `color`).

## Checks

`check`, and `apply`/`copy` on the geometries they write, report:

| Code | Severity | Meaning |
|---|---|---|
| `TYPE_NOT_READ`, `BAD_SIZE` | error | the game's DFF reader does not read this type or size |
| `NAME_TOO_LONG`, `NOT_FINITE`, `INVALID` | error | a name without room for its terminator, NaN/infinity, a JSON entry that cannot be encoded |
| `TEX_MISSING`, `NO_TEXTURE` | warn | corona/shadow texture not in `particle.txd` (it is not drawn) |
| `PARTICLE_MISSING`, `EMPTY_NAME` | warn | the particle is not an effect system of `effects.fxp` (no effect), or a name is empty |
| `NEVER_SHOWN` | warn | a light with neither `at_day` nor `at_night` |
| `RANGE`, `BAD_ENUM`, `DIR_NOT_UNIT`, `LOOK_DIR_ZERO`, `SCRIPT_NONE` | warn | values far outside what vanilla uses, unknown enumeration values, non-unit directions |
| `TEXT_TOO_LONG`, `TEXT_IGNORED` | warn | road-sign text that does not fit `chars` per line or `lines` |
| `FAR_FROM_MODEL`, `DUPLICATE` | warn | an entry far outside the model's bounding sphere; an entry listed twice |

`--fxp`/`--txd` check against a mod's own files; without them the profile's `models\effects.fxp` and
`models\particle.txd` are used (`RESOURCE_MISSING` when there is none). Vanilla baseline: no errors and 87
warnings over 1 851 models (68 lights far from their model, 16 road-sign text overflows, 3 others).

## How it works

The DFF is parsed into the lossless chunk tree of `satk.rw`: only the 2dEffect chunks of the written geometries
change, together with the sizes of their parent chunks; every other byte is copied. The bytes after the last RW
chunk (IMG sector padding) are not written to the output. The output is deterministic. `roundtrip` over the
whole vanilla game (15 356 DFFs) takes about 3 seconds.

To find models by their effects, query the index: `satk index query "SELECT d.name, f.type_name FROM fx2d f
JOIN dff d ON d.id = f.dff_id WHERE f.origin = 'dff' AND f.type_name = 'enex'"`.

## Limitations and known issues

- Only the DFF 2dEffect block is edited; the `2dfx` lines of IDE files (sun glare) are text and stay as they are.
- Positions of road signs are world coordinates (the game does not move them with the object), so copying a
  road sign to another model keeps it where it was in the world; use `--offset`.
- Types 2 (unused) and 5 (interior) have no decoded fields: they are kept as raw `data`.
- `apply` writes one DFF per run; put the results into an IMG with `satk img build` ([rw.md](rw.md)).

## Python API

```python
from satk.fx2d.doc import apply_doc, normalize, read_doc

doc = read_doc(dff_bytes)
doc["geometries"][0]["effects"][0]["color"] = [255, 0, 0, 200]
new_bytes, report = apply_doc(dff_bytes, normalize(doc))
```
