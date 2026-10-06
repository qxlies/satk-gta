# scriptapi: Lua API MTA и нативы SA-MP/open.mp в базе знаний

[English version](../en/scriptapi.md)

Пакет: `satk.kb` (модули `scriptapi*`).

## Что это

Справочник для скриптов, собранный из исходников, которые у вас уже есть, без сети: каждая Lua-функция MTA:SA
(сигнатура, client/server/shared, имена в OOP, допустимые строки enum, `file:line` реализации на C++), встроенные
события с параметрами, OOP-классы и таблицы строк enum, а также нативы, колбэки и константы SA-MP/open.mp из
локальных include-файлов Pawn. `satk kb build` пишет всё это в `<workspace>\work\kb\kb.sqlite` рядом с остальной
базой знаний; `satk kb mta` и `satk kb native` отвечают за один вызов вместо поиска по C++ или чтения вики.

## Быстрый пример

```powershell
satk kb build
satk kb mta engineRequestModel
satk kb mta "vehicle handling"
satk kb mta Vehicle:setHandling
satk kb mta --event onClientElementStreamIn
satk kb native SetObjectMaterial
satk kb mta
```

Что приходит в ответ (сокращённо):

```json
{"ok":true,"name":"engineRequestModel","side":"client",
 "sig":"int|false engineRequestModel(string modelType, [int parentID])",
 "enums":{"client-model-type":"ped|object|object-damageable|vehicle|timed-object|clump"},
 "impl":"EngineRequestModel (CScriptArgReader)","loc":"Client/mods/deathmatch/logic/luadefs/CLuaEngineDefs.cpp:903",
 "src":"mta-lua","repo":"engine/mtasa"}
{"ok":true,"cols":["kind","name","side","sig"],"rows":[
 ["function","getVehicleHandling","shared","number|string|table|false getVehicleHandling(vehicle vehicle, [string property])"],
 ["function","setVehicleHandling","shared","number|bool setVehicleHandling(vehicle vehicle, [string property], [float|int|string|bool value])"]]}
{"ok":true,"name":"onClientElementStreamIn","kind":"event","side":"client","params":"(none)",
 "loc":"Client/mods/deathmatch/logic/CClientGame.cpp:2657"}
{"ok":true,"name":"SetObjectMaterial","kind":"native",
 "sig":"native SetObjectMaterial(objectid, materialindex, modelid, txdname[], texturename[], materialcolor = 0)",
 "params":6,"inc":"a_objects","loc":"src/samp-server/pawno/include/a_objects.inc:68","src":"pawn"}
{"ok":true,"functions":{"total":1527,"client":661,"server":261,"shared":605,"neon_only":339},"events":247,
 "classes":72,"enums":102,"natives":1747,"callbacks":564}
```

Опечатка даёт `NOT_FOUND` с `did_you_mean` (`engineRequestModle` -> `engineRequestModel`). Если у общей функции
клиентская и серверная версии различаются, вторая сторона приходит вложенным объектом только с отличиями.

## Команды

| Команда | MCP | Что делает |
|---|---|---|
| `satk kb mta NAME` | — | Lua-функция: `sig`, `side`, `doc` (комментарий с сигнатурой в исходниках MTA), `enums`, `oop`, `impl`, `loc`; также `Class:method`/`Class.var`, класс (`Vehicle`: члены), enum (`client-model-type`), событие |
| `satk kb mta "слова" [--side client\|server\|shared]` | — | функции и события, в именах которых есть все слова |
| `satk kb mta --event [NAME]` | — | событие с параметрами обработчика; без имени — список событий |
| `satk kb mta` | — | счётчики: функции по сторонам, функции только из Neon, события, классы, enum, нативы |
| `satk kb native NAME [--kind native\|callback\|const] [--inc a_objects]` | — | объявление Pawn с тегами, ссылками, массивами и значениями по умолчанию, его include и `file:line`; слова дают таблицу |
| `satk kb native` | — | include-файлы с числом нативов, колбэков и констант |

MCP-клиенты вызывают эти операции через `satk_ops`/`satk_op` (у них `mcp=False`). `satk kb search` находит те же
функции, события и нативы (виды символов `lua`, `lua-event`, `native`, `callback`).

## Как это устроено

- **Исходники MTA.** Дерево — `paths.engine` на `HEAD`, если в нём есть `Client/mods/deathmatch/logic/luadefs`,
  иначе `<workspace>\src\mtasa-neon` на `upstream/master`, иначе `<workspace>\src\mtasa-blue`. Читается через
  `git cat-file` (без checkout и без fetch). `<workspace>\src\mtasa-neon` на `HEAD` добавляет только функции и
  события, которых нет в upstream (`src` = `mta-lua-neon`, пометка «только в форке Neon»).
- **Функции.** Регистрации — элементы `{"name", Func}` массивов `lua_CFunction` и
  `CLuaCFunctions::AddFunction("name", Func)`; сторона — корень исходников (`Client`, `Server`; `Shared` — обе,
  с уточнением по `#ifdef MTA_CLIENT`). Функции `ArgumentParser<F>` получают точные типы из параметров C++
  функции `F` (`std::optional` — необязательный, `std::variant` — `A|B`, классы элементов — их тип элемента,
  enum C++ — `string` с допустимыми значениями). Классические функции на `CScriptArgReader` получают аргументы из
  вызовов `Read*` в порядке исходника, вместе со значениями по умолчанию; чтения под
  `if (argStream.NextIs...)` необязательны, альтернативы `if`/`else` сливаются в `A|B`, а возвращаемое значение
  берётся из вызовов `lua_push*`. Такие ответы несут пометку «approximate arguments», а `doc` показывает
  собственный комментарий MTA с сигнатурой, если он есть у функции.
- **OOP, события, enum.** `lua_classfunction`/`lua_classvariable` между `lua_newclass` и `lua_registerclass`;
  `AddEvent("onX", "params")`; таблицы `IMPLEMENT_ENUM_BEGIN` ... `ADD_ENUM(value, "name")`.
- **Include-файлы Pawn.** Каждый `.inc` с объявлениями `native`/`forward` в папках из `kb.pawn_include` в
  `satk.toml` (путь или список) или из `SATK_KB_PAWN_INCLUDE` (пути через `;` в Windows; переменная заменяет значение из файла), а также `pawno/include`,
  `qawno/include` или `include` каждой папки в `<workspace>\src`:

  ```toml
  [kb]
  pawn_include = ["<workspace>/src/samp-server/pawno/include"]
  ```

  Без единого include-файла сборка перечисляет только нативы карт, которые читает конвертер карт satk
  (`inc` = `satk-mapconv`), и предупреждает `SKIPPED: pawn`.
- **Цена.** Около 6 с от сборки базы; таблицы `mta_func`, `mta_class`, `mta_oop`, `mta_event`, `mta_enum`,
  `pawn_sym`. В репозиторий ничего не копируется: имена, типы, имена параметров и строки попадают в `work\kb`.

## Ограничения и известные проблемы

- Нет описаний из вики MTA или документации SA-MP (нет сети и чужих текстов документации); `doc` — комментарий
  из исходника, если он есть.
- Функции на `CScriptArgReader` с ветвлением по типу (`setVehicleHandling`, `setObjectProperty`) описаны
  приблизительно: смотрите `doc` и открывайте `loc`. Возвращаемые типы классических функций перечисляют все
  типы, которые кладутся на стек.
- Нет данных «добавлено в версии». База, собранная старым satk, отвечает `NOT_READY`: выполните `satk kb build`.

## Python-API (если другие пакеты его используют)

```python
from satk.kb.scriptapi import parse_mta, parse_pawn       # чистые парсеры: {путь: текст} -> записи
from satk.kb.scriptapi_query import mta, native, overview  # ответы команд
```
