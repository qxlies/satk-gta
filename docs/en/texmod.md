# `satk texture pack|replace|extract`: texture modding

[Русская версия](../ru/texmod.md)

Package: `satk.texmod`. It reads TXD through `satk.formats.txd` and checks its results through `satk.formats.dxt`.

## What it is

Texture mods without external programs: unpack the textures of a TXD to PNG, edit them, pack them back into a TXD
(DXT1/DXT3/DXT5 or uncompressed, with mip levels) or replace a few textures in a copy of a game TXD. The game is
only read; everything is written under `<work>`: unpacked textures go to `<work>/out/texmod/<txd>/`, a finished
mod to `<work>/out/mods/<name>/<file>.txd` with a `README.txt` (modloader layout). Every TXD written is re-read by
the satk parser, the new textures are decoded, and the answer gives the PSNR against the source image.

## Quick example

```powershell
satk texture extract txd:bistro
satk texture replace txd:bistro Plate=bistro/Marble.png Panel=bistro/DinerFloor.png --format dxt1 --name bistro_demo
satk texture pack bistro --name bistro_small --max-size 64
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
- Other TXDs left in the mod folder by earlier runs are not deleted: `warn: OTHER_FILES`.

## Python API (if other packages use it)

```python
from satk.texmod.encode import encode_level, mip_chain, auto_format, psnr        # (h, w, 4) uint8 -> bytes
from satk.texmod.txdwrite import NativeSpec, native_chunk, txd_chunk, rewrite_txd  # stdlib
from satk.texmod.api import build_texture, load_txd, pack, replace, extract
```
