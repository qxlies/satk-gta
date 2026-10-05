"""Install the agent skill ``satk`` into AI clients (``satk agent install-skill``, M3 A4). Stdlib only.

The skill is one file, ``docs/agent/SKILL.md`` (frontmatter ``name`` + ``description``: the open Agent
Skills format). Clients look for ``<skills dir>/satk/SKILL.md``:

=========  ==================================================  ==============================
client     user scope                                          project scope
=========  ==================================================  ==============================
claude     ``~/.claude/skills`` (``$CLAUDE_CONFIG_DIR/skills``)  ``<project>/.claude/skills``
codex      ``~/.agents/skills``                                ``<project>/.agents/skills``
=========  ==================================================  ==============================

``--dest DIR`` installs into ``DIR/satk/SKILL.md`` (any other client, or the Claude Code plugin in
``.claude-plugin/satk/skills``). A replaced different copy in a client folder is kept as
``SKILL.md.satk-backup`` (not under ``--dest``).
"""

from __future__ import annotations

import os
from pathlib import Path

from satk.core.errors import SatkError
from satk.core.paths import atomic_write, cfg, jpath

__all__ = ["SKILL_NAME", "SKILL_CLIENTS", "source", "source_text", "skills_dir", "install"]

SKILL_NAME = "satk"
SKILL_CLIENTS = ("claude", "codex")
_BACKUP = ".satk-backup"


def source() -> Path:
    """``docs/agent/SKILL.md`` of this checkout, or the copy shipped as package data."""
    from satk.core.config import MAIN_ROOT, REPO_ROOT

    for root in (REPO_ROOT, MAIN_ROOT):
        if root is not None:
            p = Path(root) / "docs" / "agent" / "SKILL.md"
            if p.is_file():
                return p
    try:  # an installed package (wheel / portable zip) may ship it as satk/_data/agent/SKILL.md
        from importlib.resources import files

        p = Path(str(files("satk") / "_data" / "agent" / "SKILL.md"))
        if p.is_file():
            return p
    except (ModuleNotFoundError, OSError, TypeError):  # pragma: no cover
        pass
    raise SatkError("NOT_FOUND", "the skill source docs/agent/SKILL.md is not available in this installation",
                    hint="copy docs/agent/SKILL.md from the satk repository to <skills dir>/satk/SKILL.md")


def source_text() -> str:
    """The skill text, byte for byte (no newline translation: copies stay identical)."""
    return source().read_bytes().decode("utf-8")


def skills_dir(client: str, scope: str, project: str | os.PathLike | None = None) -> Path:
    """The directory that holds ``<name>/SKILL.md`` folders for ``client`` and ``scope``."""
    if scope == "user":
        if client == "claude":
            d = os.environ.get("CLAUDE_CONFIG_DIR")
            return (Path(d) if d else Path(os.path.expanduser("~")) / ".claude") / "skills"
        return Path(os.path.expanduser("~")) / ".agents" / "skills"
    root = Path(project) if project is not None else Path(cfg().paths.workspace)
    return Path(os.path.abspath(root)) / (".claude" if client == "claude" else ".agents") / "skills"


def install(target_dir: Path, text: str, *, check: bool = False, backup: bool = True) -> dict:
    """Write ``target_dir/satk/SKILL.md`` (or with ``check`` only compare). Returns one table row.

    ``backup``: keep a different previous copy as ``SKILL.md.satk-backup`` (off for ``--dest``: a folder
    the caller manages, e.g. the plugin copy under git).
    """
    dst = target_dir / SKILL_NAME / "SKILL.md"
    try:
        cur = dst.read_bytes().decode("utf-8", errors="replace")
    except FileNotFoundError:
        cur = None
    status = "missing" if cur is None else ("ok" if cur == text else "differs")
    action = "none"
    if not check and status != "ok":
        if cur is not None:
            if backup:
                atomic_write(dst.with_name(dst.name + _BACKUP), cur)
            action = "updated"
        else:
            action = "installed"
        atomic_write(dst, text)
        status = "ok"
    return {"path": jpath(dst), "status": status, "action": action}
