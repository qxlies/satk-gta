"""WP-05 checks against the real game files (READ-ONLY; SPEC §5.3 WP-05, Appendix A).

Marked ``game``. Files are read straight from ``gta-sa-clean`` (``ImgArchive``), so most checks need no
index; the ``*_index`` tests also use the real ``work/index/vanilla.sqlite`` when it exists (skipped
otherwise) and write only into an isolated temporary workspace.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from satk.core import config as _config

pytestmark = pytest.mark.game
np = pytest.importorskip("numpy")

_REAL = _config.build()                         # captured before satk_home isolates the environment
GAME: Path = _REAL.paths.game
INDEX: Path = Path(_REAL.paths.work) / "index" / "vanilla.sqlite"


@pytest.fixture(scope="module")
def gta3():
    from satk.formats.img import ImgArchive

    p = GAME / "models" / "gta3.img"
    if not p.is_file():
        pytest.skip(f"no {p}")
    with ImgArchive.open(p) as a:
        yield a


def _load(gta3, name: str, txds: list[str], sec: str | None):
    from satk.core.paths import open_ro
    from satk.model3d.mesh import build_scene
    from satk.model3d.textures import TxdChain, resolve_materials, vehicle_colours

    dff = gta3.read(gta3.find(name + ".dff"))
    chain = []
    for t in txds:
        if t == "vehicle":
            with open_ro(GAME / "models" / "generic" / "vehicle.txd") as f:
                chain.append(("vehicle", f.read()))
        else:
            chain.append((t, gta3.read(gta3.find(t + ".txd"))))
    sc = build_scene(dff, name=name, sec=sec)
    mats = resolve_materials(sc.materials, TxdChain(chain), vehicle_colours(name, GAME) if sec == "cars" else None)
    return sc, mats


def test_infernus_obj_glb(gta3, validate_glb):
    from satk.model3d.gltf import build_glb
    from satk.model3d.obj import build_obj

    sc, mats = _load(gta3, "infernus", ["infernus", "vehicle"], "cars")
    assert sc.tris == 3072                                            # Appendix A: infernus.dff 3 072 triangles
    text, mtl, pngs, st = build_obj(sc, mats, "infernus")
    assert st["tris"] == 3072 and sum(1 for ln in text.splitlines() if ln.startswith("f ")) == 3072
    maps = {ln.split(" ", 1)[1] for ln in mtl.splitlines() if ln.startswith("map_Kd")}
    assert maps and maps <= set(pngs) and st["tex_missing"] == 0
    glb, gst = build_glb(sc, mats)
    doc = validate_glb(glb)
    assert gst["tris"] == 3072 and gst["meshes"] == 15 and doc["asset"]["version"] == "2.0"
    assert sum(doc["accessors"][p["indices"]]["count"] for m in doc["meshes"] for p in m["primitives"]) == 3 * 3072


def test_lae2_roads89_prelit(gta3, validate_glb):
    from satk.model3d.gltf import build_glb

    sc, mats = _load(gta3, "lae2_roads89", ["lae2roadshub"], "objs")
    assert sc.tris == 225 and sc.verts == 232                          # Appendix A
    doc = validate_glb(build_glb(sc, mats)[0])
    assert "COLOR_0" in doc["meshes"][0]["primitives"][0]["attributes"]


@pytest.mark.parametrize("name", ["petrolcanm", "caddy"])
def test_game_nonfinite_uvs_export_as_finite_values(gta3, name):
    import math
    import struct

    from satk.model3d.gltf import build_glb, parse_glb
    from satk.model3d.obj import build_obj

    sc, mats = _load(gta3, name, [], None)
    assert any(not math.isfinite(v) for mesh in sc.meshes for uv in mesh.uv for v in uv)
    doc, binary = parse_glb(build_glb(sc, mats)[0])
    for mesh in doc["meshes"]:
        for primitive in mesh["primitives"]:
            for attr, idx in primitive["attributes"].items():
                if attr.startswith("TEXCOORD_"):
                    acc = doc["accessors"][idx]
                    view = doc["bufferViews"][acc["bufferView"]]
                    uv = struct.unpack_from(f"<{acc['count'] * 2}f", binary, view["byteOffset"])
                    assert all(math.isfinite(v) for v in uv)
    text = build_obj(sc, mats, name)[0]
    assert all(math.isfinite(float(v)) for ln in text.splitlines() if ln.startswith("vt ") for v in ln.split()[1:])


def test_soft_preview_infernus_speed_cover_determinism(gta3):
    from satk.model3d import softrender as SR

    sc, mats = _load(gta3, "infernus", ["infernus", "vehicle"], "cars")
    t0 = time.perf_counter()
    prep = SR.prepare(sc, mats)
    imgs, cover = SR.render(prep, SR.view_angles(4), 384)
    png = SR.sheet_png(imgs)
    dt = time.perf_counter() - t0
    assert dt < 3.0, dt                                                # acceptance 4: <= 3 s
    assert all(c >= 0.05 for c in cover), cover                        # >= 5 % non-background per view
    assert png == SR.sheet_png(SR.render(SR.prepare(sc, mats), SR.view_angles(4), 384)[0])


def test_ped_stands_upright(gta3):
    from satk.model3d import softrender as SR

    sc, mats = _load(gta3, "bmycr", ["bmycr"], "peds")
    prep = SR.prepare(sc, mats)
    ext = prep.P.max(axis=0) - prep.P.min(axis=0)
    assert ext[2] > 1.5 and ext[2] > 2 * max(ext[0], ext[1])           # tall along +Z


# ----------------------------------------------------------------------------- through the real index
@pytest.fixture
def real_index(satk_home):
    if not INDEX.is_file():
        pytest.skip(f"no real index {INDEX} (satk index build)")
    from satk.index.api import IndexDB, override_index

    db = IndexDB("vanilla", INDEX)
    with override_index(db):
        yield db
    db.close()


def test_model_image_and_raw_index(real_index, run_cli):
    from satk.model3d.png import png_size

    j = run_cli(["model", "image", "model:411", "--backend", "soft", "--json"]).json
    assert j["ok"] and png_size(Path(j["file"]).read_bytes()) == (768, 768)
    assert j["stats"]["tris"] == 3072 and all(c >= 0.05 for c in j["stats"]["cover"])
    for sid in ("model:17613", "model:300"):
        r = run_cli(["model", "image", sid, "--backend", "soft", "--json"])
        assert r.code == 0, r.out
    j = run_cli(["asset", "export", "model:411", "--format", "raw", "--json"]).json
    assert [Path(f).name for f in j["files"]] == ["infernus.dff", "infernus.txd", "vehicle.txd"]
    assert Path(j["files"][0]).parent.name == "vanilla" and Path(j["files"][0]).parent.parent.name == "raw"
    j = run_cli(["asset", "export", "model:17613", "--format", "glb", "--json"]).json
    assert j["stats"]["tris"] == 225


@pytest.mark.slow
def test_sample_of_models_renders(real_index):
    """Every 50th model with a DFF renders without errors (the full run is ``satk model image --all``)."""
    from satk.model3d import api
    from satk.model3d.resolve import list_models

    models = list_models(real_index)[::50]
    errors = []
    for mid, name, _sec, _txd in models:
        try:
            api.image(f"model:{mid}", 1, 64, "soft", db=real_index)
        except Exception as e:  # noqa: BLE001
            errors.append((mid, name, str(e)))
    assert len(errors) <= max(1, len(models) // 200), errors[:10]
