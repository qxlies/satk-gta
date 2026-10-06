# Blender: модели и районы GTA:SA в Blender, рендер «как в игре», экспорт в MTA

[English version](../en/blender.md)

Пакет: `satk.blender` (MIT) и аддон `blender/satk_blender` (GPL-3.0-or-later).

## Что это

Blender 5.1 в фоновом режиме (`-b`) для агента и N-панель «SATK» для человека. Модель или район читаются прямо из IMG
(DFF + цепочка TXD + COL), импортируются функциями DragonFF и сохраняются в `.blend` с упакованными
картинками; потом этот `.blend` можно отрендерить с любой позы камеры (с SID видимых объектов) или
экспортировать в ресурс MTA / папку modloader. Любой свой меш (`.blend`, `.obj`, `.glb`, `.fbx`…)
`game-ready` превращает в игровую модель: бюджет треугольников, UV, prelight день/ночь, COL, LOD, DFF, TXD.
Всё пишется только в `work\blender\` (задания, кэш, профиль Blender, копия DragonFF), `work\out\blender\` и
`work\out\exports\` рабочего пространства. Игра и ваш собственный профиль Blender не меняются.

## Быстрый пример

```powershell
satk blender doctor --quick
satk blender import-model model:411 --render
```

Что вернётся (сокращённо):

```json
{"ok":true,"cmd":"import_model","job":"20261005-111052-b8de",
 "files":{"png":[".../jobs/20261005-111052-b8de/model.png"],"blend":".../jobs/20261005-111052-b8de/scene.blend"},
 "stats":{"model":"model:411","name":"infernus","sec":"cars","hidden_dam":true,"wheels":4,"paint":[12,1,12,1],
          "meshes":17,"images":13,"txd_images":22,"unused_images":9,"missing_tex":0,"frame_margin":0.04,
          "engine":"BLENDER_EEVEE","png_std":62.81,"seconds":2.78,"process_s":4.65},
 "source":"index","warn":["EEVEE_ALPHA: used EEVEE to composite blended textures/materials"],
 "log":".../jobs/20261005-111052-b8de/blender.log"}
```

`images` — картинки, которые лежат в сохранённом `.blend` (их используют материалы модели, все упакованы);
`txd_images` — всё, что загружено из цепочки TXD (у `infernus`: 3 своих + 19 из `vehicle.txd`); лишние
(`unused_images`) удаляются до сохранения, как их и так выбросил бы Blender. `frame_margin` — наименьший
отступ проекции вершин от края кадра (доля кадра): больше нуля — модель не обрезана. Движок по умолчанию —
Workbench, но полупрозрачные текстуры (стёкла машины) он смешивать не умеет, поэтому такой рендер делает EEVEE
(`EEVEE_ALPHA`).

## Район → рендер → экспорт

Путь к `.blend` берётся из `files.blend` ответа `import-area` (`<job>` ниже — имя папки задания):

```powershell
satk blender import-area --center 2495,-1687 --box 100 --match center --lod all --area any
satk blender render --blend <workspace>/work/blender/jobs/<job>/scene.blend --pos 2495,-1740,40 --look 2495,-1687,13 --objindex
satk blender export --blend <workspace>/work/blender/jobs/<job>/scene.blend --objects lae2_roads89 --target mta-resource
```

Здесь: 187 расстановок (168 HD, 19 LOD) 116 моделей; рендер с `--objindex` видит 78 объектов, крупнейший —
`inst:lae2_stream0#18` (`Pawnshp_lae2`, 15,6 % пикселей); экспорт пишет `lae2_roads89.dff`, `.txd`, `.col`,
`.ide`, `.ipl`, `meta.xml` и `client.lua` в `work\out\exports\lae2_roads89\`.

## Игровая модель из любого меша (game-ready)

<!-- docs-smoke: skip нужен свой исходный файл -->
```powershell
satk blender game-ready <workspace>/work/tmp/crate.obj --col box
satk blender game-ready <workspace>/work/tmp/statue.blend --objects Statue,Plinth --budget 600 --height 3 --render
```

Результат — папка `work\out\blender\<name>\`: `<name>.dff` (RW 3.6.0.3, prelight день и ночь),
`<name>.col` (COL3, имя COL-модели = имя модели), `lod<name>.dff`, `<name>.txd` (формат выбирается по альфе,
DXT1 для непрозрачных текстур, с мипмапами; исходные PNG остаются в подпапке `tex`), `<name>.ide` (`objs`-строки
модели и LOD с ID `-1`) и `gameready.json` (аргументы, статистика, проверки). Ответ (сокращённо, куб с текстурой):

```json
{"ok":true,"cmd":"game_ready","name":"crate","out":".../work/out/blender/crate",
 "files":{"dff":".../crate.dff","col":".../crate.col","txd":".../crate.txd","png":[".../tex/crate.png"],"ide":".../crate.ide"},
 "stats":{"src_tris":12,"tris":12,"uv":"keep","baked":false,"prelight":"bake","day_mean":0.337,"col":"box",
          "txd_formats":{"crate":"DXT1 64x64 mips 7"}},
 "checks":{"cols":["check","status","detail"],"rows":[["dff","ok","RW 0x36003, 12 tris (budget 1040), 28 verts"],
   ["prelight","ok","day + night"],["txd","ok","crate DXT1 64x64 mips 7"],["col","ok","COL3 'crate': 0 faces, 1 boxes, 0 spheres"],…]},
 "warn":["LOD_SKIPPED: 12 triangles are below 48, no LOD model needed"]}
```

Шаги (исходные объекты и их материалы не меняются — всё делается на копиях):

1. **Источник:** `.blend` открывается как сцена (только чтение, сохраняется в папку задания), остальные
   форматы импортируются. Берутся `--objects` (с детьми) или все видимые меши; модификаторы применяются,
   меши сливаются в один объект. `--height` (м) или `--scale`, начало координат `--origin base` (центр
   основания), `center` или `keep`; дубли вершин сливаются, висячие рёбра и вырожденные грани удаляются.
2. **Бюджет:** сначала сливаются почти плоские соседние грани (угол до 1°, швы материалов и UV целы), затем
   проходы `COLLAPSE` до `--budget` треугольников (по умолчанию 1 040 — p90 ванильных моделей карты; p50 — 216).
3. **UV:** `auto` оставляет свои UV, а без них делает развёртку `smart_project`; `keep` и `smart` выбирают
   одно из двух принудительно; `box` — кубическая проекция 32 px/м.
4. **Текстуры:** материал с картинкой сохраняет её (степень двойки ≤ `--tex-size`, по умолчанию 256; цвет
   материала становится белым), простой цвет остаётся цветом материала; процедурный шейдер, цвета вершин или
   текстура на новых UV **запекаются** (Cycles `DIFFUSE`/`COLOR`, `selected_to_active`) в одну текстуру
   `<name>` на новой развёртке `smart_project`. `--bake never` запекание запрещает (предупреждения `TEX_FLAT`, `TEX_UV_MISMATCH`).
5. **Prelight:** два цветовых атрибута по углам (`satk_day`, `satk_night` — DragonFF пишет их как prelight
   и ночные цвета). `bake` — AO Cycles с плоскостью земли, день = AO × (небо + солнце), ночь = AO × тёмно-синий
   фоновый свет; `simple` — только по нормалям, без Cycles; `none` — без цветов. Яркость подогнана под ваниль:
   медиана дневного prelight по 400 случайным моделям `gta3.img` — 82/255, ночного — 26/255.
6. **COL:** `hull` — выпуклая оболочка (не больше 256 треугольников), `box` — AABB-примитив, `mesh` —
   сама упрощённая модель, `none` — без коллизии; поверхность `--surface` (eSurfaceType 0…178: 0 по умолчанию,
   4 тротуар, 43 массивное дерево, 51 металлический лист…). Плоский меш вместо оболочки получает бокс
   (`COL_HULL_FLAT`).
7. **LOD:** доля `--lod` треугольников (0,25 — как у ванили) в модели `lod<name>` с теми же текстурами, своим
   prelight и дальностью прорисовки 800 (у HD-модели — `--draw`, по умолчанию 150); меньше 48 треугольников —
   LOD не нужен (`LOD_SKIPPED`).
8. **Экспорт и проверки:** DragonFF пишет DFF и COL; satk упаковывает TXD, перечитывает всё своим
   `satk.formats` и заполняет `checks`: бюджет и RW-версия, prelight, ссылки на текстуры, TXD, COL (одна модель,
   имя, ±256 м), длины имён (COL ≤ 21, DFF ≤ 23), текстуры степени двойки. Провал проверки — предупреждение
   `GAME_READY_CHECKS`.

TXD упаковывает `satk texmod`; в рабочей копии без него текстуры остаются PNG в `tex`, рядом лежит `TODO-txd.txt`,
в ответе — `TXD_PENDING`. `--render` добавляет `preview.png` в папку задания: EEVEE, вид «как в игре»
(текстура × цвет материала × дневной prelight); сохранённый `.blend` задания открывается с тем же шейдингом.

## Команды

| Команда | MCP | Что делает |
|---|---|---|
| `satk blender doctor [--quick]` | — | версии Blender и Python, DragonFF `b3bd7aa`, изолированный профиль; при первом запуске извлекает DragonFF; `--quick` не запускает Blender |
| `satk blender import-model <id> [--render] [--views 1\|4] [--size N] [--engine workbench\|eevee] [--col] [--time HH:MM]` | `blender_job` (`import_model`) | одна модель в новый `.blend`; у машин скрыты `_dam`/`_vlo`, колёса на всех `wheel_*_dummy`, цвета из `carcols.dat` |
| `satk blender import-area --center X,Y (--box S \| --box x0,y0,x1,y1 \| --r R) [--match aabb\|center] [--area N\|any] [--lod hd\|lod\|all] [--col] [--limit N]` | `blender_job` (`import_area`) | район: меш на модель, связанные дубликаты на расстановку |
| `satk blender render --blend B (--pos X,Y,Z --look X,Y,Z \| --ypr Y,P,R \| --bm NAME) [--time HH:MM] [--size WxH] [--fov 70] [--engine workbench\|eevee] [--objindex] [--limit N]` | `blender_job` (`render`) | PNG; с `--objindex` — ещё таблица видимых SID с долей пикселей |
| `satk blender export --blend B --objects NAME… [--target mta-resource\|modloader] [--out DIR] [--name N]` | `blender_job` (`export`) | DFF/TXD/COL + IDE/IPL с исходными данными определения и расстановок (+ `meta.xml`, `client.lua`) |
| `satk blender game-ready <src> [--name N] [--objects A,B] [--budget N] [--height M \| --scale S] [--origin base\|center\|keep] [--uv auto\|keep\|smart\|box] [--bake auto\|always\|never] [--tex-size N] [--prelight bake\|simple\|none] [--col hull\|box\|mesh\|none] [--surface N] [--lod R] [--draw D] [--out DIR] [--render]` | — | меш → игровая модель: DFF + COL + LOD + TXD в `work\out\blender\<name>\`, с проверками |
| `satk blender preview <subject>` | — | лист превью в стиле SA для модели, файла DFF, папки мода или сессии: [look.md](look.md) |
| `satk blender addon-build` | — | zip расширения `satk_blender` в `work\out\blender\` |
| `satk blender run <cmd> --args '<json>'` | `blender_job` | то же через JSON (`cmd` = `doctor`, `import_model`, `import_area`, `render`, `export`) |

`id` модели — `model:411`, `model:infernus`, `411`, имя или путь к своему файлу `.dff` (берётся `.txd`
с тем же именем рядом; машина получает ещё `vehicle.txd` игры). Команды импорта принимают ещё `--profile` и
`--source auto|index|direct`. `--objects` у экспорта — имя объекта из сцены, имя модели (`lae2_roads89`) или SID
(`model:17613`, `inst:lae2_stream0#4`); расстановка экспортируется как её модель.

## Как это устроено

- **Задание** = `work\blender\jobs\<yyyymmdd-HHMMSS-xxxx>\`: `request.json` → Blender
  (`-b --factory-startup --python-exit-code 1 --python blender\satk_blender\agent_cli.py -- request.json`)
  → `response.json`, `blender.log`, `scene.blend`, PNG. Результат читается из файла, не из stdout.
  Контракт `satk-blender/1` — `src/satk/blender/contract.py` (заморожен: допустимы только совместимые изменения).
- **Окружение:** DragonFF извлекается `git archive b3bd7aa` из `src\DragonFF` рабочего пространства в
  `work\blender\dragonff` (клон не трогается); профиль Blender — `work\blender\profile`
  (`BLENDER_USER_CONFIG/SCRIPTS/EXTENSIONS/DATAFILES/RESOURCES`), `PYTHONDONTWRITEBYTECODE=1`, TEMP в папке задания.
- **Откуда данные:** индекс `work\index\vanilla.sqlite`, а пока он не собран (или собран старой версией
  satk) — прямой разбор DAT/IDE/IPL/IMG (`satk.blender.gamedata`, ~0,6 с, предупреждение `INDEX_MISSING` в
  ответе). Порядок архивов, «первый архив побеждает», LOD по обратным ссылкам — как в индексе; число расстановок
  ванили 50 935. Блобы копируются в `work\blender\cache\blobs\<hh>\<sha16>\<имя>` (обрезаны по RW-размеру,
  адресуются по содержимому).
- **Импорт:** функции `dff_importer`/`txd_importer` DragonFF вызываются напрямую (модальный оператор карты
  в `-b` ничего не грузит), картинки упаковываются, обратные грани сохраняются (`create_backfaces`).
  Район: прототипы моделей в `SATK_models` (исключены из слоя представления), расстановки — в `SATK_area`, LOD — в
  `SATK_lod` (скрыт, если есть HD), коллизии — в `SATK_col` (только с `--col`, материалы общие).
  Матрица расстановки — позиция + **сопряжённый** кватернион IPL. У объектов свойства `satk_sid`
  (`inst:`/`model:`), `satk_model`, `satk_lod`, `satk_area`, `satk_iflags` (флаги расстановки из поля
  interior IPL); у коллекции модели — `satk_ide` (дальность прорисовки, флаги, время `tobj`, IFP `anim`).
  Разбиваемые меши (`*_breakable`: фонари, гидранты, ящики) DragonFF кладёт в коллекцию `Breakable` у корня
  сцены — импорт переносит их в коллекцию своей модели (у района — в скрытую `SATK_models`) и прячет от
  рендера (`hide_render`), как игра до разрушения объекта. В ответе — `stats.breakables`.
- **Шейдинг «как в игре»** (EEVEE): группа `SATK_SA_Building` = текстура × цвет материала ×
  lerp(дневной prelight, ночной prelight, баланс); баланс по времени суток как в движке
  (1 до 06:00 → 0 к 07:00, 0 до 20:00 → 1 к 21:00). Workbench показывает текстуры без prelight.
- **`--objindex`:** второй рендер EEVEE с одним сэмплом: каждый объект излучает свой цвет, альфа текстуры
  < 0,5 отсекается (листва, заборы, провода), цвета переводятся обратно в SID. Полная таблица —
  `files.visible`, в ответе первые `--limit` строк.
- **Экспорт:** DFF (RW 3.6.0.3) и COL3 пишет DragonFF; COL — из игровой коллизии, если район импортирован
  с `--col`, иначе из рендер-меша (предупреждение `COL_FROM_MESH`). Имя COL-модели = имя модели.
  `client.lua`: `engineRequestModel` → `engineLoadTXD`/`engineImportTXD` → `engineLoadDFF`/`engineReplaceModel`
  → `engineLoadCOL`/`engineReplaceCOL` → `createObject` (порядок поворота объектов MTA — `ZXY`).
  IDE-фрагмент берёт дальность прорисовки и флаги исходного определения (`lae2_roads89`: `150, 1`;
  `tobj` — с временем, `anim` — с IFP), IPL — поле interior = area + флаги расстановки `<< 8`
  (`telgrphpole02`: `512`). Для `.blend`, импортированных до этого, данные дополняются из индекса или игры
  по `satk_profile` сцены; модель без определения получает `299, 0` и предупреждение `IDE_DEFAULTS`.
  Имя (`--name`) и папка проверяются до запуска Blender, а папка создаётся только после того, как объекты
  найдены: `NOT_FOUND`/`BAD_PARAMS` ничего не оставляют на диске.
  Писать можно только под `work\`; `--out` в игру → `PROTECTED_PATH`. В IMG — никогда.
- **Кадр модели** (`--render`): камера на азимутах 45/135/225/315° подбирается по вершинам видимых мешей
  с полем 8 % от края, проекция центрируется с учётом перспективы.
- **Время:** старт Blender ≈1,5–2 с; машина с рендером ≈4–5 с; район 100×100 м (187 расстановок, 116 моделей,
  843 картинки из TXD, 481 из них в `.blend`) ≈7–8 с; рендер с `--objindex` ≈4 с (из них 1,5 с — сам рендер);
  первый кадр EEVEE компилирует шейдеры.

## Аддон для человека

`satk blender addon-build` собирает `work\out\blender\satk_blender-0.1.0.zip`. Установка в свой профиль
Blender — ваше решение (Preferences → Get Extensions → Install from Disk). N-панель «SATK»: поиск по индексу,
Import Model, Import Area по координатам / зоне / закладке вьювера, «Game shading» и ползунок времени,
Hide/Show `_dam`/`_vlo`, Export выделенного. Зона — по имени (`GAN1`): центр её прямоугольника (`zone:` индекса,
без индекса — `data/info.zon`/`map.zon`), размер — поле Box. Export работает в открытой сцене и после себя
возвращает её как было (исключение коллекций, видимость, выделение, связи шейдеров). Блок «Game-ready» делает
то же, что `satk blender game-ready`, из выделенных мешей открытой сцены: результат — в коллекции
`SATK_gameready` и в `work\out\blender\<name>\`, выделение и исходные объекты не меняются, повторный запуск с
тем же именем заменяет прежний результат.

В настройках аддона — путь к `src` рабочей копии satk (`satk_src`) и к папке с `dragonff`. Пустые поля
означают умолчания: `src` той рабочей копии, из которой запущен аддон (или `SATK_SRC`), и `<work>\blender`
рабочего пространства satk (или `SATK_DRAGONFF`); установленное расширение DragonFF, если оно есть, берётся
вместо копии. Путей конкретной машины в аддоне нет. Импорт TXD самого DragonFF (его оператор и импорт карты) в
сессии с аддоном декодирует текстуры через `satk.formats.dxt`: у DragonFF `b3bd7aa` цвета DXT3 портятся;
TXD, которые satk не читает (PS2, Xbox), и импорт с мипмапами остаются за декодером DragonFF.

Blender запускает Python с `ignore_environment`, поэтому `PYTHONDONTWRITEBYTECODE` сам по себе не работает:
аддон и `agent_cli.py` выставляют `sys.dont_write_bytecode` сами (аддон — если переменная задана, как её
задаёт раннер satk), и `__pycache__` не появляется ни в `tools`, ни в `work\blender\dragonff`.

## Заметки о Blender 5.1

- **Нормали.** С Blender 4.1 «автосглаживания» нет: свои нормали сохраняются и на плоских гранях, но каждая
  плоская грань — отдельный веер, поэтому экспорт DFF пишет по вершине на каждый угол (у машины с плоским
  затенением вершин примерно вчетверо больше). Сначала сварите вершины, сделайте все грани гладкими, отметьте
  острые рёбра и только потом примените модификатор Weighted Normal или свои нормали; переключение гладкого
  затенения после этого сдвигает свои нормали.
- **Smooth by Angle** с 4.1 — модификатор (ассет геометрических узлов); он работает без окна с
  `--factory-startup`, а DragonFF экспортирует вычисленный стек модификаторов.
- **`use_nodes`** у материалов и миров в 5.x устарел (всегда включён); satk ставит его только там, где это
  нужно старым сборкам.
- **Миниатюры.** Blender в Windows пишет миниатюры `.blend` в папку пользователя `.thumbnails`, что бы ни было в
  `XDG_CACHE_HOME`. Каждый входной скрипт satk ставит `file_preview_type = 'NONE'` (и без резервных `.blend1`,
  без автосохранения) до любого сохранения: холодное задание, сохраняющее `.blend`, не добавляет миниатюр.
- **Байт-код.** Blender игнорирует `PYTHONDONTWRITEBYTECODE` (Python запускается с `ignore_environment`);
  входные скрипты ставят `sys.dont_write_bytecode`, поэтому в папке аддона не появляется `__pycache__`.
- **Потоки.** Задания передают `-t N` из `SATK_BLENDER_THREADS` или `[blender] threads` в `satk.toml`
  (не задано или 0 — все ядра), чтобы несколько заданий делили одну машину.
- **Фоновый режим.** `bpy.app.timers` под `-b` не срабатывают, отмены нет; живая сессия
  ([studio.md](studio.md)) вместо этого использует блокирующий цикл в главном потоке и контрольные точки `.blend`.
- **EEVEE** (`BLENDER_EEVEE` в 5.x) компилирует шейдеры на первом кадре задания; игровой вид SA из
  [look.md](look.md) делит одну группу узлов между всеми материалами, чтобы это было быстро.

## Ограничения и известные проблемы

- `export` пишет TXD в RGBA8888 без мипмапов (у писателя TXD DragonFF нет DXT-энкодера; предупреждение
  `TXD_RGBA8888`) — файлы в 4–8 раз больше. `game-ready` уже упаковывает DXT через `satk texmod`.
- `match=aabb` без индекса считает AABB по ограничивающей сфере DFF (индекс берёт бокс COL), поэтому числа
  могут слегка отличаться от `satk world near`.
- Workbench не умеет текстуру × цвет материала: машины в Workbench белые, краска видна в EEVEE. Сцены с
  полупрозрачными текстурами всё равно рендерятся в EEVEE (`EEVEE_ALPHA`).
- DragonFF теряет несколько вырожденных/дублирующихся треугольников (у `lae2_roads89` 219 из 225).
- Вся карта в Blender не помещается (50 935 расстановок): лимит задания — 5 000 расстановок (`--limit`,
  по умолчанию 2 000), разумная единица — район.
- Каждое задание платит 1,5 с на старт; пошаговая работа — в живой сессии ([studio.md](studio.md)).
- `game-ready`: UV — `smart_project` или куб (xatlas и развёртка по частям не ставятся: без новых зависимостей),
  поверхность COL одна на всю модель, prelight — модель освещения, а не `timecyc.dat`; альфа исходных текстур
  при запекании теряется; ориентацию нормалей исходника `game-ready` не чинит.

## Python-API

```python
from satk.blender.resolve import plan_model, plan_area      # без bpy: что импортировать и куда ставить
from satk.blender.runner import run_job                      # запустить задание контракта satk-blender/1
plan = plan_area(center=[2495, -1687], box=[2445, -1737, 2545, -1637], match="center", area=None, lod="all")
resp = run_job("import_area", {"center": [2495, -1687], "box": 100, "lod": "all", "area": "any"}, plan=plan)

from satk.blender.gameready import out_dir, finalize         # game-ready: папка, затем TXD/IDE/проверки
resp = run_job("game_ready", {"src": "<workspace>/work/tmp/crate.obj", "out": str(out_dir("crate"))})
done = finalize(out_dir("crate"))                            # {"files", "checks", "stats", "warn"}
```
