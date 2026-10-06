"""Real headless Cycles at both supported sizes; outputs stay in a private workspace."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from satk.core import config
from satk.texlib.build import make
from satk.texlib.catalog import catalog

pytestmark = [pytest.mark.blender, pytest.mark.slow]


@pytest.fixture(scope="module")
def cycles_work(tmp_path_factory):
    from satk.index.api import clear_cache

    directory = tmp_path_factory.mktemp("texlib-cycles") / "work"
    directory.mkdir()
    mp = pytest.MonkeyPatch()
    mp.setenv("SATK_PATHS_WORK", str(directory))
    config.reset()
    clear_cache()
    yield directory
    clear_cache()
    mp.undo()
    config.reset()


@pytest.mark.parametrize("size", (128, 256))
@pytest.mark.parametrize("preset", sorted(catalog()["presets"]))
def test_every_recipe_bakes_and_passes_at_both_sizes(cycles_work, preset, size):
    from satk.style.texture import texture_stats

    result = make(preset, size=size)
    assert result["engine"] == "CYCLES"
    assert result["bake_seconds"] < 10
    for kind, file in (("png", "file"), ("dxt", "dxt_preview")):
        with Image.open(result[file]) as im:
            rgba = np.asarray(im.convert("RGBA"))
        assert rgba.shape == (size, size, 4)
        assert texture_stats(rgba.tobytes(), size, size) == result["stats"][kind]
        assert result["style"][kind]["verdict"] == "in"
        # Compare wrap edges with strong interior lines: mortar/roof joints also
        # occur inside the tile, so comparing with global mean gradients is wrong.
        assert max(result["seams"][kind].values()) < 1.8
    metadata = json.loads((Path(result["out"]) / "texlib.json").read_text(encoding="utf-8"))
    assert metadata["key"] == result["key"]


def test_independent_bakes_are_reproducible_and_tint_changes_hue(cycles_work):
    a = make("brick", size=128, seed=123, tint="#883322", out=str(cycles_work / "a"))
    b = make("brick", size=128, seed=123, tint="#883322", out=str(cycles_work / "b"))
    c = make("brick", size=128, seed=123, tint="#223388", out=str(cycles_work / "c"))
    assert not a["cached"] and not b["cached"]
    assert Path(a["file"]).read_bytes() == Path(b["file"]).read_bytes()
    assert Path(a["dxt_preview"]).read_bytes() == Path(b["dxt_preview"]).read_bytes()
    with Image.open(a["file"]) as im:
        red = np.asarray(im)[..., :3].mean(axis=(0, 1))
    with Image.open(c["file"]) as im:
        blue = np.asarray(im)[..., :3].mean(axis=(0, 1))
    assert red[0] > red[2] and blue[2] > blue[0]


@pytest.mark.parametrize("preset,fmt", [("brick", "DXT1"), ("grime_overlay", "DXT3")])
def test_output_packs_as_one_named_texture_with_mips(cycles_work, preset, fmt):
    from satk.formats.txd import parse_txd
    from satk.texmod.api import pack

    source = make(preset, size=256)
    result = pack(source["out"], name="texlib_test", file=preset + ".txd", out=str(cycles_work / "packed"))
    textures = parse_txd(Path(result["file"]).read_bytes()).textures
    assert len(textures) == 1
    tex = textures[0]
    assert (tex.name, tex.w, tex.h, tex.d3dfmt, tex.levels) == (preset, 256, 256, fmt, 9)
    assert tex.uaddr == tex.vaddr == 1
