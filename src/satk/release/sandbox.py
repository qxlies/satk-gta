"""Windows Sandbox check of a release: a clean Windows, no network, the game copy read-only.

:func:`prepare_and_run` writes into ``<out>/sandbox/``:

* ``check.ps1`` (``packaging/sandbox/check.ps1``): unpacks the zip into ``C:\\Users\\Public\\<Cyrillic> satk``,
  runs ``satk version``, ``doctor``, ``init --game``, ``index build``, ``asset find`` and
  ``mcp selftest`` and writes ``results/result.json``;
* ``satk-check.wsb``: double-click it to run the same check by hand (one manual action).

With ``run=True`` it also starts the sandbox through the ``wsb`` command line (Windows 11 24H2+),
executes the script, waits for ``result.json`` and stops the sandbox. The host folders are mapped
read-only, except ``results``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable
from xml.sax.saxutils import escape

from ..core.errors import SatkError
from ..core.paths import atomic_write, ensure_removable, ensure_writable, jpath

__all__ = ["SCRIPT", "RELEASE_DIR", "GAME_DIR", "RESULTS_DIR", "wsb_config", "available", "prepare_and_run"]

#: The in-sandbox script, relative to the repository root.
SCRIPT = "packaging/sandbox/check.ps1"
#: Folders as the sandbox sees them.
RELEASE_DIR = r"C:\satk-release"
GAME_DIR = r"C:\game"
RESULTS_DIR = r"C:\satk-results"
_COMMAND = (r"powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\satk-release\sandbox\check.ps1 "
            rf"-Release {RELEASE_DIR} -Game {GAME_DIR} -Results {RESULTS_DIR}")


def wsb_config(release: Path, game: Path, results: Path, *, logon: bool = True) -> str:
    """Windows Sandbox configuration (XML): networking off, three mapped folders, 4 GB RAM."""
    def mapped(host: Path, inside: str, ro: bool) -> str:
        return (f"<MappedFolder><HostFolder>{escape(str(host))}</HostFolder><SandboxFolder>{inside}</SandboxFolder>"
                f"<ReadOnly>{'true' if ro else 'false'}</ReadOnly></MappedFolder>")

    logon_xml = f"<LogonCommand><Command>{escape(_COMMAND)}</Command></LogonCommand>" if logon else ""
    return ("<Configuration><Networking>Disable</Networking><MemoryInMB>4096</MemoryInMB><MappedFolders>"
            + mapped(release, RELEASE_DIR, True) + mapped(game, GAME_DIR, True) + mapped(results, RESULTS_DIR, False)
            + f"</MappedFolders>{logon_xml}</Configuration>")


def available() -> str | None:
    """Path of ``wsb.exe`` when the Windows Sandbox command line is installed, else ``None``."""
    if os.name != "nt":
        return None
    return shutil.which("wsb")


def _wsb(*args: str, timeout: int = 300) -> dict:
    p = subprocess.run(["wsb", *args, "--raw"], capture_output=True, timeout=timeout)
    out = p.stdout.decode("utf-8", "replace").strip()
    if p.returncode != 0:
        raise SatkError("EXTERNAL_TOOL", f"wsb {args[0]} failed (exit {p.returncode}): "
                        f"{(out or p.stderr.decode('utf-8', 'replace')).strip()[:300]}",
                        hint="enable the Windows feature 'Windows Sandbox', or double-click the .wsb file")
    try:
        return json.loads(out) if out else {}
    except json.JSONDecodeError:
        return {"text": out}


def prepare_and_run(zip_path: Path, game: Path, where: Path, script: bytes, *, run: bool, timeout: int = 1200,
                    log: Callable[[str], None] = lambda s: None) -> dict:
    """Write the check files into ``where`` (= ``<out>/sandbox``); with ``run`` execute them in a sandbox.

    ``script`` is :data:`SCRIPT` of the released ref.
    """
    release = zip_path.parent
    if where.parent != release:
        raise SatkError("BAD_PARAMS", "the sandbox folder must be inside the release folder")
    if where.exists():
        ensure_removable(where)
        shutil.rmtree(where)
    ensure_writable(where)
    results = where / "results"
    results.mkdir(parents=True)
    atomic_write(where / "check.ps1", script)
    wsb = where / "satk-check.wsb"
    atomic_write(wsb, wsb_config(release, game, results))
    out: dict = {"wsb": jpath(wsb), "results": jpath(results / "result.json")}
    if not run:
        out["next"] = f"double-click {jpath(wsb)} and wait for {jpath(results / 'result.json')}"
        return out
    if available() is None:
        raise SatkError("NOT_READY", "Windows Sandbox command line (wsb) not found",
                        hint=f"enable 'Windows Sandbox' in Windows features, or double-click {jpath(wsb)}")
    t0 = time.perf_counter()
    started = _wsb("start", "--config", wsb_config(release, game, results, logon=False))
    sid = started.get("Id") or started.get("id")
    if not sid:
        raise SatkError("EXTERNAL_TOOL", f"wsb start returned no id: {started}")
    log(f"sandbox {sid} started")
    try:
        _wsb("exec", "--id", sid, "-c", _COMMAND, "-r", "System", timeout=timeout)
        res_file = results / "result.json"
        deadline = time.monotonic() + timeout
        while not res_file.is_file() and time.monotonic() < deadline:
            time.sleep(2)
        if not res_file.is_file():
            raise SatkError("TIMEOUT", f"no result from the sandbox after {timeout} s", data=out)
        res = json.loads(res_file.read_text(encoding="utf-8-sig"))
    finally:
        try:
            _wsb("stop", "--id", sid, timeout=120)
        except (SatkError, subprocess.TimeoutExpired):
            log(f"sandbox {sid}: stop failed")
    out.update(seconds=round(time.perf_counter() - t0, 1), result=res)
    if not res.get("ok"):
        raise SatkError("CHECK_FAILED", "the sandbox check failed", data=out)
    if not res.get("mcp_ok"):
        if res.get("smart_app_control") != 1:
            raise SatkError("CHECK_FAILED", "the MCP self-test failed in the sandbox", data=out)
        out["warn"] = ["SAC_BLOCKED: Smart App Control is on in the sandbox and blocks the unsigned extension "
                       "modules of the wheels; the MCP self-test could not run (index and search passed)"]
    return out
