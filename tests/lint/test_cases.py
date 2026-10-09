"""Every lint rule is caught on a synthetic bad file (M2-06 acceptance), and the clean mod lints clean."""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from satk.lint import runner
from satk.lint.link import IndexView
from satk.lint.rules import Rules, rules_path
from satk.lint.runner import lint

from lint_synth import B, Mod, libid

NAN = float("nan")
CARS = "cars\n400, gm_car, gm_txd, car, PREMIER, PREMIER, null, richfamily, 10, 0, 0, -1, 0.7, 0.7, -1\nend\n"
PEDS = "peds\n290, gm_ped, gm_txd, CIVMALE, STAT_STREET_GUY, man, 1983, 0, null, 9, 9, PED_TYPE_GEN, VOICE_GEN_BMOST, " \
       "VOICE_GEN_BMOST\nend\n"
WEAP = "weap\n346, gm_gun, gm_txd, colt45, 1, 50, 0\nend\n"
FLAT = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (1.0, 1.0, 0.0)]


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


def _second_extension(dff: bytes) -> bytes:
    """The clump with a second clump-level Extension holding the collision (the old embed_col bug)."""
    t, size, lib = struct.unpack_from("<III", dff, 0)
    body = dff[12:12 + size] + B.chunk(0x03, B.chunk(0x253F2FA, B.col3("gm_box", boxes=B.BOX)))
    return struct.pack("<III", t, len(body), lib) + body


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
    elif rule == "dff.clump_ext_dup":
        f["gm_box.dff"] = _second_extension(B.dff(night=True))
    elif rule == "dff.tris_budget":
        kw["config"] = cfg({rule: {"params": {"budget": {"map": 1}}}})
    elif rule == "dff.materials_budget":
        kw["config"] = cfg({rule: {"params": {"budget": {"map": 0}}}})
    elif rule == "dff.rw_version":
        f["gm_box.dff"] = B.dff(lib=libid(0x37002), night=True)
    elif rule == "dff.flat_shading":
        m.ide += CARS
        f["gm_car.dff"] = B.dff(pos=FLAT, normals=True)
    elif rule == "dff.vert_sharing":
        m.ide += CARS
        kw["config"] = cfg({rule: {"params": {"min_tris": 1}}})
        f["gm_car.dff"] = B.dff(pos=FLAT[:3] + [FLAT[2], FLAT[1], FLAT[3]], tris=[(0, 1, 2, 0), (3, 4, 5, 0)],
                                normals=True)
    elif rule == "veh.frames":
        m.ide += CARS
        f["gm_car.dff"] = B.dff(frames=((-1, "gm_car"),), normals=True)
    elif rule == "veh.dummy_side":
        m.ide += CARS
        f["gm_car.dff"] = B.dff(frames=((-1, "gm_car"), (0, "wheel_lf_dummy", (1.0, 1.2, 0.0))), normals=True)
    elif rule == "veh.wheel_scale":
        m.ide += CARS
        f["gm_car.dff"] = B.dff(frames=((-1, "gm_car"), (0, "wheel")), atomics=((1, 0),), normals=True,
                                pos=[(0.0, -1.0, -1.0), (0.0, 1.0, -1.0), (0.0, -1.0, 1.0), (0.0, 1.0, 1.0)])
    elif rule == "veh.shadow_mesh":
        m.ide += CARS
        f["gm_car.dff"] = B.dff(normals=True)
    elif rule == "veh.env_uv2":
        m.ide += CARS
        f["gm_car.dff"] = B.dff(mats=[B.material("gm_wall", env="xvehicleenv128")], normals=True)
    elif rule == "veh.light_key_tex":
        m.ide += CARS
        f["gm_car.dff"] = B.dff(mats=[B.material("gm_wall", rgba=(255, 175, 0, 255))], normals=True)
    elif rule == "veh.paint_dirt":
        m.ide += CARS
        f["gm_car.dff"] = B.dff(mats=[B.material("vehiclegeneric256", rgba=(60, 255, 0, 255))], normals=True)
    elif rule == "veh.upgrade_frames":
        m.ide += CARS
        f["gm_car.dff"] = B.dff(frames=((-1, "gm_car"),), normals=True)
        kw["_ref_frames"] = {"gm_car": frozenset({"gm_car", "ug_nitro", "ug_roof"})}
    elif rule == "veh.hd_tris":
        m.ide += CARS
        kw["config"] = cfg({rule: {"params": {"budget": {"default": 1}}}})
        f["gm_car.dff"] = B.dff(normals=True)
    elif rule == "veh.part_tris":
        m.ide += CARS
        kw["config"] = cfg({rule: {"params": {"budget": {"chassis": 1}}}})
        f["gm_car.dff"] = B.dff(frames=((-1, "gm_car"), (0, "chassis")), atomics=((1, 0),), normals=True)
    elif rule == "veh.dam_ratio":
        m.ide += CARS
        kw["config"] = cfg({rule: {"params": {"min_tris": 1}}})
        four = [(0, 1, 2, 0), (2, 1, 3, 0), (0, 2, 1, 0), (1, 2, 3, 0)]
        geoms = [B.geometry(B.QUAD, four, normals=True), B.geometry(B.QUAD, four[:1], normals=True),
                 B.geometry(B.QUAD, four, normals=True), B.geometry(B.QUAD, four[:1], normals=True)]
        f["gm_car.dff"] = B.clump(geoms, frames=((-1, "gm_car"), (0, "door_lf_ok"), (0, "door_lf_dam"),
                                                 (0, "bump_front_ok"), (0, "bump_front_dam")),
                                  atomics=((1, 0), (2, 1), (3, 2), (4, 3)))
    elif rule == "ped.skin":
        m.ide += PEDS
        f["gm_ped.dff"] = B.dff(normals=True)
    elif rule == "weap.flash":
        m.ide += WEAP
        f["gm_gun.dff"] = B.dff(frames=((-1, "gm_gun"), (0, "gunflash")), normals=True)
    elif rule == "mat.alpha_draw_last":
        kw["config"] = cfg({rule: {"enabled": True}})
        f["gm_box.dff"] = B.dff(mats=[B.material("gm_wall", rgba=(255, 255, 255, 128))], night=True)
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
    elif rule == "col.face_light_zero":
        f["gm_box.col"] = B.col3("gm_box", boxes=B.BOX, verts=B.TRI_VERTS, faces=((0, 1, 2, 0, 0),))
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
    elif rule == "ide.draw_bigbuilding":
        m.ide = "objs\n18000, gm_box, gm_txd, 400, 0\nend\n"
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


class _RefIndex(IndexView):
    """An index that only answers reference frame names (rules comparing a replacement with vanilla)."""

    def __init__(self, frames: dict):
        super().__init__(None)
        self.frames = frames

    @property
    def available(self) -> bool:
        return True

    def ref_frames(self, dff: str):
        return self.frames.get(dff.lower())

    def model(self, name):
        return None

    def has_blob(self, kind, name):
        return None

    def txd_users(self, name, limit=50):
        return []


@pytest.mark.parametrize("rule", ALL_RULES)
def test_rule_catches_its_synthetic_bad_file(rule: str, mod: Mod, tmp_path: Path, config_file, monkeypatch):
    target, kw = _case(mod, tmp_path, rule, config_file)
    path = mod.write()
    ref = kw.pop("_ref_frames", None)
    if ref is not None:
        monkeypatch.setattr(runner, "_open_index", lambda profile, use: (_RefIndex(ref), None))
    rep = lint(target or str(path), use_index=False, **kw)
    hits = [f for f in rep.findings if f.rule == rule]
    assert hits, (rule, [f.row() for f in rep.findings])
    sev = Rules.load(config=kw.get("config"))[rule].sev
    assert {f.sev for f in hits} == {sev}
    assert all("{" not in f.msg for f in hits), hits     # every template field was filled


def test_one_clump_extension_is_clean_and_the_collision_is_named(mod: Mod):
    """A normal DFF (one Extension per clump) is quiet; the broken one names where the collision went."""
    mod.files["gm_box.dff"] = B.dff(night=True)
    rep = lint(str(mod.write()), use_index=False, only=["dff.clump_ext_dup"])
    assert rep.findings == [] and rep.checked["dff.clump_ext_dup"] == 1
    mod.files["gm_box.dff"] = _second_extension(B.dff(night=True))
    rep = lint(str(mod.write()), use_index=False, only=["dff.clump_ext_dup"])
    assert [f.sev for f in rep.findings] == ["error"] and "collision in Extension 2" in rep.findings[0].msg


def test_game_root_lints_only_what_the_game_loads(tmp_path: Path):
    g = _game_root(tmp_path, B.col3("big", boxes=B.BOX))
    rep = lint(str(g), use_index=False)
    assert rep.skipped >= 1                                 # unused.col: no COLFILE names it
    assert not any("unused.col" in f.file for f in rep.findings)
    assert rep.summary["fatal"] == 0
