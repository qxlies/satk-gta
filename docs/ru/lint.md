# `satk asset lint` — линтер ассетов

[English version](../en/lint.md)

Пакет: `satk.lint`. Правила — данные: `data/lint_rules.json`.

## Что это

Проверка модов и ванили до запуска игры: DFF (структура, лимиты, UV, prelight, нормали, данные вершин,
бюджеты), TXD (платформа, формат, степени двойки, блоки DXT, цепочка мипов, имена), COL (имя ≤ 21 и равно
модели, вершины в ±256 м, боксы без вращения, флаги, границы, поверхности), IDE (длины имён, диапазон ID,
дальность прорисовки, дубли) и связка IDE ↔ DFF/TXD ↔ COL по именам. Каждое правило ссылается на источник
(`gta-reversed` файл:строка, plugin-sdk, отчёт исследования). Линтер только читает файлы; пишет лишь по
`--save` — в `work\out\lint\` (создаётся при первом сохранении). <!-- linkcheck: ignore -->

## Быстрый пример

```powershell
satk asset lint-rules --rule col
satk asset lint models/gta3.img/infernus.dff
satk asset lint model:17613 --sev info --limit 5
satk asset lint data/maps/generic/dynamic2.ide
```

Что вернётся (сокращённо, последняя команда — настоящая ошибка в данных игры):

```json
{"ok":true,"cols":["rule","sev","file","msg"],
 "rows":[["ide.draw_min","error","data/maps/generic/dynamic2.ide","line 164: model 1489 'DYN_SALE_POST': draw distance 1 < 4, the game re-reads the line in the old mesh-count form"]],
 "n":1,"total":1,"summary":{"fatal":0,"error":1,"warn":0,"info":0},"files":1,"by_rule":{"ide.draw_min":1}}
```

Вся чистая копия игры (`satk asset lint "<workspace>\gta-sa-clean"`, ≈12 с, 19 711 файлов): 0 fatal, 1 error
(та же строка), 464 warn (352 — текстуры, которых нет в цепочке TXD; 59 — модели карты без prelight и нормалей).

## Команды

| Команда | MCP | Что делает |
|---|---|---|
| `satk asset lint TARGET [--sev info\|warn\|error\|fatal] [--rule P …] [--preset game\|strict] [--config F] [--no-index] [--fail-on S] [--save] [--profile P] [--limit N] [--cursor C]` | — (через `satk_op`) | таблица `rule/sev/file/msg` и `summary` по всем находкам; строки — не ниже `--sev` (по умолчанию `warn`) |
| `satk asset lint-rules [--rule P …] [--preset P] [--config F] [--ru] [--ref] [--limit N]` | — | правила: id, серьёзность, параметры, что проверяет, источник (`--ref`) |

`TARGET` — что угодно из этого:

- файл `.dff .txd .col .ide`, IMG-архив (все DFF/TXD/COL внутри), запись `<img>/<entry>`;
- папка мода (рекурсивно, IMG внутри раскрываются). Корень игры (есть `data/gta.dat`) проверяется так, как его
  грузит игра: только IDE из `data/default.dat` и `data/gta.dat`, отдельные `.col` — только из `COLFILE`;
- SID: `model:411` (DFF, цепочка TXD и COL модели), `dff:`, `txd:`, `col:` (одна модель коллизии), `file:`, `ide:`.

Относительный путь ищется от текущей папки, потом от корня профиля (регистр не важен). Имена, которых нет в
цели (мод берёт ванильный TXD или добавляет коллизию ванильной модели), линтер ищет в индексе профиля;
`--no-index` это выключает. SID-цели индекс требуют всегда (`INDEX_MISSING` → `satk index build`).

Серьёзность: `fatal` — игра падает или не может прочитать файл; `error` — грузится, но неправильно (нет
коллизии, мусор в текстуре, модель не видна); `warn` — работает, но нарушает бюджет или соглашение; `info` —
совет. `--fail-on error` превращает находки этого уровня и выше в ошибку `CHECK_FAILED` (код выхода 1) — для
скриптов и CI; значение по умолчанию `never` всегда возвращает `ok`.

Проверить мод для Mod Loader целиком (коллизии ID, файлы, которые Mod Loader пропустит, выпавшие записи игры)
можно командой `satk mod check` — она добавляет и находки линтера: см. [modinspect.md](modinspect.md).

## Как это устроено

- Проверки — модули `dff`, `txd`, `col`, `ide`, `link` пакета `satk.lint`; парсеры — `satk.formats`.
  Код говорит только «сработало правило X с такими полями», а серьёзность, пороги и текст сообщения берутся из
  `data/lint_rules.json`. Свои пороги — файлом `--config my.json`:
  `{"rules": {"txd.size_max": {"params": {"max": 512}}, "col.empty": {"enabled": false}}}`.
- Пресеты: `game` откалиброван на ванили (0 fatal, ложных срабатываний нет); `strict` — для нового контента:
  бюджеты p90 ванили по классам, текстуры ≤ 512, мипмапы и ночные цвета — `warn`,
  коллизия обязательна.
- Класс модели (для prelight, нормалей и бюджетов) берётся из IDE: `objs`/`tobj` → `map` (или `lod`, если в
  имени есть `lod` либо дальность > 300), `cars`, `peds`, `weap`, остальное — `other`. Одиночный файл без IDE и
  индекса угадывается: со скином — `peds`, со встроенной коллизией — `cars`, иначе `map`.
- Связка: модель IDE → `<name>.dff`, TXD из IDE → `txdp`-родители → `vehicle` для машин; COL-модель с тем же
  именем (≤ 21 символа) для объектов карты (кроме LOD); текстуры материалов ищутся по всей цепочке. Сироты
  (DFF, TXD, COL без модели) — `info`/`warn`.
- Боксы в COL — это AABB по формату (`CColBox = {min, max}`), вращение в файле не хранится. Повёрнутый или
  отражённый бокс, экспортированный как AABB, ловится как `min > max` (`col.box_inverted`) или как примитив
  вне границ модели (`col.outside_bounds`).
- Детерминизм: одинаковый вход — одинаковый ответ (сортировка: серьёзность, файл, правило). `--save` пишет все
  находки в `work\out\lint\<цель>-<хэш>.json`, имя зависит только от цели, пресета и правил. <!-- linkcheck: ignore -->

## Ограничения и известные проблемы

- Лимиты пулов (`limit_def`, число моделей, TXD- и COL-слотов) и конфликты ID с SA-MP пока не проверяются.
- Ванильные «заглушки» (`special01…`, `cutobj*`, `clothes*`, `null`, `airtrain_vlo`) не считаются потерянными
  моделями — список в параметрах `link.dff_missing`.
- Без индекса и без IDE в цели проверки связки пропускаются (линтер не знает, какие модели есть в игре).

## Python-API (если другие пакеты его используют)

```python
from satk.lint.runner import lint
rep = lint("models/gta3.img", preset="strict", only=["col", "dff.uv"])
print(rep.summary, [f.row() for f in rep.at_least("error")])
```
