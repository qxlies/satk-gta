# Crashes: report, known solution and culprit

[Русская версия](../ru/crash.md)

Package: `satk.crash`.

## What it is

`satk crash analyze` reads a dump (`.dmp` from MTA, WER, ProcDump or cdb), a text MTA crash log (`core.log`,
`server_pending_upload.log`, cdb output) or a single-player log with a crash report (`modloader.log`, SA-MP and
mod_sa logs, pasted text). It produces a report of at most 30 lines:

- the exception, the accessed address, registers and the stack; `gta_sa.exe` addresses are symbolized with sa-re
  ([re.md](re.md)): function, `file:line` in gta-reversed, MTA patches;
- `known` and `solution`: the matching entry of the CrashInfo crash list (crash addresses, SCRLog commands,
  scripts, modules) with a ready solution;
- `suspects` and `culprit`: model IDs from the registers and from `scrlog.log` → who defines them: a mod in the
  `modloader` folder, a layer of the index or nobody (the model was removed);
- `logs`: a digest of `modloader.log`, `scrlog.log` and the CLEO log from the game folder.

Everything is only read. Only `crash sample` writes, into `work\out\crash\sample\` and `work\out\crash\sample-sp\`.

## Quick example

```powershell
satk crash sample
satk crash analyze --last
satk crash info --last --part sections
satk crash sample --kind sp
satk crash analyze --last
satk crash known 0x00456809
satk crash known 038B
```

What comes back for the synthetic single-player crash (abridged, `--table`, the sa-re DB is built):

```text
#  addr      at                   fn                                 src                     mta           via
0  0x456809  gta_sa.exe+0x56809   CPickup::GiveUsAPickUpObject+0x29  game_sa/Pickup.cpp:17                 ip
1  0x458e69  gta_sa.exe+0x58e69   CPickups::Update+0x89              game_sa/Pickups.cpp:57                scan
2  0x53c0b2  gta_sa.exe+0x13c0b2  CGame::Process+0x1d2               game_sa/Game.cpp:78                   ebp
3  0x53e986  gta_sa.exe+0x13e986  Game::Idle+0x66                    app/app_game.cpp:45     HOOKPOS_Idle  ebp
crash: 0xC0000005 ACCESS_VIOLATION reading 0x0000002c at gta_sa.exe+0x56809 (thread 2412)
known: #2 0x00456809 (ip): Creation of a pickup using a model that no longer exists.
solution: A mod created a pickup using a custom model; ... [CrashInfo list 2026-10-02; satk crash known 0x00456809]
culprit: model 20101 is missing: only the disabled mod CoolPickups defines it
suspects: EAX=0x4e85 -> model 20101 'cp_trophy': defined only by disabled mod CoolPickups (CoolPickups/coolpickups.ide:3)
logs: modloader 0.3.7: 4 mods in the log; scrlog: script cpick, last [0213] CREATE_PICKUP 20101 ...; cleo: 1 scripts, errors 1
```

For your own game and a mod bisection (use your own paths):

<!-- linkcheck: off -->
<!-- docs-smoke: skip game folder paths differ on every PC -->
```powershell
satk crash analyze "C:\Program Files (x86)\Rockstar Games\GTA San Andreas\modloader\modloader.log"
satk crash analyze crash.dmp --game "C:\Program Files (x86)\Rockstar Games\GTA San Andreas"
satk crash logs --game "C:\Program Files (x86)\Rockstar Games\GTA San Andreas"
satk crash bisect "C:\Program Files (x86)\Rockstar Games\GTA San Andreas\modloader"
satk crash bisect "C:\Program Files (x86)\Rockstar Games\GTA San Andreas\modloader" --results ok,crash
```
<!-- linkcheck: on -->

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk crash analyze PATH` · `--last` `[--limit 10] [--thread TID] [--block 0] [--images DIR…] [--dir DIR…] [--scan-kb 64] [--game DIR] [--profile P]` | — (via `satk_op`) | report: frames and a summary (`crash`, `fn`, `src`, `patched`, `known`, `solution`, `culprit`, `suspects`, `regs`, `logs`, `dump`/`log`, `mta`, `pools_full`, `hint`) |
| `satk crash known [QUERY] [--kind auto\|addr\|command\|script\|module\|text]` | — | the CrashInfo list: by address (`0x00456809`, `gta_sa.exe+0x56809`), SCRLog opcode (`038B`), `script:<name>`, module (`CLEO.asi`) or words; a single hit is shown in full; without a query, list statistics |
| `satk crash logs [FILE…] [--game DIR] [--kind modloader\|scrlog\|cleo]` | — | digest of the game logs: modloader version, mods, errors, crash reports; the last SCRLog script and command, requested models; CLEO scripts and errors |
| `satk crash bisect [DIR] [--results crash,ok,…]` | — | bisection of modloader mods: the `IgnoreMods` section for the next run; writes nothing into the game |
| `satk crash info PATH\|--last [--part summary\|modules\|threads\|streams\|memory\|sections\|section\|stack\|text\|blocks] [--section TAG[:N]]` | — | parts of a dump: modules with PDB GUID/age, threads, data streams, memory, MTA sections, the raw stack |
| `satk crash list [--dir DIR…]` | — | dumps and logs, newest first; the `modloader.log` of the configured game only if it holds a crash report |
| `satk crash sample [--kind mta\|sp]` | — | a synthetic crash: MTA (dump and `core.log`) or single-player (dump, `modloader`, `scrlog.log`, `cleo.log`) |

The `via` column: `ip` = instruction pointer; `ebp` = frame chain; `scan` = a stack value preceded by a CALL; `log` =
a stack line from a log; `?` = the code bytes are unknown (warning `UNVERIFIED`). The `mta` column names an MTA
patch at that address (for a return address: on the CALL right before it).

`--last` takes the newest crash from the folders of `crash list`: `work\dumps`, `Bin\MTA\dumps\private` and <!-- linkcheck: ignore -->
`Bin\server\dumps\private` of the MTA fork, installed MTA builds (from the registry), `modloader\modloader.log` <!-- linkcheck: ignore -->
of the configured game. If a log is newer than a dump but was written together with it (MTA writes both), the dump
is taken. The `crash sample` files are used only when there is nothing else.

## How it works

- **The CrashInfo list** is `data\crashlist\gta-sa-10us-en.txt`: the English list for GTA SA 1.0 US from
  JuniorDjjr/CrashInfo (MIT), byte for byte; revision and sha256 are in `data\crashlist\source.json`, the license
  in `data\notices\crashinfo.txt`. It has 358 entries: 310 by crash address, 11 by command, 12 by script, 22 by
  module, 3 other. The text is free-form and parsed by `crashlist.py`: `Error:` entries (crash addresses; addresses
  after the word "Backtrace" are expected on the stack), variants `Problem 2:`/`Solution 2:`, the sections
  "By commands" (`Last command: [038B]`), "By scripts" (`script nebo` → `Mod:`) and modules (`CLEO.asi`). A match
  on the instruction pointer is the strongest. A variant of an entry is preferred when its address from the text
  ("0x005279B6 on the second Backtrace line") is on the stack. A return address equal to the entry address does not
  count as a match (`0x0053E986` is on the stack of every frame of the main loop).
- **Single-player reports** (`sptext.py`): `Exception At Address:` (SA-MP), `Exception at address:` (mod_sa),
  `Exception Address:`/`Exception Code:`/`Module:` (handlers of ASI loaders, `modloader.log`),
  `Unhandled exception at 0x… in …` (Visual Studio, WER). Registers `EAX: 0x…` or `EAX=…`, the stack after a
  `Backtrace`/`Stack trace`/`Call stack` line. In a file with several reports the first one is taken: the next ones
  are consequences of the first (`--block N` picks another).
- **The culprit** (`culprit.py`, `modfolder.py`). Candidates are model IDs:
  - the register named by the CrashInfo entry (`EAX` for `0x00456809`);
  - `REQUEST_MODEL`/`CREATE_*` at the end of `scrlog.log`;
  - any other register, only as a suspect and only if a mod defines that ID.

  An ID is checked against the `*.ide` files of the mods in the `modloader` folder (taking into account
  `IgnoreMods`, priority 0, `ExcludeAllMods` and `IncludeMods`, `ExclusiveMods` of other profiles from
  `modloader.ini`), against the `*.dff`/`*.txd` names of the mods and against the index of the profile whose root
  is the game folder (the layer of the definition). The game folder is `--game`, the folder the file lies in, or the
  folder of `gta_sa.exe` named in the dump (not MTA).
- **Game logs** (`gamelogs.py`): `modloader\modloader.log` (banner `Mod Loader 0.3.7`, `Game version`, profile,
  mod file paths, errors); `scrlog.log` (`script <name>` and lines `[038B] COMMAND args`); `cleo.log` (CLEO 4.4/5,
  dated lines: plugins, scripts, errors with opcodes). Only the tails of large logs are read (2 MiB for
  `scrlog.log`).
- **Bisection** (`bisect.py`): step 1 disables all loadable mods; `crash` means the cause is outside modloader. Then
  half of the remaining candidates (by name) is disabled and the rest is loaded. The plan is recomputed from
  `--results`, so no state is stored anywhere. The section `[Profiles.<profile>.IgnoreMods]` keeps the earlier
  entries; `undo` restores the original. The section names were checked against the `modloader.ini` shipped with
  Mod Loader 0.3.7.
- **Stack** (`stack.py`): `gta_sa.exe` has no PDB and no frame pointers, so the frames are a heuristic: IP, the EBP
  chain and a scan of up to 64 KiB of stack. A value counts as a return address when it lies in an executable
  section of a module and is preceded by a CALL (`E8`, `FF /2`). Code bytes come from the dump memory, otherwise
  from the module file of the same build (`--images`).
- **MTA formats** (`mta.py`): user streams `0x10000`-`0x10003` and the trailing sections after the dump (`POL`,
  `LOG`, `REP`, `MSC`, `MEM`, `CAS`, `D3D`, `DXI`, `CAT`); `core.log` has several blocks, the last one by default.

## Limitations and known issues

- CrashInfo entries are community experience, not verified facts; the links of the MixMods page ("click here") did
  not survive in the text file. The list is for GTA SA 1.0 US only, in English.
- The crash report formats of `modloader.log`, `scrlog.log` and the CLEO log are parsed by their common features;
  only `mod_sa.log` and the start lines of `modloader.log` and `cleo.log` were checked against real files. If a
  report is not recognized, `crash analyze` looks for addresses like `gta_sa.exe+0x…` in the text.
- Attribution by the mods' `*.ide` does not take modloader merging into account (which of two mods wins); that is
  the job of `satk mod`. The index does not load mods from `modloader` yet, so a `modloader:<mod>` layer of a
  definition does not occur so far; mod IDs are found by reading their `*.ide`.
- Bisection looks for one culprit; if a pair of mods causes the crash, the last step points at the wrong mod (the
  `note` reminds about this). Nested mod folders with their own `modloader.ini` are split as one mod.
- The stack scan also finds "old" return addresses of calls that already finished. MTA modules without their own
  PDBs get only `module+off`. 64-bit server dumps are parsed, but there is no RBP chain.

## Python API

```python
from satk.crash.analyze import analyze_file
from satk.crash import crashlist

env = analyze_file(path, game=game_dir)     # the 'satk crash analyze' envelope; game_dir = game folder or None
matches = crashlist.match(ip=0x456809)      # CrashInfo entries, best first
```
