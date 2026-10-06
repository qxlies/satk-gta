from __future__ import annotations

import json
from pathlib import Path

import pytest

from satk.convert import plan
from satk.core.errors import SatkError
from satk.core.registry import get_op


def test_operations_are_generic_only():
    for name in ("asset.convert", "convert.prepare", "convert.finish"):
        spec = get_op(name)
        assert spec.mcp_name is None
        assert spec.group == "blender"
    spec = get_op("asset.convert")
    bound = spec.bind({"model": "sample.glb", "kind": "vehicle", "dims": [5.9, 2.3, 1.6]})
    assert bound["dims"] == [5.9, 2.3, 1.6]


def test_source_validation_precedes_style_work(tmp_path):
    for filename, code in (("absent.glb", "NOT_FOUND"), ("model.stl", "BAD_PARAMS")):
        with pytest.raises(SatkError) as error:
            plan.prepare(str(tmp_path / filename))
        assert error.value.code == code


@pytest.mark.parametrize("dims", [[1, 2], [1, 0, 2], [1, -1, 2], [1, float("nan"), 2], [1, float("inf"), 2]])
def test_invalid_dimensions(tmp_path, dims):
    source = tmp_path / "empty.obj"
    source.write_text("# synthetic\n", encoding="utf-8")
    with pytest.raises(SatkError, match="finite positive"):
        plan.prepare(str(source), dims=dims)


def test_gltf_dependencies_are_content_checked(tmp_path):
    source = tmp_path / "scene.gltf"
    buffer = tmp_path / "mesh data.bin"
    texture = tmp_path / "image.png"
    buffer.write_bytes(b"synthetic buffer")
    texture.write_bytes(b"synthetic image stand-in")
    source.write_text(json.dumps({"buffers": [{"uri": "mesh%20data.bin"}], "images": [{"uri": "image.png"},
                        {"uri": "data:image/png;base64,AAAA"}]}), encoding="utf-8")
    fingerprints = plan.fingerprints(plan.dependencies(source))
    assert len(fingerprints) == 3
    assert plan.unchanged(fingerprints)
    texture.write_bytes(b"edited pixels")
    assert not plan.unchanged(fingerprints)
    texture.unlink()
    assert not plan.unchanged(fingerprints)


def test_gltf_remote_or_missing_resources_are_actionable(tmp_path):
    source = tmp_path / "scene.gltf"
    for uri, code in (("https://example.invalid/model.bin", "UNSUPPORTED"), ("missing.bin", "NOT_FOUND")):
        source.write_text(json.dumps({"buffers": [{"uri": uri}]}), encoding="utf-8")
        with pytest.raises(SatkError) as error:
            plan.dependencies(source)
        assert error.value.code == code
        assert error.value.hint


def test_obj_material_library_invalidates_cache(tmp_path):
    obj = tmp_path / "mesh.obj"
    mtl = tmp_path / "paint.mtl"
    obj.write_text("mtllib paint.mtl\nv 0 0 0\n", encoding="utf-8")
    mtl.write_text("newmtl paint\nKd 1 0 0\n", encoding="utf-8")
    before = plan.fingerprints(plan.dependencies(obj))
    mtl.write_text("newmtl paint\nKd 0 0 1\n", encoding="utf-8")
    assert not plan.unchanged(before)


@pytest.mark.parametrize("declaration", ['one.mtl two.mtl', '"one.mtl" "two.mtl" # materials'])
def test_obj_tracks_multiple_material_libraries(tmp_path, declaration):
    source = tmp_path / "mesh.obj"
    source.write_text(f"mtllib\t{declaration}\n", encoding="utf-8")
    libraries = [tmp_path / name for name in ("one.mtl", "two.mtl")]
    for library in libraries:
        library.write_text("newmtl paint\n", encoding="utf-8")
    assert set(plan.dependencies(source)) == {source, *libraries}
    libraries[1].unlink()
    with pytest.raises(SatkError) as error:
        plan.dependencies(source)
    assert error.value.code == "NOT_FOUND"


def test_obj_library_name_with_spaces(tmp_path):
    source, library = tmp_path / "mesh.obj", tmp_path / "my paint.mtl"
    source.write_text("mtllib my paint.mtl\n", encoding="utf-8")
    library.write_text("newmtl paint\n", encoding="utf-8")
    assert set(plan.dependencies(source)) == {source, library}


@pytest.fixture
def prepared(satk_home, monkeypatch):
    from satk.kit import plan as kit_plan
    from satk.style import api, texture

    def bands(_target, _tier, *, metrics, **_):
        return {m: {"lo": 12, "hi": 500, "p50": 1.5 if m.startswith("dims.") else 100,
                    "peer_set": "prop@1-2m", "status": "measured"} for m in metrics}

    def template(**kw):
        return {"name": kw["name"], "dims": {"target": dict(zip("LWH", kw["dims"]))},
                "frames": [{"name": kw["name"]}]}

    monkeypatch.setattr(api, "profile", bands)
    monkeypatch.setattr(kit_plan, "template_plan", template)
    monkeypatch.setattr(texture, "vanilla", lambda _: {"roles": {}})
    source = satk_home / "source.obj"
    source.write_text("v 0 0 0\nv 1 0 0\nv 0 1 1\nf 1 2 3\n", encoding="utf-8")
    folder = satk_home / "work" / "conversion"
    data, path = plan.prepare(str(source), out=str(folder))
    return source, data, path


def test_prepare_reuses_content_and_refuses_a_different_source(prepared):
    source, first, path = prepared
    original = path.read_bytes()
    second, same_path = plan.prepare(str(source), out=str(path.parent))
    assert (second, same_path) == (first, path)
    assert path.read_bytes() == original
    source.write_text("v 4 5 6\n", encoding="utf-8")
    with pytest.raises(SatkError) as error:
        plan.prepare(str(source), out=str(path.parent))
    assert error.value.code == "EXISTS"
    assert path.read_bytes() == original


@pytest.mark.parametrize("field,value", [("name", "../escape"), ("dims", [1, -1, 1]),
                                         ("template_path", "elsewhere.json"), ("key", "not-a-key")])
def test_modified_plans_are_rejected_before_writes(prepared, field, value):
    _, data, path = prepared
    data[field] = value
    path.write_text(json.dumps(data), encoding="utf-8")
    before = set(path.parent.iterdir())
    with pytest.raises(SatkError):
        plan.load(path)
    assert set(path.parent.iterdir()) == before


def test_existing_foreign_folder_is_untouched(prepared):
    source, _, path = prepared
    other = path.parent / "foreign"
    other.mkdir()
    sentinel = other / "mine.txt"
    sentinel.write_text("keep", encoding="utf-8")
    with pytest.raises(SatkError) as error:
        plan.prepare(str(source), out=str(other))
    assert error.value.code == "EXISTS"
    assert list(other.iterdir()) == [sentinel]
    assert sentinel.read_text() == "keep"


def test_output_lock_is_exclusive_and_released(prepared):
    source, _, path = prepared
    with plan.lock(path.parent):
        with pytest.raises(SatkError) as error:
            plan.prepare(str(source), out=str(path.parent))
        assert error.value.code == "BUSY"
    assert plan.prepare(str(source), out=str(path.parent))[1] == path


def test_output_must_be_inside_work(satk_home):
    with pytest.raises(SatkError) as error:
        plan.output_dir(satk_home / "not-work")
    assert error.value.code == "PROTECTED_PATH"


@pytest.mark.parametrize("name", ["con", "LPT1", "../model", "a/b"])
def test_unsafe_model_names(name):
    with pytest.raises(SatkError):
        plan.model_name(name, "world")


def test_texture_sidecar_cannot_redirect_reads_or_export_names(prepared):
    _, data, path = prepared
    row = {"role": "prop", "size": 256, "name": "../escape", "file": str(path.parent / "unrelated.png")}
    with pytest.raises(SatkError, match="invalid texture sidecar"):
        plan.texture_rows({"key": data["key"], "textures": [row]}, data, path.parent, "textures")


@pytest.mark.parametrize("entry", [{"buffers": {}}, {"images": [7]}, {"buffers": [{"uri": 42}]}])
def test_malformed_gltf_dependencies_are_usage_errors(tmp_path, entry):
    file = tmp_path / "source.gltf"
    file.write_text(json.dumps(entry), encoding="utf-8")
    with pytest.raises(SatkError) as error:
        plan.dependencies(file)
    assert error.value.code == "BAD_PARAMS"
