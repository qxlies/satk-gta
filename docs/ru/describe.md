# `satk describe`: описания моделей из gta-scout

[English version](../en/describe.md)

Пакет: `satk.describe`. Данные: пакет описаний gta-scout (MIT, Dryxio), notice —
[`data/notices/gta-scout.txt`](../../data/notices/gta-scout.txt).

## Что это

gta-scout публикует 7 815 текстовых описаний моделей и текстур SA (пакет `sa-ГГГГ-ММ-ДД.json` в его клоне), которые
написали и проверили ИИ-агенты по рендерам. `satk describe import` привязывает описания моделей к моделям профиля
**только по содержимому**: SHA-256 DFF, который модель загружает в этом профиле, должен совпасть с хэшем из пакета.
Привязанные описания ложатся в заметки (`work\notes.sqlite`, автор `import:gta-scout`, тег `desc`), и их сразу
находят `asset find … --kind note` и `note list model:<id>`. Игра и пакет только читаются; повторный импорт ничего не
меняет.

## Быстрый пример

```powershell
satk describe status
satk describe import --dry-run
satk asset find bench --kind note
```

Что вернётся (сокращённо; `find` — после настоящего `satk describe import`):

```json
{"ok":true,"pack":{"file":"<workspace>/src/gta-scout/data/annotations/sa-2026-10-03.json","version":"sa-2026-10-03","license":"MIT","entries":7815,"models_with_dff":2291,"digest":"ok","skipped":{"no_dff_source":425,"texture":5099}},"imported":2131,"models":2131}
{"ok":true,"profile":"vanilla","models_in_profile":14790,"dff_hashed":14483,"entries_bound":2131,"entries_unmatched":160,"bound":2131,"models":2131,"hash_only":0,"notes":2131,"added":0,"unchanged":2131,"replaced":0,"pruned":0,"kept_unbound":0,"dry_run":true,"sample":[["model:322","Elongated bronze cylinder with a rounded conical tip and black ringed base; pro…"]],"seconds":0.54}
{"ok":true,"cols":["id","kind","name","info"],"rows":[["note:1120","note","model:4085","Two simple backless benches with long beige-grey seats on two short legs."],["note:271","note","model:1364","Dark curved urban bench integrated between two round planters, vegetation above…"]],"n":15,"total":15,"next":null}
```

Сам импорт (пишет в `work\notes.sqlite`, около 2 с; повтор — `added: 0, unchanged: 2131` примерно за 0,6 с):

<!-- docs-smoke: skip пишет в общую базу заметок -->
```powershell
satk describe import
satk note list model:4085
```

## Команды

| Команда | MCP | Что делает |
|---|---|---|
| `satk describe import [--profile P] [--pack FILE\|DIR] [--text en\|full] [--prune] [--dry-run] [--no-verify] [--sample N]` | — | хэширует DFF профиля, привязывает записи пакета, синхронизирует заметки; ответ — счётчики и `sample` |
| `satk describe status [--pack FILE\|DIR]` | — | какой пакет найден (версия, записи, контрольная сумма) и сколько описаний уже импортировано |
| `satk describe clear [--dry-run]` | — | удалить все импортированные описания (только автор `import:gta-scout` с тегом `desc`) |

Агент вызывает `import` и `status` через `satk_ops`/`satk_op`; на `clear` он получает `CONSENT_REQUIRED` (команда
удаляет заметки — спросить пользователя). Найти и прочитать описания — существующими командами:
`satk asset find bench --kind note` (MCP `asset_find(query, kind="note")`), `satk note list model:4085`,
`satk note list --tag desc`.

Поля ответа `import`:

- `models_in_profile` — активные модели профиля с DFF; `dff_hashed` — сколько разных DFF прочитано (только те, чей
  размер есть в пакете);
- `entries_bound` / `entries_unmatched` — записи пакета с DFF, нашедшие и не нашедшие свой DFF; `bound` — пар
  «запись → модель», `models` — разных моделей, `hash_only` — пар, где имя модели у издателя другое (тот же DFF под
  другим именем, например у моделей SA-MP);
- `added` / `unchanged` / `replaced` / `pruned` / `kept_unbound` — что стало с заметками (см. ниже).

## Как это устроено

- **Пакет.** По умолчанию — самый новый `sa-*.json` в `<workspace>\src\gta-scout\data\annotations` (`paths.src` из
  конфигурации), иначе `--pack`. Проверяются формат `gta-scout-annotations-v1`, лицензия `MIT` и контрольная сумма
  пакета (канонический JSON → SHA-256, как считает gta-scout); правленому вручную пакету нужен `--no-verify`. Битые
  записи пропускаются и считаются в `skipped`.
- **Привязка.** Берутся записи `kind: model` с источником `.dff` (2 291 в `sa-2026-10-03`). Для каждой активной
  модели профиля её DFF читается целиком, как запись каталога IMG (`blob.size` байт с `blob.abs_off`, кратно 2048 —
  ровно то, что хэшировал gta-scout), и SHA-256 сравнивается с пакетом. Имена не учитываются: изменённый DFF не
  привяжется, такой же DFF под другим именем — привяжется. Записи текстур (5 099) не импортируются: в пакете только
  хэши TXD, а TXD издателя отличаются от стоковых 1.0.
- **SID.** `model:<id>`; если выигравшее определение из не-ванильного слоя перекрывает другое (скины SA-MP на ID
  300+), — `model:<name>`, чтобы заметка не попала на ванильную модель с тем же ID: заметки общие для всех профилей.
- **Заметка.** Текст — английская часть `FR: … EN: …` (`--text full` оставляет оригинал целиком; `lang` — `en` или
  `mul`), теги `desc gta-scout` плюс теги издателя, `confidence` издателя, `evidence` =
  `gta-scout:<версия>#<id записи> <архив>/<файл>.dff sha256:<хэш>`, дата — дата пакета (экспорт детерминирован).
  Новые заметки пишутся одной транзакцией через `satk.notes.db.import_jsonl`.
- **Идемпотентность.** Меняются только заметки автора `import:gta-scout` с тегом `desc`. Та же пара «модель + текст»
  с теми же тегами и уверенностью — `unchanged`; новый текст или новые теги для привязанной модели заменяют старую
  заметку (`replaced` + `added`); заметки моделей, которые больше не привязаны, остаются (`kept_unbound`), пока не
  указан `--prune`.
- **Скорость.** Ваниль: 14 483 DFF хэшируются примерно за 0,5 с (из кэша ОС), первый импорт — около 2 с, повтор —
  около 0,6 с. Профиль `samp` даёт 2 250 привязок (126 из них — модели SA-MP).

## Ограничения и известные проблемы

- Описания — наблюдения издателя по рендерам (ИИ-агенты, `confidence` 0,6–1), а не проверенные факты; 136 текстов
  из 2 131 на ванили (около 6 %) написаны как «FR / EN» внутри фраз, без пометки `EN:`, и хранятся целиком с
  `lang: mul`.
- Нужны индекс профиля (`satk index build`) и клон gta-scout в `paths.src`; без клона `status` отвечает
  предупреждением, а `import` — `NOT_FOUND` с подсказкой.
- `replaced` и `clear` удаляют заметки по одной (около 15–20 мс на заметку, пакетного удаления у `satk.notes` нет):
  смена `--text` на ванили заменяет 1 333 заметки примерно за 30 с, `clear` занимает около 35 с.
- `satk note export` выгружает импортированные описания вместе с остальными заметками; их можно восстановить
  повторным импортом, notice — `data/notices/gta-scout.txt`.

## Python-API

```python
from satk.index.api import open_index
from satk.describe.pack import find_pack, load_pack
from satk.describe.bind import bind
from satk.describe.store import records, sync

pack = load_pack(find_pack(None))
b = bind(pack, open_index("vanilla"))       # b.stats, b.descs[i].sid / .entry / .use
res = sync(records(b.descs, pack), dry_run=True)
```
