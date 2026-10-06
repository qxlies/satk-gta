"""Documentation layout and budgets (SPEC §4.13, §5.3 WP-12 acceptance 1-4; M3 A3). Fast, no game files.

The user docs are English-primary (``README.md``, ``docs/en/``) with a pure-Russian mirror (``README.ru.md``,
``docs/ru/``). Every component page exists in both languages, follows the template's required sections and is
listed in both index pages; every path/link in docs, CLAUDE.md and the READMEs exists (only generated paths under
work/ are tolerated, also with a redirected ``SATK_PATHS_WORK``); generated agent docs are current.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from satk.core import config as _config
from satk.core.config import REPO_ROOT
from satk.core.registry import invoke
from satk.docs.linkcheck import check_paths, config_roots
from satk.docs.smoke import QUICK_HEADING, extract_commands, parse_line

DOCS = REPO_ROOT / "docs"
LANGS = ("en", "ru")
#: Guide pages every language has (the index, the template and the getting-started pages).
GUIDE_PAGES = ("README", "_template", "install", "quickstart", "ids", "workflows", "troubleshooting", "glossary")
AGENT_DOCS = ("SKILL", "workflows", "errors", "evals")
#: The development workspace's CLAUDE.md source: only in the development repository (the public snapshot leaves it
#: out, data/public-exclude.txt), so it is checked when present.
WORKSPACE_CLAUDE = REPO_ROOT / "docs" / "agent" / "workspace-CLAUDE.md"
#: Component pages (each written by its package owner, in both languages).
COMPONENT_PAGES = ("game", "formats", "index", "media", "texmod", "rw", "models", "catalog", "lint", "modinspect",
                   "idmgr", "mapconv", "paths", "crash", "re", "kb", "describe", "viewer", "saap", "mta-agent",
                   "blender", "engine", "mcp", "ai", "bugreport", "release", "workspace-gta", "install",
                   "addon", "script", "scriptapi", "txdopt", "batch", "fx2d", "colgen", "studio", "style", "kit",
                   "look", "shader", "spbridge", "worldfiles")
#: Required headings of a component page, by language (docs/<lang>/_template.md).
REQUIRED = {"en": ("## Commands",), "ru": ("## Команды",)}
#: Pages whose quick example is skipped as a whole (docs-smoke skip): a release build takes a minute and
#: downloads the pinned inputs the first time.
QUICK_EXAMPLE_SKIPPED = frozenset({"release"})
#: The section of the quickstart with the ten commands, by language.
TEN_COMMANDS = {"en": "## Ten commands", "ru": "## Десять команд"}

#: User pages must not carry paths of our machine or internal jargon (work packages, SPEC, milestone lanes).
MACHINE_PATH = re.compile(r"[A-Za-z]:[\\/]Games[\\/]GTA", re.I)
JARGON = re.compile(r"\bWP-\d|\bSPEC\b|\bM[23]-\d|\bM3[- ][A-C]\d")
#: Pages allowed to name the paths of one machine: none since the public preview (workspace-gta.md writes
#: <workspace>); tests/docs/test_public_text.py checks every public text the same way.
MACHINE_PATH_OK: frozenset[str] = frozenset()
#: Known debt (component pages owned by their packages): each language carries the same. The list only
#: shrinks: a page that is clean must leave it, a page not on it must stay clean. Paid off for 0.2.1.
MACHINE_PATH_DEBT: frozenset[str] = frozenset()
JARGON_DEBT: frozenset[str] = frozenset()
_COMMENT = re.compile(r"<!--.*?-->", re.S)


def _lines(p: Path) -> int:
    return len(p.read_text(encoding="utf-8").splitlines())


def _user_pages() -> list[tuple[str, Path]]:
    """(page name, path) of the root READMEs and every docs/<lang>/*.md."""
    out = [("README", REPO_ROOT / "README.md"), ("README", REPO_ROOT / "README.ru.md")]
    for lang in LANGS:
        out += [(p.stem, p) for p in sorted((DOCS / lang).glob("*.md"))]
    return out


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("name", GUIDE_PAGES)
def test_guide_pages_exist(lang, name):
    assert (DOCS / lang / f"{name}.md").is_file()


def test_root_readmes_exist_and_point_to_the_guides():
    en = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    ru = (REPO_ROOT / "README.ru.md").read_text(encoding="utf-8")
    assert "](README.ru.md)" in en and "](docs/en/README.md)" in en
    assert "](README.md)" in ru and "](docs/ru/README.md)" in ru


@pytest.mark.parametrize("name", AGENT_DOCS)
def test_agent_docs_exist(name):
    assert (DOCS / "agent" / f"{name}.md").is_file()


def test_no_stage_todos_left_in_guide_and_agent_docs():
    files = [DOCS / lang / f"{n}.md" for lang in LANGS for n in GUIDE_PAGES]
    files += [DOCS / "agent" / f"{n}.md" for n in AGENT_DOCS]
    files += [WORKSPACE_CLAUDE] if WORKSPACE_CLAUDE.is_file() else []
    left = [f"{p.relative_to(DOCS).as_posix()}:{i}" for p in files
            for i, ln in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
            if "TODO(этап 2)" in ln or "TODO(stage 2)" in ln]
    assert left == []


def test_budgets():
    if WORKSPACE_CLAUDE.is_file():
        assert _lines(WORKSPACE_CLAUDE) <= 120  # <workspace>\CLAUDE.md
    assert _lines(REPO_ROOT / "CLAUDE.md") <= 150  # tools/CLAUDE.md
    assert _lines(DOCS / "agent" / "SKILL.md") <= 400


def test_skill_frontmatter():
    text = (DOCS / "agent" / "SKILL.md").read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert m, "SKILL.md must start with YAML frontmatter"
    fm = dict(line.split(":", 1) for line in m.group(1).splitlines() if ":" in line)
    assert fm["name"].strip() == "satk"
    desc = fm["description"].strip()
    assert desc.startswith("Use for any GTA:SA asset, map, viewer, RE, Blender or MTA task in this workspace")
    assert len(desc) <= 1024


def test_skill_names_real_tools_and_parameters():
    """Every MCP tool is in the skill's tool table, and the table uses the real parameter names."""
    from satk.core.registry import all_ops

    text = (DOCS / "agent" / "SKILL.md").read_text(encoding="utf-8")
    table = text.split("## 4. Tools", 1)[1].split("\n## ", 1)[0]
    rows = {m.group(1): m.group(2) for m in re.finditer(r"^\| `(\w+)` \|[^|]*\|(.*)\|$", table, re.M)}
    mcp = {o.mcp_name: o for o in all_ops() if o.mcp_name}
    assert set(rows) == set(mcp), (set(mcp) - set(rows), set(rows) - set(mcp))
    for name, cell in rows.items():
        params = {p.name for p in mcp[name].params}
        cell = re.sub(r"\(NOT `\w+`\)", "", cell)  # "query (NOT `q`)" names a wrong spelling on purpose
        for used in re.findall(r"`(\w+)`", cell):
            if used in params or used.upper() == used:
                continue
            # enum values / literal hints are fine; parameter-looking words must be real parameters
            assert not re.fullmatch(r"[a-z_]+", used) or used in params or used in _ENUM_WORDS, (name, used)


_ENUM_WORDS = {"vanilla", "installed", "samp", "sheet", "png", "glb", "auto", "soft", "ariane", "blender", "mock",
               "game", "status", "start", "stop", "list", "save", "rm", "add", "index", "re", "notes", "hd", "aabb",
               "all", "doctor", "build", "import_model", "import_area", "render", "export", "func", "global",
               "vtable", "struct", "model", "txd", "tex", "file"}


@pytest.mark.parametrize("lang", LANGS)
def test_quickstart_at_most_ten_commands_to_first_screenshot(lang):
    text = (DOCS / lang / "quickstart.md").read_text(encoding="utf-8")
    sec = text.split(TEN_COMMANDS[lang], 1)[1].split("\n## ", 1)[0]
    block = re.search(r"```powershell\n(.*?)\n```", sec, re.S).group(1)
    cmds = [ln for ln in block.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    assert 1 <= len(cmds) <= 10, cmds
    assert "view capture" in cmds[-2] or "view capture" in cmds[-1], "the sequence must end with a screenshot"
    assert any(QUICK_HEADING.match(ln) for ln in text.splitlines()), "quickstart needs a runnable quick example"


def test_quick_examples_parse_and_classify():
    """Every quick-example command is concrete (no placeholders/pipes) and not a typo (strict dry run)."""
    env = invoke("dev.docs_smoke", {"pages": None, "dry_run": True, "strict": True})
    assert env["ok"] is True, env
    assert {r[3] for r in env["rows"]} <= {"would-run", "skip"}
    assert {r[0].split("/")[1] for r in env["rows"]} == set(LANGS), "docs-smoke must cover docs/en and docs/ru"
    for lang in LANGS:
        for p in (DOCS / lang).glob("*.md"):
            if p.name.startswith("_"):
                continue
            for c in extract_commands(p.read_text(encoding="utf-8"), p.name):
                argv, reason = parse_line(c.text)
                assert reason in (None, "not a satk command"), f"{lang}/{p.name}:{c.line}: {reason}"


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("name", COMPONENT_PAGES)
def test_component_page_follows_template(lang, name):
    p = DOCS / lang / f"{name}.md"
    assert p.is_file(), f"component page missing: {lang}/{p.name}"
    text = p.read_text(encoding="utf-8")
    heads = [ln for ln in text.splitlines() if ln.startswith("## ")]
    # required by the template; the other sections are recommended (game.md has its own layout)
    assert any(QUICK_HEADING.match(ln) for ln in heads), f"{lang}/{p.name}: no quick example"
    for req in REQUIRED[lang]:
        assert any(h.startswith(req) for h in heads), f"{lang}/{p.name}: no '{req}'"
    cmds = [c for c in extract_commands(text, p.name) if c.argv is not None and not c.block_skip]
    skipped = name in QUICK_EXAMPLE_SKIPPED
    assert bool(cmds) != skipped, (f"{lang}/{p.name}: the quick example runs {len(cmds)} satk command(s); "
                                   "QUICK_EXAMPLE_SKIPPED is out of date")


@pytest.mark.parametrize("lang", LANGS)
def test_readme_lists_every_page(lang):
    text = (DOCS / lang / "README.md").read_text(encoding="utf-8")
    listed = set(re.findall(r"\]\(([\w-]+)\.md\)", text))
    assert set(COMPONENT_PAGES) <= listed, set(COMPONENT_PAGES) - listed
    on_disk = {p.stem for p in (DOCS / lang).glob("*.md")}
    assert on_disk <= listed | {"README"}, on_disk - listed  # no orphan pages (the template included)


def test_docs_parity_en_ru():
    """Every English page has its Russian mirror (and back), both start with the language switch."""
    env = invoke("dev.docs_parity", {})
    assert env["ok"] is True and env["pairs"] >= len(GUIDE_PAGES) + 1, env


@pytest.mark.parametrize("name,path", _user_pages(), ids=lambda v: v if isinstance(v, str) else
                         Path(v).relative_to(REPO_ROOT).as_posix())
def test_user_pages_have_no_machine_paths_or_internal_jargon(name, path):
    text = _COMMENT.sub("", path.read_text(encoding="utf-8"))
    machine = MACHINE_PATH.findall(text)
    jargon = JARGON.findall(text)
    if name not in MACHINE_PATH_OK:
        if name in MACHINE_PATH_DEBT:
            assert machine, f"{name} has no machine paths any more: remove it from MACHINE_PATH_DEBT"
        else:
            assert not machine, f"paths of one machine in a user page (write <workspace>): {machine[:3]}"
    if name in JARGON_DEBT:
        assert jargon, f"{name} has no internal jargon any more: remove it from JARGON_DEBT"
    else:
        assert not jargon, f"internal jargon in a user page: {jargon[:3]}"


def _linkcheck(c) -> list:
    files = [DOCS, REPO_ROOT / "CLAUDE.md", REPO_ROOT / "README.md", REPO_ROOT / "README.ru.md"]
    _, findings = check_paths(files, **config_roots(c))
    return [f.row() for f in findings if f.status in ("missing", "anchor")]


# The paths in the docs are resolved against a real workspace with a game copy (gta-sa-clean\..., data\gta.dat,
# work\...): without one every such path is "missing", so these checks run where the game is (the gate).
@pytest.mark.game
def test_linkcheck_docs_and_claude_md():
    assert _linkcheck(_config.load()) == []


@pytest.mark.game
def test_linkcheck_with_a_redirected_work_dir(tmp_path):
    """The gate and live tests redirect SATK_PATHS_WORK; the shared work/ paths in the docs stay generated."""
    c = _config.build({**os.environ, "SATK_PATHS_WORK": str(tmp_path / "work")})
    assert Path(c.paths.work) == tmp_path / "work"
    assert _linkcheck(c) == []


def test_generated_agent_docs_are_current():
    env = invoke("dev.gen_docs", {"check": True})
    assert env.get("ok") is True, env


def _main_checkout() -> bool:
    ws = Path(_config.load().paths.workspace)
    try:
        return os.path.samefile(REPO_ROOT, ws / "tools")
    except OSError:
        return False


@pytest.mark.skipif(not _main_checkout() and os.environ.get("SATK_CHECK_AGENT_DOCS") != "1",
                    reason="published copies are compared from the main checkout only "
                           "(a worktree may hold newer sources); set SATK_CHECK_AGENT_DOCS=1 to force")
def test_published_agent_docs_match_sources():
    from satk.docs.gate import _no_published_agent_docs

    why = _no_published_agent_docs()  # a fresh workspace: nothing was published, nothing to compare
    if why:
        pytest.skip(why)
    env = invoke("dev.sync_agent_docs", {"check": True})
    assert env["ok"] is True, env
