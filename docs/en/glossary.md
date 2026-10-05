# Glossary

[Русская версия](../ru/glossary.md)

<!-- Game terms (IDE, IPL, TXD, LOD, area, SID), project terms and the terms of the component pages
     (checked against the pages on 2026-10-05). -->

## Game formats and data

| Term | What it is |
|---|---|
| **IMG** | a game archive (`models\gta3.img` and others). Version 2 (`VER2`) has the directory at the start of the file; sizes are in sectors of 2048 bytes. When names collide, the archive registered first wins |
| **DFF** | a RenderWare model: frames, geometries, materials, 2DFX effects; car DFFs also hold the collision |
| **TXD** | a RenderWare texture dictionary; textures are usually DXT1/DXT3 compressed |
| **txdp** | a "parent" TXD: when a texture is not in its own TXD, the engine looks in the parent (and, for cars, in `vehicle`) |
| **COL** | collision files (`COLL`, `COL2`, `COL3`, `COL4`); they are matched to a model **by name** |
| **IDE** | a definitions file: model number, DFF name, TXD name, draw distance, flags; sections `objs`, `tobj`, `anim`, `cars`, `peds`, `weap`, `hier`, `txdp`, `2dfx` |
| **IPL** | a placement file: which model stands where. Text (`data\maps\…\*.ipl`) and binary (`bnry`, inside IMG, `xxx_streamN`) |
| **inst** | one placement record: model, position, quaternion, interior/area, link to the LOD |
| **LOD** | a simplified model seen from afar. A placement of an HD model links to its LOD placement; the LOD of a binary IPL points into the text IPL with the same prefix. LOD models need no collision |
| **area / interior** | the number of the "world" (0 is outside, others are interiors). In the IPL field the low byte is the area, the high bits are flags |
| **DAT** | `data\default.dat`, `data\gta.dat`: which IMG, IDE, IPL and COL files to load and in which order; SA-MP uses `.two` files |
| **IFP** | an animation package (`ANP3`/`ANPK`) |
| **ZON** | map zones (`map.zon`, `info.zon`): rectangles with codes such as `GAN1` (Ganton) or `VE` (Las Venturas); the in-game names come from `text\american.gxt` |
| **2DFX** | effects attached to a model: lights, particles, entry points and so on |
| **prelit** | vertex colours, "baked" lighting; buildings have a day set and a night set |
| **RW, RenderWare** | the rendering engine of GTA:SA; its files are nested chunks `type/size/version` |
| **DXT1/3/5** | compressed texture formats; the vanilla game has no DXT5 at all (64 exist only in the console button textures of the original installation) |
| **IPL quaternion** | the rotation is stored in the file "as is", and the world rotation is its **conjugate**; satk returns the world `rz`/`q` |
| **`water.dat`, `timecyc.dat`, `handling.cfg`** | data files the exe loads by name: water polygons, weather × hour colours, vehicle physics; in the index since schema v3 (`water:`, `tcyc:`, `handling:`) |
| **HOODLUM exe** | our reference `gta_sa.exe` 1.0 US (sha256 `a559aa77…cbd26`); symbol addresses are for it. The `.HOODLUM` section holds the function bodies that the protection moved and the thunks to them |

## satk concepts

| Term | What it is |
|---|---|
| **SID** | a stable string ID of an object: `model:411`, `inst:lae2_stream0#4`, `fn:0x53bf09`. See [ids.md](ids.md) |
| **workspace** | the folder where satk keeps `satk.toml` and everything it generates (`work\`); see [install.md](install.md) |
| **profile** | how the game sees a particular installation: `game` (your game folder), `vanilla` (the clean copy), `installed` (the original with mods), `samp` (the original with SA-MP's archive order). Each has its own index file |
| **layer** | where a file came from: `vanilla`, `samp`, `modloader:<mod>`, `modded` |
| **active / shadowed** | the object the game really loads, and the one that lost a name collision (`@layer` in the SID) |
| **blob** | the content of one IMG entry or of one loose file |
| **envelope** | the shape of every answer: `{"ok":true,…}` or `{"ok":false,"error":{…}}`; lists are `cols`/`rows` |
| **warn** | optional warnings in a successful answer: `MORE`, `LIMIT`, `INDEX_STALE`, `FALLBACK`… |
| **golden** | reference numbers (how many textures, models, placements…) the tests check against (`tests/golden/*.json`) |
| **contact sheet** | one picture with a grid of small textures and numbers; the labels are in a JSON legend |
| **pix** | the hash of a texture's pixels: the same picture in different TXDs is one `pix:` and one PNG in the cache |
| **soft renderer** | satk's own software rasterizer for model previews: no GPU and no window, byte-for-byte repeatable |
| **raw export** | the DFF + TXD + COL of a model as separate files, "as in the game" (for DragonFF and mods) |
| **target** | where `view_*` looks: `ariane` (the viewer), `game` (the real game through the MTA fork and the `satk-agent` resource; you start it yourself), `mock` (a synthetic test world) |
| **ID buffer** | a frame layer where every pixel holds the ID of the object drawn there; with it marks and shares are exact |
| **marks** | numbers drawn on the objects of a frame; the legend links a number to a SID. With an ID buffer (native Ariane, `mock`) they are exact; without one `approx:true`: a grid of `pick` rays |
| **pick** | "what is under this pixel": SID, model, hit point. Native Ariane picks what is drawn, the adapter of older builds casts a collision ray; `link:"exact"` means the target reported the IPL and index, `nearest` means it was found in the index by model and position |
| **settle / settled** | waiting until streaming has loaded the models before a frame; `settled:false` means the frame may have holes |
| **capture (`cap:`)** | a taken frame: PNG + sidecar, recorded in the notes database |
| **sidecar** | the `.json` next to a frame: target, pose, environment, legend, sha256; `satk view replay` repeats the frame |
| **bookmark (`bm:`)** | a named camera pose, for example `grove_center` |
| **SAAP/1** | San Andreas Agent Protocol, our "agent eyes" protocol (TCP 127.0.0.1, length + JSON, token, `caps`); our Ariane fork speaks it natively |
| **ARIANE_IPC/1** | the bridge of older Ariane builds without the native endpoint; satk talks to it through an adapter |
| **caps** | the capabilities of a target (`camera`, `capture`, `capture.ids`, `pick`, `env` …); a missing one gives `UNSUPPORTED` |
| **MCP** | Model Context Protocol: how an AI assistant calls the satk tools; `tools/list` is the description of all tools that the model reads at the start of a session |
| **symdb, sa-re** | the symbol database of `gta_sa.exe`: functions, globals, vtables, MTA patches with file and line |
| **confidence** | how reliably an address is attributed to a function: `high`, `medium` (an unnamed start lies in between), `low`, `none` |
| **thunk** | a short jump to the real body of a function (in `gta_sa.exe`, through `.HOODLUM`) |
| **HOOKPOS / MemPut** | the ways MTA patches `gta_sa.exe`: a jump into its own code at an address, and writing bytes |
| **upstream / trunk / neon** | where a patch comes from: vanilla MTA, our fork `engine\mtasa`, the Neon fork |
| **kb** | the knowledge base about the engine's internals: functions, structures, opcodes, checked facts (`satk kb`) |
| **Blender job** | the folder `work\blender\jobs\<id>\` with the request, answer, log, `.blend` and PNG of one call |
| **objindex** | a second Blender render where every object has its own colour: it gives the SIDs of the visible objects |
| **portable zip** | the release `satk-<version>-win64.zip` with its own Python; its folder is the workspace (`portable.txt`) |
| **worktree** | a separate working copy of the `tools` repository on its own branch (`work\wt\<id>`), so agents do not get in each other's way |

## Projects and programs

| Term | What it is |
|---|---|
| **MTA:SA** | Multi Theft Auto: San Andreas, a multiplayer mod with Lua; our engine fork is made from `mtasa-blue` |
| **Neon** | an MTA fork with engine features; for us, a code donor (only into the GPL fork) |
| **sa-engine** | the name of our project: the MTA fork with engine changes |
| **ledger** | the register of code ported from Neon (`engine\mtasa\docs\porting\neon-ledger.toml`) |
| **premake / MSBuild** | the Visual Studio project generator and the build tool; satk calls them directly, without the IDE |
| **deps-lock** | `engine\deps-lock.json`: the sha256 of every downloaded dependency of the client build; a new hash only explicitly |
| **Ariane** | a GTA map editor and viewer (on librw); our fork is the agent's "fast eyes" |
| **librw** | an open implementation of RenderWare that Ariane runs on |
| **gta-reversed** | a reverse-engineering project with the source of GTA:SA functions; for us, a reference of addresses and code (never committed to git) |
| **Ghidra** | a disassembler/decompiler; the project and export for `gta_sa.exe` are in `work\re\ghidra\`; `satk re build --ghidra-functions` takes exact function bounds from it, `satk re export ghidra` writes the symbols back |
| **SA-MP** | San Andreas Multiplayer; it changes the archive order and adds its own models |
| **Mod Loader** | a popular ASI plugin that loads mods from `modloader\<mod>\` without touching the IMG archives; satk sees those files as `modloader:<mod>` layers |
| **DragonFF** | a Blender add-on for DFF/TXD/COL; satk calls its importers directly |
| **Codex / codexctl** | a second model for parallel tasks with Claude's review (`agents\codex\`) |
