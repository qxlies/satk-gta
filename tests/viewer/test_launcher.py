"""Launcher: mock endpoint as a real process; Ariane start errors; opt-in live Ariane acceptance.

The live test needs the built viewer and ``SATK_TEST_LIVE=1`` (it opens a visible Ariane window
on the real ``gta-sa-clean`` for ~15 s and writes captures under the real ``work`` directory).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from satk.core.errors import SatkError
from satk.saap import client as C
from satk.viewer import launcher


@pytest.mark.slow
def test_mock_process_start_status_stop(satk_home):
    st = launcher.start("mock", wait=30)
    try:
        assert st["up"] is True and st["proto"] == "saap/1" and "capture.ids" in st["caps"]
        assert st["window"] == [800, 600]  # same shape as status and reuse
        pid = st["pid"]
        assert C.pid_alive(pid)
        assert C.verify_pid(C.read_endpoint("mock"), C.read_session("mock"))[0] == "ok"
        again = launcher.start("mock")
        assert again.get("reused") is True and again["pid"] == pid and again["window"] == [800, 600]
        s = launcher.status("mock")
        assert s["up"] and s["window"] == [800, 600]
    finally:
        out = launcher.stop("mock")
    assert out["stopped"] is True and out["how"] == "quit"
    assert not C.pid_alive(pid)
    assert C.read_endpoint("mock") is None and C.read_session("mock") is None
    assert launcher.stop("mock")["note"] == "not running"


# --------------------------------------------------------------------------- stale files, reused pids


@pytest.fixture
def bystander():
    """An unrelated live process of this user (stands for whatever reuses a dead viewer's pid)."""
    exe = getattr(sys, "_base_executable", None) or sys.executable  # not the venv launcher: one process
    p = subprocess.Popen([exe, "-c", "import time; time.sleep(120)"], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t_end = time.monotonic() + 10
    while C.process_info(p.pid) is None and time.monotonic() < t_end:
        time.sleep(0.05)
    yield p
    p.kill()
    p.wait(10)


@pytest.fixture
def no_force(monkeypatch):
    """Fail the test if anything would post WM_CLOSE to or terminate a process."""
    def boom(*a, **k):
        raise AssertionError("WM_CLOSE/terminate sent to an unverified pid")

    monkeypatch.setattr(launcher, "close_windows", boom)
    monkeypatch.setattr(launcher, "terminate", boom)


def _stale_files(role: str, pid: int, **extra) -> None:
    ep = {"protocol": "ariane-ipc/1" if role == "ariane" else "saap/1", "role": role,
          "impl": "ariane-legacy" if role == "ariane" else "satk-mock", "pid": pid, "port": launcher.free_port(),
          "caps": ["core"], **extra}
    C.write_endpoint(role, ep)
    C.write_session(role, {"role": role, "pid": pid, "token": "a" * 64, "args": [], **extra})


STALE_VARIANTS = {
    "other_exe": lambda pid: {"exe": "D:/ws/viewer/ariane/bin/ariane.exe",
                              "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
    "files_older": lambda pid: {"started_at": "2026-01-01T00:00:00Z"},
    "created_differs": lambda pid: {"pid_created": C.pid_created(pid) - 30.0},
    "no_details_no_answer": lambda pid: {},
}


@pytest.mark.parametrize("variant", sorted(STALE_VARIANTS))
@pytest.mark.parametrize("role", ["ariane", "mock"])
def test_stop_never_touches_a_reused_pid(satk_home, bystander, no_force, role, variant):
    """Verifier repro: discovery files naming a live unrelated pid. stop only deletes the files."""
    _stale_files(role, bystander.pid, **STALE_VARIANTS[variant](bystander.pid))
    st = launcher.status(role)
    if variant != "no_details_no_answer":
        assert st["up"] is False and st["stale"] is True and "stale" in st["note"]
    t0 = time.monotonic()
    out = launcher.stop(role)
    assert time.monotonic() - t0 < 5
    assert out["stopped"] is False and out["stale"] is True and out["up"] is False
    assert "not touched" in out["note"]
    assert bystander.poll() is None and C.pid_alive(bystander.pid)  # still running
    assert C.read_endpoint(role) is None and C.read_session(role) is None


def test_start_ariane_replaces_stale_files(satk_home, bystander, no_force):
    _stale_files("ariane", bystander.pid, **STALE_VARIANTS["files_older"](bystander.pid))
    with pytest.raises(SatkError) as e:  # stale -> a fresh start is attempted (viewer not built here)
        launcher.start("ariane")
    assert e.value.code == "NOT_READY" and "build.ps1" in e.value.hint
    assert C.read_endpoint("ariane") is None and bystander.poll() is None


@pytest.mark.slow
def test_start_mock_replaces_stale_files(satk_home, bystander, no_force):
    _stale_files("mock", bystander.pid, **STALE_VARIANTS["created_differs"](bystander.pid))
    st = launcher.start("mock", wait=30)
    try:
        assert st["up"] is True and st["pid"] != bystander.pid and not st.get("reused")
        assert any("stale" in w for w in st["warn"])
    finally:
        out = launcher.stop("mock")
    assert out["stopped"] is True and out["how"] == "quit"
    assert bystander.poll() is None


def test_ariane_start_without_build(satk_home):
    with pytest.raises(SatkError) as e:
        launcher.start("ariane")
    assert e.value.code == "NOT_READY" and "build.ps1" in e.value.hint


def test_start_unsupported_target(satk_home):
    with pytest.raises(SatkError) as e:
        launcher.start("game")
    assert e.value.code == "UNSUPPORTED"


def test_window_spec():
    assert launcher._parse_window("1280x720") == "1280x720+0+0"
    assert launcher._parse_window("800x600+10+-5") == "800x600+10+-5"
    with pytest.raises(SatkError):
        launcher._parse_window("big")


@pytest.mark.viewer
@pytest.mark.slow
@pytest.mark.skipif(os.environ.get("SATK_TEST_LIVE") != "1", reason="live Ariane test: set SATK_TEST_LIVE=1")
def test_live_ariane_acceptance(run_cli):
    """WP-07 acceptance 6 on the real viewer and gta-sa-clean (snapshot proves nothing is written)."""
    from satk.core.paths import cfg
    from satk.viewer.overlays import image_stats

    root = cfg().paths.game
    snap = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}
    t0 = time.monotonic()
    r = run_cli(["view", "start", "--target", "ariane", "--json"])
    try:
        assert r.code == 0, r.out
        st = run_cli(["view", "status", "--json"]).json
        assert st["up"] is True and st["proto"] in ("ariane-ipc/1", "saap/1") and time.monotonic() - t0 <= 90
        cap = run_cli(["view", "capture", "--pos", "2495,-1720,60", "--look", "2495,-1670,15", "--json"]).json
        stats = image_stats(cap["file"])
        # the native endpoint (P1) renders the requested 960x540; the bridge captures the window
        size = (960, 540) if st["proto"] == "saap/1" else (1280, 720)
        assert (cap["w"], cap["h"]) == size and cap["settled"] is True
        assert stats["luma_std"] > 15 and stats["black_share"] < 0.05
        run_cli(["view", "goto", "--pos", "2495,-1720,60", "--look", "2495,-1670,15"])
        pk = run_cli(["view", "pick", "640", "360", "--json"]).json
        row = dict(zip(pk["cols"], pk["rows"][0]))
        assert row["link"] == "exact" and row["id"].startswith("inst:")
    finally:
        stop = run_cli(["view", "stop", "--json"]).json
    assert stop["stopped"] is True
    after = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}
    assert after == snap
