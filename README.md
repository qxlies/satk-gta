# satk — San Andreas ToolKit

[Русская версия](README.ru.md)

[![CI](https://github.com/qxlies/satk-gta/actions/workflows/ci.yml/badge.svg)](https://github.com/qxlies/satk-gta/actions/workflows/ci.yml)

> **Status: public preview (0.2.1).** satk works and its checks pass, but commands, answers and file layouts may
> still change before 1.0. Bug reports and ideas are welcome on the
> [Issues](https://github.com/qxlies/satk-gta/issues) page.

satk is a toolkit for GTA San Andreas (PC) modders, reverse engineers and people who debug the game. It indexes your
game and lets you, or an AI assistant, find and inspect models, textures, map placements and crash addresses, build
texture and model mods, check mods for errors and conflicts, and convert maps. It is one Python package with one
command, `satk`, and one MCP server, also `satk`: both run the same operations and give the same answers.

## Security and privacy

- **Your game is only read.** The game folder, the clean copy and the source clones are write-protected; satk writes
  only into its own `work` folder (indexes, caches, pictures, exports, logs).
- **No network, no telemetry.** satk opens no internet connections by itself; downloads happen only in commands you
  run on purpose (dependency setup, `satk engine setup`, `satk dev release`). `satk doctor` shows it as a check.
- **Bug reports stay local:** `satk bug-report` writes a redacted file on your computer and sends nothing.
- With a cloud AI assistant, what satk answers goes to that assistant's provider as part of the conversation; a fully
  local setup sends nothing. Details: [docs/en/ai.md](docs/en/ai.md).

## Works without AI

Every operation is a terminal command with readable tables: `satk asset find`, `satk texture image`,
`satk crash analyze`, `satk mod conflicts`, and so on. `satk help` shows the main tasks, `satk help --all` every
command, and `satk catalog build` makes an offline web page of all models, textures and zones. An AI assistant is an
option on top: `satk mcp config --client <name>` connects Claude Code, Codex, Cursor, VS Code, LM Studio and others.

## Install in three steps

You need 64-bit Windows 10/11 and **any** GTA San Andreas (PC) folder: 1.0 US, Steam, with or without mods.

1. **Get satk.** The portable zip `satk-<version>-win64.zip` from the
   [Releases page](https://github.com/qxlies/satk-gta/releases) needs no Python and no admin rights: unpack it into
   your own folder and double-click `Start satk.cmd`. From a git checkout: clone
   [github.com/qxlies/satk-gta](https://github.com/qxlies/satk-gta) into a folder named `tools` and run
   `scripts\bootstrap.ps1 -Deps`, which creates a Python 3.12+ venv. The Releases page also has the wheel
   `satk_gta-<version>-py3-none-any.whl` (not on PyPI yet) and `SHA256SUMS.txt` to check the downloads.
2. **Point it at your game:** `satk init` finds the game itself (registry, Steam libraries, usual folders) or takes
   `--game <folder>`, and writes `satk.toml`.
3. **Index the game:** `satk index build`, then try `satk asset find infernus` or `satk help`.

<!-- docs-smoke: skip needs a clone and the network -->
```powershell
git clone https://github.com/qxlies/satk-gta.git tools
powershell -ExecutionPolicy Bypass -File tools\scripts\bootstrap.ps1 -Deps
tools\satk.cmd init --game "<folder with gta_sa.exe>"
tools\satk.cmd index build
```

Everything about installing (zip, git, Python package, where `work` lives, Windows blocking unsigned files):
[docs/en/install.md](docs/en/install.md). From zero to the first screenshot: [docs/en/quickstart.md](docs/en/quickstart.md).

## What is inside

| Commands | What they do | Page |
|---|---|---|
| `satk game` | verify, protect and reproduce a clean game copy | [game.md](docs/en/game.md) |
| `satk formats` | parsers of IMG, DFF, TXD, COL, IDE, IPL, IFP, ZON, DAT | [formats.md](docs/en/formats.md) |
| `satk index`, `asset`, `world` | SQLite index of all assets and placements: search, references, SQL | [index.md](docs/en/index.md) |
| `satk texture`, `map image`, `model` | texture sheets, the top-down map, GPU-free model previews, export to glTF/OBJ | [media.md](docs/en/media.md), [models.md](docs/en/models.md) |
| `satk texture pack`, `rw`, `col`, `img` | texture mods, DFF/COL/IMG writing without Blender | [texmod.md](docs/en/texmod.md), [rw.md](docs/en/rw.md) |
| `satk asset lint`, `mod`, `id` | mod checks, what a mod changes, Mod Loader conflicts, free model ids | [lint.md](docs/en/lint.md), [modinspect.md](docs/en/modinspect.md), [idmgr.md](docs/en/idmgr.md) |
| `satk map convert`, `ipl`, `paths` | SA-MP/MTA/IPL map conversion, binary IPL, vehicle and ped paths | [mapconv.md](docs/en/mapconv.md), [paths.md](docs/en/paths.md) |
| `satk crash`, `re`, `kb` | crash dumps and logs, `gta_sa.exe` addresses to functions, engine knowledge base | [crash.md](docs/en/crash.md), [re.md](docs/en/re.md), [kb.md](docs/en/kb.md) |
| `satk view`, `saap` | a viewer with a camera, frames with numbered objects, "what is this object" | [viewer.md](docs/en/viewer.md) |
| `satk blender`, `engine` | headless Blender import/render/export to MTA; build of the MTA fork | [blender.md](docs/en/blender.md), [engine.md](docs/en/engine.md) |
| `satk status`, `doctor`, `help`, `mcp` | overview, diagnostics with fixes, help, the MCP server | [mcp.md](docs/en/mcp.md), [ai.md](docs/en/ai.md) |

Every page, by task: [docs/en/README.md](docs/en/README.md).

## Documentation

- User guide: [docs/en/README.md](docs/en/README.md) (Russian mirror: [docs/ru/README.md](docs/ru/README.md)),
  common tasks: [docs/en/workflows.md](docs/en/workflows.md), problems:
  [docs/en/troubleshooting.md](docs/en/troubleshooting.md).
- For AI agents: [docs/agent/SKILL.md](docs/agent/SKILL.md) (installed as the `satk` skill),
  [docs/agent/workflows.md](docs/agent/workflows.md), [docs/agent/errors.md](docs/agent/errors.md) and the generated
  tool reference [docs/agent/tools.md](docs/agent/tools.md).

## Development

How to work on satk (setup, one worktree per task, tests, rules): [CLAUDE.md](CLAUDE.md), the contributor guide
for people and AI agents. The acceptance check before a merge is `satk dev gate`; the tests alone:
`python -m pytest -q -p no:cacheprovider` in the venv.

## Acknowledgements

**A special thank-you to [Dryxio](https://github.com/Dryxio).** His open projects are the foundation of a large part
of satk and of our research into the game and its engine: the satk viewer is a fork of his Ariane, `satk paths` runs
his gta-flow core, `satk describe` imports the gta-scout model descriptions, `satk kb` reads his plugin-sdk-sa and
cleo-ai, and his MTA Neon fork is where we learned how the engine works. Thank you! His GTA projects:

- [ariane](https://github.com/Dryxio/ariane): a modern map editor for GTA III, Vice City and San Andreas; the viewer
  of `satk view` is a local fork of it.
- [mtasa-neon](https://github.com/Dryxio/mtasa-neon): an experimental MTA:SA engine fork with larger worlds, raised
  limits and new Lua features; studied in depth, the donor of our engine fork.
- [gta-scout](https://github.com/Dryxio/gta-scout): GTA-style models and scenes with AI and Blender;
  `satk describe import` imports its model descriptions.
- [gta-flow](https://github.com/Dryxio/gta-flow): traffic routes with AI and Blender; its core is vendored for
  `satk paths`.
- [plugin-sdk-sa](https://github.com/Dryxio/plugin-sdk-sa): the Plugin SDK with extra layout fixes; read by
  `satk kb` and `satk re`.
- [cleo-ai](https://github.com/Dryxio/cleo-ai): CLEO modding with AI; its opcode reference feeds `satk kb opcode`.
- [reagent](https://github.com/Dryxio/reagent): AI reconstruction and validation of C/C++ code from binaries; studied
  when we designed `satk re`.
- [ghidra-bridge](https://github.com/Dryxio/ghidra-bridge): AI access to Ghidra's analysis; the model for the
  agent-facing design of `satk re`.
- [samp-source](https://github.com/Dryxio/samp-source): a byte-for-byte rebuild of SA-MP 0.3.7 R5; studied for our
  multiplayer research.
- [samp-r5-rebuild](https://github.com/Dryxio/samp-r5-rebuild): the earlier from-scratch rebuild of the SA-MP
  0.3.7-R5 client DLL.
- [wiki.mtasa-neon.com](https://github.com/Dryxio/wiki.mtasa-neon.com): the Neon documentation, based on the MTA wiki.
- [skygfx](https://github.com/Dryxio/skygfx): his SkyGfx fork, the source of the MTA bridge that Neon builds.
- [gta-reversed](https://github.com/Dryxio/gta-reversed): his fork of the gta_sa.exe reimplementation.
- [mtasa-blue](https://github.com/Dryxio/mtasa-blue): his fork of Multi Theft Auto (he also sends fixes upstream).
- [library](https://github.com/Dryxio/library): his fork of the Sanny Builder Library.
- [fastman92_limit_adjuster](https://github.com/Dryxio/fastman92_limit_adjuster): his fork of the fastman92 Limit
  Adjuster source.
- [GTA-GPS-Redux](https://github.com/Dryxio/GTA-GPS-Redux): his fork of a GPS mod for San Andreas.
- [Radar-in-style-GTA-SA-The-Definitive-Edition](https://github.com/Dryxio/Radar-in-style-GTA-SA-The-Definitive-Edition):
  his fork of a Definitive Edition style radar.
- [dryxio](https://github.com/Dryxio/dryxio): his profile with the list of projects.
- [gtastuff.com](https://gtastuff.com/): 40+ browser tools for GTA modding, and Ariane's home page.

His other public projects are not about GTA: [DockDrop](https://github.com/Dryxio/DockDrop),
[tamago-life](https://github.com/Dryxio/tamago-life), [openclaw-lullabully](https://github.com/Dryxio/openclaw-lullabully)
and [ppp-rescue](https://github.com/Dryxio/ppp-rescue).

**Thanks also to the projects satk builds on or learned from:**

- [gta-reversed](https://github.com/gta-reversed/gta-reversed) (gta-reversed contributors): the reimplementation of
  gta_sa.exe 1.0 US; its function map is the backbone of `satk re` and `satk kb`.
- [Plugin-SDK](https://github.com/DK22Pac/plugin-sdk) (DK22Pac and contributors, Zlib): class layouts and addresses.
- [Multi Theft Auto](https://github.com/multitheftauto/mtasa-blue) (the MTA team, GPL-3.0): the base of our engine
  fork, and the [MTA wiki](https://wiki.multitheftauto.com/wiki/Main_Page).
- [librw](https://github.com/aap/librw) (MIT) and [librwgta](https://github.com/aap/librwgta) with euryopa (aap): the
  RenderWare reference behind `satk rw` and the base of Ariane, which builds with the
  [Southland-FR librw fork](https://github.com/Southland-FR/librw).
- [DragonFF](https://github.com/Parik27/DragonFF) (Parik and contributors, GPL-3.0): DFF and COL in Blender for
  `satk blender`.
- [rwfury](https://github.com/Hancapo/rwfury) (Hancapo, MIT): vendored in `vendor/rwfury`.
- [CrashInfo](https://github.com/JuniorDjjr/CrashInfo) (Junior_Djjr, MIT): the crash list in `data/crashlist`.
- [Mod Loader](https://github.com/thelink2012/modloader) (LINK/2012, MIT): its loading rules are ported into `satk mod`.
- [Sanny Builder Library](https://github.com/sannybuilder/library): the opcode data behind `satk kb opcode`.
- [Ghidra](https://github.com/NationalSecurityAgency/ghidra) (NSA, Apache-2.0): function bounds for `satk re`.
- [GTAMods wiki](https://gtamods.com/wiki/Main_Page) (CC BY 4.0): file format and engine descriptions.
- [INU Tools](https://github.com/INU-ez/INU_Tools-GTA-Blender) and [INU Check](https://github.com/INU-ez/INU_Check-GTA)
  (INU, GPL-3.0): studied; INU Check is an optional engine of `satk mod check`.
- [SilentPatch](https://github.com/CookiePLMonster/SilentPatch) (Silent), the
  [Open Limit Adjuster](https://github.com/GTAmodding/III.VC.SA.LimitAdjuster), the
  [fastman92 Limit Adjuster](https://www.fastman92.com/fastman92-limit-adjuster/),
  [Project2DFX](https://github.com/ThirteenAG/III.VC.SA.IV.Project2DFX) (ThirteenAG) and
  [SkyGfx](https://github.com/aap/skygfx) (aap): engine mods whose patches and limits we cross-check.
- [open.mp](https://github.com/openmultiplayer/open.mp) (MPL-2.0): custom model ranges for `satk id`; with the
  [SA-MP reconstruction](https://github.com/dashr9230/SA-MP) (dashr9230), [Slipe Server](https://github.com/mta-slipe/Slipe-Server)
  and [GameNetworkingSockets](https://github.com/ValveSoftware/GameNetworkingSockets), part of our multiplayer research.
- [Blender](https://www.blender.org/), [Python](https://www.python.org/), [Pillow](https://github.com/python-pillow/Pillow),
  [numpy](https://github.com/numpy/numpy), the [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
  and [pytest](https://github.com/pytest-dev/pytest).

Licence notices of the code and data satk ships: [NOTICE.md](NOTICE.md). Every source we used, grouped by topic and
with notes: [docs/en/credits.md](docs/en/credits.md).

## License

MIT ([LICENSE](LICENSE)). The exception is the Blender add-on `blender/satk_blender` (GPL-3.0-or-later, its own
`LICENSE`). Third-party components and their notices: [NOTICE.md](NOTICE.md).
