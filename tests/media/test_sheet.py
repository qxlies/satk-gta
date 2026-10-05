"""Contact sheets (SPEC §4.4; WP-04 acceptance 3, 4)."""

from __future__ import annotations

import pytest

from satk.core.errors import SatkError
from satk.media import font
from satk.media import sheet as S
from satk.media.texture import resolve_refs


def _sids(db, n: int) -> list[str]:
    rows = db.query("SELECT t.name, x.name FROM texture x JOIN txd t ON t.id = x.txd_id ORDER BY x.id", limit=500)
    return [f"tex:{a}/{b}" for a, b in rows["rows"]][:n]


def _assert_number(mh, rgba: bytes, w: int, cell_xy: tuple[int, int], n: int, scale: int = 2) -> None:
    """The badge in the cell corner shows exactly the digits of ``n`` (white ink on black)."""
    cx, cy = cell_xy
    tx, ty = cx + scale + 1, cy + scale + 1  # Painter.badge: pad = scale, 1 px border
    mw, mh_, mask = font.text_mask(str(n), scale)
    for y in range(mh_):
        for x in range(mw):
            got = mh.px(rgba, w, tx + x, ty + y)[:3]
            want = (255, 255, 255) if mask[y * mw + x] else (0, 0, 0)
            assert got == want, (n, x, y, got)
    assert mh.px(rgba, w, cx, cy)[:3] == (255, 205, 40)  # badge border


def test_sheet_of_16_numbers_and_size(fake_db, mh):
    """Acceptance 4: 16 SIDs -> one PNG <= 560x560, 16 legend rows, numbers 1..16 in the cell corners."""
    sids = _sids(fake_db, 16)
    path, legend = S.contact_sheet(sids, cols=0)
    w, h, rgba = mh.png_pixels(path)
    assert w <= 560 and h <= 560 and (w, h) == (532, 532)
    assert [r[0] for r in legend] == list(range(1, 17))
    assert [r[1] for r in legend] == sids
    g = S.sheet_geometry(16, 4, 128, False)
    for i, xy in enumerate(g["cells"]):
        _assert_number(mh, rgba, w, xy, i + 1)
    # the middle of a cell shows the texture, not the badge
    cx, cy = g["cells"][0]
    assert mh.px(rgba, w, cx + 100, cy + 100)[:3] not in ((0, 0, 0), (255, 255, 255))


def test_sheet_legend_text_and_cache(fake_db, mh):
    path, legend = S.contact_sheet(["txd:bistro"])
    assert legend == [[1, "tex:bistro/vent_64", "64x64 X8R8G8B8"], [2, "tex:bistro/sw_wallbrick_01", "128x128 DXT1"]]
    assert path.parent.name == "sheets" and path.parent.parent.name == "out"
    assert path.name.startswith("txd_bistro-")
    mtime = path.stat().st_mtime_ns
    again, legend2 = S.contact_sheet(["txd:bistro"])
    assert again == path and legend2 == legend and path.stat().st_mtime_ns == mtime
    other, _ = S.contact_sheet(["txd:bistro"], labels=True)
    assert other != path
    ow, oh, orgba = mh.png_pixels(other)
    assert oh == mh.png_pixels(path)[1] + 10  # one caption strip
    caption = [mh.px(orgba, ow, x, 4 + 128 + 4)[:3] for x in range(4, 120)]
    assert (225, 225, 225) in caption


def test_small_textures_enlarged_and_alpha_on_checker(satk_home, tmp_path, mh):
    from satk.index.api import override_index

    clear = mh.payload("A8R8G8B8", 32, 32, (0, 0, 0, 0))  # fully transparent dash texture
    db = mh.make_fake(tmp_path / "game", overrides={"tex:vehicle/vehicledash32": clear})
    with override_index(db):
        path, _ = S.contact_sheet(["tex:vehicle/carplate", "tex:vehicle/vehicledash32"], cols=2)
    w, h, rgba = mh.png_pixels(path)
    g = S.sheet_geometry(2, 2, 128, False)
    (x0, y0), (x1, y1) = g["cells"]
    # carplate is 16x16 -> x8 nearest = the whole 128 cell in one colour (outside the badge)
    c = mh.px(rgba, w, x0 + 127, y0 + 127)
    assert all(mh.px(rgba, w, x0 + x, y0 + y) == c for x, y in ((40, 40), (127, 30), (64, 127)))
    # transparent texture -> checkerboard squares (8 px) visible
    a, b = mh.px(rgba, w, x1 + 60, y1 + 60), mh.px(rgba, w, x1 + 68, y1 + 60)
    assert a != b and {a[:3], b[:3]} <= {(92, 92, 96), (136, 136, 140)}


def test_failed_cell_does_not_break_the_sheet(satk_home, tmp_path, mh):
    from satk.index.api import override_index

    db = mh.make_fake(tmp_path / "game", skip_txd=("vehicle",))  # loose vehicle.txd is never written
    with override_index(db):
        path, legend = S.contact_sheet(["tex:bistro/vent_64", "tex:vehicle/carplate"])
    assert path.is_file()
    assert legend[1][2].endswith("(NOT_FOUND)") and "(" not in legend[0][2]


def test_limits(fake_db):
    refs, _ = resolve_refs(fake_db, _sids(fake_db, 3))
    with pytest.raises(SatkError) as e:
        S.render_sheet(refs * 22)  # 66 > 64
    assert e.value.code == "BAD_PARAMS"
    with pytest.raises(SatkError) as e:
        S.render_sheet(refs, cell=8)
    assert e.value.code == "BAD_PARAMS"
    with pytest.raises(SatkError) as e:
        S.render_sheet([])
    assert e.value.code == "NOT_FOUND"
    with pytest.raises(SatkError):
        S.contact_sheet([])


def test_geometry_helpers():
    assert [S.auto_cols(n) for n in (1, 2, 4, 5, 15, 16, 17, 64, 100)] == [1, 2, 2, 3, 4, 4, 5, 8, 8]
    g = S.sheet_geometry(15, 4, 128, False)
    assert (g["w"], g["h"], g["rows"]) == (532, 532, 4)
    assert S.upscale_nearest(1, 1, b"\x01\x02\x03\x04", 2) == b"\x01\x02\x03\x04" * 4
    assert S.upscale_nearest(2, 1, b"ABCDabcd", 2) == b"ABCDABCDabcdabcd" * 2
