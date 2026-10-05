# `satk dev release`: the portable Windows release

[Русская версия](../ru/release.md)

Package: `satk.release`. Packaging inputs: [`packaging/`](../../packaging/README.md).

## What it is

One command turns a git commit of satk into files you can hand to a modder who has no Python, no git and no
admin rights:

- `satk-<version>-win64.zip` (about 48 MB, 124 MB unpacked): the official embeddable CPython 3.12 from
  python.org, the runtime wheels (Pillow, numpy, mcp and what they need on Windows; no pip, no pytest), satk
  itself and double-click launchers;
- `satk_gta-<version>-py3-none-any.whl` and `satk_gta-<version>.tar.gz`: the package for `pip`/`pipx`
  (PyPI name `satk-gta`, the command stays `satk`; not on PyPI yet);
- `SHA256SUMS.txt` over the three files.

The release is built from a commit, never from the working tree: uncommitted changes only produce a
`DIRTY` warning. The same commit and the same lock give byte-identical files. After the build the zip is
unpacked into a folder whose name has Cyrillic letters and a space and is run the way a new user runs it.

## Quick example

<!-- docs-smoke: skip three release builds take about a minute; the first one downloads about 46 MB -->
```powershell
satk dev release
satk dev release --smoke game
satk dev release --offline --smoke none --sandbox prepare
```

The first build downloads the pinned files once into `<work>/cache/release` (about 46 MB); later builds
work offline. What `--smoke game` returns (shortened):

```json
{"ok":true,"version":"0.1.0","commit":"16ae5c567","out":".../work/out/release/satk-0.1.0",
 "files":[{"name":"satk-0.1.0-win64.zip","mb":47.8,"sha256":"7483a107..."},
          {"name":"satk_gta-0.1.0-py3-none-any.whl","mb":1.4,"sha256":"3a781264..."},
          {"name":"satk_gta-0.1.0.tar.gz","mb":1.2,"sha256":"0134e486..."}],
 "zip":{"entries":3139,"unpacked_mb":124.3,"python":"3.12.10","wheels":31},
 "smoke":{"cols":["step","ok","seconds","summary"],"rows":[["version",true,1.2,"satk 0.1.0, Python 3.12.10 (bundled)"],
   ["index-build",true,8.1,"..."],["first-result",true,0.3,"3 rows, first tex:tags2_lalae/grove; first result 9 s after init"]]},
 "seconds":21.7}
```

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk dev release [--ref REF] [--out DIR] [--smoke none\|quick\|game] [--game DIR] [--offline] [--relock] [--sandbox none\|prepare\|run]` | — | build the zip, the wheel, the sdist and `SHA256SUMS.txt` into `<work>/out/release/satk-<version>/` (or `--out`), then smoke-test the zip |

- `--smoke quick` (default): `satk version`, `config show`, `doctor` and `mcp selftest` from the unpacked zip
  with a clean environment (no `SATK_*`, `PYTHON*` or venv variables). `--smoke game` also runs `init --game`,
  `index build` and a first `asset find` on the game folder (`--game`, default: the clean copy `paths.game`).
  The copy lives in `<work>/tmp/release/smoke/`.
- `--relock` rebuilds `packaging/release-lock.json` from the development venv (see below) and writes it into
  the checkout; commit it.
- `--sandbox prepare` writes `<out>/sandbox/satk-check.wsb` next to the zip: double-click it and Windows
  Sandbox unpacks the zip on a clean Windows, builds the index on a read-only game copy and writes
  `<out>/sandbox/results/result.json`. `--sandbox run` does the same through the `wsb` command line of
  Windows 11 24H2+ and waits for the result.

## What is inside the zip

```
satk-<version>-win64\
  Start satk.cmd               double-click: a console where "satk" works; runs "satk init" the first time
  <Russian name>.cmd           the same launcher under its Russian name
  satk.cmd                     python\python.exe -s -X utf8 -m satk %*
  satk-mcp.cmd                 python\python.exe -s -X utf8 -m satk.mcp (stdio MCP server)
  README-FIRST.txt, README-FIRST.ru.txt
  portable.txt                 portable mode: this folder is the workspace
  LICENSE, NOTICE.md, CHANGELOG.md, README.md, satk.toml.example
  RELEASE.json                 version, commit, Python, wheel versions
  python\                      embeddable CPython; python312._pth adds ..\src and "import site"
  python\Lib\site-packages\    the runtime wheels (unpacked, dist-info kept)
  src\satk\ data\ vendor\ proto\ mta-resources\ blender\ docs\
```

The layout mirrors a checkout, so everything that finds its files relative to the code (data files,
vendored gta-flow and rwfury, SAAP conformance files, the Blender add-on) works without changes.
`-s` keeps the user's own `site-packages` out; the `._pth` file already ignores `PYTHONPATH`, and
`-X utf8` makes paths with any letters safe.

## Portable mode

`portable.txt` next to `src\` makes that folder the workspace (`satk.core.config.portable_root`):
`satk init` writes `satk.toml` there, everything generated goes to `work\`, and the per-user configuration
(`%APPDATA%\satk\satk.toml`) is not read. A `workspace` line in the folder's `satk.toml` that names another
folder (the folder was moved, or `satk.toml` came from an older version) is ignored with the warning
`PORTABLE_WORKSPACE`. `SATK_HOME` and `SATK_CONFIG` still win. Deleting the folder removes satk completely. `satk doctor` adds two checks:

- `portable`: the folder is writable, its files do not carry the Mark of the Web (Windows marks files
  unpacked from a downloaded zip; the fix is `Unblock-File`), and `RELEASE.json` matches the code;
- `app_control`: whether Smart App Control blocks the extension modules (below).

## The lock and how the inputs are verified

`packaging/release-lock.json` pins everything that is not in git:

- **Python:** `python-3.12.10-embed-amd64.zip`. 3.12 receives security fixes as source only, so 3.12.10 is
  its last release with Windows binaries. `--relock` checks the size and MD5 against python.org's release
  API and the Authenticode signature of every `.exe`, `.dll` and `.pyd` (Python Software Foundation;
  `vcruntime140*.dll` by Microsoft), then pins the SHA-256. The OpenPGP signature of the Windows release key
  was checked by hand; the commands are in [`packaging/README.md`](../../packaging/README.md).
- **Wheels:** the closure of the `pyproject.toml` extras except `dev` (`img`, `num`, `mcp`), evaluated for
  CPython 3.12 on 64-bit Windows, one wheel each, with the SHA-256 that PyPI publishes. The versions must equal
  `requirements.lock`; `excluded` lists what stays out (pytest and its dependencies).

A build checks every cached file against the lock; a mismatch is never used. Downloads go only to
python.org and PyPI over HTTPS.

## Results of the acceptance run

| Check | Result |
|---|---|
| zip, wheel, sdist and `SHA256SUMS.txt` from a commit | 20 s with the cache; two builds of one commit are identical |
| unpacked to `...\<Cyrillic> satk\` with a clean environment | `version`, `config show`, `doctor` (no runtime check fails), `mcp selftest` pass |
| `init` + `index build` + `asset find` on the clean game copy | 8 s from `init` to the first result |
| Explorer (Shell) and `Expand-Archive` unpacking | 3014 files each, the Russian launcher name intact |
| Windows Sandbox (no Python, network off, game read-only) | first result 10 s after `init`, 29 s with the sandbox start |

## Limitations and known issues

- **Smart App Control.** The extension modules inside PyPI wheels (numpy, Pillow, pydantic-core, cryptography)
  are not code-signed. Where Smart App Control is on (Windows Sandbox has it on, and so do some new
  Windows 11 installations), Windows refuses to load them: the index, search, exports and the stdlib paths
  work; previews, the fast PNG/DXT paths and the MCP server do not. `satk doctor` (`app_control`) says so.
  Changing Smart App Control is the user's decision; satk never touches it. Signing (SignPath) is a later step.
- 64-bit Windows 10/11 only. `satk doctor` in the zip still lists development components (MTA fork, viewer,
  `pytest`) as warnings or failures; they are optional.
- Updating means unpacking the new zip next to the old one and moving `work\` and `satk.toml`.
- The PyPI wheel carries `satk/_data` and `satk/_vendor`; the vendored code is found from a checkout or the
  zip, not yet from a pip installation.

## For developers

- Code: `src/satk/release/`: `lock.py` (lock, `--relock`), `fetch.py` (verified downloads), `tree.py`
  (`git archive` of the ref), `wheels.py` (wheel contents without pip), `dist.py` (wheel and sdist with the
  stdlib, in step with `pyproject.toml`, `setup.py` and `MANIFEST.in`), `build.py` (zip, checksums, smoke),
  `sandbox.py` (Windows Sandbox), `ops.py` (operation and doctor checks).
- Inputs: `packaging/portable/` (files for the zip root), `packaging/CHANGELOG.md`,
  `packaging/sandbox/check.ps1`, `packaging/release-lock.json`.
- Tests: `tests/release/` (synthetic trees and wheels, the real HEAD, no network).

## Public export

The development repository is private. The public repository (`qxlies/satk-gta`) gets one snapshot commit per
public release instead, written by `satk dev export-public` from a commit of the development repository.

| Command | MCP | What it does |
|---|---|---|
| `satk dev export-public [--ref REF] [--out DIR] [--base REPO] [--check-only] [--no-tests] [--limit N]` | — | filter and audit a commit, write it as a git repository with one commit and the tag `v<version>`, run its tests |
| `satk dev release --public [--repo DIR]` | — | a release whose files passed the same audit; `--repo` builds from another checkout, e.g. the snapshot |

1. **Files.** `git archive` of the commit (never the working tree) minus the files that match
   `data/public-exclude.txt` of that commit (gitignore syntax; `@allow <rule> <path> [<regex>]` lines accept a
   known finding). The list names the install audit of the maintainer's own game folder, the CLAUDE.md of the
   development workspace and internal review notes.
2. **Audit** of every remaining file. Any finding stops the export with a table (one row per file and rule, the
   first `--limit` rows); every line is in `<work>/out/public/satk-gta-<version>.audit.json`. `--check-only`
   stops here.

   | Rule | What is refused |
   |---|---|
   | `machine-path` | in every text file: folders of the development machine (the `Games` and `files` folders of its data drive, also in Git Bash spelling) and Windows account folders with a concrete account name (`Public`, `Default` and placeholders such as `<you>` are fine) |
   | `plan-ref` | in the files people read (READMEs, CLAUDE.md, NOTICE.md, `docs/`, `packaging/`, `proto/`, `.claude-plugin/`): references to the internal specification and plans, work package and milestone lane ids |
   | `email` | addresses other than the maintainer's commit alias, no-reply addresses and reserved example domains; license and notice files may name their authors |
   | `secret` | API keys and tokens (`sk-` keys, GitHub `ghp_`/`gho_`/`github_pat_` tokens, AWS, Slack and Google keys), private key blocks, the key name of the model router |
   | `private-repo` | links to the maintainer's other repositories (they are private) |
   | `asset` | what `satk dev assetguard` refuses (game asset magic, RenderWare chunks, images outside docs/img), with `.assetguard-allow` |
   | `large-file` | files over 5 MiB |
   | `dangling-ref` | user docs that name an excluded file |

3. **Snapshot** in `<work>/out/public/satk-gta-<version>/` (or `--out`), a git repository. Without `--base` it is
   a fresh repository with one root commit on `main`; with `--base` (a clone or the URL of the public repository)
   it is that history plus one commit that replaces the whole tree. The commit is `satk <version> public preview`
   by the `user.name`/`user.email` of the development repository, dated like the exported commit, with the
   annotated tag `v<version>`. Then blobs and modes are compared with the exported commit: the public tree is
   the development commit minus the excluded files, byte for byte. A tag that already exists with other files
   is refused (bump the version); exporting the same files again changes nothing. A folder that holds anything
   but an earlier snapshot is never replaced.
4. **Tests** of the snapshot itself: `pytest -m "not game"` with `PYTHONPATH=<snapshot>/src`, an empty
   `SATK_CONFIG` folder, an empty `SATK_HOME`, tool discovery off and no other `SATK_*` variable, so the
   development workspace is invisible. Like a fresh clone after setup, the snapshot gets a `.venv` (removed
   afterwards) that sees the packages of the development venv. The output is in
   `<work>/tmp/export-public/tests/pytest.log`.

```powershell
satk dev export-public --check-only
satk dev export-public --base https://github.com/qxlies/satk-gta.git
satk dev release --public --repo <work>/out/public/satk-gta-<version>
```

Nothing is pushed or published: after a green export push the branch and the tag of the snapshot yourself and
attach the files of `satk dev release --public` to the GitHub release.

Every `satk dev release` applies the same exclude list (an excluded file never lands in the zip, the wheel or
the sdist) and reads the built archives back to check it. With `--public` it audits the commit first and also
refuses any path of the development machine inside the built files (satk's own files: every rule of
`machine-path`; the bundled third-party files: the development machine's own folders and account).
`satk dev sync-agent-docs` skips the workspace CLAUDE.md when a checkout has none, as the public snapshot.
