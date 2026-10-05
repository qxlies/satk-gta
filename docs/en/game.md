# `satk game`: the clean game copy

[Русская версия](../ru/game.md)

Package: `satk.game`.

## What it is

`satk game` looks after the clean copy of the game (`paths.game`, by default `<workspace>\gta-sa-clean`): it
verifies it, protects its IMG archives from writes, recreates the copy from the original install and writes the
`gta_sa.exe` variant you need. The original install (`paths.installed`) is only read: no command writes into it.

A verified clean copy has 416 files, 5,029,186,364 bytes and the stock 1.0 US HOODLUM `gta_sa.exe`. It is made
by `satk init --clean-copy` (stock 1.0 US games only) or by `satk game clone`.

## Quick example

```powershell
satk game info --table
satk game verify
```

What comes back from `verify` (abridged):

```json
{"ok":true,"root":"<workspace>/gta-sa-clean","files":416,"bytes":5029186364,"mismatch":0,"missing":0,"extra":0,"exe":"hoodlum-stock","manifest":"ok","protected":"8/8","mode":"fast","hashed":376,"fast":40,"seconds":0.1}
```

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk game verify [--root R] [--deep] [--against stock\|install] [--scope stock\|all]` | `satk_op` | Compares a folder with a manifest. Fast by default: IMG and `audio\` by size and mtime, the rest by SHA-256 (about 0.2 s with a warm cache). `--deep` hashes all 5 GB (about 3-10 s on NVMe) |
| `satk game info [--root R]` | `satk_op` | `gta_sa.exe` variant and layout, `vorbisFile.dll`, ASI count, non-stock files, IMG protection |
| `satk game protect` / `unprotect [--root R]` | `satk_op` | Sets or clears the read-only attribute of the 8 IMG archives |
| `satk game clone [--src S] [--dst D] [--exe stock\|mta] [--dry-run] [--protect]` | `satk_op` | Recreates the copy in a **new** folder |
| `satk game exe [--variant stock\|mta] [--out F] [--src E] [--force]` | `satk_op` | Writes the stock or the MTA-canonical exe outside the game folders |

`game` has no MCP tool of its own: an agent finds the commands with `satk_ops("game")` and runs them with
`satk_op`. Output is a JSON envelope; `--table` prints a table. Other options: `--jobs` (threads for hashing or
copying), `--limit` (rows of differences in a `verify` error), `--manifest` (another manifest, for tests and
other game versions).

### `verify`

- `--against stock` (default): the reference is `data/manifests/stock-1.0us-hoodlum.json` (416 files). The
  `MANIFEST.sha256` in the root of the copy is checked too (field `manifest`: `ok`, `differs` or `missing`).
  `MANIFEST.sha256` itself is not counted as an extra file.
- `--against install`: the reference is the audit of one original install with mods,
  `data/manifests/install-<date>.json` (549 files; development data of the maintainers, not part of the public
  repository; the default root is then `paths.installed`). `--scope stock` checks only the 416 stock paths; for
  `gta_sa.exe` and `vorbisFile.dll` the local audit hashes `1d7c531f…` and `6ccf4258…` are used. `--scope all`
  checks everything, but SA-MP and sanext logs change while you play.
- Extra files in the copy are only a warning (`EXTRA_FILES`) with `--scope stock` and an error with `--scope all`.
- When something differs, the command fails with `REVISION` (exit 1). `error.data` holds the counters and a table
  `path, problem, expected, actual`; `problem` is one of `missing`, `size`, `sha256`, `extra`.
- The fast mode does not notice an in-place IMG edit that kept the size and mtime. Use `--deep` to be sure. If only
  the mtime of an IMG changed, the file is simply hashed and there is no error.

### `info`

Without `--root` it describes the clean copy; when there is no clean copy, the game folder (`paths.game_root`).

| Field | Meaning |
|---|---|
| `exe` | `hoodlum-stock` (stock), `hoodlum-nointro` (local exe with the skip-splashes patch), `mta-canonical` (stock with the PE checksum fixed), `mta-programdata*` (MTA runtime copies), `compact-1.0us`, `unknown`, `missing` |
| `variant`, `layout`, `re_supported` | readable name of the build, its address layout (`1.0us`) and whether `satk re` works with it; an unsupported exe adds the warning `UNSUPPORTED_EXE` (assets work with any version) |
| `variant_match` | how the exe was recognized: `sha256` (a verified hash from `data/exe_versions.json`), otherwise byte signatures or the file size (heuristic) |
| `exe_sha256`, `exe_desc`, `exe_pe` | hash, description and PE header fields of the exe |
| `vorbisfile` | `stock` or `asi-loader` (Silent's ASI Loader took the name `vorbisFile.dll`) |
| `asi` | number of `*.asi` files in the whole tree |
| `nonstock` | files outside the stock set; the first 20 are in `nonstock_files`, the split in `nonstock_roles` (`exclude` = additions inside stock folders, `nonstock` = everything else) |
| `stock_present`, `stock_missing`, `files` | stock files found and missing, all files in the folder |
| `manifest` | whether the folder has a `MANIFEST.sha256` |
| `protected` | how many of the 8 IMG archives are read-only |

For a clean copy: `exe=hoodlum-stock`, `asi=0`, `nonstock=0`, `protected=8/8`. For the audited original install:
`hoodlum-nointro`, `asi-loader`, 17 ASI, 133 non-stock files.

### `protect` / `unprotect`

Protected are `models\{gta3,gta_int,player,cutscene}.img`, `anim\{anim,cuts}.img`, `data\paths\carrec.img`,
`data\script\script.img`. The game and Ariane open IMG files for reading (`"rb"`), so protection does not get in
their way. A write attempt (for example "save" in an editor) gets an OS error instead of silently damaging the
archive. Contents and mtime do not change, `verify` stays `ok`.

The working state of the copy is **protection on**. Clear it only for the time of a deliberate edit. The command
refuses to protect the original install (`PROTECTED_PATH`): satk does not write even attributes there. `--root` is
the clean copy itself or a copy under `work\`; a subfolder of the clean copy, a place outside `work\` and IMG files
with hard links (the attribute is shared by all names of a file) are `PROTECTED_PATH` too.

### `clone`

1. The source (by default the original install) is opened read-only. A verified clean copy can be the source too
   (`--src <workspace>\gta-sa-clean`): its `vorbisFile.dll` is already the original one, and the exe (stock or MTA)
   is converted to the requested variant.
2. `dst` must be a new or empty folder, otherwise `EXISTS`. Allowed are only a folder under `work\` or the root of
   the clean copy itself (`paths.game`, if it does not exist or is empty). Everything else is `PROTECTED_PATH` (see
   "Where game.* may write").
3. Each of the 416 files is copied into `<dst>.partial`, hashed on the way and checked against the manifest; the
   mtime is kept.
4. `gta_sa.exe` is restored in memory: the 12-byte skip-splashes patch at file offset `0x347EA8` is reverted.
   `--exe mta` also sets the PE CheckSum field to `0xDBD689`.
5. `vorbisFile.dll` is taken from `vorbisHooked.dll` (from a clean copy: from its own `vorbisFile.dll`).
6. Then `MANIFEST.sha256` is written and `<dst>.partial` is renamed to `<dst>` atomically. The first hash error (or
   Ctrl+C) stops all copying: the queue is cancelled, running copies stop at the current 1 MB buffer, the partial
   copy is removed.

Skipped are `*.two`, `data\colorcycle.dat`, `models\{ps3btns,x360btns,sixaxis}.txd`, `text\languages.ini` and
everything outside `anim, audio, data, models, movies, text, ReadMe` and the root files
`eax.dll, ogg.dll, vorbis.dll, stream.ini`: 133 files of the audited install in total. About 5.03 GB of free space
is needed.

`--dry-run` writes nothing and prints the plan:

```powershell
satk game clone --dst <workspace>\work\tmp\clone --dry-run
```

```json
{"ok":true,"files":416,"bytes":5029186364,"skip_nonstock":133,"restore":["gta_sa.exe","vorbisFile.dll"],"exe":"hoodlum-stock","free_bytes":27503448064,"dry_run":true}
```

### `exe`

```powershell
satk game exe --variant mta --out <workspace>\work\re\bin\gta_sa_mta.exe
```

The default source is `gta-sa-clean\gta_sa.exe`, otherwise the exe of the original install. Any variant the stock
exe can be derived from will do: `hoodlum-stock`, `hoodlum-nointro`, `mta-canonical`. The result is checked by
SHA-256 before it is written:

- stock: `a559aa772fd136379155efa71f00c47aad34bbfeae6196b0fe1047d0645cbd26`;
- mta: `f63fa623d14e170d0ae9e7032186669cf3a177793be21c4824043f0279bd506a`.

By default the file goes to `work\re\bin\gta_sa_<variant>.exe`. `--out` must be under `work\` and outside any git
repository (for example a worktree under `work\wt\`): the game executable must not end up in `docs` or in a commit.
If a file with different contents already exists, `--force` is needed.

## Where game.* may write

`clone --dst`, `exe --out` and `protect/unprotect --root` are checked by `satk.game.guard`, and exactly the checked
path is written:

1. The spelling of the path is normalized: `\\?\D:\…`, `\\.\D:\…`, `//?/D:/…` become `D:\…`. Other namespaces
   (`\\?\Volume{…}`, `GLOBALROOT`, devices), components ending in a dot or a space and `..` inside a `\\?\` path are
   `BAD_PARAMS`. Network paths (`\\localhost\D$\…`, `\\?\UNC\…`, a mapped network drive) are `PROTECTED_PATH`: a
   share may turn out to be the original install itself.
2. Junctions, symlinks and substituted drives are resolved (`realpath`).
3. A text check against the protected roots (`paths.ensure_writable`). The lock on the clean copy is lifted only for
   its root, never for its subfolders.
4. A check by file identity (`os.path.samestat`): no existing ancestor of the target may be the same folder as the
   original install, `src` or another protected root, however it is spelled.
5. Location: under `work\` (or the root of the clean copy where that is allowed) and outside git repositories.

## Data in the repository

The repository holds only paths and hashes, no game bytes:

- `data/manifests/install-<date>.json` (development checkouts only, not in the public repository): the audit of
  one original install, 549 files `{size, sha256, mtime}`;
- `data/manifests/stock-1.0us-hoodlum.json`: the 416 files of the clean copy, the selection rules and the target
  hashes of the exe and `vorbisFile.dll`. It is generated from the audit with
  `python -m satk.game.manifests build`; a test checks that the committed file is reproduced byte for byte. The text
  of the copy's `MANIFEST.sha256` is reproduced from it byte for byte too;
- `data/exe_versions.json`: the known `gta_sa.exe` builds (hash, signatures, size, layout) for `info`.

In an installed package these files live in `satk/_data`.

## For developers

- Standard library only. Game files are opened through `paths.open_ro` (`"rb"`). Write targets are checked by
  `satk.game.guard.write_target`, every file by `ensure_writable`; `game_writer()` is enabled only for the root of
  the clean copy (`Target.unlock()`).
- Paths inside the game are compared the Windows way: case-insensitive, `\` and `/` are equal. They are built only
  with `pathlib`, component by component. String literals with `\x` escapes are forbidden in `satk/game`: the
  prototype turned `"models\x360btns.txd"` into `models60btns.txd`. A test checks this.
- Tests: `tests/game` (synthetic installs are generated in `tmp`, targets in the `work` of an isolated workspace,
  fixture `work`); tests marked `game` read the real clean copy and the original install read-only and write into
  their own `SATK_PATHS_WORK` (fixture `real_work`).
- `satk status` (section `game`) and `satk doctor` (check `game_copy`) take their data from this package.
