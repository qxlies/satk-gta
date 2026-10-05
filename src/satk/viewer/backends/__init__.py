"""Viewer backends: one object per target that speaks SAAP/1 semantics (SPEC §4.8.7).

* ``saap_native`` — any SAAP endpoint (mock server, native Ariane WP-14, MTA WP-13/16);
* ``ariane_legacy`` — the ``ARIANE_IPC/1`` adapter (Ariane fork with P0 patches, WP-08);
* ``mock`` — the mock world in-process (no server; state lives as long as this process).

:func:`get_backend` picks one from the discovery files (``work/run/endpoints/<role>.json``).
Every backend has ``call(method, params) -> result`` (raises ``SatkError`` with SAAP codes),
``hello`` (dict), ``caps``, ``target``, ``role``, ``proto``, ``impl``, ``warnings`` and ``close()``.
"""

from __future__ import annotations

from typing import Any

from ...core.errors import SatkError

__all__ = ["Backend", "TARGET_ROLES", "get_backend", "role_of"]

#: viewer target -> endpoint role
TARGET_ROLES = {"ariane": "ariane", "game": "game", "mock": "mock", "client2": "client2", "server": "server",
                "blender": "blender"}


def role_of(target: str) -> str:
    try:
        return TARGET_ROLES[target]
    except KeyError:
        raise SatkError("BAD_PARAMS", f"unknown target {target!r}", did_you_mean=list(TARGET_ROLES)) from None


class Backend:
    """Base class (documentation of the interface; subclasses implement :meth:`call`)."""

    target: str = ""
    role: str = ""
    proto: str = ""
    impl: str = ""

    def __init__(self) -> None:
        self.warnings: list[str] = []
        self._hello: dict | None = None

    @property
    def hello(self) -> dict:
        if self._hello is None:
            self._hello = self.call("hello", {"token": "0" * 64, "client": {"name": "satk"}})
        return self._hello

    @property
    def caps(self) -> list[str]:
        return list(self.hello.get("caps") or [])

    def has(self, cap: str) -> bool:
        return cap in self.caps

    def require(self, cap: str, what: str = "") -> None:
        if cap not in self.caps:
            raise SatkError("UNSUPPORTED", f"target {self.target!r} has no capability {cap!r}"
                            + (f" (needed for {what})" if what else ""), data={"capability": cap, "target": self.target})

    @property
    def viewport(self) -> tuple[int, int]:
        vp = self.hello.get("viewport") or {}
        return int(vp.get("w", 0) or 0), int(vp.get("h", 0) or 0)

    @property
    def build_id(self) -> str | None:
        return (self.hello.get("build") or {}).get("id")

    def call(self, method: str, params: dict | None = None) -> dict:  # pragma: no cover - abstract
        raise NotImplementedError

    def close(self) -> None:
        pass

    def __enter__(self) -> "Backend":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def get_backend(target: str, *, allow_inprocess_mock: bool = True) -> Any:
    """Backend for ``target`` from the discovery files.

    ``mock`` without a running mock server falls back to the in-process mock world (with a
    warning). Other targets raise ``NOT_READY`` with a ``satk view start`` hint.
    """
    from ...saap import client as C

    role = role_of(target)
    ep = C.read_endpoint(role)
    if ep is not None and C.endpoint_alive(ep, C.read_session(role)):  # a reused pid is not the endpoint
        proto = ep.get("protocol")
        if proto == "saap/1":
            from .saap_native import SaapNativeBackend

            return SaapNativeBackend(target, role)
        if proto == "ariane-ipc/1":
            from .ariane_legacy import ArianeLegacyBackend

            return ArianeLegacyBackend.from_discovery(target, role)
        if proto == "mta-lua/1":  # the game through the satk-agent Lua resource (M2-14)
            from .mta_lua import MtaLuaBackend

            return MtaLuaBackend.from_discovery(target, role)
        raise SatkError("UNSUPPORTED", f"endpoint {role!r} speaks unknown protocol {proto!r}")
    if target == "mock" and allow_inprocess_mock:
        from .mock import InProcessMockBackend

        b = InProcessMockBackend()
        b.warnings.append("mock endpoint not running: using the in-process mock (state lives only in this process)")
        return b
    hint = f"satk view start --target {target}" if target in ("ariane", "mock") else "satk view status"
    raise SatkError("NOT_READY", f"target {target!r} is not running", hint=hint, data={"target": target})
