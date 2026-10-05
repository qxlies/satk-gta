"""Read-only access to source trees for sa-re (SPEC §4.9.1, R16; owner WP-09).

Two kinds of trees:

* :class:`GitTree` — files of a commit in a git repository, read with ``git ls-tree`` and one
  ``git cat-file --batch`` process. No checkout, no index refresh, no lazy fetch
  (``GIT_NO_LAZY_FETCH=1``): the donor clones under ``src\\`` are blobless and must never touch the
  network or change state (``git status`` there stays clean).
* :class:`DirTree` — plain directory (synthetic test fixtures, or a tree without git).

Both yield ``(relpath, text)`` with forward slashes and decode UTF-8 with replacement.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from typing import Iterable, Iterator

from ..core.errors import SatkError

__all__ = ["git_env", "run_git", "GitTree", "DirTree", "SourceTree", "decode"]


def git_env() -> dict[str, str]:
    """Environment for read-only git calls on the donor clones."""
    env = dict(os.environ)
    env.update({
        "GIT_NO_LAZY_FETCH": "1",      # never fetch missing blobs from a promisor remote
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",     # no index refresh / lock files
        "GIT_ASKPASS": "",
        "LC_ALL": "C",
    })
    return env


def _git_exe() -> str:
    return os.environ.get("SATK_GIT", "git")


def run_git(repo: Path, *args: str, input_bytes: bytes | None = None, timeout: float = 120) -> bytes:
    """Run ``git -C repo args...`` read-only; ``EXTERNAL_TOOL`` on failure."""
    cmd = [_git_exe(), "-C", str(repo), *args]
    try:
        r = subprocess.run(cmd, input=input_bytes, capture_output=True, env=git_env(), timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except FileNotFoundError as e:
        raise SatkError("EXTERNAL_TOOL", "git is not installed or not on PATH", data={"cmd": cmd[:4]}) from e
    except subprocess.TimeoutExpired as e:
        raise SatkError("TIMEOUT", f"git timed out: {' '.join(cmd[3:6])}") from e
    if r.returncode != 0:
        msg = r.stderr.decode("utf-8", "replace").strip().splitlines()
        raise SatkError("EXTERNAL_TOOL", f"git {' '.join(args[:2])} failed in {repo}: {msg[-1] if msg else r.returncode}",
                        data={"repo": str(repo).replace("\\", "/"), "args": list(args[:4])})
    return r.stdout


def decode(data: bytes) -> str:
    """UTF-8 (BOM tolerated) with replacement; UTF-16 files with a BOM are decoded too."""
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    elif data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            pass
    return data.decode("utf-8", "replace")


def _match(path: str, prefixes: tuple[str, ...], exts: tuple[str, ...]) -> bool:
    if prefixes and not any(path == p or path.startswith(p.rstrip("/") + "/") for p in prefixes):
        return False
    return not exts or path.lower().endswith(exts)


class SourceTree:
    """Common interface: ``label``, ``rev``, ``files()``, ``read()``."""

    label: str
    rev: str
    missing: list[str]

    def list(self, prefixes: Iterable[str] = (), exts: Iterable[str] = ()) -> list[str]:  # pragma: no cover
        raise NotImplementedError

    def read_many(self, paths: list[str]) -> Iterator[tuple[str, str]]:  # pragma: no cover
        raise NotImplementedError

    def files(self, prefixes: Iterable[str] = (), exts: Iterable[str] = ()) -> Iterator[tuple[str, str]]:
        """``(relpath, text)`` for every file under ``prefixes`` with one of ``exts``."""
        return self.read_many(self.list(prefixes, exts))

    def read(self, relpath: str) -> str | None:
        for _p, text in self.read_many([relpath]):
            return text
        return None


class GitTree(SourceTree):
    """Files of ``ref`` in ``repo`` (no checkout). ``rev`` is the full commit sha."""

    def __init__(self, repo: str | Path, ref: str = "HEAD", label: str | None = None):
        self.repo = Path(repo)
        self.ref = ref
        self.label = label or f"{self.repo.name}@{ref}"
        out = run_git(self.repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        self.rev = out.decode().strip()
        if not self.rev:
            raise SatkError("NOT_FOUND", f"git ref {ref!r} not found in {self.repo}")
        self._index: dict[str, str] | None = None
        self.missing = []

    def _ls(self) -> dict[str, str]:
        if self._index is None:
            raw = run_git(self.repo, "ls-tree", "-r", "-z", "--full-tree", self.rev)
            idx: dict[str, str] = {}
            for rec in raw.split(b"\0"):
                if not rec:
                    continue
                meta, _, path = rec.partition(b"\t")
                parts = meta.split()
                if len(parts) == 3 and parts[1] == b"blob":
                    idx[path.decode("utf-8", "replace")] = parts[2].decode()
            self._index = idx
        return self._index

    def list(self, prefixes: Iterable[str] = (), exts: Iterable[str] = ()) -> list[str]:
        pre = tuple(p.strip("/") for p in prefixes)
        ex = tuple(e.lower() for e in exts)
        return sorted(p for p in self._ls() if _match(p, pre, ex))

    def read_many(self, paths: list[str]) -> Iterator[tuple[str, str]]:
        idx = self._ls()
        want = [(p, idx[p]) for p in paths if p in idx]
        if not want:
            return iter(())
        return self._batch(want)

    def _batch(self, want: list[tuple[str, str]]) -> Iterator[tuple[str, str]]:
        cmd = [_git_exe(), "-C", str(self.repo), "cat-file", "--batch"]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=git_env(), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        assert proc.stdin is not None and proc.stdout is not None
        import threading

        def feed() -> None:
            try:
                proc.stdin.write("".join(f"{sha}\n" for _p, sha in want).encode())
            except OSError:  # reader stopped early and git exited
                pass
            finally:
                try:
                    proc.stdin.close()
                except OSError:
                    pass

        t = threading.Thread(target=feed, daemon=True)
        t.start()
        out = proc.stdout
        try:
            for path, sha in want:
                header = out.readline()
                if not header:
                    break
                parts = header.split()
                if len(parts) >= 2 and parts[1] == b"missing":
                    self.missing.append(path)
                    continue
                size = int(parts[2])
                data = out.read(size)
                out.read(1)  # trailing LF
                yield path, decode(data)
        finally:
            t.join(timeout=5)
            try:
                out.close()
            finally:
                proc.wait(timeout=30)

    def show(self, relpath: str) -> str | None:
        """One file at this commit (``git show rev:path`` semantics, via the batch reader)."""
        return self.read(relpath)


class DirTree(SourceTree):
    """Files of a plain directory; ``rev`` = ``dir:`` + sha256 of (path, size, mtime) of all files."""

    def __init__(self, root: str | Path, label: str | None = None):
        self.root = Path(root)
        if not self.root.is_dir():
            raise SatkError("NOT_FOUND", f"source directory not found: {self.root}")
        self.label = label or self.root.name
        self.missing = []
        h = hashlib.sha256()
        for p in self._walk():
            st = (self.root / p).stat()
            h.update(f"{p}\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
        self.rev = "dir:" + h.hexdigest()[:40]

    def _walk(self) -> list[str]:
        out = []
        for dp, dn, fn in os.walk(self.root):
            dn[:] = [d for d in dn if d != ".git"]
            for f in fn:
                out.append(os.path.relpath(os.path.join(dp, f), self.root).replace("\\", "/"))
        return sorted(out)

    def list(self, prefixes: Iterable[str] = (), exts: Iterable[str] = ()) -> list[str]:
        pre = tuple(p.strip("/") for p in prefixes)
        ex = tuple(e.lower() for e in exts)
        return [p for p in self._walk() if _match(p, pre, ex)]

    def read_many(self, paths: list[str]) -> Iterator[tuple[str, str]]:
        for p in paths:
            fp = self.root / p
            if fp.is_file():
                with open(fp, "rb") as f:
                    yield p, decode(f.read())
