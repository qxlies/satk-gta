# Changelog

Notable changes of satk. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning](https://semver.org/); before 1.0 anything may change.
The release zip ships this file as `CHANGELOG.md`.

## [Unreleased]

## [0.3.0] - 2026-10-06

Public preview. The second public version: new assets in the San Andreas style made in Blender, many more modding
tools (add-ons and data patches, CLEO scripts, MTA resources and shaders, animations, world data files, collision,
2DFX, texture optimisation, engine limits, batch runs), and checks of your own model in the viewer and in the real
game. Commands, answers and file layouts can still change before 1.0.

### Added

#### Asset authoring in Blender

- `satk style`: the vanilla style in numbers. Profiles per class (cars by body type, map models by kind and size,
  peds, weapons, pickups, upgrades) with p10/p50/p90 bands of one shared metric registry, in two tiers: `vanilla`
  and the higher-detail `sa_plus` (its numbers are a proposal until they are checked in game; every answer says
  so). Markdown cards, texture checks by role and a brief checker. `satk asset check` compares a DFF, a mod folder
  or a game model with its class (structure, semantic and band rows, with the `col check` and `fx2d check`
  summaries); `satk asset anatomy` shows how a model is built.
- `satk blender session`, `blender call` and `blender methods` (studio): a live Blender 5.1 session driven step by
  step (mesh, modifier, UV, material, camera and reference methods) with numbers and an optional snapshot per step,
  checkpoints and a replayable journal. `satk asset init` and `asset status` keep a project card with gates;
  `satk ref import` prepares reference photos.
- `satk kit`: templates for 20 asset kinds made from a vanilla model (frames, dummies, part slots, material presets,
  a collision skeleton), Blender generators (shading, wheels, vehicle LOD, damage, map LOD, collision, UV regions,
  bakes) and `kit export` to a Mod Loader folder or an add-on through `mod add`. A map model leaves with ray-cast
  prelight, a primitive collision chosen by the class rule, its LOD DFF and, with `--place`, the IPL pair.
  `blender game-ready --asset-class --tier` takes its defaults from the class.
- `satk kit blank`: a clean low-poly quad base for automobile, bike, boat, heli, plane, prop and building kinds,
  built from class numbers (loops where parts are cut, UV seams, tier density) and cut into the kit slots by the
  Blender method `kit.blank_split`. The help topic `creation` gives the shortest correct path for each kind.
- `satk blender preview` (look): a game-like picture (alpha glass, lit lamps, dirt, the sheen, prelight by time of
  day) of your own files, mod folders, game models or a live session, side by side with vanilla peers on one sheet.
- `satk asset convert` (steps `convert prepare` and `convert finish`): brings a rigid third-party model into the
  San Andreas style: measured scale and triangle budget, baked small textures, a kit package, checks and a lineup
  beside class peers.
- `satk texlib`: procedural, tileable San Andreas style textures (20 presets) and a search for vanilla textures a
  map object can reference without copying pixels. `satk texture new`, `texture finish` (photo-like noise,
  occlusion, grime and edge masks with a DXT preview, landing in the texture band of its role) and
  `texture pack --asset-class`.
- Rules: `satk asset lint --preset vanilla|sa_plus --baseline vanilla` suppresses the rules the stock game also
  breaks and adds vehicle rules (frames, damage parts, paint and lamp keys, the env-map UV, dummies, shading);
  `satk rw patch --smooth-normals --recalc-bsphere`.
- Guides: user pages on the San Andreas style and on authoring; for AI assistants, style guides and help topics
  (`satk help style`, `style_vehicle`, `style_world`, `style_ped_weapon`, `style_shading`, `style_texture`,
  `authoring`, `visual_qa`, `creation`), an asset brief template and creation workflows.

#### Other modding

- Add-ons and data patches: `satk mod add` makes a new vehicle, ped, weapon or object a Mod Loader folder, modelled
  on a donor (its data lines with a free id, new names, handling id and GXT key; checks of ids, names and lengths;
  a warning when the game needs the fastman92 Limit Adjuster); `mod add object` also takes `--lod-dff`,
  `--lod-txd`, `--lod-name` and `--place`. `satk data get`, `data explain` and `data patch` read one record of
  handling.cfg, carcols.dat, peds.ide or weapon.dat with units and meanings and write field changes as a Mod Loader
  readme, an MTA Lua snippet or a patched copy.
- CLEO and SCM: `satk script disasm` (byte-exact round trip, `main.scm` and `script.img` included), `script asm`
  with line-numbered errors, `script check` (jumps, loops without wait, missing terminate, condition counts) and
  `script new` templates (hello, cheat, spawn car, teleport, text, mission). `satk kb mta` and `kb native` look up
  MTA Lua functions, events, OOP classes and enums and SA-MP/open.mp natives offline.
- MTA tools: `satk mta lint` reads a resource folder, a `.zip`, a `meta.xml` or one script and reports what would
  break on a server (Lua 5.1 syntax errors with the server's own message, unknown or wrong-side functions, wrong
  arguments, events nobody adds, render-loop mistakes, missing files, download size); `mta resource new` writes
  starter resources, `mta pack` turns DFF/TXD/COL files into a resource that replaces stock models or requests new
  ids, `mta logs` explains server and client logs, and `mta server-check` lets the MTA fork's server load a
  resource (127.0.0.1, no game client).
- MTA shaders: `satk shader textures` (world texture names by pattern, kind, model or area, with an exact pattern
  list), `shader new` (car paint, texture replacement, wet roads, water, sky tint, ped shading, post-process) and
  `shader check`.
- Single-player bridge: `satk sp` and the ASI plugin `satk_sp.asi` serve the SAAP/1 protocol from the
  single-player game (camera, screenshots, picking, entity queries). Its transport is tested offline
  (`satk sp selftest`); building it needs the Visual Studio C++ build tools, and `sp start` / `sp stop` need your
  consent and a test copy of the game.
- Animations: `satk anim` lists IFP packages, extracts animations to editable JSON, writes ANP3, ANP2 or ANPK,
  merges into packages such as `ped.ifp`, checks against a skeleton (the game's or MTA's loader), makes an MTA
  resource that replaces game animations and moves animations to and from Blender; every vanilla IFP comes back
  byte for byte.
- World data writers: `satk gxt`, `fxt`, `zone` (`info.zon`, `map.zon`), `water`, `timecyc`, `popcycle` and
  `radar` read these files with the engine's rules and write edits back as Mod Loader folders; unchanged data gives
  the same bytes.
- Collision: `satk col gen` (box, boxes, convex hull, decimated mesh, vehicle spheres with a shadow mesh),
  `col check`, `col surface` and `col derive` (surfaces chosen from texture names).
- 2DFX: `satk fx2d` dumps, applies, copies and checks the 2dEffect entries of DFF models (lights, particles, ped
  attractors, entry/exits, road signs, ...) as JSON; the round trip is exact on every vanilla DFF that has them.
- TXD optimisation: `satk texture audit` (problems sorted by bytes saved), `texture optimize` (DXT recompression,
  size caps, mips, unused and duplicate textures, PSNR per texture) and `texture budget` (the streaming memory of a
  profile, a mod or an area against the engine limit).
- Limits: `satk limits plan` compares the demand of the installed game plus extra mods with the engine capacities
  and writes fastman92 and Open Limit Adjuster INI fragments.
- Batch and recipes: `satk batch` runs one operation over many inputs (globs, IMG entries, list files, folders,
  index queries) with resumable JSONL results; `satk recipe` runs saved multi-step recipes (check a mod before
  release, lint and pack many cars, export all textures of a mod, crash triage).

#### Checks in context

- Viewer scene: `satk view place`, `view vehicle`, `view ped`, `view reload`, `view remove` and `view list` put
  your own models, vehicles (paint, dirt, lamps, damaged parts) and posed peds next to the map in the viewer; with
  `--watch` a re-exported file shows in about half a second. They need a viewer build with the `scene.*` methods
  (section 11 of the SAAP/1 text); the viewer is not part of the release, and other targets answer `UNSUPPORTED`.
- In-game loop: `satk ingame` tests a mod in the real game on the MTA fork: a loopback test server with the mod,
  `reload` without reconnecting, `spawn` and `drive` at test spots, scripted `check` suites (vehicle, object, ped,
  weapon) with a verdict and a mod-versus-vanilla frame per check, `shot` and `logs`. It is tested offline; the
  client needs a one-time administrator setup, and satk never starts the game itself (`ingame play` is for you).

#### Finding things and engine knowledge

- `satk ops <words>`, `satk help --find <words>`, and the global options `--out FILE`, `--json-out FILE` and
  `--summary`.
- `satk re nodes` (the vehicle and ped frame tables of the exe), `re limits --summary`, `re src` and `re find`
  with knowledge-base fallbacks and close-name suggestions; `formats dump` with paging, model SIDs and paths
  relative to the current folder; engine and authoring facts in `satk kb fact`; capacity rows in `satk mod check`
  and low-slot warnings in `satk id free`.
- Documentation: a page for every new component (English, with a Russian mirror), new tasks in the user guide
  index and on the common tasks page; for AI assistants, workflows S17-S26 and the matching skill sections.

### Changed

- `satk texture audit` no longer reports `no_mips` for TXDs of vehicles, peds, weapons and vehicle upgrades (their
  stock textures have one level); the field `one_level` counts those TXDs by class.
- Agent docs: the operation names and flags in the skill, the workflows, the style guides and the help topics were
  checked against the registry, and the band tables carry the numbers of `satk style profile`.
- Blender sessions: every step has a time budget (`blender call --timeout`, 90 s; `blender methods` 60 s; a session
  preview 60 s, a cold one 300 s). A step that runs too long answers `TIMEOUT` and the session stays usable; a
  session stuck inside Blender answers `TIMEOUT`, then `BUSY` until it is free, and `session stop` ends it; a
  session that died is reported with the path of Blender's crash report. A batch of two or more mutating steps
  writes one checkpoint (`--no-checkpoint` skips it). Snapshots take `--look game`; `blender methods --query
  <name>` returns the parameter table of one method (all of them: `docs/agent/studio-methods.md`).
- `satk blender preview session:NAME` takes the like model, kind and dimensions from the project's `asset.json`;
  `--lineup class` picks peers from the style peer set; session scenes get the wheel on every wheel dummy and the
  like vehicle's paint. Session step stats judge against the project's tier bands with the same code as
  `asset check`.
- `satk asset check`: no `part_missing` rows for map models, a new (not replacing) vehicle is only informational,
  and `edge` rows show by default with the tier band in their hints. `uv.fit` no longer keeps the aspect ratio
  unless asked (`keep_aspect`), and `uv.texel` takes the texture size as `px`.
- The mock viewer target answers the `scene.*` methods too (placed entities are drawn as boxes).

### Fixed

- A live Blender session no longer hangs or crashes on the next preview after `kit lod` or any step that left
  copies of materials from an evaluated mesh behind: the session puts the original materials back after every
  method.
- `kit vlo` for chassis made from a blank: when collapsing cannot reach 130 triangles it drops the smallest closed
  islands and reports them.

### For developers

- `satk dev gate --changed`: the cheap steps plus the tests affected by the changed files, each with its reason
  (`--with-game` adds the game steps, `--dry-run` shows the plan); it is not the acceptance. At most two full gates
  run per machine (`SATK_GATE_SLOTS`), each in its own work and TEMP folders that a green run frees; every step's
  complete output is kept, and a failed step names its log file.
- `satk dev gate` runs inside a restricted sandbox (no multiprocessing pipes, no POSIX shell, owner-only temp
  folders): index scans fall back to in-process work (`SATK_JOBS=<n>` sets the workers, 1 = one process) and
  tests that need what the sandbox denies skip with a `sandbox:` reason.
- `satk dev gen-docs` also writes `docs/agent/studio-methods.md`; the generated references leave out operations
  whose code is not published (`data/public-exclude.txt`).
- pytest keeps the temp folders of failed tests only.

## [0.2.1] - 2026-10-05

Public preview: the first version published at https://github.com/qxlies/satk-gta, with the portable zip, the
wheel, the sdist and `SHA256SUMS.txt` attached to the GitHub release.

### Added

- GitHub issue and pull request templates and a CI workflow on GitHub Actions.
- Public export: the public repository gets one snapshot commit per release, built from the development
  repository without its history.
- `tests/docs/test_public_text.py`: the READMEs, `NOTICE.md`, this changelog, the user and agent docs, the
  protocol text, the packaging texts and the contributor guide carry no machine-specific paths and no
  references to internal planning documents.

### Changed

- Documentation cleanup for the public preview: example paths are written as `<workspace>\…` or relative to
  the workspace, internal plan, work-package and research-report references are gone, and the README and the
  install guide point to the GitHub repository and its Releases page.
- `CLAUDE.md` is now a general contributor guide (development setup with placeholders, the same rules).
- `NOTICE.md` describes the reference-only sources without local paths.
- The test suite runs in any checkout (CI included): write-protection tests use an isolated workspace and
  sample paths are neutral. Messages and generated file headers no longer name internal work items.
- `satk dev gate` skips the agent-docs check in a workspace where no agent docs were ever published.
- `satk game verify --against install` without `--manifest` reports `NOT_FOUND` with a hint when the install
  audit is not part of this copy of satk (it describes the maintainers' own install and is not published).

## [0.2.0] - 2026-10-05

Private preview.

### Added

- Portable Windows release: `satk dev release` builds `satk-<version>-win64.zip` (official embeddable
  CPython 3.12, pinned runtime wheels, launchers, `README-FIRST.txt`), the `satk-gta` wheel and sdist,
  and `SHA256SUMS.txt`, then smoke-tests the unpacked zip in a path with Cyrillic letters and spaces.
- Portable mode: a `portable.txt` next to the launchers makes that folder the workspace; the per-user
  configuration is not read, and a `workspace` line in the folder's `satk.toml` cannot point elsewhere.
- `satk doctor` check `portable`: the folder is writable, not blocked by Windows (Mark of the Web), and
  the code matches `RELEASE.json`.
- `satk doctor` check `app_control`: reports when Smart App Control blocks the unsigned extension modules
  of numpy, Pillow and pydantic-core.

### Changed

- The package name for PyPI is `satk-gta` (the command stays `satk`).

## [0.1.0] - internal

First internal version, used from a git checkout.

### Added

- Asset index of a game installation (IMG, DFF, TXD, COL, IDE, IPL and data files) with stable IDs and
  layers (vanilla, SA-MP, modloader mods, other changes).
- Texture and model previews, map images, control of the offline viewer, crash address lookup in
  `gta_sa.exe`, Blender import and export, helpers for the MTA fork.
- MCP server for AI agents, `satk init`, `satk doctor`, `satk help`.
- Texture packing (DXT, TXD writer), map converters (SA-MP Pawn, MTA `.map`, IPL), path nodes, asset
  linter, crash dumps, game-ready export from Blender, offline HTML catalog, extended index (water,
  timecyc, handling and more), model descriptions, knowledge base of engine sources, in-game agent for MTA.

[Unreleased]: https://github.com/qxlies/satk-gta/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/qxlies/satk-gta/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/qxlies/satk-gta/releases/tag/v0.2.1
