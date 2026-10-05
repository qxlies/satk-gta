# `satk rw`, `col`, `img`: запись RenderWare без Blender

[English version](../en/rw.md)

Пакет: `satk.rw`.

## Что это

Писатели DFF, COL и IMG на чистом Python (только стандартная библиотека), без Blender и без GPL-кода. Нужны там,
где DragonFF слишком медленный или вообще не подходит: «поменять ссылку на текстуру в 300 DFF», «перештамповать
VC-модель под SA», «собрать коллизию из JSON», «собрать IMG для SA-MP DL или раздачи мода».

Игра только читается. Всё, что пишется, — **новые** файлы под `<work>/out/rw/` (или по явному пути вне
папок игры). Существующие IMG на запись не открываются никогда.

## Быстрый пример

```powershell
satk rw patch models/gta3.img/infernus.dff --rename-tex vehiclelights128=mylights128 --out docs-demo
satk formats ls models/gta3.img --name infernus
satk img build docs-demo --base models/gta3.img --include infernus.txd --out docs-demo --overwrite
satk img diff models/gta3.img docs-demo.img --change changed
satk col export models/gta3.img/infernus.dff --out docs-demo-infernus
satk col write docs-demo-infernus.json --out docs-demo-infernus
satk rw roundtrip models/gta_int.img --kind col
```

Что вернётся (сокращённо, первая и четвёртая команды):

```json
{"ok":true,"cols":["name","status","changes","out"],
 "rows":[["infernus.dff","written","texture:11","<work>/out/rw/docs-demo/infernus.dff"]],"written":1}
{"ok":true,"cols":["name","change","size_a","size_b"],"rows":[["infernus.dff","changed",215040,215040]],
 "entries_a":16316,"entries_b":2,"same":1,"changed":1,"added":0,"removed":16314,"identical":false}
```

После `rw patch` команда `satk formats dump <путь из out>` показывает новое имя текстуры.

## Команды

| Команда | MCP | Что делает |
|---|---|---|
| `satk rw patch DFF… [--rename-tex OLD=NEW…] [--rw-version iii\|vc\|sa] [--night-colors add\|remove] [--recalc-normals] [--material-color N=R,G,B[,A]…] [--out DIR] [--dry-run]` | — | правка DFF: файлы, папки (их `*.dff`), IMG целиком (все DFF) или `<img>/<entry>`; копии изменённых файлов — в `<work>/out/rw/<DIR>/` (по умолчанию `patch`) |
| `satk rw roundtrip [TARGET] [--kind dff\|col\|all] [--engine satk\|rwfury] [--no-restamp] [--step N]` | — | замер круговой записи бит в бит: вся игра, IMG, папка или файл; классы расхождений; `--no-restamp` пропускает цикл SA → VC → SA |
| `satk col write SRC [--out NAME] [--version 1\|2\|3\|4]` | — | COL из JSON-файла или JSON-строки; результат перечитывается независимым парсером (`verified`) |
| `satk col export TARGET [--model NAME] [--out NAME] [--no-raw]` | — | `.col`, запись IMG или встроенная COL машины → JSON, который принимает `col write` |
| `satk img build SRC… --out NAME [--base IMG] [--include PAT…] [--remove PAT…] [--version 2\|1] [--recursive] [--overwrite]` | — | новый IMG (VER2; `--version 1` — `.img` + `.dir` для III/VC) из папок и файлов и/или записей другого архива |
| `satk img diff A B [--change added\|removed\|changed] [--no-content]` | — | сравнение двух IMG по именам и содержимому (нулевое выравнивание секторов разницей не считается) |

Своего MCP-инструмента ни у одной из этих команд нет: агент вызывает их через `satk_op`. У всех команд есть
`--profile` (по умолчанию `vanilla`).

Пути на входе: абсолютные, от корня игры профиля (регистр не важен, `<архив>.img/<запись>`), от текущей
папки или имя внутри `<work>/out/rw/`. Поэтому `rw patch --out x` сразу продолжается `img build x`.
Источники `img build` — позиционные аргументы: пишется `satk img build ПАПКА --out NEW.img`.

### `rw patch`

- `--rename-tex` сравнивает имена без учёта регистра и переименовывает основную текстуру, маску,
  текстуры эффектов MatFX (окружение, рельеф, dual) и текстуру specular. Имя — до 31 символа ASCII
  (у specular — до 23).
- `--rw-version` ставит штамп 3.1.0.1 / 3.3.0.2 / 3.6.0.3 на все чанки и меняет то, что librw
  пишет по-разному: 12 байт свойств поверхности в Struct геометрии ниже 3.4, старый формат Skin PLG ниже 3.4
  (`0xDEADDEAD` перед матрицами, без списка костей), счётчики света и камер в Clump начиная с 3.3. Для III
  RW-источники света клампа удаляются (`lights_dropped`): формат 3.1 их не считает.
- `--night-colors add` копирует дневные prelit-цвета в ночные там, где их нет (без prelit — предупреждение
  `NO_PRELIT`); `remove` удаляет плагин.
- `--recalc-normals` считает гладкие нормали (сумма нормалей граней с весом по площади) и ставит флаг NORMALS.
- `--material-color`: `N` — номер строки `satk formats dump … --level full`, `G:I` — геометрия и слот;
  цвет — `R,G,B[,A]` или `#RRGGBB[AA]`. На геометрию ставится флаг MODULATE_MATERIAL_COLOR, иначе цвет не виден.
- Неизменённые чанки записываются байт в байт, у изменённых пересчитываются размеры вверх по дереву.
  Файл, в котором ничего не поменялось, не пишется (`unchanged`).

### JSON для `col write`

```json
{"models": [{
  "name": "mybox", "version": 3,
  "spheres": [{"center": [0, 0, 1], "radius": 1.5, "surface": "CONCRETE"}],
  "boxes": [{"min": [-1, -1, 0], "max": [1, 1, 2], "surface": 4}],
  "vertices": [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
  "faces": [[0, 1, 2, "TARMAC", 0]],
  "shadow": {"vertices": [[0, 0, 0], [1, 0, 0], [0, 1, 0]], "faces": [[0, 1, 2, 0, 0]]}
}]}
```

- Поверхность — число, имя из таблицы rwfury (`TARMAC`, `GRASS_SHORT_LUSH`…, регистр не важен),
  `[material, flag, brightness, light]` или `{"material": …, "light": …}`. Грань COL2/3 — `[a, b, c,
  material, light]`, грань COLL — `[a, b, c, material, flag, brightness, light]`.
- Если `bounds`, `flags` или `face_groups` не заданы, они считаются так, как в файлах Rockstar: флаги `0x02`
  (есть сферы, боксы или грани), `0x08` (группы граней), `0x10` (тень); группы граней — от 81 грани,
  непрерывными диапазонами не больше 50 граней (грани переставляются); `"face_groups": "none"` — без групп.
- Координаты вершин COL2/3 хранятся как `int16 / 128`: допустимо ±256 м, шаг 1/128 м.
- `col export` пишет то же самое плюс `raw` — мусорные байты Rockstar (хвост имени после NUL, выравнивание
  вершин). С `raw` пара `export` → `write` даёт исходный файл бит в бит; `--no-raw` их убирает.

## Как это устроено

| Модуль | Что |
|---|---|
| `chunk` | дерево чанков без потерь: контейнеры librw разбираются на детей, остальное хранится байтами; при записи размеры пересчитываются |
| `codecs` | типизированные кодеки: Struct геометрии, материала и клампа, кадры, строки, Skin, ночные цвета, MatFX, specular, BinMesh, 2dEffect |
| `dff` | `DffDoc`: переименование текстур, версия RW, ночные цвета, нормали, цвет материала |
| `col` | точный кодек COLL/COL2/COL3/COL4, JSON, границы, флаги и группы граней для новых моделей |
| `img` | сборка VER2/VER1 и сравнение архивов |
| `roundtrip` | замеры на установке игры (`rw roundtrip`, `tests/golden/rw_roundtrip.json`) |
| `vendor` | загрузка `vendor/rwfury` (MIT, 0.6.1, без изменений) |

**Почему не на rwfury.** Писатели rwfury пересобирают файл из модели и на ванили дают 0 из 15 356 DFF и 0 из
10 169 записей COL бит в бит. Теряются слово `unused` материала, флаги кадров и фильтра текстуры, плагины
Breakable и MatFX атомика, мусор после NUL в строках и именах COL. Поэтому писатели у satk свои, по семантике
потоков librw (MIT). rwfury вендорирован и используется для имён поверхностей, как независимый читатель в тестах
и как базовая линия эталонных чисел.

**Замер на ванили** (`satk rw roundtrip`, около 40 с, числа — в `tests/golden/rw_roundtrip.json`):

| Что | Бит в бит |
|---|---|
| DFF, дерево чанков (все 15 356: gta3, gta_int, player, cutscene и россыпь) | 15 356 (100 %) |
| DFF, все Struct и плагины через типизированные кодеки | 15 356 (100 %) |
| DFF, SA → VC → SA | 15 223 (99,1 %): 117 — список костей Skin (в формате VC его нет), 16 — файлы RW 3.5 |
| COL, файлы (254) | 254 (100 %) |
| COL, записи через кодек (10 169) | 10 167: 2 битые записи `models/coll/peds.col`, игра его не грузит |
| COL, `export` → `write` через JSON | 10 165 (+ 212 из 212 встроенных в машины) |

**Известные ошибки писателей IMG (`BUGS.md` INU_Core).** Новый архив раскладывается до записи первого байта,
поэтому каталог VER2 никогда не налезает на данные. Это покрыто тестами `tests/rw/test_img.py`:

- 1, 63, 64, 65, 67, 100 и 3 000 записей;
- пересборка базы на 64+ записей;
- плотная база плюс 67 новых;
- VER1 без заголовка VER2;
- запись `<IHH24s>` с нулевым размером в архиве;
- лимит 65 535 секторов;
- имена до 23 символов с NUL, без дублей.

## Ограничения и известные проблемы

- TXD здесь не пишется: для текстур есть `satk texture …` ([texmod.md](texmod.md)). Эффекты 2DFX в JSON пока не
  выгружаются.
- Платформенные (PS2/Xbox) геометрии не перештамповываются и не получают нормали.
- SA → VC → SA меняет порядок списка костей Skin: librw пишет его отсортированным. Семантически это то же самое.
- `img build --base` копирует записи как есть (с выравниванием) и не проверяет их содержимое.
- В установке без `vendor/rwfury` (колесо без папки `vendor`) имена поверхностей недоступны: числа работают.

## Python-API

```python
from satk.rw.dff import DffDoc
doc = DffDoc.parse(blob)
doc.rename_textures({"vehiclelights128": "mylights128"})
new_blob = doc.to_bytes()
```
