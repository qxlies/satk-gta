# Вьювер: камера, кадры с метками, «что это за объект»

[English version](../en/viewer.md)

Пакет: `satk.viewer`; вьювер — локальный форк Ariane с нативным эндпоинтом SAAP/1 (MCP-инструменты `view_*`).

## Что это

Команды `satk view` управляют целью: `ariane` (офлайн-вьювер, форк Ariane на чистой копии игры), `mock`
(синтетический мир для тестов) и позже `game` (клиент MTA). Можно перелететь к SID, снять кадр с
нумерованными метками и сеткой, спросить «что в этой точке» и получить стабильный SID расстановки
(`inst:lae2_stream0#4`). Всё пишется в `work\out\captures\<yyyymmdd>\` (PNG + файл-спутник `.json`).

## Быстрый пример

```powershell
satk view pick 400 300 --target mock
satk view capture --target mock --marks 4 --grid
satk view goto --id inst:lae2_stream0#4 --target mock
```

Что вернётся (сокращённо):

```json
{"ok":true,"cols":["px","py","id","model","name","x","y","z","dist","link"],"rows":[[400.0,300.0,"inst:lae2_stream0#4","model:17613","lae2_roads89",2489.36,-1669.24,12.9,69.25,"nearest"]],"target":"mock","mode":"visible"}
{"ok":true,"id":"cap:20261005-111222-fd82","file":"<workspace>/work/out/captures/20261005/20261005-111222-fd82.png","marks_file":"…_marks.png","grid_file":"…_grid.png","legend":[[1,"inst:lae2_stream0#4","lae2_roads89",0.0852],[2,"inst:mock#3","mock_grove_house_b",0.0431],…],"settled":true,"w":960,"h":540}
{"ok":true,"target":"mock","pose":{"pos":[2489.355,-1744.056,55.957],"look":[2489.355,-1668.57,12.375],"fov_h_deg":70.0},"framed":"inst:lae2_stream0#4","radius":32.87}
```

Без запущенного эндпоинта `mock` в каждом ответе есть ещё предупреждение, что использована цель `mock` внутри процесса.

С настоящим вьювером (окно Ariane откроется на рабочем столе, загрузка карты — секунды):

<!-- docs-smoke: skip запускает Ariane (видимое окно) -->
```powershell
satk view start --target ariane --window 960x540
satk view capture --pos 2495,-1720,60 --look 2495,-1670,15 --fov 70 --width 1920 --height 1080 --layers color,ids,depth --marks 8
satk view pick 480 270
satk view set --overlays col,zones --lod hd
satk view conformance --target ariane
satk view stop
```

Сборка форка с нативным эндпоинтом (P1) отвечает `"proto":"saap/1","impl":"ariane-satk"`: кадр любого
размера независимо от окна, ID-буфер (метки точные, по доле пикселей), глубина, `pick` по тому, что
нарисовано, `view set`. Старая сборка — `"proto":"ariane-ipc/1"` через адаптер (ниже).

## Команды

| Команда | MCP | Что делает |
|---|---|---|
| `satk view control start\|stop\|status [--target T] [--profile P] [--window WxH[+X+Y]]` (и короткие `satk view start\|stop\|status`) | `view_control` | запуск (`ariane`: свободный порт, токен, `--game-dir`, `--data-dir work\viewer`, `--window 1280x720+0+0`, `--no-vsync`, `SATK_AGENT_*` для нативного SAAP, ожидание до 90 с), остановка (`quit` → 10 с → `WM_CLOSE` → завершение, только проверенного процесса), состояние; `window` везде `[w, h]` |
| `satk view goto (--id SID \| --pos X,Y,Z [--look X,Y,Z \| --ypr Y,P,R] \| --bm NAME) [--fov 70] [--dist M]` | `view_goto` | камера к SID (кадрирование по ограничивающей сфере: `2,5·r + 5` м, 30° сверху), к позиции или закладке |
| `satk view capture [--pos … --look … \| --ypr … \| --pose JSON \| --bm NAME] [--fov F] [--width --height] [--layers color,ids,depth] [--marks N] [--grid] [--env JSON] [--compare-to CAP] [--no-settle] [--inline]` | `view_capture` | кадр + файл-спутник; метки, сетка A…H × 1…6, сравнение (картинка разницы и SSIM); `--inline` — MCP-клиент покажет картинку |
| `satk view pick PX PY [PX PY …] \| --cells D3,E4 [--capture CAP]` | `view_pick` | таблица `[px, py, id, model, name, x, y, z, dist, link]` |
| `satk view set [--time HH:MM] [--weather N] [--overlays col,zones,paths,tcyc] [--lod normal\|hd\|lod] [--draw-dist K] [--hide SID…] [--highlight SID…] [--postfx]` | `view_set` | окружение и вид; `UNSUPPORTED`, если у цели нет такой возможности |
| `satk view bookmark save\|list\|rm [NAME] [--pose JSON] [--note TEXT]` | `view_bookmark` | закладки камеры; адресуются как `bm:NAME` |
| `satk view replay <sidecar.json\|cap:…>` | — | повтор захвата, сравнение sha256 (при расхождении — SSIM) |
| `satk view conformance --target T` · `satk view mock` | — | см. [saap.md](saap.md) |

## Как это устроено

- **Цели и бэкенды.** По файлам `work\run\endpoints\` выбирается бэкенд: нативный SAAP (`mock`, Ariane с P1,
  позже MTA) или адаптер `ARIANE_IPC/1` для сборок Ariane без P1. `--target mock` без запущенного
  эндпоинта работает внутри процесса (с предупреждением).
- **Нативный Ariane (P1).** `view start` передаёт Ariane и переменные моста, и `SATK_AGENT_PORT=0`,
  `SATK_AGENT_TOKEN` (тот же токен), `SATK_AGENT_DESCRIPTOR`, `SATK_AGENT_OUT_ROOT` (= `work\`). Сборка с
  эндпоинтом (`ariane.build.json`: `saap_endpoint`) сама пишет `work\run\endpoints\ariane.json` с
  `protocol:"saap/1"`; её возможности: `core camera world.settle capture capture.size capture.ids capture.depth
  pick pick.visible entity.query entity.inspect env view asset.render`. Кадр рисуется вне экрана во временную
  камеру нужного размера (до 7680×4320) — окно своего вида не меняет; перед рендером мир догружается, пока
  стриминг не затихнет. Слои: `color` (с небом, водой и постэффектами; оверлеи тоже, если их явно не скрыли),
  `ids` (`<prefix>.ids.png`, `id = r<<16|g<<8|b` → `ids_legend`), `depth` (`<prefix>.depth.f32`, float32
  по оси взгляда, `+inf` — небо). Замер: 1920×1080 с тремя слоями при окне 960×540 — 0,5 с; 251 цвет
  ID-буфера в 5 видах (Гроув-стрит, даунтаун, Сан-Фиерро, Лас-Вентурас, Санта-Мария) — 100 % дают SID индекса.
  `pick` по умолчанию `visible`: попадает в крону пальмы, у которой нет коллизии (коллизионный луч уходит в
  землю). `entity.inspect` — TXD, текстуры материалов и всего TXD, 2DFX, LOD-родитель и дети, коллизия.
- **Адаптер Ariane** переводит SAAP в команды моста: `camera`, `capture_pose` с `settle_frames` (патч форка),
  `raycast_segment`, `inspect_zone_page`, `environment`, `asset_preview`, `quit`. `pick` и в окне, и по
  кадру строит свой луч на пиксель (в окне — ровно тот, что строит `screen_to_world`: базис из
  `camera_context`) и спрашивает `raycast_segment`. Сам `screen_to_world` ищет по списку отрисовки и там, где LOD
  и HD-модель совпадают в точке попадания, отдаёт LOD (Гроув-стрит, 82 м: `lod1carlshou1_lae` вместо
  `carlshou1_lae2`); `raycast_segment` пропускает LOD-родителей. `screen_to_world` — только запасной путь для
  моста без `raycast_segment`.
  FOV пересчитывается точно (librw считает `fov` для 4:3; проверено на живом Ariane: 70° → 69,96° от края до края).
  Размер кадра у адаптера — размер окна (нет `capture.size`).
- **Профиль.** SID разрешаются в том профиле игры, с которым запущен вьювер (`--profile` у `view start`,
  записан в `work\run\sessions\<role>.json`); без запущенного вьювера — `vanilla`.
- **Процесс вьювера.** В файлах обнаружения есть путь к исполняемому файлу и время старта процесса. Windows переиспользует pid:
  если Ariane упал, а его pid занял другой процесс, `status` говорит `stale:true`, `start` заменяет файлы и
  запускает новый вьювер, `stop` только удаляет файлы — чужому процессу не уходят ни `WM_CLOSE`, ни завершение.
- **Идентичность.** Форк Ariane сообщает IPL и индекс инстанса → `link:"exact"`. Иначе SID ищется в
  индексе по модели и позиции (допуск 5 см): `nearest`/`ambiguous`/`none`. Пока индекс не собран
  (`satk index build`), используется встроенный `FakeIndexDB`, и в ответе есть предупреждение
  `INDEX_MISSING`.
- **Метки.** С ID-буфером (`mock`, Ariane с P1) — точно, по доле пикселей. Без него — `approx:true`:
  сетка 16×9 лучей `pick` в позе кадра (учитывает перекрытия, SID точные; ~2 с на Ariane), а если у цели нет
  `pick` — проекция AABB из индекса.
- **Файл-спутник** (`.json` рядом с PNG) хранит цель, сборку, позу, окружение, размер, `settled`, легенду, sha256 — `satk view replay`
  повторяет кадр. На Ariane повтор дал тот же sha256.
- Чистая копия игры не меняется: Ariane запускается с `--game-dir` и запретом записи в IMG.

## Ограничения и известные проблемы

- Свёрнутое окно Ariane не рисует (D3D9): `satk view status` отвечает `NOT_READY` с подсказкой.
- У адаптера Ariane (сборка без P1) нет `capture.size/ids/depth`, `view`, `pick.visible`; у Ariane вообще
  нет `log`, `console`, `lua`, `mem.read` (`UNSUPPORTED`).
- Нативный Ariane: ID и глубина — только инстансы карты (вода и небо — `0`/`+inf`); `asset.render` с фиксированным
  углом места и фоном (`el`, `bg` не учитываются); крен камеры (`ypr[2]`) игнорируется; ссылка `i<id>` живёт, пока
  жив процесс. MSAA при агентном запуске по-прежнему выключен; с `--keep-msaa` захват моста теперь
  сводит MSAA через `StretchRect` и больше не пустой.
- `pick` через адаптер — по коллизии (`mode:"collision"`); объекты без COL не попадаются. Если луч всё же попал в
  LOD-инстанс (LOD без HD-детей), в ответе `lod_rows` и предупреждение `LOD: …` — это не HD-модель.
- Решения — в [troubleshooting.md](troubleshooting.md).

## Python-API

```python
from satk.viewer import api
cap = api.capture("mock", marks=4, grid=True)
rows = api.pick([[400, 300]], target="mock")["rows"]
```
