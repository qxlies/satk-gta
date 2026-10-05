"""The people's guide: ``satk``, ``satk -h`` and ``satk help`` on the command line (M3 A2).

One screen of "I want to..." lines grouped by task, without the engine, dev and ``game clone``
commands (those are in ``satk help --all``). Items whose operation is not registered (package not
merged or failed to import) are left out, so the guide never names a missing command.

English texts live here; the Russian texts are in ``guide.ru.toml`` next to this file (one language
per file). ``satk help --ru`` falls back to English when that file is missing.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from satk.core.registry import GROUPS, all_ops, get_op

__all__ = ["GUIDE", "EN", "HIDDEN_GROUPS", "HIDDEN_OPS", "MAX_LINES", "MAX_WIDTH", "texts", "render", "full_rows"]

#: (group id, ((item id, operation, command shown), ...)).
GUIDE: tuple[tuple[str, tuple[tuple[str, str, str], ...]], ...] = (
    ("start", (
        ("init", "init", "satk init"),
        ("doctor", "doctor", "satk doctor"),
        ("index", "index.build", "satk index build"),
    )),
    ("look", (
        ("find", "asset.find", "satk asset find infernus"),
        ("texture", "texture.image", "satk texture image tex:infernus/infernus92wheel32"),
        ("model", "model.image", "satk model image model:411"),
        ("map", "map.image", "satk map image 2495 -1687"),
    )),
    ("mod", (
        ("lint", "asset.lint", "satk asset lint <mod>"),
        ("retex", "texture.replace", "satk texture replace <txd> <tex>=<png>"),
        ("export", "asset.export", "satk asset export model:411 --format glb"),
        ("diff", "index.diff", "satk index diff vanilla installed"),
    )),
    ("fix", (
        ("crash", "crash.analyze", "satk crash analyze <crash.dmp>"),
        ("report", "bug_report", "satk bug-report"),
    )),
    ("ai", (
        ("mcp", "mcp.config", "satk mcp config"),
    )),
)

#: Help groups and operations that the guide never shows (developer and engine work).
HIDDEN_GROUPS = frozenset({"engine", "dev"})
HIDDEN_OPS = frozenset({"game.clone"})
#: One screen: at most this many lines, each at most this wide.
MAX_LINES = 25
MAX_WIDTH = 100

EN: dict = {
    "title": "satk: a toolkit for GTA San Andreas mods. It only reads your game and works offline.",
    "want": "I want to...",
    "more": "More: satk help <command> | satk help --all (every command) | satk help --ru (in Russian)",
    "groups": {"start": "start", "look": "look", "mod": "mod", "fix": "fix", "ai": "AI"},
    "items": {
        "init": "set satk up for my game",
        "doctor": "check that everything works",
        "index": "index my game (once, a few minutes)",
        "find": "find a model, texture or file",
        "texture": "see a texture",
        "model": "see a model",
        "map": "see a part of the map",
        "lint": "check my mod for errors",
        "retex": "replace textures in a TXD",
        "export": "export a model (glb, obj)",
        "diff": "compare my game with the stock one",
        "crash": "find out why the game crashed",
        "report": "report a problem with satk",
        "mcp": "connect an AI assistant",
    },
}

_RU_FILE = Path(__file__).with_name("guide.ru.toml")


def texts(ru: bool = False) -> dict:
    """Texts of the guide: English, or the Russian file (English for anything it lacks)."""
    if not ru:
        return EN
    try:
        data = tomllib.loads(_RU_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return EN
    out = {**EN, **{k: v for k, v in data.items() if isinstance(v, str)}}
    for k in ("groups", "items"):
        out[k] = {**EN[k], **(data.get(k) or {})}
    return out


def _present(op_name: str) -> bool:
    try:
        return get_op(op_name) is not None
    except Exception:  # noqa: BLE001 - an unknown operation is simply not shown
        return False


def render(ru: bool = False) -> dict:
    """``{"topic": "guide", "text", "items": [[group, command], ...]}`` for the registered operations."""
    t = texts(ru)
    rows: list[tuple[str, str, str]] = []
    for gid, items in GUIDE:
        first = True
        for iid, op_name, cmd in items:
            if not _present(op_name):
                continue
            rows.append((t["groups"][gid] if first else "", t["items"][iid], cmd))
            first = False
    gw = max((len(g) for g, _, _ in rows), default=0)
    dw = max((len(d) for _, d, _ in rows), default=0) + 2
    lines = [t["title"], "", t["want"]]
    for g, d, cmd in rows:
        lines.append(f"  {g.ljust(gw)}  {(d + ' ').ljust(dw, '.')} {cmd}".rstrip())
    lines += ["", t["more"]]
    return {"topic": "guide", "text": "\n".join(lines), "items": [[c, d] for _, d, c in rows]}


def full_rows(ru: bool = False) -> tuple[list[str], list[list]]:
    """Every operation: ``(cols, rows)`` ``group | command | mcp | summary`` (``summary_ru`` with ``ru``)."""
    ops = sorted(all_ops(), key=lambda o: (GROUPS.index(o.group) if o.group in GROUPS else 99, o.name))
    from satk.mcp.adapter import tool_name

    rows = [[o.group, f"satk {o.cli}", tool_name(o) or "", o.summary_ru if ru else o.summary] for o in ops]
    return ["group", "command", "mcp", "summary_ru" if ru else "summary"], rows
