"""satk.mapconv against the real vanilla index and gta-sa-clean (read-only; M2-04 acceptance 2).

Every IPL of the index (text files and ``bnry`` blobs in the IMGs) is converted IPL -> MTA ``.map`` -> read
back, and each object is compared with its ``inst`` row: model, position (0.01), area and the rotation the
engine applies (conjugated quaternion, or heading only for small tilts). Skipped without the index; writes
only into the isolated ``satk_home`` workspace.
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
from pathlib import Path

import pytest

from satk.core import config as _config
from satk.core.errors import SatkError

pytestmark = pytest.mark.game

_REAL = _config.build()                         # captured before satk_home isolates the environment
INDEX: Path = Path(_REAL.paths.work) / "index" / "vanilla.sqlite"


@pytest.fixture(scope="module")
def real_index_path() -> Path:
    if not INDEX.is_file():
        pytest.skip(f"no vanilla index: {INDEX} (satk index build)")
    return INDEX


@pytest.fixture
def real_db(real_index_path, satk_home):
    from satk.index.api import IndexDB, override_index

    try:
        db = IndexDB("vanilla", path=real_index_path)
    except SatkError as e:
        pytest.skip(f"index not usable: {e.msg}")
    db.root = _REAL.paths.game
    with override_index(db):
        yield db
    db.close()


def _expected_rows(path: Path) -> dict[str, list[tuple]]:
    uri = "file:" + path.as_posix() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as c:
        rows = c.execute("SELECT p.name, i.idx, i.model_id, i.x, i.y, i.z, i.qx, i.qy, i.qz, i.qw, i.area, i.iflags "
                         "FROM inst i JOIN ipl p ON p.id = i.ipl_id ORDER BY p.name, i.idx").fetchall()
    out: dict[str, list[tuple]] = {}
    for r in rows:
        out.setdefault(r[0].lower(), []).append(r[1:])
    return out


def _basis_expected(q, interior):
    from satk.formats.ipl import rz_deg, world_quat
    from satk.index.api import rotate

    h = rz_deg(q, interior)
    if h is not None:
        a = math.radians(h) / 2.0
        wq = (0.0, 0.0, math.sin(a), math.cos(a))
    else:
        wq = world_quat(q)
        n = math.sqrt(sum(c * c for c in wq))
        wq = tuple(c / n for c in wq)
    return [rotate(wq, v) for v in ((1, 0, 0), (0, 1, 0), (0, 0, 1))]


def test_every_ipl_to_map_matches_the_index(real_db, real_index_path):
    from satk.mapconv.convert import load_source, read_scene
    from satk.mapconv.mta import read_mta, write_mta
    from satk.mapconv.rot import euler_matrix

    expected = _expected_rows(real_index_path)
    t0 = time.perf_counter()
    n_inst = n_ipl = 0
    worst_pos = worst_rot = 0.0
    for name in sorted(expected):
        sc = read_scene(load_source(f"ipl:{name}"), "auto")
        back = read_mta(write_mta(sc))
        rows = expected[name]
        assert len(back.objects) == len(rows), name
        for o, (idx, model, x, y, z, qx, qy, qz, qw, area, iflags) in zip(back.objects, rows):
            assert o.model == model and o.interior == area and o.iflags == iflags, (name, idx)
            worst_pos = max(worst_pos, math.dist(o.pos, (x, y, z)))
            interior = area | (iflags << 8)
            got = euler_matrix(*o.rot)
            for g, e in zip(got, _basis_expected((qx, qy, qz, qw), interior)):
                worst_rot = max(worst_rot, math.dist(g, e))
            n_inst += 1
        n_ipl += 1
    seconds = time.perf_counter() - t0
    assert n_inst == sum(len(v) for v in expected.values()) and n_inst > 50_000
    assert worst_pos <= 0.01, worst_pos
    assert worst_rot <= 1e-4, worst_rot  # unit basis vectors: about 0.006 degrees
    assert seconds < 120, seconds


def test_cli_ipl_sid_to_map_and_validate(real_db, run_cli, satk_home):
    r = run_cli(["map", "convert", "ipl:lae2_stream0", "--to", "mta"])
    assert r.code == 0, r.err
    env = r.json
    assert env["objects"] == real_db.get("ipl:lae2_stream0")["n_inst"]
    text = Path(env["file"]).read_text(encoding="utf-8")
    assert 'id="object (Lae2_roads89) (5)" model="17613"' in text  # inst:lae2_stream0#4
    v = run_cli(["map", "validate", "ipl:lae2"]).json
    assert v["ok"] and v["errors"] == 0
    s = run_cli(["map", "validate", str(Path(__file__).parent / "data" / "grove_sample.pwn")]).json
    assert {r[1] for r in s["rows"] if r[0] == "error"} == {"MODEL_UNKNOWN"}  # the SA-MP wall 19379
    assert s["errors"] == 1


# --------------------------------------------------------------------------- B4: binary IPL and map clean


def test_every_vanilla_binary_ipl_roundtrips_bit_for_bit(clean_root):
    """B4 acceptance: decompile -> compile of every bnry IPL in the clean copy's IMGs gives the same bytes."""
    from satk.formats.img import ImgArchive
    from satk.mapconv.bnry import compile_ipl, decompile, first_difference

    n = n_inst = n_cars = 0
    bad: list[str] = []
    t0 = time.perf_counter()
    for img in sorted((clean_root / "models").glob("*.img")):
        with ImgArchive.open(img) as a:
            for e in a.entries:
                if e.ext != "ipl":
                    continue
                data = a.read(e)
                text, info = decompile(data, label=e.name)
                back, rep = compile_ipl(text)
                n += 1
                n_inst += rep["inst"]
                n_cars += rep["cars"]
                if back != data:
                    bad.append(f"{img.name}/{e.name}@{first_difference(back, data)}")
    assert bad == []
    assert n >= 190 and n_inst > 40_000 and n_cars > 1000, (n, n_inst, n_cars)  # vanilla: 190, 41667, 1045
    assert time.perf_counter() - t0 < 60


def test_cli_decompile_compile_compare_real(real_db, run_cli):
    d = run_cli(["ipl", "decompile", "ipl:lae2_stream0"]).json
    assert d["exact"] is True and d["inst"] == real_db.get("ipl:lae2_stream0")["n_inst"]
    text = Path(d["file"]).read_text(encoding="latin-1")
    assert "\n17613, Lae2_roads89, 0, " in text  # names from the index
    c = run_cli(["ipl", "compile", d["file"], "--compare", "ipl:lae2_stream0"]).json
    assert c["same"] is True and c["bytes"] == d["bytes"]


def test_map_clean_district_removes_hd_and_lod(real_db, run_cli, real_index_path):
    """B4 acceptance: 'map clean' of a district gives Lua that removes the HD placements and their LODs, and the
    modloader copies keep every LOD index valid (checked against the index)."""
    import re

    r = run_cli(["map", "clean", "Ganton", "--limit", "500"])
    assert r.code == 0, r.err
    env = r.json
    assert env["removed"] > 300 and env["hd"] > 20 and env["lods"] > 20, env
    chk = env["lod_check"]
    assert chk["bad"] == 0 and chk["hidden_lods"] == 0 and chk["hidden"] == env["removed"]
    rep = json.loads(Path(env["files"]["report"]).read_text(encoding="utf-8"))
    removed = {row[0] for row in rep["placements"]["rows"]}
    uri = "file:" + real_index_path.as_posix() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as c:
        rows = c.execute("SELECT 'inst:' || lower(p.name) || '#' || i.idx, i.model_id, i.x, i.y, i.z, i.area, "
                         "(SELECT 'inst:' || lower(p2.name) || '#' || l.idx FROM inst l JOIN ipl p2 ON p2.id = l.ipl_id "
                         "WHERE l.id = i.lod_id) FROM inst i JOIN ipl p ON p.id = i.ipl_id").fetchall()
    lua = Path(env["files"]["lua"]).read_text(encoding="utf-8")
    calls = [(int(m[1]), float(m[2]), (float(m[3]), float(m[4]), float(m[5])), int(m[6])) for m in re.finditer(
        r"^\s*\{(-?\d+), ([\d.]+), (-?[\d.]+), (-?[\d.]+), (-?[\d.]+), (-?\d+)\}, --", lua, re.M)]
    assert calls and len(calls) == env["calls"]
    models = {m for m, *_ in calls}
    n_hd_lod = 0
    for sid, model, x, y, z, area, lod in rows:
        if sid in removed and lod is not None:
            assert lod in removed, (sid, lod)  # the LOD of every removed HD goes too (none is shared here)
            n_hd_lod += 1
        if model not in models:
            continue
        hit = any(m == model and a == area and math.dist(cpos, (x, y, z)) <= rad + 1e-3 for m, rad, cpos, a in calls)
        assert hit == (sid in removed), sid
    assert n_hd_lod == env["hd"]
    for name, info in rep["ipl_files"].items():
        copy = Path(env["files"]["ipl"]) / info["file"]
        assert copy.is_file() and copy.stat().st_size > 0
