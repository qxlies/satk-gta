"""Top-down maps (SPEC §4.4 ``map_image``; WP-04 acceptance 5)."""

from __future__ import annotations

import pytest

from satk.core.errors import SatkError
from satk.media import mapplot as M

# the fake index: inst:lae2_stream0#4 (lae2_roads89, COL box 63 x 48.4 m) at (2489.30, -1668.50),
# its LOD inst:lae2#198 at the same point (DFF bounding sphere r = 78 m)
X, Y = 2489.296875, -1668.5


def test_map_basic_numbers(fake_db, mh):
    path, legend, mpp = M.map_image(X, Y)
    w, h, rgba = mh.png_pixels(path)
    assert (w, h) == (768, 768)
    assert mpp == pytest.approx(0.3906, abs=1e-4)
    assert legend == [[1, "inst:lae2_stream0#4", "lae2_roads89"]]
    assert path.parent.name == "maps" and path.parent.parent.name == "out"


def test_north_up_and_footprint_position(fake_db, mh):
    """The object 60 m south of the view centre is drawn below the centre; east is right."""
    r = M.render_map(X + 40, Y + 60, span=300, px=600, labels=0)
    w, h, rgba = mh.png_pixels(r["file"])
    mpp = 300 / 600
    ox = int((X - (X + 40 - 150)) / mpp)  # 220: left of the centre (object is 40 m west)
    oy = int(((Y + 60 + 150) - Y) / mpp)  # 420: below the centre (object is 60 m south)
    inside = mh.px(rgba, w, ox + 5, oy + 5)
    bg = mh.px(rgba, w, 590, 300)
    assert inside != bg and inside[0] > inside[2]  # orange fill over the dark background
    # the COL footprint spans x -31.5..31.5 and y -24..0.4 around the object
    east_in, east_out = mh.px(rgba, w, ox + int(28 / mpp), oy + 5), mh.px(rgba, w, ox + int(36 / mpp), oy + 5)
    assert east_in[0] > east_in[2] and east_out[0] < 60
    north_out, south_in = mh.px(rgba, w, ox + 5, oy - int(4 / mpp)), mh.px(rgba, w, ox + 5, oy + int(20 / mpp))
    assert north_out[0] < 60 and south_in[0] > south_in[2]
    assert r["legend"] == [] and r["counts"] == {"inst": 1, "lod": 0, "zone": 0}
    assert r["view"] == [round(X + 40 - 150, 2), round(Y + 60 - 150, 2), round(X + 40 + 150, 2), round(Y + 60 + 150, 2)]


def test_layers_lod_only_and_area(fake_db):
    r = M.render_map(X, Y, layers=("lod",))
    assert r["legend"] == [[1, "inst:lae2#198", "lodlae2_roads89"]]
    assert r["counts"]["lod"] == 1 and r["counts"]["inst"] == 0
    r = M.render_map(X, Y, layers=("inst", "lod"))
    assert r["legend"][0][1] == "inst:lae2_stream0#4" and r["counts"] == {"inst": 1, "lod": 1, "zone": 0}
    r = M.render_map(X, Y, area=5)
    assert r["legend"] == [] and r["counts"]["inst"] == 0
    r = M.render_map(X, Y, area=-1)
    assert r["counts"]["inst"] == 1


def test_zone_layer(fake_db, mh):
    sid = fake_db._db.execute("SELECT id FROM source WHERE relpath = 'data/maps/la/lae2.ipl'").fetchone()[0]
    fake_db._db.execute("INSERT INTO zone(name, label, type, level, minx, miny, minz, maxx, maxy, maxz, source_id) "
                        "VALUES ('GAN1', 'GAN', 0, 1, 2400, -1750, -50, 2550, -1600, 200, ?)", (sid,))
    fake_db._db.execute("INSERT INTO zone(name, label, type, level, minx, miny, minz, maxx, maxy, maxz, source_id) "
                        "VALUES ('FAR', 'FAR', 0, 1, 0, 0, 0, 10, 10, 10, ?)", (sid,))
    r = M.render_map(X, Y, layers=("inst", "zone"))
    assert r["zones"] == ["GAN1"] and r["counts"]["zone"] == 1
    w, h, rgba = mh.png_pixels(r["file"])
    mpp = 300 / 768
    zx = int((2400 - (X - 150)) / mpp)  # west edge of the zone
    assert mh.px(rgba, w, zx, 384)[:3] != mh.px(rgba, w, zx - 6, 384)[:3]


def test_cache_and_params(fake_db):
    a = M.render_map(X, Y)
    mtime = __import__("os").stat(a["file"]).st_mtime_ns
    b = M.render_map(X, Y)
    assert a == b and __import__("os").stat(b["file"]).st_mtime_ns == mtime
    c = M.render_map(X, Y, labels=0)
    assert c["file"] != a["file"]
    for kw in ({"span": 5}, {"px": 32}, {"layers": ("roads",)}, {"area": 300}, {"labels": 100}):
        with pytest.raises(SatkError) as e:
            M.render_map(X, Y, **kw)
        assert e.value.code == "BAD_PARAMS", kw
    with pytest.raises(SatkError):
        M.render_map(float("nan"), Y)


def test_nice_step_and_projection():
    assert M.nice_step(300, 6) == 50 and M.nice_step(600, 6) == 100 and M.nice_step(6000, 6) == 1000
    assert M.nice_step(30, 6) == 5
    assert M.world_to_px(10.0, 0.0, (0, -10, 20, 10), 0.5) == (20.0, 20.0)
