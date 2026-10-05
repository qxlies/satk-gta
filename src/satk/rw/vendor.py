"""Access to the vendored rwfury 0.6.1 (``vendor/rwfury``, MIT; see ``vendor/rwfury/VENDORED.md``).

rwfury is optional at run time: :func:`load` returns ``None`` when the directory is missing (an
installation without the checkout's ``vendor/``). satk's writers never depend on it; it supplies the
collision surface names and serves the tests as an independent reader.
"""

from __future__ import annotations

import importlib
import sys
import threading
from pathlib import Path
from types import ModuleType

__all__ = ["VERSION", "vendor_dir", "load", "surface_names"]

VERSION = "0.6.1"
_lock = threading.Lock()
_mod: ModuleType | None = None


def vendor_dir() -> Path | None:
    """``<checkout>/vendor/rwfury`` if it holds the package, else ``None``."""
    d = Path(__file__).resolve().parents[3] / "vendor" / "rwfury"
    return d if (d / "rwfury" / "__init__.py").is_file() else None


def load() -> ModuleType | None:
    """Import the vendored ``rwfury`` package (cached); ``None`` when it is not available."""
    global _mod
    with _lock:
        if _mod is not None:
            return _mod
        d = vendor_dir()
        if d is None:
            return None
        if str(d) not in sys.path:
            sys.path.insert(0, str(d))
        mod = importlib.import_module("rwfury")
        if Path(mod.__file__).resolve().parent.parent != d.resolve():   # another rwfury came first
            return None
        _mod = mod
        return mod


def surface_names() -> dict[str, int] | None:
    """``{"TARMAC": 1, ...}`` from ``rwfury.col_materials.ColMaterial``; ``None`` without rwfury."""
    mod = load()
    if mod is None:
        return None
    return {m.name: int(m.value) for m in mod.ColMaterial}
