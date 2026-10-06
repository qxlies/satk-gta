"""The texture fitter preserves alpha and reports the actual compressed pixels deterministically."""

from __future__ import annotations

import numpy as np
from PIL import Image

from satk.convert.finish import _roundtrip, fit
from satk.style.texture import texture_stats


def test_finish_preserves_alpha_and_matches_dxt_measurements(satk_home):
    folder = satk_home / "work"
    y, x = np.mgrid[:128, :128]
    raw = np.empty((128, 128, 4), dtype=np.uint8)
    raw[..., 0] = 50 + x // 3
    raw[..., 1] = 47 + x // 3
    raw[..., 2] = 45 + x // 3
    raw[..., 3] = np.where(y < 32, 0, np.where(y < 64, 128, 255))
    source = folder / "source.png"
    Image.fromarray(raw).save(source)
    metrics = {"tex.colours": [158, 298, 417, 100], "tex.sat_mean": [0.028, 0.0733, 0.1934, 100],
               "tex.val_mean": [0.2087, 0.2883, 0.4289, 100], "tex.hf_energy": [19.996, 27.1065, 49.694, 100]}
    dist = {"roles": {role: {"n": 100, "metrics": metrics} for role in ("wheel", "generic")}}
    first = fit(source, folder / "first.png", "wheel", dist)
    second = fit(source, folder / "second.png", "wheel", dist)
    with Image.open(first["file"]) as image:
        actual = np.array(image.convert("RGBA"))
    assert np.array_equal(actual[..., 3], raw[..., 3])
    assert first["alpha"] is True
    assert (folder / "first.png").read_bytes() == (folder / "second.png").read_bytes()
    assert first["dxt"] == second["dxt"] == texture_stats(_roundtrip(actual), 128, 128)
    decoded = np.frombuffer(_roundtrip(actual), dtype=np.uint8).reshape(128, 128, 4)
    assert np.abs(decoded[..., 3].astype(int) - raw[..., 3]).max() <= 8
