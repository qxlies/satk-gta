# If something does not work

[Русская версия](../ru/troubleshooting.md)

<!-- cp1251, minimized window, MSAA, disk space, "index is stale" and what came up while testing the packages.
     Re-checked 2026-10-05; the error texts are real. -->

The first step is almost always the same: `satk doctor`. It runs every check (27 in a full development
workspace) and gives each problem a command that fixes it (`fix`). An overview of the state is `satk status`. Error
and warning codes are listed in [docs/agent/errors.md](../agent/errors.md). To report a problem with satk itself,
`satk bug-report` writes a private local report ([bugreport.md](bugreport.md)).

## Garbled text in the console (cp1251)

The Windows console uses cp1251 by default. The shims `satk.cmd`/`satk.sh` set `PYTHONUTF8=1` and start Python
with `-X utf8`, so satk's output is correct in any console code page (characters the code page lacks are
replaced).

- Run your own scripts the same way: `py -3.12 -X utf8 script.py`, and always `encoding="utf-8"` in the code.
- If *other* programs print garbage in PowerShell: `chcp 65001` in that session.
- PowerShell 5.1 `Set-Content -Encoding utf8` and `Out-File -Encoding utf8` write a BOM at the start of the file;
  plain `json.load` then fails on that JSON (it needs `utf-8-sig`).

## Search finds nothing

- `satk asset find grove` returns only the **exact** names (9 `grove` textures); `warn` says how many other
  names contain the string (`MORE: 15 other names contain 'grove'`). For a substring use a pattern:
  `satk asset find "*grove*"`.
- In **cmd.exe** put the pattern in double quotes: cmd passes `'*grove*'` in single quotes together with the
  quotes, and the search silently returns 0 rows. PowerShell and Git Bash accept both kinds of quotes.
- Zones are named by the codes of `info.zon`/`map.zon` (`GAN1` is Ganton, `VE` is Las Venturas), and their in-game
  names are in `title`: `satk asset find ganton --kind zone` finds `zone:gan1`. There is no zone called `grove`:
  Grove Street is near (2495, −1687); save it as a viewer bookmark with
  `satk view bookmark save grove_center` while the camera is there.
- Through MCP the search parameter is called `query`, not `q` (`BAD_PARAMS: asset.find: unknown parameter 'q'`).

## CLI: `the following arguments are required`

A list option (`--fields`, `--pos`, `--look`, `--kinds`, `--layers` …) takes every value that follows it,
including positional arguments. Required positionals are given back automatically
(`satk asset get --fields name,sec model:411` works), optional ones are not: `satk world near --kinds inst 2495 -1687`
→ `BAD_PARAMS: kinds must be one of inst, item, zone, water, got '2495'`. Write positional arguments **first**:
`satk world near 2495 -1687 --kinds inst`.

## Viewer: `NOT_READY`, a black or empty frame

- **Not running.** `NOT_READY: target 'ariane' is not running` → `satk view start --target ariane`
  (2–3 s, the window opens in the top left corner).
- **Window minimized.** D3D9 draws nothing in a minimized window; `satk view status` answers `NOT_READY` with a
  hint. Restore the window (it may stay small and behind other windows).
- **Frame of the wrong size.** Our Ariane fork (`"proto":"saap/1"`) captures at `--width`/`--height` (960x540 by
  default) whatever the window size. An older build (`"proto":"ariane-ipc/1"`) always captures the window
  (`warn: capture size is the window size 1280x720`); start it with `satk view start --window 960x540`.
- **MSAA.** With antialiasing on, a capture could come out black. satk starts Ariane with MSAA off;
  an empty frame is an error, not a black PNG.
- **Holes in the frame, buildings not loaded.** Streaming was not done yet: the answer has `settled:false`.
  Capture again.
- **`view_pick` finds nothing.** Our fork picks what is drawn (`pick.visible`), so even a palm crown without
  collision is hit. The adapter of older builds picks by collision (`mode:"collision"`): objects without COL
  (some vegetation, wires) are not hit.
- **The `mock` target.** Without `satk view mock` it works inside the process (`warn: mock endpoint not running …`).
  Only the placement `inst:lae2_stream0#4` in its world is real; the `inst:mock*` objects are synthetic, and
  `satk asset get` answers `NOT_FOUND` for them.
- **`--target game`.** satk does not start the game itself: `UNSUPPORTED: satk cannot start target 'game'`.
  Start the MTA server and client with the `satk-agent` resource as described in [mta-agent.md](mta-agent.md).
- The viewer log: `<workspace>\work\logs\viewer-ariane.log`.

## "Index is stale" (`INDEX_STALE`) or `INDEX_MISSING`

The index is the file `<workspace>\work\index\<profile>.sqlite`. It remembers the sizes and modification times of
the source files; when something changed, answers come with the warning `INDEX_STALE: …`. Without an index,
textures, the map and models answer `INDEX_MISSING` (exit code 3). An index of an older schema also gives
`INDEX_MISSING: … has schema v2, this satk needs v3`.

```powershell
satk index status
satk index build --profile vanilla
```

A profile builds in about 8 s; the `installed` and `samp` profiles are built separately (`--profile installed`,
`--profile samp`).

## `PROTECTED_PATH` and `--out must be inside the work directory`

This is not a bug but protection: satk never writes into your game folder (`paths.game_root`, `paths.installed`)
or the source clones (`paths.src`), and into the clean copy (`<workspace>\gta-sa-clean`) only through `satk game`
commands. Exports and other `--out` options are allowed only inside `<workspace>\work\` (otherwise `BAD_PARAMS`); it
is simpler not to give `--out` at all, and the files then go to `<workspace>\work\out\…`.

## `satk init` does not take the game folder

- `AMBIGUOUS: 2 GTA San Andreas folders found` → choose one: `satk init --game <folder>` (`satk init --dry-run`
  lists the candidates).
- `UNSUPPORTED: … holds GTA San Andreas - The Definitive Edition` (or the mobile port): satk reads only the
  classic PC game (`gta_sa.exe`, `models\gta3.img`, `data\gta.dat`).
- `BAD_PARAMS: the workspace … is inside the game folder`: satk never writes into a game folder; pick a workspace
  outside it with `satk init --workspace <folder>`.
- Warnings `WORKSPACE_ONEDRIVE`, `WORKSPACE_PROGRAM_FILES`, `GAME_ONEDRIVE`, `GAME_VIRTUALSTORE`, `LOW_DISK`:
  satk works, but OneDrive sync, administrator rights, redirected copies of game files or a full disk will get in
  the way; the warning says what to do.

## An MTA crash dump is not resolved

`satk re addr` with text where `Module = …gta_sa.exe` and `Offset = 0x…` are glued into one line answers with
module `?` and `confidence: none`. Keep the dump lines as they are (each on its own line) and pass them as a file:
`satk re addr --text-file crash.txt`, or give the address yourself: `satk re addr gta_sa.exe+0x13BF09`. A bare
offset below `0x400000` (`satk re addr 0x0013BF09`) is an offset inside the module, not an address: the answer is
`address outside the gta_sa.exe image`. If `re addr` answers `NOT_READY`, the database is not built:
`satk re build` (about 10 s). For whole minidumps and `core.log` see [crash.md](crash.md).

## `DEPENDENCY`: no Pillow / numpy / mcp

The core works without them, but fast DXT, the software renderer and the MCP server do not.

```powershell
powershell -ExecutionPolicy Bypass -File tools\scripts\bootstrap.ps1 -Deps
```

The packages come from the network (~70 MB), the cache is `<workspace>\work\cache\pip`. When everything is
already installed, the command downloads nothing (about 2 s). `satk paths` answers `DEPENDENCY` for another
reason: it needs the vendored gta-flow of a checkout or of the portable zip ([paths.md](paths.md)).

## Windows blocks numpy, Pillow or the MCP server

- **Smart App Control.** The extension modules inside PyPI wheels are not code-signed, and where Smart App Control
  is on, Windows refuses to load them: the index, search and exports work; previews, the fast PNG/DXT paths and the
  MCP server do not. `satk doctor` (check `app_control`) says so. Changing Smart App Control is your decision
  (some Windows versions cannot turn it back on without a reinstall); satk never touches it.
- **Portable zip downloaded from the internet.** Windows marks the unpacked files, and starting them brings
  security prompts. `satk doctor` (check `portable`) gives the fix: `Unblock-File` on the folder.

## Not enough disk space

- Everything heavy (indexes, the PNG cache, MTA builds, MSBuild and pip temporary files) lives in the workspace, in
  `work\` and `engine\`. `work\` can be deleted completely **except** `work\notes.sqlite` (run `satk note export` first).
- The MTA fork with two builds takes about 6.8 GB; `engine\mtasa\Build\obj` (about 4.7 GB) can be deleted, it is
  rebuilt. `satk engine doctor` warns when its drive has less than 12 GB free.
- PNGs of all textures (`satk texture export-all`) take about 420 MB in `work\cache\tex`.
- When the system drive is short of space, point TEMP/TMP of MSBuild and pip to `<workspace>\work\tmp`
  (`bootstrap.ps1` already keeps the pip cache and its temporary files under `<workspace>\work`).

## The wrong Python

satk runs on CPython 3.12–3.14 (`satk doctor`, check `python`). A bare `py` starts the newest installed Python,
while the shims always use the venv of the checkout (`tools\.venv`, made by `bootstrap.ps1`); development and
`satk dev gate` run on 3.12.
No venv: run `bootstrap.ps1` (without `-Deps` it works without the network).

## The satk MCP server is not visible in the AI client, or `selftest failed`

- For Claude Code the server is registered in `<workspace>\.mcp.json` (`satk mcp config --check` compares the
  file, `--write` writes it); Claude Code asks for permission the first time a session starts in the workspace
  folder, and `/mcp` shows the state. Other clients: `satk mcp config --client <name>`, see [ai.md](ai.md).
- A check without any client: `satk mcp selftest` runs a real stdio session (now `"ok":true`, `list_bytes` 15 727 of
  16 384). If it ends with `INTERNAL: mcp selftest failed: list_budget`, the tool descriptions grew over the
  budget: the server itself works and so do the tools, but the package owners must shorten the descriptions.
  A narrower set: `SATK_MCP_GROUPS=core,index,media,view`.
- The server does not start: `satk doctor` (checks `deps`, `ops_import`, `app_control`), then
  `<workspace>\work\logs\mcp.log`.

## Quick example

```powershell
satk version
satk config show --section paths --table
satk doctor --only python,utf8,deps,disk --table
satk index status
```
