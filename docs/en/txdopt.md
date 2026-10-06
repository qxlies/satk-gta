# `satk texture optimize|audit|budget`: smaller TXDs and the streaming memory budget

[Русская версия](../ru/txdopt.md)

Package: `satk.txdopt`. It reads TXD through `satk.formats.txd`, encodes with the DXT encoder of `satk.texmod` and
learns which models use a TXD from the profile index.

## What it is

Mods are often slow or crash because their textures are far bigger than the game expects: 2048×2048 uncompressed
textures, copies of the same texture in every TXD, textures no model uses. `audit` lists what is wrong and what a fix
would save, `optimize` writes smaller copies of the TXDs (the input is only read; the output goes to
`<work>/out/txdopt/<out>/`), and `budget` shows how many bytes of streaming memory the models and textures of the
game, a mod or one place of the map need, compared with vanilla and with the game's 50 MiB default limit.

## Quick example

```powershell
satk texture audit txd:bistro --limit 2
satk texture optimize txd:bistro --max 128 --out bistro_small --limit 2
satk texture budget --area 2495 -1666 150 --limit 2
```

What comes back (shortened):

```json
{"ok":true,"cols":["txd","texture","issue","format","bytes","saving","detail"],"rows":[["bistro.txd","deco_chair_1","uncompressed","X8R8G8B8 256x256",262144,229376,"X8R8G8B8 -> DXT1"],["…"]],"total":52,"summary":{"cols":["issue","count","bytes","saving","fix"],"rows":[["uncompressed",26,1769472,1540096,"texture optimize (--dxt auto)"],["no_mips",26,1769472,-590516,"texture optimize --mips"]]},"txds":1,"textures":26}
{"ok":true,"cols":["txd","texture","action","before","after","saved","psnr"],"rows":[["bistro.txd","deco_chair_1","resize+dxt","X8R8G8B8 256x256","DXT1 128x128",253952,34.84],["…"]],"total":26,"out":"<work>/out/txdopt/bistro_small","summary":{"bytes_before":1772840,"bytes_after":208168,"saved_pct":88.3,"psnr_min":26.91,"psnr_mean":36.29,"…":"…"},"lint":{"info":26}}
{"ok":true,"cols":["id","kind","bytes","vanilla","delta","users"],"rows":[["txd:lae2roads","txd",1937408,1937408,0,20],["…"]],"summary":{"scope":"area","models":206,"total":14333952,"total_mib":13.67,"limit":{"bytes":52428800,"source":"kb fact streaming.memory (verified)","used_pct":27.3},"instances":431,"…":"…"}}
```

A mod folder works the same way: `satk texture optimize <mod folder> --max 512 --drop-unused --dedupe`, then copy
`<work>/out/txdopt/<mod>/` over the mod (same relative paths).

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk texture audit TARGET [--max 1024] [--issue I...] [--unused] [--keep G...]` | — (through `satk_op`) | findings sorted by bytes saved (`txd, texture, issue, format, bytes, saving, detail`) plus a `summary` table per issue with the fix |
| `satk texture optimize TARGET [--max N] [--mips] [--dxt auto] [--drop-unused] [--dedupe] [--share] [--keep G...] [--out NAME] [--full]` | — | optimised copies in `<work>/out/txdopt/<NAME>/` with `txdopt.json` (every row) and `README.txt`; rows carry the PSNR |
| `satk texture budget [--profile P\|MOD] [--area X Y R] [--sort size\|delta] [--kind txd\|dff]` | — | streaming bytes of DFF + TXD per resource (`id, kind, bytes, vanilla, delta, users`) and the totals against vanilla and the limit |

Targets (`TARGET`, and a mod for `budget --profile`):

- a game TXD: `txd:<name>`, `file:<path>` or `<img>/<entry>` (`models/gta3.img/bistro.txd`); <!-- linkcheck: ignore -->
- a `.txd` file, a mod folder, a `.zip` (read in place) or an `.img` (its entries): absolute, relative to the current
  folder, or relative to the profile root (`models/gta3.img`, `modloader/mymod`). <!-- linkcheck: ignore -->

`optimize`, per texture, in this order:

- `--drop-unused`: drop textures that no DFF of the TXD's models names. The models come from the IDE files of the
  input and from the index (`txdp` children included); each DFF is taken from the input when the mod ships it,
  otherwise from the game. A TXD keeps every texture when no model loads it (`hud`, `particle`, scripts,
  paintjobs), when it is `vehicle.txd`, when weapon, ped or cutscene models use it (the game also finds
  `<weapon>ICON`, crosshairs and clothes by name), or when a DFF is missing (`warn: KEPT_WHOLE`). Names starting
  with `#` or `remap` stay; add names that SA-MP `SetObjectMaterial`, MTA shaders or scripts use with `--keep`.
- `--dedupe`: a second texture with the same name and the same pixels in one TXD is dropped. Textures identical in
  several TXDs are reported (`warn: SHAREABLE`); `--share` moves them into one new parent TXD
  (`<out>_shared.txd`, next to the TXDs or inside their IMG) and appends `txdp` lines to a copy of the mod's IDE.
  Only TXDs of map objects (`objs`, `tobj`, `anim`) whose models are all defined by the mod's IDE files and that have
  no `txdp` parent or child qualify.
- Size: sides become powers of two (the nearest one, upscaling at most 30 %; `--no-pot` keeps them), and `--max N`
  halves both sides until the long side fits. Exact halvings use the alpha-weighted box filter of the mip builder,
  other ratios Lanczos on premultiplied RGBA; a 1-bit alpha texture stays a cut-out.
- Format (`--dxt`): `auto` turns uncompressed and paletted textures into DXT1 (opaque), DXT1 with 1-bit alpha
  (alpha only 0/255) or DXT5 (smooth alpha), and fully opaque DXT3/DXT5 into DXT1 **losslessly** (PSNR 100);
  `dxt1` forces DXT1, `dxt5` uses DXT5 for any alpha, `keep` changes no format. A conversion that would not make
  the texture smaller is skipped; sides that are not multiples of 4 stay uncompressed.
- `--mips`: a full chain down to 1×1 for textures with one level; stored levels that stay valid are copied, so
  adding mips never touches level 0. A resized texture with mips gets a full chain again.

The answer is the table `txd, texture, action, before, after, saved, psnr` (largest saving first; `before`/`after`
like `X8R8G8B8 256x256` → `DXT1 128x128 m8`), `summary` (bytes before/after, sector-rounded `stream_*`, counts,
`psnr_min`/`psnr_mean` of re-encoded textures), `lint` (the `txd.*` lint counts of the written files) and the
paths `out`, `report`. Warnings: `LOW_PSNR` (under 30 dB), `KEPT_WHOLE`, `SHAREABLE`, `DUP_NAME`, `STALE` (files of
earlier runs in the output folder; nothing is deleted), `UNSUPPORTED` (PS2/Xbox textures and broken TXDs are kept).

`audit` issues: `oversized` (long side over `--max`), `uncompressed`, `npot`, `no_mips` (one level, side 64 or more;
a negative saving is what mips cost; never for TXDs of vehicles, peds, weapons and vehicle upgrades, whose vanilla
textures have one level: the field `one_level` counts those TXDs by class), `dxt1_holes` (DXT1 with transparent
texels while the alpha flag is off: they render black), `alpha_unused` (alpha flag on, every texel opaque),
`duplicate` (same pixels as an earlier texture), `dup_name`, `unused` (with `--unused`) and `platform`.

`budget`: `--profile` is a profile (`vanilla`, `installed`, `samp`) or a mod folder/.zip/.img laid over `--base`
(vanilla) the way Mod Loader loads it: its DFF/TXD files replace the files of the same name, its IDE definitions,
`txdp` lines and IPL placements are added. Without `--area` every active model counts (`users` = models); with
`--area X Y R` the exterior placements whose bounding box reaches the circle (`users` = placements, `--lod all|hd|lod`).

## How it works

- **Streaming bytes.** The game accounts each streamed model or TXD by its size in the IMG archive (2048-byte
  sectors) when it loads and frees it, so `budget` adds the IMG directory sizes of a model's DFF and its TXD chain
  (own TXD, `txdp` parents) — the numbers of the index (`blob.size`); a loose Mod Loader file counts its size
  rounded up to a sector. Files outside the IMG archives (`vehicle.txd`, `models/txd/*`) are not streamed and do not
  count. On vanilla the totals equal the sizes of the blobs `model_files` returns (checked by the game tests).
- **Limit.** The kb fact `streaming.memory`: `CStreaming::Init2` writes 52 428 800 bytes (50 MiB) over the
  `stream.ini` value. MTA, SA-MP and streaming-memory mods raise it; vehicles, peds, animations and collisions use
  the same memory.
- **Which textures a model uses.** Every printable, NUL-terminated string of the DFF (and its suffixes) counts as a
  name: material textures and masks, MatFX environment textures, Breakable plugin names and 2dEffect coronas. On all
  15 354 vanilla DFFs this finds every material name the index knows.
- **Quality.** PSNR of level 0 against the source at the output size (RGB; with alpha, premultiplied RGB plus
  alpha). `bistro.txd` with `--max 128`: 88 % smaller, 36.3 dB on average (26.9 dB minimum on `StainedGlass`, a
  hard case for DXT1). Identical textures are encoded once.
- **Safety and determinism.** Inputs are opened read-only; every write is under `<work>/out/txdopt/`. Untouched
  textures and chunks are copied byte for byte (every vanilla TXD rebuilds bit-exact). Each written TXD is parsed
  back and its re-encoded textures checked; the same input and options give the same bytes.

## Limitations and known issues

- `--drop-unused` sees DFF references only: names used by scripts, SA-MP `SetObjectMaterial` or MTA shaders must be
  kept with `--keep`. When the mod's IDE files are not in the input, only game models are known.
- `--share` edits copies of the mod's IDE files; the game must load those IDEs (they stay where the mod had them).
- Only D3D9 textures are written (a D3D8 texture that changes becomes D3D9); paletted textures are converted only
  when that makes them smaller. VER1 (III/VC) archives get loose TXDs instead of a rebuilt IMG; IMG entries inside a
  `.zip` cannot be read (extract the archive first).
- `optimize` needs numpy (the encoder) and Pillow (non-power-of-two resizing), `audit` needs numpy (decoding);
  `budget` uses the standard library only.

## Python API (if other packages use it)

```python
from satk.txdopt.optimize import Opts, optimize, target_dims       # optimize(target, Opts(max=512, ...), out=...)
from satk.txdopt.audit import audit                                 # audit(target, max_side=1024, unused=True)
from satk.txdopt.budget import budget, index_world, stream_limit    # budget("vanilla", area=[x, y, r])
from satk.txdopt.usage import Usage, dff_strings                    # who uses a TXD, names in a DFF
from satk.txdopt.txdedit import rebuild, dxt_to_dxt1                # stdlib TXD rebuild, lossless DXT3/5 -> DXT1
```
