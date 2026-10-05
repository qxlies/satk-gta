"""Every lint rule is caught on a synthetic bad file (M2-06 acceptance), and the clean mod lints clean."""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from satk.lint.rules import Rules, rules_path
from satk.lint.runner import lint

from lint_synth import B, Mod, libid

NAN = float("nan")


def _game_root(root: Path, colfile: bytes) -> Path:
    """A minimal game root whose gta.dat loads models/big.col through COLFILE."""
    g = root / "game"
    (g / "data").mkdir(parents=True)
    (g / "models").mkdir()
    (g / "data" / "default.dat").write_text("IDE DATA\\DEFAULT.IDE\n", encoding="latin-1")
    (g / "data" / "gta.dat").write_text("COLFILE 0 MODELS\\BIG.COL\n", encoding="latin-1")
    (g / "data" / "default.ide").write_text("objs\n18000, big, gm_txd, 100, 0\nend\n", encoding="latin-1")
    (g / "models" / "big.col").write_bytes(colfile)
    (g / "models" / "unused.col").write_bytes(b"garbage, never loaded")
    return g


def _patch(data: bytes, off: int, fmt: str, *vals) -> bytes:
    buf = bytearray(data)
    struct.pack_into(fmt, buf, off, *vals)
    return bytes(buf)


# rule id -> function(mod, tmp_path) -> (target, lint kwargs); the mod is written afterwards
def _case(m: Mod, tmp: Path, rule: str, cfg) -> tuple[str | None, dict]:  # noqa: C901 - one branch per rule
    f = m.files
    kw: dict = {}
    if rule == "img.parse":
        f["bad.img"] = b"XXXX" + bytes(64)
    elif rule == "file.name_len":
        f["a_name_longer_than_xx.dff"] = B.dff()
    elif rule == "dff.parse":
        f["broken.dff"] = struct.pack("<III", 0x10, 400, 0x1803FFFF) + bytes(20)
    elif rule == "dff.native":
        f["gm_box.dff"] = B.dff(extra_flags=0x01000000, night=True)
    elif rule == "dff.verts_max":
        n = 65536
        f["gm_box.dff"] = B.dff(pos=[(float(i % 7), 0.0, 0.0) for i in range(n)], tris=[(0, 1, 2, 0)],
                                prelit=False, uv_sets=0, mats=[B.material()])
    elif rule == "dff.frame_name_len":
        f["gm_box.dff"] = B.dff(frames=((-1, "a_frame_name_of_thirty_chars"),), night=True)
    elif rule == "dff.no_atomics":
        f["gm_box.dff"] = B.dff(atomics=(), night=True)
    elif rule == "dff.embedded_col":
        f["gm_box.dff"] = B.dff(col=bytes(64), night=True)
    elif rule == "dff.empty_geometry":
        f["gm_box.dff"] = B.dff(tris=[], night=True)
    elif rule == "dff.uv_missing":
        f["gm_box.dff"] = B.dff(uv_sets=0, night=True)
    elif rule == "dff.uv_sets":
        f["gm_box.dff"] = B.dff(uv_sets=3, night=True)
    elif rule == "dff.tex_name_len":
        f["gm_box.dff"] = B.dff(mats=[B.material("t" * 40)], night=True)
    elif rule == "dff.prelight_missing":
        f["gm_box.dff"] = B.dff(prelit=False)
    elif rule == "dff.night_missing":
        f["gm_box.dff"] = B.dff()
    elif rule == "dff.prelight_black":
        f["gm_box.dff"] = B.dff(prelit_rgba=(0, 0, 0, 255), night=True)
    elif rule == "dff.normals_missing":
        lone = tmp / "lone"
        lone.mkdir()
        (lone / "ped.dff").write_bytes(B.dff(skin=True, prelit=False))
        return str(lone / "ped.dff"), kw
    elif rule == "dff.bad_vertex":
        f["gm_box.dff"] = B.dff(pos=[(NAN, 0.0, 0.0)] + B.QUAD[1:], night=True)
    elif rule == "dff.uv_range":
        f["gm_box.dff"] = B.dff(uvs=[(0.0, 0.0), (1000.0, 0.0), (0.0, 1.0), (1.0, 1.0)], night=True)
    elif rule == "dff.bounds_size":
        f["gm_box.dff"] = B.dff(pos=B.QUAD[:3] + [(9000.0, 0.0, 0.0)], night=True)
    elif rule == "dff.bsphere":
        f["gm_box.dff"] = B.dff(pos=B.QUAD[:3] + [(3.0, 3.0, 3.0)], night=True)
    elif rule == "dff.tris_budget":
        kw["config"] = cfg({rule: {"params": {"budget": {"map": 1}}}})
    elif rule == "dff.materials_budget":
        kw["config"] = cfg({rule: {"params": {"budget": {"map": 0}}}})
    elif rule == "dff.rw_version":
        f["gm_box.dff"] = B.dff(lib=libid(0x37002), night=True)
    elif rule == "txd.parse":
        f["gm_txd.txd"] = struct.pack("<III", 0x16, 999, 0x1803FFFF)
    elif rule == "txd.platform":
        f["gm_txd.txd"] = B.txd([B.native("gm_wall", platform=6)])
    elif rule == "txd.format":
        f["gm_txd.txd"] = B.txd([B.native("gm_wall", fmt="DXT2", w=4, h=4, levels=[bytes(16)])])
    elif rule == "txd.name_empty":
        f["gm_txd.txd"] = B.txd([B.native(""), B.native("gm_wall")])
    elif rule == "txd.name_len":
        f["gm_txd.txd"] = B.txd([B.native("x" * 32), B.native("gm_wall")])
    elif rule == "txd.dup_name":
        f["gm_txd.txd"] = B.txd([B.native("gm_wall"), B.native("GM_WALL")])
    elif rule == "txd.count":
        f["gm_txd.txd"] = B.txd([B.native("gm_wall")], count=5)
    elif rule == "txd.empty":
        f["other.txd"] = B.txd([])
    elif rule == "txd.pow2":
        f["gm_txd.txd"] = B.txd([B.native("gm_wall", fmt=21, raster=0x500, w=48, h=48, depth=32,
                                          levels=[bytes(48 * 48 * 4)])])
    elif rule == "txd.size_max":
        kw["config"] = cfg({rule: {"params": {"max": 32}}})
    elif rule == "txd.dxt_block":
        f["gm_txd.txd"] = B.txd([B.native("gm_wall", w=6, h=4, levels=[bytes(16)])])
    elif rule == "txd.mip_levels":
        f["gm_txd.txd"] = B.txd([B.native("gm_wall", w=4, h=4, levels=[bytes(8)] * 10)])
    elif rule == "txd.mip_size":
        f["gm_txd.txd"] = B.txd([B.native("gm_wall", w=64, h=64, levels=[bytes(100)])])
    elif rule == "txd.mips_missing":
        f["gm_txd.txd"] = B.txd([B.native("gm_wall", w=64, h=64, levels=[bytes(2048)])])
    elif rule == "txd.alpha_off":
        f["gm_txd.txd"] = B.txd([B.native("gm_wall", fmt="DXT3", w=4, h=4, levels=[bytes(16)])])
    elif rule == "txd.total_size":
        kw["config"] = cfg({rule: {"params": {"max_mb": 0.0001}}})
    elif rule == "col.parse":
        f["gm_box.col"] = b"NOPE" + bytes(60)
    elif rule == "col.body":
        good = f["gm_box.col"]
        f["gm_box.col"] = _patch(good, 32 + 40, "<H", 500)        # 500 spheres claimed, none stored
    elif rule == "col.name_empty":
        f["extra.col"] = B.col3("", boxes=B.BOX)
    elif rule == "col.name_len":
        f["extra.col"] = B.col3("c" * 22, boxes=B.BOX)
    elif rule == "col.vertex_range":
        f["gm_box.col"] = B.col_v1("gm_box", verts=((300.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
                                   faces=((0, 1, 2, 0),), bounds=(400.0, (0.0, 0.0, 0.0), (-1, -1, -1), (300, 1, 1)))
    elif rule == "col.empty_flag":
        f["gm_box.col"] = B.col3("gm_box", boxes=B.BOX, flags=0)
    elif rule == "col.empty":
        f["gm_box.col"] = B.col3("gm_box")
    elif rule == "col.box_inverted":
        f["gm_box.col"] = B.col3("gm_box", boxes=(((1.0, -1.0, -1.0), (-1.0, 1.0, 1.0), 0),))
    elif rule == "col.sphere_radius":
        f["gm_box.col"] = B.col3("gm_box", spheres=((0.0, 0.0, 0.0, 0.0, 0),))
    elif rule == "col.nan":
        f["gm_box.col"] = B.col3("gm_box", boxes=(((NAN, -1.0, -1.0), (1.0, 1.0, 1.0), 0),))
    elif rule == "col.outside_bounds":
        f["gm_box.col"] = B.col3("gm_box", spheres=((1.5, 0.0, 0.0, 1.5, 0),),
                                 bounds=((-2.0, -2.0, -2.0), (2.0, 2.0, 2.0), (0.0, 0.0, 0.0), 5.0))
    elif rule == "col.bsphere":
        f["gm_box.col"] = B.col3("gm_box", boxes=B.BOX, bounds=((-2.0, -2.0, -2.0), (2.0, 2.0, 2.0), (0, 0, 0), 0.5))
    elif rule == "col.surface":
        f["gm_box.col"] = B.col3("gm_box", verts=B.TRI_VERTS, faces=((0, 1, 2, 200),))
    elif rule == "col.dup_name":
        f["again.col"] = B.col3("gm_box", boxes=B.BOX)
    elif rule == "col.colfile_buffer":
        big = B.col3("big", verts=B.TRI_VERTS, faces=[(0, 1, 2, 0)] * 4200)
        return str(_game_root(tmp, big)), kw
    elif rule == "ide.parse":
        m.ide += "objs\nabc, broken\nend\n"
    elif rule == "ide.name_len":
        m.ide = "objs\n18000, gm_box_with_a_long_name_x, gm_txd, 100, 0\nend\n"
    elif rule == "ide.txd_len":
        m.ide = "objs\n18000, gm_box, gm_txd_with_a_long_name_x, 100, 0\nend\n"
    elif rule == "ide.id_range":
        m.ide = "objs\n20000, gm_box, gm_txd, 100, 0\nend\n"
    elif rule == "ide.dup_id":
        m.ide += "objs\n18000, gm_other, gm_txd, 100, 0\nend\n"
    elif rule == "ide.dup_name":
        m.ide += "objs\n18001, gm_box, gm_txd, 100, 0\nend\n"
    elif rule == "ide.draw_min":
        m.ide = "objs\n18000, gm_box, gm_txd, 2, 0\nend\n"
    elif rule == "link.dff_missing":
        m.ide += "objs\n18001, gm_gone, gm_txd, 100, 0\nend\n"
    elif rule == "link.txd_missing":
        m.ide = "objs\n18000, gm_box, gm_none, 100, 0\nend\n"
    elif rule == "link.texture_missing":
        f["gm_box.dff"] = B.dff(mats=[B.material("gm_wall"), B.material("gm_lost")], night=True)
    elif rule == "link.col_missing":
        del f["gm_box.col"]
    elif rule == "link.col_orphan":
        f["extra.col"] = B.col3("gm_nobody", boxes=B.BOX)
    elif rule == "link.col_offset":
        f["gm_box.col"] = B.col3("gm_box", boxes=(((99.0, 99.0, 0.0), (101.0, 101.0, 1.0), 0),),
                                 bounds=((99.0, 99.0, 0.0), (101.0, 101.0, 1.0), (100.0, 100.0, 0.5), 2.0))
    elif rule == "link.dff_orphan":
        f["nobody.dff"] = B.dff(night=True)
    elif rule == "link.txd_orphan":
        f["nobody.txd"] = B.txd([B.native("x", w=64, h=64, levels=B.dxt1_chain(64, 64))])
    else:
        raise KeyError(rule)
    return None, kw


ALL_RULES = sorted(json.loads(rules_path().read_text(encoding="utf-8"))["rules"])


def test_the_clean_mod_has_no_findings(mod: Mod):
    rep = lint(str(mod.write()), use_index=False)
    assert rep.findings == [] and rep.files == 4
    assert lint(str(mod.dir), use_index=False, preset="strict").findings == []


@pytest.mark.parametrize("rule", ALL_RULES)
def test_rule_catches_its_synthetic_bad_file(rule: str, mod: Mod, tmp_path: Path, config_file):
    target, kw = _case(mod, tmp_path, rule, config_file)
    path = mod.write()
    rep = lint(target or str(path), use_index=False, **kw)
    hits = [f for f in rep.findings if f.rule == rule]
    assert hits, (rule, [f.row() for f in rep.findings])
    sev = Rules.load(config=kw.get("config"))[rule].sev
    assert {f.sev for f in hits} == {sev}
    assert all("{" not in f.msg for f in hits), hits     # every template field was filled


def test_game_root_lints_only_what_the_game_loads(tmp_path: Path):
    g = _game_root(tmp_path, B.col3("big", boxes=B.BOX))
    rep = lint(str(g), use_index=False)
    assert rep.skipped >= 1                                 # unused.col: no COLFILE names it
    assert not any("unused.col" in f.file for f in rep.findings)
    assert rep.summary["fatal"] == 0
