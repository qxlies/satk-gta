# Типовые задачи

[English version](../en/workflows.md)

<!-- Человеческая версия агентных сценариев S1–S16 (агентная, с ценой в токенах — docs/agent/workflows.md).
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
