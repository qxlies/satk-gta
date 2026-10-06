"""``satk dev gate --changed``: pick the tests a change can affect.

The full gate is the merge acceptance. While a lane is still working it needs a cheaper intermediate check, so
``--changed`` looks at the files that differ from the merge-base with ``main`` (committed, staged, unstaged and new)
and selects:

* always: ``tests/core``, ``tests/docs``, ``tests/mcp`` (the registry, the docs rules and the MCP budget see every
  package), plus the cheap docs steps and the MCP selftest that the gate adds itself;
* ``src/satk/<pkg>/**``: ``tests/<pkg>``, the tests of the packages whose modules import the changed module (one level,
  from an AST scan of ``src``) and every test file that imports it; a change in ``src/satk/core`` selects everything;
* ``tests/<pkg>/**``: the changed test files (the whole folder for a ``conftest.py`` or a helper);
* ``data/**``, ``vendor/**``, ``proto/**``, ``native/**``: the modules and tests whose text names that folder or file;
* docs, packaging and scripts: the docs and release tests that check them; anything unknown selects everything.

The game steps (private index build, golden verify, ``-m game`` tests) are selected only when index, formats or game
code (or the golden numbers, or a changed test that is itself game-marked) changed.

The selection is a heuristic for intermediate checks: it never replaces the full gate before a merge. Every choice
carries its reason, and the gate prints them.
"""

from __future__ import annotations

import ast
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["ALWAYS", "Selection", "changed_files", "select", "select_from_git"]

#: Test folders every ``--changed`` run includes.
ALWAYS = ("tests/core", "tests/docs", "tests/mcp")
#: Packages whose change selects the game steps (the index and what it is built from).
GAME_PACKAGES = ("index", "formats", "game")
#: Blender-side add-on folders that have a test folder of the same name next to ``tests/blender``.
_BLENDER_SUBS = ("studio", "kit", "look")
#: Tests of the docs layout and of the repository hygiene (cheap) for documentation and packaging files.
_DOC_TESTS = ("tests/e2e/test_docs_layout.py", "tests/e2e/test_repo_hygiene.py")
#: Root files that only the docs, release and hygiene tests look at.
_ROOT_INERT = {"LICENSE", "NOTICE.md", "SECURITY.md", "CODE_OF_CONDUCT.md", "CONTRIBUTING.md", "README.md",
               "README.ru.md", "CLAUDE.md", ".gitignore", ".gitattributes", ".assetguard-allow", ".editorconfig",
               "satk.toml.example", "MANIFEST.in", "requirements.lock", "setup.py", "pyproject.toml", "satk.cmd",
               "satk.sh"}
_MARK_GAME = re.compile(r"mark\.game\b|pytestmark\s*=[^\n]*\bgame\b")
_MAX_REASONS = 4


@dataclass
class Selection:
    """Which tests ``--changed`` runs and why."""

    base: str = "main"
    merge_base: str = ""
    changed: list[str] = field(default_factory=list)
    #: Test files / folders (posix, relative to the checkout), deduplicated, folders swallow their files.
    paths: list[str] = field(default_factory=list)
    #: ``path -> reasons`` (the first few: the list is for people).
    reasons: dict[str, list[str]] = field(default_factory=dict)
    #: Run every test (a core change, a shared test helper, an unmapped file, or git could not tell).
    all_tests: bool = False
    all_reason: str = ""
    #: Select the index build, golden verify and ``-m game`` tests.
    game: bool = False
    game_reason: str = ""
    #: Limit the ``-m game`` run to these paths (empty: every test).
    game_paths: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add(self, path: str, why: str) -> None:
        r = self.reasons.setdefault(path, [])
        if why not in r and len(r) < _MAX_REASONS:
            r.append(why)

    def everything(self, why: str) -> None:
        if not self.all_tests:
            self.all_tests, self.all_reason = True, why

    def lines(self) -> list[str]:
        """Human-readable summary, one line per selected path."""
        out = [f"changed: {len(self.changed)} file(s) since {self.merge_base[:10] or '?'} (merge-base with {self.base})"]
        if self.all_tests:
            out.append(f"tests: everything ({self.all_reason})")
        else:
            out += [f"tests: {p} <- {'; '.join(self.reasons.get(p, ['always']))}" for p in self.paths]
        out.append("game steps: " + (f"yes ({self.game_reason})" if self.game else "no (--with-game adds them)"))
        out += [f"note: {n}" for n in self.notes]
        return out

    def summary(self) -> dict:
        """Compact form for the result envelope."""
        d: dict = {"base": self.base, "merge_base": self.merge_base[:10], "changed": len(self.changed),
                   "tests": "all" if self.all_tests else list(self.paths), "game": self.game}
        if self.all_tests:
            d["why_all"] = self.all_reason
        if self.notes:
            d["notes"] = self.notes
        return d


# --------------------------------------------------------------------------- git


def _git(repo: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=60)
    if p.returncode != 0:
        text = (p.stderr or p.stdout).strip()
        raise RuntimeError(text.splitlines()[-1] if text else f"git {args[0]} failed")
    return p.stdout


def changed_files(repo: Path, base: str = "main") -> tuple[str, list[str]]:
    """``(merge_base, files)``: files that differ from the merge-base of ``HEAD`` and ``base``, as posix paths.

    Committed, staged, unstaged and untracked (not ignored) files are all included; renames list both names.
    Raises ``RuntimeError`` when git cannot tell (not a repository, unknown base).
    """
    mb = _git(repo, "merge-base", "HEAD", base).strip()
    names = set(_git(repo, "diff", "--name-only", "--no-renames", "-z", mb).split("\0"))
    names |= set(_git(repo, "ls-files", "--others", "--exclude-standard", "-z").split("\0"))
    return mb, sorted(n.replace("\\", "/") for n in names if n)


# --------------------------------------------------------------------------- import graph


def _module_name(src_root: Path, f: Path) -> str:
    parts = list(f.relative_to(src_root.parent).with_suffix("").parts)  # satk/a/b
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _with_parents(mod: str, known: set[str]) -> set[str]:
    """``satk.a.b.c`` -> the known modules among it and its parents (importing it runs the parents' ``__init__``)."""
    parts = mod.split(".")
    return {".".join(parts[:i]) for i in range(2, len(parts) + 1) if ".".join(parts[:i]) in known}


def _imports(tree: ast.AST, package: str, known: set[str]) -> set[str]:
    """``satk.*`` modules (from ``known``) that ``tree`` imports; ``package`` resolves relative imports."""
    out: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name.split(".")[0] == "satk":
                    out |= _with_parents(a.name, known)
        elif isinstance(n, ast.ImportFrom):
            if n.level:
                base = package.split(".")
                if n.level > len(base):
                    continue
                mod = ".".join(base[:len(base) - (n.level - 1)] + ([n.module] if n.module else []))
            else:
                mod = n.module or ""
            if mod.split(".")[0] != "satk":
                continue
            out |= _with_parents(mod, known)
            out |= {f"{mod}.{a.name}" for a in n.names if f"{mod}.{a.name}" in known}
    return out


class _Index:
    """Lazily built facts about the checkout: the import graph of ``src`` and ``tests``, and the texts of both."""

    def __init__(self, repo: Path):
        self.repo = repo
        self.src_root = repo / "src" / "satk"
        self._modules: set[str] = set()
        self._src: dict[str, set[str]] | None = None
        self._tests: dict[str, set[str]] | None = None
        self._texts: dict[str, str] | None = None

    def _parse_all(self) -> None:
        files = sorted(self.src_root.rglob("*.py")) if self.src_root.is_dir() else []
        self._modules = {_module_name(self.src_root, f) for f in files}
        self._src, self._tests = {}, {}
        for f in files:
            tree = _parse(f)
            if tree is not None:
                m = _module_name(self.src_root, f)
                self._src[m] = _imports(tree, m if f.name == "__init__.py" else m.rpartition(".")[0], self._modules)
        tdir = self.repo / "tests"
        for f in sorted(tdir.rglob("*.py")) if tdir.is_dir() else []:
            tree = _parse(f)
            if tree is not None:
                self._tests[f.relative_to(self.repo).as_posix()] = _imports(tree, "satk", self._modules)

    @property
    def modules(self) -> set[str]:
        if self._src is None:
            self._parse_all()
        return self._modules

    @property
    def src_imports(self) -> dict[str, set[str]]:
        if self._src is None:
            self._parse_all()
        return self._src  # type: ignore[return-value]

    @property
    def test_imports(self) -> dict[str, set[str]]:
        if self._tests is None:
            self._parse_all()
        return self._tests  # type: ignore[return-value]

    @property
    def texts(self) -> dict[str, str]:
        """``path -> text`` of every ``.py`` under ``src/satk`` and ``tests``."""
        if self._texts is None:
            self._texts = {}
            for root in (self.src_root, self.repo / "tests"):
                for f in sorted(root.rglob("*.py")) if root.is_dir() else []:
                    try:
                        self._texts[f.relative_to(self.repo).as_posix()] = f.read_text(encoding="utf-8")
                    except (OSError, ValueError):
                        pass
        return self._texts

    def _want(self, module: str) -> set[str]:
        """``module`` plus its package when the package's ``__init__`` re-exports it."""
        parent = module.rpartition(".")[0]
        if parent in self.modules and module in self.src_imports.get(parent, ()):
            return {module, parent}
        return {module}

    def dependents(self, module: str) -> set[str]:
        """Source modules that import ``module`` (directly, or through the package that re-exports it)."""
        want = self._want(module)
        return {m for m, imp in self.src_imports.items() if m != module and imp & want}

    def tests_importing(self, module: str) -> list[str]:
        want = self._want(module)
        return sorted(t for t, imp in self.test_imports.items() if imp & want)

    def token_hits(self, token: str, *, data: bool = False) -> tuple[list[str], list[str]]:
        """``(source files, test files)`` whose text names ``token``.

        ``data=True``: ``token`` is a folder or file of ``data/``, matched as the first argument of a resource call
        (``read_json("style", ...)``, ``data_path("kit")``) or as ``data/<token>``. Otherwise a quoted string or a
        path segment (``"proto"``, ``/rwfury/``).
        """
        t = re.escape(token)
        if data:
            pat = re.compile(r"(?:\b(?:read_\w+|data_path|exists|list_files)\(\s*[\"']" + t + r"[\"'./]|data/" + t + r"\b)")
        else:
            pat = re.compile(r"[\"'/]" + t + r"(?:[\"'/]|\b)")
        src, tests = [], []
        for p, text in self.texts.items():
            if pat.search(text):
                (src if p.startswith("src/") else tests).append(p)
        return src, tests


def _parse(f: Path) -> ast.AST | None:
    try:
        return ast.parse(f.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return None


# --------------------------------------------------------------------------- selection


class _Mapper:
    """Applies the file -> tests rules of the module docstring to one :class:`Selection`."""

    def __init__(self, repo: Path, sel: Selection):
        self.repo, self.sel, self.idx = repo, sel, _Index(repo)
        self.game_src: list[str] = []  # code the index is built from changed: every game test is affected
        self.game_tests: list[str] = []  # changed test files that are game-marked themselves

    # -- helpers

    def test_dir(self, name: str, why: str) -> None:
        if (self.repo / "tests" / name).is_dir():
            self.sel.add(f"tests/{name}", why)

    def test_file(self, rel: str, why: str) -> None:
        """A test file that a change affects: ``test_*.py`` itself; a helper or ``conftest.py`` selects its folder."""
        parts = rel.split("/")
        if len(parts) == 3 and parts[2].startswith("test_"):
            self.sel.add(rel, why)
        elif len(parts) >= 3:
            self.sel.add(f"tests/{parts[1]}", f"{parts[-1]}: {why}")
        # tests/conftest.py and other top-level helpers import everything: not a selection by themselves

    def doc_tests(self, why: str) -> None:
        for t in _DOC_TESTS:
            self.sel.add(t, why)

    def src_file(self, rel: str, why: str) -> None:
        """A changed (or named) source file: its package tests, its importers' packages, the tests importing it."""
        parts = rel.split("/")
        if len(parts) < 4:
            self.sel.everything(f"{rel} is imported by every package")
            return
        pkg = parts[2]
        if pkg == "core":
            self.sel.everything("src/satk/core is imported by every package")
            return
        self.test_dir(pkg, why)
        if pkg in GAME_PACKAGES or rel.endswith(".sql"):
            self.game_src.append(rel)
        if rel.endswith(".py"):
            mod = _module_name(self.idx.src_root, self.repo / rel)
            if mod in self.idx.modules:
                for dep in sorted(self.idx.dependents(mod)):
                    other = dep.split(".")[1]
                    if other != pkg:
                        self.test_dir(other, f"imports {mod}")
                for t in self.idx.tests_importing(mod):
                    self.test_file(t, f"imports {mod}")

    # -- the rules

    def map_file(self, rel: str) -> None:
        parts = rel.split("/")
        top = parts[0]
        if rel.startswith("src/satk/"):
            self.src_file(rel, rel)
        elif top == "tests":
            self.tests_file(rel, parts)
        elif top == "data":
            self.data_file(rel, parts)
        elif top == "blender":
            self.test_dir("blender", f"{rel} changed")
            if len(parts) > 2 and parts[2] in _BLENDER_SUBS:
                self.test_dir(parts[2], f"{rel} changed")
        elif top in ("mta-resources", "native", "proto", "vendor"):
            self.folder_file(rel, top, parts)
        elif top in ("docs", ".claude-plugin"):
            self.doc_tests(f"{rel} changed")
            if parts[:2] == ["docs", "agent"] or top == ".claude-plugin":
                self.test_dir("mcp", f"{rel} changed")
        elif top in ("scripts", "packaging", ".github", ".githooks") or rel in _ROOT_INERT:
            self.test_dir("release", f"{rel} changed")
            self.doc_tests(f"{rel} changed")
        else:
            self.sel.everything(f"unmapped path {rel}")

    def tests_file(self, rel: str, parts: list[str]) -> None:
        if len(parts) == 2:
            self.sel.everything(f"shared test setup {rel}")
        elif parts[1] == "golden":
            self.game_src.append(rel)
            _s, tests = self.idx.token_hits(parts[-1].rsplit(".", 1)[0])
            for t in tests:
                self.test_file(t, f"reads the golden numbers {parts[-1]}")
        elif (self.repo / "tests" / parts[1]).is_dir():
            if len(parts) == 3 and parts[2].startswith("test_") and parts[2].endswith(".py"):
                if (self.repo / rel).is_file():  # a deleted test file selects nothing
                    self.sel.add(rel, "changed")
                    if _has_game_mark(self.repo, rel):
                        self.game_tests.append(rel)
            else:
                self.sel.add(f"tests/{parts[1]}", f"{rel} changed")
        else:
            self.sel.everything(f"unmapped test file {rel}")

    def data_file(self, rel: str, parts: list[str]) -> None:
        tokens = [parts[1]] if len(parts) > 2 else [parts[1], parts[1].rsplit(".", 1)[0]]
        src, tests = set(), set()
        for tok in tokens:
            s, t = self.idx.token_hits(tok, data=True)
            src.update(s)
            tests.update(t)
        own = len(parts) > 2 and (self.repo / "tests" / parts[1]).is_dir()
        if not src and not tests and not own:
            self.sel.everything(f"{rel} is not read by name from any source or test file")
            return
        for s in sorted(src):
            self.src_file(s, f"reads {rel}")
        for t in sorted(tests):
            self.test_file(t, f"reads {rel}")
        if own:
            self.sel.add(f"tests/{parts[1]}", f"{rel} changed")
        if parts[1] == "manifests":
            self.game_src.append(rel)

    def folder_file(self, rel: str, top: str, parts: list[str]) -> None:
        named = {"mta-resources": "mta", "native": "spbridge", "proto": "saap"}.get(top)
        if named:
            self.test_dir(named, f"{rel} changed")
        token = parts[1] if top == "vendor" and len(parts) > 2 else top
        src, tests = self.idx.token_hits(token)
        for s in sorted(src):
            self.src_file(s, f"names {token}")
        for t in sorted(tests):
            self.test_file(t, f"names {token}")


def _has_game_mark(repo: Path, rel: str) -> bool:
    try:
        return bool(_MARK_GAME.search((repo / rel).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return False


def select(repo: Path, changed: list[str], *, base: str = "main", merge_base: str = "") -> Selection:
    """Map the changed files to test paths (see the module docstring for the rules)."""
    sel = Selection(base=base, merge_base=merge_base, changed=list(changed))
    m = _Mapper(repo, sel)
    for a in ALWAYS:
        sel.add(a, "always")
    for rel in changed:
        m.map_file(rel)
    if sel.all_tests:
        sel.game, sel.game_reason = True, "every test is selected"
    elif m.game_src:
        more = f" (+{len(m.game_src) - 1})" if len(m.game_src) > 1 else ""
        sel.game, sel.game_reason = True, f"{m.game_src[0]} changed{more}"
    elif m.game_tests:
        sel.game, sel.game_reason = True, f"game-marked test {m.game_tests[0]} changed"
        sel.game_paths = sorted(m.game_tests)
    _collapse(sel)
    return sel


def _collapse(sel: Selection) -> None:
    """A selected folder swallows the selected files inside it (their reasons move to the folder)."""
    folders = [p for p in sel.reasons if not p.endswith(".py")]
    paths = []
    for p in sorted(sel.reasons):
        parent = next((f for f in folders if p.startswith(f + "/")), None)
        if parent is None:
            paths.append(p)
        else:
            sel.add(parent, f"{p.rsplit('/', 1)[-1]}: {sel.reasons[p][0]}")
    sel.paths = paths


def select_from_git(repo: Path, base: str = "main") -> Selection:
    """:func:`select` over :func:`changed_files`; when git cannot tell, select everything and say why."""
    try:
        mb, files = changed_files(repo, base)
    except (RuntimeError, OSError, subprocess.SubprocessError) as e:
        sel = Selection(base=base)
        sel.everything(f"cannot tell what changed ({e})")
        sel.game, sel.game_reason = True, "every test is selected"
        return sel
    return select(repo, files, base=base, merge_base=mb)
