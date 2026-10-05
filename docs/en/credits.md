# Credits and sources

[Русская версия](../ru/credits.md)

<!-- Owner: docs. The full list of sources behind satk; README.md has the short Acknowledgements. Built from
     every URL of the research reports 01-31 and the design notes (GitHub links point to repository roots).
     The Russian mirror docs/ru/credits.md has the same sections. -->

satk stands on the work of many people. This page lists the projects, documents and sites we used: the code and data satk ships or reads, and every source our research notes (reports 01–31) and design notes cite. Licence notices of the code and data that satk ships are in [NOTICE.md](../../NOTICE.md); the short version of this page is in the [README](../../README.md#acknowledgements).

The **Reports** column gives the numbers of our internal research reports that cite the source ("design" means the design notes); these notes are not part of the repository. GitHub links point to repository roots. Thank you to every author listed here.

## Special thanks: Dryxio

[Dryxio](https://github.com/Dryxio) builds AI tools for reverse engineering and classic GTA modding. His projects are the foundation of a large part of satk and of our research: the viewer, the traffic-path core, the model descriptions, the opcode reference and the MTA fork we learned the engine from all come from his work. All his public GTA-related repositories:

| Repository | What it is | How satk uses it | Reports |
|---|---|---|---|
| [Dryxio/ariane](https://github.com/Dryxio/ariane) | A modern map editor for GTA III, Vice City and San Andreas (continues aap's euryopa) | satk's viewer (`satk view`) is a local fork of Ariane with an added SAAP/1 endpoint; studied in depth | 06, 08, 09, 11, 12, 14, 17, 23, 25, 29–31, design |
| [Dryxio/mtasa-neon](https://github.com/Dryxio/mtasa-neon) | Experimental MTA:SA engine fork: larger worlds, raised engine limits, new Lua capabilities (GPL-3.0) | studied in depth; donor of our MTA fork (cherry-picks), and `satk re`/`satk kb` read its patches and limits | 01–08, 10, 12–20, 22–28, design |
| [Dryxio/gta-scout](https://github.com/Dryxio/gta-scout) | Create GTA-style 3D models and scenes with your AI and Blender (MIT) | `satk describe import` imports its shared model-description pack into the local notes | 09, 11, 12, 14, 25, 29, 30 |
| [Dryxio/gta-flow](https://github.com/Dryxio/gta-flow) | Create and edit GTA San Andreas traffic routes with AI and Blender (MIT) | its pure-Python core is vendored unmodified in `vendor/gtaflow`; `satk paths` decodes and compiles NODES*.DAT with it | 09, 11, 12, 24, 25, 29, 30 |
| [Dryxio/plugin-sdk-sa](https://github.com/Dryxio/plugin-sdk-sa) | Plugin SDK for San Andreas, extended with layout and signature fixes from LLM-assisted reverse engineering (Zlib) | `satk kb` and `satk re` read its class layouts and addresses from a local clone | 06, 07, 10, 12, 13, 24, 27 |
| [Dryxio/cleo-ai](https://github.com/Dryxio/cleo-ai) | Create GTA San Andreas mods with your AI, using CLEO | `satk kb opcode` reads its opcode reference (rendered from the Sanny Builder Library) from a local clone | 07, 12, 25, 29 |
| [Dryxio/reagent](https://github.com/Dryxio/reagent) | Reconstruct and validate C/C++ code from compiled programs with AI (MIT) | studied when we designed `satk re`; that study led us to the function map of gta-reversed that `satk re` builds on | 06–08, 10, 12, 17, 25 |
| [Dryxio/ghidra-bridge](https://github.com/Dryxio/ghidra-bridge) | Give your AI access to Ghidra's program analysis (MIT) | its idea, an agent-facing CLI over data exported from Ghidra, shaped `satk re`, which also imports a Ghidra function export | 06, 09–12, 22, 24, 25, 27 |
| [Dryxio/gta-reversed](https://github.com/Dryxio/gta-reversed) | Fork of the community reimplementation of gta_sa.exe 1.0 US | compared with upstream gta-reversed, which `satk re` and `satk kb` read | 06, 07 |
| [Dryxio/samp-source](https://github.com/Dryxio/samp-source) | Rebuilding SA-MP 0.3.7 R5 from source, byte for byte, with AI-assisted reverse engineering | studied for the multiplayer and legal reports; no code is used | 06, 07, 12, 15, 23, 25, 26 |
| [Dryxio/samp-r5-rebuild](https://github.com/Dryxio/samp-r5-rebuild) | Evidence-driven from-scratch rebuild of the SA-MP 0.3.7-R5 client DLL | the predecessor of samp-source, reviewed together with it | 07 |
| [Dryxio/skygfx](https://github.com/Dryxio/skygfx) | Fork of aap's SkyGfx: PS2 graphics of San Andreas on PC | provides the `skygfx_mta.dll` bridge that Neon's CI builds; studied for the rendering report | 01, 02, 04, 05, 18 |
| [Dryxio/library](https://github.com/Dryxio/library) | Fork of the Sanny Builder Library: scripting documentation for Sanny Builder and CLEO Redux | the upstream library is the source of the opcode data behind `satk kb opcode` | — |
| [Dryxio/wiki.mtasa-neon.com](https://github.com/Dryxio/wiki.mtasa-neon.com) · [mtasa-neon-wiki.vercel.app](https://mtasa-neon-wiki.vercel.app/) | Data-driven documentation for MTA:SA Neon, based on the MTA wiki with full upstream history (GFDL-1.3) | read while studying Neon's API and network changes | 05 |
| [Dryxio/mtasa-blue](https://github.com/Dryxio/mtasa-blue) | Fork of Multi Theft Auto (GPL-3.0); Dryxio also sends fixes upstream | context for the Neon fork-health report | 05 |
| [Dryxio/fastman92_limit_adjuster](https://github.com/Dryxio/fastman92_limit_adjuster) | Fork of the fastman92 Limit Adjuster source | noted while reviewing Neon's limit patches | 02 |
| [Dryxio/GTA-GPS-Redux](https://github.com/Dryxio/GTA-GPS-Redux) | Fork of a complete GPS mod for Grand Theft Auto San Andreas | not used by satk | — |
| [Dryxio/Radar-in-style-GTA-SA-The-Definitive-Edition](https://github.com/Dryxio/Radar-in-style-GTA-SA-The-Definitive-Edition) | Fork of a Definitive Edition style 3D radar for classic San Andreas (MIT) | not used by satk | — |
| [Dryxio/dryxio](https://github.com/Dryxio/dryxio) | Profile repository: AI tools, reverse engineering and classic GTA projects | the overview of his projects we started from | 06 |
| [gtastuff.com](https://gtastuff.com/) · [ariane](https://gtastuff.com/ariane/) · [available-ids](https://gtastuff.com/tools/available-ids/) · [missing-textures](https://gtastuff.com/tools/missing-textures/) · [ifp-editor](https://gtastuff.com/tools/ifp-editor/) | GTA Stuff: Dryxio's 40+ browser tools for GTA modding (ID finder, missing-texture checker, RW validator, IFP, handling, COL, path and zone editors) and Ariane's home page | satk does not duplicate these GUI editors; `satk id free` and `satk asset lint` do similar checks from the command line | 08, 09, 12, 29, 30 |

His other public projects, not related to GTA: [DockDrop](https://github.com/Dryxio/DockDrop), [tamago-life](https://github.com/Dryxio/tamago-life), [openclaw-lullabully](https://github.com/Dryxio/openclaw-lullabully), [ppp-rescue](https://github.com/Dryxio/ppp-rescue).

## Used directly by satk

Code, data and rules that satk ships, reads or runs. Licence notices: [NOTICE.md](../../NOTICE.md).

| Component | Author | Licence | How satk uses it |
|---|---|---|---|
| [Hancapo/rwfury](https://github.com/Hancapo/rwfury) | Hancapo | MIT | vendored unmodified in `vendor/rwfury`: collision surface names for `satk col write`, an independent reader and the round-trip baseline in tests |
| [Dryxio/gta-flow](https://github.com/Dryxio/gta-flow) | Dryxio | MIT | pure-Python core vendored unmodified in `vendor/gtaflow` for `satk paths` |
| [Dryxio/gta-scout](https://github.com/Dryxio/gta-scout) | Dryxio | MIT | model-description pack imported by `satk describe import` (data only, no code) |
| [JuniorDjjr/CrashInfo](https://github.com/JuniorDjjr/CrashInfo) | Junior_Djjr | MIT | English crash list for GTA SA 1.0 US stored unchanged in `data/crashlist`, used by `satk crash analyze` |
| [thelink2012/modloader](https://github.com/thelink2012/modloader) | LINK/2012 | MIT, datalib BSL-1.0 | file handling, priority and data-file merge rules re-implemented in Python for `satk mod` |
| [gta-reversed/gta-reversed](https://github.com/gta-reversed/gta-reversed) | gta-reversed contributors | none | read from a local clone by `satk re` and `satk kb` (function map, source locations); fragments are only printed, never stored |
| [DK22Pac/plugin-sdk](https://github.com/DK22Pac/plugin-sdk) | DK22Pac and contributors | Zlib | class layouts, addresses and exe signatures for `satk kb`, `satk re` and `satk game` (through Dryxio's plugin-sdk-sa) |
| [multitheftauto/mtasa-blue](https://github.com/multitheftauto/mtasa-blue) | Multi Theft Auto team | GPL-3.0 | our engine fork (`satk engine`); `satk re patches` and `satk kb` index MTA's memory patches and engine layouts |
| [Dryxio/mtasa-neon](https://github.com/Dryxio/mtasa-neon) | Dryxio | GPL-3.0 | donor of features for our engine fork; `satk re` and `satk kb` index the files that differ from upstream |
| [sannybuilder/library](https://github.com/sannybuilder/library) | Sanny Builder team | none | opcode data behind `satk kb opcode` (through Dryxio's cleo-ai) |
| [NationalSecurityAgency/ghidra](https://github.com/NationalSecurityAgency/ghidra) | NSA | Apache-2.0 | a function list exported from Ghidra refines function bounds in the `satk re` symbol DB |
| [Dryxio/ariane](https://github.com/Dryxio/ariane) | Dryxio, aap | none (README: GPL) | the viewer of `satk view` is a local fork (not redistributed) |
| [aap/librw](https://github.com/aap/librw) · [Southland-FR/librw](https://github.com/Southland-FR/librw) | aap; Southland-FR | MIT | `satk rw` follows librw's stream layouts (no code copied); Ariane builds against the Southland-FR fork |
| [Parik27/DragonFF](https://github.com/Parik27/DragonFF) | Parik and contributors | GPL-3.0 | `satk blender` imports and exports DFF and COL through it inside Blender |
| [Blender](https://www.blender.org/) | Blender Foundation | GPL | headless import, render and export (`satk blender`); the add-on `blender/satk_blender` |
| [INU-ez/INU_Check-GTA](https://github.com/INU-ez/INU_Check-GTA) | INU | GPL-3.0 | optional external program for `satk mod check --engine inu`; never bundled or downloaded |
| [openmultiplayer/open.mp](https://github.com/openmultiplayer/open.mp) | open.mp team | MPL-2.0 | custom model ID ranges and the `artconfig` line grammar for `satk id` |
| [fastman92 Limit Adjuster](https://www.fastman92.com/fastman92-limit-adjuster/) · [Open Limit Adjuster](https://github.com/GTAmodding/III.VC.SA.LimitAdjuster) | fastman92; GTAmodding | closed; MIT | `satk id` reads a profile's limit adjuster ini to know the last valid model ID |
| [GTAMods wiki](https://gtamods.com/wiki/Main_Page) | GTAMods community | CC BY 4.0 | file format and engine descriptions behind `satk formats`, `satk index` and the curated facts of `satk kb` |
| [MTA build data](https://mirror-cdn.multitheftauto.com/bdata/DXFiles.zip) | Multi Theft Auto team | — | `satk engine setup` downloads the DirectX build files from MTA's mirror on request |
| [Python](https://www.python.org/) · [python-pillow/Pillow](https://github.com/python-pillow/Pillow) · [numpy/numpy](https://github.com/numpy/numpy) · [modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk) · [pytest-dev/pytest](https://github.com/pytest-dev/pytest) | PSF and project authors | PSF, MIT-CMU, BSD-3-Clause, MIT, MIT | the runtime and optional dependencies (exact versions in `requirements.lock`); the portable release embeds Python |

## Engine and multiplayer

Multi Theft Auto, SA-MP/open.mp and the netcode, physics and scripting projects we compared for the engine fork.

### Multi Theft Auto

| Source | Notes | Reports |
|---|---|---|
| [multitheftauto/mtasa-blue](https://github.com/multitheftauto/mtasa-blue) | MTA:SA (GPL-3.0): the base of our engine fork and of the MTA patch index in `satk re` | 05, 12, 15–18, 20, 21, design |
| [multitheftauto/mtasa-resources](https://github.com/multitheftauto/mtasa-resources) | Official resources: the map editor, EDF, object lists, runcode | 23, 26, 30 |
| [multitheftauto/wiki.multitheftauto.com](https://github.com/multitheftauto/wiki.multitheftauto.com) · [wiki.preview.multitheftauto.com](https://wiki.preview.multitheftauto.com/) | Source of the new YAML-based MTA wiki (GFDL-1.3) and its preview site | 12, 16, 20, 24 |
| [multitheftauto/luau](https://github.com/multitheftauto/luau) | MTA's fork of Luau | 16 |
| [multitheftauto/mta-mcp-server](https://github.com/multitheftauto/mta-mcp-server) | Official MTA MCP server (wiki and debug companion) | 26 |
| [multitheftauto/testing-tools](https://github.com/multitheftauto/testing-tools) | MTA test resources | 26 |
| [multitheftauto/mtasa-docs](https://github.com/multitheftauto/mtasa-docs/blob/main/mtasa-blue/CONTRIBUTING.md) | Contributor documentation of mtasa-blue (licensing of contributions) | 25 |
| [mirror-cdn.multitheftauto.com/bdata](https://mirror-cdn.multitheftauto.com/bdata/) · [DXFiles.zip](https://mirror-cdn.multitheftauto.com/bdata/DXFiles.zip) · [netc.dll](https://mirror-cdn.multitheftauto.com/bdata/netc.dll) · [fork-support/netc.dll](https://mirror-cdn.multitheftauto.com/bdata/fork-support/netc.dll) | MTA build data: the DirectX files `satk engine setup` downloads, and the closed network modules (including the build for forks) | 01, 03–05, 15, 25, 28 |
| [cef-builds.spotifycdn.com](https://cef-builds.spotifycdn.com/index.html) | Chromium Embedded Framework builds, a dependency of the MTA client build | 01, 28 |
| [luac.mtasa.com](https://luac.mtasa.com/) | MTA's Lua compiler and obfuscation service | 15 |
| [reflectrpteam/mtasa-blue](https://github.com/reflectrpteam/mtasa-blue) | MTA fork with a Luau branch (a proposed Luau runtime) | 16 |
| [Fernando-A-Rocha/mta-add-models](https://github.com/Fernando-A-Rocha/mta-add-models) | newmodels: server-side registry of custom models for MTA | 20, 23, 29 |
| [BlueEagle12/MTA-Eagle-Loader](https://github.com/BlueEagle12/MTA-Eagle-Loader) | Eagle Loader: custom map and model loader for MTA | 29 |
| [gptrk0/mtasa-map-images](https://github.com/gptrk0/mtasa-map-images) | Orthographic map renders made inside MTA | 12 |
| [hedit/hedit](https://github.com/hedit/hedit) | In-game handling editor for MTA | 30 |
| [eXo-OpenSource/ml_pathfind](https://github.com/eXo-OpenSource/ml_pathfind) | Pathfinding server module for MTA (MIT) | 16 |
| [acc-holo-dev/mta-sdk-module](https://github.com/acc-holo-dev/mta-sdk-module) | C++20 wrapper over the MTA server module ABI | 16 |
| [dmi7ry/Luau-definition-for-MTA](https://github.com/dmi7ry/Luau-definition-for-MTA) | MTA API definitions for luau-lsp | 16 |
| [mtasa-typescript/mtasa-lua-types](https://github.com/mtasa-typescript/mtasa-lua-types) | TypeScript types for MTA Lua scripting | 16 |
| [mta-slipe/Slipe-Server](https://github.com/mta-slipe/Slipe-Server) · [nuget.org](https://www.nuget.org/packages/SlipeServer.Server) | Slipe Server: MTA server in C# on top of the official network module | 15, 16 |
| [mta-slipe/Slipe-Core](https://github.com/mta-slipe/Slipe-Core) | Slipe Core: C# compiled to Lua for MTA resources | 16 |
| [adem-hosni/IronicMTA](https://github.com/adem-hosni/IronicMTA) | Python MTA server experiment on the official network module | 15 |
| [forum.multitheftauto.com](https://forum.multitheftauto.com/topic/138847-tut-3d-modelling-in-blender-mtasa/) | MTA forum tutorial: 3D modelling in Blender for MTA:SA | 29 |

**MTA wiki**: pages on engine functions, shaders, IDs, servers, forks and crashes (reports 12, 15, 16, 18–21, 23–26, 29, 30): [Main_Page](https://wiki.multitheftauto.com/wiki/Main_Page) · [EngineReplaceModel](https://wiki.multitheftauto.com/wiki/EngineReplaceModel) · [EngineApplyShaderToWorldTexture](https://wiki.multitheftauto.com/wiki/EngineApplyShaderToWorldTexture) · [EnginePreloadWorldArea](https://wiki.multitheftauto.com/wiki/EnginePreloadWorldArea) · [RemoveWorldModel](https://wiki.multitheftauto.com/wiki/RemoveWorldModel) · [SetElementRotation](https://wiki.multitheftauto.com/wiki/SetElementRotation) · [DxCreateShader](https://wiki.multitheftauto.com/wiki/DxCreateShader) · [DxGetTexturePixels](https://wiki.multitheftauto.com/wiki/DxGetTexturePixels) · [Shader](https://wiki.multitheftauto.com/wiki/Shader) · [Shader_examples](https://wiki.multitheftauto.com/wiki/Shader_examples) · [SetDynamicPedShadowsEnabled](https://wiki.multitheftauto.com/wiki/SetDynamicPedShadowsEnabled) · [Resource:Dynamic_lighting](https://wiki.multitheftauto.com/wiki/Resource:Dynamic_lighting) · [MTA:Eir](https://wiki.multitheftauto.com/wiki/MTA:Eir) · [TakePlayerScreenShot](https://wiki.multitheftauto.com/wiki/TakePlayerScreenShot) · [Resource_Web_Access](https://wiki.multitheftauto.com/wiki/Resource_Web_Access) · [GetPlayerSerial](https://wiki.multitheftauto.com/wiki/GetPlayerSerial) · [Modules](https://wiki.multitheftauto.com/wiki/Modules) · [Animations](https://wiki.multitheftauto.com/wiki/Animations) · [Character_Skins](https://wiki.multitheftauto.com/wiki/Character_Skins) · [Garage](https://wiki.multitheftauto.com/wiki/Garage) · [Interior_IDs](https://wiki.multitheftauto.com/wiki/Interior_IDs) · [Vehicle_IDs](https://wiki.multitheftauto.com/wiki/Vehicle_IDs) · [Weapons](https://wiki.multitheftauto.com/wiki/Weapons) · [Server_mtaserver.conf](https://wiki.multitheftauto.com/wiki/Server_mtaserver.conf) · [Sync_interval_settings](https://wiki.multitheftauto.com/wiki/Sync_interval_settings) · [User:Arran_Fortuna?diff=49038](https://wiki.multitheftauto.com/wiki/User:Arran_Fortuna?diff=49038) · [Anti-cheat_guide](https://wiki.multitheftauto.com/wiki/Anti-cheat_guide) · [Forks](https://wiki.multitheftauto.com/wiki/Forks) · [Forks_Full_AC](https://wiki.multitheftauto.com/wiki/Forks_Full_AC) · [Changes_in_1.6.1](https://wiki.multitheftauto.com/wiki/Changes_in_1.6.1) · [Changes_in_1.7](https://wiki.multitheftauto.com/wiki/Changes_in_1.7) · [Compiling_MTASA](https://wiki.multitheftauto.com/wiki/Compiling_MTASA) · [Reversing_GTA:SA_Code](https://wiki.multitheftauto.com/wiki/Reversing_GTA:SA_Code) · [Famous_crash_offsets_and_their_meaning](https://wiki.multitheftauto.com/wiki/Famous_crash_offsets_and_their_meaning) · [Optimize_Custom_TXD](https://wiki.multitheftauto.com/wiki/Optimize_Custom_TXD).

### SA-MP and open.mp

| Source | Notes | Reports |
|---|---|---|
| [openmultiplayer/open.mp](https://github.com/openmultiplayer/open.mp) | open.mp server (MPL-2.0); `satk id` follows its custom model ranges | 12, 23, 25, 26, 29, 31 |
| [openmultiplayer/web](https://github.com/openmultiplayer/web) | Source of the open.mp website and documentation | 12, 24, 25, 31 |
| [openmultiplayer/launcher](https://github.com/openmultiplayer/launcher) | open.mp launcher | 12, 31 |
| [openmultiplayer/open.mp-sdk](https://github.com/openmultiplayer/open.mp-sdk) · [openmultiplayer/omp-capi](https://github.com/openmultiplayer/omp-capi) · [openmultiplayer/omp-node](https://github.com/openmultiplayer/omp-node) | open.mp component SDK, C API and Node.js bindings | 16 |
| [openmultiplayer/RakNet](https://github.com/openmultiplayer/RakNet) | open.mp's modified RakNet 2.52 for SA-MP compatibility | 15 |
| [openmultiplayer/editor](https://github.com/openmultiplayer/editor) | Abandoned open.mp web map editor | 23 |
| [ikkentim/SampSharp](https://github.com/ikkentim/SampSharp) | C# scripting for SA-MP and open.mp | 16 |
| [Southclaws/sampctl](https://github.com/Southclaws/sampctl) | SA-MP package manager; its release practice (archives with checksums) informed ours | 31 |
| [BlastHackNet/SAMP-API](https://github.com/BlastHackNet/SAMP-API) | SA-MP client structures (MIT) | 12 |
| [Knogle/libsamp](https://github.com/Knogle/libsamp) | Libre-SAMP: a compatible open reconstruction of samp.dll (MIT) | 12 |
| [dashr9230/SA-MP](https://github.com/dashr9230/SA-MP) | SA-MP reconstruction without a licence; reviewed for the legal report, not used | 12, 25 |
| [sa-mp.mp](https://www.sa-mp.mp/) | SA-MP site, successor of sa-mp.com | 25 |
| [sampwiki.blast.hk](https://sampwiki.blast.hk/wiki/Main_Page) | Static mirror of the old SA-MP wiki | 24 |

**open.mp documentation**: pages on custom models, objects, NPCs and server config (reports 12, 21, 23, 26, 29): [AddSimpleModel](https://open.mp/docs/scripting/functions/AddSimpleModel) · [CreateObject](https://open.mp/docs/scripting/functions/CreateObject) · [RemoveBuildingForPlayer](https://open.mp/docs/scripting/functions/RemoveBuildingForPlayer) · [SetObjectMaterial](https://open.mp/docs/scripting/functions/SetObjectMaterial) · [SetObjectMaterialText](https://open.mp/docs/scripting/functions/SetObjectMaterialText) · [NPC_StartPlayback](https://open.mp/docs/scripting/functions/NPC_StartPlayback) · [config.json](https://www.open.mp/docs/server/config.json) · [server beta 9](https://open.mp/blog/server-beta-9) · [launcher](https://open.mp/downloads/launcher).

### Other multiplayer platforms

| Source | Notes | Reports |
|---|---|---|
| [citizenfx/fivem](https://github.com/citizenfx/fivem) · [docs.fivem.net](https://docs.fivem.net/docs/developers/script-runtimes/) | FiveM (CitizenFX): licence history and script runtime design | 16, 25 |
| [MafiaHub/Framework](https://github.com/MafiaHub/Framework) | MafiaHub multiplayer framework: networking, ECS and JS scripting | 16 |
| [Tornamic/CoopAndreas](https://github.com/Tornamic/CoopAndreas) | CoopAndreas: co-op story mode for San Andreas (GPL-3.0) | 12 |
| [gtaconnected](https://github.com/gtaconnected) · [gtaconnected.com](https://gtaconnected.com/) | GTA Connected multiplayer | 12 |

### Netcode and physics

| Source | Notes | Reports |
|---|---|---|
| [ValveSoftware/GameNetworkingSockets](https://github.com/ValveSoftware/GameNetworkingSockets) | Valve's networking library (BSD-3-Clause) | 15, 21 |
| [lsalzman/enet](https://github.com/lsalzman/enet) · [zpl-c/enet](https://github.com/zpl-c/enet) | ENet reliable UDP and its maintained fork | 15, 21 |
| [SLikeSoft/SLikeNet](https://github.com/SLikeSoft/SLikeNet) · [facebookarchive/RakNet](https://github.com/facebookarchive/RakNet) | RakNet 4 and its SLikeNet fork | 15, 21 |
| [Sandertv/go-raknet](https://github.com/Sandertv/go-raknet) · [CloudburstMC/Network](https://github.com/CloudburstMC/Network) · [NetrexMC/RakNet](https://github.com/NetrexMC/RakNet) | RakNet protocol implementations from the Minecraft Bedrock scene | 15 |
| [mas-bandwidth/yojimbo](https://github.com/mas-bandwidth/yojimbo) · [mas-bandwidth/netcode](https://github.com/mas-bandwidth/netcode) | Glenn Fiedler's yojimbo and netcode | 15, 21 |
| [skywind3000/kcp](https://github.com/skywind3000/kcp) · [microsoft/msquic](https://github.com/microsoft/msquic) | KCP and MsQuic transports | 15 |
| [pond3r/ggpo](https://github.com/pond3r/ggpo) | GGPO rollback netcode (MIT) | 21 |
| [bulletphysics/bullet3](https://github.com/bulletphysics/bullet3) · [jrouwe/JoltPhysics](https://github.com/jrouwe/JoltPhysics) · [ZealanL/RocketSim](https://github.com/ZealanL/RocketSim) · [RenderKit/embree](https://github.com/RenderKit/embree) | Physics and ray-casting libraries compared for server-side simulation | 21 |
| [State Synchronization](https://gafferongames.com/post/state_synchronization/) · [Networked Physics](https://gafferongames.com/categories/networked-physics/) | Gaffer On Games articles on state sync and networked physics | 21 |
| [Source Multiplayer Networking](https://developer.valvesoftware.com/wiki/Source_Multiplayer_Networking) · [Lag Compensation](https://developer.valvesoftware.com/wiki/Lag_Compensation) | Valve Developer Community on Source networking | 21 |
| [Overwatch netcode (GDC)](https://gdcvault.com/play/1024001/-Overwatch-Gameplay-Architecture-and) · [Rocket League at GDC 2018](https://www.rocketleague.com/news/rocket-league-at-gdc-2018) · [VALORANT netcode](https://technology.riotgames.com/node/111) · [Unreal Replication Graph](https://dev.epicgames.com/documentation/en-us/unreal-engine/replication-graph-in-unreal-engine) | Talks and articles on netcode architecture in shipped games | 21 |
| [OneSync](https://docs.fivem.net/docs/scripting-reference/onesync/) · [game events](https://docs.fivem.net/docs/cookbook/2019/08/19/onesync-intercepting-game-events-such-as-explosions/) · [forum.cfx.re](https://forum.cfx.re/t/possibly-unwanted-side-effects-of-cancelling-weapondamageevent/4850130) · [alt:V entity sync](https://docs.altv.mp/cs/articles/getting-started/entity-sync.html) | FiveM OneSync and alt:V entity sync documentation | 21 |
| [meshedinsights.com](https://meshedinsights.com/2017/07/16/apache-bans-facebooks-license-combo/) | Licence context of the BSD+Patents combination (RakNet) | 15 |

### Scripting runtimes

| Source | Notes | Reports |
|---|---|---|
| [lua/lua](https://github.com/lua/lua) · [Lua 5.5 readme](https://www.lua.org/manual/5.5/readme.html) | Lua and the Lua 5.5 release notes | 16 |
| [LuaJIT/LuaJIT](https://github.com/LuaJIT/LuaJIT) · [luajit.org](https://luajit.org/status.html) · [lua-users.org](https://lua-users.org/lists/lua-l/2011-06/msg00513.html) · [lists.tarantool.org](https://lists.tarantool.org/pipermail/tarantool-patches/2019-November/012735.html) | LuaJIT, its status page and mailing-list threads on hooks and the C API | 16 |
| [luau-lang/luau](https://github.com/luau-lang/luau) · [sandbox](https://luau.org/sandbox) · [compatibility](https://luau.org/compatibility) · [performance](https://luau.org/performance) | Luau and its sandbox, compatibility and performance notes | 16 |
| [JohnnyMorganz/luau-lsp](https://github.com/JohnnyMorganz/luau-lsp) · [LuaLS/lua-language-server](https://github.com/LuaLS/lua-language-server) · [teal-language/tl](https://github.com/teal-language/tl) · [TypeScriptToLua](https://typescripttolua.github.io/docs/configuration) | Language servers and typed languages that compile to Lua | 16 |
| [moonsharp-devs/moonsharp](https://github.com/moonsharp-devs/moonsharp) | MoonSharp: Lua interpreter for .NET (used by Slipe Server) | 16 |
| [quickjs-ng/quickjs](https://github.com/quickjs-ng/quickjs) · [bytecodealliance/wasm-micro-runtime](https://github.com/bytecodealliance/wasm-micro-runtime) · [wasm3/wasm3](https://github.com/wasm3/wasm3) | Embeddable JS and WebAssembly runtimes | 16 |
| [forum.cfx.re](https://forum.cfx.re/t/removal-of-lua-5-3-support/5335232) | FiveM's removal of Lua 5.3 support | 16 |
| [Space Station 14 sandboxing](https://docs.spacestation14.com/en/robust-toolbox/sandboxing.html) | Precedent of a sandbox for server-sent C# code | 16 |

### Open engines and reimplementations

| Source | Notes | Reports |
|---|---|---|
| [Avatarchik/opensa](https://github.com/Avatarchik/opensa) | OpenSA (AGPL-3.0), a browser engine compatible with RenderWare data; mirror of the original repository | 12, 17 |
| [rwengine/openrw](https://github.com/rwengine/openrw) | OpenRW: clean-room GTA III engine (GPL-3.0) | 17 |
| [in0finite/SanAndreasUnity](https://github.com/in0finite/SanAndreasUnity) | San Andreas Unity: a remake of the SA engine in Unity (MIT) | 17 |
| [flwmxd/librw-vulkan-RT](https://github.com/flwmxd/librw-vulkan-RT) | Experimental Vulkan ray-tracing backend for librw | 17 |
| [M-HT/SR](https://github.com/M-HT/SR) | Static recompilation of x86 executables | 17 |

## Reverse engineering

Reimplementations, SDKs, engine mods, analysis tools and the knowledge sources behind `satk re` and `satk kb`.

### Reimplementations, SDKs and engine mods

| Source | Notes | Reports |
|---|---|---|
| [gta-reversed/gta-reversed](https://github.com/gta-reversed/gta-reversed) · [gta-reversed.github.io](https://gta-reversed.github.io/gta-reversed/) | Reimplementation of gta_sa.exe 1.0 US; its function map and sources feed `satk re` and `satk kb` | 06, 12, 17, 27 |
| [DK22Pac/plugin-sdk](https://github.com/DK22Pac/plugin-sdk) | Plugin-SDK (Zlib): class layouts and addresses for ASI plugins | 07, 12, 24, 27 |
| [sannybuilder/library](https://github.com/sannybuilder/library) · [library.sannybuilder.com](https://library.sannybuilder.com/) | Sanny Builder Library: machine-readable opcode and native function database | 24 |
| [sannybuilder](https://github.com/sannybuilder) · [sannybuilder/dev](https://github.com/sannybuilder/dev) · [sannybuilder.com](https://sannybuilder.com/) · [forum 1703](https://sannybuilder.com/forums/viewtopic.php?id=1703) · [lessons](https://lessons.sannybuilder.com/00100/00500) | Sanny Builder: the SCM/CLEO compiler, its releases, forum and lessons on game memory | 24, 27, 29, 30 |
| [cleolibrary/CLEO5](https://github.com/cleolibrary/CLEO5) | CLEO 5 (MIT): script engine extensions | 12, 24, 29, 31 |
| [cleolibrary/CLEO-Redux](https://github.com/cleolibrary/CLEO-Redux) | CLEO Redux: JS/TS scripting (proprietary EULA) | 12 |
| [user-grinch/ImGuiRedux](https://github.com/user-grinch/ImGuiRedux) | ImGui bindings for CLEO 5 and CLEO Redux | 12 |
| [CookiePLMonster/SilentPatch](https://github.com/CookiePLMonster/SilentPatch) · [silentsblog.com](https://silentsblog.com/mods/gta-sa/) · [gtaforums.com](https://gtaforums.com/topic/669045-silentpatch/) | SilentPatch (MIT): fixes for SA; its patches are cross-checked in `satk kb` and its crash text is parsed by `satk crash` | 12, 24, 30 |
| [GTAmodding/III.VC.SA.LimitAdjuster](https://github.com/GTAmodding/III.VC.SA.LimitAdjuster) | Open Limit Adjuster (MIT) | 12, 24, 29, 30 |
| [fastman92/fastman92_limit_adjuster](https://github.com/fastman92/fastman92_limit_adjuster) · [fastman92.com](https://www.fastman92.com/fastman92-limit-adjuster/) | fastman92 Limit Adjuster: ID, map and streaming limits; `satk id` reads its ini | 24, 30 |
| [GTAmodding/FramerateVigilante](https://github.com/GTAmodding/FramerateVigilante) | FramerateVigilante (MIT): fixes of frame-rate dependent bugs | 24 |
| [thelink2012/modloader](https://github.com/thelink2012/modloader) | Mod Loader (MIT): its loading rules are ported into `satk mod` | 12, 24, 29–31 |
| [ThirteenAG/Ultimate-ASI-Loader](https://github.com/ThirteenAG/Ultimate-ASI-Loader) · [ThirteenAG/WidescreenFixesPack](https://github.com/ThirteenAG/WidescreenFixesPack) | Ultimate ASI Loader and Widescreen Fixes Pack (MIT) | 12 |
| [aap/debugmenu](https://github.com/aap/debugmenu) · [gta-chaos-mod/Trilogy-ASI-Script](https://github.com/gta-chaos-mod/Trilogy-ASI-Script) | aap's debug menu and the Chaos Mod: examples of large ASI mods | 12 |
| [JuniorDjjr/VehFuncs](https://github.com/JuniorDjjr/VehFuncs) · [user-grinch/ModelExtras](https://github.com/user-grinch/ModelExtras) · [mixmods.com.br](https://www.mixmods.com.br/2025/12/sa-vehfuncs/) | VehFuncs and ModelExtras: vehicle model features that `satk asset lint` must not flag | 29 |

### Analysis tools and AI bridges

| Source | Notes | Reports |
|---|---|---|
| [NationalSecurityAgency/ghidra](https://github.com/NationalSecurityAgency/ghidra) · [Headless Analyzer](https://ghidradocs.com/11.4_PUBLIC/help/Base/help/topics/HeadlessAnalyzer/HeadlessAnalyzer.htm) · [pyghidra](https://pypi.org/project/pyghidra/) | Ghidra (Apache-2.0), its headless analyzer and PyGhidra | 10, 12 |
| [clearbluejar/pyghidra-mcp](https://github.com/clearbluejar/pyghidra-mcp) · [pypi.org](https://pypi.org/project/pyghidra-mcp/) | pyghidra-mcp: MCP server for headless Ghidra | 10, 12 |
| [LaurieWired/GhidraMCP](https://github.com/LaurieWired/GhidraMCP) · [symgraph/GhidrAssistMCP](https://github.com/symgraph/GhidrAssistMCP) · [cyberkaida/reverse-engineering-assistant](https://github.com/cyberkaida/reverse-engineering-assistant) | GhidraMCP, GhidrAssistMCP and ReVa: MCP bridges to Ghidra | 12 |
| [mrexodia/ida-pro-mcp](https://github.com/mrexodia/ida-pro-mcp) · [ifarbod/renderware3-flirt](https://github.com/ifarbod/renderware3-flirt) | IDA Pro MCP and FLIRT signatures for RenderWare 3 | 12 |
| [x64dbg/x64dbg](https://github.com/x64dbg/x64dbg) · [x64dbg/x64dbgida](https://github.com/x64dbg/x64dbgida) | x64dbg debugger and its IDA database bridge | 19 |
| [Wasdubya/x64dbgMCP](https://github.com/Wasdubya/x64dbgMCP) · [AgentSmithers/x64DbgMCPServer](https://github.com/AgentSmithers/x64DbgMCPServer) · [dariushoule/x64dbg-automate](https://github.com/dariushoule/x64dbg-automate) · [x64dbg-automate MCP](https://dariushoule.github.io/x64dbg-automate-pyclient/mcp-server/) | MCP servers and automation for x64dbg | 12, 19 |
| [Cheat Engine](https://en.wikipedia.org/wiki/Cheat_Engine) · [miscusi-peek/cheatengine-mcp-bridge](https://github.com/miscusi-peek/cheatengine-mcp-bridge) · [bethington/cheat-engine-server-python](https://github.com/bethington/cheat-engine-server-python) | Cheat Engine and its MCP bridges (for memory inspection of our own test copy) | 12, 19 |
| [mq1n/GhostMCP](https://github.com/mq1n/GhostMCP) | GhostMCP: in-process MCP for live inspection | 12 |
| [frida/frida](https://github.com/frida/frida) · [dnakov/frida-mcp](https://github.com/dnakov/frida-mcp) | Frida instrumentation and its MCP server | 12, 19 |
| [Mixaill/FakePDB](https://github.com/Mixaill/FakePDB) | FakePDB: PDB files from IDA databases | 19 |

### Knowledge sources

| Source | Notes | Reports |
|---|---|---|
| [gtaforums.com](https://gtaforums.com/topic/194199-documenting-gta-sa-memory-addresses/) | GTAForums thread documenting SA memory addresses | 24 |
| [thread 20472](https://www.blast.hk/threads/20472/) · [thread 234670](https://www.blast.hk/threads/234670/) | BlastHack threads on SA memory structures and reverse engineering | 24 |
| [OLA and FLA](https://forum.mixmods.com.br/f10-ajuda-com-o-jogo/t4392-ola-e-fla-ao-mesmo-tempo) · [FLA](https://forum.mixmods.com.br/f5-scripts-codigos/t6180-fastman92-limit-adjuster) | MixMods forum threads on the limit adjusters | 24 |
| [TsyVM/SAEncyclopedia](https://github.com/TsyVM/SAEncyclopedia) | SAEncyclopedia: evaluated and rejected as a fact source (no licence, likely AI-generated) | 24 |

**GTAMods wiki (CC BY 4.0)**: file formats, data files, memory addresses, opcodes and tools; the main public reference behind `satk formats` and `satk kb` (reports 12, 24, 27, 29, 30).

- File formats: [RenderWare_binary_stream_file](https://gtamods.com/wiki/RenderWare_binary_stream_file) · [List_of_RW_section_IDs](https://gtamods.com/wiki/List_of_RW_section_IDs) · [2d_Effect_(RW_Section)](https://gtamods.com/wiki/2d_Effect_%28RW_Section%29) · [Breakable_(RW_Section)](https://gtamods.com/wiki/Breakable_%28RW_Section%29) · [Extra_Vert_Colour_(RW_Section)](https://gtamods.com/wiki/Extra_Vert_Colour_%28RW_Section%29) · [Night_Vertex_Colors_(RW_Section)](https://gtamods.com/wiki/Night_Vertex_Colors_%28RW_Section%29) · [HAnim_PLG_(RW_Section)](https://gtamods.com/wiki/HAnim_PLG_%28RW_Section%29) · [Material_Effects_PLG_(RW_Section)](https://gtamods.com/wiki/Material_Effects_PLG_%28RW_Section%29) · [Native_Data_PLG_(RW_Section)](https://gtamods.com/wiki/Native_Data_PLG_%28RW_Section%29) · [Raster_(RW_Section)](https://gtamods.com/wiki/Raster_%28RW_Section%29) · [Skin_PLG_(RW_Section)](https://gtamods.com/wiki/Skin_PLG_%28RW_Section%29) · [Collision_File](https://gtamods.com/wiki/Collision_File) · [IMG_archive](https://gtamods.com/wiki/IMG_archive) · [IFP](https://gtamods.com/wiki/IFP) · [GXT](https://gtamods.com/wiki/GXT) · [Script.img](https://gtamods.com/wiki/Script.img) · [Streamed_Script](https://gtamods.com/wiki/Streamed_Script) · [Item_Definition](https://gtamods.com/wiki/Item_Definition) · [OBJS](https://gtamods.com/wiki/OBJS) · [TOBJ](https://gtamods.com/wiki/TOBJ) · [ANIM](https://gtamods.com/wiki/ANIM) · [HIER](https://gtamods.com/wiki/HIER) · [CARS_(IDE_Section)](https://gtamods.com/wiki/CARS_%28IDE_Section%29) · [PEDS](https://gtamods.com/wiki/PEDS) · [WEAP](https://gtamods.com/wiki/WEAP) · [TXDP](https://gtamods.com/wiki/TXDP) · [2DFX](https://gtamods.com/wiki/2DFX) · [Item_Placement](https://gtamods.com/wiki/Item_Placement) · [INST](https://gtamods.com/wiki/INST) · [CARS_(IPL_Section)](https://gtamods.com/wiki/CARS_%28IPL_Section%29) · [CULL](https://gtamods.com/wiki/CULL) · [ENEX](https://gtamods.com/wiki/ENEX) · [GRGE](https://gtamods.com/wiki/GRGE) · [JUMP](https://gtamods.com/wiki/JUMP) · [OCCL](https://gtamods.com/wiki/OCCL) · [PICK](https://gtamods.com/wiki/PICK) · [TCYC](https://gtamods.com/wiki/TCYC) · [AUZO](https://gtamods.com/wiki/AUZO) · [ZONE](https://gtamods.com/wiki/ZONE) · [Paths_(GTA_SA)](https://gtamods.com/wiki/Paths_%28GTA_SA%29) · [Saves_(GTA_SA)](https://gtamods.com/wiki/Saves_%28GTA_SA%29) · [Replays_(GTA_SA)](https://gtamods.com/wiki/Replays_%28GTA_SA%29) · [User_Files](https://gtamods.com/wiki/User_Files) · [Audio_stream](https://gtamods.com/wiki/Audio_stream) · [SFX_(SA)](https://gtamods.com/wiki/SFX_%28SA%29)
- Data files: [Gta.dat](https://gtamods.com/wiki/Gta.dat) · [Animgrp.dat](https://gtamods.com/wiki/Animgrp.dat) · [Carcols.dat](https://gtamods.com/wiki/Carcols.dat) · [Carmods.dat](https://gtamods.com/wiki/Carmods.dat) · [Handling.cfg](https://gtamods.com/wiki/Handling.cfg) · [Object.dat](https://gtamods.com/wiki/Object.dat) · [Pedstats.dat](https://gtamods.com/wiki/Pedstats.dat) · [Surfaud.dat](https://gtamods.com/wiki/Surfaud.dat) · [Water.dat](https://gtamods.com/wiki/Water.dat) · [Weapon.dat](https://gtamods.com/wiki/Weapon.dat) · [Time_cycle](https://gtamods.com/wiki/Time_cycle) · [Gta_sa.set](https://gtamods.com/wiki/Gta_sa.set) · [Decision_Maker](https://gtamods.com/wiki/Decision_Maker) · [Ped_type](https://gtamods.com/wiki/Ped_type) · [Particle_(SA)](https://gtamods.com/wiki/Particle_%28SA%29) · [List_of_particle_effects](https://gtamods.com/wiki/List_of_particle_effects)
- Engine and memory: [Memory_Addresses_(SA)](https://gtamods.com/wiki/Memory_Addresses_%28SA%29) · [Function_Memory_Addresses_(SA)](https://gtamods.com/wiki/Function_Memory_Addresses_%28SA%29) · [Object_pool](https://gtamods.com/wiki/Object_pool) · [VTable](https://gtamods.com/wiki/VTable) · [Hardcoded](https://gtamods.com/wiki/Hardcoded) · [Resource_Streaming](https://gtamods.com/wiki/Resource_Streaming) · [Map_system](https://gtamods.com/wiki/Map_system) · [LOD](https://gtamods.com/wiki/LOD) · [Ped_Bones](https://gtamods.com/wiki/Ped_Bones) · [Ped_Event](https://gtamods.com/wiki/Ped_Event) · [Task_IDs_(GTA_SA)](https://gtamods.com/wiki/Task_IDs_%28GTA_SA%29) · [Blip_Sprite_IDs](https://gtamods.com/wiki/Blip_Sprite_IDs) · [List_of_vehicles_(SA)](https://gtamods.com/wiki/List_of_vehicles_%28SA%29) · [Weapon](https://gtamods.com/wiki/Weapon) · [Game_directory_(SA)](https://gtamods.com/wiki/Game_directory_%28SA%29) · [San_Andreas_Versions](https://gtamods.com/wiki/San_Andreas_Versions) · [Referring_to_GTA_Versions](https://gtamods.com/wiki/Referring_to_GTA_Versions) · [Game_Patches](https://gtamods.com/wiki/Game_Patches) · [SA_Limit_Adjuster](https://gtamods.com/wiki/SA_Limit_Adjuster)
- Scripting: [SA_SCM](https://gtamods.com/wiki/SA_SCM) · [SCM_Instruction](https://gtamods.com/wiki/SCM_Instruction) · [List_of_opcodes](https://gtamods.com/wiki/List_of_opcodes) · [CLEO](https://gtamods.com/wiki/CLEO)
- Tools: [IMG_Tool](https://gtamods.com/wiki/IMG_Tool) · [MEd](https://gtamods.com/wiki/MEd) · [Magic.TXD](https://gtamods.com/wiki/Magic.TXD) · [TXD_Workshop](https://gtamods.com/wiki/TXD_Workshop) · [Sanny_Builder](https://gtamods.com/wiki/Sanny_Builder) · [Collision_File_Editor_II](https://gtamods.com/wiki/Collision_File_Editor_II) · [Mod_Loader](https://gtamods.com/wiki/Mod_Loader) · [Main_Page](https://gtamods.com/wiki/Main_Page)

## Crash analysis, debugging and profiling

Sources for `satk crash` and for debugging the game and the engine fork.

### Crash knowledge

| Source | Notes | Reports |
|---|---|---|
| [JuniorDjjr/CrashInfo](https://github.com/JuniorDjjr/CrashInfo) · [mixmods 2021](https://www.mixmods.com.br/2021/08/crashinfo/) · [mixmods 2022](https://www.mixmods.com.br/2022/09/crashinfo/) | CrashInfo (MIT): its crash list ships in `data/crashlist` for `satk crash analyze` | 24, 29 |
| [mods that cause crashes](https://gtaforums.com/topic/902062-list-mods-that-cause-crashes/) · [HD texture packs LOD crash fix](https://gtaforums.com/topic/927411-lots-of-hd-texture-packs-unload-lod-crash-fix/) | GTAForums threads on crash-prone mods and the LOD crash of HD texture packs | 29 |

### Debuggers and symbols

| Source | Notes | Reports |
|---|---|---|
| [WinDbg](https://aka.ms/windbg/download) · [TTD](https://aka.ms/ttd/download) · [TTD overview](https://learn.microsoft.com/en-us/windows-hardware/drivers/debuggercmds/time-travel-debugging-overview) · [TTD command line](https://learn.microsoft.com/en-us/windows-hardware/drivers/debuggercmds/time-travel-debugging-ttd-exe-command-line-util) | WinDbg and Time Travel Debugging | 19 |
| [TimMisiak/windup](https://github.com/TimMisiak/windup) · [SymBuilder](https://github.com/microsoft/WinDbg-Samples/tree/master/TargetComposition/SymBuilder) · [AddSyntheticSymbol](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/dbgeng/nf-dbgeng-idebugsymbols3-addsyntheticsymbol) | WinDbg installer helper and synthetic symbols for a binary without a PDB | 19 |
| [svnscha/mcp-windbg](https://github.com/svnscha/mcp-windbg) · [docs](https://svnscha.github.io/mcp-windbg/) · [glslang/windbg-mcp](https://github.com/glslang/windbg-mcp) | MCP servers for WinDbg | 19 |
| [msdl.microsoft.com](https://msdl.microsoft.com/download/symbols) | Microsoft public symbol server | 19 |
| [dshikashio/Pybag](https://github.com/dshikashio/Pybag) · [skelsec/minidump](https://github.com/skelsec/minidump) | Python access to DbgEng and minidump parsing | 19 |
| [ProcDump](https://learn.microsoft.com/en-us/sysinternals/downloads/procdump) · [Process Monitor](https://learn.microsoft.com/en-us/sysinternals/downloads/procmon) · [VMMap](https://learn.microsoft.com/en-us/sysinternals/downloads/vmmap) · [DebugView](https://learn.microsoft.com/en-us/sysinternals/downloads/debugview) | Sysinternals tools for dumps, file activity, memory and debug output | 19 |
| [AddressSanitizer](https://learn.microsoft.com/en-us/cpp/sanitizers/asan) · [DynamoRIO/drmemory](https://github.com/DynamoRIO/drmemory) | MSVC AddressSanitizer and Dr. Memory | 19 |
| [devblogs.microsoft.com](https://devblogs.microsoft.com/oldnewthing/20201209-00/?p=104530) | The Old New Thing on wpaexporter: exporting Windows Performance Analyzer data | 19 |
| [lldb MCP](https://lldb.llvm.org/use/mcp.html) | LLDB's built-in MCP server | 19 |

### Graphics debugging and profiling

| Source | Notes | Reports |
|---|---|---|
| [baldurk/renderdoc](https://github.com/baldurk/renderdoc) · [FAQ](https://renderdoc.org/docs/getting_started/faq.html) · [JiaboLi-GitHub/renderdoc-mcp](https://github.com/JiaboLi-GitHub/renderdoc-mcp) | RenderDoc (no D3D9 support, hence through DXVK) and its MCP server | 12, 18, 19 |
| [apitrace/apitrace](https://github.com/apitrace/apitrace) | apitrace: D3D9 call tracing and replay | 12, 19 |
| [PIX GPU captures](https://learn.microsoft.com/en-us/windows/win32/direct3dtools/pix/articles/gpu-captures/pix-gpu-captures) · [PIX and D3D11on12](https://devblogs.microsoft.com/pix/debugging-d3d11-apps-using-d3d11on12/) | PIX GPU captures (D3D12, and D3D11 through D3D11on12) | 19 |
| [wolfpld/tracy](https://github.com/wolfpld/tracy) · [GameTechDev/PresentMon](https://github.com/GameTechDev/PresentMon) | Tracy profiler and PresentMon frame timing | 19 |
| [VTune](https://intel.com/content/www/us/en/developer/articles/system-requirements/vtune-profiler/2025-1.html) · [Superluminal](https://www.superluminal.eu/docs/documentation.html) | Intel VTune and Superluminal profilers | 19 |

## File formats and libraries

Format descriptions and parsers we cross-checked `satk formats` and `satk rw` against.

| Source | Notes | Reports |
|---|---|---|
| [Hancapo/rwfury](https://github.com/Hancapo/rwfury) · [pypi.org](https://pypi.org/project/rwfury/) | rwfury (MIT): pure-Python RenderWare library, vendored for tests and collision surface names | 12, 14, 30 |
| [aap/librw](https://github.com/aap/librw) | librw (MIT): reimplementation of RenderWare; the reference for our writers | 12, 17, 24, 30 |
| [aap/librwgta](https://github.com/aap/librwgta) | librwgta: aap's GTA tools on librw, including euryopa, the base of Ariane | 12, 17, 30 |
| [Southland-FR/librw](https://github.com/Southland-FR/librw) | librw fork that Ariane builds against | 08 |
| [formats.kaitai.io](https://formats.kaitai.io/renderware_binary_stream/) | Kaitai Struct specification of the RenderWare binary stream (used as a test oracle) | 12, 14 |
| [Timic3/rw-parser](https://github.com/Timic3/rw-parser) · [rw-parser-ng](https://www.npmjs.com/package/rw-parser-ng) | rw-parser and rw-parser-ng: DFF/TXD/IFP parsers in TypeScript (GPL-3.0) | 12, 14 |
| [gta-img](https://docs.rs/gta-img) · [crates.io](https://crates.io/) | Rust crates for IMG archives and RenderWare (gta-img, rw-parser-rs, libtxd) | 12, 14 |
| [jackal1337/DFF-Loader](https://github.com/jackal1337/DFF-Loader) | DFF and TXD loader for three.js (MIT) | 12 |
| [iroxacu666/RWGuard](https://github.com/iroxacu666/RWGuard) | Scanner of malformed DFF/TXD files for SA-MP; idea for our chunk validation | 23 |
| [pillow.readthedocs.io](https://pillow.readthedocs.io/en/stable/handbook/image-file-formats.html) | Pillow image formats (DDS/DXT decoding) | 14 |

## Modding tools: viewers, map and asset editors

The tool landscape we surveyed before deciding what satk should and should not do.

### Viewers and map editors

| Source | Notes | Reports |
|---|---|---|
| [user-grinch/Neuryopa](https://github.com/user-grinch/Neuryopa) | Neuryopa: another euryopa-based viewer (GPL-3.0) | 12 |
| [ikkentim/SanMap](https://github.com/ikkentim/SanMap) | SanMap: map projection for Google Maps | 12 |
| [gta-modding.com](https://www.gta-modding.com/san_andreas/tutorials/create_map_med.html) | Tutorial on creating a map with MEd | 29 |

### IMG, TXD and model tools

| Source | Notes | Reports |
|---|---|---|
| [Bios-Marcel/IMG-Console](https://github.com/Bios-Marcel/IMG-Console) · [gtaforums.com](https://gtaforums.com/topic/524409-fastman92-img-console/) | fastman92 IMG Console | 30 |
| [MexUK/IMGF](https://github.com/MexUK/IMGF) · [X-Seti/Img-Factory-1.6](https://github.com/X-Seti/Img-Factory-1.6) · [ndenissov/UniversalIMG](https://github.com/ndenissov/UniversalIMG) · [vaibhavpandeyvpz/gtaimg](https://github.com/vaibhavpandeyvpz/gtaimg) · [Alci's IMG Editor](https://www.gta-modding.com/area/file-3-alcis-img-editor.html) | IMG archive editors | 12, 30 |
| [vaibhavpandeyvpz/txdedit](https://github.com/vaibhavpandeyvpz/txdedit) · [X-Seti/Txd-Workshop](https://github.com/X-Seti/Txd-Workshop) · [FrannDzs/Magic.TXD](https://github.com/FrannDzs/Magic.TXD) · [TXD Studio](https://libertycity.net/files/gta-san-andreas/236727-txd-studio.html) · [Modern TXD Editor](https://libertycity.net/files/gta-san-andreas/238278-modern-txd-editor-v0-2806.html) | TXD editors (Magic.TXD mirror by DK22Pac and The_GTA) | 12, 29, 30 |
| [X-Seti/Model-Workshop](https://github.com/X-Seti/Model-Workshop) · [zmodeler3.com](https://www.zmodeler3.com/) · [steve-m.com](http://steve-m.com/downloads/tools/) | Model tools: Model Workshop, ZModeler 3 and Steve-M's classic tools | 30 |
| [Kam's GTA Scripts](https://gtaforums.com/topic/907323-rel-kams-gta-scripts-2018-upd-08092024/) · [scripts list](https://www.geocities.ws/kam_b_lai/GTA/scriptslist.htm) · [KAM's GTA Tools](https://libertycity.net/files/gta-san-andreas/223453-gta-tools.html) · [vincedark56/Map_Tools_for_Kam_GTA_Scripts](https://github.com/vincedark56/Map_Tools_for_Kam_GTA_Scripts) | Kam's 3ds Max scripts and map tools built on them | 11, 30 |
| [MadGamerHD/2DFX-Tool](https://github.com/MadGamerHD/2DFX-Tool) · [2DFX tutorial](https://libertycity.net/files/gta-san-andreas/64366-creating-2dfx-with-2dfx-tool.html) | 2DFX tool for lights and effects in DFF files | 30 |
| [X-Seti/Radar-Workshop](https://github.com/X-Seti/Radar-Workshop) · [Ghady983/GTA-SA-Radar-Map-Mod-Maker](https://github.com/Ghady983/GTA-SA-Radar-Map-Mod-Maker) | Radar tile generators | 29, 30 |
| [MadGamerHD/GTA-SA-Path-Nodes-Editor](https://github.com/MadGamerHD/GTA-SA-Path-Nodes-Editor) | Path node editor for Blender (MIT) | 29 |
| [KTKTDEV/sa-anim-export](https://github.com/KTKTDEV/sa-anim-export) · [IFP Editor](https://libertycity.net/files/resources/175492-ifp-editor.html) · [AutoRiga](https://libertycity.net/files/gta-san-andreas/243096-autoriga.html) | Animation tools: export, IFP editing and automatic rigging | 29, 30 |
| [user-grinch/Carcols_Editor](https://github.com/user-grinch/Carcols_Editor) | carcols.dat editor | 30 |
| [INU-ez/INU_Check-GTA](https://github.com/INU-ez/INU_Check-GTA) · [libertycity.net](https://libertycity.net/files/243300-inu-check-v1-0-universal-game-launch.html) | INU Check (GPL-3.0): offline pre-launch check; optional engine of `satk mod check` | 29 |
| [DK22Pac/v2saconv](https://github.com/DK22Pac/v2saconv) | Converter of GTA V models to SA formats (technique studied; not for Rockstar assets) | 23 |

## Blender

The Blender side of satk and the add-ons we compared.

| Source | Notes | Reports |
|---|---|---|
| [Parik27/DragonFF](https://github.com/Parik27/DragonFF) · [extensions.blender.org](https://extensions.blender.org/add-ons/dragonff/) | DragonFF (GPL-3.0): DFF/COL import and export used by `satk blender` | 11, 12, 14, 30, 31 |
| [INU-ez/INU_Tools-GTA-Blender](https://github.com/INU-ez/INU_Tools-GTA-Blender) · [extensions.blender.org](https://extensions.blender.org/add-ons/inu-tools-gta-sa/) · [versions](https://extensions.blender.org/add-ons/inu-tools-gta-sa/versions/) · [blenderartists.org](https://blenderartists.org/t/inu-tools-1-7-full-gta-san-andreas-modding-pipeline-in-blender-dff-col-txd-ide-ipl-img-ifp/1639525) · [libertycity.net](https://libertycity.net/files/gta-san-andreas/237474-inu-tools-2.3.2-complemento-de-blender-para-modding.html) | INU Tools (GPL-3.0): full SA modding pipeline in Blender | 11, 12, 29–31 |
| [INU-ez/INU_Core_GTA](https://github.com/INU-ez/INU_Core_GTA) | INU Core (GPL-3.0): the format core of INU Tools in Python and numpy | 11, 30 |
| [Psycrow101/io_scene_gta_ifp](https://github.com/Psycrow101/io_scene_gta_ifp) | IFP animation import and export for Blender | 11, 29, 30 |
| [spicybung/DemonFF](https://github.com/spicybung/DemonFF) | DemonFF (MIT): DragonFF variant with SA-MP map export | 12 |
| [SA Map Tools for Blender](https://libertycity.net/files/gta-san-andreas/224787-sa-map-tools-for-blender.html) | Map tools add-on for Blender | 11, 29 |
| [ahujasid/blender-mcp](https://github.com/ahujasid/blender-mcp) · [ahujasid/mcp-for-blender](https://github.com/ahujasid/mcp-for-blender) | MCP servers that drive Blender | 11, 12, 22 |
| [licence](https://www.blender.org/about/license/) · [Blender 5.2](https://www.blender.org/releases/5-2/) · [add-on docs](https://docs.blender.org/manual/en/latest/advanced/extensions/addons.html) · [add-on guidelines](https://developer.blender.org/docs/handbook/extensions/addon_guidelines/) | Blender licence, release notes and extension rules (for our GPL add-on) | 11, 25, 31 |

## Rendering and graphics

Graphics mods and renderers studied for the engine fork.

| Source | Notes | Reports |
|---|---|---|
| [aap/skygfx](https://github.com/aap/skygfx) · [aap/skygfx_vc](https://github.com/aap/skygfx_vc) | SkyGfx: PS2/Xbox look for SA, III and VC (aap) | 12, 18 |
| [gta191977649/MTASA-SkyGfx](https://github.com/gta191977649/MTASA-SkyGfx) · [RAZORRZR0/SkyGfxDE](https://github.com/RAZORRZR0/SkyGfxDE) | SkyGfx ports: an MTA resource and a Definitive Edition filter | 18 |
| [ThirteenAG/III.VC.SA.IV.Project2DFX](https://github.com/ThirteenAG/III.VC.SA.IV.Project2DFX) · [ThirteenAG/XboxRainDroplets](https://github.com/ThirteenAG/XboxRainDroplets) | Project2DFX (LOD lights, draw distance) and Xbox rain droplets (MIT) | 12, 18, 30 |
| [GTAmodding/timecycle24](https://github.com/GTAmodding/timecycle24) | 24-hour timecycle | 18 |
| [ep1h/gta-sa-postfx](https://github.com/ep1h/gta-sa-postfx) · [petrgeorgievsky/gtaRenderHook](https://github.com/petrgeorgievsky/gtaRenderHook) · [crosire/reshade](https://github.com/crosire/reshade) | Post-processing and render hooks: gta-sa-postfx, gtaRenderHook, ReShade | 12, 18 |
| [doitsujin/dxvk](https://github.com/doitsujin/dxvk) · [microsoft/D3D9On12](https://github.com/microsoft/D3D9On12) · [DirectX-Specs](https://github.com/microsoft/DirectX-Specs/blob/master/d3d/TranslationLayerResourceInterop.md) | D3D9 translation layers: DXVK (Vulkan) and D3D9On12 | 12, 18, 19, 26 |
| [NVIDIAGameWorks/rtx-remix](https://github.com/NVIDIAGameWorks/rtx-remix) · [NVIDIAGameWorks/dxvk-remix](https://github.com/NVIDIAGameWorks/dxvk-remix) · [NVIDIAGameWorks/bridge-remix](https://github.com/NVIDIAGameWorks/bridge-remix) · [Remix FAQ](https://docs.omniverse.nvidia.com/kit/docs/rtx_remix/1.4.0-0/docs/remix-faq.html) | NVIDIA RTX Remix | 18 |
| [Hemry81/GTASA-Remix](https://github.com/Hemry81/GTASA-Remix) · [DSOGaming](https://dsogaming.com/?p=172314) | RTX Remix setup for San Andreas and its performance | 18 |
| [MixMods](https://www.mixmods.com.br/2026/10/sa-proper-shaders/) · [DSOGaming](https://www.dsogaming.com/?p=194337) · [GTA BOOM](https://www.gtaboom.com/gta-san-andreas-proper-shaders-deferred-rendering-mod) | Proper Shaders: deferred rendering mod by Junior_Djjr | 18 |
| [DSOGaming](https://www.dsogaming.com/news/sa_directx-2-0-mod-makes-grand-theft-auto-san-andreas-look-almost-as-good-as-modern-day-video-games) · [Direct Render v4.0](https://libertycity.net/files/gta-san-andreas/191434-direct-render-v4.0.html) | ENB-based graphics mods (SA_DirectX 2.0, Direct Render) | 18 |

## AI content pipeline

3D generation, mesh processing, texture generation and upscaling studied for the AI content report.

### 3D generation and mesh processing

| Source | Notes | Reports |
|---|---|---|
| [microsoft/TRELLIS](https://github.com/microsoft/TRELLIS) · [microsoft/TRELLIS.2](https://github.com/microsoft/TRELLIS.2) · [PozzettiAndrea/ComfyUI-TRELLIS2](https://github.com/PozzettiAndrea/ComfyUI-TRELLIS2) | TRELLIS and TRELLIS.2 image-to-3D, with a ComfyUI node | 22 |
| [Tencent-Hunyuan/Hunyuan3D-2](https://github.com/Tencent-Hunyuan/Hunyuan3D-2) · [Tencent-Hunyuan/Hunyuan3D-2.1](https://github.com/Tencent-Hunyuan/Hunyuan3D-2.1) · [ComfyUI docs](https://docs.comfy.org/tutorials/3d/hunyuan3D-2) · [PolyGen 1.5](https://www.scenario.com/models/hunyuan-polygen-15) | Hunyuan3D models and the closed PolyGen | 22 |
| [TencentARC/Pixal3D](https://github.com/TencentARC/Pixal3D) · [VAST-AI-Research/TripoSG](https://github.com/VAST-AI-Research/TripoSG) · [VAST-AI-Research/TripoSR](https://github.com/VAST-AI-Research/TripoSR) · [Stability-AI/stable-fast-3d](https://github.com/Stability-AI/stable-fast-3d) · [stepfun-ai/Step1X-3D](https://github.com/stepfun-ai/Step1X-3D) · [DreamTechAI/Direct3D-S2](https://github.com/DreamTechAI/Direct3D-S2) · [facebookresearch/sam-3d-objects](https://github.com/facebookresearch/sam-3d-objects) | Other open image-to-3D models | 22 |
| [wgsxm/PartCrafter](https://github.com/wgsxm/PartCrafter) · [buaacyw/MeshAnythingV2](https://github.com/buaacyw/MeshAnythingV2) · [zhaorw02/DeepMesh](https://github.com/zhaorw02/DeepMesh) | Part-based and low-poly mesh generation | 22 |
| [VAST-AI-Research/UniRig](https://github.com/VAST-AI-Research/UniRig) · [VAST-AI-Research/SkinTokens](https://github.com/VAST-AI-Research/SkinTokens) | Automatic rigging | 22 |
| [EricWang12/PartUV](https://github.com/EricWang12/PartUV) · [jpcy/xatlas](https://github.com/jpcy/xatlas) · [zeux/meshoptimizer](https://github.com/zeux/meshoptimizer) · [pyvista/fast-simplification](https://github.com/pyvista/fast-simplification) · [cnr-isti-vclab/PyMeshLab](https://github.com/cnr-isti-vclab/PyMeshLab) | UV unwrapping and mesh simplification | 22 |
| [SarahWeiii/CoACD](https://github.com/SarahWeiii/CoACD) · [kmammou/v-hacd](https://github.com/kmammou/v-hacd) | Convex decomposition for collisions | 22 |
| [NVlabs/nvdiffrast](https://github.com/NVlabs/nvdiffrast) | Differentiable rasterizer used by several 3D models | 22 |
| [Tripo](https://developers.tripo3d.com/en/docs/mesh-decimate.md) · [Meshy](https://www.meshy.ai/features/low-poly) | Commercial low-poly and retopology APIs | 22 |

### Textures and upscaling

| Source | Notes | Reports |
|---|---|---|
| [Comfy-Org/ComfyUI](https://github.com/Comfy-Org/ComfyUI) · [Comfy-Org/comfy-mcp](https://github.com/Comfy-Org/comfy-mcp) · [NVIDIAGameWorks/ComfyUI-RTX-Remix](https://github.com/NVIDIAGameWorks/ComfyUI-RTX-Remix) | ComfyUI, its MCP server and the RTX Remix nodes | 22 |
| [OliverCrosby/ComfyUI-Universal-Seamless-Tiles](https://github.com/OliverCrosby/ComfyUI-Universal-Seamless-Tiles) · [spinagon/ComfyUI-seamless-tiling](https://github.com/spinagon/ComfyUI-seamless-tiling) | Seamless tiling for generated textures | 22 |
| [sakalond/StableGen](https://github.com/sakalond/StableGen) · [carson-katri/dream-textures](https://github.com/carson-katri/dream-textures) | Texturing inside Blender with diffusion models | 22 |
| [QwenLM/Qwen-Image](https://github.com/QwenLM/Qwen-Image) · [Tongyi-MAI/Z-Image](https://github.com/Tongyi-MAI/Z-Image) · [FLUX.2 klein](https://bfl.ai/blog/flux2-klein-towards-interactive-visual-intelligence) · [FLUX.1 licence](https://scancode-licensedb.aboutcode.org/flux-1-nc.html) | Open image models and their licences | 22 |
| [ostris/ai-toolkit](https://github.com/ostris/ai-toolkit) · [kohya-ss/sd-scripts](https://github.com/kohya-ss/sd-scripts) | LoRA training toolkits | 22 |
| [xinntao/Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN) · [chaiNNer-org/spandrel](https://github.com/chaiNNer-org/spandrel) · [chaiNNer-org/chaiNNer](https://github.com/chaiNNer-org/chaiNNer) · [upscayl/upscayl](https://github.com/upscayl/upscayl) | Upscalers and model loaders | 22 |
| [Kim2091/PBRify_Remix](https://github.com/Kim2091/PBRify_Remix) | PBRify: CC0-trained texture upscaler and PBR maps | 22 |
| [GPUOpen-Tools/compressonator](https://github.com/GPUOpen-Tools/compressonator) · [texconv](https://github.com/microsoft/DirectXTex/wiki/Texconv) | BCn/DXT encoders to compare with ours | 22 |
| [SigLIP2](https://huggingface.co/google/siglip2-so400m-patch14-384) · [DINOv3](https://huggingface.co/facebook/dinov3-vitl16-pretrain-lvd1689m) | Vision encoders for image search and conditioning | 22 |

**OpenModelDB**: upscaler models compared for game textures (report 22): [4x-UltraSharp](https://openmodeldb.info/models/4x-UltraSharp) · [4x-RealisticRescaler](https://openmodeldb.info/models/4x-RealisticRescaler) · [4x-PBRify-UpscalerV4](https://openmodeldb.info/models/4x-PBRify-UpscalerV4) · [4x-Textures-GTAV-rgt-s](https://openmodeldb.info/models/4x-Textures-GTAV-rgt-s) · [1x-DXTDecompressor-Source-V3](https://openmodeldb.info/models/1x-DXTDecompressor-Source-V3) · [1x-SpongeBC1-Lite](https://openmodeldb.info/models/1x-SpongeBC1-Lite).

### Hardware, cloud and context

| Source | Notes | Reports |
|---|---|---|
| [discuss.pytorch.org](https://discuss.pytorch.org/t/pytorch-support-for-sm120/216099) · [RunPod](https://www.runpod.io/gpu-cloud/pricing) · [fal.ai](https://fal.ai/) | PyTorch support for new GPUs and cloud GPU pricing | 22 |
| [cinevva](https://app.cinevva.com/guides/ai-3d-model-generation-timeline-2026) · [arXiv 2608.26238](https://arxiv.org/abs/2608.26238) | Timeline of AI 3D releases and a paper on LLM-driven assembly | 22 |
| [flyaway888 AI project](https://dsogaming.com/?p=155729) · [AI-enhanced HD texture pack](https://www.dsogaming.com/news/grand-theft-auto-san-andreas-gets-a-4-6gb-ai-enhanced-hd-texture-pack/) · [Definitive Edition](https://www.pcgamer.com/uk/gta-trilogy-definitive-edition-is-a-mess/) | News on AI upscaling in the GTA community and the lessons of the Definitive Edition | 22 |

## AI agents, MCP and game testing

How AI clients connect to MCP servers, and prior art on agents that test games.

### MCP clients and packaging

| Source | Notes | Reports |
|---|---|---|
| [Claude Code MCP](https://code.claude.com/docs/en/mcp) · [plugin marketplaces](https://code.claude.com/docs/en/plugin-marketplaces) · [desktop extensions](https://www.anthropic.com/engineering/desktop-extensions) | Claude Code MCP setup, plugins and desktop extensions | 31 |
| [modelcontextprotocol/mcpb](https://github.com/modelcontextprotocol/mcpb) · [blog](https://blog.modelcontextprotocol.io/posts/2025-11-20-adopting-mcpb/) | MCP Bundles (MCPB) | 31 |
| [Codex MCP](https://developers.openai.com/codex/mcp) · [skills](https://learn.chatgpt.com/docs/build-skills) | OpenAI Codex MCP and skills | 31 |
| [Cursor](https://cursor.com/docs/mcp/install-links) · [VS Code](https://code.visualstudio.com/docs/agent-customization/mcp-servers) · [VS Code API](https://code.visualstudio.com/api/extension-guides/ai/mcp) · [Gemini CLI](https://google-gemini.github.io/gemini-cli/docs/tools/mcp-server.html) · [LM Studio](https://lmstudio.ai/docs/app/mcp) | MCP setup in Cursor, VS Code, Gemini CLI and LM Studio (`satk mcp config --client`) | 31 |
| [proofpoint.com](https://www.proofpoint.com/us/blog/threat-insight/cursorjack-weaponizing-deeplinks-exploit-cursor-ide) | Security research on MCP install deep links | 31 |
| [TabbedScamper/GTAV-CLAUDE-MCP](https://github.com/TabbedScamper/GTAV-CLAUDE-MCP) | An in-game bridge from GTA V to Claude: the pattern behind our game target | 12 |

### Agents that play and test games

| Source | Notes | Reports |
|---|---|---|
| [alexzhang13/videogamebench](https://github.com/alexzhang13/videogamebench) · [arXiv 2505.18134](https://arxiv.org/abs/2505.18134) · [arXiv 2103.15819](https://arxiv.org/abs/2103.15819) | VideoGameBench and EA SEED's research on game-testing agents | 26 |
| [BAAI-Agents/Cradle](https://github.com/BAAI-Agents/Cradle) · [JackHopkins/factorio-learning-environment](https://github.com/JackHopkins/factorio-learning-environment) · [PrismarineJS/mineflayer](https://github.com/PrismarineJS/mineflayer) · [Unity-Technologies/ml-agents](https://github.com/Unity-Technologies/ml-agents) · [alttester/AltTester-Unity-SDK](https://github.com/alttester/AltTester-Unity-SDK) · [aitorzip/DeepGTAV](https://github.com/aitorzip/DeepGTAV) | Prior art: screen-based agents, API-based game environments and instrumented builds | 26 |
| [Unreal Gauntlet](https://dev.epicgames.com/documentation/en-us/unreal-engine/gauntlet-automation-framework-in-unreal-engine) | Unreal's automation framework for test sessions | 26 |
| [ra1nty/DXcam](https://github.com/ra1nty/DXcam) · [NiiightmareXD/windows-capture](https://github.com/NiiightmareXD/windows-capture) · [IsBorderRequired](https://learn.microsoft.com/en-us/uwp/api/windows.graphics.capture.graphicscapturesession.isborderrequired) · [SendInput](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput) | Screen capture and input injection on Windows | 26 |
| [google/swiftshader](https://github.com/google/swiftshader) · [WARP](https://learn.microsoft.com/en-us/windows/win32/direct3darticles/directx-warp) · [larger runners](https://docs.github.com/en/actions/reference/runners/larger-runners) | Software rendering and CI runners for headless game tests | 26 |

## Mapping and content ecosystem

SA-MP and MTA mapping tools and converters behind `satk map convert` and `satk id`.

| Source | Notes | Reports |
|---|---|---|
| [Pottus/Texture-Studio](https://github.com/Pottus/Texture-Studio) · [wiki](https://github.com/Crayder/Texture-Studio/wiki) · [forum.open.mp](https://forum.open.mp/showthread.php?tid=174) | Texture Studio: the de facto SA-MP map editor; `satk map convert` reads its text formats | 23 |
| [ins1x/mtools](https://github.com/ins1x/mtools) | mtools: GUI add-on for Texture Studio (MPL-2.0) | 23, 29 |
| [simonseidel/Map-Editor-V3](https://github.com/simonseidel/Map-Editor-V3) · [NexiusTailer/Ultimate-Creator](https://github.com/NexiusTailer/Ultimate-Creator) | Fusez's Map Editor V3 and Ultimate Creator | 23 |
| [Papawy/Papawyconv](https://github.com/Papawy/Papawyconv) · [moon91210/ipl2map](https://github.com/moon91210/ipl2map) · [gta191977649/GTA-SAMP-MapConverter](https://github.com/gta191977649/GTA-SAMP-MapConverter) · [Fernando-A-Rocha/mta-map-to-ipl](https://github.com/Fernando-A-Rocha/mta-map-to-ipl) · [convertffs.com](https://convertffs.com/) | Map converters between IPL, Pawn and MTA `.map` | 23, 30 |
| [Pottus/ColAndreas](https://github.com/Pottus/ColAndreas) · [philip1337/samp-plugin-mapandreas](https://github.com/philip1337/samp-plugin-mapandreas) · [samp-incognito/samp-streamer-plugin](https://github.com/samp-incognito/samp-streamer-plugin) | SA-MP server plugins: collisions, height map and object streamer | 21, 23 |
| [San-Andreas-Roleplay-ES/crc32](https://github.com/San-Andreas-Roleplay-ES/crc32) | CRC32 of DFF/TXD as used by SA-MP custom model downloads | 23 |
| [Poly Haven](https://polyhaven.com/license) · [ambientCG](https://docs.ambientcg.com/license/) · [Kenney](https://kenney.nl/support) | CC0 asset libraries usable for new content | 23 |

## Packaging, distribution and adoption

Sources for the portable release, installation and community channels.

| Source | Notes | Reports |
|---|---|---|
| [docs.python.org](https://docs.python.org/3/using/windows.html) · [Python install manager](https://www.python.org/downloads/release/pymanager-252/) | Python on Windows and the new install manager | 31 |
| [astral-sh/uv](https://github.com/astral-sh/uv) | uv package and tool installer | 31 |
| [PyInstaller discussion](https://github.com/orgs/pyinstaller/discussions/5877) · [coderslegacy.com](https://coderslegacy.com/pyinstaller-exe-detected-as-virus-solutions/) | Antivirus false positives of PyInstaller builds (why we ship a portable zip instead) | 31 |
| [Artifact Signing](https://learn.microsoft.com/en-us/azure/artifact-signing/quickstart) · [melatonin.dev](https://melatonin.dev/blog/code-signing-on-windows-with-azure-trusted-signing/) · [SignPath](https://signpath.org/terms.html) | Code signing options on Windows | 31 |
| [openiv.co](https://openiv.co/how-to-use/) · [gtaforums.com](https://gtaforums.com/topic/798272-tut-use-openiv-mods-folder-keep-your-original-gta-v/) | OpenIV's mods folder: a familiar install pattern for modders | 31 |
| [spec-kit #1062](https://github.com/github/spec-kit/issues/1062) | An example of onboarding problems for newcomers | 31 |
| [Telegram (Wikipedia)](https://en.wikipedia.org/wiki/Blocking_of_Telegram_in_Russia) · [Meduza](https://meduza.io/amp/en/feature/2026/06/11/russia-has-been-blocking-telegram-for-months-meduza-asked-five-popular-channel-admins-if-it-s-working) · [Euronews](https://www.euronews.com/2026/02/10/russia-restricts-telegram-over-alleged-law-breaches-as-it-supports-state-backed-rival) · [RFE/RL](https://www.rferl.org/a/roskomnadzor-blocks-discord-tech-russia-ukraine/33152178.html) · [The Record](https://therecord.media/discord-messaging-app-banned-russia-turkey) | Restrictions of Telegram and Discord in Russia (choice of community channels) | 31 |

## Legal and policy sources

What we read for the legal report; satk ships no game data and no decompiled code because of it.

| Source | Notes | Reports |
|---|---|---|
| [mod guidelines](https://www.rockstargames.com/community-resources/mod-guidelines) · [legal](https://www.rockstargames.com/legal) · [single-player mods](https://support.rockstargames.com/articles/5NVOAYjcTomO8v6SX2k76k/pc-single-player-mods) · [en-US](https://support.rockstargames.com/en-US/articles/5NVOAYjcTomO8v6SX2k76k/pc-single-player-mods) · [diffchecker](https://www.diffchecker.com/6VRyddGs) | Rockstar Games mod guidelines, terms and the single-player mods article (with a comparison of older revisions) | 12, 17, 23, 25 |
| [DMCA policy](https://docs.github.com/en/site-policy/content-removal-policies/dmca-takedown-policy) · [github/dmca](https://github.com/github/dmca) | GitHub's DMCA policy and the public notices (Take-Two cases) | 12, 17, 24, 25 |
| [fivem.net/terms](https://fivem.net/terms) · [platform licence](https://static.cfx.re/platform-license-agreement-10-sept-2026.pdf) · [FiveM (Wikipedia)](https://en.wikipedia.org/wiki/FiveM) · [RedM](https://redm.net/) | FiveM and RedM: the licensed-platform precedent | 25 |
| [Directive 2009/24/EC](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32009L0024) · [17 U.S.C. 1201](https://www.law.cornell.edu/uscode/text/17/1201) | EU software directive (interoperability) and the US anti-circumvention law | 25 |
| [copyright.gov/ai](https://www.copyright.gov/ai/) · [NewsNet 1060](https://copyright.gov/newsnet/2025/1060.html) | US Copyright Office on AI-generated works (Part 2, 2025) | 22, 25 |
| [GPL FAQ](https://www.gnu.org/licenses/gpl-faq.html) | GNU GPL FAQ (linking and aggregation) | 25 |
| [BASS](https://www.un4seen.com/bass.html) · [FMOD](https://www.fmod.com/legal) | Licences of the audio libraries in MTA | 05, 25 |
| [TorrentFreak: lawsuit](https://torrentfreak.com/take-two-dismisses-claims-against-lead-defendants-in-gta-mods-lawsuit-230405/) · [TorrentFreak: MTA restored](https://torrentfreak.com/github-restores-repo-of-gta-mod-multi-theft-auto-after-take-two-fails-to-sue/) · [TorrentFreak](https://torrentfreak.com/?p=275921) · [HotHardware: re3](https://hothardware.com/news/gta-3-and-vice-city-reverse-engineered) · [HotHardware: takedowns](https://hothardware.com/news/take-two-gta-mods-takedown) | Coverage of the re3/reVC case and of the MTA takedown and restore | 12, 17, 23, 25 |
| [Kotaku: guidelines](https://kotaku.com/rockstar-asks-fans-to-respect-their-games-as-it-updates-strict-modding-guidelines-2000736306) · [Inven Global](https://www.invenglobal.com/articles/26384/no-exception-for-gta-6-rockstar-releases-mod-guidelines-across-all-titles) · [GTA BOOM: guidelines](https://www.gtaboom.com/rockstar-games-new-mod-guidelines-target-unofficial-gta-ports-20f4) · [Mein-MMO](https://mein-mmo.de/en/gta-mods-nur-noch-bei-rockstar,1552582) | Coverage of Rockstar's 2026 mod guidelines | 12, 16, 17, 23, 25 |
| [GTA BOOM: alt:V](https://www.gtaboom.com/the-last-gta-v-multiplayer-alternative-just-got-a-cease-and-desist-3970) · [Gameranx](https://gameranx.com/updates/id/561049/article/take-two-has-sent-a-takedown-notice-to-gta-v-fivem-alternative-altv/) · [Rockstar Intel](https://rockstarintel.com/rockstar-issues-policy-update-on-roleplay-servers-gta-rp-fivem-and-more/) | Takedowns of alternative multiplayer platforms and Rockstar's roleplay server policy | 16, 25 |
| [Dexerto](https://www.dexerto.com/gta/rockstar-takes-legal-action-after-modders-port-gta-5-to-nintendo-switch-3413248/) · [Generation Amiga](https://www.generationamiga.com/2026/07/18/gta-san-andreas-android-port-released-for-nintendo-switch/) · [GTA BOOM: browser](https://www.gtaboom.com/you-can-now-play-gta-san-andreas-in-your-web-browser-too-a8f6) | Unofficial ports and why they are a legal risk | 12, 17, 25 |
| [Kotaku: GTA Underground](https://kotaku.com/ambitious-gta-underground-mod-shutdowns-after-six-years-1847619249) · [PCGH: Carcer City](https://www.pcgameshardware.de/Grand-Theft-Auto-San-Andreas-Spiel-56275/News/Total-Conversion-Carcer-City-Demo-1492433/) · [GGRecon: Stars and Stripes](https://www.ggrecon.com/articles/grand-theft-auto-stars-and-stripes-mod-is-gta-7-in-the-3d-era) | Total conversions and how they ended | 23 |
| [gtaintel.com](https://gtaintel.com/news/san-andreas-multiplayer-in-2026) | State of San Andreas multiplayer in 2026 | 12, 21, 25 |

## Community sites and tutorials

Community hubs and tutorials that describe how modders actually work.

| Source | Notes | Reports |
|---|---|---|
| [blast.hk](https://www.blast.hk/) · [MoonLoader guide](https://www.blast.hk/threads/21056/) · [thread 254794](https://www.blast.hk/threads/254794/) | BlastHack: Russian-language SA-MP and MoonLoader scripting community | 29, 31 |
| [MixMods (GTAForums)](https://gtaforums.com/topic/999795-mixmods/) · [add cars without replacing](https://www.mixmods.com.br/2020/02/tutorial-adicionar-carros-sem-substituir/) | MixMods: Brazilian modding hub and its add-on vehicle tutorial | 29, 31 |
| [add new cars](https://gtaforums.com/topic/832297-satut-how-to-add-new-cars-without-replacing/) · [add new weapons](https://gtaforums.com/topic/990989-sahow-to-add-new-weapons/) | GTAForums tutorials on add-on vehicles and weapons (the manual process `satk id` helps with) | 29 |

## Not linked

Our reports mention these, but we do not link them: they host the game's assets or code, or they are dead. satk does not use them.

- Prineside DevTools model and texture catalogue: hosts images of the game's models and textures (reports 12, 23, 24, 31).
- GTA Stuff preview CDN: renders of the game's assets (report 09).
- AI-upscaled texture packs on ModDB: redistributed game textures (reports 22, 29).
- Content downloads on LibertyCity: a ped pack and a modified game data file (reports 18, 29).
- Forum guide to porting models from other games (report 29).
- gta-reversed-android: the repository contains the proprietary Android game binary and scripts (report 17).
- The re3/reVC code and the reVC re-upload: taken down by DMCA (report 17).
- gta-workshop: publishes the full decompiler output of gta_sa.exe (report 24).
- An old public IDA database of gta_sa.exe: it contains the executable's code (reports 10, 12).
- mod_sa: a multiplayer cheat (report 12).
- scene2res: OSDN does not resolve (report 23).
- The original OpenSA site and repository: expired certificate and 404; the mirror is listed above (report 12).
- Crspy/GTA_SA_IDB: an empty repository (reports 10, 12).

Endpoints quoted from source code (local addresses, update and registry servers), directory pages that mirror a repository listed above, category indexes and direct binary downloads are not listed.
