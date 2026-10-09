# `satk asset lint`: the asset linter

[Русская версия](../ru/lint.md)

Package: `satk.lint`. Rules are data: `data/lint_rules.json`.

## What it is

Checks mods and vanilla files before the game is started: DFF (structure, limits, UV, prelight, normals, vertex
data, budgets), TXD (platform, format, powers of two, DXT blocks, mip chain, names), COL (name of at most 21
characters that equals the model's, vertices within ±256 m, boxes without rotation, flags, bounds, surfaces, face
lighting), IDE (name lengths, ID range, draw distance, duplicates) and the IDE ↔ DFF/TXD ↔ COL links by name. On
top of the format it checks what the game expects of vehicles, peds and weapons: lamps, paint and dirt, the
environment sheen, frames, wheel size, shadow mesh, damage parts, skin, muzzle flash and flat shading. Every rule
cites its source (a `gta-reversed` file:line, plugin-sdk, a measurement on the vanilla game) and many name the
crash they prevent. The linter only reads files; it writes only with `--save`, into `work\out\lint\` (created on
the first save), and caches the vanilla rule rates of `--baseline` in `work\cache\lint\`. <!-- linkcheck: ignore -->

## Quick example

```powershell
satk asset lint-rules --rule veh --preset sa_plus
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

A car made by an AI agent (the Tesla benchmark, saved as a `premier` replacement), linted with the default tier:
`satk asset lint mymod --preset sa_plus --baseline vanilla` answers `dff.flat_shading` (normal bend 0.81°,
vanilla cars 7.2-10.9°), `veh.paint_dirt` (the body is on `vehiclegeneric256`, so it never gets dirty),
`veh.env_uv2`, `veh.dummy_side` (`ped_arm` at x = +0.81), `veh.dam_ratio` (`_dam` parts at 0.44 of `_ok`) and
`dff.vert_sharing`, each row with its `vanilla_rate` and a `hints` entry that names the fix. Vanilla `premier`
gets none of them.

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk asset lint TARGET [--sev info\|warn\|error\|fatal] [--rule P …] [--preset game\|strict\|vanilla\|sa_plus] [--baseline vanilla] [--baseline-rate R] [--like SID] [--config F] [--no-index] [--fail-on S] [--save] [--profile P] [--limit N] [--cursor C]` | — (via `satk_op`) | a `rule/sev/file/msg` table (plus `vanilla_rate` with `--baseline`), a `summary` of all findings, `hints` (how to fix the rules on the page), `suppressed` (what `--baseline`/`--like` dropped) |
| `satk asset lint-rules [--rule P …] [--preset P] [--config F] [--ru] [--ref] [--limit N]` | — | the rules: id, severity, parameters, what is checked; `--ref` adds the source, the fix and the crash or defect the rule prevents |

`TARGET` is any of:

- a `.dff .txd .col .ide` file, an IMG archive (every DFF/TXD/COL inside), an `<img>/<entry>` entry;
- a mod folder (recursive, IMGs inside are opened). A game root (it has `data/gta.dat`) is checked the way the
  game loads it: only the IDEs from `data/default.dat` and `data/gta.dat`, loose `.col` files only from `COLFILE`;
- a SID: `model:411` (the model's DFF, TXD chain and COL), `dff:`, `txd:`, `col:` (one collision model),
  `file:`, `ide:`.

A relative path is looked up from the current folder, then from the profile root (case-insensitive). Names that
are not in the target (a mod reuses a vanilla TXD or adds collision to a vanilla model) are looked up in the
profile index; `--no-index` turns that off. SID targets always need the index (`INDEX_MISSING` →
`satk index build`). A replacement (`premier.dff` without an IDE line) gets the class, vehicle type and wheel size
of the vanilla model from the index.

Severity: `fatal`: the game crashes or cannot read the file; `error`: it loads but wrongly (no collision, garbage
in a texture, an invisible model); `warn`: it works but breaks a convention (or, in `game`/`strict`, a
performance budget); `info`: advice.
`--fail-on error` turns findings of that level and above into a `CHECK_FAILED` error (exit code 1), for scripts
and CI; the default `never` always returns `ok`.

To check a Mod Loader mod as a whole (ID collisions, files Mod Loader skips, dropped game records) use
`satk mod check`, which adds these lint findings: see [modinspect.md](modinspect.md).

## Presets and detail tiers

| Preset | For | Budgets |
|---|---|---|
| `game` (default) | any file; calibrated on vanilla (0 fatal) | generous caps |
| `strict` | new content, research 22 | vanilla p90 per class; vehicles on `veh.hd_tris` (p90 per type), the whole file only capped |
| `vanilla` | detail tier `vanilla`: new content that must sit unnoticed next to vanilla | triangle and material counts as `info` (vanilla p98 per class, vanilla maxima per vehicle part): reference, never graded |
| `sa_plus` | detail tier `sa_plus`, the default for new assets: the SA language at a free detail level | no triangle or material budget runs; the engine limits stay (65,535 vertices per geometry, names, frames, TXD, collision) |

Mipmaps are class-aware in every preset: vanilla vehicle, ped and weapon textures have none (0 of 996), so
`txd.mips_missing` is only `info` for TXDs of those classes; the class of a TXD is the class of the models that use
it. Your own thresholds go into a `--config my.json` file:
`{"rules": {"txd.size_max": {"params": {"max": 512}}, "col.empty": {"enabled": false}, "txd.pow2": {"class_sev": {"cars": "error"}}}}`.

## Rules for vehicles, peds and weapons

| Rule | Checks | Prevents |
|---|---|---|
| `veh.frames` | frames every vanilla vehicle of the type has (wheel dummies, chassis, seats, lights) | crash `0x004C7DAD` (no wheel) |
| `veh.dummy_side` | `_lf`/`_lb`/`ped_arm` frames on the left (x < 0), `_rf`/`_rb` on the right | mirrored doors, the arm outside the car |
| `veh.wheel_scale` | wheel mesh diameter = the IDE wheel size (vanilla ratio 0.99-1.03) | wheels floating or sunk |
| `veh.shadow_mesh` | an embedded COL3 with a shadow mesh | crash `0x0070FB39` (no shadow model) |
| `veh.env_uv2` | the `xvehicleenv128` sheen only on geometry with a second UV set | invisible paint sheen |
| `veh.light_key_tex` | lamp key colours only on `vehiclelights128` | lamps that never light |
| `veh.paint_dirt` | the primary paint on `vehiclegrunge256` | a body that never gets dirty |
| `veh.upgrade_frames` | the `ug_*` frames of the replaced vanilla model | crash `0x007F0BF7` (tuning part) |
| `veh.hd_tris`, `veh.part_tris` | triangles of the high-detail model (chassis + parts + the wheel once) and of each part | nothing by itself: a performance hint in `game`/`strict`, `info` in `vanilla`, off in `sa_plus` |
| `dff.clump_ext_dup` | one Extension chunk per clump (a broken writer appends a second one, with the embedded collision in it) | a vehicle that streams but never appears; lost collision |
| `veh.dam_ratio` | median `_dam`/`_ok` triangle ratio within vanilla's 0.5-1.4 | decimated damage parts |
| `dff.flat_shading`, `dff.vert_sharing` | normal bend of cars/peds above the class floor; vertices split per face with flat normals | the faceted look |
| `ped.skin` | 32 bones, at most 4 weights, bone indices in range | crash `0x007C4781` (skin) |
| `weap.flash` | a textured `gunflash` atomic on firearms | shots without a muzzle flash |
| `col.face_light_zero` | collision faces all lit 0 | dark peds, cars with headlights on |
| `ide.draw_bigbuilding` | draw distance > 300 on a model with collision | collision switched off |
| `mat.alpha_draw_last` | see-through material colours without the draw-last flag (off in `game`/`vanilla`: vanilla does it on 42 % of such models) | hidden geometry behind glass |

`dff.flat_shading` uses the shading numbers of `satk.style` (`shade.normal_bend`, `shade.flat_share`). When the
median fold between neighbouring faces is also low (below half of the vanilla p10 of 7.9° for cars; vanilla cars
have 7.9 / 11.7 / 21.4° at p10 / p50 / p90), the message adds that the model is mostly flat panels: other normals
cannot round them, the body needs more curvature segments.

Each enabled rule of the table fires on at most 2 % of what it examines in the vanilla game (`--baseline vanilla` prints the
rate). 2DFX entries (crash `0x00B4C2DD`) are checked by `satk fx2d check`: see [fx2d.md](fx2d.md).

## Reference-aware answers

- `--baseline vanilla` lints the whole vanilla game once with the same preset (about 20 s, then cached in
  `work\cache\lint\`), adds a `vanilla_rate` column (share of the vanilla subjects the rule fires on) and drops
  findings of rules vanilla itself triggers on at least `--baseline-rate` (default 0.05) of its subjects, such as
  missing mipmaps. <!-- linkcheck: ignore -->
- `--like model:426` lints that reference model (its DFF, TXD chain and COL) and drops the rules it triggers too.
- `suppressed` lists the dropped rules with their counts; `--sev info` still shows what is left.

## How it works

- The checks are the `dff`, `txd`, `col`, `ide`, `link` and `semantics` modules of `satk.lint`; the parsers are
  `satk.formats`. The code only says "rule X fired with these fields"; the severity, thresholds, message text, fix
  (`hint`) and the prevented crash come from `data/lint_rules.json`.
- The model class (for prelight, normals, budgets and the semantic rules) comes from the IDE: `objs`/`tobj` →
  `map` (or `lod` when the name contains `lod` or the draw distance is over 300; `pickup` for the pickup models;
  `upgrade` for `veh_mods.ide`; `interior_prop`/`interior_shell` for the interior IDEs and `gta_int.img`), `cars`,
  `peds`, `weap`, everything else `other`. A single file without an IDE and without an index is guessed: with a
  skin it is `peds`, with embedded collision `cars`, otherwise `map`. `player.img` entries are clothes (`other`).
- Shading metrics follow `satk.style` (`shade.normal_bend`: area-weighted angle between corner and face normals;
  vanilla `premier` 10.64°); when `satk.style` is installed its implementation is used.
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
  models exist in the game); `veh.upgrade_frames` and the firearm test of `weap.flash` need the index.

## Python API (if other packages use it)

```python
from satk.lint.runner import lint
rep = lint("models/gta3.img", preset="sa_plus", only=["veh", "dff.flat_shading"])
print(rep.summary, rep.checked, [f.row() for f in rep.at_least("warn")])
```
