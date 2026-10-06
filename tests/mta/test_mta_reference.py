"""The MTA reference of ``satk mta lint``: from the knowledge base tables, and (engine) from the real MTA tree,
where the generated resources must lint clean; the live server check needs ``SATK_TEST_LIVE=1``."""

from __future__ import annotations

import json
import os
import sqlite3
import struct
from pathlib import Path

import pytest

try:
    from satk.core import config as _config

    _CFG = _config.build()                     # before satk_home points paths at a temp workspace
    REAL_ENGINE = _CFG.paths.get("engine")
except Exception:  # noqa: BLE001
    REAL_ENGINE = None

_CHECKER = "Server/mods/deathmatch/logic/CResourceChecker.Data.h"


def _kb(path: Path) -> None:
    from satk.kb.build import SCHEMA_VERSION
    from satk.kb.scriptapi_build import DDL

    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(DDL + "CREATE TABLE source(id INTEGER PRIMARY KEY, key TEXT UNIQUE, title TEXT, license TEXT,"
                            " policy TEXT, repo TEXT, ref TEXT, rev TEXT, files INTEGER);")
    rows = [("dxDrawText", "client", "mta", "bool", [[["text", "string", 0, None], ["x", "float", 0, None]]],
             "argparser", None, None),
            ("engineRequestModel", "client", "mta", "int", [[["modelType", "string", 0, None]]], "argreader",
             '{"approx":true}', '["eClientModelType"]'),
            ("engineRequestModel", "server", "neon", "int", [[["typeName", "string", 0, None]]], "argreader", None, None),
            ("outputChatBox", "client", "mta", "bool", [[["text", "string", 0, None]]], "argparser", None, None),
            ("outputChatBox", "server", "mta", "bool", [[["text", "string", 0, None]]], "argparser", None, None)]
    for name, side, origin, ret, variants, parser, flags, enums in rows:
        con.execute("INSERT INTO mta_func(name, side, origin, ret, variants, parser, flags, enums) "
                    "VALUES(?,?,?,?,?,?,?,?)", (name, side, origin, ret, json.dumps(variants), parser, flags, enums))
    con.execute("INSERT INTO mta_event(name, side, origin, params) VALUES('onClientRender','client','mta','')")
    con.execute("INSERT INTO mta_class(name, side, origin, parent, static) VALUES('Vehicle','client','mta',NULL,0)")
    con.execute("INSERT INTO mta_enum(name, ctype, side, origin, vals) VALUES('client-model-type','eClientModelType',"
                "'client','mta',?)", (json.dumps(["ped", "vehicle"]),))
    con.execute("INSERT INTO source(key, repo, rev) VALUES('mta-lua', 'engine/mtasa', 'abcdef0123456789')")
    con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    con.commit()
    con.close()


def test_reference_from_kb_tables(satk_home):
    from satk.mta.api import load_reference
    from satk.mta.lint import lint_source

    _kb(satk_home / "work" / "kb" / "kb.sqlite")
    r, warn = load_reference("kb")
    assert r.source == "kb" and r.sides("engineRequestModel") == {"client", "server"}
    assert r.side("dxDrawText", "client").exact and r.side("engineRequestModel", "client").approx
    assert r.side("engineRequestModel", "client").enums == ("client-model-type",)
    assert r.side("engineRequestModel", "server").origin == "neon" and "engine/mtasa" in r.where
    assert any(w.startswith("NOT_READY") for w in warn)                     # no tree: deprecations unknown
    res = lint_source("engineRequestModel('vehical')\noutputChatBox('x')\n", side="client", ref=r)
    assert [f.code for f in res.findings] == ["ENUM_VALUE"]
    res = lint_source("engineRequestModel('object')\n", side="server", ref=r)
    assert [f.code for f in res.findings] == ["NEON_ONLY"]


def test_auto_without_kb_or_tree_is_empty_with_a_warning(satk_home):
    from satk.mta.api import load_reference

    r, warn = load_reference("auto")
    assert not r and warn and warn[0].startswith("NOT_READY")
    assert load_reference("none")[0].funcs == {}


def _rw(cid: int) -> bytes:
    child = struct.pack("<III", 1, 12, 0x1803FFFF) + b"\x00" * 12
    return struct.pack("<III", cid, len(child), 0x1803FFFF) + child


@pytest.mark.engine
@pytest.mark.slow
def test_real_tree_reference_and_generated_resources(satk_home, monkeypatch, tmp_path, run_cli):
    if not REAL_ENGINE or not (REAL_ENGINE / _CHECKER).is_file():
        pytest.skip("no MTA source tree at paths.engine")
    monkeypatch.setenv("SATK_PATHS_ENGINE", str(REAL_ENGINE))
    _config.reset()
    from satk.mta import scaffold
    from satk.mta.api import reference_from_tree
    from satk.mta.lint import lint_path, lint_source

    r = reference_from_tree(REAL_ENGINE, cache=False)
    assert r.sides("dxDrawText") == {"client"} and r.sides("kickPlayer") == {"server"}
    assert r.side("setElementPosition", "server") is not None and "onClientRender" in r.events
    assert r.deprecation("getPlayerOccupiedVehicle", "client")[1] == "getPedOccupiedVehicle"
    assert r.version and r.version.startswith("1.") and "vehicle" in r.element_types
    for kind in scaffold.KINDS:
        d = tmp_path / f"r-{kind}"
        d.mkdir()
        for rel, content in scaffold.render(kind, f"r-{kind}").items():
            p = d / rel
            p.write_bytes(content) if isinstance(content, bytes) else p.write_text(content, encoding="utf-8")
        res = lint_path(d, r)
        assert res.findings == [], (kind, [(f.at, f.code, f.msg) for f in res.findings])
    ref_file = tmp_path / "reference.json"
    ref_file.write_text(json.dumps(r.to_json()), encoding="utf-8")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "custom.dff").write_bytes(_rw(0x10))
    (assets / "custom.txd").write_bytes(_rw(0x16))
    (assets / "custom.col").write_bytes(b"COL3" + b"\x00" * 60)
    for kind, model_id in (("vehicle", "411"), ("skin", "7"), ("object", "1337")):
        for new_id in (False, True):
            args = ["--new-id", "--parent", model_id] if new_id else ["--replace", model_id]
            built = run_cli(["mta", "pack", str(assets), "--kind", kind,
                             "--name", f"pack-{kind}-{new_id}", *args])
            assert built.code == 0 and built.json["lint"]["clean"], built.json
            checked = run_cli(["mta", "lint", built.json["dir"], "--ref", str(ref_file), "--severity", "info"])
            assert checked.code == 0 and checked.json["rows"] == [] and checked.json["total"] == 0, checked.json
    bad = lint_source("dxDrawText('x', 0, 0)\nlocal v = getPlayerOccupiedVehicle(source)\n"
                      "addEventHandler('onClientRender', root, function() end)\n", side="server", ref=r)
    assert {"WRONG_SIDE", "DEPRECATED", "EVENT_WRONG_SIDE"} <= {f.code for f in bad.findings}
    standalone = lint_source("attachElementToElement(source, root)\nbase64Encode('x')\n", ref=r)
    assert {"DEPRECATED", "REMOVED"} <= {f.code for f in standalone.findings}


@pytest.mark.engine
@pytest.mark.slow
@pytest.mark.skipif(os.environ.get("SATK_TEST_LIVE") != "1", reason="live MTA server: SATK_TEST_LIVE=1")
def test_live_server_check(satk_home, monkeypatch, run_cli, tmp_path):
    if not REAL_ENGINE or not (REAL_ENGINE / "Bin" / "server" / "MTA Server64.exe").is_file():
        pytest.skip("the fork's server is not built")
    monkeypatch.setenv("SATK_PATHS_ENGINE", str(REAL_ENGINE))
    _config.reset()
    good = run_cli(["mta", "resource", "new", "live-ok"]).json
    assert good["ok"]
    env = run_cli(["mta", "server-check", "live-ok", "--wait", "1"]).json
    assert env["started"] is True and env["errors"] == 0, env
    bad = tmp_path / "live-bad"
    bad.mkdir()
    (bad / "meta.xml").write_text('<meta><script src="server.lua" type="server"/></meta>', encoding="utf-8")
    (bad / "server.lua").write_text("function f(\n", encoding="utf-8")
    env = run_cli(["mta", "server-check", str(bad), "--wait", "0"]).json
    assert env["errors"] >= 1 and any("expected" in r[3] for r in env["rows"]), env
