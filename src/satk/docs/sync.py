"""Publish the agent docs kept in the repo to the workspace (``satk dev sync-agent-docs``, WP-12).

The workspace (``paths.workspace``) is not a git repository, so the versioned sources live in
``tools/docs/agent`` and are copied byte for byte (SPEC §4.13, WP-12 acceptance 2):

==================================  ===============================================
source (repo)                       destination (workspace)
==================================  ===============================================
``docs/agent/SKILL.md``             ``.claude/skills/satk/SKILL.md``
``docs/agent/workspace-CLAUDE.md``  ``CLAUDE.md``
==================================  ===============================================

A destination that differs from its source *and* is newer than it was edited in place: it is
not overwritten without ``force`` (move the edit into the repo source first).

The public snapshot of the repository (``satk dev export-public``, ``data/public-exclude.txt``) has no
``docs/agent/workspace-CLAUDE.md``: it describes the development workspace only. A pair whose source is
in :data:`OPTIONAL` is dropped from :data:`PAIRS` (and from :func:`plan`) when that source is missing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..core.config import REPO_ROOT

__all__ = ["ALL_PAIRS", "OPTIONAL", "PAIRS", "Pair", "plan"]

#: (source relative to the repo, destination relative to the workspace)
ALL_PAIRS: tuple[tuple[str, str], ...] = (
    ("docs/agent/SKILL.md", ".claude/skills/satk/SKILL.md"),
    ("docs/agent/workspace-CLAUDE.md", "CLAUDE.md"),
)
#: Sources a checkout may lack: the public snapshot leaves out the development workspace's CLAUDE.md.
OPTIONAL: frozenset[str] = frozenset({"docs/agent/workspace-CLAUDE.md"})


def _present(repo: Path) -> tuple[tuple[str, str], ...]:
    return tuple((s, d) for s, d in ALL_PAIRS if s not in OPTIONAL or (repo / s).is_file())


#: The pairs of this checkout (:data:`ALL_PAIRS` without the optional ones it does not have).
PAIRS: tuple[tuple[str, str], ...] = _present(REPO_ROOT)


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
    """State of every (source, destination) pair."""
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
