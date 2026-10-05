# MCP server, help, diagnostics and notes

[Русская версия](../ru/mcp.md)

Packages: `satk.mcp` (the server, `satk_ops`/`satk_op`), `satk.runtime` (status, doctor, help), `satk.notes`.

## What it is

One MCP server, `satk` (stdio), gives an agent the same operations as the CLI: every `@op` with an MCP name
becomes a tool with the same parameter schema and the same JSON answer. The tool set is built from the registry
when the server starts, so packages merged later show up on their own. Operations without a tool of their own
(most of them: `formats`, `game`, `index build`, the newer packages) are found with the tool `satk_ops(query)` and
run with the tool `satk_op(op, args)`, with the same argument checks and the same execution as a tool of their own.
Next to the server: `satk help` (help for people and agents), `satk status` (overview: what is built and what is
running), `satk doctor` (environment checks) and notes in `work\notes.sqlite`, which survive between sessions.

Connecting other AI clients (Codex, Cursor, VS Code, LM Studio and others) is on the [ai.md](ai.md) page.

## Quick example

```powershell
satk status --table
satk doctor --only python,utf8,deps,disk --table
satk help ids
satk note list --table
satk mcp config
satk mcp ops dump --table
satk mcp op "index status" --table
```

What comes back (shortened, `satk status`):

```json
{"ok":true,"satk":{"version":"0.1.0"},"game":{"root":"<workspace>/gta-sa-clean","ok":true},"warn":["INDEX_MISSING: profile vanilla not built; fix: satk index build"]}
```

`satk status` always has `warn` when something stands in the way of the tools: when a package section gave no
warnings itself, the warnings for the game copy and the index are derived from its fields (`GAME_MISSING`,
`GAME_BAD`, `INDEX_MISSING`, `INDEX_STALE`).

## Connecting Claude Code to the workspace

Other AI clients, and Claude Code for every folder, are registered with `satk mcp config --client <client>`; the
skill is installed with `satk agent install-skill`; both are described in [ai.md](ai.md). This section is about the
satk workspace's own project.

1. The file `<workspace>\.mcp.json` registers the server for the project. `satk mcp config --write` writes it,
   `satk mcp config --check` compares it, `satk mcp config --raw` prints only the JSON fragment to paste. The server
   runs as `<workspace>\tools\.venv\Scripts\python.exe -X utf8 -m satk.mcp` with `PYTHONPATH=<workspace>\tools\src`
   and `SATK_HOME=<workspace>`. `--write` keeps the other servers and keys of the file and writes UTF-8 without a
   BOM (Claude Code's JSON parser rejects the BOM that PowerShell 5.1 `Out-File -Encoding utf8` adds; `--check` and
   `satk doctor` report it). A file with broken JSON is never rewritten by `--write`: fix it first.
2. The first time a session starts in the workspace, Claude Code asks whether to trust the project server `satk`.
   `/mcp` shows its state.
3. A check without Claude Code: `satk mcp selftest` starts the server as a subprocess and runs a real stdio session
   (initialize, tools/list, `satk_help`, `satk_status`, `satk_ops`, `satk_op`), measures the tools/list answer
   (16 KB budget, plus bytes per group) and checks that stdout carries nothing but JSON-RPC frames.
4. A narrow tool set saves context: the variable `SATK_MCP_GROUPS=core,index,view` (groups:
   `core index media view re blender engine`, `all` for everything). `core` = `satk_status`, `satk_help`,
   `satk_ops`, `satk_op`, `note`. The groups limit `satk_op` too: an operation from a group outside the list answers
   `UNSUPPORTED`. An unknown group name (a typo such as `cor`) is a `BAD_PARAMS` error: the server does not start
   (the message goes to stderr and `work\logs\mcp.log`), and `satk mcp selftest` and `satk doctor` (check
   `mcp_groups`) suggest the right name.

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk status [--deep]` | `satk_status` | overview: game copy, index, viewer, symdb, Blender, engine, notes, kb, media, warnings |
| `satk help [topic] [--ru]` | `satk_help` | without a topic on the command line: the one-screen "I want to..." guide; `all` (or `--all`): every command; topics `start ids tools ops workflows schema errors viewer re blender engine golden notes` or a command name; `--ru`: in Russian |
| `satk mcp ops [query] [--limit N]` | `satk_ops` | find any operation: table `op / args / summary`, with one hit also the full JSON schema |
| `satk mcp op OP [--args JSON]` | `satk_op` | run an operation by name (`formats.dump`, `"index status"`, a tool name) with argument checks |
| `satk doctor [--deep] [--only a,b]` | - | checks `python venv deps utf8 paths disk network git blender msbuild premake mcp_config mcp_groups index ops_import` plus the checks of every package (`game_copy`, `kb`, `viewer`, ...) |
| `satk note add SID "text" [--tags ...]` | `note` (action=add) | a note on any SID |
| `satk note list [SID] [--query ...] [--tag T] [--author A]` | `note` (action=list) | notes of a SID or a full-text search (Cyrillic too, word prefixes) |
| `satk note rm note:N` · `satk note export` · `satk note import` | - | delete; export to `data/notes/notes.jsonl` of the checkout and back |
| `satk mcp` · `satk mcp selftest` · `satk mcp config [--write\|--check\|--raw]` | - | the server, the self-test, registration in `.mcp.json` |
| `satk mcp config --client <client> [--write\|--check\|--raw\|--print-command]` | - | registration in any AI client ([ai.md](ai.md)) |
| `satk agent install-skill [--client claude\|codex\|all] [--dest DIR] [--check]` | - | the satk skill for Claude Code and Codex |
| `satk dev gen-docs [--check]` | - | generates `docs/agent/tools.md` and `docs/agent/schema.md` |

## Any operation: `satk_ops` and `satk_op`

Few operations have a tool of their own: the tools/list budget is 16 KB, and new package operations are
registered with `mcp=False`. Two generic tools give an agent all the others without growing the list:

1. `satk_ops(query, limit=20)` searches names, CLI words, summaries (Russian ones too), parameters, docs and
   examples; every word of the query must match (otherwise the closest ones come back with the warning
   `NO_FULL_MATCH`). Rows are `op | args | summary`; `args` is a compact signature: `name:type` is required,
   `name?:type` optional, `name:type=default`; types `str int num bool {} [str]`, enumerations `a|b`. With one hit
   (or when the query is the exact name of an operation) the answer adds `schema` (the full JSON schema with
   parameter descriptions) and `examples`. Matching operations an agent may not run are listed in `cli_only` with
   the reason.
2. `satk_op(op, args)`: `op` is a dotted name (`formats.dump`), CLI words (`formats dump`) or a tool name; `args` is
   a JSON object of parameters. The server resolves the name, checks the policy and the arguments (`OpSpec.bind`,
   like the CLI and a tool of its own: `BAD_PARAMS` with `did_you_mean` before anything runs) and executes the
   operation the way its own tool would: an ordinary one in a worker thread under its group lock (120 s), a
   `long_running` one in the worker process with progress and a process tree (600 s; on timeout the whole tree
   dies). The answer is the operation's own envelope; `inline=true` of image operations works here too.

Closed to agents (`satk help ops` shows the current list):

| Operation | Code | Why |
|---|---|---|
| `mcp`, `mcp.selftest`, `mcp.config`, `mcp.ops`, `mcp.op` and any future `mcp.*` | `UNSUPPORTED` | management of the server itself |
| `dev.gate`, `dev.gen_docs`, `dev.sync_agent_docs`, `dev.release` | `UNSUPPORTED` | developer commands (minutes of tests, rewriting docs, building a release) |
| `view.mock` | `UNSUPPORTED` | serves until Ctrl+C and would block the call |
| `init`, `game.clone`, `game.protect`, `game.unprotect`, `engine.setup`, `note.rm`, `describe.clear`, `agent.install_skill` | `CONSENT_REQUIRED` | they change the user's setup or the game copy, download dependencies, delete data or write into the user's AI client folders: ask the user |

A package closes its own operation with a decorator above `@op`:

```python
from satk.mcp.generic import cli_only

@cli_only("rewrites the user's config", consent=True)   # consent=True -> CONSENT_REQUIRED
@op("x.reset", summary="...", summary_ru="...", mcp=False)
def reset() -> dict: ...
```

The CLI twins `satk mcp ops` and `satk mcp op` do the same in this process (long operations run here too, without
a worker process) and respect `SATK_MCP_GROUPS` when it is set.

## How it works

- A tool answer is one text block with the compact JSON envelope (like `satk ... --json`); an error is the same
  envelope with `isError`. Images are embedded only with `inline=true` (at most 1024 px).
- Ordinary operations run in worker threads (one per group at a time, timeout 120 s, `SATK_MCP_TIMEOUT`);
  `long_running` operations (`blender_job`, `engine`, `index build`, ...) run in a separate process with progress
  notifications (timeout 600 s, `SATK_MCP_LONG_TIMEOUT`). That process and everything it starts (Blender, MSBuild)
  live in one Windows Job Object: on timeout, on a cancelled call or when the server itself crashes, the whole tree
  ends, not just the first process (`satk.mcp.proctree`). On a normal finish whatever the operation left running on
  purpose is not touched.
- In the CLI a list flag before the positionals does not swallow them: `satk asset get --fields name model:411` and
  `satk note add --tags a b model:411 "text"` work (missing positionals are taken from the end of the list). Safest:
  positionals first, or list values separated by commas.
- Logs go only to stderr and `work\logs\mcp.log`; stdout belongs to JSON-RPC. A `satk_op` call is logged as
  `call satk_op -> index.status ok 12 ms`.
- An operation that ends the process like a CLI command (`SystemExit`) does not bring the server down: the call
  gets `INTERNAL` with a hint to run it in a terminal.
- Notes: table `note` + FTS5 `unicode61` (a word prefix finds the whole word, Cyrillic included); an identical
  note (SID, author, text) is stored once. Bookmarks `bm:` and captures `cap:` live in the same database. An index build does not touch the
  notes database. The default author is `claude` (or `SATK_AUTHOR`); people pass `--author human:<name>`.

## Limitations and known issues

- A cold server start takes 1-3 s (importing the MCP SDK; `satk mcp selftest` measured 1.1 s); up to 10 s on a
  heavily loaded machine.
- The tools/list budget (16 384 bytes) is shared by all 27 tools; now 15 727 bytes (`satk mcp selftest` shows the
  exact number and the bytes per group); `satk_ops` and `satk_op` together cost about 0.7 KB. The budget is split
  between the groups (`GROUP_BUDGET` in `src/satk/mcp/adapter.py`):

  | Group | Share, bytes | Now, bytes |
  |---|---|---|
  | `core` | 2140 | 2095 |
  | `index` | 3890 | 3789 |
  | `media` | 2740 | 2675 |
  | `view` | 4410 | 4186 |
  | `re` | 2210 | 2147 |
  | `blender` | 420 | 346 |
  | `engine` | 500 | 451 |

  `tests/mcp/test_adapter.py::test_each_group_within_its_share` fails on the branch of the package whose group grew,
  before the merge, so main never goes over 16 KB after several merges. Descriptions are short: summaries of about
  150 characters; obvious parameters (`limit`, `cursor`, `profile`, the `kind` filter) have no description; enum
  values are not repeated in the text. To give a group more, bytes are moved between shares (through the lead).
- The topic `satk help tools` fits in 6000 characters; with more tools it cuts the summaries to their first
  sentence. The topic `satk help ops` truncates its list when it overflows.
- `satk_ops` searches the text of the operations' descriptions: when a format (for example GXT) is not mentioned by
  any operation, the query returns an empty table with a hint, not a guess.
- If `satk.mcp` does not start: `satk doctor` (`deps`, `ops_import`), then the log `work\logs\mcp.log`. Everything
  else is in [troubleshooting.md](troubleshooting.md).

## Python API (if other packages use it)

```python
from satk.notes import db                  # notes, bookmarks, captures (viewer, index)
db.notes_for("model:411"); db.count_for("model:411")
db.bookmark_save("grove_center", {"pos": [2495, -1687, 30]})
db.capture_add("<workspace>/work/out/captures/x.png", "ariane", {"pos": [0, 0, 0]}, w=960, h=540)
from satk.notes.provider import BookmarkCaptureProvider   # SID provider bm/cap for satk.viewer
from satk.runtime.help import register_topic              # a help topic of your own for satk_help
from satk.mcp.generic import cli_only, denial, search, prepare   # policy and search of satk_op/satk_ops
```
