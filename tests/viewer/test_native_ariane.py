"""Native SAAP/1 endpoint of the Ariane fork (P1, M2-11): launcher detection and the viewer API on it.

Unit tests use a stand-in: the fake ``ariane.exe`` start is replaced by an in-process SAAP server
(the mock world) that writes the descriptor the real endpoint writes (``SATK_AGENT_DESCRIPTOR``).
The live test (``SATK_TEST_LIVE=1``, a build with ``saap_endpoint``) opens the real viewer against
``gta-sa-clean`` in a private work directory: full conformance, a 1920x1080 capture at a 960x540
window with ID and depth layers, and a visible pick on a tree crown that has no collision.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from satk.saap import client as C
from satk.saap.mock import MockWorld
from satk.saap.server import SaapServer
from satk.viewer import launcher
from satk.viewer.backends.ariane_legacy import ArianeLegacyBackend


def _fake_install(ws: Path, *, saap_endpoint: bool) -> Path:
    exe = ws / "viewer" / "ariane" / "bin" / "ariane.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    info = {"schema": 1, "ariane_commit": "0" * 40, "satk_build": True}
    if saap_endpoint:
        info["saap_endpoint"] = True
    (exe.parent / "ariane.build.json").write_text(json.dumps(info), encoding="utf-8")
    game = ws / "gta-sa-clean"
    game.mkdir()
    (game / "gta_sa.exe").write_bytes(b"MZ")
    return exe


class _FakeProc:
    """Stands for the started ariane.exe: this test process (alive), plus an optional SAAP server."""

    def __init__(self, args, env, native: bool):
        self.args = list(args)
        self.env = dict(env)
        self.pid = os.getpid()
        self.returncode = None
        self.server = None
        if native:
            self.server = SaapServer(MockWorld(), token=env["SATK_AGENT_TOKEN"], role="ariane").start()
            self.server.write_descriptor(Path(env["SATK_AGENT_DESCRIPTOR"]).parent)

    def poll(self):
        return None


@pytest.fixture
def fake_start(satk_home, monkeypatch):
    """``start(native)`` -> (launcher result, fake process); stops the stand-in afterwards."""
    procs: list[_FakeProc] = []

    def run(native: bool, *, announce: bool | None = None):
        _fake_install(satk_home, saap_endpoint=native if announce is None else announce)

        def popen(args, **kw):
            p = _FakeProc(args, kw["env"], native)
            procs.append(p)
            return p

        monkeypatch.setattr(launcher.subprocess, "Popen", popen)
        monkeypatch.setattr(ArianeLegacyBackend, "ping", lambda self: {"ok": True})
        monkeypatch.setattr(ArianeLegacyBackend, "call", lambda self, m, p=None: {
            "caps": ["core", "camera", "capture"], "build": {"id": "legacy"}, "viewport": {"w": 960, "h": 540}})
        monkeypatch.setattr(launcher, "NATIVE_WAIT_S", 0.3)
        st = launcher.start("ariane", window="960x540")
        return st, procs[-1]

    yield run
    for p in procs:
        if p.server is not None:
            p.server.stop()
    C.remove_discovery("ariane")


def test_native_endpoint_is_used(fake_start):
    st, proc = fake_start(True)
    assert st["up"] is True and st["proto"] == "saap/1" and st["port"] == proc.server.port
    assert "capture.ids" in st["caps"] and st["window"] == [800, 600]
    env = proc.env
    assert env["SATK_AGENT_PORT"] == "0" and env["SATK_AGENT_TOKEN"] == env["ARIANE_ENGINE_TOKEN"]
    assert env["SATK_AGENT_ROLE"] == "ariane"
    assert Path(env["SATK_AGENT_DESCRIPTOR"]) == C.endpoints_dir() / "ariane.json"
    assert env["SATK_AGENT_OUT_ROOT"] == str(launcher.paths.cfg().paths.work).replace("\\", "/")
    ep, sess = C.read_endpoint("ariane"), C.read_session("ariane")
    assert ep["protocol"] == "saap/1" and sess["saap_port"] == proc.server.port and sess["token"] == env["SATK_AGENT_TOKEN"]
    s = launcher.status("ariane")
    assert s["up"] is True and s["proto"] == "saap/1" and s["window"] == [800, 600]


def test_viewer_api_on_native_endpoint(fake_start):
    """Capture size, ID legend marks and visible picks go through the native backend."""
    from satk.viewer import api
    from satk.viewer.backends import get_backend
    from satk.viewer.backends.saap_native import SaapNativeBackend

    fake_start(True)
    b = get_backend("ariane")
    try:
        assert isinstance(b, SaapNativeBackend) and b.has("capture.size") and b.has("pick.visible")
    finally:
        b.close()
    pose = {"pos": [2489.3, -1720.0, 60.0], "look": [2489.3, -1668.5, 12.3], "fov_h_deg": 70}
    cap = api.capture("ariane", pose, w=320, h=240, layers=["color", "depth"], marks=2)
    assert (cap["w"], cap["h"]) == (320, 240) and cap.get("ids_file") and cap.get("depth_file")
    assert "approx" not in cap and len(cap["legend"]) >= 1
    pk = api.pick([[160, 120]], target="ariane")
    assert pk["mode"] == "visible" and len(pk["rows"]) == 1


def test_old_build_falls_back_to_the_bridge(fake_start):
    st, proc = fake_start(False)
    assert st["proto"] == "ariane-ipc/1" and st["impl"] == "ariane-legacy"
    assert "SATK_AGENT_TOKEN" in proc.env  # harmless for builds without the endpoint
    assert C.read_endpoint("ariane")["protocol"] == "ariane-ipc/1"


def test_announced_endpoint_missing_falls_back(fake_start):
    """A build that says it has the endpoint but writes no descriptor: bridge after NATIVE_WAIT_S."""
    t0 = time.monotonic()
    st, _ = fake_start(False, announce=True)
    assert st["proto"] == "ariane-ipc/1" and time.monotonic() - t0 >= 0.3


def test_native_descriptor_of_another_pid_is_ignored(satk_home):
    C.write_endpoint("ariane", {"protocol": "saap/1", "role": "ariane", "pid": 4, "port": 1})
    assert launcher._native_endpoint(os.getpid(), False) is None
    C.write_endpoint("ariane", {"protocol": "ariane-ipc/1", "role": "ariane", "pid": os.getpid(), "port": 1})
    assert launcher._native_endpoint(os.getpid(), False) is None
    C.write_endpoint("ariane", {"protocol": "saap/1", "role": "ariane", "pid": os.getpid(), "port": 5})
    assert launcher._native_endpoint(os.getpid(), False)["port"] == 5


# --------------------------------------------------------------------------- live


def _live_exe() -> Path | None:
    from satk.core.paths import cfg

    exe = Path(cfg().paths.viewer)
    info = launcher.ariane_build_info(exe) if exe.is_file() else {}
    return exe if info.get("saap_endpoint") else None


@pytest.fixture
def live_work(monkeypatch, request):
    """Private SATK_PATHS_WORK under the real work/tmp (removed afterwards)."""
    from satk.core import config as _config
    from satk.core.paths import cfg
    from satk.docs.cleanup import remove_tree

    base = Path(cfg().paths.work) / "tmp" / "M2-11" / "live-test"
    if base.exists():
        remove_tree(base)
    monkeypatch.setenv("SATK_PATHS_WORK", str(base))
    _config.reset()
    request.addfinalizer(_config.reset)
    yield base
    remove_tree(base)


@pytest.mark.viewer
@pytest.mark.slow
@pytest.mark.skipif(os.environ.get("SATK_TEST_LIVE") != "1", reason="live Ariane test: set SATK_TEST_LIVE=1")
def test_live_native_ariane(run_cli, live_work):
    import numpy as np

    from satk.core.paths import cfg
    from satk.saap import png as P
    from satk.viewer.overlays import image_stats

    if _live_exe() is None:
        pytest.skip("the configured ariane.exe has no native SAAP endpoint (rebuild the fork)")
    root = cfg().paths.game
    snap = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}
    r = run_cli(["view", "start", "--target", "ariane", "--window", "960x540", "--json"])
    try:
        assert r.code == 0, r.out
        st = r.json
        assert st["proto"] == "saap/1" and st["window"] == [960, 540]
        conf = run_cli(["view", "conformance", "--target", "ariane", "--json"]).json
        assert conf["driver"] == "saap" and conf["fail"] == 0 and conf["pass"] >= 40, conf["rows"]
        cap = run_cli(["view", "capture", "--pos", "2495,-1720,60", "--look", "2495,-1670,15", "--width", "1920",
                       "--height", "1080", "--layers", "color,ids,depth", "--marks", "5", "--json"]).json
        assert (cap["w"], cap["h"]) == (1920, 1080) and cap["settled"] is True and "approx" not in cap
        assert P.size(cap["file"]) == (1920, 1080) and P.size(cap["ids_file"]) == (1920, 1080)
        assert os.path.getsize(cap["depth_file"]) == 1920 * 1080 * 4
        stats = image_stats(cap["file"])
        assert stats["luma_std"] > 15 and stats["black_share"] < 0.05
        sc = json.loads(Path(cap["sidecar"]).read_text(encoding="utf-8"))
        legend = sc["ids_legend"]
        assert legend and all(e[1].get("src", {}).get("kind") in ("ipl_bin", "ipl_text") for e in legend)
        assert all(row[1] and row[1].startswith("inst:") for row in cap["legend"])
        depth = np.fromfile(cap["depth_file"], dtype="<f4")
        assert np.isfinite(depth).mean() > 0.99 and 30 < float(np.nanmin(depth[np.isfinite(depth)])) < 80
        # a tree crown: drawn (visible pick hits the tree), no collision there (the ray goes to the ground)
        from PIL import Image

        ids = np.asarray(Image.open(cap["ids_file"]).convert("RGB")).astype(np.uint32)
        idv = ids[..., 0] << 16 | ids[..., 1] << 8 | ids[..., 2]
        tree = next(e for e in legend if "palm" in (e[1].get("model_name") or ""))
        ys, xs = np.nonzero(idv == tree[0])
        top = np.argsort(ys)[: max(1, len(ys) // 3)]
        px, py = int(xs[top[len(top) // 2]]), int(ys[top[len(top) // 2]])
        pose = {"pos": [2495, -1720, 60], "look": [2495, -1670, 15], "fov_h_deg": 70}
        hits = {}
        c = C.connect("ariane", timeout=120)
        try:
            for mode in ("visible", "collision"):
                hits[mode] = c.call("pick", {"points": [[px, py]], "space": "capture", "w": 1920, "h": 1080,
                                             "pose": pose, "mode": mode})["hits"][0]
        finally:
            c.close()
        assert hits["visible"]["entity"]["model_name"] == tree[1]["model_name"]
        assert (hits["collision"].get("entity") or {}).get("ref") != hits["visible"]["entity"]["ref"]
    finally:
        stop = run_cli(["view", "stop", "--json"]).json
    assert stop["stopped"] is True
    after = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}
    assert after == snap
