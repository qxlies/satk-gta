"""The people's guide (M3 A2): ``satk``, ``satk -h``, ``satk help`` [--ru] [--all]."""

from __future__ import annotations

import re
import tomllib

import pytest

from satk.core import registry as R
from satk.core.cli import CommandTree
from satk.runtime import guide as G
from satk.runtime import help as H

CYR = re.compile("[а-яА-ЯёЁ]")


@pytest.mark.parametrize("ru", [False, True])
def test_guide_fits_one_screen(ru, run_cli):
    argv = ["help", "--ru"] if ru else ["help"]
    out = run_cli(argv, tty=True).out
    lines = out.rstrip("\n").splitlines()
    assert 10 <= len(lines) <= G.MAX_LINES, out
    assert max(len(ln) for ln in lines) <= G.MAX_WIDTH, max(lines, key=len)
    assert bool(CYR.search(out)) is ru  # one language per text
    assert ("Я хочу" in out) if ru else ("I want to..." in out)


def test_guide_hides_engine_dev_and_game_clone(run_cli):
    for argv in (["help"], ["help", "--ru"]):
        text = run_cli(argv + ["--json"]).json["text"]
        assert "satk engine" not in text and "satk dev" not in text and "game clone" not in text
    for _, items in G.GUIDE:
        for _, op_name, _ in items:
            assert op_name not in G.HIDDEN_OPS and op_name.split(".")[0] not in G.HIDDEN_GROUPS


def test_guide_commands_run_the_named_operations():
    tree = CommandTree.build(R.all_ops())
    for _, items in G.GUIDE:
        for _, op_name, cmd in items:
            try:
                R.get_op(op_name)
            except Exception:  # noqa: BLE001 - package not merged: the guide leaves the line out
                continue
            words = cmd.split()[1:]
            node, used, _ = tree.walk(words)
            assert node.spec is not None and node.spec.name == op_name, (cmd, used)


def test_russian_file_matches_the_english_keys():
    data = tomllib.loads(G._RU_FILE.read_text(encoding="utf-8"))
    assert set(data["groups"]) == set(G.EN["groups"]) and set(data["items"]) == set(G.EN["items"])
    for k in ("title", "want", "more"):
        assert CYR.search(data[k]), k
    for k, v in data["items"].items():
        assert CYR.search(v), k
    for k, v in G.EN["items"].items():
        assert not CYR.search(v), k


def test_missing_operations_are_left_out(isolated_ops):
    @R.op("init", summary="Set up.", summary_ru="Настроить.", mcp=False)
    def _init() -> dict:
        return {}

    env = G.render()
    assert [c for c, _ in env["items"]] == ["satk init"]
    assert "satk doctor" not in env["text"]


def test_entry_points(run_cli):
    tty = run_cli([], tty=True).out
    assert "I want to..." in tty and "satk help --all" in tty
    assert run_cli(["-h"]).json["topic"] == "guide"
    assert run_cli(["--ru"], tty=True).out.startswith("satk —")
    full = run_cli(["--all"]).json
    assert full["topic"] == "all" and full["cols"] == ["group", "command", "mcp", "summary"]
    assert {r[1] for r in full["rows"]} == {f"satk {o.cli}" for o in R.all_ops()}
    assert "satk engine build" in {r[1] for r in full["rows"]}  # --all hides nothing
    assert run_cli(["help", "--all"]).json["rows"] == full["rows"]
    assert run_cli(["help", "--json"]).json["topic"] == "guide"
    assert run_cli(["help", "start", "--json"]).json["topic"] == "start"
    rows = run_cli([]).json["rows"]  # bare satk with JSON output: the machine listing as before
    assert "satk version" in [r[0] for r in rows]


def test_agents_keep_the_start_topic():
    assert H.render(None)["topic"] == "start"  # MCP satk_help() without a topic
    assert H.render(None, ru=True)["cols"][-1] == "summary_ru"
    env = R.invoke(R.get_op("help"), {})
    assert env["topic"] == "start"
    assert H.render("guide")["topic"] == "guide" and H.render("all")["topic"] == "all"


def test_every_command_help_renders(run_cli):
    """``satk <command> -h`` for every operation (a literal % in a parameter help broke ``satk init -h``)."""
    for o in R.all_ops():
        r = run_cli([*o.cli_path, "-h"])
        assert r.code == 0 and f"usage: satk {o.cli}" in r.out, (o.name, r.out[-300:])
