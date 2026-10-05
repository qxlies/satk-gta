# Install on any machine: `satk init`

[Русская версия](../ru/install.md)

<!-- Portability, the portable zip, the wheel and the first run. One concrete layout of it: workspace-gta.md.
     Checked 2026-10-05. -->

Package: `satk.core.config`, `satk.core.detect`, `satk.runtime.setup`, `satk.runtime.location`.

## What it is

satk needs neither a particular folder layout, nor the MTA fork, nor Ariane. It needs 64-bit Windows 10/11 and
**any** folder with the PC GTA San Andreas (1.0 US, Steam, with your own mods: it does not matter). The Definitive
Edition and the mobile port are not supported: `satk init` lists them as unsupported candidates. The game is only
ever read; everything satk creates lives in `<workspace>\work\`. satk uses no network by default: downloads
happen only on explicit commands (`bootstrap.ps1 -Deps`, `satk engine setup`, `satk dev release`).

Three ways to get it:

- **Portable zip** (no Python, no git, no admin rights): `satk-<version>-win64.zip` from the
  [Releases page](https://github.com/qxlies/satk-gta/releases) (or built with `satk dev release`). Unpack it into
  a folder you own (not Program Files, not the game folder) and double-click `Start satk.cmd`: the first time it
  runs `satk init`. That folder is the workspace. Details: [release.md](release.md).
- **A git checkout** with five commands (below): the way to work on satk itself.
- **A Python package** in your own venv: `py -3.12 -m pip install -e tools` (editable, reads `data\` and
  `vendor\` from the checkout; needs setuptools, offline: `--no-build-isolation` with setuptools already
  installed) or the wheel `satk_gta-<version>-py3-none-any.whl` from the Releases page (PyPI name `satk-gta`,
  not on PyPI yet; the command stays `satk`).

## Install in five commands

<!-- docs-smoke: skip needs a clone and the network -->
```powershell
git clone https://github.com/qxlies/satk-gta.git tools
powershell -ExecutionPolicy Bypass -File tools\scripts\bootstrap.ps1 -Deps
tools\satk.cmd init --game "<folder with gta_sa.exe>"
tools\satk.cmd index build
tools\satk.cmd mcp config --write
```

1. `git clone` anywhere. If the folder that holds `tools` also has `work\` or `satk.toml`, that parent of `tools`
   is the workspace. Otherwise the workspace is `%LOCALAPPDATA%\satk` or what you give to
   `satk init --workspace <folder>`.
2. `bootstrap.ps1 -Deps` creates `tools\.venv` with the first of Python 3.12, 3.13, 3.14 that the `py` launcher
   has (or `-PythonVersion 3.13`) and installs the run-time packages Pillow, numpy and mcp from the network.
   `-Dev` adds pytest, the git pre-commit hook and the lock file (for working on satk). The pip cache and the
   temporary files go into `<workspace>\work`. It works from any folder; in a git worktree it uses the venv of
   the main checkout.
3. `satk init` looks for the game itself: the Rockstar registry, Steam libraries (`libraryfolders.vdf`),
   `Program Files`, the workspace folders and the current folder. A game folder has `gta_sa.exe` (Steam:
   `gta-sa.exe`), `models\gta3.img` and `data\gta.dat`. A single candidate is taken at once; with several you get a
   question in a terminal, and without a terminal the error `AMBIGUOUS` with the list (choose with `--game`).
   It also finds Blender, MSBuild (`vswhere`) and Ariane. Everything found is written to `<workspace>\satk.toml`.
   A workspace inside the game folder is refused; a workspace in OneDrive or Program Files, a game in OneDrive or
   with VirtualStore copies, and less than 1 GB free give warnings.
4. `satk index build` builds the index of the `game` profile (your game folder).
5. `satk mcp config --write` registers the MCP server `satk` for Claude Code in `<workspace>\.mcp.json`; other AI
   clients: `satk mcp config --client <name>`, see [ai.md](ai.md).

## Quick example

```powershell
satk init --dry-run
satk config show --section index
```

`satk init --dry-run` writes nothing: it shows the candidates with their `gta_sa.exe` variant, the tools it found
and the `satk.toml` it would write (a diff when it differs from the existing one).

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk init [--game DIR] [--workspace DIR] [--clean-copy] [--yes] [--dry-run]` | — | first-time setup, writes `<workspace>\satk.toml` |
| `satk doctor [--only a,b] [--deep]` | — | checks with a `fix` each: Python, packages, paths and their sources (`config`, `env`, `detect:vswhere`…), profiles, disk, tools |
| `satk game info [--root DIR]` | — | exe variant (`1.0 US HOODLUM`, `Steam`…), `re_supported`, files that are not stock |
| `satk config show [--section paths]` | — | the effective configuration and where the workspace came from |

## How it works

- **Workspace:** `SATK_HOME` → `paths.workspace` from the `satk.toml` found → the folder of a portable zip →
  the parent of the `tools` checkout (a git worktree uses its main checkout through `.git` → `gitdir` →
  `commondir`) → `%LOCALAPPDATA%\satk`.
- **Finding `satk.toml`:** `SATK_CONFIG` (a file or a folder) → `<checkout>\satk.toml` → `<main checkout>\satk.toml`
  → `<workspace>\satk.toml` → `%APPDATA%\satk\satk.toml`. When `init --workspace` picked a folder that satk would
  not find by itself, the last file gets a pointer `[paths] workspace = '…'`. A portable installation reads only
  its own folder.
- **Tools:** `paths.blender`, `paths.msbuild` and `paths.vcvars` have no defaults: when `satk.toml` does not set
  them, satk looks for them and caches the result in `work\cache\detect.json` (for a day). A value in `satk.toml`
  always wins. `SATK_DETECT=0` turns the search off.
- **Profiles:** `game` = `paths.game_root` (your game). A clean copy (`gta-sa-clean`, made by
  `satk init --clean-copy`, stock 1.0 US only) gives the `vanilla` profile; without it `vanilla` is an alias of
  `game`, and satk warns `NO_CLEAN_COPY`: without a clean copy the layers vanilla and modded cannot be told apart.
  `installed` and `samp` without their own folder show as "not configured" (`satk index status`), not as errors.
- **Write protection:** the game folder, `installed`, `src` and the clean copy are always protected; the default
  `safety.protected_roots` lists only the ones that exist.
- **Python:** satk runs on CPython 3.12–3.14 (`satk doctor`, check `python`); the shims run it with `-X utf8`.
  Development and `satk dev gate` use 3.12.
- **Exe versions:** `data\exe_versions.json` holds only confirmed hashes (1.0 US HOODLUM, its no-intro patch, the
  MTA variants, Compact); other versions (1.0 EU, 1.01, Steam) are recognized by plugin-sdk signatures or by size
  and marked `heuristic`. The index, textures, models and Blender work with any version; `satk re` expects the
  1.0 US layout, and on another version `re addr` warns `UNSUPPORTED_EXE`.

## Limitations and known issues

- The Ariane viewer and the MTA fork are optional; without them the `view` and `engine` commands answer
  `NOT_READY`.
- A wheel installed with `pip` (not `-e`) or `pipx` carries `data\` (manifests, `exe_versions.json`) as
  `satk/_data`, but the vendored code is not found from it yet: `satk paths` (gta-flow) answers `DEPENDENCY`.
  Use the checkout or the portable zip for it.
- Smart App Control can block the unsigned extension modules of numpy, Pillow and the MCP server; `satk doctor`
  (check `app_control`) says so. See [troubleshooting.md](troubleshooting.md) for this and other problems.
