"""SAAP/1 constants shared by the client, server, mock, adapters and conformance (SPEC §4.8)."""

from __future__ import annotations

__all__ = [
    "VERSION",
    "PROTOCOL_NAME",
    "ROLES",
    "CAPABILITIES",
    "SUB_CAPABILITIES",
    "ERROR_CODES",
    "RETRYABLE",
    "CLOSING_CODES",
    "LAYERS",
    "HIDE_ELEMENTS",
    "OVERLAYS",
    "ENTITY_KINDS",
    "method_capability",
]

VERSION = 1
PROTOCOL_NAME = "saap/1"

#: Endpoint roles (SPEC §4.8.4). ``game`` is the first MTA client.
ROLES: tuple[str, ...] = ("ariane", "game", "client1", "client2", "server", "blender", "mock")

#: Every capability name of SAAP/1 (SAAP-v1.md §7).
CAPABILITIES: tuple[str, ...] = (
    "core", "camera", "world.settle", "capture", "capture.size", "capture.ids", "capture.depth",
    "pick", "pick.visible", "entity.query", "entity.inspect", "env", "view", "asset.render",
    "log", "console", "lua", "mem.read",
)
#: Capabilities that refine options of a method rather than add methods.
SUB_CAPABILITIES: tuple[str, ...] = ("capture.size", "capture.ids", "capture.depth", "pick.visible")

#: SAAP error codes (a subset of satk's, SPEC §3.3).
ERROR_CODES: tuple[str, ...] = ("AUTH", "PROTOCOL", "UNKNOWN_METHOD", "BAD_PARAMS", "UNSUPPORTED", "NOT_READY",
                                "BUSY", "TIMEOUT", "NOT_FOUND", "REVISION", "INTERNAL")
RETRYABLE: frozenset[str] = frozenset({"NOT_READY", "BUSY", "TIMEOUT"})
#: Errors after which the endpoint closes the connection.
CLOSING_CODES: frozenset[str] = frozenset({"AUTH", "PROTOCOL"})

LAYERS: tuple[str, ...] = ("color", "ids", "depth")
HIDE_ELEMENTS: tuple[str, ...] = ("hud", "gui", "chat", "overlays")
OVERLAYS: tuple[str, ...] = ("col", "zones", "paths", "tcyc")
ENTITY_KINDS: tuple[str, ...] = ("building", "object", "dummy", "vehicle", "ped", "player", "marker")


def method_capability(method: str) -> str | None:
    """Capability that gates ``method`` (from the schema files), ``None`` if unknown."""
    from .schema import capability_of

    return capability_of(method)
