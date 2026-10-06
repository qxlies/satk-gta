"""MTA side of satk.anim: engineLoadIFP binding rules in ``anim check --loader mta`` and ``satk anim mta``.

The generated ``client.lua`` is compiled with the Lua 5.1 of the built MTA fork (``Bin/server/x64/lua5.1.dll``,
loaded read-only with ctypes; marker ``engine``, skipped without the build).
"""

from __future__ import annotations

import ctypes
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from satk.anim.check import check_ifp, mta_ids, mta_tag
from satk.anim.ifp import Anim, Ifp, Seq
from satk.anim.mta import Replace, client_lua, lua_str, meta_xml, parse_replace
from satk.anim.skeleton import ped_skeleton

K = [(0, 0, 0, 4096, 0), (0, 0, 0, 4096, 2)]


def test_mta_tag_rules():
    assert len(mta_ids()) == 64 and {0, 1, 54, 302, 5021} <= mta_ids()
    assert mta_tag(" Pelvis", -1, "ANP3") == 1 and mta_tag("R ForeArm", -1, "ANP3") == 23
    assert mta_tag("L Toe0", -1, "ANP3") == 44 and mta_tag("L Toe", -1, "ANP3") == -1   # MTA knows only ltoe0
    assert mta_tag("Normal", -1, "ANP3") == 0 and mta_tag("whatever", 22, "ANP3") == 22
    assert mta_tag("cssuitcase:Pelvis", 7, "ANPK") == 1                                 # ANPK: tag ignored, prefix cut
    assert mta_tag("fam3", -1, "ANP3") == -1


def test_check_game_vs_mta_loader():
    a = Anim("custom", [Seq(" Pelvis", -1, False, True, K),       # DFF frame name, untagged
                        Seq("Bone09", 9, False, True, K),          # an id MTA does not keep
                        Seq("L Toe", -1, False, True, K)], flags=1)
    ifp = Ifp("ANP3", "c", [a])
    game = {(f.check, f.msg.split("'")[1]) for f in check_ifp(ifp, ped_skeleton()) if f.sev == "warn"}
    assert game == {("unbound_name", " Pelvis"), ("unknown_bone", "Bone09")}
    msg = next(f.msg for f in check_ifp(ifp, ped_skeleton()) if f.check == "unbound_name")
    assert "MTA's engineLoadIFP would map it to bone 1" in msg
    mta = [(f.check, f.msg) for f in check_ifp(ifp, ped_skeleton(), loader="mta") if f.sev == "warn"]
    assert [c for c, _m in mta] == ["unknown_bone"] and "64 bone ids" in mta[0][1]
    with pytest.raises(ValueError):
        check_ifp(ifp, ped_skeleton(), loader="samp")


def test_parse_replace_and_lua_strings():
    assert parse_replace("WALK_civi") == Replace("ped", "WALK_civi", "WALK_civi")
    assert parse_replace("bar/Barserve_in=myserve") == Replace("bar", "Barserve_in", "myserve")
    assert parse_replace(" run_civi = myrun ") == Replace("ped", "run_civi", "myrun")
    for bad in ("", "=x", "ped/=x", "/walk"):
        with pytest.raises(ValueError):
            parse_replace(bad)
    assert lua_str('a"b\\c\n') == '"a\\"b\\\\c\\010"'


def test_meta_is_xml():
    root = ET.fromstring(meta_xml("satk_anim_x", "x.ifp", 'a "quoted" <desc>'))
    assert root.find("script").get("type") == "client" and root.find("file").get("src") == "x.ifp"
    assert root.find("info").get("description") == 'a "quoted" <desc>'


def test_anim_mta_op(satk_home, run_cli, ifp_files):
    r = run_cli(["anim", "mta", str(ifp_files["walk"]), "--replace", "ped/WALK_civi=walk", "run_civi=idle"])
    assert r.code == 0, r.out + r.err
    env = r.json
    d = Path(env["dir"])
    assert d == satk_home / "work" / "out" / "anim" / "mta" / "satk_anim_walk" and env["block"] == "walk"
    assert sorted(p.name for p in d.iterdir()) == ["client.lua", "meta.xml", "walk.ifp"]
    assert (d / "walk.ifp").read_bytes() == ifp_files["walk"].read_bytes()
    assert env["rows"] == [["ped", "WALK_civi", "walk", "unchecked"], ["ped", "run_civi", "idle", "unchecked"]]
    lua = (d / "client.lua").read_text(encoding="utf-8")
    assert 'engineLoadIFP(IFP_FILE, BLOCK)' in lua and '{"ped", "WALK_civi", "walk"},' in lua
    r = run_cli(["anim", "mta", str(ifp_files["walk"]), "--replace", "WALK_civi=nope"])
    assert r.json["error"]["code"] == "NOT_FOUND"
    r = run_cli(["anim", "mta", str(ifp_files["walk"]), "--block", "bad name"])
    assert r.json["error"]["code"] == "BAD_PARAMS"


@pytest.mark.engine
def test_client_lua_compiles_with_mta_lua():
    from satk.core.paths import cfg

    dll = Path(cfg().paths.engine) / "Bin" / "server" / "x64" / "lua5.1.dll"
    if not dll.is_file() or ctypes.sizeof(ctypes.c_void_p) != 8:
        pytest.skip(f"no 64-bit MTA server Lua at {dll}")
    lua = ctypes.CDLL(str(dll))
    lua.luaL_newstate.restype = ctypes.c_void_p
    lua.luaL_loadbuffer.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p]
    lua.lua_tolstring.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    lua.lua_tolstring.restype = ctypes.c_char_p
    lua.lua_close.argtypes = [ctypes.c_void_p]

    def compile_errors(src: str) -> str | None:
        state = lua.luaL_newstate()
        try:
            b = src.encode("utf-8")
            if lua.luaL_loadbuffer(state, b, len(b), b"client.lua"):
                return lua.lua_tolstring(state, -1, None).decode("utf-8", "replace")
            return None
        finally:
            lua.lua_close(state)

    assert compile_errors("local x = = 1") is not None                # the checker does catch errors
    for reps in ([], [Replace("ped", "WALK_civi", 'we"ird\\name')], [Replace("bar", "a", "b")] * 3):
        assert compile_errors(client_lua("x.ifp", "blk", reps, ["walk", "idleé"])) is None
