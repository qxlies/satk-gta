"""Reference photos (``ref.import``, ``ref.board``): described, not measured. Fast, no Blender."""

from __future__ import annotations

import pytest

from satk.core.errors import SatkError
from satk.core.registry import get_op


def _photo(path, size=(1200, 600), colour=(200, 200, 205)):
    from PIL import Image, ImageDraw

    im = Image.new("RGB", size, colour)
    d = ImageDraw.Draw(im)
    d.rectangle([size[0] // 8, size[1] // 3, size[0] * 7 // 8, size[1] * 5 // 6], fill=(40, 60, 120))
    im.save(path, "JPEG")
    return path


def test_import_has_no_grid_by_default_and_a_describe_hint(satk_home):
    src = _photo(satk_home / "street.jpg")
    r = get_op("ref.import").call({"photo": str(src), "view": "3q"})
    assert "grid" not in r and r["view"] == "3q"
    assert "describe, do not measure" in r["next"] and "features.md" in r["next"] and "references.md" in r["next"]
    side = get_op("ref.import").call({"photo": str(_photo(satk_home / "side.jpg", colour=(220, 220, 220))),
                                      "view": "side"})
    assert "ref.plane" in side["next"] and "\"length\"" in side["next"] and "--ref" in side["next"]
    unknown = get_op("ref.import").call({"photo": str(_photo(satk_home / "x.jpg", colour=(10, 10, 10)))})
    assert "--view" in unknown["next"] and "view" not in unknown


def test_grid_is_opt_in_and_the_view_is_remembered(satk_home):
    from PIL import Image

    src = _photo(satk_home / "elev.jpg")
    r = get_op("ref.import").call({"photo": str(src), "view": "side", "grid": True, "grid_step": 50})
    assert Image.open(r["grid"]).size == Image.open(r["image"]).size and r["grid_step_px"] == 50
    again = get_op("ref.import").call({"photo": str(src)})                 # the view comes from the sidecar
    assert again["reused"] is True and again["view"] == "side" and "grid" not in again
    with pytest.raises(SatkError):
        get_op("ref.import").call({"photo": str(src), "view": "isometric"})


def test_board_puts_the_references_on_one_sheet(satk_home):
    from PIL import Image

    for i, view in enumerate(("side", "front", "3q")):
        get_op("ref.import").call({"photo": str(_photo(satk_home / f"p{i}.jpg", colour=(200, 190 + i, 180))),
                                   "view": view})
    b = get_op("ref.board").call({})
    im = Image.open(b["file"])
    assert im.width * im.height <= 1_050_000 and len(b["rows"]) == 3
    assert sorted(r[1] for r in b["rows"]) == ["3q", "front", "side"]
    assert get_op("ref.board").call({})["file"] == b["file"]               # deterministic: the same board
    with pytest.raises(SatkError):
        get_op("ref.board").call({"images": [str(satk_home / "nope.jpg")]})
