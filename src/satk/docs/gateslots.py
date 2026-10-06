"""Gate slots: at most ``N`` full ``satk dev gate`` runs at the same time on one machine.

Several agents work in parallel, each in its own worktree, and every full gate is a CPU- and disk-heavy run (a test
suite plus a vanilla index build). Three or more of them at once make each other slow and flaky, so a full gate first
takes a slot and waits, with a clear message, while all slots are busy.

* A slot is the file ``<work>/run/gate-slots/slot-<i>.json`` created atomically (``O_EXCL``) with the owner: pid,
  process start time, checkout and start time. Slots live in the shared ``work`` of the machine (not in the private
  per-checkout gate folder), so gates of different worktrees see each other.
* A slot is stale when its owner is gone: the pid is dead, the pid was reused by another process (the recorded start
  time differs, :func:`satk.saap.client.pid_created`), or the file is older than :data:`MAX_AGE` (a gate takes minutes;
  a step has a 30 minute timeout). Stale slots are removed by whoever looks next; nothing needs a manual cleanup.
* Claiming runs under a short-lived lock file, so two waiters never both break the same stale slot.
* ``SATK_GATE_SLOTS`` overrides the number of slots (default 2, ``0`` = unlimited). ``--quick`` and ``--changed`` runs
  do not take a slot: they are short and light.
* Fail open: when the slot folder cannot be used (read-only work, an odd sandbox) the gate warns and runs anyway.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

__all__ = ["DEFAULT_SLOTS", "ENV_SLOTS", "MAX_AGE", "Slot", "acquire", "hold", "slot_limit", "slots_dir", "list_slots",
           "owner_state"]

DEFAULT_SLOTS = 2
ENV_SLOTS = "SATK_GATE_SLOTS"
#: A slot older than this (s) is stale whatever its owner does: nothing in a gate runs that long.
MAX_AGE = 3 * 3600.0
#: A lock file older than this (s) belongs to a crashed claimer.
_LOCK_STALE = 20.0
#: A slot file without readable content is "being written" for this long (s), then it is garbage.
_PARTIAL_GRACE = 5.0


def slot_limit(env: dict[str, str] | None = None) -> int:
    """Number of simultaneous full gates: ``SATK_GATE_SLOTS`` (``0`` = unlimited), else :data:`DEFAULT_SLOTS`."""
    raw = (os.environ if env is None else env).get(ENV_SLOTS, "").strip()
    if not raw:
        return DEFAULT_SLOTS
    try:
        n = int(raw)
    except ValueError:
        return DEFAULT_SLOTS
    return max(0, n)


def slots_dir() -> Path:
    """``<work>/run/gate-slots`` (created)."""
    from ..core import paths

    return paths.work("run", "gate-slots")


def _pid_state(pid: object, created: object) -> str:
    """``alive`` | ``dead`` | ``reused`` for an owner pid and the start time recorded with it."""
    from ..saap.client import PID_CREATED_TOL, pid_alive, pid_created

    if not isinstance(pid, int) or isinstance(pid, bool) or not pid_alive(pid):
        return "dead"
    if isinstance(created, (int, float)) and not isinstance(created, bool):
        live = pid_created(pid)
        if live is not None and abs(live - float(created)) > PID_CREATED_TOL:
            return "reused"
    return "alive"


def _read_json(path: Path) -> dict | None:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def owner_state(path: Path, now: float | None = None) -> tuple[str, dict]:
    """``(state, owner)`` of a slot file: ``live`` | ``stale`` | ``partial`` (just created, content not there yet)."""
    now = time.time() if now is None else now
    owner = _read_json(path)
    if owner is None:
        try:
            age = now - path.stat().st_mtime
        except OSError:
            return "stale", {}
        return ("partial", {}) if age < _PARTIAL_GRACE else ("stale", {})
    started = owner.get("started")
    if isinstance(started, (int, float)) and now - float(started) > MAX_AGE:
        return "stale", owner
    state = _pid_state(owner.get("pid"), owner.get("pid_created"))
    return ("live" if state == "alive" else "stale"), owner


def _describe(owner: dict, now: float) -> str:
    started = owner.get("started")
    running = f", running {_fmt_secs(now - float(started))}" if isinstance(started, (int, float)) else ""
    return f"{owner.get('label') or 'gate'} pid {owner.get('pid', '?')}{running}"


def _fmt_secs(s: float) -> str:
    s = max(0, int(s))
    return f"{s // 60}m{s % 60:02d}s" if s >= 60 else f"{s}s"


def list_slots() -> list[dict]:
    """The slot files that exist now: ``{"slot", "state", "label", "pid", "seconds"}`` (stale ones included)."""
    out = []
    now = time.time()
    try:
        files = sorted(slots_dir().glob("slot-*.json"))
    except OSError:
        return out
    for p in files:
        state, owner = owner_state(p, now)
        started = owner.get("started")
        out.append({"slot": p.stem, "state": state, "label": owner.get("label"), "pid": owner.get("pid"),
                    "seconds": round(now - float(started)) if isinstance(started, (int, float)) else None})
    return out


@contextlib.contextmanager
def _claim_lock(d: Path, deadline: float) -> Iterator[None]:
    """A short critical section around "look at the slots and take one" (a lock file with the same stale rule)."""
    lock = d / "claim.lock"
    fd = None
    while fd is None:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > _LOCK_STALE:
                    lock.unlink()
                    continue
            except OSError:
                continue
            if time.time() > deadline:
                raise TimeoutError("gate slot claim lock is busy")
            time.sleep(0.05)
    try:
        os.write(fd, str(os.getpid()).encode("ascii"))
        os.close(fd)
        fd = None
        yield
    finally:
        if fd is not None:
            os.close(fd)
        try:
            lock.unlink()
        except OSError:
            pass


@dataclass
class Slot:
    """A taken slot; :meth:`release` is idempotent."""

    path: Path | None
    index: int = 0
    waited: float = 0.0

    def release(self) -> None:
        if self.path is not None:
            try:
                self.path.unlink()
            except OSError:
                pass
            self.path = None


def _try_claim(d: Path, limit: int, label: str, root: str) -> tuple[Slot | None, list[tuple[str, dict]]]:
    """One pass: remove stale slots, take the first free one. Returns ``(slot | None, busy owners)``."""
    from ..saap.client import pid_created

    busy: list[tuple[str, dict]] = []
    now = time.time()
    with _claim_lock(d, now + 10.0):
        for i in range(limit):
            p = d / f"slot-{i}.json"
            if p.exists():
                state, owner = owner_state(p, now)
                if state == "stale":
                    try:
                        p.unlink()
                    except OSError:
                        busy.append((p.name, owner))
                        continue
                else:
                    busy.append((p.name, owner))
                    continue
            owner = {"pid": os.getpid(), "pid_created": pid_created(os.getpid()), "label": label, "root": root,
                     "started": round(now, 3)}
            try:
                fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:  # cannot happen under the claim lock, unless a foreign process writes here
                busy.append((p.name, {}))
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(owner))
            return Slot(p, i), busy
    return None, busy


def acquire(label: str = "gate", *, root: str = "", limit: int | None = None, poll: float = 1.0, notice_every: float = 20.0,
            timeout: float | None = None, say: Callable[[str], None] | None = None) -> Slot:
    """Take a gate slot, waiting while all of them are busy; ``say`` receives the waiting messages.

    Returns a no-op slot (``path=None``) when the limit is ``0`` (unlimited) or the slot folder cannot be used.
    ``timeout`` (s) raises ``SatkError(TIMEOUT)``; ``None`` waits as long as it takes.
    """
    from ..core.errors import SatkError

    n = slot_limit() if limit is None else limit
    if n <= 0:
        return Slot(None)
    say = say or (lambda _m: None)
    try:
        d = slots_dir()
    except (OSError, SatkError) as e:
        say(f"gate: slot folder unavailable ({type(e).__name__}: {e}); running without a slot")
        return Slot(None)
    t0 = time.monotonic()
    last = -notice_every
    while True:
        try:
            slot, busy = _try_claim(d, n, label, root)
        except (OSError, TimeoutError) as e:
            say(f"gate: cannot use the slot folder ({type(e).__name__}: {e}); running without a slot")
            return Slot(None)
        waited = time.monotonic() - t0
        if slot is not None:
            slot.waited = round(waited, 1)
            if waited >= 1.0:
                say(f"gate: got slot {slot.index + 1} of {n} after waiting {_fmt_secs(waited)}")
            return slot
        if timeout is not None and waited > timeout:
            raise SatkError("TIMEOUT", f"no gate slot became free in {_fmt_secs(timeout)} ({n} in use)",
                            hint=f"another gate is running ({', '.join(_describe(o, time.time()) for _, o in busy)}); "
                                 f"wait for it, or set {ENV_SLOTS} higher, or use `satk dev gate --changed`/`--quick`",
                            data={"slots": n, "busy": [{"slot": s, **o} for s, o in busy]})
        if waited - last >= notice_every:
            last = waited
            now = time.time()
            say(f"gate: waiting for a free gate slot ({len(busy)} of {n} in use: "
                f"{'; '.join(_describe(o, now) for _, o in busy)}); waited {_fmt_secs(waited)}. "
                f"A full gate waits so that at most {n} run at once (--quick and --changed do not wait; "
                f"{ENV_SLOTS} overrides)")
        time.sleep(poll)


@contextlib.contextmanager
def hold(label: str = "gate", **kw) -> Iterator[Slot]:
    """``with hold("gate-abc123"):`` takes a slot for the block and always releases it."""
    slot = acquire(label, **kw)
    try:
        yield slot
    finally:
        slot.release()


def _main(argv: list[str]) -> int:  # pragma: no cover - ``python -m satk.docs.gateslots`` lists the slots
    rows = list_slots()
    sys.stdout.write(json.dumps({"limit": slot_limit(), "slots": rows}) + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main(sys.argv[1:]))
