"""Golden checks against the real game files (READ-ONLY; SPEC Appendix A, V8, V9, V13).

Marked ``game``: skipped when the copy is missing (``SATK_TEST_NO_SKIP=1`` turns that into a failure).
"""

from __future__ import annotations

import pytest

from satk.core.paths import open_ro
from satk.formats import selftest
from satk.formats.img import ImgArchive
from satk.formats.ipl import parse_ipl_binary, parse_ipl_text, rz_deg
from satk.formats.dat import read_text, resolve_ci
from satk.formats.layout import archives
from satk.formats.txd import parse_txd, texture_hash
from satk.formats.col import iter_col
from satk.formats.dff import decode_geometries, decode_geometry, find_embedded_col, scan_dff
from satk.formats.ifp import parse_ifp
from satk.formats.zon import parse_zon

pytestmark = pytest.mark.game


def test_selftest_vanilla_golden(vanilla_full):
    """Acceptance 2: every A.1 counter (plus F2 DFF/COL/IFP/texref) matches; <= 10 s without the geometry/S2 extras."""
    r = vanilla_full
    assert r["failed"] == [] and r["ok"], r["failed"]
    assert "skipped" not in r
    assert r["checked"] >= 99 + len(selftest.GEOMETRY_GOLDEN)   # A.1 + F2 + full geometry (+ S2 parity)
    tm = r["timings"]
    # Acceptance 2 (<= 10 s, warm cache) is measured with the CLI; ~4.5 s on an idle machine. Other agents
    # can load the CPU 2x, so this wall-clock bound only guards against a gross regression.
    assert tm["total"] - tm["geometry"] - tm["dxt"] < 20, tm
    c = r["counts"]
    assert (c["dff.files"], c["dff.clumps"], c["dff.atomics"], c["dff.rw_0x35000"]) == (15356, 15660, 19556, 16)
    assert (c["geo.geometries"], c["geo.verts"], c["geo.tris"], c["geo.strip"]) == (18181, 7897315, 5634843, 17843)
    assert (c["col.files"], c["col.models"], c["ifp.files"], c["ifp.anims"]) == (254, 10169, 436, 4484)
    assert (c["texref.unresolved"], c["texref.resolved"], c["zon.zones"]) == (462, 59592, 384)


def test_selftest_installed_golden(installed_root):
    """Acceptance 3 (A.2): the original install differs from A.1 only in TXDs/loose files, so the stages that
    read the identical DFF/COL/IFP data (covered by the vanilla run) are skipped here; the full run is the
    CLI acceptance command."""
    r = selftest.run(installed_root, "installed", skip=("dff", "col", "ifp"))
    assert r["failed"] == [] and r["ok"], r["failed"]
    assert r["skipped"] == ["col", "dff", "ifp", "texref"]
    skipped = tuple(p for st in r["skipped"] for p in selftest.STAGES[st])
    assert r["checked"] == sum(1 for k in selftest.GOLDEN["installed"] if not k.startswith(skipped)) == 69
    c = r["counts"]
    assert (c["txd.files"], c["tex.total"], c["tex.fmt.DXT5"], c["blobs"]) == (4049, 32942, 64, 21150)
    assert (c["tex.src.loose"], c["tex.unique_names"], c["tex.unique_hashes"]) == (776, 13784, 14624)


def test_samp_archive_order_on_original(installed_root):
    """Acceptance 8 (V8): SA-MP registers the .two IMG lines in order, InitImageList is patched out."""
    specs = archives(installed_root, ["data/default.two", "data/gta.two"], "samp")
    assert [a.relpath for a in specs if a.ns == "main"] == [
        "samp/custom.img", "samp/samp.img", "models/gta3.img", "models/gta_int.img", "samp/sampcol.img",
        "data/script/script.img", "models/cutscene.img"]


def test_engine_archive_order_vanilla(clean_root):
    specs = archives(clean_root, ["data/default.dat", "data/gta.dat"], "engine")
    assert [(a.relpath, a.ns) for a in specs] == [
        ("models/gta3.img", "main"), ("models/gta_int.img", "main"), ("data/paths/carrec.img", "main"),
        ("data/script/script.img", "main"), ("models/cutscene.img", "main"), ("models/player.img", "player"),
        ("anim/anim.img", "anim"), ("anim/cuts.img", "cuts")]


def test_lae2_stream0_inst4_links_to_lod(clean_root):
    """V13: inst:lae2_stream0#4 -> model 17613 (lae2_roads89) @ (2489.30, -1668.50, 12.30), LOD inst:lae2#198."""
    with ImgArchive.open(clean_root / "models" / "gta3.img") as a:
        insts, _cars = parse_ipl_binary(a.read(a.find("lae2_stream0.ipl")))
    i = insts[4]
    assert i.model_id == 17613 and i.lod == 198
    assert [round(v, 2) for v in i.pos] == [2489.3, -1668.5, 12.3]
    text, _items = parse_ipl_text(read_text(resolve_ci(clean_root, "DATA\\MAPS\\LA\\LAe2.IPL")))
    assert text[198].name.lower() == "lodlae2_roads89" and text[198].model_id != 17613
    assert rz_deg(i.q, i.interior) is not None


def test_shadowed_txd_differs_between_archives(clean_root):
    """Pitfall #2: lawest1.txd has 15 textures in gta3.img and 1 in gta_int.img (first archive wins)."""
    counts = []
    for rel in ("gta3.img", "gta_int.img"):
        with ImgArchive.open(clean_root / "models" / rel) as a:
            counts.append(len(parse_txd(a.read(a.find("lawest1.txd"))).textures))
    assert counts == [15, 1]


def test_outro_pal8_and_hash_stable(clean_root):
    p = resolve_ci(clean_root, "models/txd/outro.txd")
    with open_ro(p) as f:
        data = f.read()
    t = parse_txd(data)
    (x,) = [x for x in t.textures if x.d3dfmt == "PAL8"]
    assert t.rw_version == 0x34003 and x.platform == 8 and x.pal_size == 1024 and (x.w, x.h) == (512, 512)
    assert texture_hash(data, x) == texture_hash(data, x)


def _blob(root, img: str, name: str) -> bytes:
    with ImgArchive.open(root / "models" / img) as a:
        return a.read(a.find(name))


def test_geometry_examples(clean_root):
    """A.1: infernus.dff 3 072 triangles; lae2_roads89.dff 232 vertices, 225 triangles (scan == decode)."""
    inf = _blob(clean_root, "gta3.img", "infernus.dff")
    i = scan_dff(inf)
    assert (i.clumps, i.atomics, len(i.frames), len(i.materials), i.tris) == (1, 15, 36, 64, 3072)
    assert sum(len(m.tris) // 3 for m in decode_geometries(inf)) == 3072
    assert sum(1 for m in i.materials if m.color_slot == 1) > 0           # recolourable body paint
    off, size = find_embedded_col(inf)
    (cm,) = iter_col(inf[off:off + size])
    assert (cm.version, cm.name) == (3, "infernus_col")
    road = _blob(clean_root, "gta3.img", "lae2_roads89.dff")
    r = scan_dff(road)
    assert (r.verts, r.tris, len(r.geoms)) == (232, 225, 1)
    m = decode_geometry(road, r.geoms[0].geom_off)
    assert (len(m.positions) // 3, len(m.tris) // 3) == (232, 225)
    assert m.night is not None and m.prelit is not None and m.normals is None
    assert sorted({x.texture for x in r.materials}) == ["dt_road_stoplinea", "plaintarmac1", "sidewgrass1",
                                                        "sidewgrass2", "sidewgrass3"]


def test_player_img_three_clumps(clean_root):
    with ImgArchive.open(clean_root / "models" / "player.img") as a:
        e = next(e for e in a.entries if e.ext == "dff")
        i = scan_dff(a.read(e))
    assert i.clumps == 3


def test_ifp_and_zones(clean_root):
    with open_ro(resolve_ci(clean_root, "anim/ped.ifp")) as f:
        fmt, pack, anims = parse_ifp(f.read())
    assert (fmt, pack, len(anims)) == ("ANP3", "ped", 294)
    with ImgArchive.open(clean_root / "anim" / "cuts.img") as a:
        assert parse_ifp(a.read(a.entries[0]))[0] == "ANPK"
    zones = parse_zon(read_text(resolve_ci(clean_root, "data/info.zon")), strict=True)
    assert len(zones) == 378 and len(parse_zon(read_text(resolve_ci(clean_root, "data/map.zon")))) == 6


def test_full_geometry_decode(vanilla_full):
    """Acceptance 4: every gta3/gta_int geometry decoded: 18 181 / 7 897 315 / 5 634 843 in <= 15 s."""
    r = vanilla_full
    assert r["ok"], r["failed"]
    c = r["counts"]
    assert (c["geo.decoded_geometries"], c["geo.decoded_verts"], c["geo.decoded_tris"]) == (18181, 7897315, 5634843)
    assert r["timings"]["geometry"] <= 15, r["timings"]


def test_s2_dxt_parity_on_game_textures(vanilla_full):
    """Acceptance 5 (spike S2): python/pillow/numpy agree (<= 1 per channel) on the reference textures."""
    r = vanilla_full
    assert r["ok"], r["failed"]
    par = r["dxt"]["parity"]
    assert par["dxt1_565"]["fmt"] == "DXT1" and par["dxt1_565"]["all_a255"]
    assert par["dxt1_1555"]["raster"].endswith("100") and par["dxt3"]["fmt"] == "DXT3"
    assert (par["x8r8g8b8"]["tex"], par["pal8"]["fmt"], par["a8r8g8b8"]["fmt"]) == ("vent_64", "PAL8", "A8R8G8B8")
    assert par["bistro_sw_wallbrick_01"]["fmt"] == "X8R8G8B8"   # SPEC card calls it DXT1/565; it is not
    assert all(p["max_diff"] <= 1 for p in par.values())
