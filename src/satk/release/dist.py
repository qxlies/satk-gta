"""Wheel and sdist of satk itself, built from a :class:`~satk.release.tree.Tree` with the stdlib.

The development venv has no setuptools and the release must not need the network for satk's own
files, so both archives are written here following the rules of ``pyproject.toml``, ``setup.py`` and
``MANIFEST.in`` (``tests/release`` checks they agree):

* wheel ``satk_gta-<ver>-py3-none-any.whl``: every file of ``src/satk`` + ``data/`` as ``satk/_data`` +
  ``vendor/`` as ``satk/_vendor`` (hidden files, ``__pycache__``, ``*.pyc`` and OS junk skipped), and
  ``*.dist-info`` (``METADATA`` 2.1, ``WHEEL``, ``entry_points.txt``, ``top_level.txt``, licenses,
  ``RECORD``);
* sdist ``satk_gta-<ver>.tar.gz``: ``PKG-INFO`` + the root build files + the trees of :data:`SDIST_TREES`.

Both are byte-for-byte reproducible: sorted entries, the commit time as mtime, no owners.
"""

from __future__ import annotations

import base64
import csv
import gzip
import hashlib
import io
import re
import tarfile
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from ..core.errors import SatkError
from ..core.paths import atomic_write, ensure_writable
from .tree import Tree

__all__ = ["SKIP_NAMES", "SDIST_FILES", "SDIST_TREES", "PACKAGED_TREES", "dist_name", "project_meta", "metadata",
           "wheel_files", "build_wheel", "build_sdist", "shipped"]

#: Never shipped (the same set as ``setup.py``).
SKIP_NAMES = frozenset({"__pycache__", ".DS_Store", "Thumbs.db", "desktop.ini"})
#: Root files of the sdist (``setup.py`` and ``pyproject.toml`` build it; README is the long description).
SDIST_FILES = ("LICENSE", "MANIFEST.in", "NOTICE.md", "README.md", "pyproject.toml", "setup.py")
#: Trees of the sdist (``graft`` lines of ``MANIFEST.in``).
SDIST_TREES = ("data/", "src/satk/", "vendor/")
#: Repository trees copied into the package by ``setup.py`` (source -> directory inside ``satk``).
PACKAGED_TREES = {"data/": "_data", "vendor/": "_vendor"}


def shipped(rel: str) -> bool:
    """``rel`` (POSIX, relative to its tree) is shipped: no hidden parts, caches, OS junk, bytecode."""
    parts = PurePosixPath(rel).parts
    return not any(p in SKIP_NAMES or p.startswith(".") for p in parts) and not rel.endswith((".pyc", ".pyo"))


def dist_name(name: str) -> str:
    """File-name form of a project name (PEP 427/625): ``satk-gta`` -> ``satk_gta``."""
    return re.sub(r"[-_.]+", "_", name).lower()


def project_meta(tree: Tree) -> dict[str, Any]:
    """``[project]`` of the ref's ``pyproject.toml`` (name, version, extras, scripts, ...)."""
    import tomllib

    p = tomllib.loads(tree.text("pyproject.toml")).get("project", {})
    for k in ("name", "version", "description", "requires-python"):
        if not isinstance(p.get(k), str):
            raise SatkError("CHECK_FAILED", f"pyproject.toml: project.{k} is missing")
    return p


def metadata(tree: Tree) -> str:
    """Core metadata 2.1 (``METADATA`` of the wheel, ``PKG-INFO`` of the sdist)."""
    p = project_meta(tree)
    lic = p.get("license")
    lic = lic.get("text") if isinstance(lic, dict) else lic
    lines = ["Metadata-Version: 2.1", f"Name: {p['name']}", f"Version: {p['version']}", f"Summary: {p['description']}"]
    if lic:
        lines.append(f"License: {lic}")
    for c in p.get("classifiers", []):
        lines.append(f"Classifier: {c}")
    lines.append(f"Requires-Python: {p['requires-python']}")
    for req in p.get("dependencies", []):
        lines.append(f"Requires-Dist: {req}")
    for extra, reqs in sorted((p.get("optional-dependencies") or {}).items()):
        lines.append(f"Provides-Extra: {extra}")
        for req in reqs:
            lines.append(f'Requires-Dist: {req}; extra == "{extra}"')
    body = ""
    readme = p.get("readme")
    if isinstance(readme, str) and readme in tree.files:
        lines.append("Description-Content-Type: text/markdown" if readme.endswith(".md") else
                     "Description-Content-Type: text/plain")
        body = tree.text(readme)
    return "\n".join(lines) + "\n\n" + body


def wheel_files(tree: Tree) -> dict[str, bytes]:
    """Package files of the wheel by path inside it (no ``*.dist-info``)."""
    out: dict[str, bytes] = {}
    for path in tree.under("src/satk/"):
        rel = path[len("src/"):]
        if shipped(path[len("src/satk/"):]):
            out[rel] = tree.files[path]
    for src, dst in PACKAGED_TREES.items():
        for path in tree.under(src):
            rel = path[len(src):]
            if shipped(rel):
                out[f"satk/{dst}/{rel}"] = tree.files[path]
    return out


def _record_hash(data: bytes) -> str:
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")


def _zip_time(ts: int) -> tuple[int, int, int, int, int, int]:
    return max(time.gmtime(ts)[:6], (1980, 1, 1, 0, 0, 0))


def build_wheel(tree: Tree, out_dir: Path) -> Path:
    """Write ``<dist>-<ver>-py3-none-any.whl`` into ``out_dir`` (created by the caller) and return it."""
    p = project_meta(tree)
    name, ver = dist_name(p["name"]), p["version"]
    info = f"{name}-{ver}.dist-info"
    files = wheel_files(tree)
    if not any(k.endswith("/__init__.py") and k.count("/") == 1 for k in files):
        raise SatkError("CHECK_FAILED", "src/satk/__init__.py is missing from the tree")
    scripts = p.get("scripts") or {}
    files[f"{info}/METADATA"] = metadata(tree).encode("utf-8")
    files[f"{info}/WHEEL"] = (f"Wheel-Version: 1.0\nGenerator: satk-release ({ver})\nRoot-Is-Purelib: true\n"
                              "Tag: py3-none-any\n").encode("utf-8")
    if scripts:
        files[f"{info}/entry_points.txt"] = ("[console_scripts]\n" + "".join(
            f"{k} = {v}\n" for k, v in sorted(scripts.items()))).encode("utf-8")
    files[f"{info}/top_level.txt"] = b"satk\n"
    for lic in ("LICENSE", "NOTICE.md"):
        if lic in tree.files:
            files[f"{info}/licenses/{lic}"] = tree.files[lic]
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    for k in sorted(files):
        w.writerow([k, _record_hash(files[k]), len(files[k])])
    w.writerow([f"{info}/RECORD", "", ""])
    files[f"{info}/RECORD"] = buf.getvalue().encode("utf-8")
    target = ensure_writable(out_dir / f"{name}-{ver}-py3-none-any.whl")
    dt = _zip_time(tree.timestamp)
    # dist-info last (the convention pip and the wheel spec recommend)
    order = sorted(files, key=lambda k: (k.startswith(info), k))
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for k in order:
            zi = zipfile.ZipInfo(k, dt)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o100644 << 16
            zi.create_system = 3
            zf.writestr(zi, files[k], compresslevel=9)
    return target


def build_sdist(tree: Tree, out_dir: Path) -> Path:
    """Write ``<dist>-<ver>.tar.gz`` into ``out_dir`` and return it."""
    p = project_meta(tree)
    name, ver = dist_name(p["name"]), p["version"]
    top = f"{name}-{ver}"
    files: dict[str, bytes] = {"PKG-INFO": metadata(tree).encode("utf-8")}
    for f in SDIST_FILES:
        if f not in tree.files:
            raise SatkError("CHECK_FAILED", f"{f} is missing from the tree (needed in the sdist)")
        files[f] = tree.files[f]
    for t in SDIST_TREES:
        for path in tree.under(t):
            if shipped(path):
                files[path] = tree.files[path]
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tf:
        for k in sorted(files):
            ti = tarfile.TarInfo(f"{top}/{k}")
            ti.size, ti.mtime, ti.mode = len(files[k]), tree.timestamp, 0o644
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = ""
            tf.addfile(ti, io.BytesIO(files[k]))
    target = out_dir / f"{top}.tar.gz"
    gz = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=gz, mtime=0, compresslevel=9) as g:
        g.write(raw.getvalue())
    return atomic_write(target, gz.getvalue())
