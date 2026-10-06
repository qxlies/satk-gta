"""English kb facts and the authoring facts ``asset.*`` (wave A1, lane L7).

* every fact text is English (no Cyrillic) and every authoring fact has a checker here;
* ``satk kb fact asset`` serves the authoring facts without a built KB; enum types list their members;
* ``game``-marked: each authoring fact is checked against the vanilla index (the real ``work/index``; the gate
  builds a private one), the clean game files (read-only) and gta_sa.exe.
"""

from __future__ import annotations

import re
import sqlite3
import statistics
import sys
from pathlib import Path

import pytest

from satk.core import config as _config
from satk.kb.facts import ASSET_FACTS, FACTS

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kb_synth import make_inputs, make_world  # noqa: E402

_REAL = _config.build()  # captured before satk_home isolates the environment
_CYR = re.compile(f"[{chr(0x400)}-{chr(0x4FF)}]")   # Cyrillic block
SEDANS = ("premier", "admiral", "sentinel", "washing", "merit", "glendale", "elegant", "stafford", "intruder",
          "primo")


def _strings(v):
    if isinstance(v, str):
        yield v
    elif isinstance(v, dict):
        for x in v.values():
            yield from _strings(x)
    elif isinstance(v, (list, tuple)):
        for x in v:
            yield from _strings(x)


def test_facts_are_english():
    bad = [(f["key"], s) for f in FACTS + ASSET_FACTS for s in _strings(f) if _CYR.search(s)]
    assert bad == []


def test_asset_facts_are_well_formed():
    keys = [f["key"] for f in ASSET_FACTS]
    assert len(keys) >= 12 and len(set(keys)) == len(keys) and all(k.startswith("asset.") for k in keys)
    assert not set(keys) & {f["key"] for f in FACTS}
    for f in ASSET_FACTS:
        assert f["title"] and f["value"] and f["verify"] and f["conf"] in ("K", "K2", "KE", "K2E", "E"), f["key"]
    assert set(keys) == set(CHECKS), "every authoring fact needs a checker in this file"


def test_asset_facts_without_a_built_kb(satk_home, run_cli):
    env = run_cli(["kb", "fact", "asset", "--json"]).json
    assert env["ok"] and env["total"] == len(ASSET_FACTS) and {r[4] for r in env["rows"]} == {"tested"}
    one = run_cli(["kb", "fact", "asset.vehicle.lamp_keys", "--json"]).json
    assert one["status"] == "tested" and "vehiclelightson128" in one["value"] and one["verify"]
    topic = run_cli(["kb", "fact", "asset.vehicle", "--json"]).json
    assert topic["total"] >= 8 and all(r[0].startswith("asset.vehicle.") for r in topic["rows"])
    words = run_cli(["kb", "fact", "asset wheel", "--json"]).json
    assert "asset.vehicle.wheel_scale" in [r[0] for r in words["rows"]]
    assert run_cli(["kb", "fact", "lamp", "--json"]).json["error"]["code"] == "NOT_READY"


@pytest.fixture
def kb(tmp_path, satk_home):
    from satk.kb.build import build_kb
    from satk.kb.query import kb_path

    build_kb(kb_path(), make_inputs(make_world(tmp_path / "w")))
    return kb_path()


def test_enum_type_lists_members(kb, run_cli):
    env = run_cli(["kb", "sym", "eResourceFirstID", "--json"]).json
    assert env["match"] == "enum type" and env["enum"] == "eResourceFirstID"
    assert [r[:2] for r in env["rows"]] == [["RESOURCE_ID_DFF", "0"], ["RESOURCE_ID_TXD", "20000"],
                                            ["RESOURCE_ID_COL", "20001"]]
    member = run_cli(["kb", "sym", "RESOURCE_ID_COL", "--json"]).json     # a member still answers as before
    assert member["match"] == "exact" and member["rows"][0][3] == "20001"
    from satk.kb.query import enum_members

    assert enum_members("eResourceFirstID")[-1] == ("RESOURCE_ID_COL", 20001) and enum_members("eNope") == []


def test_facts_listing_names_the_authoring_facts(kb, run_cli):
    env = run_cli(["kb", "fact", "--json"]).json
    assert env["total"] == len(FACTS) and env["asset_facts"] == len(ASSET_FACTS)
    assert all(not _CYR.search(r[1]) for r in env["rows"])
    hit = run_cli(["kb", "search", "lamp colour keys", "--json"]).json
    assert any(r[0] == "fact" and r[1] == "asset.vehicle.lamp_keys" for r in hit["rows"])


def test_func_location(kb):
    from satk.kb.query import func_location

    hit = func_location("CStreaming::RequestModel")
    assert hit["src"] == "gta-reversed" and hit["loc"].startswith("source/game_sa/Streaming.cpp:")
    assert func_location("RequestModelStream")["name"] == "CStreaming::RequestModelStream"   # unqualified
    assert func_location(0x4087E0)["name"] == "CStreaming::RequestModel"
    assert func_location("NoSuchFunction") is None


# --------------------------------------------------------------------------- checks against the game


@pytest.fixture(scope="module")
def game():
    from satk.formats.img import ImgArchive
    from satk.re.pe import PeImage

    idx_path = Path(_REAL.paths.work) / "index" / "vanilla.sqlite"
    exe = _REAL.paths.game / "gta_sa.exe"
    img = _REAL.paths.game / "models" / "gta3.img"
    if not (idx_path.is_file() and exe.is_file() and img.is_file()):
        pytest.skip("needs the clean game copy and its vanilla index (satk index build --profile vanilla)")
    con = sqlite3.connect(f"file:{idx_path.as_posix()}?mode=ro", uri=True)
    with ImgArchive.open(img) as a:
        yield {"idx": con, "exe": PeImage.open(exe), "img": a}
    con.close()


CARS = "SELECT l.dff_id FROM model m JOIN model_link l ON l.id = m.id WHERE m.sec = 'cars' AND m.active = 1"


def _q(g, sql: str, *args):
    return g["idx"].execute(sql, args).fetchall()


def _has(g, name: bytes) -> bool:
    return g["exe"].data.find(name + b"\0") >= 0


def _dff(g, name: str) -> bytes:
    a = g["img"]
    return a.read(a.find(name + ".dff"))


def _frame_geoms(g, car: str) -> dict[str, list[float]]:
    """Frame name -> xyz positions of the geometry on that frame (first geometry wins)."""
    from satk.formats.dff import decode_geometries, scan_dff

    d = _dff(g, car)
    info = scan_dff(d)
    out: dict[str, list[float]] = {}
    for gi, mesh in zip(info.geoms, decode_geometries(d)):
        if 0 <= gi.frame < len(info.frames):
            out.setdefault((info.frames[gi.frame].name or "").strip().lower(), list(mesh.positions))
    return out


def _axis(pos: list[float], k: int) -> tuple[float, float]:
    vals = pos[k::3]
    return min(vals), max(vals)


def check_lamp_keys(g):
    keys = (0xFFAF00, 0x00FFC8, 0xB9FF00, 0xFF3C00)
    rows = _q(g, f"SELECT rgba, texture FROM dff_mat WHERE dff_id IN ({CARS})")
    tex = [(t or "").lower() for rgba, t in rows if (rgba >> 8) in keys]
    assert len(tex) > 500 and set(tex) == {"vehiclelights128"}
    assert _has(g, b"vehiclelights128") and _has(g, b"vehiclelightson128")


def check_paint_keys(g):
    assert _q(g, f"SELECT count(DISTINCT dff_id) FROM dff_mat WHERE dff_id IN ({CARS}) AND color_slot = 1")[0][0] == 181
    tex = [(t or "").lower() for (t,) in _q(g, f"SELECT texture FROM dff_mat WHERE dff_id IN ({CARS}) AND color_slot = 1")]
    assert (len(tex), tex.count("vehiclegrunge256"), tex.count("vehiclegeneric256")) == (2382, 1356, 703)


def check_wheel_scale(g):
    import json

    ratios = []
    for car in SEDANS:
        extra = json.loads(_q(g, "SELECT extra FROM model WHERE name = ? AND sec = 'cars' AND active = 1", car)[0][0])
        geo = _frame_geoms(g, car)
        pos = next(v for k, v in geo.items() if k.startswith("wheel") and not k.endswith("_dummy"))
        lo_y, hi_y = _axis(pos, 1)
        lo_z, hi_z = _axis(pos, 2)
        ratios.append(max(hi_y - lo_y, hi_z - lo_z) / extra["wheel_scale_f"])
    assert 0.97 <= statistics.median(ratios) <= 1.03 and all(0.9 <= r <= 1.1 for r in ratios), ratios


def check_col_piece(g):
    from satk.formats.dff import find_embedded_col
    from satk.rw.col import ColError, decode_model, split_models

    pieces: list[int] = []
    broken = 0
    stems = [r[0] for r in _q(g, f"SELECT b.stem FROM dff JOIN blob b ON b.id = dff.blob_id WHERE dff.id IN ({CARS})")]
    for stem in stems:
        e = g["img"].find(stem + ".dff")
        d = g["img"].read(e) if e is not None else b""
        col = find_embedded_col(d) if d else None
        if col:
            try:
                pieces += [s[2][1] for s in decode_model(split_models(d[col[0]:col[0] + col[1]])[0][0]).spheres]
            except ColError:      # rccam's embedded COL is broken in vanilla
                broken += 1
    assert broken <= 2
    inside = sum(p <= 21 for p in pieces)
    assert len(pieces) == 5414 and pieces.count(0) == 3546 and round(100 * inside / len(pieces), 1) == 98.7


def check_no_mips(g):
    rows = _q(g, """SELECT t.levels, count(*) FROM texture t JOIN txd x ON x.id = t.txd_id JOIN blob b ON b.id = x.blob_id
                    WHERE lower(b.stem) IN (SELECT lower(txd) FROM model WHERE sec = 'cars' AND active = 1)
                       OR lower(b.stem) = 'vehicle' GROUP BY t.levels""")
    assert rows == [(1, 593)]


def check_upgrade_frames(g):
    n = _q(g, f"SELECT count(DISTINCT dff_id) FROM dff_frame WHERE dff_id IN ({CARS}) AND name LIKE 'ug\\_%' ESCAPE '\\'")
    assert n[0][0] == 83
    for name in ("ug_bonnet", "ug_bonnet_left", "ug_bonnet_right", "ug_spoiler", "ug_wing_left", "ug_wing_right",
                 "ug_roof", "ug_nitro", "ug_lights", "ug_frontbullbar", "ug_backbullbar"):
        assert _has(g, name.encode()), name


def check_hinges(g):
    ok = 0
    for car in SEDANS:
        geo = _frame_geoms(g, car)
        door_lo, door_hi = _axis(geo["door_lf_ok"], 1)
        bon_lo, bon_hi = _axis(geo["bonnet_ok"], 1)
        ok += door_hi <= 0.1 and door_lo < -0.5 and bon_lo >= -0.1 and bon_hi > 0.5
    assert ok >= 8, ok


def check_glass(g):
    rows = _q(g, f"""SELECT printf('%08x', m.rgba) || '|' || lower(m.texture), count(*) AS n FROM dff_mat m
                    JOIN dff_geom ge ON ge.dff_id = m.dff_id AND ge.idx = m.geom
                    JOIN dff_frame f ON f.dff_id = ge.dff_id AND f.idx = ge.frame
                    WHERE m.dff_id IN ({CARS}) AND lower(trim(f.name)) = 'windscreen_ok' GROUP BY 1 ORDER BY n DESC""")
    assert rows[0] == ("ffffff80|vehiclegeneric256", 112)


def check_normals(g):
    rows = _q(g, f"SELECT rw_flags & 16, rw_flags & 8, count(*) FROM dff_geom WHERE dff_id IN ({CARS}) GROUP BY 1, 2")
    assert rows == [(16, 0, 3323)]


def check_dirt(g):
    tex = _q(g, """SELECT t.w, t.h FROM texture t JOIN txd x ON x.id = t.txd_id JOIN blob b ON b.id = x.blob_id
                   WHERE lower(b.stem) = 'vehicle' AND lower(t.name) = 'vehiclegrunge256'""")
    assert tex and tex[0][0] == 256
    sec = g["exe"].section_at(0x5D5BC0)
    assert sec is not None and sec.executable and _has(g, b"vehiclegrunge256")


def check_frames(g):
    from satk.re.nodes import nodes

    env = nodes(None, str(_REAL.paths.game / "gta_sa.exe"))
    rows = {r[0]: r for r in env["rows"]}
    assert env["vehicle_tables"] == 12 and rows["automobile"][2:6] == [61, 40, 15, 6]


def check_big_building(g):
    big = _q(g, "SELECT count(*) FROM model WHERE active = 1 AND draw > 300")[0][0]
    lod = _q(g, "SELECT count(*) FROM model WHERE active = 1 AND draw > 300 AND id IN "
                "(SELECT model_id FROM inst WHERE is_lod = 1)")[0][0]
    assert big == 4474 and lod == 4288 and lod / big > 0.9


def check_face_light(g):
    from satk.rw.col import decode_model, split_models

    faces = day = 0
    for e in g["img"].entries:
        if e.ext != "col" or not e.name.lower().startswith("lae"):
            continue
        models, _tail = split_models(g["img"].read(e))
        for rec in models:
            for f in decode_model(rec).faces:
                faces += 1
                day += bool(f[4] & 15)
    assert faces > 10000 and day / faces > 0.9


def check_ide_flags(g):
    counts: dict[int, int] = {}
    for (flags,) in _q(g, "SELECT flags FROM model WHERE active = 1 AND sec IN ('objs', 'tobj')"):
        for b in range(32):
            if flags and flags >> b & 1:
                counts[1 << b] = counts.get(1 << b, 0) + 1
    assert counts[0x4] == 2332 and counts[0x80] == 5079 and counts[0x200000] == 1526
    for bit in (0x1, 0x8, 0x40, 0x200, 0x400, 0x800, 0x1000, 0x2000, 0x4000, 0x8000, 0x100000, 0x400000):
        assert counts.get(bit, 0) > 0, hex(bit)


def check_streaming_budget(g):
    assert g["exe"].u32(0x5B8E6A) == 0x3200000 and g["exe"].u32(0x5B8E66) == 0x8A5A80


def check_capacity_stores(g):
    secs = dict(_q(g, "SELECT sec, count(*) FROM model WHERE active = 1 GROUP BY sec"))
    assert (secs["cars"], secs["peds"], secs["weap"], secs["objs"], secs["tobj"]) == (212, 276, 50, 14045, 160)
    assert secs["anim"] + secs["hier"] == 89
    assert _q(g, "SELECT count(*) FROM ipl_item WHERE sec = 'enex'")[0][0] == 376
    assert _q(g, "SELECT count(*) FROM fx2d WHERE origin = 'ide'")[0][0] == 97
    cols = _q(g, "SELECT count(*) FROM blob b JOIN source s ON s.id = b.source_id "
                 "WHERE b.ext = 'col' AND b.active = 1 AND s.kind = 'img'")[0][0]
    assert cols + 1 == 252                       # + the engine's generic slot 0
    txds = _q(g, "SELECT count(*) FROM blob WHERE ext = 'txd' AND active = 1")[0][0]
    assert 4000 <= txds <= 4100


CHECKS = {
    "asset.vehicle.lamp_keys": check_lamp_keys, "asset.vehicle.paint_keys": check_paint_keys,
    "asset.vehicle.wheel_scale": check_wheel_scale, "asset.vehicle.col_piece": check_col_piece,
    "asset.vehicle.no_mips": check_no_mips, "asset.vehicle.upgrade_frames": check_upgrade_frames,
    "asset.vehicle.hinges": check_hinges, "asset.vehicle.glass": check_glass,
    "asset.vehicle.normals": check_normals, "asset.vehicle.dirt": check_dirt,
    "asset.vehicle.frames": check_frames, "asset.world.big_building": check_big_building,
    "asset.col.face_light": check_face_light, "asset.ide.flags": check_ide_flags,
    "asset.streaming.budget": check_streaming_budget, "asset.capacity.stores": check_capacity_stores,
}


@pytest.mark.game
@pytest.mark.parametrize("key", sorted(CHECKS))
def test_asset_fact_holds(game, key):
    CHECKS[key](game)


def test_facts_text_from_code_wins_over_an_old_kb(kb, run_cli):
    """A KB built before the translation still answers in English (status and checks stay from the build)."""
    con = sqlite3.connect(kb)
    with con:
        con.execute("UPDATE fact SET title = ?, value = ?, note = ? WHERE key = 'pools.ped'",
                    (chr(0x41F) + chr(0x443), "140 " + chr(0x441), chr(0x437)))   # Cyrillic letters
    con.close()
    one = run_cli(["kb", "fact", "pools.ped", "--json"]).json
    assert one["title"] == "Ped pool" and not _CYR.search(one["value"]) and not _CYR.search(one.get("note") or "")
    rows = run_cli(["kb", "fact", "pools", "--json"]).json["rows"]
    assert all(not _CYR.search(" ".join(map(str, r))) for r in rows)
