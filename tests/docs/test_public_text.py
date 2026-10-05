"""The public texts carry no paths of one machine and no references to internal planning notes.

The repository is published as is (a public snapshot per release), so everything a reader sees - the READMEs,
``NOTICE.md``, the changelog, the user docs in both languages, the agent docs, the SAAP/1 protocol text, the
packaging texts and the contributor guide ``CLAUDE.md`` - must work for anyone: paths are written as
``<workspace>``, ``<game>`` or relative to the workspace, and no text points to planning notes that only exist
in the maintainers' workspace. ``docs/agent/workspace-CLAUDE.md`` is the one exception: it describes the
maintainers' own workspace and is published only there (``satk dev sync-agent-docs``).

Fast, stdlib only, no game files.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from satk.core.config import REPO_ROOT

#: Paths of the maintainers' machine (any slash style, also JSON-escaped ``\\``).
MACHINE_PATH = re.compile(r"[Dd]:[\\/]+Games\b|[Dd]:[\\/]+files\b|[Cc]:[\\/]+Users[\\/]+User\b")
#: Internal planning notes (design docs, milestone plans) that are not part of the repository.
PLAN_REF = re.compile(r"docs[\\/]+design\b|M2-PLAN|M3-CANDIDATES|M3-EXECUTION|\bSPEC\.md\b|ROADMAP\.md|PAUSE-STATE")
#: Internal work-package, milestone-lane and specification ids. Checked everywhere except the agent docs: they
#: quote real answers of the current code verbatim, and the skill is published byte for byte to the workspace.
PLAN_ID = re.compile(r"\bWP-\d|\bM[1-9]-(?:\d|[A-D]\d)|\bM[1-9] (?:lane|A\d|B\d|C\d)|\b(?:lane|полос[аы]) [A-D]\d\b"
                     r"|\bSPEC\b")
#: Account e-mail addresses (examples and the commit trailer are allowed).
EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
EMAIL_OK = frozenset({"noreply@anthropic.com", "git@github.com"})
#: Secrets that look real (the bug-report page names the patterns with "..." only).
SECRET = re.compile(r"\bghp_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}|\bsk-[A-Za-z0-9_-]{20,}")

#: Generated or workspace-only files of docs/agent that are not public text of this kind.
AGENT_EXCLUDED = frozenset({"workspace-CLAUDE.md"})


def public_texts(repo: Path = REPO_ROOT) -> list[Path]:
    """Every public text file of the checkout ``repo``, sorted."""
    files: set[Path] = set()
    for name in ("README.md", "README.ru.md", "NOTICE.md", "CHANGELOG.md", "CLAUDE.md"):
        if (repo / name).is_file():
            files.add(repo / name)
    for lang in ("en", "ru"):
        files.update(p for p in (repo / "docs" / lang).rglob("*") if p.is_file())
    files.update(p for p in (repo / "docs" / "agent").rglob("*") if p.is_file() and p.name not in AGENT_EXCLUDED)
    files.update((repo / "proto").rglob("*.md"))
    for pattern in ("*.md", "*.txt"):
        files.update((repo / "packaging").rglob(pattern))
    return sorted(files)


def _is_agent_doc(p: Path, repo: Path = REPO_ROOT) -> bool:
    return p.relative_to(repo).parts[:2] == ("docs", "agent")


def _hits(rx: re.Pattern, text: str) -> list[str]:
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        out += [f"line {i}: {m.group(0)!r}" for m in rx.finditer(line)]
    return out


FILES = public_texts()


def _id(p: Path) -> str:
    return p.relative_to(REPO_ROOT).as_posix()


def test_the_public_text_set_is_complete():
    names = {_id(p) for p in FILES}
    for must in ("README.md", "README.ru.md", "NOTICE.md", "CLAUDE.md", "packaging/CHANGELOG.md",
                 "docs/en/README.md", "docs/ru/README.md", "docs/agent/SKILL.md", "proto/SAAP-v1.md",
                 "packaging/portable/README-FIRST.txt"):
        assert must in names, must
    assert "docs/agent/workspace-CLAUDE.md" not in names


@pytest.mark.parametrize("path", FILES, ids=_id)
def test_no_machine_paths_or_plan_references(path: Path):
    text = path.read_text(encoding="utf-8")
    machine = _hits(MACHINE_PATH, text)
    assert not machine, f"machine paths (write <workspace> or a relative path): {machine[:3]}"
    assert not _hits(PLAN_REF, text), f"references to internal planning notes: {_hits(PLAN_REF, text)[:3]}"
    if not _is_agent_doc(path):
        assert not _hits(PLAN_ID, text), f"internal work-package/lane/SPEC ids: {_hits(PLAN_ID, text)[:3]}"


@pytest.mark.parametrize("path", FILES, ids=_id)
def test_no_account_emails_or_secrets(path: Path):
    text = path.read_text(encoding="utf-8")
    emails = [h for h in _hits(EMAIL, text) if h.split(": ", 1)[1].strip("'") not in EMAIL_OK]
    assert not emails, f"e-mail addresses in a public text: {emails[:3]}"
    assert not _hits(SECRET, text), f"something that looks like a token: {_hits(SECRET, text)[:3]}"


@pytest.mark.parametrize("readme", ["README.md", "README.ru.md"])
def test_readmes_point_to_the_public_repository(readme: str):
    text = (REPO_ROOT / readme).read_text(encoding="utf-8")
    assert "https://github.com/qxlies/satk-gta/actions/workflows/ci.yml/badge.svg" in text
    assert "https://github.com/qxlies/satk-gta/releases" in text
    assert "git clone https://github.com/qxlies/satk-gta.git tools" in text


def test_the_patterns_catch_what_they_should():
    d, users = "D:", "C:\\Users"  # the maintainers' layout, spelled at run time (this file names no such path)
    bad = [rf"{d}\Games\GTA\work", f"{d}/Games/GTA/work", rf"{d}\\Games\\GTA", rf"{d}\files\tg", rf"{users}\User\x",
           "docs/design/SPEC.md", r"docs\design\PORTABILITY.md", "M2-PLAN.md", "M3-CANDIDATES §5.1"]
    for s in bad:
        assert MACHINE_PATH.search(s) or PLAN_REF.search(s), s
    for s in ("WP-12", "(M2-09)", "M3-A1", "M3 lane C2", "lane B4", "полоса B4", "SPEC §4.8"):
        assert PLAN_ID.search(s), s
    for s in (r"<workspace>\work", r"C:\Program Files (x86)\Rockstar Games\GTA San Andreas", r"C:\Users\Public",
              "M3 hardware", "SPECIAL", "the spec", "B4 paper", "sk-...", "ghp_..."):
        assert not (MACHINE_PATH.search(s) or PLAN_REF.search(s) or PLAN_ID.search(s) or SECRET.search(s)), s
