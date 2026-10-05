"""Entity -> SID resolution, index fallback, overlays (marks, grid, compare) and sidecars."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from satk.core.errors import SatkError
from satk.index.api import override_index
from satk.index.fake import FakeIndexDB
from satk.saap import png as P
from satk.viewer import overlays as O
from satk.viewer import sidecar as SC
from satk.viewer.resolve import IndexAccess, link_entity, src_to_sid


def test_src_to_sid():
    assert src_to_sid({"kind": "ipl_bin", "ipl": "LAE2_STREAM0", "idx": 4}) == "inst:lae2_stream0#4"
    assert src_to_sid({"kind": "ipl_text", "file": "data\\maps\\la\\LAE2.IPL", "idx": 198}) == "inst:lae2#198"
    assert src_to_sid({"kind": "runtime", "type": "object", "id": 412}) == "el:object/412"
    assert src_to_sid({"kind": "model_pos"}) is None and src_to_sid(None) is None


def test_link_entity_with_fake_index():
    with override_index(FakeIndexDB()):
        idx = IndexAccess("vanilla")
        e = {"ref": "x", "model_id": 17613, "pos": [2489.3, -1668.5, 12.3], "src": {"kind": "model_pos"}}
        assert link_entity(e, idx) == {"id": "inst:lae2_stream0#4", "link": "nearest"}
        e2 = dict(e, pos=[2489.4, -1668.5, 12.3])  # 10 cm off: outside the 5 cm tolerance
        assert link_entity(e2, idx)["link"] == "none"
        assert link_entity(dict(e, subject="inst:lae2#198"), idx) == {"id": "inst:lae2#198", "link": "exact"}
        assert link_entity(None, idx)["link"] == "none"
        assert idx.warnings == []  # an explicit fake is not a fallback


def test_link_entity_ambiguous():
    class Twin:
        def match_runtime(self, model_id, pos, tol=0.05):
            return ["inst:a#1", "inst:a#2"]

    idx = IndexAccess("vanilla", db=Twin())
    r = link_entity({"ref": "x", "model_id": 300, "pos": [0, 0, 0]}, idx)
    assert r == {"id": "inst:a#1", "link": "ambiguous", "candidates": ["inst:a#1", "inst:a#2"]}


def test_index_fallback_warns(satk_home):
    idx = IndexAccess("vanilla")  # satk_home: no index built
    assert idx.match_runtime(17613, (2489.3, -1668.5, 12.3)) == ["inst:lae2_stream0#4"]
    assert idx.fake and idx.warnings and idx.warnings[0].startswith("INDEX_MISSING")
    center, r, o = idx.locate("model:17613")
    assert o["id"] == "inst:lae2_stream0#4" and r > 10 and center[0] == pytest.approx(2489.3, abs=0.1)
    with pytest.raises(SatkError) as e:
        idx.locate("tex:a/b")
    assert e.value.code in ("UNSUPPORTED", "BAD_ID")


# --------------------------------------------------------------------------- overlays


def _png(path: Path, w: int, h: int, fill=(40, 40, 40)) -> Path:
    path.write_bytes(P.encode(w, h, bytes(fill) * (w * h)))
    return path


def test_cells():
    assert O.cell_center("A1", 800, 600) == (50.0, 50.0)
    assert O.cell_center("h6", 800, 600) == (750.0, 550.0)
    assert O.cell_of(*O.cell_center("D3", 960, 540), 960, 540) == "D3"
    for bad in ("I1", "A7", "", "D"):
        with pytest.raises(SatkError):
            O.cell_center(bad, 800, 600)


def test_marks_from_ids_and_draw(satk_home, tmp_path):
    w, h = 80, 60
    ids = bytearray(w * h * 3)
    for y in range(10, 50):            # id 1: big block, id 2: small block, id 3: absent from the legend
        for x in range(10, 50):
            ids[(y * w + x) * 3 + 2] = 1
    for y in range(20, 30):
        for x in range(60, 70):
            ids[(y * w + x) * 3 + 2] = 2
    ids[(55 * w + 75) * 3 + 2] = 3
    ip = tmp_path / "ids.png"
    ip.write_bytes(P.encode(w, h, ids))
    legend = [{"id": 1, "entity": {"ref": "a"}, "px": 0}, {"id": 2, "entity": {"ref": "b"}, "px": 0}]
    ms = O.marks_from_ids(ip, legend, 4)
    assert [m["entity"]["ref"] for m in ms] == ["a", "b"] and ms[0]["px"] == 1600
    assert 10 <= ms[0]["x"] < 50 and 20 <= ms[1]["y"] < 30
    for n, m in enumerate(ms, 1):
        m["n"] = n
    src = _png(tmp_path / "c.png", w * 10, h * 10)
    for m in ms:
        m["x"] *= 10
        m["y"] *= 10
    out = O.draw_marks(src, ms, satk_home / "work" / "m.png")
    from PIL import Image

    with Image.open(out) as im:
        im = im.convert("RGB")
        x, y = int(ms[0]["x"]), int(ms[0]["y"])
        patch = [im.getpixel((x + dx, y + dy)) for dx in range(-8, 9) for dy in range(-8, 9)]
    assert any(p[0] > 150 and p[1] < 80 for p in patch)          # red number
    assert any(min(p) > 230 for p in patch)                       # white disc


def test_grid_and_compare(satk_home, tmp_path):
    a = _png(tmp_path / "a.png", 160, 90, (10, 20, 30))
    g = O.draw_grid(a, satk_home / "work" / "g.png")
    from PIL import Image

    with Image.open(g) as im:
        assert im.size == (160, 90)
        assert im.convert("RGB").getpixel((20, 45)) != (10, 20, 30)  # grid line at x = 160/8
    same = O.compare(a, a, satk_home / "work" / "s.png")
    assert same["ssim"] == 1.0 and same["mad"] == 0.0
    b = tmp_path / "b.png"
    px = bytearray(bytes((10, 20, 30)) * (160 * 90))
    for i in range(0, len(px), 7):
        px[i] = 250
    b.write_bytes(P.encode(160, 90, bytes(px)))
    diff = O.compare(a, b, satk_home / "work" / "d.png")
    assert diff["ssim"] < 0.9 and diff["mad"] > 1
    with Image.open(diff["file"]) as im:
        assert im.size == (480, 90)
    st = O.image_stats(a)
    assert st["luma_std"] == 0 and st["black_share"] == 0


def test_marks_from_boxes_occlusion():
    boxes = [{"sid": "far", "name": "f", "rect": (0, 0, 40, 40), "depth": 100},
             {"sid": "near", "name": "n", "rect": (10, 10, 30, 30), "depth": 10}]
    ms = O.marks_from_boxes(boxes, 40, 40, 2)
    assert [m["sid"] for m in ms] == ["far", "near"]
    assert ms[0]["px"] == 1600 - 400 and ms[1]["px"] == 400


@pytest.mark.parametrize("rect", [(0, 8, 20, 12), (8, 0, 12, 20), (5, 5, 15, 15)])
def test_marks_from_boxes_anchor_is_visible(rect):
    boxes = [{"sid": "far", "rect": (0, 0, 20, 20), "depth": 10},
             {"sid": "near", "rect": rect, "depth": 1}]
    marks = O.marks_from_boxes(boxes, 20, 20, 2)
    assert len(marks) == 2
    for mark in marks:
        x, y = mark["x"], mark["y"]
        assert 0 <= x < 20 and 0 <= y < 20
        covered = rect[0] <= x < rect[2] and rect[1] <= y < rect[3]
        assert covered == (mark["sid"] == "near")


@pytest.mark.parametrize("rect", [(0, 8, 20, 12), (8, 0, 12, 20), (5, 5, 15, 15)])
def test_marks_from_ids_anchor_belongs_to_entity(tmp_path, rect):
    from PIL import Image, ImageDraw

    path = tmp_path / "synthetic_ids.png"
    with Image.new("RGB", (20, 20), (0, 0, 1)) as image:
        ImageDraw.Draw(image).rectangle((rect[0], rect[1], rect[2] - 1, rect[3] - 1), fill=(0, 0, 2))
        image.save(path)
        marks = O.marks_from_ids(path, [{"id": 1, "entity": {"ref": "far"}},
                                       {"id": 2, "entity": {"ref": "near"}}], 2)
        assert len(marks) == 2
        for mark in marks:
            assert image.getpixel((mark["x"], mark["y"])) == (0, 0, mark["id"])


# --------------------------------------------------------------------------- sidecars


def test_capture_ids_and_paths(satk_home):
    cid = SC.new_capture_id()
    p = SC.capture_paths(f"cap:{cid}")
    assert p["color"].name == f"{cid}.png" and p["color"].parent.name == cid[:8]
    SC.write(cid, {"target": "mock", "files": {"color": str(p["color"])}, "w": 1, "h": 1})
    d = SC.load(f"cap:{cid}")
    assert d["id"] == f"cap:{cid}" and d["satk_sidecar"] == 1
    assert SC.load(str(p["color"]))["id"] == d["id"]  # PNG path -> sidecar
    assert SC.find_sidecars(cid[:8])[0]["id"] == d["id"]
    with pytest.raises(SatkError) as e:
        SC.capture_paths("cap:nope")
    assert e.value.code == "BAD_ID"
    with pytest.raises(SatkError) as e:
        SC.load("cap:20000101-000000-abcd")
    assert e.value.code == "NOT_FOUND"
    bad = satk_home / "work" / "x.json"
    bad.write_text(json.dumps({"a": 1}), encoding="utf-8")
    with pytest.raises(SatkError):
        SC.load(bad)
