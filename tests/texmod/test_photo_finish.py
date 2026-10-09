"""``texture finish --photo``: the photo-like variation of vanilla textures (quiet, not dirt), synthetic images only.

The first atelier run rejected the noise finish of a clean bin paint ("harsh high-contrast noise"); its own paint
then read flat and CG-clean. ``photo`` keeps the painted structure and adds a soft tonal drift, mottling and a soft
grain, a slight hue drift and a soft light gradient, landed on the fine tonal variation of the role.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from satk.media.png import load_rgba


def _px(path) -> np.ndarray:
    w, h, rgba = load_rgba(path)
    return np.frombuffer(bytes(rgba), dtype=np.uint8).reshape(h, w, 4)


def _paint(w=128, h=128) -> np.ndarray:
    """A clean CG paint: one green, a dark panel at the top right, a row of crisp dots."""
    a = np.zeros((h, w, 4), dtype=np.uint8)
    a[..., 3] = 255
    a[..., :3] = (70, 130, 115)
    a[h // 32:h * 15 // 64, w // 2:w * 31 // 32, :3] = (30, 50, 45)
    a[h * 3 // 4, ::4, :3] = (25, 35, 30)
    return a


@pytest.fixture
def no_band(monkeypatch):
    """No vanilla index: the photo landing uses its fallback target, the band landing does nothing."""
    from satk.texmod import finish as F

    monkeypatch.setattr(F, "_role_band", lambda role, profile: ({}, ""))
    monkeypatch.setattr(F, "_photo_target", lambda role, profile, photo:
                        (F.PHOTO_FALLBACK[0] + photo * (F.PHOTO_FALLBACK[1] - F.PHOTO_FALLBACK[0]), "fallback"))
    return F


def _lum(a):
    return 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]


def test_photo_lands_the_fine_variation_and_keeps_the_paint(no_band, tmp_path, tm):
    from satk.style import texture as T

    F = no_band
    src = tm.write_png(tmp_path / "ls_bin_paint.png", _paint())
    env = F.finish(str(src), role="prop", photo=0.6, out=str(tmp_path / "f"))
    target = F.PHOTO_FALLBACK[0] + 0.6 * (F.PHOTO_FALLBACK[1] - F.PHOTO_FALLBACK[0])
    prev = _px(env["dxt_preview"])
    st = T.texture_stats(prev.tobytes(), 128, 128)
    before = T.texture_stats(_paint().tobytes(), 128, 128)
    assert abs(st["tex.tone_mid"] - target) <= F.PHOTO_TOL * target + 1e-6, (st["tex.tone_mid"], target)
    assert before["tex.flat_share"] > 0.8 and st["tex.flat_share"] < 0.1
    assert st["tex.crisp"] < before["tex.crisp"] / 3
    assert env["grime"] == 0.0 and env["photo"]["strength"] == 0.6 and env["photo"]["rows"]
    # the painted structure is kept: the panel stays dark, the colour stays green, the mean stays put
    lum = _lum(prev[..., :3].astype(float))
    assert lum[4:30, 64:124].mean() < 0.5 * lum[40:90].mean()
    g = prev[40:90, :, :3].astype(float).mean(axis=(0, 1))
    assert g[1] > g[0] and g[1] > g[2]
    assert abs(lum.mean() - _lum(_paint()[..., :3].astype(float)).mean()) < 8


def test_photo_is_deterministic_and_off_by_default(no_band, tmp_path, tm):
    F = no_band
    src = tm.write_png(tmp_path / "p.png", _paint(64, 64))
    a = F.finish(str(src), role="prop", photo=0.5, out=str(tmp_path / "a"))
    b = F.finish(str(src), role="prop", photo=0.5, out=str(tmp_path / "b"))
    assert Path(a["file"]).read_bytes() == Path(b["file"]).read_bytes()
    off = F.finish(str(src), role="prop", out=str(tmp_path / "c"))
    zero = F.finish(str(src), role="prop", photo=0.0, out=str(tmp_path / "d"))
    assert Path(off["file"]).read_bytes() == Path(zero["file"]).read_bytes() and "photo" not in off


def test_light_from_above_except_on_tiling_roles(no_band, tmp_path, tm):
    F = no_band
    flat = np.zeros((128, 128, 4), dtype=np.uint8)
    flat[..., :3] = (120, 120, 120)
    flat[..., 3] = 255
    src = tm.write_png(tmp_path / "grey.png", flat)
    prop = _lum(_px(F.finish(str(src), role="prop", photo=1.0, out=str(tmp_path / "p"))["file"])[..., :3]
                .astype(float))
    wall = _lum(_px(F.finish(str(src), role="wall", photo=1.0, out=str(tmp_path / "w"))["file"])[..., :3]
                .astype(float))
    assert prop[:32].mean() - prop[-32:].mean() > 4.0           # lighter at the top
    assert abs(wall[:32].mean() - wall[-32:].mean()) < 4.0      # a tiling wall gets no gradient (no seam)


def test_photo_cli_and_bad_values(run_cli, satk_home, tmp_path, tm):
    src = tm.write_png(tmp_path / "bin.png", _paint(64, 64))
    r = run_cli(["texture", "finish", str(src), "--photo", "0.6", "--role", "prop", "--out", str(tmp_path / "o"),
                 "--json"])
    assert r.code == 0 and r.json["ok"] and r.json["grime"] == 0.0 and r.json["photo"]["strength"] == 0.6
    bad = run_cli(["texture", "finish", str(src), "--photo", "1.5", "--json"])
    assert bad.code != 0 and bad.json["error"]["code"] == "BAD_PARAMS"
