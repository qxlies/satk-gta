# `satk formats`: GTA:SA file format parsers

[Русская версия](../ru/formats.md)

Package: `satk.formats`.

## What it is

A library that reads the game's formats: IMG, RenderWare (TXD, DFF), COL, IFP, IDE, IPL, ZON, DAT, GXT, PE and
the text data files (`water.dat`, `timecyc.dat`, `carcols.dat`, `handling.cfg`, `object.dat`). The asset index
([index.md](index.md)), textures ([media.md](media.md)), models ([models.md](models.md)) and the Blender bridge
([blender.md](blender.md)) are built on it. It uses only the standard library, so it also runs inside Blender's
bundled Python. Game files are opened read-only, an IMG is never loaded into memory whole, and the package writes
nothing to disk. Broken data raises `FormatError(kind, offset, msg)`, not `IndexError`, `struct.error` or a hang.

## Quick example

```powershell
satk formats selftest --quick
satk formats ls models/gta3.img --name infernus
satk formats dump models/gta3.img/infernus.dff
satk formats dump models/coll/weapons.col --level full
```

What comes back (shortened, third command):

```json
{"ok":true,"id":"file:models/gta3.img/infernus.dff","kind":"dff","rw_version":"0x36003","clumps":1,"atomics":15,
 "frames":36,"geoms":15,"materials":64,"verts":3573,"tris":3072,"flags":["normals","embedded_col","matfx",
 "reflection","specular"],"textures_total":13,"bbox":[-1.29,-2.68,-0.79,1.23,2.86,0.68],"embedded_col":"infernus_col"}
```

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk formats selftest [--root R] [--profile vanilla\|installed\|samp] [--quick] [--bench] [--geometry] [--dxt]` | — | parses the whole game and compares 99 counters with the reference numbers of the vanilla game (about 5 s; `--quick` skips TXD and DFF, about 1 s; `--geometry` decodes every gta3/gta_int geometry; `--dxt` compares the DXT decoder backends on reference textures and measures their speed) |
| `satk formats ls IMG [--name S] [--ext E] [--limit N] [--cursor C] [--profile P]` | — | entries of an IMG archive (the path is absolute or relative to the profile's game root, any case) |
| `satk formats dump TARGET [--level stats\|full\|tree] [--limit N] [--profile P]` | — | describes a file or an `<img>/<entry>`: IMG, TXD, DFF, COL, IFP, IDE, IPL, ZON, DAT; `tree` is the raw RW chunk tree (of a broken file too). A file under the profile's root gets its canonical SID in `id` (`file:data/maps/la/lae2.ipl`, however the path was spelled); a file outside the root gets `path` (and `entry` for an IMG entry) without a SID |

None of the three has an MCP tool of its own: an agent runs them through `satk_op` (for example
`satk_op("formats.dump", {"target": "models/gta3.img/infernus.dff"})`). From Blender:
`selftest.main(['--root', r'<game folder>', '--quick'])` (exit code 0/1, JSON on stdout).

## How it works

| Module | What |
|---|---|
| `img` | IMG v2 (`VER2`, entry `<IHH24s>`) and v1 (`.dir`); `ImgArchive.open(path)`, case-insensitive `find()` |
| `rw` | RW chunks: `iter_children` (a child never exceeds its parent), `rw_version`, `rw_payload_size` |
| `txd` | texture headers, effective alpha, `texture_hash` (blake2b-96 of mip 0 + palette) |
| `dxt` | `decode_rgba` (backends `pillow` → `numpy` → `python`), `preview_rgba`, `mean_rgba` |
| `dff` | `scan_dff` (frames, geometries, materials, 2dfx, DDL flags, bbox), `decode_geometry(ies)`, `find_embedded_col` |
| `col` | `iter_col`: COLL/COL2/COL3/COL4, surfaces, shadow faces |
| `ifp`, `zon` | `parse_ifp` (ANP3, ANP2, ANPK), `parse_zon` (`map.zon`, `info.zon`) |
| `gxt` | `load_gxt`/`parse_gxt`: SA texts (`text/*.gxt`, version 4, 8/16-bit), `TKEY`/`TDAT` tables; keys are stored as the JAMCRC32 of the upper-case name (`key_hash`), so `Gxt.text("GAN")` → "Ganton" |
| `dat`, `ide`, `ipl` | `parse_dat`, `resolve_ci` (never leaves the root: a drive in any component, `..`, `...`, UNC, device names `NUL`/`CON.txt` → `None`); every IDE section; text and `bnry` IPL (the quaternion as stored in the file; the world one is `world_quat`) |
| `layout` | archive registration order `engine` / `samp`, the `main/player/anim/cuts` namespaces |
| `pe` | PE sections and `va_to_off` (shared by `game` and `re`) |
| `water` | `parse_water`: `water.dat`/`water1.dat` polygons (3 or 4 vertices of 7 numbers + flags) |
| `timecyc` | `parse_timecyc`: `timecyc.dat` with the engine's `sscanf` semantics (23 weathers × 8 hours; the 24-hour file too) |
| `carcols` | `parse_carcols`: the `col` palette and the `car`/`car4` variants |
| `handling` | `parse_handling`: car records, `!` bikes, `%` boats, `$` aircraft; `flag_names` for the flags |
| `objectdat` | `parse_object_dat`: `object.dat` up to the `*` terminator (and the lines after it with `loaded=False`) |

Pitfalls built into the code:

- `SizeInArchive ≠ 0` → the engine uses it (`ImgEntry.size`); "the first registered archive wins".
- The data of an IMG entry ends at the end of its RW chunk, not at the sector; `player.img` has 3 clumps in a
  row, all of them are read, and frame and geometry indices run through all of them.
- RW 0x35000 (16 loose DFFs), 0x34003 (`outro.txd`); a geometry with RW < 0x34000 has 12 extra bytes in its
  Struct.
- 98% of geometries are tristrips: winding alternates, and degenerate triangles (58% of the game's strip windows)
  are dropped in a vectorised way: equal neighbouring indices are found by XOR of big integers over the raw bytes,
  all strips of a geometry are handled in one pass, and there is no loop over indices or triangles. `tris` is
  always counted after the strips are unrolled.
- DXT1 + raster 565 is opaque (index 3 of a `c0 ≤ c1` block is black, A=255); DXT1 + 1555 has 1-bit alpha.
- COL is matched by name, `hdr_model_id` is not used; the vertex count of COL2+ is the highest index + 1.
- The DFF `bbox` is in model space: the root frame is the identity matrix (the entity's matrix replaces it).

The text data files (`water`, `timecyc`, `carcols`, `handling`, `objectdat`; the index stores them in its
tables, see [index.md](index.md)) are read the way the engine reads them, vanilla errors included:

- `timecyc.dat` is read with one `sscanf` per line: `%d` for colours and shadows, `%f` for sizes and distances.
  A value that cannot be read stops parsing the line, and the remaining fields **are taken from the previous
  line** (the engine's local variables live outside the loop). Vanilla line 320 (`255` instead of three numbers)
  gives `nread = 20`. Weather names come from comments such as `//////////// EXTRASUNNY_LA`, otherwise from
  `timecyc.WEATHERS`.
- `carcols.dat`: `77.93,96` is read as 77, 93, 96 and ends up in `errors`; a model has at most 8 variants; a
  model listed twice takes its last line.
- `handling.cfg`: nothing is read after `;the end`; `^` lines (animation groups) are only counted; `0.1s` in
  `$ RCRAIDER` is accepted (the engine skips the character with `%*c`).
- `object.dat`: the first line starting with `*` stops the engine; the lines after it are returned with
  `loaded=False`.
- `water.dat`: `water1.dat` has no flags column (`flags = None`).

Speed on the author's machine (warm cache, idle): the whole selftest takes about 4.5 s (DFF about 2.5 s for
416 MB, TXD about 1 s for 748 MB); the full gta3+gta_int geometry takes about 3-4 s on its own
(`--quick --geometry`) and less together with the DFF stage (the chunk walk is shared, and `timings.geometry`
then holds only the decoding). A busy machine can be twice as slow. DXT decoding: Pillow `bcn` about
387 Mpix/s, numpy about 50, pure Python about 3.4; endpoint previews about 97 Mpix/s of source pixels, the mean
colour about 375. On 7 reference textures the backends agree byte for byte.

## Limitations and known issues

- **`dff.atomics` is 19,556.** An older reference table said 46,108: its prototype added the frame index stored
  in the Struct of every light (2,203 lights) to the atomic count. The explanation is in
  `selftest.GOLDEN_NOTES`.
- `models/coll/peds.col` (a Vice City leftover the game does not load): two of its models have a broken body.
  By default `iter_col` returns them with zero counters and writes the reason into `errors`; `strict=True` raises
  `FormatError`.
- `info.zon` stores `level = 1` in every line, so a zone's island cannot be told from that field.
- Native (console) PS2/Xbox geometry and textures of platforms other than 8/9 are not decoded (`unsupported`).
- Solutions to common problems are in [troubleshooting.md](troubleshooting.md).

## Python API (if other packages use it)

```python
from pathlib import Path
from satk.formats.img import ImgArchive
from satk.formats.dff import scan_dff, decode_geometry
from satk.formats.txd import parse_txd, mip0_bytes, palette_bytes
from satk.formats.dxt import decode_rgba, preview_rgba, mean_rgba

root = Path("<game folder>")                            # the profile root that `satk status` shows
with ImgArchive.open(root / "models" / "gta3.img") as a:
    blob = a.read(a.find("infernus.dff"))
info = scan_dff(blob)                                   # 15 atomics, 64 materials, 3,072 triangles
mesh = decode_geometry(blob, info.geoms[0].geom_off)    # Mesh: positions array('f'), tris array('I'), ...
```

- `DffInfo.flags` holds the DDL bits of `dff.flags` (`dff.FLAG_NAMES`), `Material.fx` the bits of `dff_mat.fx`
  (`dff.FX_NAMES`), and `Material.color_slot` 1..4 marks a recolourable vehicle material.
- `iter_col(buf, strict=False, errors=[...])`, `parse_zon(text, strict=False, errors=[...])`.
- `base` in `txd` is the offset of the TXD start inside `buf`, the same for every function: `parse_txd(arc, base=o)`,
  then `texture_hash(arc, t, base=o)`, `mip0_bytes(arc, t, base=o)`. Offsets in `TexInfo` (`mip0_off`,
  `pal_off`) are relative to the TXD start, so the same `TexInfo` also works for `arc[o:]` with `base=0`.
- `selftest.run(root, profile, skip=("dff", "col"))` skips stages from `selftest.STAGES` (their reference
  counters are not compared); `quick=True` skips `txd`, `dff` and `texref`.
- `decode_rgba(t, mip0, palette, backend="auto")`: the backend matters only for DXT; uncompressed and palette
  formats always take the shared stdlib path (slices and `bytes.translate`). Without Pillow and numpy the
  `python` backend works.
- Data files: every parser has the signature `parse_x(text, strict=False, errors=[...])`; the text comes from
  `dat.read_text`:

```python
from satk.formats.dat import read_text, resolve_ci
from satk.formats.handling import parse_handling, flag_names, MODEL_FLAGS
from satk.formats.timecyc import parse_timecyc

h = parse_handling(read_text(resolve_ci(root, "data/handling.cfg")))
inf = h.get("INFERNUS")                                   # HandlingRec, kind "car"
inf.values["max_vel"], flag_names(inf.values["model_flags"], MODEL_FLAGS)   # 240.0 ['IS_LOW', ...]
rows = parse_timecyc(read_text(resolve_ci(root, "data/timecyc.dat")))
rows[4].weather_name, rows[4].hour, rows[4].values["far_clip"]               # 'EXTRASUNNY_LA' 12 800.0
```
