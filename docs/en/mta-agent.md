# MTA agent: eyes in the real game (resource `satk-agent`)

[Русская версия](../ru/mta-agent.md)

Package: `satk.viewer.backends.mta_lua` and the Lua resource `mta-resources/satk-agent`. Protocol: SAAP/1,
target `game`.

## What it is

The `game` target of `satk view`: the built MTA fork ([engine.md](engine.md)) with the Lua resource `satk-agent`
on a private server bound to `127.0.0.1` only. The server exports an HTTP `rpc` endpoint protected by a token; the
MTA client sets the camera (`setCameraMatrix`), takes a frame (`dxCreateScreenSource` → `dxGetTexturePixels` →
PNG), picks the world (`processLineOfSight` with building information → `model_pos` → an index SID), changes time
and weather, returns the log and runs Lua on both sides. Ariane gives fast eyes; `game` is the ground truth.
It writes only to `work\mta\server\` (server copy, logs) and `work\out\captures\` (frames). Scripted checks of a mod
in this game are [ingame.md](ingame.md); checks of an MTA resource's scripts are [mta.md](mta.md).

## Quick example

```powershell
satk view status --target game
```

What comes back without a running server:

```json
{"ok":true,"target":"game","up":false}
```

With a server and a client (on the reference PC the client does not start yet; see "First client run"):

<!-- docs-smoke: skip starts the MTA server and the game client -->
```powershell
# in the satk checkout (the tools folder)
$env:PYTHONPATH = "$PWD\src"
.venv\Scripts\python.exe -X utf8 -m satk.viewer.backends.mta_lua up --client
satk view conformance --target game --files proto/conformance/core.jsonl proto/conformance/camera.jsonl proto/conformance/capture.jsonl --show-all
satk view capture --target game --pos 2495,-1720,60 --look 2495,-1670,15 --fov 70
satk view pick 640 360 --target game
satk view stop --target game
```

## Commands

| Command | MCP | What it does |
|---|---|---|
| `python -m satk.viewer.backends.mta_lua up [--client] [--timeout S] [--conf key=value ...] [--cvar key=value ...] [--windowed]` | — | private server in `work\mta\server` (free ports, `127.0.0.1` only), waits for the resource's `ping`, writes `work\run\endpoints\game.json` and `work\run\sessions\game.json`; `--client` also starts the client; `--conf`, `--cvar`, `--windowed`: see "Test settings" in [ingame.md](ingame.md) |
| `python -m satk.viewer.backends.mta_lua client [--force]` | — | the fork's client against `mtasa://127.0.0.1:<port>`; without `--force` it runs `preflight` first; on a Windows error dialog the process is killed and the dialog text goes into the error |
| `python -m satk.viewer.backends.mta_lua preflight` | — | read-only: the registry key `HKLM\…\Multi Theft Auto: San Andreas All`, `%ProgramData%\MTA San Andreas All`, whether `gta_sa.exe` is already running |
| `python -m satk.viewer.backends.mta_lua status` / `down` | — | state / `quit` to the resource, close the client, remove the discovery files |
| `satk view status\|goto\|capture\|pick\|set\|stop --target game` | `view_*` | as with Ariane; `view set` only changes time and weather |
| `satk view conformance --target game [--files …]` | — | SAAP/1 tests; acceptance uses the files `core`, `camera`, `capture` |

`satk view start --target game` answers `UNSUPPORTED` for now: start the game target with the command above.

## How it works

**Protocol `mta-lua/1`.** `POST http://127.0.0.1:<httpport>/satk-agent/call/rpc`, body
`[{"token","id","method","params"}]` (MTA passes a JSON array of arguments to the export), reply
`[{"ok":true,"result":…}]` or `[{"ok":false,"error":{code,message,data}}]` with SAAP/1 codes. Client methods
(camera, frame, pick, `world.settle`, `lua.exec side=client`) are passed by the server to the agent as an event,
and the server replies `{"ok":true,"pending":"<rid>"}`; the backend polls `_poll` every 10-50 ms. Server methods
(`hello`, `ping`, `status` without a client, `env.*`, `log.poll`, `console.exec`, `lua.exec side=server`, `quit`)
reply at once.

**Security.** The server listens only on `127.0.0.1` (`serverip`, `--ip`), without ASE and LAN broadcast; Lua
refuses non-loopback addresses and a wrong token (64 hex, `secrets.token_hex`). The token is stored in
`work\mta\server\mods\deathmatch\settings.xml` (`@satk-agent.token`, a private resource setting) and in
`work\run\sessions\game.json`. Own ACL (`satk-acl.xml`): the guest gets only `resource.satk-agent.http`, the resource
gets `function.shutdown`. `lua.exec` is remote code execution by design: local development only.

**Server.** `MTA Server64.exe` and `x64\*.dll` are copied from `engine\mtasa\Bin\server` to `work\mta\server` (on
later runs only changed files), the config `satk-agent.conf` is built from the `mtaserver.conf` of the fork's source tree (the copy under `Bin/server` only when the source is missing), the
resource is copied from `mta-resources/satk-agent`. Start with `--child-process` and a ready event; ready means a
reply to `ping` (about 1.1 s on the reference PC). Logs: `work\mta\server\mods\deathmatch\logs\satk-agent*.log`.

**Camera and FOV.** SAAP sets the horizontal FOV of the frame. The client sets the camera, two frames later measures
the real half-tangents of the screen (`getWorldFromScreenPosition` at the edges) and, if needed, adjusts MTA's `fov`
so that the horizontal FOV matches (tolerance 0.01°). `camera.get` returns the measured FOV.

**Frame.** Camera view → `enginePreloadWorldArea` → hide HUD and chat → wait `min(max_frames, 30)` frames and at
least 300 ms → in `onClientRender` `dxUpdateScreenSource` → `dxGetTexturePixels` → `dxConvertPixels("png")` →
latent event (20 MB/s) → file in the resource → the backend moves it to `path_prefix.png` and computes `sha256`.
A size other than the window's (`capture.size`): a centered crop with the frame's aspect ratio, stretched through a
render target; the camera FOV is recalculated so that the crop has the requested FOV. Camera, HUD, chat and time
are restored after the frame (a frame has no side effects, as with mock).

**Pick.** Window space: a ray through the pixel center (`getWorldFromScreenPosition`) →
`processLineOfSight(…, includeWorldModelInformation=true)`, the agent's player is ignored. Frame space
(`space=capture`): the backend computes the rays with the same camera model, the client only traces them (with
`enginePreloadWorldArea` for collisions near the camera). A building →
`{"kind":"building","model_id","pos","src":{"kind":"model_pos"}}`, which `satk view pick` turns into an `inst:` SID
through the index (tolerance 0.05 m); an MTA element → `el:<type>/<id>`.

**Tests.** `tests/mta`: the scripts are compiled and run by the fork's own Lua 5.1
(`engine\mtasa\Bin\server\x64\lua5.1.dll` through ctypes) in an MTA simulation (`tests/mta/sim.lua`: events, latent
events, a camera with a different FOV scale and aspect ratio, a world with a plane and a building). The full path
backend → HTTP → `server.lua` → `client.lua` → PNG passes the conformance groups
`core`/`camera`/`capture`/`pick`/`raycast`/`env`/`world`/`log`/`console`/`lua` (26 PASS, 0 FAIL, 2 skipped:
`capture.ids`, `capture.depth`); a live server: `SATK_TEST_LIVE=1`.

## First client run: what blocks it

Checked on 2026-10-04 on the reference PC (standard user, UAC on, `ConsentPromptBehaviorAdmin=5`). The MTA loader
(`Multi Theft Auto.exe`, manifest `asInvoker`, so no registry virtualization) keeps everything in
`HKLM\SOFTWARE\WOW6432Node\Multi Theft Auto: San Andreas All`; the key and its "Users: write" ACL are created by the
MTA NSIS installer, which was never run here (the official MTA must not be installed next to the fork).

- `preflight` (read-only) finds up to 4 blockers on a PC where MTA was never installed: no `Common\GTA:SA Path`; no
  key `HKLM\…\Multi Theft Auto: San Andreas All` (writing `Last Run Location` on every start would fail); no
  `%ProgramData%\MTA San Andreas All\Common` and `\1.7`.
- A forced start (`client --force`, once): after 4 s the window **"Error [U01]: Multi Theft Auto has not been
  installed properly, please reinstall."** (`GetMTASABaseDir`: `Last Run Location` was not written and cannot be
  read, `Shared/sdk/SharedUtil.Misc.hpp:550`). The process was killed automatically, no other processes were left.
  UAC did not appear. The server and `satk view conformance` kept working: `core` 5/5 PASS, `camera.bad_params`,
  `capture.unsupported_layer`, `capture.bad_params` PASS, the other 7 cases `NOT_READY: no game client has joined
  the agent server`, 2 skipped (`capture.ids`, `capture.depth`).

What this run wrote (snapshots before and after): the folder `%ProgramData%\MTA San Andreas All\1.7\` with
`report.log` (461 bytes, 5 loader log lines) and empty folders `engine\mtasa\Bin\MTA\dumps\private`. In the <!-- linkcheck: ignore -->
registry (`HKLM` 32/64, `HKCU\Software\Multi Theft Auto…`, `HKCU\…\VirtualStore`): nothing; the clean copy did not <!-- linkcheck: ignore -->
change. Rollback (PowerShell in the workspace folder, no administrator rights):

<!-- docs-smoke: skip deletes files outside work -->
```powershell
Remove-Item -Recurse -Force "$env:ProgramData\MTA San Andreas All"
Remove-Item -Recurse -Force .\engine\mtasa\Bin\MTA\dumps
```

For the client to start, a one-time setup **by an administrator** is needed: the same as the MTA installer does,
plus the value `MaxLoaderThreads`, which the loader otherwise writes itself, asking for UAC ("Update compatibility
settings", `CInstallManager::_ProcessAppCompatChecks`). The user runs it personally in a 64-bit
PowerShell "as administrator", in the workspace folder (the agent does not elevate and does not change ACLs):

<!-- docs-smoke: skip needs administrator rights -->
```powershell
$ws = (Get-Location).Path   # the workspace folder
$users = New-Object Security.Principal.SecurityIdentifier 'S-1-5-32-545'
foreach ($k in 'HKLM:\SOFTWARE\WOW6432Node\Multi Theft Auto: San Andreas All', 'HKLM:\SOFTWARE\Multi Theft Auto: San Andreas All') {
  New-Item -Path "$k\Common", "$k\1.7" -Force | Out-Null
  $acl = Get-Acl $k
  $acl.AddAccessRule((New-Object Security.AccessControl.RegistryAccessRule($users, 'FullControl', 'ContainerInherit', 'None', 'Allow')))
  Set-Acl $k $acl
}
Set-ItemProperty 'HKLM:\SOFTWARE\WOW6432Node\Multi Theft Auto: San Andreas All\Common' 'GTA:SA Path' "$ws\gta-sa-clean"
$ifeo = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options\gta_sa.exe'
New-Item -Path $ifeo -Force | Out-Null
Set-ItemProperty $ifeo MaxLoaderThreads 1 -Type DWord
New-Item -ItemType Directory -Force "$env:ProgramData\MTA San Andreas All\Common", "$env:ProgramData\MTA San Andreas All\1.7" | Out-Null
icacls "$env:ProgramData\MTA San Andreas All" /grant '*S-1-5-32-545:(OI)(CI)M'
```

Rollback of the setup (also as administrator; the key `Image File Execution Options\gta_sa.exe` did not exist before
it):

<!-- docs-smoke: skip needs administrator rights -->
```powershell
Remove-Item -Recurse -Force 'HKLM:\SOFTWARE\WOW6432Node\Multi Theft Auto: San Andreas All', 'HKLM:\SOFTWARE\Multi Theft Auto: San Andreas All'
Remove-Item -Recurse -Force 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options\gta_sa.exe'
Remove-Item -Recurse -Force "$env:ProgramData\MTA San Andreas All"
```

After the setup the client (according to the loader code; not verified by a run) writes on every start: values in
these keys (`Last Run Location`, `Settings\…`), `%ProgramData%\MTA San Andreas All\{Common,1.7}` (logs, cache), <!-- linkcheck: ignore -->
`engine\mtasa\Bin\MTA` (`config\coreconfig.xml`, `logs`, `dumps`, a copy of the exe to launch) and, if stale flags <!-- linkcheck: ignore -->
are already there, `AppCompatFlags\Layers` for `gta_sa.exe` (rollback: delete those values). Later steps that were <!-- linkcheck: ignore -->
not reached here: the MTA first-run dialogs and the serial (`netc.dll`). To check readiness:
`python -m satk.viewer.backends.mta_lua preflight` (`"ready": true`). An alternative without an administrator is a
developer mode in the fork (paths from the environment instead of `HKLM`); that is a planned engine task.

## Limitations and known issues

- One agent client (the first one that sent `satk:hello`); the agent's player is invisible, frozen, without
  collisions.
- `world.settle` waits frames after `enginePreloadWorldArea`: the streaming queue is not visible from Lua, `pending`
  is always 0 (a warning in the reply). An honest predicate needs a hook in the fork (planned).
- No `capture.ids`/`capture.depth`, `entity.*`, `view.set`, `asset.render` (`UNSUPPORTED`).
- `capture.size` larger than the window is stretched (blurry); frame width and height come from the client window.
- A minimized MTA window does not render: client requests end with `TIMEOUT` and a hint.
- `dxGetTexturePixels` returns a 32×32 stub when "Allow screen upload" is off in the MTA settings: `NOT_READY`.
- `lua.exec` is limited by MTA's own timeout ("Aborting; infinite running script"); `resource` is only `satk-agent`.
- `console.exec` runs only the command handlers of server scripts (`executeCommandHandler`).

## Python API

```python
from satk.viewer.backends import get_backend           # target "game" -> MtaLuaBackend
from satk.viewer.backends import mta_lua
mta_lua.start_server(); mta_lua.client_preflight(); mta_lua.start_client(); mta_lua.stop()
b = get_backend("game"); b.call("camera.set", {"pose": {"pos": [2495, -1720, 60], "look": [2495, -1670, 15]}})
```
