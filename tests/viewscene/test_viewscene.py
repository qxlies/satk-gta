"""satk.viewscene: ``satk view place|vehicle|ped|reload|remove|list`` against the SAAP mock.

The in-process mock (and, once, the mock endpoint over real sockets) emulates the viewer's
``scene.*`` methods; the files are synthetic (a RenderWare clump header, no game data).
"""

from __future__ import annotations

import secrets
import struct
import threading
import time
from pathlib import Path

import pytest

from satk.core.errors import SatkError
from satk.core.paths import jpath
from satk.core.registry import all_ops


@pytest.fixture(autouse=True)
def _fresh_world():
    from satk.viewer.backends.mock import reset_world

    reset_world()
    yield
    reset_world()


def _dff(path: Path) -> Path:
    """A file that starts like a DFF (clump chunk 0x10, RW 3.6)."""
    path.write_bytes(struct.pack("<III", 0x10, 12, 0x1803FFFF) + bytes(12))
    return path


def _rows(env: dict) -> list[dict]:
    return [dict(zip(env["cols"], r)) for r in env["rows"]]


def test_ops_registered_cli_only():
    ops = {o.name: o for o in all_ops()}
    for n in ("view.place", "view.vehicle", "view.ped", "view.reload", "view.remove", "view.list"):
        assert n in ops, n
        assert ops[n].mcp_name is None and ops[n].group == "view"
        assert ops[n].module == "satk.viewscene.ops"


def test_vehicle_ped_place_list_remove(run_cli, satk_home):
    d = run_cli(["view", "vehicle", "411", "--pos", "2495,-1675,13.4", "--heading", "90", "--dirt", "2",
                 "--colors", "3", "#FF0000", "--parts", "door_lf=dam", "--lights", "--id", "car",
                 "--target", "mock", "--json"]).json
    assert d["ok"] and d["id"] == "car" and d["sid"] == "el:scene/car" and d["ref"] == "@car"
    assert d["kind"] == "vehicle" and d["model_id"] == 411 and d["heading"] == 90.0
    v = d["vehicle"]
    assert v["dirt"] == 2 and v["lights"] is True and v["parts"]["door_lf"] == "dam" and v["parts"]["bonnet"] == "ok"
    assert v["colors"][0]["index"] == 3 and v["colors"][1] == {"slot": 2, "rgb": "#ff0000"}
    assert d["grounded"] is True and d["pos"] == [2495.0, -1675.0, 12.75]  # mock ground z 12 + half height
    assert d["rtt_ms"] >= 0 and d["target"] == "mock"

    p = run_cli(["view", "ped", "105", "--pos", "2497.5,-1673,13.4", "--anim", "walk_civi", "--anim-time", "0.4",
                 "--no-ground", "--target", "mock", "--json"]).json
    assert p["kind"] == "ped" and p["pos"] == [2497.5, -1673.0, 13.4] and "grounded" not in p
    assert p["ped"]["anim"] == {"ifp": "ped", "name": "walk_civi", "time": 0.4, "duration": 1.5, "bones": 32,
                                "nodes": 32}

    b = run_cli(["view", "place", "--model", "model:1280", "--pos", "2493,-1671,13.4", "--rot", "0,0,30",
                 "--scale", "2", "--target", "mock", "--json"]).json
    assert b["kind"] == "object" and b["model"] == "mock_bench" and b["heading"] == 30.0
    assert b["stats"]["bounds"] == [[-1.0, -1.0, -1.0], [1.0, 1.0, 1.0]]

    lst = run_cli(["view", "list", "--target", "mock", "--json"]).json
    rows = _rows(lst)
    assert [r["handle"] for r in rows] == ["car", p["id"], b["id"]] and lst["total"] == 3
    assert rows[0]["sid"] == "el:scene/car" and rows[0]["heading"] == 90.0 and rows[0]["reloads"] == 0

    rm = run_cli(["view", "remove", "car", "@" + p["id"], "--target", "mock", "--json"]).json
    assert rm["removed"] == ["car", p["id"]] and rm["left"] == 1
    clr = run_cli(["view", "remove", "--all", "--target", "mock", "--json"]).json
    assert clr["removed"] == 1
    assert run_cli(["view", "list", "--target", "mock", "--json"]).json["total"] == 0


def test_same_id_replaces_in_place(run_cli, satk_home):
    a = run_cli(["view", "place", "--model", "1280", "--pos", "1,2,13", "--id", "x", "--target", "mock",
                 "--json"]).json
    b = run_cli(["view", "place", "--model", "1280", "--pos", "4,5,13", "--id", "x", "--target", "mock",
                 "--json"]).json
    assert "replaced" not in a and b["replaced"] is True and b["pos"] == [4.0, 5.0, 13.0]
    assert run_cli(["view", "list", "--target", "mock", "--json"]).json["total"] == 1


def test_files_watch_and_reload(run_cli, satk_home, tmp_path):
    dff = _dff(tmp_path / "Test_Bench.dff")
    txd = tmp_path / "test_bench.txd"
    txd.write_bytes(struct.pack("<III", 0x16, 4, 0x1803FFFF) + bytes(4))
    d = run_cli(["view", "place", str(dff), "--txd", str(txd), "--pos", "2493,-1671,13.4", "--watch", "--id", "bench",
                 "--target", "mock", "--json"]).json
    assert d["dff"] == jpath(dff.resolve()) and d["txd"] == [jpath(txd.resolve())] and d["watch"] is True
    assert d["model"] == "test_bench" and "model_id" not in d

    time.sleep(0.02)
    dff.write_bytes(dff.read_bytes() + bytes(4))  # a re-export: the next scene call notices it
    row = _rows(run_cli(["view", "list", "--target", "mock", "--json"]).json)[0]
    assert row["reloads"] == 1 and row["watch"] is True and row["file"] == jpath(dff.resolve())

    r = run_cli(["view", "reload", "bench", "--target", "mock", "--json"]).json
    assert _rows(r) == [{"handle": "bench", "ok": True, "load_ms": 0.0, "error": None}]
    assert _rows(run_cli(["view", "list", "--target", "mock", "--json"]).json)[0]["reloads"] == 2

    dff.unlink()  # a reload that cannot read its file fails and keeps the entity
    r = run_cli(["view", "reload", "bench", "--target", "mock", "--json"])
    assert r.code != 0 and r.json["error"]["code"] == "BAD_PARAMS" and "previous model stays" in r.json["error"]["msg"]
    assert run_cli(["view", "list", "--target", "mock", "--json"]).json["total"] == 1
    allr = run_cli(["view", "reload", "--target", "mock", "--json"]).json
    assert _rows(allr)[0]["ok"] is False and any(w.startswith("BAD_PARAMS") for w in allr["warn"])


@pytest.mark.parametrize("argv, code", [
    (["view", "place", "{missing}", "--pos", "1,2,3"], "NOT_FOUND"),
    (["view", "place", "{bad}", "--pos", "1,2,3"], "BAD_PARAMS"),
    (["view", "place", "--model", "1280"], "BAD_PARAMS"),
    (["view", "place", "--pos", "1,2,3"], "BAD_PARAMS"),
    (["view", "place", "--model", "1280", "--pos", "1,2,3", "--rot", "1,2", "--heading", "3"], "BAD_PARAMS"),
    (["view", "vehicle", "411", "--pos", "1,2,3", "--parts", "door_lf=broken"], "BAD_PARAMS"),
    (["view", "vehicle", "411", "--pos", "1,2,3", "--parts", "no_such_part=dam"], "BAD_PARAMS"),
    (["view", "vehicle", "411", "--pos", "1,2,3", "--dirt", "16"], "BAD_PARAMS"),
    (["view", "vehicle", "411", "--pos", "1,2,3", "--colors", "red"], "BAD_PARAMS"),
    (["view", "ped", "0", "--pos", "1,2,3"], "NOT_FOUND"),
    (["view", "remove", "nope"], "NOT_FOUND"),
    (["view", "remove"], "BAD_PARAMS"),
    (["view", "reload", "nope"], "NOT_FOUND"),
])
def test_errors(run_cli, satk_home, tmp_path, argv, code):
    bad = tmp_path / "bad.dff"
    bad.write_bytes(b"this is not a renderware file")
    argv = [a.format(missing=str(tmp_path / "nope.dff"), bad=str(bad)) for a in argv]
    r = run_cli([*argv, "--target", "mock", "--json"])
    assert r.code != 0 and r.json["error"]["code"] == code, r.out


def test_capture_and_pick_see_placed_entities(run_cli, satk_home):
    run_cli(["view", "vehicle", "411", "--pos", "2495,-1675,13.4", "--id", "car", "--target", "mock"])
    cap = run_cli(["view", "capture", "--target", "mock", "--pos", "2495,-1688,19", "--look", "2495,-1675,13.4",
                   "--width", "320", "--height", "240", "--marks", "8", "--json"]).json
    assert "el:scene/car" in [row[1] for row in cap["legend"]]
    pick = run_cli(["view", "pick", "160", "120", "--capture", cap["id"], "--target", "mock", "--json"]).json
    assert pick["rows"][0][2] == "el:scene/car" and pick["rows"][0][-1] == "exact"
    from satk.viewer.backends.mock import InProcessMockBackend

    applied = InProcessMockBackend().call("view.set", {"hide": ["@car"]})["applied"]  # refs @<handle> work here too
    assert applied["hide"] == ["@car"]
    cap2 = run_cli(["view", "capture", "--target", "mock", "--pos", "2495,-1688,19", "--look", "2495,-1675,13.4",
                    "--width", "320", "--height", "240", "--marks", "8", "--json"]).json
    assert "el:scene/car" not in [row[1] for row in cap2["legend"]]


def test_scene_conformance_inprocess(run_cli, satk_home):
    r = run_cli(["view", "conformance", "--target", "mock", "--only", "^scene\\.", "--show-all", "--json"])
    d = r.json
    assert r.code == 0 and d["fail"] == 0 and d["pass"] == 8 and d["driver"] == "backend"


def test_mock_endpoint_over_saap(run_cli, satk_home, tmp_path):
    """The same operations through a real SAAP connection (mock endpoint + discovery files)."""
    from satk.saap.mock import serve

    ready = threading.Event()
    t = threading.Thread(target=serve, kwargs={"port": 0, "token": secrets.token_hex(32),
                                                "on_ready": lambda s: ready.set()}, daemon=True)
    t.start()
    assert ready.wait(10)
    try:
        dff = _dff(tmp_path / "prop.dff")
        d = run_cli(["view", "place", str(dff), "--pos", "2493,-1671,13.4", "--ground", "--target", "mock",
                     "--json"]).json
        assert d["ok"] and d["grounded"] is True and not d.get("warn")  # a real endpoint: no in-process warning
        v = run_cli(["view", "vehicle", "infernus", "--pos", "2495,-1675,13.4", "--target", "mock", "--json"]).json
        assert v["model_id"] == 411
        lst = run_cli(["view", "list", "--target", "mock", "--json"]).json
        assert lst["total"] == 2  # state lives in the endpoint between CLI calls
        assert run_cli(["view", "remove", "--all", "--target", "mock", "--json"]).json["removed"] == 2
    finally:
        run_cli(["saap", "call", "mock", "quit"])
        t.join(10)


def test_game_target_needs_a_running_endpoint(run_cli, satk_home):
    r = run_cli(["view", "list", "--target", "game", "--json"])
    assert r.code != 0 and r.json["error"]["code"] == "NOT_READY"


def test_old_viewer_build_is_unsupported(monkeypatch):
    from satk.viewscene import api

    class OldViewer:
        warnings: list = []

        def call(self, method, params):
            raise SatkError("UNKNOWN_METHOD", f"unknown method '{method}'")

        def close(self):
            pass

    monkeypatch.setattr("satk.viewer.backends.get_backend", lambda target, **kw: OldViewer())
    with pytest.raises(SatkError) as e:
        api.list_scene("ariane")
    assert e.value.code == "UNSUPPORTED" and "build.ps1" in (e.value.hint or "")


def test_game_without_view_capability_gets_a_hint(monkeypatch):
    from satk.viewscene import api

    class Mta:
        warnings: list = []

        def call(self, method, params):
            raise SatkError("UNSUPPORTED", "capability 'view' not available", data={"capability": "view"})

        def close(self):
            pass

    monkeypatch.setattr("satk.viewer.backends.get_backend", lambda target, **kw: Mta())
    with pytest.raises(SatkError) as e:
        api.vehicle("game", model=411, pos=[1, 2, 3])
    assert e.value.code == "UNSUPPORTED" and "MTA" in e.value.hint


@pytest.mark.parametrize("value, want", [(426, 426), ("426", 426), ("model:426", 426), ("Premier", "premier"),
                                         (None, None), ("", None)])
def test_model_arg(value, want):
    from satk.viewscene.api import model_arg

    assert model_arg(value) == want


def test_parse_helpers():
    from satk.viewscene.api import _heading, parse_colors, parse_parts

    assert parse_parts(["Door_LF=dam", "bonnet=OFF"]) == {"door_lf": "dam", "bonnet": "off"}
    assert parse_parts({"boot": "ok"}) == {"boot": "ok"}
    assert parse_parts(None) is None
    with pytest.raises(SatkError):
        parse_parts(["door_lf"])
    assert parse_colors(["3", "#AbCdEf", 7]) == [3, "#abcdef", 7]
    with pytest.raises(SatkError):
        parse_colors(["1", "2", "3", "4", "5"])
    assert _heading([0, 0, 0.707107, 0.707107]) == 90.0 and _heading([0, 0, 0, 1]) == 0.0
    assert _heading([0.1, 0, 0, 0.99]) is None and _heading(None) is None


def test_schemas_cover_the_scene_methods():
    from satk.saap import schema as S

    m = S.methods()
    for name in ("place", "vehicle", "ped", "reload", "remove", "clear", "list"):
        assert m[f"scene.{name}"] == "view"
    assert S.validate_params("scene.vehicle", {"model": 411, "pos": [1, 2, 3], "colors": [3, "#ff0000"],
                                               "parts": {"door_lf": "dam"}, "wheels": {"scale": [0.7, 0.8]}}) == []
    assert S.validate_params("scene.vehicle", {"model": 411, "pos": [1, 2, 3], "parts": {"door_lf": "bent"}})
    assert S.validate_params("scene.place", {"pos": [1, 2, 3]})  # neither dff nor model
    assert S.validate_params("scene.remove", {})  # handle or handles
