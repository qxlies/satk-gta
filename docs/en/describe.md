# `satk describe`: model descriptions from gta-scout

[Русская версия](../ru/describe.md)

Package: `satk.describe`. Data: the gta-scout description pack (MIT, Dryxio), notice
[`data/notices/gta-scout.txt`](../../data/notices/gta-scout.txt).

## What it is

gta-scout publishes 7 815 text descriptions of SA models and textures (the pack `sa-YYYY-MM-DD.json` in its clone),
written and checked by AI agents from renders. `satk describe import` binds the model descriptions to the models of
a profile **by content only**: the SHA-256 of the DFF the model loads in this profile must equal the hash in the
pack. Bound descriptions go into the notes (`work\notes.sqlite`, author `import:gta-scout`, tag `desc`), and
`asset find ... --kind note` and `note list model:<id>` find them right away. The game and the pack are only read; a
repeated import changes nothing.

## Quick example

```powershell
satk describe status
satk describe import --dry-run
satk asset find bench --kind note
```

What comes back (shortened; `find` after a real `satk describe import`):

```json
{"ok":true,"pack":{"file":"<workspace>/src/gta-scout/data/annotations/sa-2026-10-03.json","version":"sa-2026-10-03","license":"MIT","entries":7815,"models_with_dff":2291,"digest":"ok","skipped":{"no_dff_source":425,"texture":5099}},"imported":2131,"models":2131}
{"ok":true,"profile":"vanilla","models_in_profile":14790,"dff_hashed":14483,"entries_bound":2131,"entries_unmatched":160,"bound":2131,"models":2131,"hash_only":0,"notes":2131,"added":0,"unchanged":2131,"replaced":0,"pruned":0,"kept_unbound":0,"dry_run":true,"sample":[["model:322","Elongated bronze cylinder with a rounded conical tip and black ringed base; pro…"]],"seconds":0.54}
{"ok":true,"cols":["id","kind","name","info"],"rows":[["note:1120","note","model:4085","Two simple backless benches with long beige-grey seats on two short legs."],["note:271","note","model:1364","Dark curved urban bench integrated between two round planters, vegetation above…"]],"n":15,"total":15,"next":null}
```

The import itself (writes into `work\notes.sqlite`, about 2 s; a repeat gives `added: 0, unchanged: 2131` in about
0.6 s):

<!-- docs-smoke: skip writes into the shared notes database -->
```powershell
satk describe import
satk note list model:4085
```

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk describe import [--profile P] [--pack FILE\|DIR] [--text en\|full] [--prune] [--dry-run] [--no-verify] [--sample N]` | - | hashes the profile's DFFs, binds the pack entries, syncs the notes; the answer is counters and a `sample` |
| `satk describe status [--pack FILE\|DIR]` | - | which pack was found (version, entries, checksum) and how many descriptions are already imported |
| `satk describe clear [--dry-run]` | - | remove every imported description (only author `import:gta-scout` with tag `desc`) |

Agents reach `import` and `status` through `satk_ops`/`satk_op`; `clear` answers them `CONSENT_REQUIRED` (it deletes
notes: ask the user). Descriptions are found and read with the existing commands: `satk asset find bench --kind note`
(MCP `asset_find(query, kind="note")`), `satk note list model:4085`, `satk note list --tag desc`.

Fields of the `import` answer:

- `models_in_profile`: active models of the profile with a DFF; `dff_hashed`: how many distinct DFFs were read
  (only those whose size occurs in the pack);
- `entries_bound` / `entries_unmatched`: pack entries with a DFF that did and did not find their DFF; `bound`: pairs
  "entry -> model", `models`: distinct models, `hash_only`: pairs where the publisher's model name differs (the same
  DFF under another name, for example in SA-MP models);
- `added` / `unchanged` / `replaced` / `pruned` / `kept_unbound`: what happened to the notes (see below).

## How it works

- **The pack.** By default the newest `sa-*.json` in `<workspace>\src\gta-scout\data\annotations` (`paths.src` from
  the configuration), otherwise `--pack`. The format `gta-scout-annotations-v1`, the license `MIT` and the pack
  checksum (canonical JSON -> SHA-256, as gta-scout computes it) are checked; a pack edited by hand needs
  `--no-verify`. Broken entries are skipped and counted in `skipped`.
- **Binding.** Entries with `kind: model` and a `.dff` source are used (2 291 in `sa-2026-10-03`). For every active
  model of the profile its whole DFF is read as an IMG directory entry (`blob.size` bytes from `blob.abs_off`, a
  multiple of 2048: exactly what gta-scout hashed), and its SHA-256 is compared with the pack. Names are ignored: a
  modified DFF does not bind, the same DFF under another name does. Texture entries (5 099) are not imported: the
  pack only has TXD hashes, and the publisher's TXDs differ from the stock 1.0 ones.
- **SID.** `model:<id>`; when the winning definition from a non-vanilla layer overrides another one (SA-MP skins on
  IDs 300+), `model:<name>`, so the note does not land on the vanilla model with the same ID: notes are shared by
  all profiles.
- **The note.** The text is the English part of `FR: ... EN: ...` (`--text full` keeps the whole original; `lang` is
  `en` or `mul`), tags `desc gta-scout` plus the publisher's tags, the publisher's `confidence`, `evidence` =
  `gta-scout:<version>#<entry id> <archive>/<file>.dff sha256:<hash>`, the date is the pack date (the export is
  deterministic). New notes are written in one transaction through `satk.notes.db.import_jsonl`.
- **Idempotency.** Only notes of author `import:gta-scout` with tag `desc` change. The same "model + text" pair with
  the same tags and confidence is `unchanged`; a new text or new tags for a bound model replace the old note
  (`replaced` + `added`); notes of models that are no longer bound stay (`kept_unbound`) unless `--prune` is given.
- **Speed.** Vanilla: 14 483 DFFs are hashed in about 0.5 s (from the OS cache), the first import takes about 2 s, a
  repeat about 0.6 s. The `samp` profile gives 2 250 bindings (126 of them SA-MP models).

## Limitations and known issues

- The descriptions are the publisher's observations from renders (AI agents, `confidence` 0.6-1), not checked
  facts; 136 of the 2 131 vanilla texts (about 6 %) are written as "FR / EN" inside the sentences, without an `EN:`
  marker, and are stored whole with `lang: mul`.
- A profile index (`satk index build`) and the gta-scout clone in `paths.src` are needed; without the clone `status`
  answers with a warning and `import` with `NOT_FOUND` and a hint.
- `replaced` and `clear` delete notes one by one (about 15-20 ms per note; `satk.notes` has no batch delete):
  switching `--text` on vanilla replaces 1 333 notes in about 30 s, `clear` takes about 35 s.
- `satk note export` exports the imported descriptions together with the other notes; they can be restored by a
  repeated import; the notice is `data/notices/gta-scout.txt`.

## Python API

```python
from satk.index.api import open_index
from satk.describe.pack import find_pack, load_pack
from satk.describe.bind import bind
from satk.describe.store import records, sync

pack = load_pack(find_pack(None))
b = bind(pack, open_index("vanilla"))       # b.stats, b.descs[i].sid / .entry / .use
res = sync(records(b.descs, pack), dry_run=True)
```
