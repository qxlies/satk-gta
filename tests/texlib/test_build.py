"""Finishing, DXT alpha, cache reuse, parameter validation and guarded outputs."""

import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from satk.core.errors import SatkError
from satk.texlib import backend, build
from satk.texlib.catalog import catalog, recipe


@pytest.fixture
def baking(satk_home, monkeypatch, synthetic_bake):
    calls = []

    def bake(r, size, seed):
        calls.append((r["name"], size, seed))
        return synthetic_bake(r, size, seed)

    monkeypatch.setattr(backend, "bake", bake)
    return calls


def test_finished_bundle_and_repeat_do_not_rebake(baking):
    a = build.make("brick", size=128, seed=42)
    data = Path(a["file"]).read_bytes()
    before = Path(a["file"]).stat().st_mtime_ns
    b = build.make("brick", size=128, seed=42)
    assert len(baking) == 1 and b["cached"] is True
    assert b["file"] == a["file"] and Path(a["file"]).stat().st_mtime_ns == before
    assert all(a["style"][k]["verdict"] == "in" for k in ("png", "dxt"))
    pack = json.loads(Path(a["manifest"]).read_text(encoding="utf-8"))
    assert pack["textures"] == [{"file": "brick.png", "name": "brick", "format": "DXT1", "levels": 8}]
    c = build.make("brick", size=128, seed=43)
    assert c["file"] != a["file"] and Path(c["file"]).read_bytes() != data


@pytest.mark.parametrize("preset", ["glass_grime", "grime_overlay", "decal_overlay"])
def test_overlays_preserve_four_bit_alpha_through_dxt3(baking, preset):
    result = build.make(preset, size=128, tint="#806848")
    with Image.open(result["file"]) as im:
        source = np.array(im)
    with Image.open(result["dxt_preview"]) as im:
        preview = np.array(im)
    assert source.shape == (128, 128, 4)
    assert result["format"] == "DXT3"
    assert np.unique(source[..., 3]).size > 2
    assert (source[..., 3] % 17 == 0).all()
    assert np.array_equal(source[..., 3], preview[..., 3])


def test_modified_cached_file_is_not_overwritten(baking):
    result = build.make("concrete", size=128)
    path = Path(result["file"])
    path.write_bytes(b"user edit")
    with pytest.raises(SatkError) as exc:
        build.make("concrete", size=128)
    assert exc.value.code == "EXISTS" and path.read_bytes() == b"user edit"
    assert len(baking) == 1


def test_cache_requires_complete_checksums_and_relocates_paths(baking, satk_home):
    result = build.make("brick", size=128)
    copied = satk_home / "work" / "copied"
    shutil.copytree(result["out"], copied)
    cache = build.make("brick", size=128, out=str(copied))
    assert cache["cached"] is True and Path(cache["file"]).parent == copied
    manifest = copied / "texlib.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["sha256"] = {}
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(SatkError, match="checksums"):
        build.make("brick", size=128, out=str(copied))
    assert len(baking) == 1


def test_different_inputs_and_unknown_files_are_not_overwritten(baking, satk_home):
    destination = satk_home / "work" / "out" / "custom"
    result = build.make("concrete", out=str(destination))
    original = hashlib.sha256(Path(result["file"]).read_bytes()).hexdigest()
    with pytest.raises(SatkError) as exc:
        build.make("concrete", seed=1, out=str(destination))
    assert exc.value.code == "EXISTS"
    assert hashlib.sha256(Path(result["file"]).read_bytes()).hexdigest() == original
    other = satk_home / "work" / "out" / "another"
    other.mkdir()
    (other / "user.txt").write_text("mine", encoding="utf-8")
    with pytest.raises(SatkError):
        build.make("brick", out=str(other))
    assert (other / "user.txt").read_text(encoding="utf-8") == "mine"
    assert len(baking) == 1


@pytest.mark.parametrize("kwargs", [{"size":64}, {"size":512}, {"size":True}, {"seed":-1},
                                     {"seed":2**32}, {"tint":"bad tint"}])
def test_bad_inputs_fail_before_baking(baking, kwargs):
    with pytest.raises(SatkError) as exc:
        build.make("brick", **kwargs)
    assert exc.value.code == "BAD_PARAMS" and not baking


def test_bad_preset_and_protected_destinations_fail_before_baking(baking, satk_home):
    with pytest.raises(SatkError):
        build.make("unknown")
    with pytest.raises(SatkError) as exc:
        build.make("brick", out=str(satk_home / "outside_work"))
    assert exc.value.code == "PROTECTED_PATH"
    with pytest.raises(SatkError) as exc:
        build.make("brick", out=str(satk_home / "src" / "readonly"))
    assert exc.value.code == "PROTECTED_PATH" and not baking


def test_list_metadata_requires_no_bake_and_ops_are_generic_only(baking, run_cli):
    from satk.core.registry import get_op

    result = run_cli(["texlib", "list", "--no-previews"])
    assert result.code == 0 and result.json["n"] == 20
    assert not baking
    assert recipe("wood-planks")["name"] == "wood_planks"
    for name in ("texlib.make", "texlib.list", "texlib.vanilla"):
        assert get_op(name).mcp is False


def test_failed_bake_does_not_publish_success(baking, monkeypatch, satk_home):
    def failure(*args, **kwargs):
        raise SatkError("EXTERNAL_TOOL", "synthetic Cycles failure")

    monkeypatch.setattr(backend, "bake", failure)
    output = satk_home / "work" / "out" / "failed"
    with pytest.raises(SatkError) as exc:
        build.make("brick", out=str(output))
    assert exc.value.code == "EXTERNAL_TOOL" and not output.exists()


def test_sheet_contains_all_previews_and_reuses_them(baking):
    result = build.listing()
    assert result["n"] == len(catalog()["presets"])
    assert len(baking) == 20 and len(result["legend"]["rows"]) == 20
    with Image.open(result["sheet"]) as sheet:
        assert sheet.width == 560 and sheet.height == 840
        assert len(sheet.getcolors(sheet.width * sheet.height)) > 300
    assert build.listing()["cached"] is True and len(baking) == 20
