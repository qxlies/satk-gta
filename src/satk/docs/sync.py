"""Publish the agent docs kept in the repo to the workspace (``satk dev sync-agent-docs``, WP-12).

The workspace (``paths.workspace``) is not a git repository, so the versioned sources live in
``tools/docs/agent`` and are copied byte for byte (SPEC §4.13, WP-12 acceptance 2):

========================================  ===============================================
source (repo)                             destination (workspace)
========================================  ===============================================
``docs/agent/SKILL.md``                   ``.claude/skills/satk/SKILL.md``
``docs/agent/style/<page>.md``            ``.claude/skills/satk/style/<page>.md``
``docs/agent/briefs/<page>.md``           ``.claude/skills/satk/briefs/<page>.md``
``docs/agent/workspace-CLAUDE.md``        ``CLAUDE.md``
========================================  ===============================================

The skill's companion folders (:data:`SKILL_DIRS`: the style guides and the brief template) travel with
``SKILL.md``: the skill links to them relative to itself (``style/README.md``).

A destination that differs from its source *and* is newer than it was edited in place: it is
not overwritten without ``force`` (move the edit into the repo source first).

The public snapshot of the repository (``satk dev export-public``, ``data/public-exclude.txt``) has no
``docs/agent/workspace-CLAUDE.md``: it describes the development workspace only. A pair whose source is
in :data:`OPTIONAL` is dropped from :data:`PAIRS` (and from :func:`plan`) when that source is missing.

A task worktree of the workspace's own checkout (``<workspace>/work/wt/<id>``, a linked worktree of
``<workspace>/tools``) publishes nothing: its sources are not merged yet, and the workspace copies belong to
the main checkout (the merge step publishes them). :func:`plan` is empty there (:func:`task_worktree`).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..core.config import REPO_ROOT, main_checkout

__all__ = ["ALL_PAIRS", "OPTIONAL", "PAIRS", "SKILL_DIRS", "Pair", "plan", "skill_files", "task_worktree"]

#: (source relative to the repo, destination relative to the workspace)
ALL_PAIRS: tuple[tuple[str, str], ...] = (
    ("docs/agent/SKILL.md", ".claude/skills/satk/SKILL.md"),
    ("docs/agent/workspace-CLAUDE.md", "CLAUDE.md"),
)
#: Sources a checkout may lack: the public snapshot leaves out the development workspace's CLAUDE.md.
OPTIONAL: frozenset[str] = frozenset({"docs/agent/workspace-CLAUDE.md"})
#: Folders next to ``docs/agent/SKILL.md`` that are published with the skill (Markdown files only).
SKILL_DIRS: tuple[str, ...] = ("style", "briefs")


def skill_files(agent_dir: Path) -> list[str]:
    """The companion files of the skill in ``agent_dir`` (``docs/agent``), as sorted ``<dir>/<name>.md`` paths."""
    out: list[str] = []
    for d in SKILL_DIRS:
        folder = Path(agent_dir) / d
        if folder.is_dir():
            out += sorted(f"{d}/{p.name}" for p in folder.glob("*.md") if p.is_file())
    return out


def _present(repo: Path) -> tuple[tuple[str, str], ...]:
    base = [(s, d) for s, d in ALL_PAIRS if s not in OPTIONAL or (repo / s).is_file()]
    extra = [(f"docs/agent/{rel}", f".claude/skills/satk/{rel}") for rel in skill_files(repo / "docs" / "agent")]
    return tuple(base[:1] + extra + base[1:])


#: The pairs of this checkout (:data:`ALL_PAIRS` without the optional ones it does not have, plus the skill's
#: companion files).
PAIRS: tuple[tuple[str, str], ...] = _present(REPO_ROOT)


def task_worktree(repo: Path, workspace: Path) -> bool:
    """``True`` when ``repo`` is a linked worktree of the workspace's own checkout ``<workspace>/tools``."""
    main = main_checkout(repo)
    if main is None:
        return False
    try:
        return not os.path.samefile(main, repo) and os.path.samefile(main, Path(workspace) / "tools")
    except OSError:
        return False


@dataclass(frozen=True, slots=True)
class Pair:
    src: Path
    dst: Path
    state: str  # same | differs | missing | no-source
    dst_newer: bool = False

    def row(self) -> list:
        return [self.src.as_posix(), self.dst.as_posix(), self.state, self.dst_newer]


def _read(p: Path) -> bytes | None:
    try:
        return p.read_bytes()
    except FileNotFoundError:
        return None


def plan(repo: Path, workspace: Path) -> list[Pair]:
    """State of every (source, destination) pair; empty for a task worktree (:func:`task_worktree`)."""
    if task_worktree(repo, workspace):
        return []
    out: list[Pair] = []
    for s, d in _present(repo):
        src, dst = repo / s, workspace / d
        a, b = _read(src), _read(dst)
        if a is None:
            state = "no-source"
        elif b is None:
            state = "missing"
        elif a == b:
            state = "same"
        else:
            state = "differs"
        newer = False
        if a is not None and b is not None and a != b:
            newer = os.stat(dst).st_mtime_ns > os.stat(src).st_mtime_ns
        out.append(Pair(src, dst, state, newer))
    return out
