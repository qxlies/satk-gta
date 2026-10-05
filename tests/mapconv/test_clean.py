"""``satk map clean`` on the synthetic world of tests/mapconv/conftest.py."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

import pytest

from satk.core.errors import SatkError
from satk.mapconv.bnry import parse_bnry
from satk.mapconv.clean import HIDE_Z, Area, parse_area

BOX = "90,90,200,200"
_LUA = re.compile(r"^\s*\{(-?\d+), ([\d.]+), (-?[\d.]+), (-?[\d.]+), (-?[\d.]+), (-?\d+)\}, --", re.M)
_PWN = re.compile(r"RemoveBuildingForPlayer\(playerid, (-?\d+), (-?[\d.]+), (-?[\d.]+), (-?[\d.]+), ([\d.]+)\);")


def lua_calls(text: str) -> list[tuple]:
    return [(int(m[1]), float(m[2]), (float(m[3]), float(m[4]), float(m[5])), int(m[6])) for m in _LUA.finditer(text)]


def all_placements(db) -> list[tuple]:
    rows = db.query("SELECT lower(p.name), i.idx, i.model_id, i.x, i.y, i.z, i.area FROM inst i "
                    "JOIN ipl p ON p.id = i.ipl_id ORDER BY i.id", limit=500)["rows"]
    return [(f"inst:{r[0]}#{r[1]}", int(r[2]), (r[3], r[4], r[5]), int(r[6])) for r in rows]


def covered(calls, model, pos, area) -> bool:
    return any(m == model and a == area and math.dist(c, pos) <= r + 1e-3 for m, r, c, a in calls)


def test_parse_area(world):
    a = parse_area("200,200, 90 90")
    assert a.boxes == [(90.0, 90.0, 200.0, 200.0)] and a.label == "box_90_90_200_200"
    c = parse_area("100,-50,25")
    assert c.circle == (100.0, -50.0, 25.0) and c.contains(110, -50) and not c.contains(130, -50)
    z = parse_area("testz", world.db)
    assert z.boxes == [(90.0, 90.0, 200.0, 200.0)] and z.label == "testz"
    for bad in ("1,2", "1,1,1,5", "1,2,0", ""):
        with pytest.raises(SatkError):
            parse_area(bad, world.db)
    with pytest.raises(SatkError) as e:
        parse_area("nowhere", world.db)
    assert e.value.code == "NOT_FOUND"
    assert Area("x", boxes=[(0, 0, 1, 1), (5, 5, 6, 6)]).bbox() == (0, 0, 6, 6)


def test_clean_box_outputs(world, run_cli, satk_home):
    r = run_cli(["map", "clean", BOX, "--limit", "50"])
    assert r.code == 0, r.err
    env = r.json
    removed = {row[0]: row[3] for row in env["rows"]}
    assert removed == {"inst:t#0": "lod", "inst:t#2": "hd", "inst:t#3": "hd", "inst:t#5": "obj", "inst:t#6": "obj",
                       "inst:t_stream0#0": "hd"}
    assert (env["removed"], env["hd"], env["lods"], env["objs"], env["calls"], env["kept_shared_lods"],
            env["ipl_files"]) == (6, 3, 1, 2, 3, 1, 2)
    assert env["lod_check"] == {"files": 2, "rows": 12, "kept": 6, "hidden": 6, "hidden_lods": 0, "bad": 0}
    assert [w.split(":")[0] for w in env["warn"]] == ["LOD_SHARED"]
    base = satk_home / "work" / "out" / "mapconv" / "clean" / "box_90_90_200_200"
    # MTA Lua: every removed placement (HD and LOD) is covered, nothing that stays is
    lua = (base / "box_90_90_200_200.lua").read_text(encoding="utf-8")
    calls = lua_calls(lua)
    assert len(calls) == 3 and "removeWorldModel(r[1]" in lua and "restoreWorldModel(r[1]" in lua
    for sid, model, pos, area in all_placements(world.db):
        assert covered(calls, model, pos, area) == (sid in removed), sid
    # SA-MP Pawn: same spheres, no interior parameter
    pwn = (base / "box_90_90_200_200.pwn").read_text(encoding="utf-8")
    assert "stock RemoveArea_box_90_90_200_200(playerid)" in pwn and len(_PWN.findall(pwn)) == 3
    # modloader copies: same lines, removed rows underground, everything else byte for byte
    t = (base / "modloader" / "t.ipl").read_bytes()
    orig = world.t_ipl.split(b"\r\n")
    new = t.split(b"\r\n")
    assert len(new) == len(orig)
    changed = [i for i, (a, b) in enumerate(zip(orig, new)) if a != b]
    assert changed == [2, 4, 5, 7, 8]  # t#0, t#2, t#3, t#5, t#6 (line 1 is the comment)
    assert new[5] == b"1000, box, 0, 150, 100, -1000, 0, 0, -0.7071068, 0.7071068, 1  # turned"
    assert new[7] == b"1002 tree 0 110 110 -1000 0 0 0 1 -1"
    s = parse_bnry((base / "modloader" / "t_stream0.ipl").read_bytes())
    assert [r[2] for r in s.insts] == [HIDE_Z, 5.0] and [r[9] for r in s.insts] == [0, 9]
    assert len((base / "modloader" / "t_stream0.ipl").read_bytes()) == len(world.stream)
    rep = json.loads((base / "report.json").read_text(encoding="utf-8"))
    assert rep["lod_check"]["bad"] == 0 and rep["kept_shared_lods"] == ["inst:t#1"]
    assert rep["ipl_files"] == {"t": {"file": "t.ipl", "hidden": 5}, "t_stream0": {"file": "t_stream0.ipl", "hidden": 1}}


def test_clean_options(world, run_cli, satk_home):
    env = run_cli(["map", "clean", BOX, "--shared-lods", "--dry-run", "--name", "s"]).json
    assert env["removed"] == 7 and env["kept_shared_lods"] == 0 and env["dry_run"] is True and "files" not in env
    assert env["lod_check"]["hidden_lods"] == 1 and any(w.startswith("LOD_HIDDEN") for w in env["warn"])
    assert not (satk_home / "work" / "out" / "mapconv" / "clean" / "s").exists()
    env = run_cli(["map", "clean", "testzone", "--models", "tr*", "--to", "lua"]).json
    assert {r[0] for r in env["rows"]} == {"inst:t#5", "inst:t#6"} and env["calls"] == 1
    assert set(env["files"]) == {"lua", "report"} and "lod_check" not in env
    env = run_cli(["map", "clean", BOX, "--interior", "-1", "--models", "1002", "--dry-run"]).json
    assert {r[0] for r in env["rows"]} == {"inst:t#5", "inst:t#6", "inst:t#8"}
    env = run_cli(["map", "clean", BOX, "--interior", "-1", "--models", "1002", "--z", "0", "100", "--dry-run"]).json
    assert {r[0] for r in env["rows"]} == {"inst:t#5", "inst:t#6"}
    env = run_cli(["map", "clean", "112,110,0.5", "--no-merge", "--dry-run"]).json
    assert [r[0] for r in env["rows"]] == ["inst:t#6"] and env["calls"] == 1
    env = run_cli(["map", "clean", "1000,1000,1100,1100"]).json
    assert env["removed"] == 0 and any(w.startswith("EMPTY") for w in env["warn"]) and "files" not in env


def test_single_calls_shrink_near_twins(world, run_cli, satk_home):
    """Without merging, a tree 2 m from another tree that stays gets a radius below 1 m."""
    env = run_cli(["map", "clean", "110,110,0.5", "--to", "lua", "--name", "one"]).json
    lua = (satk_home / "work" / "out" / "mapconv" / "clean" / "one" / "one.lua").read_text(encoding="utf-8")
    calls = lua_calls(lua)
    assert env["removed"] == 1 and calls == [(1002, 0.25, (110.0, 110.0, 5.0), 0)]


def test_clean_errors(world, run_cli):
    assert run_cli(["map", "clean", BOX, "--to", "dff"]).json["error"]["code"] == "BAD_PARAMS"
    assert run_cli(["map", "clean", BOX, "--z", "1"]).json["error"]["code"] == "BAD_PARAMS"
    assert run_cli(["map", "clean", BOX, "--models", "nosuch*"]).json["error"]["code"] == "NOT_FOUND"
    assert run_cli(["map", "clean", "nowhere"]).json["error"]["code"] == "NOT_FOUND"


def test_stale_index_is_reported(world, run_cli):
    from satk.mapconv.clean import hide_in_bnry, hide_in_text

    with pytest.raises(SatkError) as e:
        hide_in_text(b"inst\n1, a, 0, 1, 2, 3, 0, 0, 0, 1\nend\n", {3})
    assert e.value.code == "REVISION"
    with pytest.raises(SatkError):
        hide_in_bnry(world.stream, {5})
    data, n = hide_in_text(b"inst\n1, a, 0, 1, 2, 3, 0, 0, 0, 1 # c 9\nbad line\n2 b 0 1 2 3 0 0 0 1\nend\n", {1})
    assert n == 1 and data.endswith(b"2 b 0 1 2 -1000 0 0 0 1\nend\n")
