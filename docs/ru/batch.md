# Пакетный режим и рецепты: одна команда на много файлов, сохранённые многошаговые проверки

[English version](../en/batch.md)

Пакет: `satk.batch`.

## Что это

Работа модостроителя однообразна: проверить пятьдесят машин, вытащить все текстуры мода, перед каждым релизом
прогнать те же четыре проверки, разбирать каждый краш одинаково. `satk batch` запускает любую операцию satk по
одному разу на каждый вход (glob, записи IMG-архива, файл со списком, папка, SQL-запрос к индексу или список SID)
и отвечает счётчиками и первыми ошибками, а не пятьюдесятью ответами. `satk recipe` выполняет сохранённый список
операций с переменными, где шаг может использовать ответ предыдущего. Оба пишут только в `<workspace>\work\`
(файлы результатов и отчёты); файлы игры только читаются.

## Быстрый пример

```powershell
satk recipe list
satk batch formats.dump --over "models/gta3.img/infer*" --dry-run
satk batch asset.lint --over "models/gta3.img/infernus.*" --arg fail_on=error --jobs 2
satk crash sample --kind sp
satk recipe run crash-triage --dry-run
satk recipe run crash-triage
```

Что приходит в ответ (сокращённо):

```json
{"ok":true,"cols":["name","source","summary","vars"],"rows":[["check-mod-before-release","shipped","Before publishing a mod - …","mod*, profile"],["crash-triage","shipped","Triage a game crash - …","crash, game"],…]}
{"ok":true,"cols":["n","item","state","args"],"rows":[[1,"infernus.dff","run","{\"target\": \"<game>/models/gta3.img/infernus.dff\"}"],[2,"infernus.txd","run",…]],"warn":["OVER: matched under the vanilla game root <game>"],"op":"formats.dump","dry_run":true,"items":2,"source":"img",…}
{"ok":true,"cols":["item","status","code","msg"],"rows":[],"op":"asset.lint","items":2,"counts":{"ok":2,"fail":0},"totals":{"fatal":0,"error":0,"warn":0,"info":1},"jobs":2,"out":"<workspace>/work/out/batch/asset.lint-78a5636d.jsonl"}
{"ok":true,"cols":["step","op","status","info"],"rows":[["recent","crash.list","ok","3 row(s)"],["analyze","crash.analyze","ok","4 row(s); crash=0xC0000005 ACCESS_VIOLATION …; fn=CPickup::GiveUsAPickUpObject+0x29; …"],["modules","crash.info","ok","3 row(s)"],["logs","crash.logs","skipped","when: false"],["bisect","crash.bisect","skipped","when: false"]],"recipe":"crash-triage","counts":{"ok":3,"skipped":2},"out":"<workspace>/work/out/recipes/crash-triage.json"}
```

Если вход не прошёл, пакет отвечает `CHECK_FAILED` (код выхода 1) и таблицей упавших входов:
`satk batch asset.lint --over "mods/cars/*.dff" --arg fail_on=error` на 50 файлах, из которых 3 битых, пишет
`3 of 50 input(s) failed, 47 ok (CHECK_FAILED 3)` и перечисляет эти три.

## Команды

| Команда | MCP | Что делает |
|---|---|---|
| `satk batch <op> --over <входы> [--arg k=v ...] [--jobs N] [--resume] [--out f.jsonl] [--dry-run]` | — (через `satk_op`) | запускает `<op>` по разу на вход; одна запись JSONL на вход в `--out` (по умолчанию `<workspace>/work/out/batch/<op>-<hash>.jsonl`); ответ: `counts`, `by_code`, `totals` (сложенные счётчики `summary`), первые ошибки |
| `satk batch report <results.jsonl> [--status fail] [--field summary.fatal ...]` | — | таблица файла результатов: статус, ошибка или поля каждого ответа; страницы через `--cursor` |
| `satk recipe list` | — | поставляемые рецепты и ваши (`<workspace>/work/recipes/*.yaml`; при одинаковом имени побеждает ваш) |
| `satk recipe show <имя\|file.yaml>` | — | переменные и шаги, и установлена ли операция каждого шага |
| `satk recipe run <имя\|file.yaml> [--var k=v ...] [--dry-run]` | — | выполняет шаги; таблица `step/op/status/info`; полный ответ каждого шага в `--out` (по умолчанию `<workspace>/work/out/recipes/<имя>.json`) |

`--over` принимает: glob (`mods/*.dff`, `mods/**/*.txd`; относительный glob, который здесь ничего не нашёл, <!-- linkcheck: ignore -->
пробуется в корне игры, поэтому `models/gta3.img/*.dff` работает откуда угодно); записи IMG
(`<file.img>/<шаблон>`); `@list.txt` (один вход на строку или JSON-объект аргументов на строку; `@-` читает stdin);
папку (её файлы и подпапки); SQL (`SELECT ...` по индексу профиля `--profile`; столбцы с именами параметров
операции заполняют эти параметры); или SID и имена через запятую (`model:411,model:415`). Каждый вход идёт в
первый обязательный параметр операции (`--param` выбирает другой). В значениях `--arg` можно писать
`{item} {name} {stem} {ext} {parent} {n}`: `--arg out=work/out/tex/{stem}`. `--param none` передаёт вход только
в эти шаблоны, поэтому пакет может запустить рецепт на каждый мод: `satk batch recipe.run --over "mods/*"
--param none --arg recipe=check-mod-before-release --arg var=mod={item} --arg out=work/out/recipes/{name}.json`.

Поставляемые рецепты:

| Рецепт | Переменные | Шаги |
|---|---|---|
| `check-mod-before-release` | `mod`, `profile` | `mod inspect`, `mod check` (падает на находках fatal/error), аудит текстур (если установлен), `id conflicts` |
| `many-cars-lint-and-pack` | `cars`, `img`, `out_dir`, `fail_on`, `profile`, `jobs`, `addons` | пакет проверки по всем DFF и по всем TXD, аудит текстур, `id conflicts`, `img build`, `formats ls` нового IMG; с `addons` (файл-список аргументов `mod add`) папка аддона на каждую машину |
| `export-all-textures-of-a-mod` | `mod`, `force` | `texture extract` одного TXD или пакет по отдельным TXD и пакет по TXD внутри IMG мода |
| `crash-triage` | `crash`, `game` | `crash list`, `crash analyze` (по умолчанию самый новый краш), модули дампа через `crash info`, `crash logs` и `crash bisect` папки игры |

## Как написать рецепт

Рецепт — файл YAML (строгое подмножество: словари, списки, значения в кавычках и без, `[a, b]`, `{k: v}`, блоки
`|`; без якорей и тегов), JSON или TOML. Положите его в `<workspace>/work/recipes/`, чтобы запускать по имени.

```yaml
name: lint-and-dump
summary: Lint one file and dump it when it is clean.
vars:
  file: {required: true, help: "a DFF, TXD or COL"}
  sev: warn
steps:
  - id: lint
    op: asset.lint
    input: ${file}
    args: {sev: "${sev}"}
    fail_if: "${steps.lint.summary.error|default:0}"
    on_error: continue
  - id: dump
    op: formats.dump
    input: ${file}
    when: "${steps.lint.summary.fatal|not}"
```

- `op` — имя операции или список кандидатов (выполняется первая установленная). Если не установлена ни одна,
  шаг получает `skipped: op not available`, а шаги, которым нужен его ответ, тоже пропускаются; `requires: mod.add`
  делает то же для операции, которая нужна шагу косвенно (внутренняя операция шага `batch`).
- `${name}` — переменная (`--var name=value`), `${work}`, `${cwd}`, `${recipe_dir}` или поле ответа
  предыдущего шага: `${steps.lint.summary.fatal}`, `${steps.ls.rows.0.1}`, `${steps.ls.col.name}` (столбец как
  список). Фильтры: `not bool len first json name stem parent ext lower eq:<значение> default:<значение>`.
  Значение из одной ссылки сохраняет тип (список остаётся списком); пустое значение (null) убирает аргумент.
- `when` / `unless` пропускают шаг; `fail_if` превращает ответ в ошибку; ошибка останавливает рецепт, если нет
  `on_error: continue`. Любой упавший шаг делает ответ `CHECK_FAILED`.
- `--dry-run` проверяет переменные, операции и аргументы и показывает план; ничего не запускает и не пишет.

## Как это работает

- **В одном процессе.** Каждый вход — обычный вызов операции (та же проверка аргументов, что в командной
  строке). Операции, про которые известно, что они потокобезопасны (проверка, дампы, поиск, разбор модов, разбор
  крашей), используют `--jobs` потоков; остальные идут по одной (`warn: JOBS`).
- **Продолжение.** Каждая запись дописывается в файл результатов сразу, поэтому прерванный пакет не теряет
  работу; `--resume` пропускает входы, чей ключ (sha256 операции и её аргументов) уже имеет запись `ok`, и
  запускает упавшие и недостающие. В конце в файле одна строка на вход в порядке входов. `--max-fail N`
  останавливает раньше.
- **Детерминизм.** Входы сортируются; один и тот же пакет получает один и тот же файл результатов по умолчанию; в
  результатах и отчётах нет отметок времени.
- **Согласие.** Операции, которые ИИ-ассистенту запускать нельзя (меняют настройку пользователя или удаляют
  данные, блокируют, работают только из командной строки), внутри пакета или рецепта выполняются только с `--yes`,
  набранным в командной строке; через MCP пакет отклоняется (`CONSENT_REQUIRED`/`UNSUPPORTED`). Рецепт проверяет
  все шаги до запуска первого. Сам MCP-сервер, `view mock` и `dev gate` в пакете не запускаются никогда.

## Ограничения и известные проблемы

- Glob не раскрывает zip-моды: сначала распакуйте их (`mod inspect`/`mod check` читают zip напрямую).
- Пакет принимает до 100 000 входов; SQL-входы читаются из индекса по 500 строк.
- Пакеты и рецепты вкладываются не глубже четырёх уровней; рецепт, запускающий сам себя, отклоняется.

## Python API

```python
from satk.batch.runner import run_batch
from satk.batch.recipe import run_recipe

summary = run_batch("asset.lint", "mods/*.dff", arg=["fail_on=error"], jobs=4)
report = run_recipe("check-mod-before-release", var=["mod=mods/mycar"])
```
