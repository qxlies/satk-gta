"""Golden style metrics on the vanilla game (read-only; marker ``game``)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from satk.style.dffmesh import dff_metrics

pytestmark = pytest.mark.game

GOLDEN = json.loads((Path(__file__).resolve().parents[1] / "golden" / "style_vanilla.json").read_text(encoding="utf-8"))


def _read(clean_root: Path, name: str) -> bytes:
    from satk.formats.img import ImgArchive

    with ImgArchive.open(clean_root / "models" / "gta3.img") as a:
        return a.read(a.find(f"{name}.dff"))


@pytest.mark.parametrize("model", sorted(GOLDEN["mesh_metrics"]))
def test_vanilla_mesh_metrics_golden(clean_root, model):
    buf = _read(clean_root, model)
    t0 = time.perf_counter()
    r = dff_metrics(buf, per_part=True)
    dt = time.perf_counter() - t0
    m = r["metrics"]
    for key, want in GOLDEN["mesh_metrics"][model].items():
        if isinstance(want, list):
            assert abs(m[key] - want[0]) <= want[1], (key, m[key], want)
        else:
            assert m[key] == want, (key, m[key], want)
    assert not any(p.endswith(("_dam", "_vlo")) for p in r["parts"]) and "chassis" in r["parts"]
    assert r["parts"].count("wheel") == 1                    # the wheel mesh once (veh.hd_tris)
    assert sum(p["geo.tris"] for p in r["by_part"].values()) == m["geo.tris"]
    # a sedan-like length: Y extent of the model-space bbox
    assert 5.0 < m["bbox"][4] - m["bbox"][1] < 6.5
    assert dt < 5                                             # ~0.05 s on an idle machine


def test_vanilla_premier_all_parts_and_damage(clean_root):
    buf = _read(clean_root, "premier")
    hd = dff_metrics(buf)["metrics"]
    every = dff_metrics(buf, select="all")
    assert every["metrics"]["geo.tris"] > hd["geo.tris"]
    assert any(p.endswith("_dam") for p in every["parts"]) and any(p.endswith("_vlo") for p in every["parts"])
    only = dff_metrics(buf, select=lambda n: n == "chassis")
    assert only["parts"] == ["chassis"]
    with pytest.raises(ValueError):
        dff_metrics(buf, select="nope")


# ----------------------------------------------------------------------------- style cache, K2, asset.check
def _cache():
    from satk.core.errors import SatkError
    from satk.style import cache

    try:
        from satk.index.api import open_index

        open_index("vanilla").query("SELECT 1 FROM model LIMIT 1", [], limit=1)
    except SatkError as e:
        pytest.skip(f"vanilla index unavailable: {e}")
    prof, models = cache.cache_paths("vanilla")
    cold = not (prof.is_file() and models.is_file())
    t0 = time.perf_counter()
    c = cache.load("vanilla")
    return c, (time.perf_counter() - t0 if cold else None)


@pytest.mark.slow
def test_style_cache_golden_profiles_and_build_time():
    c, built = _cache()
    if built is not None:
        assert built <= GOLDEN["limits"]["build_seconds"], built
    assert c.data["n_errors"] == 0 and len(c.data["models"]) > 14000
    for key, want in GOLDEN["profiles"].items():
        p = c.peers[key]
        if "n" in want:
            assert p["n"] == want["n"], key
        for metric, val in want.items():
            if metric == "n":
                continue
            (gold, tol) = val
            got = p["metrics"][metric][:3]
            for g, x in zip(gold, got):
                assert abs(x - g) <= tol * g, (key, metric, got, gold)


@pytest.mark.slow
def test_profile_is_fast_and_small():
    from satk.core.registry import invoke
    from satk.style.profile import describe

    _cache()
    describe("car.sedan", "vanilla")
    t0 = time.perf_counter()
    for cls in ("car.sedan", "prop@1-2m", "ped", "model:426"):
        describe(cls, "sa_plus")
    assert (time.perf_counter() - t0) / 4 <= GOLDEN["limits"]["profile_seconds"]
    env = invoke("style.profile", {"target": "model:426", "tier": "sa_plus"})
    assert env["ok"] and len(json.dumps(env)) <= GOLDEN["limits"]["profile_bytes"], len(json.dumps(env))


def test_premier_reflection_frames_and_anatomy(clean_root):
    import struct

    from satk.formats.dff import scan_dff
    from satk.style.anatomy import anatomy, to_markdown
    from satk.style.subject import Subject

    buf = _read(clean_root, "premier")
    info = scan_dff(buf)
    raw = bytes(buf)
    sizes = []
    i = raw.find(struct.pack("<I", 0x253F2FC))
    while i >= 0:
        t, s, v = struct.unpack_from("<III", raw, i)
        if v == 0x1803FFFF:
            sizes.append(s)
        i = raw.find(struct.pack("<I", 0x253F2FC), i + 4)
    refl = [m for m in info.materials if "reflection" in m.effects]
    assert sizes and set(sizes) == {GOLDEN["premier"]["reflection_bytes"]} and len(refl) == len(sizes)
    assert all(m.effects["reflection"]["intensity"] >= 0 for m in refl)
    a = anatomy(Subject("model:426", "premier", buf, "index", sec="cars"))
    md = to_markdown(a)
    assert a["frames_n"] == GOLDEN["premier"]["frames"] and len(md.encode()) <= GOLDEN["premier"]["anatomy_md_max"]
    assert sum(1 for ln in md.splitlines() if ln.startswith("|") and ln[1:2].isdigit()) == GOLDEN["premier"]["frames"]


@pytest.mark.slow
def test_vanilla_premier_checks_clean():
    from satk.style import check, subject

    c, _ = _cache()
    r = check.check_subject(subject.load_sid(GOLDEN["premier"]["model"]), c, tier="vanilla")
    shown = [row for row in r["rows"] if row[6] in ("error", "warn", "low", "high")]
    assert shown == [] and r["verdict"] == "pass" and r["peer_set"] == "car.sedan"


@pytest.mark.slow
def test_leave_one_out_cars_and_props():
    from satk.style import validate

    c, _ = _cache()
    r = validate.loo(c)
    cars = [v for k, v in r["peer_sets"].items() if k.startswith("car")]
    share = sum(v["models"] * v["pass_share"] for v in cars) / sum(v["models"] for v in cars)
    assert share >= GOLDEN["loo"]["cars"], share
    props = {k: v for k, v in r["peer_sets"].items() if k.startswith("prop@") and v["models"] >= 20}
    assert props and all(v["pass_share"] >= GOLDEN["loo"]["props_per_bucket"] for v in props.values()), props


@pytest.mark.slow
def test_texture_interiors_in_band():
    from satk.style import texture

    _cache()
    d = texture.vanilla("vanilla")
    assert d["roles"]["interior"]["n"] >= 50
    assert texture.loo(d)["interior"]["in_share"] >= GOLDEN["loo"]["texture_interior"]


@pytest.mark.slow
def test_vanilla_models_of_every_class_show_no_defect():
    """Calibration (data/style/defects.json ``calibration``): vanilla shows 0 defects in every class; here three
    models per class (first, middle, last by id) and the mesh/form/fit rows of each: warnings are allowed (real
    flaws vanilla has), defects are not."""
    from satk.style import check, subject

    c, _ = _cache()
    by: dict = {}
    for mid, (_name, cls, _b) in sorted(c.data["models"].items(), key=lambda kv: int(kv[0])):
        by.setdefault(cls, []).append(int(mid))
    seen = 0
    for cls, ids in sorted(by.items()):
        for mid in sorted({ids[0], ids[len(ids) // 2], ids[-1]}):
            r = check.check_subject(subject.load_sid(f"model:{mid}"), c, w2=False)
            bad = [row[:2] + [row[7][:120]] for row in r["rows"] if row[6] == "defect"]
            assert not bad, (cls, mid, bad)
            assert "mesh" in r["sections"] and "coverage" in r["sections"], (cls, mid, r["sections"])
            seen += 1
    assert seen >= 80


def test_every_kit_kinds_exemplar_passes_strict():
    """The exemplar (``like``) of every kit kind passes ``asset.check --strict`` (a builder who matches vanilla is
    not blocked by vanilla traits: data/style/strict.json), and most vanilla peers of a few families too."""
    from satk.core.registry import invoke
    from satk.core.resources import read_json

    _cache()
    kinds = read_json("kit", "kinds.json")["kinds"]
    bad = {}
    for kind, k in sorted(kinds.items()):
        env = invoke("asset.check", {"target": k["like"], "strict": True})
        assert env["ok"], (kind, env.get("error"))
        if not env["done"]:
            bad[kind] = env["blocking"][:3]
    assert not bad, bad
    for ids, share in (([416, 426, 445, 466, 467, 492, 507, 540, 546, 547, 550, 551, 560, 562, 580, 585], 0.75),
                       ([346, 347, 348, 349, 350, 351, 352, 353, 355, 356, 357, 358, 372], 0.9)):
        done = sum(bool(invoke("asset.check", {"target": f"model:{i}", "strict": True})["done"]) for i in ids)
        assert done >= share * len(ids), (ids, done)
