"""The studio with the real Blender 5.1 (markers blender, slow): session, warm latency, cold one-shot, stop.

The work directory is redirected (``SATK_PATHS_WORK``) to a temp dir: the isolated Blender profile, the
session folder and the jobs live there. The session is started with ``owner_pid`` = this process, so a
crashed test run cannot leave a Blender behind. Reads nothing of the game.
"""

from __future__ import annotations

import os
import statistics
import time
from pathlib import Path

import pytest

from satk.core import config
from satk.core.errors import SatkError
from satk.core.registry import get_op
from satk.saap import client as C
from satk.saap import conformance as CF
from satk.studio import api
from satk.studio import launcher as L
from satk.studio.mock import primitive_counts

pytestmark = [pytest.mark.blender, pytest.mark.slow]

NAME = "live"


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    from satk.docs.cleanup import remove_tree

    work = tmp_path_factory.mktemp("studio") / "work"
    work.mkdir()
    old = os.environ.get("SATK_PATHS_WORK")
    os.environ["SATK_PATHS_WORK"] = str(work)
    config.reset()
    t0 = time.monotonic()
    st = L.start(NAME, owner_pid=os.getpid(), idle=600)
    st["start_s"] = time.monotonic() - t0
    try:
        yield work, st
    finally:
        api.close_all()
        try:
            L.stop(NAME)
        finally:
            if old is None:
                os.environ.pop("SATK_PATHS_WORK", None)
            else:
                os.environ["SATK_PATHS_WORK"] = old
            config.reset()
            remove_tree(work.parent)


def _call(method: str, params: dict | None = None, **kw) -> dict:
    return api.call(method, params, session=NAME, **kw)


def test_session_is_up(live):
    work, st = live
    assert st["up"] is True and st["caps"] == ["core", "author"] and st["methods"] >= 4
    assert st["blender"].startswith("5.") and st["start_s"] < 30
    assert (work / "run" / "endpoints" / f"blender-{NAME}.json").is_file()
    again = L.start(NAME)  # a running session is reused, not doubled
    assert again["reused"] is True and again["pid"] == st["pid"]


def test_primitives_match_the_mock_counts(live):
    _call("scene.clear")
    cases = [("plane", {}), ("grid", {"x_segments": 3, "y_segments": 2}), ("cube", {"size": [1, 2, 3]}),
             ("cylinder", {"segments": 12}), ("cylinder", {"segments": 8, "cap": "none"}), ("cone", {"segments": 12}),
             ("cone", {"segments": 6, "radius_top": 0.2}), ("uv_sphere", {"segments": 8, "rings": 6}),
             ("ico_sphere", {"subdivisions": 2}), ("circle", {"segments": 10})]
    for i, (kind, p) in enumerate(cases):
        p = dict(p, kind=kind, name=f"p{i}", location=[i * 3, 0, 0])
        r = _call("mesh.primitive", p)
        row = r["stats"]["objects"][f"p{i}"]
        assert (row["verts"], row["tris"]) == primitive_counts(kind, p), (kind, row)
        assert not r.get("warn"), r
    cube = _call("scene.info", {"match": "p2"})["result"]["objects"][0]
    assert cube["dims"] == [1.0, 2.0, 3.0] and cube["tris"] == 12


def test_warm_latency_snapshot_and_errors(live):
    work, _ = live
    _call("scene.clear")
    lat = []
    for i in range(20):
        t = time.perf_counter()
        _call("mesh.primitive", {"kind": "cylinder", "segments": 16, "radius": 0.2, "depth": 2, "name": f"post{i}",
                                 "location": [i, 0, 1]})
        lat.append(time.perf_counter() - t)
    med = statistics.median(lat)
    assert med <= 0.150, f"warm call median {med * 1000:.1f} ms"
    snaps = []
    for view in ("3q", "front", "top"):
        t = time.perf_counter()
        r = _call("scene.info", {"limit": 1}, snapshot=view)
        snaps.append((time.perf_counter() - t, Path(r["snapshot"])))
    for secs, p in snaps:
        assert p.is_file() and p.read_bytes()[:2] == b"\xff\xd8" and p.stat().st_size <= 40_000
        assert secs <= 1.5, f"snapshot {p.name}: {secs * 1000:.0f} ms"  # a busy machine; the median is the target
        assert Path(os.path.commonpath([p, work])) == work
    snap_med = statistics.median(s for s, _ in snaps)
    assert snap_med <= 0.300, f"warm call + 512 px snapshot median {snap_med * 1000:.0f} ms"
    with pytest.raises(SatkError) as ei:
        _call("python", {"code": "x = 1\nraise RuntimeError('boom')"})
    assert ei.value.code == "BAD_PARAMS" and ei.value.data["line"] == 2
    with pytest.raises(SatkError) as ei:
        _call("python", {"code": "import sys\nsys.exit(3)"})  # must not end the session
    assert ei.value.code == "BAD_PARAMS"
    assert _call("scene.info", {"limit": 0})["result"]["total"] == 20  # alive


def test_python_is_journaled_with_a_checkpoint(live):
    work, _ = live
    r = _call("python", {"code": "import bpy\nbpy.data.objects['post0'].location.z = 5\nchanged = ['post0']\n"
                                  "result['n'] = len(bpy.data.objects)"})
    assert r["result"]["value"] == {"n": 20} and r["changed"] == ["post0"]
    assert Path(r["checkpoint"]).is_file() and r["checkpoint"].endswith(f"{r['n']:04d}.blend")
    journal = work / "studio" / NAME / "journal.jsonl"
    import json

    last = json.loads(journal.read_text(encoding="utf-8").splitlines()[-1])
    assert last["method"] == "python" and last["code_sha256"] and last["checkpoint"] == r["checkpoint"]


def test_conformance_author_cases_on_blender(live):
    ep, sess = C.read_endpoint(f"blender-{NAME}"), C.read_session(f"blender-{NAME}")
    cases, errs = CF.load_cases([CF.CONFORMANCE_DIR / "author.jsonl", CF.CONFORMANCE_DIR / "core.jsonl"])
    assert errs == []
    rep = CF.run(CF.SaapDriver("127.0.0.1", int(ep["port"]), sess["token"]), cases)
    assert rep["fail"] == 0, [r for r in rep["results"] if r[2] == "fail"]
    assert rep["pass"] >= 10


def test_save_then_cold_one_shot_on_the_file(live):
    work, _ = live
    out = work / "out" / "posts.blend"
    r = _call("scene.info", {"limit": 0}, save=str(out))
    assert Path(r["saved"]) == out and out.is_file()
    with pytest.raises(SatkError) as ei:
        _call("scene.info", save=str(work.parent / "outside.blend"))
    assert ei.value.code == "PROTECTED_PATH"
    # no session given and no "default" session running -> a fresh Blender on that file (same world)
    t = time.perf_counter()
    cold = api.call("mesh.primitive", {"kind": "cube", "name": "crate"}, blend=str(out),
                    save=str(work / "out" / "posts2.blend"))
    assert cold["mode"] == "cold" and time.perf_counter() - t < 60
    assert cold["stats"]["scene"]["objects"] == 21 and cold["changed"] == ["crate"]
    again = get_op("blender.call").call({"method": "scene.info", "params": {"limit": 0},
                                         "blend": str(work / "out" / "posts2.blend")})
    assert again["mode"] == "cold" and again["result"]["total"] == 21
    # the warm session opens a file too
    warm = _call("scene.info", {"limit": 0}, blend=str(work / "out" / "posts2.blend"))
    assert warm["session"] == NAME and warm["result"]["total"] == 21
    assert warm["result"]["file"].endswith("posts2.blend")


def test_stop_ends_only_this_session(live):
    work, st = live
    pid = st["pid"]
    out = L.stop(NAME)
    assert out["stopped"] is True and out["how"] == "quit" and not C.pid_alive(pid)
    assert C.read_endpoint(f"blender-{NAME}") is None and C.read_session(f"blender-{NAME}") is None
    assert L.status(NAME)["up"] is False
    assert C.pid_alive(os.getpid())
    pyc = [p for p in (Path(L.main_script()).parents[1]).rglob("__pycache__")]
    assert pyc == [], pyc  # the GPL side never leaves bytecode in the checkout
