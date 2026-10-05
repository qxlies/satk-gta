# Quick start: from zero to the first screenshot

[Русская версия](../ru/quickstart.md)

<!-- At most 10 commands to the first screenshot (checked by tests/e2e/test_docs_layout.py). Walked through by
     hand on 2026-10-05 in a full development workspace (native Ariane build): the answers below are real and
     shortened; the work folder was a private one, so its paths are replaced by work\. -->

You need Windows, Python 3.12, 3.13 or 3.14 (or the portable zip) and a GTA San Andreas folder; set satk up first
with [install.md](install.md). Here `satk` means `tools\satk.cmd` of your checkout (in Git Bash `tools/satk.sh`),
run from the workspace folder, or the console of the portable zip. The answers below come from a full development
workspace ([workspace-gta.md](workspace-gta.md)): the clean 1.0 US copy `<workspace>\gta-sa-clean` (profile
`vanilla`) and the built Ariane viewer `<workspace>\viewer\ariane\bin\ariane.exe`. With your own game folder the
numbers differ; without the viewer, steps 7–10 answer `NOT_READY` (the checked version at the end uses the test
target `mock`). When everything is installed and built, the ten commands take about 20 seconds.

## Ten commands

```powershell
# 1. Once, in the workspace folder: a venv on Python 3.12-3.14 and the run-time packages (Pillow, numpy, mcp; ~70 MB from the network)
powershell -ExecutionPolicy Bypass -File tools\scripts\bootstrap.ps1 -Deps

# 2. Is everything in place: Python, packages, game copy, index, Blender, MSBuild, viewer (27 checks)
satk doctor

# 3. The clean copy is intact: 416 files checked against the manifest
satk game verify

# 4. Index of every asset and placement of the vanilla profile (~8 s; later only when the game changes)
satk index build

# 5. Search by name: models, TXDs, textures and animations whose name contains "grove"
satk asset find "*grove*"

# 6. All 13 textures of the Infernus car on one numbered sheet (the answer has the PNG path and a legend)
satk texture image model:411 --mode sheet

# 7. Start the viewer: a 960x540 window in the top left corner (do not minimize it: a minimized window draws nothing)
satk view start --window 960x540

# 8. Camera on Grove Street at street level, looking south at CJ's house
satk view goto --pos 2490,-1655,16 --look 2500,-1700,16

# 9. A frame with 6 numbered objects: the answer has the path to *_marks.png and a "number -> SID" legend
satk view capture --marks 6

# 10. Close the viewer
satk view stop
```

With the portable zip (from the [Releases](https://github.com/qxlies/satk-gta/releases) page, see
[release.md](release.md)) skip step 1: double-click `Start satk.cmd`, it opens a console where `satk` works.

## What you will see

Times and answers of a real run (shortened):

| Step | Time | The main part of the answer |
|---|---|---|
| 1 | 2.4 s (everything already installed; the first time downloads) | `Requirement already satisfied …`, `{"ok":true,"satk":"0.1.0","python":"3.12.10",…}` |
| 2 | 2.3 s | `counts.ok: 25`, `fail: 0`; before step 4 `status: warn` is normal: `index_fresh` (fix: `satk index build`) and the optional `kb` (fix: `satk kb build`) |
| 3 | 0.9 s | `{"ok":true,"files":416,"bytes":5029186364,"mismatch":0,"missing":0,"extra":0,"exe":"hoodlum-stock","manifest":"ok","protected":"8/8",…}` |
| 4 | 8.6 s | `"counts":{"txd":4046,"texture":32878,"dff":15356,"inst":50935,…},"lod":{"links":6103,"unresolved":0}` |
| 5 | 0.8 s | 20 rows of 24 (`"n":20,"total":24,"next":"o20"`; the other 4: `--cursor o20` or `--limit 50`): `dff:csgrove1`, 4 TXDs (`txd:11grove` …), 11 textures (`tex:tags_lafront/grove` …), animations `ifp:grove1a` … |
| 6 | 0.9 s | `"files":["…/work/out/sheets/model_411-….png"],"legend":[[1,"tex:vehicle/carpback","16x16 X8R8G8B8"],…13 rows]` |
| 7 | 3.7 s | `{"up":true,"proto":"saap/1","impl":"ariane-satk","caps":["core","camera",…],"window":[960,540],"seconds":2.8}` |
| 8 | < 1 s | `{"pose":{"pos":[2490,-1655,16],"look":[2500,-1700,16],"fov_h_deg":70}}` |
| 9 | 1.1 s | the legend below, `"settled":true`, `"w":960,"h":540`, `"sha256":"013efcf1…"` |
| 10 | < 1 s | `{"up":false,"stopped":true,"how":"quit"}` |

The frame legend (step 9): number on the picture → placement SID → model name → share of the frame.

```json
[[1,"inst:lae2_stream2#127","hub_grnd_alpha",0.2239],[2,"inst:lae2_stream0#4","lae2_roads89",0.1349],
 [3,"inst:lae2_stream0#8","carlshou1_lae2",0.0796],[4,"inst:lae2_stream2#102","veg_palmbig14",0.0685],
 [5,"inst:lae2_stream0#39","ganghous05_lax",0.0358],[6,"inst:lae2_stream2#126","hubst4alpha",0.0333]]
```

Mark 3 is CJ's house (`carlshou1_lae2`) straight ahead, mark 5 is the house to its left, mark 4 a palm tree.
Any SID can be expanded: `satk asset get inst:lae2_stream0#39` → model `model:3646` `ganghous05_LAx`, file
`ipl:lae2_stream0`, its LOD and bounding box.

Where everything went:

- sheets: `<workspace>\work\out\sheets\`, frames: `<workspace>\work\out\captures\<date>\`;
- next to a frame: `<id>.png` (the clean frame), `<id>_marks.png` (with numbers), `<id>.ids.png` (the ID buffer)
  and `<id>.json` (the sidecar: camera pose, time, weather, legend, sha256); `satk view replay <sidecar>` repeats
  the frame and compares the hash;
- the viewer log: `<workspace>\work\logs\viewer-ariane.log`.

Ariane is the "fast eyes": no shadows, people, cars or particles, and approximate LODs. The fork answers
`"proto":"saap/1"` and has an ID buffer, so the marks are exact (shares are counted in pixels). The frame size is
`--width`/`--height` of `view capture` (960x540 by default) whatever the window size; `--window 960x540` only
keeps the window small. An older Ariane build without the native endpoint answers `"proto":"ariane-ipc/1"`:
its frame is the size of the window and its marks are approximate (`approx:true`), see [viewer.md](viewer.md).

## The same from an AI assistant

When the MCP server `satk` is registered (Claude Code: `<workspace>\.mcp.json`, checked with
`satk mcp config --check`; other clients: [ai.md](ai.md)), ask the assistant, for example: "Find the grove
textures and show them on one sheet" or "Fly to Grove Street and tell me what the building on the left is".
It calls the same operations (`asset_find`, `texture_image`, `view_control`, `view_capture`, `asset_get`),
5–6 calls per question.

## Quick example

The checked version (`satk dev docs-smoke` runs it; the viewer is replaced by the test target `mock`, which
without a running endpoint works inside the process and says so in `warn`):

```powershell
satk game verify
satk index build
satk asset find "*grove*"
satk texture image tex:tags_lafront/grove --mode sheet
satk view capture --target mock --marks 4
```

## Next

- [Identifiers (SIDs)](ids.md), [common tasks](workflows.md), [if something does not work](troubleshooting.md).
- Component pages are listed in the [table of contents](README.md).
