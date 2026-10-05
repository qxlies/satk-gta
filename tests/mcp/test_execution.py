"""Ownership of operations and real worker process trees across timeout/cancellation."""

from __future__ import annotations

import ctypes
import importlib.util
import os
import subprocess
import threading
import time
from ctypes import wintypes
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import anyio
import pytest

pytest.importorskip("mcp")

from satk.core import registry as R
from satk.mcp.server import SatkMcp


@pytest.mark.parametrize("same_group", [True, False])
def test_timed_out_threads_keep_group_and_capacity(isolated_ops, same_group):
    active = maximum = completed = 0
    mutex = threading.Lock()

    @R.op("zz.owned_thread", summary="Test thread ownership.", summary_ru="Тест потоков.")
    def operation() -> dict:
        nonlocal active, maximum, completed
        with mutex:
            active += 1
            maximum = max(maximum, active)
        try:
            time.sleep(0.2)
            return {}
        finally:
            with mutex:
                active -= 1
                completed += 1

    async def main():
        app = SatkMcp(groups=None, call_timeout=0.01)
        spec = R.get_op("zz.owned_thread")
        submitted = 0
        try:
            for i in range(8):
                group = "same" if same_group else f"group{i}"
                submitted += 1
                result = await app.run(replace(spec, mcp_group_explicit=group), {})
                assert result["error"]["code"] == "TIMEOUT"
        finally:
            # Drain both running and capacity-queued executions before ending the event loop.
            with anyio.fail_after(5):
                while completed < submitted:
                    await anyio.sleep(0.01)

    anyio.run(main)
    assert active == 0
    assert maximum <= (1 if same_group else 4)


def test_timed_out_thread_keeps_group_until_it_finishes(isolated_ops):
    release = threading.Event()
    first_finished = threading.Event()
    second_started = threading.Event()

    @R.op("zz.group_timeout", summary="Test timeout serialization.", summary_ru="Тест сериализации.")
    def operation(block: bool = False) -> dict:
        if block:
            try:
                assert release.wait(3), "test did not release the blocking operation"
            finally:
                first_finished.set()
        else:
            second_started.set()
            assert first_finished.is_set(), "same-group operations overlapped"
        return {"blocked": block}

    async def main():
        app = SatkMcp(groups=None, call_timeout=0.05)
        spec = R.get_op("zz.group_timeout")
        second_done = anyio.Event()
        results = []

        async def second():
            results.append(await app.run(spec, {}))
            second_done.set()

        try:
            result = await app.run(spec, {"block": True})
            assert result["error"]["code"] == "TIMEOUT"
            assert not first_finished.is_set()
            async with anyio.create_task_group() as group:
                group.start_soon(second)
                try:
                    # Waiting for the group does not consume the next call's execution deadline.
                    await anyio.sleep(0.15)
                    assert not second_started.is_set() and not second_done.is_set()
                finally:
                    release.set()
                with anyio.fail_after(5):
                    await second_done.wait()
            assert results == [{"ok": True, "blocked": False}]
        finally:
            release.set()
            with anyio.fail_after(5):
                while not first_finished.is_set():
                    await anyio.sleep(0.01)

    anyio.run(main)


def test_fast_thread_returns_result_and_progress(isolated_ops):
    @R.op("zz.fast_result", summary="Test fast thread result.", summary_ru="Тест результата.")
    def operation(value: int) -> dict:
        R.report_progress(1, 1, "done")
        return {"value": value * 2}

    async def main():
        seen = []

        async def progress(*args):
            seen.append(args)

        ctx = SimpleNamespace(session=SimpleNamespace(report_progress=progress))
        app = SatkMcp(groups=None, call_timeout=1)
        result = await app.run(R.get_op("zz.fast_result"), {"value": 7}, ctx)
        assert result == {"ok": True, "value": 14}
        assert seen == [(1, 1, "done")]

    anyio.run(main)


def test_cancelled_thread_retains_group_until_execution_ends(isolated_ops):
    active = maximum = 0
    started = threading.Event()
    mutex = threading.Lock()

    @R.op("zz.cancel_thread", summary="Test cancellation ownership.", summary_ru="Тест отмены.")
    def operation() -> dict:
        nonlocal active, maximum
        with mutex:
            active += 1
            maximum = max(maximum, active)
        started.set()
        try:
            time.sleep(0.2)
            return {}
        finally:
            with mutex:
                active -= 1

    async def main():
        app = SatkMcp(groups=None)
        spec = R.get_op("zz.cancel_thread")

        async def first(*, task_status=anyio.TASK_STATUS_IGNORED):
            with anyio.CancelScope() as scope:
                task_status.started(scope)
                await app.run(spec, {})

        try:
            async with anyio.create_task_group() as group:
                scope = await group.start(first)
                with anyio.fail_after(5):
                    while not started.is_set():
                        await anyio.sleep(0.001)
                scope.cancel()
                group.start_soon(app.run, spec, {})
        finally:
            await anyio.sleep(0.25)

    anyio.run(main)
    assert active == 0 and maximum == 1


class _PipeProcess:
    """Real Popen with async pipe reads, avoiding sandbox-denied asyncio named pipes.

    Only the transport is substituted; SatkMcp still starts its actual worker, sends its
    real request, applies its timeout and owns/terminates actual Windows descendants.
    """

    def __init__(self, command, **kwargs):
        self.proc = subprocess.Popen(command, bufsize=0, **kwargs)
        self.pid = self.proc.pid
        self.stdin = self.Input(self.proc.stdin)
        self.stdout = self.Output(self.proc.stdout)

    @property
    def returncode(self):
        return self.proc.poll()

    def kill(self):
        self.proc.kill()

    async def wait(self):
        return await anyio.to_thread.run_sync(self.proc.wait)

    async def aclose(self):
        await self.wait()
        self.proc.stdin.close()
        self.proc.stdout.close()

    class Input:
        def __init__(self, file):
            self.file = file

        async def send(self, data):
            self.file.write(data)

        async def aclose(self):
            self.file.close()

    class Output:
        def __init__(self, file):
            self.file = file

        def __aiter__(self):
            return self

        async def __anext__(self):
            data = await anyio.to_thread.run_sync(self.file.read, 65536, abandon_on_cancel=True)
            if not data:
                raise StopAsyncIteration
            return data


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Objects")
# Normal completion releases the tree on purpose (view.start leaves Ariane running), so only
# timeout and cancel must kill descendants.
@pytest.mark.parametrize("ending", ["timeout", "cancel"])
def test_worker_child_and_grandchild_die_with_request(isolated_ops, satk_home, tmp_path, monkeypatch, ending):
    path = Path(__file__).with_name("fixture_ops.py")
    fixture = importlib.util.spec_from_file_location("satk_tree_fixture", path)
    fixture.loader.exec_module(importlib.util.module_from_spec(fixture))
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handles = {}

    def open_handles():
        for role in ("worker", "child", "grandchild"):
            pidfile = tmp_path / f"{role}.pid"
            if role not in handles and pidfile.exists():
                pid = int(pidfile.read_text(encoding="ascii"))
                handle = kernel.OpenProcess(0x100001, False, pid)  # SYNCHRONIZE | TERMINATE
                if handle:
                    handles[role] = handle

    async def open_process(command, **kwargs):
        return _PipeProcess(command, **kwargs)

    monkeypatch.setattr(anyio, "open_process", open_process)

    async def main():
        app = SatkMcp(groups=None, long_timeout=2)
        with anyio.CancelScope() as scope:
            async def progress(*args):
                open_handles()
                assert set(handles) == {"worker", "child", "grandchild"}
                if ending == "cancel":
                    scope.cancel()

            ctx = SimpleNamespace(session=SimpleNamespace(report_progress=progress))
            result = await app.run(R.get_op("zz.process_tree"),
                                   {"directory": str(tmp_path), "finish": ending == "worker-exit"}, ctx)
            if ending == "worker-exit":
                assert result["ok"] is True
            else:
                assert ending == "timeout" and result["error"]["code"] == "TIMEOUT"
        if ending == "cancel":
            assert scope.cancelled_caught

    try:
        anyio.run(main)
        assert set(handles) == {"worker", "child", "grandchild"}, "test tree never became ready"
        stopped = {role: kernel.WaitForSingleObject(handle, 2000) == 0 for role, handle in handles.items()}
        assert stopped == {"worker": True, "child": True, "grandchild": True}
    finally:
        # Even the unfixed implementation must leave no test children behind.
        open_handles()
        for handle in handles.values():
            if kernel.WaitForSingleObject(handle, 0) != 0:
                kernel.TerminateProcess(handle, 1)
                kernel.WaitForSingleObject(handle, 5000)
            kernel.CloseHandle(handle)
