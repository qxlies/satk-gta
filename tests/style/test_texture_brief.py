"""style.texture and style.brief_check on synthetic inputs (no game data)."""

from __future__ import annotations

import pytest

from satk.media.png import encode_png
from satk.style import brief as B
from satk.style import texture as T

np = pytest.importorskip("numpy")


def _flat(w=64, h=64, colours=((200, 40, 40), (40, 40, 200))) -> bytes:
    a = np.zeros((h, w, 4), dtype=np.uint8)
    a[..., 3] = 255
    for i, c in enumerate(colours):
        a[:, i * w // len(colours):(i + 1) * w // len(colours), :3] = c
    return a.tobytes()


def _photo(w=64, h=64, seed=1, base=40) -> bytes:
    rng = np.random.default_rng(seed)
    a = np.empty((h, w, 4), dtype=np.uint8)
    noise = rng.normal(0, 18, (h, w, 1)) + rng.normal(0, 4, (h, w, 3))
    a[..., :3] = np.clip(base + noise + np.array([6, 3, 0]), 0, 255)
    a[..., 3] = 255
    return a.tobytes()


def test_texture_stats_separate_vector_art_from_photo_like():
    f = T.texture_stats(_flat(), 64, 64)
    p = T.texture_stats(_photo(), 64, 64)
    assert f["tex.colours"] == 2 and f["tex.colours15"] == 2 and p["tex.colours"] > 300
    assert p["tex.hf_energy"] > 10 * max(f["tex.hf_energy"], 0.1)
    assert f["tex.sat_mean"] > p["tex.sat_mean"]
    assert 0 <= p["tex.val_mean"] <= 1 and p["w"] == 64


def test_alpha_zero_pixels_are_ignored():
    a = np.zeros((4, 4, 4), dtype=np.uint8)
    a[:2, :, :] = (255, 0, 0, 255)                     # visible red half, invisible black half
    s = T.texture_stats(a.tobytes(), 4, 4)
    assert s["tex.colours"] == 1 and s["tex.lum_mean"] == pytest.approx(0.299 * 255, abs=0.01)


@pytest.mark.parametrize("name,role", [("tesla92interior128", "interior"), ("tesla_cabin128", "interior"),
                                       ("copcarla92wheel64", "wheel"), ("tesla_aero64", "wheel"),
                                       ("decal128", "decal"), ("remapbody256", "body"), ("crate_bin", "interior")
                                       if False else ("crate64", "prop"), ("brickwall", "wall"), ("xyz", "generic")])
def test_role_of(name, role):
    assert T.role_of(name) == role


def _dist():
    # independent metrics: each one walks its range in its own order
    vals = {"tex.colours": [300 + 10 * i for i in range(40)],
            "tex.sat_mean": [0.1 + (i * 7 % 40) * 0.005 for i in range(40)],
            "tex.val_mean": [0.08 + (i * 11 % 40) * 0.004 for i in range(40)],
            "tex.hf_energy": [15.0 + (i * 17 % 40) * 0.3 for i in range(40)]}
    from satk.style.cache import percentiles

    return {"roles": {"interior": {"n": 40, "metrics": {k: percentiles(v) for k, v in vals.items()}, "values": vals},
                      "generic": {"n": 40, "metrics": {k: percentiles(v) for k, v in vals.items()}, "values": vals}}}


def test_judge_texture_and_loo():
    d = _dist()
    ok = T.judge_texture("int", {"w": 128, "h": 128, "tex.colours": 500, "tex.sat_mean": 0.2, "tex.val_mean": 0.15,
                                 "tex.hf_energy": 21.0}, d, "interior")
    assert ok["verdict"] == "in" and all(r[5] == "ok" for r in ok["rows"])
    vec = T.judge_texture("int", {"w": 128, "h": 128, "tex.colours": 20, "tex.sat_mean": 0.2, "tex.val_mean": 0.15,
                                  "tex.hf_energy": 21.0}, d, "interior")
    assert vec["verdict"] == "out" and vec["rows"][0][5] == "low"      # colours judged in log space
    bright = T.judge_texture("int", {"w": 128, "h": 128, "tex.colours": 500, "tex.sat_mean": 0.2,
                                     "tex.val_mean": 0.6, "tex.hf_energy": 21.0}, d, "interior")
    assert bright["verdict"] == "out"
    assert T.judge_texture("x", {"w": 8, "h": 8, "tex.colours": 500}, d, "wheel")["used_role"] == "generic"
    assert T.loo(d)["interior"]["in_share"] >= 0.9


def test_load_images_png_and_folder(tmp_path):
    (tmp_path / "a_interior.png").write_bytes(encode_png(64, 64, _photo()))
    (tmp_path / "b.png").write_bytes(encode_png(64, 64, _flat()))
    imgs = T.load_images(str(tmp_path))
    assert [i[0] for i in imgs] == ["a_interior", "b"] and imgs[0][1:3] == (64, 64)


BAD_BRIEF = """# Brief
- SA cars are close to real scale.
- Style: clean, slightly simplified surfaces; paint that is a flat material colour;
  painted textures with crisp shapes and light wear, never
  photographs.
- Geometry: soft shading with deliberate hard edges at panel creases.
- each `_dam` part: not more than its `_ok` part;
- Budgets: chassis + all `_ok` parts + wheel: 2,400-3,800 triangles; wheel: 120-300 triangles.
- One command rebuilds the car from nothing.
"""

GOOD_BRIEF = """# Asset brief
- Kind: automobile; tier sa_plus; like model:426.
- Shape: silhouette first; panels stay soft; split normals at seams and designed creases.
- Surfaces: own textures small and photo-like; licensed photographs may be used.
- Budgets: the tier band of the class (style.profile). Do not set triangle floors or ceilings in the brief.
"""


def test_brief_check_flags_anti_patterns():
    rows = B.check_text(BAD_BRIEF, label="b.md", p50={"veh.hd_tris": 2168, "part.tris[wheel]": 158})
    terms = [r[0] for r in rows]
    for t in ("real_scale", "clean_shapes", "flat_colour", "crisp", "no_photos", "hard_edges", "dam_lighter",
              "tri_floor", "from_nothing"):
        assert t in terms, (t, terms)
    assert terms.count("tri_floor") == 1                 # wheel floor 120 is below its p50
    assert all(r[1].startswith("b.md:") for r in rows)
    no_photo = next(r for r in rows if r[0] == "no_photos")
    assert no_photo[1] == "b.md:4"                       # the phrase wraps over two lines


def test_brief_check_clean_template():
    assert B.check_text(GOOD_BRIEF, p50={"veh.hd_tris": 2168}) == []


def test_brief_terms_are_english_with_fixes():
    for name, t in B.terms()["terms"].items():
        assert t["why"].isascii() and t["fix"].isascii() and t["ref"], name
