"""Byte-level helpers of the kit export: bounding-sphere post-pass, COL embedding/renaming, re-import diff."""

from __future__ import annotations

import math
import struct

import pytest

from satk.core.errors import SatkError
from satk.formats.dff import find_embedded_col, scan_dff
from satk.formats.col import iter_col
from satk.kit.export import clump_extensions, diff_dff, embed_col, merge_contact_faces, rename_col
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


def _clump_children(dff: bytes) -> list[int]:
    t, size, _lib = struct.unpack_from("<III", dff, 0)
    assert t == 0x10
    out, off = [], 12
    while off + 12 <= 12 + size:
        ct, cs, _cl = struct.unpack_from("<III", dff, off)
        out.append(ct)
        off += 12 + cs
    return out


def test_embed_col_merges_dragonffs_two_clump_extensions(rw):
    """Regression (invisible vehicle in game): DragonFF writes one Extension per collision and a trailing empty one;
    the old embed_col replaced the first and kept the second, so the clump had two Extensions. The written DFF must
    have exactly one clump Extension, last, holding exactly one COL plug-in: the new one."""
    plain = rw.clump([rw.box(0, 0, 0)], [(-1, "car", (0, 0, 0))], [(0, 0)])
    t, size, lib = struct.unpack_from("<III", plain, 0)
    body = plain[12:12 + size]
    assert body.endswith(struct.pack("<III", 0x03, 0, lib))               # the fixture's own empty Extension
    body = body[:-12]
    old = rename_col(rw.col3("car", [(0, 0, 0, 1.0)]), "car_col")
    ext_col = struct.pack("<III", 0x253F2FA, len(old), lib) + old
    dragonff = body + struct.pack("<III", 0x03, len(ext_col), lib) + ext_col + struct.pack("<III", 0x03, 0, lib)
    dff = struct.pack("<III", 0x10, len(dragonff), lib) + dragonff
    assert [e[3] for e in clump_extensions(dff)] == [1, 0]                # the broken layout
    new = rename_col(rw.col3("car", [(0, 0, 0, 1.0), (1, 0, 0, 0.5)]), "car_col")
    out = embed_col(dff, new)
    kids = _clump_children(out)
    assert kids.count(0x03) == 1 and kids[-1] == 0x03
    exts = clump_extensions(out)
    assert len(exts) == 1 and exts[0][3] == 1
    off, size = find_embedded_col(out)
    assert [m.spheres for m in iter_col(out[off:off + size])] == [2]
    assert scan_dff(out).atomics == 1 and len(scan_dff(out).frames) == 1
    assert embed_col(out, new) == out                                     # idempotent
    rows = {r[0]: r for r in diff_dff(dff, None, {"atomics": 1, "stem": "car", "col": {"embedded": True}})}
    assert rows["clump extensions"][3] == "diff"
    rows = {r[0]: r for r in diff_dff(out, None, {"atomics": 1, "stem": "car", "col": {"embedded": True}})}
    assert "clump extensions" not in rows


def test_contact_faces_join_a_generated_vehicle_col():
    from satk.rw import col as RC

    gen = RC.ColModel(version=3, name="car_col", spheres=[((0.0, 0.0, 0.0), 1.0, (63, 0, 0, 0))])
    RC.compute_bounds(gen)
    gen.flags = RC.canonical_flags(gen)
    kit = RC.ColModel(version=3, name="car_col", vertices=[(0, 0, 100), (128, 0, 100), (128, 128, 100), (0, 128, 100)],
                      faces=[(0, 1, 2, 63, 0), (0, 2, 3, 45, 0)])
    RC.compute_bounds(kit)
    kit.flags = RC.canonical_flags(kit)
    out, n = merge_contact_faces(RC.encode_model(gen), RC.encode_model(kit))
    m = RC.decode_model(out)
    assert n == 2 and len(m.faces) == 2 and len(m.spheres) == 1 and {f[3] for f in m.faces} == {63, 45}
    again, n2 = merge_contact_faces(out, RC.encode_model(kit))
    assert n2 == 0 and again == out                                       # a COL with faces keeps its own
