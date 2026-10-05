"""gta-reversed ``docs/hooks.json`` -> named function starts (SPEC §4.9.1; owner WP-09).

Each entry: ``AddressGTA`` (hex string), ``Category`` (class/namespace), ``Name`` (may carry an
overload suffix such as ``RemoveMember-ByPed``), ``InstallSourceLocation`` (``game_sa/Door.cpp:32``,
relative to ``source/``), ``IsReversed``, ``State``, ``IsLocked``.
"""

from __future__ import annotations

import json

from ...core.errors import SatkError
from ..gitsrc import SourceTree
from . import FuncSym

__all__ = ["HOOKS_PATH", "parse_hooks", "load_hooks"]

HOOKS_PATH = "docs/hooks.json"


def parse_hooks(text: str) -> list[FuncSym]:
    """Parse the JSON text of hooks.json (sorted by address, duplicates: first wins)."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise SatkError("BAD_PARAMS", f"hooks.json is not valid JSON: {e}") from None
    if not isinstance(data, list):
        raise SatkError("BAD_PARAMS", "hooks.json: expected a JSON list")
    out: dict[int, FuncSym] = {}
    for e in data:
        try:
            addr = int(str(e["AddressGTA"]), 16)
        except (KeyError, ValueError, TypeError):
            continue
        cat = str(e.get("Category") or "").strip()
        name = str(e.get("Name") or "").strip()
        if not name:
            continue
        qual = f"{cat}::{name}" if cat else name
        loc = str(e.get("InstallSourceLocation") or "")
        f, _, ln = loc.rpartition(":")
        src_file = f or None
        try:
            src_line = int(ln) if f else None
        except ValueError:
            src_file, src_line = loc or None, None
        out.setdefault(addr, FuncSym(
            addr=addr, qual=qual, cls=cat or None, name=name, origin="hooks_json",
            src_file=src_file, src_line=src_line,
            reversed=bool(e.get("IsReversed")) if "IsReversed" in e else None,
            hook_state=e.get("State"), locked=bool(e.get("IsLocked")) if "IsLocked" in e else None,
            category=cat or None,
        ))
    return [out[a] for a in sorted(out)]


def load_hooks(tree: SourceTree, path: str = HOOKS_PATH) -> tuple[list[FuncSym], int]:
    """``(functions, raw_entry_count)`` from ``tree``; ``NOT_FOUND`` if the file is absent."""
    text = tree.read(path)
    if text is None:
        raise SatkError("NOT_FOUND", f"{path} not found in {tree.label}",
                        hint="check paths.src (gta-reversed clone)")
    try:
        raw_n = len(json.loads(text))
    except (json.JSONDecodeError, TypeError):
        raw_n = 0
    return parse_hooks(text), raw_n
