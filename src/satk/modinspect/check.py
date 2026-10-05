"""``mod check``: problems of a mod, from satk's own rules or from an external INU Check.

``engine="satk"`` (default) turns the inspector's findings into issues (ID collisions, files Mod
Loader will not load, game records a data file drops, IDs above the vanilla limit, new models
without an IDE line) and adds ``asset lint`` findings for folders, IMGs and single files.

``engine="inu"`` runs INU Check, an optional external program (GPL-3.0) the user installs
themselves; satk never downloads, bundles or links it. It is found from ``--exe``, the
``SATK_INU_CHECK`` environment variable, ``[tools] inu_check`` in ``satk.toml`` or ``PATH``.
Its command line comes from ``SATK_INU_CHECK_ARGS`` / ``[tools] inu_check_args`` (default
``--json {path}``). The adapter is deliberately thin and versioned (:data:`ADAPTER`): it accepts a
JSON list of issues or an object holding one (``issues``/``results``/``findings``/``errors``), reads
the usual field names (``file|path|asset``, ``severity|level|type``, ``message|msg|text``,
``rule|code|id|check``) and maps file names to SIDs; anything else falls back to
``SEVERITY: file: message`` text lines with an ``INU_FORMAT`` warning.

Stdlib only (lint is imported lazily).
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

from ..core.errors import SatkError
from ..core.paths import cfg, jpath

__all__ = ["ADAPTER", "COLS", "find_inu", "run_inu", "parse_inu", "satk_issues", "SEV_ORDER"]

ADAPTER = "inu-adapter/1"
COLS = ["rule", "sev", "file", "msg", "sid"]
SEV_ORDER = {"fatal": 3, "error": 2, "warn": 1, "info": 0}
_EXE_NAMES = ("inu_check", "inucheck", "inu-check", "INUCheck", "InuCheck")
_SEV = {"fatal": "fatal", "critical": "fatal", "error": "error", "err": "error", "severe": "error",
        "warning": "warn", "warn": "warn", "info": "info", "information": "info", "notice": "info",
        "hint": "info", "note": "info"}
_TEXT_LINE = re.compile(r"^\s*\[?(fatal|critical|error|err|warning|warn|info|note|hint)\]?\s*[:\-]?\s*"
                        r"(?:(?P<file>[^\s:]+\.[A-Za-z0-9]{2,4})\s*[:\-]\s*)?(?P<msg>.+)$", re.I)


def _sid_of(file: str) -> str | None:
    name = file.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if "." not in name:
        return None
    stem, ext = name.rsplit(".", 1)
    if ext in ("dff", "txd", "col"):
        return f"{ext}:{stem}"
    return f"file:{name}"


def find_inu(exe: str | None = None) -> list[str]:
    """Command prefix of INU Check; ``NOT_READY`` with a hint when it is not installed."""
    raw = exe or os.environ.get("SATK_INU_CHECK") or cfg().get("tools.inu_check")
    if raw:
        parts = shlex.split(str(raw), posix=False)
        parts = [p.strip('"') for p in parts]
        if parts and (Path(parts[0]).is_file() or shutil.which(parts[0])):
            return parts
        raise SatkError("NOT_FOUND", f"INU Check not found at {raw!r}",
                        hint="point --exe / SATK_INU_CHECK / [tools] inu_check in satk.toml at the installed program")
    for n in _EXE_NAMES:
        hit = shutil.which(n)
        if hit:
            return [hit]
    raise SatkError("NOT_READY", "INU Check is not installed (it is optional; satk never downloads it)",
                    hint="install INU Check yourself, then set SATK_INU_CHECK or [tools] inu_check in satk.toml "
                         "| satk mod check <path> (satk's own checks)")


def _norm_item(it) -> list | None:
    if isinstance(it, str):
        m = _TEXT_LINE.match(it)
        if not m:
            return ["inu", "info", "", it.strip(), None]
        return ["inu", _SEV.get(m.group(1).lower(), "warn"), m.group("file") or "", m.group("msg").strip(),
                _sid_of(m.group("file") or "")]
    if not isinstance(it, dict):
        return None
    low = {str(k).lower(): v for k, v in it.items()}

    def pick(*names):
        for n in names:
            v = low.get(n)
            if v not in (None, ""):
                return v
        return None

    file = str(pick("file", "path", "asset", "target", "filename") or "")
    sev = _SEV.get(str(pick("severity", "level", "type", "sev") or "warn").lower(), "warn")
    msg = str(pick("message", "msg", "text", "description", "detail") or "")
    rule = str(pick("rule", "code", "id", "check", "name") or "inu")
    return [f"inu.{rule}" if not rule.startswith("inu") else rule, sev, file, msg, _sid_of(file) if file else None]


def parse_inu(out: str) -> tuple[list[list], str, list[str]]:
    """``(rows, format, warnings)`` from INU Check's stdout."""
    s = out.strip()
    start = min([i for i in (s.find("{"), s.find("[")) if i >= 0], default=-1)
    if start >= 0:
        try:
            doc = json.loads(s[start:])
        except ValueError:
            doc = None
        if doc is not None:
            fmt = "json"
            items = doc
            if isinstance(doc, dict):
                ver = doc.get("version") or doc.get("schema") or doc.get("format")
                fmt = f"json v{ver}" if ver else "json"
                items = next((doc[k] for k in ("issues", "results", "findings", "errors", "messages", "problems")
                              if isinstance(doc.get(k), list)), None)
                if items is None:
                    return [], fmt, ["INU_FORMAT: JSON object without an issue list; see the raw output with --json"]
            if not isinstance(items, list):
                return [], fmt, ["INU_FORMAT: unexpected JSON shape"]
            rows = [r for r in (_norm_item(i) for i in items) if r is not None]
            return rows, fmt, []
    rows = [r for r in (_norm_item(ln) for ln in s.splitlines() if ln.strip()) if r is not None]
    return rows, "text", ["INU_FORMAT: no JSON in INU Check's output; parsed as text lines"] if rows else []


def run_inu(path: Path, exe: str | None = None, timeout: float = 300.0) -> tuple[list[list], dict, list[str]]:
    """Run INU Check on ``path``: ``(rows, info, warnings)``."""
    cmd = find_inu(exe)
    tmpl = os.environ.get("SATK_INU_CHECK_ARGS") or cfg().get("tools.inu_check_args") or "--json {path}"
    args = [a.replace("{path}", str(path)) for a in shlex.split(str(tmpl), posix=False)]
    args = [a.strip('"') for a in args]
    try:
        cp = subprocess.run(cmd + args, capture_output=True, timeout=timeout, check=False,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        raise SatkError("TIMEOUT", f"INU Check did not finish in {timeout:g} s", hint="--timeout 900") from None
    except OSError as e:
        raise SatkError("EXTERNAL_TOOL", f"INU Check could not start: {e}") from None
    out = cp.stdout.decode("utf-8", errors="replace")
    rows, fmt, warn = parse_inu(out)
    if cp.returncode != 0 and not rows:
        err = cp.stderr.decode("utf-8", errors="replace").strip()
        raise SatkError("EXTERNAL_TOOL", f"INU Check exited with {cp.returncode}: {err[-300:] or out[-300:]}",
                        hint="check SATK_INU_CHECK_ARGS / [tools] inu_check_args (the command line it expects)")
    info = {"adapter": ADAPTER, "exe": jpath(cmd[0]), "format": fmt, "exit": cp.returncode}
    return rows, info, warn


# --------------------------------------------------------------------------- satk's own checks


def satk_issues(rows: list[list], summary: dict, world) -> list[list]:
    """Issues from ``inspect`` rows (``kind, target, change, by, detail``)."""
    out: list[list] = []
    removed: dict[str, int] = {}
    for kind, target, change, by, detail in rows:
        if change == "override-id":
            out.append(["mod.id_collision", "warn", by, f"{target}: {detail}", target])
        elif change == "ignored" and kind in ("ide", "ipl", "zon", "data", "asi", "img", "nodes"):
            out.append(["mod.not_loaded", "warn", by, f"{target}: {detail}", target])
        elif change == "ignored" and kind not in ("other",):
            out.append(["mod.ignored", "info", by, f"{target}: {detail}", target])
        elif change == "remove":
            removed[by] = removed.get(by, 0) + 1
        elif change == "new-id" and "limit adjuster" in detail:
            out.append(["mod.id_limit", "warn", by, f"{target}: {detail}", target])
        elif change == "add" and kind == "dff" and "no IDE line" in detail:
            out.append(["mod.unused_model", "info", by, f"{target}: {detail}", target])
    for by, n in removed.items():
        out.append(["mod.drops_records", "warn", by,
                    f"{n} game record(s) are missing from this file; with Mod Loader they disappear from the game",
                    None])
    out.sort(key=lambda r: (-SEV_ORDER[r[1]], r[2].lower(), r[0]))
    return out


def lint_issues(path: Path, kind: str, profile: str) -> tuple[list[list], list[str]]:
    """``asset lint`` findings for a folder, IMG or single file (zips are not linted)."""
    if kind == "zip":
        return [], ["LINT: zip archives are not linted (extract the mod to lint its DFF/TXD/COL files)"]
    from ..lint.runner import lint

    try:
        rep = lint(str(path), profile=profile)
    except SatkError as e:
        return [], [f"LINT: {e.code}: {e.msg}"]
    rows = [[f.rule, f.sev, f.file, f.msg, _sid_of(f.file)] for f in rep.findings]
    return rows, list(rep.warn or [])
