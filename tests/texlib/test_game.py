"""Acceptance against the real vanilla index and the public style.texture operation."""

from __future__ import annotations

from pathlib import Path

import pytest

from satk.core import config
from satk.texlib.vanilla import export_plan, search


@pytest.mark.game
def test_real_vanilla_brick_references_are_usable():
    from satk.index.api import open_index

    db = open_index("vanilla")
    result = search("brick", role="wall", limit=20)
    assert result["total"] >= 20 and result["n"] == 20
    for row in result["rows"]:
        assert "brick" in row[3] or "brck" in row[3]
        assert db.texture_ref(row[0]).sid == row[0]
        plan = export_plan([row[4]])
        assert plan["ide_txd"] == row[2] and plan["pack_txd"] is False


@pytest.mark.game
@pytest.mark.blender
@pytest.mark.slow
def test_all_presets_pass_public_style_texture_on_real_vanilla(tmp_path, monkeypatch):
    from satk.style import texture as T
    from satk.style.ops import style_texture
    from satk.texlib.build import make
    from satk.texlib.catalog import catalog

    # Load the actual index-derived distributions before redirecting outputs.
    dist = T.vanilla("vanilla")
    directory = tmp_path / "work"
    directory.mkdir()
    with monkeypatch.context() as mp:
        mp.setenv("SATK_PATHS_WORK", str(directory))
        config.reset()
        # The public operation still uses the measured distributions, never the shipped recipe bands.
        mp.setattr(T, "vanilla", lambda profile="vanilla": dist)
        try:
            for preset in catalog()["presets"]:
                result = make(preset, size=256)
                for file in ("file", "dxt_preview"):
                    report = style_texture(result[file], role=result["role"], full=True)
                    assert report["out"] == 0, (preset, file, report)
                    assert report["n"] == 4
                assert Path(result["file"]).is_relative_to(directory)
        finally:
            config.reset()
    config.reset()
