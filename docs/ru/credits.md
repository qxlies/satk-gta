# Благодарности и источники

[English version](../en/credits.md)

<!-- Владелец: документация. Полный список источников satk; короткие благодарности — в README.ru.md.
     Собран из всех URL отчётов 01-31 и проектных документов (ссылки GitHub ведут на корень репозитория).
     Разделы те же, что в docs/en/credits.md. -->

satk стоит на работе многих людей. На этой странице перечислены проекты, документы и сайты, которыми мы пользовались: код и данные, которые satk поставляет или читает, и все источники, на которые ссылаются наши исследовательские отчёты (01–31) и проектные документы. Лицензионные уведомления о коде и данных в поставке satk — в [NOTICE.md](../../NOTICE.md); краткая версия этой страницы — в [README](../../README.ru.md#благодарности).

В столбце **«Отчёты»** — номера наших внутренних исследовательских отчётов, которые ссылаются на источник («проект» — проектные документы); сами отчёты в репозиторий не входят. Ссылки GitHub ведут на корень репозитория. Спасибо всем перечисленным авторам.

## Особая благодарность: Dryxio

[Dryxio](https://github.com/Dryxio) делает ИИ-инструменты для реверс-инжиниринга и моддинга классических GTA. Его проекты — основа значительной части satk и наших исследований: вьювер, ядро путей трафика, описания моделей, справочник опкодов и форк MTA, по которому мы изучали движок, — всё это выросло из его работы. Все его публичные репозитории, связанные с GTA:

| Репозиторий | Что это | Как это использует satk | Отчёты |
|---|---|---|---|
| [Dryxio/ariane](https://github.com/Dryxio/ariane) | Современный редактор карт для GTA III, Vice City и San Andreas (продолжение euryopa от aap) | вьювер satk (`satk view`) — локальный форк Ariane с добавленным SAAP/1; подробно изучен | 06, 08, 09, 11, 12, 14, 17, 23, 25, 29–31, проект |
| [Dryxio/mtasa-neon](https://github.com/Dryxio/mtasa-neon) | Экспериментальный форк движка MTA:SA: большие миры, поднятые лимиты, новые возможности Lua (GPL-3.0) | подробно изучен; донор нашего форка MTA (cherry-pick), `satk re` и `satk kb` читают его патчи и лимиты | 01–08, 10, 12–20, 22–28, проект |
| [Dryxio/gta-scout](https://github.com/Dryxio/gta-scout) | Создание 3D-моделей и сцен в стиле GTA с ИИ и Blender (MIT) | `satk describe import` импортирует его общий пакет описаний моделей в локальные заметки | 09, 11, 12, 14, 25, 29, 30 |
| [Dryxio/gta-flow](https://github.com/Dryxio/gta-flow) | Создание и правка маршрутов трафика GTA San Andreas с ИИ и Blender (MIT) | его ядро на чистом Python вендорено без изменений в `vendor/gtaflow`; `satk paths` декодирует и собирает им NODES*.DAT | 09, 11, 12, 24, 25, 29, 30 |
| [Dryxio/plugin-sdk-sa](https://github.com/Dryxio/plugin-sdk-sa) | Plugin SDK для San Andreas, дополненный исправлениями раскладок и сигнатур из реверса с помощью LLM (Zlib) | `satk kb` и `satk re` читают из локального клона его раскладки классов и адреса | 06, 07, 10, 12, 13, 24, 27 |
| [Dryxio/cleo-ai](https://github.com/Dryxio/cleo-ai) | Создание модов GTA San Andreas с ИИ через CLEO | `satk kb opcode` читает из локального клона его справочник опкодов (собран из Sanny Builder Library) | 07, 12, 25, 29 |
| [Dryxio/reagent](https://github.com/Dryxio/reagent) | Восстановление и проверка C/C++-кода из скомпилированных программ с помощью ИИ (MIT) | изучен при проектировании `satk re`; этот разбор привёл нас к карте функций gta-reversed, на которой стоит `satk re` | 06–08, 10, 12, 17, 25 |
| [Dryxio/ghidra-bridge](https://github.com/Dryxio/ghidra-bridge) | Доступ ИИ к анализу программ в Ghidra (MIT) | его идея — CLI для агента поверх данных, выгруженных из Ghidra, — легла в основу `satk re`, который тоже импортирует выгрузку функций из Ghidra | 06, 09–12, 22, 24, 25, 27 |
| [Dryxio/gta-reversed](https://github.com/Dryxio/gta-reversed) | Форк общественной реимплементации gta_sa.exe 1.0 US | сравнён с upstream gta-reversed, который читают `satk re` и `satk kb` | 06, 07 |
| [Dryxio/samp-source](https://github.com/Dryxio/samp-source) | Побайтная пересборка SA-MP 0.3.7 R5 из исходников с реверсом при помощи ИИ | изучен для отчётов о мультиплеере и праве; код не используется | 06, 07, 12, 15, 23, 25, 26 |
| [Dryxio/samp-r5-rebuild](https://github.com/Dryxio/samp-r5-rebuild) | Пересборка клиентской DLL SA-MP 0.3.7-R5 с нуля с опорой на доказательства | предшественник samp-source, рассмотрен вместе с ним | 07 |
| [Dryxio/skygfx](https://github.com/Dryxio/skygfx) | Форк SkyGfx от aap: графика PS2-версии San Andreas на PC | даёт мост `skygfx_mta.dll`, который собирает CI Neon; изучен для отчёта о рендеринге | 01, 02, 04, 05, 18 |
| [Dryxio/library](https://github.com/Dryxio/library) | Форк Sanny Builder Library: документация по скриптам для Sanny Builder и CLEO Redux | upstream-библиотека — источник данных об опкодах для `satk kb opcode` | — |
| [Dryxio/wiki.mtasa-neon.com](https://github.com/Dryxio/wiki.mtasa-neon.com) · [mtasa-neon-wiki.vercel.app](https://mtasa-neon-wiki.vercel.app/) | Документация MTA:SA Neon на основе MTA wiki с полной историей upstream (GFDL-1.3) | читали при разборе API и сетевых изменений Neon | 05 |
| [Dryxio/mtasa-blue](https://github.com/Dryxio/mtasa-blue) | Форк Multi Theft Auto (GPL-3.0); Dryxio также отправляет исправления в upstream | контекст для отчёта о состоянии форка Neon | 05 |
| [Dryxio/fastman92_limit_adjuster](https://github.com/Dryxio/fastman92_limit_adjuster) | Форк исходников fastman92 Limit Adjuster | учтён при разборе патчей лимитов в Neon | 02 |
| [Dryxio/GTA-GPS-Redux](https://github.com/Dryxio/GTA-GPS-Redux) | Форк полноценного GPS-мода для GTA San Andreas | в satk не используется | — |
| [Dryxio/Radar-in-style-GTA-SA-The-Definitive-Edition](https://github.com/Dryxio/Radar-in-style-GTA-SA-The-Definitive-Edition) | Форк 3D-радара в стиле Definitive Edition для классической San Andreas (MIT) | в satk не используется | — |
| [Dryxio/dryxio](https://github.com/Dryxio/dryxio) | Репозиторий профиля: ИИ-инструменты, реверс-инжиниринг и проекты по классическим GTA | обзор его проектов, с которого мы начинали | 06 |
| [gtastuff.com](https://gtastuff.com/) · [ariane](https://gtastuff.com/ariane/) · [available-ids](https://gtastuff.com/tools/available-ids/) · [missing-textures](https://gtastuff.com/tools/missing-textures/) · [ifp-editor](https://gtastuff.com/tools/ifp-editor/) | GTA Stuff: 40+ браузерных инструментов Dryxio для моддинга GTA (поиск свободных ID, проверка недостающих текстур, валидатор RW, редакторы IFP, handling, COL, путей и зон) и домашняя страница Ariane | satk не повторяет эти GUI-редакторы; `satk id free` и `satk asset lint` делают похожие проверки из командной строки | 08, 09, 12, 29, 30 |

Другие его публичные проекты, не связанные с GTA: [DockDrop](https://github.com/Dryxio/DockDrop), [tamago-life](https://github.com/Dryxio/tamago-life), [openclaw-lullabully](https://github.com/Dryxio/openclaw-lullabully), [ppp-rescue](https://github.com/Dryxio/ppp-rescue).

## Что satk использует напрямую

Код, данные и правила, которые satk поставляет, читает или запускает. Лицензионные уведомления: [NOTICE.md](../../NOTICE.md).

| Компонент | Автор | Лицензия | Как его использует satk |
|---|---|---|---|
| [Hancapo/rwfury](https://github.com/Hancapo/rwfury) | Hancapo | MIT | вендорен без изменений в `vendor/rwfury`: имена поверхностей коллизий для `satk col write`, независимый ридер и эталон round-trip в тестах |
| [Dryxio/gta-flow](https://github.com/Dryxio/gta-flow) | Dryxio | MIT | ядро на чистом Python вендорено без изменений в `vendor/gtaflow` для `satk paths` |
| [Dryxio/gta-scout](https://github.com/Dryxio/gta-scout) | Dryxio | MIT | пакет описаний моделей, который импортирует `satk describe import` (только данные, без кода) |
| [JuniorDjjr/CrashInfo](https://github.com/JuniorDjjr/CrashInfo) | Junior_Djjr | MIT | английский список крэшей GTA SA 1.0 US хранится без изменений в `data/crashlist`, его использует `satk crash analyze` |
| [thelink2012/modloader](https://github.com/thelink2012/modloader) | LINK/2012 | MIT, datalib BSL-1.0 | правила обработки файлов, приоритетов и слияния data-файлов переписаны на Python для `satk mod` |
| [gta-reversed/gta-reversed](https://github.com/gta-reversed/gta-reversed) | участники gta-reversed | нет | `satk re` и `satk kb` читают локальный клон (карта функций, места в исходниках); фрагменты только печатаются и не сохраняются |
| [DK22Pac/plugin-sdk](https://github.com/DK22Pac/plugin-sdk) | DK22Pac и участники | Zlib | раскладки классов, адреса и сигнатуры exe для `satk kb`, `satk re` и `satk game` (через plugin-sdk-sa от Dryxio) |
| [multitheftauto/mtasa-blue](https://github.com/multitheftauto/mtasa-blue) | команда Multi Theft Auto | GPL-3.0 | наш форк движка (`satk engine`); `satk re patches` и `satk kb` индексируют патчи памяти и раскладки движка MTA |
| [Dryxio/mtasa-neon](https://github.com/Dryxio/mtasa-neon) | Dryxio | GPL-3.0 | донор возможностей для нашего форка движка; `satk re` и `satk kb` индексируют файлы, отличающиеся от upstream |
| [sannybuilder/library](https://github.com/sannybuilder/library) | команда Sanny Builder | нет | данные об опкодах для `satk kb opcode` (через cleo-ai от Dryxio) |
| [NationalSecurityAgency/ghidra](https://github.com/NationalSecurityAgency/ghidra) | АНБ США | Apache-2.0 | список функций, выгруженный из Ghidra, уточняет границы функций в символьной БД `satk re` |
| [Dryxio/ariane](https://github.com/Dryxio/ariane) | Dryxio, aap | нет (README: GPL) | вьювер `satk view` — локальный форк (не распространяется) |
| [aap/librw](https://github.com/aap/librw) · [Southland-FR/librw](https://github.com/Southland-FR/librw) | aap; Southland-FR | MIT | `satk rw` следует раскладкам потоков librw (код не копируется); Ariane собирается с форком Southland-FR |
| [Parik27/DragonFF](https://github.com/Parik27/DragonFF) | Parik и участники | GPL-3.0 | `satk blender` импортирует и экспортирует через него DFF и COL внутри Blender |
| [Blender](https://www.blender.org/) | Blender Foundation | GPL | импорт, рендер и экспорт без окна (`satk blender`); аддон `blender/satk_blender` |
| [INU-ez/INU_Check-GTA](https://github.com/INU-ez/INU_Check-GTA) | INU | GPL-3.0 | необязательная внешняя программа для `satk mod check --engine inu`; не входит в поставку и не скачивается |
| [openmultiplayer/open.mp](https://github.com/openmultiplayer/open.mp) | команда open.mp | MPL-2.0 | диапазоны ID кастомных моделей и грамматика строк `artconfig` для `satk id` |
| [fastman92 Limit Adjuster](https://www.fastman92.com/fastman92-limit-adjuster/) · [Open Limit Adjuster](https://github.com/GTAmodding/III.VC.SA.LimitAdjuster) | fastman92; GTAmodding | закрытая; MIT | `satk id` читает ini лимит-аджастера профиля, чтобы знать последний допустимый ID модели |
| [GTAMods wiki](https://gtamods.com/wiki/Main_Page) | сообщество GTAMods | CC BY 4.0 | описания форматов и движка, на которых построены `satk formats`, `satk index` и проверенные факты `satk kb` |
| [данные сборки MTA](https://mirror-cdn.multitheftauto.com/bdata/DXFiles.zip) | команда Multi Theft Auto | — | `satk engine setup` по запросу скачивает файлы DirectX для сборки с зеркала MTA |
| [Python](https://www.python.org/) · [python-pillow/Pillow](https://github.com/python-pillow/Pillow) · [numpy/numpy](https://github.com/numpy/numpy) · [modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk) · [pytest-dev/pytest](https://github.com/pytest-dev/pytest) | PSF и авторы проектов | PSF, MIT-CMU, BSD-3-Clause, MIT, MIT | среда выполнения и необязательные зависимости (точные версии в `requirements.lock`); переносимый релиз включает Python |

## Движок и мультиплеер

Multi Theft Auto, SA-MP/open.mp и проекты сетевого кода, физики и скриптов, которые мы сравнивали для форка движка.

### Multi Theft Auto

| Источник | Заметки | Отчёты |
|---|---|---|
| [multitheftauto/mtasa-blue](https://github.com/multitheftauto/mtasa-blue) | MTA:SA (GPL-3.0): основа нашего форка движка и индекса патчей MTA в `satk re` | 05, 12, 15–18, 20, 21, проект |
| [multitheftauto/mtasa-resources](https://github.com/multitheftauto/mtasa-resources) | Официальные ресурсы: редактор карт, EDF, списки объектов, runcode | 23, 26, 30 |
| [multitheftauto/wiki.multitheftauto.com](https://github.com/multitheftauto/wiki.multitheftauto.com) · [wiki.preview.multitheftauto.com](https://wiki.preview.multitheftauto.com/) | Исходники новой MTA wiki на YAML (GFDL-1.3) и её превью | 12, 16, 20, 24 |
| [multitheftauto/luau](https://github.com/multitheftauto/luau) | Форк Luau от MTA | 16 |
| [multitheftauto/mta-mcp-server](https://github.com/multitheftauto/mta-mcp-server) | Официальный MCP-сервер MTA (вики и отладочный помощник) | 26 |
| [multitheftauto/testing-tools](https://github.com/multitheftauto/testing-tools) | Тестовые ресурсы MTA | 26 |
| [multitheftauto/mtasa-docs](https://github.com/multitheftauto/mtasa-docs/blob/main/mtasa-blue/CONTRIBUTING.md) | Документация для контрибьюторов mtasa-blue (лицензия вкладов) | 25 |
| [mirror-cdn.multitheftauto.com/bdata](https://mirror-cdn.multitheftauto.com/bdata/) · [DXFiles.zip](https://mirror-cdn.multitheftauto.com/bdata/DXFiles.zip) · [netc.dll](https://mirror-cdn.multitheftauto.com/bdata/netc.dll) · [fork-support/netc.dll](https://mirror-cdn.multitheftauto.com/bdata/fork-support/netc.dll) | Данные сборки MTA: файлы DirectX, которые скачивает `satk engine setup`, и закрытые сетевые модули (в том числе сборка для форков) | 01, 03–05, 15, 25, 28 |
| [cef-builds.spotifycdn.com](https://cef-builds.spotifycdn.com/index.html) | Сборки Chromium Embedded Framework — зависимость сборки клиента MTA | 01, 28 |
| [luac.mtasa.com](https://luac.mtasa.com/) | Сервис MTA для компиляции и обфускации Lua | 15 |
| [reflectrpteam/mtasa-blue](https://github.com/reflectrpteam/mtasa-blue) | Форк MTA с веткой Luau (предложенный рантайм Luau) | 16 |
| [Fernando-A-Rocha/mta-add-models](https://github.com/Fernando-A-Rocha/mta-add-models) | newmodels: серверный реестр кастомных моделей для MTA | 20, 23, 29 |
| [BlueEagle12/MTA-Eagle-Loader](https://github.com/BlueEagle12/MTA-Eagle-Loader) | Eagle Loader: загрузчик кастомных карт и моделей для MTA | 29 |
| [gptrk0/mtasa-map-images](https://github.com/gptrk0/mtasa-map-images) | Ортографические рендеры карты, сделанные внутри MTA | 12 |
| [hedit/hedit](https://github.com/hedit/hedit) | Внутриигровой редактор handling для MTA | 30 |
| [eXo-OpenSource/ml_pathfind](https://github.com/eXo-OpenSource/ml_pathfind) | Серверный модуль поиска пути для MTA (MIT) | 16 |
| [acc-holo-dev/mta-sdk-module](https://github.com/acc-holo-dev/mta-sdk-module) | Обёртка C++20 над ABI серверных модулей MTA | 16 |
| [dmi7ry/Luau-definition-for-MTA](https://github.com/dmi7ry/Luau-definition-for-MTA) | Определения API MTA для luau-lsp | 16 |
| [mtasa-typescript/mtasa-lua-types](https://github.com/mtasa-typescript/mtasa-lua-types) | Типы TypeScript для скриптов MTA на Lua | 16 |
| [mta-slipe/Slipe-Server](https://github.com/mta-slipe/Slipe-Server) · [nuget.org](https://www.nuget.org/packages/SlipeServer.Server) | Slipe Server: сервер MTA на C# поверх официального сетевого модуля | 15, 16 |
| [mta-slipe/Slipe-Core](https://github.com/mta-slipe/Slipe-Core) | Slipe Core: C#, компилируемый в Lua для ресурсов MTA | 16 |
| [adem-hosni/IronicMTA](https://github.com/adem-hosni/IronicMTA) | Эксперимент с сервером MTA на Python поверх официального сетевого модуля | 15 |
| [forum.multitheftauto.com](https://forum.multitheftauto.com/topic/138847-tut-3d-modelling-in-blender-mtasa/) | Урок на форуме MTA: 3D-моделирование в Blender для MTA:SA | 29 |

**MTA wiki**: страницы о функциях движка, шейдерах, ID, серверах, форках и крэшах (отчёты 12, 15, 16, 18–21, 23–26, 29, 30): [Main_Page](https://wiki.multitheftauto.com/wiki/Main_Page) · [EngineReplaceModel](https://wiki.multitheftauto.com/wiki/EngineReplaceModel) · [EngineApplyShaderToWorldTexture](https://wiki.multitheftauto.com/wiki/EngineApplyShaderToWorldTexture) · [EnginePreloadWorldArea](https://wiki.multitheftauto.com/wiki/EnginePreloadWorldArea) · [RemoveWorldModel](https://wiki.multitheftauto.com/wiki/RemoveWorldModel) · [SetElementRotation](https://wiki.multitheftauto.com/wiki/SetElementRotation) · [DxCreateShader](https://wiki.multitheftauto.com/wiki/DxCreateShader) · [DxGetTexturePixels](https://wiki.multitheftauto.com/wiki/DxGetTexturePixels) · [Shader](https://wiki.multitheftauto.com/wiki/Shader) · [Shader_examples](https://wiki.multitheftauto.com/wiki/Shader_examples) · [SetDynamicPedShadowsEnabled](https://wiki.multitheftauto.com/wiki/SetDynamicPedShadowsEnabled) · [Resource:Dynamic_lighting](https://wiki.multitheftauto.com/wiki/Resource:Dynamic_lighting) · [MTA:Eir](https://wiki.multitheftauto.com/wiki/MTA:Eir) · [TakePlayerScreenShot](https://wiki.multitheftauto.com/wiki/TakePlayerScreenShot) · [Resource_Web_Access](https://wiki.multitheftauto.com/wiki/Resource_Web_Access) · [GetPlayerSerial](https://wiki.multitheftauto.com/wiki/GetPlayerSerial) · [Modules](https://wiki.multitheftauto.com/wiki/Modules) · [Animations](https://wiki.multitheftauto.com/wiki/Animations) · [Character_Skins](https://wiki.multitheftauto.com/wiki/Character_Skins) · [Garage](https://wiki.multitheftauto.com/wiki/Garage) · [Interior_IDs](https://wiki.multitheftauto.com/wiki/Interior_IDs) · [Vehicle_IDs](https://wiki.multitheftauto.com/wiki/Vehicle_IDs) · [Weapons](https://wiki.multitheftauto.com/wiki/Weapons) · [Server_mtaserver.conf](https://wiki.multitheftauto.com/wiki/Server_mtaserver.conf) · [Sync_interval_settings](https://wiki.multitheftauto.com/wiki/Sync_interval_settings) · [User:Arran_Fortuna?diff=49038](https://wiki.multitheftauto.com/wiki/User:Arran_Fortuna?diff=49038) · [Anti-cheat_guide](https://wiki.multitheftauto.com/wiki/Anti-cheat_guide) · [Forks](https://wiki.multitheftauto.com/wiki/Forks) · [Forks_Full_AC](https://wiki.multitheftauto.com/wiki/Forks_Full_AC) · [Changes_in_1.6.1](https://wiki.multitheftauto.com/wiki/Changes_in_1.6.1) · [Changes_in_1.7](https://wiki.multitheftauto.com/wiki/Changes_in_1.7) · [Compiling_MTASA](https://wiki.multitheftauto.com/wiki/Compiling_MTASA) · [Reversing_GTA:SA_Code](https://wiki.multitheftauto.com/wiki/Reversing_GTA:SA_Code) · [Famous_crash_offsets_and_their_meaning](https://wiki.multitheftauto.com/wiki/Famous_crash_offsets_and_their_meaning) · [Optimize_Custom_TXD](https://wiki.multitheftauto.com/wiki/Optimize_Custom_TXD).

### SA-MP и open.mp

| Источник | Заметки | Отчёты |
|---|---|---|
| [openmultiplayer/open.mp](https://github.com/openmultiplayer/open.mp) | Сервер open.mp (MPL-2.0); `satk id` следует его диапазонам кастомных моделей | 12, 23, 25, 26, 29, 31 |
| [openmultiplayer/web](https://github.com/openmultiplayer/web) | Исходники сайта и документации open.mp | 12, 24, 25, 31 |
| [openmultiplayer/launcher](https://github.com/openmultiplayer/launcher) | Лаунчер open.mp | 12, 31 |
| [openmultiplayer/open.mp-sdk](https://github.com/openmultiplayer/open.mp-sdk) · [openmultiplayer/omp-capi](https://github.com/openmultiplayer/omp-capi) · [openmultiplayer/omp-node](https://github.com/openmultiplayer/omp-node) | SDK компонентов open.mp, C API и привязки для Node.js | 16 |
| [openmultiplayer/RakNet](https://github.com/openmultiplayer/RakNet) | Изменённый RakNet 2.52 от open.mp для совместимости с SA-MP | 15 |
| [openmultiplayer/editor](https://github.com/openmultiplayer/editor) | Заброшенный веб-редактор карт open.mp | 23 |
| [ikkentim/SampSharp](https://github.com/ikkentim/SampSharp) | Скрипты на C# для SA-MP и open.mp | 16 |
| [Southclaws/sampctl](https://github.com/Southclaws/sampctl) | Менеджер пакетов SA-MP; его практика релизов (архивы с контрольными суммами) повлияла на нашу | 31 |
| [BlastHackNet/SAMP-API](https://github.com/BlastHackNet/SAMP-API) | Структуры клиента SA-MP (MIT) | 12 |
| [Knogle/libsamp](https://github.com/Knogle/libsamp) | Libre-SAMP: совместимая открытая реконструкция samp.dll (MIT) | 12 |
| [dashr9230/SA-MP](https://github.com/dashr9230/SA-MP) | Реконструкция SA-MP без лицензии; рассмотрена в отчёте о праве, не используется | 12, 25 |
| [sa-mp.mp](https://www.sa-mp.mp/) | Сайт SA-MP, преемник sa-mp.com | 25 |
| [sampwiki.blast.hk](https://sampwiki.blast.hk/wiki/Main_Page) | Статичное зеркало старой SA-MP wiki | 24 |

**Документация open.mp**: страницы о кастомных моделях, объектах, NPC и конфигурации сервера (отчёты 12, 21, 23, 26, 29): [AddSimpleModel](https://open.mp/docs/scripting/functions/AddSimpleModel) · [CreateObject](https://open.mp/docs/scripting/functions/CreateObject) · [RemoveBuildingForPlayer](https://open.mp/docs/scripting/functions/RemoveBuildingForPlayer) · [SetObjectMaterial](https://open.mp/docs/scripting/functions/SetObjectMaterial) · [SetObjectMaterialText](https://open.mp/docs/scripting/functions/SetObjectMaterialText) · [NPC_StartPlayback](https://open.mp/docs/scripting/functions/NPC_StartPlayback) · [config.json](https://www.open.mp/docs/server/config.json) · [блог: server beta 9](https://open.mp/blog/server-beta-9) · [лаунчер](https://open.mp/downloads/launcher).

### Другие мультиплеерные платформы

| Источник | Заметки | Отчёты |
|---|---|---|
| [citizenfx/fivem](https://github.com/citizenfx/fivem) · [docs.fivem.net](https://docs.fivem.net/docs/developers/script-runtimes/) | FiveM (CitizenFX): история лицензии и устройство рантаймов скриптов | 16, 25 |
| [MafiaHub/Framework](https://github.com/MafiaHub/Framework) | Мультиплеерный фреймворк MafiaHub: сеть, ECS и скрипты на JS | 16 |
| [Tornamic/CoopAndreas](https://github.com/Tornamic/CoopAndreas) | CoopAndreas: кооперативное прохождение сюжета San Andreas (GPL-3.0) | 12 |
| [gtaconnected](https://github.com/gtaconnected) · [gtaconnected.com](https://gtaconnected.com/) | Мультиплеер GTA Connected | 12 |

### Сетевой код и физика

| Источник | Заметки | Отчёты |
|---|---|---|
| [ValveSoftware/GameNetworkingSockets](https://github.com/ValveSoftware/GameNetworkingSockets) | Сетевая библиотека Valve (BSD-3-Clause) | 15, 21 |
| [lsalzman/enet](https://github.com/lsalzman/enet) · [zpl-c/enet](https://github.com/zpl-c/enet) | Надёжный UDP ENet и его поддерживаемый форк | 15, 21 |
| [SLikeSoft/SLikeNet](https://github.com/SLikeSoft/SLikeNet) · [facebookarchive/RakNet](https://github.com/facebookarchive/RakNet) | RakNet 4 и его форк SLikeNet | 15, 21 |
| [Sandertv/go-raknet](https://github.com/Sandertv/go-raknet) · [CloudburstMC/Network](https://github.com/CloudburstMC/Network) · [NetrexMC/RakNet](https://github.com/NetrexMC/RakNet) | Реализации протокола RakNet из мира Minecraft Bedrock | 15 |
| [mas-bandwidth/yojimbo](https://github.com/mas-bandwidth/yojimbo) · [mas-bandwidth/netcode](https://github.com/mas-bandwidth/netcode) | yojimbo и netcode Гленна Фидлера | 15, 21 |
| [skywind3000/kcp](https://github.com/skywind3000/kcp) · [microsoft/msquic](https://github.com/microsoft/msquic) | Транспорты KCP и MsQuic | 15 |
| [pond3r/ggpo](https://github.com/pond3r/ggpo) | Rollback-неткод GGPO (MIT) | 21 |
| [bulletphysics/bullet3](https://github.com/bulletphysics/bullet3) · [jrouwe/JoltPhysics](https://github.com/jrouwe/JoltPhysics) · [ZealanL/RocketSim](https://github.com/ZealanL/RocketSim) · [RenderKit/embree](https://github.com/RenderKit/embree) | Библиотеки физики и трассировки лучей, сравнённые для серверной симуляции | 21 |
| [синхронизация состояния](https://gafferongames.com/post/state_synchronization/) · [сетевая физика](https://gafferongames.com/categories/networked-physics/) | Статьи Gaffer On Games о синхронизации состояния и сетевой физике | 21 |
| [сетевой код Source](https://developer.valvesoftware.com/wiki/Source_Multiplayer_Networking) · [компенсация лагов](https://developer.valvesoftware.com/wiki/Lag_Compensation) | Valve Developer Community о сетевом коде Source | 21 |
| [неткод Overwatch (GDC)](https://gdcvault.com/play/1024001/-Overwatch-Gameplay-Architecture-and) · [Rocket League на GDC 2018](https://www.rocketleague.com/news/rocket-league-at-gdc-2018) · [неткод VALORANT](https://technology.riotgames.com/node/111) · [Replication Graph в Unreal](https://dev.epicgames.com/documentation/en-us/unreal-engine/replication-graph-in-unreal-engine) | Доклады и статьи об архитектуре неткода в вышедших играх | 21 |
| [OneSync](https://docs.fivem.net/docs/scripting-reference/onesync/) · [игровые события](https://docs.fivem.net/docs/cookbook/2019/08/19/onesync-intercepting-game-events-such-as-explosions/) · [forum.cfx.re](https://forum.cfx.re/t/possibly-unwanted-side-effects-of-cancelling-weapondamageevent/4850130) · [синхронизация сущностей alt:V](https://docs.altv.mp/cs/articles/getting-started/entity-sync.html) | Документация FiveM OneSync и синхронизации сущностей alt:V | 21 |
| [meshedinsights.com](https://meshedinsights.com/2017/07/16/apache-bans-facebooks-license-combo/) | Лицензионный контекст сочетания BSD+Patents (RakNet) | 15 |

### Рантаймы скриптов

| Источник | Заметки | Отчёты |
|---|---|---|
| [lua/lua](https://github.com/lua/lua) · [заметки к Lua 5.5](https://www.lua.org/manual/5.5/readme.html) | Lua и заметки к выпуску Lua 5.5 | 16 |
| [LuaJIT/LuaJIT](https://github.com/LuaJIT/LuaJIT) · [luajit.org](https://luajit.org/status.html) · [lua-users.org](https://lua-users.org/lists/lua-l/2011-06/msg00513.html) · [lists.tarantool.org](https://lists.tarantool.org/pipermail/tarantool-patches/2019-November/012735.html) | LuaJIT, его страница статуса и обсуждения хуков и C API в рассылках | 16 |
| [luau-lang/luau](https://github.com/luau-lang/luau) · [песочница](https://luau.org/sandbox) · [совместимость](https://luau.org/compatibility) · [производительность](https://luau.org/performance) | Luau и его заметки о песочнице, совместимости и производительности | 16 |
| [JohnnyMorganz/luau-lsp](https://github.com/JohnnyMorganz/luau-lsp) · [LuaLS/lua-language-server](https://github.com/LuaLS/lua-language-server) · [teal-language/tl](https://github.com/teal-language/tl) · [TypeScriptToLua](https://typescripttolua.github.io/docs/configuration) | Языковые серверы и типизированные языки, компилируемые в Lua | 16 |
| [moonsharp-devs/moonsharp](https://github.com/moonsharp-devs/moonsharp) | MoonSharp: интерпретатор Lua для .NET (его использует Slipe Server) | 16 |
| [quickjs-ng/quickjs](https://github.com/quickjs-ng/quickjs) · [bytecodealliance/wasm-micro-runtime](https://github.com/bytecodealliance/wasm-micro-runtime) · [wasm3/wasm3](https://github.com/wasm3/wasm3) | Встраиваемые рантаймы JS и WebAssembly | 16 |
| [forum.cfx.re](https://forum.cfx.re/t/removal-of-lua-5-3-support/5335232) | Отказ FiveM от поддержки Lua 5.3 | 16 |
| [песочница Space Station 14](https://docs.spacestation14.com/en/robust-toolbox/sandboxing.html) | Прецедент песочницы для C#-кода, присланного сервером | 16 |

### Открытые движки и реимплементации

| Источник | Заметки | Отчёты |
|---|---|---|
| [Avatarchik/opensa](https://github.com/Avatarchik/opensa) | OpenSA (AGPL-3.0) — браузерный движок, совместимый с данными RenderWare; зеркало исходного репозитория | 12, 17 |
| [rwengine/openrw](https://github.com/rwengine/openrw) | OpenRW: clean-room-движок GTA III (GPL-3.0) | 17 |
| [in0finite/SanAndreasUnity](https://github.com/in0finite/SanAndreasUnity) | San Andreas Unity: ремейк движка SA на Unity (MIT) | 17 |
| [flwmxd/librw-vulkan-RT](https://github.com/flwmxd/librw-vulkan-RT) | Экспериментальный бэкенд librw на Vulkan с трассировкой лучей | 17 |
| [M-HT/SR](https://github.com/M-HT/SR) | Статическая рекомпиляция x86-программ | 17 |

## Реверс-инжиниринг

Реимплементации, SDK, движковые моды, инструменты анализа и источники знаний, на которых стоят `satk re` и `satk kb`.

### Реимплементации, SDK и движковые моды

| Источник | Заметки | Отчёты |
|---|---|---|
| [gta-reversed/gta-reversed](https://github.com/gta-reversed/gta-reversed) · [gta-reversed.github.io](https://gta-reversed.github.io/gta-reversed/) | Реимплементация gta_sa.exe 1.0 US; её карта функций и исходники питают `satk re` и `satk kb` | 06, 12, 17, 27 |
| [DK22Pac/plugin-sdk](https://github.com/DK22Pac/plugin-sdk) | Plugin-SDK (Zlib): раскладки классов и адреса для ASI-плагинов | 07, 12, 24, 27 |
| [sannybuilder/library](https://github.com/sannybuilder/library) · [library.sannybuilder.com](https://library.sannybuilder.com/) | Sanny Builder Library: машиночитаемая база опкодов и нативных функций | 24 |
| [sannybuilder](https://github.com/sannybuilder) · [sannybuilder/dev](https://github.com/sannybuilder/dev) · [sannybuilder.com](https://sannybuilder.com/) · [форум, тема 1703](https://sannybuilder.com/forums/viewtopic.php?id=1703) · [уроки](https://lessons.sannybuilder.com/00100/00500) | Sanny Builder: компилятор SCM/CLEO, его релизы, форум и уроки об игровой памяти | 24, 27, 29, 30 |
| [cleolibrary/CLEO5](https://github.com/cleolibrary/CLEO5) | CLEO 5 (MIT): расширения скриптового движка | 12, 24, 29, 31 |
| [cleolibrary/CLEO-Redux](https://github.com/cleolibrary/CLEO-Redux) | CLEO Redux: скрипты на JS/TS (проприетарная EULA) | 12 |
| [user-grinch/ImGuiRedux](https://github.com/user-grinch/ImGuiRedux) | Привязки ImGui для CLEO 5 и CLEO Redux | 12 |
| [CookiePLMonster/SilentPatch](https://github.com/CookiePLMonster/SilentPatch) · [silentsblog.com](https://silentsblog.com/mods/gta-sa/) · [gtaforums.com](https://gtaforums.com/topic/669045-silentpatch/) | SilentPatch (MIT): исправления SA; его патчи сверяются в `satk kb`, а его тексты крэшей разбирает `satk crash` | 12, 24, 30 |
| [GTAmodding/III.VC.SA.LimitAdjuster](https://github.com/GTAmodding/III.VC.SA.LimitAdjuster) | Open Limit Adjuster (MIT) | 12, 24, 29, 30 |
| [fastman92/fastman92_limit_adjuster](https://github.com/fastman92/fastman92_limit_adjuster) · [fastman92.com](https://www.fastman92.com/fastman92-limit-adjuster/) | fastman92 Limit Adjuster: лимиты ID, карты и стриминга; `satk id` читает его ini | 24, 30 |
| [GTAmodding/FramerateVigilante](https://github.com/GTAmodding/FramerateVigilante) | FramerateVigilante (MIT): исправления ошибок, зависящих от FPS | 24 |
| [thelink2012/modloader](https://github.com/thelink2012/modloader) | Mod Loader (MIT): его правила загрузки перенесены в `satk mod` | 12, 24, 29–31 |
| [ThirteenAG/Ultimate-ASI-Loader](https://github.com/ThirteenAG/Ultimate-ASI-Loader) · [ThirteenAG/WidescreenFixesPack](https://github.com/ThirteenAG/WidescreenFixesPack) | Ultimate ASI Loader и Widescreen Fixes Pack (MIT) | 12 |
| [aap/debugmenu](https://github.com/aap/debugmenu) · [gta-chaos-mod/Trilogy-ASI-Script](https://github.com/gta-chaos-mod/Trilogy-ASI-Script) | Отладочное меню aap и Chaos Mod: примеры крупных ASI-модов | 12 |
| [JuniorDjjr/VehFuncs](https://github.com/JuniorDjjr/VehFuncs) · [user-grinch/ModelExtras](https://github.com/user-grinch/ModelExtras) · [mixmods.com.br](https://www.mixmods.com.br/2025/12/sa-vehfuncs/) | VehFuncs и ModelExtras: возможности моделей машин, которые `satk asset lint` не должен считать ошибками | 29 |

### Инструменты анализа и мосты для ИИ

| Источник | Заметки | Отчёты |
|---|---|---|
| [NationalSecurityAgency/ghidra](https://github.com/NationalSecurityAgency/ghidra) · [headless-анализатор](https://ghidradocs.com/11.4_PUBLIC/help/Base/help/topics/HeadlessAnalyzer/HeadlessAnalyzer.htm) · [pyghidra](https://pypi.org/project/pyghidra/) | Ghidra (Apache-2.0), её headless-анализатор и PyGhidra | 10, 12 |
| [clearbluejar/pyghidra-mcp](https://github.com/clearbluejar/pyghidra-mcp) · [pypi.org](https://pypi.org/project/pyghidra-mcp/) | pyghidra-mcp: MCP-сервер для Ghidra без окна | 10, 12 |
| [LaurieWired/GhidraMCP](https://github.com/LaurieWired/GhidraMCP) · [symgraph/GhidrAssistMCP](https://github.com/symgraph/GhidrAssistMCP) · [cyberkaida/reverse-engineering-assistant](https://github.com/cyberkaida/reverse-engineering-assistant) | GhidraMCP, GhidrAssistMCP и ReVa: MCP-мосты к Ghidra | 12 |
| [mrexodia/ida-pro-mcp](https://github.com/mrexodia/ida-pro-mcp) · [ifarbod/renderware3-flirt](https://github.com/ifarbod/renderware3-flirt) | MCP для IDA Pro и FLIRT-сигнатуры RenderWare 3 | 12 |
| [x64dbg/x64dbg](https://github.com/x64dbg/x64dbg) · [x64dbg/x64dbgida](https://github.com/x64dbg/x64dbgida) | Отладчик x64dbg и его мост к базам IDA | 19 |
| [Wasdubya/x64dbgMCP](https://github.com/Wasdubya/x64dbgMCP) · [AgentSmithers/x64DbgMCPServer](https://github.com/AgentSmithers/x64DbgMCPServer) · [dariushoule/x64dbg-automate](https://github.com/dariushoule/x64dbg-automate) · [x64dbg-automate MCP](https://dariushoule.github.io/x64dbg-automate-pyclient/mcp-server/) | MCP-серверы и автоматизация для x64dbg | 12, 19 |
| [Cheat Engine](https://en.wikipedia.org/wiki/Cheat_Engine) · [miscusi-peek/cheatengine-mcp-bridge](https://github.com/miscusi-peek/cheatengine-mcp-bridge) · [bethington/cheat-engine-server-python](https://github.com/bethington/cheat-engine-server-python) | Cheat Engine и его MCP-мосты (для осмотра памяти нашей тестовой копии) | 12, 19 |
| [mq1n/GhostMCP](https://github.com/mq1n/GhostMCP) | GhostMCP: MCP внутри процесса для живого осмотра | 12 |
| [frida/frida](https://github.com/frida/frida) · [dnakov/frida-mcp](https://github.com/dnakov/frida-mcp) | Инструментация Frida и её MCP-сервер | 12, 19 |
| [Mixaill/FakePDB](https://github.com/Mixaill/FakePDB) | FakePDB: PDB-файлы из баз IDA | 19 |

### Источники знаний

| Источник | Заметки | Отчёты |
|---|---|---|
| [gtaforums.com](https://gtaforums.com/topic/194199-documenting-gta-sa-memory-addresses/) | Тема GTAForums с адресами памяти SA | 24 |
| [тема 20472](https://www.blast.hk/threads/20472/) · [тема 234670](https://www.blast.hk/threads/234670/) | Темы BlastHack о структурах памяти SA и реверс-инжиниринге | 24 |
| [OLA и FLA](https://forum.mixmods.com.br/f10-ajuda-com-o-jogo/t4392-ola-e-fla-ao-mesmo-tempo) · [FLA](https://forum.mixmods.com.br/f5-scripts-codigos/t6180-fastman92-limit-adjuster) | Темы форума MixMods о лимит-аджастерах | 24 |
| [TsyVM/SAEncyclopedia](https://github.com/TsyVM/SAEncyclopedia) | SAEncyclopedia: рассмотрена и отклонена как источник фактов (без лицензии, вероятно сгенерирована ИИ) | 24 |

**GTAMods wiki (CC BY 4.0)**: форматы файлов, data-файлы, адреса памяти, опкоды и инструменты; главный открытый справочник для `satk formats` и `satk kb` (отчёты 12, 24, 27, 29, 30).

- Форматы файлов: [RenderWare_binary_stream_file](https://gtamods.com/wiki/RenderWare_binary_stream_file) · [List_of_RW_section_IDs](https://gtamods.com/wiki/List_of_RW_section_IDs) · [2d_Effect_(RW_Section)](https://gtamods.com/wiki/2d_Effect_%28RW_Section%29) · [Breakable_(RW_Section)](https://gtamods.com/wiki/Breakable_%28RW_Section%29) · [Extra_Vert_Colour_(RW_Section)](https://gtamods.com/wiki/Extra_Vert_Colour_%28RW_Section%29) · [Night_Vertex_Colors_(RW_Section)](https://gtamods.com/wiki/Night_Vertex_Colors_%28RW_Section%29) · [HAnim_PLG_(RW_Section)](https://gtamods.com/wiki/HAnim_PLG_%28RW_Section%29) · [Material_Effects_PLG_(RW_Section)](https://gtamods.com/wiki/Material_Effects_PLG_%28RW_Section%29) · [Native_Data_PLG_(RW_Section)](https://gtamods.com/wiki/Native_Data_PLG_%28RW_Section%29) · [Raster_(RW_Section)](https://gtamods.com/wiki/Raster_%28RW_Section%29) · [Skin_PLG_(RW_Section)](https://gtamods.com/wiki/Skin_PLG_%28RW_Section%29) · [Collision_File](https://gtamods.com/wiki/Collision_File) · [IMG_archive](https://gtamods.com/wiki/IMG_archive) · [IFP](https://gtamods.com/wiki/IFP) · [GXT](https://gtamods.com/wiki/GXT) · [Script.img](https://gtamods.com/wiki/Script.img) · [Streamed_Script](https://gtamods.com/wiki/Streamed_Script) · [Item_Definition](https://gtamods.com/wiki/Item_Definition) · [OBJS](https://gtamods.com/wiki/OBJS) · [TOBJ](https://gtamods.com/wiki/TOBJ) · [ANIM](https://gtamods.com/wiki/ANIM) · [HIER](https://gtamods.com/wiki/HIER) · [CARS_(IDE_Section)](https://gtamods.com/wiki/CARS_%28IDE_Section%29) · [PEDS](https://gtamods.com/wiki/PEDS) · [WEAP](https://gtamods.com/wiki/WEAP) · [TXDP](https://gtamods.com/wiki/TXDP) · [2DFX](https://gtamods.com/wiki/2DFX) · [Item_Placement](https://gtamods.com/wiki/Item_Placement) · [INST](https://gtamods.com/wiki/INST) · [CARS_(IPL_Section)](https://gtamods.com/wiki/CARS_%28IPL_Section%29) · [CULL](https://gtamods.com/wiki/CULL) · [ENEX](https://gtamods.com/wiki/ENEX) · [GRGE](https://gtamods.com/wiki/GRGE) · [JUMP](https://gtamods.com/wiki/JUMP) · [OCCL](https://gtamods.com/wiki/OCCL) · [PICK](https://gtamods.com/wiki/PICK) · [TCYC](https://gtamods.com/wiki/TCYC) · [AUZO](https://gtamods.com/wiki/AUZO) · [ZONE](https://gtamods.com/wiki/ZONE) · [Paths_(GTA_SA)](https://gtamods.com/wiki/Paths_%28GTA_SA%29) · [Saves_(GTA_SA)](https://gtamods.com/wiki/Saves_%28GTA_SA%29) · [Replays_(GTA_SA)](https://gtamods.com/wiki/Replays_%28GTA_SA%29) · [User_Files](https://gtamods.com/wiki/User_Files) · [Audio_stream](https://gtamods.com/wiki/Audio_stream) · [SFX_(SA)](https://gtamods.com/wiki/SFX_%28SA%29)
- Data-файлы: [Gta.dat](https://gtamods.com/wiki/Gta.dat) · [Animgrp.dat](https://gtamods.com/wiki/Animgrp.dat) · [Carcols.dat](https://gtamods.com/wiki/Carcols.dat) · [Carmods.dat](https://gtamods.com/wiki/Carmods.dat) · [Handling.cfg](https://gtamods.com/wiki/Handling.cfg) · [Object.dat](https://gtamods.com/wiki/Object.dat) · [Pedstats.dat](https://gtamods.com/wiki/Pedstats.dat) · [Surfaud.dat](https://gtamods.com/wiki/Surfaud.dat) · [Water.dat](https://gtamods.com/wiki/Water.dat) · [Weapon.dat](https://gtamods.com/wiki/Weapon.dat) · [Time_cycle](https://gtamods.com/wiki/Time_cycle) · [Gta_sa.set](https://gtamods.com/wiki/Gta_sa.set) · [Decision_Maker](https://gtamods.com/wiki/Decision_Maker) · [Ped_type](https://gtamods.com/wiki/Ped_type) · [Particle_(SA)](https://gtamods.com/wiki/Particle_%28SA%29) · [List_of_particle_effects](https://gtamods.com/wiki/List_of_particle_effects)
- Движок и память: [Memory_Addresses_(SA)](https://gtamods.com/wiki/Memory_Addresses_%28SA%29) · [Function_Memory_Addresses_(SA)](https://gtamods.com/wiki/Function_Memory_Addresses_%28SA%29) · [Object_pool](https://gtamods.com/wiki/Object_pool) · [VTable](https://gtamods.com/wiki/VTable) · [Hardcoded](https://gtamods.com/wiki/Hardcoded) · [Resource_Streaming](https://gtamods.com/wiki/Resource_Streaming) · [Map_system](https://gtamods.com/wiki/Map_system) · [LOD](https://gtamods.com/wiki/LOD) · [Ped_Bones](https://gtamods.com/wiki/Ped_Bones) · [Ped_Event](https://gtamods.com/wiki/Ped_Event) · [Task_IDs_(GTA_SA)](https://gtamods.com/wiki/Task_IDs_%28GTA_SA%29) · [Blip_Sprite_IDs](https://gtamods.com/wiki/Blip_Sprite_IDs) · [List_of_vehicles_(SA)](https://gtamods.com/wiki/List_of_vehicles_%28SA%29) · [Weapon](https://gtamods.com/wiki/Weapon) · [Game_directory_(SA)](https://gtamods.com/wiki/Game_directory_%28SA%29) · [San_Andreas_Versions](https://gtamods.com/wiki/San_Andreas_Versions) · [Referring_to_GTA_Versions](https://gtamods.com/wiki/Referring_to_GTA_Versions) · [Game_Patches](https://gtamods.com/wiki/Game_Patches) · [SA_Limit_Adjuster](https://gtamods.com/wiki/SA_Limit_Adjuster)
- Скрипты: [SA_SCM](https://gtamods.com/wiki/SA_SCM) · [SCM_Instruction](https://gtamods.com/wiki/SCM_Instruction) · [List_of_opcodes](https://gtamods.com/wiki/List_of_opcodes) · [CLEO](https://gtamods.com/wiki/CLEO)
- Инструменты: [IMG_Tool](https://gtamods.com/wiki/IMG_Tool) · [MEd](https://gtamods.com/wiki/MEd) · [Magic.TXD](https://gtamods.com/wiki/Magic.TXD) · [TXD_Workshop](https://gtamods.com/wiki/TXD_Workshop) · [Sanny_Builder](https://gtamods.com/wiki/Sanny_Builder) · [Collision_File_Editor_II](https://gtamods.com/wiki/Collision_File_Editor_II) · [Mod_Loader](https://gtamods.com/wiki/Mod_Loader) · [Main_Page](https://gtamods.com/wiki/Main_Page)

## Анализ крэшей, отладка и профилирование

Источники для `satk crash` и для отладки игры и форка движка.

### Знания о крэшах

| Источник | Заметки | Отчёты |
|---|---|---|
| [JuniorDjjr/CrashInfo](https://github.com/JuniorDjjr/CrashInfo) · [mixmods 2021](https://www.mixmods.com.br/2021/08/crashinfo/) · [mixmods 2022](https://www.mixmods.com.br/2022/09/crashinfo/) | CrashInfo (MIT): его список крэшей лежит в `data/crashlist` для `satk crash analyze` | 24, 29 |
| [моды, вызывающие крэши](https://gtaforums.com/topic/902062-list-mods-that-cause-crashes/) · [крэш LOD с HD-текстурами](https://gtaforums.com/topic/927411-lots-of-hd-texture-packs-unload-lod-crash-fix/) | Темы GTAForums о модах, вызывающих крэши, и о крэше LOD с HD-текстурами | 29 |

### Отладчики и символы

| Источник | Заметки | Отчёты |
|---|---|---|
| [WinDbg](https://aka.ms/windbg/download) · [TTD](https://aka.ms/ttd/download) · [обзор TTD](https://learn.microsoft.com/en-us/windows-hardware/drivers/debuggercmds/time-travel-debugging-overview) · [командная строка TTD](https://learn.microsoft.com/en-us/windows-hardware/drivers/debuggercmds/time-travel-debugging-ttd-exe-command-line-util) | WinDbg и Time Travel Debugging | 19 |
| [TimMisiak/windup](https://github.com/TimMisiak/windup) · [SymBuilder](https://github.com/microsoft/WinDbg-Samples/tree/master/TargetComposition/SymBuilder) · [AddSyntheticSymbol](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/dbgeng/nf-dbgeng-idebugsymbols3-addsyntheticsymbol) | Помощник установки WinDbg и синтетические символы для бинарника без PDB | 19 |
| [svnscha/mcp-windbg](https://github.com/svnscha/mcp-windbg) · [документация](https://svnscha.github.io/mcp-windbg/) · [glslang/windbg-mcp](https://github.com/glslang/windbg-mcp) | MCP-серверы для WinDbg | 19 |
| [msdl.microsoft.com](https://msdl.microsoft.com/download/symbols) | Публичный сервер символов Microsoft | 19 |
| [dshikashio/Pybag](https://github.com/dshikashio/Pybag) · [skelsec/minidump](https://github.com/skelsec/minidump) | Доступ к DbgEng из Python и разбор минидампов | 19 |
| [ProcDump](https://learn.microsoft.com/en-us/sysinternals/downloads/procdump) · [Process Monitor](https://learn.microsoft.com/en-us/sysinternals/downloads/procmon) · [VMMap](https://learn.microsoft.com/en-us/sysinternals/downloads/vmmap) · [DebugView](https://learn.microsoft.com/en-us/sysinternals/downloads/debugview) | Инструменты Sysinternals для дампов, файловой активности, памяти и отладочного вывода | 19 |
| [AddressSanitizer](https://learn.microsoft.com/en-us/cpp/sanitizers/asan) · [DynamoRIO/drmemory](https://github.com/DynamoRIO/drmemory) | AddressSanitizer в MSVC и Dr. Memory | 19 |
| [devblogs.microsoft.com](https://devblogs.microsoft.com/oldnewthing/20201209-00/?p=104530) | The Old New Thing о wpaexporter: выгрузка данных Windows Performance Analyzer | 19 |
| [lldb MCP](https://lldb.llvm.org/use/mcp.html) | Встроенный MCP-сервер LLDB | 19 |

### Отладка графики и профилирование

| Источник | Заметки | Отчёты |
|---|---|---|
| [baldurk/renderdoc](https://github.com/baldurk/renderdoc) · [FAQ](https://renderdoc.org/docs/getting_started/faq.html) · [JiaboLi-GitHub/renderdoc-mcp](https://github.com/JiaboLi-GitHub/renderdoc-mcp) | RenderDoc (D3D9 не поддерживает, поэтому через DXVK) и его MCP-сервер | 12, 18, 19 |
| [apitrace/apitrace](https://github.com/apitrace/apitrace) | apitrace: запись и воспроизведение вызовов D3D9 | 12, 19 |
| [захват GPU в PIX](https://learn.microsoft.com/en-us/windows/win32/direct3dtools/pix/articles/gpu-captures/pix-gpu-captures) · [PIX и D3D11on12](https://devblogs.microsoft.com/pix/debugging-d3d11-apps-using-d3d11on12/) | Захват кадров GPU в PIX (D3D12 и D3D11 через D3D11on12) | 19 |
| [wolfpld/tracy](https://github.com/wolfpld/tracy) · [GameTechDev/PresentMon](https://github.com/GameTechDev/PresentMon) | Профайлер Tracy и замер кадров PresentMon | 19 |
| [VTune](https://intel.com/content/www/us/en/developer/articles/system-requirements/vtune-profiler/2025-1.html) · [Superluminal](https://www.superluminal.eu/docs/documentation.html) | Профайлеры Intel VTune и Superluminal | 19 |

## Форматы файлов и библиотеки

Описания форматов и парсеры, с которыми мы сверяли `satk formats` и `satk rw`.

| Источник | Заметки | Отчёты |
|---|---|---|
| [Hancapo/rwfury](https://github.com/Hancapo/rwfury) · [pypi.org](https://pypi.org/project/rwfury/) | rwfury (MIT): библиотека RenderWare на чистом Python, вендорена для тестов и имён поверхностей коллизий | 12, 14, 30 |
| [aap/librw](https://github.com/aap/librw) | librw (MIT): реимплементация RenderWare; образец для наших писателей | 12, 17, 24, 30 |
| [aap/librwgta](https://github.com/aap/librwgta) | librwgta: инструменты aap для GTA на librw, включая euryopa — основу Ariane | 12, 17, 30 |
| [Southland-FR/librw](https://github.com/Southland-FR/librw) | Форк librw, с которым собирается Ariane | 08 |
| [formats.kaitai.io](https://formats.kaitai.io/renderware_binary_stream/) | Спецификация Kaitai Struct для бинарного потока RenderWare (тестовый оракул) | 12, 14 |
| [Timic3/rw-parser](https://github.com/Timic3/rw-parser) · [rw-parser-ng](https://www.npmjs.com/package/rw-parser-ng) | rw-parser и rw-parser-ng: парсеры DFF/TXD/IFP на TypeScript (GPL-3.0) | 12, 14 |
| [gta-img](https://docs.rs/gta-img) · [crates.io](https://crates.io/) | Rust-крейты для архивов IMG и RenderWare (gta-img, rw-parser-rs, libtxd) | 12, 14 |
| [jackal1337/DFF-Loader](https://github.com/jackal1337/DFF-Loader) | Загрузчик DFF и TXD для three.js (MIT) | 12 |
| [iroxacu666/RWGuard](https://github.com/iroxacu666/RWGuard) | Сканер испорченных DFF/TXD для SA-MP; идея для нашей проверки чанков | 23 |
| [pillow.readthedocs.io](https://pillow.readthedocs.io/en/stable/handbook/image-file-formats.html) | Форматы изображений Pillow (декодирование DDS/DXT) | 14 |

## Инструменты моддинга: вьюверы, редакторы карт и ассетов

Ландшафт инструментов, который мы изучили, прежде чем решить, что satk должен и не должен делать.

### Вьюверы и редакторы карт

| Источник | Заметки | Отчёты |
|---|---|---|
| [user-grinch/Neuryopa](https://github.com/user-grinch/Neuryopa) | Neuryopa: ещё один вьювер на основе euryopa (GPL-3.0) | 12 |
| [ikkentim/SanMap](https://github.com/ikkentim/SanMap) | SanMap: проекция карты для Google Maps | 12 |
| [gta-modding.com](https://www.gta-modding.com/san_andreas/tutorials/create_map_med.html) | Урок по созданию карты в MEd | 29 |

### Инструменты для IMG, TXD и моделей

| Источник | Заметки | Отчёты |
|---|---|---|
| [Bios-Marcel/IMG-Console](https://github.com/Bios-Marcel/IMG-Console) · [gtaforums.com](https://gtaforums.com/topic/524409-fastman92-img-console/) | fastman92 IMG Console | 30 |
| [MexUK/IMGF](https://github.com/MexUK/IMGF) · [X-Seti/Img-Factory-1.6](https://github.com/X-Seti/Img-Factory-1.6) · [ndenissov/UniversalIMG](https://github.com/ndenissov/UniversalIMG) · [vaibhavpandeyvpz/gtaimg](https://github.com/vaibhavpandeyvpz/gtaimg) · [Alci's IMG Editor](https://www.gta-modding.com/area/file-3-alcis-img-editor.html) | Редакторы архивов IMG | 12, 30 |
| [vaibhavpandeyvpz/txdedit](https://github.com/vaibhavpandeyvpz/txdedit) · [X-Seti/Txd-Workshop](https://github.com/X-Seti/Txd-Workshop) · [FrannDzs/Magic.TXD](https://github.com/FrannDzs/Magic.TXD) · [TXD Studio](https://libertycity.net/files/gta-san-andreas/236727-txd-studio.html) · [Modern TXD Editor](https://libertycity.net/files/gta-san-andreas/238278-modern-txd-editor-v0-2806.html) | Редакторы TXD (зеркало Magic.TXD от DK22Pac и The_GTA) | 12, 29, 30 |
| [X-Seti/Model-Workshop](https://github.com/X-Seti/Model-Workshop) · [zmodeler3.com](https://www.zmodeler3.com/) · [steve-m.com](http://steve-m.com/downloads/tools/) | Инструменты для моделей: Model Workshop, ZModeler 3 и классические инструменты Steve-M | 30 |
| [Kam's GTA Scripts](https://gtaforums.com/topic/907323-rel-kams-gta-scripts-2018-upd-08092024/) · [список скриптов](https://www.geocities.ws/kam_b_lai/GTA/scriptslist.htm) · [KAM's GTA Tools](https://libertycity.net/files/gta-san-andreas/223453-gta-tools.html) · [vincedark56/Map_Tools_for_Kam_GTA_Scripts](https://github.com/vincedark56/Map_Tools_for_Kam_GTA_Scripts) | Скрипты Kam для 3ds Max и инструменты карт на их основе | 11, 30 |
| [MadGamerHD/2DFX-Tool](https://github.com/MadGamerHD/2DFX-Tool) · [урок по 2DFX](https://libertycity.net/files/gta-san-andreas/64366-creating-2dfx-with-2dfx-tool.html) | Инструмент 2DFX для огней и эффектов в DFF | 30 |
| [X-Seti/Radar-Workshop](https://github.com/X-Seti/Radar-Workshop) · [Ghady983/GTA-SA-Radar-Map-Mod-Maker](https://github.com/Ghady983/GTA-SA-Radar-Map-Mod-Maker) | Генераторы тайлов радара | 29, 30 |
| [MadGamerHD/GTA-SA-Path-Nodes-Editor](https://github.com/MadGamerHD/GTA-SA-Path-Nodes-Editor) | Редактор узлов путей для Blender (MIT) | 29 |
| [KTKTDEV/sa-anim-export](https://github.com/KTKTDEV/sa-anim-export) · [IFP Editor](https://libertycity.net/files/resources/175492-ifp-editor.html) · [AutoRiga](https://libertycity.net/files/gta-san-andreas/243096-autoriga.html) | Инструменты анимации: экспорт, правка IFP и автоматический риггинг | 29, 30 |
| [user-grinch/Carcols_Editor](https://github.com/user-grinch/Carcols_Editor) | Редактор carcols.dat | 30 |
| [INU-ez/INU_Check-GTA](https://github.com/INU-ez/INU_Check-GTA) · [libertycity.net](https://libertycity.net/files/243300-inu-check-v1-0-universal-game-launch.html) | INU Check (GPL-3.0): офлайн-проверка перед запуском; необязательный движок `satk mod check` | 29 |
| [DK22Pac/v2saconv](https://github.com/DK22Pac/v2saconv) | Конвертер моделей GTA V в форматы SA (изучена техника; не для ассетов Rockstar) | 23 |

## Blender

Blender-часть satk и аддоны, которые мы сравнивали.

| Источник | Заметки | Отчёты |
|---|---|---|
| [Parik27/DragonFF](https://github.com/Parik27/DragonFF) · [extensions.blender.org](https://extensions.blender.org/add-ons/dragonff/) | DragonFF (GPL-3.0): импорт и экспорт DFF/COL, который использует `satk blender` | 11, 12, 14, 30, 31 |
| [INU-ez/INU_Tools-GTA-Blender](https://github.com/INU-ez/INU_Tools-GTA-Blender) · [extensions.blender.org](https://extensions.blender.org/add-ons/inu-tools-gta-sa/) · [версии](https://extensions.blender.org/add-ons/inu-tools-gta-sa/versions/) · [blenderartists.org](https://blenderartists.org/t/inu-tools-1-7-full-gta-san-andreas-modding-pipeline-in-blender-dff-col-txd-ide-ipl-img-ifp/1639525) · [libertycity.net](https://libertycity.net/files/gta-san-andreas/237474-inu-tools-2.3.2-complemento-de-blender-para-modding.html) | INU Tools (GPL-3.0): полный конвейер моддинга SA в Blender | 11, 12, 29–31 |
| [INU-ez/INU_Core_GTA](https://github.com/INU-ez/INU_Core_GTA) | INU Core (GPL-3.0): ядро форматов INU Tools на Python и numpy | 11, 30 |
| [Psycrow101/io_scene_gta_ifp](https://github.com/Psycrow101/io_scene_gta_ifp) | Импорт и экспорт анимаций IFP для Blender | 11, 29, 30 |
| [spicybung/DemonFF](https://github.com/spicybung/DemonFF) | DemonFF (MIT): вариант DragonFF с экспортом карт для SA-MP | 12 |
| [SA Map Tools for Blender](https://libertycity.net/files/gta-san-andreas/224787-sa-map-tools-for-blender.html) | Аддон с инструментами карт для Blender | 11, 29 |
| [ahujasid/blender-mcp](https://github.com/ahujasid/blender-mcp) · [ahujasid/mcp-for-blender](https://github.com/ahujasid/mcp-for-blender) | MCP-серверы для управления Blender | 11, 12, 22 |
| [лицензия](https://www.blender.org/about/license/) · [Blender 5.2](https://www.blender.org/releases/5-2/) · [документация по аддонам](https://docs.blender.org/manual/en/latest/advanced/extensions/addons.html) · [правила для аддонов](https://developer.blender.org/docs/handbook/extensions/addon_guidelines/) | Лицензия Blender, заметки к выпуску и правила расширений (для нашего GPL-аддона) | 11, 25, 31 |

## Рендеринг и графика

Графические моды и рендереры, изученные для форка движка.

| Источник | Заметки | Отчёты |
|---|---|---|
| [aap/skygfx](https://github.com/aap/skygfx) · [aap/skygfx_vc](https://github.com/aap/skygfx_vc) | SkyGfx: вид PS2/Xbox для SA, III и VC (aap) | 12, 18 |
| [gta191977649/MTASA-SkyGfx](https://github.com/gta191977649/MTASA-SkyGfx) · [RAZORRZR0/SkyGfxDE](https://github.com/RAZORRZR0/SkyGfxDE) | Порты SkyGfx: ресурс MTA и фильтр для Definitive Edition | 18 |
| [ThirteenAG/III.VC.SA.IV.Project2DFX](https://github.com/ThirteenAG/III.VC.SA.IV.Project2DFX) · [ThirteenAG/XboxRainDroplets](https://github.com/ThirteenAG/XboxRainDroplets) | Project2DFX (LOD-огни, дальность прорисовки) и капли дождя Xbox (MIT) | 12, 18, 30 |
| [GTAmodding/timecycle24](https://github.com/GTAmodding/timecycle24) | 24-часовой таймцикл | 18 |
| [ep1h/gta-sa-postfx](https://github.com/ep1h/gta-sa-postfx) · [petrgeorgievsky/gtaRenderHook](https://github.com/petrgeorgievsky/gtaRenderHook) · [crosire/reshade](https://github.com/crosire/reshade) | Постобработка и хуки рендера: gta-sa-postfx, gtaRenderHook, ReShade | 12, 18 |
| [doitsujin/dxvk](https://github.com/doitsujin/dxvk) · [microsoft/D3D9On12](https://github.com/microsoft/D3D9On12) · [DirectX-Specs](https://github.com/microsoft/DirectX-Specs/blob/master/d3d/TranslationLayerResourceInterop.md) | Слои трансляции D3D9: DXVK (Vulkan) и D3D9On12 | 12, 18, 19, 26 |
| [NVIDIAGameWorks/rtx-remix](https://github.com/NVIDIAGameWorks/rtx-remix) · [NVIDIAGameWorks/dxvk-remix](https://github.com/NVIDIAGameWorks/dxvk-remix) · [NVIDIAGameWorks/bridge-remix](https://github.com/NVIDIAGameWorks/bridge-remix) · [FAQ Remix](https://docs.omniverse.nvidia.com/kit/docs/rtx_remix/1.4.0-0/docs/remix-faq.html) | NVIDIA RTX Remix | 18 |
| [Hemry81/GTASA-Remix](https://github.com/Hemry81/GTASA-Remix) · [DSOGaming](https://dsogaming.com/?p=172314) | Настройка RTX Remix для San Andreas и её производительность | 18 |
| [MixMods](https://www.mixmods.com.br/2026/10/sa-proper-shaders/) · [DSOGaming](https://www.dsogaming.com/?p=194337) · [GTA BOOM](https://www.gtaboom.com/gta-san-andreas-proper-shaders-deferred-rendering-mod) | Proper Shaders: мод отложенного рендеринга от Junior_Djjr | 18 |
| [DSOGaming](https://www.dsogaming.com/news/sa_directx-2-0-mod-makes-grand-theft-auto-san-andreas-look-almost-as-good-as-modern-day-video-games) · [Direct Render v4.0](https://libertycity.net/files/gta-san-andreas/191434-direct-render-v4.0.html) | Графические моды на основе ENB (SA_DirectX 2.0, Direct Render) | 18 |

## Конвейер ИИ-контента

3D-генерация, обработка мешей, генерация текстур и апскейл, изученные для отчёта об ИИ-контенте.

### 3D-генерация и обработка мешей

| Источник | Заметки | Отчёты |
|---|---|---|
| [microsoft/TRELLIS](https://github.com/microsoft/TRELLIS) · [microsoft/TRELLIS.2](https://github.com/microsoft/TRELLIS.2) · [PozzettiAndrea/ComfyUI-TRELLIS2](https://github.com/PozzettiAndrea/ComfyUI-TRELLIS2) | TRELLIS и TRELLIS.2 (изображение → 3D) с узлом для ComfyUI | 22 |
| [Tencent-Hunyuan/Hunyuan3D-2](https://github.com/Tencent-Hunyuan/Hunyuan3D-2) · [Tencent-Hunyuan/Hunyuan3D-2.1](https://github.com/Tencent-Hunyuan/Hunyuan3D-2.1) · [документация ComfyUI](https://docs.comfy.org/tutorials/3d/hunyuan3D-2) · [PolyGen 1.5](https://www.scenario.com/models/hunyuan-polygen-15) | Модели Hunyuan3D и закрытый PolyGen | 22 |
| [TencentARC/Pixal3D](https://github.com/TencentARC/Pixal3D) · [VAST-AI-Research/TripoSG](https://github.com/VAST-AI-Research/TripoSG) · [VAST-AI-Research/TripoSR](https://github.com/VAST-AI-Research/TripoSR) · [Stability-AI/stable-fast-3d](https://github.com/Stability-AI/stable-fast-3d) · [stepfun-ai/Step1X-3D](https://github.com/stepfun-ai/Step1X-3D) · [DreamTechAI/Direct3D-S2](https://github.com/DreamTechAI/Direct3D-S2) · [facebookresearch/sam-3d-objects](https://github.com/facebookresearch/sam-3d-objects) | Другие открытые модели «изображение → 3D» | 22 |
| [wgsxm/PartCrafter](https://github.com/wgsxm/PartCrafter) · [buaacyw/MeshAnythingV2](https://github.com/buaacyw/MeshAnythingV2) · [zhaorw02/DeepMesh](https://github.com/zhaorw02/DeepMesh) | Генерация мешей по частям и низкополигональных мешей | 22 |
| [VAST-AI-Research/UniRig](https://github.com/VAST-AI-Research/UniRig) · [VAST-AI-Research/SkinTokens](https://github.com/VAST-AI-Research/SkinTokens) | Автоматический риггинг | 22 |
| [EricWang12/PartUV](https://github.com/EricWang12/PartUV) · [jpcy/xatlas](https://github.com/jpcy/xatlas) · [zeux/meshoptimizer](https://github.com/zeux/meshoptimizer) · [pyvista/fast-simplification](https://github.com/pyvista/fast-simplification) · [cnr-isti-vclab/PyMeshLab](https://github.com/cnr-isti-vclab/PyMeshLab) | UV-развёртка и упрощение мешей | 22 |
| [SarahWeiii/CoACD](https://github.com/SarahWeiii/CoACD) · [kmammou/v-hacd](https://github.com/kmammou/v-hacd) | Выпуклая декомпозиция для коллизий | 22 |
| [NVlabs/nvdiffrast](https://github.com/NVlabs/nvdiffrast) | Дифференцируемый растеризатор, который используют многие 3D-модели | 22 |
| [Tripo](https://developers.tripo3d.com/en/docs/mesh-decimate.md) · [Meshy](https://www.meshy.ai/features/low-poly) | Коммерческие API для низкополигональных мешей и ретопологии | 22 |

### Текстуры и апскейл

| Источник | Заметки | Отчёты |
|---|---|---|
| [Comfy-Org/ComfyUI](https://github.com/Comfy-Org/ComfyUI) · [Comfy-Org/comfy-mcp](https://github.com/Comfy-Org/comfy-mcp) · [NVIDIAGameWorks/ComfyUI-RTX-Remix](https://github.com/NVIDIAGameWorks/ComfyUI-RTX-Remix) | ComfyUI, его MCP-сервер и узлы RTX Remix | 22 |
| [OliverCrosby/ComfyUI-Universal-Seamless-Tiles](https://github.com/OliverCrosby/ComfyUI-Universal-Seamless-Tiles) · [spinagon/ComfyUI-seamless-tiling](https://github.com/spinagon/ComfyUI-seamless-tiling) | Бесшовные тайлы для сгенерированных текстур | 22 |
| [sakalond/StableGen](https://github.com/sakalond/StableGen) · [carson-katri/dream-textures](https://github.com/carson-katri/dream-textures) | Текстурирование в Blender диффузионными моделями | 22 |
| [QwenLM/Qwen-Image](https://github.com/QwenLM/Qwen-Image) · [Tongyi-MAI/Z-Image](https://github.com/Tongyi-MAI/Z-Image) · [FLUX.2 klein](https://bfl.ai/blog/flux2-klein-towards-interactive-visual-intelligence) · [лицензия FLUX.1](https://scancode-licensedb.aboutcode.org/flux-1-nc.html) | Открытые модели изображений и их лицензии | 22 |
| [ostris/ai-toolkit](https://github.com/ostris/ai-toolkit) · [kohya-ss/sd-scripts](https://github.com/kohya-ss/sd-scripts) | Наборы для обучения LoRA | 22 |
| [xinntao/Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN) · [chaiNNer-org/spandrel](https://github.com/chaiNNer-org/spandrel) · [chaiNNer-org/chaiNNer](https://github.com/chaiNNer-org/chaiNNer) · [upscayl/upscayl](https://github.com/upscayl/upscayl) | Апскейлеры и загрузчики моделей | 22 |
| [Kim2091/PBRify_Remix](https://github.com/Kim2091/PBRify_Remix) | PBRify: апскейлер текстур, обученный на CC0, и PBR-карты | 22 |
| [GPUOpen-Tools/compressonator](https://github.com/GPUOpen-Tools/compressonator) · [texconv](https://github.com/microsoft/DirectXTex/wiki/Texconv) | Кодировщики BCn/DXT для сравнения с нашим | 22 |
| [SigLIP2](https://huggingface.co/google/siglip2-so400m-patch14-384) · [DINOv3](https://huggingface.co/facebook/dinov3-vitl16-pretrain-lvd1689m) | Визуальные энкодеры для поиска изображений и кондиционирования | 22 |

**OpenModelDB**: модели апскейла, сравнённые для игровых текстур (отчёт 22): [4x-UltraSharp](https://openmodeldb.info/models/4x-UltraSharp) · [4x-RealisticRescaler](https://openmodeldb.info/models/4x-RealisticRescaler) · [4x-PBRify-UpscalerV4](https://openmodeldb.info/models/4x-PBRify-UpscalerV4) · [4x-Textures-GTAV-rgt-s](https://openmodeldb.info/models/4x-Textures-GTAV-rgt-s) · [1x-DXTDecompressor-Source-V3](https://openmodeldb.info/models/1x-DXTDecompressor-Source-V3) · [1x-SpongeBC1-Lite](https://openmodeldb.info/models/1x-SpongeBC1-Lite).

### Железо, облако и контекст

| Источник | Заметки | Отчёты |
|---|---|---|
| [discuss.pytorch.org](https://discuss.pytorch.org/t/pytorch-support-for-sm120/216099) · [RunPod](https://www.runpod.io/gpu-cloud/pricing) · [fal.ai](https://fal.ai/) | Поддержка новых GPU в PyTorch и цены облачных GPU | 22 |
| [cinevva](https://app.cinevva.com/guides/ai-3d-model-generation-timeline-2026) · [arXiv 2608.26238](https://arxiv.org/abs/2608.26238) | Хронология выпусков ИИ-3D и статья о сборке силами LLM | 22 |
| [ИИ-проект flyaway888](https://dsogaming.com/?p=155729) · [ИИ-пак HD-текстур](https://www.dsogaming.com/news/grand-theft-auto-san-andreas-gets-a-4-6gb-ai-enhanced-hd-texture-pack/) · [Definitive Edition](https://www.pcgamer.com/uk/gta-trilogy-definitive-edition-is-a-mess/) | Новости об ИИ-апскейле в сообществе GTA и уроки Definitive Edition | 22 |

## ИИ-агенты, MCP и тестирование игр

Как ИИ-клиенты подключаются к MCP-серверам и что уже сделано в тестировании игр агентами.

### MCP-клиенты и упаковка

| Источник | Заметки | Отчёты |
|---|---|---|
| [Claude Code MCP](https://code.claude.com/docs/en/mcp) · [маркетплейсы плагинов](https://code.claude.com/docs/en/plugin-marketplaces) · [расширения Claude Desktop](https://www.anthropic.com/engineering/desktop-extensions) | Настройка MCP в Claude Code, плагины и расширения для Claude Desktop | 31 |
| [modelcontextprotocol/mcpb](https://github.com/modelcontextprotocol/mcpb) · [блог](https://blog.modelcontextprotocol.io/posts/2025-11-20-adopting-mcpb/) | MCP Bundles (MCPB) | 31 |
| [Codex MCP](https://developers.openai.com/codex/mcp) · [skills](https://learn.chatgpt.com/docs/build-skills) | MCP и skills в OpenAI Codex | 31 |
| [Cursor](https://cursor.com/docs/mcp/install-links) · [VS Code](https://code.visualstudio.com/docs/agent-customization/mcp-servers) · [API VS Code](https://code.visualstudio.com/api/extension-guides/ai/mcp) · [Gemini CLI](https://google-gemini.github.io/gemini-cli/docs/tools/mcp-server.html) · [LM Studio](https://lmstudio.ai/docs/app/mcp) | Настройка MCP в Cursor, VS Code, Gemini CLI и LM Studio (`satk mcp config --client`) | 31 |
| [proofpoint.com](https://www.proofpoint.com/us/blog/threat-insight/cursorjack-weaponizing-deeplinks-exploit-cursor-ide) | Исследование безопасности диплинков установки MCP | 31 |
| [TabbedScamper/GTAV-CLAUDE-MCP](https://github.com/TabbedScamper/GTAV-CLAUDE-MCP) | Мост из GTA V к Claude: образец для нашей цели `game` | 12 |

### Агенты, которые играют и тестируют игры

| Источник | Заметки | Отчёты |
|---|---|---|
| [alexzhang13/videogamebench](https://github.com/alexzhang13/videogamebench) · [arXiv 2505.18134](https://arxiv.org/abs/2505.18134) · [arXiv 2103.15819](https://arxiv.org/abs/2103.15819) | VideoGameBench и исследование EA SEED об агентах-тестировщиках | 26 |
| [BAAI-Agents/Cradle](https://github.com/BAAI-Agents/Cradle) · [JackHopkins/factorio-learning-environment](https://github.com/JackHopkins/factorio-learning-environment) · [PrismarineJS/mineflayer](https://github.com/PrismarineJS/mineflayer) · [Unity-Technologies/ml-agents](https://github.com/Unity-Technologies/ml-agents) · [alttester/AltTester-Unity-SDK](https://github.com/alttester/AltTester-Unity-SDK) · [aitorzip/DeepGTAV](https://github.com/aitorzip/DeepGTAV) | Предшественники: агенты по экрану, игровые среды с API и инструментированные сборки | 26 |
| [Unreal Gauntlet](https://dev.epicgames.com/documentation/en-us/unreal-engine/gauntlet-automation-framework-in-unreal-engine) | Фреймворк автоматизации тестовых сессий в Unreal | 26 |
| [ra1nty/DXcam](https://github.com/ra1nty/DXcam) · [NiiightmareXD/windows-capture](https://github.com/NiiightmareXD/windows-capture) · [IsBorderRequired](https://learn.microsoft.com/en-us/uwp/api/windows.graphics.capture.graphicscapturesession.isborderrequired) · [SendInput](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput) | Захват экрана и ввод в Windows | 26 |
| [google/swiftshader](https://github.com/google/swiftshader) · [WARP](https://learn.microsoft.com/en-us/windows/win32/direct3darticles/directx-warp) · [большие раннеры GitHub](https://docs.github.com/en/actions/reference/runners/larger-runners) | Программный рендер и CI-раннеры для тестов игры без окна | 26 |

## Маппинг и экосистема контента

Инструменты маппинга и конвертеры SA-MP и MTA, на которые опираются `satk map convert` и `satk id`.

| Источник | Заметки | Отчёты |
|---|---|---|
| [Pottus/Texture-Studio](https://github.com/Pottus/Texture-Studio) · [вики](https://github.com/Crayder/Texture-Studio/wiki) · [forum.open.mp](https://forum.open.mp/showthread.php?tid=174) | Texture Studio: де-факто стандартный редактор карт SA-MP; `satk map convert` читает его текстовые форматы | 23 |
| [ins1x/mtools](https://github.com/ins1x/mtools) | mtools: GUI-надстройка для Texture Studio (MPL-2.0) | 23, 29 |
| [simonseidel/Map-Editor-V3](https://github.com/simonseidel/Map-Editor-V3) · [NexiusTailer/Ultimate-Creator](https://github.com/NexiusTailer/Ultimate-Creator) | Fusez's Map Editor V3 и Ultimate Creator | 23 |
| [Papawy/Papawyconv](https://github.com/Papawy/Papawyconv) · [moon91210/ipl2map](https://github.com/moon91210/ipl2map) · [gta191977649/GTA-SAMP-MapConverter](https://github.com/gta191977649/GTA-SAMP-MapConverter) · [Fernando-A-Rocha/mta-map-to-ipl](https://github.com/Fernando-A-Rocha/mta-map-to-ipl) · [convertffs.com](https://convertffs.com/) | Конвертеры карт между IPL, Pawn и `.map` MTA | 23, 30 |
| [Pottus/ColAndreas](https://github.com/Pottus/ColAndreas) · [philip1337/samp-plugin-mapandreas](https://github.com/philip1337/samp-plugin-mapandreas) · [samp-incognito/samp-streamer-plugin](https://github.com/samp-incognito/samp-streamer-plugin) | Плагины сервера SA-MP: коллизии, карта высот и стример объектов | 21, 23 |
| [San-Andreas-Roleplay-ES/crc32](https://github.com/San-Andreas-Roleplay-ES/crc32) | CRC32 файлов DFF/TXD, как его считают загрузки кастомных моделей SA-MP | 23 |
| [Poly Haven](https://polyhaven.com/license) · [ambientCG](https://docs.ambientcg.com/license/) · [Kenney](https://kenney.nl/support) | Библиотеки ассетов CC0, пригодные для нового контента | 23 |

## Упаковка, распространение и внедрение

Источники для переносимого релиза, установки и каналов сообщества.

| Источник | Заметки | Отчёты |
|---|---|---|
| [docs.python.org](https://docs.python.org/3/using/windows.html) · [менеджер установки Python](https://www.python.org/downloads/release/pymanager-252/) | Python в Windows и новый менеджер установки | 31 |
| [astral-sh/uv](https://github.com/astral-sh/uv) | Установщик пакетов и инструментов uv | 31 |
| [обсуждение PyInstaller](https://github.com/orgs/pyinstaller/discussions/5877) · [coderslegacy.com](https://coderslegacy.com/pyinstaller-exe-detected-as-virus-solutions/) | Ложные срабатывания антивирусов на сборки PyInstaller (поэтому мы выпускаем переносимый zip) | 31 |
| [Artifact Signing](https://learn.microsoft.com/en-us/azure/artifact-signing/quickstart) · [melatonin.dev](https://melatonin.dev/blog/code-signing-on-windows-with-azure-trusted-signing/) · [SignPath](https://signpath.org/terms.html) | Варианты подписи кода в Windows | 31 |
| [openiv.co](https://openiv.co/how-to-use/) · [gtaforums.com](https://gtaforums.com/topic/798272-tut-use-openiv-mods-folder-keep-your-original-gta-v/) | Папка mods в OpenIV: привычная модерам схема установки | 31 |
| [spec-kit #1062](https://github.com/github/spec-kit/issues/1062) | Пример проблем входа для новичков | 31 |
| [Telegram (Википедия)](https://en.wikipedia.org/wiki/Blocking_of_Telegram_in_Russia) · [Meduza](https://meduza.io/amp/en/feature/2026/06/11/russia-has-been-blocking-telegram-for-months-meduza-asked-five-popular-channel-admins-if-it-s-working) · [Euronews](https://www.euronews.com/2026/02/10/russia-restricts-telegram-over-alleged-law-breaches-as-it-supports-state-backed-rival) · [RFE/RL](https://www.rferl.org/a/roskomnadzor-blocks-discord-tech-russia-ukraine/33152178.html) · [The Record](https://therecord.media/discord-messaging-app-banned-russia-turkey) | Ограничения Telegram и Discord в России (выбор каналов сообщества) | 31 |

## Правовые источники и политики

Что мы прочли для отчёта о праве; из-за этого satk не содержит ни данных игры, ни декомпилированного кода.

| Источник | Заметки | Отчёты |
|---|---|---|
| [правила для модов](https://www.rockstargames.com/community-resources/mod-guidelines) · [правовая информация](https://www.rockstargames.com/legal) · [одиночные моды](https://support.rockstargames.com/articles/5NVOAYjcTomO8v6SX2k76k/pc-single-player-mods) · [en-US](https://support.rockstargames.com/en-US/articles/5NVOAYjcTomO8v6SX2k76k/pc-single-player-mods) · [diffchecker](https://www.diffchecker.com/6VRyddGs) | Правила Rockstar Games для модов, условия и статья об одиночных модах (со сравнением старых редакций) | 12, 17, 23, 25 |
| [политика DMCA](https://docs.github.com/en/site-policy/content-removal-policies/dmca-takedown-policy) · [github/dmca](https://github.com/github/dmca) | Политика DMCA GitHub и публичные уведомления (дела Take-Two) | 12, 17, 24, 25 |
| [fivem.net/terms](https://fivem.net/terms) · [лицензия платформы](https://static.cfx.re/platform-license-agreement-10-sept-2026.pdf) · [FiveM (Википедия)](https://en.wikipedia.org/wiki/FiveM) · [RedM](https://redm.net/) | FiveM и RedM: прецедент лицензированной платформы | 25 |
| [Директива 2009/24/EC](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32009L0024) · [17 U.S.C. 1201](https://www.law.cornell.edu/uscode/text/17/1201) | Директива ЕС о программах (интероперабельность) и закон США об обходе защиты | 25 |
| [copyright.gov/ai](https://www.copyright.gov/ai/) · [NewsNet 1060](https://copyright.gov/newsnet/2025/1060.html) | Бюро авторского права США о работах, созданных ИИ (часть 2, 2025) | 22, 25 |
| [FAQ по GPL](https://www.gnu.org/licenses/gpl-faq.html) | FAQ по GNU GPL (линковка и агрегация) | 25 |
| [BASS](https://www.un4seen.com/bass.html) · [FMOD](https://www.fmod.com/legal) | Лицензии аудиобиблиотек в MTA | 05, 25 |
| [TorrentFreak: иск](https://torrentfreak.com/take-two-dismisses-claims-against-lead-defendants-in-gta-mods-lawsuit-230405/) · [TorrentFreak: MTA восстановлена](https://torrentfreak.com/github-restores-repo-of-gta-mod-multi-theft-auto-after-take-two-fails-to-sue/) · [TorrentFreak](https://torrentfreak.com/?p=275921) · [HotHardware: re3](https://hothardware.com/news/gta-3-and-vice-city-reverse-engineered) · [HotHardware: снятия](https://hothardware.com/news/take-two-gta-mods-takedown) | Освещение дела re3/reVC и снятия и восстановления MTA | 12, 17, 23, 25 |
| [Kotaku: правила](https://kotaku.com/rockstar-asks-fans-to-respect-their-games-as-it-updates-strict-modding-guidelines-2000736306) · [Inven Global](https://www.invenglobal.com/articles/26384/no-exception-for-gta-6-rockstar-releases-mod-guidelines-across-all-titles) · [GTA BOOM: правила](https://www.gtaboom.com/rockstar-games-new-mod-guidelines-target-unofficial-gta-ports-20f4) · [Mein-MMO](https://mein-mmo.de/en/gta-mods-nur-noch-bei-rockstar,1552582) | Освещение правил Rockstar для модов 2026 года | 12, 16, 17, 23, 25 |
| [GTA BOOM: alt:V](https://www.gtaboom.com/the-last-gta-v-multiplayer-alternative-just-got-a-cease-and-desist-3970) · [Gameranx](https://gameranx.com/updates/id/561049/article/take-two-has-sent-a-takedown-notice-to-gta-v-fivem-alternative-altv/) · [Rockstar Intel](https://rockstarintel.com/rockstar-issues-policy-update-on-roleplay-servers-gta-rp-fivem-and-more/) | Снятие альтернативных мультиплеерных платформ и политика Rockstar для RP-серверов | 16, 25 |
| [Dexerto](https://www.dexerto.com/gta/rockstar-takes-legal-action-after-modders-port-gta-5-to-nintendo-switch-3413248/) · [Generation Amiga](https://www.generationamiga.com/2026/07/18/gta-san-andreas-android-port-released-for-nintendo-switch/) · [GTA BOOM: браузер](https://www.gtaboom.com/you-can-now-play-gta-san-andreas-in-your-web-browser-too-a8f6) | Неофициальные порты и почему они юридический риск | 12, 17, 25 |
| [Kotaku: GTA Underground](https://kotaku.com/ambitious-gta-underground-mod-shutdowns-after-six-years-1847619249) · [PCGH: Carcer City](https://www.pcgameshardware.de/Grand-Theft-Auto-San-Andreas-Spiel-56275/News/Total-Conversion-Carcer-City-Demo-1492433/) · [GGRecon: Stars and Stripes](https://www.ggrecon.com/articles/grand-theft-auto-stars-and-stripes-mod-is-gta-7-in-the-3d-era) | Тотальные конверсии и чем они закончились | 23 |
| [gtaintel.com](https://gtaintel.com/news/san-andreas-multiplayer-in-2026) | Состояние мультиплеера San Andreas в 2026 году | 12, 21, 25 |

## Сайты сообщества и уроки

Площадки сообщества и уроки, по которым видно, как моддеры работают на деле.

| Источник | Заметки | Отчёты |
|---|---|---|
| [blast.hk](https://www.blast.hk/) · [гайд по MoonLoader](https://www.blast.hk/threads/21056/) · [тема 254794](https://www.blast.hk/threads/254794/) | BlastHack: русскоязычное сообщество скриптов SA-MP и MoonLoader | 29, 31 |
| [MixMods (GTAForums)](https://gtaforums.com/topic/999795-mixmods/) · [машины без замены](https://www.mixmods.com.br/2020/02/tutorial-adicionar-carros-sem-substituir/) | MixMods: бразильская площадка моддинга и её урок по add-on-машинам | 29, 31 |
| [новые машины](https://gtaforums.com/topic/832297-satut-how-to-add-new-cars-without-replacing/) · [новое оружие](https://gtaforums.com/topic/990989-sahow-to-add-new-weapons/) | Уроки GTAForums по add-on-машинам и оружию (ручной процесс, с которым помогает `satk id`) | 29 |

## Без ссылок

Наши отчёты упоминают эти источники, но ссылок на них мы не даём: там лежат ассеты или код игры, либо они недоступны. satk их не использует.

- Каталог моделей и текстур Prineside DevTools: на нём лежат изображения моделей и текстур игры (отчёты 12, 23, 24, 31).
- CDN превью GTA Stuff: рендеры ассетов игры (отчёт 09).
- ИИ-апскейл текстур на ModDB: распространяют текстуры игры (отчёты 22, 29).
- Загрузки контента на LibertyCity: пак педов и изменённый файл данных игры (отчёты 18, 29).
- Тема форума о переносе моделей из других игр (отчёт 29).
- gta-reversed-android: в репозитории лежат проприетарный бинарник и скрипты игры для Android (отчёт 17).
- Код re3/reVC и перезаливка reVC: сняты по DMCA (отчёт 17).
- gta-workshop: публикует полный вывод декомпилятора gta_sa.exe (отчёт 24).
- Старая публичная IDA-база gta_sa.exe: в ней содержится код исполняемого файла (отчёты 10, 12).
- mod_sa: чит для мультиплеера (отчёт 12).
- scene2res: OSDN не отвечает (отчёт 23).
- Исходные сайт и репозиторий OpenSA: просроченный сертификат и 404; зеркало указано выше (отчёт 12).
- Crspy/GTA_SA_IDB: пустой репозиторий (отчёты 10, 12).

Адреса из исходного кода (локальные адреса, серверы обновлений и реестров), страницы каталогов, повторяющие репозиторий из списка выше, индексы разделов и прямые ссылки на бинарники не перечислены.
