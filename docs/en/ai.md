# Connect an AI assistant

[Русская версия](../ru/ai.md)

Package: `satk.mcp` (`satk mcp config --client`, `satk agent install-skill`). The server itself: [mcp.md](mcp.md).

## What it is

satk works without any AI. If you use an AI assistant, satk becomes its toolbox: the assistant searches your
game's index, shows texture sheets and map frames, reads crash dumps, lints your mod and builds texture mods,
all through the same operations as the `satk` command. The link is MCP (Model Context Protocol): the client
starts `satk` as a local process and talks to it over stdin/stdout.

One command registers satk in a client, one more installs the satk skill (how to use the tools well).

## Quick example

```powershell
satk mcp config --client cursor
satk mcp config --client lmstudio --groups core,index,media
```

The first command prints, without writing anything: the config file of the client, the entry for satk, an install
link and whether the file already has it (`status`: `ok`, `missing`, `differs`, `bom`). Add `--write` to put the
entry into the file:

<!-- docs-smoke: skip it writes the user's Cursor config and skill folders -->
```powershell
satk mcp config --client cursor --write
satk agent install-skill
```

## What goes to the cloud

- satk itself sends nothing anywhere: no telemetry, no network. The MCP server only answers the client that
  started it, on this computer.
- A **cloud** assistant (Claude, ChatGPT/Codex, Cursor, Copilot, Gemini, ...) sends what satk answers to its
  provider as part of the conversation: names, IDs, coordinates, file paths, crash stacks, and images when the
  assistant opens them. That is how the client works, not satk; check your provider's data policy.
- **Fully local:** LM Studio (or another local client) with a local model. Nothing leaves your computer.
- Every entry satk writes sets `OTEL_SDK_DISABLED=true`: even if an OpenTelemetry SDK is installed on the
  machine, the satk server exports no traces.

## Clients

| `--client` | Client | User scope (all folders) | Project scope (one folder) | Own command |
|---|---|---|---|---|
| `claude-code` | Claude Code | `~/.claude.json` via `claude mcp add` | `<project>/.mcp.json` | `claude mcp add` |
| `codex` | Codex CLI, IDE extension and app (one config) | `~/.codex/config.toml` (`CODEX_HOME`) | `<project>/.codex/config.toml` (trusted projects) | `codex mcp add` |
| `cursor` | Cursor | `~/.cursor/mcp.json` | `<project>/.cursor/mcp.json` | install link |
| `windsurf` | Windsurf | `~/.codeium/windsurf/mcp_config.json` | - | - |
| `vscode` | VS Code, Copilot agent mode | `mcp.json` in the VS Code user folder | `<project>/.vscode/mcp.json` | install link |
| `claude-desktop` | Claude Desktop | `claude_desktop_config.json` in its app-data folder | - | - |
| `gemini` | Gemini CLI | `~/.gemini/settings.json` | `<project>/.gemini/settings.json` | `gemini mcp add` |
| `lmstudio` | LM Studio (local models) | `~/.lmstudio/mcp.json` | - | - |
| `continue` | Continue | - | `<project>/.continue/mcpServers/satk.yaml` | - |
| `generic` | any other MCP client | `--path FILE` | `--path FILE` | - |

- `--scope user` (the default where it exists) makes satk available in every folder you open; `--scope project
  --project <folder>` only in that folder (for example your mod folder). Without `--project` the project is the
  satk workspace.
- `--print-command` prints only the client's own registration command (Claude Code, Codex, Gemini CLI), to run it
  yourself. `--raw` prints only the fragment in the client's format (JSON, TOML for Codex, YAML for Continue).
- VS Code keeps servers under `servers` (not `mcpServers`) and wants `"type": "stdio"`: satk writes both.
- The Microsoft Store build of Claude Desktop keeps its config in a package folder; satk finds it.

## How satk edits a config file

- Other servers and settings in the file stay as they are; for Codex the TOML file is parsed again after the
  edit and nothing else may change.
- The previous file is kept next to it as `<file>.satk-backup`; the write is atomic and without a byte-order mark.
- A file that does not parse (for example JSON with comments) is never rewritten: satk answers `BAD_PARAMS` and
  gives the fragment to paste by hand.
- The answer shows a `diff` of the satk entry only, never of other servers (their keys stay out of the output).
- `--check` only compares and fails when the entry is missing or different; run it after updating satk.
- Claude Code's user config `~/.claude.json` is a big state file that the running client rewrites: satk does not
  edit it, `--write` runs `claude mcp add` (and `claude mcp remove` first when the entry differs).

## Which tools the assistant gets

The server has 25 tools of its own plus `satk_ops` / `satk_op`, which reach every other operation. The list of
tools costs context (up to ~16 KB), so satk registers only the groups this workspace can serve
(`SATK_MCP_GROUPS`):

- `--groups auto` (default): `core,index,media`, plus `view` when the viewer is installed, `re` when the symbol
  database is built, `blender` when Blender is found, `engine` when the MTA fork is there. Re-run the command after
  installing one of them.
- `--groups all`: every tool. `--groups core,index`: an explicit list (`core index media view re blender engine`).
- An unknown group name stops the server with an error (`satk doctor`, check `mcp_groups`, names it).

## The skill

`satk agent install-skill` copies the satk skill (`SKILL.md`: IDs, tools, standard workflows, error codes) to
Claude Code (`~/.claude/skills/satk/`) and Codex (`~/.agents/skills/satk/`). `--client claude|codex|all`,
`--scope project --project <folder>` for one folder, `--dest <dir>` for any other client that reads skills,
`--check` to see whether the copies are current. Clients without skills: point their instructions file (for example
`AGENTS.md`) at the installed `SKILL.md`.

## Claude Code plugin

The satk repository is also a Claude Code plugin marketplace (`.claude-plugin/marketplace.json`). The plugin
brings the skill and an MCP server entry that runs `satk mcp`, so the `satk` command must be on PATH (a pip/pipx
install, or the folder of the portable package):

```powershell
claude plugin marketplace add qxlies/satk-gta    # or the path to your satk checkout
claude plugin install satk@satk
```

## Fully local with LM Studio

1. Install LM Studio and download a model that supports tool use.
2. `satk mcp config --client lmstudio --groups core,index,media --write` (a small tool list suits small contexts).
3. In LM Studio open the chat, enable the `satk` server in the integrations panel, ask:
   "Find the texture ws_rooftarmac1 and show a sheet".

## Troubleshooting

| Symptom | Fix |
|---|---|
| The client shows no satk tools | restart the client (most read the config at start); `satk mcp selftest` checks the server itself |
| `status: bom` | the file starts with a byte-order mark (PowerShell 5.1 writes one): `--write` repairs it |
| `BAD_PARAMS ... not valid JSON` | the config has comments or a syntax error: fix it or paste the fragment from the error by hand |
| `differs` after updating satk | `satk mcp config --client <client> --write` writes the new entry (backup first) |
| `NOT_FOUND 'claude' is not on PATH` | install Claude Code, or run the printed command where `claude` works |

## Commands

| Command | What it does |
|---|---|
| `satk mcp config --client <client> [--scope user\|project] [--project DIR]` | show the entry, the file and its status |
| `... --write` / `--check` / `--raw` / `--print-command` | write it / compare / only the fragment / only the client's command |
| `... --groups auto\|all\|<list>` · `--name <name>` · `--path FILE` | tool groups, server name, another config file |
| `satk mcp config [--write\|--check\|--raw]` (no `--client`) | the satk workspace's own `.mcp.json` for Claude Code (all tools) |
| `satk agent install-skill [--client claude\|codex\|all] [--scope ...] [--dest DIR] [--check]` | install or check the skill |
| `satk mcp selftest` | start the server and run a real MCP session (tool list, size budget, clean output) |
