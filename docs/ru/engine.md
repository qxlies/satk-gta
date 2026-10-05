# `satk engine`: форк MTA — настройка, зависимости, сборка

[English version](../en/engine.md)

Пакет: `satk.engine`. Правила для агента внутри форка — `engine\mtasa\CLAUDE.md`.

## Что это

`engine\mtasa` — наш форк mtasa-blue (GPL-3.0), созданный **без сети** из локального клона `src\mtasa-neon`.
`satk engine` настраивает его, держит зависимости сборки под sha256-пинами и собирает клиент (Win32) и сервер (x64)
напрямую через MSBuild; ошибки сборки возвращаются строками `file, line, code, msg`. Пишет только в `engine\` и
`work\engine\` рабочего пространства; `src\` и игру не трогает.

## Быстрый пример

```powershell
satk engine doctor --table
satk engine status
satk engine rc-test --table
```

Что вернёт `doctor` (сокращённо, в JSON):

```json
{"ok":true,"cols":["check","status","msg","fix"],"rows":[["fork","ok","branch main @ e563d3478, base 715ec056d, 2 commit(s) ahead",null]],"status":"ok","summary":"15/15 engine checks ok"}
```

## Команды

| Команда | MCP | Что делает |
|---|---|---|
| `satk engine doctor [--deep]` | — | 15 проверок: checkout, remotes, донор чист, шимы, MSBuild/toolset/rc/premake, DXFiles, пины, установленные зависимости, решение, ключевые артефакты, место на диске |
| `satk engine status [--deep]` | `engine` (`cmd=status`) | ветка и ревизия, последние сборки, зафиксированные зависимости, сколько ключевых артефактов есть |
| `satk engine setup [--deps] [--offline] [--update-pins] [--reuse-from DIR]` | — | офлайн-клон и remotes, шимы и обёртка premake; `--deps` — ещё и зависимости клиента (загрузки, только с вашего согласия) |
| `satk engine rc-test [--control]` | — | 4 клиентских `.rc` через `rc.exe` с шимом `afxres.h`; `--control` доказывает ошибку без шима |
| `satk engine gen` | — | `premake5 vs2026` с `DXSDK_DIR` → `Build\MTASA.sln` (около 5 с) |
| `satk engine build [--project P] [--platform Win32\|x64] [--config Release\|Debug\|Nightly] [--target T] [--jobs N] [--toolset v143]` | `engine` (`cmd=build`) | сборка; `P` = `server`, `client`, `all`, `changed` или имя проекта (`"Game SA"`) |
| `satk engine server-smoke` | — | сервер x64 на `127.0.0.1:22103/22105`, ждёт готовности, `shutdown` |
| `satk engine run CMD --args JSON` | `engine` | единый MCP-инструмент: `status`, `doctor`, `build` |

Ещё параметры `build`: `--no-deps` (с именем проекта — не собирать проекты, на которые он ссылается),
`--regen auto|always|never` (перегенерировать проекты через premake), `--max-errors` (сколько ошибок вернуть; в
логе — все).

## Как это устроено

**Раскладка.** `engine\mtasa` — репозиторий форка (ветка `main` от upstream `715ec056d`; `lab/neon` — Neon только
для чтения). Вне репозитория лежат `Directory.Build.targets` и `shims\afxres.h` (MFC не нужен: шим подключается к
`ResourceCompile` только у проектов под `engine\mtasa\`), `satk-premake.lua`, `bootstrap.ps1`, `deps\` и
`deps-lock.json`. Эти файлы генерирует `satk engine setup` из `src/satk/engine/templates`.

**Git.** `neon` указывает на локальный клон `src\mtasa-neon` (только fetch); `upstream` — на GitHub и без вашего
согласия не fetch'ится (полная история — около 1 ГБ; `skipFetchAll`). У обоих push = `DISABLED`. `origin` пока нет:
он появится, когда форк опубликуют в приватный репозиторий.

**Зависимости.** DXFiles, CEF, discord-rpc, rapidjson, Unifont и закрытые
`net.dll`/`net_64.dll`/`net_arm64.dll`/`netc.dll` хранятся один раз в `engine\deps` (`cache\`, `net\<sha256>\`).
Хэши — в `deps-lock.json`: для CEF, discord-rpc, rapidjson и Unifont это пины upstream из `utils\buildactions`, для
DXFiles — наш, для net-модулей — «доверие при первой загрузке». Источники по порядку: хранилище, проверенные копии
(сам форк), загрузка. Действия premake `install_cef/install_discord/install_unifont/install_data` запускаются через
`satk-premake.lua` с `SATK_OFFLINE=1`: premake получает файлы из `deps\` и в сеть не ходит. Если CDN отдаст другой
`net.dll`, `setup --deps` откажет с `REVISION`; принять новый хэш — только явно через `--update-pins`.

**Сборка.** MSBuild вызывается напрямую (`-nodeReuse:false`, TEMP/TMP — в `<workspace>\work\tmp\engine-build`).
Перед сборкой проверяется отпечаток дерева (список файлов, `premake5.lua`, установленные зависимости): если он
изменился, проекты перегенерируются — premake раскрывает glob'ы только при генерации. `--project changed` собирает то, что изменилось с последней успешной
сборки платформы: правка `.cpp` → только его проект (DLL/EXE), заголовок или статическая библиотека → решение
платформы целиком, документация — ничего. Логи: `work\engine\build\logs`. Параллельная вторая сборка получает
`BUSY`.

Замеры на эталонной машине (Release): сервер x64 с нуля 3:00, клиент Win32 3:14, правка одного `.cpp` в
`Client/game_sa` → `"Game SA"` 2 с, no-op x64 1,5 с. `engine\` занимает около 6,8 ГБ (две конфигурации).

**Neon.** `engine\mtasa\docs\porting\neon-ledger.toml` — реестр переносов (волны 0–3, диапазоны PR, предсказанные
конфликты); `engine\mtasa\docs\limits.toml` — лимиты движка: ваниль / наш trunk / Neon.

## Ограничения и известные проблемы

- `satk engine` не запускает клиент MTA. Для первого запуска клиента нужна однократная настройка от администратора —
  см. [mta-agent.md](mta-agent.md). `server-smoke` — только loopback, без ресурсов, `ase 0`, без рассылки по LAN.
- Имена и бренд пока от upstream (форк ещё не переименован); свой `BRANCH_ID` появится вместе с переименованием.
- Новый MSVC (сейчас 14.51, v145) может сломать сборку раньше, чем это заметит CI upstream: запасной вариант —
  `--toolset v143` (MSVC 14.44).
- Каждая сборка Win32 перелинковывает `core.dll` (upstream `gen_language_list` всегда переписывает заголовок).
- Мало места на диске — `satk engine doctor` предупреждает, если на диске форка свободно < 12 ГБ; `Build\obj`
  (около 4,7 ГБ) можно удалить, он пересобирается.
- Другие решения — в [troubleshooting.md](troubleshooting.md).

## Python-API

```python
from satk.engine.build import build, plan, parse_msbuild_log   # сборка и разбор логов MSBuild
from satk.engine.doctor import run_checks, status               # проверки и состояние
from satk.engine.setup import fork_info, manifest, load_lock    # форк и зависимости
```
