"""satk dev gate: machine-wide slots (at most N full gates at once), stale cleanup, the environment override."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from satk.core.errors import SatkError
from satk.docs import gateslots as S


@pytest.fixture
def slots(satk_home):
    return S.slots_dir()


def test_limit_default_and_override(monkeypatch):
    monkeypatch.delenv(S.ENV_SLOTS, raising=False)
    assert S.slot_limit() == S.DEFAULT_SLOTS == 2
    monkeypatch.setenv(S.ENV_SLOTS, "5")
    assert S.slot_limit() == 5
    monkeypatch.setenv(S.ENV_SLOTS, "0")
    assert S.slot_limit() == 0
    monkeypatch.setenv(S.ENV_SLOTS, "many")  # garbage falls back to the default
    assert S.slot_limit() == 2
    assert S.slot_limit({S.ENV_SLOTS: "3"}) == 3


def test_two_slots_then_the_third_times_out(slots, monkeypatch):
    monkeypatch.delenv(S.ENV_SLOTS, raising=False)
    said: list[str] = []
    a = S.acquire("a", say=said.append)
    b = S.acquire("b", say=said.append)
    assert {a.index, b.index} == {0, 1} and sorted(p.name for p in slots.glob("slot-*.json")) == ["slot-0.json", "slot-1.json"]
    with pytest.raises(SatkError) as ei:
        S.acquire("c", poll=0.05, notice_every=0.0, timeout=0.3, say=said.append)
    assert ei.value.code == "TIMEOUT" and "2 in use" in ei.value.msg
    assert any("waiting for a free gate slot" in m and "2 of 2 in use" in m and "pid" in m for m in said)
    a.release()
    c = S.acquire("c", say=said.append)  # the freed slot is taken again
    assert c.index == a.index
    for s in (b, c):
        s.release()
    assert not list(slots.glob("slot-*.json"))
    b.release()  # release is idempotent


def test_slot_env_override_and_unlimited(slots, monkeypatch):
    monkeypatch.setenv(S.ENV_SLOTS, "1")
    with S.hold("one"):
        with pytest.raises(SatkError):
            S.acquire("two", poll=0.05, timeout=0.2)
    monkeypatch.setenv(S.ENV_SLOTS, "0")
    held = [S.acquire(f"g{i}") for i in range(5)]  # unlimited: nothing is created, nobody waits
    assert all(h.path is None for h in held) and not list(slots.glob("slot-*.json"))


def _write_owner(slots: Path, i: int, **owner) -> Path:
    p = slots / f"slot-{i}.json"
    p.write_text(json.dumps(owner), encoding="utf-8")
    return p


def _dead_pid() -> int:
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def test_stale_slots_are_taken_over(slots, monkeypatch):
    monkeypatch.delenv(S.ENV_SLOTS, raising=False)
    now = time.time()
    _write_owner(slots, 0, pid=_dead_pid(), started=now, label="crashed")  # a gate that died: pid gone
    from satk.saap.client import pid_created

    _write_owner(slots, 1, pid=os.getpid(), pid_created=(pid_created(os.getpid()) or now) - 3600, started=now,
                 label="pid-reuse")  # the pid is alive but is another process than the recorded one
    assert [r["state"] for r in S.list_slots()] == ["stale", "stale"]
    a, b = S.acquire("x"), S.acquire("y")
    assert {a.index, b.index} == {0, 1}
    owners = [json.loads((slots / f"slot-{i}.json").read_text(encoding="utf-8")) for i in (0, 1)]
    assert {o["label"] for o in owners} == {"x", "y"} and all(o["pid"] == os.getpid() for o in owners)
    a.release(), b.release()


def test_live_owner_is_respected_and_old_owner_is_not(slots, monkeypatch):
    monkeypatch.setenv(S.ENV_SLOTS, "1")
    from satk.saap.client import pid_created

    live = dict(pid=os.getpid(), pid_created=pid_created(os.getpid()), started=time.time(), label="me")
    p = _write_owner(slots, 0, **live)
    assert S.owner_state(p)[0] == "live"
    with pytest.raises(SatkError):
        S.acquire("other", poll=0.05, timeout=0.2)
    p.write_text(json.dumps({**live, "started": time.time() - S.MAX_AGE - 10}), encoding="utf-8")  # a hung gate
    assert S.owner_state(p)[0] == "stale"
    S.acquire("other", poll=0.05, timeout=1).release()


def test_half_written_slot_is_busy_for_a_moment_then_garbage(slots, monkeypatch):
    monkeypatch.setenv(S.ENV_SLOTS, "1")
    p = slots / "slot-0.json"
    p.write_text("", encoding="utf-8")  # created by another process, content not there yet
    assert S.owner_state(p)[0] == "partial"
    with pytest.raises(SatkError):
        S.acquire("w", poll=0.05, timeout=0.2)
    old = time.time() - 60
    os.utime(p, (old, old))
    assert S.owner_state(p)[0] == "stale"
    S.acquire("w", poll=0.05, timeout=1).release()


def test_unusable_slot_folder_fails_open(satk_home, monkeypatch):
    def boom():
        raise OSError("read-only work")

    monkeypatch.setattr(S, "slots_dir", boom)
    said: list[str] = []
    slot = S.acquire("g", say=said.append)
    assert slot.path is None and "without a slot" in said[0]


_WORKER = textwrap.dedent("""
    import json, sys, time
    from pathlib import Path
    from satk.docs import gateslots as S
    out, name = Path(sys.argv[1]), sys.argv[2]
    (out / f"started-{name}").write_text("x")
    with S.hold(name, poll=0.05, notice_every=0.2, say=lambda m: print(m, file=sys.stderr)) as slot:
        t0 = time.time()
        (out / f"acquired-{name}").write_text("x")
        deadline = time.time() + 120
        while not (out / "go").exists() and time.time() < deadline:  # hold the slot until the test lets go
            time.sleep(0.05)
        t1 = time.time()
    (out / f"done-{name}.json").write_text(json.dumps({"name": name, "t0": t0, "t1": t1, "waited": slot.waited}))
""")


def _wait(cond, seconds=90.0):
    t0 = time.time()
    while not cond():
        assert time.time() - t0 < seconds, "timed out"
        time.sleep(0.05)


def test_three_concurrent_dummy_gates_never_run_more_than_two(satk_home, tmp_path, monkeypatch):
    """Three processes compete for two slots: two hold one, the third waits until one lets go, then runs.

    Deterministic whatever the machine load: the holders wait for a ``go`` file instead of a timer.
    """
    monkeypatch.delenv(S.ENV_SLOTS, raising=False)
    src = Path(__file__).resolve().parents[2] / "src"
    env = {**os.environ, "PYTHONPATH": str(src), "PYTHONUTF8": "1", "SATK_HOME": str(satk_home), "SATK_CONFIG": "none"}
    for k in [k for k in env if k.startswith("SATK_") and k not in ("SATK_HOME", "SATK_CONFIG")]:
        env.pop(k)
    procs = [subprocess.Popen([sys.executable, "-c", _WORKER, str(tmp_path), f"g{i}"], env=env, stderr=subprocess.PIPE,
                              text=True) for i in range(3)]
    try:
        _wait(lambda: len(list(tmp_path.glob("started-*"))) == 3 and len(list(tmp_path.glob("acquired-*"))) >= 2)
        time.sleep(1.5)  # all three are up; the third must still be waiting
        assert len(list(tmp_path.glob("acquired-*"))) == 2
        assert sorted(p.name for p in S.slots_dir().glob("slot-*.json")) == ["slot-0.json", "slot-1.json"]
        assert [r["state"] for r in S.list_slots()] == ["live", "live"]
        (tmp_path / "go").write_text("go")
        errs = [p.communicate(timeout=120)[1] for p in procs]
    finally:
        (tmp_path / "go").write_text("go")
        for p in procs:
            if p.poll() is None:
                p.kill()
    assert [p.returncode for p in procs] == [0, 0, 0], errs
    runs = sorted((json.loads(f.read_text(encoding="utf-8")) for f in tmp_path.glob("done-*.json")), key=lambda r: r["t0"])
    assert len(runs) == 3
    events = sorted([(r["t0"], 1) for r in runs] + [(r["t1"], -1) for r in runs], key=lambda e: (e[0], e[1]))
    live = peak = 0
    for _, d in events:
        live += d
        peak = max(peak, live)
    assert peak == 2, runs
    assert runs[2]["t0"] >= min(runs[0]["t1"], runs[1]["t1"]) and runs[2]["waited"] > 1.0  # it started after one let go
    assert any("waiting for a free gate slot" in e for e in errs)
    assert not list(S.slots_dir().glob("slot-*.json"))
