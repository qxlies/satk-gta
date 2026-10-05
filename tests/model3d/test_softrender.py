"""The ``soft`` rasterizer: coverage, z-buffer, textures, alpha, determinism."""

from __future__ import annotations

import pytest

np = pytest.importorskip("numpy")

from satk.model3d import softrender as SR  # noqa: E402
from satk.model3d.mesh import build_scene  # noqa: E402
from satk.model3d.textures import TxdChain, resolve_materials  # noqa: E402

BG = SR.DEFAULT_BG


def _prep(dff, txd=None, name="m", sec=None, colours=None):
    sc = build_scene(dff, name=name, sec=sec)
    ch = TxdChain([("t", txd)] if txd else [])
    return SR.prepare(sc, resolve_materials(sc.materials, ch, colours))


def _fg(img) -> float:
    return float((np.abs(img.astype(int) - np.array(BG)).sum(axis=2) > 0).mean())


def test_view_angles():
    assert SR.view_angles(4) == [(45.0, 25.0), (135.0, 25.0), (225.0, 25.0), (315.0, 25.0)]
    assert SR.view_angles(1, 10) == [(45.0, 10.0)]
    with pytest.raises(ValueError):
        SR.view_angles(0)


def test_box_renders_textured_and_framed(m):
    prep = _prep(m.box_dff(), m.solid_txd({"boxtex": (220, 30, 30, 255)}))
    imgs, cover = SR.render(prep, SR.view_angles(4), 96)
    assert len(imgs) == 4 and all(i.shape == (96, 96, 3) and i.dtype == np.uint8 for i in imgs)
    for img, c in zip(imgs, cover):
        assert 0.15 < _fg(img) < 0.95 and c > 0.15
        # tight framing: something within the margin band on at least two opposite sides
        ys, xs = np.nonzero((np.abs(img.astype(int) - np.array(BG)).sum(axis=2) > 0))
        assert min(xs.min(), ys.min()) <= 8 and max(xs.max(), ys.max()) >= 87
        fg = img[(np.abs(img.astype(int) - np.array(BG)).sum(axis=2) > 0)]
        assert (fg[:, 0].astype(int) > fg[:, 1].astype(int) + 40).mean() > 0.9   # red texture dominates


def test_deterministic_bytes(m):
    a = SR.sheet_png(SR.render(_prep(m.car_dff(), sec="cars"), SR.view_angles(4), 64)[0])
    b = SR.sheet_png(SR.render(_prep(m.car_dff(), sec="cars"), SR.view_angles(4), 64)[0])
    assert a == b


def test_zbuffer_front_quad_hides_back_quad(m):
    # two parallel quads facing the camera at az=0/el=0: red in front (y=+1), blue behind (y=-1)
    def quad(y, w):
        return [(-w, y, -w), (w, y, -w), (w, y, w), (-w, y, w)]
    pos = quad(1.0, 1.0) + quad(-1.0, 2.0)
    tris = [(0, 1, 2, 0), (0, 2, 3, 0), (4, 5, 6, 1), (4, 6, 7, 1)]
    g = m.geometry(pos, tris, mats=[m.material((255, 0, 0, 255)), m.material((0, 0, 255, 255))])
    prep = _prep(m.clump([g], [(-1, "q", (0, 0, 0))], [(0, 0)]))
    img = SR.render(prep, [(0.0, 0.0)], 64)[0][0]
    c = img[32, 32].astype(int)
    assert c[0] > 100 and c[2] < 30                     # centre: the red front quad wins
    corner = img[4, 4].astype(int)
    assert corner[2] > 100 and corner[0] < 30           # corners: only the bigger blue quad


def test_alpha_clip_and_translucent_glass(m):
    pos = [(-1, 0, -1), (1, 0, -1), (1, 0, 1), (-1, 0, 1)]
    uv = [(0, 1), (1, 1), (1, 0), (0, 0)]
    tris = [(0, 1, 2, 0), (0, 2, 3, 0)]
    alpha = [0, 0, 255, 255] * 4                       # left half of every row transparent
    txd = m.solid_txd({"leaf": (0, 255, 0, 255)}, alpha={"leaf": alpha})
    g = m.geometry(pos, tris, uv=uv, mats=[m.material((255, 255, 255, 255), "leaf")])
    prep = _prep(m.clump([g], [(-1, "leaf", (0, 0, 0))], [(0, 0)]), txd)
    img = SR.render(prep, [(0.0, 0.0)], 64)[0][0]
    # camera on +Y looking at -Y: image left = world +X = u 1 (opaque), image right = u 0 (alpha 0)
    assert tuple(int(c) for c in img[32, 52]) == BG     # clipped texels show the background
    assert img[32, 12, 1] > 150 and img[32, 12, 0] < 60
    g2 = m.geometry(pos, tris, mats=[m.material((255, 0, 0, 128))])
    prep2 = _prep(m.clump([g2], [(-1, "glass", (0, 0, 0))], [(0, 0)]))
    px = SR.render(prep2, [(0.0, 0.0)], 64)[0][0][32, 32].astype(int)
    assert px[0] > BG[0] - 10 and px[1] < BG[1] - 40   # red blended over the background, not opaque
    assert px[1] > 40


def test_prelit_darkens_and_missing_textures_reported(m):
    bright = _prep(m.box_dff(tex="gone", prelit=(255, 255, 255, 255)))
    dark = _prep(m.box_dff(tex="gone", prelit=(60, 60, 60, 255)))
    assert bright.tex_missing == ["gone"]
    a = SR.render(bright, [(45.0, 25.0)], 64)[0][0].astype(int)
    b = SR.render(dark, [(45.0, 25.0)], 64)[0][0].astype(int)
    assert a[32, 32].sum() > b[32, 32].sum() + 150


def test_vehicle_preview_hides_damage_parts(m):
    prep = _prep(m.car_dff(), m.solid_txd({"paint": (255, 255, 255, 255)}), sec="cars", colours={1: (0, 200, 0)})
    img = SR.render(prep, SR.view_angles(1), 128)[0][0].astype(int)
    red = (img[:, :, 0] > 150) & (img[:, :, 1] < 60) & (img[:, :, 2] < 60)
    blue = (img[:, :, 2] > 150) & (img[:, :, 0] < 60)
    green = (img[:, :, 1] > 120) & (img[:, :, 0] < 80)
    assert not red.any() and not blue.any()             # bonnet_dam (red) and chassis_vlo (blue) hidden
    assert green.mean() > 0.05                          # paint slot 1 recoloured green


def test_empty_model_gives_placeholder(m):
    pos = [(0, 0, 0), (1, 0, 0), (2, 0, 0)]
    g = m.geometry(pos, [])
    prep = _prep(m.clump([g], [(-1, "e", (0, 0, 0))], [(0, 0)]))
    assert prep.tris == 0
    imgs, cover = SR.render(prep, SR.view_angles(2), 32)
    assert cover == [0.0, 0.0] and imgs[0].shape == (32, 32, 3)


def test_sheet_layout(m):
    imgs = [np.full((10, 10, 3), i * 40, dtype=np.uint8) for i in range(4)]
    sh = SR.sheet(imgs)
    assert sh.shape == (20, 20, 3)
    assert tuple(sh[2, 2]) == (0, 0, 0) and tuple(sh[2, 12]) == (40, 40, 40) and tuple(sh[12, 2]) == (80, 80, 80)
    assert SR.sheet(imgs[:3], 3).shape == (10, 30, 3)
    png = SR.sheet_png(imgs)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_non_finite_uvs_do_not_crash(m):
    """A few game models carry NaN/inf UVs (model:518, 1650, 3412 in vanilla): sample texel 0, never raise."""
    nan, inf = float("nan"), float("inf")
    pos, tris, uv, nrm = m.box()
    uv = [(nan, 0.5) if i % 3 == 0 else ((inf, -inf) if i % 3 == 1 else t) for i, t in enumerate(uv)]
    g = m.geometry(pos, tris, uv=uv, normals=nrm, mats=[m.material((255, 255, 255, 255), "t")])
    prep = _prep(m.clump([g], [(-1, "nan", (0, 0, 0))], [(0, 0)]), m.solid_txd({"t": (10, 200, 10, 255)}))
    imgs, cover = SR.render(prep, SR.view_angles(2), 48)
    assert all(c > 0.1 for c in cover)
    pos[0] = (nan, 0.0, 0.0)                            # a NaN vertex: its triangles are dropped
    g = m.geometry(pos, tris, uv=uv, normals=nrm, mats=[m.material((255, 255, 255, 255), "t")])
    prep = _prep(m.clump([g], [(-1, "nan", (0, 0, 0))], [(0, 0)]), m.solid_txd({"t": (10, 200, 10, 255)}))
    assert prep.tris == 12 - 2
    assert all(c > 0.1 for c in SR.render(prep, SR.view_angles(2), 48)[1])
