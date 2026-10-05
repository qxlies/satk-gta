# Bug report: a private, local report for satk problems

[Русская версия](../ru/bugreport.md)

Package: `satk.bugreport`. Command: `satk bug-report`.

## What it is

`satk bug-report` collects what a maintainer needs to understand a problem with satk and writes it to one
Markdown file under `work/out/bugreport/`. It never sends anything: you read the file and attach it to an
issue or a message yourself. Paths, your user and computer names, e-mail addresses and tokens are replaced
before you see the text, and no game file is included.

## Quick example

```powershell
satk bug-report --logs 1
satk bug-report --what "index build stops at 40%" --yes
```

The first command only shows the report (in a terminal it asks before writing, `y` writes it); the second
writes it at once and prints where:

```json
{"ok":true,"written":true,"path":"<workspace>/work/out/bugreport/satk-bug-report-20261005-120000.md",
 "sections":[["What happened",1],["Environment",6],["Network",1],["satk doctor",28],["Configuration",12],
 ["Game",2],["Operations",1],["Recent errors",8]],"redacted":{"game":5,"satk":2,"user":3,"workspace":21}}
```

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk bug-report [--what TEXT] [--logs N] [--out FILE] [--yes]` | `satk_op` | collect, redact, show; write after a yes or with `--yes` |

- `--what`: what happened, in your words; it goes to the top of the report (redacted like the rest).
- `--logs N`: how many recent entries of `work/logs/errors.log` to include (default 3, `0` for none).
- `--out FILE`: another file name; folders satk must not write to (the game) are refused (`PROTECTED_PATH`).
- `--yes`: write without asking. Without it and outside a terminal (an AI agent, a script) nothing is
  written: the result holds the full `text` so it can be shown first, and `next` says how to write it.

## What goes in

| Section | Content |
|---|---|
| What happened | your `--what` text |
| Environment | satk and Python versions, Windows version, encodings, where satk runs from |
| Network | the policy line: `network: none by default` |
| satk doctor | every check with status, message and fix |
| Configuration | workspace, paths and where each comes from, profiles, `SATK_*` variables |
| Game | edition, `gta_sa.exe` variant, size and SHA-256, missing files |
| Operations | how many commands loaded, packages that failed to import (with the error) |
| Recent errors | the last `--logs` entries of `errors.log`, at most 40 lines each |

Never included: game files, their names or listings, images, the index, notes, viewer endpoint tokens and
environment variables other than `SATK_*`.

## How redaction works

| Found in the text | Replaced with |
|---|---|
| game folders (`game_root`, `installed`, the clean copy) | `<game>` |
| the workspace | `<workspace>` |
| the satk folder | `<satk>` |
| your profile folder and any other user's profile folder on any drive | `%USERPROFILE%` |
| your user name and computer name as words | `<user>`, `<host>` |
| e-mail addresses | `<email>` |
| values of `*TOKEN*`, `*SECRET*`, `*PASSWORD*`, `*API_KEY*` variables, `token=...`, `Bearer ...`, `sk-...`, `ghp_...` and similar | `<redacted>` |

Paths match in every spelling (`\`, `/`, doubled `\\` as in JSON, any letter case). A user name that is an
ordinary word (`User`, `Admin`) is replaced only inside paths. The result shows how many replacements of
each kind were made (`redacted`). Read the report before you share it: redaction catches the usual places,
not every possible one.

## Privacy of satk in general

- satk opens no internet connections and sends no telemetry. Sockets are used only on 127.0.0.1 for the
  viewer, the game and SAAP. Downloads happen only in commands you run on purpose: `satk engine setup`
  (pinned MTA build dependencies), `satk dev release` (pinned files from python.org and PyPI) and
  `scripts\bootstrap.ps1 -Deps` / `-Dev` (pip).
- `satk doctor` shows this as the `network` check; `satk doctor --deep` also scans the satk sources and
  fails if any file imports a network module without being on the allowlist (`satk.runtime.network`).
