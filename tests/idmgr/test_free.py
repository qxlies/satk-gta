"""``satk id free`` and the id-space helpers (synthetic profiles of tests/idmgr/conftest.py)."""

from __future__ import annotations

import pytest

from satk.core.errors import SatkError
from satk.idmgr.ranges import ENGINE_RESERVED, SAMP_RESERVED, f92_max_id, fmt_ranges, iter_range, parse_ranges


def test_parse_and_format_ranges():
    assert parse_ranges("612-799, 15000..15099;-1000..-1002 20500") == [
        (612, 799), (15000, 15099), (-1000, -1002), (20500, 20500)]
    assert list(iter_range(-1000, -1002)) == [-1000, -1001, -1002]
    assert fmt_ranges([612, 613, 614, 700, -1000, -1001, 5]) == ["612-614", "700", "-1000..-1001", "5"]
    for bad in ("799-612", "abc", "1-2-3"):
        with pytest.raises(SatkError) as e:
            parse_ranges(bad)
        assert e.value.code == "BAD_PARAMS"


def test_f92_ini():
    assert f92_max_id("[ID LIMITS]\nApply ID limit patch = 1\ndff = 25000\n") == 24999
    assert f92_max_id("FILE_TYPE_DFF = 30000") == 29999
    assert f92_max_id("txd = 5000") is None


def test_taken_sources(world):
    from satk.idmgr.free import open_profiles, taken_ids

    t = taken_ids(open_profiles(None))
    assert t.profiles == ["installed", "vanilla"]
    for i in (400, 401, 7, 300, 321, 1000, 1001, 1003, 1004):      # loaded IDE lines
        assert i in t.ids
    assert "sampobj" in t.ids[19000]                                # SAMP/samp.ide, present but not loaded
    for i in (15000, 15001, 15002):                                 # modloader IDE files and readme lines
        assert i in t.ids
    assert 1002 not in t.ids and 1005 not in t.ids
    assert all(i in t.ids for a, b, _ in ENGINE_RESERVED for i in iter_range(a, b))
    assert all(i in t.ids for a, b, _ in SAMP_RESERVED for i in iter_range(a, b))
    assert taken_ids(open_profiles(["vanilla"]), samp=False).ids.get(18700) is None
    # f92 ini only in 'installed': not every profile has it -> stock limit plus a hint
    assert t.max_id == 19999 and any(h.startswith("F92:") for h in t.hints)
    only = taken_ids(open_profiles(["installed"]))
    assert only.max_id == 24999 and "fastman92" in only.max_from


def test_cli_free_never_returns_a_taken_id(world, run_cli):
    from satk.idmgr.free import open_profiles, taken_ids

    taken = taken_ids(open_profiles(None)).ids
    for kind in ("vehicle", "ped", "weapon", "object"):
        r = run_cli(["id", "free", "--kind", kind, "--count", "40"])
        assert r.code == 0, r.err
        env = r.json
        assert len(env["ids"]) == 40 and not set(env["ids"]) & set(taken), kind
        assert env["profiles"] == ["installed", "vanilla"]
    v = run_cli(["id", "free", "--kind", "vehicle", "--count", "3"]).json
    assert v["ids"] == [402, 403, 404] and v["blocks"] == ["402-404"]
    assert v["capacity"] == {"store": 212, "used": 5}  # 400, 401, 15000-15002
    codes = [x.split(":")[0] for x in v["warn"] if not x.startswith("PROFILE_SKIPPED")]  # depends on built indexes
    assert codes == ["F92"]  # 402-404: inside the stock vehicle range 400-611
    v = run_cli(["id", "free", "--kind", "vehicle", "--range", "700-710"]).json
    assert v["ids"] == [700] and any(x.startswith("ADDON_VEHICLE") for x in v["warn"])
    o = run_cli(["id", "free", "--kind", "object", "--range", "999-1010", "--count", "2"]).json
    assert o["ids"] == [999, 1002] and o["free_in_range"] == 8 and o["taken_in_range"] == 4


def test_cli_free_contiguous_range_mods_and_limits(world, run_cli, tmp_path, builders):
    r = run_cli(["id", "free", "--kind", "object", "--range", "999-1010", "--count", "3", "--contiguous"]).json
    assert r["ids"] == [1005, 1006, 1007]
    mod = tmp_path / "newmod"
    builders.w(mod, "newmod.ide", "objs\n1005, newobj, newobj, 100, 0\nend\n")
    r = run_cli(["id", "free", "--kind", "object", "--range", "1005-1010", "--mods", str(mod)]).json
    assert r["ids"] == [1006]
    r = run_cli(["id", "free", "--kind", "object", "--range", "19998-20003", "--count", "9"]).json
    assert any(w.startswith("OVER_LIMIT") for w in r["warn"]) and any(w.startswith("NOT_ENOUGH") for w in r["warn"])
    r = run_cli(["id", "free", "--kind", "ped", "--profile", "vanilla", "--count", "3"]).json
    assert r["profiles"] == ["vanilla"] and r["capacity"] == {"store": 278, "used": 1} and "warn" not in r
    r = run_cli(["id", "free", "--kind", "ped", "--profile", "installed", "--range", "20000-20002"]).json
    assert r["ids"] == [20000] and r["max_id"] == 24999 and "warn" not in r
    r = run_cli(["id", "free", "--kind", "vehicle", "--count", "2", "--no-samp", "--range", "1-10"]).json
    assert r["ids"] == [1, 4]  # 2-3 are engine-reserved; SA-MP's client skins 1-6 ignored


def test_cli_free_samp_dl_and_errors(world, run_cli, tmp_path):
    art = tmp_path / "artconfig.txt"
    art.write_text('AddSimpleModel(-1, 19379, -1000, "wall.dff", "wall.txd");\n'
                   'AddSimpleModelTimed(-1, 19379, -1001, "a.dff", "a.txd", 20, 6);\n'
                   'AddCharModel(305, 20001, "skin.dff", "skin.txd");\n', encoding="utf-8")
    r = run_cli(["id", "free", "--kind", "object", "--target", "samp-dl", "--count", "2", "--artconfig", str(art)]).json
    assert r["ids"] == [-1002, -1003] and r["blocks"] == ["-1002..-1003"]
    r = run_cli(["id", "free", "--kind", "ped", "--target", "samp-dl", "--artconfig", str(art)]).json
    assert r["ids"] == [20002]
    r = run_cli(["id", "free", "--kind", "vehicle", "--target", "samp-dl"])
    assert r.code == 1 and r.json["error"]["code"] == "UNSUPPORTED"
    r = run_cli(["id", "free"])
    assert r.code == 2 and "--kind" in r.json["error"]["msg"]
    r = run_cli(["id", "free", "--kind", "object", "--profile", "samp"])
    assert r.json["error"]["code"] == "INDEX_MISSING"


def test_no_index_at_all(satk_home, run_cli):
    r = run_cli(["id", "free", "--kind", "object"])
    assert r.code == 3 and r.json["error"]["code"] == "INDEX_MISSING"


def test_skipped_profile_warnings_ignore_duplicates(monkeypatch):
    """A skipped profile that loads like an opened one (game = installed) is not reported; samp is."""
    from types import SimpleNamespace

    from satk.idmgr import ops as O

    shapes = {"installed": ("d:/gta", ("data/default.dat", "data/gta.dat"), "engine"),
              "game": ("d:/gta", ("data/default.dat", "data/gta.dat"), "engine"),
              "samp": ("d:/gta", ("data/default.two", "data/gta.two"), "samp")}
    fake = SimpleNamespace(profile=lambda n: SimpleNamespace(root=shapes[n][0], dat=list(shapes[n][1]), img_order=shapes[n][2]))
    monkeypatch.setattr(O, "cfg", lambda: fake)
    w = O._skipped_warnings([("game", "INDEX_MISSING: x"), ("samp", "INDEX_MISSING: old schema")], ["installed"])
    assert len(w) == 1 and w[0].startswith("PROFILE_SKIPPED: samp")
