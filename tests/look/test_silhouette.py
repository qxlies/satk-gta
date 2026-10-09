"""Silhouettes and numbered previews (``satk.look.silhouette``, ``satk.look.ops.numbered_copy``): fast, no Blender."""

from __future__ import annotations

import numpy as np
import pytest

from satk.look import silhouette as S
from satk.look.ops import numbered_copy


def _car(w: int, h: int, roof: float = 0.5) -> np.ndarray:
    """A crude side silhouette: a body box and a cabin box ``roof`` of the length long."""
    m = np.zeros((h, w), dtype=bool)
    m[h // 2:, :] = True
    a = int(w * (0.5 - roof / 2))
    m[: h // 2, a:a + int(w * roof)] = True
    return m


def test_photo_mask_finds_the_object_on_a_plain_background():
    rgb = np.full((300, 600, 3), 230, dtype=np.uint8)
    rgb[100:250, 50:550] = (40, 60, 120)
    assert S.photo_box(rgb) == (50, 100, 550, 250)
    assert S.photo_box(np.full((40, 40, 3), 200, dtype=np.uint8)) is None


def test_compare_aligns_by_the_boxes_and_reports_the_top_line():
    model = _car(400, 120)
    photo = np.zeros((500, 900), dtype=bool)
    photo[100:400, 100:800] = _car(700, 300)                     # the same shape, another scale and place
    rep = S.compare(model, photo, length_m=4.6, height_m=1.5, stations=10)
    assert rep["iou"] > 0.97 and max(abs(r[3]) for r in rep["stations"]) < 0.05
    longer = np.zeros((500, 900), dtype=bool)
    longer[100:400, 100:800] = _car(700, 300, roof=0.8)          # a longer cabin: the top line differs at the ends
    rep2 = S.compare(model, longer, length_m=4.6, height_m=1.5, stations=10)
    assert rep2["iou"] < rep["iou"] and rep2["worst"][0][3] == pytest.approx(0.75, abs=0.1)
    flipped = S.compare(model, photo[:, ::-1], length_m=4.6, height_m=1.5, stations=10, flip=True)
    assert flipped["iou"] == pytest.approx(rep["iou"], abs=0.01)
    with pytest.raises(ValueError):
        S.compare(np.zeros((10, 10), dtype=bool), photo, length_m=1, height_m=1)


def test_numbered_copies_keep_every_run(tmp_path, satk_home):
    latest = tmp_path / "preview.jpg"
    latest.write_bytes(b"one")
    a = numbered_copy(latest)
    assert a.endswith("preview-001.jpg")
    assert numbered_copy(latest) == a                           # same pixels: nothing new
    latest.write_bytes(b"two")
    b = numbered_copy(latest)
    assert b.endswith("preview-002.jpg") and (tmp_path / "preview-001.jpg").read_bytes() == b"one"
