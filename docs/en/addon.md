# `satk mod add` and `satk data`: add-on models for Mod Loader, handling and weapon data

[Русская версия](../ru/addon.md)

Package: `satk.addon`. Mod Loader itself (what a mod changes, conflicts, merged lines) is on the
[modinspect page](modinspect.md); free ids on the [idmgr page](idmgr.md).

## What it is

Adding a car, skin, gun or object to San Andreas is mostly bookkeeping: a free model id, a vehicles.ide line,
handling, colours, upgrades, a GXT name, names that do not collide with anything, and all of it in the form Mod
Loader reads. `satk mod add` does that bookkeeping from a donor model: it copies the donor's data lines token by
token (only ids and names change), checks ids and names against the profile, and writes a ready Mod Loader
folder under `<work>/out/addon/<name>/`. `satk data` shows any record of `handling.cfg`, `weapon.dat`,
`carcols.dat` or `peds.ide`/`pedstats.dat` with units and meanings, and patches handling and weapon fields as a
Mod Loader folder, an MTA Lua snippet or a full copy of the file. The game folder is only read.

## Quick example

```powershell
satk data get handling infernus --field fMass fTractionMultiplier fMaxVelocity
satk data explain handling fTractionBias
satk data patch handling infernus fMass=1500 fTractionMultiplier=0.8 --profile vanilla
satk data get weapon PISTOL --field damage
satk mod add vehicle --dff dff:infernus --txd txd:infernus --like 411 --name infernus2 --game-name "Infernus II" --profile vanilla --force
```

What comes back (shortened):

```json
{"ok":true,"cols":["field","value","unit","meaning"],"rows":[["fMass",1400.0,"kg","Vehicle mass. …"],["fTractionMultiplier",0.7,"x","Overall tyre grip. …"],["fMaxVelocity",240.0,"km/h","Top speed …"]],"id":"handling:INFERNUS","file":"data/handling.cfg","lines":[101],"models":["model:411 infernus"]}
{"ok":true,"id":"handling:fTractionBias","kind":"car","key":"traction_bias","name":"fTractionBias","col":"L","type":"float","unit":"front share","range":[0,1],"meaning":"Split of grip between the axles: 1.0 all at the front, 0.0 all at the rear, 0.5 even. …","mta":"tractionBias"}
{"ok":true,"cols":["field","old","new"],"rows":[["fMass","1400.0","1500.0"],["fTractionMultiplier","0.70","0.80"]],"id":"handling:INFERNUS","target":"modloader","out":"<work>/out/addon/infernus-handling-modloader","files":["infernus-handling-modloader.txt"],"new":["INFERNUS     1500.0    2725.3 …"]}
{"ok":true,"cols":["field","poor","std","pro","cop","unit","meaning"],"rows":[["damage",25,25,25,25,"health","Damage per hit."]],"id":"weapon:PISTOL","weapon_id":22}
{"ok":true,"cols":["file","line"],"rows":[["vehicles.ide","612, \tinfernus2, \tinfernus2, \tcar, \t\tINFERNUS2, \tINFERN1, …"],["handling.cfg","INFERNUS2     1400.0 …"],["carcols.dat","infernus2, 12,1, 64,1, …"],["carmods.dat","infernus2, nto_b_s, nto_b_l, nto_b_tw"],["infernus2.fxt","INFERN1 Infernus II"]],"warn":["ADDON_VEHICLE: vehicle id 612 is outside 400-611: …","NEW_HANDLING_ID: …","GXT_KEY: GXT key INFERNU is used by the game; the vehicle uses INFERN1"],"id":"model:612","donor":"model:411 infernus","out":"<work>/out/addon/infernus2","files":["infernus2.dff","infernus2.fxt","infernus2.txd","infernus2.txt"]}
```

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk mod add vehicle\|ped\|weapon\|object --dff F --txd F [--col F] --like DONOR --name NAME [--id auto\|N] [--lod-dff F [--lod-txd F] [--lod-name N]] [--place X,Y,Z]` | — (through `satk_op`) | a Mod Loader folder that adds a new model with the donor's data lines; table `file/line` |
| `satk data get handling\|carcols\|peds\|weapon KEY [--field F ...]` | — | one record: every field, its value and unit (with `--field`: also the meaning) |
| `satk data patch handling\|weapon KEY field=value ... [--target modloader\|mta\|full]` | — | new values for one record; table `field/old/new` and the output folder |
| `satk data explain handling\|weapon\|peds [FIELD]` | — | a field (or flag bit): unit, range, type, column letter, editor and MTA names; without a field, all fields |

`mod add` options:

- `--dff`, `--txd`, `--col`: files; `dff:<name>` and `txd:<name>` copy the game's own model from the profile's
  IMG archives (a quick way to make a variant). Objects may leave out `--txd` and keep the donor's TXD;
  a `--col` with one model is renamed to the new name; from a COL with many models the one named like the new
  model or the donor is taken;
- `--like`: the donor, `model:411`, `411` or `infernus`; it must be of the kind you add;
- `--name`: model, TXD and file name (letters, digits, `_`, at most 19 characters, stored in lower case);
- `--id auto` (default) takes the first free id of the kind the way `satk id free` does (every layer of the
  profile index, modloader mods and readmes included, engine and SA-MP ids); `--id N` must be free;
- `--profile`: the game the donor, ids and names come from (default `installed`, your game);
- vehicles: `--game-name` (shown in game, written to an FXT file under a free GXT key of at most 7
  characters), `--handling own|donor` (copy the donor's handling lines under the handling id `NAME`, or share the
  donor's), `--cargrp donor|<number>|<label>` (add the car to car groups of cargrp.dat);
- weapons: `--weapon-type` = the donor's type (default: its weapon.dat lines get the new model), `none`, or a
  new type name;
- objects with a LOD: `--lod-dff` copies the LOD model (named by `--lod-name`, default the file's stem, e.g.
  `lodmybin`) and gives it its own `objs` line in the same IDE file: the next free id, the TXD of the object (or
  `--lod-txd`, which is copied and named like the LOD), draw distance 800 (the vanilla LOD median), flags 0; the
  LOD name is checked like any model name. `--place X,Y,Z` (objects) also writes `data/maps/<name>.ipl` with the
  object's `inst` line (the world position, identity rotation) and, with a LOD, the LOD's `inst` line right after it;
  the HD line ends with the index of the LOD line (`1`), the LOD line with `-1`; the readme gets the `IPL` line for
  gta.dat. Without `--lod-dff` the placement has no LOD link (`-1`);
- `--out NAME` (folder under `<work>/out/addon/`), `--force` (replace that folder), `--dry-run` (check and show,
  write nothing).

`data` keys: handling takes a vehicle (`model:411`, `411`, `infernus`) or a handling id (`INFERNUS`,
`handling:INFERNUS`); weapon takes a type (`PISTOL`, `weapon:22`, `22`), a weapon model (`colt45`, `model:346`)
or `aim:<anim group>`; carcols and peds take a model. Field names may be handling-editor names (`fMass`,
`vecCentreOfMass.z`), satk keys (`mass`), MTA names (`tractionMultiplier`) or `bike.`/`boat.`/`flying.` +
name for the extra lines of bikes, boats and aircraft. `data patch` writes `<work>/out/addon/<key>-<file>-<target>/`
(`--name` to change it): `modloader` = a readme with only the changed lines, `mta` = `handling.lua`/`weapon.lua`
with `setModelHandling`/`setWeaponProperty` calls, `full` = `data/handling.cfg` or `data/weapon.dat` with only
those lines changed. Weapon patches change every skill line unless `--skill poor|std|pro|cop`.

## How it works

- **What goes where.** Mod Loader reads data lines straight from `.txt` files in a mod (its readme feature)
  for vehicles.ide and peds.ide (`cars`/`peds`), handling.cfg, carcols.dat, carmods.dat, gta.dat and weapon.dat
  (gun and melee lines). `mod add` writes those lines to `<name>.txt` and checks each one against the readme
  patterns of Mod Loader 0.3.7. Weapons and objects get their own `data/maps/<name>.ide` plus a gta.dat line in
  the readme (Mod Loader loads an IDE only when gta.dat names it). Files without a readme reader (cargrp.dat,
  object.dat) are written as the game's file plus the change: a partial data file would make Mod Loader drop the
  records it lacks.
- **Byte-exact lines.** Lines are split into tokens and the separators between them; only the changed tokens are
  rewritten, in the style of the old value (`1400.0` -> `1500.0`, `0.70` -> `0.80`, hex case and width kept).
  Every vanilla `handling.cfg` and `weapon.dat` line is written back byte for byte (game tests).
- **Checks.** The donor exists and is of the right kind; the id is free; the model name and the DFF, TXD and COL
  file names are not used by the game's archives, its IDE files or (with a built index) any modloader mod; the
  vehicle GXT key is free in vehicles.ide and `text/american.gxt` (a used one gets a digit); a new handling id
  must not exist and fits 13 characters. The DFF and TXD must parse; a vehicle without embedded collision, a ped
  without a skin and textures missing from the TXD are warnings.
- **Reading the game.** Records come from the profile's own data files (what `gta.dat` loads), not from the
  result of other Mod Loader mods (`satk mod effective` shows that).

## Limitations and known issues

- The stock engine has fixed limits: vehicle ids outside 400-611, new handling ids and new weapon types need a
  limit adjuster (fastman92 LA); the warnings `ADDON_VEHICLE`, `NEW_HANDLING_ID` and `NEW_WEAPON_TYPE` say so.
  Vehicle audio settings for new ids are not written.
- `satk mod inspect` shows the vehicles.ide, handling, gta.dat and IDE-file parts of an add-on folder; it does not
  read readme lines for carcols.dat of a model that the same readme defines, nor carmods.dat and weapon.dat lines
  (Mod Loader itself does).
- COL files are shipped as streamed files (like a `gta3.img` entry).
- The engine reads at most 23 models per car group; `--cargrp` adds the name at the end of a group line, so a
  group that is already full gets the warning `CARGRP_FULL` (vanilla group 6 lists 26).
- The ten `SPECIAL01-10` ped lines do not match Mod Loader's ped readme pattern: such a donor gets an IDE file.
- MTA cannot set the monetary value or the lights of handling, any bike/boat/flying field, aim offsets or the
  `cop` pistol skill; those become warnings. Weapon animation frames are converted to seconds (frames / 30).

## Python API (if other packages use it)

```python
from satk.addon.game import GameData
from satk.addon import handling, weapon, fields
from satk.addon.add import add
from satk.addon.data import get, patch, explain
```
