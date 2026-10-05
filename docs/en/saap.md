# SAAP/1: the common "agent's eyes" protocol

[Русская версия](../ru/saap.md)

Package: `satk.saap`. The normative text is [`proto/SAAP-v1.md`](../../proto/SAAP-v1.md).

## What it is

SAAP/1 (San Andreas Agent Protocol) is the one protocol satk uses to drive everything that can show the world:
the mock endpoint, the Ariane viewer and, through a bridge, the real game in MTA (`--target game`, see
[mta-agent.md](../ru/mta-agent.md), in Russian); a native endpoint in the MTA fork and Blender come later. TCP on
127.0.0.1 only, a `u32 big-endian length + JSON` frame, a token in `hello`, capabilities in `caps`. Images never
travel inline: the endpoint writes files under `work\` and returns their paths.

## Quick example

```powershell
satk saap validate
satk view conformance --target mock
```

What comes back (shortened):

```json
{"ok":true,"cols":["file","kind","items","errors"],"rows":[["camera.jsonl","cases",5,0],["SAAP-v1.md","spec",21,0]],"valid":true,"schemas":21}
{"ok":true,"cols":["case","caps","result","ms","detail"],"rows":[["auth.bad_token","core","skip",0.0,"needs the SAAP transport"]],"total":48,"pass":38,"fail":0,"skip":10,"percent":100.0}
```

Without a running mock endpoint the conformance run is in-process, and the 10 transport cases (AUTH, PROTOCOL)
are skipped. With the endpoint running (`satk view start --target mock`) all 48 pass.

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk saap validate [files…]` | — | checks `*.jsonl` cases, `SAAP-v1.md` and `*.json` envelopes against the schemas (by default `proto/conformance/*.jsonl` and `proto/SAAP-v1.md`) |
| `satk saap call ROLE METHOD ['{json}'] [--timeout S]` | — | one request to a running endpoint, for example `satk saap call mock camera.get` |
| `satk saap cpp-selftest [--toolsets v143 v145] [--keep]` | — | builds and runs the `saap_frame.hpp` self-test with `cl.exe` (`/std:c++14` and `/std:c++latest`) |
| `satk view mock [--port 0]` | — | the mock endpoint in this console until `quit`/Ctrl+C |
| `satk view conformance --target T [--only RE] [--files F…] [--show-all]` | — | runs the cases against a target, filtered by its `caps` |

## How it works

- `proto/schema/<method>.json` is a JSON Schema 2020-12 for the `params` and `result` of each of the 21 methods;
  shared types (`Pose`, `Env`, `EntityRef`) are in `common.json`. The validator in `satk.saap.schema` uses only
  the stdlib (it also works inside Blender's Python); the tests cross-check it with the `jsonschema` package.
- `proto/conformance/*.jsonl` holds 48 "request → expectation" cases, negative ones included: a wrong token →
  `AUTH` and a disconnect, a frame over 1 MiB → `PROTOCOL` (both the header alone and the whole frame with its
  body), an unknown method → `UNKNOWN_METHOD`, a stale `expect_rev` → `REVISION`, an unknown layer →
  `UNSUPPORTED`.
- The disconnect after an error is "soft": the endpoint sends the response, shuts down its sending side, reads
  and discards whatever the client is still sending (≤ 4 MiB, ≤ 2 s) and only then closes the socket. Otherwise
  Windows resets the connection (RST) and the client loses the `PROTOCOL` response; that happened with a frame
  body over 1 MiB.
- Discovery: the endpoint writes `work\run\endpoints\<role>.json` (without the token), the launcher writes
  `work\run\sessions\<role>.json` (with the token). The files record the image path (`exe`) and the process
  start time (`pid_created`). Windows reuses pids, so the client checks not only "the process is alive" but also
  that it is the same process (`satk.saap.client.verify_pid`); files that point to another process are stale.
- The mock is a deterministic synthetic world near Grove Street: every capability of the protocol, identical
  requests give byte-identical PNGs, streaming is simulated (without a settle the frame has holes).
- `proto/cpp/saap_frame.hpp` (MIT, C++14, header-only): the frame, the limits, a constant-time token compare, an
  atomic descriptor write. Native endpoints vendor it: the Ariane fork
  (`viewer\ariane\tools\euryopa\saap\`) and later the MTA fork. <!-- linkcheck: ignore -->
- The native Ariane endpoint passes every applicable case: `satk view conformance --target ariane` →
  43 pass, 0 fail, 5 skip (Ariane has no `log`, `console`, `lua` or `mem.read`). It writes
  `work\run\endpoints\ariane.json` itself, listens on an ephemeral port and accepts a `path_prefix` only inside
  `work\` (`SATK_AGENT_OUT_ROOT`).

## Limitations and known issues

- There is one protocol version (`saap: 1`); within it fields are only added, and unknown fields are ignored.
- The mock's `lua.exec` and `mem.read` are toys (arithmetic and `print`; pseudo-memory with `MZ` at 0x400000).
- Solutions to common problems: [troubleshooting.md](../ru/troubleshooting.md) (in Russian).

## Python API (if other packages use it)

```python
from satk.saap.client import connect
with connect("mock") as c:
    pose = c.call("camera.get")["pose"]
```
