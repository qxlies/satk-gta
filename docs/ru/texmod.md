# `satk texture pack|replace|extract`: моддинг текстур

[English version](../en/texmod.md)

Пакет: `satk.texmod`. Читает TXD через `satk.formats.txd`, проверяет результат через `satk.formats.dxt`.

## Что это

Сборка текстурных модов без внешних программ: выгрузить текстуры TXD в PNG, поправить их, собрать обратно в TXD
(DXT1/DXT3/DXT5 или без сжатия, с мип-уровнями) или заменить несколько текстур в копии игрового TXD. Игра только
читается; всё пишется под `<work>`: выгрузка — в `<work>/out/texmod/<txd>/`, готовый мод — в
`<work>/out/mods/<name>/<файл>.txd` с `README.txt` (раскладка modloader). Каждый записанный TXD перечитывается
парсером satk, новые текстуры декодируются, в ответе — PSNR против исходной картинки.

## Быстрый пример

```powershell
satk texture extract txd:bistro
satk texture replace txd:bistro Plate=bistro/Marble.png Panel=bistro/DinerFloor.png --format dxt1 --name bistro_demo
satk texture pack bistro --name bistro_small --max-size 64
```

Что вернётся (сокращённо):

```json
{"ok":true,"cols":["name","file","fmt","size","mips","state"],"rows":[["vent_64","vent_64.png","X8R8G8B8","64x64",1,"written"],["…"]],"dir":"<work>/out/texmod/bistro","source":"txd:bistro","txd":"bistro.txd"}
{"ok":true,"cols":["name","fmt","size","mips","psnr","source"],"rows":[["Plate","DXT1","128x128",1,40.34,"<work>/out/texmod/bistro/Marble.png"],["Panel","DXT1","128x128",1,40.55,"…/DinerFloor.png"]],"file":"<work>/out/mods/bistro_demo/bistro.txd","mod":"<work>/out/mods/bistro_demo","readme":"…/README.txt","bytes":1658152}
{"ok":true,"cols":["name","fmt","size","mips","psnr","source"],"rows":[["vent_64","X8R8G8B8","64x64",1,100.0,"…/vent_64.png"],["…"]],"file":"<work>/out/mods/bistro_small/bistro_small.txd","…":"…"}
```

Чтобы поставить мод, скопируйте `<work>/out/mods/bistro_demo/` в `<папка игры>/modloader/`.

## Команды

| Команда | MCP | Что делает |
|---|---|---|
| `satk texture extract TXD [--out DIR] [--force]` | — (через `satk_op`) | mip 0 всех текстур в `<имя>.png` + `texmod.json` (имена, форматы, мипы); правленые PNG не перезаписываются без `--force` (`state: kept`, `warn: KEPT`) |
| `satk texture pack DIR [--name X] [--file F] [--format auto] [--mips N] [--quality normal] [--no-pot] [--max-size N]` | — | картинки папки (`.png`, с Pillow ещё `.bmp .tga .jpg .dds …`) → `<work>/out/mods/<X>/<F или X>.txd`; имя текстуры — имя файла |
| `satk texture replace TXD TEX=IMG... [--name X] [--file F] [--format auto] [--mips N] [--add] [...]` | — | копия TXD, где перечисленные текстуры заменены (`--add` — дописаны новые), остальное байт в байт; файл по умолчанию — исходное имя (`bistro.txd`), папка — имя TXD |

Параметры:

- `TXD` — `txd:<имя>` (из индекса), `file:<путь>`, `<img>/<запись>` (`models/gta3.img/bistro.txd`) или путь к `.txd` <!-- linkcheck: ignore -->
  (абсолютный или от корня профиля). У `extract` и `replace` есть `--profile` (по умолчанию `vanilla`).
- Картинки и папки — абсолютный путь, от текущей папки или от `<work>/out/texmod/` (туда пишет `extract`,
  поэтому `bistro/Marble.png` и `pack bistro` работают из любой папки). <!-- linkcheck: ignore -->
- `--format`: `auto` (по умолчанию), `dxt1 dxt3 dxt5 a8r8g8b8 x8r8g8b8 r5g6b5 a1r5g5b5 a4r4g4b4`.
  `auto` для новой текстуры: непрозрачная → DXT1, альфа только 0/255 → DXT1 с 1-битной альфой (`DXT1+a`),
  плавная альфа → DXT5; стороны не кратны 4 → A8R8G8B8/X8R8G8B8. При замене `auto` сохраняет семейство
  заменяемой текстуры: DXT остаётся DXT (вариант выбирает альфа картинки, DXT3 остаётся DXT3), несжатая —
  несжатой (X8R8G8B8 → A8R8G8B8, если у картинки есть альфа). В папке после `extract` `auto` берёт формат из
  `texmod.json`.
- `--mips`: не задан — полная цепочка до 1×1 для новой текстуры и «как было» при замене и в папке после
  `extract`; `0` — без мипов; `N` — не больше `N` уровней.
- `--pot` (по умолчанию) приводит стороны к степени двойки (ближайшей, Lanczos), `--max-size N` уменьшает
  длинную сторону; об изменении размера — `warn: RESIZED`, сторона больше 2048 — `warn: BIG`.
- `--quality`: `fast` (только PCA), `normal` (+ уточнение МНК), `high` (+ перебор концов ±1, в 5 раз
  медленнее, +0,3 дБ).
- Ответ — таблица `name, fmt, size, mips, psnr, source` и пути `file`, `mod`, `readme`. PSNR сравнивает mip 0
  после декодирования с картинкой (RGB; для текстур с альфой — RGB, умноженный на альфу, плюс альфа);
  `100` — без потерь.

## Как это устроено

- **Энкодер DXT** (numpy, `satk.texmod.bc`): концы отрезка — главная ось цвета блока (PCA) и её вариант,
  сжатый на 1/16; затем 2–3 шага МНК по индексам с квантованием в 565 и выбором лучшего на блок. Ошибка
  считается точно так же, как декодирует `satk.formats.dxt` (и игра): разворот 565 и целочисленная
  интерполяция. DXT1 с альфой: блоки с прозрачными пикселями — трёхцветный режим (индекс 3 = прозрачный),
  концы подбираются по непрозрачным. DXT3 — явная 4-битная альфа; DXT5 — пробуются оба режима блока
  (8 значений и 6 значений + точные 0/255), берётся меньшая ошибка.
- **Качество** на 26 текстурах `bistro.txd` в DXT1: в среднем 36,3 дБ (`normal`, минимум 26,9 у витража
  `StainedGlass`); энкодер DDS из Pillow 12 на них же даёт 33,0 дБ. Скорость на 1024×1024: DXT1 `fast` 0,2 с,
  `normal` 0,6 с, `high` 3,2 с; DXT5 `normal` 0,8 с.
- **Мипы** — бокс-фильтр 2×2 (для нечётных сторон — точный по площади); при альфе цвет усредняется с весом
  альфы, поэтому прозрачные тексели не дают тёмной каймы. Размер уровня `i` — `max(1, w>>i) × max(1, h>>i)`,
  уровни DXT меньше 4×4 занимают один блок.
- **TXD** (`satk.texmod.txdwrite`, только стандартная библиотека) пишется как ванильные файлы SA PC: платформа 9
  (D3D9), `deviceId` 2, штамп RW `0x1803FFFF`, rasterFormat DXT1 `0x200` (с альфой `0x100`), DXT3/DXT5 `0x300`,
  X8R8G8B8 `0x600`, A8R8G8B8 `0x500`, `| 0x8000` при мипах; FOURCC `DXTn` или D3DFORMAT; флаги 1 = альфа,
  8 = сжатие; rasterType 4. Фильтр новых текстур — 6 (трилинейный), адресация — wrap. При замене имя (в
  написании TXD), маска, фильтр и адресация берутся у старой текстуры; если появились мипы, фильтр без
  мип-фильтрации повышается (1 → 3, 2 → 6), иначе игра их не использует.
- **Детерминизм:** одинаковый вход — те же байты TXD и тот же README (в нём пути и sha256 картинок, без
  времени). В `README.txt` — по разделу на каждый TXD папки мода; повторный запуск обновляет только свой раздел.

## Ограничения и известные проблемы

- Пишутся только D3D9-текстуры (платформа 9); палитровые (PAL4/PAL8), кубические и D3D8-текстуры не создаются
  (заменить такую текстуру можно — новая будет D3D9). Текстуры чужих платформ `extract` пропускает
  (`warn: UNSUPPORTED`).
- Имя текстуры — ASCII, не длиннее 31 символа. DXT требует сторон, кратных 4 (`--no-pot` + `--format dxt1`
  на 6×6 дают `BAD_PARAMS`).
- `--file` у мода — имя файла, который заменит modloader; для `replace` оставляйте исходное.
- Нужны numpy (энкодер) и Pillow (масштабирование и картинки не в PNG); без Pillow читаются только 8-битные PNG.
- Другие TXD, оставшиеся в папке мода от прошлых запусков, не удаляются: `warn: OTHER_FILES`.

## Python-API (если другие пакеты его используют)

```python
from satk.texmod.encode import encode_level, mip_chain, auto_format, psnr        # (h, w, 4) uint8 -> байты
from satk.texmod.txdwrite import NativeSpec, native_chunk, txd_chunk, rewrite_txd  # стандартная библиотека
from satk.texmod.api import build_texture, load_txd, pack, replace, extract
```
