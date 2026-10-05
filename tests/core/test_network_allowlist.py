"""Network policy (M3 A2): network modules are imported only by the allowlisted files.

A new ``import urllib.request`` / ``socket`` / ``ssl`` / ``http.client`` anywhere else in the product
(src, vendor, blender, scripts, packaging) fails here. A file that really needs the network is added to
``satk.runtime.network.ALLOWED`` with the reason, in the same change.
"""

from __future__ import annotations

import pytest

from satk.runtime import network as N


def test_no_network_imports_outside_the_allowlist(repo_root):
    found = N.scan(repo_root)
    bad = N.violations(found)
    assert bad == [], "network imports outside satk.runtime.network.ALLOWED:\n" + "\n".join(
        f"  {f.path}:{f.line} {f.module}" for f in bad)
    assert found, "the scan found nothing: SCAN_DIRS or the parser is broken"


def test_allowlist_has_no_stale_entries(repo_root):
    assert N.stale_entries(N.scan(repo_root), repo_root) == []
    for path, (mods, reason) in N.ALLOWED.items():
        assert path.startswith(("src/", "vendor/", "blender/", "scripts/", "packaging/")) and path.endswith(".py")
        assert mods and mods <= N.NETWORK_MODULES and len(reason) >= 20, path


SAMPLE = '''
import os, socket as s
import urllib.parse
from urllib.parse import quote
from urllib import parse, request
from http import HTTPStatus
from http import client
from . import socket
import importlib


def later():
    import ssl
    import http.client
    importlib.import_module("urllib.request")
    __import__("ftplib")
    importlib.import_module(name_from_somewhere)
    import requests.adapters
'''


def test_scanner_finds_every_spelling(tmp_path):
    f = tmp_path / "src" / "satk" / "newpkg" / "ops.py"
    f.parent.mkdir(parents=True)
    f.write_text(SAMPLE, encoding="utf-8")
    (tmp_path / "src" / "satk" / "broken.py").write_text("def (:\n", encoding="utf-8")  # skipped, not fatal
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("import socket\n", encoding="utf-8")  # tests are not scanned
    found = N.scan(tmp_path)
    got = sorted((x.line, x.module) for x in found)
    assert got == [(2, "socket"), (5, "urllib.request"), (7, "http.client"), (13, "ssl"), (14, "http.client"),
                   (15, "urllib.request"), (16, "ftplib"), (18, "requests")]
    assert {x.path for x in found} == {"src/satk/newpkg/ops.py"}
    assert N.violations(found) == found
    allowed = {"src/satk/newpkg/ops.py": (frozenset({"socket"}), "x" * 20)}
    assert {x.module for x in N.violations(found, allowed)} == {"urllib.request", "http.client", "ssl", "ftplib",
                                                                 "requests"}


@pytest.mark.parametrize("name,expect", [
    ("socket", "socket"), ("ssl", "ssl"), ("urllib", "urllib"), ("urllib.parse", None), ("urllib.parse.quote", None),
    ("urllib.request", "urllib.request"), ("urllib.request.pathname2url", "urllib.request"),
    ("urllib.error", "urllib.error"), ("http", None), ("http.HTTPStatus", None), ("http.client", "http.client"),
    ("http.server", "http.server"), ("xmlrpc.client", "xmlrpc.client"), ("socketserver", "socketserver"),
    ("webbrowser", "webbrowser"), ("httpx", "httpx"), ("asyncio", None), ("json", None), ("sockets", None),
])
def test_network_module(name, expect):
    assert N.network_module(name) == expect


def test_policy_line_is_shown_by_doctor():
    assert N.POLICY.startswith("network: none by default")
