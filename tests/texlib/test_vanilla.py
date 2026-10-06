"""Search and export plans use real SQLite queries over synthetic records."""

import pytest

from satk.core.errors import SatkError
from satk.texlib.vanilla import export_plan, material_preset, parse_reference, search


def test_name_role_search_excludes_non_map_and_shadowed_records(texture_index):
    result = search("brick", role="wall", db=texture_index.db)
    ids = [r[0] for r in result["rows"]]
    assert len(ids) == result["total"] == 5
    assert ids[-1] == "tex:district/brick_lod"
    assert all("car/" not in sid and "inactive" not in sid and "unused" not in sid for sid in ids)
    for row in result["rows"]:
        assert row[0] == f"tex:{row[2]}/{row[3]}"
        assert row[4] == f"vanilla:{row[2]}/{row[3]}"
        assert row[10] == 1 and row[11].startswith("model:")
    assert "INDEX_STALE" in result["warn"][0]
    assert "not vehicles" in result["constraints"]["use"].lower()
    assert "load" in result["constraints"]["streaming"]


def test_colour_and_synonyms_are_index_only(texture_index):
    red = search("brick", colour="red", role="wall", db=texture_index.db)
    assert red["rows"][0][0] == "tex:district/brick_red"
    assert red["rows"][0][8:10] == ["#98483c", 0.0]
    blue = search("blue brick", db=texture_index.db)
    assert blue["rows"][0][0] == "tex:bluewalls/brick_blue"
    assert not any("missing_mean" in r[0] for r in blue["rows"])
    road = search("asphalt", role="road", db=texture_index.db)
    assert [r[0] for r in road["rows"]] == ["tex:district/asphalt_road"]


def test_wildcards_literal_sql_chars_and_empty_result(texture_index):
    assert search("br?ck_r*", db=texture_index.db)["total"] == 1
    texture_index.texture("district", "brickXred")
    assert search("brick_red", db=texture_index.db)["total"] == 1
    assert search("% ' OR 1=1 --", db=texture_index.db)["total"] == 0
    assert search("unobtainium", db=texture_index.db)["rows"] == []


def test_pagination_is_stable_and_bound_to_search(texture_index):
    full = search("brick", role="wall", db=texture_index.db)
    cursor, ids = None, []
    while True:
        page = search("brick", role="wall", limit=2, cursor=cursor, db=texture_index.db)
        ids.extend(r[0] for r in page["rows"])
        cursor = page["next"]
        if cursor is None:
            break
    assert ids == [r[0] for r in full["rows"]]
    cursor = search("brick", role="wall", limit=1, db=texture_index.db)["next"]
    with pytest.raises(SatkError, match="cursor"):
        search("asphalt", role="road", cursor=cursor, db=texture_index.db)


@pytest.mark.parametrize("kwargs", [{"query": ""}, {"query": "x", "role": "car"},
                                     {"query": "x", "colour": "#xyz123"}, {"query": "x", "cursor": "o-1"}])
def test_invalid_search_is_bad_params(texture_index, kwargs):
    with pytest.raises(SatkError) as exc:
        search(db=texture_index.db, **kwargs)
    assert exc.value.code == "BAD_PARAMS"


def test_material_and_export_plan_carry_references_only(texture_index):
    m = material_preset("Vanilla:DISTRICT/BRICK_RED", db=texture_index.db)
    assert m["shared"] is True and m["copy_pixels"] is False
    assert m["texture"] == "brick_red" and m["ide_txd"] == "district"
    assert "png" not in m and "pixels" not in m
    refs = ["vanilla:district/brick_red", "vanilla:district/brick_old"]
    plan = export_plan(refs + refs, db=texture_index.db)
    assert plan["ide_txd"] == "district" and plan["pack_txd"] is False
    assert plan["reference_only"] is True and len(plan["materials"]) == 2
    assert plan["texture_names"] == ["brick_old", "brick_red"]
    assert export_plan([]) == {"reference_only": False}


def test_parent_chain_resolves_and_detects_shadowing(texture_index):
    texture_index.txd("child", parent="district")
    texture_index.texture("child", "wall_panel")
    refs = ["vanilla:district/brick_red", "vanilla:child/wall_panel"]
    plan = export_plan(refs, db=texture_index.db)
    assert plan["ide_txd"] == "child" and plan["txd_chain"] == ["child", "district"]
    texture_index.texture("child", "brick_red")
    with pytest.raises(SatkError, match="shadowed"):
        export_plan(refs, db=texture_index.db)


def test_unrelated_dictionaries_and_own_textures_are_refused(texture_index):
    with pytest.raises(SatkError, match="unrelated TXDs"):
        export_plan(["vanilla:district/brick_red", "vanilla:bluewalls/brick_blue"], db=texture_index.db)
    with pytest.raises(SatkError, match="own packed"):
        export_plan(["vanilla:district/brick_red"], own_textures=["custom.png"], db=texture_index.db)


@pytest.mark.parametrize("asset_class", ["vehicle", "car", "vehicle_upgrade", "ped", "weapon"])
def test_vehicle_and_other_non_map_uses_are_rejected(texture_index, asset_class):
    with pytest.raises(SatkError) as exc:
        material_preset("vanilla:district/brick_red", asset_class=asset_class, db=texture_index.db)
    assert exc.value.code == "UNSUPPORTED"


def test_missing_unused_vehicle_and_cyclic_reference(texture_index):
    for ref, code in (("vanilla:district/absent", "NOT_FOUND"), ("vanilla:district/brick_unused", "UNSUPPORTED"),
                      ("vanilla:car/brick_vehicle", "UNSUPPORTED")):
        with pytest.raises(SatkError) as exc:
            material_preset(ref, db=texture_index.db)
        assert exc.value.code == code
    texture_index.sql("UPDATE txd SET parent='district',parent_via='txdp' WHERE name='district'")
    with pytest.raises(SatkError, match="cyclic"):
        export_plan(["vanilla:district/brick_red"], db=texture_index.db)


@pytest.mark.parametrize("text", ["brick", "vanilla:a", "vanilla:../brick", "vanilla:a/x@y", "vanilla:a/b/c",
                                  "vanilla:a.txd/b", "vanilla:a\\b/c", "vanilla:a/" + "x" * 32])
def test_reference_syntax_is_not_a_path(text):
    with pytest.raises(SatkError) as exc:
        parse_reference(text)
    assert exc.value.code == "BAD_PARAMS"
