"""In-process mock backend: the SAAP mock world without a server (tests, quick demos, MCP).

The world is a process-wide singleton, so within one long-lived process (the MCP server)
camera moves and bookmarks persist between calls; each CLI invocation starts fresh.
"""

from __future__ import annotations

import threading

from ...saap.mock import MockWorld
from ...saap.server import precheck
from . import Backend

__all__ = ["InProcessMockBackend", "reset_world"]

_lock = threading.RLock()
_world: MockWorld | None = None


def _get_world() -> MockWorld:
    global _world
    with _lock:
        if _world is None:
            _world = MockWorld()
        return _world


def reset_world() -> None:
    """Forget the in-process mock state (tests)."""
    global _world
    with _lock:
        _world = None


class InProcessMockBackend(Backend):
    target = "mock"
    role = "mock"
    proto = "in-process"
    impl = "satk-mock"

    def __init__(self, world: MockWorld | None = None):
        super().__init__()
        self.world = world or _get_world()

    def call(self, method: str, params: dict | None = None) -> dict:
        params = params or {}
        with _lock:
            precheck(method, params, self.world.caps)
            if method == "hello":
                return self.world.handle("hello", params)
            return self.world.handle(method, params)
