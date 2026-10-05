"""``satk bug-report``: collect -> redact -> show -> write (only after a yes). Never sends anything."""

from __future__ import annotations

import datetime as _dt
import os
import sys
from pathlib import Path

from satk.core.errors import SatkError
from satk.core.paths import atomic_write, cfg, ensure_writable, jpath

from . import collect as C

__all__ = ["bug_report", "default_path"]


def default_path(now: _dt.datetime) -> Path:
    """``<work>/out/bugreport/satk-bug-report-<YYYYMMDD-HHMMSS>.md``."""
    return Path(cfg().paths.work) / "out" / "bugreport" / f"satk-bug-report-{now.strftime('%Y%m%d-%H%M%S')}.md"


def _interactive() -> bool:
    try:
        from satk.core.cli import surface

        return (surface() == "cli" and sys.stdin is not None and sys.stdin.isatty()
                and sys.stderr is not None and sys.stderr.isatty())
    except (AttributeError, ValueError):
        return False


def _ask(text: str, path: Path) -> bool:
    err = sys.stderr
    err.write(text + "\n")
    err.write(f"Write this report to {jpath(path)}? Nothing is sent anywhere. [y/N] ")
    err.flush()
    ans = (sys.stdin.readline() or "").strip().lower()
    return ans in ("y", "yes")


def bug_report(*, what: str | None = None, logs: int = 3, out: str | None = None, yes: bool = False) -> dict:
    """See :func:`satk.bugreport.ops.bug_report`."""
    if logs < 0 or logs > 50:
        raise SatkError("BAD_PARAMS", f"logs must be 0..50, got {logs}", hint="satk bug-report --logs 3")
    now = _dt.datetime.now(_dt.timezone.utc)
    path = Path(os.path.abspath(os.path.expanduser(out))) if out else default_path(now.astimezone())
    redact = C.redactor_for()
    sections = C.collect(what=what, logs=logs)
    text = C.render(sections, redact, now=now)
    res: dict = {"path": jpath(path), "sections": [[t, len(b)] for t, b in sections],
                 "redacted": dict(sorted(redact.counts.items())), "bytes": len(text.encode("utf-8"))}
    if not yes:
        if _interactive():
            if not _ask(text, path):
                return {"written": False, "cancelled": True, **res}
        else:
            return {"written": False, **res, "text": text,
                    "next": "read the text above, then: satk bug-report --yes (same options) to write it"}
    ensure_writable(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, text)
    return {"written": True, **res,
            "next": "open the file, check it, then attach it to your issue or message (satk sends nothing)"}
