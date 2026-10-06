# `satk formats`: парсеры форматов GTA:SA

[English version](../en/formats.md)

Пакет: `satk.formats`.

## Что это

Библиотека чтения форматов игры: IMG, RenderWare (TXD, DFF), COL, IFP, IDE, IPL, ZON, DAT, GXT, PE и текстовые
файлы данных (`water.dat`, `timecyc.dat`, `carcols.dat`, `handling.cfg`, `object.dat`). На ней стоят индекс
ассетов ([index.md](index.md)), текстуры ([media.md](media.md)), модели ([models.md](models.md)) и мост в Blender
([blender.md](blender.md)). Нужна только стандартная библиотека, поэтому пакет работает и во встроенном Python
Blender. Игровые файлы открываются только на чтение, IMG целиком в память не грузится, на диск пакет ничего не
пишет. Битые данные дают `FormatError(kind, offset, msg)`, а не `IndexError`, `struct.error` или зависание.

## Быстрый пример

```powershell
satk formats selftest --quick
satk formats ls models/gta3.img --name infernus
satk formats dump models/gta3.img/infernus.dff
satk formats dump models/coll/weapons.col --level full
```

Что вернётся (сокращённо, третья команда):

```json
{"ok":true,"id":"file:models/gta3.img/infernus.dff","kind":"dff","rw_version":"0x36003","clumps":1,"atomics":15,
 "frames":36,"geoms":15,"materials":64,"verts":3573,"tris":3072,"flags":["normals","embedded_col","matfx",
 "reflection","specular"],"textures_total":13,"bbox":[-1.29,-2.68,-0.79,1.23,2.86,0.68],"embedded_col":"infernus_col"}
```

## Команды

| Команда | MCP | Что делает |
|---|---|---|
| `satk formats selftest [--root R] [--profile vanilla\|installed\|samp] [--quick] [--bench] [--geometry] [--dxt]` | — | разбирает всю игру и сверяет 99 счётчиков с эталонными числами ванильной игры (около 5 с; `--quick` пропускает TXD и DFF, около 1 с; `--geometry` декодирует всю геометрию gta3/gta_int; `--dxt` сравнивает бэкенды декодера DXT на эталонных текстурах и замеряет их скорость) |
| `satk formats ls IMG [--name S] [--ext E] [--limit N] [--cursor C] [--profile P]` | — | записи IMG-архива (путь абсолютный, от текущей папки или от корня игры профиля, регистр не важен) |
| `satk formats dump TARGET [--level stats\|full\|tree] [--limit N] [--cursor C] [--profile P]` | — | разбор файла или записи `<img>/<entry>`: IMG, TXD, DFF, COL, IFP, IDE, IPL, ZON, DAT; `tree` — сырое дерево RW-чанков (в том числе битого файла). Файл под корнем профиля получает канонический SID в `id` (`file:data/maps/la/lae2.ipl`, как бы ни был написан путь); файл вне корня — `path` (и `entry` для записи IMG) без SID |

Своего MCP-инструмента нет ни у одной из трёх команд: агент вызывает их через `satk_op` (например,
`satk_op("formats.dump", {"target": "models/gta3.img/infernus.dff"})`). Из Blender:
`selftest.main(['--root', r'<папка игры>', '--quick'])` (код выхода 0/1, JSON в stdout).

## Как это устроено

- **Пути.** Относительный путь сначала ищется в текущей папке, затем под корнем игры профиля; `resolved_from`
  говорит, где он нашёлся (`cwd`, `profile`, `absolute`). Если файл есть в обоих местах, побеждает текущая папка
  с предупреждением `PATH_SHADOWS`; SID `file:` всегда означает файл игры. SID `model:`, `dff:`, `inst:` и `txd:`
  ищутся в индексе профиля (`satk formats dump model:426` разбирает DFF модели premier; `resolved_from`
  равно `index`).
- **Постраничный вывод.** `--level full` и `tree` обрезают каждый список (строки, `geom_rows`, `frame_names`,
  `effects_list`) по `--limit` (20, не больше 500). Обрезанный ответ перечисляет списки в `truncated`
  (`"frames 20 of 51"`), предупреждает `TRUNCATED` и даёт `next`: повторите с `--cursor <next>`, чтобы получить
  следующую страницу всех списков.

| Модуль | Что |
|---|---|
| `img` | IMG v2 (`VER2`, запись `<IHH24s>`) и v1 (`.dir`); `ImgArchive.open(path)`, `find()` без учёта регистра |
| `rw` | RW-чанки: `iter_children` (ребёнок не больше родителя), `rw_version`, `rw_payload_size` |
| `txd` | заголовки текстур, эффективная альфа, `texture_hash` (blake2b-96 от mip 0 + палитры) |
| `dxt` | `decode_rgba` (бэкенды `pillow` → `numpy` → `python`), `preview_rgba`, `mean_rgba` |
| `dff` | `scan_dff` (кадры, геометрии, материалы, 2dfx, флаги DDL, bbox), `decode_geometry(ies)`, `find_embedded_col` |
| `col` | `iter_col`: COLL/COL2/COL3/COL4, поверхности, теневые грани |
| `ifp`, `zon` | `parse_ifp` (ANP3, ANP2, ANPK), `parse_zon` (`map.zon`, `info.zon`) |
| `gxt` | `load_gxt`/`parse_gxt`: тексты SA (`text/*.gxt`, версия 4, 8/16 бит), таблицы `TKEY`/`TDAT`; ключи хранятся как JAMCRC32 имени в верхнем регистре (`key_hash`), поэтому `Gxt.text("GAN")` → «Ganton» |
| `dat`, `ide`, `ipl` | `parse_dat`, `resolve_ci` (не выходит за корень: диск в любом компоненте пути, `..`, `...`, UNC, имена устройств `NUL`/`CON.txt` → `None`); все секции IDE; текстовые и `bnry` IPL (кватернион как в файле, мировой — `world_quat`) |
| `layout` | порядок регистрации архивов `engine` / `samp`, пространства имён `main/player/anim/cuts` |
| `pe` | секции PE и `va_to_off` (общие для `game` и `re`) |
| `water` | `parse_water`: полигоны `water.dat`/`water1.dat` (3 или 4 вершины по 7 чисел + флаги) |
| `timecyc` | `parse_timecyc`: `timecyc.dat` с семантикой `sscanf` движка (23 погоды × 8 часов; 24-часовой файл тоже) |
| `carcols` | `parse_carcols`: палитра `col` и варианты `car`/`car4` |
| `handling` | `parse_handling`: записи машин, `!` мотоциклов, `%` лодок, `$` авиации; `flag_names` для флагов |
| `objectdat` | `parse_object_dat`: `object.dat` до терминатора `*` (и строки после него с `loaded=False`) |

Подводные камни, зашитые в код:

- `SizeInArchive ≠ 0` → движок берёт его (`ImgEntry.size`); «первый зарегистрированный архив побеждает».
- Данные записи IMG кончаются по концу RW-чанка, а не по сектору; в `player.img` по 3 clump подряд, читаются все,
  индексы кадров и геометрий сквозные.
- RW 0x35000 (16 россыпных DFF), 0x34003 (`outro.txd`); у геометрии с RW < 0x34000 в Struct лишние 12 байт.
- 98 % геометрий — tristrip: обход чередуется, вырожденные треугольники (58 % окон стрипов игры) выбрасываются
  векторно: равные соседние индексы ищутся XOR'ом больших целых по сырым байтам, все стрипы геометрии
  обрабатываются за один проход, цикла по индексам и треугольникам нет. `tris` везде считается после разворота
  стрипов.
- DXT1 + raster 565 непрозрачна (индекс 3 блока `c0 ≤ c1` — чёрный, A=255), DXT1 + 1555 — 1-битная альфа.
- COL сопоставляется по имени, `hdr_model_id` не используется; число вершин COL2+ — максимальный индекс + 1.
- `bbox` DFF — в пространстве модели: корневой кадр — единичная матрица (её заменяет матрица сущности).

Текстовые файлы данных (`water`, `timecyc`, `carcols`, `handling`, `objectdat`; индекс кладёт их в свои
таблицы, см. [index.md](index.md)) читаются так же, как их читает движок, вместе с ошибками ванили:

- `timecyc.dat` читается одним `sscanf` на строку: `%d` для цветов и теней, `%f` для размеров и дальностей.
  Значение, которое не читается, останавливает разбор строки, а оставшиеся поля **берутся из предыдущей
  строки** (локальные переменные движка живут вне цикла). Строка 320 ванили (`255` вместо трёх чисел) даёт
  `nread = 20`. Имена погод берутся из комментариев вида `//////////// EXTRASUNNY_LA`, иначе из
  `timecyc.WEATHERS`.
- `carcols.dat`: `77.93,96` читается как 77, 93, 96 и попадает в `errors`; у модели не больше 8 вариантов;
  модель, указанная дважды, берётся из последней строки.
- `handling.cfg`: после `;the end` ничего не читается; `^`-строки (группы анимаций) только считаются; `0.1s` в
  `$ RCRAIDER` допустим (движок пропускает символ через `%*c`).
- `object.dat`: первая строка, начинающаяся с `*`, останавливает движок; строки после неё возвращаются с
  `loaded=False`.
- `water.dat`: у `water1.dat` нет колонки флагов (`flags = None`).

Скорость на машине автора (тёплый кэш, без нагрузки): selftest целиком — около 4,5 с (DFF около 2,5 с на
416 МБ, TXD около 1 с на 748 МБ); полная геометрия gta3+gta_int — около 3–4 с отдельно (`--quick --geometry`)
и меньше вместе с DFF-стадией (обход чанков общий, в `timings.geometry` тогда только декодирование).
Загруженная машина может работать вдвое медленнее. Декодирование DXT: Pillow `bcn` около 387 Мпикс/с, numpy
около 50, чистый Python около 3,4; превью по концам отрезков — около 97 Мпикс/с исходных пикселей, средний цвет —
около 375. На 7 эталонных текстурах бэкенды совпадают побайтно.

## Ограничения и известные проблемы

- **`dff.atomics` = 19 556.** В старой эталонной таблице стояло 46 108: её прототип добавлял к числу атомиков
  номер кадра из Struct каждого источника света (2 203 света). Пояснение — в `selftest.GOLDEN_NOTES`.
- `models/coll/peds.col` (остаток от Vice City, игра его не грузит): у двух его моделей битое тело. `iter_col`
  по умолчанию отдаёт их с нулевыми счётчиками и пишет причину в `errors`; `strict=True` бросает `FormatError`.
- `info.zon` хранит `level = 1` во всех строках, поэтому остров зоны по этому полю не определить.
- Нативная (консольная) геометрия PS2/Xbox и текстуры платформ, кроме 8/9, не декодируются (`unsupported`).
- Решения общих проблем — в [troubleshooting.md](troubleshooting.md).

## Python-API (если другие пакеты его используют)

```python
from pathlib import Path
from satk.formats.img import ImgArchive
from satk.formats.dff import scan_dff, decode_geometry
from satk.formats.txd import parse_txd, mip0_bytes, palette_bytes
from satk.formats.dxt import decode_rgba, preview_rgba, mean_rgba

root = Path("<папка игры>")                             # корень профиля, который показывает `satk status`
with ImgArchive.open(root / "models" / "gta3.img") as a:
    blob = a.read(a.find("infernus.dff"))
info = scan_dff(blob)                                   # 15 атомиков, 64 материала, 3 072 треугольника
mesh = decode_geometry(blob, info.geoms[0].geom_off)    # Mesh: positions array('f'), tris array('I'), ...
```

- `DffInfo.flags` — биты DDL `dff.flags` (`dff.FLAG_NAMES`), `Material.fx` — биты `dff_mat.fx` (`dff.FX_NAMES`),
  `Material.color_slot` 1..4 отмечает перекрашиваемый материал машины.
- `iter_col(buf, strict=False, errors=[...])`, `parse_zon(text, strict=False, errors=[...])`.
- `base` в `txd` — смещение начала TXD внутри `buf`, одно и то же для всех функций: `parse_txd(arc, base=o)`,
  затем `texture_hash(arc, t, base=o)`, `mip0_bytes(arc, t, base=o)`. Смещения в `TexInfo` (`mip0_off`,
  `pal_off`) отсчитываются от начала TXD, поэтому тот же `TexInfo` годится и для `arc[o:]` с `base=0`.
- `selftest.run(root, profile, skip=("dff", "col"))` пропускает стадии из `selftest.STAGES` (их эталонные
  счётчики не сверяются); `quick=True` пропускает `txd`, `dff` и `texref`.
- `decode_rgba(t, mip0, palette, backend="auto")`: бэкенд важен только для DXT; несжатые и палитровые форматы
  всегда идут общим путём на стандартной библиотеке (срезы и `bytes.translate`). Без Pillow и numpy работает
  бэкенд `python`.
- Файлы данных: у всех парсеров сигнатура `parse_x(text, strict=False, errors=[...])`, текст даёт
  `dat.read_text`:

```python
from satk.formats.dat import read_text, resolve_ci
from satk.formats.handling import parse_handling, flag_names, MODEL_FLAGS
from satk.formats.timecyc import parse_timecyc

h = parse_handling(read_text(resolve_ci(root, "data/handling.cfg")))
inf = h.get("INFERNUS")                                   # HandlingRec, вид "car"
inf.values["max_vel"], flag_names(inf.values["model_flags"], MODEL_FLAGS)   # 240.0 ['IS_LOW', ...]
rows = parse_timecyc(read_text(resolve_ci(root, "data/timecyc.dat")))
rows[4].weather_name, rows[4].hour, rows[4].values["far_clip"]               # 'EXTRASUNNY_LA' 12 800.0
```
