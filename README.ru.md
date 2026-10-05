# satk — San Andreas ToolKit

[English version](README.md)

[![CI](https://github.com/qxlies/satk-gta/actions/workflows/ci.yml/badge.svg)](https://github.com/qxlies/satk-gta/actions/workflows/ci.yml)

> **Статус: публичная предварительная версия (0.2.1).** satk работает и проходит свои проверки, но команды, ответы
> и раскладка файлов ещё могут измениться до 1.0. Сообщения об ошибках и идеи ждём на странице
> [Issues](https://github.com/qxlies/satk-gta/issues).

satk — набор инструментов для тех, кто моддит GTA San Andreas (PC), разбирает её устройство и отлаживает игру. Он
индексирует вашу игру и позволяет вам или ИИ-ассистенту находить и разглядывать модели, текстуры, расстановки на карте
и адреса крэшей, собирать моды текстур и моделей, проверять моды на ошибки и конфликты, конвертировать карты. Это один
Python-пакет с одной командой `satk` и одним MCP-сервером, тоже `satk`: они выполняют одни и те же операции и дают
одинаковые ответы.

## Безопасность и приватность

- **Игра только читается.** Папка игры, чистая копия и клоны исходников защищены от записи; satk пишет только в свою
  папку `work` (индексы, кэши, картинки, экспорт, логи).
- **Ни сети, ни телеметрии.** Сам satk в интернет не ходит; загрузки бывают только в командах, которые вы
  запускаете намеренно (установка зависимостей, `satk engine setup`, `satk dev release`). `satk doctor` показывает
  это отдельной проверкой.
- **Отчёт об ошибке остаётся у вас:** `satk bug-report` пишет обезличенный файл на вашем компьютере и ничего не
  отправляет.
- С облачным ИИ-ассистентом ответы satk уходят его провайдеру как часть разговора; полностью локальный вариант не
  отправляет ничего. Подробно: [docs/ru/ai.md](docs/ru/ai.md).

## Работает и без ИИ

Каждая операция — команда терминала с понятными таблицами: `satk asset find`, `satk texture image`,
`satk crash analyze`, `satk mod conflicts` и так далее. `satk help --ru` показывает основные задачи, `satk help --all` —
все команды, а `satk catalog build` делает офлайн-страницу со всеми моделями, текстурами и зонами. ИИ-ассистент —
дополнительная возможность: `satk mcp config --client <имя>` подключает Claude Code, Codex, Cursor, VS Code,
LM Studio и другие.

## Установка в три шага

Нужны 64-битная Windows 10/11 и **любая** папка с GTA San Andreas (PC): 1.0 US, Steam, с модами или без.

1. **Получите satk.** Переносимому zip `satk-<version>-win64.zip` со страницы
   [Releases](https://github.com/qxlies/satk-gta/releases) не нужны ни Python, ни права администратора: распакуйте
   его в свою папку и дважды щёлкните `Start satk.cmd`. Из git-клона: склонируйте
   [github.com/qxlies/satk-gta](https://github.com/qxlies/satk-gta) в папку с именем `tools` и запустите
   `scripts\bootstrap.ps1 -Deps` — он создаст venv на Python 3.12+. На странице Releases есть и wheel
   `satk_gta-<version>-py3-none-any.whl` (на PyPI его пока нет), и `SHA256SUMS.txt` для проверки загрузок.
2. **Укажите игру:** `satk init` сам находит игру (реестр, библиотеки Steam, обычные папки) или берёт
   `--game <папка>` и записывает `satk.toml`.
3. **Проиндексируйте игру:** `satk index build`, затем попробуйте `satk asset find infernus` или `satk help --ru`.

<!-- docs-smoke: skip нужен клон и сеть -->
```powershell
git clone https://github.com/qxlies/satk-gta.git tools
powershell -ExecutionPolicy Bypass -File tools\scripts\bootstrap.ps1 -Deps
tools\satk.cmd init --game "<папка с gta_sa.exe>"
tools\satk.cmd index build
```

Всё об установке (zip, git, Python-пакет, где живёт `work`, блокировка неподписанных файлов в Windows):
[docs/ru/install.md](docs/ru/install.md). От нуля до первого скриншота: [docs/ru/quickstart.md](docs/ru/quickstart.md).

## Что внутри

| Команды | Что делают | Страница |
|---|---|---|
| `satk game` | проверка, защита и воспроизведение чистой копии игры | [game.md](docs/ru/game.md) |
| `satk formats` | парсеры IMG, DFF, TXD, COL, IDE, IPL, IFP, ZON, DAT | [formats.md](docs/ru/formats.md) |
| `satk index`, `asset`, `world` | SQLite-индекс всех ассетов и расстановок: поиск, связи, SQL | [index.md](docs/ru/index.md) |
| `satk texture`, `map image`, `model` | листы текстур, карта сверху, превью моделей без GPU, экспорт в glTF/OBJ | [media.md](docs/ru/media.md), [models.md](docs/ru/models.md) |
| `satk texture pack`, `rw`, `col`, `img` | моды текстур, запись DFF/COL/IMG без Blender | [texmod.md](docs/ru/texmod.md), [rw.md](docs/ru/rw.md) |
| `satk asset lint`, `mod`, `id` | проверка модов, что меняет мод, конфликты Mod Loader, свободные id моделей | [lint.md](docs/ru/lint.md), [modinspect.md](docs/ru/modinspect.md), [idmgr.md](docs/ru/idmgr.md) |
| `satk map convert`, `ipl`, `paths` | конвертация карт SA-MP/MTA/IPL, бинарный IPL, пути машин и пешеходов | [mapconv.md](docs/ru/mapconv.md), [paths.md](docs/ru/paths.md) |
| `satk crash`, `re`, `kb` | дампы и логи крэшей, адреса `gta_sa.exe` → функции, база знаний о движке | [crash.md](docs/ru/crash.md), [re.md](docs/ru/re.md), [kb.md](docs/ru/kb.md) |
| `satk view`, `saap` | вьювер с камерой, кадры с нумерованными объектами, «что это за объект» | [viewer.md](docs/ru/viewer.md) |
| `satk blender`, `engine` | Blender без окна: импорт, рендер, экспорт в MTA; сборка форка MTA | [blender.md](docs/ru/blender.md), [engine.md](docs/ru/engine.md) |
| `satk status`, `doctor`, `help`, `mcp` | обзор, диагностика с исправлениями, справка, MCP-сервер | [mcp.md](docs/ru/mcp.md), [ai.md](docs/ru/ai.md) |

Все страницы по задачам: [docs/ru/README.md](docs/ru/README.md).

## Документация

- Руководство пользователя: [docs/ru/README.md](docs/ru/README.md) (английская версия:
  [docs/en/README.md](docs/en/README.md)), типовые задачи: [docs/ru/workflows.md](docs/ru/workflows.md), проблемы:
  [docs/ru/troubleshooting.md](docs/ru/troubleshooting.md).
- Для ИИ-агентов (на английском): [docs/agent/SKILL.md](docs/agent/SKILL.md) (ставится как skill `satk`),
  [docs/agent/workflows.md](docs/agent/workflows.md), [docs/agent/errors.md](docs/agent/errors.md) и генерируемый
  справочник инструментов [docs/agent/tools.md](docs/agent/tools.md).

## Разработка

Как работать над satk (настройка, worktree на задачу, тесты, правила): [CLAUDE.md](CLAUDE.md) — руководство
для людей и ИИ-агентов, на английском.
Проверка перед слиянием — `satk dev gate`; только тесты — `python -m pytest -q -p no:cacheprovider` в venv.

## Благодарности

**Особое спасибо [Dryxio](https://github.com/Dryxio).** Его открытые проекты — основа значительной части satk и наших
исследований игры и её движка: вьювер satk — форк его Ariane, `satk paths` работает на ядре его gta-flow,
`satk describe` импортирует описания моделей из gta-scout, `satk kb` читает его plugin-sdk-sa и cleo-ai, а по его
форку MTA Neon мы разбирались, как устроен движок. Спасибо! Его проекты по GTA:

- [ariane](https://github.com/Dryxio/ariane): современный редактор карт для GTA III, Vice City и San Andreas;
  вьювер `satk view` — его локальный форк.
- [mtasa-neon](https://github.com/Dryxio/mtasa-neon): экспериментальный форк движка MTA:SA с большими мирами,
  поднятыми лимитами и новыми возможностями Lua; подробно изучен, донор нашего форка движка.
- [gta-scout](https://github.com/Dryxio/gta-scout): модели и сцены в стиле GTA с ИИ и Blender;
  `satk describe import` импортирует его описания моделей.
- [gta-flow](https://github.com/Dryxio/gta-flow): маршруты трафика с ИИ и Blender; его ядро вендорено для
  `satk paths`.
- [plugin-sdk-sa](https://github.com/Dryxio/plugin-sdk-sa): Plugin SDK с дополнительными исправлениями раскладок;
  его читают `satk kb` и `satk re`.
- [cleo-ai](https://github.com/Dryxio/cleo-ai): моддинг на CLEO с ИИ; его справочник опкодов питает `satk kb opcode`.
- [reagent](https://github.com/Dryxio/reagent): восстановление и проверка C/C++-кода из бинарников с помощью ИИ;
  изучен при проектировании `satk re`.
- [ghidra-bridge](https://github.com/Dryxio/ghidra-bridge): доступ ИИ к анализу Ghidra; образец для устройства
  `satk re`, рассчитанного на агента.
- [samp-source](https://github.com/Dryxio/samp-source): побайтная пересборка SA-MP 0.3.7 R5; изучен в наших
  исследованиях мультиплеера.
- [samp-r5-rebuild](https://github.com/Dryxio/samp-r5-rebuild): более ранняя пересборка клиентской DLL SA-MP
  0.3.7-R5 с нуля.
- [wiki.mtasa-neon.com](https://github.com/Dryxio/wiki.mtasa-neon.com): документация Neon на основе MTA wiki.
- [skygfx](https://github.com/Dryxio/skygfx): его форк SkyGfx, из которого Neon собирает мост для MTA.
- [gta-reversed](https://github.com/Dryxio/gta-reversed): его форк реимплементации gta_sa.exe.
- [mtasa-blue](https://github.com/Dryxio/mtasa-blue): его форк Multi Theft Auto (он также отправляет исправления
  в upstream).
- [library](https://github.com/Dryxio/library): его форк Sanny Builder Library.
- [fastman92_limit_adjuster](https://github.com/Dryxio/fastman92_limit_adjuster): его форк исходников fastman92
  Limit Adjuster.
- [GTA-GPS-Redux](https://github.com/Dryxio/GTA-GPS-Redux): его форк GPS-мода для San Andreas.
- [Radar-in-style-GTA-SA-The-Definitive-Edition](https://github.com/Dryxio/Radar-in-style-GTA-SA-The-Definitive-Edition):
  его форк радара в стиле Definitive Edition.
- [dryxio](https://github.com/Dryxio/dryxio): его профиль со списком проектов.
- [gtastuff.com](https://gtastuff.com/): 40+ браузерных инструментов для моддинга GTA и домашняя страница Ariane.

Другие его публичные проекты не связаны с GTA: [DockDrop](https://github.com/Dryxio/DockDrop),
[tamago-life](https://github.com/Dryxio/tamago-life), [openclaw-lullabully](https://github.com/Dryxio/openclaw-lullabully)
и [ppp-rescue](https://github.com/Dryxio/ppp-rescue).

**Спасибо и проектам, на которых стоит satk и у которых мы учились:**

- [gta-reversed](https://github.com/gta-reversed/gta-reversed) (участники gta-reversed): реимплементация
  gta_sa.exe 1.0 US; её карта функций — основа `satk re` и `satk kb`.
- [Plugin-SDK](https://github.com/DK22Pac/plugin-sdk) (DK22Pac и участники, Zlib): раскладки классов и адреса.
- [Multi Theft Auto](https://github.com/multitheftauto/mtasa-blue) (команда MTA, GPL-3.0): основа нашего форка
  движка, а также [MTA wiki](https://wiki.multitheftauto.com/wiki/Main_Page).
- [librw](https://github.com/aap/librw) (MIT) и [librwgta](https://github.com/aap/librwgta) с euryopa (aap): образец
  RenderWare для `satk rw` и основа Ariane, который собирается с
  [форком librw от Southland-FR](https://github.com/Southland-FR/librw).
- [DragonFF](https://github.com/Parik27/DragonFF) (Parik и участники, GPL-3.0): DFF и COL в Blender для
  `satk blender`.
- [rwfury](https://github.com/Hancapo/rwfury) (Hancapo, MIT): вендорен в `vendor/rwfury`.
- [CrashInfo](https://github.com/JuniorDjjr/CrashInfo) (Junior_Djjr, MIT): список крэшей в `data/crashlist`.
- [Mod Loader](https://github.com/thelink2012/modloader) (LINK/2012, MIT): его правила загрузки перенесены
  в `satk mod`.
- [Sanny Builder Library](https://github.com/sannybuilder/library): данные об опкодах для `satk kb opcode`.
- [Ghidra](https://github.com/NationalSecurityAgency/ghidra) (АНБ США, Apache-2.0): границы функций для `satk re`.
- [GTAMods wiki](https://gtamods.com/wiki/Main_Page) (CC BY 4.0): описания форматов файлов и движка.
- [INU Tools](https://github.com/INU-ez/INU_Tools-GTA-Blender) и [INU Check](https://github.com/INU-ez/INU_Check-GTA)
  (INU, GPL-3.0): изучены; INU Check — необязательный движок `satk mod check`.
- [SilentPatch](https://github.com/CookiePLMonster/SilentPatch) (Silent),
  [Open Limit Adjuster](https://github.com/GTAmodding/III.VC.SA.LimitAdjuster),
  [fastman92 Limit Adjuster](https://www.fastman92.com/fastman92-limit-adjuster/),
  [Project2DFX](https://github.com/ThirteenAG/III.VC.SA.IV.Project2DFX) (ThirteenAG) и
  [SkyGfx](https://github.com/aap/skygfx) (aap): движковые моды, с патчами и лимитами которых мы сверяемся.
- [open.mp](https://github.com/openmultiplayer/open.mp) (MPL-2.0): диапазоны кастомных моделей для `satk id`; вместе
  с [реконструкцией SA-MP](https://github.com/dashr9230/SA-MP) (dashr9230), [Slipe Server](https://github.com/mta-slipe/Slipe-Server)
  и [GameNetworkingSockets](https://github.com/ValveSoftware/GameNetworkingSockets) — часть наших исследований
  мультиплеера.
- [Blender](https://www.blender.org/), [Python](https://www.python.org/), [Pillow](https://github.com/python-pillow/Pillow),
  [numpy](https://github.com/numpy/numpy), [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
  и [pytest](https://github.com/pytest-dev/pytest).

Лицензионные уведомления о коде и данных в поставке satk: [NOTICE.md](NOTICE.md). Все источники, которыми мы
пользовались, по темам и с пояснениями: [docs/ru/credits.md](docs/ru/credits.md).

## Лицензия

MIT ([LICENSE](LICENSE)). Исключение — Blender-аддон `blender/satk_blender` (GPL-3.0-or-later, свой `LICENSE`).
Сторонние компоненты и их уведомления: [NOTICE.md](NOTICE.md).
