"""Log rows of the in-game loop: the agent's ring (server + client debug output), server and client files.

Sources:

* ``agent`` -- ``log.poll`` of satk-agent while the server runs: server debug messages (``onDebugMessage``)
  and the client's script output (``onClientDebugMessage``, stream ``script``);
* ``server`` -- ``<work>/mta/server/mods/deathmatch/logs/satk-agent.log`` (the server console);
* ``scripts`` -- ``satk-agent-scripts.log`` next to it (server debugscript: ``ERROR``/``WARNING``/``INFO``);
* ``client`` -- the fork client's ``Bin/MTA/logs``: ``clientscript.log`` (client debugscript), ``console.log``
  and ``logfile.txt`` (the engine log, debug level).

Rows are ``[t, src, level, msg, where]`` (``where`` = ``file:line`` when the message names one), oldest
first. Reads only. Stdlib only.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

__all__ = ["LEVELS", "log_files", "parse_line", "read_rows", "collect"]

LEVELS = {"debug": 0, "info": 1, "warn": 2, "error": 3}
_STAMP = re.compile(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\]\s*(.*)$")
_CLOCK = re.compile(r"^(\d\d:\d\d:\d\d)\s+-\s+(?:\[(\w+)\]\s*)?(.*)$")
_LEVEL = re.compile(r"^(ERROR|WARNING|INFO|DEBUG)\s*:\s*(.*)$", re.I)
_OUTPUT = re.compile(r"^\[(Output|Error|Warning|Info|Debug)\]\s*:\s*(.*)$", re.I)
_WHERE = re.compile(r"^((?:@?[\w.:\-\[\]/\\]+?)\.lua):(\d+):\s*(.*)$")
_MAX_BYTES = 2 << 20


def log_files() -> list[tuple[str, Path]]:
    """``[(src, path)]`` of the log files that exist."""
    import os

    from ..core import paths

    out: list[tuple[str, Path]] = []
    try:
        root = Path(os.path.abspath(paths.cfg().paths.work)) / "mta" / "server"
        logs = root / "mods" / "deathmatch" / "logs"
        out += [("server", logs / "satk-agent.log"), ("scripts", logs / "satk-agent-scripts.log")]
    except Exception:  # noqa: BLE001 - no work dir: no server logs
        pass
    try:
        from ..engine.common import layout

        cl = layout().bin / "MTA" / "logs"
        out += [("client", cl / "clientscript.log"), ("client", cl / "console.log"), ("client", cl / "logfile.txt")]
    except Exception:  # noqa: BLE001
        pass
    return [(s, p) for s, p in out if p.is_file()]


def _norm_level(word: str | None, default: str = "info") -> str:
    w = (word or "").lower()
    if w.startswith("err"):
        return "error"
    if w.startswith("warn"):
        return "warn"
    if w == "debug":
        return "debug"
    if w in ("info", "output"):
        return "info"
    return default


def parse_line(src: str, line: str) -> list | None:
    """One log line -> ``[t, src, level, msg, where]`` (``None`` for blank lines)."""
    s = line.rstrip("\r\n")
    if not s.strip():
        return None
    t = ""
    level = "info"
    m = _STAMP.match(s)
    if m:
        t, s = m.group(1), m.group(2)
    else:
        m = _CLOCK.match(s)
        if m:
            t, s = m.group(1), m.group(3)
            level = _norm_level(m.group(2), "debug")
    m = _OUTPUT.match(s)
    if m:
        level, s = _norm_level(m.group(1)), m.group(2)
    m = _LEVEL.match(s)
    if m:
        level, s = _norm_level(m.group(1)), m.group(2)
    where = ""
    m = _WHERE.match(s)
    if m:
        where, s = f"{m.group(1)}:{m.group(2)}", m.group(3)
    elif src == "server" and re.match(r"^(ERROR|WARNING)\b", s):
        level = _norm_level(s.split()[0])
    return [t, src, level, s.strip(), where]


def read_rows(src: str, path: Path, start: int = 0) -> list[list]:
    """Rows of one file from byte offset ``start`` (the last 2 MiB at most)."""
    try:
        size = path.stat().st_size
        if start > size:
            start = 0
        with open(path, "rb") as f:
            f.seek(max(start, size - _MAX_BYTES))
            data = f.read()
    except OSError:
        return []
    text = data.decode("utf-8", errors="replace")
    rows = []
    for ln in text.splitlines():
        r = parse_line(src, ln)
        if r:
            rows.append(r)
    return rows


def _agent_rows(items: list[dict]) -> list[list]:
    rows = []
    for it in items:
        where = f"{it['file']}:{it['line']}" if it.get("file") and it.get("line") is not None else ""
        stream = str(it.get("stream") or "server")
        rows.append([f"+{float(it.get('t') or 0):.1f}s", "agent:" + stream, _norm_level(it.get("level")),
                     str(it.get("msg") or ""), where])
    return rows


def collect(*, sources: set[str], level: str, since: dict[str, int] | None, grep: str | None, limit: int,
            bridge=None) -> tuple[list[list], list[str]]:
    """Rows of the chosen sources, filtered; the last ``limit`` rows of each source, oldest first."""
    warn: list[str] = []
    rows: list[list] = []
    if "agent" in sources and bridge is not None:
        try:
            rows += _agent_rows((bridge.log_poll(0, 1000) or {}).get("items") or [])
        except Exception as e:  # noqa: BLE001 - the server may be down: files still work
            warn.append(f"NOT_READY: no agent log ({getattr(e, 'msg', e)})")
    for src, path in log_files():
        if src not in sources:
            continue
        start = (since or {}).get(f"{src}:{path.name}", 0)
        rows += read_rows(src, path, start)
    pat = re.compile(grep, re.I) if grep else None
    floor = LEVELS.get(level, 1)
    out = [r for r in rows if LEVELS.get(r[2], 1) >= floor and (not pat or pat.search(r[3]) or pat.search(r[4]))]
    if len(out) > limit:
        out = out[-limit:]
    return out, warn


def summary(rows: list[list]) -> dict[str, Any]:
    n: dict[str, int] = {}
    for r in rows:
        n[r[2]] = n.get(r[2], 0) + 1
    return n
