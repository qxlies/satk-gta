# <Component>: <what it does, briefly>

[Русская версия](../ru/_template.md)

<!-- Template of a component page docs/en/<name>.md; its Russian mirror starts from docs/ru/_template.md.
     docs-smoke does not run files whose name starts with "_". -->

> **How to fill it in (delete this block):**
>
> - write the page in English at `docs/en/<name>.md`; the Russian mirror `docs/ru/<name>.md` has the same sections,
>   code blocks and quick-example commands, in Russian (never mix languages inside one file). Commands,
>   parameters, paths and SIDs stay exactly as they are typed;
> - the line under the title is the language switch: copy it from this template and replace `_template` with the
>   page name; the mirror links back with `[English version](../en/<name>.md)` (`satk dev docs-parity` checks both);
> - "Quick example" holds real commands, without angle-bracket placeholders, pipes or redirection. `satk dev
>   docs-smoke` runs them; viewer examples use `--target mock`. A block that cannot run unattended gets the HTML
>   comment `docs-smoke: skip <reason>` on the line above it;
> - `satk dev linkcheck` checks absolute paths and links; an example path that must not exist gets the HTML
>   comment `linkcheck: ignore` at the end of its line. Write `<workspace>`, not the paths of your machine;
> - before committing: `satk dev linkcheck docs/en/<name>.md docs/ru/<name>.md`, `satk dev docs-smoke
>   docs/en/<name>.md` and `satk dev docs-parity`;
> - "Quick example" and "Commands" are required, and the page is listed in `docs/en/README.md` and
>   `docs/ru/README.md` (checked by `tests/e2e/test_docs_layout.py`); the other sections are recommended;
> - budget: one to three screens; details go to docstrings and `satk <command> -h`.

Package: `satk.<pkg>`.

## What it is

Two or three sentences: why a person and an AI assistant need the component, what it reads and what it writes
(always under `<workspace>\work\`).

## Quick example

```powershell
satk version
```

What comes back (shortened):

```json
{"ok":true,"satk":"0.1.0"}
```

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk <group> <command> [--flag]` | `tool_name` or — | one line |

## How it works

Briefly: data sources, caches, determinism, time and memory limits.

## Limitations and known issues

- …; fixes are in [troubleshooting.md](troubleshooting.md).

## Python API (if other packages use it)

```python
from satk.<pkg> import <api>
```
