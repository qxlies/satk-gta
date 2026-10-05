"""Names and locations of RH_Scoped*Install sites absent from docs/hooks.json.

These declarations are additional start candidates, not proof of function bounds: some
installs point inside another function. Comments, strings and macro definitions are ignored.
No C++ is executed or copied into the database.
"""

from __future__ import annotations

import json
import re

from ..cpp import ConstEval, LineIndex, ScopeMap, iter_calls, norm_type, split_top
from . import FuncSym

_CONTEXTS = {"RH_ScopedClass", "RH_ScopedVirtualClass", "RH_ScopedNamespace"}
_CALLS = (r"RH_Scoped(?:Class|VirtualClass|Namespace|Install|GlobalInstall|OverloadedInstall|"
          r"GlobalOverloadedInstall|NamedInstall|NamedGlobalInstall|VMTInstall|VMTOverloadedInstall|"
          r"NamedVMTInstall|ConstructorInstall|DestructorInstall|VMTDestructorInstall)")


def _literal(text: str) -> str | None:
    try:
        value = json.loads(text.strip())
    except (ValueError, TypeError):
        return None
    return value if isinstance(value, str) else None


def scan_installs(text: str, blanked: str, path: str, ev: ConstEval) -> list[FuncSym]:
    """Read already-blanked source; preserve real line numbers and lexical C++ scopes."""
    calls = list(iter_calls(blanked, _CALLS))
    if not calls:
        return []
    scopes = ScopeMap(blanked)
    positions = [m.start() for m, _a, _b in calls]
    functions = scopes.qualifiers(positions)
    namespaces = scopes.qualifiers(positions, functions=False)
    owners: dict[str, str] = {}
    lines = LineIndex(text)
    out = []
    for (match, a0, a1), function, namespace in zip(calls, functions, namespaces):
        macro = match.group(1)
        args = split_top(blanked[a0:a1])
        raw = split_top(text[a0:a1])
        if not args:
            continue
        if macro in _CONTEXTS:
            owners[function] = norm_type(args[0])
            continue
        owner = owners.get(function, function.rsplit("::", 1)[0] if "::" in function else namespace)
        ctor = macro == "RH_ScopedConstructorInstall"
        dtor = "Destructor" in macro
        named = "Named" in macro
        overloaded = "Overloaded" in macro
        addr_index = 0 if ctor or dtor else (2 if named or overloaded else 1)
        if len(args) <= addr_index:
            continue
        addr = ev.eval(args[addr_index])
        if not isinstance(addr, int) or not 0 < addr < (1 << 32):
            continue
        if "Global" in macro:
            owner = namespace
        if ctor or dtor:
            if not owner:
                continue
            name = ("~" if dtor else "") + owner.rsplit("::", 1)[-1].split("<", 1)[0]
        else:
            name = norm_type(args[0])
            if named and len(raw) > 1:
                name = _literal(raw[1]) or name
        if (ctor or overloaded) and len(raw) > 1:
            suffix = _literal(raw[1])
            if suffix:
                name += "-" + suffix
        if not name:
            continue
        qual = name if "::" in name or not owner else f"{owner}::{name}"
        cls, _, short = qual.rpartition("::")
        options = blanked[a0:a1]
        reversed_opt = re.search(r"\.Reversed\s*=\s*(true|false)\b", options)
        locked_opt = re.search(r"\.Locked\s*=\s*(true|false)\b", options)
        out.append(FuncSym(addr, qual, cls or None, short or qual, "gta_reversed",
                           src_file=path, src_line=lines.line(match.start()),
                           reversed=reversed_opt.group(1) == "true" if reversed_opt else None,
                           locked=locked_opt.group(1) == "true" if locked_opt else None, how=macro))
    return out
