"""Backend for native SAAP/1 endpoints (mock server, Ariane WP-14, MTA WP-13/16)."""

from __future__ import annotations

from ...saap.client import SaapClient, connect, read_endpoint
from . import Backend

__all__ = ["SaapNativeBackend"]


class SaapNativeBackend(Backend):
    proto = "saap/1"

    def __init__(self, target: str, role: str, client: SaapClient | None = None, *, timeout: float = 120.0):
        super().__init__()
        self.target, self.role = target, role
        self.client = client or connect(role, timeout=timeout)
        self._hello = self.client.hello
        self.impl = str(self._hello.get("impl") or (read_endpoint(role) or {}).get("impl") or "")

    def call(self, method: str, params: dict | None = None) -> dict:
        if method == "hello":
            return self._hello or self.client.hello
        return self.client.call(method, params or {})

    def close(self) -> None:
        self.client.close()
