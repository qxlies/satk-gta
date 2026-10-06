# `satk style`: the vanilla SA style in numbers, and checks against it

[Русская версия](../ru/style.md)

Package: `satk.style`.

## What it is

"Make it look like San Andreas" needs numbers, not adjectives. `satk style` measures every model of the
vanilla game once (triangles, how soft the shading is, UVs, size, texel density, prelight, collision) and
answers what a class of models looks like: a sedan, a 1-2 m street prop, a ped, a weapon. `satk asset check`
compares your own model (a `.dff`, a mod folder or a game model) with its class: frames and parts, the numbers,
and the engine conventions (paint on the dirt texture, lamp colour keys, dummies on the right side).
`satk style texture` tells whether a texture looks like a vanilla one of its role. The game is only read; the
cache is written to `<workspace>\work\style\`.

Two detail tiers: `vanilla` (inside the measured band of the class, for replacements that must blend in) and
`sa_plus` (the default for new assets: the same look with higher triangle budgets where the outline and the
curves are; these numbers are a proposal until checked in game, and every answer says so).

## Quick example

```powershell
satk style profile car.sedan --tier vanilla --metrics veh.hd_tris shade.normal_bend dims.L
satk asset check model:426 --tier vanilla
satk asset anatomy model:426 --md
```

What comes back (shortened):

```json
{"ok":true,"cols":["metric","p10","p50","p90","n","lo","hi"],"rows":[["veh.hd_tris",2032,2207,2338,24,2032,2338],["shade.normal_bend",7.7012,9.9785,13.9871,24,7.7012,13.9871],["dims.L",5.4961,5.815,6.1527,24,5.4961,6.1527]],"peer_set":"car.sedan (24 vanilla models)","tier":"vanilla (measured)","exemplars":["model:466 glendale","model:442 romero","model:550 sunrise"]}
{"ok":true,"cols":["check","part","value","p10","p50","p90","verdict","hint","ref"],"rows":[],"cls":"car.sedan","verdict":"pass"}
```

The first call builds the cache (about 10-20 s); later calls answer in a fraction of a second.

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk style profile <class or model> [--like SID] [--tier vanilla\|sa_plus] [--metrics ...]` | — (through `satk_op`) | p10/p50/p90 of the peer set and the tier band; exemplars; scale anchors; `--classes` lists the classes |
| `satk asset check <dff\|folder\|SID> [--like SID] [--cls auto] [--tier] [--md] [--full]` | — | structure, metrics and engine rules against vanilla; `verdict` pass, review or fail |
| `satk asset anatomy <dff\|SID> [--md]` | — | frame tree with positions, parts, materials by role, collision and TXD summary |
| `satk style texture <png\|txd\|folder\|tex:SID> [--role auto]` | — | a texture against the vanilla textures of its role (interior, wheel, decal, body, ped, weapon, wall, ground, prop) |
| `satk style brief-check <brief.md> [--cls auto]` | — | wording in a task brief that leads to known mistakes ("crisp", "flat colour", "never photographs", ...) |
| `satk style card <class> [--tier] [--md]`, `satk style card --lint-preset --tier vanilla` | — | a Markdown table of a class for guides; a lint config with budgets for `satk asset lint --config` |
| `satk style build [--force] [--validate] [--textures]` | — | build the cache now; `--validate` measures how many vanilla models pass their own band |

Classes: vehicles by type (`car`, `bike`, `bmx`, `quad`, `mtruck`, `boat`, `plane`, `heli`, `trailer`, `train`)
and, for cars, by body (`car.sedan`, `car.coupe_muscle`, `car.sports`, `car.suv_pickup`, `car.van`, ...); map
models by kind and size (`prop@1-2m`, `building@16-32m`, `terrain`, `vegetation`, `interior_prop`, `lod`, ...);
`ped`, `weapon`, `pickup`, `upgrade`. A model id (`model:426`) or name (`premier`) means "the class of this model".

## How it works

- Every number has one definition (`satk.style.registry`): `veh.hd_tris` = chassis + every `_ok` part + the wheel
  once; `shade.normal_bend` = how far the vertex normals bend away from the face normals (0 = faceted);
  `dims.L` = length of the undamaged model.
- The cache lives in `<workspace>\work\style\<profile>-<index hash>-<sig>.json`; a rebuilt index makes a new one.
- Bands are advice, not law: a value is `low`/`high` only well outside the p10..p90 band of the class (`edge`
  in between is not counted). The width was calibrated so that about 9 of 10 vanilla models pass their own
  class with at most one row (`satk style build --validate`). Structure and engine-rule errors (`error`) mean
  the game shows the model wrong; they make the verdict `fail`.
- `asset check` of a file named like a vanilla model (`premier.dff`) compares it with that model (a
  replacement); `--like` and `--cls` choose explicitly; `asset.json` next to the file can set `tier` and `like`.

## Limitations and known issues

- The `sa_plus` numbers are a proposal; check new assets next to vanilla in previews and in game.
- Small classes (wagons, RC cars) borrow the band of their parent class; the answer says so in `fallback`.
- The class of a model without an IDE line or a vanilla name is a guess (frames and size); pass `--cls` or
  `--like` for an exact comparison.

## Python API (if other packages use it)

```python
from satk.style import api
api.profile("car.sedan", "vanilla")["shade.normal_bend"]   # {'p10': 7.7, 'p50': 9.98, 'p90': 14.0, ...}
api.band("veh.hd_tris", "car", "sa_plus")                  # (3000, 4500)
api.check("mymod/premier.dff", tier="sa_plus")[0]["verdict"]
from satk.style.metrics import mesh_metrics                # canonical mesh metrics, also inside Blender
```
