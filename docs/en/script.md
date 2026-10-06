# `satk script`: CLEO and SCM scripts (disassembler, assembler, checks, templates)

[Русская версия](../ru/script.md)

Package: `satk.script`.

## What it is

Reads GTA San Andreas scripts and writes them back byte for byte: CLEO 4/5 custom scripts (`.cs`, `.cm`, `.s`,
`.cs4`, `.cs5`), the streamed scripts inside `script.img` and `main.scm` with all its headers (global
variables, objects, missions, streamed scripts). The text form is close to Sanny Builder's opcode syntax
(`0001: wait 0`, `:LABEL`, `@LABEL`), so a modder or an AI assistant can read a script, change a line, assemble
it again and check it before it reaches the game. `satk script new` writes small working CLEO scripts from
templates (a cheat code, a car spawner, a teleport, live text on screen, a mission loop).

Everything is written under `<workspace>\work\out\script\<name>\`; game files are only read. To install a
script, copy the `.cs` into the game's `cleo` folder yourself (CLEO 4 or 5 must be installed).

## Quick example

```powershell
satk script new spawn_car --name mycar --model 522 --cheat BIKE
satk script check mycar/mycar.txt
satk script disasm mycar/mycar.cs --limit 6
satk script asm mycar/mycar.disasm.txt --compare mycar/mycar.cs
satk script disasm script.img --entry trains --limit 3
```

What comes back (shortened):

```json
{"ok":true,"file":"<workspace>/work/out/script/mycar/mycar.cs","text":"<workspace>/work/out/script/mycar/mycar.txt","template":"spawn_car","bytes":164,"commands":19,"opdb":"kb (3696 commands)","install":"copy mycar.cs into the game's cleo folder (CLEO 4 or 5); satk never writes into the game"}
{"ok":true,"cols":["sev","at","code","msg"],"rows":[],"n":0,"total":0,"src":"mycar.txt","kind":"cleo","clean":true}
{"ok":true,"file":"<workspace>/work/out/script/mycar/mycar.disasm.txt","kind":"cleo","bytes":164,"commands":19,"exact":true,"head":["{$CLEO .cs}","03A4: script_name 'MYCAR'",":MYCAR_11","0001: wait 250","00D6: if 1","0256: is_player_playing $2"],"warn":["KEPT: mycar.txt exists and differs (your source?); wrote mycar.disasm.txt (--force overwrites)"]}
{"ok":true,"file":"<workspace>/work/out/script/mycar/mycar.cs","kind":"cleo","bytes":164,"same":true}
{"ok":true,"file":"<workspace>/work/out/script/trains/trains.txt","src":"script.img:trains.scm","kind":"external","bytes":790,"commands":131,"padding_trimmed":1258,"exact":true,"head":["{$EXTERNAL}","03A4: script_name 'TRAINS'","0005: set_var_float $9525 0.0"]}
```

`satk script disasm main.scm` writes the whole vanilla `main.scm` (360 418 commands, 135 missions) as text in
about 3 seconds; `satk script asm main/main.txt --compare main.scm` gives the same 3 079 599 bytes back.

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk script disasm FILE [--entry NAME] [--out PATH] [--annotate]` | — (`satk_op`) | binary -> text in `work\out\script\<name>\<name>.txt`; `exact` = assembling it gives the same bytes (checked for files up to 1 MB, `--verify` forces it); `head` = the first code lines |
| `satk script asm FILE [--out PATH] [--compare FILE]` | — | text -> `.cs`/`.scm`/`main.scm` next to it; errors carry line numbers and `did_you_mean`; `same`/`first_diff` with `--compare` |
| `satk script check FILE [--entry NAME] [--severity warn]` | — | table `sev/at/code/msg` for text or binary scripts; `clean`, `counts` |
| `satk script new TEMPLATE [--name X] [--cheat C] [--key K] [--model M] [--pos X Y Z] [--text T]` | — | a template as `.txt` plus the assembled `.cs`, checked |

Common options: `--opdb auto|kb|cleo-ai|core|<file.json>` picks the opcode database, `--kind
auto|cleo|external|main` overrides the file kind, `--profile` names the game whose `data\script\` and `cleo\`
folders resolve short names (`main.scm`, `script.img`). Relative names are also looked up in
`work\out\script\`. `disasm` never overwrites a different `<name>.txt` next to the binary (your source or a
template): it writes `<name>.disasm.txt` instead; `asm` of `<name>.disasm.txt` writes `<name>.cs`.

Templates: `hello` (a message, then one per key press), `cheat` (cheat code: money, health, armour),
`spawn_car` (cheat code spawns `--model`), `teleport` (cheat code moves the player to `--pos`), `text` (a key
toggles live text: by default the player's position and heading), `mission` (a cheat code starts it; reach the
marker at `--pos` within 2 minutes for $500).

## The text form

| Text | Binary | Notes |
|---|---|---|
| `0001: wait 0`, `wait 0` | command `0001` | the opcode or the name (looked up in the opcode db); a name after an opcode must match it |
| `8019: not ...`, `not ...` | bit 15 of the opcode | negated condition |
| `:LOOP`, `@LOOP` | jump offset | absolute in `main.scm`'s main code, negative from the start in CLEO, streamed scripts and missions |
| `5`, `-300`, `0x8A5A80` | int8/int16/int32 | the smallest type that holds the value; `5:i32` keeps another width |
| `1.0`, `-0.5`, `1.0e-08` | float32 | shortest text that reads back to the same bits; `0x7FC00000:f` for NaN |
| `$12` / `&13` | global variable | `$N` = byte offset N*4, `&N` = raw byte offset |
| `0@`, `0@s`, `0@v` | local number / 8-byte / 16-byte string variable | `32@` and `33@` are the timers |
| `s$4`, `v$5` | global 8-byte / 16-byte string variable | |
| `$3(0@,10i)`, `0@(1@,4f)`, `s$20($5,3s)` | arrays | base(index variable, size + element type `i f s v`) |
| `'TEXT'`, `v'TEXT'`, `"text"`, `r'TEXT'` | 8-byte, 16-byte, length-prefixed, untyped string | escapes `\\ \' \" \xHH`; `r'...'` is 128 bytes for `05B6` |
| `#INFO` | object number in `main.scm` | negative ids of the `DEFINE OBJECT` table; in CLEO scripts `#NAME` is looked up in the index |
| `true` / `false` | int8 1 / 0 | |
| `hex 90 90 00*12 end` | raw bytes | data the disassembler could not (or should not) read as commands |
| `{$CLEO .cs}` `{$EXTERNAL}` `{$MAIN}` `{$MISSION}` `{$USE SAMPFUNCS}` | | file kind (default CLEO `.cs`), mission start, preferred opcode extension |
| `DEFINE ...` | `main.scm` headers | `GLOBALS_SIZE`, `OBJECT(S)`, `MISSION(S)`, `SCRIPT name OFFSET n SIZE n`, ... |

Comments: `// ...`, `/* ... */` and `{ ... }` (so Sanny Builder's `{name}` hints are fine; `--annotate` adds
them).

## How it works

- **Opcode database.** `auto` takes the `opcode` table of the knowledge base (`satk kb build`: every SA command
  plus the CLEO 5, CLEO+, NewOpcodes, SAMPFUNCS and plugin extensions, with parameter types), else the same
  reference read straight from the cleo-ai clone, else a bundled subset of 358 common commands (the
  templates use only these). The parameter types drive the checks: integer vs float vs string, variables for
  outputs, labels, models, and the variable part of commands like `0AB1 cleo_call` (ended by a `0x00` byte).
- **Disassembly follows the control flow** from the code start, every mission and every label parameter, so
  data inside code (machine code blobs, Sanny Builder footers `__SBFTR`) is kept as `hex` instead of being read
  as nonsense commands. Unreached bytes that decode exactly are shown as commands. Every byte is represented,
  so the text always assembles back to the same file.
- **Checked on the vanilla game:** `main.scm` and all 79 `script.img` entries (with and without their sector
  padding) round-trip bit for bit, with no byte left as `hex`; real CLEO scripts with machine code and Sanny
  Builder footers do too.
- **Checks** (`satk script check`): `ASM` (assembler errors), `UNDECODED`, `JUMP_OUTSIDE`, `JUMP_MISALIGNED`,
  `JUMP_ABSOLUTE` (a positive offset in a CLEO script), `JUMP_ZERO` (a label on the first byte encodes as 0, which
  the game reads as the start of `main.scm`), `NO_TERMINATE` (a CLEO script must end with `0A93`, a jump or a
  return), `FALLS_INTO_DATA`, `LOOP_NO_WAIT` (a loop without `wait` that has no way out, or changes no
  variable), `IF_COUNT`, `NOT_CONDITION`, `SCRIPT_NAME` (more than 7 characters). The vanilla `main.scm` has no
  error and no `LOOP_NO_WAIT`.

## Limitations and known issues

- GTA San Andreas only (PC 1.0 format). GTA III/VC `main.scm` headers are refused with `UNSUPPORTED`.
- Low-level syntax only: no Sanny Builder high-level constructs (`if ... then`, `while`, `0@ = 5`, named
  variables like `$PLAYER_CHAR`, classes). Write global variables as `$N` (`$2` = `$PLAYER_CHAR`, `$3` =
  `$PLAYER_ACTOR`).
- With the bundled core subset, commands outside it are refused by `asm` and kept as `hex` by `disasm`; run
  `satk kb build` (needs the cleo-ai clone) for every command.
- `LOOP_NO_WAIT` is a heuristic: a loop that calls a routine (`gosub`, `cleo_call`) is assumed to wait there.
- Strings are bytes: non-ASCII characters are written as `\xHH`; `asm` accepts latin-1 text.

## Python API

```python
from satk.script.opdb import load_db
from satk.script.disasm import disassemble
from satk.script.asm import assemble
from satk.script.check import check_program

db = load_db("auto")
d = disassemble(data, "cleo", db)            # d.text, d.program
r = assemble(d.text, db)                     # r.data == data
findings = check_program(r.program, r.lines)
```
