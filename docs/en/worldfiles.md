# World data files: text, zones, water, time cycle, population, radar

[Русская версия](../ru/worldfiles.md)

Package: `satk.worldfiles`.

## What it is

A total conversion changes more than models: the game's text (`text/*.gxt`), the zone names (`info.zon`,
`map.zon`), the water (`water.dat`), the sky and fog (`timecyc.dat`), the street population (`popcycle.dat`)
and the radar map (144 `radarNN.txd` tiles). satk reads each of these files with the engine's own rules and
writes them back: unchanged data gives the same bytes (all five stock GXTs, both zone files and both water
files round-trip bit for bit), edits touch only what changed, and every output is a Mod Loader folder (or an
MTA Lua snippet for water) under `<workspace>\work\out\worldfiles\`. The game is only read.

## Quick example

```powershell
satk gxt get CRED001 FEM_OK
satk gxt patch american MYZONE="Old Town" CRED001="My mod team" --out docs-gxt
satk zone list --at 2495,-1687
satk zone add OLDTWN --box 2400,-1720,0,2530,-1620,200 --text "Old Town" --dry-run
satk water list --at 1000,-2000,300 --limit 3
satk timecyc get SUNNY_LA 12 --field sky_top far_clip
satk popcycle get GANGLAND weekday 20 --field max_peds max_cars
satk radar export --tile 32 --out radar-small.png
```

What comes back (shortened):

```json
{"ok":true,"cols":["key","table","text"],"rows":[["CRED001","MAIN","Producer"],["FEM_OK","MAIN","OK"]]}
{"ok":true,"cols":["key","table","old","new"],"rows":[["MYZONE","MAIN","-","Old Town"],["CRED001","MAIN","Producer","My mod team"]],"files":["docs-gxt.fxt"]}
{"ok":true,"rows":[[150,"GAN1","GAN","Ganton",0,1,[2222.56,-1722.33,-89.08],[2632.83,-1628.53,110.92]],[82,"LA","LA","Los Santos",...]],"shown":"GAN1","shown_text":"Ganton"}
{"ok":true,"rows":[["#150 GAN1","partial",...],["#82 LA","inside",...]],"warn":["PARTIAL_OVERLAP: ..."],"zones":379,"dry_run":true}
{"ok":true,"rows":[[127,"quad",592.0,-2112.0,976.0,-1896.0,0.0,"visible"],...],"counts":{"quads":301,"tris":6,"verts":1021},"free":{"quads":0,"tris":0,"verts":0}}
{"ok":true,"cols":["weather","hour","sky_top","far_clip"],"rows":[["SUNNY_LA",12,[30,117,210],800.0]]}
{"ok":true,"cols":["zone_type","day","hours","max_peds","max_cars"],"rows":[["GANGLAND","weekday","20-22",8,9]]}
{"ok":true,"path":"<workspace>/work/out/worldfiles/radar-small.png","size":384,"tiles":144}
```

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk gxt get [KEY ...] [--search TEXT] [--gxt german]` | — (through `satk_op`) | texts of keys in every table, a text search, or the tables with counts |
| `satk gxt export [american\|file.gxt] [--format json\|dir]` | — | the GXT as a JSON document, or a folder of `<TABLE>.txt` files with `KEY text` lines (UTF-8) |
| `satk gxt write <json\|dir> --out american.gxt` | — | a GTA SA GXT (version 4), verified by parsing it back |
| `satk gxt patch <american\|file> KEY="text" ... [--target fxt\|full]` | — | `fxt`: an FXT whose keys override the GXT; `full`: a patched `text/american.gxt` |
| `satk gxt keys [--scan]` | — | how many keys have names; `--scan` finds more in a modded game and caches them |
| `satk fxt write KEY="text" ... \| file.json \| file.txt` | — | an FXT for CLEO (`CLEO_TEXT`) or Mod Loader |
| `satk zone list [--at X,Y] [--name GLOB] [--file info\|map]` | — | zones, their labels and shown names; which name the game shows at a point |
| `satk zone check [--file ...]` | — | engine rules, missing labels, partial overlaps, the zone pools |
| `satk zone add NAME --box X0,Y0,Z0,X1,Y1,Z1 [--text "Name"]` | — | a Mod Loader copy of `info.zon` with the new zone (+ FXT for the name) |
| `satk zone remove NAME\|INDEX ...`, `zone export`, `zone write <json>` | — | remove zones; JSON document out and back |
| `satk water list [--at X,Y,R]` | — | water polygons, pool use, engine issues |
| `satk water add --rect X0,Y0,X1,Y1 --z Z [--shallow] [--target modloader\|mta]` | — | a full `water.dat` copy, or `water.lua` with `createWater` for MTA |
| `satk water remove IDX ...`, `water export`, `water write <json>` | — | remove polygons; JSON document out and back |
| `satk timecyc get [WEATHER] [HOUR] [--field ...]` | — | time cycle values by name; one point shows all fields |
| `satk timecyc patch WEATHER HOUR field=value ...` | — | `sky_top=30,117,210`, `far_clip*=1.5`, `sky_top.g+=10`; Mod Loader folder |
| `satk timecyc diff A [B]` | — | two files (paths or profiles) field by field |
| `satk popcycle get [ZONE] [DAY] [HOUR]`, `popcycle patch ZONE DAY HOUR field=value ...` | — | population per zone type, day and 2-hour slot |
| `satk radar export [--tile 128]` | — | the 144 tiles as one PNG (north up, 500 m per tile) |
| `satk radar build <image> \| --from-map` | — | 144 `radarNN.txd` (DXT1, the stock container), PSNR check, preview |

Weathers are names (`SUNNY_LA`), globs (`*_SF`), numbers or `all`; hours are the time points of the file
(`0 5 6 7 12 19 20 22`) or `all`. Timecyc fields: `amb amb_obj dir sky_top sky_bot sun_core sun_corona
sun_size sprite_size sprite_bright shadow light_shadow pole_shadow far_clip fog_start light_on_ground
low_clouds bottom_clouds water postfx1 postfx2 cloud_alpha high_light_min water_fog_alpha dir_mult`
(components `.r .g .b .a`; `postfx1/2` are `a r g b`). Popcycle zone types: `BUSINESS DESERT ENTERTAINMENT
COUNTRYSIDE ... AIRPORT_RUNWAY` (`satk popcycle get` lists them).

## How it works

- **GXT.** Keys are stored as JAMCRC32 hashes of the upper-case name, sorted for the game's binary search;
  every table after `MAIN` repeats its name and starts on a 4-byte boundary. The document keeps the string
  order of the file, which is what makes the writer bit-exact. Key names come from a list of 14 047 stock
  names shipped with satk (85 % of the 16 588 keys); unknown keys appear as `0x1A2B3C4D` and write back
  unchanged. Texts use the code page of the SA fonts: ASCII plus the accented letters of the European versions
  (`é` is byte `0x9E`, `ß` is `0x96`), checked against the stock German, Spanish, French and Italian texts.
  `--charset cp1251` is for font mods drawn in a Cyrillic layout.
- **FXT.** CLEO and Mod Loader look FXT keys up before the GXT, so `gxt patch` (default `--target fxt`)
  changes or adds texts without replacing `american.gxt`.
- **Zones.** Names and labels are `char[8]` (7 characters); boxes are truncated to integers; the shown name is
  the smallest zone (width + height) containing the player. The pools are fixed: 380 navigation zones and 39
  map zones, and the engine creates one of each itself, so vanilla `info.zon` (378 zones) has room for one
  more; `zone add` and `zone check` warn above that.
- **Water.** Quads must be axis-aligned rectangles (bottom-left, bottom-right, top-left, top-right). Vanilla
  `water.dat` uses all 301 quads, all 6 triangles and all 1021 vertices of the single-player pools: adding water
  needs a limit adjuster or removing other polygons. MTA:SA has its own pools (512 quads) and adds water with
  `createWater`: `water add --target mta`.
- **Timecyc, popcycle.** Patches replace only the number tokens of changed values; comments, tabs and line ends
  stay, so patching and patching back gives the original bytes. Vanilla `timecyc.dat` line 320 has 20 readable
  values (the engine keeps the rest from the line before); a patch of that line writes it whole with the values
  the engine actually used. Mod Loader takes `timecyc.dat` and `popcycle.dat` of a mod as whole files.
- **Radar.** Tile `N = row * 12 + column`, row 0 at the north edge, column 0 at the west edge, 500 m each. Each
  TXD holds one 128 x 128 DXT1 texture named like the file; satk writes the stock container exactly (the only
  difference to the stock files is junk the original tools left in unused name bytes). `radar build` decodes
  its own tiles again and reports the PSNR against the source (the stock radar re-encoded: 43.8 dB).
  `--from-map` draws a schematic map: land, `water.dat`, outdoor building footprints from the index, car paths.

## Limitations and known issues

- GXT files of GTA III and Vice City (other layouts) are not read; 16-bit GXTs are read and written as UTF-16.
- `--target fxt` needs key names (an FXT cannot address a key by its hash); use `--target full` for those.
- Water polygons can be added only as rectangles; triangles are kept and listed but not created.
- satk writes no MTA resource for the radar: `radar build` targets single player (Mod Loader) and engine
  forks.
- `timecyc` is read in the stock 8-hour layout and in the 24-hour layout of *timecycle24* files; other
  extended formats (more weathers) are not.

## Python API

```python
from satk.worldfiles.gxt import read_gxt, write_gxt, key_hash
from satk.worldfiles.zon import parse_zon_doc, write_zon, zones_at
from satk.worldfiles.water import parse_water_doc, write_water
from satk.worldfiles.timecyc import TimecycFile, select_rows
from satk.worldfiles.radar import build_tiles, read_tiles
```
