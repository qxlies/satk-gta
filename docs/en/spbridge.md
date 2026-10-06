# spbridge: agent eyes and hands in single-player GTA:SA

[Русская версия](../ru/spbridge.md)

Package: `satk.spbridge`.

## What it is

Most GTA:SA modding happens in the normal single-player game - the stock `gta_sa.exe` with Mod Loader, ASI and
CLEO mods - which MTA does not load. `spbridge` gives an AI agent (and a person) the same view and control there
as the MTA and Ariane backends do elsewhere: a small plugin **`satk_sp.asi`** serves the SAAP/1 protocol over
`127.0.0.1` (same framing, auth and methods as the MTA bridge), so `satk saap call sp ...` and, later, the viewer
can drive the real engine - move the camera, set time and weather, take a screenshot of the D3D9 back buffer, pick
an entity under a pixel, query and inspect nearby entities, toggle the HUD, and the `sp.*` "hands" (teleport,
spawn a vehicle/ped/object by model id with a streaming request). It reads only engine memory and writes only
PNG captures under the work directory.

The native project (MIT C++, `native/sp_bridge`) is built with the VS C++ build tools; its transport layer is
proven **offline, without the game** by compiling a host-side SAAP server and running the SAAP/1 conformance suite
against it over TCP (`satk sp selftest`). The 1.0 US engine addresses live in one table
(`satk.spbridge.addresses`), cross-checked against the knowledge base and the clean executable.

## Quick example

```powershell
satk sp addresses
satk sp status
```

What comes back (shortened):

```json
{"ok":true,"cols":["name","kind","addr","symbol"],"total":52,"game_version":"1.0us"}
```

<!-- docs-smoke: skip needs the VS C++ build tools and takes a few seconds -->
```powershell
satk sp selftest
```

`satk sp selftest` compiles the host-side SAAP server (no game) and runs the SAAP/1 conformance cases against it
over TCP; a green run (`"fail":0`) proves the ASI's framing, auth and envelope handling.

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk sp addresses [--verify] [--emit-header]` | — | show the 1.0 US engine address table; verify it against the KB; regenerate the C++ header |
| `satk sp build asi\|test\|all` | — | compile `satk_sp.asi` (x86) and/or the host-side SAAP test server with `cl.exe` |
| `satk sp selftest` | — | offline proof: build the test server and run SAAP/1 conformance over TCP |
| `satk sp status` | — | is the SP endpoint (role `sp`) running? pid, port, caps |
| `satk sp start --copy <dir> --yes` | — | start a **test copy** of the game with the ASI (consent-gated, CLI only) |
| `satk sp stop` | — | send `quit` and remove the discovery files (consent-gated, CLI only) |

`sp start`/`sp stop` touch a real game process, so they are consent-gated and refused to agents through
`satk_op`; they also refuse the original install and the clean copy. Use a dedicated test copy made with
`satk game clone --dst <dir>`.

## How it works

- **Transport**: `satk_sp.asi` listens on `127.0.0.1` and speaks SAAP/1 - the same `u32` length prefix + JSON
  framing (`proto/cpp/saap_frame.hpp`), the same `hello`-with-token auth, and SAAP error codes. It writes
  `work/run/endpoints/sp.json` (`"protocol":"saap/1"`, role `sp`), so `satk.saap.client` and the viewer's
  `SaapNativeBackend` discover and drive it with no new transport code.
- **Main-thread pump**: the ASI rewrites one instruction - the `call CGame::Process` inside `Idle` (`0x53E981` on
  1.0 US) - to a trampoline that runs the original tick and then services the socket once per frame. Every SAAP
  method runs on the game's main thread, so engine calls are safe, with a tiny per-frame budget.
- **Methods**: camera (`CCamera` fixed mode), env (`CClock`/`CWeather`), capture (D3D9 back buffer → PNG + SHA-256),
  pick/raycast (`CWorld::ProcessLineOfSight`), `entity.query`/`entity.inspect` (walking the ped/vehicle/object/
  building/dummy pools), `log.poll` (the ASI's own ring). The frozen SAAP method set has no spawn/teleport, so
  those are `sp.*` extension methods announced through the `sp.control` capability.
- **Determinism and safety**: captures are written only under `SATK_AGENT_OUT_ROOT` (a path outside it is
  `BAD_PARAMS`); without a token the ASI does not listen at all; it patches nothing unless the hook site really is
  `call CGame::Process`, so loading it into another build does nothing.
- **Offline test**: the game-independent server layer (`saap_server.hpp`, `saap_json.hpp`, `saap_sha256.hpp`,
  `saap_png.hpp`) compiles into a console test server with a synthetic backend; `satk sp selftest` runs the SAAP
  conformance cases against it over real TCP.

## Limitations and known issues

- Single edition: **1.0 US (HOODLUM)**. Other `gta_sa.exe` builds are not patched (the ASI stays silent).
- G0 scope: capture is the back buffer at its own size (no `capture.size`/`ids`/`depth` layer yet); `world.settle`
  streams around the camera and reports `pending: 0` (the streaming queue is not surfaced).
- The `view_*` commands do not take `--target sp` yet (the viewer's target table has no `sp` role); until then
  use `satk saap call sp ...`.
- `satk sp start` does not run in automated agent sessions; prepare the command and run it yourself against a
  test copy.

## Python API (if other packages use it)

```python
from satk.spbridge import addresses, backend
table = addresses.all_symbols()          # the 1.0 US address table
b = backend.sp_backend()                 # a SaapNativeBackend for a running SP endpoint
```
