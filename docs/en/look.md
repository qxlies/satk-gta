# Look: SA-like previews of game models, your own files and live sessions

[Русская версия](../ru/look.md)

Package: `satk.look` (MIT) and `blender/satk_blender/look` (GPL-3.0-or-later).

## What it is

`satk blender preview` shows a model the way San Andreas draws it, so an AI assistant or a person can judge a
new asset next to vanilla ones in one small picture. The subject can be a game model (`model:426`), your own
`.dff` file (its `.txd` next to it is found automatically), a whole mod folder, or the scene of a live studio
session (`session:NAME`). The answer is one JPEG sheet of at most 1024 px and 300 KB plus the shading numbers
of every model (the same numbers as `asset check`). Everything is written under `<workspace>\work\out\preview\`
and `<workspace>\work\blender\`; your files and the game are only read.

The game look follows the engine: glass is see-through (material alpha), every lamp on `vehiclelights128` is
white (lit with `--lights on`), car bodies get dirt level 0..16 of `vehiclegrunge256` by the formula decoded
from the exe (default 2, a lightly used car), MatFX environment and specular come from the DFF, map models use
their day/night prelight plus the ambient light of `timecyc.dat` for the chosen time, and the frame gets the
game's colour filter and display gamma. Peds stand upright, gun flashes are hidden.

## Quick example

```powershell
satk blender preview model:426 --views 3q,side --states ok,dam
satk blender preview model:426 --lineup model:405,model:560 --passes game,wire
```

What comes back from the first command (shortened):

```json
{"ok":true,"subject":"model:426","files":{"sheet":".../work/out/preview/model_426-9b1c.../preview.jpg"},
 "sheet":{"w":768,"h":800,"bytes":61203},
 "legend":{"entries":[["e0","premier","sid"]],"rows":["ok/game","dam/game"],"cols":["3q","side"]},
 "stats":{"e0":{"geo.tris":2250,"dff.verts_per_tri":1.2564,"shade.normal_bend":10.643,"shade.flat_share":0.1031,
                "uv.zero_area_share":0.0067,"dims":[2.58,5.5,1.52]}},
 "env":[{"time":"12:00","balance":0.0,"weather":"EXTRASUNNY_LA","source":"index"}],"seconds":6.4}
```

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk blender preview <subject>` | — (`satk_op`) | one sheet: views x states x passes x times, plus the shading numbers |
| `--like model:426` | | the game model a file stands for: its class, paint and lineup reference (a session of an asset project takes it from `asset.json`) |
| `--lineup class` or `--lineup model:405,model:560` | | the subject next to the like model and its class peers (or the given models) at one scale, on one ground |
| `--views 3q,rear3q,front,rear,side,top` | | cameras (default `3q,rear3q,side,top`; a lineup: `side,3q`) |
| `--passes game,clay,wire,raw,tex` | | the game look, grey clay, clay with triangle edges, materials as imported, own textures at native scale (PNG) |
| `--states ok,dam,vlo,col` | | undamaged, damaged parts, low LOD, collision |
| `--time 12:00,23:00` `--lights on` `--dirt 0..16` | | time of day (one row each), lamps on, dirt level |
| `--context 60` (with an `inst:` subject) | | the placement in its map surroundings |
| `--timeout 60` | | seconds Blender may take: 60 in a live session (which then stops the preview and stays usable), 300 for a cold job |
| `satk blender call look.render --session N --params '{"views":["3q"]}'` | — | the game look inside a live session |
| `satk model image <file.dff>` | `model_image` | the GPU-free soft preview, now with the same dirt, lamp and light rules |

## How it works

satk resolves the subject into model plans (cached copies under `work`), computes the shading numbers from the
DFF bytes, and runs one headless Blender job (or the studio method `look.preview` inside a session, where the
lineup models are imported and removed again). The game look is one shared node group, so EEVEE compiles few
shaders, and every material takes the slimmest variant of it (no transparent branch, no day/night branch, no
env, no specular unless it needs them), because EEVEE syncs each material on every render. A cold preview of
4 views x 2 states takes about 5 s, a session preview about 1.1 s (the first one in a fresh session about
0.5 s more, while the shaders compile). Cells are lossless PNG; the sheet is composed and encoded on the
satk side. The same call reuses its output folder.

Class peers of `--lineup class` come from the style peer set of the reference (the set `style profile` and
`asset check` use, such as `car.sedan` or `prop@1-2m`): models whose name shares a word with the like model
first (a litter bin `bin1` gets `CJ_WASTEBIN` and `CJ_BIN1`, not any 1-2 m prop), then the nearest in shape
and triangles; the answer names the peer set in a `NOTE`. A session of an asset project without `--like`
takes the like model from `asset.json`, or its kind and target size when it has none. The session's own
scene is shown the way the game shows an imported model: its wheel on every wheel dummy and, for a vehicle,
the like model's paint on the paint keys (for the render only); a building's LOD slot is the `vlo` state.

## Limitations and known issues

- The light multiplier of lit models and the night share are not yet calibrated against in-game captures; the
  numbers live in `satk.look.gamelook` and are marked uncalibrated.
- Skinned peds are shown in their bind pose (no animation).
- A session subject is rendered as it is apart from the wheels and paint above; damage and LOD states use
  the `_dam`/`_vlo` name suffixes and the kit's LOD slot.

## Python API (if other packages use it)

```python
from satk.look import gamelook as G
G.dirt_rgb((120, 100, 80), 2)       # (238, 236, 233)
G.env_at("21:30")                   # timecyc light: amb, amb_obj, dir, colour filter, day/night balance
```
