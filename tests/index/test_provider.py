"""SID provider registration (SPEC §3.2, §5.1 rule 6)."""

from __future__ import annotations

import pytest

from satk.core import registry
from satk.core.errors import SatkError
from satk.core.ids import KIND_OWNER, Sid, provider_for, providers
from satk.index import api, provider
from satk.index.api import FakeIndexDB, override_index


def test_discover_registers_index_kinds():
    registry.discover()
    assert "satk.index.ops" not in registry.import_errors()
    index_kinds = [k for k, o in KIND_OWNER.items() if o == "index"]
    assert sorted(provider.PROVIDER.kinds) == sorted(index_kinds)
    for k in index_kinds:
        assert provider_for(k) is provider.PROVIDER
    assert all(providers()[k] is provider.PROVIDER for k in index_kinds)


def test_register_idempotent():
    provider.register()
    provider.register()
    assert provider_for("model") is provider.PROVIDER


def test_provider_with_fake():
    p = provider_for("model")
    with override_index(lambda prof: FakeIndexDB(prof)):
        assert p.get(Sid.parse("model:411"), ["name"], "vanilla") == {"ok": True, "id": "model:411", "name": "infernus"}
        f = p.find("infernus", "model", 20, None, "vanilla")
        assert f["rows"][0][0] == "model:411"
        r = p.refs(Sid.parse("model:17613"), "inst", 50, None, "vanilla")
        assert r["rows"][0][0] == "inst:lae2_stream0#4"
        assert p.get(Sid.parse("model:300"), ["name"], "samp")["name"] == "lapdna"
        assert p.get(Sid.parse("model:300@vanilla"), ["name"], "samp")["name"] == "cutobj01"


def test_provider_without_index(satk_home, monkeypatch):
    monkeypatch.delenv("SATK_INDEX_FAKE", raising=False)
    api.clear_cache()
    with pytest.raises(SatkError) as e:
        provider_for("tex").get(Sid.parse("tex:bistro/vent_64"), None, "vanilla")
    assert e.value.code == "INDEX_MISSING" and e.value.exit_code == 3
