"""``satk mcp selftest``: a real stdio session against ``python -m satk.mcp`` (SPEC §5.3 WP-06 acc. 1, 7).

Plain JSON-RPC over pipes (stdlib only, independent of the SDK client):
``initialize`` -> ``notifications/initialized`` -> ``tools/list`` -> ``tools/call satk_help`` ->
``tools/call satk_status`` -> the generic tools (``satk_ops("version")``, ``satk_op("version")``, a refused
``satk_op("dev.gate")``) -> an unknown tool (error path) -> close stdin -> exit code.

Checked: server name/instructions, the tool set equals the registry (in this process, same
``SATK_MCP_GROUPS``) and is not empty, ``tools/list`` size <= 16 384 bytes (bytes per group are
reported; a group above its ``GROUP_BUDGET`` share is named in ``warn``), results are envelopes,
and **every line on stdout is a JSON-RPC frame** (logs must go to stderr / ``work/logs/mcp.log``).
An unknown name in ``SATK_MCP_GROUPS``/``--groups`` is ``BAD_PARAMS`` before the server starts.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from typing import Any

from satk import __version__
from satk.core.config import SRC_ROOT
from satk.core.errors import SatkError

from . import adapter

__all__ = ["run", "PROTOCOL_VERSION"]

#: Protocol version offered in ``initialize`` (the server answers with the one it speaks).
PROTOCOL_VERSION = "2025-11-25"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class _Session:
    def __init__(self, cmd: list[str], env: dict[str, str], cwd: str):
        self.t0 = time.perf_counter()
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     env=env, cwd=cwd, creationflags=_NO_WINDOW)
        self.lines: queue.Queue[bytes | None] = queue.Queue()
        self.frames: list[bytes] = []
        self.stderr: list[str] = []
        threading.Thread(target=self._pump_out, daemon=True).start()
        threading.Thread(target=self._pump_err, daemon=True).start()

    def _pump_out(self) -> None:
        assert self.proc.stdout is not None
        for line in iter(self.proc.stdout.readline, b""):
            self.frames.append(line)
            self.lines.put(line)
        self.lines.put(None)

    def _pump_err(self) -> None:
        assert self.proc.stderr is not None
        for line in iter(self.proc.stderr.readline, b""):
            self.stderr.append(line.decode("utf-8", "replace").rstrip())
            if len(self.stderr) > 200:
                del self.stderr[:100]

    def send(self, msg: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write((json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def request(self, rid: int, method: str, params: dict | None, deadline: float) -> tuple[dict, bytes]:
        msg: dict[str, Any] = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        self.send(msg)
        while True:
            left = deadline - time.perf_counter()
            if left <= 0:
                raise TimeoutError(f"no answer to {method} in time")
            try:
                raw = self.lines.get(timeout=left)
            except queue.Empty:
                raise TimeoutError(f"no answer to {method} in time") from None
            if raw is None:
                raise EOFError(f"server closed stdout while waiting for {method}")
            try:
                frame = json.loads(raw)
            except ValueError:
                continue  # counted by the purity check
            if isinstance(frame, dict) and frame.get("id") == rid:
                return frame, raw

    def close(self, wait: float = 10.0) -> int | None:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except OSError:
            pass
        try:
            return self.proc.wait(timeout=wait)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)
            return None


def _tool_payload(frame: dict) -> tuple[bool, dict | None]:
    res = frame.get("result") or {}
    texts = [c.get("text") for c in res.get("content") or [] if c.get("type") == "text"]
    try:
        env = json.loads(texts[0]) if texts else None
    except ValueError:
        env = None
    return bool(res.get("isError")), env


def run(groups: str | None = None, timeout: float = 60.0, protocol: str = PROTOCOL_VERSION) -> dict:
    """Run the self-test; returns the summary or raises ``SatkError`` with per-check details.

    ``protocol`` is the version offered in ``initialize`` (any handshake-era version the SDK speaks).
    """
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONPATH"] = str(SRC_ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    if groups is not None:
        env["SATK_MCP_GROUPS"] = groups
    sel = adapter.resolve_groups(env.get("SATK_MCP_GROUPS"))  # BAD_PARAMS for a typo, before any process starts
    expected = [adapter.tool_name(o) for o in adapter.mcp_ops(sel)]
    checks: list[dict] = []

    def check(name: str, ok: bool, detail: Any = None) -> bool:
        checks.append({"name": name, "ok": bool(ok), **({"detail": detail} if detail not in (None, "") else {})})
        return ok

    cmd = [sys.executable, "-X", "utf8", "-m", "satk.mcp"]
    s = _Session(cmd, env, str(SRC_ROOT.parent))
    deadline = time.perf_counter() + timeout
    out: dict[str, Any] = {"server": None, "protocol": None, "tools": None, "list_bytes": None,
                           "budget": adapter.LIST_BUDGET}
    code: int | None = None
    try:
        init, _ = s.request(1, "initialize", {"protocolVersion": protocol, "capabilities": {},
                                              "clientInfo": {"name": "satk-selftest", "version": __version__}},
                            deadline)
        out["startup_ms"] = round((time.perf_counter() - s.t0) * 1000)
        r = init.get("result") or {}
        info = r.get("serverInfo") or {}
        out["server"] = f"{info.get('name')} {info.get('version')}"
        out["protocol"] = r.get("protocolVersion")
        check("initialize", info.get("name") == adapter.SERVER_NAME and "tools" in (r.get("capabilities") or {})
              and r.get("protocolVersion") == protocol, init.get("error") or r.get("protocolVersion"))
        check("instructions", r.get("instructions") == adapter.INSTRUCTIONS
              and len(adapter.INSTRUCTIONS.encode("utf-8")) <= 1024)
        s.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

        lst, raw = s.request(2, "tools/list", {}, deadline)
        tools = (lst.get("result") or {}).get("tools") or []
        names = [t.get("name") for t in tools]
        out["tools"] = len(tools)
        out["list_bytes"] = len(json.dumps(lst.get("result") or {}, ensure_ascii=False,
                                           separators=(",", ":")).encode("utf-8"))
        out["wire_bytes"] = len(raw.rstrip(b"\r\n"))
        check("tools_match_registry", names == expected,
              None if names == expected else {"server": names, "registry": expected})
        check("tools_nonempty", bool(tools), None if tools else "the server lists no tools")
        out["groups"] = adapter.group_bytes(tools)
        over = adapter.over_share(out["groups"])
        check("list_budget", out["list_bytes"] <= adapter.LIST_BUDGET,
              f"{out['list_bytes']} bytes" + (f"; groups over their share: {'; '.join(over)}" if over else ""))
        if over:
            out["warn"] = [f"MCP_BUDGET: group {x} (share in satk.mcp.adapter.GROUP_BUDGET)" for x in over]
        schemas_ok = all(t.get("inputSchema", {}).get("type") == "object" and t.get("description") for t in tools)
        check("tool_schemas", schemas_ok)

        calls = []
        rid = 3
        for tool in ("satk_help", "satk_status"):
            if tool not in names:
                check(f"call_{tool}", True, "not in the selected groups (skipped)")
                continue
            t1 = time.perf_counter()
            fr, _ = s.request(rid, "tools/call", {"name": tool, "arguments": {}}, deadline)
            rid += 1
            is_err, payload = _tool_payload(fr)
            ok = not is_err and isinstance(payload, dict) and payload.get("ok") is True
            check(f"call_{tool}", ok, None if ok else (payload or fr.get("error")))
            calls.append({"tool": tool, "ms": round((time.perf_counter() - t1) * 1000)})
        if "satk_op" in names and "satk_ops" in names:  # generic access (M2-01)
            generic = [
                ("satk_ops", {"query": "version"},
                 lambda err, env: not err and (env.get("rows") or [[None]])[0][0] == "version"),
                ("satk_op", {"op": "version", "args": {}},
                 lambda err, env: not err and env.get("ok") is True and "satk" in env),
                ("satk_op", {"op": "dev gate"},
                 lambda err, env: err and (env.get("error") or {}).get("code") == "UNSUPPORTED"),
            ]
            for tool, arguments, good in generic:
                t1 = time.perf_counter()
                fr, _ = s.request(rid, "tools/call", {"name": tool, "arguments": arguments}, deadline)
                rid += 1
                is_err, payload = _tool_payload(fr)
                ok = isinstance(payload, dict) and bool(good(is_err, payload))
                label = f"call_{tool}_" + str(arguments.get("op") or arguments.get("query")).replace(" ", "_")
                check(label, ok, None if ok else (payload or fr.get("error")))
                calls.append({"tool": tool, "args": arguments, "ms": round((time.perf_counter() - t1) * 1000)})
        out["calls"] = calls
        fr, _ = s.request(rid, "tools/call", {"name": "no_such_tool_x", "arguments": {}}, deadline)
        is_err, payload = _tool_payload(fr)
        check("unknown_tool_error", is_err and (payload or {}).get("error", {}).get("code") == "UNKNOWN_METHOD",
              payload)
    except (TimeoutError, EOFError, OSError) as e:
        check("session", False, f"{type(e).__name__}: {e}")
    finally:
        code = s.close()
    check("exit_code", code == 0, code)
    stray = []
    for raw_line in s.frames:
        try:
            fr = json.loads(raw_line)
            if not (isinstance(fr, dict) and fr.get("jsonrpc") == "2.0"):
                stray.append(raw_line[:120].decode("utf-8", "replace"))
        except ValueError:
            stray.append(raw_line[:120].decode("utf-8", "replace"))
    out["stdout_frames"] = len(s.frames)
    check("stdout_only_jsonrpc", not stray, stray[:5] or None)
    out["elapsed_ms"] = round((time.perf_counter() - s.t0) * 1000)
    failed = [c for c in checks if not c["ok"]]
    if failed:
        tail = s.stderr[-15:]
        dep = any("No module named 'mcp'" in ln or "DEPENDENCY" in ln for ln in s.stderr)
        raise SatkError("DEPENDENCY" if dep else "INTERNAL",
                        f"mcp selftest failed: {', '.join(c['name'] for c in failed)}",
                        hint="see stderr_tail and work/logs/mcp.log",
                        data={"checks": checks, "stderr_tail": tail, **{k: v for k, v in out.items() if v is not None}})
    out["checks"] = len(checks)
    return out
