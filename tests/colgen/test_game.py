"""satk.colgen against the vanilla game (READ-ONLY; gta-sa-clean). Marked ``game``.

* 20 vanilla props: generated hull, box and boxes collisions pass the asset lint, contain the render mesh and
  re-parse with ``satk.formats``; mesh mode passes the lint too;
* ``--surface auto`` agrees with the vanilla surfaces on >= 80 % of the faces of a model sample (index needed
  for the TXD context and the vanilla collisions; skipped without it);
* vehicles: spheres + closed shadow mesh with the engine winding;
* ``col check`` over every vanilla collision finds only the two real defects of the shipped data.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from satk.core import config as _config

pytestmark = pytest.mark.game
np = pytest.importorskip("numpy")

_REAL = _config.build()                         # captured before satk_home isolates the environment
GAME: Path = _REAL.paths.game

#: 20 props of gta3.img (objs with a COLFILE collision, < 15 m, 30..1500 triangles, model id % 41 == 0).
PROPS = ("aw_streettree2", "Pinebg_hism", "genVEG_tallgrass04", "GENERATOR_LOW", "fenceshit2", "wheel_or1",
         "exh_c_j", "wg_r_lr_bl1", "rbmp_c_l", "fbmp_lr_sv1", "CJ_AIRCON", "DYN_ROADBARRIER_5b", "DYN_PORCH_4",
         "DYN_WINE_BREAK", "CJ_CARDBRD_PICKUP", "kmb_goflag", "acwinch1", "des_ruin2_", "sw_logcover",
         "GAY_telgrphpole")


@pytest.fixture(scope="module")
def gta3():
    from satk.formats.img import ImgArchive

    p = GAME / "models" / "gta3.img"
    if not p.is_file():
        pytest.skip(f"no {p}")
    with ImgArchive.open(p) as a:
        yield a


def _dff(gta3, name: str) -> bytes:
    e = gta3.find(name + ".dff")
    assert e is not None, name
    return gta3.read(e)


def _vanilla_index():
    from satk.core.errors import SatkError
    from satk.index.api import open_index

    try:
        db = open_index("vanilla")
        db.query("SELECT 1 FROM model LIMIT 1", [], limit=1)
    except SatkError as e:
        pytest.skip(f"vanilla index unavailable: {e}")
    return db


@pytest.mark.parametrize("mode", ["hull", "box", "boxes", "mesh"])
def test_twenty_props_pass_lint_contain_the_mesh_and_reparse(gta3, tmp_path, mode):
    from satk.colgen.build import GenOptions, generate
    from satk.colgen.check import check_model
    from satk.formats.col import iter_col
    from satk.lint.runner import lint
    from satk.rw import col as COL

    recs = []
    for name in PROPS:
        r = generate(_dff(gta3, name), name, opts=GenOptions(mode=mode))
        m = r.model
        if mode != "mesh":
            assert r.fit["outside"] <= 1 / 128, (name, r.fit)          # the render mesh is inside
        else:
            assert len(m.faces) <= 1000 and r.fit["dev"] < 0.5, (name, r.fit)
        assert [f.row() for f in check_model(m).findings if f.sev != "info"] == [], name
        recs.append(COL.encode_model(m))
    blob = b"".join(recs)
    back = list(iter_col(blob))
    assert [b.name.lower() for b in back] == [n.lower()[:21] for n in PROPS]
    p = tmp_path / f"props_{mode}.col"
    p.write_bytes(blob)
    rep = lint(str(p), use_index=False)
    assert rep.at_least("warn") == [], [f.row() for f in rep.at_least("warn")][:5]


def test_vehicle_spheres_and_closed_shadow(gta3, tmp_path):
    from satk.colgen.build import GenOptions, generate
    from satk.colgen.check import check_model
    from satk.lint.runner import lint
    from satk.rw import col as COL

    for name in ("infernus", "admiral", "bus"):
        r = generate(_dff(gta3, name), name, sec="cars", opts=GenOptions())
        m = r.model
        assert r.mode == "spheres" and 10 <= len(m.spheres) <= 20 and 100 <= len(m.shadow_faces) <= 300
        assert {s[2][0] for s in m.spheres} == {63} and r.fit["coverage"] >= 0.5
        assert [f.row() for f in check_model(m).findings] == [], name       # closed shadow, engine winding
        p = tmp_path / f"{name}.col"
        p.write_bytes(COL.encode_model(m))
        assert lint(str(p), use_index=False).at_least("warn") == []


def test_surface_auto_matches_vanilla_faces(tmp_path):
    """Mesh-mode collisions of every 97th vanilla model with collision faces: the surface of each generated
    face against the surface of the nearest vanilla collision face (TXD context from the index)."""
    from satk.colgen.build import GenOptions, generate
    from satk.colgen.derive import _read_col, col_faces, col_vertices
    from satk.colgen.nearest import nearest_tri
    from satk.colgen.sources import resolve_sources
    from satk.model3d.resolve import model_files

    db = _vanilla_index()
    ids: list[int] = []
    off = 0
    while True:
        env = db.query("SELECT l.id FROM model_link l JOIN model m ON m.id = l.id AND m.active = 1 "
                       "JOIN col c ON c.id = l.col_id WHERE l.col_via = 'colfile' AND l.dff_id IS NOT NULL "
                       "AND c.faces > 0 ORDER BY l.id LIMIT 500 OFFSET ?", [off], limit=500)
        ids += [int(r[0]) for r in env["rows"]]
        if len(env["rows"]) < 500:
            break
        off += 500
    hit = tot = 0
    cache: dict = {}
    for mid in ids[::97]:
        src = resolve_sources(f"model:{mid}")[0]
        cm = _read_col(model_files(db, mid).col, cache)
        r = generate(src.read(), src.name, sec=src.sec, txd=src.txd, opts=GenOptions(mode="mesh", max_faces=20000))
        m = r.model
        if not m.faces:
            continue
        V = np.asarray(m.vertices, float) / 128.0
        F = np.asarray([f[:3] for f in m.faces])
        S = np.asarray([f[3] for f in m.faces])
        VV = col_vertices(cm)
        FF, mats, _l = col_faces(cm)
        j, d = nearest_tri(V[F].mean(axis=1), VV, FF)
        ok = d <= 1.0
        hit += int((S[ok] == np.asarray(mats)[j[ok]]).sum())
        tot += int(ok.sum())
    assert tot > 10000
    assert hit / tot >= 0.80, f"{100 * hit / tot:.1f} % of {tot} faces"


def test_vanilla_collisions_check_only_the_known_defects(clean_root):
    from satk.colgen.check import check_blob

    from satk.formats.img import ImgArchive

    errors = []
    models = 0
    with ImgArchive.open(clean_root / "models" / "gta3.img") as a:
        for e in a.entries:
            if e.ext != "col":
                continue
            reps, problems = check_blob(a.read(e))
            assert not [p for p in problems if not p.startswith("DUPLICATE_NAME")], (e.name, problems)
            models += len(reps)
            errors += [(e.name, f.model, f.check) for r in reps for f in r.findings if f.sev == "error"]
    assert models > 8000
    # two real defects of the shipped data: bounds that miss geometry by > 0.5 m
    assert sorted(errors) == [("las2_4.col", "newlas2sh_LAS2", "bounds"),
                              ("vegepart.col", "veg_largefurs02", "bounds")]


def test_derive_a_slice_of_the_index():
    from satk.colgen.derive import derive, table_text

    _vanilla_index()
    res = derive("vanilla", holdout=10, limit=400)
    assert res.report["models"] >= 300 and res.report["faces"] > 1000
    assert res.textures and all(0 <= v[0] <= 178 for v in res.textures.values())
    assert set(res.lighting) >= {"day", "night"}
    import json

    doc = json.loads(table_text(res))
    assert doc["format"] == "satk-colgen-tex-surface" and doc["textures"] == res.textures
