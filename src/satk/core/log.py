"""Logging: stderr and files under ``work/logs`` only; stdout stays clean (MCP, JSON).

* :func:`get_logger` — ``logging.getLogger("satk.<name>")``;
* :func:`setup` — idempotent stderr handler, level from ``SATK_LOG`` (default WARNING);
* :func:`add_file` — extra file handler, e.g. ``add_file("mcp")`` -> ``work/logs/mcp.log``;
* :func:`record_exception` — append a traceback to ``work/logs/errors.log`` and return its path
  (used for ``INTERNAL`` errors so the envelope can point to it).
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
import sys
import traceback
from pathlib import Path

__all__ = ["get_logger", "setup", "add_file", "record_exception", "log_path"]

_FMT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_configured = False
_stderr_handler: logging.Handler | None = None


def get_logger(name: str = "satk") -> logging.Logger:
    """Logger under the ``satk`` namespace."""
    if name != "satk" and not name.startswith("satk."):
        name = f"satk.{name}"
    return logging.getLogger(name)


def _level(lvl: str | int) -> int:
    if isinstance(lvl, str):
        v = logging.getLevelName(lvl.strip().upper())
        return v if isinstance(v, int) else logging.WARNING
    return int(lvl)


def _effective_root_level(root: logging.Logger) -> int:
    levels = [h.level for h in root.handlers if h.level != logging.NOTSET]
    return min(levels) if levels else logging.WARNING


def setup(level: str | int | None = None, *, stderr: bool = True) -> logging.Logger:
    """Configure the ``satk`` logger once: stderr handler (never stdout).

    The stderr level comes from ``level`` or ``SATK_LOG`` (default WARNING). File handlers
    added by :func:`add_file` keep their own level and do not make stderr more verbose.
    """
    global _configured, _stderr_handler
    root = logging.getLogger("satk")
    lvl = _level(level if level is not None else os.environ.get("SATK_LOG", "WARNING"))
    if not _configured:
        root.propagate = False
        if stderr:
            h = logging.StreamHandler(sys.stderr)
            h.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
            root.addHandler(h)
            _stderr_handler = h
        _configured = True
    if _stderr_handler is not None:
        _stderr_handler.setLevel(lvl)
    root.setLevel(min(lvl, _effective_root_level(root)))
    return root


def log_path(name: str = "satk") -> Path:
    """``<work>/logs/<name>.log`` (directory created)."""
    from .paths import work

    return work("logs") / f"{name}.log"


def add_file(name: str, level: int = logging.INFO) -> Path:
    """Add a UTF-8 file handler ``work/logs/<name>.log`` to the ``satk`` logger."""
    path = log_path(name)
    root = logging.getLogger("satk")
    for h in root.handlers:
        if isinstance(h, logging.FileHandler) and Path(h.baseFilename) == path:
            return path
    fh = logging.FileHandler(path, encoding="utf-8")
    fh.setLevel(level)
    fh.setFormatter(logging.Formatter(_FMT))
    root.addHandler(fh)
    if root.level == logging.NOTSET or root.level > level:
        root.setLevel(level)  # handlers filter by their own level (stderr stays quiet)
    return path


def record_exception(exc: BaseException, context: str = "") -> Path | None:
    """Append ``exc``'s traceback to ``work/logs/errors.log``; ``None`` if that fails."""
    try:
        path = log_path("errors")
        stamp = _dt.datetime.now().isoformat(timespec="seconds")
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"--- {stamp} pid={os.getpid()} {context}\n{tb}\n")
        return path
    except Exception:  # noqa: BLE001 - logging must never raise
        return None
