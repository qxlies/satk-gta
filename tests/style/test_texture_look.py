"""The texture look (style.texture look advice, the asset.check texture section) on synthetic images, no game data.

A clean CG paint (one fill, crisp dotted lines, a small emblem: the bin of the first atelier run) is flat and too
sharp against a population of photo-like textures; a photo-like image (soft drift, light gradient, fine grain) is
not. The advice never blocks: asset.check lists it under ``advice``.
"""

from __future__ import annotations

import pytest

from satk.style import check as CK
from satk.style import subject as S
from satk.style import texture as T

np = pytest.importorskip("numpy")


def cg_paint(w=128, h=128) -> np.ndarray:
    """One flat green, a darker panel, two rows of crisp dark dots, a small cream emblem."""
    a = np.zeros((h, w, 4), dtype=np.uint8)
    a[..., 3] = 255
    a[..., :3] = (70, 130, 115)
    a[4:30, 60:124, :3] = (30, 50, 45)
    for y in (64, 100):
        a[y, ::4, :3] = (25, 35, 30)
    a[80:88, 60:66, :3] = (230, 225, 200)
    return a


def photo_like(w=128, h=128, seed=3) -> np.ndarray:
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    base = 110 + 18 * np.sin(2 * np.pi * xx / w) * np.cos(2 * np.pi * yy / h) - 20 * (yy / h - 0.5)
    lum = base + rng.normal(0, 5, (h, w))
    a = np.zeros((h, w, 4), dtype=np.uint8)
    a[..., 3] = 255
    a[..., 0] = np.clip(lum * 0.8, 0, 255)
    a[..., 1] = np.clip(lum * 1.1, 0, 255)
    a[..., 2] = np.clip(lum * 0.95 + rng.normal(0, 2, (h, w)), 0, 255)
    return a


def _stats(img) -> dict:
    h, w = img.shape[:2]
    return T.texture_stats(img.tobytes(), w, h)


def _dist(n=40) -> dict:
    """A role population of photo-like textures (different seeds) under every role used here."""
    vals: dict[str, list] = {}
    for i in range(n):
        for k, v in _stats(photo_like(seed=100 + i)).items():
            if k.startswith("tex."):
                vals.setdefault(k, []).append(v)
    from satk.style.cache import percentiles

    role = {"n": n, "metrics": {k: percentiles(v) for k, v in vals.items()}, "values": vals}
    return {"format": "satk.style-textures/1", "roles": {r: role for r in ("prop", "generic", "wall", "body")}}


def test_look_metrics_tell_cg_clean_from_photo_like():
    cg, ph = _stats(cg_paint()), _stats(photo_like())
    assert set(T.LOOK_METRICS) <= set(cg) and set(T.LOOK_METRICS) <= set(ph)
    assert cg["tex.tone_mid"] < 0.5 < 2.0 < ph["tex.tone_mid"]
    assert cg["tex.flat_share"] > 0.5 > 0.05 > ph["tex.flat_share"]
    assert cg["tex.crisp"] > 3 * ph.get("tex.crisp", 1.0)
    assert ph["tex.tone_lo"] > cg["tex.tone_lo"] and ph["tex.chroma_lo"] > 0


def test_look_advice_flat_and_sharp_only_for_the_cg_paint():
    d = _dist()
    adv = T.look_advice(_stats(cg_paint()), d, "prop")
    assert [a["look"] for a in adv] == ["flat/CG-clean", "too sharp"]
    assert "--photo" in adv[0]["fix"] and "not dirt" in adv[0]["fix"] and "vanilla prop" in adv[0]["why"]
    assert T.look_advice(_stats(photo_like(seed=7)), d, "prop") == []
    j = T.judge_texture("paint", _stats(cg_paint()), d, "prop")
    assert j["look"] == adv and {r[0] for r in j["look_rows"]} == set(T.LOOK_METRICS)
    # look rows say "advice" on the metrics behind an advice, never a band verdict (low/high count as out of band)
    assert {r[0] for r in j["look_rows"] if r[5] == "advice"} == {"tex.tone_mid", "tex.flat_share", "tex.crisp"}
    assert {r[5] for r in j["look_rows"]} <= {"advice", "ok"}


@pytest.mark.parametrize("name,cls,role", [("ls_bin_paint", "prop", "prop"), ("ls_bin_paint", "car.sedan", "body"),
                                           ("tesla92interior128", "car.sedan", "interior"),
                                           ("tesla92interior128", "prop", "prop"), ("brickwall", "building", "wall"),
                                           ("mystuff", "building", "wall"), ("mystuff", "terrain", "ground"),
                                           ("mystuff", None, "generic"), ("mygun", "weapon", "weapon")])
def test_role_for_follows_the_model_family(name, cls, role):
    assert T.role_for(name, cls) == role


def _bin_subject(tmp_path, img) -> S.Subject:
    from satk.texmod.encode import encode_level
    from satk.texmod.txdwrite import NativeSpec, native_chunk, txd_chunk

    from .rwkit import box_mesh, clump, geometry, material

    P, Tr, _N = box_mesh(0.5, 0.5, 0.9)
    g = geometry(P, [(a, b, c, 0) for a, b, c in Tr], [material(tex="ls_bin_paint")],
                 prelit=[(90, 80, 70, 255)] * len(P))
    (tmp_path / "ls_litterbin.dff").write_bytes(clump([g], [(-1, "ls_litterbin", (0, 0, 0))], [(0, 0)]))
    h, w = img.shape[:2]
    lv = (encode_level(img, "DXT1", alpha=False),)
    txd = txd_chunk([native_chunk(NativeSpec("ls_bin_paint", "DXT1", w, h, lv, False)),
                     native_chunk(NativeSpec("unused_other", "DXT1", w, h, lv, False))])
    (tmp_path / "ls_litterbin.txd").write_bytes(txd)
    return S.load_file(tmp_path / "ls_litterbin.dff")


def test_asset_check_texture_section_is_advice(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "vanilla", lambda profile="vanilla": _dist())
    subj = _bin_subject(tmp_path, cg_paint())
    rows = CK.texture_rows(subj, "prop")
    assert [(r[0], r[1], r[2], r[6], r[9]) for r in rows] == [
        ("tex.look", "ls_bin_paint", "flat/CG-clean", "warn", "texture"),
        ("tex.look", "ls_bin_paint", "too sharp", "warn", "texture")]       # the unused texture is not judged
    assert rows[0][7].startswith("flat/CG-clean (128x128, role prop)") and "never blocks" in rows[0][7]
    st = CK.strict_status(subj, {"class": "prop", "rows": rows})
    assert not any("tex.look" in b for b in st["blocking"]) and len(st["advice"]) == 2
    # a photo-like texture: one info row with the numbers, hidden by default
    pdir = tmp_path / "p"
    pdir.mkdir()
    ok = CK.texture_rows(_bin_subject(pdir, photo_like()), "prop")
    assert [(r[2], r[6]) for r in ok] == [("photo-like", "info")] and "tone_mid" in ok[0][7]


def test_texture_section_is_skipped_without_a_txd_or_an_index(tmp_path, monkeypatch):
    subj = _bin_subject(tmp_path, cg_paint())

    def no_index(profile="vanilla"):
        from satk.core.errors import SatkError

        raise SatkError("INDEX_MISSING", "no index")

    monkeypatch.setattr(T, "vanilla", no_index)
    assert CK.texture_rows(subj, "prop") is None
    subj.txd_file = None
    assert CK.texture_rows(subj, "prop") is None
    assert "texture" in CK.SECTIONS and CK.SECTIONS.index("texture") < CK.SECTIONS.index("reference")


def test_style_texture_cli_shows_the_look(run_cli, satk_home, tmp_path, monkeypatch):
    from satk.media import png as _png

    monkeypatch.setattr(T, "vanilla", lambda profile="vanilla": _dist())
    p = tmp_path / "ls_bin_paint.png"
    img = cg_paint()
    p.write_bytes(_png.encode(128, 128, img.tobytes()))
    js = run_cli(["style", "texture", str(p), "--cls", "prop"]).json
    assert js["ok"] and js["look"] == 1 and js["cols"][-1] == "look"
    assert js["rows"][0][1] == "prop" and js["rows"][0][-1] == "flat/CG-clean, too sharp"
    assert any("flat/CG-clean" in a for a in js["advice"])
    full = run_cli(["style", "texture", str(p), "--cls", "prop", "--full"]).json
    # the band rows stay the band metrics (consumers count them); the look metrics come apart, as advice
    assert {r[2] for r in full["rows"]} <= set(T.roles()["check"])
    assert {"tex.tone_mid", "tex.flat_share", "tex.crisp"} <= {r[2] for r in full["look_rows"]["rows"]}
    assert {r[7] for r in full["look_rows"]["rows"]} <= {"advice", "ok"}
