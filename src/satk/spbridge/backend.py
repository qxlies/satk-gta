"""SAAP/1 backend selector for ``target=sp`` (the single-player ASI endpoint).

``satk_sp.asi`` is a native SAAP/1 endpoint: it writes ``work/run/endpoints/sp.json`` with
``"protocol": "saap/1"`` and listens on 127.0.0.1, exactly like the native Ariane and (planned)
MTA endpoints. So the viewer needs no new transport - it reuses
:class:`satk.viewer.backends.saap_native.SaapNativeBackend`.

This module exists so the spbridge package owns the ``sp`` wiring without editing viewer files.
:func:`sp_backend` returns a ready ``SaapNativeBackend`` for a running SP endpoint; the one-line
viewer hook that makes ``view_* target=sp`` work (adding ``"sp": "sp"`` to
``satk.viewer.backends.TARGET_ROLES``) is not applied yet; it belongs to the viewer package.
"""

from __future__ import annotations

from typing import Any

from . import ROLE

__all__ = ["ROLE", "sp_backend"]


def sp_backend(*, timeout: float = 120.0) -> Any:
    """A :class:`SaapNativeBackend` connected to the running SP endpoint (role ``sp``).

    Raises ``NOT_READY`` (via the SAAP client) when no SP endpoint is discoverable.
    """
    from ..viewer.backends.saap_native import SaapNativeBackend

    return SaapNativeBackend("sp", ROLE, timeout=timeout)
