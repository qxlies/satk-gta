"""Synthetic game installs for tests/game (no game bytes; everything is generated in tmp).

Tests get everything through fixtures (``fake_game``, ``local_exe``, ``tree_snapshot``): with
``--import-mode=importlib`` a test module cannot ``import conftest``.
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import struct
from dataclasses import dataclass
from pathlib import Path

import pytest

from satk.game import exe as X
from satk.game import manifests as M

#: Fixed mtimes (seconds) so the fast verify path is exercised deterministically.
MTIME = 1_100_000_000


def blob(name: str, size: int) -> bytes:
    """Deterministic pseudo-random bytes (never looks like a RW/COL asset)."""
    out = bytearray()
    seed = name.encode("utf-8")
    i = 0
    while len(out) < size:
        out += hashlib.sha256(seed + i.to_bytes(4, "little")).digest()
        i += 1
    data = bytes(out[:size])
    return b"TEST" + data[4:] if size >= 4 else data


@functools.lru_cache(maxsize=None)
def fake_local_exe() -> bytes:
    """A minimal PE image with the no-intro patch at the real offset (like the local install)."""
    size = X.PATCH_OFFSET + 0x200
    b = bytearray(blob("exe", size))
    b[0:2] = b"MZ"
    struct.pack_into("<I", b, 0x3C, 0x80)
    b[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HHI", b, 0x84, 0x14C, 11, 0x427101CA)
    opt = 0x80 + 24
    struct.pack_into("<H", b, opt, 0x10B)
    struct.pack_into("<I", b, opt + 56, 0x1177000)
    struct.pack_into("<I", b, opt + 64, X.STOCK_HDR_CHECKSUM)
    end = X.PATCH_OFFSET + len(X.NOINTRO_PATCH)
    b[X.PATCH_OFFSET:end] = X.NOINTRO_PATCH
    return bytes(b)


#: relpath -> size of the synthetic install (stock, excluded and non-stock files).
FILES: dict[str, int] = {
    **{rel: 4096 + 512 * i for i, rel in enumerate(M.PROTECT_IMGS)},  # the 8 IMG archives
    "anim/ped.ifp": 300,
    "audio/SFX/GENRL": 5000,
    "audio/CONFIG/PakFiles.dat": 120,
    "data/gta.dat": 700,
    "data/maps/la/lae2.ide": 333,
    "data/colorcycle.dat": 40,
    "data/default.two": 50,
    "models/generic/vehicle.txd": 2048,
    "models/x360btns.txd": 64,
    "movies/Logo.mpg": 1500,
    "text/american.gxt": 900,
    "text/languages.ini": 30,
    "ReadMe/ReadMe.txt": 77,
    "eax.dll": 1000,
    "ogg.dll": 800,
    "vorbis.dll": 1200,
    "stream.ini": 197,
    "vorbisFile.dll": 537,     # the ASI loader that took the name
    "vorbisHooked.dll": 655,   # the original vorbisFile.dll
    "cleo.asi": 100,
    "scripts/global.ini": 20,
    "scripts/mod.asi": 90,
    "SAMP/SAMP.img": 2048,
    "modloader/pack/readme.txt": 15,
    "chatlog.txt": 10,
}
STOCK_COUNT = 23  # files with role stock/restore below (8 IMG + 13 others + exe + vorbisFile)
NONSTOCK_COUNT = 11


@dataclass
class FakeGame:
    root: Path            # the synthetic "original install"
    install_json: Path    # its audit manifest (flat format, backslash paths like the real one)
    stock_json: Path      # stock manifest generated from it
    install: M.Manifest
    stock: M.Manifest
    stock_exe: bytes
    mta_exe: bytes
    stock_count: int = STOCK_COUNT
    nonstock_count: int = NONSTOCK_COUNT


def write_tree(root: Path) -> dict[str, dict]:
    audit: dict[str, dict] = {}
    for rel, size in FILES.items():
        p = M.to_path(root, rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(blob(rel, size))
        os.utime(p, (MTIME, MTIME))
    exe = M.to_path(root, "gta_sa.exe")
    exe.write_bytes(fake_local_exe())
    os.utime(exe, (MTIME, MTIME))
    for rel in list(FILES) + ["gta_sa.exe"]:
        p = M.to_path(root, rel)
        data = p.read_bytes()
        audit[rel.replace("/", "\\")] = {"mtime": int(p.stat().st_mtime), "sha256": hashlib.sha256(data).hexdigest(),
                                         "size": len(data)}
    return audit


@functools.lru_cache(maxsize=None)
def _fake_targets() -> tuple[bytes, bytes]:
    return bytes(X.to_stock(fake_local_exe(), strict=False)), X.build(fake_local_exe(), "mta", strict=False)


@pytest.fixture
def fake_game(tmp_path: Path) -> FakeGame:
    root = tmp_path / "install"
    audit = write_tree(root)
    install_json = tmp_path / "install.json"
    install_json.write_text(json.dumps(audit, indent=0, sort_keys=True), encoding="utf-8")
    install = M.load_manifest(install_json)
    stock_exe, mta_exe = _fake_targets()
    body = M.build_stock(install, exe_sha256={"hoodlum-stock": X.sha256(stock_exe), "mta-canonical": X.sha256(mta_exe)},
                         vorbisfile_sha256=audit["vorbisHooked.dll"]["sha256"], source="install.json")
    stock_json = tmp_path / "stock.json"
    stock_json.write_text(M.dump_stock(body), encoding="utf-8")
    return FakeGame(root, install_json, stock_json, install, M.load_manifest(stock_json), stock_exe, mta_exe)


@pytest.fixture
def work(satk_home: Path) -> Path:
    """``paths.work`` of the isolated workspace: game.* write targets must lie under it."""
    return satk_home / "work"


@pytest.fixture
def real_work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Real configuration (real clean copy / install) with ``paths.work`` redirected to tmp."""
    from satk.core import config

    w = tmp_path / "work"
    w.mkdir()
    monkeypatch.setenv("SATK_PATHS_WORK", str(w))
    config.reset()
    yield w
    monkeypatch.delenv("SATK_PATHS_WORK")
    config.reset()


@pytest.fixture
def junction():
    """``junction(link, target) -> link``: a directory junction (no admin rights needed), removed afterwards."""
    import subprocess

    made: list[Path] = []

    def make(link: Path, target: Path) -> Path:
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
        if r.returncode != 0 or not link.exists():
            pytest.skip(f"cannot create a junction: {r.stdout} {r.stderr}")
        made.append(link)
        return link

    yield make
    for link in made:
        if os.path.lexists(link):
            os.rmdir(link)  # removes the junction, never the target's contents


@pytest.fixture
def local_exe() -> bytes:
    """Synthetic "local install" executable (see :func:`fake_local_exe`)."""
    return fake_local_exe()


@pytest.fixture
def tree_snapshot():
    """``snap(root) -> {relpath: (size, mtime_ns, sha256)}`` to prove a tree was not modified."""
    return snapshot


def snapshot(root: Path) -> dict[str, tuple[int, int, str]]:
    """relpath -> (size, mtime_ns, sha256) of every file (to prove a tree was not modified)."""
    out = {}
    for dp, _, fns in os.walk(root):
        for fn in fns:
            p = Path(dp, fn)
            st = p.stat()
            out[p.relative_to(root).as_posix()] = (st.st_size, st.st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
    return out
