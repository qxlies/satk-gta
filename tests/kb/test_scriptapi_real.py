"""Real-data acceptance of the scripting API references: the local MTA source and any configured Pawn includes.

Builds only the scripting API part of the KB into a temp directory (the shared ``work/kb`` is not touched) from
``satk.kb.scriptapi_build.default_inputs()`` (``paths.engine`` or ``<src>/mtasa-neon``; ``kb.pawn_include``).
Marked ``game`` and ``slow`` like the other real-source KB tests; the donor clones must stay clean.
"""

from __future__ import annotations

import os
import time

import pytest

pytestmark = [pytest.mark.game, pytest.mark.slow]


def _skip(reason: str):
    if os.environ.get("SATK_TEST_NO_SKIP") == "1":
        pytest.fail(reason, pytrace=False)
    pytest.skip(reason)


def _status(tree) -> str | None:
    from satk.re.gitsrc import GitTree, run_git

    if not isinstance(tree, GitTree):
        return None
    return run_git(tree.repo, "status", "--porcelain", "--untracked-files=no").decode("utf-8", "replace")


@pytest.fixture(scope="module")
def real(tmp_path_factory):
    from satk.kb.build import Inputs, build_kb
    from satk.kb.scriptapi_build import default_inputs

    sa = default_inputs()
    if sa.mta is None:
        _skip(f"no MTA:SA source tree: {sa.skipped.get('mta-lua')}")
    trees = [sa.mta, sa.neon] + [t for _lb, t in sa.pawn]
    before = [_status(t) for t in trees]
    out = tmp_path_factory.mktemp("kbsa") / "kb.sqlite"
    t0 = time.perf_counter()
    stats = build_kb(out, Inputs(scriptapi=sa))
    elapsed = time.perf_counter() - t0
    return out, stats, elapsed, before, [_status(t) for t in trees]


def test_build_fast_and_sources_clean(real):
    _out, st, elapsed, before, after = real
    assert elapsed <= 60, f"scripting API build took {elapsed:.0f} s"
    assert before == after, "a source tree changed while building"
    m = st["scriptapi"]["mta-lua"]
    assert m["names"] >= 1400 and m["client"] >= 1100 and m["server"] >= 700
    assert m["argparser"] >= 300 and m["argreader"] >= 1200 and m["unresolved"] <= 5
    assert m["classes"] >= 60 and m["enums"] >= 80 and m["events"] >= 200


def test_counts_overview(real):
    from satk.kb.scriptapi_query import overview

    ov = overview(path=real[0])
    f = ov["functions"]
    assert f["total"] >= 1400 and f["shared"] >= 500 and f["client"] >= 500 and f["server"] >= 200
    assert ov["events"] >= 200 and ov["classes"] >= 60


def test_engine_request_model(real):
    from satk.kb.scriptapi_query import mta

    env = mta("engineRequestModel", path=real[0])
    assert env["name"] == "engineRequestModel" and env["side"] == "client"
    assert env["sig"].startswith("int|false engineRequestModel(string ") and "[int parentID]" in env["sig"]
    assert "ped" in env["enums"]["client-model-type"].split("|")
    assert env["loc"].startswith("Client/mods/deathmatch/logic/luadefs/CLuaEngineDefs.cpp:")


def test_set_vehicle_handling(real):
    from satk.kb.scriptapi_query import mta

    env = mta("setVehicleHandling", path=real[0])
    assert env["side"] == "shared" and "server" in env
    assert "bool" in env["sig"].split(" setVehicleHandling(")[0] and "setVehicleHandling(vehicle " in env["sig"]
    assert "Vehicle:setHandling" in env["oop"]
    assert "string property" in (env.get("doc") or env["server"].get("doc", ""))


def test_argument_parser_function_exact(real):
    from satk.kb.scriptapi_query import mta

    env = mta("engineSetModelFlags", path=real[0])
    assert env["sig"] == "bool engineSetModelFlags(int modelID, int flags, [bool ideFlags])"
    assert env["impl"].endswith("(ArgumentParser)") and "notes" not in env


def test_events_classes_typos(real):
    from satk.core.errors import SatkError
    from satk.kb.scriptapi_query import mta

    ev = mta("onClientElementStreamIn", event=True, path=real[0])
    assert ev["side"] == "client" and ev["kind"] == "event"
    cls = mta("Vehicle", path=real[0])
    assert cls["kind"] == "class" and cls["parent"] == "Element" and cls["members"]["total"] >= 100
    with pytest.raises(SatkError) as ei:
        mta("engineRequestModle", path=real[0])
    assert ei.value.code == "NOT_FOUND" and ei.value.did_you_mean[0] == "engineRequestModel"


def test_native_set_object_material(real):
    """With include folders configured the real declaration, else the built-in map native."""
    from satk.kb.scriptapi_query import native

    env = native("SetObjectMaterial", path=real[0])
    assert env["kind"] == "native" and env["params"] >= 5
    assert env["sig"].startswith("native SetObjectMaterial(") and "= 0" in env["sig"]
    if real[1]["scriptapi"]["pawn"].get("files"):
        assert env["loc"].endswith(".inc:" + env["loc"].rsplit(":", 1)[1])
    else:
        assert env["inc"] == "satk-mapconv"
