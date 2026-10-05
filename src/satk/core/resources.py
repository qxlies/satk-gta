"""Data files shipped with satk (``<repo>/data``): manifests, ``exe_versions.json``, rules, notices.

One API for both ways satk runs (``docs/en/install.md``):

* **from a checkout** (shims, ``pip install -e``): ``<repo>/data`` is read in place
  (:data:`satk.core.config.DATA_ROOT`);
* **installed as a package** (``pip install <checkout>``, ``pipx``, a wheel): the build copies
  ``data/`` into the package as ``satk/_data`` (the ``build_py`` hook in ``setup.py``), and it is
  found through :func:`importlib.resources.files`.

The packaged copy wins when it exists: it was built together with this code, and a checkout never
has ``src/satk/_data``. Names are relative POSIX paths inside the data root, given whole or in
parts: ``read_json("manifests/stock-1.0us-hoodlum.json")`` = ``read_json("manifests",
"stock-1.0us-hoodlum.json")``. Absolute names, ``..`` and backslashes are ``BAD_PARAMS``.

Example::

    from satk.core import resources
    db = resources.read_json("exe_versions.json")
    stock = resources.data_path("manifests", "stock-1.0us-hoodlum.json")  # a Path (may not exist)

The data is read-only here: writers (``python -m satk.game.manifests build``, ``satk note export``)
target the checkout's ``data/`` explicitly. stdlib only; :mod:`importlib.resources` is imported
lazily.
"""

from __future__ import annotations

import json
import os
from pathlib import Path, PurePath
from typing import Any

from .config import DATA_ROOT
from .errors import SatkError

__all__ = [
    "PACKAGE",
    "PACKAGE_DATA",
    "SOURCES",
    "HINT",
    "data_root",
    "data_source",
    "data_path",
    "exists",
    "read_bytes",
    "read_text",
    "read_json",
    "list_files",
    "info",
    "reset",
]

#: Package whose resources hold the installed copy.
PACKAGE = "satk"
#: Directory inside :data:`PACKAGE` that the build fills with ``<repo>/data``.
PACKAGE_DATA = "_data"
#: Values of :func:`data_source`.
SOURCES: tuple[str, ...] = ("package", "checkout", "missing")

#: Fix hint for missing data files.
HINT = ("data/ ships with satk: run from a full checkout (satk.cmd / satk.sh) or reinstall the package "
        "from one (pip install <checkout>)")

_resolved: tuple[Path, str] | None = None


def _package_dir() -> Path | None:
    """``satk/_data`` of an installed package (``None`` in a checkout or when it is not on disk)."""
    try:
        from importlib.resources import files

        t = files(PACKAGE) / PACKAGE_DATA
    except (ImportError, TypeError, ValueError, OSError):  # pragma: no cover - broken import machinery
        return None
    # A regular package on disk gives a pathlib.Path; a zip import would not (unsupported).
    if isinstance(t, Path) and t.is_dir():
        return t
    return None


def _resolve() -> tuple[Path, str]:
    global _resolved
    if _resolved is None:
        pkg = _package_dir()
        if pkg is not None:
            _resolved = (pkg, "package")
        elif DATA_ROOT.is_dir():
            _resolved = (DATA_ROOT, "checkout")
        else:
            _resolved = (DATA_ROOT, "missing")
    return _resolved


def reset() -> None:
    """Forget the resolved data root (tests)."""
    global _resolved
    _resolved = None


def data_root() -> Path:
    """Directory holding the data files (the checkout's ``data/`` when nothing is found)."""
    return _resolve()[0]


def data_source() -> str:
    """Where :func:`data_root` comes from: ``package`` | ``checkout`` | ``missing``."""
    return _resolve()[1]


def _parts(names: tuple[str | os.PathLike, ...]) -> list[str]:
    out: list[str] = []
    for n in names:
        s = n.as_posix() if isinstance(n, PurePath) else os.fspath(n)
        if not isinstance(s, str) or "\\" in s or s.startswith("/") or (len(s) > 1 and s[1] == ":"):
            raise SatkError("BAD_PARAMS", f"bad data name {s!r}: use a relative POSIX path like 'manifests/x.json'")
        for p in s.split("/"):
            if p in ("", "."):
                continue
            if p == "..":
                raise SatkError("BAD_PARAMS", f"bad data name {s!r}: '..' is not allowed")
            out.append(p)
    return out


def data_path(*names: str | os.PathLike) -> Path:
    """Path of ``names`` inside the data root (no existence check); no names = the root itself."""
    return data_root().joinpath(*_parts(names))


def exists(*names: str | os.PathLike) -> bool:
    """Whether the data file or directory ``names`` exists."""
    return data_path(*names).exists()


def _file(names: tuple[str | os.PathLike, ...]) -> Path:
    parts = _parts(names)
    if not parts:
        raise SatkError("BAD_PARAMS", "a data file name is required")
    p = data_root().joinpath(*parts)
    if not p.is_file():
        raise SatkError("NOT_FOUND", f"satk data file not found: {'/'.join(parts)} (in {p.parent.as_posix()})",
                        hint=HINT)
    return p


def read_bytes(*names: str | os.PathLike) -> bytes:
    """Content of a data file (``NOT_FOUND`` with a reinstall hint when it is missing)."""
    p = _file(names)
    try:
        return p.read_bytes()
    except OSError as e:
        raise SatkError("NOT_FOUND", f"cannot read satk data file {p.as_posix()}: {e}", hint=HINT) from None


def read_text(*names: str | os.PathLike, encoding: str = "utf-8") -> str:
    """Text of a data file (UTF-8 by default)."""
    p = _file(names)
    try:
        return p.read_text(encoding=encoding)
    except OSError as e:
        raise SatkError("NOT_FOUND", f"cannot read satk data file {p.as_posix()}: {e}", hint=HINT) from None
    except UnicodeDecodeError as e:
        raise SatkError("INTERNAL", f"satk data file {p.as_posix()} is not {encoding}: {e}") from None


def read_json(*names: str | os.PathLike) -> Any:
    """Parsed JSON data file (``INTERNAL`` when it is not valid JSON)."""
    text = read_text(*names)
    try:
        return json.loads(text)
    except ValueError as e:
        raise SatkError("INTERNAL", f"satk data file {'/'.join(_parts(names))} is not valid JSON: {e}") from None


def list_files(*names: str | os.PathLike, pattern: str = "*") -> list[str]:
    """Sorted relative POSIX names of the files matching ``pattern`` in data directory ``names``
    (not recursive; ``[]`` when the directory does not exist)."""
    parts = _parts(names)
    d = data_root().joinpath(*parts)
    if not d.is_dir():
        return []
    prefix = "/".join(parts)
    return sorted(f"{prefix}/{f.name}" if prefix else f.name for f in d.glob(pattern) if f.is_file())


def info() -> dict:
    """``{root, source, files}`` for ``satk version`` / ``satk doctor`` (``files`` counts all data files)."""
    root, source = _resolve()
    n = sum(1 for f in root.rglob("*") if f.is_file() and "__pycache__" not in f.parts) if root.is_dir() else 0
    return {"root": root.as_posix(), "source": source, "files": n}
