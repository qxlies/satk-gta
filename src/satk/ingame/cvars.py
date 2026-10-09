"""Launch-time client cvars: ``coreconfig.xml`` values written before the MTA client starts, with backup and restore.

The fork's client reads its cvars (``fps_limit``, ``vsync``, ``display_windowed``, ``sae_preset``, ``sae_limits``,
``sae_*``) from ``<engine>/Bin/MTA/config/coreconfig.xml`` when it starts, and writes the file again when it exits.
The satk-agent console cannot reach the client's core console commands (``sae_set``, ``sae_preset``), so a run that
needs other values restarts the client with the file edited first. This module owns that file for the run:

* :func:`apply` backs the file up **once** (``<work>/mta/ingame/cvar-backup/``; the state in ``cvars.json``), then
  rebuilds it from the backup plus two layers: ``base`` (``ingame start/play --cvar``, ``--windowed``) and ``run``
  (the bench: ``--preset``, ``--profile``, ``--cvar``). Only ``<key>`` elements of ``<settings>`` are touched; every
  other byte of the file stays. It refuses while ``gta_sa.exe`` runs (the client would overwrite the edit on exit).
* :func:`restore` copies the backup back (or removes the file when there was none) and forgets the state; it also
  refuses while the game runs. ``ingame stop`` closes the client the launcher started and then restores.
* :func:`launch_client` and :func:`relaunch` start the client through :func:`satk.viewer.backends.mta_lua.start_client`
  and record which cvars it was launched with, so a bench with the same values does not restart it again.

Everything stdlib; the process list and the clock are module attributes so tests replace them.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape, unescape

from ..core import paths
from ..core.errors import SatkError

__all__ = ["WINDOWED", "config_path", "parse_pairs", "collect", "bench_pairs", "edit_text", "load_state", "gta_running",
           "guard", "apply", "restore", "status", "launch_client", "relaunch", "close_client", "restore_after_stop"]

#: ``--windowed``: windowed mode (``display_windowed`` 1; ``display_fullscreen_style`` 0 = standard, the value the
#: video settings keep for a window; Client/core/Graphics/CVideoModeManager.cpp reads both at start).
WINDOWED = {"display_windowed": "1", "display_fullscreen_style": "0"}
_KEY = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")
_SKELETON = "<mainconfig>\n    <settings>\n    </settings>\n</mainconfig>\n"
STOP_WAIT_S = 30.0


# --------------------------------------------------------------------------- parsing


def parse_pairs(items: dict[str, str] | list[str] | tuple[str, ...] | None, flag: str = "--cvar") -> dict[str, str]:
    """``key=value`` items (or a dict) as a validated ``{key: value}``; a repeated key keeps the last value."""
    if not items:
        return {}
    pairs: list[tuple[str, str]] = []
    if isinstance(items, dict):
        pairs = [(str(k), str(v)) for k, v in items.items()]
    else:
        for it in items:
            k, sep, v = str(it).partition("=")
            if not sep:
                raise SatkError("BAD_PARAMS", f"{flag} expects key=value, got {it!r}", hint=f"{flag} fps_limit=0")
            pairs.append((k, v))
    out: dict[str, str] = {}
    for k, v in pairs:
        k, v = k.strip(), v.strip()
        if not _KEY.fullmatch(k):
            raise SatkError("BAD_PARAMS", f"{flag}: {k!r} is not a cvar name (letters, digits, underscore)")
        if len(v) > 200 or any(ord(c) < 32 for c in v):
            raise SatkError("BAD_PARAMS", f"{flag} {k}: the value must be a single line of at most 200 characters")
        out[k] = v
    return out


def collect(cvar: list[str] | dict[str, str] | None, windowed: bool = False) -> dict[str, str]:
    """``--windowed`` and ``--cvar`` as one dict (an explicit ``--cvar`` wins over the shortcut)."""
    out = dict(WINDOWED) if windowed else {}
    out.update(parse_pairs(cvar))
    return out


def bench_pairs(preset: str | None, profile: str | None, cvar: list[str] | dict[str, str] | None) -> dict[str, str]:
    """The launch-time cvars of a bench request: ``--preset`` -> ``sae_preset``, ``--profile`` -> ``sae_limits``."""
    out: dict[str, str] = {}
    for key, val in (("sae_preset", preset), ("sae_limits", profile)):
        if val:
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", str(val)):
                raise SatkError("BAD_PARAMS", f"{key} must be a plain word, got {val!r}")
            out[key] = str(val)
    out.update(parse_pairs(cvar))
    return out


# --------------------------------------------------------------------------- the file


def config_path() -> Path:
    """The client's ``coreconfig.xml`` (``<engine>/Bin/MTA/config``)."""
    from ..engine.common import layout

    return layout().bin / "MTA" / "config" / "coreconfig.xml"


def _state_path() -> Path:
    return paths.work("mta", "ingame", "cvars.json")


def _backup_dir() -> Path:
    return paths.work("mta", "ingame", "cvar-backup")


def edit_text(text: str, pairs: dict[str, str]) -> tuple[str, list[dict[str, Any]]]:
    """``text`` with the ``<key>`` elements of ``<settings>`` set to ``pairs``; ``(new text, [{key, was, now}])``.

    An existing element is replaced in place, a missing one is added before ``</settings>``; nothing else changes.
    """
    nl = "\r\n" if "\r\n" in text else "\n"
    if "<settings/>" in text and "<settings>" not in text:
        text = text.replace("<settings/>", "<settings></settings>", 1)
    m = re.search(r"<settings>(.*?)</settings>", text, re.S)
    if not m:
        raise SatkError("EXTERNAL_TOOL", "coreconfig.xml has no <settings> section", hint="satk ingame cvar-restore")
    body = m.group(1)
    changes: list[dict[str, Any]] = []
    for key, val in pairs.items():
        pat = re.compile(rf"<{key}>([^<]*)</{key}>|<{key}\s*/>")
        found = pat.search(body)
        new_el = f"<{key}>{escape(val)}</{key}>" if val != "" else f"<{key}/>"
        if found:
            was = unescape(found.group(1)) if found.group(1) is not None else ""
            body = body[:found.start()] + new_el + body[found.end():]
        else:
            was = None
            tail = body.rstrip(" \t")
            indent = "        "
            if not tail.endswith(("\n", "\r")):
                tail += nl
            body = tail + indent + new_el + nl + "    "
        changes.append({"key": key, "was": was, "now": val})
    return text[:m.start(1)] + body + text[m.end(1):], changes


def _read_cfg(p: Path) -> tuple[str, bool]:
    raw = p.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    return raw[3:].decode("utf-8", errors="replace") if bom else raw.decode("utf-8", errors="replace"), bom


# --------------------------------------------------------------------------- state


def load_state() -> dict[str, Any]:
    try:
        d = json.loads(_state_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(st: dict[str, Any]) -> None:
    paths.atomic_write(_state_path(), json.dumps(st, indent=1, sort_keys=True, ensure_ascii=False) + "\n")


def _total(st: dict[str, Any]) -> dict[str, str]:
    lay = st.get("layers") or {}
    return {**(lay.get("base") or {}), **(lay.get("run") or {})}


def gta_running() -> list[int]:
    """Pids of running ``gta_sa.exe`` (the process that rewrites ``coreconfig.xml``)."""
    from ..viewer.backends import mta_lua

    return mta_lua._running("gta_sa.exe")


def guard(pairs: dict[str, str]) -> None:
    """``NOT_READY`` when ``pairs`` would change the file while the game runs (a repeat of the applied values passes)."""
    if not pairs:
        return
    st = load_state()
    if st and all(_total(st).get(k) == v for k, v in pairs.items()):
        return
    pids = gta_running()
    if pids:
        raise SatkError("NOT_READY", f"gta_sa.exe is running (pid {pids}): the client rewrites coreconfig.xml when it exits, "
                        "so launch-time cvars cannot be changed now",
                        hint="satk ingame stop (closes the client), or close the game, then run this again",
                        data={"gta_sa": pids})


def apply(pairs: dict[str, str], *, layer: str = "base", replace_layer: bool = False) -> dict[str, Any]:
    """Write ``pairs`` into ``coreconfig.xml`` (backup first, once). ``layer``: ``base`` (merged) or ``run``.

    ``replace_layer`` replaces the whole layer instead of merging (the bench sets ``run`` anew every restart; an empty
    ``pairs`` then clears it). Returns ``{config, backup, changed: [{key, was, now}], cvars}``.
    """
    if layer not in ("base", "run"):
        raise ValueError(layer)
    st = load_state()
    cur = dict((st.get("layers") or {}).get(layer) or {})
    new = dict(pairs) if replace_layer else {**cur, **pairs}
    if new == cur and st:
        return {"config": paths.jpath(config_path()), "backup": st.get("backup"), "changed": [], "cvars": _total(st)}
    pids = gta_running()
    if pids:
        raise SatkError("NOT_READY", f"gta_sa.exe is running (pid {pids}): close the game before changing its config",
                        hint="satk ingame stop", data={"gta_sa": pids})
    cfg = config_path()
    if not st:
        st = {"config": paths.jpath(cfg), "existed": cfg.is_file(), "since": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "layers": {"base": {}, "run": {}}}
        if st["existed"]:
            dst = _backup_dir() / f"coreconfig.{time.strftime('%Y%m%d-%H%M%S')}.xml"
            paths.atomic_write(dst, cfg.read_bytes())
            st["backup"] = paths.jpath(dst)
        _save_state(st)  # the backup is recorded before the first edit
    st.setdefault("layers", {"base": {}, "run": {}})[layer] = new
    total = _total(st)
    if st.get("existed"):
        if not Path(st["backup"]).is_file():
            raise SatkError("NOT_FOUND", f"the backup {st['backup']} is gone", hint="restore coreconfig.xml by hand, "
                            "then delete work/mta/ingame/cvars.json")
        text, bom = _read_cfg(Path(st["backup"]))
    else:
        text, bom = _SKELETON, False
    text, changes = edit_text(text, total)
    data = text.encode("utf-8")
    paths.atomic_write(cfg, (b"\xef\xbb\xbf" + data) if bom else data)
    st.pop("launched", None)  # the file changed: the running client (if any) was launched with other values
    _save_state(st)
    return {"config": paths.jpath(cfg), "backup": st.get("backup"), "changed": changes, "cvars": total}


def restore(*, force: bool = False) -> dict[str, Any]:
    """Put the original ``coreconfig.xml`` back (``ingame cvar-restore``); refuses while ``gta_sa.exe`` runs."""
    st = load_state()
    if not st:
        return {"restored": False, "note": "no launch-time cvars are recorded"}
    pids = gta_running()
    if pids and not force:
        raise SatkError("NOT_READY", f"gta_sa.exe is running (pid {pids}): it rewrites coreconfig.xml when it exits",
                        hint="satk ingame stop (closes the client), then satk ingame cvar-restore",
                        data={"gta_sa": pids})
    cfg = config_path()
    if st.get("existed"):
        bak = Path(st["backup"])
        if not bak.is_file():
            raise SatkError("NOT_FOUND", f"the backup {st['backup']} is gone", hint="restore coreconfig.xml by hand")
        paths.atomic_write(cfg, bak.read_bytes())
    else:
        try:
            paths.ensure_writable(cfg).unlink()
        except FileNotFoundError:
            pass
    _state_path().unlink(missing_ok=True)
    out: dict[str, Any] = {"restored": True, "config": paths.jpath(cfg), "keys": sorted(_total(st))}
    if st.get("existed"):
        out["from"] = st["backup"]
    else:
        out["note"] = "there was no coreconfig.xml before: removed"
    return out


def status() -> dict[str, Any] | None:
    """What is recorded (None when no launch-time cvars are in effect)."""
    st = load_state()
    if not st:
        return None
    out: dict[str, Any] = {"config": st.get("config"), "backup": st.get("backup"), "cvars": _total(st),
                           "since": st.get("since")}
    if st.get("launched"):
        out["launched"] = st["launched"]
    return {k: v for k, v in out.items() if v not in (None, {})}


# --------------------------------------------------------------------------- the client


def _session() -> dict[str, Any]:
    from ..saap import client as C

    return C.read_session("game") or {}


def close_client(wait: float = STOP_WAIT_S) -> dict[str, Any]:
    """Close the client the launcher started and wait until ``gta_sa.exe`` is gone.

    A game that satk did not start is never closed: ``NOT_READY``.
    """
    from ..viewer.backends import mta_lua

    sess = _session()
    closed = mta_lua._stop_client(sess) if sess.get("client_pid") else False
    end = time.monotonic() + wait
    while gta_running() and time.monotonic() < end:
        time.sleep(0.5)
    left = gta_running()
    if left:
        raise SatkError("NOT_READY", f"gta_sa.exe (pid {left}) is still running and was not started by satk, or did not exit",
                        hint="close the game, then run this again", data={"gta_sa": left})
    return {"closed": closed}


def _mark_launched(res: dict[str, Any]) -> None:
    st = load_state()
    if not st:
        return
    st["launched"] = {"pid": res.get("client_pid"), "cvars": _total(st), "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    _save_state(st)


def launch_client(pairs: dict[str, str] | None = None, *, timeout: float = 180.0) -> dict[str, Any]:
    """Write ``pairs`` into the ``base`` layer (if any), start the client and record what it was launched with."""
    from ..viewer.backends import mta_lua

    cv = None
    if pairs:
        cv = apply(pairs, layer="base")
    res = mta_lua.start_client(timeout=timeout)
    _mark_launched(res)
    if cv and cv["changed"]:
        res = {**res, "cvars": cv["cvars"]}
    return res


def relaunch(run: dict[str, str], *, joined: bool, timeout: float = 180.0) -> dict[str, Any]:
    """Make the running client carry the ``run`` cvars on top of the ``base`` ones: restart it when it does not.

    ``{restarted: False}`` when the client already was launched with exactly these values (or nothing is recorded
    and ``run`` is empty). Otherwise the client satk started is closed, the file is rewritten and a new client
    joins the same server. A client satk did not start is not touched (``NOT_READY``). Costs one client start
    (about a minute) per distinct set of values.
    """
    from ..viewer.backends import mta_lua

    st = load_state()
    want = {**((st.get("layers") or {}).get("base") or {}), **run}
    launched = (st.get("launched") or {}).get("cvars")
    if joined and (launched == want if launched is not None else not run and not (st.get("layers") or {}).get("run")):
        return {"restarted": False, "cvars": want}
    sess = _session()
    if not sess.get("client_pid"):
        raise SatkError("NOT_READY", "launch-time cvars need a client that satk started (satk ingame play); the running "
                        "game was not started by satk" if joined else "no client has been started by satk yet",
                        hint="satk ingame play [--cvar ...], then run the bench again")
    close_client()
    cv = apply(run, layer="run", replace_layer=True)
    t0 = time.monotonic()
    res = mta_lua.start_client(timeout=timeout)
    _mark_launched(res)
    return {"restarted": True, "cvars": cv["cvars"], "changed": cv["changed"],
            "seconds": round(time.monotonic() - t0, 1)}


def restore_after_stop(wait: float = 20.0) -> dict[str, Any] | None:
    """After ``ingame stop``: wait for the client to exit, then restore; a note instead of an error when it cannot."""
    if not load_state():
        return None
    end = time.monotonic() + wait
    while gta_running() and time.monotonic() < end:
        time.sleep(0.5)
    try:
        return restore()
    except SatkError as e:
        return {"restored": False, "note": f"{e.code}: {e.msg}", "hint": "satk ingame cvar-restore"}
