"""SAAP capability ``author`` (the Blender studio): schemas, spec document, conformance on the mocks."""

from __future__ import annotations

import secrets

import pytest

from satk.saap import conformance as CF
from satk.saap import schema as S
from satk.saap import specdoc
from satk.saap.mock import MockWorld
from satk.saap.protocol import CAPABILITIES
from satk.saap.server import SaapServer
from satk.studio.mock import MockStudioWorld


def test_author_is_an_additive_capability():
    assert "author" in CAPABILITIES and CAPABILITIES[:3] == ("core", "camera", "world.settle")
    m = S.methods()
    assert m["author.call"] == "author" and m["author.methods"] == "author"


def test_author_call_params_schema():
    ok = {"method": "mesh.primitive", "params": {"kind": "cube"}, "stats": "scene",
          "snapshot": {"view": "3q", "size": 512, "look": "clay", "objects": ["a"]}, "checkpoint": True,
          "open": "C:/w/in.blend", "save": "C:/w/out.blend"}
    assert S.validate_params("author.call", ok) == []
    for bad in ({}, {"method": "Mesh Primitive"}, {"method": "x", "extra": 1}, {"method": "x", "stats": "all"},
                {"method": "x", "snapshot": {"size": 4096}}, {"method": "x", "snapshot": {"zoom": 2}},
                {"method": "x", "params": []}):
        assert S.validate_params("author.call", bad), bad
    assert S.validate_result("author.call", {"method": "x", "n": 1, "ms": 0.5}) == []
    assert S.validate_result("author.call", {"method": "x", "ms": 0.5})  # n is required
    assert S.validate_params("author.methods", {"query": "mesh", "limit": 5}) == []
    assert S.validate_result("author.methods", {"methods": [{"name": "a"}], "total": 1}) == []


def test_spec_document_covers_author():
    rep = specdoc.check()
    assert rep["errors"] == []
    text = (S.PROTO_DIR / "SAAP-v1.md").read_text(encoding="utf-8")
    assert "### `author.call`" in text and "| `author` |" in text and "blender-<name>.json" in text


@pytest.mark.parametrize("world", ["mock", "studio"])
def test_conformance_author_cases(satk_home, world):
    tok = secrets.token_hex(32)
    w = MockWorld() if world == "mock" else MockStudioWorld()
    role = "mock" if world == "mock" else "blender"
    srv = SaapServer(w, token=tok, role=role).start()
    try:
        rep = CF.run(CF.SaapDriver("127.0.0.1", srv.port, tok))
    finally:
        srv.stop()
    res = {r[0]: r[2] for r in rep["results"]}
    assert rep["fail"] == 0, [r for r in rep["results"] if r[2] == "fail"]
    author = sorted(k for k in res if k.startswith("author."))
    assert len(author) == 7 and all(res[k] == "pass" for k in author)
    if world == "studio":  # only core + author: the viewer cases are skipped, core and transport pass
        assert res["core.hello"] == "pass" and res["camera.revision"] == "skip"
