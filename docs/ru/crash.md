# Крэши: отчёт, известное решение и виновник

[English version](../en/crash.md)

Пакет: `satk.crash`.

## Что это

`satk crash analyze` читает дамп (`.dmp` от MTA, WER, ProcDump или cdb), текстовый крэш-лог MTA (`core.log`,
`server_pending_upload.log`, вывод cdb) или лог одиночной игры с отчётом о падении (`modloader.log`, логи SA-MP и
mod_sa, вставленный текст). Он выдаёт отчёт не длиннее 30 строк:

- исключение, адрес доступа, регистры и стек; адреса `gta_sa.exe` символизируются через sa-re ([re.md](re.md)):
  функция, `file:line` в gta-reversed, патчи MTA;
- функции и строки исходников в модулях форка из подходящих локальных PDB;
- `known` и `solution` — подходящая запись списка крэшей CrashInfo (адреса падения, команды SCRLog, скрипты,
  модули) с готовым решением;
- `suspects` и `culprit` — ID моделей из регистров и из `scrlog.log` → кто их определяет: мод в папке `modloader`,
  слой индекса или никто (модель удалили);
- `logs` — сводка `modloader.log`, `scrlog.log` и лога CLEO из папки игры.

Всё только читается. Пишет только `crash sample` — в `work\out\crash\sample\` и `work\out\crash\sample-sp\`.

## Быстрый пример

```powershell
satk crash sample
satk crash analyze --last
satk crash info --last --part sections
satk crash sample --kind sp
satk crash analyze --last
satk crash known 0x00456809
satk crash known 038B
```

Что вернётся на синтетический крэш одиночной игры (сокращённо, `--table`, база sa-re собрана):

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

Свою игру и бисекцию модов смотрите так (пути — свои):

<!-- linkcheck: off -->
<!-- docs-smoke: skip пути к папке игры у каждого свои -->
```powershell
satk crash analyze "C:\Program Files (x86)\Rockstar Games\GTA San Andreas\modloader\modloader.log"
satk crash analyze crash.dmp --game "C:\Program Files (x86)\Rockstar Games\GTA San Andreas"
satk crash logs --game "C:\Program Files (x86)\Rockstar Games\GTA San Andreas"
satk crash bisect "C:\Program Files (x86)\Rockstar Games\GTA San Andreas\modloader"
satk crash bisect "C:\Program Files (x86)\Rockstar Games\GTA San Andreas\modloader" --results ok,crash
```
<!-- linkcheck: on -->

## Команды

| Команда | MCP | Что делает |
|---|---|---|
| `satk crash analyze PATH` · `--last` `[--limit 10] [--thread TID] [--block 0] [--images DIR…] [--dir DIR…] [--scan-kb 64] [--game DIR] [--profile P]` | — (через `satk_op`) | отчёт: кадры и сводка (`crash`, `fn`, `src`, `patched`, `known`, `solution`, `culprit`, `suspects`, `regs`, `logs`, `dump`/`log`, `mta`, `pools_full`, `hint`) |
| `satk crash known [QUERY] [--kind auto\|addr\|command\|script\|module\|text]` | — | список CrashInfo: по адресу (`0x00456809`, `gta_sa.exe+0x56809`), опкоду SCRLog (`038B`), `script:<имя>`, модулю (`CLEO.asi`) или словам; одна запись — целиком; без запроса — статистика списка |
| `satk crash logs [FILE…] [--game DIR] [--kind modloader\|scrlog\|cleo]` | — | сводка логов игры: версия modloader, моды, ошибки, отчёты о падении; последний скрипт и команда SCRLog, запрошенные модели; скрипты и ошибки CLEO |
| `satk crash bisect [DIR] [--results crash,ok,…]` | — | бисекция модов modloader: секция `IgnoreMods` для следующего запуска; в игру ничего не пишет |
| `satk crash info PATH\|--last [--part summary\|modules\|threads\|streams\|memory\|sections\|section\|stack\|text\|blocks] [--section TAG[:N]]` | — | части дампа: модули с PDB GUID/age, потоки, потоки данных, память, секции MTA, сырой стек |
| `satk crash list [--dir DIR…]` | — | дампы и логи, новые сверху; `modloader.log` настроенной игры — только если в нём есть отчёт о падении |
| `satk crash sample [--kind mta\|sp]` | — | синтетический крэш: MTA (дамп и `core.log`) или одиночная игра (дамп, `modloader`, `scrlog.log`, `cleo.log`) |

Колонка `via`: `ip` — указатель команды; `ebp` — цепочка кадров; `scan` — значение на стеке, перед которым стоит
CALL; `log` — строка стека из лога; `?` — байты кода неизвестны (предупреждение `UNVERIFIED`); `/pdb` означает символ
из подходящего PDB, например `ip/pdb` или `scan?/pdb`. Колонка `mta` называет
патч MTA по этому адресу (для адреса возврата — на CALL прямо перед ним).

`--last` берёт самый новый крэш из каталогов `crash list`: `work\dumps`, `Bin\MTA\dumps\private` и <!-- linkcheck: ignore -->
`Bin\server\dumps\private` форка MTA, установленные MTA (из реестра), `modloader\modloader.log` <!-- linkcheck: ignore -->
настроенной игры. Если лог новее дампа, но записан вместе с ним (MTA пишет оба), берётся дамп. Файлы из
`crash sample` берутся, только если больше ничего нет.

## Символы модулей форка (PDB)

В Windows кадры внутри `client.dll`, `core.dll`, `game_sa.dll`, `multiplayer_sa.dll` и других модулей разрешаются
через локальные PDB. Отчёт показывает `function+offset` в `fn`, `file:line` в `src`, если есть сведения о строках,
и `/pdb` в `via`. Для `gta_sa.exe` по-прежнему используется база символов sa-re. Адрес возврата ищется на один байт
раньше, чтобы выбрать вызывающую функцию и строку; показанное смещение относится к исходному адресу возврата.

Образы ищутся по путям из дампа, в `--images DIR…` и в `Bin` настроенного форка, включая `mta`, `mods/deathmatch`
и каталоги сервера. PDB ищутся рядом с образами и в тех же каталогах. Поэтому `satk crash analyze --last`
автоматически находит обычную сборку форка; для сохранённой сборки используйте
`satk crash analyze crash.dmp --images <build>/Bin`. Сохраняйте DLL и PDB именно той сборки, которая упала.

У образа для чтения байтов кода должны совпадать метка времени, размер и имеющийся идентификатор CodeView из дампа.
GUID/сигнатура и возраст самого PDB должны совпадать с идентификатором PDB из дампа или с CodeView локального образа
для текстового лога. При несовпадении выводится `REVISION`, символы этого PDB игнорируются; другой подходящий файл
всё ещё может быть использован. Дамп с идентификатором PDB позволяет использовать сохранённый PDB, даже если
старого DLL уже нет; для проверки CALL всё ещё нужен подходящий образ или память из дампа. Текстовому логу без
списка модулей нужен локальный образ. Без PDB, как у `netc.dll`, выводится имя экспорта, если оно известно, иначе
только `module+offset`.

DbgHelp берётся из установки Visual Studio, которую использует `satk engine`, с запасным вариантом из Windows
`System32`. Используется `ctypes`, дополнительные пакеты Python не нужны, символы не скачиваются, игра не запускается.
Если DbgHelp не может прочитать найденный PDB, отчёт сохраняет адрес или экспорт и выдаёт `EXTERNAL_TOOL`.

## Как это устроено

- **Список CrashInfo** — `data\crashlist\gta-sa-10us-en.txt`: английский список для GTA SA 1.0 US из
  JuniorDjjr/CrashInfo (MIT), байт в байт; ревизия и sha256 — в `data\crashlist\source.json`, лицензия —
  `data\notices\crashinfo.txt`. В нём 358 записей: 310 по адресу падения, 11 по командам, 12 по скриптам, 22 по
  модулям, 3 прочих. Текст свободный, его разбирает `crashlist.py`: записи `Error:` (адреса падения; адреса после
  слова «Backtrace» ждут на стеке), варианты `Problem 2:`/`Solution 2:`, разделы «By commands»
  (`Last command: [038B]`), «By scripts» (`script nebo` → `Mod:`), модули (`CLEO.asi`). Совпадение по указателю
  команды сильнее всего. Варианту записи отдаётся предпочтение, если его адрес из текста («0x005279B6 во второй
  строке Backtrace») есть на стеке. Адрес возврата, равный адресу записи, совпадением не считается (`0x0053E986`
  есть на стеке любого кадра главного цикла).
- **Отчёты одиночной игры** (`sptext.py`): `Exception At Address:` (SA-MP), `Exception at address:` (mod_sa),
  `Exception Address:`/`Exception Code:`/`Module:` (обработчики ASI-загрузчиков, `modloader.log`),
  `Unhandled exception at 0x… in …` (Visual Studio, WER). Регистры `EAX: 0x…` или `EAX=…`, стек после строки
  `Backtrace`/`Stack trace`/`Call stack`. В файле с несколькими отчётами берётся первый: следующие — последствия
  первого (`--block N` выбирает другой).
- **Виновник** (`culprit.py`, `modfolder.py`). Кандидаты — ID моделей:
  - регистр, который называет запись CrashInfo (для `0x00456809` — `EAX`);
  - `REQUEST_MODEL`/`CREATE_*` в конце `scrlog.log`;
  - любой другой регистр — только как подозреваемый и только если ID определяет мод.

  ID проверяется по `*.ide` модов в папке `modloader` (с учётом `IgnoreMods`, приоритета 0, `ExcludeAllMods` и
  `IncludeMods`, `ExclusiveMods` других профилей из `modloader.ini`), по именам `*.dff`/`*.txd` модов и по индексу
  профиля, корень которого — папка игры (слой определения). Папка игры — `--game`, папка, где лежит файл, или папка
  `gta_sa.exe` из дампа (не MTA).
- **Логи игры** (`gamelogs.py`): `modloader\modloader.log` (баннер `Mod Loader 0.3.7`, `Game version`, профиль,
  пути файлов модов, ошибки); `scrlog.log` (`script <имя>` и строки `[038B] COMMAND args`); `cleo.log` (CLEO 4.4/5,
  строки с датой: плагины, скрипты, ошибки с опкодами). Читаются только хвосты больших логов (у `scrlog.log` —
  2 МиБ).
- **Бисекция** (`bisect.py`): шаг 1 — все загружаемые моды отключены; `crash` значит, что причина вне modloader.
  Дальше половина оставшихся кандидатов (по имени) отключается, остальные моды загружаются. План пересчитывается из
  `--results`, поэтому состояние нигде не хранится. Секция `[Profiles.<профиль>.IgnoreMods]` сохраняет прежние
  записи; `undo` возвращает исходную. Имена секций сверены с `modloader.ini`, который поставляет Mod Loader 0.3.7.
- **Стек** (`stack.py`): у `gta_sa.exe` нет PDB и указателей кадра, поэтому кадры — эвристика: IP, цепочка EBP и скан
  до 64 КиБ стека. Значение считается адресом возврата, если оно в исполняемой секции модуля и перед ним CALL
  (`E8`, `FF /2`). Байты кода берутся из памяти дампа, иначе из файла модуля той же сборки (`--images`).
- **Форматы MTA** (`mta.py`): user-stream'ы `0x10000`–`0x10003` и хвостовые секции после дампа (`POL`, `LOG`, `REP`,
  `MSC`, `MEM`, `CAS`, `D3D`, `DXI`, `CAT`); в `core.log` несколько блоков, по умолчанию — последний.

## Ограничения и известные проблемы

- Записи CrashInfo — эмпирика сообщества, а не проверенные факты; ссылки страницы MixMods («click here») в
  текстовом файле не сохранились. Список только для GTA SA 1.0 US, английский.
- Форматы отчёта о падении в `modloader.log`, `scrlog.log` и лога CLEO разобраны по их общим признакам; настоящими
  файлами проверены только `mod_sa.log` и стартовые строки `modloader.log` и `cleo.log`. Если отчёт не распознан,
  `crash analyze` ищет в тексте адреса вида `gta_sa.exe+0x…`.
- Атрибуция по `*.ide` модов не учитывает слияние modloader (кто из двух модов победит) — это задача `satk mod`.
  Индекс ещё не загружает моды из `modloader`, поэтому слой `modloader:<мод>` у определения пока не встречается;
  ID модов находит чтение их `*.ide`.
- Бисекция ищет одного виновника; если падение даёт пара модов, последний шаг укажет не на тот мод (об этом
  напоминает `note`). Вложенные папки модов со своим `modloader.ini` делятся как один мод.
- Скан стека находит и «старые» адреса возврата уже завершённых вызовов. Модули MTA без своих PDB получают только
  `module+off`. 64-битные дампы сервера разбираются, но цепочки RBP нет.

## Python-API

```python
from satk.crash.analyze import analyze_file
from satk.crash import crashlist

env = analyze_file(path, game=game_dir)     # конверт 'satk crash analyze'; game_dir — папка игры или None
matches = crashlist.match(ip=0x456809)      # записи CrashInfo, лучшие первыми
```
