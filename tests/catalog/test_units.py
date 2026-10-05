"""Units of satk.catalog (M2-09): background map, thumbnails, chunk format, output directory names, zones."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from satk.catalog.collect import WORLD


def _px(img, x: float, y: float, px: int):
    """Pixel of world (x, y) on a +-3000 m map of side ``px``."""
    return img.getpixel((int((x + 3000) / 6000 * px), int((3000 - y) / 6000 * px)))


def test_worldmap_land_water_lights():
    pytest.importorskip("numpy")
    Image = pytest.importorskip("PIL.Image")
    from satk.catalog.worldmap import _WATER, render_world

    fp = [(0.0, 0.0, 400.0, 400.0, 0.0, 60.0, False),        # land tile above the sea
          (-1000.0, -1000.0, -600.0, -600.0, -40.0, -5.0, False),  # sea floor
          (1000.0, 1000.0, 1400.0, 1400.0, 0.0, 80.0, True)]   # LOD tile: not land
    fp += [(150.0 + i, 150.0, 160.0 + i, 160.0, 0.0, 5.0, False) for i in range(0, 40, 4)]  # objects -> lights
    b1 = render_world(fp, "webp", WORLD, px=600)
    assert b1 == render_world(fp, "webp", WORLD, px=600)  # deterministic
    img = Image.open(io.BytesIO(b1)).convert("RGB")
    assert img.size == (600, 600)
    water = _px(img, -800, -800, 600)
    assert all(abs(a - b) <= 6 for a, b in zip(water, _WATER))
    assert all(abs(a - b) <= 6 for a, b in zip(_px(img, 1200, 1200, 600), _WATER))
    land = _px(img, 350, 50, 600)
    assert land != water and sum(land) > sum(water) + 30
    light = _px(img, 170, 155, 600)
    assert light[0] > light[2] + 60  # orange
    png = render_world([], "png", WORLD, px=64)
    with Image.open(io.BytesIO(png)) as im:
        assert im.size == (64, 64) and im.convert("RGB").getpixel((5, 5)) == _WATER


def test_encode_thumb(tmp_path, ch):
    Image = pytest.importorskip("PIL.Image")
    from satk.catalog.thumbs import THUMB, encode_thumb

    big = ch.solid_png(tmp_path / "big.png", (10, 200, 30), size=512)
    small = ch.solid_png(tmp_path / "small.png", (200, 10, 30), size=32)
    for ext in ("webp", "png"):
        with Image.open(io.BytesIO(encode_thumb(big, ext))) as im:
            assert im.size == (THUMB, THUMB) and im.format == ext.upper()
        with Image.open(io.BytesIO(encode_thumb(small, ext))) as im:
            assert im.size == (32, 32)  # never upscaled
    assert encode_thumb(small, "png") == small.read_bytes()
    rgba = Image.new("RGBA", (300, 150), (255, 0, 0, 0))
    rgba.save(tmp_path / "alpha.png")
    with Image.open(io.BytesIO(encode_thumb(tmp_path / "alpha.png", "webp"))) as im:
        assert im.size == (128, 64) and im.mode == "RGBA"


def test_image_format():
    from satk.catalog.thumbs import image_format
    from satk.core.errors import SatkError

    assert image_format("png") == "png"
    assert image_format("auto") in ("webp", "png")
    with pytest.raises(SatkError):
        image_format("gif")


def test_js_chunk_is_deterministic_and_parses(ch, tmp_path):
    from satk.catalog.site import js

    a = js("m/3", {"b": [1, 2], "a": "Los Santos   </script>"})
    assert a == js("m/3", {"a": "Los Santos   </script>", "b": [1, 2]})
    assert a.startswith('CAT.add("m/3",{"a":')
    p = tmp_path / "x.js"
    p.write_text(a, encoding="utf-8")
    assert ch.chunk(p) == ("m/3", {"a": "Los Santos   </script>", "b": [1, 2]})


def test_out_root_names(satk_home):
    from satk.catalog.build import out_root
    from satk.core.errors import SatkError
    from satk.core.paths import cfg

    work = Path(cfg().paths.work)
    assert out_root(None, "vanilla", False) == work / "out" / "catalog"
    assert out_root(None, "samp", False) == work / "out" / "catalog-samp"
    assert out_root(None, "vanilla", True) == work / "out" / "catalog-sample"
    assert out_root("demo", "vanilla", False) == work / "out" / "demo"
    assert out_root(str(work / "x" / "y"), "vanilla", False) == work / "x" / "y"
    for bad in (str(work.parent / "z"), str(work), "..", "../z"):
        with pytest.raises(SatkError):
            out_root(bad, "vanilla", False)


def test_zone_models_counts_hd_points_in_boxes():
    from satk.catalog.collect import _zone_models

    zones = [{"box": [0.0, 0.0, -10.0, 100.0, 100.0, 50.0]}, {"box": [-1000.0, -1000.0, -500.0, 1000.0, 1000.0, 500.0]},
             {"box": [500.0, 500.0, 0.0, 600.0, 600.0, 10.0]}]
    placed = [(7, 10.0, 10.0, 0.0), (7, 20.0, 20.0, 0.0), (9, 50.0, 50.0, 49.0), (9, 50.0, 50.0, 60.0),
              (11, -900.0, 900.0, 0.0)]
    got = _zone_models(zones, placed)
    assert got == {0: [[7, 2], [9, 1]], 1: [[7, 2], [9, 2], [11, 1]]}
    assert [z["ninst"] for z in zones] == [3, 5, 0] and [z["nmodels"] for z in zones] == [2, 3, 0]


def test_remove_stale_only_listed_suffixes(satk_home, tmp_path):
    from satk.catalog.files import remove_stale, write_if_changed
    from satk.core.paths import work

    root = work("out", "cat-test")
    keep = root / "a" / "keep.js"
    assert write_if_changed(keep, "x") is True and write_if_changed(keep, "x") is False
    write_if_changed(root / "a" / "old.js", "y")
    write_if_changed(root / "b" / "c" / "old.js", "y")
    write_if_changed(root / "a" / "readme.txt", "z")
    (root / "a" / ".keep.js.1.2.3.tmp").write_bytes(b"")
    assert remove_stale(root, [keep], (".js",)) == 3
    assert keep.is_file() and (root / "a" / "readme.txt").is_file() and not (root / "b").exists()
