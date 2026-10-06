# Viewer: camera, frames with marks, "what is this object"

[Русская версия](../ru/viewer.md)

Package: `satk.viewer`; the viewer is a local fork of Ariane with a native SAAP/1 endpoint (the `view_*` MCP tools).

## What it is

The `satk view` commands drive a target: `ariane` (the offline viewer, an Ariane fork running on the clean game
copy), `mock` (a synthetic world for tests) and later `game` (the MTA client). You can fly to a SID, take a frame
with numbered marks and a grid, ask "what is at this point" and get the stable SID of a placement
(`inst:lae2_stream0#4`). Everything is written to `work\out\captures\<yyyymmdd>\` (PNG + a sidecar `.json`).
Your own models, vehicles and peds can be placed next to the map ([viewscene.md](viewscene.md)); behaviour in the
real game is checked by [ingame.md](ingame.md).

## Quick example

```powershell
satk view pick 400 300 --target mock
satk view capture --target mock --marks 4 --grid
satk view goto --id inst:lae2_stream0#4 --target mock
```

What comes back (shortened):

```json
{"ok":true,"cols":["px","py","id","model","name","x","y","z","dist","link"],"rows":[[400.0,300.0,"inst:lae2_stream0#4","model:17613","lae2_roads89",2489.36,-1669.24,12.9,69.25,"nearest"]],"target":"mock","mode":"visible"}
{"ok":true,"id":"cap:20261005-111222-fd82","file":"<workspace>/work/out/captures/20261005/20261005-111222-fd82.png","marks_file":"…_marks.png","grid_file":"…_grid.png","legend":[[1,"inst:lae2_stream0#4","lae2_roads89",0.0852],[2,"inst:mock#3","mock_grove_house_b",0.0431],…],"settled":true,"w":960,"h":540}
{"ok":true,"target":"mock","pose":{"pos":[2489.355,-1744.056,55.957],"look":[2489.355,-1668.57,12.375],"fov_h_deg":70.0},"framed":"inst:lae2_stream0#4","radius":32.87}
```

Without a running mock endpoint every answer also has the warning that the in-process mock was used.

With the real viewer (the Ariane window opens on the desktop; loading the map takes seconds):

<!-- docs-smoke: skip starts Ariane (a visible window) -->
```powershell
satk view start --target ariane --window 960x540
satk view capture --pos 2495,-1720,60 --look 2495,-1670,15 --fov 70 --width 1920 --height 1080 --layers color,ids,depth --marks 8
satk view pick 480 270
satk view set --overlays col,zones --lod hd
satk view conformance --target ariane
satk view stop
```

A fork build with the native endpoint (P1) answers `"proto":"saap/1","impl":"ariane-satk"`: frames of any size
regardless of the window, an ID buffer (exact marks by pixel share), depth, `pick` by what is drawn, `view set`.
An older build answers `"proto":"ariane-ipc/1"` through the adapter (below).

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk view control start\|stop\|status [--target T] [--profile P] [--window WxH[+X+Y]]` (and the short `satk view start\|stop\|status`) | `view_control` | start (`ariane`: a free port, a token, `--game-dir`, `--data-dir work\viewer`, `--window 1280x720+0+0`, `--no-vsync`, `SATK_AGENT_*` for the native SAAP, waits up to 90 s), stop (`quit` → 10 s → `WM_CLOSE` → termination, only of a verified process), status; `window` is always `[w, h]` |
| `satk view goto (--id SID \| --pos X,Y,Z [--look X,Y,Z \| --ypr Y,P,R] \| --bm NAME) [--fov 70] [--dist M]` | `view_goto` | camera to a SID (framed by its bounding sphere: `2.5·r + 5` m away, 30° from above), to a position or to a bookmark |
| `satk view capture [--pos … --look … \| --ypr … \| --pose JSON \| --bm NAME] [--fov F] [--width --height] [--layers color,ids,depth] [--marks N] [--grid] [--env JSON] [--compare-to CAP] [--no-settle] [--inline]` | `view_capture` | a frame + sidecar; marks, an A…H × 1…6 grid, comparison (a difference image and SSIM); `--inline` lets an MCP client show the picture |
| `satk view pick PX PY [PX PY …] \| --cells D3,E4 [--capture CAP]` | `view_pick` | table `[px, py, id, model, name, x, y, z, dist, link]` |
| `satk view set [--time HH:MM] [--weather N] [--overlays col,zones,paths,tcyc] [--lod normal\|hd\|lod] [--draw-dist K] [--hide SID…] [--highlight SID…] [--postfx]` | `view_set` | environment and view; `UNSUPPORTED` when the target lacks the capability |
| `satk view bookmark save\|list\|rm [NAME] [--pose JSON] [--note TEXT]` | `view_bookmark` | camera bookmarks; addressed as `bm:NAME` |
| `satk view replay <sidecar.json\|cap:…>` | — | repeats a capture and compares sha256 (SSIM when they differ) |
| `satk view conformance --target T` · `satk view mock` | — | see the SAAP page ([Russian for now](../ru/saap.md)) |

## How it works

- **Targets and backends.** The files in `work\run\endpoints\` choose the backend: native SAAP (mock, Ariane
  with P1, later MTA) or the `ARIANE_IPC/1` adapter for Ariane builds without P1. `--target mock` without a
  running endpoint works in-process (with a warning).
- **Native Ariane (P1).** `view start` passes Ariane both the bridge variables and `SATK_AGENT_PORT=0`,
  `SATK_AGENT_TOKEN` (the same token), `SATK_AGENT_DESCRIPTOR`, `SATK_AGENT_OUT_ROOT` (= `work\`). A build with
  the endpoint (`ariane.build.json`: `saap_endpoint`) writes `work\run\endpoints\ariane.json` with
  `protocol:"saap/1"` itself; its capabilities: `core camera world.settle capture capture.size capture.ids
  capture.depth pick pick.visible entity.query entity.inspect env view asset.render`. A frame is drawn offscreen
  into a temporary camera of the requested size (up to 7680×4320), so the window keeps its own view; before the
  render the world is loaded until streaming settles. Layers: `color` (with sky, water and post effects;
  overlays too, unless they were hidden explicitly), `ids` (`<prefix>.ids.png`, `id = r<<16|g<<8|b` →
  `ids_legend`), `depth` (`<prefix>.depth.f32`, float32 along the view axis, `+inf` is the sky). Measured:
  1920×1080 with three layers at a 960×540 window takes 0.5 s; 251 ID-buffer colours in 5 views (Grove Street,
  downtown, San Fierro, Las Venturas, Santa Maria) all give index SIDs (100 %).
  `pick` defaults to `visible`: it hits the crown of a palm tree that has no collision (the collision ray goes
  into the ground). `entity.inspect` gives the TXD, the textures of the materials and of the whole TXD, 2DFX, the
  LOD parent and children, the collision.
- **The Ariane adapter** translates SAAP into bridge commands: `camera`, `capture_pose` with `settle_frames`
  (a fork patch), `raycast_segment`, `inspect_zone_page`, `environment`, `asset_preview`, `quit`. `pick`, both in
  the window and on a frame, builds its own ray per pixel (in the window exactly the one `screen_to_world`
  builds: the basis from `camera_context`) and asks `raycast_segment`. `screen_to_world` itself searches the
  render list and, where an LOD and an HD model coincide at the hit point, returns the LOD (Grove Street, 82 m:
  `lod1carlshou1_lae` instead of `carlshou1_lae2`); `raycast_segment` skips LOD parents. `screen_to_world` is only
  the fallback for a bridge without `raycast_segment`.
  The FOV is converted exactly (librw computes `fov` for 4:3; checked on a live Ariane: 70° → 69.96° edge to
  edge). The adapter's frame size is the window size (no `capture.size`).
- **Profile.** SIDs resolve in the game profile the viewer was started with (`--profile` of `view start`,
  stored in `work\run\sessions\<role>.json`); with no viewer running, `vanilla`.
- **The viewer process.** The discovery files hold the exe path and the process start time. Windows reuses
  pids: if Ariane crashed and another process took its pid, `status` says `stale:true`, `start` replaces the
  files and starts a new viewer, `stop` only removes the files; the other process gets neither `WM_CLOSE` nor
  termination.
- **Identity.** The Ariane fork reports the IPL and the instance index → `link:"exact"`. Otherwise the
  SID is looked up in the index by model and position (5 cm tolerance): `nearest`/`ambiguous`/`none`. While the
  index is not built (`satk index build`), the built-in `FakeIndexDB` is used and the answer has the
  warning `INDEX_MISSING`.
- **Marks.** With an ID buffer (mock, Ariane with P1) they are exact, by pixel share. Without one,
  `approx:true`: a 16×9 grid of `pick` rays in the frame's pose (respects occlusion, SIDs are exact; ~2 s on
  Ariane), and when the target has no `pick`, the projection of the index AABBs.
- **The sidecar** stores the target, build, pose, environment, size, `settled`, the legend and sha256;
  `satk view replay` repeats the frame. On Ariane the replay gave the same sha256.
- The clean game copy is not changed: Ariane runs with `--game-dir` and IMG writes disabled.

## Limitations and known issues

- A minimised Ariane window does not draw (D3D9): `satk view status` answers `NOT_READY` with a hint.
- The Ariane adapter (a build without P1) has no `capture.size/ids/depth`, `view`, `pick.visible`; Ariane has
  no `log`, `console`, `lua`, `mem.read` at all (`UNSUPPORTED`).
- Native Ariane: IDs and depth cover map instances only (water and sky are `0`/`+inf`); `asset.render` uses a
  fixed elevation and background (`el` and `bg` are ignored); camera roll (`ypr[2]`) is ignored; an `i<id>` ref
  lives as long as the process. MSAA stays off in agent runs; with `--keep-msaa` the bridge capture now
  resolves MSAA through `StretchRect` and is no longer empty.
- `pick` through the adapter goes by collision (`mode:"collision"`); objects without a COL are not hit. If the
  ray still hits an LOD instance (an LOD without HD children), the answer has `lod_rows` and the warning
  `LOD: …`: this is not the HD model.
- Fixes: the troubleshooting page ([Russian for now](../ru/troubleshooting.md)).

## Python API

```python
from satk.viewer import api
cap = api.capture("mock", marks=4, grid=True)
rows = api.pick([[400, 300]], target="mock")["rows"]
```
