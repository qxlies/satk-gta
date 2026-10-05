# The full development workspace: one case of the general setup

[Русская версия](../ru/workspace-gta.md)

<!-- How satk finds this layout without a single path in the code. Checked 2026-10-05. -->

## What it is

The maintainers work in one folder, the *workspace*, that holds the satk checkout next to the game copies, the
donor source clones and the optional forks. Some pages show answers from such a workspace and write its paths as
`<workspace>\…`. The layout is not a requirement of satk, just one concrete case; the general setup is
[install.md](install.md).

| Folder | Configuration key | Profile |
|---|---|---|
| `<workspace>` | `paths.workspace` | — |
| `<workspace>\tools` | the repository checkout (`satk.cmd`, `.venv`) | — |
| `<workspace>\gta-sa-clean` | `paths.game` (the clean 1.0 US copy) | `vanilla` |
| `<workspace>\GTA San Andreas` | `paths.installed` = `paths.game_root` (the installed game with its mods) | `installed`, `samp`, `game` |
| `<workspace>\work` | `paths.work` | — |
| `<workspace>\src` | `paths.src` (donor clones, read-only) | — |
| `<workspace>\viewer\ariane` | the Ariane fork (optional, built locally) | — |
| `<workspace>\engine\mtasa` | the MTA fork (optional) | — |

## Quick example

```powershell
satk config show --section paths
satk doctor --only paths
```

`satk config show` prints the paths and `"sources":["defaults"]`: no `satk.toml` and no `SATK_*` variables are
involved. `satk doctor --only paths` prints `workspace <workspace> (checkout)` when run from `<workspace>\tools`
and `(main-checkout)` from a worktree, then the work folder, the game and the number of protected roots. Where
Blender and MSBuild came from is shown by the checks `blender` (`detect:program_files`) and `msbuild`
(`detect:vswhere`): `satk doctor --only paths,blender,msbuild`.

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk config show` | — | the paths of this layout without any `satk.toml` |
| `satk init --dry-run` | — | candidates `GTA San Andreas` (hoodlum-nointro) and `gta-sa-clean` (hoodlum-stock) |

## How it works

- Such a workspace needs no `satk.toml`. The workspace follows from where the code is: the checkout is called
  `tools` and has `work\` next to it, so the workspace is the parent folder of `tools`. A worktree
  (`<workspace>\work\wt\<name>`) finds the main checkout through `.git` → `gitdir` → `commondir` and uses the same
  workspace and the same `tools\.venv`.
- Blender 5.1 and MSBuild of VS2026 are not written in the code: they are found in `Program Files` and with
  `vswhere -prerelease`.
- `paths.game_root` defaults to `paths.installed`; `[index] default_profile` is `vanilla`.
- The hard floor of write protection (`GTA San Andreas`, `src`, `gta-sa-clean` of this workspace) also follows
  from where the checkout is and cannot be lifted by configuration.
