# MCP-сервер, справка, диагностика и заметки

[English version](../en/mcp.md)

Пакеты: `satk.mcp` (сервер, `satk_ops`/`satk_op`), `satk.runtime` (status, doctor, help), `satk.notes`.

## Что это

Один MCP-сервер `satk` (stdio) отдаёт агенту те же операции, что и CLI: каждый `@op` с MCP-именем становится
инструментом с той же схемой параметров и тем же JSON-ответом. Набор инструментов собирается из реестра при старте
сервера, поэтому пакеты, слитые позже, появляются сами. Операции без своего инструмента (их большинство: `formats`,
`game`, `index build`, новые пакеты) агент находит инструментом `satk_ops(query)` и запускает инструментом
`satk_op(op, args)` — с той же проверкой аргументов и тем же исполнением, что у собственного инструмента.
Рядом с сервером — `satk help` (справка для людей и агентов), `satk status` (обзор: что собрано и что запущено),
`satk doctor` (проверка окружения) и заметки в `work\notes.sqlite`, которые переживают сессии.

Подключение других ИИ-клиентов (Codex, Cursor, VS Code, LM Studio и других) — на странице [ai.md](ai.md).

## Быстрый пример

```powershell
satk status --table
satk doctor --only python,utf8,deps,disk --table
satk help ids
satk note list --table
satk mcp config
satk mcp ops dump --table
satk mcp op "index status" --table
```

Что вернётся (сокращённо, `satk status`):

```json
{"ok":true,"satk":{"version":"0.1.0"},"game":{"root":"<workspace>/gta-sa-clean","ok":true},"warn":["INDEX_MISSING: profile vanilla not built; fix: satk index build"]}
```

`warn` в `satk status` есть всегда, когда что-то мешает инструментам: если секция пакета сама предупреждений не
дала, для копии игры и индекса они выводятся из её полей (`GAME_MISSING`, `GAME_BAD`, `INDEX_MISSING`,
`INDEX_STALE`).

## Подключение Claude Code к рабочему пространству

Другие ИИ-клиенты и Claude Code для всех папок подключаются командой `satk mcp config --client <клиент>`, skill —
командой `satk agent install-skill`: всё это описано на странице [ai.md](ai.md). Этот раздел — о собственном
проекте рабочего пространства satk.

1. Файл `<workspace>\.mcp.json` регистрирует сервер для проекта. Его пишет `satk mcp config --write`, сверяет
   `satk mcp config --check`, а `satk mcp config --raw` печатает только фрагмент JSON для вставки. Сервер
   запускается командой `<workspace>\tools\.venv\Scripts\python.exe -X utf8 -m satk.mcp` с
   `PYTHONPATH=<workspace>\tools\src` и `SATK_HOME=<workspace>`. `--write` сохраняет другие серверы и ключи файла
   и пишет UTF-8 без BOM (BOM, который добавляет PowerShell 5.1 `Out-File -Encoding utf8`, JSON-парсер Claude Code
   не принимает; `--check` и `satk doctor` о нём сообщают). Файл с битым JSON `--write` не перезаписывает: сначала
   исправьте его.
2. При первом запуске сессии в рабочем пространстве Claude Code спросит, доверять ли проектному серверу `satk`.
   Состояние показывает команда `/mcp`.
3. Проверка без Claude Code: `satk mcp selftest` поднимает сервер подпроцессом и проводит настоящую stdio-сессию
   (initialize, tools/list, `satk_help`, `satk_status`, `satk_ops`, `satk_op`), меряет ответ tools/list (бюджет
   16 КБ, плюс байты по группам) и проверяет, что в stdout нет ничего, кроме кадров JSON-RPC.
4. Узкий набор инструментов экономит контекст: переменная `SATK_MCP_GROUPS=core,index,view` (группы:
   `core index media view re blender engine`, `all` — все). `core` = `satk_status`, `satk_help`, `satk_ops`,
   `satk_op`, `note`. Группы ограничивают и `satk_op`: операция из группы вне списка отвечает `UNSUPPORTED`.
   Неизвестное имя группы (опечатка вроде `cor`) — ошибка `BAD_PARAMS`: сервер не стартует (сообщение уходит в
   stderr и `work\logs\mcp.log`), а `satk mcp selftest` и `satk doctor` (проверка `mcp_groups`) подсказывают
   правильное имя.

## Команды

| Команда | MCP | Что делает |
|---|---|---|
| `satk status [--deep]` | `satk_status` | обзор: копия игры, индекс, вьювер, symdb, Blender, движок, заметки, kb, media, предупреждения |
| `satk help [topic] [--ru]` | `satk_help` | без темы в командной строке — экранная шпаргалка «Я хочу…»; `all` (или `--all`) — все команды; темы `start ids tools ops workflows schema errors viewer re blender engine golden notes` или имя команды; `--ru` — по-русски |
| `satk mcp ops [запрос] [--limit N]` | `satk_ops` | поиск любой операции: таблица `op / args / summary`, при одном совпадении — ещё и полная JSON-схема |
| `satk mcp op ОПЕРАЦИЯ [--args JSON]` | `satk_op` | выполнить операцию по имени (`formats.dump`, `"index status"`, имя инструмента) с проверкой аргументов |
| `satk doctor [--deep] [--only a,b]` | — | проверки `python venv deps utf8 paths disk network git blender msbuild premake mcp_config mcp_groups index ops_import` и проверки всех пакетов (`game_copy`, `kb`, `viewer`, …) |
| `satk note add SID "текст" [--tags …]` | `note` (action=add) | заметка к любому SID |
| `satk note list [SID] [--query …] [--tag T] [--author A]` | `note` (action=list) | заметки по SID или полнотекстовый поиск (и по кириллице, по префиксам слов) |
| `satk note rm note:N` · `satk note export` · `satk note import` | — | удаление; экспорт в `data/notes/notes.jsonl` этого checkout и обратно |
| `satk mcp` · `satk mcp selftest` · `satk mcp config [--write\|--check\|--raw]` | — | сервер, самопроверка, регистрация в `.mcp.json` |
| `satk mcp config --client <клиент> [--write\|--check\|--raw\|--print-command]` | — | регистрация в любом ИИ-клиенте ([ai.md](ai.md)) |
| `satk agent install-skill [--client claude\|codex\|all] [--dest ПАПКА] [--check]` | — | skill satk для Claude Code и Codex |
| `satk dev gen-docs [--check]` | — | генерирует `docs/agent/tools.md` и `docs/agent/schema.md` |

## Любая операция: `satk_ops` и `satk_op`

Своих инструментов у операций мало: бюджет tools/list — 16 КБ, и новые операции пакетов регистрируются с
`mcp=False`. Два универсальных инструмента дают агенту все остальные, не увеличивая список:

1. `satk_ops(query, limit=20)` ищет по именам, словам CLI, сводкам (и русским), параметрам, описаниям и примерам;
   все слова запроса должны совпасть (иначе приходят ближайшие и предупреждение `NO_FULL_MATCH`). Строки —
   `op | args | summary`; `args` — компактная сигнатура: `name:type` — обязательный, `name?:type` — необязательный,
   `name:type=умолчание`; типы `str int num bool {} [str]`, перечисления `a|b`. При одном совпадении (или если
   запрос — точное имя операции) в ответ добавляются `schema` (полная JSON-схема с описаниями параметров) и
   `examples`. Подходящие операции, которые агенту запускать нельзя, перечислены в `cli_only` с причиной.
2. `satk_op(op, args)`: `op` — имя через точку (`formats.dump`), слова CLI (`formats dump`) или имя инструмента;
   `args` — JSON-объект параметров. Сервер разрешает имя, проверяет политику и аргументы (`OpSpec.bind`, как CLI и
   собственный инструмент: `BAD_PARAMS` с `did_you_mean` ещё до запуска) и исполняет операцию так же, как её
   собственный инструмент: обычную — в рабочем потоке под замком её группы (120 с), `long_running` — в
   процессе-работнике с прогрессом и деревом процессов (600 с; по таймауту гибнет всё дерево). Ответ — конверт самой
   операции; `inline=true` у операций с картинками работает и здесь.

Закрыты для агента (актуальный список показывает `satk help ops`):

| Операция | Код | Почему |
|---|---|---|
| `mcp`, `mcp.selftest`, `mcp.config`, `mcp.ops`, `mcp.op` и любые будущие `mcp.*` | `UNSUPPORTED` | управление самим сервером |
| `dev.gate`, `dev.gen_docs`, `dev.sync_agent_docs`, `dev.release` | `UNSUPPORTED` | команды разработчика (минуты тестов, перезапись документов, сборка релиза) |
| `view.mock` | `UNSUPPORTED` | работает до Ctrl+C и заблокировал бы вызов |
| `init`, `game.clone`, `game.protect`, `game.unprotect`, `engine.setup`, `note.rm`, `describe.clear`, `agent.install_skill` | `CONSENT_REQUIRED` | меняют настройку пользователя или копию игры, качают зависимости, удаляют данные или пишут в папки ИИ-клиентов пользователя — спросить пользователя |

Пакет закрывает свою операцию декоратором над `@op`:

```python
from satk.mcp.generic import cli_only

@cli_only("rewrites the user's config", consent=True)   # consent=True -> CONSENT_REQUIRED
@op("x.reset", summary="...", summary_ru="...", mcp=False)
def reset() -> dict: ...
```

CLI-двойники `satk mcp ops` и `satk mcp op` делают то же самое в этом процессе (долгие операции — тоже здесь, без
процесса-работника) и учитывают `SATK_MCP_GROUPS`, если она задана.

## Как это устроено

- Ответ инструмента — один текстовый блок с компактным JSON-конвертом (как `satk … --json`); ошибка — тот же
  конверт с `isError`. Картинки встраиваются только при `inline=true` (не больше 1024 px).
- Обычные операции выполняются в рабочих потоках (по одной на группу одновременно, таймаут 120 с,
  `SATK_MCP_TIMEOUT`); операции `long_running` (`blender_job`, `engine`, `index build`, …) — в отдельном процессе
  с уведомлениями о прогрессе (таймаут 600 с, `SATK_MCP_LONG_TIMEOUT`). Этот процесс и всё, что он запустил
  (Blender, MSBuild), живут в одном Job Object Windows: по таймауту, при отмене вызова или при падении самого
  сервера завершается всё дерево, а не только первый процесс (`satk.mcp.proctree`). При обычном завершении то, что
  операция намеренно оставила работать, не трогается.
- В CLI флаг-список перед позиционными аргументами их не съедает: `satk asset get --fields name model:411` и
  `satk note add --tags a b model:411 "текст"` работают (недостающие позиционные берутся с конца списка). Надёжнее
  всего — позиционные первыми или значения списка через запятую.
- Логи — только в stderr и `work\logs\mcp.log`; stdout принадлежит JSON-RPC. Вызов `satk_op` пишется в лог как
  `call satk_op -> index.status ok 12 ms`.
- Операция, которая завершает процесс как команда CLI (`SystemExit`), не роняет сервер: вызов получает `INTERNAL`
  с подсказкой запустить её в терминале.
- Заметки: таблица `note` + FTS5 `unicode61` (префикс слова находит всё слово, и по кириллице тоже: «машин»
  находит «машина»); одинаковая заметка (SID, автор, текст) хранится один раз. Закладки `bm:` и захваты `cap:` лежат
  в той же базе. Сборка индекса базу заметок не трогает. Автор по умолчанию — `claude` (или `SATK_AUTHOR`); люди
  передают `--author human:<имя>`.

## Ограничения и известные проблемы

- Холодный старт сервера — 1–3 с (импорт MCP SDK; `satk mcp selftest` намерил 1,1 с); на сильно нагруженной
  машине — до 10 с.
- Бюджет tools/list (16 384 байт) общий на все 27 инструментов; сейчас 15 727 байт (точное число и байты по группам
  показывает `satk mcp selftest`); `satk_ops` и `satk_op` вместе стоят около 0,7 КБ. Бюджет поделён между группами
  (`GROUP_BUDGET` в `src/satk/mcp/adapter.py`):

  | Группа | Доля, байт | Сейчас, байт |
  |---|---|---|
  | `core` | 2140 | 2095 |
  | `index` | 3890 | 3789 |
  | `media` | 2740 | 2675 |
  | `view` | 4410 | 4186 |
  | `re` | 2210 | 2147 |
  | `blender` | 420 | 346 |
  | `engine` | 500 | 451 |

  `tests/mcp/test_adapter.py::test_each_group_within_its_share` падает на ветке того пакета, чья группа выросла,
  ещё до слияния, поэтому main не уходит за 16 КБ после нескольких слияний. Описания короткие: сводки около
  150 символов; у очевидных параметров (`limit`, `cursor`, `profile`, фильтр `kind`) описания нет; значения enum в
  тексте не повторяются. Чтобы дать группе больше, байты переносят между долями (через лида).
- Тема `satk help tools` укладывается в 6000 символов; если инструментов станет больше, она сокращает сводки до
  первого предложения. Тема `satk help ops` при переполнении обрезает список.
- `satk_ops` ищет по тексту описаний операций: если формат (например, GXT) не упомянут ни в одной операции, запрос
  вернёт пустую таблицу с подсказкой, а не догадку.
- Если `satk.mcp` не стартует: `satk doctor` (`deps`, `ops_import`), затем лог `work\logs\mcp.log`. Остальное — в
  [troubleshooting.md](troubleshooting.md).

## Python-API (если другие пакеты его используют)

```python
from satk.notes import db                  # заметки, закладки, захваты (viewer, index)
db.notes_for("model:411"); db.count_for("model:411")
db.bookmark_save("grove_center", {"pos": [2495, -1687, 30]})
db.capture_add("<workspace>/work/out/captures/x.png", "ariane", {"pos": [0, 0, 0]}, w=960, h=540)
from satk.notes.provider import BookmarkCaptureProvider   # провайдер SID bm/cap для satk.viewer
from satk.runtime.help import register_topic              # своя тема для satk_help
from satk.mcp.generic import cli_only, denial, search, prepare   # политика и поиск satk_op/satk_ops
```
