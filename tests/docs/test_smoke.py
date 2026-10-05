"""satk.docs.smoke and ``satk dev docs-smoke`` (WP-12). Synthetic pages; real ``satk`` subprocesses."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from satk.core.cli import CommandTree
from satk.core.registry import all_ops
from satk.docs.smoke import classify, default_pages, extract_commands, parse_line, shared_state_writer, tokenize


def test_tokenize_windows_style():
    assert tokenize(r'satk dev guard-test "D:\ws\GTA San Andreas\x.tmp"') == [
        "satk", "dev", "guard-test", r"D:\ws\GTA San Andreas\x.tmp"]
    assert tokenize("""satk blender run x --args '{"center":[1,2]}'""")[-1] == '{"center":[1,2]}'
    assert tokenize('a "" b') == ["a", "", "b"]
    with pytest.raises(ValueError):
        tokenize('satk "unclosed')


@pytest.mark.parametrize("line,argv,reason", [
    ("", None, None),
    ("# comment", None, None),
    ("rem cmd comment", None, None),
    (":: cmd comment", None, None),
    ("satk version", ["version"], None),
    ("PS> satk version", ["version"], None),
    ("satk version   # trailing comment", ["version"], None),
    (r"D:\ws\tools\satk.cmd config show", ["config", "show"], None),
    (r'& "D:\ws\tools\satk.cmd" version', ["version"], None),
    ("D:/ws/tools/satk.sh version", ["version"], None),
    ("powershell -File x.ps1", None, "not a satk command"),
    ("cd D:\\ws", None, "not a satk command"),
])
def test_parse_line(line, argv, reason):
    assert parse_line(line) == (argv, reason)


@pytest.mark.parametrize("line,word", [
    ("satk asset get <sid>", "placeholder"),
    ("satk version | findstr ok", "shell syntax"),
    ("satk version > out.txt", "shell syntax"),
    ("satk version; satk version", "shell syntax"),
])
def test_parse_line_not_runnable(line, word):
    argv, reason = parse_line(line)
    assert reason and reason.startswith("not runnable") and word in reason


PAGE = """# Страница

```powershell
satk outside-quick-example
```

## Быстрый пример

```powershell
satk version
# комментарий
satk config show `
  --section paths
```

```json
{"ok": true}
```

<!-- docs-smoke: skip needs a GPU -->

```powershell
satk view capture --target ariane
```

### Подраздел внутри примера

```cmd
satk nosuchgroup run
```

## Дальше

```powershell
satk after-section
```
"""


def test_extract_commands_scope_directive_continuation():
    cmds = extract_commands(PAGE, "p.md")
    texts = [c.text for c in cmds]
    assert texts == ["satk version", "satk config show --section paths", "satk view capture --target ariane",
                     "satk nosuchgroup run"]
    assert cmds[0].line == 10 and cmds[1].argv == ["config", "show", "--section", "paths"]
    assert cmds[2].block_skip == "needs a GPU"
    assert cmds[3].block_skip is None


def test_classify_against_real_registry():
    tree = CommandTree.build(all_ops())
    cmds = {c.text: c for c in extract_commands(PAGE, "p.md")}
    assert classify(cmds["satk version"], tree, strict=False) == ("run", "")
    assert classify(cmds["satk config show --section paths"], tree, strict=False)[0] == "run"
    assert classify(cmds["satk view capture --target ariane"], tree, strict=False)[0] == "skip"
    # an unknown group is a typo whether strict or not (all packages are merged; a page about a package that
    # is not merged yet marks its block with <!-- docs-smoke: skip ... -->)
    for strict in (False, True):
        st, why = classify(cmds["satk nosuchgroup run"], tree, strict=strict)
        assert st == "fail" and "nosuchgroup" in why
    typo = extract_commands("## Быстрый пример\n```\nsatk dev no-such-dev-cmd\nsatk --json version\nsatk\n"
                            "satk inedx build\nsatk index biuld\ncd x\n```\n")
    assert classify(typo[0], tree, strict=False)[0] == "fail"  # known group, unknown command
    assert classify(typo[1], tree, strict=False)[0] == "run"  # global flags are fine
    assert classify(typo[2], tree, strict=False)[0] == "run"  # bare satk lists commands
    st, why = classify(typo[3], tree, strict=False)  # verifier repro: used to be "skip (package not merged?)"
    assert st == "fail" and "did you mean 'index'" in why
    st, why = classify(typo[4], tree, strict=False)
    assert st == "fail" and "did you mean 'build'" in why
    assert classify(typo[5], tree, strict=False)[0] == "skip"  # other commands: not run...
    assert classify(typo[5], tree, strict=True)[0] == "fail"  # ...unless strict


def test_shared_state_writers():
    tree = CommandTree.build(all_ops())
    page = ("## Быстрый пример\n```\nsatk index build\nsatk --json re build --no-trunk\nsatk note add model:411 x\n"
            "satk view bookmark save here\nsatk view bookmark list\nsatk index status\nsatk asset find grove\n```\n")
    got = [shared_state_writer(c, tree) for c in extract_commands(page)]
    assert got == ["index.build", "re.build", "note.add", "view.bookmark", None, None, None]
    page = "## Quick example\n```\nsatk kb build\nsatk kb search x\nsatk paths import\nsatk paths near 1 2\n```\n"
    got = [shared_state_writer(c, tree) for c in extract_commands(page)]
    assert got == ["kb.build", None, "paths.import", None]


def test_default_pages_cover_both_languages_without_templates(tmp_path: Path):
    for lang in ("ru", "en"):
        (tmp_path / "docs" / lang).mkdir(parents=True)
        for name in ("b.md", "a.md", "_template.md"):
            (tmp_path / "docs" / lang / name).write_text("# x\n", encoding="utf-8")
    got = [p.relative_to(tmp_path).as_posix() for p in default_pages(tmp_path)]
    assert got == ["docs/en/a.md", "docs/en/b.md", "docs/ru/a.md", "docs/ru/b.md"]  # English first
    assert default_pages(tmp_path / "nowhere") == []


def test_quick_heading_in_both_languages():
    page = ("# T\n\n## Quick example\n```\nsatk version\n```\n## Commands\n```\nsatk nothing\n```\n"
            "## Быстрый пример\n```\nsatk help\n```\n")
    assert [c.text for c in extract_commands(page)] == ["satk version", "satk help"]


def _page(tmp: Path, body: str) -> Path:
    p = tmp / "page.md"
    p.write_text("# T\n\n## Быстрый пример\n\n```powershell\n" + body + "\n```\n", encoding="utf-8")
    return p


def test_op_dry_run(satk_home: Path, run_cli, tmp_path: Path):
    p = _page(tmp_path, "satk version\ncd somewhere")
    r = run_cli(["dev", "docs-smoke", str(p), "--dry-run"])
    assert r.code == 0, r.out
    assert [row[3] for row in r.json["rows"]] == ["would-run", "skip"]
    assert "private_work" not in r.json  # nothing rewrites shared state

    bad = tmp_path / "bad"
    bad.mkdir()
    p = _page(bad, "satk version\nsatk inedx build\ncd somewhere")
    r = run_cli(["dev", "docs-smoke", str(p), "--dry-run"])
    assert r.code == 1 and r.json["error"]["code"] == "EXTERNAL_TOOL"
    assert [row[3] for row in r.json["error"]["data"]["rows"]] == ["would-run", "fail", "skip"]
    r = run_cli(["dev", "docs-smoke", str(p), "--dry-run", "--strict"])
    assert [row[3] for row in r.json["error"]["data"]["rows"]] == ["would-run", "fail", "fail"]


@pytest.mark.slow
def test_op_runs_writers_in_a_private_work_dir(satk_home: Path, run_cli, tmp_path: Path):
    """A page that writes notes runs with its own SATK_PATHS_WORK: the shared notes DB is not touched."""
    page = _page(tmp_path, 'satk note add model:411 "docs-smoke private"\nsatk note list model:411')
    shared_notes = satk_home / "work" / "notes.sqlite"
    before = shared_notes.stat().st_mtime_ns if shared_notes.exists() else None
    r = run_cli(["dev", "docs-smoke", str(page)])
    assert r.code == 0, r.out
    env = r.json
    assert [row[3] for row in env["rows"]] == ["ok", "ok"]
    assert list(env["private_work"].values()) == ["removed"]
    after = shared_notes.stat().st_mtime_ns if shared_notes.exists() else None
    assert after == before
    smoke_tmp = satk_home / "work" / "tmp" / "docs-smoke"
    assert not smoke_tmp.exists() or not any(smoke_tmp.iterdir())
    log = [json.loads(x) for x in Path(env["log"]).read_text(encoding="utf-8").splitlines()]
    assert log[1]["work"] and "docs-smoke private" in log[1]["stdout"]  # the second command saw the first one's note

    r = run_cli(["dev", "docs-smoke", str(page), "--isolate", "none", "--dry-run"])
    assert r.code == 0 and "private_work" not in r.json


@pytest.mark.slow
def test_op_runs_commands_and_reports_failures(satk_home: Path, run_cli, tmp_path: Path):
    ok = _page(tmp_path, "satk version\nsatk config show --section paths")
    r = run_cli(["dev", "docs-smoke", str(ok)])
    assert r.code == 0, r.out
    env = r.json
    assert env["ok"] is True and env["passed"] == 2 and "failed" not in env
    assert [row[3] for row in env["rows"]] == ["ok", "ok"]
    assert env["log"].endswith(".jsonl") and Path(env["log"]).is_file()

    bad = tmp_path / "bad"
    bad.mkdir()
    protected = satk_home / "src" / "x.tmp"  # src\ of the isolated workspace is a protected root
    page = _page(bad, f'satk dev guard-test "{protected}"')
    r = run_cli(["dev", "docs-smoke", str(page)])
    assert r.code == 1
    err = r.json["error"]
    assert err["code"] == "EXTERNAL_TOOL"
    row = err["data"]["rows"][0]
    assert row[3] == "fail" and row[4] == 1 and "PROTECTED_PATH" in row[6]
    assert not protected.exists()
