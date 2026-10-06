# `satk mta`: инструменты разработчика MTA:SA (проверка, заготовки ресурсов, паки моделей, логи)

[English version](../en/mta.md)

Пакет: `satk.mta`.

## Что это

Инструменты для тех, кто пишет ресурсы MTA:SA. `satk mta lint` читает папку ресурса (её `meta.xml` и все
скрипты) и находит то, что сломается на сервере, ещё до загрузки: синтаксические ошибки Lua с тем же текстом,
что печатает сервер MTA, функции MTA, которых нет или которые работают только на другой стороне, устаревшие
функции, неверные аргументы, события, которые никто не добавляет, ошибки в цикле отрисовки и в OOP,
отсутствующие файлы и объём, который игрокам придётся скачать. `satk mta resource new` пишет заготовку ресурса,
уже оформленную по принятым правилам; `satk mta pack` превращает папку файлов DFF/TXD/COL в ресурс, который их
загружает (замена штатных id или новые id); `satk mta logs` превращает `server.log`, `clientscript.log` или
текст `/debugscript` в строки с подсказкой; `satk mta server-check` даёт настоящему серверу MTA загрузить
ресурс (на 127.0.0.1, без игрового клиента).

Создаваемые ресурсы по умолчанию пишутся в `<workspace>\work\out\mta\<имя>\`; satk никогда не пишет на сервер
и в игру. Скопируйте ресурс в `<server>\mods\deathmatch\resources\` сами.

## Быстрый пример

```powershell
satk mta resource new my-panel --force
satk mta lint my-panel
satk mta resource new my-cars --kind vehicle-pack --force
satk mta lint my-cars --severity warn
```

Что возвращается (сокращённо):

```json
{"ok":true,"dir":"<workspace>/work/out/mta/my-panel","kind":"script","files":["client.lua","meta.xml","server.lua","shared.lua"],"lint":{"clean":true},"install":"copy the folder my-panel into <server>/mods/deathmatch/resources/ and 'start my-panel' in the server console (satk never writes into a server or the game)","next":"satk mta lint my-panel"}
{"ok":true,"cols":["sev","at","code","msg"],"rows":[],"n":0,"total":0,"next":null,"resource":"my-panel","scripts":3,"sides":["client","server"],"clean":true,"download_kb":1,"ref":{"source":"kb","functions":1861,"events":247,"mta":"1.7.0"}}
```

Ресурс с ошибками даёт такие строки (`satk mta lint broken --limit 4`):

```json
{"ok":true,"cols":["sev","at","code","msg"],"rows":[
 ["error","meta.xml:6","FILE_MISSING","<script src=\"missing.lua\">: file not found (MTA: Couldn't find file(s) missing.lua)"],
 ["error","client.lua:6:17","EVENT_WRONG_SIDE","'onPlayerJoin' is a server event: it never fires in a client script"],
 ["error","client.lua:15:1","UNKNOWN_FUNCTION","outputChatBoxx is not an MTA:SA client function and is not defined in this resource: did you mean outputChatBox?"],
 ["warn","client.lua:3:65","RENDER_HEAVY","dxCreateFont in an onClientRender handler: creates a font every frame (memory grows until the client runs out of video memory)"]],
 "n":4,"total":35,"next":"4","counts":{"error":15,"warn":20,"info":4},"clean":false}
```

## Команды

| Команда | MCP | Что делает |
|---|---|---|
| `satk mta lint PATH [--side S] [--severity warn] [--codes A,B] [--budget-mb 20] [--ref R]` | — (`satk_op`) | проверяет папку ресурса, `.zip` ресурса, её `meta.xml` или один `.lua`; таблица `sev/at/code/msg`, `counts`, `clean`, `download_kb` |
| `satk mta resource new NAME [--kind K] [--oop] [--model M --pos X Y Z] [--texture T] [--force]` | — | заготовка ресурса: `script`, `map`, `shader`, `vehicle-pack`, `skin-pack`, `object-pack` |
| `satk mta pack SRC [--kind vehicle\|skin\|object] [--new-id] [--replace ID ...] [--parent ID] [--lod M] [--name N]` | — | ресурс, загружающий свои модели; отчёт о загрузке с размером каждого файла и советами |
| `satk mta logs FILE [--level error\|warning\|info\|all] [--resource R] [--grep TEXT] [--no-group]` | — | строки лога как `line/level/res/at/msg/n/hint`, повторы объединены, счётчики `by_resource` |
| `satk mta server-check PATH [--wait 3]` | — | собранный сервер MTA загружает и запускает ресурс на 127.0.0.1; его сообщения о ресурсе |
| `satk mta ref [--out FILE]` | — | справочник MTA, по которому идут проверки; `--out` пишет его в JSON для `--ref FILE` |

`--ref` выбирает справочник MTA: `auto` (база знаний из `satk kb build`, иначе дерево исходников MTA), `kb`,
`source`, `none` (только проверки Lua) или JSON-файл из `satk mta ref --out`. Относительные имена (`my-panel`)
ищутся и в `work\out\mta\`.

## Что проверяет `satk mta lint`

| Код | Уровень | Что значит |
|---|---|---|
| `SYNTAX` | error | скрипт не компилируется; текст тот же, что печатает сервер MTA (`'end' expected (to close 'function' at line 3) near '<eof>'`), с подсказкой для синтаксиса Lua 5.2+ (`goto`, `//`, `!=`, `continue`) |
| `UNKNOWN_FUNCTION` | error | вызываемая глобальная функция не функция MTA, и ресурс её не определяет; `did you mean` |
| `WRONG_SIDE` | error | клиентская функция (или `localPlayer`) в серверном скрипте и наоборот; в скрипте `shared` внутри функции — `info` |
| `DEPRECATED` / `REMOVED` / `MIN_VERSION` | warn / error / warn | собственный список обновлений сервера MTA: замена, удалённая функция, поведение, зависящее от `<min_mta_version>` |
| `NEON_ONLY` | warn | функция есть только в форке MTA Neon |
| `ARG_COUNT` / `ARG_TYPE` / `ENUM_VALUE` | warn | мало или много аргументов, литерал не того типа, строка, которую функция не принимает (`engineRequestModel('vehical')`) |
| `EVENT_WRONG_SIDE` / `EVENT_UNKNOWN` / `EVENT_NOT_REMOTE` | error / warn / warn | клиентское событие в серверном скрипте, событие, которое никто не добавляет, `triggerServerEvent` без `addEvent(name, true)` на сервере |
| `OOP_DISABLED` | error | `localPlayer.position`, `Vehicle(...)` без `<oop>true</oop>` (векторы и матрицы работают всегда) |
| `RENDER_HEAVY` | warn | `dxCreate*`, `engineLoad*`, `getElementsByType`, вывод в чат ... каждый кадр в обработчике `onClientRender` |
| `HANDLER_CALLED` | warn | `setTimer(f(), ...)`, `addEventHandler(..., f())`: передаётся результат, а не функция |
| `DRAW_OUTSIDE_RENDER` / `RESOURCE_START_ROOT` | warn / info | `dxDraw*` на верхнем уровне; `onClientResourceStart` на `root` (срабатывает для каждого ресурса) |
| `UNDEFINED_GLOBAL` / `IMPLICIT_GLOBAL` / `DUPLICATE_FUNCTION` | warn / info / warn | глобальная переменная, которой на этой стороне ничего не присваивают; глобальная, которая должна быть `local`; одна глобальная функция определена дважды |
| `LUA_FIELD` / `MISSING_LIBRARY` / `DISABLED_FUNCTION` | error | `math.round`, `table.unpack` (Lua 5.2), `io.*`, `require`, `dofile`, `os.execute` |
| `ELEMENT_TYPE` / `NOT_EQ` / `ESCAPE_52` / `ENCODING` | warn | `getElementType(x) == "Player"`, `not a == b`, `"\x41"` в Lua 5.1, скрипт не в UTF-8 |
| `META_*`, `FILE_MISSING`, `BAD_PATH`, `SCRIPT_TYPE`, `DUPLICATE_FILE`, `EXPORT_MISSING`, `FILE_INVALID` | error / warn | `meta.xml`: ошибки XML, отсутствующие файлы, пути, которые MTA отвергает, неизвестные типы скриптов, экспорты, которых никто не определяет, битые файлы PNG/TXD/DFF/COL |
| `DOWNLOAD_SIZE` / `FILE_SIZE` / `NOT_LISTED` / `UNKNOWN_TAG` | warn / info | объём скачивания против бюджета, большие файлы, файлы `.lua` не из `meta.xml`, теги, которые MTA пропускает |

Комментарий `-- satk:ignore` (или `-- satk:ignore CODE1 CODE2`) глушит находки своей строки;
`-- satk:ignore-file CODE` глушит код во всём файле.

`RENDER_HEAVY` отслеживает вспомогательные функции, вызванные по имени, в том числе из других скриптов той же
стороны. Вызов внутри `if` или выражения с коротким замыканием всё ещё может выполняться каждый кадр.
Распознаётся ленивое заполнение постоянного кэша: `font = font or dxCreateFont(...)` и
`if not font then font = dxCreateFont(...) end`. Переменная `local font`, объявленная внутри обработчика,
создаётся заново каждый кадр и кэшем не считается.

## Паки моделей

<!-- docs-smoke: skip нужны свои файлы моделей -->
```powershell
satk mta pack mods/cars --kind vehicle
satk mta pack mods/cars --kind vehicle --new-id --name my-cars
satk mta pack mods/house.dff --kind object --new-id --lod 300
satk mta pack models.json --kind skin --replace 7 --replace 9
```

На вход подаётся каталог (поиск рекурсивный), один `.dff` или список JSON (также допустим `{"models": [...]}`).
Пути в JSON отсчитываются от файла списка; поля `name`, `txd`, `col`, `replace`, `parent` и `lod` необязательны:

```json
[{"name":"crate","dff":"models/crate.dff","txd":"models/shared.txd","col":"models/crate.col","parent":1337,"lod":300}]
```

При поиске в каталоге предпочтение получают одноимённые TXD/COL рядом с DFF, затем однозначные соответствия
в остальной части входного каталога. Если одноимённого TXD нет, единственный TXD рядом с DFF (или во всём
входном каталоге) может быть общим. Общий исходный файл копируется один раз, а разные файлы с совпадающими
именами получают разные переносимые имена на выходе.
Отсутствующие файлы, повторяющиеся имена моделей и недопустимые значения отвергаются до записи ресурса.
Имя ресурса содержит 1–64 латинские буквы, цифры, `_` или `-`; имена устройств Windows вроде `CON` запрещены.

- **Замена** (по умолчанию): каждая модель заменяет штатный id. Id берётся из `--replace` (по одному на модель,
  в порядке имён, в том числе для JSON: `411`, `model:411` или `model:infernus`), из списка JSON или из имени
  файла, если это имя штатной модели (`infernus.dff` заменяет 411; нужен `satk index build`). Id замен должны
  различаться и соответствовать выбранному типу моделей.
- **Новые id** (`--new-id`, MTA 1.6): каждый клиент вызывает `engineRequestModel(type, parent)`. Сервер создаёт
  элементы с родительской моделью и данными элемента `satk:<resource>:model` (`MODEL_DATA_KEY` в `models.lua`).
  Клиенты переключают подходящие элементы на новый id при запуске, появлении в зоне стрима и изменении этих
  данных. Паки скинов учитывают и пешеходов, и игроков, включая локального игрока. У каждого пака свой ключ
  данных, поэтому одинаковые имена моделей в разных паках не конфликтуют. Другие ресурсы используют
  `exports["my-cars"]:createPackVehicle(name, x, y, z)` (скины: `setPackSkin(ped, name)`, объекты:
  `createPackObject(...)`). Подсказка по установке называет проверочную команду для администратора.
  Параметр `--parent` требует `--new-id`.
- Файлы грузятся в порядке COL -> TXD -> DFF; `--lod` задаёт `engineSetModelLODDistance` (объекты с новыми id
  получают 300 м). `download.bytes` — точный объём моделей, текстур, коллизий и скачиваемых скриптов
  (`models.lua`, `client.lua`), с однократным учётом общих файлов. Отчёт показывает 20 самых больших файлов,
  сравнивает итог с `--budget-mb` и даёт советы (`satk texture audit` / `satk texture optimize` для больших
  текстур). Бюджет и дальность прорисовки должны быть конечными положительными числами.
  Файлы копируются как есть: satk их не шифрует и не обфусцирует.

## Логи и проверка сервером

<!-- docs-smoke: skip нужны лог сервера и собранный сервер MTA -->
```powershell
satk mta logs server.log --level error
satk mta logs clientscript.log --resource my-panel
satk mta server-check my-panel
```

`satk mta logs` читает последние 32 МБ `server.log`, `clientscript.log`, сохранённую консоль сервера или текст,
скопированный из `/debugscript`. Повторяющиеся сообщения объединяются (`[DUP x12]` тоже учитывается), ошибки
идут первыми. Подсказки: `Bad argument @ 'setElementPosition'` получает аргументы функции,
`attempt to call global 'x'` — сторону `x` или вариант написания, отсутствующие файлы и ошибки ACL — способ
исправить.
При усечении большого лога неполная первая строка отбрасывается, а `line` равен `0`, поскольку абсолютный
номер строки лога неизвестен; место в скрипте в поле `at` сохраняется. Если справочник не загрузился,
предупреждение сообщает о доступности только встроенных подсказок, а разобранные сообщения всё равно возвращаются.

`satk mta server-check` нужен сервер форка MTA (`satk engine build --project server --platform x64`). Он
копирует сервер и ресурс в `work\mta\check\`, запускает сервер без окна на 127.0.0.1 со свободными портами и
без других ресурсов, вводит в его консоль `start <имя>`, ждёт `--wait` секунд и останавливает. `started`
говорит, запустился ли ресурс; строки — собственные сообщения сервера (ошибки загрузки, ошибки серверных
скриптов, предупреждения об устаревших функциях). Клиентские скрипты там не выполняются (нет игрового
клиента): их проверяет `satk mta lint`.

## Как устроено

- **Lua 5.1, как в MTA.** Парсер свой (без зависимостей) и повторяет компилятор Lua 5.1.5, который поставляет
  MTA: те же токены, особенности (вложенные `[[`, `\x` — не управляющая последовательность, правило «ambiguous
  syntax») и пределы (200 локальных переменных, 60 upvalue, 200 уровней вложенности). Тесты сравнивают верные и
  ошибочные скрипты с собственной `lua5.1.dll` MTA, включая скрипты с искусственными изменениями. Проверка
  корпуса исходников компилирует файлы Lua без их выполнения. Не-ASCII символ в `near '...'` печатается
  символом, а не сырым байтом.
- **По сторонам.** Скрипты одной стороны живут в одном состоянии Lua, поэтому глобальная, определённая в
  `a.lua` (клиент), известна в `b.lua` (клиент), но не на сервере; скрипты `shared` проверяются для обеих сторон.
  Для отдельного файла, сторону которого определить не удалось, допустима сигнатура любой стороны;
  предупреждения об устаревании выдаются, если оно касается всех подходящих сторон. Параметр `--side`
  задаёт сторону явно.
- **Справочник MTA** берётся из `satk kb build` (функции по сторонам со списками аргументов, события, классы
  OOP, строки enum; см. [scriptapi.md](scriptapi.md)). Устаревшие функции — собственный список обновлений
  сервера MTA из дерева исходников. Без базы знаний satk разбирает дерево напрямую (около 5 с, затем кэш в
  `work\cache\mta\`); без дерева работают только проверки Lua.

## Ограничения и известные проблемы

- Вызовы через переменные, `exports`, `call()` и глобальные, созданные во время работы (`loadstring`,
  `_G[name]`), не отслеживаются; для ресурса, который ими пользуется, находки `UNKNOWN_FUNCTION` и
  `UNDEFINED_GLOBAL` мягче.
- Проверки аргументов доверяют только точным сигнатурам (функции с новым разбором аргументов); у функций,
  читающих аргументы с ветвлениями, проверяются только первые аргументы.
- События, добавленные другим ресурсом, выглядят неизвестными (`EVENT_UNKNOWN` так и пишет); собственные типы
  элементов, созданные другим ресурсом, — `info`.
- `min_mta_version` проверяется на формат и для функций, которые MTA перечисляет с версией; полной таблицы
  «функция появилась в версии X» нет.

## Python API

```python
from pathlib import Path
from satk.mta.api import load_reference
from satk.mta.lint import lint_path, lint_source
from satk.mta.luaparse import parse, LuaSyntaxError

ref, warnings = load_reference("auto")
result = lint_path(Path("my-panel"), ref)
for f in result.findings:
    print(f.sev, f.at, f.code, f.msg)
```
