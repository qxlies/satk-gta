# `satk index`: SQLite-индекс ассетов, профили и запросы

[English version](../en/index.md)

<!-- Сверено 2026-10-05: индекс vanilla (схема v3) собран в отдельном рабочем каталоге, быстрый
     пример и Python-API выполнены, ответы ниже настоящие, сокращены. -->

Пакет: `satk.index`. Схема — `src/satk/index/schema.sql`; эталонные числа — `tests/golden/index_vanilla.json`.

## Что это

Один SQLite-файл на профиль загрузки (`work\index\<profile>.sqlite`) со всем, что грузит игра: все архивы и
россыпь, TXD и текстуры, DFF с материалами, COL, IDE-определения, IPL-расстановки с LOD и габаритами, зоны
(с игровыми названиями из `text/american.gxt`: `asset find ganton --kind zone` → `zone:gan1` «Ganton»;
`view goto --id zone:gan1` летит к центру зоны), анимации и полнотекстовый поиск. Начиная со схемы v3 — ещё и
файлы данных, которые `gta_sa.exe` грузит по имени: вода (`water.dat`, `water1.dat`), `timecyc.dat`, цвета машин
(`carcols.dat`), `handling.cfg`, отношения типов педов (`ped.dat`) и физика объектов (`object.dat`), см. раздел
«Данные игры (схема v3)» ниже.
Индекс старой схемы (v1, v2) не открывается (`INDEX_MISSING: … has schema v2, this satk needs v3`) —
его нужно пересобрать: `satk index build`.

Индекс отвечает на вопрос «что видит игра»: какой файл побеждает при совпадении имён, какой TXD у модели и
через какого родителя нашлась текстура, кто кого перекрыл (SA-MP, моды). Игровые файлы только читаются; пакет
пишет лишь в `work\index\` (`<profile>.sqlite`, `hashcache.sqlite`). Сборка ванили занимает около 8 с.

## Быстрый пример

```powershell
satk index build
satk asset get model:411
satk asset refs model:17613 --rel inst
satk asset find ws_rooftarmac1 --kind tex --limit 3
satk world near 2495 -1687 --r 30 --limit 5
satk index query "SELECT sid, name, n_inst FROM v_model ORDER BY n_inst DESC LIMIT 3"
satk asset get tcyc:extrasunny_la/12
satk world near -1500 -1700 --r 50 --kinds water
satk index verify
```

Что вернётся (сокращённо, вторая команда):

```json
{"ok":true,"id":"model:411","name":"infernus","sec":"cars","txd":"infernus","layer":"vanilla","n_inst":0,
 "tex":{"total":13,"missing":0},"geo":{"verts":3573,"tris":3072,"bs":[-0.03,0.09,-0.06,3.13]},
 "handling":{"mass":1400.0,"max_vel":240.0,"accel":30.0,"gears":5,"drive":"4","engine":"P","brake":11.0,
 "steer_lock":30.0,"value":95000},
 "colors":[[12,1],[64,1],[123,1],[116,1],[112,1],[106,1],[80,1],[75,1]],"color_rgb":{"1":"#f5f5f5","12":"#5d7e8d",…},
 "links":{"ide":"ide:data/vehicles.ide","dff":"dff:infernus","txd_chain":["txd:infernus","txd:vehicle"],
 "col":"col:infernus_col","col_via":"embedded","handling":"handling:infernus"}}
```

## Команды

| Команда | MCP | Что делает |
|---|---|---|
| `satk index build [--profile P \| --all] [--jobs N]` | — | собрать индекс профиля (`vanilla`, `installed`, `samp`, `game`; без `--profile` — `[index] default_profile`); файл заменяется атомарно |
| `satk index status [--deep]` | — | по профилям: собран ли, свежий ли, размер, число моделей, текстур и расстановок; `state` — `not configured` для профиля без папки игры, `alias of game` для `vanilla` без чистой копии |
| `satk index verify [--profile P] [--golden F] [--metrics]` | — | сверка с `tests/golden/index_<profile>.json` (Приложение A) |
| `satk index hash [--profile P] [--recompute]` | — | хэш содержимого; одинаков у двух сборок одних и тех же файлов |
| `satk index bench [--profile P] [--n N]` | — | p50/p95 для `get`, `find`, `refs`, `near r=100`; `ok`, если все p95 < 50 мс |
| `satk index query "<SELECT…>" [--params …] [--db index\|re\|notes]` | `index_query` | SQL только для чтения: запись, `ATTACH`, `PRAGMA x=…` → `READ_ONLY` |
| `satk index diff A B [--kind model\|txd\|tex\|file]` | `index_diff` | что добавлено, перекрыто, изменено или удалено между двумя профилями |
| `satk asset find <query> [--kind K]` | `asset_find` | поиск по имени, сначала точные совпадения; `'*grove*'` — шаблон |
| `satk asset get <sid> [--fields a,b]` | `asset_get` | объект по SID со ссылками `links` на связанные SID (SID других пакетов отдаются их провайдерам) |
| `satk asset refs <sid> [--rel R]` | `asset_refs` | без `--rel` — сколько связей каждого вида; с `--rel` — таблица |
| `satk world near X Y [Z] [--r R \| --box x0,y0,x1,y1] [--match aabb\|center] [--area N\|any] [--lod hd\|lod\|all] [--kinds inst,item,zone,water]` | `world_near` | расстановки рядом, ближние первыми; `water` — полигоны `water.dat` (v3) |

У всех запросов есть `--profile` (по умолчанию `vanilla`; без чистой копии это алиас профиля `game` — вашей
папки с игрой, см. [install.md](install.md)). Если DAT/IDE/IPL/IMG изменились после сборки, в ответе появится
`warn: ["INDEX_STALE: … (satk index build)"]`.

Связи для `--rel`: `model` → `inst tex dff txd col ide overrides`; `tex` → `models same_pixels txd`;
`txd` → `textures models parent children file`; `inst` → `model lod hd_children ipl near`;
`file` → `parsed shadowed_by shadows`; `ipl` → `inst`; `ide` → `models`; `zone` → `inst`; `ifp` → `anims`;
`handling` → `models`; `tcyc` → `hours`.

## Данные игры (схема v3)

Эти файлы `gta_sa.exe` грузит по имени, а не через `gta.dat`. Парсеры — в `satk.formats` (см.
[formats.md](formats.md)), загрузка в индекс — `src/satk/index/gamedata.py`. Строки `source` у них прежние
(`kind = other`), но они входят в проверку свежести: правка такого файла даёт `INDEX_STALE`.

| Файл | Таблицы | SID | Что видно |
|---|---|---|---|
| `data/water.dat`, `data/water1.dat` | `water_quad`, `water_rtree` | `water:<n>`, `water:water1/<n>` | квадраты и треугольники воды, флаги (1 — видимая, 2 — мелководье), волны; `world near --kinds water` |
| `data/timecyc.dat` | `timecyc` | `tcyc:<погода>/<час>`, `tcyc:<погода>` | 23 погоды × 8 часов (0, 5, 6, 7, 12, 19, 20, 22): цвета неба и света, дальность прорисовки, туман, вода, пост-эффекты |
| `data/handling.cfg` | `handling` | `handling:<id>` | строки машин, `!` — мотоциклы, `%` — лодки, `$` — авиация; флаги расшифрованы |
| `data/carcols.dat` | `carcol`, `car_color` | — | палитра (индекс → RGB) и варианты цветов каждой модели |
| `data/ped.dat` | `ped_rel` | — | кто кого ненавидит, недолюбливает, любит или уважает (`hate dislike like respect`) |
| `data/object.dat` | `object_data` | — | масса, разрушение, реакция на столкновение, эффект |

Как это видно в ответах:

- `asset get model:<машина>` — `handling` (основные поля), `colors` (пары или четвёрки индексов палитры) и
  `color_rgb`, ссылка `links.handling`; у педа — `pedtype_rel` (отношения его типа из `ped.dat`); у объекта из
  `object.dat` — `physics` (`dmg_effect`, `col_response`, `fx`; `loaded: false` — строка после `*`, которую движок
  не читает).
- `asset get handling:infernus` — все поля строки в единицах файла (км/ч, м/с²), `model_flags` и `handling_flags`
  по именам, `models` — машины с этим `handling` в `vehicles.ide`.
- `asset get tcyc:extrasunny_la/12` — цвета в виде `#rrggbb`, `water` — `#rrggbbaa`; `tcyc:extrasunny_la` —
  список часов и дальности прорисовки. Погоду можно задать номером: `tcyc:0/12`.
- `asset find infernus` находит и `handling:infernus`; `asset find sunny` — погоды `tcyc:*`.
- Представления для SQL: `v_vehicle` (поля `vehicles.ide` + `handling` + число вариантов цвета) и `v_ped`
  (поля `peds.ide`: тип, стат, голоса, радио).

Индекс повторяет то, как движок читает эти файлы, включая его ошибки (они перечислены в `meta.notes`):

- `timecyc.dat`, строка 320 (`RAINY_COUNTRYSIDE`, 20:00) начинается с одного `255` вместо трёх чисел: движок
  читает 20 значений со сдвигом, остальные остаются от 19:00; у строки `nread = 20`.
- `carcols.dat`: цвет 98 записан как `77.93,96` (читается как 77, 93, 96); `moonbeam` ссылается на цвет 227,
  которого нет в палитре (0–126).
- `ped.dat`: вторая строка `Respect` у типа заменяет первую, поэтому `CIVMALE` уважает только `CIVFEMALE`.
- `object.dat`: строка `*********melee weapons` останавливает чтение, поэтому последняя запись (`flowera`) не
  загружается.
- `handling.cfg`: всё после `;the end` игнорируется; `$ RCRAIDER` содержит `0.1s` (лишняя `s` пропускается).
- Профиль на `.two`-файлах SA-MP (`samp`) читает `HANDLING.two` и `timecyc.two`, как клиент SA-MP.

Числа ванили (`tests/golden/index_vanilla.json`): 301 квадрат и 6 треугольников воды (`water1.dat` — 267),
184 строки `timecyc.dat`, 210 + 13 + 12 + 24 записи `handling.cfg` (все 212 машин находят свою), палитра из 127 цветов,
199 моделей с 1 157 вариантами цвета, 113 пар `ped_rel`, 995 строк `object.dat` (загружаются 994).

SID `water:`, `tcyc:` и `handling:` понимают `asset get/refs/find` и API индекса. Другие пакеты (заметки,
вьювер) начнут их принимать, когда эти виды внесут в список `satk.core.ids` (его меняет владелец ядра).

## Как это устроено

Конвейер (`build.py`): поиск файлов профиля и порядка регистрации IMG (`formats.layout`) → отнесение каждого
файла к слою → разбор блобов в 12 процессах (`scan.py`, парсеры из `satk.formats`) → выбор победителей
(`resolve.py`) → связи, LOD, габариты, R-дерево (`link.py`) → файлы данных (`gamedata.py`, v3) →
полнотекстовый поиск FTS5 по триграммам (`search.py`) → `meta` + `content_hash` + атомарная замена.

- **Слои.** Файл относится к `vanilla`, если пара (путь, sha256) есть в `gta-sa-clean\MANIFEST.sha256`; иначе
  действуют правила `[layers].rules` (`SAMP/**` → `samp`, `modloader/<мод>/**` → `modloader:<мод>`); иначе —
  `modded`. Суммы SHA-256 кэшируются в `work\index\hashcache.sqlite` по ключу (корень, путь, размер, mtime);
  первичное заполнение кэша — из манифестов `tools\data\manifests`, так что хэшировать 5 ГБ не нужно.
- **Победители.** Блобы: «первый зарегистрированный архив побеждает» для пары (пространство имён, имя); россыпь
  конкурирует внутри `main` после всех IMG. IDE: для одного ID побеждает больший приоритет слоя (`vanilla` 0 <
  `samp` 10 < `modded` 20 < `modloader` 30), затем более поздний файл и строка. COL: первое вхождение имени
  (DAT `COLFILE`, затем россыпь, затем IMG).
- **Текстуры модели** (`model_tex`): свой TXD → родители `txdp` → `vehicle`. Неявный родитель `vehicle` ставится
  TXD каждой машины, поэтому через него находятся и текстуры деталей тюнинга с тем же TXD.
- **Расстановки.** Кватернион хранится так же, как в IPL; мировой поворот — сопряжённый; `rz` в ответах
  уже мировой. Габариты берутся из COL (иначе из сферы DFF, иначе это точка) → `inst_rtree`. LOD бинарного
  `xxx_streamN` — индекс в текстовом `xxx`; `is_lod` означает «на меня кто-то ссылается».
- **SID.** `dff:`/`txd:`/`tex:` называют активную версию; версия, перекрытая другим слоем, — `@<слой>`;
  перекрытая внутри своего слоя доступна только как `file:<архив>/<запись>`. `model:<id>@<слой>` — неактивное
  определение.
- **Чтение.** `IndexDB` открывает файл с `mode=ro` и `PRAGMA query_only`, по соединению на поток;
  `open_index()` кэширует его и переоткрывает файл после пересборки (по mtime). На Windows открытый файл нельзя
  заменить переименованием — тогда новая сборка копируется в него через SQLite backup API.
- **Детерминизм.** Строки вставляются в фиксированном порядке; `content_hash` — sha256 канонического дампа
  ключевых таблиц (без mtime). Две сборки одних и тех же файлов дают один хэш (это проверяет тест).

Числа ванили: 21 147 блобов, 4 046 TXD, 32 878 текстур, 15 356 DFF, 14 832 определения, 50 935 расстановок,
6 103 LOD-связи (все разрешены), файл около 58 МБ; `get` — около 0,4 мс, `find` и `near r=100` — около 1,5 мс.

## Ограничения и известные проблемы

- Инкрементальной сборки нет: каждая сборка полная (около 8 с).
- R-деревья (`inst_rtree`, `item_rtree`, `water_rtree`) через `index query` не читаются: модуль rtree готовит
  внутренние запросы, а авторизатор «только SELECT» их запрещает. Для пространственных запросов — `world near`.
- `--kinds` у `world near` проверяется по списку `api.NEAR_KINDS`; в описании MCP-инструмента `water` не
  упомянут (бюджет `tools/list`), но работает.
- `asset find` без шаблона возвращает только точные совпадения, если они есть (`warn: MORE: N …` сообщает,
  сколько ещё имён содержат строку); для подстроки — `'*строка*'`.
- Параметр поиска называется `query`, а не `q`: `q` занят глобальным флагом `-q`.
- Секции `path` текстовых IPL (наследие Vice City, 165 152 строки) в `ipl_item` не хранятся.
- Профиль `samp`: позиция `GTA_INT.IMG` в `default.two` — допущение, оно записано в `meta.assumptions`.
- В `tests/golden/index_vanilla.json` два расхождения с первым проектом объяснены в `_comment`: `atomic 46 108` у
  прототипа включал `Struct` источников света (верное число — 19 556); 5 из 467 «неразрешённых» — материалы с
  пустым именем текстуры, а не `cutscene.img`.

## Python-API (если другие пакеты его используют)

```python
from satk.index.api import open_index, override_index, FakeIndexDB

db = open_index("vanilla")
mf = db.model_files(411)                 # dff, txd_chain [infernus.txd, vehicle.txd], col
data = db.read_blob(mf.dff)              # байты записи IMG (open_ro)
tex = db.texture_ref("tex:bistro/vent_64")   # где лежат пиксели mip0
db.match_runtime(17613, (2489.3, -1668.5, 12.3))  # ['inst:lae2_stream0#4']
db.get("handling:infernus")["car"]["max_vel"]      # 240.0 (v3; SID разбирает api.parse_sid)
db.near(-1500, -1700, r=10, kinds=("water",))      # water:0

with override_index(FakeIndexDB()):      # в тестах других пакетов
    ...
```
