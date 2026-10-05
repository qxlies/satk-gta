"""SPEC §4.2: parsers accept ``bytes | memoryview`` and give identical results."""

from __future__ import annotations

from satk.formats.col import iter_col
from satk.formats.dff import decode_geometries, find_embedded_col, scan_dff
from satk.formats.dxt import decode_rgba, mean_rgba, preview_rgba
from satk.formats.ifp import parse_ifp
from satk.formats.txd import mip0_bytes, parse_txd


def test_memoryview_inputs(b):
    col = b.col_v23("c", 3, faces=[(0, 1, 2, 5)], verts=[(0, 0, 0)] * 3, spheres=[(0, 0, 0, 1, 2)])
    g = b.geometry([(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 1)], strips=[(0, [0, 1, 2, 3])], night=True,
                   fx2d=b.fx_entries(b.fx_light()))
    d = b.clump([g], col=col)
    mv = memoryview(d)
    assert scan_dff(mv) == scan_dff(d)
    assert [list(m.tris) for m in decode_geometries(mv)] == [list(m.tris) for m in decode_geometries(d)]
    assert find_embedded_col(mv) == find_embedded_col(d)
    assert list(iter_col(memoryview(col + col))) == list(iter_col(col + col))
    a = b.anp3("p", [("w", [("r", 4, 3)])])
    k = b.anpk("q", [("a", [("r", 2, b"KRT0")])])
    assert parse_ifp(memoryview(a)) == parse_ifp(a) and parse_ifp(memoryview(k)) == parse_ifp(k)
    txd = b.txd([b.native("a")])
    t = parse_txd(txd).textures[0]
    m0 = mip0_bytes(txd, t)
    assert decode_rgba(t, memoryview(m0)) == decode_rgba(t, m0)
    assert preview_rgba(t, memoryview(m0)) == preview_rgba(t, m0)
    assert mean_rgba(t, memoryview(m0)) == mean_rgba(t, m0)
