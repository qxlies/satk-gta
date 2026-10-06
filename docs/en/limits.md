# Limits: plan engine capacity before installing mods

[Русская версия](../ru/limits.md)

Package: `satk.limits`.

## What it is

`satk limits plan` reads the effective installation and additional mods, compares their demand with GTA SA
1.0 US capacities, and writes a report plus separate fastman92 and Open Limit Adjuster INI fragments under
`<workspace>/work/out/limits/<plan>/`. It does not install mods, edit the game, or build an index.

## Quick example

```powershell
satk limits plan --profile vanilla --limit 8
satk limits plan --profile installed --limit 8
```

The table has `limit`, `need`, `stock`, `headroom`, `risk`, `how_to_raise`, `unit`, and `basis` columns.
`files` contains the complete JSON report and both INIs; `next` pages the table. Pagination never truncates
the saved report or settings. Repeating an unchanged plan preserves the existing output files.

Verified stock examples:

| Limit | Need | Stock | Headroom |
|---|---:|---:|---:|
| Vehicle model infos | 212 | 212 | 0 |
| Ped model infos | 276 | 278 | 2 |
| Weapon model infos | 50 | 51 | 1 |
| Object model infos | 14045 | 14070 | 25 |
| Streaming COL slots, including generic | 252 | 255 | 3 |
| IDE 2DFX | 97 | 100 | 3 |
| IPL entry-exits, before runtime additions | 376 | 400 | 24 |

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk limits plan [--profile installed]` | `satk_op` → `limits.plan` | Include enabled installed Mod Loader mods. |
| `satk limits plan --profile vanilla` | same operation | Inspect the clean copy. |
| `satk limits plan --profile installed --mods mods/city mods/cars` | same operation | Add folders, ZIPs or IMGs, using Mod Loader priorities. |
| `satk limits plan --profile mods/city --base vanilla` | same operation | Use a mod path as the profile, over a specified base. |
| `satk limits plan --area 2495 -1666 150` | same operation | Estimate a streaming scenario near exterior placement centres. |

The Python operation accepts `profile` and `mods` as lists. A profile name may precede paths in `profile`.
Paths are resolved from the current directory, then the game root. Duplicate paths are counted once.

## How it works

The planner reuses the Mod Loader resolver, capacity facts, format parsers, `texture.budget` sector accounting,
and `crash.known`. It counts effective IDE allocations, model-ID span, IPL records, COL archives and collision
records, TXDs, IMG entries, effects, paths, handling, weapon data, population groups, upgrades and timecycle rows.
Stock facts carry source symbols and addresses; readable executable operands are reported separately.

`exact` means a file-derived count. `inventory` means a total that is not a simultaneous engine pool: for
example, vanilla has 50935 placements and 18795 DFF effects. Its eight IMG files use six streaming IMG slots;
the animation/cutscene archives are opened separately. Clothing TXDs have a separate namespace.

`lower_bound` is a minimum; its headroom is at most the displayed number. Buildings and dummies use permanent
placements plus the largest streamed block. The default memory estimate uses the largest individual model
and its TXD chain. `--area` includes complete blocks intersecting the circle; `scenario` is an estimate,
not measured peak usage. Missing models, TXDs and unresolved placements make estimates incomplete.
`runtime` and `unknown` rows do not claim spare capacity. `at_stock` means zero room; `exceeded` means the
measured requirement exceeds stock. A plan with no exceeded rows is not proof that the game cannot crash.

INI assignments cover measured excesses with a target of 10% spare capacity. OLA uses `[SALIMITS]`; memory
values are MiB and OLA's documented 2048 MiB cap is respected. Existing numeric declarations are not lowered;
symbolic values such as `unlimited` or percentages require review. Declarations are compared with demand,
but do not prove an ASI hook is active. Review the fragment against your adjuster version and use one adjuster
per limit. Unsupported settings and limits without a known crash signature are stated explicitly.

## Limitations and known issues

- Live objects, traffic, peds, vehicle structures, collision-model occupancy, rendering and script pools need
  runtime measurements. No guessed assignments are generated for them.
- Unsupported multi-mod merges, including `weapon.dat`, `cargrp.dat` and `carmods.dat`, produce unknown demand.
  Readme handling follows the existing resolver; weapon/carmods readme-only edits are not resolved.
- Stock `cargrp.dat` contains up to 29 names per line, but the stock reader consumes at most 23. Both counts
  are shown, with a truncation warning. Vanilla also has 42 model definitions without DFFs, including dynamic
  special-ped and cutscene placeholders; these remain visible as unresolved resources.
- Metadata reads are bounded to 128 MiB per binary asset and 16 MiB per text input. ZIPs containing unreadable
  nested IMG payloads must be extracted first. Invalid counted inputs fail with a structured error.

Related: [mod inspection](modinspect.md), [free model IDs](idmgr.md), [texture budgets](txdopt.md),
[crash signatures](crash.md), and [engine facts](kb.md).
