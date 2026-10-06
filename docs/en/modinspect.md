# Mod inspector: what a mod changes and how Mod Loader combines mods

[Русская версия](../ru/modinspect.md)

Package: `satk.modinspect`. Rules ported from Mod Loader 0.3 (thelink2012/modloader, MIT) and its
datalib (BSL-1.0); notices in `src/satk/modinspect/NOTICE-modloader.txt`.

## What it is

`satk mod` answers the modder's question "what does my mod break and what does it conflict with" the way
[Mod Loader](https://github.com/thelink2012/modloader) would load it, without starting the game:

- `mod inspect` reads one mod (a folder, a `.zip` read in place, an `.img` or one file) and lists what changes
  compared with a profile's game files: replaced and new streamed files, data-file records (`field old->new`),
  records a data file drops, new and overridden IDE IDs, readme lines Mod Loader merges;
- `mod conflicts` combines several mods (the `modloader/` folder of a profile or the folders/zips you pass) and
  names the winner of every place two mods touch, with the rule that decided it;
- `mod effective` prints the data line the game finally gets (for example the `handling.cfg` line of model 411)
  and, without a key, can write the whole merged file;
- `mod check` turns findings into issues, and adds `asset lint`; with `--engine inu` it runs INU Check
  instead, if you installed it yourself.

Everything is read only. The only writer is `mod effective --save`, into `work\out\mods\effective\<profile>\`. <!-- linkcheck: ignore -->

## Quick example

```powershell
satk mod conflicts --profile game
satk mod effective handling 411 --profile game
satk mod inspect data/handling.cfg --profile vanilla
```

The first command lists the conflicts of the mods installed in the game's `modloader/` folder; the second shows
the `handling.cfg` line model 411 uses after the merge; the third inspects a single file (a path relative to the
profile root also works): the clean copy's own file, so nothing differs.

Output for a synthetic mod (`--table`; the acceptance test `tests/modinspect/test_ops.py`): a DFF, a partial
`handling.cfg` and a readme with a `carcols.dat` line:

```text
kind  target             change   by                   detail
----  -----------------  -------  -------------------  ------------------------------------------------
dff   dff:fastcar        replace  cars/fastcar.dff     model 401 fastcar in models/gta3.img
data  handling:FASTCAR   modify   data/handling.cfg:2  mass 1200->1250, max_vel 250->260
data  handling:TESTBOAT  modify   data/handling.cfg:3  boat.thrust_y 0.6->0.9
data  handling:NEWCAR    add      data/handling.cfg:5  NEWCAR 1600.0 3000.0 2.0 ...
data  handling:^0        remove   data/handling.cfg    the mod's file lacks it and replaces the game's; ...
data  carcols:testcar    modify   readme.txt:3         colours 1,2,2,1->3,3,0,0
```

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk mod inspect PATH [--profile vanilla] [--change ...] [--kind ...]` | — (via `satk_op`) | rows `kind, target, change, by, detail`; summary `changes`, `unchanged` (files identical to the game's), `new_ids` |
| `satk mod conflicts [MODS...] [--profile installed] [--with-installed] [--ml-profile NAME] [--priority name=N ...]` | — | rows `target, kind, winner, losers, rule`; `mods` in install order; `DROPS` warnings |
| `satk mod effective FILE [KEY] [--profile installed] [--mod PATH ...] [--with-installed] [--save]` | — | `FILE`: `handling`, `carcols`, `ide`, `gta`, `default`, `object`, a data file name or an IDE; `KEY`: handling id or model ID/name, model ID, colour index, path |
| `satk mod check PATH [--profile vanilla] [--engine satk\|inu] [--exe PATH] [--sev info] [--no-lint]` | — | rows `rule, sev, file, msg, sid`: ID collisions, files Mod Loader skips, dropped game records, IDs over the limit, engine capacity (`mod.capacity`) and [`asset lint`](lint.md) findings |

`change` values: `replace` (a game file is swapped), `add` (new file or record), `modify` (a record differs),
`remove` (a game record disappears), `new-id`, `override-id` (the ID belongs to another model in the game),
`load` (ASI/CLEO), `ignored` (Mod Loader will not use the file; the detail says why).

## How it works

- **Capacity (`mod.capacity`).** The stock single-player engine has fixed stores: 212 vehicle, 278 ped, 51
  weapon, 14,070 object, 169 timed-object and 92 clump model slots, 255 COL file slots (slot 0 is the engine's
  own), 5,000 TXD slots, 100 IDE `2dfx` entries and 400 entry-exits. The vanilla game leaves few of them free
  (2 ped, 1 weapon, 3 COL, 3 `2dfx`, 24 entry-exits). For every store the mod adds to, `mod check` adds one row:
  `error` when the total is over the stock size, `warn` when fewer than 10 slots are left, `info` otherwise.
  Limit adjusters raise these stores. When `satk texture budget` is available, a mod with TXD/DFF files also
  gets a `mod.streaming` hint about the 50 MiB streaming memory.

- **Files** (`classify.py`): the first Mod Loader plugin that accepts a file handles it: FX names (`hud.txd`,
  `particle.txd`, `vehicle.txd` ...), ASI/CLEO, std.data (data files by name, IDE/IPL by path, `*.txt` readmes),
  sprites (TXDs in a folder named txd), std.stream (DFF, TXD, COL, IFP, binary IPL, `carrec*.rrr`, `nodes*.dat` only inside
  a `*.img` folder). Names starting with `.` are skipped. A mod IDE/IPL loads only if a `gta.dat` line names
  its path, or its file name names exactly one line.
- **Who wins a replaced file** (`modloader.py`): mods install in order of priority (1..100, default 50,
  `modloader.ini` `[Profiles.<name>.Priority]`), then by name **length**, then by name; the last one wins. So
  at equal priority `longname` beats `zz`. `IgnoreMods`, `IgnoreFiles`, `IncludeMods` with `ExcludeAllMods`,
  `ExclusiveMods`, profile `Parents` and `.profiles/*.ini` are honoured.
- **Data files** (`merge.py`, `traits.py`): with one mod file and no readme lines the mod's file replaces the
  game's. Otherwise Mod Loader merges per record: a game record that **any** merged mod file lacks is
  **removed** (a partial `handling.cfg` deletes every vehicle it does not list); among changed values the
  **least common** wins and a tie goes to the mod installed **first**; the game's value wins only when no mod
  changes it. Records: `handling.cfg` by id (case-sensitive; boat/bike/plane lines travel with their standard
  line), `carcols.dat` by colour index and model, IDE by model ID, `gta.dat` by directive and path,
  `object.dat` by model. Numbers compare as 32-bit floats, so `1400` and `1400.0` are equal.
- **Mod Loader's reading quirks** (kept): `#` and `;` start a comment anywhere, so `;the end` does not stop
  Mod Loader (the game stops there); extra trailing tokens are ignored; the first line of an id wins inside a
  merged file; readmes over 60000 bytes are not read.
- **The game under the mods** (`base.py`) is read from the profile root: IMG directories, the IDE files the DAT
  files load, the data files. No index build is needed. Files whose bytes equal the game's copy get no row and
  are counted in `unchanged`.

## Limitations and known issues

- Merge rules are ported for `handling.cfg`, `carcols.dat`, IDE, `gta.dat`/`default.dat` and `object.dat`;
  other merged files (`weapon.dat`, `ped.dat`, `water.dat` ...) are compared line by line and a conflict on them
  is reported per file. Text IPLs are replaced, never merged (as in Mod Loader).
- The tie order of merged files assumes Mod Loader keeps its virtual file list in install order (its
  `unordered_multimap`; Mod Loader's own IPL code assumes the same). Verify a tie in the game if it matters.
- Readme lines: handling, `carcols`, `vehicles.ide`/`peds.ide`/`veh_mods.ide` and `gta.dat` patterns are
  recognised; `weapon.dat` and `carmods.dat` readme lines are not.
- An `.img` inside a mod replaces the game archive of the same name; other IMGs need an `IMG` line in a
  `gta.dat`. Zips are read in place; 7z/rar are not supported (extract them).
- INU Check is optional and never downloaded. Its command line (`SATK_INU_CHECK_ARGS`, default `--json {path}`)
  and JSON layout are not verified against a real copy; the adapter (`inu-adapter/1`) accepts the usual field
  names and falls back to `SEVERITY: file: message` lines with an `INU_FORMAT` warning.

## Python API (if other packages use it)

```python
from satk.modinspect.base import BaseGame
from satk.modinspect.modloader import read_folder
from satk.modinspect.world import World
from satk.modinspect.merge import merge

base = BaseGame("installed")
with World(base, read_folder(base.root).loaded) as w:
    trait, plan, _ = w.plan("handling.cfg")
    outcome = {o.key: o for o in merge(plan.stores, trait)}[("veh", "INFERNUS")]
```
