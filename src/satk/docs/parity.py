"""English/Russian parity of the user docs (``satk dev docs-parity``).

The user docs are English-primary with a pure-Russian mirror (one language per file):

==========================  ==========================
English (primary)           Russian mirror
==========================  ==========================
``README.md``               ``README.ru.md``
``docs/en/<page>.md``       ``docs/ru/<page>.md``
==========================  ==========================

Templates (``_*.md``) and index pages (``README.md``) are pages like any other. Checks per pair:

* ``mirror`` - both files exist;
* ``switch`` - the page starts with the language switch: the first line after the ``#`` title (blank
  lines and HTML comments skipped) is exactly ``[<SWITCH_TEXT[lang]>](<mirror>)``: "Russian version" in
  Russian on an English page, ``[English version](<mirror>)`` on a Russian page, ``<mirror>`` being the
  relative path of the other file (:func:`switch_line`);
* ``language`` - an English page has no Cyrillic outside its switch line (a Russian page cannot be
  checked the same way: commands, paths and SIDs are Latin);
* ``sections`` - the same number of ``##`` headings (a section added to one language only);
* ``fences`` - the same number of fenced code blocks;
* ``quick`` - the quick examples run the same satk commands (quoted arguments may be translated).

Standard library only.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

__all__ = ["SWITCH_TEXT", "Issue", "Pair", "pairs", "check", "switch_line"]

#: Text of the switch link, by the language of the page that carries it (data, not prose: the link a
#: Russian reader looks for on an English page is in Russian).
SWITCH_TEXT = {"en": "Русская версия",
               "ru": "English version"}
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_QUOTED = re.compile(r"\"[^\"]*\"|'[^']*'")


@dataclass(frozen=True, slots=True)
class Issue:
    """One parity problem: ``page`` relative to the repository, ``check`` one of the module's checks."""

    page: str
    check: str
    detail: str

    def row(self) -> list:
        return [self.page, self.check, self.detail]


@dataclass(frozen=True, slots=True)
class Pair:
    """An English page and its Russian mirror (either may be missing)."""

    en: Path
    ru: Path


def pairs(repo: Path) -> list[Pair]:
    """The root README pair plus every page name found in ``docs/en`` or ``docs/ru``, sorted."""
    out = [Pair(repo / "README.md", repo / "README.ru.md")]
    en, ru = repo / "docs" / "en", repo / "docs" / "ru"
    names = {p.name for d in (en, ru) if d.is_dir() for p in d.glob("*.md")}
    out += [Pair(en / n, ru / n) for n in sorted(names)]
    return out


def _rel(p: Path, repo: Path) -> str:
    try:
        return p.relative_to(repo).as_posix()
    except ValueError:
        return p.as_posix()


def _link_target(src: Path, dst: Path) -> str:
    """Relative link from ``src`` to ``dst`` with forward slashes (both under one repository)."""
    return os.path.relpath(dst, src.parent).replace(os.sep, "/")


def switch_line(lang: str, src: Path, mirror: Path) -> str:
    """The exact switch line page ``src`` (in ``lang``) must carry."""
    return f"[{SWITCH_TEXT[lang]}]({_link_target(src, mirror)})"


def _first_line_after_title(text: str) -> tuple[int, str] | None:
    """(line number, text) of the first content line after the ``#`` title; None without a title."""
    stripped = _COMMENT.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    lines = stripped.splitlines()
    title = next((i for i, ln in enumerate(lines) if ln.strip()), None)
    if title is None or not lines[title].startswith("# "):
        return None
    for i in range(title + 1, len(lines)):
        if lines[i].strip():
            return i + 1, lines[i].strip()
    return None


def _outside_fences(text: str) -> list[str]:
    out, fence = [], None
    for ln in text.splitlines():
        m = _FENCE.match(ln)
        if m:
            mark = m.group(1)
            if fence is None:
                fence = mark
            elif mark[0] == fence[0] and len(mark) >= len(fence) and not ln.strip()[len(mark):].strip():
                fence = None
            continue
        if fence is None:
            out.append(ln)
    return out


def _count_fences(text: str) -> int:
    n, fence = 0, None
    for ln in text.splitlines():
        m = _FENCE.match(ln)
        if not m:
            continue
        mark = m.group(1)
        if fence is None:
            fence, n = mark, n + 1
        elif mark[0] == fence[0] and len(mark) >= len(fence) and not ln.strip()[len(mark):].strip():
            fence = None
    return n


def _sections(text: str) -> int:
    return sum(1 for ln in _outside_fences(text) if ln.startswith("## "))


def _quick(text: str) -> list[str]:
    from .smoke import extract_commands

    return [_QUOTED.sub('"…"', c.text) for c in extract_commands(text) if c.argv is not None]


def _check_page(lang: str, page: Path, mirror: Path, text: str, repo: Path) -> list[Issue]:
    rel = _rel(page, repo)
    out: list[Issue] = []
    want = switch_line(lang, page, mirror)
    got = _first_line_after_title(text)
    if got is None:
        out.append(Issue(rel, "switch", "no '# Title' line at the top"))
    elif got[1] != want:
        out.append(Issue(rel, "switch", f"line {got[0]} must be {want!r}, found {got[1][:80]!r}"))
    if lang == "en":
        switch_no = got[0] if got and got[1] == want else None
        stripped = _COMMENT.sub(lambda m: "\n" * m.group(0).count("\n"), text)
        for i, ln in enumerate(stripped.splitlines(), 1):
            if i != switch_no and _CYRILLIC.search(ln):
                out.append(Issue(rel, "language", f"line {i}: Cyrillic on an English page: {ln.strip()[:80]!r}"))
                break
    return out


def check(repo: Path) -> tuple[int, list[Issue]]:
    """(pairs checked, issues) for the checkout ``repo``."""
    issues: list[Issue] = []
    ps = pairs(repo)
    for p in ps:
        have = {"en": p.en.is_file(), "ru": p.ru.is_file()}
        if not have["en"] and not have["ru"]:
            continue
        if not have["en"]:
            issues.append(Issue(_rel(p.ru, repo), "mirror", f"no English page {_rel(p.en, repo)}"))
        if not have["ru"]:
            issues.append(Issue(_rel(p.en, repo), "mirror", f"no Russian mirror {_rel(p.ru, repo)}"))
        texts = {lang: (p.en if lang == "en" else p.ru).read_text(encoding="utf-8")
                 for lang in ("en", "ru") if have[lang]}
        for lang, text in texts.items():
            page, mirror = (p.en, p.ru) if lang == "en" else (p.ru, p.en)
            issues += _check_page(lang, page, mirror, text, repo)
        if len(texts) < 2:
            continue
        en, ru, rel = texts["en"], texts["ru"], _rel(p.en, repo)
        a, b = _sections(en), _sections(ru)
        if a != b:
            issues.append(Issue(rel, "sections", f"{a} '##' sections, the Russian mirror has {b}"))
        a, b = _count_fences(en), _count_fences(ru)
        if a != b:
            issues.append(Issue(rel, "fences", f"{a} code blocks, the Russian mirror has {b}"))
        qa, qb = _quick(en), _quick(ru)
        if qa != qb:
            only = [c for c in qa if c not in qb][:1] or [c for c in qb if c not in qa][:1] or ["(order)"]
            issues.append(Issue(rel, "quick", f"quick examples differ ({len(qa)} vs {len(qb)} commands): {only[0][:80]}"))
    return len(ps), issues
