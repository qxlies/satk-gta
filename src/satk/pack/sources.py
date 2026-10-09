"""Input files of ``satk pack build``: a folder or a list of DFF / TXD / COL / IFP / IPL / DAT files the user owns.

A folder contributes every file with a streamable extension (recursively, sorted); its logical names are the
lower-case relative paths with forward slashes. A single file contributes its lower-case base name. ``flat`` makes
every logical name the base name. Two inputs with the same logical name are an error (the pack is a mapping, not a
stack of layers); the same file given twice is taken once. Stdlib only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..core.errors import SatkError
from ..core.paths import jpath
from .layout import STREAM_EXTS

__all__ = ["Source", "collect", "logical_problem", "MAX_LOGICAL_BYTES"]

#: Longest logical name in bytes (UTF-8).
MAX_LOGICAL_BYTES = 240


@dataclass(frozen=True)
class Source:
    """One file to pack: ``logical`` is the name inside the pack, ``path`` the file on disk."""

    logical: str
    path: Path
    size: int

    @property
    def ext(self) -> str:
        return self.logical.rsplit(".", 1)[-1] if "." in self.logical.rsplit("/", 1)[-1] else ""


def logical_problem(name: str) -> str | None:
    """Why ``name`` cannot be a logical name, or ``None``: lower-case, ``/`` separated, no empty or dotted parts."""
    if not name:
        return "empty name"
    if name != name.lower():
        return "not lower-case"
    if "\\" in name or name.startswith("/") or name.endswith("/"):
        return "must be a relative path with forward slashes"
    if any(ord(c) < 32 or ord(c) == 127 for c in name):
        return "contains a control character"
    parts = name.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return "contains an empty, '.' or '..' component"
    if ":" in name:
        return "contains ':'"
    if len(name.encode("utf-8")) > MAX_LOGICAL_BYTES:
        return f"longer than {MAX_LOGICAL_BYTES} bytes"
    ext = parts[-1].rsplit(".", 1)[-1] if "." in parts[-1] else ""
    if ext not in STREAM_EXTS or parts[-1].startswith("."):
        return f"extension must be one of {', '.join(STREAM_EXTS)}"
    return None


def _resolve(raw: str) -> Path:
    s = str(raw).strip().strip('"')
    if not s:
        raise SatkError("BAD_PARAMS", "an empty input path", hint="satk pack build mymod --out mymod.saepak")
    p = Path(s)
    if not p.is_absolute():
        p = Path.cwd() / p
    p = Path(os.path.abspath(p))
    if not p.exists():
        raise SatkError("NOT_FOUND", f"no such file or folder: {s}",
                        hint="a folder or DFF/TXD/COL/IFP/IPL/DAT files (absolute or relative to the current folder)")
    return p


def collect(inputs: list[str], *, flat: bool = False) -> tuple[list[Source], list[str]]:
    """Files of the inputs as sorted :class:`Source` rows plus warnings (``SKIPPED: ...``)."""
    if not inputs:
        raise SatkError("BAD_PARAMS", "no input given", hint="satk pack build mymod --out mymod.saepak")
    found: dict[str, Source] = {}
    seen_paths: set[str] = set()
    skipped: list[str] = []

    def add(logical: str, path: Path) -> None:
        key = os.path.normcase(str(path))
        if key in seen_paths:
            return
        why = logical_problem(logical)
        if why:
            raise SatkError("BAD_PARAMS", f"{jpath(path)}: logical name {logical!r}: {why}",
                            hint="rename the file, or pass the folder that holds it")
        prev = found.get(logical)
        if prev is not None:
            raise SatkError("BAD_PARAMS", f"two inputs map to the logical name {logical!r}",
                            hint="a pack maps each name once; remove one input or rename a file",
                            data={"a": jpath(prev.path), "b": jpath(path)})
        try:
            size = path.stat().st_size
        except OSError as e:
            raise SatkError("NOT_FOUND", f"cannot read {jpath(path)}: {e}") from None
        seen_paths.add(key)
        found[logical] = Source(logical, path, size)

    for raw in inputs:
        p = _resolve(raw)
        if p.is_dir():
            for dirpath, dirnames, filenames in os.walk(p):
                dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
                for fn in sorted(filenames):
                    full = Path(dirpath) / fn
                    ext = fn.rsplit(".", 1)[-1].lower() if "." in fn else ""
                    if ext not in STREAM_EXTS or fn.startswith("."):
                        skipped.append(fn)
                        continue
                    rel = full.relative_to(p).as_posix().lower()
                    add(fn.lower() if flat else rel, full)
        else:
            ext = p.name.rsplit(".", 1)[-1].lower() if "." in p.name else ""
            if ext not in STREAM_EXTS:
                raise SatkError("BAD_PARAMS", f"{p.name}: not a streamable file (extension .{ext or '?'})",
                                hint="a pack holds " + ", ".join("." + e for e in STREAM_EXTS) + " files")
            add(p.name.lower(), p)
    warn: list[str] = []
    if skipped:
        sample = ", ".join(sorted(set(skipped))[:4])
        warn.append(f"SKIPPED: {len(skipped)} file(s) without a streamable extension left out (first: {sample})")
    if not found:
        raise SatkError("BAD_PARAMS", "no DFF/TXD/COL/IFP/IPL/DAT files in the inputs",
                        hint="a pack holds " + ", ".join("." + e for e in STREAM_EXTS) + " files")
    return [found[k] for k in sorted(found, key=lambda s: s.encode("utf-8"))], warn
