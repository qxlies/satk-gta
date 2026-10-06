# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""The studio world: a SAAP endpoint (role ``blender``, caps ``core`` + ``author``) inside Blender.

The dispatch (``author.call``, journal, reply shaping) is ``satk.studio.core`` (MIT, shared with the mock);
this module supplies the Blender host: the method context, stats, snapshots, checkpoints, open/save, and
runs every request on the main thread (``pump.py``). Writes go only under ``out_root`` (the satk work
directory) and the session/job directory.

Reliability:

* after every method :func:`repair_evaluated_refs` puts the originals back where a method stored a
  depsgraph copy in real data (``mesh.materials.append(m)`` with ``m`` from ``evaluated.to_mesh()``); such a
  pointer dangles after the next depsgraph rebuild and crashed (or hung) the session in the next preview;
* every request has a time budget (``author.call`` ``timeout``, else :data:`CALL_BUDGET_S`): a watchdog
  thread raises :class:`CallTimeout` inside the running method when it overruns (an error envelope; the
  session stays usable), and the waiting connection answers ``TIMEOUT`` by itself when Blender is stuck in
  native code; later requests then get ``BUSY`` at once instead of queueing behind it;
* ``hello``, ``ping``, ``status`` and ``quit`` are answered on the connection thread, and the server lock is
  released while a connection waits for the main thread, so ``session status`` and ``session stop`` work
  while a step runs.
"""

from __future__ import annotations

import ctypes
import datetime as _dt
import difflib
import fnmatch
import json
import os
import queue
import sys
import threading
import time
import traceback

import bpy

from satk.core.errors import SatkError
from satk.studio import core as K

from . import pump, snapshot, stats

__all__ = ["Ctx", "BlenderHost", "BlenderWorld", "CallTimeout", "repair_evaluated_refs", "main", "serve", "oneshot",
           "CALL_BUDGET_S", "ANSWER_GRACE_S"]

#: Time budget (s) of a request whose client gives none (``author.call`` ``timeout`` overrides it).
CALL_BUDGET_S = 600.0
#: Bounds of a client's ``timeout``.
BUDGET_MIN_S, BUDGET_MAX_S = 1.0, 7200.0
#: After the budget, the waiting connection answers ``TIMEOUT`` itself this much later (s) when the
#: method could not be stopped (Blender busy inside native code).
ANSWER_GRACE_S = 3.0
#: How often (s) the watchdog looks at the running job, and how often it repeats a stop.
WATCH_S = 0.2
KICK_S = 1.0
#: Requests answered on the connection thread (they touch no ``bpy`` data).
OFF_MAIN = frozenset({"hello", "ping", "status", "quit"})


class CallTimeout(SatkError):
    """Raised inside the main thread (asynchronously, by the watchdog) when a step overruns its budget."""

    def __init__(self, *args):  # noqa: ARG002 - PyThreadState_SetAsyncExc instantiates it without arguments
        super().__init__("TIMEOUT", "the step ran longer than its time budget and was stopped; the steps before it "
                         "stay applied", hint="give it more time (--timeout) or split the work into smaller steps")


def _async_raise(thread_id: int, exc: type | None) -> bool:
    """Raise ``exc`` in the thread ``thread_id`` at its next Python instruction (``None`` clears it)."""
    try:
        n = ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(thread_id),
                                                       ctypes.py_object(exc) if exc is not None else None)
    except Exception:  # noqa: BLE001 - no ctypes API: the connection thread still answers
        return False
    return n == 1


def repair_evaluated_refs() -> int:
    """Replace references to depsgraph (copy-on-evaluation) datablocks held by real data with the originals.

    A method that builds a mesh from ``obj.evaluated_get(dg).to_mesh()`` and copies its ``materials`` stores
    *evaluated* materials in ``bpy.data``; the next depsgraph rebuild frees them and Blender crashes or hangs
    in its evaluation threads. Returns the number of repaired references.
    """
    n = 0
    for coll in (bpy.data.meshes, bpy.data.curves, bpy.data.metaballs):
        for d in coll:
            mats = d.materials
            for i, m in enumerate(mats):
                if m is not None and m.is_evaluated:
                    mats[i] = m.original
                    n += 1
    for o in bpy.data.objects:
        data = o.data
        if data is not None and data.is_evaluated:
            o.data = data.original
            n += 1
        for slot in o.material_slots:
            m = slot.material
            if slot.link == "OBJECT" and m is not None and m.is_evaluated:
                slot.material = m.original
                n += 1
    return n


def _fwd(p: str) -> str:
    return os.path.abspath(p).replace("\\", "/")


def _inside(path: str, root: str) -> bool:
    try:
        a = os.path.normcase(os.path.realpath(path))
        r = os.path.normcase(os.path.realpath(root))
        return os.path.commonpath([a, r]) == r
    except (ValueError, OSError):
        return False


def _atomic_json(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


# --------------------------------------------------------------------------- method context


class Ctx:
    """What a method gets (contract K3): the scene, its output folder and helpers."""

    def __init__(self, host: "BlenderHost", n: int, warns: list[str]):
        self.host = host
        self.n = n
        self._warn = warns

    @property
    def scene(self):
        return bpy.context.scene

    @property
    def out_dir(self) -> str:
        return self.host.dir

    @property
    def journal(self) -> K.Journal:
        return self.host.journal

    def warn(self, text: str) -> None:
        self._warn.append(str(text))

    def stats(self, names: list[str] | None = None) -> dict:
        return stats.collect(names)

    def snapshot(self, view: str = "3q", size: int = 512, look: str = "clay", objects: list[str] | None = None) -> str:
        return self.host.snapshot({"view": view, "size": size, "look": look, "objects": objects}, self.n)

    def obj(self, name: str):
        """The object ``name`` or ``NOT_FOUND`` with close names."""
        o = bpy.data.objects.get(str(name))
        if o is None:
            near = difflib.get_close_matches(str(name), [x.name for x in bpy.data.objects], n=3, cutoff=0.5)
            raise SatkError("NOT_FOUND", f"no object {name!r}", data={"did_you_mean": near} if near else None)
        return o

    def objects(self, pattern: str = "*", types: tuple[str, ...] | None = None) -> list:
        """Objects of the scene whose name matches the glob ``pattern``, sorted by name."""
        return sorted((o for o in self.scene.objects
                       if fnmatch.fnmatchcase(o.name, pattern) and (types is None or o.type in types)),
                      key=lambda o: o.name)

    def collection(self, name: str | None = None):
        """The scene collection, or the collection ``name`` (created under it when missing)."""
        if not name:
            return self.scene.collection
        c = bpy.data.collections.get(name)
        if c is None:
            c = bpy.data.collections.new(name)
        if c.name not in self.scene.collection.children and c is not self.scene.collection:
            try:
                self.scene.collection.children.link(c)
            except RuntimeError:
                pass  # already linked deeper in the hierarchy
        return c


# --------------------------------------------------------------------------- host


class BlenderHost:
    """Host of :class:`satk.studio.core.AuthorCore` in Blender.

    Folders come from the request: ``dir`` (the session or job folder), and with a project ``journal``,
    ``snaps`` and ``checkpoints`` inside the project. ``project`` carries the class bands and target
    dimensions for the stats.
    """

    def __init__(self, req: dict):
        self.dir = os.path.abspath(req["dir"])
        self.out_root = os.path.abspath(req.get("out_root") or self.dir)
        self.journal = K.Journal(req.get("journal"))
        self.snaps = os.path.abspath(req.get("snaps") or os.path.join(self.dir, "snaps"))
        self.dragonff = req.get("dragonff") or None
        self.checkpoints = K.Checkpoints(req.get("checkpoints") or os.path.join(self.dir, "checkpoints"), "blend",
                                         self._save_blend, self._load_blend,
                                         keep=int(req.get("keep") or K.KEEP_CHECKPOINTS))
        proj = req.get("project") or {}
        stats.configure(bands=proj.get("bands"), target_dims=proj.get("target_dims"))
        self._warn: list[str] = []

    def make_ctx(self, n: int, warn: list[str]) -> Ctx:
        self._warn = warn
        return Ctx(self, n, warn)

    def after_method(self, name: str, warn: list[str]) -> None:  # noqa: ARG002 - core hook signature
        """After every method: no real datablock may keep a pointer to a depsgraph copy."""
        n = repair_evaluated_refs()
        if n:
            _log(f"{name}: repaired {n} reference(s) to depsgraph copies")

    def stats(self, names: list[str] | None) -> dict:
        return stats.collect(names)

    def object_names(self) -> list[str]:
        return sorted(o.name for o in stats.geometry_objects())

    def _check_write(self, path: str, what: str) -> str:
        p = os.path.abspath(path)
        if not _inside(p, self.out_root):
            raise SatkError("PROTECTED_PATH", f"{what}: {_fwd(p)} is outside the satk work directory",
                            hint=f"write under {_fwd(self.out_root)}")
        return p

    def snapshot(self, spec: dict, n: int) -> str:
        views = spec.get("views")
        view = str(spec.get("view") or ("sheet" if views else "3q"))
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in view)[:32]
        path = os.path.join(self.snaps, f"{n:04d}-{safe}.jpg")
        return snapshot.take(path, view=view if not views else "3q", size=int(spec.get("size") or 512),
                             look=str(spec.get("look") or "clay"), objects=spec.get("objects"), warn=self._warn,
                             wire=spec.get("wire"), refs=spec.get("refs", True) is not False,
                             ref_alpha=spec.get("ref_alpha"), ghost=str(spec.get("ghost") or "overlay"),
                             views=views)

    @staticmethod
    def _save_blend(path: str) -> None:
        bpy.ops.wm.save_as_mainfile(filepath=path, check_existing=False, copy=True, compress=False,
                                    relative_remap=False)

    @staticmethod
    def _load_blend(path: str) -> None:
        bpy.ops.wm.open_mainfile(filepath=os.path.abspath(path), load_ui=False)

    def check_open(self, path: str) -> None:
        if not str(path).lower().endswith(".blend") or not os.path.isfile(path):
            raise SatkError("NOT_FOUND", f"no .blend file {_fwd(path)}")

    def open(self, path: str) -> None:
        self._load_blend(path)

    def check_save(self, path: str) -> None:
        if not str(path).lower().endswith(".blend"):
            raise SatkError("BAD_PARAMS", f"save: {path!r} must end with .blend")
        self._check_write(path, "save")

    def save(self, path: str) -> str:
        p = self._check_write(path, "save")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        self._save_blend(p)
        return _fwd(p)

    def fingerprint(self) -> tuple:
        """A cheap summary of the scene: changes made outside the methods (GUI edits) alter it."""
        rows = []
        for o in sorted(bpy.data.objects, key=lambda x: x.name):
            row = [o.name, o.type, tuple(round(x, 5) for x in o.matrix_world.translation),
                   tuple(round(x, 5) for x in o.matrix_world.to_euler()),
                   tuple(round(x, 5) for x in o.matrix_world.to_scale()), len(o.modifiers)]
            if o.type == "MESH" and o.data is not None:
                import numpy as np

                me = o.data
                row += [len(me.vertices), len(me.polygons)]
                co = np.empty(len(me.vertices) * 3, dtype=np.float64)
                me.vertices.foreach_get("co", co)
                row.append(round(float((co * np.resize(np.array([1.0, 2.0, 3.0]), co.shape)).sum()), 4))
            rows.append(tuple(row))
        return tuple(rows)


# --------------------------------------------------------------------------- world


class _Job:
    __slots__ = ("method", "params", "result", "error", "done", "budget", "queued", "started", "label",
                 "interrupted", "abandoned", "running", "kicked")

    def __init__(self, method: str, params: dict, budget: float):
        self.method, self.params, self.budget = method, params, float(budget)
        self.result = self.error = None
        self.done = threading.Event()
        self.queued = time.monotonic()
        self.started: float | None = None
        self.interrupted = self.abandoned = self.running = False
        self.kicked = 0.0
        inner = params.get("method") if method == "author.call" else None
        self.label = f"{method} {inner}" if inner else method


def _budget(method: str, params: dict) -> float:
    t = params.get("timeout") if method == "author.call" else None
    if isinstance(t, (int, float)) and not isinstance(t, bool) and t > 0:
        return max(BUDGET_MIN_S, min(BUDGET_MAX_S, float(t)))
    return CALL_BUDGET_S


class BlenderWorld(K.AuthorWorld):
    impl = "satk-blender-studio"

    def __init__(self, req: dict):
        methods, errors = K.discover_methods()
        self.host = BlenderHost(req)
        ver = bpy.app.version_string.split()[0]
        super().__init__(K.AuthorCore(self.host, methods, errors, journal=self.host.journal,
                                      checkpoint_every=int(req.get("checkpoint_every") or 0)),
                         build={"id": f"blender-{ver}"}, info={"blender": ver, "name": req.get("name") or "cold"})
        self.main_ident = threading.get_ident()
        self.jobs: queue.Queue = queue.Queue()
        self.last = time.monotonic()
        self.closing = threading.Event()
        self.mutated_since_ckpt = False
        self.server_lock = None  # the SAAP server's lock, released while a connection waits (serve())
        self.current: _Job | None = None
        self._wd_lock = threading.Lock()
        self._fp = None          # GUI: scene fingerprint after the last method call
        self._dirty = False      # GUI: a depsgraph update happened since
        self._info: dict = {}
        self._refresh_info()

    # -- status (answered on the connection thread from a cache the main thread refreshes) --------

    def _refresh_info(self) -> None:
        out: dict = {"objects": len(bpy.data.objects), "dir": _fwd(self.host.dir)}
        if bpy.data.filepath:
            out["file"] = _fwd(bpy.data.filepath)
        cps = self.host.checkpoints.list()
        if cps:
            out["checkpoints"] = len(cps)
            out["last_checkpoint"] = cps[-1]["n"]
        self._info = out

    def busy(self) -> dict | None:
        """The running request: ``{"method", "s", "budget_s", "over"?}`` or ``None``."""
        job = self.current
        if job is None or job.started is None:
            return None
        s = time.monotonic() - job.started
        out = {"method": job.label, "s": round(s, 1), "budget_s": round(job.budget, 1)}
        if s > job.budget:
            out["over"] = True
        return out

    def status_info(self) -> dict:
        out = dict(self._info)
        b = self.busy()
        if b:
            out["busy"] = b
        return out

    def dispatch(self, method: str, params: dict) -> dict:
        if method == "quit":
            self.quit_requested = True
            b = self.busy()
            return {"busy": b} if b else {}
        return super().dispatch(method, params)

    # -- requests ----------------------------------------------------------------------------

    def handle(self, method: str, params: dict) -> dict:
        params = params or {}
        if method in OFF_MAIN or threading.get_ident() == self.main_ident:
            return self.dispatch(method, params)
        stuck = self.current
        if stuck is not None and stuck.abandoned and not stuck.done.is_set():
            raise SatkError("BUSY", f"the session is still inside {stuck.label} "
                            f"({time.monotonic() - (stuck.started or stuck.queued):.0f} s; it overran its "
                            f"{stuck.budget:g} s budget)",
                            hint="wait and retry, or restart it: satk blender session stop, then session start "
                                 "(a project session resumes from its newest checkpoint)",
                            data={"busy": self.busy()})
        job = _Job(method, params, _budget(method, params))
        self.jobs.put(job)
        lock = self.server_lock
        released = False
        if lock is not None:  # let status/stop through while this request waits for the main thread
            try:
                lock.release()
                released = True
            except RuntimeError:
                released = False
        try:
            self._wait(job)
        finally:
            if released:
                lock.acquire()
        if job.error is not None:
            raise job.error
        return job.result

    def _wait(self, job: _Job) -> None:
        while not job.done.wait(0.1):
            if self.closing.is_set():
                job.abandoned = True
                raise SatkError("NOT_READY", "the Blender session is closing")
            now = time.monotonic()
            if job.started is None:
                if now - job.queued > job.budget + ANSWER_GRACE_S:
                    job.abandoned = True
                    raise SatkError("BUSY", f"{job.label} waited {now - job.queued:.0f} s for an earlier step",
                                    hint="satk blender session status shows what runs", data={"busy": self.busy()})
            elif now - job.started > job.budget + ANSWER_GRACE_S:
                job.abandoned = True
                raise SatkError("TIMEOUT", f"{job.label} did not finish within its {job.budget:g} s budget and "
                                "could not be stopped (Blender is busy inside native code)",
                                hint="satk blender session status shows it; restart with session stop + session "
                                     "start (a project session resumes from its newest checkpoint)",
                                data={"busy": self.busy(), "retryable": False})

    def watchdog(self) -> None:
        """Thread body: stop a method that overruns its budget (CallTimeout raised in the main thread, again
        every :data:`KICK_S` seconds in case the method's own ``except`` swallowed it)."""
        while not self.closing.wait(WATCH_S):
            job = self.current
            if job is None or job.started is None or not job.running:
                continue
            now = time.monotonic()
            if now - job.started > job.budget and now - job.kicked > KICK_S:
                with self._wd_lock:
                    if self.current is job and job.running:
                        if not job.interrupted:
                            _log(f"{job.label}: over its {job.budget:g} s budget, stopping it")
                        job.interrupted = True
                        job.kicked = now
                        _async_raise(self.main_ident, CallTimeout)

    def run_job(self, job: _Job) -> None:
        if job.abandoned:  # its client already got an answer
            job.done.set()
            return
        self.last = job.started = time.monotonic()
        self.current = job
        rev = self.core.rev
        try:
            job.running = True
            try:
                self.check_external()
                job.result = self.dispatch(job.method, job.params)
            finally:
                with self._wd_lock:
                    job.running = False
                    if job.interrupted:
                        _async_raise(self.main_ident, None)  # a late timeout must not hit the code below
        except CallTimeout as e:
            job.error = e
        except Exception as e:  # noqa: BLE001 - handed to the connection thread
            job.error = e
        except BaseException as e:  # SystemExit from user code must not end the session
            job.error = SatkError("BAD_PARAMS", f"{type(e).__name__} inside a method")
            if isinstance(e, KeyboardInterrupt):
                job.done.set()
                raise
        finally:
            try:
                if self.core.rev != rev:
                    self.mutated_since_ckpt = self.core._since_ckpt > 0
                if not bpy.app.background:
                    self._fp = self.host.fingerprint()
                    self._dirty = False
                self._refresh_info()
            except Exception as e:  # noqa: BLE001 - bookkeeping must not lose the answer
                _log(f"after {job.label}: {type(e).__name__}: {e}")
            finally:
                self.last = time.monotonic()
                self.current = None
                job.done.set()

    # -- GUI: edits made by a person in the Blender window -----------------------------------

    def watch_external(self) -> None:
        """GUI mode: note depsgraph updates; :meth:`check_external` journals real changes."""
        self._fp = self.host.fingerprint()

        def on_update(scene, depsgraph=None):  # noqa: ARG001 - Blender handler signature
            self._dirty = True

        on_update.persistent = True  # type: ignore[attr-defined]
        bpy.app.handlers.depsgraph_update_post.append(bpy.app.handlers.persistent(on_update))

    def check_external(self) -> dict | None:
        """If the scene changed outside the methods since the last call: an ``external`` journal entry."""
        if bpy.app.background or not self._dirty or self._fp is None:
            return None
        self._dirty = False
        fp = self.host.fingerprint()
        if fp == self._fp:
            return None
        old = {row[0]: row for row in self._fp}
        changed = [row[0] for row in fp if old.get(row[0]) != row]
        changed += [n for n in old if n not in {row[0] for row in fp}]
        self._fp = fp
        return self.core.record_external(changed)

    def final_checkpoint(self) -> str | None:
        """A checkpoint of the last step when the session ends after unsaved changes (resume continues there)."""
        if not self.mutated_since_ckpt or self.core.n <= 0:
            return None
        try:
            path = self.host.checkpoints.save(self.core.n)
        except Exception as e:  # noqa: BLE001 - the session is ending anyway
            _log(f"final checkpoint failed: {type(e).__name__}: {e}")
            return None
        self.core.journal.append({"n": self.core.n, "method": "session.end", "ok": True, "checkpoint": path})
        return path


# --------------------------------------------------------------------------- start-up


def _prefs() -> None:
    """No thumbnails in the user's .thumbnails folder, no .blend1 backups, no autosave."""
    fp = bpy.context.preferences.filepaths
    for k, v in (("file_preview_type", "NONE"), ("save_version", 0), ("use_auto_save_temporary_files", False)):
        try:
            setattr(fp, k, v)
        except (AttributeError, TypeError):
            pass


def _empty_scene() -> None:
    """The factory startup scene without its cube, camera and light (an authoring session starts empty)."""
    if bpy.data.filepath:
        return
    bpy.data.batch_remove(list(bpy.data.objects))
    for coll in (bpy.data.meshes, bpy.data.cameras, bpy.data.lights, bpy.data.materials):
        dead = [x for x in coll if x.users == 0]
        if dead:
            bpy.data.batch_remove(dead)


def _log(msg: str) -> None:
    print(f"satk-studio {_dt.datetime.now().strftime('%H:%M:%S')} {msg}", flush=True)


def _remove_own(path: str | None) -> None:
    if not path:
        return
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        if d.get("pid") == os.getpid():
            os.remove(path)
    except (OSError, ValueError):
        pass


def serve(req: dict) -> None:
    """A named session: SAAP endpoint until ``quit``, idle timeout or the owner's exit."""
    from satk.saap.server import SaapServer

    token = os.environ.get("SATK_AGENT_TOKEN")
    if not token:
        raise SystemExit("satk-studio: SATK_AGENT_TOKEN is not set")
    _prefs()
    _empty_scene()
    world = BlenderWorld(req)
    start = {"name": req.get("name"), "blender": world.info.get("blender")}
    if bpy.data.filepath:
        start["open"] = _fwd(bpy.data.filepath)
    if req.get("resume_n") is not None:
        start["resume_n"] = int(req["resume_n"])
    if req.get("project_dir"):
        start["project"] = _fwd(req["project_dir"])
    world.core.journal_event("session.start", **start)
    if bpy.app.background:
        t0 = time.perf_counter()
        try:
            snapshot.warm_up()
            _log(f"renderer warmed up in {time.perf_counter() - t0:.2f} s")
        except Exception as e:  # noqa: BLE001 - snapshots then just start slower
            _log(f"renderer warm-up failed: {type(e).__name__}: {e}")
    srv = SaapServer(world, token=token, role="blender", port=int(req.get("port") or 0))
    world.server_lock = srv.lock
    threading.Thread(target=world.watchdog, name="satk-studio-watchdog", daemon=True).start()
    srv.start()
    d = srv.descriptor()
    d.update(name=req.get("name"), dir=_fwd(world.host.dir), blender=world.info.get("blender"))
    if req.get("project_dir"):
        d["project"] = _fwd(req["project_dir"])
    _atomic_json(req["endpoint"], d)
    _log(f"serving {len(world.core.methods)} methods on 127.0.0.1:{srv.port} (pid {os.getpid()})")
    for e in world.core.errors:
        _log(f"plug-in error: {e}")
    idle = float(req.get("idle_s") or 0)
    owner = req.get("owner_pid")

    def finish(why: str) -> None:
        world.closing.set()
        srv.stop()
        cp = world.final_checkpoint()
        _remove_own(req.get("endpoint"))
        _remove_own(req.get("session_file"))
        _log(f"stopped ({why}) after {world.core.calls} calls" + (f"; checkpoint {cp}" if cp else ""))

    if bpy.app.background:
        why = "error"
        try:
            why = pump.serve_blocking(world, srv, idle_s=idle, owner_pid=owner)
        finally:
            finish(why)
    else:
        def on_stop(why: str) -> None:
            finish(why)
            bpy.ops.wm.quit_blender()

        world.watch_external()
        pump.start_timer(world, srv, idle_s=idle, owner_pid=owner, on_stop=on_stop)


def oneshot(req: dict) -> None:
    """One request (cold mode); the SAAP-like answer goes to ``response.json``."""
    from satk.saap import schema as S
    from satk.saap.server import to_saap_error

    t0 = time.perf_counter()
    method = req.get("saap_method") or "author.call"
    params = req.get("call") or {}
    out: dict
    try:
        _prefs()
        _empty_scene()
        world = BlenderWorld(req)
        errs = S.validate_params(method, params)
        if errs:
            raise SatkError("BAD_PARAMS", errs[0], data={"errors": errs[:10]})
        out = {"saap": 1, "id": "cold", "ok": True, "result": world.dispatch(method, params)}
    except SatkError as e:
        out = {"saap": 1, "id": "cold", "ok": False, "error": to_saap_error(e)}
    except Exception as e:  # noqa: BLE001
        tb = traceback.format_exc(limit=3)
        out = {"saap": 1, "id": "cold", "ok": False,
               "error": {"code": "INTERNAL", "message": f"{type(e).__name__}: {e}", "retryable": False,
                         "data": {"trace": tb[-800:]}}}
    out["meta"] = {"ms": round((time.perf_counter() - t0) * 1000, 2)}
    _atomic_json(req["response"], out)


def main(req: dict) -> None:
    mode = req.get("mode")
    if mode == "serve":
        serve(req)
    elif mode == "oneshot":
        oneshot(req)
    else:
        print(f"satk-studio: unknown mode {mode!r}", file=sys.stderr)
        raise SystemExit(2)
