"""Public snapshot of the repository (``satk dev export-public``) and the public checks of ``satk dev release``.

The development repository is private: its history names the development machine, internal plans and
files about the maintainer's own game install. The public repository gets one snapshot commit per public
release instead, made from the files of a commit of the development repository:

1. :func:`satk.release.tree.export` reads the commit (``git archive``, never the working tree);
2. :func:`load_rules` reads ``data/public-exclude.txt`` of that commit (gitignore-style patterns plus
   ``@allow`` audit exceptions) and :func:`apply_excludes` drops the matching files;
3. :func:`audit` checks every remaining file (:data:`RULES`) and the export stops on any finding;
4. :func:`write_snapshot` writes the files as a git repository: a fresh repository with one root commit,
   or a clone of the public repository (``base``) plus one commit that replaces the whole tree; the commit
   is tagged ``v<version>`` and its blobs are compared with the development commit;
5. :func:`run_tests` runs the snapshot's own non-game test suite with an empty configuration, so the
   development workspace is not visible to it.

``satk dev release`` applies the same exclude list to the zip, the wheel and the sdist, and
:func:`check_release_files` reads the built archives back.

Standard library only (plus :mod:`satk.core.assetguard`).
"""

from __future__ import annotations

import bisect
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable, Iterator

from ..core import assetguard as _ag
from ..core.errors import SatkError
from ..core.paths import ensure_removable, ensure_writable, jpath
from .tree import Tree

__all__ = ["EXCLUDE_FILE", "RULES", "SIZE_LIMIT", "USER_DOCS", "Pattern", "Allow", "Rules", "Finding",
           "compile_pattern", "parse_rules", "load_rules", "apply_excludes", "audit", "group", "machine_hits",
           "unused_patterns", "write_snapshot", "fresh_env", "checkout_venv", "run_tests", "check_release_files",
           "report", "COMMIT_SUBJECT"]

#: The exclude list, relative to the repository root.
EXCLUDE_FILE = "data/public-exclude.txt"
#: Audit rules (the names ``@allow`` lines use).
RULES = ("machine-path", "plan-ref", "email", "secret", "private-repo", "asset", "large-file", "dangling-ref")
#: Files above this size are refused (a public source repository has no business with them).
SIZE_LIMIT = 5 * 1024 * 1024
#: Files read by people (gitignore-style, anchored): internal plan references are refused here only.
USER_DOCS = ("/*.md", "/docs/", "/.github/", "/packaging/**/*.md", "/packaging/**/*.txt",
             "/proto/**/*.md", "/.claude-plugin/", "/blender/**/*.md", "/mta-resources/**/*.md")
#: Subject of the snapshot commit; ``{version}`` is the satk version of the exported commit.
COMMIT_SUBJECT = "satk {version} public preview"

# --------------------------------------------------------------------------- patterns of the audit

_SEP = r"(?:\\\\|\\|/)+"  # one folder separator as text: "\", an escaped "\\" or "/"
#: Folders of the development machine on its data drive (the workspace and the tools folder).
_MACHINE = re.compile(rf"(?i)(?<![A-Za-z0-9])D:{_SEP}(?:Games|files)(?![A-Za-z0-9_])"
                      r"|(?<![A-Za-z0-9_.-])(?:/mnt)?/d/(?:Games|files)(?![A-Za-z0-9_])")
#: Names in an account folder path that are placeholders or built-in accounts, not a person.
_HOME_OK = ("Public", "Default", "Default User", "All Users", "USERNAME", "YourName", "you", "name")
#: A Windows account folder with a concrete account name (the name is group 1).
_HOME = re.compile(rf"(?i)(?<![A-Za-z0-9])(?:C:{_SEP}|/c/)Users{_SEP}"
                   rf"(?!(?:{'|'.join(re.escape(n) for n in _HOME_OK)})(?![A-Za-z0-9_])|[<(%$*{{\[])"
                   r"([^\s\\/\"'`<>|:*?,;)\]]+)")
#: The development machine's own account folder (also checked inside third-party files of the zip).
_OWN_HOME = re.compile(rf"(?i)(?<![A-Za-z0-9])(?:C:{_SEP}|/c/)Users{_SEP}User(?![A-Za-z0-9_])")
_PLAN = re.compile(r"docs[\\/]+design\b|\bSPEC\b|\b(?:M2-PLAN|M3-CANDIDATES|M3-EXECUTION|PAUSE-STATE)\b"
                   r"|\bPORTABILITY\.md|\bROADMAP\.md|\bWP-\d+|\bM[1-9]-\d+|\bM[1-9][- ](?:lane )?[A-C]\d\b"
                   r"|\bM[1-3]\b|\blane [A-C]\d\b")
_EMAIL = re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9][A-Za-z0-9._%+-]*@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*"
                    r"\.([A-Za-z]{2,24}))(?![A-Za-z0-9-])")
#: Addresses that may appear anywhere: the maintainer's commit alias and no-reply addresses.
_EMAIL_OK = frozenset({"qx@li.es"})
#: "TLDs" that are file extensions (``a@b.txd`` is a file name, not an address).
_FILE_EXTS = frozenset({"txd", "dff", "img", "col", "ifp", "ipl", "ide", "dat", "png", "jpg", "jpeg", "py", "md",
                        "json", "txt", "toml", "cmd", "ps1", "exe", "dll", "asi", "lua", "cpp", "h", "sh", "zip"})
_SECRET = re.compile(
    r"(?<![A-Za-z0-9_])sk[-_](?:live|test|ant|proj|or)[-_][A-Za-z0-9_-]{16,}"
    r"|(?<![A-Za-z0-9_-])sk-[A-Za-z0-9]{32,}"
    r"|\bgh[pousr]_[A-Za-z0-9]{30,}"
    r"|\bgithub_pat_[A-Za-z0-9_]{40,}"
    r"|\bAKIA[0-9A-Z]{16}\b"
    r"|\bxox[abprs]-[A-Za-z0-9-]{10,}"
    r"|\bAIza[0-9A-Za-z_-]{35}"
    r"|-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----"
    r"|(?i:rout[m]y)")  # the key name of a model router the maintainer uses
#: Repositories of the maintainer other than the public one (private: links to them are dead).
_PRIVATE_REPO = re.compile(r"(?i)\bqxlies/(?!satk-gta(?:\.git)?(?![A-Za-z0-9_.-]))[A-Za-z0-9_.-]+")


# --------------------------------------------------------------------------- the exclude list


def _glob_regex(pat: str) -> str:
    """One gitignore glob (no ``!``, no trailing ``/``) as a regex fragment over ``/``-separated paths."""
    out: list[str] = []
    i = 0
    while i < len(pat):
        c = pat[i]
        if pat.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pat.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "[" and pat.find("]", i + 2) != -1:
            j = pat.find("]", i + 2)
            body = pat[i + 1:j]
            if body.startswith("!"):
                body = "^" + body[1:]
            out.append("[" + body.replace("\\", "\\\\") + "]")
            i = j + 1
        elif c == "\\" and i + 1 < len(pat):
            out.append(re.escape(pat[i + 1]))
            i += 2
        else:
            out.append(re.escape(c))
            i += 1
    return "".join(out)


@dataclass(frozen=True)
class Pattern:
    """One line of the exclude list."""

    text: str
    negate: bool
    regex: re.Pattern

    def match(self, path: str) -> bool:
        return self.regex.match(path) is not None


def compile_pattern(line: str) -> Pattern | None:
    """A gitignore line as a :class:`Pattern` (``None`` for blank lines and comments)."""
    raw = line.strip()
    if not raw or raw.startswith("#"):
        return None
    negate = raw.startswith("!")
    body = raw[1:] if negate else raw
    if body.startswith(("\\#", "\\!")):
        body = body[1:]
    dir_only = body.endswith("/")
    body = body.rstrip("/")
    if not body:
        raise SatkError("BAD_PARAMS", f"{EXCLUDE_FILE}: empty pattern {line.strip()!r}")
    anchored = "/" in body
    body = body.lstrip("/")
    prefix = "" if anchored else "(?:.*/)?"
    suffix = "/.*" if dir_only else "(?:/.*)?"
    return Pattern(raw, negate, re.compile(f"^{prefix}{_glob_regex(body)}{suffix}$", re.S))


@dataclass(frozen=True)
class Allow:
    """``@allow <rule> <path pattern> [<regex>]``: findings of ``rule`` in matching files are accepted."""

    rule: str
    path: Pattern
    text: re.Pattern | None
    line: str

    def covers(self, rule: str, path: str, found: str) -> bool:
        return (rule == self.rule and self.path.match(path)
                and (self.text is None or self.text.search(found) is not None))


@dataclass
class Rules:
    """The parsed exclude list."""

    patterns: list[Pattern] = field(default_factory=list)
    allows: list[Allow] = field(default_factory=list)
    source: str = ""

    def excluded(self, path: str) -> Pattern | None:
        """The pattern that excludes ``path`` (last match wins), else ``None``."""
        hit: Pattern | None = None
        for p in self.patterns:
            if p.match(path):
                hit = None if p.negate else p
        return hit

    def allowed(self, rule: str, path: str, found: str) -> bool:
        return any(a.covers(rule, path, found) for a in self.allows)


def parse_rules(text: str, source: str = EXCLUDE_FILE) -> Rules:
    """Parse the exclude list (patterns and ``@allow`` lines)."""
    rules = Rules(source=source)
    for n, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if s.startswith("@"):
            parts = s.split(None, 3)
            if parts[0] != "@allow" or len(parts) < 3 or parts[1] not in RULES:
                raise SatkError("BAD_PARAMS", f"{source}:{n}: expected '@allow <rule> <path pattern> [<regex>]' "
                                f"with a rule of {', '.join(RULES)}: {s}")
            path = compile_pattern(parts[2])
            try:
                text_re = re.compile(parts[3]) if len(parts) > 3 else None
            except re.error as e:
                raise SatkError("BAD_PARAMS", f"{source}:{n}: bad regex: {e}") from None
            if path is None or path.negate:
                raise SatkError("BAD_PARAMS", f"{source}:{n}: bad path pattern {parts[2]!r}")
            rules.allows.append(Allow(parts[1], path, text_re, s))
            continue
        p = compile_pattern(line)
        if p is not None:
            rules.patterns.append(p)
    return rules


def load_rules(tree: Tree, fallback: Path | None = None) -> Rules:
    """The exclude list of the exported commit; a commit without one uses ``fallback`` (this checkout's)."""
    if EXCLUDE_FILE in tree.files:
        return parse_rules(tree.text(EXCLUDE_FILE), f"{tree.commit[:9]}:{EXCLUDE_FILE}")
    if fallback is None:
        from ..core.config import REPO_ROOT

        fallback = REPO_ROOT / EXCLUDE_FILE
    try:
        return parse_rules(Path(fallback).read_text(encoding="utf-8"), jpath(fallback))
    except FileNotFoundError:
        raise SatkError("NOT_FOUND", f"no {EXCLUDE_FILE} in commit {tree.commit[:9]} or in this checkout",
                        hint=f"create {EXCLUDE_FILE} (gitignore-style patterns) and commit it") from None


def apply_excludes(tree: Tree, rules: Rules) -> tuple[Tree, list[tuple[str, str]]]:
    """``tree`` without the excluded files, and ``[(path, pattern)]`` of what was dropped."""
    kept: dict[str, bytes] = {}
    dropped: list[tuple[str, str]] = []
    for path in sorted(tree.files):
        p = rules.excluded(path)
        if p is None:
            kept[path] = tree.files[path]
        else:
            dropped.append((path, p.text))
    return Tree(commit=tree.commit, timestamp=tree.timestamp, files=kept), dropped


def unused_patterns(tree: Tree, rules: Rules) -> list[str]:
    """Exclude patterns that match no file of ``tree`` (stale lines)."""
    return [p.text for p in rules.patterns if not p.negate and not any(p.match(f) for f in tree.files)]


# --------------------------------------------------------------------------- audit


@dataclass(frozen=True, order=True)
class Finding:
    path: str
    line: int
    rule: str
    text: str
    context: str = ""

    def row(self) -> list:
        return [self.path, self.line, self.rule, self.text]


def _is_text(data: bytes) -> bool:
    return b"\0" not in data[:8192]


def _is_licence(path: str) -> bool:
    name = PurePosixPath(path).name.upper()
    return (name.startswith(("LICENSE", "LICENCE", "COPYING", "NOTICE", "AUTHORS", "CONTRIBUTORS"))
            or path.startswith("data/notices/"))


def _email_ok(match: re.Match, path: str) -> bool:
    addr, domain, tld = match.group(0).lower(), match.group(1).lower(), match.group(2).lower()
    local = addr.split("@", 1)[0]
    if tld in _FILE_EXTS or addr in _EMAIL_OK or _is_licence(path):
        return True
    if local in ("noreply", "no-reply", "git") or domain.endswith("noreply.github.com"):
        return True
    return (domain in ("example.com", "example.net", "example.org") or domain.startswith("example.")
            or domain.endswith((".example", ".invalid", ".test", ".localhost", ".example.com", ".example.org")))


def machine_hits(text: str, *, own_only: bool = False) -> Iterator[re.Match]:
    """Machine paths in ``text``; ``own_only`` = only the development machine's own folders and account."""
    yield from _MACHINE.finditer(text)
    if own_only:
        yield from _OWN_HOME.finditer(text)
    else:
        yield from _HOME.finditer(text)


def _line_of(pos: int, starts: list[int]) -> int:
    return bisect.bisect_right(starts, pos)


def _parse_assetguard_allow(text: str) -> dict[str, set[str]]:
    """``.assetguard-allow`` of the tree (the format of :func:`satk.core.assetguard.load_allowlist`)."""
    out: dict[str, set[str]] = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        parts = line.split(None, 1)
        if len(parts) == 2 and len(parts[0]) == 64:
            out.setdefault(parts[1].strip().replace("\\", "/").lower(), set()).add(parts[0].lower())
    return out


def audit(tree: Tree, rules: Rules, *, dropped: Iterable[str] = ()) -> list[Finding]:
    """Every finding in ``tree`` (already filtered by the exclude list), sorted; ``[]`` = publishable."""
    docs = [compile_pattern(g) for g in USER_DOCS]
    allow_assets = _parse_assetguard_allow(tree.files.get(_ag.ALLOWLIST_FILE, b"").decode("utf-8", "replace"))
    gone = sorted(set(dropped))
    out: list[Finding] = []
    for path in sorted(tree.files):
        data = tree.files[path]
        found: list[tuple[str, int, str]] = []  # (rule, offset or -1, text)
        if len(data) > SIZE_LIMIT:
            found.append(("large-file", -1, f"{len(data)} bytes > {SIZE_LIMIT}"))
        for f in _ag.check_blob(path, data[:64], len(data)):
            if f.rule == "large-file":
                continue
            shas = allow_assets.get(path.lower())
            if shas and hashlib.sha256(data).hexdigest() in shas:
                continue
            found.append(("asset", -1, f"{f.rule}: {f.detail}"))
        lines: list[int] = []
        text = ""
        if _is_text(data):
            text = data.decode("utf-8", "replace")
            lines = [0] + [m.end() for m in re.finditer("\n", text)]
            found += [("machine-path", m.start(), m.group(0)) for m in machine_hits(text)]
            found += [("secret", m.start(), m.group(0)) for m in _SECRET.finditer(text)]
            found += [("private-repo", m.start(), m.group(0)) for m in _PRIVATE_REPO.finditer(text)]
            found += [("email", m.start(), m.group(0)) for m in _EMAIL.finditer(text) if not _email_ok(m, path)]
            if any(d is not None and d.match(path) for d in docs):
                found += [("plan-ref", m.start(), m.group(0)) for m in _PLAN.finditer(text)]
                for g in gone:
                    for spelled in {g, g.replace("/", "\\")}:
                        found += [("dangling-ref", m.start(), g) for m in re.finditer(re.escape(spelled), text)]
        for rule, pos, hit in found:
            if rules.allowed(rule, path, hit):
                continue
            if pos < 0:
                out.append(Finding(path, 0, rule, hit))
                continue
            n = _line_of(pos, lines)
            ctx = text[lines[n - 1]:lines[n] if n < len(lines) else len(text)].strip()
            out.append(Finding(path, n, rule, hit[:120], ctx[:200]))
    return sorted(set(out))


def group(findings: list[Finding]) -> list[list]:
    """One row per (file, rule): ``[path, rule, hits, first line, first text]``, sorted by path and rule."""
    by: dict[tuple[str, str], list[Finding]] = {}
    for f in findings:
        by.setdefault((f.path, f.rule), []).append(f)
    rows = [[p, r, len(fs), fs[0].line, fs[0].text] for (p, r), fs in by.items()]
    return sorted(rows, key=lambda r: (r[0], r[1]))


# --------------------------------------------------------------------------- the snapshot repository


def _git(cwd: Path, *args: str, env: dict[str, str] | None = None, check: bool = True,
         timeout: int = 600) -> subprocess.CompletedProcess:
    try:
        p = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, env=env, timeout=timeout)
    except FileNotFoundError:
        raise SatkError("DEPENDENCY", "git is not installed or not on PATH") from None
    if check and p.returncode != 0:
        err = (p.stderr or p.stdout).decode("utf-8", "replace").strip()
        raise SatkError("EXTERNAL_TOOL", f"git {' '.join(args[:2])} failed: {err[-400:]}")
    return p


def _out(p: subprocess.CompletedProcess) -> str:
    return p.stdout.decode("utf-8", "replace").strip()


def _identity(repo: Path) -> tuple[str, str]:
    name = _out(_git(repo, "config", "user.name", check=False))
    email = _out(_git(repo, "config", "user.email", check=False))
    if not name or not email:
        raise SatkError("NOT_READY", f"git user.name/user.email are not configured for {jpath(repo)}",
                        hint="git config user.name <name>; git config user.email <email>")
    return name, email


def _ls_tree(repo: Path, rev: str) -> dict[str, tuple[str, str]]:
    """``{path: (mode, blob)}`` of ``rev``."""
    raw = _git(repo, "ls-tree", "-r", "-z", "--full-tree", rev).stdout.decode("utf-8")
    out: dict[str, tuple[str, str]] = {}
    for item in raw.split("\0"):
        if item:
            meta, path = item.split("\t", 1)
            mode, _kind, sha = meta.split()
            out[path] = (mode, sha)
    return out


def _force_remove(func: Callable, path: str, _exc: BaseException) -> None:
    os.chmod(path, stat.S_IWRITE)  # git objects are read-only on Windows
    func(path)


def _rmtree(path: Path) -> None:
    ensure_removable(path)
    for attempt in range(20):
        try:
            shutil.rmtree(path, onexc=_force_remove)
            return
        except FileNotFoundError:
            return
        except OSError:
            if attempt == 19:
                raise
            time.sleep(0.25)


def _previous_snapshot(dest: Path) -> bool:
    """``dest`` holds a snapshot this command wrote (a git repository whose last commit is ours)."""
    if not (dest / ".git").is_dir():
        return False
    p = _git(dest, "log", "-1", "--format=%s", check=False)
    return p.returncode == 0 and re.fullmatch(r"satk \S+ public preview", _out(p)) is not None


def _clear_worktree(dest: Path) -> None:
    for child in dest.iterdir():
        if child.name == ".git":
            continue
        if child.is_dir():
            _rmtree(child)
        else:
            ensure_removable(child)
            os.chmod(child, stat.S_IWRITE)
            child.unlink()


def write_snapshot(tree: Tree, dev_repo: Path, dest: Path, *, version: str, base: str | None = None,
                   log: Callable[[str], None] = lambda s: None) -> dict:
    """Write ``tree`` (already filtered and audited) as a git repository at ``dest``; see the module doc."""
    dest = ensure_writable(Path(os.path.abspath(dest)))
    if dest.exists() and any(dest.iterdir()):
        if not _previous_snapshot(dest):
            raise SatkError("EXISTS", f"{jpath(dest)} exists and is not a snapshot written by this command",
                            hint="pass another --out or remove the folder")
        log(f"replacing the previous snapshot {jpath(dest)}")
        _rmtree(dest)
    name, email = _identity(dev_repo)
    date = f"{tree.timestamp} +0000"
    inherited = {k: v for k, v in os.environ.items()  # a hook's repository variables would redirect git
                 if k.upper() not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                                      "GIT_OBJECT_DIRECTORY")}
    env = dict(inherited, GIT_AUTHOR_NAME=name, GIT_AUTHOR_EMAIL=email, GIT_AUTHOR_DATE=date,
               GIT_COMMITTER_NAME=name, GIT_COMMITTER_EMAIL=email, GIT_COMMITTER_DATE=date,
               GIT_TERMINAL_PROMPT="0")
    tag = f"v{version}"
    subject = COMMIT_SUBJECT.format(version=version)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if base:
        log(f"git clone {base}")
        p = subprocess.run(["git", "clone", "-q", base, str(dest)], capture_output=True, env=env, timeout=900)
        if p.returncode != 0:
            raise SatkError("EXTERNAL_TOOL", f"git clone {base} failed: "
                            f"{p.stderr.decode('utf-8', 'replace').strip()[-400:]}",
                            hint="--base takes a clone or the URL of the public repository")
        _clear_worktree(dest)
    else:
        dest.mkdir(parents=True, exist_ok=True)
        _git(dest, "init", "-q", "-b", "main", env=env)
    for path in sorted(tree.files):
        target = ensure_writable(dest.joinpath(*path.split("/")))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(tree.files[path])
    _git(dest, "add", "-A", env=env)
    modes = _ls_tree(dev_repo, tree.commit)
    exe = [p for p in sorted(tree.files) if modes.get(p, ("",))[0] == "100755"]
    if exe:
        _git(dest, "update-index", "--chmod=+x", "--", *exe, env=env)
    new_tree = _out(_git(dest, "write-tree", env=env))
    head = _git(dest, "rev-parse", "-q", "--verify", "HEAD^{commit}", check=False)
    has_head = head.returncode == 0
    old_tag = _git(dest, "rev-parse", "-q", "--verify", f"refs/tags/{tag}^{{tree}}", check=False)
    if old_tag.returncode == 0:
        if _out(old_tag) != new_tree:
            raise SatkError("EXISTS", f"the public repository already has {tag} with other files",
                            hint="bump the version (pyproject.toml and satk.__version__) and export again")
        status = "unchanged"
    elif has_head and _out(_git(dest, "rev-parse", "HEAD^{tree}")) == new_tree:
        _git(dest, "tag", "-a", tag, "-m", subject, env=env)
        status = "tagged"
    else:
        _git(dest, "commit", "-q", "-m", subject, env=env)
        _git(dest, "tag", "-a", tag, "-m", subject, env=env)
        status = "committed"
    # The public commit must hold exactly the development commit's blobs minus the excluded files.
    want = {p: modes[p] for p in tree.files if p in modes}
    got = _ls_tree(dest, "HEAD")
    diff = sorted(p for p in set(want) | set(got) if want.get(p) != got.get(p))
    if diff or len(want) != len(tree.files):
        raise SatkError("CHECK_FAILED", f"the snapshot differs from commit {tree.commit[:9]} in {len(diff)} file(s): "
                        f"{', '.join(diff[:5])}", hint="check .gitattributes of the exported commit")
    commit = _out(_git(dest, "rev-parse", "HEAD"))
    count = int(_out(_git(dest, "rev-list", "--count", "HEAD")))
    return {"repo": jpath(dest), "status": status, "public_commit": commit, "tag": tag,
            "branch": _out(_git(dest, "rev-parse", "--abbrev-ref", "HEAD")), "commits": count,
            "author": f"{name} <{email}>", "files": len(got), "base": base}


# --------------------------------------------------------------------------- the snapshot's own tests

_PYTEST_TAIL = re.compile(r"^=*\s*(\d+ (?:passed|failed|error).*?) in [\d.]+s", re.M)
#: The same deselection as ``satk dev gate`` (the real MTA-fork build writes outside the workspace).
DESELECT = ("tests/engine/test_real_fork.py",)


def fresh_env(snapshot: Path, scratch: Path, protect: Iterable[Path] = ()) -> dict[str, str]:
    """A fresh user's environment: no ``SATK_*``/``PYTHON*``/venv variables, an empty configuration
    folder and an empty workspace, tool discovery off; TEMP under ``scratch``. ``protect`` (the protected
    roots of the development workspace) stays protected through ``SATK_SAFETY_PROTECTED_ROOTS``."""
    drop = ("SATK_", "PYTHON", "VIRTUAL_ENV", "CONDA_", "PIP_")
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(drop)}
    for d in ("config", "home", "temp"):
        (scratch / d).mkdir(parents=True, exist_ok=True)
    env.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(snapshot / "src"),
               SATK_CONFIG=str(scratch / "config"), SATK_HOME=str(scratch / "home"), SATK_DETECT="0",
               TEMP=str(scratch / "temp"), TMP=str(scratch / "temp"))
    roots = sorted({jpath(r) for r in protect})
    if roots:
        env["SATK_SAFETY_PROTECTED_ROOTS"] = json.dumps(roots)
    return env


def _watch(roots: Iterable[Path]) -> dict[str, int]:
    """Modification times of ``roots`` and of their direct entries (a write into them changes one)."""
    out: dict[str, int] = {}
    for r in roots:
        try:
            out[jpath(r)] = Path(r).stat().st_mtime_ns
            for c in Path(r).iterdir():
                out[jpath(c)] = c.stat().st_mtime_ns
        except OSError:
            continue
    return out


def checkout_venv(snapshot: Path) -> Path:
    """``<snapshot>/.venv`` as a fresh clone has it after setup (CI does the same): a venv of this Python's
    base interpreter without pip whose site-packages also reads this environment's packages (a ``.pth``
    file), so the shims and their tests find the checkout's own venv. Returns its ``python``."""
    import sysconfig

    venv = ensure_writable(snapshot / ".venv")
    py = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not py.is_file():
        p = subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], capture_output=True,
                           timeout=300)
        if p.returncode != 0 or not py.is_file():
            raise SatkError("EXTERNAL_TOOL", "python -m venv failed: "
                            f"{p.stderr.decode('utf-8', 'replace').strip()[-300:]}")
    site = Path(sysconfig.get_path("purelib", vars={"base": str(venv), "platbase": str(venv)}))
    host = {sysconfig.get_path("purelib"), sysconfig.get_path("platlib")}
    # Inside such a venv (the snapshot's own tests) the packages come through its .pth file, and a .pth
    # is not read in a folder that another .pth added: pass those site-packages folders on as well.
    host |= {p for p in sys.path if p and Path(p).name.lower() == "site-packages" and Path(p).is_dir()}
    site.mkdir(parents=True, exist_ok=True)
    (site / "_satk_host_packages.pth").write_text("".join(f"{h}\n" for h in sorted(host)), encoding="utf-8")
    return py


def run_tests(snapshot: Path, scratch: Path, *, protect: Iterable[Path] = (), timeout: int = 2400) -> dict:
    """Run ``pytest -m "not game"`` of the snapshot with the venv of :func:`checkout_venv` in
    :func:`fresh_env`; returns a summary (the venv and ignored files are removed afterwards).

    Run it only on an audited snapshot: a test that names a folder of the development machine sees an
    empty configuration and, through the ``satk_home`` fixture, no ``SATK_*`` variable, so the write guard
    no longer covers that folder. ``protect`` is watched as a last line of defense: any change in those
    roots (or their direct entries) fails the run and is reported, never cleaned up here."""
    try:
        import pytest  # noqa: F401
    except ImportError:
        raise SatkError("DEPENDENCY", "pytest is not installed in this Python",
                        hint="run satk dev export-public from a checkout with the development venv") from None
    if scratch.exists():
        _rmtree(scratch)
    ensure_writable(scratch)
    protect = [Path(r) for r in protect]
    env = fresh_env(snapshot, scratch, protect)
    env["COLUMNS"] = "240"  # longer reasons in pytest's short summary
    py = checkout_venv(snapshot)
    argv = [str(py), "-m", "pytest", "-q", "-p", "no:cacheprovider", "-m", "not game",
            *[f"--deselect={d}" for d in DESELECT]]
    before = _watch(protect)
    t0 = time.perf_counter()
    try:
        p = subprocess.run(argv, cwd=snapshot, env=env, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
        code, text = p.returncode, p.stdout + p.stderr
    except subprocess.TimeoutExpired:
        code, text = -1, f"timeout after {timeout} s"
    finally:
        _rmtree(snapshot / ".venv")
        _git(snapshot, "clean", "-q", "-f", "-d", "-X", check=False)  # __pycache__ of subprocesses
    after = _watch(protect)
    touched = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    tail = _PYTEST_TAIL.findall(text)
    failed = re.findall(r"^(?:FAILED|ERROR) (\S+)", text, re.M)
    log_file = scratch / "pytest.log"
    log_file.write_text(text, encoding="utf-8")
    dirty = _out(_git(snapshot, "status", "--porcelain", check=False)).splitlines()
    res = {"ok": code == 0 and not touched, "summary": tail[-1] if tail else (text.strip().splitlines() or ["no output"])[-1][:200],
           "seconds": round(time.perf_counter() - t0, 1), "log": jpath(log_file)}
    if failed:
        res["failed"] = failed
    if dirty:
        res["left_in_snapshot"] = dirty[:20]
    if touched:
        res["touched_protected"] = touched[:20]
    return res


# --------------------------------------------------------------------------- built release files


def _archive_entries(path: Path) -> Iterator[tuple[str, str | None, bytes]]:
    """``(entry name, repository path or None for third-party files, bytes)`` of a zip, wheel or sdist."""
    if path.suffix in (".zip", ".whl"):
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                name = info.filename
                if path.suffix == ".whl":
                    if name.startswith("satk/_data/"):
                        rel: str | None = "data/" + name[len("satk/_data/"):]
                    elif name.startswith("satk/_vendor/"):
                        rel = "vendor/" + name[len("satk/_vendor/"):]
                    elif name.startswith("satk/"):
                        rel = "src/" + name
                    else:
                        rel = name  # *.dist-info: satk's own metadata
                else:
                    rel = name.split("/", 1)[1] if "/" in name else name
                    if rel.startswith("python/"):
                        rel = None  # the embeddable CPython and the third-party wheels
                yield name, rel, zf.read(info)
    elif path.name.endswith(".tar.gz"):
        with tarfile.open(path, "r:gz") as tf:
            for m in tf.getmembers():
                if m.isfile():
                    f = tf.extractfile(m)
                    yield m.name, m.name.split("/", 1)[1] if "/" in m.name else m.name, f.read() if f else b""
    else:
        raise SatkError("BAD_PARAMS", f"not a release archive: {path.name}")


def check_release_files(paths: Iterable[Path], rules: Rules, *, strict: bool) -> list[Finding]:
    """Read the built archives back: no excluded file inside; with ``strict`` also no machine path
    (satk's own files: every rule of :func:`machine_hits`; third-party files: the development machine's own)."""
    out: list[Finding] = []
    for arc in paths:
        for name, rel, data in _archive_entries(arc):
            where = f"{arc.name}!{name}"
            if rel is not None:
                p = rules.excluded(rel)
                if p is not None:
                    out.append(Finding(where, 0, "excluded", p.text))
            if strict and _is_text(data):
                text = data.decode("utf-8", "replace")
                for m in machine_hits(text, own_only=rel is None):
                    if rel is not None and rules.allowed("machine-path", rel, m.group(0)):
                        continue
                    out.append(Finding(where, text.count("\n", 0, m.start()) + 1, "machine-path", m.group(0)))
    return sorted(set(out))


def report(findings: list[Finding], dest: Path, **meta) -> Path:
    """Write every finding as JSON (``{"meta": ..., "findings": [[path, line, rule, text, context]]}``)."""
    from ..core.paths import atomic_write

    doc = {"meta": meta, "findings": [[f.path, f.line, f.rule, f.text, f.context] for f in findings]}
    return atomic_write(dest, json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
