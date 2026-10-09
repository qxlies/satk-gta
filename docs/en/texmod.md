# `satk texture pack|replace|extract|finish|new`: texture modding

[Русская версия](../ru/texmod.md)

Package: `satk.texmod`. It reads TXD through `satk.formats.txd` and checks its results through `satk.formats.dxt`.

## What it is

Texture mods without external programs: unpack the textures of a TXD to PNG, edit them, pack them back into a TXD
(DXT1/DXT3/DXT5 or uncompressed, with mip levels) or replace a few textures in a copy of a game TXD. `--asset-class vehicle|ped|weapon|map|lod`
packs with the formats and mip levels the vanilla game uses for that kind of model, `texture new` paints a flat,
gradient or banded base image of any size, and `texture finish` gives a flat or drawn image the soft, photo-like
San Andreas look without photographs and lands it inside the vanilla band of its role. The game is only read; everything is written under `<work>`: unpacked textures go to `<work>/out/texmod/<txd>/`, a finished
mod to `<work>/out/mods/<name>/<file>.txd` with a `README.txt` (modloader layout). Every TXD written is re-read by
the satk parser, the new textures are decoded, and the answer gives the PSNR against the source image.

## Quick example

```powershell
satk texture extract txd:bistro
satk texture replace txd:bistro Plate=bistro/Marble.png Panel=bistro/DinerFloor.png --format dxt1 --name bistro_demo
satk texture pack bistro --name bistro_small --max-size 64
satk texture pack bistro --name bistro_vehicle --asset-class vehicle
satk texture finish bistro/Marble.png --preset wall
satk texture new bin_base --size 256 128 --color "#7a7c7c" --rect 0:0:1:0.6=#5c7a68
satk texture finish new/bin_base.png --role prop
```

What comes back (shortened):

```json
{"ok":true,"cols":["name","file","fmt","size","mips","state"],"rows":[["vent_64","vent_64.png","X8R8G8B8","64x64",1,"written"],["…"]],"dir":"<work>/out/texmod/bistro","source":"txd:bistro","txd":"bistro.txd"}
{"ok":true,"cols":["name","fmt","size","mips","psnr","source"],"rows":[["Plate","DXT1","128x128",1,40.34,"<work>/out/texmod/bistro/Marble.png"],["Panel","DXT1","128x128",1,40.55,"…/DinerFloor.png"]],"file":"<work>/out/mods/bistro_demo/bistro.txd","mod":"<work>/out/mods/bistro_demo","readme":"…/README.txt","bytes":1658152}
{"ok":true,"cols":["name","fmt","size","mips","psnr","source"],"rows":[["vent_64","X8R8G8B8","64x64",1,100.0,"…/vent_64.png"],["…"]],"file":"<work>/out/mods/bistro_small/bistro_small.txd","…":"…"}
```

To install the mod, copy `<work>/out/mods/bistro_demo/` into `<game folder>/modloader/`.

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk texture extract TXD [--out DIR] [--force]` | — (through `satk_op`) | mip 0 of every texture to `<name>.png` + `texmod.json` (names, formats, mips); edited PNGs are not overwritten without `--force` (`state: kept`, `warn: KEPT`) |
| `satk texture pack DIR [--name X] [--file F] [--format auto] [--mips N] [--quality normal] [--no-pot] [--max-size N]` | — | the images of a folder (`.png`; with Pillow also `.bmp .tga .jpg .dds …`) → `<work>/out/mods/<X>/<F or X>.txd`; the texture name is the file name |
| `satk texture replace TXD TEX=IMG... [--name X] [--file F] [--format auto] [--mips N] [--add] [...]` | — | a copy of the TXD with the listed textures replaced (`--add` appends new ones), everything else byte for byte; the file name defaults to the source name (`bistro.txd`), the folder to the TXD name |
| `satk texture pack DIR --asset-class vehicle\|ped\|weapon\|map\|lod [--out MODDIR]` | — | formats and mip levels as in the vanilla game for that class (table below); `--out` writes `<MODDIR>/<file>` into any writable folder (a Mod Loader mod) without a README |
| `satk texture finish IMG [--preset photo_like\|interior\|wheel\|wall] [--mask AO.png] [--edge EDGE.png] [--role R] [--grime 0..1] [--wear 0..1] [--grain 0..1] [--soft PX] [--supersample 1\|2\|4\|8] [--photo 0..1] [--out DIR]` | — | the soft SA look without photographs: `<stem>-<preset>.png`, its DXT1 preview `…-dxt.png`, a stats table (value, saturation, contrast, colours) and the vanilla band of the role; the result is pulled into the inner part of that band. Vehicle roles stay clean (grime and wear 0) unless asked. `--photo` adds the quiet variation of a photo instead of the noise (soft tonal drift, mottling and grain, a slight hue drift, light from above; no grime; the painted structure stays) |
| `satk texture new NAME [--size W H] [--color #rrggbb] [--color2 C --gradient u\|v] [--rect u0:v0:u1:v1=#hex ...] [--alpha N] [--out DIR]` | — | a base image of any size (each side a multiple of 4): a colour, an optional gradient and rectangles in UV space (`v` up, the rectangles `uv.fit` and `kit.uv_region` use) → `<work>/out/texmod/new/<NAME>.png`, so no Pillow script is needed |

Parameters:

- `TXD` is `txd:<name>` (from the index), `file:<path>`, `<img>/<entry>` (`models/gta3.img/bistro.txd`) or a path to a `.txd` <!-- linkcheck: ignore -->
  (absolute or relative to the profile root). `extract` and `replace` take `--profile` (default `vanilla`).
- Images and folders are an absolute path, relative to the current folder or to `<work>/out/texmod/` (where
  `extract` writes, so `bistro/Marble.png` and `pack bistro` work from any folder). <!-- linkcheck: ignore -->
- `--format`: `auto` (default), `dxt1 dxt3 dxt5 a8r8g8b8 x8r8g8b8 r5g6b5 a1r5g5b5 a4r4g4b4`.
  `auto` for a new texture: opaque → DXT1, alpha only 0/255 → DXT1 with 1-bit alpha (`DXT1+a`), smooth alpha →
  DXT5; sides not divisible by 4 → A8R8G8B8/X8R8G8B8. When replacing, `auto` keeps the family of the replaced
  texture: DXT stays DXT (the image's alpha picks the variant, DXT3 stays DXT3), uncompressed stays uncompressed
  (X8R8G8B8 → A8R8G8B8 if the image has alpha). In a folder made by `extract`, `auto` takes the format from
  `texmod.json`.
- `--mips`: when omitted, the full chain down to 1×1 for a new texture, and "as before" when replacing and in a
  folder made by `extract`; `0` means no mips; `N` means at most `N` levels.
- `--pot` (default) resizes the sides to powers of two (the nearest one, Lanczos), `--max-size N` shrinks the
  long side; a resize gives `warn: RESIZED`, a side over 2048 gives `warn: BIG`.
- `--quality`: `fast` (PCA only), `normal` (+ least-squares refinement), `high` (+ an endpoint search of ±1,
  5 times slower, +0.3 dB).
- The answer is the table `name, fmt, size, mips, psnr, source` and the paths `file`, `mod`, `readme`. PSNR
  compares mip 0 after decoding with the image (RGB; for textures with alpha, RGB multiplied by alpha, plus
  alpha); `100` means lossless.

`--asset-class` (measured on the vanilla game; an explicit `--format`/`--mips` still wins):

| Class | Opaque | 1-bit alpha | Smooth alpha | Mip levels | Largest vanilla side |
|---|---|---|---|---|---|
| `vehicle` | DXT1 | DXT1 | DXT3 (never DXT5) | 1 (0 of 593 vanilla vehicle textures have mips) | 256 |
| `ped` | X8R8G8B8 | A8R8G8B8 | A8R8G8B8 | 1 (273 of 289 are uncompressed) | 256 |
| `weapon` | DXT1 | DXT1 | DXT3 | 1 | 128 |
| `map` | DXT1 | DXT1 | DXT3 | a full chain from 256 px (57-77 % of vanilla 256-512 px map textures), else 1 | 512 |
| `lod` | DXT1 | DXT1 | DXT3 | 1 | 512 |

A larger image gives `warn: CLASS_SIZE` (the `sa_plus` tier allows about twice the vanilla side).

`texture finish` presets and the vanilla band they aim at (p10/p50/p90 of the vanilla game; `value` = mean of
max(r, g, b), `colours` = exact RGB colours of the DXT1 preview):

| Preset | Role | Vanilla value | Vanilla colours |
|---|---|---|---|
| `interior` | car interiors (n=147) | 0.085 / 0.119 / 0.261 | 364 / 590 / 921 |
| `wheel` | car wheels (n=133) | 0.21 / 0.27 / 0.48 | 208 / 296 / 412 |
| `wall` | map walls (n=300), tileable, keeps the value | 0.46 / 0.64 / 0.81 | 77 / 564 / 2,708 |
| `photo_like` | anything: keeps the value | — | — |

A flat 128×128 fill finished with `--preset interior` lands inside the band (value 0.13, 650 colours after DXT1).

`texture finish` takes more inputs and one more guarantee:

- **Clean and soft by default.** `--grime` (darker blotches, heavier at the bottom) and `--wear` (edge wear from
  `--edge`) default to 0 for the vehicle roles `interior wheel decal body`: car paint is a flat key colour and the
  engine's dirt level does the dirt, so own vehicle textures carry none. Map roles keep the preset's grime (0.20 for
  `wall`) and a wear of 0.35. `--grain` is the share of fine grain in the noise (lower is softer); `--soft` is the
  blur of the final soft filter in output pixels (default 0.5: slightly out of focus at native size, like SA).
- **Paint big, finish small:** `--supersample 4` takes an image painted at 4 times the texture size (512 px for a
  128 px texture) as it is; the noise and masks work at that size and the soft filter downsizes it.

- `--mask AO.png` darkens by ambient occlusion (white = open); `--edge EDGE.png` is the worn-edge mask (white = edge;
  `kit.bake` writes `<object>_ao.png` and `<object>_edge.png`): edges turn lighter and greyer, like chipped paint.
  A mask of another size than the image is resized to it (`warn: RESIZED_MASK`), so a bake at the wrong size is not
  an error.
- `--role` names the vanilla texture role (`interior wheel decal body ped weapon wall ground prop generic`) whose
  band the result lands in. `auto` (default): the preset's role, else the role the file name suggests, else `prop`.
- **Band landing:** after finishing, the DXT1 preview is measured with the `style.texture` metrics (`tex.val_mean`,
  `tex.sat_mean`, `tex.hf_energy`, `tex.colours`) against the vanilla distribution of the role (the cached sample of
  `style.texture`; the three measured bands above when the style cache is not built). A metric in the outer quarter
  of its p10..p90 band, or beyond it, is pulled to 20 % inside the inner part (value and saturation through their
  targets, detail and colour count through the noise gain), up to four rounds; the `landing` table lists what moved.
  A flat grey-green base for a bin lands with value 0.48, saturation 0.26, detail 42 and 1,500 colours against the
  prop band 0.27-0.85, 0.05-0.70, 12-57, 152-2,468. Without a band for the role nothing is changed.

## How it works

- **DXT encoder** (numpy, `satk.texmod.bc`): the segment ends are the principal colour axis of the block (PCA)
  and a variant of it shrunk by 1/16; then 2-3 least-squares steps over the indices with quantisation to 565,
  keeping the best per block. The error is computed exactly the way `satk.formats.dxt` (and the game) decodes:
  565 expansion and integer interpolation. DXT1 with alpha: blocks with transparent pixels use the three-colour
  mode (index 3 = transparent), and the ends are fitted to the opaque pixels. DXT3 has explicit 4-bit alpha;
  DXT5 tries both block modes (8 values, and 6 values + exact 0/255) and keeps the smaller error.
- **Quality** on the 26 textures of `bistro.txd` as DXT1: 36.3 dB on average (`normal`, minimum 26.9 on the
  `StainedGlass` window); Pillow 12's DDS encoder gives 33.0 dB on the same textures. Speed on 1024×1024: DXT1
  `fast` 0.2 s, `normal` 0.6 s, `high` 3.2 s; DXT5 `normal` 0.8 s.
- **Mips** use a 2×2 box filter (area-exact for odd sides); with alpha, the colour is averaged weighted by alpha,
  so transparent texels give no dark fringe. Level `i` is `max(1, w>>i) × max(1, h>>i)`; DXT levels below 4×4
  take one block.
- **TXD** (`satk.texmod.txdwrite`, standard library only) is written like the vanilla SA PC files: platform 9
  (D3D9), `deviceId` 2, RW stamp `0x1803FFFF`, rasterFormat DXT1 `0x200` (with alpha `0x100`), DXT3/DXT5 `0x300`,
  X8R8G8B8 `0x600`, A8R8G8B8 `0x500`, `| 0x8000` with mips; FOURCC `DXTn` or a D3DFORMAT; flags 1 = alpha,
  8 = compressed; rasterType 4. New textures get filter 6 (trilinear) and wrap addressing. When replacing, the
  name (as the TXD spells it), the mask, the filter and the addressing are taken from the old texture; if mips
  appear, a filter without mip filtering is raised (1 → 3, 2 → 6), otherwise the game does not use them.
- **Finish** works at 4× the size (or at the size of a `--supersample` painting): it desaturates towards the
  preset, multiplies a lognormal noise field (soft tileable blotches at two scales plus a faint grain) whose strength
  is fitted so the luminance contrast meets the preset, darkens by the AO mask and by the optional grime, scales the
  mean value to the preset, lightens the optional worn edges, blurs by `--soft` and box-filters back. The noise seed is the image content
  plus the preset and the masks, so the same input gives the same PNG. When `satk.style` provides `style.texture`,
  its answer replaces the built-in band, and its role distribution drives the band landing.
- **Determinism:** the same input gives the same TXD bytes and the same README (it holds the paths and sha256 of
  the images, no time). `README.txt` has one section per TXD of the mod folder; a repeat run updates only its own
  section.

## Limitations and known issues

- Only D3D9 textures (platform 9) are written; palette (PAL4/PAL8), cube and D3D8 textures are not created (such
  a texture can be replaced: the new one is D3D9). Textures of other platforms are skipped by `extract`
  (`warn: UNSUPPORTED`).
- A texture name is ASCII, at most 31 characters. DXT needs sides divisible by 4 (`--no-pot` + `--format dxt1`
  on 6×6 gives `BAD_PARAMS`).
- The mod's `--file` is the name of the file modloader replaces; keep the source name for `replace`.
- numpy (the encoder) and Pillow (resizing and non-PNG images) are needed; without Pillow only 8-bit PNGs are read.
- `texture new` makes opaque or uniformly translucent images; per-pixel alpha shapes are drawn elsewhere (the finish
  step keeps an image's alpha).
- Other TXDs left in the mod folder by earlier runs are not deleted: `warn: OTHER_FILES`.

## Python API (if other packages use it)

```python
from satk.texmod.encode import encode_level, mip_chain, auto_format, psnr        # (h, w, 4) uint8 -> bytes
from satk.texmod.txdwrite import NativeSpec, native_chunk, txd_chunk, rewrite_txd  # stdlib
from satk.texmod.api import build_texture, load_txd, pack, replace, extract
```
