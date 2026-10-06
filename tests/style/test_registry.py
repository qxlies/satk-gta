"""satk.style.registry: one definition per canonical metric, consistent with mesh_metrics."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from satk.style import metrics as SM
from satk.style import registry as R

#: Names the plan freezes for contract K1 (mesh_metrics must return them when defined).
K1 = ("shade.normal_bend", "shade.flat_share", "shade.hard_at_seam", "geo.tris", "geo.pieces",
      "geo.largest_piece_share", "geo.sliver_share", "geo.median_dihedral", "uv.zero_area_share",
      "dff.verts_per_tri", "bbox")
#: Families the registry must define (section 1 of the metrics synthesis).
FAMILIES = ("veh.", "file.", "part.", "dam.", "shade.", "geo.", "uv.", "tex.", "light.", "col.", "dims.")


def test_k1_keys_are_registered_and_computed():
    assert set(K1) <= set(SM.KEYS)
    computed = {n for n, m in R.REGISTRY.items() if m.computed_by == "mesh_metrics"}
    assert computed == set(SM.KEYS)


@pytest.mark.parametrize("name", list(R.REGISTRY))
def test_every_metric_is_complete_and_english(name):
    m = R.REGISTRY[name]
    assert m.name == name and m.unit in R.UNITS and m.scope in R.SCOPES
    assert len(m.definition) >= 12 and m.definition.isascii() and m.definition.endswith(".")
    assert m.computed_by in ("", "mesh_metrics", "measure", "texture", "check")


def test_families_and_named_metrics():
    names = set(R.REGISTRY)
    for fam in FAMILIES:
        assert any(n.startswith(fam) for n in names), fam
    for n in ("veh.hd_tris", "veh.hi_tris", "dims.L", "dims.W", "dims.H", "dims.wheelbase", "dims.track",
              "dims.wheel_d", "tex.colours15", "tex.lum_std", "light.night_tint", "col.face_light_dominant"):
        assert n in names, n
    assert "wheel mesh once" in R.REGISTRY["veh.hd_tris"].definition
    assert "4 on cars" in R.REGISTRY["veh.hi_tris"].definition


def _sci(x: float) -> str:
    """``1e-4`` style, as the definitions spell small tolerances."""
    m, e = f"{x:e}".split("e")
    return f"{float(m):g}e{int(e)}"


def test_definitions_match_metric_constants():
    d = {n: m.definition for n, m in R.REGISTRY.items()}
    assert f"{SM.FLAT_DEG:g} deg" in d["shade.flat_share"]
    assert f"{SM.HARD_DEG:g} deg" in d["shade.hard_edge_share"]
    assert _sci(SM.SEAM_UV) in d["shade.hard_at_seam"]
    assert f"{SM.SLIVER_DEG:g} deg" in d["geo.sliver_share"]
    assert _sci(SM.UV_ZERO) in d["uv.zero_area_share"]
    assert f"{_sci(SM.WELD_M)} m" in R.CONVENTIONS["weld"]
    bins = ", ".join(f"{int(a)}-{int(b)}" for a, b in zip(SM.DIHEDRAL_BINS, SM.DIHEDRAL_BINS[1:]))
    assert bins in d["shade.hard_by_dihedral"]


def test_lookup_and_rows():
    assert R.lookup("part.tris[chassis]") is R.REGISTRY["part.tris[<frame>]"]
    assert R.lookup("dam.ok_ratio[door_lf]").unit == "ratio"
    assert R.lookup("shade.normal_bend").unit == "deg"
    assert R.lookup("nope") is None and R.lookup("nope[x]") is None
    mesh = R.rows("mesh")
    assert {r[0] for r in mesh} >= set(SM.KEYS) and all(len(r) == 4 for r in R.rows())


def test_registry_is_pure_data():
    src = Path(R.__file__).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom):
            assert node.module in ("__future__", "dataclasses") and not node.level
        assert not isinstance(node, ast.Import)
