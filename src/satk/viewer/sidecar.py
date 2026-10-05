"""Capture ids, file layout and sidecars (SPEC §3.6, §4.10.5).

A capture ``cap:20261004-153201-ab12`` lives in ``work/out/captures/20261004/``:
``<id>.png`` (colour), ``<id>.ids.png``/``<id>.depth.f32`` (when requested), ``<id>_marks.png``,
``<id>_grid.png``, ``<id>_cmp.png`` and the sidecar ``<id>.json`` with everything needed to
repeat it (``satk view replay``): target, backend build, protocol, request, actual pose,
environment, size, ``settled``, legend, sha256 and time.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import secrets
from pathlib import Path
from typing import Any

from ..core import paths
from ..core.errors import SatkError
from ..core.ids import Sid

__all__ = ["SIDECAR_VERSION", "new_capture_id", "capture_dir", "capture_paths", "sidecar_path", "write", "load",
           "find_sidecars"]

SIDECAR_VERSION = 1
_ID = re.compile(r"^(\d{8})-(\d{6})-([0-9a-f]{4,8})$")


def new_capture_id(now: _dt.datetime | None = None) -> str:
    """``20261004-153201-ab12`` (local time + 4 random hex digits)."""
    t = now or _dt.datetime.now()
    return f"{t:%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"


def _key(cap: str) -> str:
    s = str(cap)
    key = Sid.parse(s).key if s.startswith("cap:") else s
    if not _ID.match(key):
        raise SatkError("BAD_ID", f"bad capture id {cap!r} (expected cap:YYYYMMDD-HHMMSS-xxxx)")
    return key


def capture_dir(cap: str) -> Path:
    key = _key(cap)
    return paths.work("out", "captures", key[:8])


def capture_paths(cap: str) -> dict[str, Path]:
    key = _key(cap)
    d = capture_dir(key)
    return {"prefix": d / key, "color": d / f"{key}.png", "ids": d / f"{key}.ids.png", "depth": d / f"{key}.depth.f32",
            "marks": d / f"{key}_marks.png", "grid": d / f"{key}_grid.png", "cmp": d / f"{key}_cmp.png",
            "sidecar": d / f"{key}.json"}


def sidecar_path(cap_or_path: str | Path) -> Path:
    """Sidecar path of a ``cap:`` SID, a bare id or a path to the sidecar/PNG."""
    s = str(cap_or_path)
    if s.startswith("cap:") or _ID.match(s):
        return capture_paths(s)["sidecar"]
    p = Path(s)
    if p.suffix.lower() == ".png":
        stem = p.name.split(".")[0].replace("_marks", "").replace("_grid", "").replace("_cmp", "")
        p = p.with_name(stem + ".json")
    return p


def write(cap: str, data: dict) -> Path:
    p = capture_paths(cap)["sidecar"]
    doc = {"satk_sidecar": SIDECAR_VERSION, "id": f"cap:{_key(cap)}", **data,
           "time": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    return paths.atomic_write(p, json.dumps(doc, ensure_ascii=False, indent=1, default=str))


def load(cap_or_path: str | Path) -> dict:
    p = sidecar_path(cap_or_path)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SatkError("NOT_FOUND", f"no capture sidecar {paths.jpath(p)}", hint="satk asset find <id> --kind cap") from None
    except ValueError as e:
        raise SatkError("BAD_PARAMS", f"sidecar {paths.jpath(p)} is not JSON: {e}") from None
    if not isinstance(d, dict) or d.get("satk_sidecar") != SIDECAR_VERSION:
        raise SatkError("BAD_PARAMS", f"{paths.jpath(p)} is not a satk capture sidecar")
    d["_path"] = paths.jpath(p)
    return d


def find_sidecars(q: str | None = None, limit: int = 50) -> list[dict]:
    """Newest-first sidecars whose id/target contains ``q``."""
    root = paths.work("out", "captures")
    out: list[dict[str, Any]] = []
    for p in sorted(root.glob("*/*.json"), reverse=True):
        if not _ID.match(p.stem):
            continue
        if q and q.lower() not in p.stem and q.lower() not in p.read_text(encoding="utf-8")[:400].lower():
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            continue
        d["_path"] = paths.jpath(p)
        out.append(d)
        if len(out) >= limit:
            break
    return out
