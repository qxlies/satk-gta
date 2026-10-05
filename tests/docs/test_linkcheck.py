"""satk.docs.linkcheck and ``satk dev linkcheck`` (WP-12). Synthetic files only."""

from __future__ import annotations

from pathlib import Path

import pytest

from satk.docs.linkcheck import check_file, extract_refs, headings, slugify


def _refs(text: str):
    return [(r.kind, r.text) for r in extract_refs(text)]


def _status(findings):
    return {(f.ref, f.status) for f in findings}


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    """A fake workspace: tools/satk.cmd, work/, game/data/gta.dat, a dir with spaces."""
    w = tmp_path / "ws"
    (w / "tools").mkdir(parents=True)
    (w / "tools" / "satk.cmd").write_text("@echo off\n", encoding="utf-8")
    (w / "work" / "index").mkdir(parents=True)
    (w / "game" / "data").mkdir(parents=True)
    (w / "game" / "data" / "gta.dat").write_text("IMG MODELS\\GTA3.IMG\n", encoding="utf-8")
    (w / "Program Files" / "Blender 5.1").mkdir(parents=True)
    (w / "Program Files" / "Blender 5.1" / "blender.exe").write_bytes(b"MZ")
    (w / "venv" / "Scripts").mkdir(parents=True)
    (w / "venv" / "Scripts" / "python.exe").write_bytes(b"MZ")
    (w / "docs").mkdir()
    (w / "docs" / "other.md").write_text("# Другой заголовок\n\n## Раздел `code` и всё!\n", encoding="utf-8")
    return w


def _md(ws: Path, text: str, name: str = "page.md") -> Path:
    p = ws / "docs" / name
    p.write_text(text, encoding="utf-8")
    return p


def _check(ws: Path, text: str):
    md = _md(ws, text)
    return check_file(md, workspace=ws, work=ws / "work", game=ws / "game")


# --------------------------------------------------------------------------- extraction


def test_extract_links_images_and_angle_targets():
    refs = _refs("See [a](x/y.md#sec), ![img](img/p.png) and [b](<dir with space/f.md>), [u](https://e.org).")
    assert ("link", "x/y.md") in refs and ("link", "img/p.png") in refs
    assert ("link", "dir with space/f.md") in refs and ("link", "https://e.org") in refs


def test_extract_absolute_paths_prose_span_fence():
    text = "Prose D:\\ws\\tools, code `D:/ws/work/x`\n\n```powershell\nsatk dev guard-test \"C:\\A B\\c.tmp\"\n```\n"
    refs = _refs(text)
    assert ("abs", "D:\\ws\\tools") in refs
    assert ("abs", "D:/ws/work/x") in refs
    assert ("abs", "C:\\A B\\c.tmp") in refs  # quoted: spaces kept


def test_comments_and_pragmas_are_ignored():
    text = ("a <!-- D:\\in\\comment --> b\n"
            "c D:\\ignored\\line <!-- linkcheck: ignore -->\n"
            "<!-- linkcheck: off -->\nD:\\off\\region\n<!-- linkcheck: on -->\nD:\\after\\on\n")
    refs = [r[1] for r in _refs(text)]
    assert refs == ["D:\\after\\on"]


def test_rel_spans_only_whole_path_like_spans():
    refs = _refs("`tools\\satk.cmd`, `tex:bistro/vent_64`, `satk.core.ids`, `satk dev linkcheck x/y`, `GTA San Andreas\\`")
    assert ("rel", "tools\\satk.cmd") in refs and ("rel", "GTA San Andreas\\") in refs
    assert all(t not in ("tex:bistro/vent_64", "satk.core.ids") for _, t in refs)
    # a command-like span may be extracted, but its first word has no separator, so the checker ignores
    # it (see test_unanchored_spans_that_are_not_paths)


def test_slugify_and_headings_cyrillic_and_duplicates():
    assert slugify("Раздел `code` и всё!") == "раздел-code-и-всё"
    assert slugify("Быстрый пример") == "быстрый-пример"
    hs = headings("# A\n## A\n```\n# not a heading\n```\n### Б в\n")
    assert hs == {"a", "a-1", "б-в"}


# --------------------------------------------------------------------------- checking


def test_existing_and_missing_absolute_paths(ws: Path):
    tools = str(ws / "tools" / "satk.cmd")
    missing = str(ws / "nope" / "x.txt")
    f = _check(ws, f"Run {tools} and {missing}.\n")
    st = _status(f)
    assert (tools, "ok") in st
    assert (missing, "missing") in st
    det = next(x.detail for x in f if x.status == "missing")
    assert "nearest existing" in det


def test_path_with_spaces_longest_existing_candidate(ws: Path):
    exe = str(ws / "Program Files" / "Blender 5.1" / "blender.exe")
    f = _check(ws, f"Blender: `{exe} -b --factory-startup` here\n")
    assert (exe, "ok") in _status(f)


def test_command_without_extension_and_placeholders(ws: Path):
    py = str(ws / "venv" / "Scripts" / "python")
    tmp = str(ws / "work" / "index") + "\\<profile>.sqlite"
    f = _check(ws, f"`{py} -m pytest` and `{tmp}`\n")
    assert {s for _, s in _status(f)} == {"ok"}


def test_missing_under_work_is_generated_warning(ws: Path):
    gen = str(ws / "work" / "out" / "sheets" / "a.png")
    f = _check(ws, f"Output: {gen}\n")
    assert (gen, "generated") in _status(f)


def test_missing_under_an_extra_generated_root_is_generated(ws: Path):
    """A redirected SATK_PATHS_WORK: the shared <workspace>/work the docs mention stays 'generated'."""
    private = ws / "private-work"
    private.mkdir()
    (ws / "work" / "cache" / "tex").mkdir(parents=True)
    shared = str(ws / "work" / "out" / "sheets" / "a.png")
    text = f"Output: {shared}, `work\\out\\mods\\x.txd`, `cache\\tex\\<hh>.png`\n"
    md = _md(ws, text)
    st = _status(check_file(md, workspace=ws, work=private, game=ws / "game"))
    assert (shared, "missing") in st and ("work\\out\\mods\\x.txd", "missing") in st  # without the extra root
    st = _status(check_file(md, workspace=ws, work=private, game=ws / "game", generated=[ws / "work"]))
    assert (shared, "generated") in st and ("work\\out\\mods\\x.txd", "generated") in st
    assert ("cache\\tex\\<hh>.png", "ok") in st  # the extra root is a base for work-relative spans too


def test_config_roots_add_the_default_work_dir(satk_home: Path, monkeypatch):
    from satk.core import config as _config
    from satk.docs.linkcheck import config_roots

    monkeypatch.setenv("SATK_PATHS_WORK", str(satk_home / "elsewhere"))
    _config.reset()
    roots = config_roots(_config.load())
    assert roots["work"] == satk_home / "elsewhere"
    assert tuple(roots["generated"]) == (satk_home / "work",)
    assert roots["workspace"] == satk_home


def test_relative_spans_resolve_against_bases(ws: Path):
    f = _check(ws, "`tools\\satk.cmd`, `data/gta.dat`, `tools\\missing.cmd`, `nowhere\\x.txt`\n")
    st = _status(f)
    assert ("tools\\satk.cmd", "ok") in st  # workspace base
    assert ("data/gta.dat", "ok") in st  # game base
    assert ("tools\\missing.cmd", "missing") in st
    assert ("nowhere\\x.txt", "missing") in st  # first segment in no base, but clearly a path


def test_typo_in_the_first_segment_is_reported_with_did_you_mean(ws: Path):
    """Verifier repro (WP-12 fix round): typos in the first segment used to be ignored."""
    f = _check(ws, "`toolz\\satk.cmd`, `docs/othr.md`, `gam\\data\\gta.dat`, `doc/other.md`, `tools\\satk.cmd`\n")
    st = _status(f)
    assert ("tools\\satk.cmd", "ok") in st
    for ref, near in (("toolz\\satk.cmd", "tools"), ("gam\\data\\gta.dat", "game"), ("doc/other.md", "docs")):
        x = next(x for x in f if x.ref == ref)
        assert x.status == "missing" and f"did you mean '{near}'" in x.detail, x
    assert ("docs/othr.md", "missing") in st  # anchored in the workspace, the second segment is wrong


def test_unanchored_spans_that_are_not_paths(ws: Path):
    text = ("`tex:bistro/vent_64` `satk.core.ids` `vanilla/installed/samp` `Win32/x64` `.rdata/.data` "
            "`satk formats ls models\\gta3.img` `python -m pytest tests/core`\n")
    assert _check(ws, text) == []


def test_relative_spans_resolve_against_directories_the_page_mentions(ws: Path):
    (ws / "viewer" / "ariane" / "bin").mkdir(parents=True)
    (ws / "viewer" / "ariane" / "bin" / "ariane.exe").write_bytes(b"MZ")
    (ws / "tools" / "blender" / "addon").mkdir(parents=True)
    (ws / "work" / "blender").mkdir(parents=True)  # work\blender exists, but has no addon\
    (ws / "work" / "cache" / "tex").mkdir(parents=True)
    text = ("| `viewer\\ariane\\` | built (`bin\\ariane.exe`) |\n"
            "| `tools\\` | repo (`blender\\addon`) |\n"
            "Derived files: `cache\\tex\\<hh>\\<pix>.png`, `bin\\nothing.exe`\n")
    st = _status(_check(ws, text))
    assert ("bin\\ariane.exe", "ok") in st  # base: viewer\ariane (mentioned on the page)
    assert ("blender\\addon", "ok") in st  # tools\blender\addon, not work\blender\addon
    assert ("cache\\tex\\<hh>\\<pix>.png", "ok") in st  # paths.work is a base
    assert ("bin\\nothing.exe", "missing") in st


def test_protocol_method_names_are_not_paths(ws: Path):
    (ws / "tools" / "lister").mkdir()
    st = _status(_check(ws, "MCP `tools/list` is 17 KB; `tools/lister` exists, `tools/listed` does not\n"))
    assert not any(r == "tools/list" for r, _ in st)
    assert ("tools/lister", "ok") in st and ("tools/listed", "missing") in st


def test_relative_spans_of_the_original_install(ws: Path):
    """Files only the original install has (game.md: skipped by `satk game clone`) resolve there."""
    inst = ws / "install"
    (inst / "data").mkdir(parents=True)
    (inst / "data" / "colorcycle.dat").write_text("x", encoding="utf-8")
    md = _md(ws, "`data\\colorcycle.dat` and `data\\gta.dat` and `data\\nothing.dat`\n")
    st = _status(check_file(md, workspace=ws, work=ws / "work", game=ws / "game", installed=inst))
    assert ("data\\colorcycle.dat", "ok") in st and ("data\\gta.dat", "ok") in st
    assert ("data\\nothing.dat", "missing") in st
    st0 = _status(check_file(md, workspace=ws, work=ws / "work", game=ws / "game"))
    assert ("data\\colorcycle.dat", "missing") in st0  # without the install root it is reported


def test_links_and_anchors(ws: Path):
    f = _check(ws, "[ok](other.md#раздел-code-и-всё) [bad](other.md#нет-такого) [self](#заголовок) "
                   "[gone](missing.md) [web](https://example.org/x)\n\n# Заголовок\n")
    st = _status(f)
    assert ("other.md#раздел-code-и-всё", "ok") in st
    assert ("other.md#нет-такого", "anchor") in st
    assert ("#заголовок", "ok") in st
    assert ("missing.md", "missing") in st
    assert not any("example.org" in r for r, _ in st)


# --------------------------------------------------------------------------- operation


def test_op_ok_and_missing(satk_home: Path, run_cli):
    docs = satk_home / "docs"
    docs.mkdir()
    (satk_home / "work" / "x.txt").write_text("x", encoding="utf-8")
    good = docs / "good.md"
    good.write_text(f"See {satk_home / 'work' / 'x.txt'}\n", encoding="utf-8")
    r = run_cli(["dev", "linkcheck", str(good), "--show-ok"])
    assert r.code == 0, r.out
    env = r.json
    assert env["ok"] is True and env["missing"] == 0 and env["passed"] == 1
    assert env["rows"] and env["rows"][0][3] == "ok"

    bad = docs / "bad.md"
    bad.write_text(f"See {satk_home / 'nope.txt'}\n", encoding="utf-8")
    r = run_cli(["dev", "linkcheck", str(docs)])
    assert r.code == 1
    err = r.json["error"]
    assert err["code"] == "NOT_FOUND" and err["data"]["missing"] == 1
    assert err["data"]["rows"][0][3] == "missing"


def test_op_missing_input(satk_home: Path, run_cli):
    r = run_cli(["dev", "linkcheck", str(satk_home / "nope.md")])
    assert r.code == 1 and r.json["error"]["code"] == "NOT_FOUND"
