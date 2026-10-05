# Agent workflows S1-S16 (satk)

<!-- Executable runner: src/satk/docs/workflows.py (S1-S8). English only.
     S1-S8: `satk dev workflow-cost --target ariane --steps` runs them through the MCP dispatch path and
     tests/e2e/test_workflows.py asserts the answers. S9-S16: the same estimate, measured by hand through the
     generic tools (not yet in workflow-cost). Measured 2026-10-05 (0.2.0 preview) with the
     indexes vanilla/installed/samp, the symbol DB, the knowledge base and the model descriptions built. -->

Each workflow: the question, the call chain (MCP names; `satk_op` runs operations without a tool of their own),
what the answer looks like, the stop condition and the measured cost. "Calls" counts tool calls including image
Reads; tokens = (arguments + result) characters / 3.5 + images w*h/750 (an estimate, +-25 %).
Re-measure S1-S8 after changes: `satk dev workflow-cost` (all) or `satk dev workflow-cost S2 --target ariane --steps`.

| # | Question | Calls (budget) | Tokens | Time | Status |
|---|---|---|---|---|---|
| S1 | Where is texture X used and how does it look? | 5 (5) | ~620 | 0.9 s | works |
| S2 | Fly to Grove Street: what is the building on the left? | 6 (8) | ~1 310 | 4.0 s incl. viewer start | works (Ariane, mock) |
| S3 | MTA crashed, here is the log | 3 (4) | ~1 340 | 0.6 s | works |
| S4 | What does my install change? (profile diff -> look) | 3 (4) | ~1 210 | 1.0 s | works |
| S5 | Area -> Blender -> render -> MTA resource | 4 (5) | ~1 850 | 10.5 s | works |
| S6 | Does the viewer match the game? | 3 | - | - | needs `target=game` started by the user |
| S7 | How many models have no collision in Las Venturas? | 1 (1) | ~100 | 0.4 s | works |
| S8 | What does SA-MP override? | 2 (2) | ~350 | 0.9 s | works |
| S9 | Run an operation that has no tool | 2 | ~350 | < 0.1 s | works |
| S10 | Here is a crash dump | 2-4 | ~500-920 | 0.2 s | works |
| S11 | What is at offset X of CPed / what does opcode Y do? | 1 each | ~110-240 | < 0.1 s | works (needs the kb) |
| S12 | Check my mod | 1-3 | ~80-400 each | < 1 s | works |
| S13 | Replace a texture | 3 | ~970 | 0.3 s | works |
| S14 | Find a model by how it looks | 4 | ~360 | 0.1 s | works (needs descriptions) |
| S15 | Vehicle data | 1-2 | ~140-340 | < 0.1 s | works |
| S16 | Convert a map for MTA | 2 | ~350 | 0.2 s | works |

A session usually starts with `satk_status` (~600 tokens) and, if needed, one `satk_help(topic)` (500-1 050).

## S1. Texture: where used, how it looks

1. `asset_find(query="ws_rooftarmac1", kind="tex", limit=5)` (`satk asset find ws_rooftarmac1 --kind tex --limit 5`)
   -> `{"cols":["id","kind","name","info"],"rows":[["tex:2notherbuildsfe/ws_rooftarmac1","tex","ws_rooftarmac1","DXT1 256x256"],...],"total":257,"next":"o5","warn":["MORE: 34 other names contain 'ws_rooftarmac1': asset find '*ws_rooftarmac1*'"]}`.
   The same name in 257 TXDs is normal. 169 tokens.
2. Which models use it (all TXDs at once) - one SQL, 145 tokens:
   `index_query(sql="SELECT v.sid, v.name, v.n_inst FROM model_tex t JOIN v_model v ON v.id = t.model_id WHERE t.texture = ? ORDER BY v.n_inst DESC", params=["ws_rooftarmac1"], limit=5)`
   -> `total` 470, top `model:1684 portakabin` (15 placements), `model:3824 box_hse_10_SFXRF` (12) ...
3. How many different images hide behind the name - 97 tokens:
   `index_query(sql="SELECT v.pix, count(*) AS txds FROM texture t JOIN v_tex v ON v.rid = t.id WHERE t.name = ? GROUP BY v.pix ORDER BY txds DESC", params=["ws_rooftarmac1"])`
   -> 3 rows: `pix:45f2dcad0a0e790249e06eb9` 243 TXDs, `pix:404679390e4ba32005cc74c5` 13, `pix:a9402c6bc5d7f741d16bf5a9` 1.
4. `texture_image(ids=[the 3 pix SIDs], mode="sheet")` -> `{"files":[".../out/sheets/pix_45f2..._3-....png"],"legend":[[1,"tex:a51_ext/ws_rooftarmac1","256x256 DXT1"],...]}` (113 tokens).
5. Read the sheet: 268x268 -> ~96 tokens.
- One TXD's texture only: `asset_refs(id="tex:711_sfw/ws_rooftarmac1")` -> `{"rels":{"models":1,"same_pixels":242,"txd":1}}`,
  then `asset_refs(id, rel="models")`.
- Stop when: you can name the count of TXDs and models, the main users, and show ONE sheet. Never export every texture.

## S2. Viewer: "what is the building on the left?"

Pose: bookmark `bm:grove_center` (street level at Grove Street, looking south at CJ's house; pos 2490,-1655,16,
look 2500,-1700,16, fov 70). Without the bookmark pass `pos`/`look`, or save it with
`view_bookmark(action="save", name="grove_center", pose={"pos":[2490,-1655,16],"look":[2500,-1700,16],"fov":70})`.

1. `view_control(action="status")` -> `{"target":"ariane","up":false}` (22 tokens).
2. `view_control(action="start", window="960x540")` -> `{"up":true,"proto":"saap/1","impl":"ariane-satk","caps":[...],"window":[960,540],"seconds":2.9}` (122 tokens).
3. `view_capture(bm="grove_center", marks=6)` -> (292 tokens)
   `{"id":"cap:...","file":".../captures/20261005/....png","marks_file":"..._marks.png","sidecar":"....json","legend":[[1,"inst:lae2_stream2#127","hub_grnd_alpha",0.2239],[2,"inst:lae2_stream0#4","lae2_roads89",0.1349],[3,"inst:lae2_stream0#8","carlshou1_lae2",0.0796],[4,"inst:lae2_stream2#102","veg_palmbig14",0.0685],[5,"inst:lae2_stream0#39","ganghous05_lax",0.0358],[6,"inst:lae2_stream2#126","hubst4alpha",0.0333]],"settled":true,"w":960,"h":540}`.
   The capture is offscreen at the requested size; marks come from the ID buffer (exact, no `approx`).
4. Read `marks_file` (960x540, ~690 tokens): CJ's house (mark 3) is ahead, mark 5 is the house on the left.
5. Optional: `view_pick(points=[[154,270]], capture="cap:...")` -> `inst:lae2_stream0#39` (92 tokens).
6. `asset_get(id="inst:lae2_stream0#39")` -> `ganghous05_LAx`, `model:3646`, `ipl:lae2_stream0`, its LOD, `aabb` (93 tokens).
- Answer: "the house left of CJ's house is `inst:lae2_stream0#39`, model 3646 `ganghous05_LAx` from `lae2_stream0`".
  If the look matters: `texture_image(ids=["model:3646"], mode="sheet")` (+2 calls).
- Mock: only `inst:lae2_stream0#4` is a real placement; `inst:mock*` are synthetic (`asset_get` -> `NOT_FOUND`).
- Stop the viewer at the end: `view_control(action="stop")` (not counted).

## S3. Crash address -> function -> MTA patches

1. `re_addr(text=["Module = C:\\Games\\GTA San Andreas\\gta_sa.exe\nCode = 0xC0000005\nOffset = 0x0013BF09\n"])` (718 tokens) <!-- linkcheck: ignore -->
   (the path is the player's install as written in the dump)
   -> `{"id":"fn:0x53bee0","addr":"0x53bf09","module":"gta_sa.exe","fn":"CGame::Process","off":"0x29","confidence":"high","src":"game_sa/Game.cpp:78","reversed":true,"patches":{"cols":["addr","kind","symbol","origin","src","hit"],"rows":[["0x53bf09","hookinstall","HOOKPOS_CStreaming_Update_Caller","trunk","Client/multiplayer_sa/CMultiplayerSA.cpp:638",true],...],"total":22}}`.
   Keep the dump's line breaks: `Module = ...` and `Offset = ...` squeezed into ONE line resolve to module `?`
   (`confidence:"none"`). `gta_sa.exe+0x13BF09` and `0x53BF09` work as plain strings.
2. `re_src(fn="CGame::Process", context=12)` -> `{file, line, lines:[...]}` of gta-reversed (226 tokens). Show, never save.
3. `re_patches(fn="CGame::Process", limit=10)` -> 22 patch sites `[addr, kind, symbol, origin, src, func]`
   (393 tokens); `origin` trunk = the MTA fork of the workspace, neon/upstream = donors.
- Golden: `0x53BF09` -> `CGame::Process+0x29` (`Game.cpp:78`) + `HOOKPOS_CStreaming_Update_Caller`.
- A whole crash dump or MTA `core.log`: S10.

## S4. What does my install change? (profile diff -> look)

1. `index_diff(a="vanilla", b="installed", kind="txd", limit=10)` -> `summary.txd` `{"added":3,"changed":0,"removed":0}`,
   rows `txd:ps3btns`, `txd:sixaxis`, `txd:x360btns` (126 tokens).
2. `texture_image(ids=["txd:ps3btns"], mode="sheet", profile="installed")` -> one sheet with 30 cells + legend (375 tokens).
3. Read the sheet (796x664, ~705 tokens).
- For a model mod: `index_diff(..., kind="model")` -> per row `asset_get(id, profile=...)` -> `model_image`.
- Mod files that are not installed yet: S12.

## S5. Blender round trip

1. `blender_job(cmd="import_area", args={"center":[2495,-1687],"box":60,"lod":"hd"})` -> `files.blend`,
   `stats` `{"instances":47,"models":31,"images":109,"txd_images":213,...}` (204 tokens, ~4 s).
2. `blender_job(cmd="render", args={"blend":<files.blend>,"pose":{"pos":[2495,-1757,45],"look":[2495,-1687,13]},"size":"960x540","objindex":true})`
   -> `files.png` + visible SIDs with pixel share (559 tokens, ~3 s). Render args take `pose` (or `bm`), not `pos`.
3. Read the PNG (~690 tokens).
4. `blender_job(cmd="export", args={"blend":<files.blend>,"objects":["carlshou1_lae2"],"target":"mta-resource"})`
   -> DFF/TXD/COL + `meta.xml` + `client.lua` under `work/out/exports/...` (399 tokens, ~2 s).
- A new mesh instead of game objects: `satk_op(op="blender.game_ready", args={"src": "<.blend or .obj>"})` ->
  DFF + COL + LOD + TXD in `work/out/blender/<name>/`.

## S6. Viewer vs game

`view_control(action="status", target="game")` -> `{"up":false}` until the user starts the game target
(`python -m satk.viewer.backends.mta_lua up --client`; the MTA client needs a one-time admin setup on the PC).
`view_control(action="start", target="game")` answers `UNSUPPORTED` (satk does not start it). With it up:
`view_capture(target="ariane", bm=X)` -> `view_capture(target="game", bm=X, compare_to="cap:...")` -> `diff.file`, `ssim`.

## S7. One SQL answer

`index_query(sql="SELECT i.is_lod, count(DISTINCT m.id) AS models FROM v_inst i JOIN v_model m ON m.id = i.model_id JOIN zone z ON z.name = 'VE' WHERE i.x BETWEEN z.minx AND z.maxx AND i.y BETWEEN z.miny AND z.maxy AND i.area = 0 AND m.col_via IS NULL GROUP BY i.is_lod")`
-> `[[0,1],[1,911]]`: 911 LOD models (no collision by design) and 1 non-LOD model without one
(`model:3523 lodenmotel13`, an unreferenced LOD). Zone `VE` (info.zon, type 0) is Las Venturas; `LA`, `SF` likewise.
Schema: `satk_help("schema")` / `docs/agent/schema.md`; views `v_model`, `v_inst`, `v_tex`, `v_file`, `v_vehicle`, `v_ped`.
Read-only: writes, ATTACH and PRAGMA writes -> `READ_ONLY`; 2 s timeout; `limit` caps rows (`warn: LIMIT`).

## S8. What SA-MP overrides

1. `index_diff(a="vanilla", b="samp", kind="model", limit=10)` -> `summary.model` `{"added":1435,"overridden":12,"removed":0,"same_def_other_layer":212}`
   + rows `[id, kind, change, a, b]` (310 tokens).
2. `asset_get(id="model:300", profile="samp", fields=["name","sec","layer"])` -> `lapdna` (`peds`, layer `samp`);
   the vanilla definition is `model:300@vanilla` = `cutobj01` (`hier`).

## S9. An operation without a tool (generic access)

1. `satk_ops(query="dump", limit=3)` -> rows `op | args | summary`: `formats.dump` (`target:str, level:stats|full|tree="stats", ...`), ... (285 tokens).
2. `satk_op(op="formats.dump", args={"target":"models/gta3.img/infernus.txd"})` ->
   `{"id":"file:models/gta3.img/infernus.txd","kind":"txd","textures":3,"rw_version":"0x36003","by_fmt":{"DXT1":2,"DXT3":1}}` (68 tokens).
- An unknown parameter answers `BAD_PARAMS` with the real names; a refused operation `UNSUPPORTED` (CLI only) or
  `CONSENT_REQUIRED` (ask the user). `satk_ops` with one hit returns the full JSON schema of that operation.

## S10. Crash dump -> stack -> function -> patches

1. `satk_op(op="crash.list", args={"limit":5})` -> dumps and crash logs, newest first, with `module`, `offset`, `code`
   (work/dumps, the MTA fork's Bin, installed MTA builds, samples) (122 tokens).
2. `satk_op(op="crash.analyze", args={"last":true,"limit":5})` (or `"path": "<.dmp or core.log>"`) -> (392 tokens)
   `{"crash":"0xC0000005 ACCESS_VIOLATION reading 0x00000010 at gta_sa.exe+0xe6a3 (thread 4120)","fn":"CStreaming::Update+0x33","src":"game_sa/Streaming.cpp:111","patched":"no patch at the address; 2 MTA patch sites in this function","pools_full":["ped 140/140"],"cols":["#","addr","at","fn","src","mta","via"],"rows":[[0,"0x40e6a3","gta_sa.exe+0xe6a3","CStreaming::Update+0x33","game_sa/Streaming.cpp:111",null,"ip"],[1,"0x53bf10","gta_sa.exe+0x13bf10","CGame::Process+0x30","game_sa/Game.cpp:78","HOOKPOS_CStreaming_Update_Caller","scan"],...]}`
   (the generated sample from `satk_op(op="crash.sample")`). Frames that could not be verified are marked `?` with `warn: UNVERIFIED`.
3. `re_src(fn="CStreaming::Update", context=12)` (289 tokens) and/or 4. `re_patches(fn="CStreaming::Update", limit=10)` (114 tokens).
- Single-player crash with mods (`satk_op(op="crash.sample", args={"kind":"sp"})` makes one):
  `satk_op(op="crash.analyze", args={"path":"<gta_sa .dmp>","game":"<game folder>","limit":3})` -> (~560 tokens)
  `fn` `CPickup::GiveUsAPickUpObject+0x29`, `known` `["#2 0x00456809 (ip): Creation of a pickup using a model that no
  longer exists."]` + `solution`, `culprit` "model 20101 is missing: only the disabled mod CoolPickups defines it",
  `suspects` (EAX=0x4e85 -> model 20101), `logs` (modloader, scrlog last command `[0213] CREATE_PICKUP 20101 ...`, cleo).
- No culprit found: `satk_op(op="crash.bisect", args={"path":"<modloader folder>"})` prints the IgnoreMods section
  for the next test run; the user runs the game and reports the result (`results`). `crash.known` looks up the
  CrashInfo list by address, opcode, script or module; `crash.logs` digests modloader/scrlog/cleo logs.
- Stop when: you can name the crashing function, its source line, the known solution or the culprit mod, and
  whether MTA patches it.

## S11. Engine knowledge (kb)

Needs the knowledge base: `satk_status` -> `kb.built`; otherwise the user runs `satk kb build` (~30 s, reads the donor
sources).
- `satk_op(op="kb.struct", args={"name":"CPed","at":"0x540"})` -> `{"size":"0x79C","bases":"CPhysical","fields":{"rows":[["0x540","m_fHealth","float",4,"plugin-sdk"]]}}` (135 tokens).
- `satk_op(op="kb.opcode", args={"query":"0A8C","limit":1})` -> `WRITE_MEMORY`, CLEO, 4 parameters, handler
  `WriteMemory (source/game_sa/Scripts/Commands/CLEO/CLEOMemoryCommands.cpp:11)` (112 tokens).
- `satk_op(op="kb.search", args={"query":"CStreaming RequestModel","limit":3})` -> `CStreaming::RequestModel` at
  0x4087E0 in gta-reversed `Streaming.cpp:1377` and plugin-sdk, `total` 16 (235 tokens).
- `kb.fact` lists curated engine facts (pools, limits, entry points) with automatic checks; `kb.sym` finds symbols.

## S12. Check a mod (Mod Loader view) and lint its files

- `satk_op(op="mod.check", args={"path":"<mod folder, .zip or .img>"})` -> rows `rule | sev | file | msg | sid`:
  ID collisions, files Mod Loader skips, dropped game records, IDs over the limit, asset lint (or INU Check when the
  user installed it, `engine="inu"`). A one-line `handling.cfg` -> `mod.drops_records` warn: "239 game record(s)
  are missing from this file; with Mod Loader they disappear from the game".
- `satk_op(op="mod.inspect", args={"path": ...})` -> rows `kind | target | change | by | detail`
  (`handling:INFERNUS` modify `mass 1400->1500, ...`; `handling:ADMIRAL` remove ...). A .zip is read without extracting.
- Several mods: `satk_op(op="mod.conflicts")` (the profile's modloader folder or given folders: winner per file and
  per data record), `satk_op(op="mod.effective", args={"file":"handling","key":"411"})` (the line the game gets).

Single asset files:

`satk_op(op="asset.lint", args={"target":"<file, folder, IMG entry or SID>","sev":"warn"})` -> rows
`rule | sev | file | msg`, `summary` per severity, `by_rule`.
- `model:411` (vanilla): 0 at `warn` and above, 17 `info` (`txd.mips_missing` in `vehicle.txd`) (78 tokens).
- A game root is linted the way the game loads it (IDE files from default.dat/gta.dat only).
- Rules and presets: `satk_op(op="asset.lint_rules")`; `preset="strict"` for new content.

## S13. Texture replacement (modloader layout)

1. `satk_op(op="texture.extract", args={"txd":"txd:bistro"})` -> every texture as PNG + `texmod.json` in
   `work/out/texmod/bistro/` (536 tokens). Edited PNGs survive a repeated extract.
2. The user (or you) edits a PNG.
3. `satk_op(op="texture.replace", args={"txd":"txd:bistro","swaps":["vent_64=<png path>"]})` ->
   `work/out/mods/bistro/bistro.txd` + README, rows with format, size, mips and PSNR (167 tokens).
4. S12 on the new TXD (269 tokens). A new TXD from a folder of PNGs: `texture.pack`.

## S14. Find a model by how it looks

Model descriptions (MIT texts from gta-scout, ~2 100 vanilla models) are imported once into notes:
`satk describe import` (the user; `satk_op(op="describe.status")` shows the state).
1. `asset_find(query="bench", kind="note", limit=3)` -> `[["note:1120","note","model:4085","Two simple backless benches ..."],...]`, `total` 15 (134 tokens).
2. `asset_get(id="model:4085", fields=["name","sec","txd","n_inst"])` (43 tokens).
3. `model_image(id="model:4085", views=1, size=256)` (97 tokens) -> 4. Read (256x256, ~87 tokens).

## S15. Vehicle data (index schema v3)

1. `asset_get(id="model:411", fields=["name","handling","colors","links"])` -> `handling` summary (`mass` 1400,
   `max_vel` 240, `gears` 5, `drive` "4"), 8 colour pairs, `links.handling` = `handling:infernus` (142 tokens).
2. `asset_get(id="handling:infernus")` -> every handling.cfg field plus model and handling flags (198 tokens).
- Water: `world_near(x, y, r, kinds=["water"])` -> `water:<idx>` quads. Time cycle: `asset_get(id="tcyc:extrasunny_la/12")`.

## S16. Map conversion (SA-MP Pawn, MTA .map, text IPL, JSON)

1. `satk_op(op="map.validate", args={"src":"ipl:lae2_stream0","limit":3})` -> rows `sev | code | where | msg`
   (`LOD_EXTERNAL`: 52 LOD links point into another file; `TILT_IGNORED`: 13 small tilts the engine ignores),
   `objects` 377, `errors` 0 (~150 tokens).
2. `satk_op(op="map.convert", args={"src":"ipl:lae2_stream0","to":"mta"})` -> `file`
   `work/out/mapconv/lae2_stream0.map` + a JSON report, `objects` 377, `skipped` `{"cars":5}` (~190 tokens).
- `src` is a file (`.pwn`, `.map`, `.ipl`, `.json`) or an `ipl:` SID; `to` is pawn, mta, ipl or json. Euler angles
  and IPL quaternions are converted exactly (the IPL quaternion is the conjugate of the world rotation).
