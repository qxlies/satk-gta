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
game's colour filter and display gamma. Peds stand upright, gun flashes are hidden; a parked helicopter, plane or
boat shows its static rotor or propeller (the spinning discs only appear at speed).

Two review tools build on the same scene: **close-up regions** (`--regions`) render every review region of the
asset's kind as its own sheet (a car: front, rear, left, right, wheels and arches, underside, interior, roof; a
building: each facade, roof, entrance, LOD comparison; a prop: sides, top, base, details; every kind has its
list), and the **light-leak check** (`satk look leak`, or the pass `--passes leak`) finds the places where a model
lets the background through: see-through gaps between parts, open shells, faces the game culls.

## Quick example

```powershell
satk blender preview model:426 --views 3q,side --states ok,dam
satk blender preview model:426 --lineup model:405,model:560 --passes game,wire
satk blender preview model:400 --regions front,wheels --passes game,leak
satk look leak model:400 --regions wheels
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
| `--ref photo.jpg [--ref-view side\|front\|rear] [--ref-flip] [--ref-box x0,y0,x1,y1]` | | a TRUE elevation photo (far away, both wheels round) drawn behind the subject in that view of the game pass at 30 %, scaled so the object in the photo spans the subject (found against the photo's border colour, or `--ref-box`); the side view shows the front on the left, `--ref-flip` mirrors a photo that faces right. An outline check, no numbers; never a three-quarter photo |
| `satk blender call look.render --session N --params '{"views":["3q"]}'` | — | the game look inside a live session; like the preview it puts the wheel on every wheel dummy and paints the paint keys (`colors`, default the new-model blue) for the render only |
| `satk blender call look.silhouette --session N --params '{"ref":"side.jpg","view":"side"}'` | — | the model's silhouette against a true side, front or rear photo: overlap (IoU), the top line at 20 stations along the length in metres (`worst` = the biggest differences) and an overlay PNG; information for a critic, never a gate |
| `satk model image <file.dff>` | `model_image` | the GPU-free soft preview, now with the same dirt, lamp and light rules |
| `--regions all` or `--regions front,wheels` `[--kind automobile]` | | close-up review regions of the subject's kind: one sheet per region `preview-NNN-<region>.jpg` (one `NNN` per run) and an index sheet `preview-NNN-index.jpg`; the answer lists each region with what to check there. The kind comes from `--kind`, `asset.json`, the index row of the model (or of `--like`), else the frame names |
| `--passes leak` | | the light-leak cell of each view (see below) and the located gaps in the answer |
| `satk look leak <subject> [--regions ...] [--views ...] [--package <dir>]` | — (`satk_op`) | the light-leak check from the review cameras of the kind: one sheet, the gaps (`id kind region view area_cm2 point near seen_in`), `clean`; `--package` writes `<dir>/checks/<stem>.leak.json` (the folder of the exported DFF; one file per DFF), which `asset check --strict` reads (it accepts only a full run: every leak region of the kind, default settings) |
| `satk blender call look.leak --session N --params '{"kind":"automobile"}'` | — | the same check on the scene of a live session |

## How it works

satk resolves the subject into model plans (cached copies under `work`), computes the shading numbers from the
DFF bytes, and runs one headless Blender job (or the studio method `look.preview` inside a session, where the
lineup models are imported and removed again). The game look is one shared node group, so EEVEE compiles few
shaders, and every material takes the slimmest variant of it (no transparent branch, no day/night branch, no
env, no specular unless it needs them), because EEVEE syncs each material on every render. A cold preview of
4 views x 2 states takes about 5 s, a session preview about 1.1 s (the first one in a fresh session about
0.5 s more, while the shaders compile). Cells are lossless PNG; the sheet is composed and encoded on the
satk side. The same call reuses its output folder; every run keeps its own sheet `preview-NNN.jpg` (the answer
names it; `preview.jpg` is the latest), and a run that draws the same pixels reuses the newest number.

Class peers of `--lineup class` come from the style peer set of the reference (the set `style profile` and
`asset check` use, such as `car.sedan` or `prop@1-2m`): models whose name shares a word with the like model
first (a litter bin `bin1` gets `CJ_WASTEBIN` and `CJ_BIN1`, not any 1-2 m prop), then the nearest in shape
and triangles; the answer names the peer set in a `NOTE`. A session of an asset project without `--like`
takes the like model from `asset.json`, or its kind and target size when it has none. The session's own
scene is shown the way the game shows an imported model: its wheel on every wheel dummy and, for a vehicle,
the like model's paint on the paint keys (for the render only); a building's LOD slot is the `vlo` state.
The class of a session's model comes from its tags, not only from its meshes: `satk_sec` on the kit's frame
objects and blank pieces, the kit collection's group, a vehicle blank, the empties of the scene (wheel dummies,
`chassis_dummy`). So a session car always gets the game look of a car: the paint keys swapped (the like
model's colours or the new-model blue, never the green key) and dirt level 2 on `vehiclegrunge256`.

### Review regions

The regions of every kind are data: `data/kit/regions/<kind>.json` (every kit kind, plus `lod` and `terrain`
for map LODs and large flat ground pieces: no street-level cameras). A region names what to
check and how to frame it: cameras by azimuth and elevation (`az` 0 = in front, 90 = right, -90 = left, or
`out` = the side its frame is on), a box in fractions of the model (x left to right, y rear to front, z bottom
to top) or frames (`wheel_*_dummy`: one close-up per wheel), and render options (hide the doors and the glass of
an interior cutaway, no ground under an underside view, the `vlo` state next to `ok` for a building's LOD, cameras
standing inside an interior room). A box region is framed from points spread over the triangles, so a wall that
runs through it counts; a region the model has no surface in is skipped (`SKIPPED` note and a
`preview-NNN-<region>.skipped.txt` marker). The `lod` region of a map model shows its LOD model: the IPL LOD of a
game model, or the LOD DFF beside a file (its kit sidecar names it, else `lod<name>.dff`); without one the answer
says NOT_FOUND. Inside cameras with `rooms` stand in every room: floor faces under a ceiling, grouped per storey and
per touching 1 m cells, one camera position per room at eye height (up to six rooms, largest first).

### The light-leak check

Every pixel of a review camera is a ray through the model (a BVH of its visible meshes, no rendering). The ray
follows the engine: glass and alpha textures let it through; on a vehicle a back face stops it (vehicles are
drawn double-sided), on everything else it is skipped (world objects, peds and weapons are drawn with back faces
culled, unless the IDE flag `0x200000` turns culling off). The cell is the model black on a bright background,
the gaps painted and boxed:

- `gap` (red): the background seen between parts, enclosed by the model and through its shell (an up probe
  meets the shell from inside) - a hole through the body, a slit between panels. Roof rails, spoilers and open
  frames are outside the shell and stay quiet. Slits are gaps too: a lane of background at most 3.5 cm wide and
  10 cm2 big between two parts at about the same depth, found by closing the model's silhouette (never next to a
  wheel) - for map kinds and melee weapons, and on planes in the straight-down and straight-up views (the wing
  roots); road vehicles leave slits out (their rails, bars and grilles stand off by design);
- `inside` (orange): a vehicle shows the inside of its shell through an opening; it blocks in the wheel close-ups
  when the inner surface is at least 1 m behind the opening and 60 cm2 big (an open arch shows the far side of
  the body); elsewhere it is listed as information (`info`, pale);
- `see_through` (magenta): only culled back faces along the ray, the game shows the background: a hole into a
  shell blocks (the culled faces lie behind the rim of the opening, or the piece is not flat); the back of a
  one-sided flat plane (a fence, a facade card) seen from behind is information. Back faces met edge-on and a
  front face lying on a back face (a double-sided card) are not counted;
- `escape` (red): a camera inside a room sees the void through a crack (a slit up to 12 cm; a door or window
  opening is information).

Calibration: no gap on 55 vanilla models (34 cars, vans, trucks and buses, bikes, a bicycle, a quad, helicopters,
boats, a plane, a trailer, a train, props, a building, an interior, weapons, a ped, a pickup, an upgrade); the
see-through front arches of an agent-built SUV are found in the wheel close-ups. Each gap has its view, pixel box,
area, an approximate 3D point (the depth of the rim around it) and the nearest part; the same gap seen from several
views is merged (`seen_in`). `<stem>.leak.json` holds `clean`, `blocking`, `counts`, `gaps`, `info`, the
parameters, the views, the `coverage` of the run (every leak region of the kind at the default settings: `full`)
and the SHA-256 of the checked DFF; satk also keeps its own record of every run
(`<work>/out/leak/records/<sha256>.json`), and `asset check --strict` refuses a file that differs from it.
Recalibrated after the slit and hole rules: 201 of 212 vanilla vehicles, 47 of 50 weapons and every sampled ped
are clean; vanilla map objects still show real see-through (open backs, one-sided shells) from the facade cameras.

## Limitations and known issues

- The light multiplier of lit models and the night share are not yet calibrated against in-game captures; the
  numbers live in `satk.look.gamelook` and are marked uncalibrated.
- Skinned peds are shown in their bind pose (no animation).
- A session subject is rendered as it is apart from the wheels and paint above; damage and LOD states use
  the `_dam`/`_vlo` name suffixes and the kit's LOD slot.
- The leak check sees only what its cameras see: a crack hidden from every review camera is not found. Thresholds
  (1 m, 60 cm2, 12 cm) are calibrated on vanilla models; designed openings inside the shell of a closed body (a
  window without glass) are reported as gaps.

## Python API (if other packages use it)

```python
from satk.look import gamelook as G
G.dirt_rgb((120, 100, 80), 2)       # (238, 236, 233)
G.env_at("21:30")                   # timecyc light: amb, amb_obj, dir, colour filter, day/night balance
```
