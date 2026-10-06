# texlib: procedural SA textures and vanilla reuse

[Русская версия](../ru/texlib.md)

Package: `satk.texlib`. Blender node code: `blender/satk_blender/texlib` (GPL).

## What it is

texlib makes small, tileable materials with the grain, softened detail and colour ranges of San Andreas.
It also searches the vanilla index for textures that a map object can reference directly, without copying
game pixels into a mod. Generated images and preview sheets go under `<workspace>/work/out/texlib/`.

## Quick example

```powershell
satk texlib list --no-previews
```

The table lists 20 presets, their texture roles, DXT formats and descriptions. Omit `--no-previews` to bake a
labelled sheet of all 20 DXT previews; transparent materials appear over a checkerboard.

## Commands

All operations use the generic `satk_op` interface; they add no dedicated MCP tools.

| Command | What it does |
|---|---|
| `satk texlib make brick --size 256 --seed 7` | Bake one preset and return the PNG, DXT preview, packing manifest, style verdicts and timings |
| `satk texlib make plaster --size 128 --tint '#887c68'` | Set a hue while retaining the role's subdued saturation and value range |
| `satk texlib list` | Bake missing 128 px previews and return one labelled sheet |
| `satk texlib vanilla brick --role wall` | Find reusable vanilla brick textures with their TXD names |
| `satk texlib vanilla asphalt --role road --colour grey` | Rank matching textures by approximate indexed colour |

`make` accepts only 128 or 256 px. Seeds are integers from 0 to 4294967295. Tint and colour accept `#RRGGBB`
or names such as `grey`, `brown`, `red`, `blue`, `green`, `beige` and `rust`. `--out` is a **directory under
work**, not a PNG filename. Default output directories include a content hash. Identical inputs reuse
verified files; changing a recipe, seed, size, tint or pipeline creates a different directory. Existing edited
files or a directory belonging to different inputs are refused instead of overwritten.

## Presets and roles

| Style role | Presets |
|---|---|
| `wall` | `concrete`, `brick`, `plaster`, `tiles`, `roof_tiles`, `corrugated_metal`, `glass_grime` |
| `ground` | `asphalt`, `grass`, `dirt`, `sand` |
| `prop` | `rusty_metal`, `painted_metal`, `wood_planks` |
| `interior` | `fabric`, `leather`, `rubber`, `plastic` |
| `decal` | `grime_overlay`, `decal_overlay` |

Hyphens and underscores are interchangeable in preset names. Interior presets are deliberately dark for
upholstery and trim. `glass_grime` and both overlays retain graded transparency in DXT3; other presets use DXT1.

## How it works

The original JSON node recipes contain no game pixels. UV sine/cosine coordinates make the noise periodic in
both axes; brick joints, corrugations, planks and weave use repeating node patterns. Headless Cycles bakes
emission on a plane at four times the requested size, using the CPU and an isolated profile. An extra emission
bake supplies alpha where needed. No installed Blender add-on or DragonFF extraction is needed.

The finish box-downsamples, applies wrap-around smoothing, adds two scales of grain, and grades the colour and
value against numeric vanilla distributions shipped with the recipes. These are the `style.texture` samples
of clean PC 1.0 US: 160 textures each for wall, ground and prop; 84 for interior; 45 for decal. No index is
needed to generate textures. Both the PNG and its actual DXT round trip must receive an `in` verdict from the
same judge used by [style.texture](style.md); a failed check produces `CHECK_FAILED`, not a successful bundle.
An `in` verdict allows individual metrics at the edge of the distribution; the returned rows show them.

Each bundle contains the material PNG, `preview/<preset>.png`, `texmod.json` and `texlib.json`. The packing <!-- linkcheck: ignore -->
manifest includes only the material, with DXT1/DXT3 and one mip level for interior textures or a full chain for
world surfaces. Pass the returned output directory to [texture.pack](texmod.md); the preview subdirectory is
not packed. The metadata records file hashes, style metrics and bake/process timings. Reproducibility applies
to the same Blender version and pipeline. Scratch bakes use the process's temporary directory.

## Vanilla references and streaming

Search uses active textures actually referenced by vanilla `objs`/`tobj` models. Role matching is a name
heuristic; colours use the index's approximate DXT endpoint means, without decoding textures. Full-size
textures rank ahead of tiny LOD textures. Results include `id` (a tex SID), `ide_txd`, `texture_name`,
`material_preset`, size, colour, map usage count and an example model. Use `limit` and the returned `cursor`
to page the results. Colour words may also appear in the query, for example `red brick`.

Write the returned texture name into the DFF material and TXD name into the object's IDE TXD field, without
the `.txd` extension. Keep the vanilla TXD and any `txdp` parents in registered archives and let the engine
load them with the model. A matching name or a nearby object does not keep that TXD loaded. Replacing only a
DFF retains the existing IDE dictionary. Do not package the vanilla pixels.

This reuse is for **map objects only**, not vehicles, upgrades, peds or weapons. A model has one IDE TXD field:
its materials must all resolve within that dictionary and its parent chain. Separate objects are needed for
unrelated dictionaries or a mixture of custom packed images and reference-only vanilla textures.

## Python API and kit followups

```python
from satk.texlib.vanilla import material_preset, export_plan

material = material_preset("vanilla:chicano10_lae/brickred", asset_class="map")
plan = export_plan([material["vanilla"]], asset_class="map", own_textures=[])
```

The helper verifies the texture and map usage, rejects non-map use, checks parent availability and texture-name
shadowing, and returns the IDE dictionary without extracting or writing anything. `material_preset` returns a
kit-compatible `shared=True` description. The GPL helper `satk_blender.texlib.materials.apply_reference` applies
one to a Blender material using a synthetic placeholder and records `satk_vanilla`.

The current kit exporter does **not** yet consume these presets. Its integration points are:

1. Route `vanilla:` material names to `apply_reference` and retain each material's `satk_vanilla` reference.
2. Collect those references in the Blender export manifest and call `export_plan` before packaging.
3. For `reference_only=True`, skip TXD packing (including the empty TXD), use `ide_txd` in the map object's IDE
   definition, and carry that reference through add-on packaging instead of requiring a new TXD file.

These hooks belong to kit and its packaging integration. Until connected, use the helper to prepare the
reference and the matching IDE definition; `kit.export` alone does not implement vanilla reuse.
