"""SP bridge operations: registration, agent policy, status and the address op."""

from __future__ import annotations

import pytest

from satk.core.errors import SatkError
from satk.core.registry import get_op, invoke
from satk.mcp.generic import denial
from satk.spbridge import addresses as A


OPS = ("sp.addresses", "sp.build", "sp.selftest", "sp.status", "sp.start", "sp.stop")


def test_ops_registered():
    for name in OPS:
        spec = get_op(name)
        assert spec.mcp is False, f"{name} must be CLI/generic only (mcp=False), not a new MCP tool"


def test_consent_gated_ops():
    """start/stop touch a real game process: agents get CONSENT_REQUIRED, never silent run."""
    for name in ("sp.start", "sp.stop"):
        d = denial(get_op(name))
        assert d is not None and d[0] == "CONSENT_REQUIRED", f"{name} must be consent-gated"
    # the offline, safe ops are agent-callable
    for name in ("sp.addresses", "sp.status", "sp.selftest", "sp.build"):
        assert denial(get_op(name)) is None, f"{name} should be agent-callable"


def test_addresses_op():
    r = invoke("sp.addresses", {})
    assert r["ok"] and r["total"] == len(A.all_symbols())
    assert r["game_version"] == "1.0us"
    v = invoke("sp.addresses", {"verify": True})
    # verify may report unknowns if the KB is absent, but must never mismatch.
    if v.get("verify"):
        assert not v["verify"]["mismatches"]


def test_status_without_endpoint(satk_home):
    r = invoke("sp.status", {})
    assert r["ok"] and r["running"] is False


def test_start_refuses_without_consent(satk_home):
    r = invoke("sp.start", {"copy": str(satk_home / "nope")})
    assert r["ok"] is False
    assert r["error"]["code"] in ("NOT_FOUND", "BAD_PARAMS", "CONSENT_REQUIRED")


def test_start_refuses_protected_roots(satk_home, monkeypatch):
    # The clean copy / install default under the isolated workspace; starting them is refused.
    from satk.core import paths

    game_dir = paths.cfg().paths.game
    r = invoke("sp.start", {"copy": str(game_dir), "yes": True})
    assert r["ok"] is False and r["error"]["code"] == "BAD_PARAMS"
