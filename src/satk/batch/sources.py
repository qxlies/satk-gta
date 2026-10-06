"""Inputs of a batch: what ``--over`` expands to.

``over`` is one of (checked in this order):

* ``sql:<statement>`` or a statement starting with ``SELECT``/``WITH``: rows of a read-only index query
  (``--profile``); columns named like parameters of the operation fill those parameters, otherwise the
  first column is the input;
* ``@file`` (``@-`` = stdin): one input per line; ``#`` comments and blank lines are skipped; a line that is
  a JSON object is a whole argument set (``{"target": "a.dff", "sev": "info"}``);
* a glob (``*``, ``?``, ``[``; ``**`` = any depth); a segment ending in ``.img`` followed by an entry
  pattern globs inside IMG archives (``models/gta3.img/*.dff``). A relative pattern that matches nothing
  under the current folder is tried under the game root of ``--profile`` (vanilla by default);
* an existing folder: its direct children (files and folders), sorted;
* an existing file: that one input;
* otherwise a comma-separated list of literal inputs (SIDs, names: ``model:411,model:415``). A single value
  that looks like a path (has ``/`` or ``\\`` or an extension) but does not exist is ``NOT_FOUND``.

Inputs are sorted (paths case-insensitively) so a run is deterministic; at most :data:`MAX_ITEMS`.
"""

from __future__ import annotations

import fnmatch
import glob as _glob
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.errors import SatkError
from ..core.paths import jpath

__all__ = ["Inputs", "expand", "MAX_ITEMS", "is_sql"]

MAX_ITEMS = 100_000
_SQL = re.compile(r"^\s*(select|with)\b", re.I)
_SID = re.compile(r"^[a-z][a-z0-9_]+:", re.I)
_GLOB = re.compile(r"[*?\[]")
_PAGE = 500


@dataclass
class Inputs:
    """Expanded inputs: ``items`` are strings or argument dicts; ``kind`` says where they came from."""

    kind: str                       # sql | list | glob | img | dir | file | items
    items: list[Any]
    source: str
    base: str | None = None         # common folder of path inputs (shown once, labels are relative)
    warn: list[str] = field(default_factory=list)


def is_sql(over: str) -> bool:
    return over.strip().lower().startswith("sql:") or bool(_SQL.match(over))


def _too_many(n: int, source: str) -> SatkError:
    return SatkError("BAD_PARAMS", f"--over gives {n} inputs, at most {MAX_ITEMS}",
                     hint="narrow the glob or the query, or split the list file", data={"over": source})


def _sql(over: str, profile: str | None, params: set[str]) -> Inputs:
    sql = over.strip()
    if sql.lower().startswith("sql:"):
        sql = sql[4:].strip()
    sql = sql.rstrip().rstrip(";")
    from ..index.api import open_index

    db = open_index(profile or "vanilla")
    items: list[Any] = []
    cols: list[str] = []
    offset = 0
    while True:
        env = db.query(f"SELECT * FROM ({sql}) LIMIT {_PAGE} OFFSET {offset}", [], _PAGE)
        cols = env["cols"]
        rows = env["rows"]
        for r in rows:
            items.append(dict(zip(cols, r)))
        if len(rows) < _PAGE:
            break
        offset += _PAGE
        if len(items) > MAX_ITEMS:
            raise _too_many(len(items), over)
    mapped = [c for c in cols if c in params]
    if not mapped:
        first = cols[0] if cols else None
        items = [it.get(first) for it in items]
        items = ["" if v is None else str(v) for v in items]
    else:
        items = [{k: v for k, v in it.items() if k in params and v is not None} for it in items]
    warn = list(env.get("warn") or [])
    return Inputs("sql", items, over, warn=warn)


def _list_file(spec: str) -> Inputs:
    name = spec[1:].strip()
    if not name:
        raise SatkError("BAD_PARAMS", "--over @: give a list file (@inputs.txt) or @- for stdin")
    if name == "-":
        text = sys.stdin.read()
    else:
        p = Path(name)
        if not p.is_file():
            raise SatkError("NOT_FOUND", f"list file not found: {jpath(p)}",
                            hint="--over @<file with one input per line>")
        text = p.read_text(encoding="utf-8-sig", errors="replace")
    items: list[Any] = []
    for no, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("{"):
            try:
                d = json.loads(s)
            except json.JSONDecodeError as e:
                raise SatkError("BAD_PARAMS", f"{name}:{no}: invalid JSON object: {e}") from None
            if not isinstance(d, dict):
                raise SatkError("BAD_PARAMS", f"{name}:{no}: expected a JSON object")
            items.append(d)
        else:
            items.append(s)
        if len(items) > MAX_ITEMS:
            raise _too_many(len(items), spec)
    return Inputs("list", items, spec)


def _sort(paths: list[str]) -> list[str]:
    return sorted(set(paths), key=lambda s: (s.lower().replace("\\", "/"), s))


def _img_split(pattern: str) -> tuple[str, str] | None:
    """``("models/gta3.img", "*.dff")`` when a non-final segment ends with ``.img``."""
    segs = re.split(r"[\\/]", pattern)
    for k, s in enumerate(segs[:-1]):
        if s.lower().endswith(".img"):
            rest = segs[k + 1:]
            if len(rest) != 1:
                raise SatkError("BAD_PARAMS", f"IMG entries have no folders: {pattern!r}",
                                hint="models/gta3.img/*.dff")
            left = "/".join(segs[: k + 1])
            return left, rest[0]
    return None


def _glob_files(pattern: str) -> list[str]:
    return _sort(_glob.glob(pattern, recursive=True))


def _anchors(pattern: str, profile: str | None) -> list[tuple[str, str | None]]:
    """(pattern to glob, note) candidates: as given, then under the profile's game root."""
    out: list[tuple[str, str | None]] = [(pattern, None)]
    if not os.path.isabs(pattern) and not pattern.startswith(("~", ".")):
        try:
            from ..core.paths import profile_root

            root = profile_root(profile or "vanilla")
        except SatkError:
            root = None
        if root is not None and Path(root).is_dir():
            out.append((str(Path(root) / pattern), f"matched under the {profile or 'vanilla'} game root {jpath(root)}"))
    return out


def _img_entries(left: str, entry_pat: str, profile: str | None) -> tuple[list[str], list[str]]:
    from ..formats.img import ImgArchive
    from ..formats.rw import FormatError

    warn: list[str] = []
    imgs: list[str] = []
    for pat, note in _anchors(left, profile):
        imgs = [p for p in _glob_files(pat) if os.path.isfile(p)] if _GLOB.search(pat) else (
            [pat] if os.path.isfile(pat) else [])
        if imgs:
            if note:
                warn.append(f"OVER: {note}")
            break
    items: list[str] = []
    for img in imgs:
        try:
            with ImgArchive.open(img) as a:
                names = [e.name for e in a.entries]
        except (FormatError, OSError) as e:
            warn.append(f"IMG_UNREADABLE: {jpath(img)}: {e}")
            continue
        base = jpath(img)
        for n in sorted(names, key=str.lower):
            if fnmatch.fnmatch(n.lower(), entry_pat.lower()):
                items.append(f"{base}/{n}")
        if len(items) > MAX_ITEMS:
            raise _too_many(len(items), left)
    return items, warn


def _common_base(items: list[str]) -> str | None:
    paths = [i for i in items if isinstance(i, str) and ("/" in i or "\\" in i)]
    if len(paths) != len(items) or len(items) < 2:
        return None
    try:
        base = os.path.commonpath([os.path.dirname(p.replace("\\", "/")) for p in paths])
    except ValueError:
        return None
    return base.replace("\\", "/") or None


def expand(over: str, *, profile: str | None = None, params: set[str] | None = None) -> Inputs:
    """Expand ``--over`` (see the module docstring); ``params`` = parameter names of the operation."""
    if not isinstance(over, str) or not over.strip():
        raise SatkError("BAD_PARAMS", "--over is empty", hint='--over "mods/*.dff" | @list.txt | "SELECT ..."')
    over = over.strip()
    if is_sql(over):
        return _sql(over, profile, params or set())
    if over.startswith("@"):
        return _list_file(over)
    if _GLOB.search(over):
        split = _img_split(over)
        if split is not None:
            items, warn = _img_entries(split[0], split[1], profile)
            kind = "img"
        else:
            items, warn = [], []
            for pat, note in _anchors(over, profile):
                items = [jpath(p) for p in _glob_files(pat)]
                if items:
                    if note:
                        warn.append(f"OVER: {note}")
                    break
            kind = "glob"
        if len(items) > MAX_ITEMS:
            raise _too_many(len(items), over)
        return Inputs(kind, items, over, base=_common_base(items), warn=warn)
    p = Path(over)
    if p.is_dir():
        kids = _sort([jpath(c) for c in p.iterdir()])
        if len(kids) > MAX_ITEMS:
            raise _too_many(len(kids), over)
        return Inputs("dir", kids, over, base=jpath(p))
    if p.is_file():
        return Inputs("file", [jpath(p)], over)
    split = _img_split(over)
    if split is not None:  # one IMG entry: models/gta3.img/infernus.dff
        items, warn = _img_entries(split[0], split[1], profile)
        if items:
            return Inputs("img", items, over, warn=warn)
    parts = [x.strip() for x in over.split(",") if x.strip()]
    if len(parts) == 1 and not _SID.match(parts[0]) and ("/" in parts[0] or "\\" in parts[0]
                                                       or re.search(r"\.[A-Za-z0-9]{1,4}$", parts[0])):
        raise SatkError("NOT_FOUND", f"--over: no such file or folder: {over}",
                        hint="a glob needs * or ? (mods/*.dff); a list file needs @ (@inputs.txt)")
    return Inputs("items", parts, over)
