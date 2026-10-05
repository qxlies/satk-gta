# kb: база знаний о внутренностях GTA:SA — функции, структуры, опкоды, факты

[English version](../en/kb.md)

Пакет: `satk.kb`.

## Что это

Локальная SQLite-база с полнотекстовым поиском (FTS5) по исходникам, которые уже лежат в `<workspace>\src`:
gta-reversed, plugin-sdk, MTA (`Client/game_sa`, `multiplayer_sa`, `sdk/game` из upstream и отдельно файлы Neon,
которые от него отличаются), справочник опкодов cleo-ai, необязательные заметки Markdown в
`<workspace>\docs\research` (если такой папки нет, источник пропускается) и 40 проверенных фактов о движке.
Отвечает на вопросы «где определена функция и какая у неё сигнатура», «какой размер у класса и что лежит по
смещению», «что делает опкод», «сколько слотов в пуле». Пишет
только `work\kb\kb.sqlite` (~83 МБ, пересобирается за 30–60 с, можно удалять). Код источников в ответах — только
строки для показа; в репозиторий и в файлы он не попадает.

## Быстрый пример

```powershell
satk kb build
satk kb search "CStreaming RequestModel" --limit 3
satk kb struct CPed --at 0x540
satk kb opcode 0A8C
satk kb fact streaming.memory
```

Что вернётся (сокращённо):

```json
{"ok":true,"cols":["kind","name","loc","src","info"],
 "rows":[["func","CStreaming::RequestModel","source/game_sa/Streaming.cpp:1377","gta-reversed",
          "0x4087E0 void CStreaming::RequestModel(int32 modelId, int32 streamingFlags) // Request a given model to be loaded. ..."]],
 "n":3,"total":16,"next":"o:3"}
{"ok":true,"name":"CPed","kind":"class","src":"gta-reversed","loc":"source/game_sa/Entity/Ped/Ped.h:111","size":"0x79C","calc":"0x79C",
 "layout":"ok","bases":"CPhysical","sizes":{"plugin-sdk":"0x79C","mta-upstream":"0x79C (CPedSAInterface)"},
 "fields":{"cols":["off","name","type","size","via"],"rows":[["0x540","m_fHealth","float",4,"plugin-sdk"]],"total":1}}
{"ok":true,"op":"0A8C","name":"WRITE_MEMORY","ext":"CLEO","descr":"Writes the value at the memory address",
 "input":["address: int","size: int","value: any","vp: bool"],"handler":"WriteMemory (source/game_sa/Scripts/Commands/CLEO/CLEOMemoryCommands.cpp:11)"}
```

Сам `satk kb build` отвечает счётчиками (`sym` 68 513, `struct` 3 354, `opcode` 3 739, `fact` 40, …), итогом
проверки фактов (`verified` 40) и сводкой раскладок по источникам.

## Команды

| Команда | MCP | Что делает |
|---|---|---|
| `satk kb build [--no-research]` | — | пересобрать базу из всех источников (только чтение git, без сети) |
| `satk kb search "слова" [--source S] [--kind sym\|code\|opcode\|fact]` | — | поиск: символы с `file:line`, факты, опкоды, строки исходников; адрес вроде `0x5B8E64` находит все символы и строки, где он упомянут |
| `satk kb sym NAME [--kind K] [--source S]` | — | символ по имени (точное, `::член`, префикс, подстрока) или адресу; виды `func global struct vtable limit const enum define hookpos` |
| `satk kb struct NAME [--at 0x540] [--match S] [--inherited] [--bits] [--source S]` | — | размер (заявленный и вычисленный), базы, поля со смещениями; `--at` — поле по смещению, с заходом в базы и вложенные структуры |
| `satk kb opcode 0A8C` · `WRITE_MEMORY` · `"car coordinates"` `[--ext E]` | — | опкод: параметры, описание, обработчик в gta-reversed |
| `satk kb fact [KEY\|тема\|слова] [--status mismatch]` | — | 40 фактов о движке (пулы, стриминг, мир, скрипты, размеры) с уровнем доверия и проверками |

Источники (`--source`): `gta-reversed`, `plugin-sdk`, `mta-upstream`, `mta-neon`, `cleo-ai`, `research`, `facts`.
MCP-клиенты вызывают эти операции через универсальные инструменты `satk_ops`/`satk_op` (у них `mcp=False`).

## Как это устроено

- **Чтение источников.** Клоны читаются через `git cat-file` по ревизии (`gta-reversed` — `origin/master`, MTA —
  `upstream/master` и Neon — `HEAD`), без checkout и без докачки (`GIT_NO_LAZY_FETCH=1`): `git status` в `src\` не
  меняется. Ревизии записываются в таблицу `source`; `satk status --deep` (раздел `kb`) показывает источники,
  ушедшие вперёд после сборки.
- **Функции.** `<workspace>\src\gta-reversed\docs\hooks.json` соединяется с определениями в `.cpp` (комментарий
  `// 0xADDR` над функцией, сигнатура, первая строка комментария) и объявлениями в классах. Остальные определения с
  адресом и без — тоже символы. Плюс имена plugin-sdk, `#define FUNC_/VAR_/HOOKPOS_…` MTA, глобалы `StaticRef`,
  vtable, лимиты, константы и значения перечислений (часть берётся из сканеров `satk re`).
- **Структуры.** Поля классов разбираются из заголовков; смещения считаются по правилам 32-битного MSVC
  (выравнивание, `#pragma pack`, vptr, базы, битовые поля, объединения, простые шаблоны). Смещения из
  `VALIDATE_OFFSET`/`offsetof` того же источника — якоря (`via` = `assert`). Для класса gta-reversed смещения
  plugin-sdk с тем же именем поля подтверждают расчёт (`via` = `plugin-sdk`) или дают пометку о расхождении; расчёт
  они подменяют, только если он сам не сходится с заявленным размером. `layout`: `ok` — вычисленный размер равен
  заявленному (`VALIDATE_SIZE`/`static_assert`), затем `mismatch`, `unverified` (размер не заявлен), `partial` (есть
  тип неизвестного размера). Сейчас у gta-reversed `ok` — 743 класса из 761 с заявленным размером (2 `mismatch`,
  16 `partial`); у plugin-sdk — 391 `ok` и ни одного `mismatch`.
- **Опкоды** берутся из `<workspace>\src\cleo-ai\reference\opcode-index.md` и подробных разделов рядом (данные
  Sanny Builder Library); обработчик — по `REGISTER_COMMAND_HANDLER` в
  `<workspace>\src\gta-reversed\source\game_sa\Scripts\Commands`.
- **Факты** (`satk.kb.facts`) — 40 отобранных фактов. Уровни доверия: `code` (код gta-reversed), `2src`
  (второй независимый источник), `exe` (байты `gta_sa.exe`). Каждая сборка заново проверяет их по базе и по байтам
  чистого `gta_sa.exe` (только чтение): `verified` / `mismatch` / `unchecked`.
- **Поиск.** FTS5 `unicode61` с `_` внутри слов: каждое слово запроса — префикс, `camelCase` делится на слова
  (`InfoForModel` найдёт `ms_aInfoForModel`). Порядок: точное имя, факты, опкоды, символы, строки кода (не меньше
  трети страницы). Адреса в тексте (`0x05B8E55` тоже) лежат в `addr_ref`.
- База пишется во временный файл рядом и подменяется атомарно; запросы открывают её только на чтение и сразу
  закрывают.

## Ограничения и известные проблемы

- `loc` — путь внутри репозитория источника на той ревизии, из которой собрана база. Для `mta-upstream` рабочая
  копия `src\mtasa-neon` — это Neon, поэтому строку upstream смотрите через
  `git -C <workspace>\src\mtasa-neon show upstream/master:<путь>`.
- Раскладка — эвристика без компилятора: сложные шаблоны, `#ifdef` и макросы дают `partial`/`mismatch`; смещения с
  `via` = `calc` у таких классов проверяйте по `VALIDATE_OFFSET` или дизассемблеру.
- Сигнатуры и комментарии — однострочные выдержки; полный код функции gta-reversed — `satk re src`.
- Заголовки, значения и примечания 40 фактов пока написаны по-русски (данные `satk.kb.facts`).
- gtamods и форумы не зеркалятся; SilentPatch, OLA и CrashInfo в базу пока не входят.
- `NOT_READY` — база не собрана или собрана старой версией схемы: `satk kb build`.

## Python-API (если другие пакеты его используют)

```python
from satk.kb import query

query.search("CStreaming RequestModel")          # таблица, как у CLI
query.struct("CPed", at="0x540")                 # объект с полями
query.sym("0x8A5A80")                            # символы по адресу
```
