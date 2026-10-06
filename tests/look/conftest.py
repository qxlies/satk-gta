"""Fixtures of satk.look tests: the synthetic DFF/TXD builders of tests/model3d (no game files)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location("_satk_m3d_builders", Path(__file__).parents[1] / "model3d" / "conftest.py")
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)
M = _MOD.M


@pytest.fixture
def m():
    return M


@pytest.fixture
def car_files(tmp_path):
    """``<tmp>/mod/mycar.dff`` + ``mycar.txd`` (a tiny vehicle) and ``crate.dff`` with the folder's only TXD."""
    d = tmp_path / "mod"
    d.mkdir()
    (d / "mycar.dff").write_bytes(M.car_dff())
    (d / "mycar.txd").write_bytes(M.solid_txd({"paint": (200, 200, 200, 255), "tyre": (30, 30, 30, 255)}))
    return d


def png_file(path: Path, w: int, h: int, rgb) -> Path:
    from satk.media.png import encode_png

    path.write_bytes(encode_png(w, h, bytes(rgb) * (w * h), 3))
    return path


@pytest.fixture
def png():
    """``png(path, w, h, rgb)``: a solid RGB PNG file."""
    return png_file
