"""Check that paths and links mentioned in Markdown files exist (``satk dev linkcheck``, WP-12).

What is checked (SPEC §5.3 WP-12, acceptance 1: "all mentioned paths exist"):

* Markdown links and images ``[text](target)`` / ``[text](<target with spaces>)``: relative to
  the file; ``#anchor`` parts are checked against the headings of the target ``.md`` (GitHub
  slugs). URLs (``http:``, ``mailto:`` ...) are not fetched.
* Absolute Windows paths (``C:\\ws\\tools``, ``C:/ws/work/...``) anywhere: prose,
  inline code and fenced blocks. Paths with spaces are found by trying the longest run of
  words that exists (``C:\\Program Files\\Blender Foundation\\...``) or by quotes.
* Inline code spans that are a whole relative path (``tools\\satk.cmd``, ``src/satk/core``). Bases, in
  order: the file's directory, the git checkout that contains it, the workspace (``paths.workspace``),
  ``paths.work`` (``cache\\tex``), the game copy (``data/gta.dat``), the original install
  (``paths.installed``: files the clean copy does not have, ``data/colorcycle.dat``) and, in a second
  pass, every directory the page itself mentions (``bin\\ariane.exe`` next to ``viewer\\ariane\\``). The
  span is ok when a base that has its first segment has the full path, missing otherwise.
  A span whose first segment is in no base is still reported (``missing``, with a "did you mean")
  when it is clearly a path (:func:`_looks_like_path`: ``toolz\\satk.cmd``, ``gta-sa-cleen\\``,
  ``doc/ROADMAP.md``); other spans (``tex:bistro/vent_64`` = a SID, ``satk.core.ids``, ``vanilla/installed``,
  a command line ``satk formats ls models\\gta3.img``, MCP method names like ``tools/list``:
  :data:`NOT_PATHS`) are not paths and are ignored.

Placeholders end a path: ``work\\tmp\\<wp-id>\\x`` checks ``work\\tmp``. Missing paths under the
work directory are reported as ``generated`` (a warning, not a failure: ``work`` is
regenerable, SPEC §2.1). "The work directory" is ``paths.work`` plus every extra ``generated`` root:
:func:`config_roots` adds the default ``<workspace>/work``, so a run with a redirected
``SATK_PATHS_WORK`` (the gate, a test's private work) still treats the shared ``work\\...`` paths that the
docs mention as generated, not missing. HTML comments are ignored; pragmas::

    some text D:\\nowhere <!-- linkcheck: ignore -->     (ignore this line)
    <!-- linkcheck: off --> ... <!-- linkcheck: on -->   (ignore a region)
"""

from __future__ import annotations

import os
import re
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

__all__ = ["Ref", "Finding", "extract_refs", "check_file", "check_paths", "config_roots", "slugify", "headings"]

_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})(.*)$")
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_COMMENT = re.compile(r"<!--(.*?)-->", re.S)
_CODE_SPAN = re.compile(r"(`+)(.+?)\1")
_LINK = re.compile(r"(!?)\[((?:[^\[\]]|\[[^\]]*\])*)\]\(\s*(<[^>]*>|[^)\s]+)(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)")
_ABS_START = re.compile(r"(?<![\w/\\.:])([A-Za-z]):[\\/]")
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]+:")
_PLACEHOLDER = re.compile(r"[<>{}*?%$…]|\.\.\.")
#: Characters that end a bare (unquoted) path word.
_STOP = set("`\"'|*?<>")
_TRAIL = ".,;:)]}»!"
#: A whole inline-code span that looks like a relative path (letters incl. Cyrillic, spaces).
_REL_SPAN = re.compile(r"^[\w .\-()+@<>{}$%…*]+(?:[\\/][\w .\-()+@<>{}$%…*]*)+$")
_MAX_WORDS = 8
#: Inline-code spans that look like relative paths but are protocol names (MCP/JSON-RPC methods): a
#: `tools/list` span would otherwise be checked against the workspace's ``tools\`` directory.
NOT_PATHS = frozenset({"tools/list", "tools/call", "resources/list", "resources/read", "prompts/list",
                       "notifications/progress", "notifications/initialized"})


@dataclass(frozen=True, slots=True)
class Ref:
    """A path or link mentioned in a Markdown file."""

    line: int
    kind: str  # "link" | "abs" | "rel"
    text: str  # as written (without quotes)
    candidates: tuple[str, ...] = ()  # abs paths with spaces: progressively longer variants
    anchor: str | None = None


@dataclass(frozen=True, slots=True)
class Finding:
    """Result for one reference. ``status``: ok | missing | generated | anchor."""

    file: str
    line: int
    ref: str
    status: str
    detail: str = ""
    path: str = ""  # resolved absolute path (ok: what exists; missing/generated: where it was expected)

    def row(self) -> list:
        return [self.file, self.line, self.ref, self.status, self.detail]


# --------------------------------------------------------------------------- extraction


def _strip_comments(text: str) -> tuple[str, set[int]]:
    """Blank out HTML comments (keeping line numbers); return ignored line numbers (pragmas)."""
    ignored: set[int] = set()
    off_from: int | None = None
    lines_before = 0
    pos = 0
    for m in _COMMENT.finditer(text):
        lines_before += text.count("\n", pos, m.start())
        pos = m.start()
        body = m.group(1).strip().lower()
        line_no = lines_before + 1
        if body.startswith("linkcheck:"):
            what = body.split(":", 1)[1].strip()
            if what == "ignore":
                ignored.add(line_no)
            elif what == "off" and off_from is None:
                off_from = line_no
            elif what == "on" and off_from is not None:
                ignored.update(range(off_from, line_no + 1))
                off_from = None
    if off_from is not None:
        ignored.update(range(off_from, text.count("\n") + 2))
    stripped = _COMMENT.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    return stripped, ignored


def _abs_refs(fragment: str, line: int) -> Iterator[Ref]:
    """Absolute Windows paths in a piece of text (prose, code span or code line)."""
    for m in _ABS_START.finditer(fragment):
        start = m.start()
        quote = fragment[start - 1] if start > 0 and fragment[start - 1] in "\"'" else None
        if quote:
            end = fragment.find(quote, start)
            text = fragment[start:end if end >= 0 else len(fragment)]
            yield Ref(line, "abs", text, (text,))
            continue
        # bare path: words separated by single spaces, stop at a stop char or two spaces
        rest = fragment[start:]
        stop = len(rest)
        for i, ch in enumerate(rest):
            if ch in _STOP or ch == "\t" or rest.startswith("  ", i):
                stop = i
                break
        words = rest[:stop].split(" ")
        cands: list[str] = []
        acc = ""
        for w in words[:_MAX_WORDS]:
            acc = f"{acc} {w}" if acc else w
            c = acc.rstrip(_TRAIL)
            if c and (not cands or cands[-1] != c):
                cands.append(c)
        if cands:
            yield Ref(line, "abs", cands[0], tuple(cands))


def extract_refs(text: str) -> list[Ref]:
    """All path/link references of a Markdown text (see the module docstring)."""
    stripped, ignored = _strip_comments(text)
    refs: list[Ref] = []
    fence: tuple[str, int] | None = None
    for i, raw in enumerate(stripped.splitlines(), start=1):
        fm = _FENCE.match(raw)
        if fence is None and fm:
            fence = (fm.group(1)[0], len(fm.group(1)))
            continue
        if fence is not None:
            if fm and fm.group(1)[0] == fence[0] and len(fm.group(1)) >= fence[1] and not fm.group(2).strip():
                fence = None
                continue
            if i not in ignored:
                refs.extend(_abs_refs(raw, i))
            continue
        if i in ignored:
            continue
        line = raw
        # links first (their targets are not prose), then code spans, then the prose rest
        for m in _LINK.finditer(line):
            target = m.group(3)
            if target.startswith("<") and target.endswith(">"):
                target = target[1:-1]
            refs.append(_link_ref(target, i))
        line = _LINK.sub(lambda m: " " * len(m.group(0)), line)
        for m in _CODE_SPAN.finditer(line):
            span = m.group(2).strip()
            if _ABS_START.match(span) and " " not in span:
                refs.append(Ref(i, "abs", span.rstrip(_TRAIL), (span.rstrip(_TRAIL),)))
            elif _ABS_START.search(span):
                refs.extend(_abs_refs(span, i))
            elif _REL_SPAN.match(span) and not _SCHEME.match(span):
                refs.append(Ref(i, "rel", span))
        line = _CODE_SPAN.sub(lambda m: " " * len(m.group(0)), line)
        refs.extend(_abs_refs(line, i))
    return refs


def _link_ref(target: str, line: int) -> Ref:
    path, _, anchor = target.partition("#")
    return Ref(line, "link", urllib.parse.unquote(path), (), anchor or None)


# --------------------------------------------------------------------------- headings


def slugify(heading: str) -> str:
    """GitHub-style anchor of a heading text (Unicode letters kept, punctuation dropped)."""
    s = re.sub(r"`([^`]*)`", r"\1", heading).strip().lower()
    s = re.sub(r"[^\w\- ]", "", s)
    return s.replace(" ", "-")


def headings(text: str) -> set[str]:
    """Anchors of all headings of a Markdown text (duplicates get ``-1``, ``-2`` ...)."""
    seen: dict[str, int] = {}
    out: set[str] = set()
    fence = False
    for raw in text.splitlines():
        if _FENCE.match(raw):
            fence = not fence
            continue
        if fence:
            continue
        m = _HEADING.match(raw)
        if not m:
            continue
        slug = slugify(m.group(2))
        n = seen.get(slug, 0)
        seen[slug] = n + 1
        out.add(slug if n == 0 else f"{slug}-{n}")
    return out


# --------------------------------------------------------------------------- checking


def _git_root(p: Path) -> Path | None:
    for d in (p, *p.parents):
        if (d / ".git").exists():
            return d
    return None


def _cut_placeholder(path: str) -> tuple[str, bool]:
    """Path up to (not including) the first segment with a placeholder; (path, was_cut)."""
    parts = re.split(r"([\\/])", path)
    out = ""
    for k in range(0, len(parts), 2):
        seg = parts[k]
        if _PLACEHOLDER.search(seg):
            return out.rstrip("\\/"), True
        out += seg + (parts[k + 1] if k + 1 < len(parts) else "")
    return out, False


def _under(p: Path, root: Path | None) -> bool:
    if root is None:
        return False
    try:
        a = os.path.normcase(os.path.abspath(p))
        b = os.path.normcase(os.path.abspath(root))
    except (OSError, ValueError):
        return False
    return a == b or a.startswith(b.rstrip("\\/") + os.sep)


#: Internal status of a path-like span no base anchors yet (never returned by :func:`check_file`).
UNANCHORED = "unanchored"


def _norm(p: str | Path) -> str:
    return str(Path(p)).replace(os.sep, "/")


def _uniq(paths: Iterable[Path]) -> list[Path]:
    out: list[Path] = []
    keys: set[str] = set()
    for p in paths:
        k = os.path.normcase(os.path.normpath(str(p)))
        if k not in keys:
            keys.add(k)
            out.append(p)
    return out


def _exists(path: str) -> bool:
    try:
        return Path(path).exists()
    except (OSError, ValueError):
        return False


_EXE_SUFFIXES = (".exe", ".cmd", ".bat", ".ps1", ".com")
_FILE_EXT = re.compile(r"[^.\s\\/]\.[A-Za-z][A-Za-z0-9]{0,7}$")


def _looks_like_path(span: str) -> bool:
    """True for a relative span that is a file-system path even when no base anchors its first segment.

    Its first word (a command line ``satk formats ls models\\gta3.img`` is not a path) contains a
    separator and either uses Windows separators, ends with one (``gta-sa-clean\\``) or ends with a
    file name with an extension (``docs/ROADMAP.md``). ``vanilla/installed/samp`` or ``Win32/x64`` (words
    joined by slashes) are not.
    """
    word = span.strip().split(" ")[0]
    if not re.search(r"[\\/]", word):
        return False
    if "\\" in word or word.endswith("/"):
        return True
    return bool(_FILE_EXT.search(word.rstrip(_TRAIL)))


def _exists_cmd(path: str) -> bool:
    """Exists as written, or as a command without its extension (``.../Scripts/python``)."""
    if _exists(path):
        return True
    p = Path(path)
    return not p.suffix and any(_exists(path + ext) for ext in _EXE_SUFFIXES)


class _Checker:
    def __init__(self, workspace: Path | None, work: Path | None, bases: Iterable[Path] = (),
                 game: Path | None = None, installed: Path | None = None, generated: Iterable[Path] = ()):
        self.workspace = workspace
        self.work = work
        self.game = game
        self.installed = installed
        self.extra_bases = [Path(b) for b in bases]
        #: Regenerable roots: ``work`` plus the extra ``generated`` roots (the default work dir when
        #: ``paths.work`` is redirected). A missing path under one of them is ``generated``.
        self.work_roots = _uniq([Path(r) for r in (work, *generated) if r is not None])
        self._anchor_cache: dict[str, set[str]] = {}
        self._list_cache: dict[str, list[str]] = {}

    def _anchors(self, md: Path) -> set[str] | None:
        key = os.path.normcase(str(md))
        if key not in self._anchor_cache:
            try:
                self._anchor_cache[key] = headings(md.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                return None
        return self._anchor_cache[key]

    def _missing(self, md: Path, rel: str, ref: Ref, path: str, detail: str = "") -> Finding:
        # under work/ = regenerable output, unless it is inside the checkout of the file itself
        # (a git worktree under work/wt/<id> is source, not output)
        generated = (any(_under(Path(path), r) for r in self.work_roots)
                     and not _under(Path(path), _git_root(md.parent)))
        status = "generated" if generated else "missing"
        if not detail and status == "missing":
            parent = Path(path)
            while parent != parent.parent and not _exists(str(parent)):
                parent = parent.parent
            detail = f"nearest existing: {str(parent).replace(os.sep, '/')}"
        elif status == "generated":
            detail = "under work/ (regenerable)"
        return Finding(rel, ref.line, ref.text, status, detail, str(Path(path)).replace(os.sep, "/"))

    def check(self, md: Path, rel: str, ref: Ref, context: Iterable[Path] = ()) -> Finding | None:
        """One reference. A relative path-like span that no base anchors comes back with the internal
        status ``unanchored`` (:func:`check_file` retries it against the page's directories)."""
        if ref.kind == "link":
            return self._check_link(md, rel, ref)
        if ref.kind == "abs":
            cands = ref.candidates or (ref.text,)
            first_cut, _ = _cut_placeholder(cands[0])
            if len(first_cut) <= 3:  # "D:\" alone is not a reference worth checking
                return None
            best = None
            for c in cands:
                cut, _ = _cut_placeholder(c)
                if len(cut) > 3 and _exists_cmd(cut):
                    best = (c, cut)
            if best:
                return Finding(rel, ref.line, best[0], "ok", path=_norm(best[1]))
            return self._missing(md, rel, Ref(ref.line, ref.kind, ref.text), first_cut)
        return self._check_rel(md, rel, ref, list(context))

    def _check_rel(self, md: Path, rel: str, ref: Ref, context: list[Path]) -> Finding | None:
        """Relative inline-code path: anchored when its first segment exists in a base.

        The span may continue with arguments (``tools/scripts/bootstrap.ps1 -Deps``) or contain
        spaces (``GTA San Andreas/``): the longest run of words that exists wins.
        """
        text = ref.text.strip()
        if text.lower() in NOT_PATHS:
            return None
        full_cut, _ = _cut_placeholder(text)
        first = re.split(r"[\\/]", full_cut.strip(), maxsplit=1)[0].strip()
        if not first or first in (".", ".."):
            return None
        bases = _uniq(self._bases(md) + context)
        anchored = [b for b in bases if _exists(str(b / first))]
        if not anchored:
            if not _looks_like_path(text):
                return None  # not a path of this file system (a SID, a module name, ...)
            return self._unanchored(rel, ref, first, full_cut.strip(), bases)
        cands: list[str] = []
        acc = ""
        for w in text.split(" ")[:_MAX_WORDS]:
            acc = f"{acc} {w}" if acc else w
            c = acc.rstrip(_TRAIL)
            if c and (not cands or cands[-1] != c):
                cands.append(c)
        for c in reversed(cands):
            cut = _cut_placeholder(c)[0].strip()
            if not cut:
                continue
            for base in anchored:
                if _exists_cmd(str(base / cut)):
                    return Finding(rel, ref.line, c, "ok", f"base: {base.as_posix()}", _norm(base / cut))
        shown = next((c for c in cands if re.search(r"[\\/]", c)), text)
        cut = _cut_placeholder(shown)[0].strip()
        return self._missing(md, rel, Ref(ref.line, ref.kind, shown), str(anchored[0] / cut))

    def _listing(self, base: Path) -> list[str]:
        key = os.path.normcase(str(base))
        if key not in self._list_cache:
            try:
                self._list_cache[key] = sorted(os.listdir(base))
            except OSError:
                self._list_cache[key] = []
        return self._list_cache[key]

    def _unanchored(self, rel: str, ref: Ref, first: str, cut: str, bases: list[Path]) -> Finding:
        """A path-like span whose first segment is in none of the bases: a typo (``toolz\\satk.cmd``)."""
        import difflib

        best: tuple[float, str, Path] | None = None
        for b in bases:
            names = self._listing(b)
            low = {n.lower(): n for n in names}
            for m in difflib.get_close_matches(first.lower(), list(low), n=1, cutoff=0.75):
                score = difflib.SequenceMatcher(None, first.lower(), m).ratio()
                if best is None or score > best[0]:
                    best = (score, low[m], b)
        detail = (f"'{first}' is in none of the {len(bases)} bases (the file's directory, its checkout, workspace, "
                  "work, game, install, directories the page mentions)")
        if best is not None:
            detail = f"no '{first}' in any base; did you mean '{best[1]}' ({best[2].as_posix()})?"
        where = (best[2] if best else bases[0]) / cut
        return Finding(rel, ref.line, ref.text, UNANCHORED, detail, _norm(where))

    def _bases(self, md: Path) -> list[Path]:
        out = list(self.extra_bases) + [md.parent]
        g = _git_root(md.parent)
        if g is not None:
            out.append(g)
        out.extend(self._bases_static)
        return _uniq(out)

    @property
    def _bases_static(self) -> list[Path]:
        out: list[Path] = []
        if self.workspace is not None:
            out.append(self.workspace)
        out.extend(self.work_roots)  # work-relative: index\, cache\tex\..., tmp\<id>\
        if self.game is not None:
            out.append(self.game)  # game-relative paths: data/gta.dat, models/gta3.img
        if self.installed is not None:
            out.append(self.installed)  # files only the original install has: data/colorcycle.dat, *.two
        return out

    def _check_link(self, md: Path, rel: str, ref: Ref) -> Finding | None:
        target = ref.text
        if _SCHEME.match(target) and not re.match(r"^[A-Za-z]:[\\/]", target):
            return None  # URL: not fetched
        if not target:
            dest = md
        else:
            cut, was_cut = _cut_placeholder(target)
            if was_cut and not cut:
                return None
            p = Path(cut)
            dest = p if p.is_absolute() else (md.parent / p)
            if not _exists(str(dest)):
                return self._missing(md, rel, ref, str(dest))
        if ref.anchor and dest.suffix.lower() == ".md" and dest.is_file():
            anchors = self._anchors(dest)
            if anchors is not None and ref.anchor.lower() not in anchors:
                return Finding(rel, ref.line, f"{target}#{ref.anchor}", "anchor", "no such heading")
        return Finding(rel, ref.line, target + (f"#{ref.anchor}" if ref.anchor else ""), "ok", path=_norm(dest))


def check_file(md: Path, *, workspace: Path | None = None, work: Path | None = None,
               bases: Iterable[Path] = (), game: Path | None = None, checker: _Checker | None = None,
               display: str | None = None, installed: Path | None = None,
               generated: Iterable[Path] = ()) -> list[Finding]:
    """Check one Markdown file; returns a finding per reference (``ok`` included).

    Two passes: relative spans that no fixed base anchors (``bin\\ariane.exe`` next to
    ``viewer\\ariane\\``, ``Build\\MTASA.sln`` on the page about ``engine\\mtasa``) are retried against every
    directory the page itself mentions; what is still unanchored is ``missing`` (a typo like
    ``toolz\\satk.cmd``).
    """
    ck = checker or _Checker(workspace, work, bases, game, installed, generated)
    text = md.read_text(encoding="utf-8")
    rel = display or md.as_posix()
    refs = extract_refs(text)
    results: list[Finding | None] = [ck.check(md, rel, ref) for ref in refs]
    context = _uniq([Path(f.path) for f in results
                     if f is not None and f.status == "ok" and f.path and Path(f.path).is_dir()])
    out: list[Finding] = []
    seen: set[tuple[int, str]] = set()
    for ref, f in zip(refs, results):
        if f is not None and ref.kind == "rel" and f.status != "ok" and context:
            # `blender\satk_blender` next to `tools\`: work\blender exists, the page means tools\blender
            g = ck.check(md, rel, ref, context)
            if g is not None and (g.status == "ok" or f.status == UNANCHORED):
                f = g
        if f is not None and f.status == UNANCHORED:
            f = Finding(f.file, f.line, f.ref, "missing", f.detail, f.path)
        if f is None or (f.line, f.ref) in seen:
            continue
        seen.add((f.line, f.ref))
        out.append(f)
    return out


def iter_markdown(paths: Iterable[str | Path]) -> Iterator[Path]:
    """Files as given; directories expand to their ``*.md`` (recursively, sorted)."""
    for p in paths:
        p = Path(p)
        if p.is_dir():
            yield from sorted(x for x in p.rglob("*.md") if ".git" not in x.parts and ".venv" not in x.parts)
        else:
            yield p


def check_paths(paths: Iterable[str | Path], *, workspace: Path | None, work: Path | None,
                bases: Iterable[Path] = (), game: Path | None = None,
                installed: Path | None = None,
                generated: Iterable[Path] = ()) -> tuple[list[Path], list[Finding]]:
    """Check many files/directories; returns (files checked, findings)."""
    ck = _Checker(workspace, work, bases, game, installed, generated)
    files = list(iter_markdown(paths))
    findings: list[Finding] = []
    for f in files:
        findings.extend(check_file(f.resolve(), checker=ck, display=str(f.resolve()).replace(os.sep, "/")))
    return files, findings


def config_roots(c) -> dict:
    """Keyword arguments of :func:`check_paths` from a loaded config (``satk.core.config.load()``).

    ``generated`` holds the default work dir ``<workspace>/work``: with ``SATK_PATHS_WORK`` pointing
    elsewhere (the gate, a test's private work) the docs still mention the shared work dir, and its
    regenerable files are ``generated``, not ``missing``.
    """
    workspace = Path(c.paths.workspace)
    installed = c.paths.get("installed")
    return {"workspace": workspace, "work": Path(c.paths.work), "game": Path(c.paths.game),
            "installed": Path(installed) if installed else None, "generated": (workspace / "work",)}
