# SAAP/1: общий протокол «глаз агента»

[English version](../en/saap.md)

Пакет: `satk.saap`. Норматив — [`proto/SAAP-v1.md`](../../proto/SAAP-v1.md).

## Что это

SAAP/1 (San Andreas Agent Protocol) — один протокол, через который satk управляет всем, что умеет показывать
мир: mock-эндпоинтом, вьювером Ariane и — через мост — настоящей игрой в MTA (`--target game`, см.
[mta-agent.md](mta-agent.md)); нативный эндпоинт в форке MTA и Blender — позже. TCP только на 127.0.0.1,
фрейм `u32 big-endian длина + JSON`, токен в `hello`, возможности в `caps`. Картинки никогда не передаются внутри ответа:
эндпоинт пишет файлы под `work\` и возвращает пути.

## Быстрый пример

```powershell
satk saap validate
satk view conformance --target mock
```

Что вернётся (сокращённо):

```json
{"ok":true,"cols":["file","kind","items","errors"],"rows":[["camera.jsonl","cases",5,0],["SAAP-v1.md","spec",23,0]],"valid":true,"schemas":23}
{"ok":true,"cols":["case","caps","result","ms","detail"],"rows":[["auth.bad_token","core","skip",0.0,"needs the SAAP transport"]],"total":48,"pass":38,"fail":0,"skip":10,"percent":100.0}
```

Без запущенного mock-эндпоинта conformance идёт «в процессе», и 10 транспортных кейсов (AUTH, PROTOCOL)
пропускаются. С эндпоинтом (`satk view start --target mock`) проходят все 48.

## Команды

| Команда | MCP | Что делает |
|---|---|---|
| `satk saap validate [files…]` | — | проверяет кейсы `*.jsonl`, `SAAP-v1.md` и конверты `*.json` по схемам (по умолчанию `proto/conformance/*.jsonl` и `proto/SAAP-v1.md`) |
| `satk saap call ROLE METHOD ['{json}'] [--timeout S]` | — | один запрос к запущенному эндпоинту, например `satk saap call mock camera.get` |
| `satk saap cpp-selftest [--toolsets v143 v145] [--keep]` | — | собирает и запускает самотест `saap_frame.hpp` через `cl.exe` (`/std:c++14` и `/std:c++latest`) |
| `satk view mock [--port 0]` | — | mock-эндпоинт в этом окне до `quit`/Ctrl+C |
| `satk view conformance --target T [--only RE] [--files F…] [--show-all]` | — | прогон кейсов против цели, отфильтрованных по её `caps` |

## Как это устроено

- `proto/schema/<method>.json` — JSON Schema 2020-12 для `params` и `result` каждого из 23 методов,
  общие типы (`Pose`, `Env`, `EntityRef`) — `common.json`. Валидатор в `satk.saap.schema` — только stdlib
  (работает и из Python Blender); тесты сверяют его с пакетом `jsonschema`.
- `proto/conformance/*.jsonl` — 48 кейсов «запрос → ожидание», включая негативные: неверный токен → `AUTH`
  и разрыв, фрейм больше 1 МиБ → `PROTOCOL` (и только заголовок, и целиком с телом), неизвестный метод →
  `UNKNOWN_METHOD`, устаревший `expect_rev` → `REVISION`, неизвестный слой → `UNSUPPORTED`.
- Разрыв после ошибки «мягкий»: эндпоинт отправляет ответ, закрывает свою сторону на запись, дочитывает и
  выбрасывает то, что клиент ещё шлёт (≤ 4 МиБ, ≤ 2 с), и только потом закрывает сокет. Иначе Windows
  обрывает соединение (RST), и клиент теряет ответ `PROTOCOL` — так было с телом фрейма больше 1 МиБ.
- Обнаружение: эндпоинт пишет `work\run\endpoints\<role>.json` (без токена), запускающий —
  `work\run\sessions\<role>.json` (с токеном). В файлах есть путь образа (`exe`) и время старта процесса
  (`pid_created`). Windows переиспользует pid, поэтому клиент проверяет не только «процесс жив», но и что это
  тот самый процесс (`satk.saap.client.verify_pid`); файлы, указывающие на чужой процесс, считаются
  устаревшими.
- Mock — детерминированный синтетический мир у Гроув-стрит: все возможности протокола, одинаковые запросы
  дают побайтно одинаковые PNG, стриминг имитируется (без settle кадр «с дырами»). Методы `author` работают
  на маленькой искусственной сцене (`satk.studio.mock`).
- Сессии студии Blender ([studio.md](studio.md)) — эндпоинты с ролью `blender` и возможностями `core` + `author`;
  у каждой сессии своя пара файлов `blender-<name>.json`.
- `proto/cpp/saap_frame.hpp` (MIT, C++14, один заголовочный файл): фрейм, лимиты, сравнение токена за постоянное время,
  атомарная запись дескриптора. Его вендорят нативные эндпоинты: форк Ariane
  (`viewer\ariane\tools\euryopa\saap\`) и позже форк MTA. <!-- linkcheck: ignore -->
- Нативный эндпоинт Ariane проходит все применимые кейсы: `satk view conformance --target ariane` →
  43 pass, 0 fail, 11 skip (`log`, `console`, `lua`, `mem.read` и `author` у Ariane нет). Он сам пишет
  `work\run\endpoints\ariane.json`, слушает эфемерный порт и принимает `path_prefix` только внутри `work\`
  (`SATK_AGENT_OUT_ROOT`).

## Ограничения и известные проблемы

- Версия протокола одна (`saap: 1`); внутри неё поля только добавляются, неизвестные поля игнорируются.
- `lua.exec` и `mem.read` у mock — игрушечные (арифметика и `print`; псевдопамять с `MZ` по 0x400000).
- Решения типовых проблем — в [troubleshooting.md](troubleshooting.md).

## Python-API (если другие пакеты его используют)

```python
from satk.saap.client import connect
with connect("mock") as c:
    pose = c.call("camera.get")["pose"]
```
