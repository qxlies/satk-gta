"""Write targets of ``satk.game``: clone destination, ``game exe --out``, protect root (SPEC §4.1, R12/R13).

``core.paths.ensure_writable`` checks protected roots by normalized paths, ``realpath`` and
file identity, including aliases such as UNC loopback shares and mapped drives. ``game.*``
operations write gigabytes of game files or change attributes, so their targets have additional
location restrictions here, and the checked canonical path is the one that is then written:

1. **spelling**: ``\\\\?\\``, ``\\\\.\\`` and ``\\??\\`` drive paths become ``D:\\...``; other
   namespaces (volume GUIDs, devices), components ending in a dot or space and ``.``/``..``
   inside extended paths are ``BAD_PARAMS``; network (UNC) paths are ``PROTECTED_PATH``;
2. **resolution**: junctions, symlinks and drive mappings are resolved (``realpath``); a target
   that resolves onto a network share is refused;
3. **core guard**: ``ensure_writable`` on the canonical path (the clean copy is unlocked with
   ``game_writer()`` only when the target *is* the clean copy root and the caller allows it);
4. **identity**: no existing ancestor of the target may be the same directory
   (``os.path.samestat``) as the original install, ``src`` or another protected root; the clean
   copy only as the exact target. This catches any alias the steps above missed;
5. **location** (rule 6 of ``tools/CLAUDE.md``): the target must lie under ``paths.work`` (or be
   the clean copy root where allowed) and outside any git working tree, so a game binary never
   lands in ``docs`` or a repository.

Standard library only.
"""

from __future__ import annotations

import os
import re
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from pathlib import Path

from satk.core.errors import SatkError
from satk.core.paths import HARD_PROTECTED, cfg, ensure_writable, game_writer, jpath

__all__ = ["Target", "canonical", "write_target", "inside", "WORK_HINT"]

WORK_HINT = "write under <workspace>/work (e.g. work/tmp/wp-01/... or work/re/bin/...)"


def _work_dir() -> str:
    try:
        return jpath(cfg().paths.work)
    except Exception:  # noqa: BLE001 - a hint never masks the real error
        return "<workspace>/work"


def _work_hint() -> str:
    """:data:`WORK_HINT` with the configured work directory."""
    return WORK_HINT.replace("<workspace>/work", _work_dir())
_DRIVE = re.compile(r"^[A-Za-z]:(\\|$)")


@dataclass(frozen=True)
class Target:
    """A checked write target: the canonical ``path`` and whether it is the clean copy root."""

    path: Path
    game_root: bool = False

    def unlock(self) -> AbstractContextManager[None]:
        """``game_writer()`` for the clean copy root itself, a no-op for every other target."""
        return game_writer() if self.game_root else nullcontext()


def _bad(msg: str, s: str) -> SatkError:
    return SatkError("BAD_PARAMS", f"{msg}: {s!r}",
                     hint=f"pass a plain local path like {_work_dir()}/tmp/wp-01/clone")


def _network(s: str) -> SatkError:
    return SatkError("PROTECTED_PATH", f"network (UNC) paths are not accepted for game writes: {jpath(s)}",
                     hint="a share can alias a protected root; " + _work_hint(), data={"path": jpath(s)})


def _strip_namespace(s: str) -> tuple[str, bool]:
    """``(path without \\\\?\\ / \\\\.\\ / \\??\\, was_extended)``; refuses non-drive namespaces."""
    for pre in ("\\\\?\\", "\\\\.\\", "\\??\\"):
        if s.startswith(pre):
            body = s[len(pre):]
            if body[:4].lower() == "unc\\":
                raise _network("\\\\" + body[4:])
            if not _DRIVE.match(body):
                raise _bad("unsupported Windows path namespace", s)
            return body, True
    return s, False


def canonical(path: str | os.PathLike, *, what: str = "path") -> Path:
    """Canonical absolute local path of ``path`` (see steps 1-2 of the module docstring).

    Raises ``BAD_PARAMS`` for namespaces and ambiguous components, ``PROTECTED_PATH`` for
    network paths (also when a mapped drive or link resolves onto a share).
    """
    s = os.fspath(path)
    if not s or chr(0) in s:
        raise _bad(f"empty or invalid {what}", s)
    if os.name != "nt":  # pragma: no cover - the toolkit runs on Windows
        return Path(os.path.realpath(os.path.abspath(s)))
    s = s.replace("/", "\\")
    s, extended = _strip_namespace(s)
    if s.startswith("\\\\"):
        raise _network(s)
    drive, tail = os.path.splitdrive(s)
    for part in tail.split("\\"):
        if part in (".", ".."):
            if extended:  # literal in the extended namespace, collapsed elsewhere: ambiguous
                raise _bad(f"'.' or '..' in an extended {what}", os.fspath(path))
        elif part != part.rstrip(" ."):
            raise _bad(f"{what} component ends with a dot or space (ambiguous on Windows)", os.fspath(path))
    a = os.path.abspath(s)
    try:
        r = os.path.realpath(a)
    except (OSError, ValueError) as e:
        raise _bad(f"cannot resolve {what} ({e})", os.fspath(path)) from None
    r, _ = _strip_namespace(r)
    if r.startswith("\\\\"):
        raise _network(r)
    return Path(r)


def _stat(p: Path) -> os.stat_result | None:
    try:
        return os.stat(p)
    except (OSError, ValueError):
        return None


def _ancestors(p: Path) -> list[Path]:
    """``p`` and its parents, nearest first."""
    return [p, *p.parents]


def _same_as(p: Path, root_st: os.stat_result) -> bool:
    st = _stat(p)
    return st is not None and os.path.samestat(st, root_st)


def _norm(p: Path | str) -> str:
    return os.path.normcase(os.fspath(p))


def _within_text(child: Path, parent: Path) -> bool:
    c, p = _norm(child), _norm(parent)
    try:
        return os.path.commonpath([c, p]) == p
    except ValueError:  # different drives
        return False


def inside(child: Path, parent: Path) -> bool:
    """``child`` is ``parent`` or below it: by text (both canonical) or by file identity."""
    if _within_text(child, parent):
        return True
    pst = _stat(parent)
    return pst is not None and any(_same_as(a, pst) for a in _ancestors(child))


def _roots() -> tuple[list[tuple[str, Path]], list[Path], Path]:
    """``([(display, root)] never writable, [clean copy roots], work root)``, all canonical."""
    c = cfg()
    never: dict[str, Path] = {}
    games: dict[str, Path] = {}

    def add(bucket: dict[str, Path], p: str | os.PathLike | None) -> None:
        if p is None:
            return
        try:
            cp = canonical(p)
        except SatkError:
            return
        bucket.setdefault(_norm(cp), cp)

    for kind, r in HARD_PROTECTED.items():
        add(games if kind == "game" else never, r)
    add(games, c.paths.game)
    for name in ("installed", "src", "game_root"):
        add(never, c.paths.get(name))
    for r in c.protected_roots:
        add(never, r)
    for k in list(never):
        if k in games:
            del never[k]
    return [(jpath(p), p) for p in never.values()], list(games.values()), canonical(c.paths.work)


def _refuse(msg: str, path: Path, root: str, hint: str | None = None) -> SatkError:
    return SatkError("PROTECTED_PATH", msg, hint=hint or _work_hint(), data={"path": jpath(path), "root": root})


def write_target(path: str | os.PathLike, *, what: str = "path", allow_game_root: bool = False,
                 location: bool = True) -> Target:
    """Check a ``game.*`` write target (module docstring, steps 1-5) and return it canonical.

    Args:
        path: the requested target (file or directory; it need not exist).
        what: name used in messages (``"clone destination"``, ``"--out"``...).
        allow_game_root: the clean copy root itself (``paths.game``) is acceptable (``clone``
            may recreate it, ``protect`` sets attributes in it); anything strictly inside it
            never is.
        location: enforce "under ``paths.work`` (or the clean copy root) and outside git working
            trees"; ``False`` only for the ``<dst>.partial`` sibling of an accepted target.

    Raises ``PROTECTED_PATH`` (protected root, alias of one, network path, outside ``work``,
    inside a repository) or ``BAD_PARAMS`` (ambiguous spelling). Never creates anything.
    """
    p = canonical(path, what=what)
    never, games, work_root = _roots()
    chain = _ancestors(p)
    game_root = False
    for g in games:
        st = _stat(g)
        if not (_within_text(p, g) or (st is not None and any(_same_as(a, st) for a in chain))):
            continue
        if not (allow_game_root and (_norm(p) == _norm(g) or (st is not None and _same_as(p, st)))):
            raise _refuse(f"{what} {jpath(p)} is inside the clean game copy {jpath(g)}", p, jpath(g),
                          hint="only the copy root itself may be recreated or protected; " + _work_hint())
        game_root = True
    with game_writer() if game_root else nullcontext():
        ensure_writable(p)  # core check of every protected root (install and src never unlock)
    for display, root in never:
        st = _stat(root)
        if st is not None and any(_same_as(a, st) for a in chain):
            raise _refuse(f"{what} {jpath(p)} is inside protected root {display} (same directory by file identity)",
                          p, display)
    if not location:
        return Target(p, game_root)
    if not game_root and not inside(p, work_root):
        raise _refuse(f"{what} {jpath(p)} is outside the work directory {jpath(work_root)}; "
                      "satk writes game files only there (tools/CLAUDE.md rule 6)", p, jpath(work_root))
    for a in chain:
        if os.path.lexists(a / ".git"):
            raise _refuse(f"{what} {jpath(p)} is inside the git working tree {jpath(a)}; "
                          "game files must not land in a repository", p, jpath(a))
        if _norm(a) == _norm(work_root):
            break  # the work root itself is not a repository (checked by the loop up to here)
    return Target(p, game_root)
