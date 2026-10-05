"""vendor/rwfury is the unmodified MIT release 0.6.1 with its notice (M3 rule: vendor only MIT, with notice)."""

from __future__ import annotations

import hashlib
from pathlib import Path

from satk.rw import vendor

REPO = Path(__file__).resolve().parents[2]
VENDOR = REPO / "vendor" / "rwfury"


def test_vendored_files_are_byte_identical_to_the_release():
    sums = (VENDOR / "SHA256SUMS").read_text(encoding="ascii").splitlines()
    listed = {}
    for line in sums:
        digest, rel = line.split("  ", 1)
        listed[rel] = digest
    on_disk = {p.relative_to(VENDOR).as_posix() for p in VENDOR.rglob("*")
               if p.is_file() and "__pycache__" not in p.parts}
    assert on_disk - set(listed) == {"SHA256SUMS", "VENDORED.md", ".gitattributes"}
    for rel, digest in listed.items():
        assert hashlib.sha256((VENDOR / rel).read_bytes()).hexdigest() == digest, rel
    assert len(listed) == 34


def test_license_is_mit_and_notices_exist():
    lic = (VENDOR / "LICENSE").read_text(encoding="utf-8")
    assert lic.startswith("MIT License") and "Copyright (c) 2026 Hancapo" in lic
    notice = (REPO / "data" / "notices" / "rwfury.txt").read_text(encoding="utf-8")
    assert "rwfury 0.6.1" in notice and "Permission is hereby granted" in notice
    assert "rwfury" in (REPO / "NOTICE.md").read_text(encoding="utf-8")
    assert "0.6.1" in (VENDOR / "VENDORED.md").read_text(encoding="utf-8")


def test_loader_and_surface_names():
    mod = vendor.load()
    assert mod is not None and Path(mod.__file__).resolve().parent == (VENDOR / "rwfury").resolve()
    names = vendor.surface_names()
    assert names["DEFAULT"] == 0 and names["TARMAC"] == 1 and names["GRASS_SHORT_LUSH"] == 9
    assert vendor.VERSION == "0.6.1"
