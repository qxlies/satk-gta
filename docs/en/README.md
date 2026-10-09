# satk user guide

[Русская версия](../ru/README.md)

<!-- Index of every page of docs/en; the Russian mirror docs/ru/README.md has the same
     sections. Checked by `satk dev linkcheck`, `satk dev docs-smoke` and `satk dev docs-parity`. -->

satk (San Andreas ToolKit) is a toolkit for GTA San Andreas (PC) modding, reverse engineering and debugging. People
and AI assistants use the same thing: the `satk` command in a terminal and the `satk` MCP server in an AI client run
the same operations and give the same answers. `satk help` shows the main tasks, `satk help --all` every command.

## Safety and privacy

- **The game is only read.** Your game folder, the clean copy and the source clones are write-protected; satk writes
  only into `<workspace>\work\` (indexes, caches, pictures, exports, logs). Every write goes through one guard
  that refuses protected paths with `PROTECTED_PATH`.
- **No network, no telemetry.** satk opens no internet connections by itself. Downloads happen only in commands
  you run on purpose (`bootstrap.ps1 -Deps`, `satk engine setup`, `satk dev release`); `satk doctor` shows it as
  the `network` check. Sockets are used only on 127.0.0.1 (the viewer, the game, SAAP).
- **AI is optional.** Everything works from the command line. With a cloud AI assistant, what satk answers goes to
  that assistant's provider as part of the conversation; a fully local setup (LM Studio) sends nothing: see
  [ai.md](ai.md).
- **Bug reports stay on your computer:** `satk bug-report` writes a redacted local file and sends nothing
  ([bugreport.md](bugreport.md)).

## Start here

1. [Install on any machine](install.md): `satk init` finds your game; the portable zip needs no Python.
2. [Quick start](quickstart.md): from zero to the first screenshot in ten commands.
3. [Identifiers (SIDs)](ids.md): how models, textures, placements and functions are addressed.
4. [Common tasks](workflows.md): ready-made command chains.
5. [If something does not work](troubleshooting.md) and the [glossary](glossary.md).

One concrete layout of a workspace (game copy, source clones, viewer and MTA fork side by side):
[workspace-gta.md](workspace-gta.md).

## I want to...

| I want to... | Command | Page |
|---|---|---|
| set satk up for my game | `satk init`, then `satk doctor` | [install.md](install.md) |
| index my game (once, after it changes) | `satk index build` | [index.md](index.md) |
| find a model, texture or file | `satk asset find infernus` | [index.md](index.md), [ids.md](ids.md) |
| see textures on one sheet | `satk texture image model:411 --mode sheet` | [media.md](media.md) |
| see a model or a part of the map | `satk model image model:411`, `satk map image --center 2495,-1687` | [models.md](models.md), [media.md](media.md) |
| browse everything in a web page, offline | `satk catalog build` | [catalog.md](catalog.md) |
| export a model to glTF or OBJ | `satk asset export model:411 --format glb` | [models.md](models.md) |
| replace textures in a TXD | `satk texture replace txd:bistro Plate=bistro/Marble.png` | [texmod.md](texmod.md) |
| edit DFF, COL or IMG files without Blender | `satk rw patch`, `satk col write`, `satk img build` | [rw.md](rw.md) |
| generate collision for a model, check COL files | `satk col gen model:1337`, `satk col check` | [colgen.md](colgen.md) |
| add a light or a particle effect to a model | `satk fx2d copy model:lamppost1 my.dff --filter light` | [fx2d.md](fx2d.md) |
| make a mod's textures smaller, check streaming memory | `satk texture audit mymod`, `satk texture optimize mymod --max 512`, `satk texture budget` | [txdopt.md](txdopt.md) |
| check my mod for errors | `satk asset lint`, `satk mod check` | [lint.md](lint.md), [modinspect.md](modinspect.md) |
| see what a mod changes and which mods conflict | `satk mod inspect mymod.zip`, `satk mod conflicts` | [modinspect.md](modinspect.md) |
| find free model ids or remap a mod | `satk id free --kind vehicle` | [idmgr.md](idmgr.md) |
| add a new car, ped, weapon or object as an add-on | `satk mod add vehicle --dff my.dff --txd my.txd --like 411 --name mycar` | [addon.md](addon.md) |
| read, explain or change handling and weapon data | `satk data get handling infernus`, `satk data patch handling infernus fMass=1500` | [addon.md](addon.md) |
| write, disassemble, check or assemble a CLEO script | `satk script new hello --name test`, `satk script disasm mymod.cs` | [script.md](script.md) |
| check an MTA resource before uploading it, or start a new one | `satk mta lint my-panel`, `satk mta resource new my-panel` | [mta.md](mta.md) |
| find the world texture names for an MTA shader | `satk shader textures --kind road`, `satk shader new wet_roads --name wetroads` | [shader.md](shader.md) |
| look up an MTA Lua function or event, or a SA-MP native | `satk kb mta setVehicleHandling`, `satk kb native CreateObject` | [scriptapi.md](scriptapi.md) |
| run one command over many files, or a saved multi-step check | `satk batch asset.lint --over "mods/*.dff"`, `satk recipe run check-mod-before-release --var mod=mymod` | [batch.md](batch.md) | <!-- linkcheck: ignore -->
| change game text, zones, water or the time cycle | `satk gxt get CRED001`, `satk zone list --at 2495,-1687` | [worldfiles.md](worldfiles.md) |
| edit or add an animation | `satk anim list ifp:ped --name walk`, `satk anim extract anim:ped/walk_civi --out walk.json` | [anim.md](anim.md) |
| see whether my mods fit into the engine limits | `satk limits plan --profile installed` | [limits.md](limits.md) |
| [obs.md](obs.md) | validate, read, summarize, convert and merge engine traces, bench results and `.saenet` / `.saerec` containers | `satk obs` |
| [obs-spec.md](obs-spec.md) | the observability formats: timeline key, JSONL records, bench JSON, container layout, versions | `satk obs schema` |
| pack files into one verifiable `.saepak`, derive render caches, count duplicate textures | `satk pack build mymod`, `satk pack census` | [pack.md](pack.md) |
| convert a map (SA-MP, MTA, IPL) | `satk map convert my_map.pwn --to mta` | [mapconv.md](mapconv.md) |
| compare my game with the stock one | `satk index diff vanilla installed` | [index.md](index.md) |
| find out why the game crashed | `satk crash analyze --last` | [crash.md](crash.md) |
| turn a `gta_sa.exe` address into a function | `satk re addr 0x53BF09` | [re.md](re.md), [kb.md](kb.md) |
| fly a camera and ask "what is this object" | `satk view goto`, `satk view capture --marks 6` | [viewer.md](viewer.md) |
| open an area in Blender and export it to MTA | `satk blender import-area --center 2495,-1687 --r 60` | [blender.md](blender.md) |
| create a new car, prop or building in Blender that looks like the stock game | `satk blender session start --gui` | [authoring.md](authoring.md), [sa-style.md](sa-style.md) |
| build an asset with my own AI agents | `satk asset inventory`, `asset check --strict` | [asset-pipeline.md](asset-pipeline.md) |
| check a model against the stock style of its class | `satk asset check model:426 --tier vanilla` | [style.md](style.md) |
| start a new asset from a template or a clean blank | `satk kit kinds`, `satk kit blank` | [kit.md](kit.md) |
| see my model the way the game draws it, next to stock ones | `satk blender preview model:426 --lineup model:405,model:560` | [look.md](look.md) |
| bring a third-party model into the San Andreas style | `satk asset convert model.glb --kind prop --dims 1.2,1.2,1.8` | [convert.md](convert.md) |
| make a tileable texture in the San Andreas style | `satk texlib make brick --size 256` | [texlib.md](texlib.md) |
| put my own car or model next to the map in the viewer | `satk view vehicle 411 --pos 2495,-1675,13.4` | [viewscene.md](viewscene.md) |
| test a mod in the real game (MTA) | `satk ingame status`, `satk ingame start --mod <folder>` | [ingame.md](ingame.md) |
| connect an AI assistant | `satk mcp config --client cursor` | [ai.md](ai.md), [mcp.md](mcp.md) |
| report a problem with satk | `satk bug-report` | [bugreport.md](bugreport.md) |

## All pages

| Page | What it covers | Commands |
|---|---|---|
| [install.md](install.md) | install on any machine: zip, git checkout, Python package; finding the game | `satk init`, `doctor` |
| [quickstart.md](quickstart.md) | from zero to the first screenshot | ten commands |
| [ids.md](ids.md) | stable identifiers (SIDs) of everything satk addresses | `satk asset get` |
| [workflows.md](workflows.md) | common tasks as command chains | all |
| [troubleshooting.md](troubleshooting.md) | known problems and their fixes | `satk doctor` |
| [glossary.md](glossary.md) | game formats, satk concepts, related projects | — |
| [workspace-gta.md](workspace-gta.md) | one concrete workspace layout | `satk config show` |
| [game.md](game.md) | the clean game copy: verify, protect, reproduce | `satk game` |
| [formats.md](formats.md) | GTA:SA file format parsers (IMG, DFF, TXD, COL, IDE, IPL, IFP, ZON, DAT) | `satk formats` |
| [index.md](index.md) | the SQLite asset index: profiles, search, references, SQL | `satk index`, `asset`, `world` |
| [media.md](media.md) | textures, contact sheets, the top-down map | `satk texture`, `map image` |
| [texmod.md](texmod.md) | texture modding: DXT encoder, building and patching TXDs, Mod Loader folders | `satk texture pack`, `replace`, `extract` |
| [texlib.md](texlib.md) | procedural SA materials and vanilla map texture references | `satk texlib make`, `list`, `vanilla` |
| [rw.md](rw.md) | writing RenderWare without Blender: DFF patches, COL from JSON, IMG archives | `satk rw`, `col`, `img` |
| [colgen.md](colgen.md) | collision generated from models (box, boxes, hull, mesh, spheres), collision checks and the surface table | `satk col gen`, `col check`, `col surface` |
| [fx2d.md](fx2d.md) | 2dEffect (2DFX) entries of DFF models (lights, particles, entry points) as editable JSON | `satk fx2d` |
| [addon.md](addon.md) | add-on models as Mod Loader folders; reading, patching and explaining handling, weapon and ped data | `satk mod add`, `data` |
| [txdopt.md](txdopt.md) | smaller TXDs (DXT recompression, mip trimming), texture audit and the streaming memory budget | `satk texture optimize`, `audit`, `budget` |
| [script.md](script.md) | CLEO and SCM scripts: disassembler, assembler, checks, templates | `satk script` |
| [scriptapi.md](scriptapi.md) | MTA Lua functions, events, OOP classes and enums; SA-MP/open.mp natives and callbacks, offline from the knowledge base | `satk kb mta`, `native` |
| [mta.md](mta.md) | MTA:SA resources: Lua 5.1 lint with MTA's own messages, starter resources, model packs, logs, a server load check | `satk mta lint`, `resource new`, `pack`, `logs` |
| [shader.md](shader.md) | MTA shaders: world texture names and exact pattern lists, starter shader resources, fxc and MTA checks | `satk shader textures`, `new`, `check` |
| [anim.md](anim.md) | IFP animations: list, JSON, write, merge into ped.ifp, check against a skeleton, to and from Blender | `satk anim` |
| [models.md](models.md) | GPU-free model previews, export to glTF, OBJ and raw | `satk model`, `asset export` |
| [catalog.md](catalog.md) | an offline HTML catalog of models, textures and zones with thumbnails | `satk catalog build` |
| [lint.md](lint.md) | the asset linter: DFF, TXD, COL, IDE and the links between them | `satk asset lint`, `asset lint-rules` |
| [style.md](style.md) | SA style guidance and class references; defects, coverage and strict completion of a model or texture | `satk style profile`, `asset check`, `style texture` |
| [batch.md](batch.md) | run any operation over many files, saved multi-step recipes | `satk batch`, `recipe` |
| [modinspect.md](modinspect.md) | what a mod changes and how Mod Loader combines mods | `satk mod inspect`, `conflicts`, `effective`, `check` |
| [limits.md](limits.md) | engine capacity planning, stock headroom, crash signatures and adjuster INI suggestions | `satk limits plan` |
| [pack.md](pack.md) | `.saepak` content containers (content addressed, IMG compatible, per-chunk hashes), derived-asset cache files and the texture dedup census | `satk pack build`, `verify`, `inspect`, `dac`, `census` |
| [saepak.md](saepak.md) | the byte layout of the `.saepak` container, format 1.0 | `satk pack build`, `verify`, `inspect` |
| [dac.md](dac.md) | the byte layout of the DAC (derived-asset cache) file, format 1.0 | `satk pack dac` |
| [imagegen.md](imagegen.md) | UI icons from an image API (keyed, resized, validated, with provenance) and a Pillow placeholder set that needs no key | `satk imagegen` |
| [idmgr.md](idmgr.md) | free model ids, id conflicts between mods, remapping a mod | `satk id` |
| [mapconv.md](mapconv.md) | map converters (SA-MP Pawn, MTA `.map`, IPL, JSON), binary IPL, area cleaning | `satk map convert`, `map validate`, `ipl`, `map clean` |
| [worldfiles.md](worldfiles.md) | world data of total conversions: GXT/FXT text, zones, water, time cycle, population cycle, radar tiles | `satk gxt`, `fxt`, `zone`, `water`, `timecyc`, `popcycle`, `radar` |
| [paths.md](paths.md) | car, boat and pedestrian paths (NODES*.DAT) | `satk paths` |
| [crash.md](crash.md) | crash dumps and logs: report, known solution, culprit | `satk crash` |
| [re.md](re.md) | the `gta_sa.exe` symbol DB: address to function, source, MTA patches | `satk re` |
| [kb.md](kb.md) | knowledge base of the engine: sources, struct layouts, opcodes, facts | `satk kb` |
| [describe.md](describe.md) | model descriptions (gta-scout) in the notes | `satk describe` |
| [viewer.md](viewer.md) | the viewer: camera, frames with marks, "what is this object" | `satk view` |
| [viewscene.md](viewscene.md) | your own models, vehicles and peds in the running viewer, reloaded when the files change | `satk view place`, `vehicle`, `ped` |
| [saap.md](saap.md) | SAAP/1, the protocol of the agent's eyes (overview) | `satk saap`, `view conformance` |
| [mta-agent.md](mta-agent.md) | eyes in the real game through an MTA resource (target `game`) | `satk view … --target game` |
| [ingame.md](ingame.md) | the in-game test loop: a mod in the real game (MTA fork), test spots, behaviour checks vs vanilla with frames, hot reload | `satk ingame start`, `drive`, `check`, `reload` |
| [blender.md](blender.md) | headless Blender: import models and areas, renders, export to MTA; the add-on | `satk blender` |
| [studio.md](studio.md) | a live Blender session for step-by-step modelling by an AI assistant | `satk blender session`, `call` |
| [inventory.md](inventory.md) | the task list of an asset: every part as an item, checked as built, missing, unattached or rejected | `satk asset inventory`, `inventory starter` |
| [look.md](look.md) | SA-like previews of game models, your own DFF files and live sessions, side by side | `satk blender preview` |
| [kit.md](kit.md) | templates, shading, generators and export for any asset kind (cars to peds) | `satk kit kinds`, `kit template`, `kit export` |
| [convert.md](convert.md) | convert rigid 3D models to SA scale, geometry, textures and packages | `satk asset convert`, `convert prepare`, `convert finish` |
| [engine.md](engine.md) | the MTA fork: setup, dependencies, build | `satk engine` |
| [spbridge.md](spbridge.md) | agent eyes and hands in single-player GTA:SA: the `satk_sp.asi` SAAP/1 bridge, offline-tested | `satk sp` |
| [mcp.md](mcp.md) | the MCP server, status, doctor, help and notes | `satk status`, `doctor`, `help`, `note`, `mcp` |
| [ai.md](ai.md) | connecting AI clients (Claude Code, Codex, Cursor, VS Code, LM Studio and others), the skill | `satk mcp config --client`, `agent install-skill` |
| [sa-style.md](sa-style.md) | what makes an asset look like San Andreas: textures, shape, shading, size, detail tiers, mistakes | `satk asset get`, `texture image` |
| [authoring.md](authoring.md) | creating assets of any kind in a live Blender session, gate by gate | `satk blender session`, `asset check` |
| [asset-pipeline.md](asset-pipeline.md) | building an asset with your own AI agents: builder, critic, polisher and six evidence gates | `satk asset inventory`, `asset check --strict` |
| [bugreport.md](bugreport.md) | a private, local report for satk problems | `satk bug-report` |
| [release.md](release.md) | the portable Windows release | `satk dev release` |
| [credits.md](credits.md) | acknowledgements and every source satk and its research used, by topic | — |

A new page starts from the template [`_template.md`](_template.md).

## How to read these pages

- Commands are written for PowerShell or cmd. In a git checkout `satk` means `<workspace>\tools\satk.cmd`
  (Git Bash: `satk.sh`); the portable zip opens a console where `satk` works as is.
- In a terminal answers are tables; in a pipe and for an AI assistant they are compact JSON. `--table` and
  `--json` switch the format.
- Write positional arguments first: `satk asset get model:411 --fields name,sec`
  (see [troubleshooting](troubleshooting.md#cli-the-following-arguments-are-required)).
- The **Quick example** section of every page is real: `satk dev docs-smoke` runs all of them and fails when a
  command breaks or does not exist. Viewer examples use the test target `--target mock`.
- Every page has a Russian mirror, linked at its top.

## Quick example

```powershell
satk version
satk help
satk config show --section paths
```

`satk help` prints the "I want to..." menu above with the exact commands; `satk config show` shows where satk looks
for the game, the work folder and the tools.

## For developers

- How to develop satk: [`CLAUDE.md`](../../CLAUDE.md) at the repository root.
- Docs for AI agents (model-facing, English only): [`SKILL.md`](../agent/SKILL.md),
  [`workflows.md`](../agent/workflows.md), [`errors.md`](../agent/errors.md), [`evals.md`](../agent/evals.md),
  the generated references [`tools.md`](../agent/tools.md) and [`schema.md`](../agent/schema.md), the style
  guides [`style/README.md`](../agent/style/README.md) and the brief template
  [`asset-brief.md`](../agent/briefs/asset-brief.md).
- Checks of these pages: `satk dev docs-smoke` (quick examples), `satk dev linkcheck docs README.md README.ru.md`
  (paths and links) and `satk dev docs-parity` (every English page has its Russian mirror).
