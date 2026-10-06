# Файлы мира: текст, зоны, вода, цикл времени, население, радар

[English version](../en/worldfiles.md)

Пакет: `satk.worldfiles`.

## Что это

Тотальная конверсия меняет не только модели: тексты игры (`text/*.gxt`), названия зон (`info.zon`,
`map.zon`), воду (`water.dat`), небо и туман (`timecyc.dat`), население улиц (`popcycle.dat`) и карту радара
(144 тайла `radarNN.txd`). satk читает каждый из этих файлов по правилам самого движка и записывает обратно:
неизменённые данные дают те же байты (все пять стоковых GXT, оба файла зон и оба файла воды проходят круг
бит в бит), правка затрагивает только изменённое, а результат — папка для Mod Loader (или Lua-фрагмент MTA
для воды) в `<workspace>\work\out\worldfiles\`. Игра только читается.

## Быстрый пример

```powershell
satk gxt get CRED001 FEM_OK
satk gxt patch american MYZONE="Old Town" CRED001="My mod team" --out docs-gxt
satk zone list --at 2495,-1687
satk zone add OLDTWN --box 2400,-1720,0,2530,-1620,200 --text "Old Town" --dry-run
satk water list --at 1000,-2000,300 --limit 3
satk timecyc get SUNNY_LA 12 --field sky_top far_clip
satk popcycle get GANGLAND weekday 20 --field max_peds max_cars
satk radar export --tile 32 --out radar-small.png
```

Что возвращается (сокращённо):

```json
{"ok":true,"cols":["key","table","text"],"rows":[["CRED001","MAIN","Producer"],["FEM_OK","MAIN","OK"]]}
{"ok":true,"cols":["key","table","old","new"],"rows":[["MYZONE","MAIN","-","Old Town"],["CRED001","MAIN","Producer","My mod team"]],"files":["docs-gxt.fxt"]}
{"ok":true,"rows":[[150,"GAN1","GAN","Ganton",0,1,[2222.56,-1722.33,-89.08],[2632.83,-1628.53,110.92]],[82,"LA","LA","Los Santos",...]],"shown":"GAN1","shown_text":"Ganton"}
{"ok":true,"rows":[["#150 GAN1","partial",...],["#82 LA","inside",...]],"warn":["PARTIAL_OVERLAP: ..."],"zones":379,"dry_run":true}
{"ok":true,"rows":[[127,"quad",592.0,-2112.0,976.0,-1896.0,0.0,"visible"],...],"counts":{"quads":301,"tris":6,"verts":1021},"free":{"quads":0,"tris":0,"verts":0}}
{"ok":true,"cols":["weather","hour","sky_top","far_clip"],"rows":[["SUNNY_LA",12,[30,117,210],800.0]]}
{"ok":true,"cols":["zone_type","day","hours","max_peds","max_cars"],"rows":[["GANGLAND","weekday","20-22",8,9]]}
{"ok":true,"path":"<workspace>/work/out/worldfiles/radar-small.png","size":384,"tiles":144}
```

## Команды

| Команда | MCP | Что делает |
|---|---|---|
| `satk gxt get [KEY ...] [--search TEXT] [--gxt german]` | — (через `satk_op`) | тексты ключей во всех таблицах, поиск по тексту или список таблиц со счётчиками |
| `satk gxt export [american\|file.gxt] [--format json\|dir]` | — | GXT как JSON-документ или папка файлов `<TABLE>.txt` со строками `KEY текст` (UTF-8) |
| `satk gxt write <json\|dir> --out american.gxt` | — | GXT GTA SA (версия 4), проверка обратным чтением |
| `satk gxt patch <american\|file> KEY="текст" ... [--target fxt\|full]` | — | `fxt`: FXT, ключи которого перекрывают GXT; `full`: исправленный `text/american.gxt` |
| `satk gxt keys [--scan]` | — | у скольких ключей известны имена; `--scan` ищет новые в модифицированной игре и кэширует их |
| `satk fxt write KEY="текст" ... \| file.json \| file.txt` | — | FXT для CLEO (`CLEO_TEXT`) или Mod Loader |
| `satk zone list [--at X,Y] [--name GLOB] [--file info\|map]` | — | зоны, их метки и названия; какое имя игра покажет в точке |
| `satk zone check [--file ...]` | — | правила движка, метки без текста, частичные перекрытия, лимиты зон |
| `satk zone add NAME --box X0,Y0,Z0,X1,Y1,Z1 [--text "Имя"]` | — | копия `info.zon` для Mod Loader с новой зоной (+ FXT с названием) |
| `satk zone remove NAME\|INDEX ...`, `zone export`, `zone write <json>` | — | удаление зон; JSON-документ туда и обратно |
| `satk water list [--at X,Y,R]` | — | полигоны воды, занятость лимитов, проблемы для движка |
| `satk water add --rect X0,Y0,X1,Y1 --z Z [--shallow] [--target modloader\|mta]` | — | полная копия `water.dat` или `water.lua` с `createWater` для MTA |
| `satk water remove IDX ...`, `water export`, `water write <json>` | — | удаление полигонов; JSON-документ туда и обратно |
| `satk timecyc get [WEATHER] [HOUR] [--field ...]` | — | значения цикла времени по именам; одна точка — все поля |
| `satk timecyc patch WEATHER HOUR field=value ...` | — | `sky_top=30,117,210`, `far_clip*=1.5`, `sky_top.g+=10`; папка Mod Loader |
| `satk timecyc diff A [B]` | — | два файла (пути или профили) поле за полем |
| `satk popcycle get [ZONE] [DAY] [HOUR]`, `popcycle patch ZONE DAY HOUR field=value ...` | — | население по типу зоны, дню и 2-часовому слоту |
| `satk radar export [--tile 128]` | — | 144 тайла одной PNG (север сверху, 500 м на тайл) |
| `satk radar build <image> \| --from-map` | — | 144 `radarNN.txd` (DXT1, стоковый контейнер), проверка PSNR, превью |

Погода задаётся именем (`SUNNY_LA`), маской (`*_SF`), номером или `all`; часы — точками времени файла
(`0 5 6 7 12 19 20 22`) или `all`. Поля timecyc: `amb amb_obj dir sky_top sky_bot sun_core sun_corona
sun_size sprite_size sprite_bright shadow light_shadow pole_shadow far_clip fog_start light_on_ground
low_clouds bottom_clouds water postfx1 postfx2 cloud_alpha high_light_min water_fog_alpha dir_mult`
(компоненты `.r .g .b .a`; у `postfx1/2` порядок `a r g b`). Типы зон popcycle: `BUSINESS DESERT ENTERTAINMENT
COUNTRYSIDE ... AIRPORT_RUNWAY` (список выводит `satk popcycle get`).

## Как это работает

- **GXT.** Ключи хранятся как JAMCRC32 от имени в верхнем регистре и отсортированы под двоичный поиск игры;
  каждая таблица после `MAIN` повторяет своё имя и начинается с границы 4 байт. Документ сохраняет порядок
  строк файла — поэтому запись бит-в-бит. Имена ключей берутся из списка 14 047 стоковых имён, который идёт
  с satk (85 % из 16 588 ключей); неизвестные ключи показываются как `0x1A2B3C4D` и записываются обратно без
  изменений. Тексты — в кодовой странице шрифтов SA: ASCII плюс буквы с диакритикой европейских версий
  (`é` — байт `0x9E`, `ß` — `0x96`), сверено со стоковыми немецкими, испанскими, французскими и итальянскими
  текстами. `--charset cp1251` — для мод-шрифтов с кириллической раскладкой.
- **FXT.** CLEO и Mod Loader ищут ключи FXT раньше GXT, поэтому `gxt patch` (по умолчанию `--target fxt`)
  меняет или добавляет тексты, не заменяя `american.gxt`.
- **Зоны.** Имена и метки — `char[8]` (7 символов); границы обрезаются до целых; показывается самая маленькая
  зона (ширина + высота), в которой стоит игрок. Лимиты фиксированы: 380 навигационных зон и 39 зон карты, по
  одной из них движок создаёт сам, поэтому в стоковом `info.zon` (378 зон) есть место ещё для одной;
  `zone add` и `zone check` предупреждают о превышении.
- **Вода.** Квад — прямоугольник по осям (низ-лево, низ-право, верх-лево, верх-право). Стоковый `water.dat`
  занимает все 301 квад, все 6 треугольников и все 1021 вершину пулов одиночной игры: для новой воды нужен
  лимит-аджастер или удаление других полигонов. У MTA:SA свои пулы (512 квадов), вода добавляется через
  `createWater`: `water add --target mta`.
- **Timecyc, popcycle.** Правка заменяет только числа изменённых значений; комментарии, табуляции и концы строк
  остаются, поэтому правка и обратная правка дают исходные байты. В стоковом `timecyc.dat` строка 320 содержит
  20 читаемых значений (остальные движок берёт из предыдущей строки); правка этой строки записывает её целиком
  с теми значениями, которые движок действительно использовал. Mod Loader берёт `timecyc.dat` и `popcycle.dat`
  мода целиком.
- **Радар.** Тайл `N = строка * 12 + столбец`, строка 0 — северный край, столбец 0 — западный, по 500 м. Каждый
  TXD содержит одну текстуру 128 x 128 DXT1 с именем файла; satk пишет стоковый контейнер в точности
  (единственное отличие от стоковых файлов — мусор, оставленный оригинальными инструментами в неиспользуемых
  байтах имени). `radar build` заново декодирует свои тайлы и сообщает PSNR относительно исходника (стоковый
  радар после перекодирования: 43,8 дБ). `--from-map` рисует схему: суша, `water.dat`, контуры зданий из
  индекса, пути машин.

## Ограничения и известные проблемы

- GXT от GTA III и Vice City (другие форматы) не читаются; 16-битные GXT читаются и пишутся как UTF-16.
- `--target fxt` требует имён ключей (FXT не может обратиться к ключу по хэшу); для таких — `--target full`.
- Воду можно добавлять только прямоугольниками; треугольники сохраняются и показываются, но не создаются.
- Ресурс MTA для радара satk не пишет: `radar build` — для одиночной игры (Mod Loader) и форков движка.
- `timecyc` читается в стоковом 8-часовом формате и в 24-часовом формате файлов *timecycle24*; другие
  расширенные форматы (больше погод) — нет.

## Python API

```python
from satk.worldfiles.gxt import read_gxt, write_gxt, key_hash
from satk.worldfiles.zon import parse_zon_doc, write_zon, zones_at
from satk.worldfiles.water import parse_water_doc, write_water
from satk.worldfiles.timecyc import TimecycFile, select_rows
from satk.worldfiles.radar import build_tiles, read_tiles
```
