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
| check my mod for errors | `satk asset lint`, `satk mod check` | [lint.md](lint.md), [modinspect.md](modinspect.md) |
| see what a mod changes and which mods conflict | `satk mod inspect mymod.zip`, `satk mod conflicts` | [modinspect.md](modinspect.md) |
| find free model ids or remap a mod | `satk id free --kind vehicle` | [idmgr.md](idmgr.md) |
| convert a map (SA-MP, MTA, IPL) | `satk map convert my_map.pwn --to mta` | [mapconv.md](mapconv.md) |
| compare my game with the stock one | `satk index diff vanilla installed` | [index.md](index.md) |
| find out why the game crashed | `satk crash analyze --last` | [crash.md](crash.md) |
| turn a `gta_sa.exe` address into a function | `satk re addr 0x53BF09` | [re.md](re.md), [kb.md](kb.md) |
| fly a camera and ask "what is this object" | `satk view goto`, `satk view capture --marks 6` | [viewer.md](viewer.md) |
| open an area in Blender and export it to MTA | `satk blender import-area --center 2495,-1687 --r 60` | [blender.md](blender.md) |
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
| [rw.md](rw.md) | writing RenderWare without Blender: DFF patches, COL from JSON, IMG archives | `satk rw`, `col`, `img` |
| [models.md](models.md) | GPU-free model previews, export to glTF, OBJ and raw | `satk model`, `asset export` |
| [catalog.md](catalog.md) | an offline HTML catalog of models, textures and zones with thumbnails | `satk catalog build` |
| [lint.md](lint.md) | the asset linter: DFF, TXD, COL, IDE and the links between them | `satk asset lint`, `asset lint-rules` |
| [modinspect.md](modinspect.md) | what a mod changes and how Mod Loader combines mods | `satk mod inspect`, `conflicts`, `effective`, `check` |
| [idmgr.md](idmgr.md) | free model ids, id conflicts between mods, remapping a mod | `satk id` |
| [mapconv.md](mapconv.md) | map converters (SA-MP Pawn, MTA `.map`, IPL, JSON), binary IPL, area cleaning | `satk map convert`, `map validate`, `ipl`, `map clean` |
| [paths.md](paths.md) | car, boat and pedestrian paths (NODES*.DAT) | `satk paths` |
| [crash.md](crash.md) | crash dumps and logs: report, known solution, culprit | `satk crash` |
| [re.md](re.md) | the `gta_sa.exe` symbol DB: address to function, source, MTA patches | `satk re` |
| [kb.md](kb.md) | knowledge base of the engine: sources, struct layouts, opcodes, facts | `satk kb` |
| [describe.md](describe.md) | model descriptions (gta-scout) in the notes | `satk describe` |
| [viewer.md](viewer.md) | the viewer: camera, frames with marks, "what is this object" | `satk view` |
| [saap.md](saap.md) | SAAP/1, the protocol of the agent's eyes (overview) | `satk saap`, `view conformance` |
| [mta-agent.md](mta-agent.md) | eyes in the real game through an MTA resource (target `game`) | `satk view … --target game` |
| [blender.md](blender.md) | headless Blender: import models and areas, renders, export to MTA; the add-on | `satk blender` |
| [engine.md](engine.md) | the MTA fork: setup, dependencies, build | `satk engine` |
| [mcp.md](mcp.md) | the MCP server, status, doctor, help and notes | `satk status`, `doctor`, `help`, `note`, `mcp` |
| [ai.md](ai.md) | connecting AI clients (Claude Code, Codex, Cursor, VS Code, LM Studio and others), the skill | `satk mcp config --client`, `agent install-skill` |
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
  and the generated references [`tools.md`](../agent/tools.md) and [`schema.md`](../agent/schema.md).
- Checks of these pages: `satk dev docs-smoke` (quick examples), `satk dev linkcheck docs README.md README.ru.md`
  (paths and links) and `satk dev docs-parity` (every English page has its Russian mirror).
