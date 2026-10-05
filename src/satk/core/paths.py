"""Paths and the write guard (SPEC §3.4). FROZEN contract.

Rules every package follows:

* every writer calls :func:`ensure_writable` (or writes via :func:`atomic_write`, which does);
* removals and renames use :func:`ensure_removable`, which also protects ancestors of roots;
* game files are opened only via :func:`open_ro` (always ``"rb"``);
* paths in JSON output go through :func:`jpath` (absolute, forward slashes);
* only ``game.*`` operations may write into the clean copy, inside :func:`game_writer`.

Protected roots = ``[safety].protected_roots`` from the config, plus ``paths.installed``,
``paths.src``, ``paths.game_root`` and ``paths.game`` themselves (existing or not), **plus** a
floor that no config or environment variable can remove: ``GTA San Andreas``, ``src`` and
``gta-sa-clean`` under the workspace derived from the checkout location
(:func:`satk.core.config.derived_workspace`; empty when satk does not run from a
``<workspace>/tools`` checkout or a worktree of it). ``game_writer()`` unlocks only the clean
copy, never the original install, the user's game (``game_root``) or ``src``.
"""

from __future__ import annotations

import contextvars
import os
import re
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Callable, Iterator

from . import config as _config
from .errors import SatkError

__all__ = [
    "cfg",
    "work",
    "tmp",
    "profile_root",
    "ensure_writable",
    "ensure_removable",
    "is_protected",
    "game_writer",
    "open_ro",
    "atomic_write",
    "jpath",
    "HARD_PROTECTED",
]

def _hard_protected() -> dict[str, str]:
    ws = _config.derived_workspace()
    if ws is None:
        return {}
    return {"installed": str(ws.path / "GTA San Andreas"), "src": str(ws.path / "src"),
            "game": str(ws.path / "gta-sa-clean")}


#: Always protected, whatever the configuration says (R12): the layout of the workspace the
#: code runs from (``<workspace>/tools`` or a worktree of it); ``{}`` for an installed package.
HARD_PROTECTED: dict[str, str] = _hard_protected()

_writer_depth: contextvars.ContextVar[int] = contextvars.ContextVar("satk_game_writer", default=0)
_BAD_CHARS = re.compile(r'[<>"|?*\x00-\x1f]')


def cfg() -> "_config.Config":
    """Merged configuration: ``satk.toml`` + env + defaults (cached; see ``config.reset``)."""
    return _config.load()


def jpath(p: str | os.PathLike) -> str:
    """Absolute path with forward slashes for JSON: ``C:/ws/work/...`` (upper-case drive letter)."""
    s = os.path.abspath(os.fspath(p)).replace("\\", "/")
    if len(s) >= 2 and s[1] == ":":
        s = s[0].upper() + s[1:]
    return s


def _windows_path(s: str) -> str:
    """Map DOS/extended drive and UNC names to one spelling; refuse device namespaces.

    Dot/space suffixes are ambiguous: Win32 can open literal interior components made
    with the extended API. Never trim away a junction with such a name; reject it.
    """
    s = s.replace("/", "\\")
    extended = s.startswith("\\\\?\\")
    if extended:
        body = s[4:]
        if body[:4].lower() == "unc\\":
            s = "\\\\" + body[4:]
        elif re.match(r"^[a-zA-Z]:\\", body):
            s = body
        else:
            raise SatkError("BAD_PARAMS", f"unsupported Windows path namespace: {s!r}")
    elif s.startswith(("\\\\.\\", "\\??\\", "\\\\??\\")):
        raise SatkError("BAD_PARAMS", f"unsupported Windows path namespace: {s!r}")

    drive, tail = os.path.splitdrive(s)
    if drive.startswith("\\\\"):
        share = drive[2:].split("\\")
        if len(share) != 2 or not all(share) or any(p in (".", "..") for p in share):
            raise SatkError("BAD_PARAMS", f"invalid UNC path: {s!r}")
        parts = share + tail.lstrip("\\").split("\\")
        prefix = "\\\\"
    else:
        if drive and not re.fullmatch(r"[a-zA-Z]:", drive):
            raise SatkError("BAD_PARAMS", f"invalid drive: {s!r}")
        parts = tail.split("\\")
        prefix = drive
    for part in parts:
        if extended and part in (".", ".."):
            raise SatkError("BAD_PARAMS", f"ambiguous extended Windows path: {s!r}")
        if part not in (".", "..") and part != part.rstrip(" ."):
            raise SatkError("BAD_PARAMS", f"ambiguous Windows path component: {part!r}")
    return prefix + "\\".join(parts)


def _norm(p: str | os.PathLike) -> str:
    s = os.fspath(p)
    return os.path.normcase(os.path.abspath(_windows_path(s) if os.name == "nt" else s))


def _resolved(p: str) -> str:
    """Resolve existing ancestors strictly (including 8.3 names), allowing missing leaves.

    Inaccessible paths, loops and dangling links cannot safely be treated as ordinary
    missing directories. Fail closed instead of silently keeping an unresolved alias.
    """
    cur = Path(p)
    missing: list[str] = []
    while True:
        try:
            return os.fspath(cur.resolve(strict=True).joinpath(*reversed(missing)))
        except FileNotFoundError:
            if cur.parent == cur or os.path.lexists(cur):
                raise SatkError("BAD_PARAMS", f"cannot resolve path safely: {p!r}") from None
            missing.append(cur.name)
            cur = cur.parent
        except (OSError, ValueError, RuntimeError) as e:
            raise SatkError("BAD_PARAMS", f"cannot resolve path safely: {p!r}: {e}") from None


def _variants(p: str | os.PathLike) -> set[str]:
    """Normalized absolute path plus its realpath (follows junctions/symlinks, 8.3 names)."""
    a = _norm(p)
    return {a, _norm(_resolved(a))}


def _within(child: str, root: str) -> bool:
    try:
        return os.path.commonpath([child, root]) == root
    except ValueError:  # different drives
        return False


def _ancestor_identities(
    path: str, stats: dict[str, os.stat_result | None],
) -> Iterator[tuple[os.stat_result, tuple[str, ...]]]:
    """Existing ancestors and their relative suffixes, nearest first (metadata only)."""
    cur = Path(path)
    suffix: tuple[str, ...] = ()
    while True:
        name = os.fspath(cur)
        if name not in stats:
            try:
                stats[name] = os.stat(cur)
            except FileNotFoundError:
                stats[name] = None
            except (OSError, ValueError) as e:
                raise SatkError("BAD_PARAMS", f"cannot inspect path identity safely: {name!r}: {e}") from None
        st = stats[name]
        if st is not None:
            yield st, suffix
        if cur.parent == cur:
            return
        suffix = (cur.name, *suffix)
        cur = cur.parent


def _within_alias(child: str, root: str, stats: dict[str, os.stat_result | None]) -> bool:
    """Containment through aliases whose realpath keeps a different drive/share name.

    SMB admin/custom shares can have the same file identity as a local directory,
    while resolving to a UNC name. Anchor missing protected roots at their nearest
    existing parent and compare the remaining components as well.
    """
    if _within(child, root):
        return True
    anchor = next(_ancestor_identities(root, stats), None)
    if anchor is None:
        return False  # normally rejected earlier by strict _resolved
    root_st, root_suffix = anchor
    return any(
        os.path.samestat(st, root_st) and suffix[:len(root_suffix)] == root_suffix
        for st, suffix in _ancestor_identities(child, stats)
    )


def _roots() -> tuple[dict[str, str], set[str], set[str]]:
    """({normalized root: display path}, game roots, never-unlock roots)."""
    c = cfg()
    prot: dict[str, str] = {}
    game: set[str] = set()
    never: set[str] = set()
    for kind, r in HARD_PROTECTED.items():
        for v in _variants(r):
            prot.setdefault(v, jpath(r))
            (game if kind == "game" else never).add(v)
    for r in c.protected_roots:
        for v in _variants(r):
            prot.setdefault(v, jpath(r))
    clean = c.paths.get("game")
    if clean is not None:
        for v in _variants(clean):
            game.add(v)
            prot.setdefault(v, jpath(clean))
    for name in ("installed", "src", "game_root"):
        p = c.paths.get(name)
        if p is None or (name == "game_root" and clean is not None and _norm(p) == _norm(clean)):
            continue  # a game_root that *is* the clean copy stays unlockable by game_writer()
        for v in _variants(p):
            never.add(v)
            prot.setdefault(v, jpath(p))
    return prot, game, never


def is_protected(path: str | os.PathLike, *, include_ancestors: bool = False) -> str | None:
    """The protected root (``jpath``) containing ``path`` in the current mode, else ``None``.

    ``include_ancestors=True`` also checks whether ``path`` contains a protected root,
    for removals/renames. UNC and local spellings are compared by file identity too.
    """
    prot, game, never = _roots()
    cands = sorted(_variants(path))
    never_roots = sorted(never)
    game_roots = sorted(game)
    protected_roots = sorted(prot.items())
    unlocked = _writer_depth.get() > 0

    def find(within: Callable[[str, str], bool]) -> str | None:
        def overlaps(c: str, r: str) -> bool:
            return within(c, r) or (include_ancestors and within(r, c))

        for c in cands:
            for r in never_roots:
                if overlaps(c, r):
                    return prot[r]
        for c in cands:
            if unlocked and any(within(c, g) for g in game_roots):
                continue
            for r, display in protected_roots:
                if overlaps(c, r):
                    return display
        return None

    # Windows realpath canonicalizes local aliases; only SMB paths retain a
    # different drive/share spelling. Include resolved variants and active
    # game-writer roots so mapped drives and UNC clean-copy exceptions still work.
    identity_paths = [*cands, *prot]
    if unlocked:
        identity_paths.extend(game_roots)
    if not any(os.path.splitdrive(p)[0].startswith("\\\\") for p in identity_paths):
        return find(_within)

    # Refuse known spellings before probing unrelated (possibly unavailable) shares.
    # In game_writer mode, first recognize clean-copy aliases by identity as well.
    if not unlocked:
        direct = find(_within)
        if direct is not None:
            return direct
    stats: dict[str, os.stat_result | None] = {}
    return find(lambda c, r: _within_alias(c, r, stats))


def _work_hint() -> str:
    try:
        return jpath(cfg().paths.work)
    except Exception:  # noqa: BLE001 - a hint must never mask the real error
        return "<workspace>/work"


def _check_chars(s: str) -> None:
    body = _windows_path(s) if os.name == "nt" else s
    if body[1:2] == ":":
        body = body[2:]
    if not s or (os.name == "nt" and (_BAD_CHARS.search(body) or ":" in body)):
        raise SatkError("BAD_PARAMS", f"invalid path {s!r}", hint="check the quoting of the path argument")


def _checked_path(path: str | os.PathLike, *, removable: bool) -> Path:
    _check_chars(os.fspath(path))
    root = is_protected(path, include_ancestors=removable)
    if root is not None:
        msg = (f"refusing to remove or rename path overlapping protected root {root}: {jpath(path)}"
               if removable else f"refusing to write inside protected root {root}: {jpath(path)}")
        raise SatkError(
            "PROTECTED_PATH",
            msg,
            hint=f"write under {_work_hint()} (satk.core.paths.work/tmp)",
            data={"path": jpath(path), "root": root},
        )
    return Path(os.path.abspath(os.fspath(path)))


def ensure_writable(path: str | os.PathLike) -> Path:
    """Return ``Path(path)`` (absolute) if writing there is allowed, else raise ``PROTECTED_PATH``.

    Never creates anything. Call it before every write or ``mkdir``. Before deleting
    or renaming a tree, use :func:`ensure_removable` on the source and replaced target.
    Paths with characters Windows forbids (``<>"|?*``, control characters) are rejected
    with ``BAD_PARAMS``: they are usually mangled shell quoting, not real paths.
    Device namespaces and ambiguous dot/space suffixes are also rejected.
    """
    return _checked_path(path, removable=False)


def ensure_removable(path: str | os.PathLike) -> Path:
    """Like :func:`ensure_writable`, also refuse ancestors of protected roots.

    Call before removal/rename of a tree, including replacement of a destination.
    This additive guard leaves the write/mkdir contract unchanged. ``game_writer``
    still unlocks only the clean copy; ancestors containing install/src stay protected.
    Never modifies the filesystem.
    """
    return _checked_path(path, removable=True)


@contextmanager
def game_writer() -> Iterator[None]:
    """Allow writes into the clean game copy (``paths.game``) inside this block.

    Only ``game.*`` operations use it (attributes, ``clone``). The original install and
    ``src`` stay protected even here.
    """
    token = _writer_depth.set(_writer_depth.get() + 1)
    try:
        yield
    finally:
        _writer_depth.reset(token)


def open_ro(path: str | os.PathLike) -> BinaryIO:
    """Open a game file for reading. Always binary read-only (``"rb"``)."""
    return open(path, "rb")  # noqa: SIM115 - caller owns the handle


def work(*parts: str) -> Path:
    """``<work>/<parts...>``; creates the directory and returns it.

    If the last part has a file extension (``work("index", "vanilla.sqlite")``), only its
    parent directory is created and the file path is returned.
    """
    base = Path(os.path.abspath(cfg().paths.work))
    p = Path(os.path.abspath(base.joinpath(*parts))) if parts else base
    if not _within(_norm(p), _norm(base)):
        raise SatkError("BAD_PARAMS", f"path escapes the work directory: {parts!r}")
    ensure_writable(p)
    d = p.parent if parts and Path(parts[-1]).suffix else p
    d.mkdir(parents=True, exist_ok=True)
    return p


def tmp(wp_or_job: str) -> Path:
    """``<work>/tmp/<id>/`` (created). Each WP/job has its own temp directory."""
    s = str(wp_or_job)
    if not s or any(ch in s for ch in "/\\:") or s in (".", ".."):
        raise SatkError("BAD_PARAMS", f"bad temp id {wp_or_job!r}")
    p = Path(os.path.abspath(cfg().paths.work)) / "tmp" / s
    ensure_writable(p)
    p.mkdir(parents=True, exist_ok=True)
    return p


def profile_root(profile: str = "vanilla") -> Path:
    """Game root directory of a load profile (``vanilla`` -> ``gta-sa-clean``)."""
    return cfg().profile(profile).root


def atomic_write(path: str | os.PathLike, data: bytes | str, *, encoding: str = "utf-8") -> Path:
    """Write ``data`` to ``path`` atomically (temp file + ``os.replace``) after the guard."""
    p = ensure_writable(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = data.encode(encoding) if isinstance(data, str) else data
    fd, name = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".tmp", dir=p.parent)
    tmp_path = Path(name)
    try:
        # Retain the exclusively created handle: never reopen a guessable staging path.
        with os.fdopen(fd, "wb") as f:
            fd = None
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        # Windows can briefly deny replacement while another writer or reader holds
        # the destination. Keep this writer's staging file intact between attempts.
        deadline = time.monotonic() + 2.0
        delay = 0.005
        while True:
            try:
                os.replace(tmp_path, p)
                break
            except OSError as exc:
                if not isinstance(exc, PermissionError) and getattr(exc, "winerror", None) not in (5, 32, 33):
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise
                time.sleep(min(delay, remaining))
                delay = min(delay * 2, 0.05)
    finally:
        if fd is not None:
            os.close(fd)
        tmp_path.unlink(missing_ok=True)
    return p
