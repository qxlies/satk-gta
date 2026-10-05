# Catalog: an offline HTML browser of models, textures and zones with thumbnails

[Русская версия](../ru/catalog.md)

Package: `satk.catalog`.

## What it is

`satk catalog build` turns the index (`satk.index`) into a static site: one page,
`work\out\catalog\index.html`, that opens with a double click, with no server and no internet (no external
CDNs, fonts or scripts). It has a search over models, textures, TXDs and zones with 128 px thumbnails, and
detail pages for models (DFF, COL, IDE fields, TXD chain, textures, placements on a map), textures (where used,
same pixels), TXDs and zones (what is placed there). Thumbnails come from the `satk texture` and `satk model`
caches (missing ones are generated). The game is only read; everything is written into the catalog folder under
`work\out\`.

The catalog contains game textures: it is a local file for your own use and must not be published.

## Quick example

```powershell
satk catalog build --limit 60 --out catalog-demo
```

What comes back (shortened):

```json
{"ok":true,"file":"<workspace>/work/out/catalog-demo/index.html","dir":"<workspace>/work/out/catalog-demo",
 "counts":{"models":60,"textures":70,"images":64,"txds":59,"zones":384,"placements":0,"exterior":0},
 "thumbs":{"textures":{"kept":0,"written":64,"failed":0},"models":{"kept":0,"converted":59,"failed":0}},
 "format":"webp","map":true,"files":134,"mb":0.3,"changed":10,"removed":0,"seconds":3.2}
```

The full vanilla catalog is built without `--limit`; open `file` in a browser:

<!-- docs-smoke: skip a full build takes minutes when the thumbnail cache is empty -->
```powershell
satk catalog build
```

If a browser (or an embedded preview pane) shows only "file missing: data/meta.js", it does not load
neighbouring scripts over `file://`. Serve the folder locally instead and open `http://127.0.0.1:8000/`:

<!-- docs-smoke: skip starts a server that keeps running -->
```powershell
py -3.12 -m http.server 8000 --bind 127.0.0.1 --directory <workspace>\work\out\catalog
```

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk catalog build [--out NAME] [--limit N] [--jobs 12] [--no-thumbs] [--fmt auto\|webp\|png] [--profile vanilla]` | — | build or update the catalog; returns `file`, `counts`, `thumbs`, `mb`, `changed`, `timing` |

Parameters:

- `--out`: folder name under `work\out\` (`catalog-demo`) or a path inside `work\`. Default: `catalog`,
  `catalog-<profile>` for other profiles, `…-sample` with `--limit` (a sample never overwrites the full catalog).
  A non-empty folder without `catalog.json` is refused: the build deletes files it would not have created itself.
- `--limit N`: only the first N models (by ID) and the textures/TXDs they use; all zones.
- `--no-thumbs`: render and convert nothing, reuse existing thumbnails (a fast rebuild of the data).
- `--fmt`: `auto` = WebP (about 2 KB per texture, 1 KB per model) when Pillow supports WebP, else PNG.

In the browser: the search box (`/` focuses it, `Esc` clears it), the `Models` / `Textures` / `TXDs` / `Zones`
filters and an IDE section filter for models. A query matches name substrings (for textures, `txd/name`), all
words at once; exact matches rank higher. A number is a model ID; a SID plus `Enter` opens its page directly:
`model:411`, `tex:bistro/vent_64`, `txd:vehicle`, `zone:gan1`, `pix:<hex>`. On the map: wheel to zoom, drag to
pan, click a point or a table row to select a placement. Every page lists ready `satk` commands with a `copy`
button.

## How it works

- **Data** is read through the public index API (`IndexDB.query` in pages of 500 rows and `IndexDB.insts`):
  vanilla has 14,832 models, 32,874 textures (14,559 unique images), 4,042 TXDs, 384 zones and 50,935
  placements; about 3 s.
- **Site:** `index.html` (CSS and JS inlined) + `data\*.js`. Browsers block `fetch()` of local files over
  `file://`, so the data comes as scripts `CAT.add("name", {...})`. Opening the page loads only the search index
  (`meta`, `models`, `textures`, `txds`, `zones`, about 2.5 MB); model pages (`data\m\<id>>7>.js`), "where a <!-- linkcheck: ignore -->
  texture is used" (`data\t\…`) and "what is in a zone" (`data\z.js`) load on demand; thumbnails are <!-- linkcheck: ignore -->
  `loading="lazy"`, and results are appended 120 at a time while scrolling.
- **Thumbnails:** textures come from the `work\cache\tex\<hh>\<pix>.png` cache (a missing mip0 is written through
  `satk.media`) and are downscaled to 128 px → `t\x\<hh>\<pix>.webp`; models use the 128 px `soft` previews from <!-- linkcheck: ignore -->
  `work\cache\model\` (`satk.model3d`; missing ones are rendered) → `t\m\<id>>7>\<id>.webp`. The preview keys are <!-- linkcheck: ignore -->
  stored in `catalog.json`: while the index hash and the renderer version stay the same, existing files are not
  checked again.
- **Map** (`map.webp`, 1536 px for 6 × 6 km): land is the terrain tiles above sea level (lighter is higher), the
  lights are the density of HD objects; placements are drawn on top (cyan: exterior, violet: interiors,
  blue: LOD). Water is not drawn yet, so the coastline is approximate. The zone of a placement is the smallest
  `info.zon` zone that contains the point.
- **Determinism:** the same index gives byte-identical files; a file is rewritten only when it changes, stale
  files (`data\`, `t\`) are removed. A repeated full build: 13 s, `changed: 0`. <!-- linkcheck: ignore -->
- **Speed** (vanilla, 12 processes, the `texture export-all` and `model image --all` caches already filled, the
  machine busy with other builds): 4 min 17 s, of which texture thumbnails 52 s and model thumbnails 197 s (about
  11 s on an idle machine); 29,612 files, 56 MB in total (texture thumbnails 30 MB, models 15 MB, data 10 MB).

## Limitations and known issues

- Needs a built index of the profile (`INDEX_MISSING` → `satk index build`).
- The full-size texture on its page is loaded from `work\cache\tex\` by a relative path: if the catalog is moved
  elsewhere, only the 128 px thumbnail remains.
- Models without a DFF (42 in vanilla) and previews without visible pixels show a placeholder or an empty frame.
- Without numpy there is no background map (points are drawn on a grid); without Pillow thumbnails are PNG.
- The catalog UI is English only.
- Other problems: [troubleshooting.md](../ru/troubleshooting.md) (Russian).

## Tests

`tests/catalog` uses a synthetic index (no game files). The `game`-marked tests (`tests/catalog/test_game.py`) use
the configured `work\index\vanilla.sqlite` when it has the current schema (`satk dev gate` builds a private one
before its game tests); when it is missing or has an older schema, they build a private vanilla index once per
module into a pytest temp folder (`satk index build` with `SATK_PATHS_WORK` pointing there, about 10 s with a warm
OS cache) and never touch the shared index.

## Python API (if other packages use it)

```python
from satk.catalog import build

env = build(limit=60, out="catalog-demo", jobs=12)   # {"file": ".../work/out/catalog-demo/index.html", ...}
```

Lower level: `satk.catalog.collect.collect(db, profile, limit)` (catalog data),
`satk.catalog.worldmap.render_world(footprints, ext, world)` (the map), `satk.catalog.site.js(name, payload)`.
