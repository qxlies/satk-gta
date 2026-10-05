"""``satk crash bisect`` (M3 C1): halving plan with modloader.ini fragments; nothing is written."""

from __future__ import annotations

from pathlib import Path

import pytest
from sp_helpers import DEFAULT_INI, make_game

from satk.core.errors import SatkError
from satk.crash.bisect import parse_results, plan

MODS = ("Alpha", "Bravo", "Charlie", "Delta", "Echo")


def _ignored(section: str) -> list[str]:
    return [ln.strip() for ln in section.splitlines()[1:] if ln.strip() and not ln.strip().startswith(";")]


@pytest.fixture
def game(tmp_path: Path) -> Path:
    return make_game(tmp_path / "g", {m: {"x.txt": m} for m in MODS + ("_ignore",)})


def _snapshot(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_steps_find_the_culprit(game: Path):
    before = _snapshot(game)
    s1 = plan(str(game))                                       # a game folder works as well as modloader/
    assert (s1["step"], s1["steps"], s1["candidates"]) == (1, 4, 5)
    assert s1["section"].splitlines()[0] == "[Profiles.Default.IgnoreMods]"
    assert _ignored(s1["section"]) == ["_ignore", *MODS] and _ignored(s1["undo"]) == ["_ignore"]
    # culprit = Delta: step 2 keeps Alpha..Charlie (ok), step 3 keeps Delta (crash) -> Delta
    s2 = plan(str(game / "modloader"), ["ok"])
    assert s2["keep"] == ["Alpha", "Bravo", "Charlie"] and _ignored(s2["section"]) == ["_ignore", "Delta", "Echo"]
    s3 = plan(str(game), ["ok", "ok"])
    assert s3["keep"] == ["Delta"] and _ignored(s3["section"]) == ["_ignore", "Echo"] and s3["candidates"] == 2
    done = plan(str(game), ["ok", "ok", "crash"])
    assert done["done"] is True and done["culprit"] == "Delta" and _ignored(done["section"]) == ["_ignore", "Delta"]
    other = plan(str(game), ["ok", "crash", "crash", "ok"])
    assert other["culprit"] == "Bravo"
    assert _snapshot(game) == before                           # read-only


def test_crash_with_all_mods_off_ends_outside_modloader(game: Path):
    r = plan(str(game), ["crash"])
    assert r["done"] is True and "culprit" not in r
    assert "outside modloader" in r["result"]
    with pytest.raises(SatkError) as e:
        plan(str(game), ["crash", "ok"])
    assert e.value.code == "BAD_PARAMS"


def test_results_and_errors(game: Path, tmp_path: Path):
    assert parse_results(["ok,crash", "good", "bad;c"]) == ["ok", "crash", "ok", "crash", "crash"]
    with pytest.raises(SatkError) as e:
        parse_results(["maybe"])
    assert e.value.code == "BAD_PARAMS"
    with pytest.raises(SatkError) as e:
        plan(str(game), ["ok", "ok", "ok", "ok", "ok"])
    assert e.value.code == "BAD_PARAMS" and "too many results" in e.value.msg
    with pytest.raises(SatkError) as e:
        plan(str(tmp_path / "nothing"))
    assert e.value.code == "NOT_FOUND"
    empty = make_game(tmp_path / "e", {"_ignore": {"a.txt": "x"}})
    with pytest.raises(SatkError) as e:
        plan(str(empty))
    assert e.value.code == "NOT_FOUND" and "1 disabled" in e.value.msg


def test_profile_spelling_nested_and_single_mod(tmp_path: Path):
    ini = DEFAULT_INI.replace("Profile = Default", "Profile = Test").replace("Profiles.Default.", "Profiles.Test.") \
        .replace("[Profiles.Test.IgnoreMods]", "[profiles.test.ignoremods]")
    g = make_game(tmp_path / "g", {"Only": {"modloader.ini": "[Folder.Config]\n"}, "_ignore": {"a": "x"}}, ini=ini)
    r = plan(str(g))
    assert r["profile"] == "Test" and r["section"].startswith("[profiles.test.ignoremods]\n")
    assert r["steps"] == 1 and any(w.startswith("NESTED: Only") for w in r["warn"])
    assert plan(str(g), ["ok"])["culprit"] == "Only"


def test_cli(game: Path, run_cli, satk_home):
    r = run_cli(["crash", "bisect", str(game), "--results", "ok", "crash"])
    assert r.code == 0 and r.json["keep"] == ["Alpha", "Bravo"] and r.json["step"] == 3
    r = run_cli(["crash", "bisect", str(game), "--results", "ok,crash"], tty=True)
    assert r.code == 0 and "[Profiles.Default.IgnoreMods]" in r.out
    r = run_cli(["crash", "bisect"])                           # no configured game in the isolated home
    assert r.code != 0 and r.json["error"]["code"] == "NOT_FOUND"
