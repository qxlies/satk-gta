"""``satk engine worktree create | refresh | list | remove``: sparse checkouts of the MTA fork.

A worktree is another checkout of the fork repository (``git worktree``) under ``<work>/wt/<name>``. The
``server`` profile materialises only what the x64 server and the googletest project need, so a lane can
generate and build in its own tree (``satk engine build --fork <path>``) while the configured fork
``engine/mtasa`` is busy with other lanes. The list of folders is **computed** from the premake files of
the base revision, never written by hand:

* the ``include`` lines of the root ``premake5.lua`` outside the ``os.target() == "windows"`` block (server,
  shared code, server-side vendor libraries) are checked out completely;
* every included test project (plus the required legacy ``Tests_Client``) is checked out too, and so are
  all projects they ``links`` to (``gtest``, ``cryptopp``, ...), transitively;
* every quoted relative path in the premake files of those folders (``"../../vendor/sparsehash/src"``,
  ``"../../Client/sdk"``) pulls in the folder it names (``vendor/<name>`` whole, other paths as written);
* a source of those folders that includes a header from outside them with ``#include "../..."``
  (``Tests/client`` takes ``Client/core/PdbDirectoryDiscovery.h``) pulls in that one file, and the quoted
  includes of such a file in turn;
* every other included script (the client) keeps its premake file and recursively included scripts, so that
  premake can generate the solution; those client projects have no sources in the tree and are not built;
* ``utils/buildactions`` (the ``premake.path`` of the root script) and ``utils/premake5.exe`` come with it.

Git's own mechanism is used (``worktree add --no-checkout``, ``sparse-checkout set --no-cone``, ``read-tree``),
always with ``GIT_NO_LAZY_FETCH=1``; nothing is fetched. The first ``sparse-checkout`` in a repository makes git
enable ``extensions.worktreeConfig`` in the repository config (so the sparse flag stays per worktree); that
is reported in the answer. Stdlib only.
"""

from __future__ import annotations

import os
import posixpath
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from ..core.errors import SatkError
from ..core.paths import atomic_write, cfg, ensure_removable, ensure_writable, jpath
from .common import (
    Layout,
    exclusive,
    fork_id_of,
    git,
    git_raw,
    layout,
    pid_alive,
    read_json,
    same_path,
    sha256_file,
    write_json,
)

__all__ = [
    "PROFILES",
    "EXTRA_PROJECTS",
    "SparseProfile",
    "wt_root",
    "check_wt_path",
    "parse_includes",
    "parse_projects",
    "parse_links",
    "path_refs",
    "include_roots",
    "quoted_includes",
    "SOURCE_EXT",
    "compute_server_profile",
    "included_scripts",
    "emit_patterns",
    "read_blobs",
    "compute_profile",
    "create",
    "refresh",
    "listing",
    "remove",
    "install_server_deps",
]

PROFILES = ("server", "full")
#: Required legacy project; further test projects are discovered from the include graph.
EXTRA_PROJECTS = ("Tests_Client",)
#: Folders that ride along because satk tools read them (the sa-engine manifest and its notes).
ALWAYS_DIRS = ("docs",)
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}[A-Za-z0-9_-]$|^[A-Za-z0-9]$")


# --------------------------------------------------------------------------- paths


def wt_root() -> Path:
    """``<work>/wt``: the only place a satk-managed worktree may live."""
    return Path(os.path.abspath(cfg().paths.work)) / "wt"


def check_wt_path(path: str | os.PathLike) -> Path:
    """Absolute path of a worktree, or ``BAD_PARAMS``/``PROTECTED_PATH``/``EXISTS``-style errors.

    Rules: the path is ``<work>/wt/<name>`` (one level, ``<name>`` made of letters, digits, ``._-``); a
    junction or link inside ``work`` that leads out of ``wt`` is refused (real paths are compared); nothing
    is created here.
    """
    if path is None or str(path).strip() == "":
        raise SatkError("BAD_PARAMS", "path is required", hint=f"e.g. --path {jpath(wt_root() / 'sae2-srv')}")
    root = wt_root()
    p = Path(os.path.abspath(os.fspath(path)))
    if not same_path(p.parent, root):
        raise SatkError("BAD_PARAMS", f"worktrees live in {jpath(root)}/<name>; got {jpath(p)}",
                        hint=f"use --path {jpath(root / (p.name or 'name'))}", data={"allowed": jpath(root)})
    if not _NAME.match(p.name) or p.name.endswith("."):
        raise SatkError("BAD_PARAMS", f"bad worktree name {p.name!r}",
                        hint="1-64 characters: letters, digits, '.', '_', '-'; start with a letter or digit")
    ensure_writable(p)
    if p.exists() and not same_path(Path(os.path.realpath(p)).parent, os.path.realpath(root)):
        raise SatkError("PROTECTED_PATH", f"{jpath(p)} is a link that leads out of {jpath(root)}",
                        hint="remove the link; worktrees are plain folders")
    return p


# --------------------------------------------------------------------------- premake parsing

_INCLUDE = re.compile(r"""^\s*(?:include|dofile)\s*\(?\s*["']([^"']+)["']\s*\)?""")
_WIN_IF = re.compile(r"""^(\s*)if\s+os\.target\s*\(\s*\)\s*==\s*["']windows["']\s+then\s*$""")
_PROJECT = re.compile(r"""^\s*project\s*\(?\s*["']([^"']+)["']""", re.M)
_LINKS = re.compile(r"\blinks\s*\{([^}]*)\}", re.S)
_STR = re.compile(r"""["']([^"'\r\n]+)["']""")
_INCLUDEDIRS = re.compile(r"\bincludedirs\s*\{([^}]*)\}", re.S)
_QUOTED_INCLUDE = re.compile(r'^[ \t]*#[ \t]*include[ \t]+"([^"\r\n]+)"', re.M)
#: Extensions of the files whose quoted includes are followed.
SOURCE_EXT = (".h", ".hpp", ".hxx", ".inl", ".c", ".cc", ".cpp", ".cxx")
_PREMAKE_PATH = re.compile(r"""premake\.path\s*=\s*premake\.path\s*\.\.\s*["']([^"']*)["']""")
_LUA_COMMENTS = re.compile(r'''--\[(=*)\[.*?\]\1\]|--[^\n]*|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*' ''', re.S | re.X)


def _uncomment(text: str) -> str:
    return _LUA_COMMENTS.sub(lambda m: "\n" * m.group().count("\n") if m.group().startswith("--") else m.group(), text)


def parse_includes(root_text: str) -> list[tuple[str, bool]]:
    """``[(directory, in_windows_block)]`` of every ``include "dir"`` of the root ``premake5.lua``.

    ``in_windows_block`` is true between ``if os.target() == "windows" then`` and its ``end`` (the client and
    its vendor libraries); the rest is built on every platform and is what the server needs.
    """
    out: list[tuple[str, bool]] = []
    indent: str | None = None
    for line in _uncomment(root_text).splitlines():
        if indent is None:
            m = _WIN_IF.match(line)
            if m:
                indent = m.group(1)
                continue
        elif re.match(rf"^{re.escape(indent)}end\s*$", line):
            indent = None
            continue
        m = _INCLUDE.match(line)
        if m:
            out.append((m.group(1).rstrip("/"), indent is not None))
    return out


def included_scripts(tree: Iterable[str], read: Callable[[str], str | None]) -> dict[str, bool]:
    """Reachable literal include/dofile scripts, relative to each caller; value = Windows-only branch.

    A script reached by both branches is shared. Cycles terminate. Missing literal targets are an error:
    a profile must not silently omit a script which premake itself will try to load.
    """
    files = set(tree)
    out: dict[str, bool] = {}
    queue = [("premake5.lua", False)]
    while queue:
        script, windows = queue.pop()
        if script in out and (out[script] is False or windows):
            continue
        text = read(script)
        if text is None:
            raise SatkError("NOT_READY", f"included premake script {script!r} is absent from the revision")
        out[script] = windows
        for name, child_windows in parse_includes(text):
            path = posixpath.normpath(posixpath.join(posixpath.dirname(script), name.replace("\\", "/")))
            if path == ".." or path.startswith(("../", "/")) or ":" in path:
                raise SatkError("NOT_READY", f"premake include {name!r} in {script} leaves the repository")
            target = path if path in files else posixpath.join(path, "premake5.lua")
            if target not in files:
                raise SatkError("NOT_READY", f"premake include {name!r} in {script} has no tracked script",
                                hint="check the includes of the selected fork revision")
            queue.append((target, windows or child_windows))
    return dict(sorted(out.items()))


def parse_projects(text: str) -> list[str]:
    """Names of the projects defined by a premake file (``project "name"``)."""
    return _PROJECT.findall(_uncomment(text))


def parse_links(text: str) -> list[str]:
    """Names inside every ``links { ... }`` (system libraries and project names alike)."""
    names: list[str] = []
    for block in _LINKS.findall(_uncomment(text)):
        names += _STR.findall(block)
    return names


def include_roots(root_text: str, dirs: set[str]) -> set[str]:
    """Folders the root script puts on every project's include path (``includedirs { "vendor" }``): a project
    that names one of them (``"../../vendor"``) means "the libraries it includes", not the whole folder."""
    roots: set[str] = set()
    for block in _INCLUDEDIRS.findall(root_text):
        for s in _STR.findall(block):
            s = posixpath.normpath(s)
            if s in dirs and "/" not in s:
                roots.add(s)
    return roots


def quoted_includes(text: str) -> list[str]:
    """Targets of ``#include "..."`` lines (angle-bracket includes come from include paths, not from here)."""
    return _QUOTED_INCLUDE.findall(text)


def path_refs(text: str, base_dir: str, tree: set[str], dirs: set[str], skip: Iterable[str] = ()) -> set[str]:
    """Folders and files named by relative paths in a premake file.

    ``base_dir`` is the folder of the premake file (posix, relative to the repository root). A quoted string
    is resolved against it; the part before the first glob character counts. A path under ``vendor`` stands
    for the whole ``vendor/<name>``; any other existing folder stands for itself, an existing file for
    itself. Strings that leave the repository, use premake tokens (``%{``) or name nothing are ignored, and so
    are the folders in ``skip`` (the include roots of the workspace).
    """
    skipped = set(skip)
    out: set[str] = set()
    for s in _STR.findall(_uncomment(text)):
        if "%{" in s or "$(" in s or s.startswith(("/", "-")) or ":" in s[:3]:
            continue
        lit = re.split(r"[*?]", s, maxsplit=1)[0]
        if not lit or lit in (".", "./"):
            continue
        glob = lit != s
        full = posixpath.normpath(posixpath.join(base_dir, lit)) if base_dir else posixpath.normpath(lit)
        if full.startswith("..") or full == ".":
            continue
        if glob and not lit.endswith("/"):
            full = posixpath.dirname(full)
            if not full:
                continue
        parts = full.split("/")
        if full in skipped:
            continue
        if parts[0] == "vendor" and len(parts) >= 2 and ("vendor/" + parts[1]) in dirs:
            out.add("vendor/" + parts[1])
        elif full in dirs:
            out.add(full)
        elif full in tree:
            out.add(full)
    return out


# --------------------------------------------------------------------------- profile


@dataclass
class SparseProfile:
    """What a sparse checkout contains: whole folders, single files, and why."""

    name: str
    full_dirs: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)  # single files (premake stubs of client projects, premake5.exe)
    why: dict[str, str] = field(default_factory=dict)
    projects: list[str] = field(default_factory=list)  # premake projects whose sources are in the checkout

    def patterns(self) -> list[str]:
        return emit_patterns(self.full_dirs, self.files)


def _under(path: str, folder: str) -> bool:
    return path == folder or path.startswith(folder + "/")


def _minimal(paths: Iterable[str]) -> list[str]:
    """Drop folders that lie below another folder of the set; sorted."""
    ps = sorted(set(paths))
    out: list[str] = []
    for p in ps:
        if not any(_under(p, q) for q in out):
            out.append(p)
    return out


def compute_server_profile(tree: Iterable[str], read: Callable[[str], str | None]) -> SparseProfile:
    """The ``server`` profile of a revision: ``tree`` = every file path (posix, relative to the root),
    ``read(path)`` = text of a premake file (``None`` when absent)."""
    files = set(tree)
    dirs: set[str] = set()
    for f in files:
        d = posixpath.dirname(f)
        while d:
            if d in dirs:
                break
            dirs.add(d)
            d = posixpath.dirname(d)
    root = read("premake5.lua")
    if root is None:
        raise SatkError("NOT_READY", "premake5.lua not found in the base revision",
                        hint="the base must be a revision of the MTA fork")
    scripts = included_scripts(files, read)
    includes = [(posixpath.dirname(p), win) for p, win in scripts.items() if p != "premake5.lua"]
    roots = include_roots(root, dirs)
    why: dict[str, str] = {}
    full: set[str] = set()

    for d, in_win in includes:
        if (not in_win or _under(d, "Server") or _under(d, "Shared")) and d in dirs:
            full.add(d)
            why[d] = ("server/shared include" if in_win else
                      "root premake5.lua include outside the Windows block (server/shared/vendor)")

    # project name -> folder, over every included folder (needed to follow links and EXTRA_PROJECTS)
    by_name: dict[str, str] = {}
    for script in scripts:
        d = posixpath.dirname(script)
        txt = read(script)
        if txt:
            for name in parse_projects(txt):
                by_name.setdefault(name, d)
    queue = list(full)
    for name in EXTRA_PROJECTS:
        d = by_name.get(name)
        if d is None:
            raise SatkError("NOT_READY", f"project {name!r} is not defined by any included premake file",
                            hint="the profile list EXTRA_PROJECTS in satk.engine.worktree is out of date")
        if d not in full:
            full.add(d)
            why[d] = f"project {name} (extra project of the server profile)"
            queue.append(d)

    # New Tests/* projects must ride along automatically, including those inside the Windows block.
    for script in scripts:
        d = posixpath.dirname(script)
        names = parse_projects(read(script) or "")
        if d and (_under(d, "Tests") or any(n.startswith("Tests_") for n in names)) and d not in full:
            full.add(d)
            why[d] = f"test project included by premake ({', '.join(names) or script})"
            queue.append(d)

    premakes = {f for f in files if f.endswith("premake5.lua")} | set(scripts)
    scanned_scripts: set[str] = set()
    while queue:
        d = queue.pop()
        for pf in sorted(f for f in premakes if _under(f, d) and f not in scanned_scripts):
            scanned_scripts.add(pf)
            txt = read(pf)
            if not txt:
                continue
            base = posixpath.dirname(pf)
            for lib in parse_links(txt):
                dep = by_name.get(lib)
                if dep and dep not in full and not any(_under(dep, q) for q in full):
                    full.add(dep)
                    why[dep] = f"linked by {pf} ('{lib}')"
                    queue.append(dep)
            for ref in sorted(path_refs(txt, base, files, dirs, roots)):
                if any(_under(ref, q) for q in full):
                    continue
                full.add(ref)
                why[ref] = f"named in {pf}"
                if ref in dirs or ref in premakes:
                    queue.append(ref)

    # headers reached by "../" includes from outside the folders chosen so far (one file each, transitively)
    single: set[str] = set()
    single_origin: set[str] = set()  # single files pulled in by an include (their sibling includes count too)

    def covered(path: str) -> bool:
        return path in single or any(_under(path, q) for q in full)

    pending = [f for f in sorted(files) if f.endswith(SOURCE_EXT) and not f.startswith(("vendor/", "utils/"))
               and any(_under(f, q) for q in full)]
    scanned: set[str] = set(pending)
    while pending:
        f = pending.pop()
        txt = read(f)
        if not txt:
            continue
        own = covered(f) and f not in single_origin
        for inc in quoted_includes(txt):
            if own and not inc.startswith("../"):
                continue  # inside the folders: siblings are there already
            tgt = posixpath.normpath(posixpath.join(posixpath.dirname(f), inc))
            if tgt in files and not covered(tgt):
                single.add(tgt)
                single_origin.add(tgt)
                why[tgt] = f"included by {f}"
                if tgt not in scanned and tgt.endswith(SOURCE_EXT):
                    scanned.add(tgt)
                    pending.append(tgt)

    # utils: only the premake actions and the premake binary
    for m in _PREMAKE_PATH.finditer(root):
        for part in m.group(1).split(";"):
            part = part.strip().strip("/")
            if part and part in dirs:
                full.add(part)
                why[part] = "premake.path of the root script (build actions)"
    extra_files: set[str] = set()
    if "utils/premake5.exe" in files:
        extra_files.add("utils/premake5.exe")
        why["utils/premake5.exe"] = "the premake binary satk runs"
    for d in ALWAYS_DIRS:
        if d in dirs:
            full.add(d)
            why[d] = "satk tools read it (patch-site manifest, notes)"

    # client projects keep only their premake file, so the solution can be generated
    stubs: set[str] = set()
    for pf in scripts:
        if pf != "premake5.lua" and not any(_under(pf, q) for q in full):
            stubs.add(pf)
            why[pf] = "premake script of a project outside the profile (generation only)"
    # a file named in a premake file (not a folder) counts as a single file
    single |= {p for p in full if p in files}
    full -= single
    top_full = _minimal(full)
    singles = sorted((extra_files | stubs | single))
    singles = [f for f in singles if not any(_under(f, q) for q in top_full)]
    present = sorted(n for n, d in by_name.items() if any(_under(d, q) for q in top_full))
    return SparseProfile("server", top_full, singles, why, present)


# --------------------------------------------------------------------------- sparse-checkout patterns


def emit_patterns(full_dirs: Iterable[str], files: Iterable[str]) -> list[str]:
    """Non-cone ``sparse-checkout`` patterns: all files of the root, no top-level folder, then the wanted
    folders; a folder that is only partly wanted is opened level by level (``/A/`` ``!/A/*`` ``/A/B/``)
    like git's cone mode does, so single files below it can be named and nothing else comes along."""
    root: dict = {"kids": {}, "full": False, "files": set()}

    def node_for(parts: list[str]) -> dict:
        n = root
        for p in parts:
            n = n["kids"].setdefault(p, {"kids": {}, "full": False, "files": set()})
        return n

    for d in full_dirs:
        node_for(d.strip("/").split("/"))["full"] = True
    for f in files:
        parts = f.strip("/").split("/")
        node_for(parts[:-1])["files"].add(parts[-1])

    lines = ["/*", "!/*/"]

    def walk(n: dict, prefix: str) -> None:
        for name in sorted(n["kids"]):
            k = n["kids"][name]
            here = f"{prefix}/{name}"
            if k["full"]:
                lines.append(here + "/")
            elif k["kids"]:
                lines.append(here + "/")
                lines.append("!" + here + "/*")  # nothing of the folder by default; the wanted parts follow
                for fn in sorted(k["files"]):
                    lines.append(f"{here}/{fn}")
                walk(k, here)
            else:
                for fn in sorted(k["files"]):
                    lines.append(f"{here}/{fn}")

    for fn in sorted(root["files"]):
        lines.append("/" + fn)
    walk(root, "")
    return lines


# --------------------------------------------------------------------------- reading a revision


def read_blobs(repo: Path, rev: str, paths: Iterable[str]) -> dict[str, str]:
    """Text of several files of a revision in one ``git cat-file --batch`` (missing files are absent)."""
    paths = list(paths)
    if not paths:
        return {}
    req = "".join(f"{rev}:{p}\n" for p in paths).encode("utf-8")
    raw = git_raw("cat-file", "--batch", cwd=repo, input=req)
    out: dict[str, str] = {}
    pos = 0
    for p in paths:
        nl = raw.index(b"\n", pos)
        head = raw[pos:nl].decode("utf-8", errors="replace").split()
        pos = nl + 1
        if len(head) == 3 and head[1] == "blob":
            size = int(head[2])
            out[p] = raw[pos:pos + size].decode("utf-8-sig", errors="replace")
            pos += size + 1
    return out


def compute_profile(repo: Path, rev: str, profile: str) -> SparseProfile:
    """Profile ``server`` for a revision of the repository (``full`` has no patterns: a normal checkout)."""
    if profile == "full":
        return SparseProfile("full")
    if profile != "server":
        raise SatkError("BAD_PARAMS", f"unknown profile {profile!r}", did_you_mean=list(PROFILES))
    names = git("ls-tree", "-r", "--name-only", "-z", rev, cwd=repo, no_lazy=True).split("\0")
    tree = [n for n in names if n]
    wanted = [n for n in tree if n.endswith(".lua")
              or (n.endswith(SOURCE_EXT) and not n.startswith(("vendor/", "utils/")))]
    blobs = read_blobs(repo, rev, wanted)
    return compute_server_profile(tree, blobs.get)


# --------------------------------------------------------------------------- operations


def _repo(fork: str | None) -> Path:
    L = layout()
    repo = Path(os.path.abspath(fork)) if fork else L.fork
    if not (repo / ".git").exists():
        raise SatkError("NOT_READY", f"no fork checkout at {jpath(repo)}", hint="satk engine setup")
    return repo


def _registered(repo: Path) -> list[dict]:
    """``git worktree list --porcelain`` as dicts ``{path, head, branch, bare, locked, prunable}``."""
    out = git("worktree", "list", "--porcelain", cwd=repo, no_lazy=True)
    items: list[dict] = []
    cur: dict = {}
    for line in out.splitlines() + [""]:
        if not line.strip():
            if cur:
                items.append(cur)
                cur = {}
            continue
        key, _, val = line.partition(" ")
        if key == "worktree":
            cur["path"] = val.strip()
        elif key == "HEAD":
            cur["head"] = val.strip()
        elif key == "branch":
            cur["branch"] = val.strip().removeprefix("refs/heads/")
        elif key in ("bare", "detached", "locked", "prunable"):
            cur[key] = val.strip() or True
    return items


def _dir_stats(path: Path) -> tuple[int, int]:
    """(files, bytes) below ``path`` without ``.git``."""
    n = size = 0
    for dirpath, dirnames, filenames in os.walk(path):
        if dirpath == str(path):
            dirnames[:] = [d for d in dirnames if d != ".git"]
        for f in filenames:
            n += 1
            try:
                size += os.lstat(os.path.join(dirpath, f)).st_size
            except OSError:
                pass
    return n, size


_TEMPLATE_HEADER = ("<!-- DELETING THIS FILE IS NOT RECOMMENDED ('mtaserver.conf.template')!\n"
                    "     It is automatically used by the server for inserting missing settings into 'mtaserver.conf' "
                    "on startup.\n-->\n")


def _copy_file(src: Path, dst: Path, *, overwrite: bool) -> bool:
    """Atomic copy; returns True when ``dst`` was written (an existing identical file is left alone)."""
    if dst.is_file():
        if not overwrite or (dst.stat().st_size == src.stat().st_size and dst.read_bytes() == src.read_bytes()):
            return False
    ensure_writable(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_name(dst.name + ".part")
    part.write_bytes(src.read_bytes())
    os.replace(part, dst)
    return True


def install_server_deps(L: Layout) -> dict:
    """What the server needs next to its executable, the offline part of the fork's ``install_data`` action:

    * the pinned x64 ``net.dll`` (and ``net_d.dll``) from ``engine/deps``, checked against ``deps-lock.json``;
    * ``Shared/data/MTA San Andreas/server`` copied over ``Bin/server`` (OpenSSL and MySQL DLLs);
    * ``Server/mods/deathmatch/*.conf`` and ``*.xml`` into ``Bin/server/mods/deathmatch`` when absent, plus the
      ``mtaserver.conf.template`` the server merges missing settings from.

    Nothing is downloaded. A missing pin or folder is reported in ``notes`` and skipped; a pinned file that does
    not match its hash raises ``REVISION``.
    """
    from .setup import load_lock

    notes: list[str] = []
    done: list[str] = []
    lock = {it.get("id"): it for it in load_lock(L).get("items", []) if isinstance(it, dict)}
    pin = lock.get("net-x64")
    if not pin or not (L.deps / pin.get("file", "")).is_file():
        notes.append("net-x64 is not pinned in engine/deps-lock.json (satk engine setup --deps): no net.dll")
    else:
        src = L.deps / pin["file"]
        if sha256_file(src) != pin["sha256"]:
            raise SatkError("REVISION", "engine/deps net_64.dll does not match deps-lock.json",
                            hint="satk engine doctor --deep")
        for name in ("net.dll", "net_d.dll"):
            if _copy_file(src, L.bin / "server" / "x64" / name, overwrite=True):
                done.append("x64/" + name)
    data = L.fork / "Shared" / "data" / "MTA San Andreas" / "server"
    if data.is_dir():
        for f in sorted(p for p in data.rglob("*") if p.is_file()):
            if _copy_file(f, L.bin / "server" / f.relative_to(data), overwrite=True):
                done.append(f.relative_to(data).as_posix())
    else:
        notes.append("Shared/data/MTA San Andreas/server not in the tree: no OpenSSL/MySQL DLLs")
    dm = L.fork / "Server" / "mods" / "deathmatch"
    out_dm = L.bin / "server" / "mods" / "deathmatch"
    for pattern in ("*.conf", "*.xml"):
        for f in sorted(dm.glob(pattern)) if dm.is_dir() else []:
            if _copy_file(f, out_dm / f.name, overwrite=False):
                done.append("mods/deathmatch/" + f.name)
    conf, tpl = out_dm / "mtaserver.conf", out_dm / "mtaserver.conf.template"
    if conf.is_file() and not tpl.is_file():
        ensure_writable(tpl)
        atomic_write(tpl, (_TEMPLATE_HEADER + conf.read_text(encoding="utf-8-sig")).encode("utf-8"))
        done.append("mods/deathmatch/mtaserver.conf.template")
    out = {"status": "ok" if done or not notes else "skipped", "installed": len(done)}
    if notes:
        out["notes"] = notes
    return out


def _meta_write(L: Layout, data: dict) -> None:
    ensure_writable(L.meta)
    L.meta.parent.mkdir(parents=True, exist_ok=True)
    write_json(L.meta, data)


def create(path: str, branch: str | None, base: str = "main", profile: str = "server",
           fork: str | None = None) -> dict:
    """``git worktree add --no-checkout -b <branch> <path> <base>`` + sparse profile + checkout + deps."""
    if profile not in PROFILES:
        raise SatkError("BAD_PARAMS", f"unknown profile {profile!r}", did_you_mean=list(PROFILES))
    if not branch:
        raise SatkError("BAD_PARAMS", "branch is required", hint="--branch feat/<lane>-base")
    repo = _repo(fork)
    wt = check_wt_path(path)
    if wt.exists():
        raise SatkError("EXISTS", f"{jpath(wt)} already exists", hint="pick another name or `engine worktree remove`")
    git("check-ref-format", "--branch", branch, cwd=repo, no_lazy=True)
    if git("rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", cwd=repo, check=False, no_lazy=True):
        raise SatkError("EXISTS", f"branch {branch} already exists in the fork",
                        hint="choose a new branch name; worktree create makes the branch from the base")
    base_rev = git("rev-parse", "--verify", f"{base}^{{commit}}", cwd=repo, check=False, no_lazy=True)
    if not base_rev:
        raise SatkError("NOT_FOUND", f"base {base!r} is not a commit of the fork", hint="git -C <fork> branch -a")
    L = layout(wt)
    steps: list[list] = []
    t_all = time.perf_counter()

    def step(name: str, t0: float, extra: str = "") -> None:
        steps.append([name, round(time.perf_counter() - t0, 2), extra])

    with exclusive("worktree"):
        t0 = time.perf_counter()
        prof = compute_profile(repo, base_rev, profile)
        step("profile", t0, f"{len(prof.full_dirs)} folders, {len(prof.files)} single files")
        cfg_before = git("config", "--get", "extensions.worktreeConfig", cwd=repo, check=False, no_lazy=True)
        created = False
        try:
            t0 = time.perf_counter()
            git("worktree", "add", "--no-checkout", "-b", branch, str(wt), base, cwd=repo, no_lazy=True)
            created = True
            step("worktree add --no-checkout", t0)
            if profile == "server":
                t0 = time.perf_counter()
                patterns = prof.patterns()
                git_raw("sparse-checkout", "set", "--no-cone", "--stdin", cwd=wt,
                        input=("\n".join(patterns) + "\n").encode("utf-8"))
                step("sparse-checkout set", t0, f"{len(patterns)} patterns")
            t0 = time.perf_counter()
            git("read-tree", "-m", "-u", "HEAD", cwd=wt, no_lazy=True, timeout=1800)
            step("read-tree -m -u HEAD", t0)
            missing = [d for d in prof.full_dirs if not (wt / d).is_dir()] + \
                      [f for f in prof.files if not (wt / f).is_file()]
            if missing:
                raise SatkError("EXTERNAL_TOOL", f"sparse checkout is missing {len(missing)} wanted path(s): "
                                f"{', '.join(missing[:5])}", hint="the profile patterns and the tree disagree")
            L = layout(wt)
            _meta_write(L, {"id": L.fork_id, "path": jpath(wt), "profile": profile, "branch": branch, "base": base,
                            "base_rev": base_rev, "repo": jpath(repo), "projects": prof.projects,
                            "created": time.strftime("%Y-%m-%dT%H:%M:%S")})
            deps = None
            if profile == "server":
                t0 = time.perf_counter()
                deps = install_server_deps(L)
                step("deps", t0, f"{deps['status']}, {deps['installed']} file(s)" + (f"; {deps['notes'][0]}" if deps.get("notes") else ""))
        except BaseException:
            if created:  # roll back what this call made, nothing else
                git("worktree", "remove", "--force", str(wt), cwd=repo, check=False, no_lazy=True)
                git("branch", "-D", branch, cwd=repo, check=False, no_lazy=True)
                L.meta.unlink(missing_ok=True)
            raise
        cfg_after = git("config", "--get", "extensions.worktreeConfig", cwd=repo, check=False, no_lazy=True)
    nfiles, nbytes = _dir_stats(wt)
    head = git("rev-parse", "--short=9", "HEAD", cwd=wt, no_lazy=True)
    out = {
        "ok": True, "fork": jpath(wt), "fork_id": L.fork_id, "branch": branch, "base": base, "head": head,
        "profile": profile, "files": nfiles, "size_mb": round(nbytes / 1e6, 1),
        "seconds": round(time.perf_counter() - t_all, 2),
        "cols": ["step", "seconds", "note"], "rows": steps, "n": len(steps), "total": len(steps), "next": None,
        "folders": prof.full_dirs, "single_files": prof.files,
        "use": f"satk engine build --fork {jpath(wt)} --project server --platform x64",
    }
    if cfg_after != cfg_before and cfg_after == "true":
        out["warn"] = ["REPO_CONFIG: git enabled extensions.worktreeConfig in the fork repository (once; "
                       "keeps the sparse flag per worktree)"]
    return out


def _guess_profile(L: Layout) -> str:
    data = read_json(L.meta, {}) or {}
    return data.get("profile", "?") if isinstance(data, dict) else "?"


def refresh(path: str, fork: str | None = None) -> dict:
    """Recompute an existing managed worktree's recorded profile at its current HEAD.

    Git preserves local changes (no force/reset/read-tree). Build generation is invalidated after applying
    the profile, since newly materialised sources can change premake globs without changing the git tree.
    """
    repo = _repo(fork)
    wt = check_wt_path(path)
    if not wt.is_dir() or not any(same_path(w["path"], wt) for w in _registered(repo)):
        raise SatkError("NOT_FOUND", f"{jpath(wt)} is not an existing worktree of {jpath(repo)}",
                        hint="satk engine worktree list")
    L = layout(wt)
    meta = read_json(L.meta, {}) or {}
    profile = meta.get("profile") if isinstance(meta, dict) else None
    if profile not in PROFILES:
        raise SatkError("NOT_READY", f"no recorded server/full profile for {jpath(wt)}",
                        hint="refresh accepts worktrees made by satk engine worktree create")
    for target in (L.build_dir, L.meta, L.state):
        ensure_writable(target)
    # Sparse checkout writes the worktree index/info and may enable extensions.worktreeConfig in the
    # common repository. Validate the resolved metadata too (a .git file can point outside the checkout).
    for flag in ("--absolute-git-dir", "--git-common-dir"):
        metadata = git("rev-parse", flag, cwd=wt, no_lazy=True)
        for rel in ("", "index", "config", "config.worktree", "info/sparse-checkout"):
            ensure_writable(wt / metadata / rel)
    with exclusive("worktree"), exclusive(L.lock_name):
        head = git("rev-parse", "HEAD", cwd=wt, no_lazy=True)
        prof = compute_profile(wt, head, profile)
        for target in [wt / p for p in prof.full_dirs + prof.files]:
            ensure_writable(target)
        if profile == "server":
            git_raw("sparse-checkout", "set", "--no-cone", "--stdin", cwd=wt,
                    input=("\n".join(prof.patterns()) + "\n").encode("utf-8"))
        else:
            git("sparse-checkout", "disable", cwd=wt, no_lazy=True)
        missing = [d for d in prof.full_dirs if not (wt / d).is_dir()] + \
                  [f for f in prof.files if not (wt / f).is_file()]
        if missing:
            raise SatkError("EXTERNAL_TOOL", f"sparse checkout is missing {len(missing)} wanted path(s): "
                            f"{', '.join(missing[:5])}", hint="check local changes and the premake includes")
        state = read_json(L.state, {}) or {}
        if isinstance(state, dict) and "gen" in state:
            state.pop("gen")
            write_json(L.state, state)
        _meta_write(L, {**meta, "projects": prof.projects, "refreshed_rev": head})
    return {"ok": True, "fork": jpath(wt), "fork_id": L.fork_id, "head": head[:9], "profile": profile,
            "folders": prof.full_dirs, "single_files": prof.files, "projects": prof.projects,
            "hint": f"satk engine build --fork {jpath(wt)} --project server --platform x64"}


def listing(fork: str | None = None, sizes: bool = False) -> dict:
    """Worktrees of the fork repository, with the satk registry data of those under ``work/wt``."""
    repo = _repo(fork)
    root = wt_root()
    rows = []
    for w in _registered(repo):
        p = Path(w["path"])
        managed = same_path(p.parent, root)
        L = layout(p)
        row = [p.name if managed else jpath(p), L.fork_id or "(configured)", w.get("branch") or "(detached)",
               (w.get("head") or "")[:9], _guess_profile(L) if managed else ("full" if not L.is_default else "(configured)"),
               managed, p.is_dir()]
        if sizes:
            n, b = _dir_stats(p) if p.is_dir() else (0, 0)
            row += [n, round(b / 1e6, 1)]
        rows.append(row)
    cols = ["name", "fork_id", "branch", "head", "profile", "managed", "exists"] + (["files", "size_mb"] if sizes else [])
    return {"ok": True, "cols": cols, "rows": rows, "n": len(rows), "total": len(rows), "next": None,
            "repo": jpath(repo), "root": jpath(root)}


def remove(path: str, force: bool = False, delete_branch: bool = False, fork: str | None = None) -> dict:
    """``git worktree remove`` of a satk-managed worktree; the branch is deleted only on request (``-d``)."""
    repo = _repo(fork)
    wt = check_wt_path(path)
    reg = [w for w in _registered(repo) if same_path(w["path"], wt)]
    if not reg:
        raise SatkError("NOT_FOUND", f"{jpath(wt)} is not a worktree of {jpath(repo)}",
                        hint="satk engine worktree list")
    L = layout(wt)
    lockf = L.build_dir / f".{L.lock_name}.lock"
    if lockf.is_file():
        try:
            pid = int(lockf.read_text(encoding="utf-8").split()[0])
        except (OSError, ValueError, IndexError):
            pid = -1
        if pid_alive(pid):
            raise SatkError("BUSY", f"a build runs in {jpath(wt)} (pid {pid})", hint="wait for it")
    ensure_removable(wt)
    meta = read_json(L.meta, {}) or {}
    branch = reg[0].get("branch")
    args = ["worktree", "remove"] + (["--force"] if force else []) + [str(wt)]
    t0 = time.perf_counter()
    with exclusive("worktree"):
        git(*args, cwd=repo, no_lazy=True, timeout=1800)
        deleted = None
        if delete_branch:
            if not branch or meta.get("branch") != branch:
                raise SatkError("BAD_PARAMS", f"branch {branch!r} was not created by `engine worktree create`; "
                                "it is left alone", data={"removed_worktree": True})
            git("branch", "-d", branch, cwd=repo, no_lazy=True)
            deleted = branch
    L.meta.unlink(missing_ok=True)
    L.state.unlink(missing_ok=True)
    out = {"ok": True, "removed": jpath(wt), "seconds": round(time.perf_counter() - t0, 2)}
    if deleted:
        out["branch_deleted"] = deleted
    elif branch:
        out["branch_kept"] = branch
    return out
