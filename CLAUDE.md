# Developing satk (for agents and people)

<!-- Contributor guide. <= 150 lines (tests/e2e/test_docs_layout.py). English only. Checked 2026-10-06. -->

This is the contributor guide of the satk repository. Using satk (not developing it): `docs/en/README.md`
(Russian mirror `docs/ru/README.md`); for AI agents `docs/agent/SKILL.md`. Paths below use `<workspace>` for the
folder that holds the checkout `tools` next to `work\` (one concrete layout: `docs/en/workspace-gta.md`). In such a
workspace its own `CLAUDE.md` adds the rules for the folders around the checkout (what must not be touched, what
needs consent); those rules win.

## Environment

- Windows 10/11, CPython 3.12 for development (`py -3.12`; a bare `py` may start a newer version). satk itself
  runs on 3.12-3.14.
- One venv per workspace: `<workspace>\tools\.venv`, made by `scripts\bootstrap.ps1 -Dev` (Pillow, numpy, mcp,
  pytest; exact versions in `requirements.lock`, `-FromLock` installs them). The `satk` package is **not
  installed** into it: the shims set `PYTHONPATH=<checkout>\src` and run `python -X utf8 -m satk`.
- Run `satk.cmd` (cmd/PowerShell) or `satk.sh` (Git Bash) of **your own** checkout.
- The console may be cp1251: always `encoding="utf-8"`; the shims set `PYTHONUTF8=1`. No symlinks, writes are
  atomic. PowerShell 5.1 `Set-Content -Encoding utf8` adds a BOM: edit files with an editor or Python.
- First setup: clone into `<workspace>\tools`, run `bootstrap.ps1 -Dev` (it also sets the pre-commit hook), then
  `satk init --game "<folder with gta_sa.exe>"` (`--clean-copy` makes the stock 1.0 US copy the game tests use).

## One task = one worktree

```powershell
git -C <workspace>\tools worktree add <workspace>\work\wt\<id> -b <branch> main
<workspace>\work\wt\<id>\satk.cmd version           # src from the worktree, python from tools\.venv
cd <workspace>\work\wt\<id>
<workspace>\tools\.venv\Scripts\python -m pytest -q tests/core tests/<pkg>
```

A worktree has no `.venv` of its own: the shim takes `<workspace>\tools\.venv` (or `%SATK_HOME%\tools\.venv`).
The local `satk.toml` is shared too: read from the worktree, else from the main checkout `<workspace>\tools`.
A maintainer merges into `main` after acceptance; before you continue in an old worktree run
`git merge --no-edit main`.

**Acceptance = `satk dev gate`** (run it from your checkout/worktree; ~5 min, `--quick` ~2 min without game
data). It runs assetguard, the generated- and agent-docs checks, all tests, `mcp selftest`, a vanilla index build
in a private `work\tmp\gate-<checkout hash>\work` (one per worktree: parallel gates do not collide) with the golden
checks, and the game-data tests. Do not set `SATK_PATHS_WORK` for the gate itself. A green gate is enough to
merge; a model review only for risky changes (write protection, parsing other people's mod files, network
protocols), and only once. While working use `satk dev gate --changed`: cheap steps plus tests affected by changed
files, each with its reason (`--with-game` adds the game steps, `--dry-run` shows the plan); it is not the acceptance.
At most 2 full gates run per machine (`SATK_GATE_SLOTS`; a third waits); TEMP is `work\tmp\gate-<hash>\tmp`.

## Ownership

Every file belongs to exactly one package. Do not edit other packages' files: ask their owner (in a multi-agent
run, through the lead, who gives each task its own set of files). `tests/<pkg>/**` belong to the package owner. A
package owns `src/satk/<pkg>/**`, `tests/<pkg>/**` and its page (`docs/en/<pkg>.md`, Russian mirror
`docs/ru/<pkg>.md`).

| Shared point | Owner |
|---|---|
| `pyproject.toml`, `setup.py`, `MANIFEST.in`, `src/satk/core/config.py` (additions only), package `satk.release`, `packaging/**` | release |
| `src/satk/core/{cli,detect}.py`, `src/satk/runtime/**` (status, doctor, help, gen-docs), package `satk.bugreport` | first run |
| `README*`, `docs/en/**` (except `ai.md`), `docs/ru/{README,install,quickstart,troubleshooting,faq,legal}.md`, `src/satk/docs/**`, `tests/{docs,e2e}/**` | docs |
| `src/satk/mcp/**`, `docs/agent/**` (except the generated `tools.md`, `schema.md`), `docs/{en,ru}/ai.md`, `.claude-plugin/**`, `tests/mcp/**` | AI clients |
| other `src/satk/core/**` (frozen contracts), `scripts/`, `.gitignore`, `LICENSE`, shims | the maintainers |
| `blender/satk_blender/**`: `studio/`; `kit/`, `exporter.py`, `gameready.py`; the rest (`look/`, `agent_cli.py`, `render.py`, ...). `src/satk/blender/`: `ops.py`, `gameready.py`, `packaging.py` (kit); `runner.py`, `resolve.py`, `contract.py` (look) | studio; kit; look |
| `data/style/**` except `topics/` (style), `data/kit/**` (kit); `data/style/topics/**` with `docs/agent/style/**` | style, kit; AI clients |
| `tests/conftest.py` | nobody |
| `NOTICE.md`, `.assetguard-allow` | append only; a merge conflict is resolved by union |
| `CLAUDE.md` (this file) | the merge step |

Outside the repository (full development workspace only): `<workspace>\viewer\ariane` (Ariane fork) and
`<workspace>\engine\mtasa` (MTA fork) have their own `CLAUDE.md`; the workspace `CLAUDE.md` and
`<workspace>\.claude\skills\satk\` are published from `docs/agent` by `satk dev sync-agent-docs`. DB schemas
change only through their owners: index (`index`), symdb (`re`), notes (`notes`), kb (`kb`).

## Code rules

1. **Frozen contracts** (`core.registry`, `core.ids`, `core.envelope`, `core.errors`, `core.paths`):
   compatible changes only (new fields/parameters with defaults), through the maintainers. Never invent error
   codes: the list is in `src/satk/core/errors.py`; warnings are `warn: ["CODE: text"]`.
2. **A new operation** is an `@op` in `src/satk/<pkg>/ops.py` (example in the `src/satk/core/registry.py`
   docstring). Parameters come from annotations (`str int float bool list[...] dict Literal[...] X | None`),
   descriptions from the `Args:` docstring section; `summary` <= 300 characters (English: it is the MCP
   description), `summary_ru` only feeds `satk help --ru`. Return a `dict` (`envelope.table/obj`) or raise
   `SatkError` with a `hint`. SID providers, `@doctor_check`, `@status_provider` live in the package too; there
   are no central lists. A parameter without a default is a CLI positional.
3. **MCP budget.** `tools/list` of all MCP tools <= 16 384 bytes (`satk mcp selftest`, `tests/mcp`). Now 15 727
   bytes for 27 tools, with a share per group (`GROUP_BUDGET` in `src/satk/mcp/adapter.py`). New operations are
   `mcp=False`: agents reach them through `satk_ops`/`satk_op`. Mark operations an agent must not run (they write
   the user's config or the game copy, block, download, delete data) with `@satk.mcp.generic.cli_only(reason,
   consent=...)` above `@op`.
4. **Answers save tokens:** table envelope, `limit=20` by default, no `null` and no empties, coordinates to 0.01,
   paths via `paths.jpath()` (absolute, forward slashes), images as paths, never base64.
5. **Fast imports:** `ops.py` and the stdlib packages (`core formats index re game saap`) import no
   Pillow/numpy/mcp at module level, only inside functions (`errors.require_module`). Checked by
   `tests/core/test_layout.py`. `satk version` <= 0.6 s on an idle machine.
6. **Writes** only after `paths.ensure_writable()` (or through `paths.atomic_write`); game files only through
   `paths.open_ro()`. Write only under `work\` (and into your own repositories).
7. **Determinism:** the same input gives the same output (sorting, no time or randomness in data); derived
   files are content-addressed (`cache/tex/<hh>/<pix>.png`). A repeat recomputes nothing.
8. **Temp files:** `paths.tmp("<id>")` = `<workspace>\work\tmp\<id>\`. Never delete other people's files.
9. **Windows path strings** only through `pathlib`/raw strings: the literal `"models\x360btns.txd"` breaks
   (`\x36`); it happened before.
10. **No machine paths in code** (`docs/en/install.md`): paths come from `cfg().paths.*` and
    `satk.core.detect`, examples use `<workspace>`; `tests/core/test_portability.py` greps `src/satk`, and
    `tests/docs/test_public_text.py` checks the public texts.
11. **Package data** (`data/`) is read through `satk.core.resources` (`read_json`, `data_path`): wheels carry it.
12. **Language:** code, comments, docstrings, summaries, help, messages, warnings and data an agent reads are
    English. The only Russian text in code is `summary_ru`.

## Tests

- pytest; synthetic fixtures are generated in the tests; **game files never go into the repository**.
- Markers: `game` (reads `gta-sa-clean`/the original install read-only; skipped without the copy), `viewer`,
  `blender`, `engine` (skipped without the tool), `slow`, `e2e` (`tests/e2e`). `SATK_TEST_NO_SKIP=1` turns
  skips into failures (acceptance). Tests that open the Ariane window also need `SATK_TEST_LIVE=1`.
- Fixtures in `tests/conftest.py`: `satk_home` (isolated workspace; it drops every `SATK_*` variable),
  `run_cli` (in-process CLI), `isolated_ops`, `clean_root`, `installed_root`, `repo_root`. Live runs write into
  their own `SATK_PATHS_WORK` (example: `tests/e2e/test_scenario.py`), not into the shared `work\`, and remove
  it with `satk.docs.cleanup.remove_tree` (retries + an error on leftovers), never `rmtree(ignore_errors=True)`.
- Golden numbers live in `tests/golden/*.json` (numbers only, no assets). In a restricted sandbox (the Codex
  one) the gate works around what is denied; direct runs: `PYTHONPATH=src;tests` and `-p sandbox_compat`.
- Full run: `python -m pytest -q -p no:cacheprovider` (~4 min; keep TEMP on a drive with free space);
  the end-to-end scenario with a window: `SATK_TEST_LIVE=1 python -m pytest -m e2e`.

## Assets and licenses

- Never commit game assets. The hook `scripts/hooks/pre-commit` runs `satk dev assetguard --staged`;
  `--no-verify` is forbidden. A synthetic fixture that looks like an asset goes into `.assetguard-allow` with
  its sha256.
- No code of gta-reversed, Ariane/euryopa, samp-source, Neon or DragonFF in the MIT code. Fragments of
  gta-reversed (`re_src`) are only printed, never written to files. `blender/satk_blender` is GPL and imports `bpy`.
- Vendored MIT code keeps its license and is listed in `NOTICE.md` (`vendor/`, `data/notices/`).

## Documentation

- Model-facing text is English only: this file, `docs/agent/**`, skills, MCP instructions and descriptions,
  `satk help` without `--ru`. User docs: English first (`README.md`, `docs/en/<page>.md`) with a pure-Russian
  mirror (`README.ru.md`, `docs/ru/<page>.md`). Never mix languages inside one file.
- Public texts carry no machine paths (write `<workspace>`, `<game>` or a relative path) and no references to
  internal planning notes: `tests/docs/test_public_text.py`.
- Every component has a page following `docs/en/_template.md`, written by its owner in English at
  `docs/en/<pkg>.md` with its Russian mirror; the docs owner builds the index pages.
- "Quick example" sections are executable: `satk dev docs-smoke docs/en/<page>.md` (all pages: `satk dev docs-smoke`,
  ~1.5 min; a typo is an error; pages with `index build`/`re build` run in their own temp work).
- Paths and links: `satk dev linkcheck docs CLAUDE.md README.md`; en/ru structure: `satk dev docs-parity`.
- After changing operations or `schema.sql`: `satk dev gen-docs` (check: `satk dev gen-docs --check`).
- Agent workflows S1-S8 are executable: `satk dev workflow-cost [S1 ...] [--steps]` measures calls and tokens,
  `tests/e2e/test_workflows.py` checks the answers; S9-S16 are documented in `docs/agent/workflows.md`. When you
  change a tool, re-measure and update `docs/agent/workflows.md` and section 6 of `docs/agent/SKILL.md`.
- Agent docs are published: `satk dev sync-agent-docs` (`--check` compares) copies `docs/agent/SKILL.md` and the
  workspace `CLAUDE.md` source into the workspace; `satk agent install-skill --dest .claude-plugin/satk/skills`
  refreshes the plugin copy (`tests/mcp/test_skill_plugin.py` compares it).
- Commits end with the attribution line from the session's system instructions.
