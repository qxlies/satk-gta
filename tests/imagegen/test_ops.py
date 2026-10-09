"""Registration, CLI and output hygiene of the imagegen operations."""

from __future__ import annotations

import json
import logging

import pytest

pytest.importorskip("PIL")
pytest.importorskip("numpy")

from satk.core import registry as R  # noqa: E402
from satk.imagegen import client as C  # noqa: E402
from satk.mcp.generic import denial  # noqa: E402

from .conftest import FAKE_KEY, FakeEndpoint, badge_image, ok_body, png_of  # noqa: E402

NAMES = {"imagegen.generate", "imagegen.key", "imagegen.icon_set", "imagegen.placeholder", "imagegen.finalize"}


def test_operations_are_registered_with_the_agent_rules():
    R.discover(force=True)
    ops = {o.name: o for o in R.all_ops() if o.name.startswith("imagegen.")}
    assert set(ops) == NAMES
    for o in ops.values():
        assert not o.mcp and 0 < len(o.summary) <= R.MAX_SUMMARY and o.summary_ru.strip()
    # the two operations that spend the user's API credit are not callable through satk_op
    for name in ("imagegen.generate", "imagegen.icon_set"):
        code, reason = denial(ops[name])
        assert code in ("CONSENT_REQUIRED", "UNSUPPORTED") and "key" in reason
    for name in ("imagegen.key", "imagegen.placeholder", "imagegen.finalize"):
        assert denial(ops[name]) is None
    # there is no parameter that could carry the key
    for o in ops.values():
        assert not {"api_key", "token", "authorization", "headers"} & {p.name for p in o.params}


def test_cli_placeholder_and_output_has_no_key(satk_home, run_cli, fake_key, capsys):
    r = run_cli(["imagegen", "placeholder", "--only", "lock"])
    assert r.code == 0 and FAKE_KEY not in r.out + r.err
    env = r.json
    assert env["ok"] and env["n"] == 2


def test_cli_generate_error_output_and_logs_have_no_key(satk_home, run_cli, monkeypatch, fake_key, caplog):
    ep = FakeEndpoint([(401, f"bad key {FAKE_KEY}".encode())])
    monkeypatch.setattr(C, "TRANSPORT", ep)
    with caplog.at_level(logging.DEBUG):
        r = run_cli(["imagegen", "generate", "a padlock"])
    assert r.code == 1
    env = r.json
    assert env["ok"] is False and env["error"]["data"]["imagegen_code"] == "AUTH_FAILED"
    assert FAKE_KEY not in r.out + r.err + caplog.text
    assert len(ep.requests) == 1 and ep.requests[0]["headers"]["Authorization"] == f"Bearer {FAKE_KEY}"


def test_cli_missing_key_envelope(satk_home, run_cli, monkeypatch):
    monkeypatch.delenv("SATK_IMAGEGEN_API_KEY", raising=False)
    r = run_cli(["imagegen", "generate", "x"])
    assert r.code == 3  # NOT_READY
    err = r.json["error"]
    assert err["data"]["imagegen_code"] == "NO_API_KEY" and "placeholder" in err["hint"]


def test_cli_icon_set_dry_run(satk_home, run_cli):
    r = run_cli(["imagegen", "icon-set", "--dry-run", "--only", "lock"])
    env = r.json
    assert r.code == 0 and env["planned_calls"] == 3 and env["n"] == 3


def test_cli_key_and_finalize_roundtrip(satk_home, run_cli, tmp_path):
    from pathlib import Path

    raw = Path(satk_home) / "work" / "tmp" / "lock.png"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(png_of(badge_image(300)))
    r = run_cli(["imagegen", "key", str(raw), "--sizes", "24,48", "--name", "lock"])
    assert r.code == 0 and r.json["n"] == 2
    r = run_cli(["imagegen", "placeholder"])
    assert r.code == 0 and r.json["n"] == 13
    r = run_cli(["imagegen", "finalize", "--use-placeholder", "classic,balanced,high,extreme,lock"])
    assert r.code == 0, r.out
    assert r.json["n"] == 13 and set(r.json["chosen"].values()) == {"placeholder"}


def test_a_hostile_endpoint_override_is_rejected(satk_home, run_cli, monkeypatch, fake_key):
    monkeypatch.setenv("SATK_IMAGEGEN_ENDPOINT", "http://evil.example/v1/chat/completions")
    r = run_cli(["imagegen", "generate", "x"])
    assert r.code == 2 and FAKE_KEY not in r.out + r.err
    assert json.loads(r.out)["error"]["code"] == "BAD_PARAMS"
