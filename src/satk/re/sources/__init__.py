"""Symbol sources for the sa-re symbol DB (SPEC §4.9.1; owner WP-09).

Each module scans one source and returns plain records; :mod:`satk.re.build` merges them:

* :mod:`.hooks`      — gta-reversed ``docs/hooks.json`` (named function starts);
* :mod:`.gtarev`     — gta-reversed sources: ``StaticRef`` globals, vtables, ``VALIDATE_SIZE``,
  constants, pools/stores/id ranges;
* :mod:`.pluginsdk`  — plugin-sdk ``ADDRESS_BY_VERSION`` names, RW wrappers, globals, ``RefList`` calls;
* :mod:`.exe`        — gta_sa.exe: sections, thunks, call targets, padding/data-pointer starts, vtable slots;
* :mod:`.mta`        — MTA ``HOOKPOS_*`` / ``HookInstall*`` / ``Mem*`` sites and Neon relocation manifests.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["FuncSym", "GlobalSym", "VtableSym", "StructSize", "PatchSite", "CallSite", "LimitDef"]


@dataclass(slots=True)
class FuncSym:
    addr: int
    qual: str
    cls: str | None
    name: str
    origin: str                      # hooks_json | plugin_sdk | gta_reversed
    src_file: str | None = None
    src_line: int | None = None
    reversed: bool | None = None
    hook_state: str | None = None
    locked: bool | None = None
    category: str | None = None
    how: str | None = None           # plugin-sdk: addrof | meta | wrapper (statistics only)


@dataclass(slots=True)
class GlobalSym:
    addr: int
    name: str
    type: str | None
    elem_type: str | None
    array_len: int | None
    elem_size: int | None
    byte_size: int | None
    origin: str                      # gta_reversed | plugin_sdk
    src_file: str | None = None
    src_line: int | None = None


@dataclass(slots=True)
class VtableSym:
    addr: int
    cls: str
    slots: int
    src_file: str | None = None
    src_line: int | None = None


@dataclass(slots=True)
class StructSize:
    name: str
    size: int
    src_file: str | None = None
    src_line: int | None = None


@dataclass(slots=True)
class PatchSite:
    addr: int
    kind: str                        # hookpos | hookinstall | hookinstallcall | memput | memset | memcpy | ...
    src_file: str
    src_line: int
    len: int | None = None
    symbol: str | None = None
    note: str | None = None


@dataclass(slots=True)
class CallSite:
    site: int
    callee: int
    caller: int | None
    kind: str                        # call | jump | ...
    origin: str                      # plugin_sdk


@dataclass(slots=True)
class LimitDef:
    name: str
    kind: str                        # pool | array | id_range | streaming | store
    vanilla: int
    global_addr: int | None
    source: str | None
