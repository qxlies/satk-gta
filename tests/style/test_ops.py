"""satk.style operations: registration, MCP budget rules, fast import, CLI on synthetic files, help topics (K8)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from satk.core.registry import GROUPS, MAX_SUMMARY, all_ops, discover

from .rwkit import car_dff, prop_dff

OPS = ("style.build", "style.profile", "style.card", "style.texture", "style.brief_check", "asset.check",
       "asset.anatomy")


def test_ops_registered_cli_only_and_english():
    discover()
    ops = {o.name: o for o in all_ops()}
    for name in OPS:
        o = ops[name]
        assert o.mcp is False, name                         # tools/list must not grow
        assert len(o.summary) <= MAX_SUMMARY and o.summary.isascii(), name
        assert o.summary_ru and not o.summary_ru.isascii(), name
        assert o.group in GROUPS and o.examples, name
        for p in o.params:
            assert p.help and p.help.isascii(), (name, p.name)


def test_ops_module_imports_no_numpy():
    code = ("import sys; import satk.style.ops, satk.style.api; "
            "bad = [m for m in ('numpy', 'PIL') if m in sys.modules]; print(bad); sys.exit(1 if bad else 0)")
    src = Path(__file__).resolve().parents[2] / "src"
    # A minimal environment, but with the user folders: a checkout outside a workspace (a fresh clone, CI)
    # derives its default workspace from them at import.
    keep = ("SYSTEMROOT", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "HOME", "LOCALAPPDATA", "APPDATA", "TEMP", "TMP")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env={**env, "PYTHONPATH": str(src)})
    assert r.returncode == 0, r.stdout + r.stderr


def test_brief_check_cli(satk_home, run_cli, tmp_path):
    p = tmp_path / "BRIEF.md"
    p.write_text("Painted look: clean readable shapes, no photographs; close to real scale.\n", encoding="utf-8")
    r = run_cli(["style", "brief-check", str(p)])
    assert r.code == 0, r.err
    env = r.json
    assert env["flags"] == 3 and {row[0] for row in env["rows"]} == {"clean_shapes", "no_photos", "real_scale"}


def test_anatomy_cli_markdown(satk_home, run_cli, tmp_path):
    p = tmp_path / "mycar.dff"
    p.write_bytes(car_dff())
    r = run_cli(["asset", "anatomy", str(p), "--md"])
    assert r.code == 0, r.err
    text = r.json["text"]
    assert text.startswith("# mycar") and "|3|wheel_lf_dummy|0|-0.9,1.6,-0.3|" in text
    js = run_cli(["asset", "anatomy", str(p)]).json
    assert js["frames_n"] == 13 and js["col"]["spheres"] == 2


def test_check_without_index_explains(satk_home, run_cli, tmp_path):
    from satk.index.api import clear_cache

    clear_cache()                                   # an index opened by an earlier test must not leak in
    p = tmp_path / "box.dff"
    p.write_bytes(prop_dff())
    r = run_cli(["asset", "check", str(p), "--cls", "prop"])
    assert r.code != 0
    err = r.json["error"] if r.out else {}
    assert err.get("code") == "INDEX_MISSING" and "index build" in err.get("hint", "")


def test_texture_cli_rejects_unknown_role(satk_home, run_cli, tmp_path):
    r = run_cli(["style", "texture", str(tmp_path), "--role", "spaceship"])
    assert r.code != 0 and r.json["error"]["code"] == "BAD_PARAMS"


def test_register_topics(tmp_path):
    from satk.runtime import help as H
    from satk.style.ops import register_topics

    (tmp_path / "zz_style_test.md").write_text("# Style test topic\nuse satk style profile\n", encoding="utf-8")
    (tmp_path / "zz_too_long.md").write_text("x" * (H.MAX_TOPIC_CHARS + 1), encoding="utf-8")
    (tmp_path / "not-an-id.md").write_text("# skipped\n", encoding="utf-8")
    try:
        names = register_topics(tmp_path)
        assert names == ["zz_style_test"]
        env = H.render("zz_style_test")
        assert env["text"].startswith("# Style test topic") and H.TOPICS["zz_style_test"][0] == "Style test topic"
    finally:
        H.TOPICS.pop("zz_style_test", None)
    assert register_topics(tmp_path / "missing") == []
