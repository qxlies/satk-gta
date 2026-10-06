# Scene: your own models, vehicles and peds in the viewer

[Русская версия](../ru/viewscene.md)

Package: `satk.viewscene`; the SAAP methods `scene.*` of the viewer (protocol text: `proto/SAAP-v1.md`, section 11).

## What it is

`satk view place|vehicle|ped` put your own work into the running viewer, next to the map: a model straight from
the files you just exported (DFF + TXD), a vehicle with paint, dirt, lamps and damaged parts, a ped in a pose from
an animation. Each entity gets a handle; frames with marks, `view pick` and the ID layer show it as
`el:scene/<handle>`. With `--watch` the viewer re-reads the files when they change, so a re-export from Blender or
`satk rw patch` shows up in about half a second. This is the fast visual check; behaviour (physics, collisions,
damage, streaming) is checked in the real game through MTA ([mta-agent.md](mta-agent.md)). Nothing is written:
the viewer only reads the files it is given.

## Quick example

```powershell
satk view vehicle 411 --pos 2495,-1675,13.4 --heading 90 --dirt 2 --colors 3 1 --target mock
satk view ped 105 --pos 2497.5,-1673,13.4 --heading 180 --target mock
satk view place --model 1280 --pos 2493,-1671,13.4 --heading 30 --target mock
satk view list --target mock
```

What the viewer answers (shortened; the mock answers with the same fields):

```json
{"ok":true,"id":"s1","sid":"el:scene/s1","ref":"@s1","kind":"vehicle","model":"infernus","model_id":411,"pos":[2495.0,-1675.0,12.75],"heading":90.0,"grounded":true,"vehicle":{"wheels":{"from":"own","scale":[0.7,0.7],"count":4},"colors":[{"slot":1,"index":3,"rgb":"#840410"},{"slot":2,"index":1,"rgb":"#f5f5f5"}],"dirt":2,"lights":false,"parts":{"door_lf":"ok","bonnet":"ok"}},"load_ms":19.8,"rtt_ms":23.2,"target":"ariane"}
{"ok":true,"id":"s1","sid":"el:scene/s1","kind":"ped","model":"fam1","pos":[2497.5,-1673.0,13.3],"heading":180.0,"ped":{"anim":{"ifp":"ped","name":"idle_stance","time":0.0,"duration":1.5,"bones":32,"nodes":32}}}
{"ok":true,"cols":["handle","sid","kind","model","x","y","z","heading","watch","reloads","load_ms","file","error"],"rows":[["bench","el:scene/bench","object","bench",2493.0,-1671.0,12.75,30.0,true,2,5.2,"<workspace>/work/tmp/bench/bench.dff",null]]}
```

The mock draws placed entities as boxes and keeps them only for one command; the real viewer keeps them until
`view remove` or until it stops. With the viewer (a visible window):

<!-- docs-smoke: skip starts Ariane and writes files under work -->
```powershell
satk view start --target ariane --window 960x540
satk asset export model:1280 --format raw
New-Item -ItemType Directory -Force work\tmp\bench
Copy-Item work\cache\raw\vanilla\parkbench1.dff work\tmp\bench\bench.dff
Copy-Item work\cache\raw\vanilla\benches_cj.txd work\tmp\bench\bench.txd
satk view place work\tmp\bench\bench.dff --txd work\tmp\bench\bench.txd --pos 2493,-1671,13.4 --ground --watch --id bench
satk view vehicle premier --pos 2495,-1675,13.4 --heading 90 --dirt 2 --id car
satk view ped 105 --pos 2497.5,-1673,13.4 --heading 180 --id fam
satk view capture --pos 2503,-1666,17 --look 2495,-1674,13.4 --marks 6
satk rw patch work\tmp\bench\bench.dff --material-color 0=255,0,0 --out bench_red
Copy-Item work\out\rw\bench_red\bench.dff work\tmp\bench\bench.dff
satk view capture --pos 2503,-1666,17 --look 2495,-1674,13.4 --marks 6
satk view list
satk view stop
```

Copy the patched DFF over the watched one (an exporter writing in place does the same) and the next frame shows
it; `view list` counts the reloads.

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk view place [DFF] [--txd T…] [--model ID] --pos X,Y,Z [--heading H \| --rot RX,RY,RZ] [--scale S] [--ground] [--watch] [--id NAME]` | — | a model from files, or a game object; `--model` with a DFF lends its TXD as a fallback |
| `satk view vehicle MODEL\|--dff D --pos X,Y,Z [--heading H] [--colors C1 C2 …] [--dirt 0-15] [--lights] [--parts door_lf=dam bonnet=off …] [--wheel-model W] [--wheel-scale D] [--no-ground]` | — | a vehicle with its frames, wheels from the IDE, carcols paint (index or `#rrggbb`), dirt, lamps, part states |
| `satk view ped MODEL\|--dff D --pos X,Y,Z [--heading H] [--anim NAME] [--ifp FILE] [--anim-time S] [--no-ground]` | — | a skinned ped posed by an IFP frame (default `ped` / `idle_stance`) |
| `satk view reload [HANDLE]` | — | re-reads the files of one entity or of all; a failed reload keeps the old model |
| `satk view remove HANDLE… \| --all` | — | removes entities |
| `satk view list` | — | handles, SIDs, positions, files, reload counts, the last reload error |

Every command takes `--target ariane|mock` (default `ariane`). AI agents reach them with
`satk_ops("view place")` → `satk_op("view.place", {...})`. Time and weather: `satk view set --time 21:30
--weather 8`.

## How it works

- The viewer loads each entity into its own copy (clump, textures), so a reload swaps it without touching the
  game data. TXDs are searched in the order given, then the game's (`vehicle.txd` for vehicles); textures that are
  in none of them come back as a `NOT_FOUND` warning with their names.
- Vehicles: atomics `*_vlo` and damaged parts are hidden unless asked for; `--parts NAME=dam` shows `NAME_dam`,
  `=off` hides the part. Wheels are copies of the vehicle's wheel (left ones turned), scaled by the IDE wheel size
  when they come from another model. Paint replaces the four paint material colours; dirt level `i` remaps
  `vehiclegrunge256` to `c·i/16 + 255·(16−i)/16`; lamps use `vehiclelightson128`. Glass (material alpha below
  255) is drawn after everything opaque.
- Peds are skinned and drawn in the pose of one animation frame, also in the ID and depth layers, so a pick hits
  the arm where it is drawn.
- `--ground` (default for vehicles and peds) casts a ray down onto the collision of the map and puts the lowest
  point of the model on it.
- Watching polls the files every 200 ms and reloads once they stayed unchanged for 150 ms.

## Limitations and known issues

- The viewer has no specular highlights, shadows or particle effects; vehicles are lit by the time cycle's object
  ambient light and one light from above. For the real look and for behaviour use the game through MTA.
- CJ (ped 0) is made of clothes and is not supported; use a ped skin such as 105 (`fam1`).
- Entities live as long as the viewer process; `view start` after `view stop` starts empty.
- An older viewer build answers `UNSUPPORTED` with a hint to rebuild it; the `game` target has no `scene.*`.

## Python API (if other packages use it)

```python
from satk.viewscene import api
api.vehicle("ariane", model=426, pos=[2495, -1675, 13.4], dirt=2)
```
