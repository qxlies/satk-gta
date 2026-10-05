# `satk asset lint`: the asset linter

[Русская версия](../ru/lint.md)

Package: `satk.lint`. Rules are data: `data/lint_rules.json`.

## What it is

Checks mods and vanilla files before the game is started: DFF (structure, limits, UV, prelight, normals, vertex
data, budgets), TXD (platform, format, powers of two, DXT blocks, mip chain, names), COL (name of at most 21
characters that equals the model's, vertices within ±256 m, boxes without rotation, flags, bounds, surfaces),
IDE (name lengths, ID range, draw distance, duplicates) and the IDE ↔ DFF/TXD ↔ COL links by name. Every rule
cites its source (a `gta-reversed` file:line, plugin-sdk, a research report). The linter only reads files; it
writes only with `--save`, into `work\out\lint\` (created on the first save). <!-- linkcheck: ignore -->

## Quick example

```powershell
satk asset lint-rules --rule col
satk asset lint models/gta3.img/infernus.dff
satk asset lint model:17613 --sev info --limit 5
satk asset lint data/maps/generic/dynamic2.ide
```

What comes back (shortened; the last command finds a real error in the game data):

```json
{"ok":true,"cols":["rule","sev","file","msg"],
 "rows":[["ide.draw_min","error","data/maps/generic/dynamic2.ide","line 164: model 1489 'DYN_SALE_POST': draw distance 1 < 4, the game re-reads the line in the old mesh-count form"]],
 "n":1,"total":1,"summary":{"fatal":0,"error":1,"warn":0,"info":0},"files":1,"by_rule":{"ide.draw_min":1}}
```

The whole clean game copy (`satk asset lint "<workspace>\gta-sa-clean"`, about 12 s, 19,711 files): 0 fatal, 1 error
(the same line), 464 warn (352 are textures missing from the TXD chain; 59 are map models with neither prelight
nor normals).

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk asset lint TARGET [--sev info\|warn\|error\|fatal] [--rule P …] [--preset game\|strict] [--config F] [--no-index] [--fail-on S] [--save] [--profile P] [--limit N] [--cursor C]` | — (via `satk_op`) | a `rule/sev/file/msg` table and a `summary` of all findings; rows at or above `--sev` (default `warn`) |
| `satk asset lint-rules [--rule P …] [--preset P] [--config F] [--ru] [--ref] [--limit N]` | — | the rules: id, severity, parameters, what is checked, source (`--ref`) |

`TARGET` is any of:

- a `.dff .txd .col .ide` file, an IMG archive (every DFF/TXD/COL inside), an `<img>/<entry>` entry;
- a mod folder (recursive, IMGs inside are opened). A game root (it has `data/gta.dat`) is checked the way the
  game loads it: only the IDEs from `data/default.dat` and `data/gta.dat`, loose `.col` files only from `COLFILE`;
- a SID: `model:411` (the model's DFF, TXD chain and COL), `dff:`, `txd:`, `col:` (one collision model),
  `file:`, `ide:`.

A relative path is looked up from the current folder, then from the profile root (case-insensitive). Names that
are not in the target (a mod reuses a vanilla TXD or adds collision to a vanilla model) are looked up in the
profile index; `--no-index` turns that off. SID targets always need the index (`INDEX_MISSING` →
`satk index build`).

Severity: `fatal`: the game crashes or cannot read the file; `error`: it loads but wrongly (no collision, garbage
in a texture, an invisible model); `warn`: it works but breaks a budget or a convention; `info`: advice.
`--fail-on error` turns findings of that level and above into a `CHECK_FAILED` error (exit code 1), for scripts
and CI; the default `never` always returns `ok`.

To check a Mod Loader mod as a whole (ID collisions, files Mod Loader skips, dropped game records) use
`satk mod check`, which adds these lint findings: see [modinspect.md](modinspect.md).

## How it works

- The checks are the `dff`, `txd`, `col`, `ide` and `link` modules of `satk.lint`; the parsers are `satk.formats`.
  The code only says "rule X fired with these fields"; the severity, thresholds and message text come from
  `data/lint_rules.json`. Your own thresholds go into a `--config my.json` file:
  `{"rules": {"txd.size_max": {"params": {"max": 512}}, "col.empty": {"enabled": false}}}`.
- Presets: `game` is calibrated on vanilla (0 fatal, no false positives); `strict` is for new content:
  vanilla p90 budgets per class, textures ≤ 512, missing mipmaps and night colours are `warn`,
  collision is required.
- The model class (for prelight, normals and budgets) comes from the IDE: `objs`/`tobj` → `map` (or `lod` when
  the name contains `lod` or the draw distance is over 300), `cars`, `peds`, `weap`, everything else `other`.
  A single file without an IDE and without an index is guessed: with a skin it is `peds`, with embedded
  collision `cars`, otherwise `map`.
- Links: IDE model → `<name>.dff`, the IDE's TXD → its `txdp` parents → `vehicle` for cars; a COL model with the
  same name (≤ 21 characters) for map objects (except LODs); material textures are looked up along the whole
  chain. Orphans (a DFF, TXD or COL without a model) are `info`/`warn`.
- COL boxes are AABBs by format (`CColBox = {min, max}`); the file stores no rotation. A rotated or mirrored box
  exported as an AABB is caught as `min > max` (`col.box_inverted`) or as a primitive outside the model bounds
  (`col.outside_bounds`).
- Determinism: the same input gives the same answer (sorted by severity, file, rule). `--save` writes all
  findings to `work\out\lint\<target>-<hash>.json`; the name depends only on the target, preset and rules. <!-- linkcheck: ignore -->

## Limitations and known issues

- Pool limits (`limit_def`, the number of models, TXD and COL slots) and ID conflicts with SA-MP are not checked
  yet.
- Vanilla placeholders (`special01…`, `cutobj*`, `clothes*`, `null`, `airtrain_vlo`) are not reported as missing
  models; the list is in the parameters of `link.dff_missing`.
- Without an index and without IDEs in the target the link checks are skipped (the linter does not know which
  models exist in the game).

## Python API (if other packages use it)

```python
from satk.lint.runner import lint
rep = lint("models/gta3.img", preset="strict", only=["col", "dff.uv"])
print(rep.summary, [f.row() for f in rep.at_least("error")])
```
