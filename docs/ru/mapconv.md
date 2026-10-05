# `satk map` и `satk ipl`: конвертеры маппинга, бинарный IPL, чистка района

[English version](../en/mapconv.md)

Пакет: `satk.mapconv`. Основание: наш обзор экосистемы маппинга (карты Pawn для SA-MP, `.map` MTA, IPL,
чистильщики карт).

## Что это

Перевод карт между четырьмя форматами: Pawn SA-MP/open.mp (`CreateObject`, `CreateDynamicObject(Ex)`,
материалы, `RemoveBuildingForPlayer`), MTA `.map`, текстовый IPL игры и канонический JSON satk. И проверка
карты по индексу: есть ли такие модели, лежат ли координаты в мире, что удаляют удаления. Pawn читается
**только как литералы**: код не исполняется. Всё пишется в `work\out\mapconv\`, рядом — JSON-отчёт о потерях.

Два сервиса для карты самой игры: `satk ipl decompile|compile` переводит бинарный IPL (`*_streamN.ipl` в
`gta3.img`) в текст и обратно байт в байт; `satk map clean` убирает объекты района вместе с их LOD — копиями IPL
для modloader, скриптом Lua для MTA и строками `RemoveBuildingForPlayer` для SA-MP. ID моделей (свободные ID,
конфликты, переадресация мода) — на [странице `satk id`](../en/idmgr.md) (пока только на английском).

## Быстрый пример

```powershell
satk map convert ipl:lae2 --to mta
satk map validate ipl:lae2
satk map convert tests/mapconv/data/grove_sample.pwn --to mta
satk map validate tests/mapconv/data/grove_sample.pwn
satk ipl decompile ipl:lae2_stream0
satk map clean Ganton --dry-run
```

Что вернётся (сокращённо; пример `grove_sample.pwn` лежит в репозитории, команды — из корня рабочей копии):

```json
{"ok":true,"file":"<workspace>/work/out/mapconv/lae2.map","report":"<workspace>/work/out/mapconv/lae2.mta.report.json","src":"ipl:lae2","from":"ipl","to":"mta","objects":377,"removals":0,"materials":0,"texts":0,"models":0,"errors":0,"warnings":0,"skipped":{"enex":10,"grge":1},"issues":[["info","TILT_IGNORED","file","1 inst have a small tilt …"]]}
{"ok":true,"cols":["sev","code","where","msg"],"rows":[["info","TILT_IGNORED","file","…"]],"n":1,"total":1,"next":null,"format":"ipl","profile":"vanilla","objects":377,"errors":0,"warnings":0}
{"ok":true,"file":"<workspace>/work/out/mapconv/grove_sample.map","from":"pawn","to":"mta","objects":5,"removals":1,"materials":1,"texts":1,"errors":0,"warnings":0}
{"ok":true,"cols":["sev","code","where","msg"],"rows":[["error","MODEL_UNKNOWN","obj 2 (line 7)","model 19379 is not in profile 'vanilla' (an SA-MP object id: validate with --profile samp)"]],"n":1,"total":1,"errors":1}
{"ok":true,"file":"<workspace>/work/out/mapconv/decompiled/lae2_stream0.ipl","src":"ipl:lae2_stream0","inst":377,"cars":5,"bytes":16384,"style":"vanilla","pad":"sector","exact":true}
{"ok":true,"cols":["sid","model","name","role","pos","area"],"rows":[["inst:lae2#7",714,"veg_bevtree2","obj",[2490.62,-1806.57,14.38],0],…],"total":524,"area":"ganton","removed":524,"hd":66,"lods":66,"objs":392,"calls":176,"kept_shared_lods":0,"ipl_files":5,"lod_check":{"files":5,"rows":1445,"kept":921,"hidden":524,"hidden_lods":0,"bad":0},"dry_run":true}
```

Проверка обратной сборки: соберите `file`, который напечатал `ipl decompile`, и сравните с оригиналом —
`satk ipl compile <file> --compare ipl:lae2_stream0` отвечает `"same":true`.

## Команды

| Команда | MCP | Что делает |
|---|---|---|
| `satk map convert SRC --to mta\|pawn\|ipl\|json [--out PATH]` | — (`satk_op`) | читает `.pwn/.inc/.txt`, `.map`, `.ipl` (текст или `bnry`), `.json` или SID `ipl:<имя>` из индекса и пишет в другой формат; `file`, `report`, счётчики, `issues [sev, code, where, msg]` |
| `satk map validate SRC [--profile vanilla] [--strict]` | — | таблица `sev/code/where/msg` + `objects removals materials errors warnings`; `--strict` — ошибка `CHECK_FAILED`, если есть `error` |
| `satk ipl decompile SRC [--out PATH] [--no-names]` | — | файл `bnry` или SID `ipl:<имя>` → текстовый IPL (`inst` + `cars`) в `work\out\mapconv\decompiled\`; `exact` — обратная сборка даст те же байты |
| `satk ipl compile SRC [--out PATH] [--pad auto\|sector\|none] [--compare FILE\|ipl:<имя>]` | — | текстовый IPL → `bnry` в `work\out\mapconv\compiled\`; с `--compare` — `same` и `first_difference` |
| `satk map clean AREA [--to ipl lua pawn] [--interior 0] [--models …] [--shared-lods] [--dry-run]` | — | убирает объекты прямоугольника `x0,y0,x1,y1`, круга `x,y,r` или зоны (`Ganton`, `GAN1`) вместе с LOD; таблица `sid/model/name/role/pos/area`, `lod_check`, файлы в `work\out\mapconv\clean\<имя>\` |

Параметры `convert`:

- `--out`: относительный путь и умолчание (`<имя источника>.<расширение>`) — под `work\out\mapconv\`;
  абсолютный — как есть (существующий файл вне `work\` — только с `--force`). Без `--to` формат берётся
  из расширения `--out`.
- `--pawn-func auto|CreateObject|CreateDynamicObject|CreateDynamicObjectEx`: `auto` — та же функция, что в
  источнике, иначе `CreateDynamicObject`.
- `--materials embed|drop`: в `.map` материалы пишутся дочерними `<material>`/`<materialText>` (расширение
  satk) или выбрасываются. В отчёте они есть всегда.
- `--tilt warn|dontstream`: для IPL — см. «Повороты».
- `--no-names`: не искать имена моделей в индексе (для `id` объектов MTA, имён в IPL и комментариев Pawn).

`validate` проверяет: координаты конечны, `|x|,|y| ≤ 3000` (`OUT_OF_MAP`), `z` в `-200…2500` (`Z_RANGE`);
модель есть в профиле (`MODEL_UNKNOWN`; для ID SA-MP `11682…11753`, `18631…19999` подсказка `--profile samp`);
дубли объектов и удалений (`DUP_OBJECT`, `REMOVE_DUP` — двойное удаление роняет клиент SA-MP); слоты
материалов `0…15`, модель и текстура материала есть в индексе (`MAT_*`); удаление что-то задевает
(`REMOVE_NOTHING`), а если удаляет HD-объект с LOD — подсказывает ID LOD-модели (`REMOVE_NO_LOD`); лимиты
SA-MP (1000 `CreateObject`, около 1000 удалений). Без индекса остаются структурные проверки и `warn: NO_INDEX`.

## Как это устроено

- **Общая модель.** Любой формат читается в одну сцену (`satk.mapconv.scene`): объекты (модель, позиция,
  углы Эйлера SA-MP, `interior`/`world` с `-1` = «все», дальности прорисовки и стриминга, поля MTA, LOD и флаги IPL,
  материалы по слотам), удаления, кастомные модели DL, диагностика с номером строки. JSON — это сцена как есть.
- **Pawn.** Токенизатор понимает комментарии, строки с экранированием, `0x…`, теги `Float:`, массивы `{…}` и
  константы (`OBJECT_MATERIAL_SIZE_*`, `STREAMER_OBJECT_SD`, `true`). Вызов с нелитеральным аргументом
  (`x + 1.0`, `GetX()`) пропускается с `NOT_LITERAL` и строкой. Хэндлы отслеживаются по присваиваниям
  (`tmpobjid = …`, `objs[3] = …`). Порядок аргументов разный: `SetObjectMaterialText(obj, text, index…)`, но
  `SetDynamicObjectMaterialText(obj, index, text…)`. Кодировка — UTF-8, иначе cp1251.
- **MTA `.map`.** Атрибуты как у редактора MTA; `dimension="-1"` — все измерения. То, чего в MTA нет,
  хранится расширениями, которые MTA игнорирует: `allInteriors`, `drawDistance`, `streamDistance`,
  `lodIndex`, `iplFlags`. Поэтому Pawn → `.map` → Pawn проходит без потерь (тест — до 0,01).
- **Повороты.** SA-MP и MTA передают углы в `CMatrix::SetRotate(x, y, z)` = `Rz·Rx·Ry`, они копируются как
  есть. IPL хранит **сопряжённый** кватернион; и если `|qx|,|qy| ≤ 0,05`, игра берёт только курс. Чтение IPL
  повторяет это правило (`TILT_IGNORED`): углы в `.map` такие, как видно в игре. При записи в IPL наклоны
  меньше ≈5,73° пропали бы молча — `TILT_LOST`; `--tilt dontstream` ставит таким объектам флаг
  `dont_stream`, и загрузчик берёт полный поворот (если `qx` и `qy` оба ненулевые; другие эффекты флага не
  изучены).
- **Потери** при записи — предупреждения `LOST_*` в `issues` и в отчёте: в IPL нет материалов, удалений,
  измерений и дистанций; в Pawn нет масштаба и прозрачности MTA (`scale`, `alpha`) и LOD IPL; в `.map` нет кастомных моделей DL.
- **Сверка с игрой.** Тест `tests/mapconv/test_game.py` переводит все 220 IPL ванили (50 935 расстановок)
  IPL → `.map` и сверяет с индексом: модель, позиция (≤ 1e-6 м), поворот (≤ 1e-4°). ≈2,5 с.

## Бинарный IPL

- **Формат.** Заголовок 0x4C байт (сигнатура, шесть счётчиков, шесть пар смещение/размер), записи `inst` по
  40 байт (позиция, кватернион как в файле, модель, interior, LOD) и генераторы машин по 48 байт. В бинарном
  виде есть только `inst` и `cars`; остальные секции `ipl compile` пропускает с `LOST_SECTIONS`.
- **Точный текст.** Числа с плавающей точкой пишутся кратчайшей десятичной записью, которая читается обратно
  в тот же float32 (`-0` остаётся `-0`); interior — беззнаковым числом. Раскладка заголовка и выравнивание
  хранятся в строке-комментарии `# satk-bnry: style=vanilla pad=sector`: у всех файлов ванили поля размеров
  нулевые, а файл дополнен нулями до 2048 байт, как запись IMG. Файл неизвестной раскладки разбирается с
  `exact: false` и `NOT_EXACT`.
- **Сверка с игрой.** Тест `tests/mapconv/test_game.py` разбирает и собирает все 190 бинарных IPL чистой копии
  (41 667 расстановок, 1 045 генераторов машин): каждый файл возвращается бит в бит, примерно за секунду.
- **LOD-индексы** бинарного IPL указывают в секцию `inst` родительского текстового IPL (`lae2_stream0` →
  `lae2.ipl`) и копируются как есть. Сборка текстового IPL с LOD-ссылками, который получен не из
  `ipl decompile`, даёт предупреждение `LOD_SPACE`.

## Чистка района

- **Выбор.** Все расстановки, чья позиция внутри района, в интерьере `--interior` (`-1` — все), по желанию в
  диапазоне `--z z0 z1` и только `--models` (ID или имена с `*` и `?`). Их LOD-родители берутся, где бы они ни
  стояли. LOD убирается, только если уходят все его HD-потомки; LOD, который обслуживает и объект снаружи,
  остаётся (`LOD_SHARED`; `--shared-lods` убирает и его, тогда `LOD_HIDDEN` считает объекты, оставшиеся без
  LOD). LOD внутри района, все потомки которого снаружи, тоже остаётся.
- **Копии для modloader** (`modloader\<файл>.ipl`): полные копии всех текстовых и бинарных IPL с убранными
  объектами, те же строки в том же порядке. Убранная строка сохраняет модель, поворот, флаги и LOD-индекс и
  уходит под землю (`z = -1000`): удаление строк сдвинуло бы LOD-индексы остального файла и файлов
  `*_streamN`, которые в него ссылаются. Меняется только токен `z` (текст) или число float32 `z` (бинарный файл).
  Папку целиком кладут в папку мода modloader.
- **MTA** (`<имя>.lua`): серверный скрипт — `removeWorldModel` при старте ресурса и `restoreWorldModel` при
  остановке. MTA сверяет модель, расстояние в 3D и интерьер и не идёт по LOD-связям, поэтому у LOD свои записи.
- **SA-MP** (`<имя>.pwn`): `stock` со строками `RemoveBuildingForPlayer` для вызова из `OnPlayerConnect`
  (параметра интерьера нет; SA-MP держит около 1000 удалений на игрока — `REMOVE_LIMIT`).
- **Меньше вызовов.** Расстановки одной модели и одного кода интерьера сливаются в одну сферу, если внутри нет
  оставшихся расстановок той же модели (проверка в 2D по всему профилю, поэтому безопасна и для 2D-, и для
  3D-сравнения); иначе группа делится — вплоть до отдельных расстановок с радиусом не больше 0,25 м
  (`--no-merge` — по вызову на расстановку). Ganton: 524 расстановки, 176 вызовов.
- **Сверка LOD** (`lod_check`): копии читаются обратно и построчно сравниваются с оригиналами и индексом
  (модель, LOD-индекс, позиция; у убранных строк отличается только `z`); `bad` должен быть 0.

## Ограничения и известные проблемы

- Pawn — только литералы: циклы, `#define`-макросы, вычисления и массивы координат не разбираются
  (исполнение AMX в песочнице пока не планируется). Препроцессорные строки пропускаются.
- `.db` Texture Studio и `artconfig.txt` пока не читаются; `map convert --to ipl` пишет текстовый IPL (бинарный — `ipl compile`).
- MTA не знает «все интерьеры»: объекты SA-MP с interior `-1` получают `interior="0"` и `allInteriors="true"`;
  в IPL — `area` 0 (`INTERIOR_ALL`; 13 означало бы «видно во всех»).
- Удаления в Pawn пишутся парой строк (модель и LOD), в `.map` — одним элементом с `lodModel`; обратно
  пара не склеивается.
- LOD-ссылки `bnry` указывают в родительский текстовый IPL и при чтении отбрасываются (`LOD_EXTERNAL`).
- Неравномерный `scaleX/Y/Z` в `.map` не поддерживается (`BAD_ELEMENT`).
- `map clean` прячет убранные объекты под землю, а не удаляет: их коллизия и 2D-эффекты (свет) остаются
  там, вне поля зрения. Скрипты, которые ищут здание по позиции (миссия, меняющая модель двери), его не найдут.

## Python-API (если другие пакеты его используют)

```python
from satk.mapconv.pawn import read_pawn, write_pawn
from satk.mapconv.mta import read_mta, write_mta
from satk.mapconv.ipl import read_ipl, write_ipl
from satk.mapconv.rot import euler_quat, quat_euler, ipl_quat, ipl_euler
from satk.mapconv.validate import validate_scene
from satk.mapconv.bnry import decompile, compile_ipl, parse_bnry, encode_bnry, f32_text
from satk.mapconv.clean import parse_area, select, clean, removal_calls
```
