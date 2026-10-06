"""satk.ingame.logs: the line formats of the MTA server and client logs, filters, 'since start'."""

from __future__ import annotations

import pytest

from satk.ingame import logs as L


@pytest.mark.parametrize("src,line,want", [
    ("server", "[2026-10-05 22:32:01] Starting satk-testdrive-mod",
     ["2026-10-05 22:32:01", "server", "info", "Starting satk-testdrive-mod", ""]),
    ("server", "[2026-10-05 22:32:01] WARNING: File 'm/a.dff' in resource 'x' contains errors",
     ["2026-10-05 22:32:01", "server", "warn", "File 'm/a.dff' in resource 'x' contains errors", ""]),
    ("scripts", "[2026-10-05 22:32:01] ERROR: satk-testdrive\\server.lua:12: attempt to index nil",
     ["2026-10-05 22:32:01", "scripts", "error", "attempt to index nil", "satk-testdrive\\server.lua:12"]),
    ("scripts", "[2026-10-05 22:32:01] INFO: satk-testdrive: content abc loaded (1 models)",
     ["2026-10-05 22:32:01", "scripts", "info", "satk-testdrive: content abc loaded (1 models)", ""]),
    ("client", "[2026-10-05 17:34:13] [Output] : Your car: Premier",
     ["2026-10-05 17:34:13", "client", "info", "Your car: Premier", ""]),
    ("client", "[2026-10-05 17:34:13] WARNING: satk-testdrive/client.lua:40: Bad argument @ 'engineLoadDFF'",
     ["2026-10-05 17:34:13", "client", "warn", "Bad argument @ 'engineLoadDFF'", "satk-testdrive/client.lua:40"]),
    ("client", "18:47:36 - [DEBUG] Connecting to 127.0.0.1:22030 ...",
     ["18:47:36", "client", "debug", "Connecting to 127.0.0.1:22030 ...", ""]),
])
def test_parse_line(src, line, want):
    assert L.parse_line(src, line) == want


def test_blank_lines():
    assert L.parse_line("server", "   \r\n") is None


def test_collect_filters_and_since(tmp_path, monkeypatch):
    server = tmp_path / "satk-agent.log"
    scripts = tmp_path / "satk-agent-scripts.log"
    server.write_text("[2026-10-05 10:00:00] old line\n", encoding="utf-8")
    scripts.write_text("[2026-10-05 10:00:00] WARNING: a.lua:1: early\n", encoding="utf-8")
    monkeypatch.setattr(L, "log_files", lambda: [("server", server), ("scripts", scripts)])
    marks = {"server:satk-agent.log": server.stat().st_size, "scripts:satk-agent-scripts.log": scripts.stat().st_size}
    with open(server, "a", encoding="utf-8") as f:
        f.write("[2026-10-05 10:01:00] Starting satk-testdrive\n[2026-10-05 10:01:01] ERROR: boom\n")
    with open(scripts, "a", encoding="utf-8") as f:
        f.write("[2026-10-05 10:01:00] ERROR: b.lua:2: late\n")
    rows, warn = L.collect(sources={"server", "scripts"}, level="info", since=None, grep=None, limit=50)
    assert [r[3] for r in rows] == ["old line", "Starting satk-testdrive", "boom", "early", "late"] and warn == []
    rows, _ = L.collect(sources={"server", "scripts"}, level="warn", since=marks, grep=None, limit=50)
    assert [(r[1], r[2], r[3]) for r in rows] == [("server", "error", "boom"), ("scripts", "error", "late")]
    rows, _ = L.collect(sources={"scripts"}, level="debug", since=None, grep=r"b\.lua", limit=50)
    assert [r[3] for r in rows] == ["late"]
    rows, _ = L.collect(sources={"server"}, level="debug", since=None, grep=None, limit=1)
    assert [r[3] for r in rows] == ["boom"]


def test_agent_rows_and_a_down_server():
    class B:
        def log_poll(self, since, max_items):
            return {"items": [{"seq": 1, "t": 2.5, "stream": "script", "level": "error", "msg": "x", "file": "c.lua",
                               "line": 3}]}

    class Down:
        def log_poll(self, since, max_items):
            raise RuntimeError("not reachable")

    rows, warn = L.collect(sources={"agent"}, level="info", since=None, grep=None, limit=10, bridge=B())
    assert rows == [["+2.5s", "agent:script", "error", "x", "c.lua:3"]]
    rows, warn = L.collect(sources={"agent"}, level="info", since=None, grep=None, limit=10, bridge=Down())
    assert rows == [] and warn[0].startswith("NOT_READY: no agent log")
