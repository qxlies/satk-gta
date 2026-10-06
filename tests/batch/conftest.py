"""Fixtures for satk.batch tests: synthetic DFF folders and an isolated registry with fake operations."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Callable

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import synth  # noqa: E402

BROKEN = synth.BROKEN  # kept here for the fixture; tests import it from ``synth``, never from ``conftest``


@pytest.fixture
def dff_dir(tmp_path: Path) -> Path:
    """``cars/car01.dff`` .. ``car50.dff``; :data:`BROKEN` are not valid DFFs."""
    d = tmp_path / "cars"
    d.mkdir()
    good = synth.dff()
    for i in range(1, 51):
        data = synth.broken_dff(BROKEN.index(i)) if i in BROKEN else good
        (d / f"car{i:02d}.dff").write_bytes(data)
    return d


@pytest.fixture
def work_files(satk_home: Path) -> Callable[[], set[str]]:
    """Snapshot of every file under the isolated workspace (dry runs must not add any)."""
    def snap() -> set[str]:
        return {p.relative_to(satk_home).as_posix() for p in satk_home.rglob("*") if p.is_file()}

    return snap


@pytest.fixture
def fake_ops(isolated_ops):
    """An empty registry with the batch operations and a helper to register fake operations.

    ``reg(name, fn, consent=None)``: ``consent=True`` marks the operation CLI-only with consent,
    ``consent=False`` CLI-only without consent.
    """
    from satk.batch import ops as batch_ops
    from satk.core.registry import op
    from satk.mcp.generic import cli_only

    importlib.reload(batch_ops)  # registers batch, batch.report, recipe.* in the isolated registry

    def reg(name: str, fn: Callable, *, consent: bool | None = None, summary: str = "fake operation") -> Callable:
        f = op(name, summary=summary, summary_ru="test", mcp=False)(fn)
        if consent is not None:
            f = cli_only("test reason", consent=consent)(f)
        return f

    return reg
