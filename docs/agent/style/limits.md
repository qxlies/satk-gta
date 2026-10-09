# Limits: the hard engine rules (capacity, names, ids, formats, crash-safe construction)

<!-- Model-facing, English only. Measured on the clean 1.0 US copy (single player, no limit adjuster) with
     `id free`, `re limits`, `kb fact` and index queries. Rule ids L1-L10 are this file's own. -->

These are the rules the engine and the file formats enforce: break one and the asset fails to load, renders
wrong or crashes the game. They are the only hard numbers in these guides; everything else (triangle counts,
densities, shading and texture statistics, dimensions) is style judged by eye, with vanilla numbers as reference.
A stock game is nearly full: before building, know the target platform, whether the asset replaces or adds, and
which construction mistakes crash the game.

## Stock capacity (single player, no limit adjuster)

| Fact | Stock size | Used by vanilla | Free | Source |
|---|---|---|---|---|
| vehicle model infos | 212 | 212 | 0 | `id.free --kind vehicle` |
| ped model infos | 278 | 276 | 2 | `id.free --kind ped` |
| weapon model infos | 51 | 50 | 1 | `id.free --kind weapon` |
| object model infos | 14,070 | 14,045 | 25 | `id.free --kind object` |
| COL archive slots | 255 | 251 IMG `.col` archives (gta3 216, gta_int 35) + 3 loose | about 3 | `re.limits --kind store --match col` |
| TXD slots | 5,000 | 4,042 (3,979 in IMGs, 63 loose) | about 950 | `re.limits --kind store --match txd` |
| IDE 2DFX store | 100 | 97 | 3 (DFF 2DFX do not use it: 18,795 in vanilla) | `re.limits` |
| entry-exit markers (`enex`) | 400 | 376 | 24 | `re.limits` |
| streaming memory | 50 MiB | runtime | runtime | `kb.fact streaming`, `texture.budget` |

MTA raises several pools (collision models, buildings, env-map materials) and allocates model ids itself; SA-MP DL
uses its own id ranges (`id.free --target samp-dl`). Check a whole pack, not one model: `mod.check` adds capacity
rows per kind against the target.

## Rules

- **L1 Pick the target first:** `sp-stock` (Mod Loader, no limit adjuster), `sp-la` (a limit adjuster), `mta`
  (resource) or `samp-dl`. The target decides the ids, the packaging and the capacity; record it with `asset.init
  --target`.
- **L2 Replacement or add-on.** On `sp-stock` a new vehicle is impossible (all vehicle slots are used) and only
  two peds, one weapon and 25 objects fit. Replace an existing id, or require a limit adjuster and say so in the
  readme. A new vehicle id outside 400-611 also needs the handling and audio patches of a limit adjuster.
- **L3 One collision archive per pack.** Put every COL model of a map pack into one `.col` archive: about three
  archive slots are free, and overflowing them crashes (CrashInfo #17). Vehicles embed their COL3 in the DFF.
- **L4 Share TXDs like vanilla:** buildings 2-3 models per TXD, LOD TXDs shared by about 15 models, street props
  one TXD each (`world.md`). A pack that gives every model its own TXD eats the free slots.
- **L5 2DFX in the DFF.** Lights, particles, attractors and entry markers go into the DFF 2DFX extension
  (`fx2d.*`), never into IDE `2dfx` lines (three slots free). Broken DFF 2DFX crashes the map loader
  (CrashInfo #26): validate with `fx2d.check`.
- **L6 Interiors:** at most 24 new entry-exit markers fit in a stock game.
- **L7 Streaming memory:** 50 MiB for everything streamed. Vanilla own vehicle TXDs are about 10 KB (p90 27 KB),
  ped TXDs 128 KB (uncompressed atlas); an `sa_plus` ped atlas of 256x512 is 512 KB, 4x vanilla. Measure with
  `texture.budget` (the streamed DFF + TXD bytes of a profile or of a mod over vanilla, top offenders).
- **L8 Names:** model name at most 23 characters, at most 21 for a model with collision (the COL name limit; lint
  `col.name_len`); TXD name at most 23, texture name at most 31, frame name at most 23; Mod Loader and IMG file
  names at most 23 characters including the extension. LOD names: `lod` + the HD name without its first 3
  characters. The COL of a vehicle is named `<model>_col`.
- **L9 A replacement inherits everything bound to the id;** list and decide each item in the readme:
  vehicles: the `carmods.dat` upgrade list (keep every `ug_*` frame), `cargrp.dat` groups, the engine sound
  (bound to the id in the exe), paintjob slots; peds: `pedgrp.dat`, `ped.dat`, the voice; weapons: `weapon.dat`;
  objects: `object.dat`, `procobj.dat`, script references. `data.explain` shows what a data line means.
- **L10 Known crash causes and the check that prevents each:**

| Fact | Crash address | Prevented by | Source |
|---|---|---|---|
| CrashInfo #120: the game cannot find the vehicle's wheel | `0x004C7DAD` | lint `veh.frames` (required frames per vehicle type: `wheel`, `wheel_*_dummy`) | `crash.known` |
| CrashInfo #68: vehicle shadow model missing or broken | `0x0070FB39` | lint `veh.shadow_mesh` (embedded COL3 shadow mesh present and valid) | `crash.known` |
| CrashInfo #295: upgrade frame missing for a `carmods.dat` part | `0x007F0BF7` | lint `veh.upgrade_frames` | `crash.known` |
| CrashInfo #17: ColModels limit | `0x0156173D` | one COL archive per pack (L3); `mod.check` capacity rows | `crash.known` |
| CrashInfo #6: paintjob limit (remap textures added to a car that had none) | `0x015691EE` | keep the paintjob slots of the replaced car | `crash.known` |
| CrashInfo #26: broken 2DFX in a map model | `0x004C4C7E` | `fx2d.check` | `crash.known` |
| CrashInfo #130: edited skin model (peds, parachute) | `0x007C4781` | lint `ped.skin` (bone count, ids and order like vanilla; at most 4 weights per vertex) | `crash.known` |
| CrashInfo #64: model or TXD missing, or a file name too long | `0x00749B7B` | lint `link.*` and `*.name_len` rules | `crash.known` |

## Engine semantics that are easy to get wrong

| Fact | Value | Source |
|---|---|---|
| big buildings | a building with LOD children or with `LODDistMultiplier (1.0) x draw` above 300 loses its collision and is never streamed out: HD models with collision use draw 299 or less | engine (file loader, entity) |
| collision face light 0 | ambient light only (about 3x darker by day) and cars switch on their headlights; not black | engine (renderer, vehicle) |
| atomic extension 0x1F | does not matter for vehicles (the engine sets the car pipeline) | engine |
| vertices per geometry | at most 65,535 (16-bit indices; lint `dff.verts_max`); split a bigger mesh into parts | RenderWare geometry format |
| texture formats | power-of-two sides; DXT1, DXT3 or 32-bit; never DXT5; exactly one mip level on vehicle, ped and weapon textures | vanilla index, `textures.md` |
| colour keys | paint 60,255,0 / 255,0,175 on `vehiclegrunge256`; lamp keys only on `vehiclelights128`; UV2 on geometries whose material uses `xvehicleenv128` | engine (vehicle model info), `vehicles.md` |
| wheel size | the wheel mesh diameter equals the IDE `wheel_scale` | engine (vehicle model info) |
| collision coordinates | mesh and hull vertices within 256 m of the model origin (compressed COL coordinates) | COL format |
| wheel radius | the engine uses `wheel_scale / 2` as the physics radius and clones the `wheel` mesh to every wheel dummy | engine (vehicle model info) |
| lamp keys | work only on `vehiclelights128` (swapped for `vehiclelightson128` when lit) | engine (vehicle model info) |

## How to check

- `id.free --kind <kind>` prints free ids and the store capacity; `mod.check <pack>` checks ids over the limit,
  collisions between mods and capacity per kind; `crash.known <address or words>` explains a crash address.
- `re.limits --kind store|pool --match <name>` reads one limit; without filters the answer is long, so filter.
- `texture.budget` adds up streamed DFF and TXD bytes (a profile, or a mod over vanilla, or the placements near
  an area) against the 50 MiB budget.
- `asset.check` adds `col.check` and `fx2d.check` rows when those operations are installed.
