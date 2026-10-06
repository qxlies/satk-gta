# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""Run SAAP requests on Blender's main thread.

``bpy`` may only be used from the main thread. The SAAP server reads requests in its connection
threads and queues them (``world.jobs``); the main thread executes them:

* ``-b`` (background): :func:`serve_blocking` blocks the entry script in a loop. ``bpy.app.timers``
  never fire in background mode, so a timer cannot do this there;
* GUI: :func:`start_timer` registers a persistent timer that drains the queue between UI events.

Both stop when the server stops (``quit``), after ``idle_s`` seconds without requests, or when the
owner process (``owner_pid``) is gone.
"""

from __future__ import annotations

import queue
import time

from satk.core.errors import SatkError

__all__ = ["serve_blocking", "start_timer"]

#: How often (s) the idle and owner checks run.
CHECK_S = 1.0
#: After ``quit`` was answered, wait at most this long (s) for the server to stop.
QUIT_GRACE_S = 2.0


def _should_stop(world, server, idle_s: float, owner_pid: int | None) -> str | None:
    if server.stopping.is_set():
        return "quit"
    now = time.monotonic()
    if world.quit_requested:
        world.quit_at = getattr(world, "quit_at", None) or now
        if now - world.quit_at > QUIT_GRACE_S:
            return "quit"
    if idle_s and now - world.last > idle_s:
        return "idle"
    if owner_pid:
        from satk.saap.client import pid_alive

        if not pid_alive(owner_pid):
            return "owner"
    return None


def serve_blocking(world, server, *, idle_s: float = 0.0, owner_pid: int | None = None, poll: float = 0.05) -> str:
    """Execute queued requests until the session ends; returns why (``quit``/``idle``/``owner``)."""
    next_check = 0.0
    while True:
        if server.stopping.is_set():
            return "quit"
        try:
            job = world.jobs.get(timeout=poll)
        except queue.Empty:
            now = time.monotonic()
            if now >= next_check or world.quit_requested:
                next_check = now + CHECK_S
                why = _should_stop(world, server, idle_s, owner_pid)
                if why:
                    return why
            continue
        try:
            world.run_job(job)
        except SatkError as e:  # a late watchdog stop that landed outside the method
            if e.code != "TIMEOUT":
                raise


def start_timer(world, server, *, idle_s: float = 0.0, owner_pid: int | None = None, on_stop=None,
                interval: float = 0.01) -> None:
    """GUI mode: drain the queue from a persistent ``bpy.app.timers`` callback."""
    import bpy

    state = {"next": 0.0}

    def tick():
        for _ in range(16):
            try:
                job = world.jobs.get_nowait()
            except queue.Empty:
                break
            try:
                world.run_job(job)
            except SatkError as e:  # a late watchdog stop that landed outside the method
                if e.code != "TIMEOUT":
                    raise
        now = time.monotonic()
        if now >= state["next"] or server.stopping.is_set():
            state["next"] = now + CHECK_S
            why = _should_stop(world, server, idle_s, owner_pid)
            if why:
                if on_stop is not None:
                    on_stop(why)
                return None
        return interval

    bpy.app.timers.register(tick, first_interval=0.0, persistent=True)
