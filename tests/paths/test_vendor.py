"""The vendored gta-flow core: provenance, MIT notice and the private loader (M2-05)."""

from __future__ import annotations

import hashlib
import re
import sys

import pytest

from satk.core.config import REPO_ROOT
from satk.core.errors import SatkError
from satk.paths import vendor

VENDOR = REPO_ROOT / "vendor" / "gtaflow"


def _table() -> dict[str, str]:
    text = (VENDOR / "VENDORED.md").read_text(encoding="utf-8")
    return dict(re.findall(r"^\| `([^`]+)` \| `([0-9a-f]{64})` \|$", text, re.M))


def test_vendored_files_match_the_recorded_upstream_hashes():
    table = _table()
    assert set(table) == {"LICENSE", "NOTICE.md", "sa_traffic/__init__.py",
                          *(f"sa_traffic/{m}.py" for m in vendor.MODULES)}
    for rel, sha in table.items():
        data = (VENDOR / rel).read_bytes().replace(b"\r\n", b"\n")  # autocrlf checkouts
        assert hashlib.sha256(data).hexdigest() == sha, rel


def test_only_the_core_is_vendored():
    files = sorted(p.relative_to(VENDOR).as_posix() for p in VENDOR.rglob("*")
                   if p.is_file() and "__pycache__" not in p.parts)
    assert files == sorted({"VENDORED.md", *_table()})


def test_mit_notice_is_kept():
    lic = (VENDOR / "LICENSE").read_text(encoding="utf-8")
    assert lic.startswith("MIT License") and "Copyright (c) 2026 Dryxio" in lic
    assert "The above copyright notice and this permission notice shall be included" in lic
    assert "c3d9ad40c0facfbe7351a134ba3601e49be8d6be" in (VENDOR / "VENDORED.md").read_text(encoding="utf-8")


def test_loader_uses_a_private_package_name():
    gf = vendor.gtaflow()
    assert gf is vendor.gtaflow()
    for name in vendor.MODULES:
        mod = getattr(gf, name)
        assert mod.__name__ == f"{vendor.PACKAGE}.{name}"
        assert mod.__file__.replace("\\", "/").endswith(f"vendor/gtaflow/sa_traffic/{name}.py")
    assert gf.compiler.decode is gf.codec.decode  # relative imports resolve inside the private package
    assert "sa_traffic" not in sys.modules or sys.modules["sa_traffic"] is not sys.modules[vendor.PACKAGE]


def test_missing_vendor_dir_is_a_dependency_error(monkeypatch, tmp_path):
    monkeypatch.setattr(vendor, "REPO_ROOT", tmp_path)
    with pytest.raises(SatkError) as ei:
        vendor._package_dir()
    assert ei.value.code == "DEPENDENCY"
