# NOTICE

satk is licensed under the MIT License (see `LICENSE`), except `blender/satk_blender/`,
which is GPL-3.0-or-later and carries its own `LICENSE`.

## Not included

- **No game data.** Grand Theft Auto: San Andreas is a trademark of Take-Two Interactive /
  Rockstar Games. This repository contains no files from the game (IMG/DFF/TXD/COL/IFP,
  textures, scripts, executables) and no images derived from them. The pre-commit hook
  (`scripts/hooks/pre-commit` -> `satk dev assetguard`) rejects such files. Synthetic test
  fixtures that merely imitate file headers are listed in `.assetguard-allow`.
- **No decompiled or reference code.** gta-reversed, Ariane/euryopa, samp-source,
  DragonFF and the Neon fork of MTA are used only as read-only references in local clones
  that each user makes for themselves (the `src` folder of the workspace). Their code is not
  copied here; `satk re src` shows gta-reversed fragments only in the terminal/MCP response and
  never writes them to files.
- File formats are implemented from public format descriptions.

## Runtime dependencies (optional, installed by `scripts/bootstrap.ps1 -Deps`)

Exact versions: `requirements.lock`.

| Package | License | Used for |
|---|---|---|
| Pillow | MIT-CMU (HPND) | PNG, DXT decode, image resize, contact sheets |
| numpy | BSD-3-Clause | software renderer, glTF buffers |
| mcp (Python SDK) | MIT | MCP stdio transport |
| pytest | MIT | tests |

The core (`satk.core`, `satk.formats`, `satk.index`, `satk.re`, `satk.game`, `satk.saap`)
uses only the Python standard library.

## Portable zip: bundled third-party software

The release archive `satk-<version>-win64.zip` (built by `satk dev release`) also contains:

- the official embeddable CPython 3.12 for Windows from python.org (Python Software Foundation
  License; its licence text and the notices of the bundled libraries are in `python/LICENSE.txt`);
- the runtime wheels pinned in `packaging/release-lock.json` (Pillow, numpy, mcp and their
  dependencies), unpacked unchanged into `python/Lib/site-packages/`; each keeps its own licence
  files in its `*.dist-info` folder.

The wheel and the sdist (`satk-gta` on PyPI) contain only satk itself, its data and the vendored
code listed below.

## Vendored third-party code

| Component | Version | License | Where | Used for |
|---|---|---|---|---|
| rwfury (Hancapo) | 0.6.1, unmodified | MIT | `vendor/rwfury` (notice: `data/notices/rwfury.txt`) | collision surface names in `satk col write`; reference reader and round-trip baseline in `tests/rw` |
| gta-flow core `sa_traffic` (Dryxio) | 0.1.0a1, rev `c3d9ad40`, pure-Python core only | MIT | `vendor/gtaflow` (provenance and file hashes: `vendor/gtaflow/VENDORED.md`; licence and upstream notice: `vendor/gtaflow/LICENSE`, `vendor/gtaflow/NOTICE.md`) | traffic path nodes (`satk paths`): NODES*.DAT codec, compiler and validation |

satk's RenderWare writers (`satk.rw`) are its own code; they follow the stream layouts of librw
(MIT, https://github.com/aap/librw) as a format description. No librw code is copied.

## Third-party data

| Data | Source | License | Notice |
|---|---|---|---|
| CrashInfo crash list, GTA SA 1.0 US, English (`data/crashlist/`) | [JuniorDjjr/CrashInfo](https://github.com/JuniorDjjr/CrashInfo), English list for GTA SA 1.0 US, stored unchanged | MIT | `data/notices/crashinfo.txt` |
| gta-scout model descriptions (imported into the local notes database by `satk describe import`; not stored in the repo) | [Dryxio/gta-scout](https://github.com/Dryxio/gta-scout), annotation pack `sa-2026-10-03.json`, rev `ab2c6f27` | MIT (with the publisher's note on game assets) | `data/notices/gta-scout.txt` |

## Ported rules

- `src/satk/modinspect/`: file handling, profile/priority and data-file merge rules re-implemented in Python
  from Mod Loader (thelink2012/modloader, MIT, (c) 2013-2015 LINK/2012) and its datalib (BSL-1.0); license
  texts in `src/satk/modinspect/NOTICE-modloader.txt`. INU Check (GPL-3.0) is only run as an external
  program if the user installed it; nothing of it is included.
