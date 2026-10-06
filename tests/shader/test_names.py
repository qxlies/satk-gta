"""Texture names, kinds and selections over the synthetic index (``satk.shader.names``)."""

from __future__ import annotations

import pytest

from satk.core.errors import SatkError
from satk.shader import names as N


def test_world_loads_universe_uses_lods_and_paint(index):
    w = N.load_world(index)
    assert {"dt_road", "vehiclegrunge256", "waterclear256", "bmyst_body", "oakleaf1", "vent_64"} <= set(w.names)
    assert w.names == sorted(w.names)
    assert w.tex_txds["waterclear256"] == {"particle"}
    assert 411 in w.uses["vehiclegrunge256"]
    assert w.lod_models == {17858}
    assert w.paint == {"vehiclegrunge256"}
    assert "oakleaf1" in w.alpha
    assert N.load_world(index) is w   # cached per file


def test_kinds_on_synthetic_data(index):
    w = N.load_world(index)
    assert N.kind_names(w, "road") == {"dt_road_stoplinea", "plaintarmac1"}   # used by world models; no sign
    assert N.kind_names(w, "tree") == {"oakleaf1", "oakbark64"}                 # IDE tree flag + names
    assert N.kind_names(w, "water") == {"waterclear256", "waterwake"}           # scope any
    assert N.kind_names(w, "glass") == {"glass_64", "ab_window"}
    assert N.kind_names(w, "vehicle_paint") == {"vehiclegrunge256"}
    assert {"infernus92wheel32", "vehiclegrunge256", "carplate"} <= N.kind_names(w, "vehicle")
    assert N.kind_names(w, "ped") == {"bmyst_body", "bmyst_head"}
    with pytest.raises(SatkError) as e:
        N.kind_names(w, "roads")
    assert e.value.code == "BAD_PARAMS" and "road" in e.value.did_you_mean


def test_kinds_json_matches_the_literal():
    import typing

    from satk.shader.ops import Kind

    assert set(typing.get_args(Kind)) == set(N.kinds())
    for k, r in N.kinds().items():
        assert r.get("about"), k
        assert r.get("scope") in ("world", "vehicle", "ped", "any"), k


def test_select_intersects_and_excludes(index):
    w = N.load_world(index)
    s = N.select(index, w, patterns=["*road*", "*tarmac*"], exclude=["*stop*"])
    assert s.names == {"plaintarmac1", "dt_road", "roadsign01_128"}
    s = N.select(index, w, kind="road", patterns=["*road*"])
    assert s.names == {"dt_road_stoplinea"}
    s = N.select(index, w, patterns=["nothing_like_this*"])
    assert not s.names and s.warn[0].startswith("NO_MATCH")
    with pytest.raises(SatkError) as e:
        N.select(index, w)
    assert e.value.code == "BAD_PARAMS"


def test_select_model_txd_and_area_with_spill(index):
    w = N.load_world(index)
    s = N.select(index, w, model="model:411")
    assert "infernus92wheel32" in s.names and s.model_ids == {411}
    s = N.select(index, w, model="infernus")
    assert "vehiclegrunge256" in s.names
    s = N.select(index, w, model="txd:bistro")
    assert s.names == {"vent_64", "sw_wallbrick_01"}
    with pytest.raises(SatkError) as e:
        N.select(index, w, model="txd:bistr")
    assert e.value.code == "NOT_FOUND" and "txd:bistro" in e.value.did_you_mean
    s = N.select(index, w, area=[2489.3, -1668.5, 30])
    assert s.names == {"dt_road_stoplinea", "plaintarmac1", "sidewgrass1", "sidewgrass2", "sidewgrass3"}
    cols, rows, spill = N.rows(w, s)
    assert cols[-1] == "inside" and all(r[-1] >= 1 for r in rows)
    with pytest.raises(SatkError):
        N.select(index, w, area=[1, 2])


def test_rows_counts_and_order(index):
    w = N.load_world(index)
    s = N.select(index, w, patterns=["oak*", "glass_64"])
    cols, rows, spill = N.rows(w, s)
    assert cols == N.ROW_COLS
    by = {r[0]: r for r in rows}
    assert by["oakleaf1"][1:] == ["vegtest", 1, 1, 3]
    assert by["glass_64"][4] == 2
    assert [r[0] for r in rows][0] in ("oakleaf1", "oakbark64")   # most placements first
    assert spill == {}


def test_material_names_without_a_txd_are_left_out(index):
    w = N.load_world(index)
    s = N.select(index, w, model="model:1235")
    assert s.names == {"roadsign01_128"} and s.warn[0].startswith("MISSING: 1 ")
