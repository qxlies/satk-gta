# SAAP/1 — San Andreas Agent Protocol, version 1

Status: **FROZEN v1** (2026-10-04). Source of truth for the satk mock, the
`ARIANE_IPC/1` adapter, the native Ariane endpoint, the MTA Lua resource, the native MTA
client/server endpoints (planned) and the Blender studio. Machine-readable parts: `schema/*.json` (JSON Schema 2020-12, one file
per method with `$defs.params` and `$defs.result`, shared types in `common.json`),
`conformance/*.jsonl` (request → expectation cases) and `cpp/saap_frame.hpp` (framing,
limits, token check, descriptor writing; MIT, header-only, C++14).

Inside the major version fields are only **added**; receivers ignore unknown fields and
extensions are announced through capabilities (`caps`). Anything not stated here is
implementation-defined and must not be relied on by clients.

Contents: 1 Transport · 2 Envelope · 3 Errors · 4 Authentication · 5 Discovery ·
6 Common types · 7 Capabilities · 8 Methods · 9 Conformance · 10 Implementation notes ·
11 Scene (extension of `view`).

---

## 1. Transport

- TCP on **127.0.0.1 only**. The launcher chooses the port (`SATK_AGENT_PORT`, `0` = ephemeral;
  the endpoint reports the real port in its descriptor, §5).
- Frame: `u32` **big-endian** payload length `N`, then `N` bytes of UTF-8 JSON. The payload is
  one JSON object. `N = 0` is a protocol error.
- Limits: request payload ≤ **1 MiB** (1 048 576 bytes), response payload ≤ **8 MiB**
  (8 388 608 bytes). An endpoint that reads a length prefix above its request limit answers
  `PROTOCOL` (without reading the payload) and closes the connection. A client that reads a
  length above the response limit closes the connection and reports `PROTOCOL`.
- **Closing after an error** (`PROTOCOL`, `AUTH`): send the error frame, half-close
  (`shutdown(SD_SEND)`), read and discard what the client still sends (≤ 4 MiB, ≤ 2 s, stop
  after 0.25 s of silence or at EOF), then close. Closing with unread bytes in the receive
  buffer (e.g. the body of an oversize frame) makes the OS reset the connection, and the
  client loses the error frame.
- Binary data (frames, buffers, previews) is **never** sent inline. The endpoint writes files
  under the `path_prefix` given in the request and returns their absolute paths. The client
  checks that every returned path lies inside the satk work directory
  (`<workspace>\work`, normally `work\out\captures\` or `work\run\captures\`).
- Several connections may be open at once. On one connection at most **one** request is in
  flight: the client sends the next request only after the response to the previous one.
- Endpoints run methods on the **main thread** of the game/viewer with a dispatcher budget of
  ≤ 2 ms per frame; network I/O is non-blocking. Long methods (`capture`, `world.settle`,
  `camera.set` with `stream:"settle"`) complete after N frames; the response is held until the
  method is done or its timeout expires.
- A connection silent for 10 s in the middle of a frame may be dropped by the endpoint.

## 2. Envelope

Request:

```json
{"saap":1,"id":"r17","method":"camera.set","params":{"pose":{"pos":[2495,-1720,60],"look":[2495,-1670,15]},"fov_h_deg":70}}
```

| Field | Type | Meaning |
|---|---|---|
| `saap` | integer, `1` | major version; any other value → `PROTOCOL` |
| `id` | string, 1–64 chars | unique within the connection; echoed in the response |
| `method` | string | method name (§8) |
| `params` | object | method parameters; may be omitted for methods without parameters |

Response (success / failure):

```json
{"saap":1,"id":"r17","ok":true,"result":{"pose":{"pos":[2495,-1720,60],"look":[2495,-1670,15]},"rev":9},"meta":{"frame":12345,"rev":{"scene":3,"camera":9},"ms":4.1}}
{"saap":1,"id":"r18","ok":false,"error":{"code":"UNSUPPORTED","message":"capability 'capture.ids' not available","retryable":false,"data":{}},"meta":{"frame":12346}}
```

| Field | Type | Meaning |
|---|---|---|
| `saap` | `1` | |
| `id` | string | the request `id` (`""` when the request could not be parsed) |
| `ok` | boolean | |
| `result` | object | present when `ok` is true |
| `error` | object | present when `ok` is false: `code`, `message`, `retryable`, `data` |
| `meta` | object | optional: `frame` (endpoint frame counter), `rev` (`scene`, `camera`), `ms` (handling time) |

Rules:

- `id` uniqueness is the client's duty; the endpoint does not check it.
- `null` result fields may be omitted; clients treat a missing optional field as absent.
- Field names are `snake_case`; enum values are lower case.

## 3. Errors

`error.code` is one of (a subset of satk's error codes, `src/satk/core/errors.py`):

| Code | When | `retryable` | Connection |
|---|---|---|---|
| `AUTH` | first request is not `hello`, or the token is wrong | false | **closed** |
| `PROTOCOL` | bad frame, bad JSON, payload not an object, `saap` ≠ 1, missing `id`/`method`, oversize frame | false | **closed** |
| `UNKNOWN_METHOD` | method name not defined by this protocol or the endpoint | false | kept |
| `BAD_PARAMS` | params violate the method schema or are semantically invalid | false | kept |
| `UNSUPPORTED` | method or option needs a capability the endpoint does not have (`data.capability`) | false | kept |
| `NOT_READY` | world not loaded, window minimized, device lost | true | kept |
| `BUSY` | another long request is being served and the endpoint does not queue | true | kept |
| `TIMEOUT` | the method did not finish within its `timeout_ms` | true | kept |
| `NOT_FOUND` | unknown entity ref, model or log cursor | false | kept |
| `REVISION` | `expect_rev` is stale (`data.rev` = current revision) | false | kept |
| `INTERNAL` | anything else | false | kept |

`message` is short English text for humans; `data` is a small object with machine-readable
details (may be `{}`).

## 4. Authentication

- The first request on every connection must be `hello` with `params.token` (32–256 bytes of
  UTF-8, no `\r` or `\n`). The comparison is constant-time. On any other first request or a
  wrong token the endpoint answers `AUTH` and closes the connection.
- The launcher generates the token (`secrets.token_hex(32)`) and passes it to the endpoint in
  `SATK_AGENT_TOKEN` (for the `ARIANE_IPC/1` adapter: `ARIANE_ENGINE_TOKEN`).
- Without a token the endpoint does not listen at all. The only exception is the `mock` role
  with `SATK_AGENT_INSECURE=1` (accepts any token of valid shape; tests only).
- A second `hello` on an authenticated connection is allowed and returns the same result.

## 5. Discovery

Two small JSON files under `<workspace>\work\run\`:

`endpoints\<role>.json` — written **atomically by the endpoint** right after `listen()`
(temp file + rename). It never contains the token:

```json
{"protocol":"saap/1","role":"ariane","impl":"ariane-satk","impl_version":"0.1","pid":1234,"port":50123,
 "build":{"id":"ariane-fnv1a64-…","git":"bab8b1c"},"caps":["core","camera","capture"],"started_at":"2026-10-04T15:32:01Z"}
```

For the `ARIANE_IPC/1` adapter the launcher writes this file itself with
`"protocol":"ariane-ipc/1","impl":"ariane-legacy"`.

`sessions\<role>.json` — written by the **launcher** (the endpoint when it launches itself,
e.g. `satk view mock`): `{"role","pid","token","log","launched_by","args","started_at"}`.
The threat model is the local user only.

Optional fields of either file (written by satk's own endpoints and launcher; recommended for
every endpoint): `exe` — the real image path of the process; `pid_created` — the process start
time, Unix seconds (`GetProcessTimes` creation time).

`satk.saap.client.connect(role)` reads both files, checks that `pid` is alive, that it is the
**same process** (below) and that the `pid` in both files agree, then connects and sends
`hello`.

**Stale files and reused pids.** Windows reuses pids, so files left behind by a crashed endpoint
can name an unrelated live process. A pid only counts as the endpoint when its image path
equals `exe` (when recorded) and its start time matches `pid_created` (±1 s) or, without it, is
not later than `started_at` + 2 s. Otherwise the files are stale: clients treat the role as not
running, `satk view start` replaces the files, and `satk view stop` only deletes them — it never
sends `WM_CLOSE` or terminates a pid it cannot verify.

Roles: `ariane`, `game` (= `client1`), `client2`, `server`, `blender`, `mock`.

Several Blender studio sessions can run at once: each has its own pair of files named
`blender-<name>.json` (the descriptor keeps `"role":"blender"` and adds `"name"`).

## 6. Common types

All types are defined in `schema/common.json` (`$defs`).

**Vec3** — `[x, y, z]`, numbers, GTA world units (≈ metres), right-handed, **Z up**.

**Pose** — camera pose, one of:

- `{"pos": Vec3, "look": Vec3}` — camera position and a point it looks at;
- `{"pos": Vec3, "ypr": [yaw, pitch, roll]}` — degrees. `yaw = 0` looks along **+Y**
  (north), yaw grows counter-clockwise seen from above (as GTA heading): the view direction is
  `(-sin yaw · cos pitch, cos yaw · cos pitch, sin pitch)`. Positive pitch looks up. Positive
  roll banks the camera clockwise as seen from behind it (the horizon tilts counter-clockwise
  in the image); endpoints that cannot roll ignore it and say so in their logs.

A Pose may also carry `fov_h_deg` (number, 1–179): the **horizontal** field of view for the
aspect ratio of the image being produced. Each implementation converts it to its own
convention (Ariane/librw: 4:3-based FOV; MTA: `setCameraMatrix` FOV). When a method also has a
top-level `fov_h_deg`, the top-level value wins.

Poses returned by endpoints always use the `look` form (plus `fov_h_deg`); `look` is then
`pos + forward` scaled to the distance of the original look point, or 10 m when unknown.

**Env** — `{"time": "HH:MM", "weather": int, "weather_b"?: int, "blend"?: 0..1, "freeze"?: bool}`.
`time` is 24-hour game time; `weather` is the weather id of `data/timecyc.dat` (0–22 in SA);
`weather_b` + `blend` describe interpolation to a second weather; `freeze` stops the game clock.
`env.set` takes any subset of these fields.

**EntityRef** — a visible or queried world entity:

```json
{"ref":"opaque-runtime-handle","kind":"building","model_id":17613,"model_name":"lae2_roads89",
 "pos":[2489.3,-1668.5,12.3],"rot":{"q_world":[0,0,0,1]},"area":0,"lod":false,
 "subject":"inst:lae2_stream0#4","src":{"kind":"ipl_bin","ipl":"lae2_stream0","idx":4}}
```

| Field | Type | Meaning |
|---|---|---|
| `ref` | string, required | opaque runtime handle, valid while the entity exists in this endpoint process; pass it back to `entity.inspect`, `view.set.hide/highlight` |
| `kind` | enum | `building`, `object`, `dummy`, `vehicle`, `ped`, `player`, `marker` |
| `model_id` | integer | game model id |
| `model_name` | string | model name, lower case |
| `pos` | Vec3 | world position of the entity origin |
| `rot.q_world` | `[x,y,z,w]` | world rotation quaternion (IPL quaternions are conjugated) |
| `area` | integer | interior (area) id, 0 = outside |
| `lod` | boolean | the entity is a LOD (low-detail) model |
| `subject` | SID string | satk stable id when the endpoint knows it (`inst:…`, `el:…`) |
| `src` | object | where the entity comes from, one of: `{"kind":"ipl_text","file":"data/maps/la/lae2.ipl","idx":198}`, `{"kind":"ipl_bin","ipl":"lae2_stream0","idx":4}`, `{"kind":"model_pos"}` (only model and position known), `{"kind":"runtime","type":"object","id":412}` (MTA element) |

When `subject` is missing, satk derives it (`satk.viewer.resolve`):
`src.ipl_text` → `inst:<file stem>#<idx>`, `src.ipl_bin` → `inst:<ipl>#<idx>` (link `exact`);
otherwise the index looks the entity up by `(model_id, pos)` with 0.05 m tolerance
(one match: `nearest`, several: `ambiguous` + candidates); MTA elements become `el:<type>/<id>`.

**Paths** — absolute, forward or backward slashes accepted, UTF-8.

## 7. Capabilities

`hello.result.caps` lists what the endpoint implements. A method whose capability is absent
answers `UNSUPPORTED`. Sub-capabilities refine a method:

| Capability | Methods / options |
|---|---|
| `core` | `hello`, `ping`, `status`, `quit` (always present) |
| `camera` | `camera.get`, `camera.set`, `camera.release` |
| `world.settle` | `world.settle`; `camera.set` `stream:"settle"`; `capture.settle` |
| `capture` | `capture` with the `color` layer |
| `capture.size` | `capture` honours `w`/`h` (otherwise the image has the window size) |
| `capture.ids` | `capture` layer `ids` + `ids_legend` |
| `capture.depth` | `capture` layer `depth` |
| `pick` | `pick` (`mode:"collision"`), `raycast` |
| `pick.visible` | `pick` `mode:"visible"` (ID-buffer accurate) |
| `entity.query` | `entity.query` |
| `entity.inspect` | `entity.inspect` |
| `env` | `env.get`, `env.set` |
| `view` | `view.set`; `scene.*` (§11: the agent's own models, vehicles and peds) |
| `asset.render` | `asset.render` |
| `log` | `log.poll` |
| `console` | `console.exec` |
| `lua` | `lua.exec` (dev builds only) |
| `mem.read` | `mem.read` (dev builds only) |
| `author` | `author.call`, `author.methods` (the Blender studio: step-by-step modelling) |

Implementation matrix: mock — all; Ariane via the `ARIANE_IPC/1` adapter — `core camera
world.settle capture pick entity.query entity.inspect env asset.render`; native Ariane adds
`capture.size capture.ids capture.depth pick.visible view`; the MTA Lua resource answers what its
`hello` lists (`docs/en/mta-agent.md`); the native MTA endpoints are planned; the Blender studio
(`satk blender session`) answers `core author`.

## 8. Methods

Each method below lists its capability, params, result and one example exchange
(`→` request, `←` response). The schemas in `schema/<method>.json` are normative; the tables
are a readable copy. Defaults apply when a field is omitted.

### `hello`

Capability: `core`

Params:

| Field | Type | Default | Meaning |
|---|---|---|---|
| `token` | string 32–256 | required | session token (§4) |
| `client` | `{name, version}` | required | client identification for logs |
| `want_caps` | string[] | — | capabilities the client intends to use (informational) |

Result: `saap` (1), `role`, `impl`, `impl_version`, `build` (`{id, git?}`), `caps` (string[]),
`game` (`{version:"1.0us", root?, exe_sha256?}`), `world` (`{units:"m", up:"z"}`),
`limits` (`{max_request, max_response}`), `viewport` (`{w, h}`: window/back-buffer size).

Example:

```json
→ {"saap":1,"id":"h1","method":"hello","params":{"token":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","client":{"name":"satk","version":"0.1.0"}}}
← {"saap":1,"id":"h1","ok":true,"result":{"saap":1,"role":"mock","impl":"satk-mock","impl_version":"0.1.0","build":{"id":"satk-mock-1"},"caps":["core","camera","capture"],"game":{"version":"1.0us"},"world":{"units":"m","up":"z"},"limits":{"max_request":1048576,"max_response":8388608},"viewport":{"w":800,"h":600}},"meta":{"frame":1}}
```

### `ping`

Capability: `core`

Params: none.

Result: `t_ms` (number) — endpoint monotonic clock in milliseconds.

Example:

```json
→ {"saap":1,"id":"p1","method":"ping","params":{}}
← {"saap":1,"id":"p1","ok":true,"result":{"t_ms":123456.5}}
```

### `status`

Capability: `core`

Params: none.

Result: `frame` (integer), `fps` (number), `pose` (Pose with `fov_h_deg`), `env` (Env),
`streaming` (`{pending}` — models requested but not loaded), `rev` (`{scene, camera}`),
optional `window` (`{w, h, minimized}`).

Example:

```json
→ {"saap":1,"id":"s1","method":"status"}
← {"saap":1,"id":"s1","ok":true,"result":{"frame":812,"fps":60.0,"pose":{"pos":[2495,-1720,60],"look":[2495,-1670,15],"fov_h_deg":70},"env":{"time":"12:00","weather":0},"streaming":{"pending":0},"rev":{"scene":3,"camera":9}}}
```

### `quit`

Capability: `core`

Params: none.

Result: `{}`. The endpoint answers first, then closes all connections and shuts down (an
embedded endpoint may only stop listening and ask its host to exit).

Example:

```json
→ {"saap":1,"id":"q1","method":"quit","params":{}}
← {"saap":1,"id":"q1","ok":true,"result":{}}
```

### `camera.get`

Capability: `camera`

Params: none.

Result: `pose` (Pose, `look` form), `fov_h_deg`, `near`, `far` (clip planes, m), `rev`
(camera revision, incremented by every camera change).

Example:

```json
→ {"saap":1,"id":"c1","method":"camera.get","params":{}}
← {"saap":1,"id":"c1","ok":true,"result":{"pose":{"pos":[2495,-1720,60],"look":[2495,-1670,15]},"fov_h_deg":70,"near":0.1,"far":3000,"rev":9}}
```

### `camera.set`

Capability: `camera` (`stream:"settle"` also needs `world.settle`)

Params:

| Field | Type | Default | Meaning |
|---|---|---|---|
| `pose` | Pose | required | new pose |
| `fov_h_deg` | number 1–179 | unchanged | horizontal FOV |
| `expect_rev` | integer | — | optimistic lock: if the current camera revision differs → `REVISION` |
| `stream` | `none` \| `settle` \| `anchor` | `none` | `settle`: wait until streaming settles before answering; `anchor`: move the streaming focus to the camera (engines whose streaming follows the player) |

Result: `pose` (as applied), `rev` (new camera revision), `settled` (when `stream` ≠ `none`).

Example:

```json
→ {"saap":1,"id":"c2","method":"camera.set","params":{"pose":{"pos":[2495,-1720,60],"look":[2495,-1670,15]},"fov_h_deg":70,"expect_rev":9}}
← {"saap":1,"id":"c2","ok":true,"result":{"pose":{"pos":[2495,-1720,60],"look":[2495,-1670,15],"fov_h_deg":70},"rev":10}}
```

### `camera.release`

Capability: `camera`

Params: none.

Result: `{}` — the camera goes back to the player (game) or the user (viewer).

Example:

```json
→ {"saap":1,"id":"c3","method":"camera.release","params":{}}
← {"saap":1,"id":"c3","ok":true,"result":{}}
```

### `world.settle`

Capability: `world.settle`

Params:

| Field | Type | Default | Meaning |
|---|---|---|---|
| `max_frames` | integer 1–1200 | 120 | give up after this many frames |
| `quiet_frames` | integer 1–60 | 2 | frames in a row with no pending models and a stable render list |
| `timeout_ms` | integer 1–60000 | 5000 | wall-clock limit (`TIMEOUT` when exceeded) |

Result: `settled` (boolean), `frames` (frames waited), `pending` (models still pending).

Example:

```json
→ {"saap":1,"id":"w1","method":"world.settle","params":{"max_frames":120,"quiet_frames":2}}
← {"saap":1,"id":"w1","ok":true,"result":{"settled":true,"frames":14,"pending":0}}
```

### `capture`

Capability: `capture` (`capture.size` for `w`/`h`, `capture.ids`, `capture.depth` for layers)

Params:

| Field | Type | Default | Meaning |
|---|---|---|---|
| `path_prefix` | string | required | absolute path prefix; files are `<prefix>.png` (color), `<prefix>.ids.png`, `<prefix>.depth.f32` |
| `w`, `h` | integer 16–7680 | window size | image size (needs `capture.size`; without it the size is the window and is reported in the result) |
| `pose` | Pose | current camera | stateless capture: the pose is applied for this capture only and the previous camera is restored |
| `env` | Env | current | environment for this capture only |
| `layers` | string[] | `["color"]` | `color`, `ids`, `depth`; anything else → `UNSUPPORTED` |
| `settle` | `{max_frames, quiet_frames}` | `{max_frames:120, quiet_frames:2}` when `world.settle` is present | wait for streaming first; `{"max_frames":0}` captures immediately |
| `hide` | string[] | `["hud","gui","overlays"]` | screen elements to hide: `hud`, `gui`, `chat`, `overlays` |
| `format` | `png` | `png` | color/ids image format |

Result: `files` (`{color, ids?, depth?}` absolute paths), `ids_legend` (with `ids`:
`[{id, entity: EntityRef, px}]`, `px` = pixel count), `w`, `h`, `pose` (actual, with
`fov_h_deg`), `frame`, `settled`, `pending`, `frames_waited`, `sha256` (of the color file).

Layer formats: `color` — 8-bit RGB PNG. `ids` — 8-bit RGB PNG, pixel value
`(r << 16 | g << 8 | b)` = legend `id`, 0 = nothing (sky). `depth` — raw little-endian float32,
`w × h` values row-major from the top-left, linear distance along the view axis in metres,
`+inf` where nothing was hit.

Example:

```json
→ {"saap":1,"id":"k1","method":"capture","params":{"path_prefix":"<workspace>/work/out/captures/20261004/20261004-153201-ab12","w":960,"h":540,"pose":{"pos":[2495,-1720,60],"look":[2495,-1670,15],"fov_h_deg":70},"layers":["color","ids"]}}
← {"saap":1,"id":"k1","ok":true,"result":{"files":{"color":"<workspace>/work/out/captures/20261004/20261004-153201-ab12.png","ids":"<workspace>/work/out/captures/20261004/20261004-153201-ab12.ids.png"},"ids_legend":[{"id":1,"entity":{"ref":"m1","kind":"building","model_id":17613,"model_name":"lae2_roads89","pos":[2489.3,-1668.5,12.3],"src":{"kind":"model_pos"}},"px":51234}],"w":960,"h":540,"pose":{"pos":[2495,-1720,60],"look":[2495,-1670,15],"fov_h_deg":70},"frame":930,"settled":true,"pending":0,"frames_waited":3,"sha256":"5d41402abc4b2a76b9719d911017c592ae0e1f2a3cbd3ae0a6dcb1cc0b8f3e11"}}
```

### `pick`

Capability: `pick` (`mode:"visible"` needs `pick.visible`)

Params:

| Field | Type | Default | Meaning |
|---|---|---|---|
| `points` | `[[px, py], …]` (1–256) | required | pixel coordinates from the top-left corner; integer values address pixel centres (pixel `(0,0)` covers `[0,1)×[0,1)`); fractions allowed |
| `space` | `capture` \| `window` | `window` | `capture`: pixels of a `w × h` image taken at `pose`; `window`: current window pixels |
| `w`, `h` | integer | window size | image size for `space:"capture"` |
| `pose` | Pose | current camera | pose for `space:"capture"` (stateless) |
| `mode` | `visible` \| `collision` | `collision` | `visible` = what is drawn (ID buffer); `collision` = collision geometry ray |

Result: `hits` — one per point: `{px, py, hit, pos?, normal?, dist?, entity?: EntityRef}`.

Example:

```json
→ {"saap":1,"id":"k2","method":"pick","params":{"points":[[400,300]],"space":"window","mode":"collision"}}
← {"saap":1,"id":"k2","ok":true,"result":{"hits":[{"px":400,"py":300,"hit":true,"pos":[2489.3,-1668.5,12.8],"normal":[0,0,1],"dist":71.4,"entity":{"ref":"m1","kind":"building","model_id":17613,"model_name":"lae2_roads89","pos":[2489.3,-1668.5,12.3],"src":{"kind":"model_pos"}}}]}}
```

### `raycast`

Capability: `pick`

Params: `from` (Vec3, required), `to` (Vec3, required), `mode` (`visible` | `collision`,
default `collision`).

Result: `hit` (boolean), `pos`, `normal`, `dist` (from `from`), `entity` (EntityRef) when hit.

Example:

```json
→ {"saap":1,"id":"k3","method":"raycast","params":{"from":[2489.3,-1668.5,60],"to":[2489.3,-1668.5,0]}}
← {"saap":1,"id":"k3","ok":true,"result":{"hit":true,"pos":[2489.3,-1668.5,12.8],"normal":[0,0,1],"dist":47.2,"entity":{"ref":"m1","kind":"building","model_id":17613}}}
```

### `entity.query`

Capability: `entity.query`

Params:

| Field | Type | Default | Meaning |
|---|---|---|---|
| `center` | Vec3 | required | query centre |
| `r` | number > 0, ≤ 3000 | required | radius, horizontal (XY) distance from `center` |
| `kinds` | EntityRef kind[] | all | filter |
| `model` | integer | — | only this model id |
| `include_lod` | boolean | false | also list LOD entities |
| `limit` | integer 1–256 | 50 | page size |
| `cursor` | string | — | `next` of the previous page |

Result: `items` (EntityRef[], sorted by distance, then `ref`), `next` (cursor or absent),
`total` (optional, number of matches).

Example:

```json
→ {"saap":1,"id":"e1","method":"entity.query","params":{"center":[2489.3,-1668.5,12.3],"r":5,"limit":10}}
← {"saap":1,"id":"e1","ok":true,"result":{"items":[{"ref":"m1","kind":"building","model_id":17613,"model_name":"lae2_roads89","pos":[2489.3,-1668.5,12.3],"rot":{"q_world":[0,0,0,1]},"area":0,"lod":false,"src":{"kind":"model_pos"}}],"total":1}}
```

### `entity.inspect`

Capability: `entity.inspect`

Params: `ref` (string, required) — an EntityRef `ref`.

Result: `entity` (EntityRef), `model` (`{id, name, txd?, flags?, draw?}`; `draw` = draw
distance), `lod` (`{parent?: EntityRef, children: EntityRef[]}`), `runtime` (free-form object,
endpoint-specific). Unknown or expired ref → `NOT_FOUND`.

Example:

```json
→ {"saap":1,"id":"e2","method":"entity.inspect","params":{"ref":"m1"}}
← {"saap":1,"id":"e2","ok":true,"result":{"entity":{"ref":"m1","kind":"building","model_id":17613},"model":{"id":17613,"name":"lae2_roads89","txd":"lae2roads","flags":0,"draw":299},"lod":{"children":[]}}}
```

### `env.get`

Capability: `env`

Params: none.

Result: Env (current values), optionally `weathers` (`[{id, name}]`).

Example:

```json
→ {"saap":1,"id":"v1","method":"env.get","params":{}}
← {"saap":1,"id":"v1","ok":true,"result":{"time":"12:00","weather":0,"weather_b":0,"blend":0,"freeze":false}}
```

### `env.set`

Capability: `env`

Params: any subset of Env (`time`, `weather`, `weather_b`, `blend`, `freeze`).

Result: Env as applied (all fields).

Example:

```json
→ {"saap":1,"id":"v2","method":"env.set","params":{"time":"21:30","weather":8}}
← {"saap":1,"id":"v2","ok":true,"result":{"time":"21:30","weather":8,"weather_b":8,"blend":0,"freeze":false}}
```

### `view.set`

Capability: `view`

Params (all optional): `overlays` (subset of `col`, `zones`, `paths`, `tcyc`; replaces the
current set), `lod_mode` (`normal` | `hd` | `lod`), `draw_dist_mul` (0.1–10), `area` (integer
or `-1` = all), `hide` (ref[]), `highlight` (ref[]), `wireframe` (boolean), `postfx`
(boolean). Unknown overlay names → `UNSUPPORTED`.

Result: `applied` — the full current view state (same fields).

Example:

```json
→ {"saap":1,"id":"v3","method":"view.set","params":{"overlays":["col"],"highlight":["m1"]}}
← {"saap":1,"id":"v3","ok":true,"result":{"applied":{"overlays":["col"],"lod_mode":"normal","draw_dist_mul":1,"area":0,"hide":[],"highlight":["m1"],"wireframe":false,"postfx":true}}}
```

### `asset.render`

Capability: `asset.render`

Params:

| Field | Type | Default | Meaning |
|---|---|---|---|
| `model` | integer \| string | required | model id or name |
| `path_prefix` | string | required | files are `<prefix>_<azimuth>.png` |
| `views` | number[] (1–16) | `[45,135,225,315]` | azimuths in degrees (0 = camera on +Y looking at −Y) |
| `el` | number −89…89 | 25 | elevation in degrees |
| `size` | integer 16–2048 | 256 | square image size |
| `bg` | `transparent` \| `#rrggbb` | `transparent` | background |

Result: `files` (paths, one per view, in order of `views`), optional `stats` (`{tris, tex_missing}`).

Example:

```json
→ {"saap":1,"id":"a1","method":"asset.render","params":{"model":411,"views":[45],"size":256,"path_prefix":"<workspace>/work/run/captures/infernus"}}
← {"saap":1,"id":"a1","ok":true,"result":{"files":["<workspace>/work/run/captures/infernus_45.png"]}}
```

### `log.poll`

Capability: `log`

Params: `streams` (subset of `console`, `script`, `server`, `debug`; default all), `since`
(sequence number; items with `seq > since`; default: from the oldest kept), `max` (1–1000,
default 100), `min_level` (`debug` | `info` | `warn` | `error`).

Result: `items` (`[{seq, t, stream, level, msg, file?, line?, resource?}]`, ascending `seq`;
`t` = endpoint time in ms), `next_seq` (pass as `since` next time), `dropped` (items lost from
the ring buffer since `since`).

Example:

```json
→ {"saap":1,"id":"l1","method":"log.poll","params":{"since":0,"max":10}}
← {"saap":1,"id":"l1","ok":true,"result":{"items":[{"seq":1,"t":1000,"stream":"console","level":"info","msg":"mock endpoint started"}],"next_seq":1,"dropped":0}}
```

### `console.exec`

Capability: `console`

Params: `line` (string, 1–1024 chars, required) — one console command line.

Result: `accepted` (boolean), `output` (string[], lines printed by the command).

Example:

```json
→ {"saap":1,"id":"x1","method":"console.exec","params":{"line":"echo hello"}}
← {"saap":1,"id":"x1","ok":true,"result":{"accepted":true,"output":["hello"]}}
```

### `lua.exec`

Capability: `lua` (development builds only)

Params: `code` (string, required, ≤ 64 KiB), `side` (`client` | `server`, default `client`),
`resource` (default `satk-agent`), `timeout_ms` (1–60000, default 5000).

Result: `values` (returned values as JSON, nesting depth ≤ 4, ≤ 64 KiB), `prints` (string[]),
`error` (`{msg, file?, line?}`) when the chunk failed (the request itself is still `ok`).

Example:

```json
→ {"saap":1,"id":"x2","method":"lua.exec","params":{"code":"print('hi') return 1 + 2"}}
← {"saap":1,"id":"x2","ok":true,"result":{"values":[3],"prints":["hi"]}}
```

### `mem.read`

Capability: `mem.read` (development builds only)

Params: `addr` (integer or `"0x…"` string, required), `len` (integer 1–4096, required).

Result: `hex` (lower-case hex string, `2 × len` characters). Unreadable memory → `NOT_FOUND`.

Example:

```json
→ {"saap":1,"id":"x3","method":"mem.read","params":{"addr":"0x858b9c","len":4}}
← {"saap":1,"id":"x3","ok":true,"result":{"hex":"00000000"}}
```

### `author.call`

Capability: `author`

Runs one studio *method* in the scene (plug-ins with a `METHODS` table; `author.methods` lists
them) and reports what changed. Every call is journaled; requests run one at a time on the
endpoint's main thread.

Params:

| Field | Type | Default | Meaning |
|---|---|---|---|
| `method` | string (dotted, ≤ 64) | required | studio method, e.g. `mesh.primitive`, `scene.info`, `python` |
| `params` | object | `{}` | the method's own parameters |
| `stats` | `auto` \| `none` \| `scene` | `auto` | `auto`: after a mutating method, rows for the changed objects + scene totals; `scene`: totals also after a read-only one |
| `snapshot` | `{view, views, size, look, objects, wire, ghost, refs, ref_alpha}` | — | JPEG after the step: `view` `3q`/`front`/`rear`/`left`/`right`/`top` or a camera name, `views` 2–4 views as a 2x2 sheet, `size` 64–1024 (512), `look` `clay`/`raw`/`game`/`wire`, `wire` edge overlay, `ghost` `overlay`/`lineup`/`hide` for reference models, `refs`/`ref_alpha` reference photos behind the model |
| `checkpoint` | boolean | — | `true`: save a `.blend` checkpoint after the step; `false`: none; absent: the endpoint's rule (every N-th mutating step, `python` steps, a batch with two or more mutating steps) |
| `timeout` | number (1–7200) | 600 | time budget in seconds: a method that overruns it is stopped and answered `TIMEOUT` (the endpoint stays up); when it cannot be stopped the endpoint answers `TIMEOUT` itself a few seconds later and then `BUSY` until it is free |
| `open` | Path | — | open this `.blend` before the step |
| `save` | Path | — | save the scene to this `.blend` after the step (inside the work directory) |

Result: `method`, `n` (journal sequence number), `ms`, optional `result` (the method's own
answer), `changed` (object names), `stats` (`{scene: {objects, tris, verts, bbox}, objects:
{name: {tris, verts, dims, ...}}}` of the evaluated meshes), `snapshot`, `checkpoint`, `saved`
(paths), `warn` (`"CODE: text"`), `truncated` (what was cut to keep the reply ≤ 1.5 KB). An
exception inside a method is an error (`BAD_PARAMS` for wrong parameters, else `INTERNAL`);
the endpoint stays up. An unknown method is `NOT_FOUND` with `data.did_you_mean`.

The method `batch` (`params.steps`: 1–200 `{method, params}`) runs the steps in order, journals each
one, then gives one `stats`, one snapshot and one checkpoint; the result lists `steps`
(`[{n, method, result?}]`, each result cut to its short values). A failing step stops the batch: its error carries `data.step` and
`data.done`, and the earlier steps stay applied. Every endpoint also serves `session.checkpoint`
(`tag`), `session.restore` (`ref`: step, tag or `last`), `session.checkpoints`, `session.prune`
(`keep`) and `session.journal` (`last`); a world without checkpoints answers them `UNSUPPORTED`.

Example:

```json
→ {"saap":1,"id":"a1","method":"author.call","params":{"method":"mesh.primitive","params":{"kind":"cylinder","segments":12,"radius":0.3,"depth":1.0,"name":"post"}}}
← {"saap":1,"id":"a1","ok":true,"result":{"method":"mesh.primitive","n":3,"result":{"object":"post"},"changed":["post"],"stats":{"scene":{"objects":1,"tris":44,"verts":24},"objects":{"post":{"tris":44,"verts":24,"dims":[0.6,0.6,1.0]}}},"ms":2.1}}
```

### `author.methods`

Capability: `author`

Params: `query` (string ≤ 64: a word to filter names and descriptions), `limit` (1–1000,
default 500).

Result: `methods` (`[{name, doc, readonly?}]`, sorted by name), `total`, optional `errors`
(plug-in modules that failed to import).

Example:

```json
→ {"saap":1,"id":"m1","method":"author.methods","params":{"query":"primitive"}}
← {"saap":1,"id":"m1","ok":true,"result":{"methods":[{"name":"mesh.primitive","doc":"Add a primitive with explicit density"}],"total":1}}
```

## 9. Conformance

`conformance/*.jsonl` holds one test case per line:

```json
{"id":"camera.set.revision","caps":["camera"],"doc":"stale expect_rev is rejected","steps":[
  {"call":"camera.get","save":{"rev":"result.rev"}},
  {"call":"camera.set","params":{"pose":{"pos":[0,0,50],"look":[0,10,0]},"expect_rev":"${rev}"},"expect":{"ok":true}},
  {"call":"camera.set","params":{"pose":{"pos":[0,0,50],"look":[0,10,0]},"expect_rev":"${rev}"},"expect":{"ok":false,"error":"REVISION"}}]}
```

Case fields: `id` (unique), `caps` (all required; otherwise the case is skipped), `doc`,
`transport` (`"saap"` = needs raw SAAP framing; such cases are skipped for adapters that are
not SAAP endpoints), `hello` (default `true`: the runner opens a fresh connection and
authenticates first; `false` = raw connection), `steps`.

Step kinds:

- `{"call": method, "params": {...}}` — a normal request; `"invalid": true` marks params that
  must **fail** schema validation (used by negative cases);
- `{"send": {...}}` — send this exact envelope object (no `id` is added);
- `{"raw": {"length": N, "body": "text", "fill": F}}` — send a frame header announcing `N` bytes
  followed by `body` (UTF-8) as is and then `F` filler bytes (optional, ≤ 2 MiB; used to send a
  whole oversize frame);
- `"expect"` — `{"ok": bool, "error": CODE, "disconnect": bool, "result": PATTERN}`;
  `disconnect: true` means the endpoint must close the connection after the answer;
- `"save"` — `{"var": "result.dotted.path"}`, used later as `"${var}"` (a whole-string
  reference keeps the JSON type).

Built-in variables: `${token}` (valid token), `${bad_token}` (valid shape, wrong value),
`${prefix}` (a fresh absolute `path_prefix` inside the work directory).

Pattern language (subset match: only listed keys are compared): literals compare equal;
`{"$approx": x, "tol": t}` (number or number list); `{"$type": "string"}`; `{"$gte": n}`,
`{"$lte": n}`; `{"$len": n}`, `{"$minlen": n}`; `{"$contains": pattern}` (some list element
matches); `{"$match": "regex"}`; `{"$file": {"png": [w, h]}}` / `{"$file": {"png": "result"}}`
(the size given by `result.w`/`result.h`) / `{"$file": {"size": n}}` / `{"$file": {}}` (the path
exists inside the work directory and has that PNG size / byte size). Strings inside patterns
are expanded like params (`"${ref}"`).

Every successful `result` is also validated against `schema/<method>.json#/$defs/result`.

Run: `satk view conformance --target mock|ariane|game` (cases filtered by the endpoint's
`caps`); validate the files themselves: `satk saap validate proto/conformance/*.jsonl proto/SAAP-v1.md`.

## 10. Implementation notes

- **Framing in C++**: `cpp/saap_frame.hpp` — `saap::encode_header`, `saap::FrameReader`
  (incremental, non-blocking friendly), `saap::token_equal` (constant time),
  `saap::write_file_atomic` (descriptor files), limits as constants. JSON parsing is left to
  the host (`nlohmann/json` or the vendored json-c in MTA).
- **Main-thread dispatch**: accept and read on the frame tick (non-blocking `select()` with a
  zero timeout), execute at most one request per connection per frame, keep long requests
  pending across frames without blocking.
- **Stateless capture**: when `pose`/`env` are given the endpoint applies them for the
  capture only and restores the previous state afterwards (camera revision still increments).
- **Revisions**: `rev.camera` changes on every camera change (including restores),
  `rev.scene` on every world/visibility/environment change.
- **ARIANE_IPC/1 adapter** (Python, `satk.viewer.backends.ariane_legacy`): `hello`/`status` →
  `capabilities`, `camera_context`, `environment`; `camera.set` → `camera`; `capture` →
  `capture_pose` with `settle_frames` (a fork patch; without it a double capture warms the
  streaming); `pick` → `screen_to_world`; `raycast` → `raycast_segment`; `entity.query` →
  `inspect_zone_page`; `env.*` → `environment`; `asset.render` → `asset_preview` (one view per
  call); everything else → `UNSUPPORTED`. Capture size = window size.
- **Native Ariane endpoint** (fork patch P1, `viewer/ariane/tools/euryopa/saap/`): started with <!-- linkcheck: ignore -->
  `SATK_AGENT_PORT=0`, `SATK_AGENT_TOKEN`, `SATK_AGENT_DESCRIPTOR` (it writes its own descriptor, `impl`
  `ariane-satk`) and `SATK_AGENT_OUT_ROOT` (every `path_prefix` must lie inside, else `BAD_PARAMS`). Captures
  render offscreen at the requested size into a temporary camera (the window keeps its view, the camera
  revision does not change); settling happens inside the request (synchronous streaming). `ids` are instance
  colour codes remapped to a dense legend sorted by pixel count; `depth` comes from a separate pass with the
  same alpha cut-off as `ids`. `pick` `mode:"visible"` renders `ids` + `depth` at the request pose/size;
  `raycast` `mode:"visible"` renders 8×8 pixels with a 1° FOV along the segment. Entity refs are
  `i<instance id>`; `src` is `ipl_bin`/`ipl_text`. `asset.render` ignores `el` and `bg` (fixed preview
  camera); camera roll is ignored. Methods outside its caps (`log.poll`, `console.exec`, `lua.exec`,
  `mem.read`) answer `UNSUPPORTED`.

## 11. Scene: the agent's own entities

Extension of capability `view` (endpoints that implement `view` and not `scene.*` answer
`UNKNOWN_METHOD`). The agent puts its own work into the live view: a model straight from files
(DFF + TXDs), a vehicle or a ped. Entities live as long as the endpoint process; they have a
**handle** (`id` chosen by the client, or `s1`, `s2`, ...) and the entity ref `@<handle>`, which
works everywhere a ref does (`entity.inspect`, `view.set` `hide`/`highlight`). They are part of
every layer of `capture` (`ids_legend`), of `pick` (both modes; `collision` tests their bounds),
`raycast` and `entity.query` (kinds `object`, `vehicle`, `ped`). Their EntityRef has
`src: {"kind": "runtime", "type": "scene", "id": <handle>}` (satk: `el:scene/<handle>`) and an
extra object `scene` (`handle`, `dff`, `txd`, `watch`).

Common params of `scene.place`, `scene.vehicle` and `scene.ped`:

| Field | Type | Default | Meaning |
|---|---|---|---|
| `pos` | Vec3 | required | entity origin (vehicles: their centre; peds: the root, about 1 m above the feet) |
| `rot` | 3 or 4 numbers | — | `[rx, ry, rz]` degrees (X, then Y, then Z about world axes) or a quaternion `[x, y, z, w]` |
| `heading` | number | 0 | degrees about Z, counter-clockwise seen from above; not with `rot` |
| `ground` | boolean | `false` | drop onto the collision below `pos`: the lowest point of the model rests on it |
| `dff` | absolute path | — | the model file; with `model` the file replaces the game's model |
| `txd` | path or 1–8 paths | — | texture dictionaries, searched in order before the game's |
| `model` | integer or string | — | game model (id or name); one of `dff`/`model` is required |
| `watch` | boolean | `false` | reload when the files (DFF, TXDs, an IFP file) change: polled, applied once stable, about 0.5 s |
| `id` | string `[A-Za-z0-9_.-]{1,48}` | new `s<N>` | handle; placing again with the same `id` replaces that entity in place |

Result (all three): `handle`, `ref` (`@<handle>`), `entity` (EntityRef), `replaced`, `stats`
(`atomics`, `tris`, `textures`, `missing_textures` — names the TXDs did not have, `bounds` in
entity space), `grounded`, `warn` (`"CODE: text"`), `load_ms`. Errors: a missing file →
`NOT_FOUND` (`data.path`), a file that is not a clump/TXD → `BAD_PARAMS`, an unknown model →
`NOT_FOUND`, more than 256 entities → `BAD_PARAMS`.

Time and weather are `env.set` (§8); a capture can also take `env` for one frame.

### `scene.place`

Capability: `view`

Params: the common ones plus `scale` (number or Vec3, > 0, default 1). Map objects are drawn
with the world's own pipelines; atomics named `*_dam` and `*_vlo` are hidden.

Result: the common result.

Example:

```json
→ {"saap":1,"id":"s1","method":"scene.place","params":{"dff":"<workspace>/work/tmp/bench/bench.dff","txd":"<workspace>/work/tmp/bench/bench.txd","pos":[2493.0,-1671.0,13.4],"heading":30,"ground":true,"watch":true,"id":"bench"}}
← {"saap":1,"id":"s1","ok":true,"result":{"handle":"bench","ref":"@bench","entity":{"ref":"@bench","kind":"object","model_name":"bench","pos":[2493,-1671,12.75],"rot":{"q_world":[0,0,0.258819,0.965926]},"src":{"kind":"runtime","type":"scene","id":"bench"}},"replaced":false,"stats":{"atomics":1,"tris":164,"textures":2,"bounds":[[-0.32,-1.26,-0.4],[0.34,1.29,0.42]]},"grounded":true,"load_ms":4.9}}
```

### `scene.vehicle`

Capability: `view`

Params: the common ones (with `model` from `data/vehicles.ide`) plus:

| Field | Type | Default | Meaning |
|---|---|---|---|
| `colors` | 1–4 items: carcols index or `"#rrggbb"` | the first carcols set | paint slots 1–4 (material keys 60,255,0 / 255,0,175 / 0,255,255 / 255,0,255) |
| `dirt` | integer 0–15 | 0 | dirt level: `vehiclegrunge256` with RGB = c·i/16 + 255·(16−i)/16 |
| `lights` | boolean | `false` | lamps on (`vehiclelightson128`, unlit) |
| `parts` | object part → `ok`\|`dam`\|`off` | all `ok` | per frame (`door_lf`, `bonnet`, `bump_front`, ...): intact, damaged or missing |
| `wheels` | `{model?, scale?}` | from the IDE | wheel model (veh_mods wheels) and diameter in m (or `[front, rear]`) |

The frame hierarchy is kept (`*_dummy` frames, `*_ok`/`*_dam` atomics, `*_vlo` hidden); wheels
are copies of the wheel atomic (left ones turned 180°); env maps where the DFF has MatFX
(strength from the reflection plugin); glass (material alpha < 255) is drawn after everything
opaque. No specular highlights, no shadows.

Result: the common result plus `vehicle` (`type`, `txd`, `wheels` (`from`, `scale`, `count`),
`colors` (`[{slot, index?, rgb}]`), `dirt`, `lights`, `parts` (every part and its state),
`materials` (counts of paint, lamp and dirt materials)). An unknown part → `BAD_PARAMS` with
`data.parts`.

Example:

```json
→ {"saap":1,"id":"v1","method":"scene.vehicle","params":{"model":426,"pos":[2495.0,-1675.0,13.4],"heading":90,"dirt":2,"colors":[3,1],"parts":{"door_lf":"dam"},"ground":true,"id":"car"}}
← {"saap":1,"id":"v1","ok":true,"result":{"handle":"car","ref":"@car","entity":{"ref":"@car","kind":"vehicle","model_id":426,"model_name":"premier","pos":[2495,-1675,13.04],"rot":{"q_world":[0,0,0.707107,0.707107]},"src":{"kind":"runtime","type":"scene","id":"car"}},"replaced":false,"vehicle":{"type":"car","txd":"premier","wheels":{"from":"own","scale":[0.7,0.7],"count":4},"colors":[{"slot":1,"index":3,"rgb":"#840410"},{"slot":2,"index":1,"rgb":"#f5f5f5"}],"dirt":2,"lights":false,"parts":{"door_lf":"dam","bonnet":"ok"}},"stats":{"atomics":15,"tris":2652,"textures":13},"grounded":true,"load_ms":19.8}}
```

### `scene.ped`

Capability: `view`

Params: the common ones (with `model` from `data/peds.ide`) plus `anim` (`{ifp, name, time}`:
`ifp` is a game animation file — `ped` or an entry of `anim/anim.img` — or an absolute `.ifp`
path; `name` the animation, `time` seconds into it; default `ped`, `idle_stance`, 0). The ped is
skinned (Skin + HAnim) and drawn in that static pose, also in the ID and depth layers. CJ
(model 0) uses the clothes system and is `NOT_FOUND`; an unknown animation → `NOT_FOUND` with
`data.animations` (names in the file).

Result: the common result plus `ped` (`anim`: `ifp`, `name`, `time`, `duration`, `bones`
(tracks matched to the skeleton), `nodes`).

Example:

```json
→ {"saap":1,"id":"p1","method":"scene.ped","params":{"model":105,"pos":[2497.5,-1673.0,13.4],"heading":180,"ground":true,"id":"fam"}}
← {"saap":1,"id":"p1","ok":true,"result":{"handle":"fam","ref":"@fam","entity":{"ref":"@fam","kind":"ped","model_id":105,"model_name":"fam1","pos":[2497.5,-1673,13.3],"rot":{"q_world":[0,0,1,0]},"src":{"kind":"runtime","type":"scene","id":"fam"}},"replaced":false,"ped":{"anim":{"ifp":"ped","name":"idle_stance","time":0,"duration":1.5,"bones":32,"nodes":32}},"stats":{"atomics":1,"tris":1113,"textures":1},"grounded":true,"load_ms":21}}
```

### `scene.reload`

Capability: `view`

Params: `handle` (optional; without it every entity is reloaded).

Re-reads the files of the entity (or all) with the parameters it was placed with. A failed
reload keeps the previous model (`ok:false` and `error` in the row; for a single `handle` the
call fails with `BAD_PARAMS`). Watched entities do this by themselves.

Result: `reloaded` (`[{handle, ok, load_ms, error?}]`), `failed`.

Example:

```json
→ {"saap":1,"id":"r1","method":"scene.reload","params":{"handle":"bench"}}
← {"saap":1,"id":"r1","ok":true,"result":{"reloaded":[{"handle":"bench","ok":true,"load_ms":5.2}],"failed":0}}
```

### `scene.remove`

Capability: `view`

Params: `handle` or `handles` (1–256; `@` prefix allowed). An unknown handle → `NOT_FOUND`
and nothing is removed.

Result: `removed` (handles), `left` (entities still placed).

Example:

```json
→ {"saap":1,"id":"d1","method":"scene.remove","params":{"handles":["car","fam"]}}
← {"saap":1,"id":"d1","ok":true,"result":{"removed":["car","fam"],"left":1}}
```

### `scene.clear`

Capability: `view`

Params: none.

Result: `removed` (count).

Example:

```json
→ {"saap":1,"id":"d2","method":"scene.clear","params":{}}
← {"saap":1,"id":"d2","ok":true,"result":{"removed":3}}
```

### `scene.list`

Capability: `view`

Params: none.

Result: `items` (`[{handle, entity, stats, vehicle?, ped?, grounded?, warn?, reloads, load_ms,
error?}]` in placement order; `error` is the last failed reload), `total`.

Example:

```json
→ {"saap":1,"id":"l2","method":"scene.list","params":{}}
← {"saap":1,"id":"l2","ok":true,"result":{"items":[{"handle":"bench","entity":{"ref":"@bench","kind":"object","model_name":"bench","pos":[2493,-1671,12.75],"src":{"kind":"runtime","type":"scene","id":"bench"},"scene":{"handle":"bench","dff":"<workspace>/work/tmp/bench/bench.dff","watch":true}},"reloads":2,"load_ms":5.2}],"total":1}}
```
