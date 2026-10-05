"""Install wheels without pip: the files a wheel puts into ``site-packages``.

A wheel is a zip whose root is ``purelib``/``platlib``; ``<name>-<ver>.data/{purelib,platlib}/`` go to
the same root, while ``scripts``, ``headers`` and ``data`` are not needed by satk and are skipped
(console scripts, C headers, the pywin32 post-install script). ``*.dist-info`` is kept so
:mod:`importlib.metadata` sees the versions. Path traversal and two wheels writing the same file
are errors.
"""

from __future__ import annotations

import os
import re
import zipfile
from pathlib import PurePosixPath
from typing import Iterator

from ..core.errors import SatkError

__all__ = ["SKIPPED_DATA", "wheel_entries", "parse_filename"]

#: ``.data`` sub-directories that are not installed.
SKIPPED_DATA = frozenset({"scripts", "headers", "data"})
_DATA = re.compile(r"^[^/]+\.data/([^/]+)/(.*)$")
_WHEEL = re.compile(r"^(?P<name>[^-]+)-(?P<ver>[^-]+)(?:-(?P<build>\d[^-]*))?-(?P<py>[^-]+)-(?P<abi>[^-]+)"
                    r"-(?P<plat>[^-]+)\.whl$")


def parse_filename(filename: str) -> dict[str, str]:
    """``{name, ver, py, abi, plat}`` of a wheel file name (PEP 427), ``BAD_PARAMS`` otherwise."""
    m = _WHEEL.match(filename)
    if not m:
        raise SatkError("BAD_PARAMS", f"not a wheel file name: {filename}")
    return {k: v for k, v in m.groupdict().items() if v is not None}


def _safe(name: str, wheel: str) -> str:
    p = PurePosixPath(name)
    if name.startswith("/") or "\\" in name or ":" in name or any(part in ("", ".", "..") for part in p.parts):
        raise SatkError("CHECK_FAILED", f"{wheel}: unsafe path in wheel: {name!r}")
    return p.as_posix()


def wheel_entries(path: str | os.PathLike) -> Iterator[tuple[str, bytes]]:
    """``(path relative to site-packages, bytes)`` for every installed file of the wheel, sorted."""
    wheel = os.path.basename(os.fspath(path))
    with zipfile.ZipFile(path) as zf:
        names = sorted(i.filename for i in zf.infolist() if not i.is_dir())
        for name in names:
            rel = _safe(name, wheel)
            m = _DATA.match(rel)
            if m:
                kind, rest = m.groups()
                if kind in SKIPPED_DATA:
                    continue
                if kind not in ("purelib", "platlib") or not rest:
                    raise SatkError("CHECK_FAILED", f"{wheel}: unknown .data directory {kind!r}")
                rel = rest
            yield rel, zf.read(name)
