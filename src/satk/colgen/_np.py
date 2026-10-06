"""numpy for the geometry modules of ``satk.colgen``, imported on first use.

satk never imports numpy at module level (fast ``satk version``, numpy stays optional until a collision is
generated): ``from ._np import np`` gives a stand-in whose first attribute access imports numpy through
``require_module`` (a ``DEPENDENCY`` error with the install hint when it is missing).
"""

from __future__ import annotations

from typing import Any

from ..core.errors import require_module

__all__ = ["np"]


class _LazyNumpy:
    _mod: Any = None

    def __getattr__(self, name: str) -> Any:
        mod = _LazyNumpy._mod
        if mod is None:
            mod = _LazyNumpy._mod = require_module("numpy", purpose="collision generation (satk col gen)")
        return getattr(mod, name)


np: Any = _LazyNumpy()
