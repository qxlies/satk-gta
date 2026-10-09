# In-game test loop: a mod in the real game, with checks

[Русская версия](../ru/ingame.md)

Package: `satk.ingame` and the Lua resources `mta-resources/satk-testdrive` and `mta-resources/satk-bench`. Engine: the MTA fork ([engine.md](engine.md)).

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
satk ingame bench-scenes
satk ingame status
```

`spots` lists the test spots, check areas and camera presets; `suites` the checks; `bench-scenes` the bench scenes; `status` the server, what your
client loaded and whether the one-time client setup is done (`preflight.setup_done`).

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk ingame start [--mod <folder or files>] [--kind vehicle\|object\|ped\|weapon] [--replace SID \| --new [--base SID]] [--conf key=value ...] [--cvar key=value ...] [--windowed]` | — | builds the resources, starts or reuses the loopback server (port 22040, a free one when busy), applies the models in a joined client; tells whether the client setup is done and how to join |
| `satk ingame play [--cvar key=value ...] [--windowed]` | CLI only | starts the MTA client against the server (for you; refuses without the setup or when the game already runs); the cvars are written before the start |
| `satk ingame reload [--force]` | — | hot reload: changed files only, the content resource restarts, the client re-applies the models; answers the files and the time |
| `satk ingame drive [--model mod\|SID] [--at runway\|wall\|ramp\|grove\|night\|x,y,z]` | — | puts you into the vehicle at a test spot |
| `satk ingame spawn [--model ...] [--kind ...] [--at ...] [--camera ...] [--time HH:MM] [--weather N]` | — | a vehicle, object, ped or weapon (given to you) at a spot, with a camera preset |
| `satk ingame check [--suite ...] [--only ...] [--no-reference]` | — | scripted checks with a frame each and a verdict, mod vs vanilla; report in `work\out\ingame\<model>\<suite>\` |
| `satk ingame shot [--camera ...] [--target ...]` | — | one frame of the game (HUD hidden) into `work\out\ingame\shots\` |
| `satk ingame logs [--source ...] [--level ...] [--since start] [--grep ...]` | — | server, script and client log lines, newest last |
| `satk ingame bench --scene S1..S9 --label <name> [--preset P] [--profile X] [--duration SEC] [--warmup SEC] [--run ID] [--cvar name=value ...] [--settle SEC] [--restore-cvars]` | — | frame-time and memory bench in the joined client; `--preset`/`--profile`/`--cvar` restart the client with launch-time cvars; result file `work\out\bench\<run>\<scene>-<label>.json` |
| `satk ingame bench-compare <A> <B> [--p50-tol 5] [--p99-tol 10] [--va-yellow-mib 3072]` | — | A/B rules on two result files, a verdict per rule (no game needed) |
| `satk ingame cvar-restore` | — | puts the original client `coreconfig.xml` back (refuses while `gta_sa.exe` runs) |
| `satk ingame status` / `stop` / `spots` / `suites` / `bench-scenes` | — | state / stop the server, close the client satk started and restore the client config / static lists |

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

## Test settings: server config and launch-time cvars

The agent reaches the server (`executeCommandHandler`) but not the client's core console (`sae_preset`, `sae_set`,
`fps_limit`). So settings that a run depends on are written **before** the process starts: the server config by
`ingame start`, the client config (`coreconfig.xml`) before the client starts.

| Option | Where | What it does |
|---|---|---|
| `--conf key=value ...` (`start`, `python -m satk.viewer.backends.mta_lua up`) | server | sets or replaces any `mtaserver.conf` element in the generated `satk-agent.conf`: `sae_policy`, `sae_dev_stock_translator`, `sae_policy_transport`, `sae_actor_stream_distance`, `fpslimit`, `maxplayers`, ... Not allowed (`BAD_PARAMS`): the loopback/ASE/LAN/HTTP safety values, ports, file names and the resource list (`serverip`, `serverport`, `httpport`, `ase`, `donotbroadcastlan`, `acl`, `password`, `crash_dump_upload`, ...). The config is read at server start: a running server that was started with other `--conf` values answers `NOT_READY` (`satk ingame stop` first). |
| `--cvar key=value ...` (`start`, `play`) | client | writes `<key>` into the `<settings>` of the client's `coreconfig.xml` before the client starts (`vsync=0`, `fps_limit=0`, `sae_preset=classic`, `sae_limits=mta`, any `sae_*`); the sa-engine client only adds missing `sae_*` keys at start, so a written value is kept. |
| `--windowed` (`start`, `play`) | client | shortcut for `--cvar display_windowed=1 display_fullscreen_style=0` (windowed mode; style 0 is the standard one the video settings keep for a window). An explicit `--cvar` wins. |

**Server template.** The generated config starts from the `mtaserver.conf` of the fork's source tree (folder `Server`)
(the `fpslimit` and `sae_*` documentation of the fork). The copy under `Bin/server` is written once by the build and goes
stale (older `fpslimit`, no `sae_*` keys, `crash_dump_upload` 1), so it is used only when the source template is missing.
The answer names the one used (`server.template`: `fork-source` or `bin`) and warns `STALE_TEMPLATE` when the two differ.
The safety values stay enforced either way: loopback `serverip`, `ase` 0, `donotbroadcastlan` 1, no LAN or crash upload,
only `satk-agent` and the test resources.

**Backup and restore.** The first write of a run copies `coreconfig.xml` to `work\mta\ingame\cvar-backup\` (once per
run; the state is `work\mta\ingame\cvars.json`). Later writes rebuild the file from that backup plus the recorded values, so
only `<key>` elements of `<settings>` differ from your original. `satk ingame stop` closes the client satk started, waits
for `gta_sa.exe` to exit and restores the original; `satk ingame cvar-restore` does the same by hand (it removes the file
when there was none). The client rewrites `coreconfig.xml` when it exits, so nothing is written or restored while
`gta_sa.exe` runs: `--cvar` then answers `NOT_READY` (repeating the values already applied is a no-op) and
`cvar-restore` asks you to stop first.

<!-- docs-smoke: skip needs the running game client -->
```powershell
satk ingame start --clear --windowed --cvar vsync=0 fps_limit=0
satk ingame play
satk ingame stop
satk ingame start --clear --conf sae_policy=0 fpslimit=0
satk ingame stop
```

## Bench: frame time and memory

`satk ingame bench` measures a scene in the real game and `bench-compare` judges two runs. The resource
`satk-bench` (MIT) is a client scene runner; `satk ingame start` installs it next to `satk-testdrive`, and `bench`
installs and starts it on demand in a running server. It needs the joined client (`satk ingame play`); without one it
stops with `NOT_READY`.

<!-- docs-smoke: skip needs the running game client -->
```powershell
satk ingame bench --scene S2 --label mta --run r1 --duration 300
satk ingame bench --scene S2 --label sae --run r1 --duration 300 --preset classic
satk ingame bench-compare r1/S2-mta r1/S2-sae
```

**Stages.** A scene is a list of stages. satk sends one stage at a time and polls until the client is idle. A stage
freezes time and weather, sets a fixed camera or a flying path (the local player is frozen under the camera so the world
streams around it), spawns client-side vehicles and peds, warms up for 5 s and samples for 20 s (`--duration` sets the
sample of every stage, `--warmup` the warm-up). Per frame the resource stores the `timeSlice` of `onClientPreRender`;
once a second it reads `getProcessMemoryStats()` (VA), `engineStreamingGetUsedMemory()` and, when the client build has
them, `getEngineStats()`, `getEngineLimits()` and `getDrawDistanceInfo()`. Every optional function is feature-detected,
so the same scene runs on a stock MTA client (those blocks are then simply absent). The environment, camera and player
are restored at the end.

| Scene | Stages | Measures |
|---|---|---|
| S1 | Grove Street, 5 peds, one stage per `fps_limit` 60/144/165/240 | baseline; `gameTimeMs` against `getTickCount` (clock ratio) and the `frameCounter` rate |
| S2 | LS flyover at 120 m, 180 s (`--duration 300`) | streaming, draw-distance cost, VA growth |
| S3 | SF to LV at 60 m, 300 s | cold and warm streaming |
| S4 | 18:00-19:30 time-lapse over LS, 90 s | day/night prelight spikes (the series has the per-second maximum) |
| S5 | 64 touching vehicles on the airport pad | collision cost |
| S6 | 110 walking peds on the pad | ped simulation cost |
| S7 | six draw-distance viewpoints V1-V6, 10 s each | visible counts and high-water marks; V6 is V4 in fog |
| S8 | a client-side ped fires an M4 for 20 s | shots per real second and per game second |
| S9 | 4 MiB `dxCreateTexture` + 2 MiB DFF per step until the VA yellow line | VA per MiB of content, guard reaction, release |

`bench-scenes` prints the table with the planned seconds. The camera paths and positions are data in
`src/satk/ingame/bench_scenes.py`; the paths reuse the spots of `satk ingame spots`.

**Settings first (a client restart).** `--preset P` is the launch-time cvar `sae_preset`, `--profile X` is `sae_limits`
(the capacity profile applies at client start only) and every `--cvar name=value` is that cvar (see "Test settings"). The
agent cannot run `sae_preset`/`sae_set`, so the bench relaunches the client: it closes the client satk started
(`ingame play`), writes the values into `coreconfig.xml` (backup once), starts the client, waits until it has joined and
loaded the resources, waits `--settle` seconds (default 10) and runs the scene. The result records the values
(`request.launch`) and checks them against what the client reports (`NOT_APPLIED` / `UNVERIFIED` warnings).
The cost is **one client start (about a minute) per distinct set of values**: a bench with the same values as the running
client does not restart it, and a bench without the options after one with them restarts it back to the `start`/`play`
values. A game that satk did not start is never closed (`NOT_READY`: start it with `ingame play`). The values stay in
the config until `ingame stop` or `ingame cvar-restore`; `--restore-cvars` closes the client after the scene and restores
at once (the next bench then needs `ingame play`). S1 sets the frame limit per step on the **server** (`setFPSLimit`
through the agent, the previous server value is put back after the scene; a refusal is a `SERVER_FPS` warning) and the
resource also calls the client's `setFPSLimit`. The limits are combined by the minimum, so launch the client with
`--cvar fps_limit=0 vsync=0` and the server with `--conf fpslimit=0` (or a value of at least 240) for the 144/165/240 steps.

**Result file.** `work\out\bench\<run>\<scene>-<label>.json` (schema `satk-bench/1`): the request, what the client
build has, and per stage the frame statistics (`n`, `avg_ms`, `p50_ms`, `p95_ms`, `p99_ms`, `p999_ms`, `max_ms`,
`fps_avg`, `fps_wall`, `low1_fps` = FPS of the mean of the slowest 1 % of frames, `q` = the 101 percentiles), the
per-second `series` (`[t, frames, avg_ms, max_ms, va_mib, streaming_mib]`), `va` (start, end, peak, growth), `stream`,
`clock` (`ratio`, `frame_counter_hz`, `timer_mode`), the engine tables at the start and the end of the sample,
`limits_max` (every numeric counter of `getEngineLimits()` at its maximum), spawn and streamed-in counts, and the probe
(`shots_per_s`; loader steps and `stop_reason`). A summary pools the stages. `timeSlice` may be whole milliseconds on
some builds: `integer_share` near 1 means the percentiles are quantized. If a run fails after some stages, the partial
file is saved with `"incomplete": true`.

**Compare.** `bench-compare A B` takes A as the reference and B as the candidate, by file path, `<run>/<scene>-<label>`
or `<scene>-<label>` of the newest run. For every stage present in both: p50 frame time of B at most 5 % above A, p99 at
most 10 % above, peak used VA of B below 3072 MiB (the 3 GiB yellow line); fewer than 30 frames is no measurement
(FAIL). FPS, 1 % low, p95, maximum, clock ratio, frame-counter rate and shots per second are shown as `info`. The answer
is a table `stage, metric, A, B, delta, limit, verdict` plus `verdict` (`pass` or `FAIL`) and the list `failed`.

## Limitations and known issues

- The client needs the one-time administrator setup; `status` and `start` say whether it is done.
- Checks take over your player for their duration (up to a minute for `speed`); keep the game window visible (a
  minimized D3D9 window does not render frames).
- `weapon` models can only replace a vanilla weapon (MTA cannot request new weapon ids).
- Numbers are measured in the running game and vary a little between runs; verdicts use margins.
- The bench reads `getEngineStats()` and its siblings by the names of the sa-engine client; on a stock client they are absent and only frame times, VA and streaming memory are recorded.
