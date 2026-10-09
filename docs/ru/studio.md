# Студия: живая сессия Blender для пошагового моделирования

[English version](../en/studio.md)

Пакет: `satk.studio` (MIT) и `blender/satk_blender/studio` (GPL-3.0-or-later).

## Что это

ИИ-ассистент моделирует прямо в Blender, по одному шагу, вместо того чтобы вписывать координаты в скрипт. Сессия —
это Blender 5.1 без окна, который остаётся открытым; каждый шаг — один *метод* (`mesh.loft`, `modifier.add`,
`ref.plane`, ...), и ответ приходит за миллисекунды с нужными числами: что изменилось, треугольники и вершины,
размеры, дефекты сборки изменённых объектов (висящие в воздухе или утопленные детали, сквозные арки, ...) и, по
запросу, маленький снимок JPEG. Одни и те же методы годятся для любого вида ассета: машин, пропов, зданий,
интерьеров, оружия, педов, пикапов и тюнинга. *Проект ассета* позволяет продолжить работу после перерыва: журнал,
контрольные точки, снимки, фото-референсы и решения лежат в одной папке в `<workspace>\work\assets\`. Игра и ваш
собственный профиль Blender не трогаются.

## Быстрый пример

```powershell
satk asset init docs-bench --kind prop --dims 1.8,0.6,0.9 --force
satk blender session start --project docs-bench --no-resume
satk blender call scene.clear --session docs-bench
satk blender call mesh.primitive --session docs-bench --params '{"kind":"cube","size":[1.6,0.12,0.04],"name":"slat"}'
satk blender call modifier.add --session docs-bench --params '{"object":"slat","type":"ARRAY","settings":{"count":4,"relative":[0,1.25,0]}}' --snapshot sheet
satk asset status docs-bench
satk blender session stop --name docs-bench
```

Что вернёт пятая команда (сокращённо):

```json
{"ok":true,"method":"modifier.add","n":4,"result":{"added":["slat:array"]},"changed":["slat"],
 "stats":{"scene":{"objects":1,"tris":48,"verts":32,"dims":[1.6,0.57,0.04],"vs_target":[0.889,0.95,0.044]},
          "objects":{"slat":{"tris":48,"verts":32,"dims":[1.6,0.57,0.04],"geo.pieces":4,"geo.open_edges":0}}},
 "snapshot":".../work/assets/docs-bench/snaps/0004-sheet.jpg","ms":61.3,"session":"docs-bench"}
```

## Команды

| Команда | MCP | Что делает |
|---|---|---|
| `satk asset init <name> --kind K [--intent replace\|add] [--like SID] [--tier vanilla\|sa_plus] [--target sp\|mta\|samp] [--dims W,L,H] [--detail simple\|standard\|hero\|none]` | — (`satk_op`) | создать проект ассета (`asset.json`; уровень по умолчанию — `sa_plus`) со стартовым инвентарём его вида (`<project>/design/inventory.json`, [inventory.md](inventory.md)) |
| `satk asset status <name> [--record JSON] [--sheet]` | — (`satk_op`) | карточка для продолжения работы (до 1 КБ); записать решение, этап, проблему или результат |
| `satk ref import <photo> [--project P] [--view side\|front\|rear\|top\|3q\|detail] [--max-px N] [--grid] [--grid-step N]` | — (`satk_op`) | фото-референс: поворот по EXIF применён, метаданные удалены, не больше 1600 px; вид хранится рядом (side, front, rear, top — настоящая проекция для `ref.plane`; 3q и detail только описываются, никогда не измеряются); копия с подписанной сеткой пикселей только с `--grid` |
| `satk ref board [--project P] [--images F …] [--labels L …] [--cols N]` | — (`satk_op`) | один подписанный JPEG (около 1 мегапикселя) из 1-12 фото-референсов (по умолчанию все референсы проекта) для раунда проверки |
| `satk blender session start [--name N] [--project P] [--blend F] [--gui] [--threads N] [--no-resume]` | — (`satk_op`) | запустить сессию (или взять уже запущенную) |
| `satk blender session status\|list\|stop\|restore\|replay\|prune [--name N] [--ref R] [--source S]` | — (`satk_op`) | состояние; загрузить контрольную точку; повторить журнал; уборка |
| `satk blender call <method> [--params JSON] [--params-file F] [--session N] [--snapshot VIEW\|sheet] [--look L] [--checkpoint\|--no-checkpoint] [--save F] [--timeout S]` | — (`satk_op`) | один шаг; `batch` выполняет список шагов; `--params-file` (`params_file` в `satk_op`) читает параметры из файла JSON |
| `satk blender methods [--query WORD]` | — (`satk_op`) | методы сессии; точное имя метода (`mesh.lathe`) даёт таблицу его параметров и полную справку |

Все методы с параметрами и значениями по умолчанию, по строке на метод: [studio-methods.md](../agent/studio-methods.md)
(создаётся из исходников плагинов командой `satk dev gen-docs`). Параметр, нужный только в части случаев, говорит
когда (`segments (required if kind not plane|grid|cube|ico_sphere)`, `distance (required if with points)`).

## Методы

| Группа | Методы |
|---|---|
| `scene.*` | `info`, `object`, `stats` (числа сейчас; `file` — все строки в файл), `clear`, `delete`, `rename`, `parent`, `transform` (`apply`), `duplicate` (`mirror` x/y/z), `join`, `empty`, `collection`, `origin`, `props`, `visible`, `tag` (id пунктов инвентаря на объектах или гранях) и `items` (их факты для `asset inventory`, [inventory.md](inventory.md)) |
| `mesh.*` | `primitive` (круглым формам нужен `segments`; `rounded_box` и `capsule` гладкие), `loft` (сечения вдоль оси: точки или скруглённая форма `shape`, `steps` между ними, `half` с модификатором MIRROR, `parts` для kit), `sweep` (профиль вдоль пути), `lathe`, `curve`, `convert`, `extrude`, `inset`, `transform` (мягкий `falloff`), `relax`, `deform` (изгиб, сужение, скрутка, cast, решётка), `attach` (прижать, сварить или соединить мостом деталь с основой), `flare` (колёсная арка с отбортовкой, загибом и подкрылком), `bevel`, `subdivide`, `delete`, `normals`, `loopcut` (разрезы в точных позициях), `bisect`, `symmetrize`, `bridge`, `merge`, `dissolve` (по умолчанию только плоское), `mark` (острые рёбра или швы по углу, границам материалов или UV), `group`, `info` |
| `modifier.*` | `add`, `set`, `apply`, `remove`, `list` для MIRROR, SUBSURF, BEVEL, SOLIDIFY, SHRINKWRAP, WEIGHTED_NORMAL, DECIMATE, TRIANGULATE, ARRAY, CURVE, WELD и SMOOTH_BY_ANGLE (разрешённые настройки, углы в градусах) |
| `material.*` | `create` (цвет 0-255, прозрачность, текстура; `preset` из набора kit), `assign` (весь объект или выбранные грани), `set`, `list` |
| `uv.*` | `unwrap` (smart, по швам, куб, цилиндр, сфера, плоская проекция), `fit` (в прямоугольник атласа), `layers` (вторая UV-карта), `texel` (px/m) |
| `camera.*`, `ref.*` | именованные камеры (`add` по виду или по `location` + `target`, `views`, `list`); `ref.plane` ставит за моделью НАСТОЯЩУЮ проекцию (`view` front, rear, side/left, right или top) и камеру `ref_cam_<view>` под неё, с масштабом по `length` (настоящая длина объекта: объект ищется по цвету краёв фото или задаётся `span` [x0, x1] в пикселях), по `width` (всё фото) или по двум точкам `points` и расстоянию `distance` между ними; `ref.list` |
| `kit.*`, `look.*` | плагины kit ([kit.md](kit.md)): `template`, `blank`, `blank_split`, `fill`, `wheel`, `shade`, `vlo`, `damage`, `col`, `export`, ...; плагины вида ([look.md](look.md)): `apply`, `restore`, `render` (ракурсы в выбранном виде; `colors` — цвета покраски), `preview`, `silhouette` (контур против настоящего фото сбоку, спереди или сзади: справка, никогда не порог) |
| `io.*`, `shade.basic`, `python` | импорт DFF; `io.ghost` приносит ванильную модель как образец, который не считается и не экспортируется; быстрое гладкое/плоское затенение; Python как запасной выход с записью в журнал |
| `session.*` | `checkpoint` (`tag`, например G1: не удаляется), `restore`, `checkpoints`, `prune`, `journal` |

Правки сетки принимают выбор граней `select`: `{"side": "+z"}`, `{"normal": [0, 0.7, 0.7], "within": 20}`,
`{"where": ["z>0.4", "y<-1.2"]}`, `{"box": [[x0, y0, z0], [x1, y1, z1]]}`, `{"group": "roof"}`,
`{"material": "glass"}`, `{"near": {"point": [x, y, z], "radius": 0.2}}` (центры граней в радиусе),
`{"loop": {"point": [x, y, z], "dir": "z"}}` (кольцо рёбер через ребро, ближайшее к точке; `"ring": true` берёт
полосу четырёхугольников поперёк него), `{"grow": 1}` (добавить соседние кольца граней), `{"linked": true}`
(затем целые сваренные куски), `{"item": "I05"}` (грани, помеченные пунктом инвентаря); ключи сочетаются и
работают в координатах объекта. Правки вершин (`mesh.transform`, `mesh.relax`) двигают только вершины самого
кольца. `save_group` даёт новым граням имя, и следующий шаг выбирает их по имени, а не по координатам. Машины
смотрят в +Y, Z вверх.

## Мягкие формы

Кузова строятся из задуманных скруглённых сечений и профилей, а не из коробок, которые двигают жёсткими плитами.

- **`mesh.loft` с формами.** Сечение — `{"at": y, "shape": {...}}`: `w` половина ширины, `h` высота, `z` низ, `exp`
  (или `exp_top`/`exp_bottom`) показатель суперэллипса (2 — круг, 3-5 — мягкая коробка), `mid` высота линии пояса в
  долях `h`, `crown` (м, выпуклая крыша), `tumble` (ширина крыши / ширина по поясу: остекление заваливается внутрь),
  `shoulder` (м, полка по линии пояса под стёклами), `flat_bottom`. Вершины уходят в углы (`spacing: turn`); линия
  пояса и обе осевые линии всегда вершины. `steps` добавляет кольца между сечениями по монотонному сплайну
  параметров формы (`interp: smooth`); сечение с `"interp": "linear"` делает промежуток после себя плоским (лобовое
  стекло). `half: true` строит только x >= 0 с модификатором MIRROR. `parts` пишет атрибут граней `satk_part`, по
  которому режет `kit.blank_split` (`angle`: 0 — грани смотрят вверх, 90 — вбок, 180 — вниз; `model` называет модель
  kit). Лофт из форм затенён гладко; лофт из точек `points` остаётся плоским без `smooth: true`, а `mesh.lathe`
  плоский до `shade.basic` или `kit.shade`.
- **`mesh.sweep`** ведёт профиль (точки или форма) вдоль пути (точки или объект-кривая): бамперы от арки до арки
  (`half: true` начинает путь на плоскости зеркала), молдинги, пороги, рейлинги, трубы; `scale` и `twist`
  меняются вдоль пути.
- **`mesh.transform` + `falloff`** мягко изгибает поверхность вокруг выбора (`radius`, `curve`, `connected`);
  `mesh.relax` разглаживает бугры; `mesh.deform` гнёт, сужает, скручивает или деформирует решёткой.
- **`mesh.attach`** ставит деталь на основу: `snap` (вершины касания на поверхность; `rigid` сначала сдвигает всю
  деталь), `weld` (влить деталь в `to` и слить открытые края: две оболочки становятся одной), `bridge` (влить её в
  `to` и заполнить щель между открытыми краями); после `weld` и `bridge` объекта детали больше нет. Код, который сам
  объединяет сетки, может вызвать `weld_boundaries(obj, dist)` из `satk_blender.studio.methods.attach`.
- **`mesh.flare`** вырезает круглую колёсную арку в боковых гранях (`arch` {`center`, `radius`} — тогда нужен
  `segments` по верху — или `select` = проём), поднимает вваренную отбортовку (`width`, `out`, `chamfer`;
  `out: 0` — простой вырез седана), загибает край внутрь (`lip` — глубина загиба) и закрывает колёсную нишу
  подкрылком (`depth`, `liner_material`).

Половина кузова машины (сокращённо: сечения у кормы, крыши и носа; арка; бампер; зеркало):

```json
{"steps": [
 {"method": "mesh.loft", "params": {"name": "body", "samples": 32, "half": true, "steps": 2, "sections": [
   {"at": -2.6, "shape": {"w": 0.92, "h": 0.56, "z": 0.34, "exp": 3.5, "mid": 0.75}},
   {"at": -1.55, "interp": "linear", "shape": {"w": 1.03, "h": 0.74, "z": 0.28, "exp_top": 4, "exp_bottom": 4.5, "mid": 0.91, "crown": 0.03, "flat_bottom": true}},
   {"at": -0.95, "shape": {"w": 1.03, "h": 1.22, "z": 0.28, "exp_top": 5, "exp_bottom": 4.5, "mid": 0.565, "crown": 0.06, "tumble": 0.78, "shoulder": 0.05, "flat_bottom": true}},
   {"at": 0.25, "interp": "linear", "shape": {"w": 1.03, "h": 1.22, "z": 0.28, "exp_top": 5, "exp_bottom": 4.5, "mid": 0.565, "crown": 0.06, "tumble": 0.78, "shoulder": 0.05, "flat_bottom": true}},
   {"at": 0.95, "shape": {"w": 1.03, "h": 0.75, "z": 0.28, "exp_top": 4, "exp_bottom": 4.5, "mid": 0.9, "crown": 0.035, "flat_bottom": true}},
   {"at": 2.62, "shape": {"w": 0.91, "h": 0.52, "z": 0.34, "exp": 3.5, "mid": 0.72}}]}},
 {"method": "mesh.flare", "params": {"object": "body", "arch": {"center": [1.0, 1.65, 0.35], "radius": 0.44},
   "segments": 7, "width": 0.05, "out": 0.02, "liner_material": "liner"}},
 {"method": "mesh.sweep", "params": {"name": "bumper", "half": true, "samples": 16, "profile": {"w": 0.09, "h": 0.2, "exp": 3},
   "path": [[0, 2.66, 0.42], [0.72, 2.62, 0.42], [0.93, 2.32, 0.42], [0.95, 2.13, 0.42]]}},
 {"method": "mesh.primitive", "params": {"kind": "rounded_box", "name": "mirror", "size": [0.14, 0.05, 0.04],
   "radius": 0.015, "segments": 2, "location": [1.13, 0.8, 1.03]}},
 {"method": "mesh.attach", "params": {"object": "mirror", "to": "body", "select": {"where": ["x<-0.06"]}, "rigid": true,
   "max_dist": 0.12}}]}
```

Сохраните это в `car.json` и выполните `satk blender call batch --params-file car.json --snapshot 3q`.

## Снимки

`--snapshot 3q|front|rear|left|right|top`, имя камеры или `sheet` (лист 2x2 из `3q`, `front`, `left`, `top`);
объект JSON (или `--look`) добавляет `look` (по умолчанию `clay` с тонкой сеткой рёбер, `raw` — материалы и
текстуры, `wire` и `game` — игровой вид SA из [look.md](look.md) с землёй и полуденным светом; видно то, что
игра рисует вблизи, то есть без деталей повреждений, низких LOD, коллизии и образцов; около 0,3-1 с),
`ghost` (`overlay`, `lineup`, `hide`), `refs` и `ref_alpha` (насколько плотно модель рисуется поверх
фото-референса). Снимки — JPEG, по умолчанию 512 px, обычно 10-40 КБ; открывайте их, только когда нужно
посмотреть.

## Как это работает

- `session start` запускает Blender с изолированным профилем satk и без окна (`--gui` открывает окно); Blender
  открывает локальную точку SAAP (роль `blender`, возможности `core` + `author`, [saap.md](saap.md)) и пишет
  `work\run\endpoints\blender-<name>.json`. Несколько именованных сессий могут работать одновременно.
- Запросы выполняются в главном потоке Blender по одному. Числа берутся из вычисленных сеток (с
  модификаторами), как их увидит экспорт; модификаторы остаются живыми до экспорта. Ответ меньше 1,5 КБ
  (пакет шагов получает примерно на 100 байт больше на шаг, не больше 3 КБ: каждый шаг сохраняет короткие
  значения своего результата, например имя объекта и число треугольников; `python` возвращает до 4000 символов
  своего `result`, а более длинный, или любой при `out`, пишет в файл JSON); тёплый шаг занимает около
  5-20 мс, снимок 512 px — около 50-100 мс.
- Статистика шага — это просто числа, их никогда не сверяют с полосами: счётчики и размеры изменённых объектов,
  топология (`geo.pieces`, `geo.open_edges`, ...), `scene.dims` [W, L, H], измеренные как ванильные справочные
  числа (только геометрия высокой детализации, ширина по кузову без зеркал), и в проекте `vs_target` (отношение к
  его целевым размерам). Если у изменённых объектов есть дефекты сборки, блок `form` перечисляет каждый с местом:
  `floating` (деталь, зазор в мм, центр), `intersect` (детали глубоко внутри других), `loose_share` (грани кузова
  вне его самого большого сваренного куска), `hard_corners` (изломы 60-100 градусов, жёсткие вне любого шва) и
  `see_through` (арки без подкрылка), плюс одно предупреждение `FORM:`. Числа затенения (`shade.normal_bend`,
  `shade.flat_share`, `dff.verts_per_tri`) даёт `scene.stats` по запросу и раздел `reference` в `asset check`:
  это справка, а не цель.
- У каждого шага есть лимит времени (`--timeout`, по умолчанию 90 с для `blender call`): метод, который
  работает дольше, останавливается с ответом `TIMEOUT`, и сессия остаётся рабочей. Если Blender застрял
  внутри собственного кода и остановить его нельзя, сессия всё равно отвечает `TIMEOUT` через несколько
  секунд после лимита, а новым шагам — `BUSY`, пока не освободится; `session status` показывает этот шаг в
  `busy`, а `session stop` завершает зависшую сессию примерно за 3 с. Упавшая сессия даёт ошибку
  `EXTERNAL_TOOL` с путём к отчёту Blender о сбое и командой, которая её продолжит.
- После каждого метода сессия возвращает исходные материалы туда, где метод сохранил копию из графа
  зависимостей (сетка, собранная из вычисленной сетки); такой указатель раньше ронял или подвешивал
  следующее превью.
- С `--project` журнал (`journal.jsonl`, каждый шаг с параметрами, неудачные тоже), `checkpoints` (копия
  `.blend` каждые 5 изменяющих шагов, один раз после каждого `batch` с двумя и более изменяющими шагами,
  после каждого шага `python` и для каждой метки этапа; `--checkpoint` требует точку, `--no-checkpoint`
  отказывается от неё; хранятся последние 10 без метки), `snaps` и `refs` лежат в папке проекта. Новая
  сессия проекта открывает его последнюю контрольную точку; при остановке сессия сохраняет её.
- `session replay --source <project> --name <other>` восстанавливает сцену по журналу в другой сессии
  (загружая контрольные точки там, где журнал откатывался или человек правил сцену в окне Blender).
- `asset.json` хранит вид ассета, замену или добавление, образец, платформу, уровень детализации, целевые
  размеры, этапы G0-G5, решения, проблемы и последние проверку и экспорт; `asset status` превращает его в
  короткую карточку. Состояние этапа: `open`, `review` (сдан исполнителем), `done` (не принимается, пока
  обязательные пункты инвентаря этого этапа не построены) или `skipped` (с причиной `why`, она видна в карточке).
  `asset init --like` должен называть модель того же семейства, что и вид (пикап-грузовик — не подбираемый предмет
  `pickup`); у педа целевой размер стоит по +Z. `<project>/design/inventory.json` — список задач ассета (`--detail`
  выбирает уровень стартового списка; `none` не создаёт инвентаря, и `asset check --strict` такой ассет никогда не
  признаёт готовым; [inventory.md](inventory.md)).
- Сессия заканчивается по `stop`, после `--idle` секунд без запросов (по умолчанию час) или когда завершается
  её процесс-владелец; `stop` завершает только тот процесс Blender, который сам запустил.
- Новые методы — плагины: модуль с таблицей `METHODS` в `satk_blender.studio.methods` (или в пакетах `kit` и
  `look`) находится при запуске сессии.

## Ограничения и известные проблемы

- `--save`, снимки и контрольные точки пишутся только внутри рабочей папки; проекты тоже лежат там.
- В Blender без окна нет отмены; используйте контрольные точки (`session restore --ref <step|tag|last>`).
- Блоку `form` нужен пакет стиля (`satk.style`); сцены больше 150 000 треугольников его пропускают (тогда
  запустите `asset check` на экспорте).
- Метод, застрявший внутри собственного кода Blender (рендер, вычисление модификатора), прервать нельзя;
  сессия отвечает и сообщает об этом, но закончить его можно только через `session stop` + `session start`.
- Ручные правки в окне `--gui` записываются в журнал одним шагом `external` с контрольной точкой; повтор
  журнала загружает эту точку, а не повторяет правку.

## Python-API (если другие пакеты его используют)

```python
from satk.studio import api
api.call("mesh.primitive", {"kind": "cube", "size": [1, 2, 0.5], "name": "crate"}, session="docs", snapshot="3q")
api.call("batch", {"steps": [{"method": "scene.empty", "params": {"name": "wheel_lf_dummy"}}]}, session="docs")
```
