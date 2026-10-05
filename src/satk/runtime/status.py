"""``satk status`` / MCP ``satk_status``: one cheap overview of the workspace (SPEC §4.7 #2).

Sections come from :func:`satk.core.registry.status_provider` registrations of every package
(``game`` WP-01, ``index`` WP-03, ``viewer`` WP-07, ``re`` WP-09, ``blender`` WP-10, ``engine``
WP-11, ``notes`` WP-06 ...). For the well-known sections whose package is not merged yet (or
failed to import) a *basic* section is computed here from files on disk and marked
``"basic": true``, so the first call of a session is useful from day one.

A provider may put ``"warn": [...]`` into its section; those lines are hoisted to the
top-level ``warn`` (prefixed with the section name). A provider that raises becomes
``{"error": {...}}`` plus a warning; it never breaks the whole status. When a ``game`` or
``index`` section brings no warning of its own, the essential ones are derived from its fields
(``GAME_MISSING``/``GAME_BAD`` for ``ok:false``, ``INDEX_MISSING``/``INDEX_STALE`` for the
vanilla profile), so the first call of a session always says what blocks the tools.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any, Callable

from satk import __version__
from satk.core.config import REPO_ROOT
from satk.core.errors import SatkError
from satk.core.log import record_exception
from satk.core.paths import cfg, jpath
from satk.core.registry import import_errors, status_providers

from .sysinfo import file_version

__all__ = ["collect", "KNOWN_SECTIONS", "basic_section"]

#: Sections shown in this order (others follow alphabetically).
KNOWN_SECTIONS = ("game", "index", "viewer", "re", "blender", "engine", "notes")
_SLOW_MS = 1000


def _git_head() -> tuple[str | None, str | None]:
    try:
        from satk.core.ops import _git_head as gh  # WP-00 helper (reads .git without a subprocess)

        return gh(REPO_ROOT)
    except Exception:  # noqa: BLE001
        return None, None


def _mb(p: Path) -> float:
    return round(p.stat().st_size / 2**20, 1)


# --------------------------------------------------------------------------- basic sections


def _basic_game(deep: bool) -> tuple[dict, list[str]]:
    root = Path(cfg().paths.game)
    ok = (root / "gta_sa.exe").is_file()
    sec: dict[str, Any] = {"root": jpath(root), "ok": ok, "exe": "gta_sa.exe" if ok else None}
    return sec, ([] if ok else [f"GAME_MISSING: no gta_sa.exe in {jpath(root)}"])


def _basic_index(deep: bool) -> tuple[dict, list[str]]:
    c = cfg()
    d = Path(c.paths.work) / "index"
    sec: dict[str, Any] = {}
    warn: list[str] = []
    default = c.default_profile
    for prof in c.profiles:
        f = d / f"{prof}.sqlite"
        if f.is_file():
            sec[prof] = {"built": True, "mb": _mb(f)}
        elif prof == default:
            sec[prof] = {"built": False}
            warn.append(f"INDEX_MISSING: profile {prof} not built (satk index build)")
    return sec, warn


def _basic_viewer(deep: bool) -> tuple[dict, list[str]]:
    exe = cfg().paths.get("viewer")
    built = bool(exe and Path(exe).is_file())
    return {"ariane": {"built": built, "exe": jpath(exe) if built else None}}, []


def _basic_re(deep: bool) -> tuple[dict, list[str]]:
    f = Path(cfg().paths.work) / "re" / "symdb.sqlite"
    return ({"built": True, "mb": _mb(f)} if f.is_file() else {"built": False}), []


def _basic_blender(deep: bool) -> tuple[dict, list[str]]:
    exe = cfg().paths.get("blender")
    if not exe or not Path(exe).is_file():
        return {"ok": False}, []
    return {"ok": True, "version": file_version(exe)}, []


def _basic_engine(deep: bool) -> tuple[dict, list[str]]:
    repo = cfg().paths.get("engine")
    if not repo or not Path(repo).is_dir():
        return {"repo": False}, []
    built = (Path(repo) / "Bin" / "server" / "MTA Server64.exe").is_file()
    return {"repo": (Path(repo) / ".git").exists(), "built": built, "path": jpath(repo)}, []


_BASIC: dict[str, Callable[[bool], tuple[dict, list[str]]]] = {
    "game": _basic_game, "index": _basic_index, "viewer": _basic_viewer, "re": _basic_re,
    "blender": _basic_blender, "engine": _basic_engine,
}


# --------------------------------------------------------------------------- derived warnings
# The §4.7 status contract promises warn:[...] for what blocks an agent. Provider sections
# report state (ok:false, built:false, fresh:false) but need not phrase warnings, so the
# essential ones are derived here from those fields when the section brought none itself.


def _warn_game(sec: dict) -> list[str]:
    if sec.get("ok") is not False:
        return []
    root = sec.get("root", "the configured game path")
    if sec.get("error") or not sec.get("exe"):
        return [f"GAME_MISSING: no clean game copy at {root} ({sec.get('error') or 'gta_sa.exe missing'}); "
                "fix: satk game clone"]
    return [f"GAME_BAD: clean copy at {root} has problems (exe {sec.get('exe')}); fix: satk game verify --deep"]


def _warn_index(sec: dict) -> list[str]:
    name = cfg().default_profile
    st = sec.get(name)
    if not isinstance(st, dict):
        return []
    if st.get("built") is False:
        return [f"INDEX_MISSING: profile {name} not built; fix: satk index build"]
    if st.get("built") and st.get("fresh") is False:
        why = "; ".join(map(str, st.get("stale") or [])) or "sources changed"
        return [f"INDEX_STALE: profile {name} ({why}); fix: satk index build"]
    return []


_DERIVED: dict[str, Callable[[dict], list[str]]] = {"game": _warn_game, "index": _warn_index}


def basic_section(name: str, deep: bool = False) -> tuple[dict, list[str]] | None:
    """Fallback section for a well-known package without a status provider (``None`` if unknown)."""
    fn = _BASIC.get(name)
    if fn is None:
        return None
    sec, warn = fn(deep)
    sec["basic"] = True
    return sec, warn


# --------------------------------------------------------------------------- collect


def _order(name: str) -> tuple[int, str]:
    return (KNOWN_SECTIONS.index(name), "") if name in KNOWN_SECTIONS else (len(KNOWN_SECTIONS), name)


def _strip_empty(v: Any) -> Any:
    if isinstance(v, dict):
        out = {}
        for k, x in v.items():
            x = _strip_empty(x)
            if x is None or (isinstance(x, (list, dict)) and not x):
                continue
            out[k] = x
        return out
    if isinstance(v, list):
        return [_strip_empty(x) for x in v]
    return v


def collect(deep: bool = False, sections: list[str] | None = None) -> dict:
    """Status payload: ``{satk:{...}, <section>: {...}, warn: [...]}``."""
    branch, commit = _git_head()
    out: dict[str, Any] = {"satk": {"version": __version__, "branch": branch, "commit": commit,
                                    "workspace": jpath(cfg().paths.workspace)}}
    warn: list[str] = []
    provs = status_providers()
    names = sorted(set(provs) | set(_BASIC), key=_order)
    if sections:
        unknown = [s for s in sections if s not in names]
        if unknown:
            raise SatkError("BAD_PARAMS", f"unknown status section(s): {', '.join(unknown)}", data={"sections": names})
        names = [n for n in names if n in sections]
    for name in names:
        t0 = time.perf_counter()
        fn = provs.get(name)
        sec_warn: list[str] = []
        if fn is None:
            res = basic_section(name, deep)
            if res is None:  # pragma: no cover
                continue
            sec, sec_warn = res
        else:
            try:
                sec = fn(deep)
                if not isinstance(sec, dict):
                    raise TypeError(f"status provider returned {type(sec).__name__}, expected dict")
                sec = dict(sec)
                sec_warn = [f"{name}: {w}" for w in sec.pop("warn", None) or ()]
            except SatkError as e:
                sec = {"error": e.error_dict()}
                sec_warn = [f"{name}: {e.code} {e.msg}"]
            except Exception as e:  # noqa: BLE001 - one broken provider must not hide the others
                where = record_exception(e, context=f"status provider {name}")
                sec = {"error": {"code": "INTERNAL", "msg": f"{type(e).__name__}: {e}",
                                 "hint": f"traceback in {jpath(where)}" if where else None}}
                sec_warn = [f"{name}: INTERNAL {type(e).__name__}"]
        if not sec_warn and name in _DERIVED:
            sec_warn = _DERIVED[name](sec)
        warn.extend(sec_warn)
        ms = (time.perf_counter() - t0) * 1000
        if ms >= _SLOW_MS:
            sec["ms"] = round(ms)
        out[name] = sec
    errs = import_errors()
    if errs:
        warn.append(f"OPS_IMPORT: {len(errs)} package(s) failed to load ({', '.join(sorted(errs))}); see satk doctor")
    if not os.environ.get("PYTHONUTF8") and not sys.flags.utf8_mode:
        warn.append("UTF8: python is not in UTF-8 mode (run via satk.cmd)")
    out = _strip_empty(out)
    if warn:
        out["warn"] = warn
    return out
