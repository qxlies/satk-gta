"""satk.docs.parity and ``satk dev docs-parity`` (M3 A3). Synthetic repositories only."""

from __future__ import annotations

from pathlib import Path

from satk.docs.parity import SWITCH_TEXT, check, pairs, switch_line

RU_SWITCH = SWITCH_TEXT["en"]  # the link an English page carries (Russian words)


def _page(title: str, switch: str, body: str = "") -> str:
    return f"# {title}\n\n{switch}\n\n<!-- owner comment -->\n\n{body}"


EN_BODY = "## What it is\n\nText.\n\n## Quick example\n\n```powershell\nsatk version\nsatk asset find \"grove\"\n```\n"
RU_BODY = ("## Что это\n\nТекст.\n\n## Быстрый пример\n\n```powershell\nsatk version\n"
           "satk asset find \"роща\"\n```\n")


def _repo(tmp: Path, en_pages: dict[str, str], ru_pages: dict[str, str], readme: bool = True) -> Path:
    r = tmp / "repo"
    for lang, pages in (("en", en_pages), ("ru", ru_pages)):
        (r / "docs" / lang).mkdir(parents=True, exist_ok=True)
        for name, text in pages.items():
            (r / "docs" / lang / f"{name}.md").write_text(text, encoding="utf-8")
    if readme:
        (r / "README.md").write_text(_page("satk", f"[{RU_SWITCH}](README.ru.md)", "Text.\n"), encoding="utf-8")
        (r / "README.ru.md").write_text(_page("satk", "[English version](README.md)", "Текст.\n"), encoding="utf-8")
    return r


def _good(name: str) -> tuple[str, str]:
    return (_page("Page", f"[{RU_SWITCH}](../ru/{name}.md)", EN_BODY),
            _page("Страница", f"[English version](../en/{name}.md)", RU_BODY))


def _checks(issues) -> set[tuple[str, str]]:
    return {(i.page, i.check) for i in issues}


def test_switch_line_is_relative_to_the_page(tmp_path: Path):
    r = tmp_path
    assert switch_line("en", r / "docs" / "en" / "a.md", r / "docs" / "ru" / "a.md") == f"[{RU_SWITCH}](../ru/a.md)"
    assert switch_line("ru", r / "README.ru.md", r / "README.md") == "[English version](README.md)"


def test_a_matching_pair_passes(tmp_path: Path):
    en, ru = _good("game")
    r = _repo(tmp_path, {"game": en}, {"game": ru})
    n, issues = check(r)
    assert n == 2 and issues == []  # README pair + game
    assert [p.en.name for p in pairs(r)] == ["README.md", "game.md"]


def test_missing_mirror_in_either_direction(tmp_path: Path):
    en, ru = _good("a")
    r = _repo(tmp_path, {"a": en}, {"b": ru.replace("../en/a.md", "../en/b.md")}, readme=False)
    got = _checks(check(r)[1])
    assert ("docs/en/a.md", "mirror") in got and ("docs/ru/b.md", "mirror") in got
    assert not any(c == "switch" for _, c in got)  # both pages still link to the (missing) mirror correctly


def test_switch_must_follow_the_title(tmp_path: Path):
    en, ru = _good("a")
    bad_en = "# Page\n\nSome text first.\n\n" + f"[{RU_SWITCH}](../ru/a.md)\n"
    bad_ru = ru.replace("[English version](../en/a.md)", "[English](../en/a.md)")
    r = _repo(tmp_path, {"a": bad_en}, {"a": bad_ru}, readme=False)
    got = check(r)[1]
    assert {("docs/en/a.md", "switch"), ("docs/ru/a.md", "switch")} <= _checks(got)
    assert "Some text first" in next(i.detail for i in got if i.page == "docs/en/a.md" and i.check == "switch")


def test_root_readme_pair_is_checked(tmp_path: Path):
    r = _repo(tmp_path, {}, {}, readme=False)
    (r / "README.md").write_text("# satk\n\nNo switch here.\n", encoding="utf-8")
    got = _checks(check(r)[1])
    assert ("README.md", "switch") in got and ("README.md", "mirror") in got


def test_english_page_without_cyrillic_outside_the_switch(tmp_path: Path):
    en, ru = _good("a")
    r = _repo(tmp_path, {"a": en + "\nA mixed line: пример.\n"}, {"a": ru}, readme=False)
    got = check(r)[1]
    assert _checks(got) == {("docs/en/a.md", "language")}
    assert "пример" in got[0].detail


def test_structure_sections_fences_and_quick_commands(tmp_path: Path):
    en, ru = _good("a")
    en2 = en + "\n## Extra\n\n```json\n{}\n```\n"
    r = _repo(tmp_path, {"a": en2}, {"a": ru}, readme=False)
    assert _checks(check(r)[1]) == {("docs/en/a.md", "sections"), ("docs/en/a.md", "fences")}
    en3 = en.replace("satk version\n", "satk version\nsatk help\n")
    r = _repo(tmp_path / "x", {"a": en3}, {"a": ru}, readme=False)
    got = check(r)[1]
    assert _checks(got) == {("docs/en/a.md", "quick")} and "satk help" in got[0].detail


def test_translated_quoted_arguments_are_the_same_command(tmp_path: Path):
    en, ru = _good("a")  # 'satk asset find "grove"' vs 'satk asset find "роща"'
    r = _repo(tmp_path, {"a": en}, {"a": ru}, readme=False)
    assert check(r)[1] == []


def test_op_ok_and_failure(satk_home: Path, tmp_path: Path, run_cli):
    en, ru = _good("a")
    r = _repo(tmp_path, {"a": en}, {"a": ru})
    res = run_cli(["dev", "docs-parity", "--root", str(r)])
    assert res.code == 0, res.out
    assert res.json["ok"] is True and res.json["pairs"] == 2
    (r / "docs" / "ru" / "a.md").unlink()
    res = run_cli(["dev", "docs-parity", "--root", str(r)])
    assert res.code == 1
    err = res.json["error"]
    assert err["code"] == "CHECK_FAILED" and err["data"]["rows"] == [["docs/en/a.md", "mirror",
                                                                      "no Russian mirror docs/ru/a.md"]]
    res = run_cli(["dev", "docs-parity", "--root", str(tmp_path / "nowhere")])
    assert res.code == 1 and res.json["error"]["code"] == "NOT_FOUND"
