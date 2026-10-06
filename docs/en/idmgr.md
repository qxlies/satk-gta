# `satk id`: free model ids, conflicts between mods, remapping a mod

[Русская версия](../ru/idmgr.md)

Package: `satk.idmgr`. Binary IPL and map cleaning, the other half of this toolset, are on the
[mapconv page](mapconv.md).

## What it is

An add-on car, skin, weapon or object needs a model id nobody else uses, and two mods that picked the same id
silently break each other. `satk id` answers three questions from the asset index: which ids are free in every
profile you play (vanilla, installed with its modloader folders, SA-MP), which ids and model names are defined
twice, and how to move a mod's ids to a free range. Nothing in the game folder is changed: a remapped mod is a copy
under `<work>/out/idmgr/`.

## Quick example

```powershell
satk id free --kind vehicle --count 3
satk id free --kind object --count 20 --contiguous
satk id conflicts --profile installed
satk id free --kind ped --target samp-dl --count 2
```

What comes back (shortened):

```json
{"ok":true,"kind":"vehicle","target":"sp","ids":[612,613,614],"blocks":["612-614"],"count":3,"range":"400-19999","free_in_range":3689,"taken_in_range":15911,"profiles":["installed","samp","vanilla"],"max_id":19999,"max_from":"engine","capacity":{"store":212,"used":212},"warn":["STORE_FULL: the stock engine has 212 vehicle model slots and a profile already defines 212: …","ADDON_VEHICLE: vehicle ids outside 400-611 need fastman92 LA (…)"]}
{"ok":true,"kind":"object","ids":[3136,3137,…,3155],"blocks":["3136-3155"],"count":20,…}
{"ok":true,"cols":["sev","code","id","name","where","other"],"rows":[],"n":0,"total":0,"profile":"installed","definitions":16286,"errors":0,"warnings":0}
{"ok":true,"kind":"ped","target":"samp-dl","ids":[20001,20002],"blocks":["20001-20002"],"range":"20001-30000","free_in_range":10000,"warn":["NO_ARTCONFIG: …"]}
```

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk id free --kind vehicle\|ped\|weapon\|object [--count N] [--range A-B] [--contiguous]` | — (through `satk_op`) | the first free ids of a range across every built profile; `capacity` = the stock store size vs the most ids one profile defines |
| `satk id conflicts [MOD ...] [--profile installed] [--all]` | — | the table `sev/code/id/name/where/other` of ids and names defined twice |
| `satk id remap MOD --to A-B [--map old=new] [--ids A-B]` | — | a copy of the mod with its ids moved; `map` (`old`, `new`, `name`), `changes` (`file`, `kind`, `ids`) and `<work>/out/idmgr/<mod>.remap.json` |

`id free` options:

- `--profile vanilla installed`: only these profiles (default: every profile whose index is built);
- `--range 612-799,15000..15099`: where to look, in this order; `a..b` may go down (`-1000..-1100`);
  the default is 400 (vehicles) or 1 up to the id limit;
- `--max-id N`: the last valid model id. Default 19999 (`NUM_MODEL_INFOS = 20000` of the stock engine), or
  `dff - 1` from a fastman92 LA ini (`dff = N` / `FILE_TYPE_DFF = N`) when every checked profile has one;
- `--mods FOLDER ...`: mods not installed yet whose ids count as taken;
- `--no-samp`: ignore SA-MP's ids (client skins 1-6, 8, 42, 65, 74, 86, 119, 149, 208, 265-273, 289,
  skins 300-311, objects 11682-11753 and 18631-19999);
- `--target samp-dl`: SA-MP 0.3.DL / open.mp custom ids instead: objects -1000..-30000 (`AddSimpleModel`),
  skins 20001..30000 (`AddCharModel`); `--artconfig FILE` marks the ids an `artconfig.txt` already uses.

`id conflicts` codes: `CLASH` (error: two mods, one id, different models), `REPLACES` (a mod puts another
model name on a base-game id), `DUPLICATE` (two mods define the same id and name), `DUP_IN_MOD`, `NAME_TWICE`
(one model name on two ids: their DFF/TXD files collide in modloader); with `--all` also `REDEFINES` (a mod
rewrites a base-game line with the same name) and `BASE` (SA-MP reusing stock ids). `--no-use-profile` compares
only the given mod folders.

`id remap` moves every id the mod adds, old ids in ascending order to the next free ids of `--to`; a line that
redefines a base-game model under the same name is a replacement and stays (`kept`). `--map 15000=16000`
pairs win, `--ids` limits which old ids move, `--name` sets the output folder under `<work>/out/idmgr/`,
`--dry-run` writes nothing, `--changed-only` writes only the files that changed, `--force` replaces an earlier
output folder.

## How it works

- **Who takes an id.** For each profile: the IDE lines the game loads (the index's `model` table, every layer),
  IDE files that are present but not loaded by the DAT files (modloader folders, `SAMP\samp.ide` in a
  single-player install, stray copies) and IDE-like readme lines in modloader folders (modloader reads
  `vehicles.ide`/`peds.ide` lines from readme files). Plus the ids the engine uses without an IDE line: 2-3
  (cutscene loader values), 290-319 (special characters, cutscene objects), 374-399 (temporary collision,
  clothes and hand models). Sources: the ModelInfo.h and eModelID.h headers of gta-reversed.
- **Store limits.** The stock engine has 212 vehicle, 278 ped, 51 weapon and 14,000 + 70 object model slots;
  the vanilla game already uses 212, 276, 50 and 14,045 of them. `STORE_FULL` warns when the ids asked for do
  not fit; `STORE_LOW` warns when fewer than 10 slots are left (vanilla: 2 ped and 1 weapon slot); then a limit
  adjuster is needed (Open Limit Adjuster, fastman92 LA). SA-MP's own definitions are not
  counted (SA-MP raises its limits itself).
- **Remapping keeps bytes.** Only the id tokens change: the first field of `objs tobj anim cars peds weap
  hier` and `2dfx` lines, the model of text IPL `inst` and `cars` lines, the model fields of binary IPL
  records (patched in place), and readme lines whose first field is an old id and whose second field is that
  model's name. Spacing, comments, line ends and every other file stay as they were.

## Limitations and known issues

- IMG archives inside a mod are copied unchanged (`NOT_REWRITTEN`): the ids of binary IPLs inside them stay.
  Scripts (`.cs`, `.cm`, `.lua`, `.pwn`) may use model ids satk cannot see (`SCRIPTS`).
- Data files that refer to models by name (`handling.cfg`, `carcols.dat`, `carmods.dat`, audio settings) need
  no change; files that use numeric ids outside IDE/IPL/readme lines are not rewritten.
- The SA-MP client skin list and the DL ranges come from SA-MP/open.mp documentation, not from code.
- MTA allocates ids at run time (`engineRequestModel`), so it needs no free id.

## Python API (if other packages use it)

```python
from satk.idmgr.free import open_profiles, taken_ids, free_ids
from satk.idmgr.scan import profile_defs, scan_mod, scan_mods
from satk.idmgr.conflicts import conflicts
from satk.idmgr.remap import plan, rewrite, rewrite_bytes
```
