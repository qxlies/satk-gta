# Типовые задачи

[English version](../en/workflows.md)

<!-- Человеческая версия агентных сценариев S1–S29 (агентная, с ценой в токенах — docs/agent/workflows.md).
     Цепочки прогнаны заново 2026-10-05 с индексами схемы v3; числа в комментариях — настоящие ответы. -->

Команды — для PowerShell/cmd: `satk` — это шим `tools\satk.cmd` рабочего пространства (или команда `satk`
установленного пакета). ИИ-ассистент выполняет те же цепочки через MCP-инструменты с теми же именами
(`satk asset find` = `asset_find`; операции без своего инструмента — через `satk_op`), см.
[docs/agent/workflows.md](../agent/workflows.md).

## Быстрый пример

```powershell
satk asset refs tex:711_sfw/ws_rooftarmac1
satk world near 2495 -1687 --r 30 --limit 10
satk re addr gta_sa.exe+0x13BF09
satk index diff vanilla installed --kind txd
satk view capture --target mock --grid
satk view pick --cells D3 --target mock
```

По одной команде из нескольких сценариев ниже; команды вьювера работают со встроенной заглушкой (`--target mock`),
которой окно не нужно.

## Найти текстуру и понять, где она используется (S1)

```powershell
satk asset find ws_rooftarmac1 --kind tex --limit 5          # total 257: текстура с этим именем в 257 TXD
satk index query "SELECT v.sid, v.name, v.n_inst FROM model_tex t JOIN v_model v ON v.id = t.model_id WHERE t.texture = ? ORDER BY v.n_inst DESC" --params ws_rooftarmac1 --limit 5
satk index query "SELECT v.pix, count(*) AS txds FROM texture t JOIN v_tex v ON v.rid = t.id WHERE t.name = ? GROUP BY v.pix ORDER BY txds DESC" --params ws_rooftarmac1
satk texture image pix:45f2dcad0a0e790249e06eb9 pix:404679390e4ba32005cc74c5 pix:a9402c6bc5d7f741d16bf5a9 --mode sheet
```

Первый запрос — сколько TXD содержат текстуру; второй — какие модели её используют (470, больше всего расстановок у
`portakabin`); третий — сколько на самом деле разных картинок за этим именем (3); четвёртый рисует их одним листом.
Для одной конкретной текстуры: `satk asset refs tex:711_sfw/ws_rooftarmac1` (сколько моделей, TXD и одинаковых
картинок: 1, 1, 242) и `satk asset refs tex:711_sfw/ws_rooftarmac1 --rel models`.

## Посмотреть район сверху

```powershell
satk world near 2495 -1687 --r 30 --limit 10                 # что стоит вокруг точки, по расстоянию (всего 41)
satk map image --center 2495,-1687 --span 300 --labels 20    # карта 768×768 с номерами и легендой
```

## Посмотреть место во вьювере и узнать, что это за здание (S2)

```powershell
satk view start --window 960x540
satk view capture --bm grove_center --marks 6                # закладка: Гроув-стрит, взгляд на дом Си-Джея
satk view pick 154 270                                       # что под пикселем (по коллизии; --capture cap:… — в другом кадре)
satk asset get inst:lae2_stream0#39                          # дом слева: ganghous05_LAx, model:3646
satk view stop
```

Закладки — `satk view bookmark list`; своя закладка: `satk view goto --pos X,Y,Z --look X,Y,Z`, затем
`satk view bookmark save <имя>`. Сетка вместо пикселей: `satk view capture --grid` и `satk view pick --cells D3`.
Ariane — «быстрые глаза»: нет теней, людей, машин и частиц, LOD приблизительный. Настоящая игра как цель
(`--target game`, [mta-agent.md](mta-agent.md)) уже есть, но запуск клиента MTA пока ненадёжен.

## Разобрать крэш MTA (S3, S10)

```powershell
satk re addr gta_sa.exe+0x13BF09             # или текст дампа: satk re addr --text-file crash.txt
satk re src CGame::Process --context 12      # код gta-reversed вокруг функции (только показать)
satk re patches --fn CGame::Process          # 22 места, где MTA патчит эту функцию, с file:line
```

`re addr` понимает адреса (`0x53BF09`, `gta_sa.exe+0x13BF09`) и текст крэш-дампа MTA, если строки `Module = …` и
`Offset = …` идут отдельными строками, как в самом дампе. `re patches` сворачивает места upstream, которые повторяет
trunk (`raw_total` 33).

Целый minidump или `core.log` ([crash.md](crash.md)):

```powershell
satk crash sample                            # сгенерированный пример дампа, чтобы попробовать
satk crash list --limit 5                    # дампы и логи крэшей, новые первыми
satk crash analyze --last --limit 5          # стек с функциями: CStreaming::Update+0x33, game_sa/Streaming.cpp:111
```

## Что меняет моя установка (S4, S8)

```powershell
satk index diff vanilla installed --kind txd                 # +3 TXD кнопок консолей в оригинальной установке
satk texture image txd:ps3btns --mode sheet --profile installed
satk index diff vanilla samp --kind model                    # SA-MP: +1 435 моделей, 12 перекрыто
satk asset get model:300 --profile samp                      # lapdna (samp) вместо cutobj01
```

Профили `installed` и `samp` строятся отдельно: `satk index build --profile installed` и `--profile samp`.

## Проверить мод (S12)

```powershell
satk mod check mymod.zip                       # проблемы, таблица rule | sev | file | msg | sid
satk mod inspect mymod.zip                     # что мод заменяет, добавляет и меняет
satk mod conflicts --profile game              # моды в папке modloader игры, которые спорят за файл или запись
```

`mod check` сообщает о пересечениях ID, файлах, которые Mod Loader пропустит, записях игры, которые теряет неполный
файл данных, ID сверх лимита и находках линтера ассетов ([lint.md](lint.md)); `mod inspect` перечисляет, что мод
заменяет, добавляет и меняет ([modinspect.md](modinspect.md)). Подходят папка, `.zip`, `.img` или отдельный файл.

## Заменить текстуры (S13)

```powershell
satk texture extract txd:bistro                              # все текстуры в PNG + texmod.json: work\out\texmod\bistro\
satk texture replace txd:bistro vent_64=my_vent.png          # PSNR по каждой текстуре, папка для Mod Loader
satk mod check <workspace>\work\out\mods\bistro              # 26 info (мипмапы), ничего серьёзнее
```

Результат — `work\out\mods\bistro\bistro.txd` + `README.txt`, папка для Mod Loader; в ответе — формат, размер, мипмапы
и PSNR каждой текстуры. Новый TXD из папки PNG — `satk texture pack` ([texmod.md](texmod.md)).

## Модель: превью и экспорт

```powershell
satk model image model:411                     # 4 ракурса одним листом 768×768 (программный рендер)
satk asset export model:411 --format glb       # glTF для Blender и просмотрщиков: work\out\models\infernus\
satk asset export model:411 --format raw       # DFF + TXD «как в игре» (коллизия — внутри DFF)
```

## Найти модель по тому, как она выглядит (S14)

```powershell
satk describe import                           # один раз: 2 131 описание gta-scout в заметки
satk asset find bench --kind note              # 15 моделей, в описании которых есть скамейка
satk model image model:4085 --views 1 --size 256
```

Описания привязываются к моделям по хэшу DFF ([describe.md](describe.md)).

## Данные машин (S15)

```powershell
satk asset get model:411 --fields name,handling,colors,links  # mass 1400, max_vel 240, 8 пар цветов
satk asset get handling:infernus                              # все поля handling.cfg и флаги
```

## Район в Blender и обратно в MTA (S5)

```powershell
satk blender doctor --quick
satk blender import-area --center 2495,-1687 --box 60 --lod hd
satk blender render --blend <workspace>\work\blender\jobs\<job>\scene.blend --pos 2495,-1757,45 --look 2495,-1687,13 --objindex
satk blender export --blend <workspace>\work\blender\jobs\<job>\scene.blend --objects carlshou1_lae2 --target mta-resource
```

`<job>` — папка задания из ответа `import-area` (`files.blend`). Район 60×60 м: импорт около 7 с, рендер около 3 с,
экспорт около 2 с; результат экспорта — в `work\out\exports\`.

## Конвертировать карту (S16)

```powershell
satk map validate ipl:lae2_stream0 --limit 3   # 377 объектов, 0 ошибок, 2 строки info
satk map convert ipl:lae2_stream0 --to mta     # work\out\mapconv\lae2_stream0.map + отчёт JSON
```

`map convert` читает Pawn SA-MP, `.map` MTA, текстовый IPL и JSON и пишет любой из них ([mapconv.md](mapconv.md)).

## Одним SQL-запросом (S7)

```powershell
satk index query "SELECT i.is_lod, count(DISTINCT m.id) AS models FROM v_inst i JOIN v_model m ON m.id = i.model_id JOIN zone z ON z.name = 'VE' WHERE i.x BETWEEN z.minx AND z.maxx AND i.y BETWEEN z.miny AND z.maxy AND i.area = 0 AND m.col_via IS NULL GROUP BY i.is_lod"
```

Ответ `[[0,1],[1,911]]`: в Лас-Вентурасе 911 LOD-моделей без коллизии (так и задумано) и одна не-LOD расстановка без
неё. Схема таблиц — `satk help schema` или [docs/agent/schema.md](../agent/schema.md).

## Знания о движке (S11)

```powershell
satk kb build                                  # один раз, около 30 с: читает исходники-доноры
satk kb struct CPed --at 0x540                 # m_fHealth, float; размер CPed — 0x79C байт
satk kb opcode 0A8C                            # WRITE_MEMORY (CLEO) и его обработчик в gta-reversed
```

Подробнее — в [kb.md](kb.md).

## Добавить новую машину отдельным аддоном (S17)

```powershell
satk mod add vehicle --dff dff:infernus --txd txd:infernus --like 411 --name infernus2 --game-name "Infernus II" --dry-run
satk mod add vehicle --dff my_car.dff --txd my_car.txd --like 411 --name mycar --game-name "My Car"
satk mod check <workspace>\work\out\addon\mycar
```

`mod add` копирует строки данных донора (IDE, handling, carcols, carmods, игровое название) со свободным id и новыми
именами в папку для Mod Loader `work\out\addon\<name>\`; эту папку копируют в папку игры `modloader`. Читайте
предупреждения: id машины больше 611 или новое имя handling требуют fastman92 Limit Adjuster. Педы, оружие и объекты
добавляются так же ([addon.md](addon.md)).

## Изменить данные handling или оружия (S18)

```powershell
satk data get handling infernus --field fMass fTractionMultiplier   # значение, единица и смысл каждого поля
satk data explain handling fTractionBias                           # единица, диапазон, колонка, имя в MTA
satk data patch handling infernus fMass=1500 fTractionMultiplier=0.8
satk data patch handling infernus fMass=1500 --target mta          # вместо этого — фрагмент Lua с setModelHandling
```

По умолчанию пишется папка для Mod Loader, в readme которой только изменённая строка; `--target full` пишет
исправленную копию всего файла. Оружие: `satk data get weapon PISTOL`, `satk data patch weapon ...`.

## Написать или исправить скрипт CLEO (S19)

```powershell
satk script new spawn_car --name mycar --model 522 --cheat BIKE   # проверенный .txt и собранный .cs
satk script check mycar/mycar.txt                                  # ошибки с номерами строк
satk script asm mycar/mycar.txt
satk script disasm mymod.cs                                        # чужой скрипт в виде текста
satk script asm mymod/mymod.txt --compare mymod.cs                 # что изменила ваша правка
```

satk никогда не пишет в игру: скопируйте `.cs` в папку игры `cleo` (CLEO 4 или 5). Сигнатуры опкодов:
`satk kb opcode <id или слова>` ([script.md](script.md)).

## Найти функцию MTA Lua или натив SA-MP (S20)

```powershell
satk kb mta engineRequestModel                 # сигнатура, клиент/сервер, enum, строка исходника C++
satk kb mta "vehicle handling"                 # все функции, в имени которых есть оба слова
satk kb mta --event onClientElementStreamIn
satk kb native SetObjectMaterial
```

Ответы берутся из локальных исходников через базу знаний (после обновления — `satk kb build`). Для полного списка
SA-MP/open.mp укажите в `satk.toml` параметр `kb.pawn_include` — папку include от pawno или qawno
([scriptapi.md](scriptapi.md)).

## Уменьшить текстуры мода (S21)

```powershell
satk texture audit mymod                                        # проблемы по сэкономленным байтам и способ исправить
satk texture optimize mymod --max 512 --drop-unused --dedupe    # копии в work\out\txdopt\mymod\, PSNR для каждой текстуры
satk texture budget --area 2495 -1666 150                       # память стриминга вокруг точки против лимита 50 МиБ
```

Перед тем как копировать результат поверх мода, посмотрите `psnr_min` и каждое предупреждение `LOW_PSNR`
([txdopt.md](txdopt.md)).

## Проверить мод перед релизом; много файлов сразу (S22)

```powershell
satk recipe list
satk recipe run check-mod-before-release --var mod=mymod --dry-run
satk recipe run check-mod-before-release --var mod=mymod
satk batch asset.lint --over "models/gta3.img/infernus.*" --arg fail_on=error --jobs 2
```

Рецепт — сохранённая цепочка операций (что меняет мод, проверка, аудит текстур, конфликты id); пакетный режим
выполняет одну операцию для маски, записей IMG, файла-списка, папки или запроса к индексу и пишет по строке JSON на
каждый вход ([batch.md](batch.md)).

## Добавить модели уличный фонарь (S23)

```powershell
satk fx2d dump model:lamppost1                                       # свет фонарного столба в виде JSON
satk fx2d copy model:lamppost1 my_lamp.dff --filter light --offset 0,0,1
satk fx2d check my_lamp.dff
```

Результат — копия DFF в `work\out\fx2d\`; `fx2d apply` записывает обратно JSON, исправленный руками
([fx2d.md](fx2d.md)).

## Коллизия для новой модели (S24)

```powershell
satk col gen my_model.dff                       # файл COL3 в work\out\colgen\
satk col gen my_models --archive my_models.col  # один .col на папку моделей
satk col check my_models.col
```

Способ выбирается для каждой модели (оболочка для мелких объектов, сферы для машин, сетка для больших); поверхности
берутся из имён текстур, `satk col surface <текстура>` объясняет выбор ([colgen.md](colgen.md)).

## Создать новый ассет в стиле SA (S25)

ИИ-ассистент (или вы) моделирует ассет в живой сессии Blender, показывает картинку рядом с двумя стоковыми
моделями класса после грубой формы и ещё раз после окончательной, затем выгружает папку Mod Loader и проверяет
её против стоковой модели. Весь процесс: [authoring.md](authoring.md); правила стиля:
[sa-style.md](sa-style.md).

## Написать и проверить ресурс MTA (S27)

```powershell
satk mta resource new my-panel                  # заготовка ресурса в work\out\mta\my-panel\
satk mta lint my-panel                          # что сломается на сервере, до загрузки
satk mta pack mods\cars --kind vehicle          # ресурс, который загружает папку файлов DFF/TXD/COL
satk mta server-check my-panel                  # собранный сервер MTA загружает ресурс (127.0.0.1, без клиента игры)
```

`satk mta logs server.log` превращает логи сервера и клиента в строки с подсказкой ([mta.md](mta.md)). Папку
ресурса в `<server>\mods\deathmatch\resources` копируете вы сами: satk никогда не пишет в сервер.

## Анимации (S28)

```powershell
satk anim list ifp:ped --name walk              # анимации ped.ifp: кости, ключи, длительность, движение корня
satk anim extract anim:ped/walk_civi --out walk.json
satk anim write walk.json --out mywalk.ifp --pack mywalk
satk anim check mywalk.ifp --loader mta
satk anim mta mywalk.ifp --replace ped/WALK_civi=WALK_civi
```

Последняя команда пишет клиентский ресурс, который заменяет анимацию игры. Вместо него `anim merge` правит пакет
вроде `ped.ifp`, а `anim to-blender` и `anim from-blender` позволяют править анимации в Blender ([anim.md](anim.md)).

## Увидеть новую модель рядом с картой, а потом в игре (S29)

```powershell
satk view vehicle --dff work\out\kit\mycar\files\mycar\mycar.dff --txd work\out\kit\mycar\files\mycar\mycar.txd --pos 2495,-1675,13.4 --watch
satk view capture --pos 2503,-1666,17 --look 2495,-1674,13.4 --marks 6
satk ingame start --mod work\out\addon\mycar
satk ingame check --suite vehicle
satk ingame reload
```

Вьювер показывает выгруженные файлы рядом с картой через доли секунды после каждой повторной выгрузки (`view place` —
для объекта, `view ped` — для педа); нужна сборка вьювера с методами сцены ([viewscene.md](viewscene.md)). Поведение
(езда, столкновения, повреждения, свет, стриминг) проверяется в настоящей игре: `ingame start` запускает закрытый
тестовый сервер и пишет одноразовую настройку от имени администратора, клиент вы запускаете сами командой
`satk ingame play`, а `ingame check` возвращает вердикт и кадр «мод против ванили» для каждой проверки
([ingame.md](ingame.md)).

## Собрать сервер форка MTA

```powershell
satk engine doctor
satk engine build --project server             # x64, около 3 мин с нуля; после правки одного .cpp — секунды
satk engine server-smoke                       # запуск на 127.0.0.1 и штатная остановка
```

## Заметки

```powershell
satk note add model:411 "Infernus: спорткар, основа для теста стриминга" --tags test
satk note list model:411
satk note list --query спорткар
```
