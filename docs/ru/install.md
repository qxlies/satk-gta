# Установка на любой машине: `satk init`

[English version](../en/install.md)

<!-- Переносимость, переносимый архив, wheel и первый запуск. Одна конкретная раскладка: workspace-gta.md.
     Сверено 2026-10-05. -->

Пакет: `satk.core.config`, `satk.core.detect`, `satk.runtime.setup`, `satk.runtime.location`.

## Что это

satk не требует ни особой раскладки папок, ни форка MTA, ни Ariane. Нужны 64-битная Windows 10/11 и
**любая** папка с GTA San Andreas для ПК (1.0 US, Steam, со своими модами — неважно). The Definitive Edition и
мобильная версия не поддерживаются: `satk init` показывает их как неподдерживаемых кандидатов. Игра всегда только
читается; всё, что создаёт satk, лежит в `<workspace>\work\`. По умолчанию satk не ходит в сеть: загрузки бывают
только по явным командам (`bootstrap.ps1 -Deps`, `satk engine setup`, `satk dev release`).

Три способа установки:

- **Переносимый архив** (без Python, git и прав администратора): `satk-<version>-win64.zip` со страницы
  [Releases](https://github.com/qxlies/satk-gta/releases) (или собранный командой `satk dev release`).
  Распакуйте его в свою папку (не в Program Files и не в папку игры) и дважды щёлкните `Start satk.cmd`: при
  первом запуске он выполнит `satk init`. Эта папка и есть рабочее пространство. Подробности —
  [release.md](release.md).
- **Клон git-репозитория** и пять команд (ниже) — способ для тех, кто дорабатывает сам satk.
- **Python-пакет** в своём venv: `py -3.12 -m pip install -e tools` (редактируемая установка, `data\` и `vendor\`
  читаются из клона; нужен setuptools, без сети — `--no-build-isolation` и заранее установленный setuptools) или
  wheel `satk_gta-<version>-py3-none-any.whl` со страницы Releases (имя на PyPI — `satk-gta`, на PyPI его пока
  нет; команда по-прежнему `satk`).

Запускайте satk через шимы (`tools\satk.cmd`, `tools/satk.sh`) или консольный скрипт `satk` после установки
через pip. Самодельная обёртка `.cmd` вида `call ... %*` заново раскрывает `%` в аргументах (SQL
`LIKE '%car%'` приходит искажённым); пользуйтесь шимами или кладите аргументы в файл (`--args @args.json`).

## Установка за пять команд

<!-- docs-smoke: skip нужны клон и сеть -->
```powershell
git clone https://github.com/qxlies/satk-gta.git tools
powershell -ExecutionPolicy Bypass -File tools\scripts\bootstrap.ps1 -Deps
tools\satk.cmd init --game "<папка с gta_sa.exe>"
tools\satk.cmd index build
tools\satk.cmd mcp config --write
```

1. `git clone` — куда угодно. Если в папке, где лежит `tools`, есть ещё `work\` или `satk.toml`, то этот
   родитель `tools` и есть рабочее пространство. Иначе рабочее пространство — `%LOCALAPPDATA%\satk` или папка,
   заданная через `satk init --workspace <папка>`.
2. `bootstrap.ps1 -Deps` создаёт `tools\.venv` на первом из Python 3.12, 3.13, 3.14, который знает лаунчер `py`
   (или на `-PythonVersion 3.13`), и ставит из сети пакеты для работы: Pillow, numpy и mcp. `-Dev` добавляет
   pytest, git-хук перед коммитом и файл блокировки версий (для доработки satk). Кэш pip и временные файлы
   кладутся в `<workspace>\work`. Скрипт работает из любой папки; в git worktree он берёт venv основного клона.
3. `satk init` ищет игру сам: реестр Rockstar, библиотеки Steam (`libraryfolders.vdf`), `Program Files`, папки
   рабочего пространства и текущая папка. В папке игры есть `gta_sa.exe` (у Steam — `gta-sa.exe`),
   `models\gta3.img` и `data\gta.dat`. Единственный кандидат берётся сразу; если их несколько, в терминале будет
   вопрос, а без терминала — ошибка `AMBIGUOUS` со списком (выберите через `--game`). Там же находятся Blender,
   MSBuild (`vswhere`) и Ariane. Всё найденное записывается в `<workspace>\satk.toml`. Рабочее пространство
   внутри папки игры отвергается; рабочее пространство в OneDrive или Program Files, игра в OneDrive или с
   копиями в VirtualStore и меньше 1 ГБ свободного места дают предупреждения.
4. `satk index build` строит индекс профиля `game` (это ваша папка с игрой).
5. `satk mcp config --write` регистрирует MCP-сервер `satk` для Claude Code в `<workspace>\.mcp.json`; другие
   ИИ-клиенты — `satk mcp config --client <имя>`, см. [ai.md](ai.md).

## Быстрый пример

```powershell
satk init --dry-run
satk config show --section index
```

`satk init --dry-run` ничего не пишет: показывает кандидатов с вариантом `gta_sa.exe`, найденные инструменты и
`satk.toml`, который был бы записан (если он отличается от существующего — в виде `diff`).

## Команды

| Команда | MCP | Что делает |
|---|---|---|
| `satk init [--game DIR] [--workspace DIR] [--clean-copy] [--yes] [--dry-run]` | — | первичная настройка, запись `<workspace>\satk.toml` |
| `satk doctor [--only a,b] [--deep]` | — | проверки, у каждой — `fix`: Python, пакеты, пути и их источники (`config`, `env`, `detect:vswhere`…), профили, диск, инструменты |
| `satk game info [--root DIR]` | — | вариант exe (`1.0 US HOODLUM`, `Steam`…), `re_supported`, нестандартные файлы |
| `satk config show [--section paths]` | — | действующая конфигурация и то, откуда взято рабочее пространство |

## Как это устроено

- **Рабочее пространство:** `SATK_HOME` → `paths.workspace` из найденного `satk.toml` → папка переносимого
  архива → родитель клона `tools` (git worktree берёт свой основной клон через `.git` → `gitdir` → `commondir`)
  → `%LOCALAPPDATA%\satk`.
- **Поиск `satk.toml`:** `SATK_CONFIG` (файл или папка) → `<клон>\satk.toml` → `<основной клон>\satk.toml` →
  `<workspace>\satk.toml` → `%APPDATA%\satk\satk.toml`. Если `init --workspace` выбрал папку, которую satk сам
  не найдёт, в последний файл записывается указатель `[paths] workspace = '…'`. Переносимая установка читает
  только свою папку.
- **Инструменты:** у `paths.blender`, `paths.msbuild` и `paths.vcvars` нет значений по умолчанию: если их нет в
  `satk.toml`, satk ищет их сам и кэширует результат в `work\cache\detect.json` (на сутки). Значение из
  `satk.toml` всегда важнее. `SATK_DETECT=0` выключает поиск.
- **Профили:** `game` = `paths.game_root` (ваша игра). Чистая копия (`gta-sa-clean`, её делает
  `satk init --clean-copy`, только для стоковой 1.0 US) даёт профиль `vanilla`; без неё `vanilla` — алиас `game`,
  и satk предупреждает `NO_CLEAN_COPY`: без чистой копии слои vanilla и modded неразличимы. `installed` и `samp`
  без своей папки показываются как «не настроен» (`satk index status`), а не как ошибка.
- **Защита записи:** папка игры, `installed`, `src` и чистая копия защищены всегда; в `safety.protected_roots`
  по умолчанию попадают только существующие.
- **Python:** satk работает на CPython 3.12–3.14 (`satk doctor`, проверка `python`); шимы запускают его с
  `-X utf8`. Для разработки и `satk dev gate` используется 3.12.
- **Версии exe:** в `data\exe_versions.json` только подтверждённые хэши (1.0 US HOODLUM, его патч без заставок,
  варианты MTA, Compact); остальные версии (1.0 EU, 1.01, Steam) узнаются по сигнатурам plugin-sdk или по размеру
  с пометкой `heuristic`. Индекс, текстуры, модели и Blender работают с любой версией; `satk re` рассчитан на
  раскладку 1.0 US, и на другой версии `re addr` предупреждает `UNSUPPORTED_EXE`.

## Ограничения и известные проблемы

- Вьювер Ariane и форк MTA необязательны; без них команды `view` и `engine` отвечают `NOT_READY`.
- Wheel, установленный через `pip` (без `-e`) или `pipx`, несёт `data\` (манифесты, `exe_versions.json`) в виде
  `satk/_data`, но вендоренный код из него пока не находится: `satk paths` (gta-flow) отвечает `DEPENDENCY`.
  Для него нужен клон или переносимый архив.
- Smart App Control может блокировать неподписанные модули расширений numpy, Pillow и MCP-сервера; об этом
  сообщает `satk doctor` (проверка `app_control`). Это и другие решения — в [troubleshooting.md](troubleshooting.md).
