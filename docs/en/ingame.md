# In-game test loop: a mod in the real game, with checks

[Русская версия](../ru/ingame.md)

Package: `satk.ingame` and the Lua resource `mta-resources/satk-testdrive`. Engine: the MTA fork ([engine.md](engine.md)).

## What it is

Quick visual checks belong to the viewer ([viewer.md](viewer.md)); behaviour belongs to the real game: physics,
collisions, damage, streaming and LOD, lights, scripts. `satk ingame` puts a mod (DFF, TXD, COL and its data lines)
into the MTA fork: a private server on `127.0.0.1` with the `satk-agent` bridge ([mta-agent.md](mta-agent.md)),
the logic resource `satk-testdrive` and a generated resource with the mod's files. Your MTA client joins it; satk
spawns the model at test spots, runs scripted checks next to the vanilla model with a frame and a verdict per check,
and hot-reloads files you change, without reconnecting. It writes only under `<workspace>\work\` (`mta\server`,
`mta\ingame`, `out\ingame`). satk never starts the game itself: you start the client.

## Quick example

```powershell
satk ingame spots
satk ingame suites
satk ingame status
```

`spots` lists the test spots, check areas and camera presets; `suites` the checks; `status` the server, what your
client loaded and whether the one-time client setup is done (`preflight.setup_done`).

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk ingame start [--mod <folder or files>] [--kind vehicle\|object\|ped\|weapon] [--replace SID \| --new [--base SID]]` | — | builds the resources, starts or reuses the loopback server (port 22040, a free one when busy), applies the models in a joined client; tells whether the client setup is done and how to join |
| `satk ingame play` | CLI only | starts the MTA client against the server (for you; refuses without the setup or when the game already runs) |
| `satk ingame reload [--force]` | — | hot reload: changed files only, the content resource restarts, the client re-applies the models; answers the files and the time |
| `satk ingame drive [--model mod\|SID] [--at runway\|wall\|ramp\|grove\|night\|x,y,z]` | — | puts you into the vehicle at a test spot |
| `satk ingame spawn [--model ...] [--kind ...] [--at ...] [--camera ...] [--time HH:MM] [--weather N]` | — | a vehicle, object, ped or weapon (given to you) at a spot, with a camera preset |
| `satk ingame check [--suite ...] [--only ...] [--no-reference]` | — | scripted checks with a frame each and a verdict, mod vs vanilla; report in `work\out\ingame\<model>\<suite>\` |
| `satk ingame shot [--camera ...] [--target ...]` | — | one frame of the game (HUD hidden) into `work\out\ingame\shots\` |
| `satk ingame logs [--source ...] [--level ...] [--since start] [--grep ...]` | — | server, script and client log lines, newest last |
| `satk ingame status` / `stop` / `spots` / `suites` | — | state / stop the server / static lists |

Agents reach every command except `play` through `satk_op`.

## First live run

Once, the client setup (what the MTA installer would do): `satk ingame start` writes
`work\mta\ingame\admin-setup.ps1` and its undo `admin-rollback.ps1`. Run the setup yourself in a 64-bit PowerShell
started as administrator. Then:

<!-- docs-smoke: skip starts the MTA server and needs the game client -->
```powershell
satk ingame start --mod "<mod folder>"
satk ingame play
satk ingame drive --at runway
satk ingame check --suite vehicle
satk ingame reload
satk ingame logs --level warn --since start
satk ingame stop
```

`play` waits until the client joins (or, with MTA already open: press F8 and type `connect 127.0.0.1 22040`). In
the game, `/tdhelp` lists the chat commands (`/mod`, `/car`, `/tp <spot>`, `/fix`, `/flip`, `/dmg`, `/lights`,
`/time`, `/weather`, `/handling`, `/clear`).

## How it works

**Models.** Every `.dff` of the mod is a model; its `.txd` and `.col` are found by name (or the TXD named in the
mod's IDE line); a `.col` archive gives the matching entry. A `.txd` alone retextures the vanilla models that use it.
Mode `replace` (default for vanilla names, always for weapons) replaces the vanilla id; mode `new` takes a fresh id
with `engineRequestModel(kind, base)`; the new id shares the TXD slot of its base, so textures with the same names
show on the vanilla base too while the mod is loaded. Load order on the client: COL, TXD, DFF. A `handling.cfg` line of the mod becomes `setVehicleHandling`
properties (MTA keeps `engineAcceleration` x 0.4); the IDE line gives the wheel scale and draw distance.

**Resources.** `satk-testdrive-mod` holds the mod's files and `manifest.lua` (models, spots, check geometry, a
`rev` hashed over everything); clients download only changed files. `reload` restarts this resource only; the
logic resource keeps your vehicle, re-loads the models into the same ids and reports the time from the restart to
the loaded models (`restart_to_loaded_ms`).

**Checks.** A check parks your player next to the test area (frozen, invisible, controls off), creates its own test
elements, measures, asks satk for a frame at a camera pose and restores everything. With a `new` model the mod and
the vanilla base run side by side in one pass (left: mod); with `replace`, and for the visual checks of a `new`
model with a TXD (`damage`, `lights`, `dirt`, `lod`, `night`), the reference runs in a second pass with the mod
unloaded, and the two frames are joined into `<check>-<frame>-pair.png`. Areas: the LS airport
runways (runway 1 for speed runs, with lane keeping and a loop back to the start when the runway ends; runway 2 for
the pads and a crash wall).

| Suite | Checks |
|---|---|
| vehicle | `components` (frames vs vanilla, exhaust and light dummies), `rest` (wheels on the ground, settling, ride height), `speed` (top speed, 0-100 vs vanilla and the handling), `crash` (50 km/h into a wall: no driving through, depth, damage), `damage` (damage parts), `lights` (midnight), `dirt`, `lod` (frames at 20/60/140 m) |
| object | `collision_ped`, `collision_car` (blocked or through), `lod`, `night` (prelight) |
| ped | `anims` (six animations play, no bone changes length, proportions), `walk` |
| weapon | `held` (muzzle vs hand, vs vanilla), `fire` (shots, gun flash) |

Verdicts: `pass`, `warn`, `fail`, `info` (look at the frame), `error` (the check could not run).

## Limitations and known issues

- The client needs the one-time administrator setup; `status` and `start` say whether it is done.
- Checks take over your player for their duration (up to a minute for `speed`); keep the game window visible (a
  minimized D3D9 window does not render frames).
- `weapon` models can only replace a vanilla weapon (MTA cannot request new weapon ids).
- Numbers are measured in the running game and vary a little between runs; verdicts use margins.
