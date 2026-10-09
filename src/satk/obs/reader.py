"""Locating and reading observability files of any kind: JSONL streams, bench JSON, containers. Stdlib only."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

from ..core import paths
from ..core.errors import SatkError
from . import bench as B
from . import container as C
from . import key as K
from . import stream as S
from .schemas import BENCH_SCHEMA, LEGACY_BENCH_SCHEMA

__all__ = ["KINDS", "resolve", "out_path", "out_dir", "detect_kind", "iter_records", "record_rows", "bench_rows", "container_rows",
           "page", "get_path", "info_text", "RECORD_COLS"]

KINDS = ("jsonl", "bench", "container")
RECORD_COLS = ["seq", "rec", "t_us", "tick", "sub", "frame", "info"]
_KEY_MEMBERS = {"rec", "t_us", "tick", "sub", "frame", "tsrc"}


def obs_dir() -> Path:
    return paths.work("out", "obs")


def resolve(spec: str) -> Path:
    """An input file: the path as given, else the same relative path under ``work/out/obs``."""
    if not spec or not str(spec).strip():
        raise SatkError("BAD_PARAMS", "path is empty")
    p = Path(str(spec)).expanduser()
    cands = [p] if p.is_absolute() else [p, obs_dir() / p]
    for c in cands:
        if c.is_file():
            return c
    raise SatkError("NOT_FOUND", f"no file {spec!r}", hint="a path, or a name under work/out/obs (satk obs sample writes examples there)")


def out_path(spec: str | None, default_name: str) -> Path:
    """An output file: relative names live under ``work/out/obs``; every path goes through the write guard."""
    name = spec or default_name
    p = Path(name).expanduser()
    if not p.is_absolute():
        p = obs_dir() / p
    p = paths.ensure_writable(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def out_dir(spec: str) -> Path:
    """An output folder: relative names live under ``work/out/obs``; every path goes through the write guard."""
    p = Path(spec).expanduser()
    if not p.is_absolute():
        p = obs_dir() / p
    p = paths.ensure_writable(p)
    p.mkdir(parents=True, exist_ok=True)
    return p


def detect_kind(path: Path) -> str:
    """``container`` (magic), ``bench`` (one JSON object with a bench ``schema``) or ``jsonl`` (lines with ``rec``)."""
    if C.is_container(path) or path.suffix.lower() in (".saenet", ".saerec"):
        return "container"
    with open(path, "rb") as f:
        head = f.read(1 << 16)
    text = head.decode("utf-8", "replace").lstrip("﻿")
    first = next((ln for ln in text.splitlines() if ln.strip()), "")
    try:
        obj = json.loads(first)
    except ValueError:
        obj = None
    if isinstance(obj, dict) and "rec" in obj:
        return "jsonl"
    if isinstance(obj, dict) and obj.get("schema") in (BENCH_SCHEMA, LEGACY_BENCH_SCHEMA):
        return "bench"
    if path.stat().st_size <= 64 * 1024 * 1024:
        try:
            doc = json.loads(path.read_text(encoding="utf-8-sig"))
        except (ValueError, OSError):
            doc = None
        if isinstance(doc, dict) and doc.get("schema") in (BENCH_SCHEMA, LEGACY_BENCH_SCHEMA):
            return "bench"
    if path.suffix.lower() == ".jsonl":
        return "jsonl"
    raise SatkError("BAD_PARAMS", f"{path.name}: cannot tell what this file is",
                    hint="expected a JSONL stream (first line {\"rec\":\"hdr\",...}), a bench JSON (schema sae-bench/1) or a "
                         ".saenet/.saerec container; pass kind= to force")


def kind_of(path: Path, kind: str) -> str:
    if kind == "auto":
        return detect_kind(path)
    if kind not in KINDS:
        raise SatkError("BAD_PARAMS", f"kind must be auto, {', '.join(KINDS)}")
    return kind


def iter_records(path: Path, kind: str, *, chunk: int | None = None) -> Iterator[tuple[int, dict[str, Any]]]:
    """``(ordinal, record)`` of a JSONL file (ordinal = line number) or a container (ordinal = record number in file order)."""
    if kind == "jsonl":
        for lineno, rec, err in S.iter_jsonl(path):
            if err == "empty line":
                continue
            if err or not isinstance(rec, dict):
                raise SatkError("BAD_PARAMS", f"{path.name} line {lineno}: {err or 'not a JSON object'}",
                                hint=f"satk obs validate {path.name}")
            yield lineno, rec
    elif kind == "container":
        try:
            cont = C.open_container(path)
        except C.ContainerError as e:
            raise SatkError("BAD_PARAMS", f"{path.name}: {e.msg}", hint=f"satk obs validate {path.name}") from None
        try:
            for i, rec in enumerate(cont.iter_records(chunk), 1):
                yield i, rec
        except C.ContainerError as e:
            raise SatkError("BAD_PARAMS", f"{path.name}: {e.msg}", hint=f"satk obs validate {path.name}") from None
        except ValueError as e:
            raise SatkError("BAD_PARAMS", f"{path.name}: a chunk holds bad JSON: {e}", hint=f"satk obs validate {path.name}") from None
    else:
        raise SatkError("BAD_PARAMS", "records exist in JSONL streams and containers only")


# --------------------------------------------------------------------------- tables


def page(rows: list[list[Any]], limit: int, cursor: str | None) -> tuple[list[list[Any]], str | None]:
    """A slice of ``rows`` and the cursor of the next page (an offset)."""
    try:
        start = int(cursor) if cursor else 0
    except ValueError:
        raise SatkError("BAD_PARAMS", f"bad cursor {cursor!r}") from None
    if start < 0:
        raise SatkError("BAD_PARAMS", f"bad cursor {cursor!r}")
    sl = rows[start:start + limit]
    nxt = str(start + limit) if start + limit < len(rows) else None
    return sl, nxt


def get_path(obj: Any, dotted: str) -> Any:
    """``a.b.c`` into nested dicts (and ``a.3`` into lists, 0-based); None when absent."""
    cur = obj
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


def info_text(rec: dict[str, Any], width: int = 110) -> str:
    rest = {k: v for k, v in rec.items() if k not in _KEY_MEMBERS}
    s = json.dumps(rest, separators=(",", ":"), ensure_ascii=False)
    return s if len(s) <= width else s[:width - 3] + "..."


def record_rows(path: Path, kind: str, *, recs: list[str] | None = None, t_from: int | None = None, t_to: int | None = None,
                tick_from: int | None = None, tick_to: int | None = None, node: str | None = None,
                fields: list[str] | None = None, chunk: int | None = None) -> tuple[list[str], list[list[Any]]]:
    """Columns and all matching rows of a stream or container."""
    cols = ["seq", *fields] if fields else list(RECORD_COLS)
    rows: list[list[Any]] = []
    want = set(recs or [])
    for seq, rec in iter_records(path, kind, chunk=chunk):
        if want and rec.get("rec") not in want:
            continue
        if node is not None and rec.get("node") != node:
            continue
        t = rec.get("t_us")
        if t_from is not None and (t is None or t < t_from):
            continue
        if t_to is not None and (t is None or t > t_to):
            continue
        tk = rec.get("tick")
        if tick_from is not None and (tk is None or tk == K.NONE or tk < tick_from):
            continue
        if tick_to is not None and (tk is None or tk == K.NONE or tk > tick_to):
            continue
        if fields:
            rows.append([seq, *[get_path(rec, f) for f in fields]])
        else:
            rows.append([seq, rec.get("rec"), t, tk, rec.get("sub"), rec.get("frame"), info_text(rec)])
    return cols, rows


def bench_rows(doc: dict[str, Any], fields: list[str] | None) -> tuple[list[str], list[list[Any]]]:
    """Stage rows of a bench document (``satk-bench/1`` or ``sae-bench/1``): the standard columns or dotted ``fields``."""
    if not fields:
        return list(B.TABLE_COLS), B.table_rows(doc)
    rows = []
    for st in doc.get("stages") or []:
        rows.append([st.get("id"), *[get_path(st, f) for f in fields]])
    return ["stage", *fields], rows


CHUNK_COLS = ["chunk", "type", "codec", "stored", "raw", "recs", "t_first_us", "t_last_us", "crit", "offset"]


def container_rows(cont: C.Container) -> list[list[Any]]:
    out = []
    for c in cont.chunks:
        out.append([c.index, c.name, {0: "none", 1: "zlib", 2: "zstd"}.get(c.codec, str(c.codec)), c.stored_size, c.raw_size,
                    c.rec_count, None if c.t_first_us < 0 else c.t_first_us, None if c.t_last_us < 0 else c.t_last_us,
                    c.critical, c.offset])
    return out
