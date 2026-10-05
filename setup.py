"""Build hook of satk: ship the repository's ``data/`` and ``vendor/`` inside the package.

All project metadata lives in ``pyproject.toml``; setuptools runs this file only to pick up
``cmdclass``. ``data/`` (game manifests, ``exe_versions.json``, rules, notices) sits next to ``src/``,
outside the package, so ``package-data`` alone cannot reach it: the ``build_py`` step below copies
it into ``<build>/satk/_data``, where :mod:`satk.core.resources` finds it after a normal install
(``pip install <checkout>``, ``pipx install``, a wheel). Editable installs (``pip install -e``) and the
shims read ``<checkout>/data`` directly, so the copy is skipped for them. ``MANIFEST.in`` puts
``data/`` into the sdist, so a wheel built from the sdist carries it too.

``vendor/`` (MIT code vendored with its notices: gta-flow, rwfury) is copied the same way to
``satk/_vendor``; its loaders look for ``<checkout>/vendor`` first. ``satk dev release``
(``src/satk/release/dist.py``) builds the same wheel with the stdlib and must stay in step with this file.

stdlib-only helpers (:func:`data_files`) are importable without setuptools (``tests/core/test_resources.py``).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
#: Repository data directory (source of the packaged copy).
DATA = ROOT / "data"
#: Where the copy lands inside the build tree / the installed package.
TARGET = ("satk", "_data")
#: Vendored third-party code (copied to ``satk/_vendor`` when present).
VENDOR = ROOT / "vendor"
VENDOR_TARGET = ("satk", "_vendor")
#: Never shipped (caches, editor and OS leftovers).
SKIP_NAMES = frozenset({"__pycache__", ".DS_Store", "Thumbs.db", "desktop.ini"})


def data_files(root: str | os.PathLike = DATA) -> list[tuple[Path, str]]:
    """``[(source file, relative POSIX name)]`` of everything shipped from ``root``, sorted by name."""
    root = Path(root)
    out: list[tuple[Path, str]] = []
    for f in root.rglob("*"):
        rel = f.relative_to(root)
        if not f.is_file() or any(p in SKIP_NAMES or p.startswith(".") for p in rel.parts):
            continue
        if f.suffix in (".pyc", ".pyo"):
            continue
        out.append((f, rel.as_posix()))
    return sorted(out, key=lambda t: t[1])


def copy_data(build_lib: str | os.PathLike, root: str | os.PathLike = DATA,
              target: tuple[str, ...] = TARGET) -> list[str]:
    """Copy :func:`data_files` of ``root`` into ``<build_lib>/satk/_data``; returns the written paths."""
    if not Path(root).is_dir():
        raise RuntimeError(f"satk build: {root} is missing - build from a full checkout or an sdist made by it")
    dst_root = Path(build_lib).joinpath(*target)
    written: list[str] = []
    for src, rel in data_files(root):
        dst = dst_root.joinpath(*rel.split("/"))
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        written.append(str(dst))
    return written


def _main() -> None:
    from setuptools import setup
    from setuptools.command.build_py import build_py

    class BuildPyWithData(build_py):
        """``build_py`` + ``data/`` -> ``satk/_data`` (not for editable installs)."""

        def run(self) -> None:
            super().run()
            if not getattr(self, "editable_mode", False):
                copy_data(self.build_lib)
                if VENDOR.is_dir():
                    copy_data(self.build_lib, VENDOR, VENDOR_TARGET)

        def get_outputs(self, include_bytecode: bool = True) -> list[str]:
            outs = super().get_outputs(include_bytecode)
            if getattr(self, "editable_mode", False) or not DATA.is_dir():
                return outs
            dst = Path(self.build_lib).joinpath(*TARGET)
            outs = [*outs, *(str(dst.joinpath(*rel.split("/"))) for _, rel in data_files())]
            if VENDOR.is_dir():
                vdst = Path(self.build_lib).joinpath(*VENDOR_TARGET)
                outs += [str(vdst.joinpath(*rel.split("/"))) for _, rel in data_files(VENDOR)]
            return outs

    setup(cmdclass={"build_py": BuildPyWithData})


if __name__ == "__main__":
    _main()
