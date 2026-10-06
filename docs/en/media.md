# `satk texture` and `satk map`: textures, contact sheets, top-down maps

[Русская версия](../ru/media.md)

Package: `satk.media`.

## What it is

Pictures for people and agents, made from the asset index (`satk.index`) and the game files: PNGs of single
textures, numbered contact sheets ("one picture instead of N") and a top-down map of an area with numbered
objects. Game files are opened read-only, and everything is written under `<work>`: the texture cache to
`<work>/cache/tex/`, sheets to `<work>/out/sheets/`, maps to `<work>/out/maps/`. The answer holds **paths** to the
PNGs and a JSON legend; an agent reads the labels from the legend, not from the picture.

## Quick example

```powershell
satk texture image tex:bistro/vent_64
satk texture image txd:lawest1 --mode sheet
satk map image --center 2495,-1687 --span 300
```

What comes back (shortened):

```json
{"ok":true,"files":["<work>/cache/tex/6e/6e63ecc6dc8ee4ad8bfd1139.png"],"legend":[[1,"tex:bistro/vent_64","64x64 X8R8G8B8"]]}
{"ok":true,"files":["<work>/out/sheets/txd_lawest1-e08191d440c08b7a.png"],"legend":[[1,"tex:lawest1/newtreeleaves128","128x128 DXT3"],["…"]]}
{"ok":true,"file":"<work>/out/maps/map_2495_m1687_300m_768_….png","legend":[[1,"inst:lae2_stream0#92","stormd_fillb"],["…"]],"m_per_px":0.3906,"view":[2345.0,-1837.0,2645.0,-1537.0],"counts":{"inst":397,"lod":0,"zone":0}}
```

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk texture image SID... [--mode png\|sheet] [--size N] [--labels]` | `texture_image` | texture PNGs (`tex:`/`pix:`; `txd:` and `model:` expand into all their textures) or contact sheets; `files` + `legend [n, sid, "WxH FMT"]` |
| `satk texture export-all [--jobs 12]` | — (through `satk_op`; long-running) | mip 0 of every unique texture of the profile into the cache (14,560 in `vanilla`); finished ones are skipped |
| `satk map image --center X,Y [--span 300] [--px 768] [--layers inst,lod,zone] [--area 0] [--labels 20]` | `map_image` (`x`, `y`) | a top-down map; `file`, `legend [n, sid, name]`, `m_per_px`, `view`, `counts` |

Every command takes `--profile` (default `vanilla`).

Parameters:

- `--size`: in `png` mode, the long side (default 256, `0` = original; small textures are not enlarged); in
  `sheet` mode, the cell size (default 128).
- A sheet holds up to 8×8 cells; more than 64 textures are laid out over several sheets (`warn: PAGES`), at most
  4 (beyond that `warn: TRUNCATED`). In `png` mode one call writes at most 64 files (`warn: TRUNCATED`).
- `--layers` (default `inst`): `inst` = HD placements (outdoors orange, interiors purple), `lod` = LODs in blue,
  `zone` = zone rectangles with names (the answer's `zones` lists the zones that contain the centre).
- `--area -1` shows all interiors at once; `--labels 0` turns the numbers off.
- MCP: `inline=true` attaches the picture to the answer (at most 1024 px); by default only the path comes back.
- `SID...` may also be the path of your own `.txd` (all its PC textures, legend `file:<name>.txd/<texture>`) or
  `.png` file: your textures go on the same sheet as the vanilla ones (`txd:vehicle`), no index needed for them.
- Small pictures for agents: `satk.media.encode` writes JPEG or WebP with a pixel limit (`max_px`) and a byte
  limit (`max_bytes`: the quality steps down from 85, then the picture shrinks); previews use it
  (`satk blender preview`: at most 1024 px and 300 KB, [look.md](look.md)). Textures stay lossless PNG.

## How it works

- **Content-addressed cache.** A PNG path is derived from the pixel hash: `<work>/cache/tex/<hh>/<pix>.png`
  (mip 0) and `<pix>@<size>.png` (a downscaled copy). Equal pixels from different TXDs share one file; a finished
  file is neither recomputed nor rewritten. Sheets and maps are addressed by the request key too (a map also by
  the index's `content_hash`), so a repeat returns the finished file.
- **Decoding:** `satk.formats.dxt.decode_rgba` (Pillow `bcn` → numpy → pure Python). The mip 0 offsets come from
  the index (`TexRef`), so the TXD is not parsed again. DXT1 with a 565 raster is opaque; PAL8 (`outro`) goes
  through its palette. Fully opaque pictures are saved as RGB.
- **PNG:** Pillow, or the standard library's `zlib` without it (`SATK_MEDIA_NO_PILLOW=1` forces that path). The
  pixels are the same either way; the files carry no metadata (time and the like).
- **Numbers and labels** are drawn with a built-in 5×7 bitmap font: the result is the same on any machine and
  does not depend on Pillow's fonts. A cell number is white digits on a black plate with a yellow border in the
  top-left corner; transparent textures lie on a grey checkerboard, small ones are enlarged by an integer factor.
- **Map:** footprints are the world AABBs of the placements from the index (rotation already applied), north is
  up, with a grid in world coordinates and a scale bar. Numbers go to the largest visible objects; very large ones
  (roads, ground: more than 15% of the frame) come last, and numbers never overlap.
- **Speed** on the author's machine (12 processes, Pillow): `export-all` over vanilla takes 17.5 s for 14,560
  textures (about 420 MB of PNG), a repeat 0.9 s; without Pillow (numpy + zlib) 19.5 s. A 300 m map takes
  0.04-0.3 s, a sheet of 15 textures 0.03 s.

## Limitations and known issues

- The profile's index must be built: without it the commands answer `INDEX_MISSING` (exit code 3) with the hint
  `satk index build`.
- Map footprints are axis-aligned boxes, not the rotated outlines of the models; the map shows no terrain or
  water.
- Textures of console platforms (not 8/9) have no pixels: `UNSUPPORTED`, a red frame on the sheet and a mark in
  the legend. Solutions to other problems are in [troubleshooting.md](troubleshooting.md).

## Python API (if other packages use it)

```python
from satk.media import texture_png, contact_sheet, map_image, export_all

png = texture_png("tex:bistro/vent_64", size=256)                     # Path
sheet, legend = contact_sheet(["txd:lawest1"], cols=0)                 # cols=0 = automatic (up to 8)
mp, legend, m_per_px = map_image(2495, -1687, span=300, layers=("inst", "zone"))
stats = export_all(jobs=12)                                            # {"written", "skipped", "seconds", "backend", ...}
from satk.media.encode import save_image
save_image("<work>/out/x.jpg", w, h, rgba, max_px=1024, max_bytes=300_000)   # {"path", "w", "h", "bytes", "quality"}
```

Low level: `satk.media.texture.resolve_refs(db, sids)`, `decode_ref(TexRef)`, `png_for_ref(TexRef, size)`;
`satk.media.sheet.render_sheet(refs, ...)`; `satk.media.mapplot.render_map(...)` (the full `map_image` answer).
