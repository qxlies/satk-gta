# `satk script`: скрипты CLEO и SCM (дизассемблер, ассемблер, проверки, шаблоны)

[English version](../en/script.md)

Пакет: `satk.script`.

## Что это

Читает скрипты GTA San Andreas и записывает их обратно байт в байт: пользовательские скрипты CLEO 4/5 (`.cs`,
`.cm`, `.s`, `.cs4`, `.cs5`), потоковые скрипты внутри `script.img` и `main.scm` со всеми заголовками
(глобальные переменные, объекты, миссии, потоковые скрипты). Текстовая форма близка к опкодному синтаксису
Sanny Builder (`0001: wait 0`, `:LABEL`, `@LABEL`): моддер или ИИ-ассистент читает скрипт, меняет строку,
собирает снова и проверяет его до того, как он попадёт в игру. `satk script new` пишет небольшие рабочие
CLEO-скрипты по шаблонам (чит-код, спавн машины, телепорт, живой текст на экране, цикл миссии).

Всё пишется в `<workspace>\work\out\script\<имя>\`; файлы игры только читаются. Чтобы поставить скрипт,
скопируйте `.cs` в папку `cleo` игры сами (нужен установленный CLEO 4 или 5).

## Быстрый пример

```powershell
satk script new spawn_car --name mycar --model 522 --cheat BIKE
satk script check mycar/mycar.txt
satk script disasm mycar/mycar.cs --limit 6
satk script asm mycar/mycar.disasm.txt --compare mycar/mycar.cs
satk script disasm script.img --entry trains --limit 3
```

Что возвращается (сокращённо):

```json
{"ok":true,"file":"<workspace>/work/out/script/mycar/mycar.cs","text":"<workspace>/work/out/script/mycar/mycar.txt","template":"spawn_car","bytes":164,"commands":19,"opdb":"kb (3696 commands)","install":"copy mycar.cs into the game's cleo folder (CLEO 4 or 5); satk never writes into the game"}
{"ok":true,"cols":["sev","at","code","msg"],"rows":[],"n":0,"total":0,"src":"mycar.txt","kind":"cleo","clean":true}
{"ok":true,"file":"<workspace>/work/out/script/mycar/mycar.disasm.txt","kind":"cleo","bytes":164,"commands":19,"exact":true,"head":["{$CLEO .cs}","03A4: script_name 'MYCAR'",":MYCAR_11","0001: wait 250","00D6: if 1","0256: is_player_playing $2"],"warn":["KEPT: mycar.txt exists and differs (your source?); wrote mycar.disasm.txt (--force overwrites)"]}
{"ok":true,"file":"<workspace>/work/out/script/mycar/mycar.cs","kind":"cleo","bytes":164,"same":true}
{"ok":true,"file":"<workspace>/work/out/script/trains/trains.txt","src":"script.img:trains.scm","kind":"external","bytes":790,"commands":131,"padding_trimmed":1258,"exact":true,"head":["{$EXTERNAL}","03A4: script_name 'TRAINS'","0005: set_var_float $9525 0.0"]}
```

`satk script disasm main.scm` выводит весь ванильный `main.scm` (360 418 команд, 135 миссий) текстом
примерно за 3 секунды; `satk script asm main/main.txt --compare main.scm` возвращает те же 3 079 599 байт.

## Команды

| Команда | MCP | Что делает |
|---|---|---|
| `satk script disasm FILE [--entry NAME] [--out PATH] [--annotate]` | — (`satk_op`) | бинарник -> текст в `work\out\script\<имя>\<имя>.txt`; `exact` = сборка текста даёт те же байты (проверяется для файлов до 1 МБ, `--verify` включает всегда); `head` = первые строки кода |
| `satk script asm FILE [--out PATH] [--compare FILE]` | — | текст -> `.cs`/`.scm`/`main.scm` рядом; ошибки с номерами строк и `did_you_mean`; `same`/`first_diff` с `--compare` |
| `satk script check FILE [--entry NAME] [--severity warn]` | — | таблица `sev/at/code/msg` для текста или бинарника; `clean`, `counts` |
| `satk script new TEMPLATE [--name X] [--cheat C] [--key K] [--model M] [--pos X Y Z] [--text T]` | — | шаблон как `.txt` плюс собранный и проверенный `.cs` |

Общие параметры: `--opdb auto|kb|cleo-ai|core|<file.json>` выбирает базу опкодов, `--kind
auto|cleo|external|main` задаёт вид файла, `--profile` называет игру, в чьих папках `data\script\` и `cleo\`
ищутся короткие имена (`main.scm`, `script.img`). Относительные имена ищутся также в `work\out\script\`.
`disasm` никогда не перезаписывает другой `<имя>.txt` рядом с бинарником (ваш исходник или шаблон): пишет
`<имя>.disasm.txt`; `asm` файла `<имя>.disasm.txt` пишет `<имя>.cs`.

Шаблоны: `hello` (сообщение, потом ещё одно на каждое нажатие клавиши), `cheat` (чит-код: деньги, здоровье,
броня), `spawn_car` (чит-код создаёт `--model`), `teleport` (чит-код переносит игрока в `--pos`), `text` (клавиша
включает живой текст: по умолчанию позиция и курс игрока), `mission` (чит-код запускает; доберитесь до метки в
`--pos` за 2 минуты и получите $500).

## Текстовая форма

| Текст | Бинарно | Примечания |
|---|---|---|
| `0001: wait 0`, `wait 0` | команда `0001` | опкод или имя (ищется в базе опкодов); имя после опкода должно ему соответствовать |
| `8019: not ...`, `not ...` | бит 15 опкода | отрицание условия |
| `:LOOP`, `@LOOP` | смещение перехода | абсолютное в основном коде `main.scm`, отрицательное от начала в CLEO, потоковых скриптах и миссиях |
| `5`, `-300`, `0x8A5A80` | int8/int16/int32 | наименьший тип, вмещающий значение; `5:i32` сохраняет другую ширину |
| `1.0`, `-0.5`, `1.0e-08` | float32 | самый короткий текст, читающийся в те же биты; `0x7FC00000:f` для NaN |
| `$12` / `&13` | глобальная переменная | `$N` = смещение N*4 байт, `&N` = смещение в байтах |
| `0@`, `0@s`, `0@v` | локальная числовая / 8-байтная / 16-байтная строковая | `32@` и `33@` — таймеры |
| `s$4`, `v$5` | глобальная 8-байтная / 16-байтная строковая | |
| `$3(0@,10i)`, `0@(1@,4f)`, `s$20($5,3s)` | массивы | база(индексная переменная, размер + тип элемента `i f s v`) |
| `'TEXT'`, `v'TEXT'`, `"text"`, `r'TEXT'` | 8-байтная, 16-байтная, с длиной, нетипизированная строка | экранирование `\\ \' \" \xHH`; `r'...'` длиной 128 байт у `05B6` |
| `#INFO` | номер объекта в `main.scm` | отрицательные id таблицы `DEFINE OBJECT`; в CLEO-скриптах `#ИМЯ` ищется в индексе |
| `true` / `false` | int8 1 / 0 | |
| `hex 90 90 00*12 end` | сырые байты | данные, которые дизассемблер не смог (или не должен) читать как команды |
| `{$CLEO .cs}` `{$EXTERNAL}` `{$MAIN}` `{$MISSION}` `{$USE SAMPFUNCS}` | | вид файла (по умолчанию CLEO `.cs`), начало миссии, предпочтительное расширение опкодов |
| `DEFINE ...` | заголовки `main.scm` | `GLOBALS_SIZE`, `OBJECT(S)`, `MISSION(S)`, `SCRIPT name OFFSET n SIZE n`, ... |

Комментарии: `// ...`, `/* ... */` и `{ ... }` (подсказки Sanny Builder `{name}` не мешают; `--annotate`
добавляет их).

## Как это устроено

- **База опкодов.** `auto` берёт таблицу `opcode` базы знаний (`satk kb build`: все команды SA плюс
  расширения CLEO 5, CLEO+, NewOpcodes, SAMPFUNCS и плагинов, с типами параметров), иначе тот же справочник
  прямо из клона cleo-ai, иначе встроенное подмножество из 358 частых команд (шаблоны используют только их).
  Типы параметров ведут проверки: целое, дробное или строка, переменные для выходов, метки, модели и
  переменная часть команд вроде `0AB1 cleo_call` (заканчивается байтом `0x00`).
- **Дизассемблер идёт по потоку управления** от начала кода, каждой миссии и каждого параметра-метки, поэтому
  данные внутри кода (блоки машинного кода, хвосты Sanny Builder `__SBFTR`) остаются `hex`, а не читаются как
  бессмысленные команды. Недостижимые байты, которые точно разбираются, показываются командами. Представлен
  каждый байт, поэтому текст всегда собирается обратно в тот же файл.
- **Проверено на ванильной игре:** `main.scm` и все 79 записей `script.img` (с выравниванием по секторам и
  без) совпадают бит в бит, ни один байт не остаётся `hex`; так же реальные CLEO-скрипты с машинным кодом и
  хвостами Sanny Builder.
- **Проверки** (`satk script check`): `ASM` (ошибки сборки), `UNDECODED`, `JUMP_OUTSIDE`, `JUMP_MISALIGNED`,
  `JUMP_ABSOLUTE` (положительное смещение в CLEO-скрипте), `JUMP_ZERO` (метка на первом байте кодируется как 0,
  а игра читает это как начало `main.scm`), `NO_TERMINATE` (CLEO-скрипт должен кончаться `0A93`, переходом или
  возвратом), `FALLS_INTO_DATA`, `LOOP_NO_WAIT` (цикл без `wait` без выхода или не меняющий переменных),
  `IF_COUNT`, `NOT_CONDITION`, `SCRIPT_NAME` (длиннее 7 символов). В ванильном `main.scm` нет ни ошибок, ни
  `LOOP_NO_WAIT`.

## Ограничения и известные проблемы

- Только GTA San Andreas (формат PC 1.0). Заголовки `main.scm` GTA III/VC отклоняются с `UNSUPPORTED`.
- Только низкоуровневый синтаксис: нет высокоуровневых конструкций Sanny Builder (`if ... then`, `while`,
  `0@ = 5`, именованные переменные вроде `$PLAYER_CHAR`, классы). Глобальные переменные пишутся как `$N`
  (`$2` = `$PLAYER_CHAR`, `$3` = `$PLAYER_ACTOR`).
- С встроенным подмножеством команды вне его `asm` отклоняет, а `disasm` оставляет `hex`; для всех команд
  выполните `satk kb build` (нужен клон cleo-ai).
- `LOOP_NO_WAIT` — эвристика: цикл, вызывающий подпрограмму (`gosub`, `cleo_call`), считается ждущим в ней.
- Строки — это байты: не-ASCII символы пишутся как `\xHH`; `asm` принимает текст latin-1.

## Python-API (если другие пакеты его используют)

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
