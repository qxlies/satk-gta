"""What --over expands to (satk.batch.sources)."""

from __future__ import annotations

from pathlib import Path

import pytest

import synth
from satk.batch.sources import expand, is_sql
from satk.core.errors import SatkError
from satk.core.paths import jpath


def _tree(root: Path) -> Path:
    (root / "m" / "sub").mkdir(parents=True)
    for rel in ("m/B.dff", "m/a.dff", "m/a.txd", "m/sub/c.dff", "m/readme.txt"):
        (root / rel).write_bytes(synth.dff() if rel.endswith(".dff") else b"x")
    (root / "m" / "pack.img").write_bytes(synth.img([("Zed.DFF", synth.dff()), ("one.txd", synth.txd()),
                                                     ("two.dff", synth.dff())]))
    return root / "m"


def test_glob_sorted_case_insensitive_and_recursive(tmp_path, satk_home):
    m = _tree(tmp_path)
    got = expand(str(m / "*.dff"))
    assert got.kind == "glob"
    assert [Path(p).name for p in got.items] == ["a.dff", "B.dff"]
    assert got.base == jpath(m)
    deep = expand(str(m / "**" / "*.dff"))
    assert [Path(p).name for p in deep.items] == ["a.dff", "B.dff", "c.dff"]
    assert all(p == jpath(p) for p in deep.items)
    assert expand(str(m / "*.nothing")).items == []


def test_img_entries(tmp_path, satk_home):
    m = _tree(tmp_path)
    got = expand(str(m / "pack.img" / "*.dff"))
    assert got.kind == "img"
    assert got.items == [f"{jpath(m / 'pack.img')}/two.dff", f"{jpath(m / 'pack.img')}/Zed.DFF"]
    star = expand(str(m / "*.img" / "*.txd"))
    assert star.items == [f"{jpath(m / 'pack.img')}/one.txd"]
    one = expand(str(m / "pack.img" / "zed.dff"))  # no glob: one entry, any case
    assert one.items == [f"{jpath(m / 'pack.img')}/Zed.DFF"]
    with pytest.raises(SatkError) as ei:
        expand(str(m / "pack.img" / "a" / "*.dff"))
    assert ei.value.code == "BAD_PARAMS"


def test_unreadable_img_is_a_warning(tmp_path, satk_home):
    (tmp_path / "bad.img").write_bytes(b"not an img at all")
    got = expand(str(tmp_path / "*.img" / "*"))
    assert got.items == [] and any(w.startswith("IMG_UNREADABLE") for w in got.warn)


def test_folder_file_and_literal_items(tmp_path, satk_home):
    m = _tree(tmp_path)
    kids = expand(str(m))
    assert kids.kind == "dir" and [Path(p).name for p in kids.items] == ["a.dff", "a.txd", "B.dff", "pack.img",
                                                                          "readme.txt", "sub"]
    one = expand(str(m / "a.dff"))
    assert one.kind == "file" and one.items == [jpath(m / "a.dff")]
    sids = expand("model:411, model:415,")
    assert sids.kind == "items" and sids.items == ["model:411", "model:415"]
    assert expand("infernus,cheetah").items == ["infernus", "cheetah"]
    with pytest.raises(SatkError) as ei:
        expand(str(m / "missing.dff"))
    assert ei.value.code == "NOT_FOUND" and "glob needs" in ei.value.hint
    with pytest.raises(SatkError):
        expand("  ")


def test_list_file(tmp_path, satk_home):
    lst = tmp_path / "inputs.txt"
    lst.write_text("﻿# comment\n\na.dff\n  b.dff  \n{\"target\": \"c.dff\", \"sev\": \"info\"}\n", encoding="utf-8")
    got = expand(f"@{lst}")
    assert got.kind == "list" and got.items == ["a.dff", "b.dff", {"target": "c.dff", "sev": "info"}]
    bad = tmp_path / "bad.txt"
    bad.write_text("{not json}\n", encoding="utf-8")
    with pytest.raises(SatkError) as ei:
        expand(f"@{bad}")
    assert ei.value.code == "BAD_PARAMS" and "bad.txt:1" in ei.value.msg
    with pytest.raises(SatkError) as ei:
        expand(f"@{tmp_path / 'nope.txt'}")
    assert ei.value.code == "NOT_FOUND"


def test_sql_over_the_fake_index(satk_home):
    from satk.index.api import FakeIndexDB, override_index

    assert is_sql("SELECT 1") and is_sql("  with x as (select 1) select * from x") and is_sql("sql:select 1")
    assert not is_sql("selection/*.dff")
    with override_index(FakeIndexDB()):
        one_col = expand("SELECT sid FROM v_model ORDER BY sid", params={"id"})
        assert one_col.kind == "sql" and "model:411" in one_col.items and all(isinstance(x, str) for x in one_col.items)
        named = expand("sql: SELECT sid AS id, 'x' AS other FROM v_model WHERE name = 'infernus'", params={"id"})
        assert named.items == [{"id": "model:411"}]
        with pytest.raises(SatkError) as ei:
            expand("SELECT * FROM no_such_table")
        assert ei.value.code == "BAD_PARAMS"


def test_relative_glob_falls_back_to_the_game_root(tmp_path, satk_home, monkeypatch):
    game = satk_home / "gta-sa-clean"  # the default game root of the isolated workspace
    (game / "data").mkdir(parents=True)
    (game / "data" / "x.ide").write_text("objs\nend\n", encoding="latin-1")
    monkeypatch.chdir(tmp_path)
    got = expand("data/*.ide")
    assert got.items == [jpath(game / "data" / "x.ide")]
    assert any(w.startswith("OVER: matched under the vanilla game root") for w in got.warn)
