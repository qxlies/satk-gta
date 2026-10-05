"""Loader of the vendored gta-flow core (``vendor/gtaflow``; MIT, see ``vendor/gtaflow/VENDORED.md``).

The vendored ``sa_traffic`` package is loaded under the private name :data:`PACKAGE`
(``satk_vendor_gtaflow``) straight from its directory: ``sys.path`` is not changed, and an
installed upstream ``sa_traffic`` can neither shadow nor be shadowed by it. Modules are imported on
first use only (``satk version`` stays fast).

Example::

    from satk.paths.vendor import gtaflow
    gf = gtaflow()
    area = gf.codec.decode(data)          # one nodes<N>.dat
    files, manifest = gf.compiler.compile_document(gf.document.import_files(paths))
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

from ..core.config import REPO_ROOT
from ..core.errors import SatkError

__all__ = ["PACKAGE", "MODULES", "vendor_dir", "gtaflow"]

#: Name of the vendored package in ``sys.modules``.
PACKAGE = "satk_vendor_gtaflow"
#: Vendored modules (``vendor/gtaflow/sa_traffic/<name>.py``).
MODULES = ("codec", "document", "compiler", "oracle", "controls", "signals")

_lock = threading.Lock()
_loaded: SimpleNamespace | None = None


def vendor_dir() -> Path:
    """``<checkout>/vendor/gtaflow`` (holds ``LICENSE``, ``NOTICE.md``, ``sa_traffic/``)."""
    return REPO_ROOT / "vendor" / "gtaflow"


def _package_dir() -> Path:
    d = vendor_dir() / "sa_traffic"
    if not (d / "__init__.py").is_file():
        raise SatkError(
            "DEPENDENCY", f"vendored gta-flow not found: {d.as_posix()}",
            hint="run satk from a checkout (vendor/gtaflow is part of the repository, not of the wheel)",
        )
    return d


def gtaflow() -> SimpleNamespace:
    """The vendored modules as attributes: ``codec document compiler oracle controls signals``."""
    global _loaded
    with _lock:
        if _loaded is not None:
            return _loaded
        pkg = sys.modules.get(PACKAGE)
        if pkg is None:
            d = _package_dir()
            spec = importlib.util.spec_from_file_location(
                PACKAGE, d / "__init__.py", submodule_search_locations=[str(d)])
            if spec is None or spec.loader is None:  # pragma: no cover - only for a broken checkout
                raise SatkError("DEPENDENCY", f"cannot load vendored gta-flow from {d.as_posix()}")
            pkg = importlib.util.module_from_spec(spec)
            sys.modules[PACKAGE] = pkg
            try:
                spec.loader.exec_module(pkg)
            except BaseException:
                sys.modules.pop(PACKAGE, None)
                raise
        mods = {name: importlib.import_module(f"{PACKAGE}.{name}") for name in MODULES}
        _loaded = SimpleNamespace(**mods)
        return _loaded
