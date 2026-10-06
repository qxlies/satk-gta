# Contributing to satk

Thank you for helping. satk is a preview: interfaces still move, so for anything bigger than a small fix please open
an issue first and describe what you want to change. Issues and pull requests may be written in English or Russian.

By taking part you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md). Security problems are reported
privately, see [SECURITY.md](SECURITY.md).

## Ground rules

- **No game files, ever.** Do not commit, attach or paste files from GTA San Andreas or from mods: `.img`, `.dff`,
  `.txd`, `.col`, `.ifp`, textures or pictures taken from the game, audio, `gta_sa.exe`, `.scm`, and so on. Tests
  build synthetic inputs in code. The pre-commit hook and CI run `satk dev assetguard`; bypassing the hook with
  `git commit --no-verify` is not allowed. A synthetic fixture that only looks like an asset goes into
  `.assetguard-allow` together with its sha256 and a short reason in the pull request.
- **No foreign reference code.** satk is MIT. Do not copy code from gta-reversed, Ariane/euryopa, SA-MP sources,
  MTA (or its forks) or DragonFF into it. `blender/satk_blender` is GPL-3.0 and is the only GPL part. Vendored MIT
  code keeps its license and is listed in [NOTICE.md](NOTICE.md).
- **The game is only read.** Code reads game files through `satk.core.paths.open_ro()` and writes only after
  `paths.ensure_writable()` (or with `paths.atomic_write`), which keep every write inside satk's `work` folder.
- **No paths of your machine** in code, tests or docs: paths come from the configuration (`cfg().paths.*`) and
  `satk.core.detect`; examples use placeholders such as `<workspace>` or `<game folder>`.

## Development setup

You need 64-bit Windows 10/11, CPython **3.12** and git. A GTA San Andreas folder is optional: without it the tests
that need game data are skipped.

```powershell
git clone https://github.com/qxlies/satk-gta tools
cd tools
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.lock
git config core.hooksPath scripts/hooks
.\satk.cmd version
```

`requirements.lock` pins every package of the development environment (Pillow, numpy, mcp, pytest and their
dependencies); install exactly these versions. `powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1 -Dev
-FromLock` does the same in one go; it also rewrites `requirements.lock` from the venv. Commit changes of
`requirements.lock` only in a pull request that changes dependencies.

satk is not installed into the venv. The shims `satk.cmd` (cmd, PowerShell) and `satk.sh` (Git Bash) of your
checkout take `.venv`, set `PYTHONPATH=<checkout>\src` and `PYTHONUTF8=1`, and run `python -X utf8 -m satk`. Without
the shims:

```powershell
$env:PYTHONPATH = "src"; $env:PYTHONUTF8 = "1"
.venv\Scripts\python -m satk version
```

To work with a real game, run `.\satk.cmd init --game "<game folder>"` once (it writes `satk.toml` into your
workspace) and `.\satk.cmd index build`. Installing in general: [docs/en/install.md](docs/en/install.md).

## Tests

```powershell
.venv\Scripts\python -m pytest -q -p no:cacheprovider
```

pytest finds `src` by itself (`pyproject.toml`). Markers:

| Marker | Meaning | When it is missing |
|---|---|---|
| `game` | reads a GTA San Andreas folder (read only) | skipped |
| `viewer` | needs the Ariane viewer | skipped |
| `blender` | needs Blender | skipped |
| `engine` | needs the MTA fork checkout and build tools | skipped |
| `slow` | takes more than a few seconds | always runs |
| `e2e` | end-to-end scenario across packages | always runs |

Tests that open a viewer window also need `SATK_TEST_LIVE=1`. `SATK_TEST_NO_SKIP=1` turns every skip into a
failure. CI deselects the `game`, `viewer`, `blender` and `engine` tests (the command is below).

Write tests next to the code they cover (`tests/<package>/`). Build inputs in the test (for example a tiny IMG or
DFF from bytes); use the `satk_home` fixture for an isolated workspace and `run_cli` to run commands in-process. A
test that needs the game, a viewer, Blender or build tools carries the matching marker, so that it is skipped where
the tool is missing.

## Before you open a pull request

1. Run what CI runs; none of it needs game data:

   ```powershell
   .venv\Scripts\python -m pytest -q -p no:cacheprovider -m "not game and not viewer and not blender and not engine and not live"
   .\satk.cmd dev assetguard --all
   .\satk.cmd dev gen-docs --check
   .\satk.cmd mcp selftest
   ```

2. You changed an operation, its parameters or an SQL schema: `.\satk.cmd dev gen-docs` regenerates the reference
   docs that `satk dev gen-docs --check` compares.
3. You changed documentation: `.\satk.cmd dev linkcheck docs README.md` checks paths and links;
   `.\satk.cmd dev docs-smoke docs/en/<page>.md` runs the page's "Quick example".

CI (GitHub Actions, Windows, Python 3.12) runs the commands of step 1 on every push to `main` and every pull request.
Before merging, maintainers also run `satk dev gate`: the same checks plus the game-data tests and an index of the
stock game checked against golden numbers. The gate also runs inside a restricted sandbox (for example the Codex
Windows sandbox): it works around what is denied there and reports it, see `docs/en/troubleshooting.md`.

## Code

- New commands are operations: an `@op` in `src/satk/<package>/ops.py` (see the docstring of
  `src/satk/core/registry.py`). The CLI, `satk help` and the MCP server are generated from it. A short English
  `summary` (at most 300 characters) is also the MCP description.
- Errors are `SatkError` with a `hint`. Use only the error codes listed in `src/satk/core/errors.py`; warnings are
  strings `"CODE: text"` in `warn`.
- The core contracts (`satk.core.registry`, `ids`, `envelope`, `errors`, `paths`) are stable: only compatible
  changes (new fields or parameters with defaults).
- Answers save tokens: tables, small default limits, no empty fields, images as file paths.
- Imports stay fast: `ops.py` and the stdlib-only packages import Pillow, numpy and mcp inside functions, not at
  module level (`tests/core/test_layout.py` checks it).
- The same input gives the same output: sort, and keep time and randomness out of data.
- New operations are not MCP tools by default (`mcp=False`): the MCP `tools/list` has a byte budget, and agents
  reach every operation through `satk_ops` and `satk_op`.

## Language

- Code, comments, docstrings, messages, `satk help` and everything an AI model reads (`docs/agent/`, MCP
  descriptions) are English. The only Russian text in code is the optional `summary_ru` of an operation.
- User documentation is English first (`README.md`, `docs/en/<page>.md`) with a Russian mirror (`README.ru.md`,
  `docs/ru/<page>.md`). Each file is in one language only. If you cannot write the Russian mirror, say so in the
  pull request and the maintainers will update it.

## Commits and pull requests

- Branch from `main`; one topic per pull request. Keep commits focused: the subject line says what changes (at most
  72 characters), the body says why.
- Add or update tests for every change in behavior, and docs for every user-visible change.
- Fill in the checklist of the pull request template. CI must be green.
- The public repository receives one snapshot commit per release. An accepted pull request is applied to the
  development tree, appears in the next snapshot and keeps your authorship as a `Co-authored-by` line.
- Contributions are licensed under the license of the files they change: MIT, and GPL-3.0 for
  `blender/satk_blender`.
