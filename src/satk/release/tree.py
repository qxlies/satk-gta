"""Files of a git ref of the checkout (``git archive``): the release never packs the working tree.

:func:`export` reads ``git archive --format=tar <ref>`` into memory (a few MB) and returns a
:class:`Tree`: ``{posix path: bytes}`` plus the commit and its timestamp, which the zip, the wheel
and the sdist use as the modification time of every entry, so the same ref gives the same bytes.
"""

from __future__ import annotations

import io
import os
import re
import subprocess
import tarfile
from dataclasses import dataclass, field
from pathlib import Path

from ..core.errors import SatkError

__all__ = ["Tree", "export", "dirty_files", "read_version"]


@dataclass
class Tree:
    """Content of a git ref: file bytes by POSIX path (directories are implicit)."""

    commit: str
    timestamp: int
    files: dict[str, bytes] = field(default_factory=dict)

    def under(self, prefix: str) -> list[str]:
        """Sorted paths below ``prefix`` (a directory ``"src/"`` or an exact file)."""
        if not prefix.endswith("/"):
            return [prefix] if prefix in self.files else []
        return sorted(p for p in self.files if p.startswith(prefix))

    def text(self, path: str) -> str:
        try:
            return self.files[path].decode("utf-8")
        except KeyError:
            raise SatkError("NOT_FOUND", f"{path} is not in commit {self.commit[:9]}") from None


def _git(repo: Path, *args: str, binary: bool = False) -> bytes | str:
    try:
        p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, timeout=300)
    except FileNotFoundError:
        raise SatkError("DEPENDENCY", "git is not installed or not on PATH",
                        hint="satk dev release runs from a git checkout of satk") from None
    if p.returncode != 0:
        err = p.stderr.decode("utf-8", "replace").strip()
        raise SatkError("BAD_PARAMS", f"git {' '.join(args[:2])} failed: {err}",
                        hint="pass an existing commit, branch or tag in --ref")
    return p.stdout if binary else p.stdout.decode("utf-8", "replace")


def export(repo: str | os.PathLike, ref: str = "HEAD") -> Tree:
    """All files of ``ref`` (symlinks and submodules are refused: the release has neither)."""
    repo = Path(repo)
    line = _git(repo, "log", "-1", "--format=%H %ct", ref, "--").strip()
    commit, ts = line.split()
    raw = _git(repo, "archive", "--format=tar", commit, binary=True)
    tree = Tree(commit=commit, timestamp=int(ts))
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as tf:
        for m in tf.getmembers():
            if m.isdir() or m.name == "pax_global_header" or m.type == tarfile.XGLTYPE:
                continue
            if not m.isfile():
                raise SatkError("UNSUPPORTED", f"{m.name}: only regular files can be released (symlink/submodule?)")
            f = tf.extractfile(m)
            tree.files[m.name] = f.read() if f is not None else b""
    return tree


def dirty_files(repo: str | os.PathLike) -> list[str]:
    """Paths with uncommitted changes (``git status --porcelain``), untracked files included."""
    out = _git(Path(repo), "status", "--porcelain", "--untracked-files=normal")
    return sorted(line[3:] for line in out.splitlines() if line.strip())


_VERSION = re.compile(r'^__version__\s*=\s*["\']([^"\']+)["\']', re.M)


def read_version(tree: Tree) -> str:
    """``satk.__version__`` of the ref (must equal ``project.version`` in its ``pyproject.toml``)."""
    import tomllib

    m = _VERSION.search(tree.text("src/satk/__init__.py"))
    if not m:
        raise SatkError("INTERNAL", "no __version__ in src/satk/__init__.py")
    meta = tomllib.loads(tree.text("pyproject.toml"))
    v = meta.get("project", {}).get("version")
    if v != m.group(1):
        raise SatkError("CHECK_FAILED", f"version mismatch: pyproject.toml {v}, satk.__version__ {m.group(1)}",
                        hint="set the same version in both files")
    return v
