# Наблюдаемость: проверка, чтение, сводки и преобразование трассировок движка и результатов бенча

[English version](../en/obs.md)

Пакет: `satk.obs`. Описание форматов: [obs-spec.md](obs-spec.md).

## Что это

Клиент, сервер и боты sa-engine записывают измерения в одно семейство форматов: потоки JSONL с записями кадров,
тиков, статистики и сети, документы JSON бенча и контейнеры `.saenet` / `.saerec`. `satk obs` — набор инструментов
для них: проверяет файл по спецификации, выводит и фильтрует записи, сворачивает трассировку в несколько строк,
преобразует файлы результатов `satk ingame bench` и трассировки в общую схему бенча, сливает трассировки нескольких
узлов по общему ключу времени и упаковывает потоки в контейнеры. Читает любой переданный файл, а пишет только в
`<workspace>\work\out\obs\`. Игра и работающий движок не нужны.

## Быстрый пример

```powershell
satk obs sample
satk obs validate sample/client-fixed.jsonl
satk obs summarize sample/client-fixed.jsonl
satk obs read sample/client-fixed.jsonl --rec frame --fields frame_ms ticks fence_ms.F5 --limit 5
satk obs convert sample/session.saerec --scene S0 --label rec
satk obs merge sample/client-fixed.jsonl sample/server.jsonl --out merged-demo.jsonl
satk obs validate sample/session.saerec
```

Ответ `validate` для исправного файла (сокращённо):

```json
{"ok":true,"cols":["where","severity","code","message"],"rows":[],"n":0,"total":0,"verdict":"pass","errors":0,"warnings":0,"kind":"jsonl","schema":"sae-obs/1","records":368}
```

Относительное имя, которое не является файлом, ищется в `work\out\obs`, куда `obs sample` пишет примеры. Файл с
ошибками — тоже успешный ответ: `verdict` равен `FAIL`, а строки таблицы говорят, где и почему.

## Команды

| Команда | MCP | Назначение |
|---|---|---|
| `satk obs validate <path> [--kind auto] [--strict] [--deep false]` | `satk_op` → `obs.validate` | проверить поток, JSON бенча (`sae-bench/1` или прежний `satk-bench/1`) или контейнер; таблица проблем и вердикт |
| `satk obs read <path> [--rec ...] [--t-from US --t-to US] [--tick-from T --tick-to T] [--node N] [--fields ...] [--chunks] [--chunk N] [--out FILE]` | то же | записи потока или контейнера, стадии бенча или таблица чанков; постранично; `--out` пишет выборку в JSONL |
| `satk obs summarize <path> [--node N]` | то же | статистика времени кадра, тики, потерянное время, длительности фенсов, счётчики, события и последний снимок движка по узлам; для бенча таблица стадий |
| `satk obs convert <src> [--out F] [--scene S] [--label L] [--skip-s N] [--until-s N]` | то же | записать `sae-bench/1` из результата `satk-bench/1` (без потерь) или из записей кадров потока или контейнера |
| `satk obs merge <paths...> [--out F]` | то же | слить потоки нескольких узлов в один JSONL, упорядоченный по ключу времени |
| `satk obs pack <src> <out>` / `satk obs unpack <src>` | то же | поток JSONL в `.saenet` / `.saerec` (вид по расширению, zlib по умолчанию) и обратно |
| `satk obs schema [name] [--definition D]` | то же | список схем JSON Schema (`common`, `record`, `bench`, `layout`) или одна схема либо одно определение |
| `satk obs sample [--dir sample]` | то же | записать детерминированные примеры файлов |

Агенты вызывают все операции через `satk_ops` и `satk_op`.

## Как это работает

- **Схемы.** В `data/obs` лежат схемы JSON Schema для записей и документа бенча и побайтовое описание контейнера.
  satk проверяет встроенным небольшим валидатором для той части JSON Schema, которую используют схемы (те же
  файлы читает любой внешний валидатор); тесты проверяют, что результаты совпадают.
- **Правила потока**, которые схемой не выразить, проверяются за один проход по файлу: порядок ключей, сетка тиков из
  заголовка, согласованность режима симуляции, накопительные счётчики и числа в завершающей записи.
- **Контейнеры** проверяются на трёх глубинах: заголовок и таблица чанков (`--deep false`), каждый чанк (CRC,
  размер, кодек) и записи внутри. Контейнер, который писатель не успел закрыть, читается сканированием с начала и
  сопровождается предупреждением.
- **Числа бенча.** Арифметика статистики кадров та же, что в `satk ingame bench`; преобразованный результат даёт те
  же вердикты `bench-compare`, что и исходный.
- **Детерминизм.** `obs sample`, `pack` и `merge` пишут одни и те же байты для одних и тех же входных данных (сжатые чанки
  зависят от сборки zlib).

## Ограничения и известные проблемы

- `satk ingame bench` по-прежнему пишет `satk-bench/1`; чтобы получить `sae-bench/1`, примените к его файлам `satk obs convert`.
- Содержимое записей `net`, `input` и `snap` минимально: пакеты сетевой трассировки и воспроизведения дополняют его
  новыми необязательными полями.
- Кодек 2 (zstd) в контейнере зарезервирован; чанки с ним перечисляются, но не проверяются.
- Чтение очень большой трассировки — один проход на Python: около миллиона записей за несколько секунд. `summarize` и
  `convert` держат записи кадров в памяти (около 1 КБ на кадр), `validate` читает потоком.

## Python-API (если другие пакеты его используют)

```python
from satk.obs import schemas, stream, bench, container, key, sample
report = stream.check_file("trace.jsonl")          # Report: problems, by_code, kinds, nodes
doc = bench.convert_satk_bench(old_result)         # satk-bench/1 dict -> sae-bench/1 dict
with container.Writer("t.saerec", "SREC", dt_s_us=33333) as w:
    w.meta(header_record)
    w.add(record)
```
