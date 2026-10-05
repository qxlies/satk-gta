"""Synthetic gta-scout packs and a fake index with real DFF bytes for satk.describe tests (no game files).

* ``dff_bytes(seed, sectors)`` — deterministic pseudo-DFF payload, padded to whole 2048-byte sectors
  (what an IMG directory entry holds and what gta-scout hashes);
* ``scout_entry(...)`` / ``write_pack(path, entries)`` — pack entries with valid ids and pack digest;
* ``fake_world`` — a :class:`satk.index.fake.FakeIndexDB` whose DFF blobs (infernus, lae2_roads89,
  lodlae2_roads89; lapdna in ``samp``) are real bytes on disk, installed with ``override_index``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from satk.describe.pack import FORMAT, digest

SECTOR = 2048


def dff_bytes(seed: int, sectors: int = 2) -> bytes:
    """Pseudo-DFF payload of ``sectors`` * 2048 bytes (not a real RW file; never committed)."""
    out = bytearray()
    i = 0
    while len(out) < sectors * SECTOR:
        out += hashlib.sha256(f"satk-describe-test:{seed}:{i}".encode()).digest()
        i += 1
    return bytes(out[: sectors * SECTOR])


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def scout_entry(name: str, description: str, *, data: bytes | None = None, dff: str | None = None,
                archive: str | None = "gta3.img", entry: str | None = None, kind: str = "model",
                tags=("thing",), confidence=0.9, txd: str = "sometxd", sources: list | None = None,
                game: str = "sa") -> dict:
    """One pack entry; ``data`` = the DFF bytes it was made from (its SHA-256 goes into ``sources``)."""
    if sources is None:
        sources = []
        if data is not None and kind == "model":
            sources.append({"archive": archive, "entry": entry or f"{(dff or name).lower()}.dff",
                            "bytes": len(data), "sha256": sha(data)})
            sources.append({"archive": archive, "entry": f"{txd}.txd", "bytes": 4096,
                            "sha256": sha(b"txd" + data[:16])})
    selector = {"name": name, "dff": dff or name, "txd": txd} if kind == "model" else \
        {"name": name, "txd": txd, "archive": archive or "gta3.img"}
    e = {"game": game, "kind": kind, "selector": selector, "description": description, "tags": list(tags),
         "confidence": confidence, "review_method": "model-visual", "limitations": "none",
         "image_sha256": [sha(description.encode())], "sources": sources}
    e["id"] = digest(e)
    return e


def write_pack(path: Path, entries: list[dict], *, version: str = "sa-2026-10-03", license: str = "MIT",  # noqa: A002
               fmt: str = FORMAT, digest_ok: bool = True) -> Path:
    pack = {"format": fmt, "version": version, "license": license, "scope": "test pack", "entries": entries}
    pack["sha256"] = digest(pack) if digest_ok else "0" * 64
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")
    return path


#: DFF payloads of the fake index (bytes on disk = what the importer hashes).
INFERNUS = dff_bytes(411, 3)
ROADS = dff_bytes(17613, 2)
LOD = dff_bytes(17858, 1)
LAPDNA = dff_bytes(300, 2)


@pytest.fixture
def scout() -> SimpleNamespace:
    """Helpers of this conftest (``scout.entry(...)``, ``scout.write_pack(...)``, payload constants)."""
    return SimpleNamespace(dff_bytes=dff_bytes, sha=sha, entry=scout_entry, write_pack=write_pack,
                           INFERNUS=INFERNUS, ROADS=ROADS, LOD=LOD, LAPDNA=LAPDNA, SECTOR=SECTOR)


@pytest.fixture
def fake_world(satk_home, tmp_path):
    """``fake_world(profile)`` -> FakeIndexDB with real DFF bytes, returned by ``open_index`` in the test."""
    from satk.index.api import override_index
    from satk.index.fake import FakeIndexDB

    made: dict[str, FakeIndexDB] = {}

    def factory(profile: str):
        if profile not in made:
            payloads = {"dff:infernus": INFERNUS, "dff:lae2_roads89": ROADS, "dff:lodlae2_roads89": LOD}
            if profile == "samp":
                payloads["dff:lapdna"] = LAPDNA
            made[profile] = FakeIndexDB(profile, root=tmp_path / f"game-{profile}", payloads=payloads)
        return made[profile]

    with override_index(factory):
        yield factory


@pytest.fixture
def pack_file(tmp_path):
    """The standard test pack: infernus (bench text), roads under another name, one wrong hash,
    one texture entry, one reference-only model."""
    entries = [
        scout_entry("infernus", "FR: Banc rouge. EN: Red sports car parked next to a park bench.", data=INFERNUS,
                    tags=("car", "red")),
        scout_entry("roads_published_name", "Flat grey road segment with green verges.", data=ROADS,
                    dff="roads_published_name"),
        scout_entry("lodlae2_roads89", "Coarse low-detail road block.", data=dff_bytes(999, 1)),  # other bytes
        scout_entry("vent", "Metal vent grille.", kind="texture", data=b"x" * SECTOR),
        scout_entry("ghost", "Reference-only model without sources."),
    ]
    return write_pack(tmp_path / "packs" / "sa-2026-10-03.json", entries)
