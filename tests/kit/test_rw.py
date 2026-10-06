"""Byte-level helpers of the kit export: bounding-sphere post-pass, COL embedding/renaming, re-import diff."""

from __future__ import annotations

import math
import struct

import pytest

from satk.core.errors import SatkError
from satk.formats.dff import find_embedded_col, scan_dff
from satk.formats.col import iter_col
from satk.kit.export import diff_dff, embed_col, rename_col
from satk.kit.rwfix import fix_buffer, fix_bspheres


def _spheres(dff: bytes) -> list[tuple]:
    from satk.style.dffmesh import dff_parts

    out = []
    for p in dff_parts(dff, select="all"):
        pos = list(p["pos"])
        out.append(pos)
    return out


def _geo_spheres(dff: bytes):
    info = scan_dff(dff)
    return [g.bsphere for g in info.geoms]


def test_fix_buffer_moves_spheres_into_frame_space(rw):
    # DragonFF writes model-space centres: a part 2 m in front of the origin gets centre (0, 2, 0)
    g1 = rw.box(0.0, 0.0, 0.0, bsphere=(0.0, 2.0, 0.0, 0.1))
    g2 = rw.box(1.0, 0.0, 0.5, half=0.25, bsphere=(5.0, 5.0, 5.0, 0.01))
    dff = rw.clump([g1, g2], [(-1, "root", (0, 0, 0)), (0, "door", (0, 2, 0))], [(0, 0), (1, 1)])
    buf = bytearray(dff)
    r = fix_buffer(buf)
    assert r == {"geometries": 2, "fixed": 2, "max_move_m": pytest.approx(math.dist((5, 5, 5), (1, 0, 0.5)), abs=1e-3)}
    (x, y, z, rad), (x2, y2, z2, rad2) = _geo_spheres(bytes(buf))
    assert (x, y, z) == pytest.approx((0, 0, 0), abs=1e-6) and rad >= math.sqrt(3) * 0.5
    assert (x2, y2, z2) == pytest.approx((1.0, 0.0, 0.5), abs=1e-6) and rad2 == pytest.approx(math.sqrt(3) * 0.25, rel=1e-3)
    again = bytearray(buf)
    assert fix_buffer(again)["max_move_m"] == 0.0 and len(again) == len(dff)


def test_fix_bspheres_file_roundtrip(rw, tmp_path):
    p = tmp_path / "x.dff"
    p.write_bytes(rw.clump([rw.box(3, 0, 0, bsphere=(9, 9, 9, 0.1))], [(-1, "root", (0, 0, 0))], [(0, 0)]))
    r = fix_bspheres(str(p))
    assert r["fixed"] == 1
    assert _geo_spheres(p.read_bytes())[0][:3] == pytest.approx((3, 0, 0), abs=1e-6)


def test_embed_col_adds_replaces_and_keeps_padding(rw):
    dff = rw.clump([rw.box(0, 0, 0)], [(-1, "car", (0, 0, 0))], [(0, 0)]) + b"\0" * 100
    c1 = rw.col3("car", [(0, 0, 0, 1.0)])
    out = embed_col(dff, rename_col(c1, "car_col"))
    assert out.endswith(b"\0" * 100)
    off, size = find_embedded_col(out)
    models = list(iter_col(out[off:off + size]))
    assert [m.name for m in models] == ["car_col"] and models[0].spheres == 1
    c2 = rw.col3("other", [(0, 0, 0, 1.0), (1, 0, 0, 0.5)])
    out2 = embed_col(out, rename_col(c2, "car_col"))
    off, size = find_embedded_col(out2)
    assert [m.spheres for m in iter_col(out2[off:off + size])] == [2]
    assert scan_dff(out2).atomics == 1 and len(scan_dff(out2).frames) == 1


def test_rename_col_limits():
    with pytest.raises(SatkError):
        rename_col(b"COL3" + b"\0" * 200, "x" * 22)


def test_diff_dff_against_a_plan(rw):
    dff = rw.clump([rw.box(0, 0, 0)], [(-1, "car", (0, 0, 0)), (0, "chassis_dummy", (0, 0, 0)), (1, "chassis", (0, 0, 0))],
                   [(2, 0)], col=rw.col3("car_col", [(0, 0, 0, 1)]))
    plan = {"frames": [{"name": "car", "parent": -1}, {"name": "chassis_dummy", "parent": 0},
                       {"name": "chassis", "parent": 1}]}
    rows = {r[0]: r for r in diff_dff(dff, plan, {"atomics": 1, "stem": "car", "col": {"embedded": True}})}
    assert all(r[3] == "ok" for r in rows.values()), rows
    bad = {"frames": [{"name": "car", "parent": -1}, {"name": "door", "parent": 0}]}
    rows = {r[0]: r for r in diff_dff(dff, bad, {"atomics": 2, "stem": "car2", "col": {"embedded": True}})}
    assert rows["frames"][3] == "diff" and rows["atomics"][3] == "diff" and rows["embedded COL"][3] == "diff"
