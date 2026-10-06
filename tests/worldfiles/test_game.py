"""satk.worldfiles against the vanilla game (READ-ONLY; gta-sa-clean). Marked ``game``.

* the five stock GXTs: read -> JSON -> write is bit-exact; the German one also through the folder form; the
  shipped key names cover >= 80 % of american.gxt; the accented letters decode to real words;
* info.zon / map.zon and water.dat / water1.dat: export -> write is bit-exact; the engine-pool facts the docs
  state (379/380 navigation zones; 301/301 quads, 6/6 triangles, 1021/1021 water vertices);
* timecyc.dat and popcycle.dat: patch and patch back give the original bytes; the short vanilla line 320;
* radar: the 144 stock tiles decode; the TXD writer rebuilds the stock container byte for byte (except the
  uninitialised name padding of some stock files); export -> build gives PSNR >= 30 dB per tile.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from satk.core import config as _config

pytestmark = pytest.mark.game

_REAL = _config.build()                         # captured before satk_home isolates the environment
GAME: Path = _REAL.paths.game


@pytest.fixture(scope="module")
def root() -> Path:
    if not (GAME / "gta_sa.exe").is_file():
        pytest.skip(f"no vanilla copy at {GAME}")
    return GAME


@pytest.mark.parametrize("lang", ["american", "french", "german", "italian", "spanish"])
def test_gxt_round_trip_bit_exact(root, lang):
    from satk.worldfiles import gxtnames
    from satk.worldfiles.gxt import doc_from_json, doc_to_json, read_gxt, write_gxt

    raw = (root / "text" / f"{lang}.gxt").read_bytes()
    doc = read_gxt(raw, gxtnames.shipped())
    assert write_gxt(doc) == raw
    assert write_gxt(doc_from_json(json.loads(json.dumps(doc_to_json(doc), ensure_ascii=False)))) == raw
    if lang == "american":
        named = sum(1 for t in doc.tables for k, _v in t.entries if not k.startswith("0x"))
        assert named / doc.count() >= 0.8
    texts = [v for t in doc.tables for _k, v in t.entries]
    word = {"german": "drücken", "french": "Caméra", "spanish": "¿", "italian": "à"}.get(lang)
    if word:
        assert any(word in t for t in texts)


def test_gxt_folder_round_trip(root, satk_home, run_cli):
    r = run_cli(["gxt", "export", str(root / "text" / "german.gxt"), "--format", "dir", "--out", "de"])
    assert r.code == 0 and r.json["round_trip"] is True, r.out
    r = run_cli(["gxt", "write", r.json["path"], "--out", "german.gxt"])
    assert r.code == 0, r.out
    assert Path(r.json["path"]).read_bytes() == (root / "text" / "german.gxt").read_bytes()


@pytest.mark.parametrize("name", ["info.zon", "map.zon"])
def test_zon_round_trip_bit_exact(root, name):
    from satk.worldfiles.zon import doc_from_json, doc_to_json, parse_zon_doc, write_zon

    raw = (root / "data" / name).read_bytes()
    errs: list = []
    doc = parse_zon_doc(raw.decode("latin-1"), errs)
    assert errs == [] and write_zon(doc) == raw
    obj = json.loads(json.dumps(doc_to_json(doc)))
    assert write_zon(doc_from_json(obj)) == raw
    raws = [z["raw"] for z in obj["zones"] if "raw" in z]
    assert raws == (["VERO2\t, 0, 1046.15, -1722.26, -89.0839, 1161.52, -1577.59, 110.916, 1, VERO"]
                    if name == "info.zon" else [])


def test_zone_pool_facts(root, satk_home, run_cli):
    r = run_cli(["zone", "check", "--file", str(root / "data" / "info.zon")])
    assert r.code == 0, r.out
    env = r.json
    assert env["zones"] == 378 and env["free"]["navigation"] == 1
    assert env["summary"] == {"info": 3}                     # three partial overlaps, nothing else


@pytest.mark.parametrize("name, counts", [("water.dat", {"quads": 301, "tris": 6, "verts": 1021}),
                                          ("water1.dat", {"quads": 261, "tris": 6, "verts": 867})])
def test_water_round_trip_and_pools(root, name, counts):
    from satk.worldfiles.water import counts as count, doc_from_json, doc_to_json, parse_water_doc, write_water

    raw = (root / "data" / name).read_bytes()
    doc = parse_water_doc(raw.decode("latin-1"))
    obj = json.loads(json.dumps(doc_to_json(doc)))
    assert not any("raw" in p for p in obj["polys"])        # the stock number format rebuilds every line
    assert write_water(doc_from_json(obj)) == raw
    assert count(doc.polys) == counts


def test_timecyc_patch_and_back(root):
    from satk.worldfiles.timecyc import TimecycFile, select_rows, slot_index

    raw = (root / "data" / "timecyc.dat").read_bytes()
    tf = TimecycFile(raw.decode("latin-1"))
    assert len(tf.rows) == 184 and tf.render() == raw
    short = [r for r in tf.rows if r.nread < 51]
    assert [(r.line, r.weather_name, r.hour, r.nread) for r in short] == [(320, "RAINY_COUNTRYSIDE", 20, 20)]
    far = slot_index("far_clip")[0][0]
    rows = [r for r in select_rows(tf.rows, "all", "12")]
    for r in rows:
        tf.set(r, {far: tf.flat(r)[far] * 2})
    patched = tf.render()
    assert len(tf.changed_lines()) == 23
    back = TimecycFile(patched.decode("latin-1"))
    for r in select_rows(back.rows, "all", "12"):
        back.set(r, {far: back.flat(r)[far] / 2})
    assert back.render() == raw


def test_popcycle_patch_and_back(root):
    from satk.worldfiles.popcycle import PopcycleFile

    raw = (root / "data" / "popcycle.dat").read_bytes()
    pf = PopcycleFile(raw.decode("latin-1"))
    assert len(pf.rows) == 480 and pf.errors == [] and pf.render() == raw
    sel = pf.select("GANGLAND", "all", "all")
    old = {id(r): r.values[0] for r in sel}
    for r in sel:
        pf.set(r, {0: min(255, r.values[0] + 5)})
    back = PopcycleFile(pf.render().decode("latin-1"))
    for r in back.select("GANGLAND", "all", "all"):
        back.set(r, {0: r.values[0] - 5})
    assert back.render() == raw and len(old) == 24


def test_radar_tiles_and_container(root):
    np = pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    from satk.formats.img import ImgArchive
    from satk.formats.txd import mip0_bytes, parse_txd
    from satk.texmod.encode import psnr
    from satk.texmod.txdwrite import NativeSpec, native_chunk, txd_chunk
    from satk.worldfiles import radar as R

    img = root / "models" / "gta3.img"
    identical = 0
    with ImgArchive.open(img) as a:
        names = a.by_name()
        for i in range(144):
            buf = a.read(names[f"{R.tile_name(i)}.txd"])
            tx = parse_txd(buf).textures[0]
            assert (tx.name, tx.d3dfmt, tx.w, tx.h, tx.levels) == (R.tile_name(i), "DXT1", 128, 128, 1)
            out = txd_chunk([native_chunk(NativeSpec(tx.name, "DXT1", 128, 128, (mip0_bytes(buf, tx),),
                                                     filter=1, addressing=0x11))])
            if out == buf[:len(out)]:
                identical += 1
            else:                                            # stock name fields carry junk after the NUL
                diff = [k for k in range(len(out)) if out[k] != buf[k]]
                assert all(8 <= k - 52 < 72 for k in diff), diff[:8]    # name and mask fields only
    assert identical >= 100
    tiles, warn = R.read_tiles(img)
    assert warn == []
    mos = R.mosaic(tiles, 128)
    # orientation: spots well inside a water.dat quad are drawn as sea (blue over red)
    from satk.worldfiles.water import parse_water_doc

    quads = [p.bbox() for p in parse_water_doc((root / "data" / "water.dat").read_text("latin-1")).polys
             if p.kind == "quad"]
    wet = blue = 0
    for x in range(-2875, 3000, 250):
        for y in range(-2875, 3000, 250):
            if not any(q[0] <= x - 60 and q[1] <= y - 60 and x + 60 <= q[2] and y + 60 <= q[3] for q in quads):
                continue
            px, py = int((x + 3000) / 6000 * 1536), int((3000 - y) / 6000 * 1536)
            m = mos[py - 10:py + 10, px - 10:px + 10, :3].reshape(-1, 3).mean(0)
            wet += 1
            blue += int(m[2] > m[0] + 20)
    assert wet >= 20 and blue >= 0.8 * wet, (wet, blue)
    txds = R.build_tiles(mos, 128)
    back = R.mosaic([R.decode_tile(b)[1] for b in txds], 128)
    worst = min(psnr(mos[r * 128:(r + 1) * 128, c * 128:(c + 1) * 128], back[r * 128:(r + 1) * 128,
                                                                           c * 128:(c + 1) * 128], alpha=False)
                for r in range(12) for c in range(12))
    assert worst >= 30.0 and psnr(mos, back, alpha=False) >= 35.0
    assert np.array_equal(back.shape, mos.shape)
