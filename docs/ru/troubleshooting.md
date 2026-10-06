# Если что-то не работает

[English version](../en/troubleshooting.md)

<!-- cp1251, свёрнутое окно, MSAA, место на диске, «индекс устарел» и то, что всплыло при проверке пакетов.
     Перепроверено 2026-10-05; тексты ошибок настоящие. -->

Первое действие почти всегда одно: `satk doctor`. Он выполняет все проверки (в полном рабочем пространстве
разработчика их 27) и к каждой проблеме даёт команду-исправление (`fix`). Обзор состояния — `satk status`. Коды
ошибок и предупреждений перечислены в [docs/agent/errors.md](../agent/errors.md). Чтобы сообщить о проблеме в самом
satk, `satk bug-report` пишет закрытый локальный отчёт ([bugreport.md](bugreport.md)).

## Кракозябры в консоли (cp1251)

Консоль Windows по умолчанию работает в cp1251. Шимы `satk.cmd`/`satk.sh` ставят `PYTHONUTF8=1` и запускают
Python с `-X utf8`, поэтому вывод satk корректен в любой кодировке консоли (символы, которых в ней нет,
заменяются).

- Свои скрипты запускайте так же: `py -3.12 -X utf8 script.py`, а в коде всегда указывайте `encoding="utf-8"`.
- Если в PowerShell ломается вывод *других* программ: `chcp 65001` в этой сессии.
- PowerShell 5.1 `Set-Content -Encoding utf8` и `Out-File -Encoding utf8` пишут BOM в начало файла; такой JSON
  обычный `json.load` не читает (нужно `utf-8-sig`).

## Поиск ничего не находит

- `satk asset find grove` возвращает только **точные** имена (9 текстур `grove`); в `warn` сказано, сколько ещё
  имён содержат строку (`MORE: 15 other names contain 'grove'`). Подстрока ищется шаблоном:
  `satk asset find "*grove*"`.
- В **cmd.exe** шаблон берите в двойные кавычки: `'*grove*'` в одинарных кавычках cmd передаёт вместе с
  кавычками, и поиск молча возвращает 0 строк. В PowerShell и Git Bash работают оба вида кавычек.
- Зоны называются кодами `info.zon`/`map.zon` (`GAN1` — Гантон, `VE` — Лас-Вентурас), а игровые названия лежат в
  поле `title`: `satk asset find ganton --kind zone` находит `zone:gan1`. Зоны `grove` нет: Гроув-стрит — около
  точки (2495, −1687); сохраните её как закладку вьювера командой `satk view bookmark save grove_center`, пока
  камера там.
- Через MCP параметр поиска называется `query`, а не `q` (`BAD_PARAMS: asset.find: unknown parameter 'q'`).

## CLI: `the following arguments are required`

Параметр-список (`--fields`, `--pos`, `--look`, `--kinds`, `--layers` …) забирает все значения, которые идут за
ним, в том числе позиционные аргументы. Обязательные позиционные аргументы возвращаются автоматически
(`satk asset get --fields name,sec model:411` работает), необязательные — нет:
`satk world near --kinds inst 2495 -1687` → `BAD_PARAMS: kinds must be one of inst, item, zone, water, got '2495'`.
Пишите позиционные аргументы **первыми**: `satk world near 2495 -1687 --kinds inst`.

Команды поиска принимают слова без кавычек: `satk ops make txd from png` (= `satk mcp ops`), `satk kb search
CStreaming RequestModel`, `satk help --find crash address`. Длинный ответ можно отправить в файл:
`--out result.json` пишет весь JSON и печатает одну строку; `--summary` печатает только эту строку. В режиме
JSON ошибка дополнительно выводится одной строкой в stderr.

## `%` в аргументах пропадает

Обёртка вида `call satk.cmd %*` заставляет `cmd` заново раскрыть `%...%`, и `LIKE '%car%'` приходит
изменённым. Вызывайте `tools\satk.cmd` (или `satk.sh`, или консольный скрипт `satk`) напрямую либо
передавайте такие аргументы в JSON-файле: `satk mcp op index.query --args @args.json`.

## Вьювер: `NOT_READY`, чёрный или пустой кадр

- **Не запущен.** `NOT_READY: target 'ariane' is not running` → `satk view start --target ariane`
  (2–3 с, окно откроется в левом верхнем углу).
- **Окно свёрнуто.** D3D9 в свёрнутом окне ничего не рисует; `satk view status` ответит `NOT_READY` с подсказкой.
  Разверните окно (его можно оставить маленьким и под другими окнами).
- **Кадр не того размера.** Наш форк Ariane (`"proto":"saap/1"`) снимает кадр размера `--width`/`--height`
  (по умолчанию 960x540) независимо от окна. Старая сборка (`"proto":"ariane-ipc/1"`) всегда снимает окно
  (`warn: capture size is the window size 1280x720`); запускайте её через `satk view start --window 960x540`.
- **MSAA.** Со включённым сглаживанием кадр мог получиться чёрным. satk запускает Ariane с выключенным MSAA;
  пустой кадр даёт ошибку, а не чёрный PNG.
- **Дыры в кадре, недогруженные здания.** Стриминг не успел: в ответе `settled:false`. Снимите ещё раз.
- **`view_pick` ничего не находит.** Наш форк выбирает то, что нарисовано (`pick.visible`), поэтому попадает даже
  в крону пальмы без коллизии. Адаптер старых сборок выбирает по коллизии (`mode:"collision"`): объекты без COL
  (часть растительности, провода) не попадаются.
- **Цель `mock`.** Без `satk view mock` она работает внутри процесса (`warn: mock endpoint not running …`).
  В её мире настоящая только расстановка `inst:lae2_stream0#4`; объекты `inst:mock*` синтетические, и
  `satk asset get` отвечает на них `NOT_FOUND`.
- **`--target game`.** Игру satk сам не запускает: `UNSUPPORTED: satk cannot start target 'game'`. Сервер и клиент
  MTA с ресурсом `satk-agent` запускаются так, как описано в [mta-agent.md](mta-agent.md).
- Лог вьювера — `<workspace>\work\logs\viewer-ariane.log`.

## «Индекс устарел» (`INDEX_STALE`) или `INDEX_MISSING`

Индекс — это файл `<workspace>\work\index\<профиль>.sqlite`. Он помнит размеры и время изменения исходных
файлов; если что-то поменялось, ответы приходят с предупреждением `INDEX_STALE: …`. Без индекса текстуры, карта и
модели отвечают `INDEX_MISSING` (код выхода 3). Индекс старой схемы тоже даёт
`INDEX_MISSING: … has schema v2, this satk needs v3`.

```powershell
satk index status
satk index build --profile vanilla
```

Профиль собирается около 8 с; профили `installed` и `samp` строятся отдельно (`--profile installed`,
`--profile samp`).

## `PROTECTED_PATH` и `--out must be inside the work directory`

Это не ошибка, а защита: satk никогда не пишет в вашу папку с игрой (`paths.game_root`, `paths.installed`) и в
клоны исходников (`paths.src`), а в чистую копию (`<workspace>\gta-sa-clean`) — только командами `satk game`.
Экспорт и другие `--out` допускаются только внутри `<workspace>\work\` (иначе `BAD_PARAMS`); проще не указывать
`--out` вовсе — тогда файлы лягут в `<workspace>\work\out\…`.

## `satk init` не принимает папку с игрой

- `AMBIGUOUS: 2 GTA San Andreas folders found` → выберите одну: `satk init --game <папка>` (список кандидатов
  покажет `satk init --dry-run`).
- `UNSUPPORTED: … holds GTA San Andreas - The Definitive Edition` (или мобильную версию): satk читает только
  классическую игру для ПК (`gta_sa.exe`, `models\gta3.img`, `data\gta.dat`).
- `BAD_PARAMS: the workspace … is inside the game folder`: satk никогда не пишет в папку игры; выберите рабочее
  пространство вне её через `satk init --workspace <папка>`.
- Предупреждения `WORKSPACE_ONEDRIVE`, `WORKSPACE_PROGRAM_FILES`, `GAME_ONEDRIVE`, `GAME_VIRTUALSTORE`,
  `LOW_DISK`: satk работает, но синхронизация OneDrive, права администратора, перенаправленные копии игровых
  файлов или полный диск будут мешать; что делать, написано в самом предупреждении.

## Крэш-дамп MTA не разбирается

`satk re addr` с текстом, где `Module = …gta_sa.exe` и `Offset = 0x…` слеплены в одну строку, отвечает модулем `?`
и `confidence: none`. Сохраните строки дампа как есть (каждую на своей строке) и передайте файлом:
`satk re addr --text-file crash.txt`, или задайте адрес сами: `satk re addr gta_sa.exe+0x13BF09`. Голое смещение
меньше `0x400000` (`satk re addr 0x0013BF09`) — это смещение в модуле, а не адрес: ответ будет
`address outside the gta_sa.exe image`. Если `re addr` отвечает `NOT_READY`, база не собрана: `satk re build`
(около 10 с). Целые minidump и `core.log` разбирает `satk crash`, см. [crash.md](crash.md).

## `DEPENDENCY`: нет Pillow / numpy / mcp

Ядро работает без них, но быстрый DXT, программный рендер и MCP-сервер — нет.

```powershell
powershell -ExecutionPolicy Bypass -File tools\scripts\bootstrap.ps1 -Deps
```

Пакеты берутся из сети (~70 МБ), кэш — `<workspace>\work\cache\pip`. Если всё уже установлено, команда ничего не
скачивает (около 2 с). У `satk paths` `DEPENDENCY` бывает по другой причине: ему нужен вендоренный gta-flow из
клона или переносимого архива ([paths.md](paths.md)).

## Windows блокирует numpy, Pillow или MCP-сервер

- **Smart App Control.** Модули расширений из wheel-пакетов PyPI не подписаны, и там, где включён Smart App
  Control, Windows отказывается их загружать: индекс, поиск и экспорт работают, а превью, быстрые PNG/DXT и
  MCP-сервер — нет. Об этом сообщает `satk doctor` (проверка `app_control`). Менять Smart App Control или нет —
  решаете вы (некоторые версии Windows не включают его обратно без переустановки); satk его не трогает.
- **Переносимый архив скачан из интернета.** Windows помечает распакованные файлы, и при запуске появляются
  предупреждения безопасности. `satk doctor` (проверка `portable`) даёт исправление: `Unblock-File` на папку.

## Не хватает места на диске

- Всё тяжёлое (индексы, кэш PNG, сборки MTA, временные файлы MSBuild и pip) живёт в рабочем пространстве, в
  `work\` и `engine\`.
  `work\` можно удалить целиком, **кроме** `work\notes.sqlite` (сначала выполните `satk note export`).
- Форк MTA с двумя сборками занимает около 6,8 ГБ; `engine\mtasa\Build\obj` (около 4,7 ГБ) можно удалить — он
  пересоберётся. `satk engine doctor` предупреждает, если на его диске свободно меньше 12 ГБ.
- PNG всех текстур (`satk texture export-all`) — около 420 МБ в `work\cache\tex`.
- Если на системном диске мало места, направьте TEMP/TMP для MSBuild и pip в `<workspace>\work\tmp`
  (`bootstrap.ps1` и так держит кэш pip и свои временные файлы в `<workspace>\work`).

## Не тот Python

satk работает на CPython 3.12–3.14 (`satk doctor`, проверка `python`). `py` без версии запускает самый новый
установленный Python, а шимы всегда берут venv клона (`tools\.venv`, его создаёт `bootstrap.ps1`); разработка и
`satk dev gate` идут на 3.12.
Нет venv — запустите `bootstrap.ps1` (без `-Deps` он работает без сети).

## MCP-сервер satk не виден в ИИ-клиенте или `selftest failed`

- Для Claude Code сервер регистрируется в `<workspace>\.mcp.json` (`satk mcp config --check` сверяет файл,
  `--write` пишет его); Claude Code спрашивает разрешение при первом запуске сессии в папке рабочего пространства,
  состояние показывает команда `/mcp`. Другие клиенты — `satk mcp config --client <имя>`, см. [ai.md](ai.md).
- Проверка без клиента: `satk mcp selftest` проводит настоящую stdio-сессию (сейчас `"ok":true`, `list_bytes`
  15 727 из 16 384). Если она завершается `INTERNAL: mcp selftest failed: list_budget`, описания инструментов
  выросли сверх бюджета: сам сервер исправен и инструменты работают, но владельцам пакетов надо сократить описания.
  Узкий набор инструментов: `SATK_MCP_GROUPS=core,index,media,view`.
- Сервер не стартует: `satk doctor` (проверки `deps`, `ops_import`, `app_control`), затем
  `<workspace>\work\logs\mcp.log`.

## `WinError 5` в песочнице (песочница Codex для Windows, урезанные токены)

Процесс в песочнице не может создавать именованные каналы, запускать Git Bash и пользоваться временными папками
с правами только для владельца. satk справляется там, где это возможно: пулы воркеров (сборка индекса, миниатюры,
экспорт текстур) работают в одном процессе, а ответ содержит `warn: ["UNSUPPORTED: worker processes are not
available here ..."]`; результат тот же, только медленнее. `SATK_JOBS=1` (или `--jobs 1`) включает такой режим
намеренно. `satk dev gate` сам находит ограничения, подключает к pytest `tests/sandbox_compat.py` и перечисляет
найденное в `warn`; немногие тесты, которым нужны Git Bash или каналы asyncio-подпроцессов, пропускаются с причиной,
которая начинается с `sandbox:`, а gate их считает («N skipped for the sandbox»). Вне такой песочницы ничего не
меняется.

## Быстрый пример

```powershell
satk version
satk config show --section paths --table
satk doctor --only python,utf8,deps,disk --table
satk index status
```
