# Быстрый старт: от нуля до первого скриншота

[English version](../en/quickstart.md)

<!-- Не больше 10 команд до первого скриншота (проверяет tests/e2e/test_docs_layout.py). Пройдено руками
     2026-10-05 в полном рабочем пространстве разработчика (нативная сборка Ariane): ответы ниже настоящие,
     сокращены; рабочий каталог был отдельный, поэтому его пути заменены на work\. -->

Нужно: Windows, Python 3.12, 3.13 или 3.14 (или переносимый архив) и папка с GTA San Andreas; сначала настройте
satk по [install.md](install.md). Здесь `satk` — это `tools\satk.cmd` вашего клона (в Git Bash — `tools/satk.sh`),
запущенный из папки рабочего пространства, или консоль переносимого архива. Ответы ниже получены в полном рабочем
пространстве разработчика ([workspace-gta.md](workspace-gta.md)): чистая копия 1.0 US `<workspace>\gta-sa-clean`
(профиль `vanilla`) и собранный вьювер Ariane `<workspace>\viewer\ariane\bin\ariane.exe`. С вашей папкой игры числа
будут другими; без вьювера шаги 7–10 ответят `NOT_READY` (проверяемая версия в конце использует тестовую цель
`mock`). Если всё уже установлено и собрано, десять команд занимают около 20 секунд.

## Десять команд

```powershell
# 1. Один раз, в папке рабочего пространства: venv на Python 3.12-3.14 и пакеты для работы (Pillow, numpy, mcp; ~70 МБ из сети)
powershell -ExecutionPolicy Bypass -File tools\scripts\bootstrap.ps1 -Deps

# 2. Всё ли на месте: Python, пакеты, копия игры, индекс, Blender, MSBuild, вьювер (27 проверок)
satk doctor

# 3. Чистая копия цела: 416 файлов сверены с манифестом
satk game verify

# 4. Индекс всех ассетов и расстановок профиля vanilla (~8 с; потом — только когда игра поменялась)
satk index build

# 5. Поиск по имени: модели, TXD, текстуры и анимации, в имени которых есть «grove»
satk asset find "*grove*"

# 6. Все 13 текстур машины Infernus одним пронумерованным листом (в ответе путь к PNG и легенда)
satk texture image model:411 --mode sheet

# 7. Запустить вьювер: окно 960x540 в левом верхнем углу (не сворачивайте: свёрнутое окно ничего не рисует)
satk view start --window 960x540

# 8. Камера на Гроув-стрит на уровне улицы, взгляд на юг — на дом Си-Джея
satk view goto --pos 2490,-1655,16 --look 2500,-1700,16

# 9. Кадр с 6 нумерованными объектами: в ответе путь к *_marks.png и легенда «номер -> SID»
satk view capture --marks 6

# 10. Закрыть вьювер
satk view stop
```

С переносимым архивом (со страницы [Releases](https://github.com/qxlies/satk-gta/releases), см.
[release.md](release.md)) шаг 1 не нужен: дважды щёлкните `Start satk.cmd` — откроется консоль, в которой работает
`satk`.

## Что вы увидите

Время и ответы настоящего прогона (сокращены):

| Шаг | Время | Главное в ответе |
|---|---|---|
| 1 | 2,4 с (всё уже установлено; в первый раз — загрузка) | `Requirement already satisfied …`, `{"ok":true,"satk":"0.1.0","python":"3.12.10",…}` |
| 2 | 2,3 с | `counts.ok: 25`, `fail: 0`; до шага 4 `status: warn` — это нормально: `index_fresh` (исправление: `satk index build`) и необязательная `kb` (исправление: `satk kb build`) |
| 3 | 0,9 с | `{"ok":true,"files":416,"bytes":5029186364,"mismatch":0,"missing":0,"extra":0,"exe":"hoodlum-stock","manifest":"ok","protected":"8/8",…}` |
| 4 | 8,6 с | `"counts":{"txd":4046,"texture":32878,"dff":15356,"inst":50935,…},"lod":{"links":6103,"unresolved":0}` |
| 5 | 0,8 с | 20 строк из 24 (`"n":20,"total":24,"next":"o20"`; остальные 4 — `--cursor o20` или `--limit 50`): `dff:csgrove1`, 4 TXD (`txd:11grove` …), 11 текстур (`tex:tags_lafront/grove` …), анимации `ifp:grove1a` … |
| 6 | 0,9 с | `"files":["…/work/out/sheets/model_411-….png"],"legend":[[1,"tex:vehicle/carpback","16x16 X8R8G8B8"],…13 строк]` |
| 7 | 3,7 с | `{"up":true,"proto":"saap/1","impl":"ariane-satk","caps":["core","camera",…],"window":[960,540],"seconds":2.8}` |
| 8 | < 1 с | `{"pose":{"pos":[2490,-1655,16],"look":[2500,-1700,16],"fov_h_deg":70}}` |
| 9 | 1,1 с | легенда ниже, `"settled":true`, `"w":960,"h":540`, `"sha256":"013efcf1…"` |
| 10 | < 1 с | `{"up":false,"stopped":true,"how":"quit"}` |

Легенда кадра (шаг 9): номер на картинке → SID расстановки → имя модели → доля кадра.

```json
[[1,"inst:lae2_stream2#127","hub_grnd_alpha",0.2239],[2,"inst:lae2_stream0#4","lae2_roads89",0.1349],
 [3,"inst:lae2_stream0#8","carlshou1_lae2",0.0796],[4,"inst:lae2_stream2#102","veg_palmbig14",0.0685],
 [5,"inst:lae2_stream0#39","ganghous05_lax",0.0358],[6,"inst:lae2_stream2#126","hubst4alpha",0.0333]]
```

Метка 3 — дом Си-Джея (`carlshou1_lae2`) прямо по курсу, метка 5 — дом слева от него, метка 4 — пальма.
Любой SID можно раскрыть: `satk asset get inst:lae2_stream0#39` → модель `model:3646` `ganghous05_LAx`, файл
`ipl:lae2_stream0`, его LOD и габариты.

Куда всё легло:

- листы — `<workspace>\work\out\sheets\`, кадры — `<workspace>\work\out\captures\<дата>\`;
- рядом с кадром: `<id>.png` (чистый кадр), `<id>_marks.png` (с номерами), `<id>.ids.png` (ID-буфер) и
  `<id>.json` (файл-спутник: поза камеры, время, погода, легенда, sha256); `satk view replay <файл-спутник>`
  повторит кадр и сверит хэш;
- лог вьювера — `<workspace>\work\logs\viewer-ariane.log`.

Ariane — «быстрые глаза»: нет теней, людей, машин и частиц, LOD приблизительный. Наш форк отвечает
`"proto":"saap/1"` и умеет ID-буфер, поэтому метки точные (доли считаются по пикселям). Размер кадра задают
`--width`/`--height` команды `view capture` (по умолчанию 960x540) независимо от размера окна; `--window 960x540`
лишь делает окно маленьким. Старая сборка Ariane без нативного эндпоинта отвечает `"proto":"ariane-ipc/1"`:
у неё кадр равен окну, а метки приблизительные (`approx:true`), см. [viewer.md](viewer.md).

## То же самое через ИИ-ассистента

Если MCP-сервер `satk` зарегистрирован (Claude Code — `<workspace>\.mcp.json`, проверка —
`satk mcp config --check`; другие клиенты — [ai.md](ai.md)), попросите ассистента, например: «Найди текстуры
grove и покажи их одним листом» или «Слетай на Гроув-стрит и скажи, что за здание слева». Он вызовет те же
операции (`asset_find`, `texture_image`, `view_control`, `view_capture`, `asset_get`) — по 5–6 вызовов на вопрос.

## Быстрый пример

Проверяемая версия (её выполняет `satk dev docs-smoke`; вьювер заменён тестовой целью `mock`, которая без
запущенного эндпоинта работает прямо в процессе и сообщает об этом в `warn`):

```powershell
satk game verify
satk index build
satk asset find "*grove*"
satk texture image tex:tags_lafront/grove --mode sheet
satk view capture --target mock --marks 4
```

## Дальше

- [Идентификаторы (SID)](ids.md), [типовые задачи](workflows.md), [если что-то не работает](troubleshooting.md).
- Страницы компонентов — в [оглавлении](README.md).
