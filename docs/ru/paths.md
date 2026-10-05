# `satk paths`: пути машин, лодок и пешеходов (NODES*.DAT)

[English version](../en/paths.md)

<!-- Сверено 2026-10-05: быстрый пример выполнен на профиле vanilla; ответы ниже настоящие,
     сокращены. -->

Пакет: `satk.paths` поверх вендоренного ядра gta-flow (`vendor/gtaflow`, MIT).

## Что это

Сеть путей San Andreas — 64 региона `nodes0.dat` … `nodes63.dat` внутри `models\gta3.img`: узлы машин, лодок и
пешеходов, навигационные узлы (navi: полосы и направление) и связи между узлами. `satk paths` читает их из
IMG-архивов профиля **только на чтение**, складывает в отдельную базу `work\index\paths-<profile>.sqlite` (индекс
ассетов не трогается) и отвечает на вопросы «какие пути рядом с точкой» и «куда ведёт этот узел». `export` пишет
JSON-оверлей района для вьювера, Blender и графиков; `compile` пересобирает регионы компилятором gta-flow и
доказывает, что неизменённая сеть получается **байт в байт** такой же, а правка узла меняет только его регион.
Всё пишется под `work\`: база — в `work\index\`, файлы — в `work\out\paths\<profile>\`.

## Быстрый пример

```powershell
satk paths import
satk paths near 2495 -1687
satk paths node 15:6
satk paths export --area 2400,-1750,2600,-1600 --kind car
satk paths compile
```

Что вернётся (сокращённо):

```json
{"ok":true,"profile":"vanilla","db":"<workspace>/work/index/paths-vanilla.sqlite","reused":false,"areas":64,"nodes":68237,"vehicle":30587,"car":28991,"boat":1596,"ped":37650,"navis":31466,"links":143622,"sources":{"models/gta3.img":64},"check":"valid: 0 errors, 4 warnings","network_sha256":"7a0b8988…","seconds":0.94}
{"ok":true,"cols":["node","kind","pos","d","width","flood","links"],"rows":[["15:668","ped",[2496.0,-1686.12,12.38],1.33,0.25,5,["15:667","15:669","15:671"]],["…"]],"n":20,"total":43}
{"ok":true,"id":"15:6","kind":"car","pos":[2502.0,-1669.75,13.0],"width":0.0,"flood":1,"spawn":15,"behaviour":0,"flags":["switched_off","switched_off_orig","not_highway"],"links":[{"to":"15:5","dist":4,"navi":"15:17","lanes":[0,1]},{"to":"15:7","dist":6,"navi":"15:19","lanes":[1,0]}],"navis":["15:19"],"region":15}
{"ok":true,"path":"<workspace>/work/out/paths/vanilla/car_2400_-1750_2600_-1600.json","nodes":64,"segments":74,"navis":74,"bytes":27709}
{"ok":true,"profile":"vanilla","areas":64,"identical":64,"byte_identical":true,"oracle":"valid: 0 errors, 4 warnings","out":"<workspace>/work/out/paths/vanilla/compiled","written":0,"seconds":5.6}
```

## Команды

| Команда | MCP | Что делает |
|---|---|---|
| `satk paths import [--profile vanilla] [--force]` | — | 64 региона из IMG профиля в `work\index\paths-<profile>.sqlite`; повтор при неизменных архивах отвечает `reused: true` за миллисекунды |
| `satk paths near X Y [--r 30] [--z Z] [--kind all\|vehicle\|car\|boat\|ped] [--limit 20]` | — | узлы в радиусе, ближние первыми: адрес `area:idx`, тип, позиция, расстояние, ширина, `flood`, связанные узлы |
| `satk paths node AREA:IDX` | — | один узел: флаги, `spawn`/`behaviour`, связи с длиной, navi и полосами в обе стороны, navi, прикреплённые к нему |
| `satk paths export [--area minx,miny,maxx,maxy] [--kind ...] [--name N] [--no-navis]` | — | JSON-оверлей `satk.paths.overlay/1` в `work\out\paths\<profile>\<name>.json` |
| `satk paths compile [--edits JSON\|@file] [--write changed\|all\|none] [--name compiled]` | — | пересборка через gta-flow, сверка с исходными байтами и с независимым оракулом; `manifest.json` и регионы в `work\out\paths\<profile>\<name>\` |

Параметры и ответы:

- **Адрес узла** `15:6` = регион 15, индекс 6 в нём (как в файле). Регионы — сетка 8×8 квадратов по 750 единиц
  от точки (−3000, −3000); узел, перенесённый в другой квадрат, при `compile` меняет регион.
- **Типы:** `car` — машины, `boat` — узлы машин с флагом воды, `ped` — пешеходы; `vehicle` = `car` + `boat`.
- **Полосы** `lanes: [a→b, b→a]` в `node` и в отрезках оверлея: число полос в каждую сторону по navi этого
  отрезка (`[1,0]` — одностороннее движение). `flood` — номер связной компоненты: машина из компоненты 1 не
  доедет до узла компоненты 2.
- `near`: `--z` переключает на трёхмерное расстояние; если ничего не нашлось — `warn: EMPTY`. Первый вызов любой
  команды без базы импортирует её сам (`warn: IMPORTED`); если IMG-архивы поменялись после импорта —
  `warn: INDEX_STALE` и подсказка `satk paths import`.
- `export --area`: четыре числа через запятую. Если первое отрицательное, пишите через `=`:
  `--area=-100,-200,100,200`. Без `--area` экспортируется вся карта (около 20 МБ, около 3,5 с). В файле: `nodes`
  (точные координаты), `segments` (оба конца, их позиции, длина, navi, полосы, байт перекрёстка `inter`) и `navis`.
- `compile --edits`: JSON-объект `{"area:idx": [x, y, z]}` (сдвиг узла; `[x, y]` сохраняет высоту) или
  `{"area:idx": {"pos": [...], "width": 2.0, "spawn": 0..15, "behaviour": 0..15}}`. В PowerShell JSON удобнее
  положить в файл и передать `--edits @edits.json`. `--write changed` (по умолчанию) пишет только изменённые
  регионы, `all` — все 64, `none` — только `manifest.json`. Старые `nodes*.dat` в папке `<name>` перед записью
  удаляются.

Пример правки (узел 15:6 сдвинут на 2 единицы по X): регион 15 меняется, остальные 63 совпадают байт в
байт, оракул ошибок не находит:

<!-- docs-smoke: skip JSON в кавычках зависит от оболочки -->
```powershell
satk paths compile --edits '{"15:6":[2500,-1669.75,13]}' --name moved
```

Получившийся `nodes15.dat` заменяет одноимённый файл из `gta3.img` (например, как файл мода для Mod Loader).
Игру satk не меняет.

## Как это устроено

- **Источник.** Архивы профиля берутся в порядке регистрации движком (`satk.formats.layout.archives`,
  пространство имён `main`): выигрывает первый архив, где есть `nodes<N>.dat`. В ванили все 64 региона лежат в
  `models\gta3.img`; копии в `data\Paths\` движок не читает, и satk их тоже не трогает.
- **Разбор** — вендоренный `codec` gta-flow: без потерь, вместе с резервными секциями и хвостом-выравниванием по
  сектору IMG. При импорте байты каждого региона проверяет независимый оракул gta-flow (в ванили — 0 ошибок и 4
  известных предупреждения `exceptional attached navi` в регионах 6 и 14).
- **База** (`PRAGMA user_version` = 1): таблицы `node`, `navi` и `link` с разобранными полями и `area` с исходными
  байтами регионов; `compile` собирает из них. Отпечаток архивов (размер и mtime) решает, нужен ли повторный
  импорт. Запросы `near`/`node` занимают миллисекунды; импорт ванили — около 1 с, `compile` — 5–7 с.
- **Компиляция** идёт тем же путём, что в gta-flow: `document.import_files` → правки (`document.apply`) →
  `compiler.compile_document` → `oracle.check`. Каждый регион сравнивается с исходником; `manifest.json`
  перечисляет `identical`/`changed` и суммы sha256. Всё детерминировано: одинаковый вход даёт одинаковые файлы.
- **Вендоринг.** `vendor/gtaflow` — ядро gta-flow ревизии `c3d9ad4` без изменений (`LICENSE` и `NOTICE.md`
  автора сохранены, хэши — в [`VENDORED.md`](../../vendor/gtaflow/VENDORED.md)). `satk.paths.vendor` загружает
  его под именем `satk_vendor_gtaflow`, не трогая `sys.path`.

## Ограничения и известные проблемы

- Только формат ПК «compact» (как у gta-flow): координаты в пределах ±4096, до 1024 navi на регион, 64 региона
  карты. Интерьерные регионы движок строит сам, в файлах их нет.
- Правки — только существующих узлов (позиция, ширина, `spawn`, `behaviour`). Новые дороги, перекрёстки и
  светофоры gta-flow умеет делать, но через `satk paths` это пока не выведено — см. документацию gta-flow.
- Слои модов (Mod Loader) не учитываются: читаются только IMG-архивы из DAT-файлов профиля.
- В игре не проверено: `compile` доказывает формат и топологию, а не то, что машины поедут как надо.
- `vendor/gtaflow` есть только в клоне репозитория и в переносимом архиве; установка через `pip` и клон без этой
  папки отвечают `DEPENDENCY`.

## Python-API

```python
from satk.paths.db import import_profile, open_paths
from satk.paths.build import compile_network, export_overlay

db, warnings = open_paths("vanilla")
with db:
    row = db.node(15, 6)            # sqlite3.Row: x, y, z, kind, flags ...
res = compile_network("vanilla", edits={"15:6": [2500, -1669.75, 13]}, name="moved")
```
