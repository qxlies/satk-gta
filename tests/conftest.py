"""Shared pytest setup for satk (SPEC §2.2, §5.1 rule 5). Owned by WP-00.

Markers (declared in ``pyproject.toml``): ``game`` (reads gta-sa-clean / the original install,
read-only), ``viewer``, ``blender``, ``engine``, ``slow``, ``e2e``.

* ``game``/``viewer``/``blender``/``engine`` tests are skipped automatically when the thing
  they need is missing (game copy, ``ariane.exe``, ``blender.exe``, the MTA fork checkout);
  ``SATK_TEST_NO_SKIP=1`` turns the skip into a failure (for acceptance runs).
* Fixtures: ``satk_home`` (isolated workspace + config), ``run_cli`` (in-process CLI),
  ``isolated_ops`` (empty operation registry), ``clean_root`` / ``installed_root`` (game roots
  from the real config; skip if missing), ``repo_root``.

Synthetic fixtures are generated inside tests; game files are never copied into the repo.
"""

from __future__ import annotations

import io
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:  # pyproject's pythonpath does this too (also for -m unittest)
    sys.path.insert(0, str(SRC_ROOT))

from satk.core import config as _config  # noqa: E402

_MARKER_HELP = {
    "game": "reads the game files (gta-sa-clean / original install), read-only",
    "viewer": "needs the Ariane viewer",
    "blender": "needs Blender",
    "engine": "needs the MTA fork / build tools",
    "slow": "takes more than a few seconds",
    "e2e": "end-to-end scenario across packages",
}


def pytest_configure(config: pytest.Config) -> None:
    for name, text in _MARKER_HELP.items():
        config.addinivalue_line("markers", f"{name}: {text}")
    # Tool discovery (satk.core.detect) still runs, but never writes the shared work/cache.
    os.environ.setdefault("SATK_DETECT", "nocache")


def _availability() -> dict[str, str | None]:
    """marker -> skip reason (``None`` when available)."""
    try:
        c = _config.build()
    except Exception as e:  # noqa: BLE001 - a broken satk.toml must not break collection
        reason = f"satk config error: {e}"
        return {k: reason for k in ("game", "viewer", "blender", "engine")}
    game = c.paths.game
    viewer = c.paths.get("viewer")
    blender = c.paths.get("blender")
    engine = c.paths.get("engine")
    return {
        "game": None if (game / "gta_sa.exe").is_file() else f"game copy not found: {game}",
        "viewer": None if viewer and viewer.is_file() else f"viewer not built: {viewer}",
        "blender": None if blender and blender.is_file() else f"blender not found: {blender}",
        "engine": None if engine and engine.is_dir() else f"engine checkout not found: {engine}",
    }


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("SATK_TEST_NO_SKIP") == "1":
        return
    # get_closest_marker, not it.keywords: parametrize ids ("engine") are keywords too.
    needed = {m for it in items for m in ("game", "viewer", "blender", "engine") if it.get_closest_marker(m)}
    if not needed:
        return
    avail = _availability()
    for it in items:
        for m in needed:
            if it.get_closest_marker(m) is not None and avail.get(m):
                it.add_marker(pytest.mark.skip(reason=avail[m]))
                break


# --------------------------------------------------------------------------- fixtures


@pytest.fixture
def repo_root() -> Path:
    """Root of the checkout under test (``tools`` or a worktree)."""
    return REPO_ROOT


@pytest.fixture
def make_junction(tmp_path: Path):
    """Create Windows directory aliases, restricted to this test's temporary tree."""
    if os.name != "nt":
        pytest.skip("Windows junctions")
    import _winapi

    def make(link: Path, target: Path, *, literal: bool = False) -> Path:
        assert link.absolute().is_relative_to(tmp_path.absolute())
        assert target.resolve().is_relative_to(tmp_path.resolve())
        name = "\\\\?\\" + str(link) if literal else str(link)
        _winapi.CreateJunction(str(target), name)
        return link

    return make


@pytest.fixture
def satk_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> Path:
    """Isolated workspace: ``SATK_HOME=<tmp>/ws``, no ``satk.toml``, fresh config cache.

    All default paths (work, game, src, ...) then live under the temp workspace; the hard
    protection of the real workspace roots (of a ``<workspace>/tools`` checkout) stays active.
    """
    ws = tmp_path / "ws"
    (ws / "work").mkdir(parents=True)
    for k in list(os.environ):
        if k.startswith("SATK_") and k not in ("SATK_LOG", "SATK_TEST_NO_SKIP"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SATK_HOME", str(ws))
    monkeypatch.setenv("SATK_CONFIG", "none")
    _config.reset()
    request.addfinalizer(_config.reset)
    # The index cache is keyed by profile only: a database opened under an earlier test's workspace would otherwise
    # still be served here (while its temp file exists), hiding "no index" in this one.
    from satk.index import api as _index_api

    _index_api.clear_cache()
    request.addfinalizer(_index_api.clear_cache)
    return ws


@dataclass
class CliResult:
    code: int
    out: str
    err: str

    @property
    def json(self) -> Any:
        """The JSON envelope printed on stdout."""
        return json.loads(self.out)


class _Tty(io.StringIO):
    def isatty(self) -> bool:  # noqa: D401
        return True


@pytest.fixture
def run_cli() -> Callable[..., CliResult]:
    """Run the CLI in-process: ``run_cli(["version"])`` -> ``CliResult(code, out, err)``.

    stdout is not a TTY (JSON mode) unless ``tty=True``.
    """
    from satk.core.cli import main

    def run(argv: Sequence[str], *, tty: bool = False) -> CliResult:
        out = _Tty() if tty else io.StringIO()
        err = io.StringIO()
        code = main(list(argv), stdout=out, stderr=err)
        return CliResult(code, out.getvalue(), err.getvalue())

    return run


@pytest.fixture
def isolated_ops():
    """Empty operation registry for the test (the real one is restored afterwards)."""
    from satk.core.registry import isolated_registry

    with isolated_registry():
        yield


def _real_root(name: str) -> Path:
    c = _config.build()
    p = c.paths.game if name == "game" else c.paths.installed
    if not (p / "gta_sa.exe").is_file():
        reason = f"{name} root not found: {p}"
        if os.environ.get("SATK_TEST_NO_SKIP") == "1":
            pytest.fail(reason, pytrace=False)
        pytest.skip(reason)
    return p


@pytest.fixture
def clean_root() -> Path:
    """``gta-sa-clean`` from the real configuration (read-only use; skip if missing)."""
    return _real_root("game")


@pytest.fixture
def installed_root() -> Path:
    """The original install from the real configuration (read-only; skip if missing)."""
    return _real_root("installed")
