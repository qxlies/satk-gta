"""The satk MCP server: stdio, ``mcp==2.3.0`` (SPEC §4.7). All MCP SDK code lives in this module (R7).

Spike S3 (WP-06): the SDK's high-level ``MCPServer`` derives tool schemas from Python signatures
through pydantic, which would break CLI/MCP schema parity and inflate ``tools/list``; the
low-level ``mcp.server.lowlevel.Server`` of the same package takes our registry schemas verbatim,
so it is used here (same transport, same wire protocol).

* Tools = every registered ``@op`` with an MCP name (``satk.mcp.adapter``), filtered by
  ``SATK_MCP_GROUPS``; computed at start-up, so later-merged packages appear automatically.
* Generic access (M2-01, ``satk.mcp.generic``): ``satk_ops`` searches every operation, ``satk_op``
  resolves one, checks the agent policy and its arguments and then dispatches it exactly like its
  own tool would run (thread under its group lock, or the worker subprocess when ``long_running``).
* Ordinary operations run in worker threads (serialized per MCP group, so one viewer connection
  never sees two requests at once), timeout :data:`CALL_TIMEOUT`; ``long_running`` operations run
  in a subprocess (``satk.mcp.worker``) with progress notifications and :data:`LONG_TIMEOUT`.
* Results: one text block with the compact JSON envelope; ``isError`` for ``ok:false``; images as
  image content only when the call passed ``inline=true`` (<= 1024 px).
* stdout carries JSON-RPC only: the transport diverts fd 1 to stderr while serving and
  ``sys.stdout`` is redirected to stderr as well; logs go to stderr (WARNING+) and
  ``work/logs/mcp.log`` (INFO).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
import sys
import time
from typing import Any

import anyio
import mcp_types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from satk import __version__
from satk.core import envelope
from satk.core.config import SRC_ROOT
from satk.core.errors import SatkError
from satk.core.log import add_file, get_logger, record_exception, setup
from satk.core.paths import jpath
from satk.core.registry import OpSpec, import_errors, invoke, progress_handler

from . import adapter, generic

__all__ = ["SatkMcp", "serve", "CALL_TIMEOUT", "LONG_TIMEOUT", "MAX_INLINE_IMAGES"]

log = get_logger("mcp")

#: Response deadline for ordinary operations. Timed-out threads keep their capacity and
#: group lock in a server-owned background task until execution actually ends.
CALL_TIMEOUT = float(os.environ.get("SATK_MCP_TIMEOUT", "120"))
#: Seconds a ``long_running`` operation (subprocess) may take (SPEC §3.1: 600 s).
LONG_TIMEOUT = float(os.environ.get("SATK_MCP_LONG_TIMEOUT", "600"))
MAX_INLINE_IMAGES = 4
_THREADS = 4


def _err(code: str, msg: str, **kw: Any) -> dict:
    return SatkError(code, msg, **kw).to_dict()


class SatkMcp:
    """Registry-driven MCP server (one instance per connection/process)."""

    def __init__(self, groups: set[str] | None | str = "env", *, call_timeout: float | None = None,
                 long_timeout: float | None = None):
        if groups == "env":
            groups = adapter.resolve_groups(os.environ.get("SATK_MCP_GROUPS"))  # BAD_PARAMS on a typo
        self.groups: set[str] | None = groups  # type: ignore[assignment]
        self.call_timeout = CALL_TIMEOUT if call_timeout is None else call_timeout
        self.long_timeout = LONG_TIMEOUT if long_timeout is None else long_timeout
        ops = adapter.mcp_ops(self.groups)
        self.ops: dict[str, OpSpec] = {n: o for o in ops if (n := adapter.tool_name(o))}
        self.tools = [types.Tool(name=adapter.tool_name(o), description=o.description, input_schema=o.input_schema())
                      for o in ops]
        self._locks: dict[str, anyio.Lock] = {}
        self._limiter: anyio.CapacityLimiter | None = None
        self._thread_tasks: set[asyncio.Task[dict]] = set()
        self.server: Server = Server(adapter.SERVER_NAME, version=__version__, instructions=adapter.INSTRUCTIONS,
                                     on_list_tools=self._on_list_tools, on_call_tool=self._on_call_tool)
        errs = import_errors()
        if errs:
            log.warning("ops modules failed to import (their tools are missing): %s", errs)
        log.info("satk mcp %s: %d tools (groups=%s): %s", __version__, len(self.tools),
                 ",".join(sorted(self.groups)) if self.groups else "all", ", ".join(self.ops))

    # ------------------------------------------------------------------ handlers

    async def _on_list_tools(self, ctx: Any, params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=self.tools)

    async def _on_call_tool(self, ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        name = params.name
        args = dict(params.arguments or {})
        t0 = time.perf_counter()
        spec = self.ops.get(name)
        target: OpSpec | None = None  # the operation satk_op dispatched to
        via = ""  # log: " -> <op>" for satk_op calls
        if spec is None:
            import difflib

            env = _err("UNKNOWN_METHOD", f"no tool {name!r}",
                       did_you_mean=difflib.get_close_matches(name, list(self.ops), n=3, cutoff=0.5))
        else:
            try:
                if spec.name == generic.RUN_OP:
                    via = f" -> {args.get('op')}"
                    target, args = self._generic_target(spec, args)
                    via = f" -> {target.name}"
                    env = await self.run(target, args, ctx)
                else:
                    with generic.serving(self.groups):  # satk_ops sees this server's groups
                        env = await self.run(spec, args, ctx)
            except SatkError as e:  # satk_op: unknown/refused operation or bad arguments
                env = e.to_dict()
                if isinstance(e.data, dict) and e.data.get("op"):
                    via = f" -> {e.data['op']}"
            except Exception as e:  # noqa: BLE001 - never let a tool crash the connection
                where = record_exception(e, context=f"mcp tool {name}")
                env = _err("INTERNAL", f"{type(e).__name__}: {e}",
                           hint=f"traceback in {jpath(where)}" if where else None)
        content: list[Any] = [types.TextContent(text=adapter.result_text(env))]
        ok = bool(env.get("ok", True))
        if ok and adapter.wants_inline(target or spec, args):
            content.extend(await self._images(env))
        ms = (time.perf_counter() - t0) * 1000
        log.info("call %s%s %s %.0f ms", name, via, "ok" if ok else (env.get("error") or {}).get("code"), ms)
        return types.CallToolResult(content=content, is_error=not ok)

    def _generic_target(self, spec: OpSpec, args: dict) -> tuple[OpSpec, dict]:
        """``satk_op(op, args)`` -> (operation, its arguments); policy and validation errors raise."""
        own = spec.bind(args)  # the tool's own schema: op (string, required), args (object)
        return generic.prepare(own["op"], own.get("args"), self.groups)

    # ------------------------------------------------------------------ execution

    def _lock(self, group: str) -> anyio.Lock:
        lk = self._locks.get(group)
        if lk is None:
            lk = self._locks[group] = anyio.Lock()
        return lk

    async def run(self, spec: OpSpec, args: dict, ctx: Any = None) -> dict:
        """Run one operation and return its envelope (never raises ``SatkError``)."""
        if spec.long_running:
            return await self._run_subprocess(spec, args, ctx)
        if self._limiter is None:
            self._limiter = anyio.CapacityLimiter(_THREADS)
        session = getattr(ctx, "session", None)
        label = spec.mcp_name or spec.name

        def call() -> dict:
            def progress(done: float, total: float | None, msg: str | None) -> None:
                if session is not None:
                    anyio.from_thread.run(session.report_progress, done, total, msg)

            try:
                with progress_handler(progress):
                    return invoke(spec, args)
            except SystemExit as e:  # a CLI-style exit must not end the server (it is a BaseException)
                return _err("INTERNAL", f"{label} tried to exit the process (code {e.code})",
                            hint=f"CLI only: run `satk {spec.cli}` in a terminal")

        ready = anyio.Event()

        async def execute() -> dict:
            async with self._lock(spec.mcp_group):
                ready.set()
                return await anyio.to_thread.run_sync(call, abandon_on_cancel=False, limiter=self._limiter)

        def finished(task: asyncio.Task[dict]) -> None:
            self._thread_tasks.discard(task)
            ready.set()  # also wake the request if lock acquisition failed
            if not task.cancelled():
                error = task.exception()  # retrieve failures even after the request has left
                if error is not None:
                    log.error("tool %s thread failed", label,
                              exc_info=(type(error), error, error.__traceback__))

        # The asyncio server owns this task independently of the request's cancel scope.
        # Keeping a strong reference also prevents a timed-out execution being collected.
        task = asyncio.create_task(execute(), name=f"satk:{label}")
        self._thread_tasks.add(task)
        task.add_done_callback(finished)
        try:
            # As before, time queued behind another call in the group is not charged
            # to this call's execution deadline.
            await ready.wait()
            with anyio.move_on_after(self.call_timeout):
                return await asyncio.shield(task)
            # Prefer a result already available when the deadline and completion race.
            if task.done():
                return task.result()
            log.warning("tool %s exceeded %g s (thread continues)", label, self.call_timeout)
            return _err("TIMEOUT", f"{label} did not finish in {self.call_timeout:g} s",
                        hint="retry with a smaller request (limit/size/span)")
        finally:
            # A request cancelled while queued for its group has no running thread.
            # Once ready, only execute() releases the group and capacity on completion.
            if not ready.is_set():
                task.cancel()

    async def _run_subprocess(self, spec: OpSpec, args: dict, ctx: Any) -> dict:
        """Run a ``long_running`` op in ``satk.mcp.worker``; a timeout or cancel kills its whole tree.

        The worker and everything it starts (Blender, MSBuild, ...) live in one
        :class:`~satk.mcp.proctree.ProcessTree`: the worker announces its pid first and gets its
        request only after it has been contained, so no grandchild can escape the kill.
        """
        import inspect

        from .proctree import ProcessTree

        try:
            file = inspect.getfile(spec.fn)
        except (TypeError, OSError):
            file = None
        req = {"op": spec.name, "args": args, "module": spec.module, "file": file}
        req_line = (json.dumps(req, ensure_ascii=False, default=str) + "\n").encode("utf-8")
        env_vars = dict(os.environ)
        env_vars["PYTHONUTF8"] = "1"
        env_vars["PYTHONPATH"] = str(SRC_ROOT) + (os.pathsep + env_vars["PYTHONPATH"]
                                                  if env_vars.get("PYTHONPATH") else "")
        cmd = [sys.executable, "-X", "utf8", "-m", "satk.mcp.worker"]
        session = getattr(ctx, "session", None)
        result: dict | None = None
        t0 = time.perf_counter()
        try:
            tree: ProcessTree | None = ProcessTree()
        except OSError as e:  # pragma: no cover - job objects are available on every supported Windows
            log.warning("worker %s: no process-tree containment (%s); falling back to taskkill /T", spec.name, e)
            tree = None
        proc = None
        finished = False
        try:
            with anyio.CancelScope(shield=True):  # never lose the process between its start and containment
                proc = await anyio.open_process(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=None,
                                                env=env_vars, cwd=str(SRC_ROOT.parent), **ProcessTree.popen_kwargs())
                if tree is not None and not tree.add(proc.pid):
                    log.warning("worker %s: launcher pid %s not contained", spec.name, proc.pid)
            buf = b""
            sent = False
            with anyio.move_on_after(self.long_timeout) as scope:
                async for chunk in proc.stdout:
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        if not line.strip():
                            continue
                        try:
                            msg = json.loads(line)
                        except ValueError:
                            log.warning("worker %s: non-JSON line %r", spec.name, line[:200])
                            continue
                        if not isinstance(msg, dict):
                            continue
                        if "hello" in msg and not sent:
                            hello = msg.get("hello")
                            wpid = hello.get("pid") if isinstance(hello, dict) else None
                            if tree is not None and isinstance(wpid, int) and not tree.add(wpid):
                                log.warning("worker %s: pid %s not contained", spec.name, wpid)
                            await proc.stdin.send(req_line)
                            await proc.stdin.aclose()
                            sent = True
                        elif "progress" in msg and session is not None:
                            done, total, text = (list(msg["progress"]) + [None, None, None])[:3]
                            await session.report_progress(done, total, text)
                        elif "envelope" in msg:
                            result = msg["envelope"]
                await proc.wait()
            if scope.cancelled_caught:
                log.warning("worker %s timed out after %.0f s: killing its process tree", spec.name, self.long_timeout)
                return _err("TIMEOUT", f"{spec.mcp_name or spec.name} did not finish in {self.long_timeout:.0f} s",
                            hint=f"run it from a terminal instead: satk {spec.cli} ...")
            finished = True
        finally:
            with anyio.CancelScope(shield=True):
                await self._end_tree(spec, proc, tree, finished)
        log.info("worker %s exit %s in %.1f s", spec.name, proc.returncode, time.perf_counter() - t0)
        if result is None:
            return _err("INTERNAL", f"worker for {spec.name} exited with code {proc.returncode} without a result",
                        hint="see work/logs/mcp.log and work/logs/errors.log")
        return result

    async def _end_tree(self, spec: OpSpec, proc: Any, tree: Any, finished: bool) -> None:
        """Normal end: release the tree. Timeout/cancel/error: kill every process in it, then reap."""
        killed = False
        if tree is not None:
            try:
                if finished:
                    tree.release()
                else:
                    tree.kill()
                    killed = True
                    tree.close()
            except OSError as e:
                log.warning("worker %s: process tree cleanup failed: %s", spec.name, e)
        if not finished and not killed and proc is not None and os.name == "nt" and proc.returncode is None:
            def taskkill() -> None:  # fallback while the launcher -> worker -> tool chain is still intact
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=30,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

            with contextlib.suppress(Exception):
                await anyio.to_thread.run_sync(taskkill)
        if proc is not None:
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError, OSError):
                    proc.kill()
            with contextlib.suppress(Exception):
                await proc.aclose()  # closes the pipes and reaps the process

    async def _images(self, env: dict) -> list[Any]:
        from .inline import encode_image

        out: list[Any] = []
        for p in adapter.image_candidates(env, MAX_INLINE_IMAGES):
            try:
                b64, mime, _ = await anyio.to_thread.run_sync(encode_image, p)
                out.append(types.ImageContent(data=b64, mime_type=mime))
            except Exception as e:  # noqa: BLE001 - the JSON result is already there
                out.append(types.TextContent(text=f"inline image skipped ({p.name}): {e}"))
        return out


def serve(groups: str | None = None) -> int:
    """Run the server on stdin/stdout until the client closes the connection.

    Returns 2 without touching stdout when ``SATK_MCP_GROUPS``/``groups`` names an unknown group
    (the error goes to stderr and ``work/logs/mcp.log``; the MCP host shows the server as failed).
    """
    if groups is not None:
        os.environ["SATK_MCP_GROUPS"] = groups
    setup()
    try:
        add_file("mcp")
    except Exception:  # noqa: BLE001 - logging to file is best effort
        pass
    try:
        adapter.resolve_groups(os.environ.get("SATK_MCP_GROUPS"))
    except SatkError as e:
        log.error("satk mcp not started: %s (did you mean: %s)", e.msg, ", ".join(e.did_you_mean) or "-")
        sys.stderr.write(envelope.dumps(e.to_dict()) + "\n")
        sys.stderr.flush()
        return 2

    async def main() -> None:
        sys.stdout.flush()
        async with stdio_server() as (read_stream, write_stream):
            # fd 1 now points at stderr; make Python-level prints go there too (and never buffer
            # into the real stdout that is restored after the session).
            with contextlib.redirect_stdout(sys.stderr):
                app = SatkMcp()
                await app.server.run(read_stream, write_stream, app.server.create_initialization_options())

    try:
        anyio.run(main)
    except KeyboardInterrupt:
        return 130
    log.info("satk mcp: connection closed")
    return 0
