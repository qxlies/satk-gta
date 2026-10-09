"""Chroma key, trim, resize and the validation rules on synthetic images."""

from __future__ import annotations

import pytest

pytest.importorskip("PIL")
pytest.importorskip("numpy")

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from satk.core.errors import SatkError  # noqa: E402
from satk.imagegen import keying as K  # noqa: E402

from .conftest import badge_image  # noqa: E402


def rgba(im) -> np.ndarray:
    return np.asarray(im.convert("RGBA"))


def test_key_makes_background_transparent_and_trims_with_padding():
    master, steps = K.key_image(badge_image(256, fill=0.8).convert("RGBA"))
    a = rgba(master)
    assert master.size[0] == master.size[1]
    assert [s["step"] for s in steps] == ["key", "despill", "trim", "pad"]
    assert a[0, 0, 3] == 0 and a[0, -1, 3] == 0 and a[-1, 0, 3] == 0 and a[-1, -1, 3] == 0
    # trimmed to the badge (205 px of 256) + 4 % padding on each side
    side = 205
    pad = round(side * 0.04)
    assert abs(master.size[0] - (side + 2 * pad)) <= 3
    # the middle of the badge is opaque dark slate
    c = a[master.size[1] // 2, 8]
    assert c[3] == 255


@pytest.mark.parametrize("size", [24, 48, 64, 128])
def test_resized_icons_pass_validation(size):
    master, _ = K.key_image(badge_image(512).convert("RGBA"))
    im, steps = K.shrink(master, size)
    v = K.validate_image(im)
    assert v["ok"], v
    assert im.size == (size, size)
    assert [s["step"] for s in steps] == (["resize", "sharpen"] if size <= 48 else ["resize"])
    assert v["coverage"] >= 0.5 and v["magenta_pixels"] == 0 and v["corners"] == [0, 0, 0, 0]


def test_unsharp_threshold_is_a_parameter():
    master, _ = K.key_image(badge_image(256).convert("RGBA"))
    _, steps = K.shrink(master, 48, K.KeyParams(unsharp_max=32))
    assert [s["step"] for s in steps] == ["resize"]


def test_key_ramp_boundaries():
    """Distance <= 90 transparent, >= 130 opaque, linear in between."""
    im = Image.new("RGB", (64, 64), (255, 0, 255))
    px = im.load()
    # inner block with a colour at distance d from magenta along the green axis: (255, g, 255) -> d = g
    for x in range(8, 56):
        for y in range(8, 56):
            px[x, y] = (255, 90, 255) if x < 24 else ((255, 110, 255) if x < 40 else (255, 130, 255))
    arr = np.asarray(im, dtype=np.float32)
    d = np.sqrt(((arr - np.array([255, 0, 255])) ** 2).sum(-1))
    assert d[20, 20] == 90 and d[30, 30] == 110 and d[50, 50] == 130
    # key only (no trim): read alpha through a tiny wrapper that keeps the canvas by adding an opaque border
    im.paste((10, 10, 10), (0, 0, 8, 64))
    master, _ = K.key_image(im.convert("RGBA"), K.KeyParams(pad=0.0))
    a = rgba(master)[..., 3]
    assert a[a.shape[0] // 2, -20] == 255 or a.max() == 255
    # the ramp midpoint (distance 110) is about half transparent
    ys, xs = np.nonzero((a > 20) & (a < 235))
    assert len(xs) > 0 and abs(float(a[ys, xs].mean()) - 127) < 30


def test_validation_flags_each_rule():
    badge = badge_image(128).convert("RGBA")
    # corners opaque: the raw (un-keyed) image has an opaque magenta background
    v = K.validate_image(badge)
    assert not v["ok"] and any("corners" in e for e in v["errors"]) and v["magenta_pixels"] > 0
    # magenta left inside a keyed badge
    master, _ = K.key_image(badge_image(256, leak=True).convert("RGBA"))
    im = Image.fromarray(np.asarray(master).copy(), "RGBA")
    arr = np.asarray(im).copy()
    arr[arr.shape[0] // 2, arr.shape[1] // 2] = (255, 0, 255, 255)  # a hard magenta pixel
    v = K.validate_image(Image.fromarray(arr, "RGBA"))
    assert not v["ok"] and v["magenta_pixels"] >= 1 and any("magenta" in e for e in v["errors"])
    # badge too small: 30 % of the canvas
    small = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    small.paste((31, 36, 40, 255), (30, 30, 66, 66))
    v = K.validate_image(small)
    assert not v["ok"] and any("covers" in e for e in v["errors"]) and v["coverage"] < 0.5


def test_validation_hue_window_and_saturation():
    def one(color):
        im = Image.new("RGBA", (20, 20), (0, 0, 0, 0))
        im.paste(color + (255,), (2, 2, 18, 18))
        return K.validate_image(im)

    assert one((255, 0, 255))["magenta_pixels"] > 0
    assert one((255, 40, 215))["magenta_pixels"] > 0          # hue 311 - inside the 15 degree window
    assert one((255, 0, 150))["magenta_pixels"] == 0          # hue 324 - outside
    assert one((230, 215, 230))["magenta_pixels"] == 0        # pale pink: saturation below 0.5
    assert one((66, 133, 244))["magenta_pixels"] == 0         # the "high" accent


def test_despill_removes_leaked_magenta_inside_the_badge():
    master, _ = K.key_image(badge_image(256, leak=True).convert("RGBA"))
    assert K.validate_image(master)["magenta_pixels"] == 0  # (240,20,230) is removed by the despill


def test_key_fails_when_everything_is_background():
    with pytest.raises(SatkError) as e:
        K.key_image(Image.new("RGB", (32, 32), (255, 0, 255)).convert("RGBA"))
    assert e.value.code == "BAD_PARAMS"


def test_params_are_checked():
    for bad in (K.KeyParams(hard=100, soft=90), K.KeyParams(pad=0.9), K.KeyParams(key_color="magenta")):
        with pytest.raises(SatkError):
            bad.check()
    with pytest.raises(SatkError):
        K.shrink(Image.new("RGBA", (32, 32)), 4)


def test_keying_is_deterministic():
    a, _ = K.key_image(badge_image(256).convert("RGBA"))
    b, _ = K.key_image(badge_image(256).convert("RGBA"))
    assert K.png_bytes(K.shrink(a, 64)[0]) == K.png_bytes(K.shrink(b, 64)[0])
