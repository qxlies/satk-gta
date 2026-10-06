"""Who uses a TXD: DFF string scan, keep patterns, verdicts with and without an index (no game files)."""

from __future__ import annotations

import struct

from satk.txdopt.inputs import load_bundle
from satk.txdopt.usage import Usage, dff_strings, keep_matcher


def test_dff_strings_cover_materials_suffixes_and_breakable_names(synth):
    names = dff_strings(synth.dff(["Wall_01", "roof"]))
    assert {"wall_01", "roof", "all_01", "oof"} <= names
    # a 2dEffect-like field preceded by printable flag bytes still yields the name as a suffix
    blob = b"\x01\x02AB" + b"coronastar\0" + b"\x00" * 8
    assert "coronastar" in dff_strings(blob)
    # Breakable plugin: char[32] texture names after u16 material indices
    brk = struct.pack("<3H", 0, 1, 0) + b"glass_a".ljust(32, b"\0") + b"glass_b".ljust(32, b"\0")
    assert {"glass_a", "glass_b"} <= dff_strings(synth.dff(["x"], extra_ext=synth.chunk(0x253F2FD, brk)))


def test_keep_matcher():
    k = keep_matcher(["sign_*", "LOGO"])
    assert k("#emap") and k("remap_body") and k("Sign_01") and k("logo") and not k("wall")


def test_verdicts_from_the_bundle_only(synth, tmp_path, satk_home):
    d = synth.make_mod(tmp_path / "m")
    (d / "extra.txd").write_bytes(synth.txd([synth.native("x", synth.smooth(4, 4), "DXT1")]))
    (d / "child.ide").write_text("objs\n2100, kid, kidtxd, 100, 0\nend\ntxdp\nkidtxd, smalltxd\nend\n"
                                 "weap\n2101, gun, guntxd, null, 1, 50, 0\nend\n", encoding="latin-1")
    (d / "kid.dff").write_bytes(synth.dff(["kidonly", "unused2"]))
    with load_bundle(str(d)) as b:
        u = Usage(b, "vanilla")
        assert any(w.startswith("INDEX_MISSING") for w in u.warn)
        v = u.verdict("bigtxd")
        assert v.models == ["big1", "big2"] and {"wall", "floor", "roof"} <= v.names and "unused1" not in v.names
        v = u.verdict("smalltxd")                      # the txdp child kid falls back to smalltxd
        assert v.models == ["small1", "kid"] and "unused2" in v.names
        assert u.verdict("extra").names is None and "no model loads it" in u.verdict("extra").why
        assert u.verdict("guntxd").names is None and "weap" in u.verdict("guntxd").why
        (d / "nodff.ide").write_text("objs\n2200, ghost, ghosttxd, 100, 0\nend\n", encoding="latin-1")
        u.close()
    with load_bundle(str(d)) as b:
        u = Usage(b, "vanilla")
        v = u.verdict("ghosttxd")
        assert v.names is None and "no DFF found for user model 'ghost'" in v.why
        assert u.parent_of("kidtxd") == "smalltxd" and u.vehicle_parent("vehicle")
        u.close()


def test_verdicts_from_the_index(ws):
    with load_bundle("txd:boxes") as b:
        u = Usage(b, "vanilla")
        v = u.verdict("boxes")
        assert v.models == ["box1", "box2"] and "boxtex" in v.names and "oldtex" not in v.names
        assert u.verdict("testcar").names is not None and u.verdict("vehicle").names is None
        assert u.txd_exists("boxes") and not u.txd_exists("nothing_here")
        u.close()
