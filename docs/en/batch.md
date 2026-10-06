# Batch mode and recipes: one command over many files, saved multi-step checks

[Русская версия](../ru/batch.md)

Package: `satk.batch`.

## What it is

Modding work is repetitive: lint fifty cars, extract every texture of a mod, run the same four checks before each
release, look at every crash the same way. `satk batch` runs any satk operation once per input (a glob, the entries
of an IMG archive, a list file, a folder, an SQL query over the index or a list of SIDs) and answers with counts and
the first failures instead of fifty answers. `satk recipe` runs a saved list of operations with variables, where a
step can use the answer of an earlier one. Both write only under `<workspace>\work\` (results files and reports);
the game files are read only.

## Quick example

```powershell
satk recipe list
satk batch formats.dump --over "models/gta3.img/infer*" --dry-run
satk batch asset.lint --over "models/gta3.img/infernus.*" --arg fail_on=error --jobs 2
satk crash sample --kind sp
satk recipe run crash-triage --dry-run
satk recipe run crash-triage
```

What comes back (shortened):

```json
{"ok":true,"cols":["name","source","summary","vars"],"rows":[["check-mod-before-release","shipped","Before publishing a mod - …","mod*, profile"],["crash-triage","shipped","Triage a game crash - …","crash, game"],…]}
{"ok":true,"cols":["n","item","state","args"],"rows":[[1,"infernus.dff","run","{\"target\": \"<game>/models/gta3.img/infernus.dff\"}"],[2,"infernus.txd","run",…]],"warn":["OVER: matched under the vanilla game root <game>"],"op":"formats.dump","dry_run":true,"items":2,"source":"img",…}
{"ok":true,"cols":["item","status","code","msg"],"rows":[],"op":"asset.lint","items":2,"counts":{"ok":2,"fail":0},"totals":{"fatal":0,"error":0,"warn":0,"info":1},"jobs":2,"out":"<workspace>/work/out/batch/asset.lint-78a5636d.jsonl"}
{"ok":true,"cols":["step","op","status","info"],"rows":[["recent","crash.list","ok","3 row(s)"],["analyze","crash.analyze","ok","4 row(s); crash=0xC0000005 ACCESS_VIOLATION …; fn=CPickup::GiveUsAPickUpObject+0x29; …"],["modules","crash.info","ok","3 row(s)"],["logs","crash.logs","skipped","when: false"],["bisect","crash.bisect","skipped","when: false"]],"recipe":"crash-triage","counts":{"ok":3,"skipped":2},"out":"<workspace>/work/out/recipes/crash-triage.json"}
```

When an input fails, the batch answers `CHECK_FAILED` (exit code 1) with the failed inputs as a table:
`satk batch asset.lint --over "mods/cars/*.dff" --arg fail_on=error` on 50 files with 3 broken ones says
`3 of 50 input(s) failed, 47 ok (CHECK_FAILED 3)` and lists the three.

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk batch <op> --over <inputs> [--arg k=v ...] [--jobs N] [--resume] [--out f.jsonl] [--dry-run]` | — (through `satk_op`) | runs `<op>` once per input; one JSONL record per input in `--out` (default `<workspace>/work/out/batch/<op>-<hash>.jsonl`); answer: `counts`, `by_code`, `totals` (summed `summary` counters), the first failures |
| `satk batch report <results.jsonl> [--status fail] [--field summary.fatal ...]` | — | table of a results file: status, error, or fields of each answer; paging with `--cursor` |
| `satk recipe list` | — | the shipped recipes and yours (`<workspace>/work/recipes/*.yaml`; yours win on the same name) |
| `satk recipe show <name\|file.yaml>` | — | variables and steps, and whether each step's operation is installed |
| `satk recipe run <name\|file.yaml> [--var k=v ...] [--dry-run]` | — | runs the steps; table `step/op/status/info`; every step's full answer in `--out` (default `<workspace>/work/out/recipes/<name>.json`) |

`--over` takes: a glob (`mods/*.dff`, `mods/**/*.txd`; a relative glob that matches nothing here is tried under the <!-- linkcheck: ignore -->
game root, so `models/gta3.img/*.dff` works anywhere); IMG entries (`<file.img>/<pattern>`); `@list.txt` (one input
per line, or a JSON object of arguments per line; `@-` reads stdin); a folder (its files and subfolders); SQL
(`SELECT ...` over the index of `--profile`; columns named like the operation's parameters fill them); or
comma-separated SIDs and names (`model:411,model:415`). Each input goes to the operation's first required
parameter (`--param` picks another). `--arg` values may use `{item} {name} {stem} {ext} {parent} {n}`:
`--arg out=work/out/tex/{stem}`. `--param none` passes the input only to these templates, so a batch can run a
recipe per mod: `satk batch recipe.run --over "mods/*" --param none --arg recipe=check-mod-before-release
--arg var=mod={item} --arg out=work/out/recipes/{name}.json`.

Shipped recipes:

| Recipe | Variables | Steps |
|---|---|---|
| `check-mod-before-release` | `mod`, `profile` | `mod inspect`, `mod check` (fails on fatal/error findings), texture audit (when installed), `id conflicts` |
| `many-cars-lint-and-pack` | `cars`, `img`, `out_dir`, `fail_on`, `profile`, `jobs`, `addons` | a lint batch over every DFF and every TXD, texture audit, `id conflicts`, `img build`, `formats ls` of the new IMG; with `addons` (a list file of `mod add` arguments) an add-on folder per car |
| `export-all-textures-of-a-mod` | `mod`, `force` | `texture extract` of one TXD, or a batch over the loose TXDs and one over the TXDs inside the mod's IMGs |
| `crash-triage` | `crash`, `game` | `crash list`, `crash analyze` (newest crash by default), `crash info` modules of a dump, `crash logs` and `crash bisect` of a game folder |

## Writing a recipe

A recipe is a YAML file (a strict subset: mappings, lists, quoted or plain values, `[a, b]`, `{k: v}`, `|`
blocks; no anchors or tags), JSON or TOML. Put it in `<workspace>/work/recipes/` to run it by name.

```yaml
name: lint-and-dump
summary: Lint one file and dump it when it is clean.
vars:
  file: {required: true, help: "a DFF, TXD or COL"}
  sev: warn
steps:
  - id: lint
    op: asset.lint
    input: ${file}
    args: {sev: "${sev}"}
    fail_if: "${steps.lint.summary.error|default:0}"
    on_error: continue
  - id: dump
    op: formats.dump
    input: ${file}
    when: "${steps.lint.summary.fatal|not}"
```

- `op` is an operation name or a list of candidates (the first installed one runs). When none is installed, the
  step is `skipped: op not available` and steps that need its answer are skipped too; `requires: mod.add` does the
  same for an operation the step needs indirectly (the inner operation of a `batch` step).
- `${name}` is a variable (`--var name=value`), `${work}`, `${cwd}`, `${recipe_dir}` or a field of an earlier
  answer: `${steps.lint.summary.fatal}`, `${steps.ls.rows.0.1}`, `${steps.ls.col.name}` (a column as a list).
  Filters: `not bool len first json name stem parent ext lower eq:<value> default:<value>`. A value that is a
  single reference keeps its type (a list stays a list); a null value leaves the argument out.
- `when` / `unless` skip a step; `fail_if` turns an answer into a failure; a failure stops the recipe unless
  `on_error: continue`. Any failed step makes the answer `CHECK_FAILED`.
- `--dry-run` checks the variables, the operations and the arguments and shows the plan; it runs and writes
  nothing.

## How it works

- **In one process.** Each input is a normal call of the operation (the same validation as the command line).
  Operations known to be thread-safe (lint, dumps, lookups, mod inspection, crash analysis) use `--jobs` threads;
  the others run one by one (`warn: JOBS`).
- **Resume.** Every record is appended to the results file as soon as it is known, so an interrupted batch keeps
  its work; `--resume` skips the inputs whose key (sha256 of the operation and its arguments) already has an `ok`
  record and runs the failed and missing ones. At the end the file holds one line per input, in input order.
  `--max-fail N` stops early.
- **Deterministic.** Inputs are sorted; the same batch gets the same default results file; results and reports
  carry no timestamps.
- **Consent.** Operations an AI assistant may not run (they change the user's setup or delete data, block, or
  are command-line only) run inside a batch or a recipe only with `--yes` typed on the command line; through
  MCP the batch is refused (`CONSENT_REQUIRED`/`UNSUPPORTED`). Recipes check every step before the first one
  runs. The MCP server itself, `view mock` and `dev gate` never run in a batch.

## Limitations and known issues

- Zip mods are not expanded by globs: extract them first (`mod inspect`/`mod check` read zips directly).
- A batch keeps up to 100 000 inputs; SQL inputs page through the index 500 rows at a time.
- Batches and recipes nest four levels at most; a recipe that runs itself is refused.

## Python API

```python
from satk.batch.runner import run_batch
from satk.batch.recipe import run_recipe

summary = run_batch("asset.lint", "mods/*.dff", arg=["fail_on=error"], jobs=4)
report = run_recipe("check-mod-before-release", var=["mod=mods/mycar"])
```
