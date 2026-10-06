"""Studio dispatch shared by the Blender world, the cold one-shot run and the mock (contract K3).

Stdlib only: this module is also imported from Blender's Python (3.13), next to ``bpy``.

**Method plug-ins (K3).** A Blender-side module exposes ``METHODS: dict[str, fn(ctx, params) -> dict]``.
:func:`discover_methods` loads every submodule of ``satk_blender.studio.methods`` plus
``satk_blender.kit.methods`` and ``satk_blender.look.methods`` when they import. A method returns a
JSON-able dict; two keys are taken out of it: ``changed`` (names of the objects it created or changed;
the stats cover them) and ``warn`` (strings ``"CODE: text"``). Optional attributes on the function:
``readonly`` (no journal replay, no stats by default; :func:`readonly`) and ``checkpoint`` (always save
a ``.blend`` checkpoint after it; :func:`checkpointed`). ``ctx`` is supplied by the host; in Blender it
gives ``scene``, ``out_dir``, ``n``, ``stats(names)``, ``snapshot(...)``, ``journal``, ``warn(text)``,
``obj(name)``, ``objects(pattern)`` and ``call(method, params)`` (another method, not journaled).

**One step** (``author.call``)::

    {"method": "mesh.primitive", "params": {...}, "stats": "auto", "snapshot": {"view": "3q"},
     "checkpoint": false, "open": "<blend>", "save": "<blend>"}
    -> {"method", "n", "result"?, "changed"?, "stats"?, "snapshot"?, "checkpoint"?, "saved"?, "ms", "warn"?}

**A batch** is the method ``batch`` with ``params = {"steps": [{"method", "params"}, ...]}``: every step
is run and journaled on its own; stats, one snapshot and one checkpoint follow the whole batch (a batch with
two or more mutating steps writes that checkpoint by default; ``"checkpoint": false`` skips it). The reply
lists the steps as ``steps: [{n, method, result?}]`` with each result cut to its short values
(:func:`compact`). A failing step stops the batch; the error names it (``data.step``) and the steps before it
stay applied.

**Built-in session methods** (every host): ``session.checkpoint`` (``tag`` optional; tagged checkpoints
are never pruned), ``session.restore`` (``ref`` = step number, tag or ``last``), ``session.checkpoints``,
``session.prune`` (keep the last ``keep`` untagged ones) and ``session.journal`` (the last steps).

The reply is cut to :data:`MAX_REPLY` bytes (a batch gets :data:`STEP_REPLY` more per step, at most
:data:`MAX_BATCH_REPLY`; :func:`fit_reply`). Every call is appended to the JSONL journal (code of ``python``
steps with its sha256), failed calls too; numbering continues across sessions that share a journal. A
checkpoint is taken after every ``checkpoint_every``-th mutating step. A host may define
``after_method(name, warn)``: it runs after every method (also a failed one), before the stats; the Blender
host repairs references to depsgraph copies there.

Example::

    core = AuthorCore(host, *discover_methods(), journal=Journal("<work>/studio/default/journal.jsonl"))
    core.call({"method": "scene.info"})
"""

from __future__ import annotations

import copy
import difflib
import hashlib
import importlib
import inspect
import json
import math
import os
import pkgutil
import re
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from ..core.errors import SatkError

__all__ = [
    "MAX_REPLY",
    "STEP_REPLY",
    "MAX_BATCH_REPLY",
    "MAX_STEPS",
    "METHOD_RE",
    "TAG_RE",
    "PACKAGES",
    "OPTIONAL_MODULES",
    "VIEWS",
    "LOOKS",
    "KEEP_CHECKPOINTS",
    "Method",
    "readonly",
    "checkpointed",
    "methods_from_module",
    "discover_methods",
    "Journal",
    "Checkpoints",
    "fit_reply",
    "compact",
    "jsonable",
    "AuthorCore",
    "AuthorWorld",
]

#: Byte budget of one ``author.call`` reply (compact JSON).
MAX_REPLY = 1536
#: Extra bytes of a batch reply per step (the compact step results), up to :data:`MAX_BATCH_REPLY`.
STEP_REPLY = 96
MAX_BATCH_REPLY = 3072
#: Steps of one ``batch`` call.
MAX_STEPS = 200
METHOD_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")
#: Checkpoint tags (gate names such as ``G1``, or words such as ``blockout``).
TAG_RE = re.compile(r"^[A-Za-z0-9_]{1,32}$")
#: Packages whose submodules are always scanned for ``METHODS``.
PACKAGES: tuple[str, ...] = ("satk_blender.studio.methods",)
#: Modules (or packages) scanned when they exist: the kit and look lanes plug in here.
OPTIONAL_MODULES: tuple[str, ...] = ("satk_blender.kit.methods", "satk_blender.look.methods")
#: Named snapshot views (a camera object of the scene may be named instead).
VIEWS: tuple[str, ...] = ("3q", "front", "rear", "left", "right", "top")
LOOKS: tuple[str, ...] = ("clay", "game", "raw", "wire")
#: Untagged checkpoints kept by default (tagged ones are always kept).
KEEP_CHECKPOINTS = 10
_STR_MAX = 200
#: Characters of the full description of one method (``author.methods`` with its exact name).
HELP_MAX = 1800


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 3] + "..."


# --------------------------------------------------------------------------- method plug-ins


def readonly(fn: Callable) -> Callable:
    """Mark a method as read-only (journaled with ``ro``, no stats unless asked)."""
    fn.readonly = True  # type: ignore[attr-defined]
    return fn


def checkpointed(fn: Callable) -> Callable:
    """Mark a method that always gets a ``.blend`` checkpoint after it (``python``)."""
    fn.checkpoint = True  # type: ignore[attr-defined]
    return fn


@dataclass(frozen=True)
class Method:
    name: str
    fn: Callable
    module: str
    readonly: bool = False
    checkpoint: bool = False

    @property
    def doc(self) -> str:
        first = (self.fn.__doc__ or "").strip().splitlines()
        return first[0].strip()[:120] if first else ""


def methods_from_module(mod: Any, out: dict[str, Method], errors: list[str]) -> None:
    """Add the ``METHODS`` of ``mod`` to ``out`` (the first definition of a name wins)."""
    table = getattr(mod, "METHODS", None)
    if table is None:
        return
    where = getattr(mod, "__name__", "?")
    if not isinstance(table, dict):
        errors.append(f"{where}: METHODS is not a dict")
        return
    for name, fn in table.items():
        if not isinstance(name, str) or len(name) > 64 or not METHOD_RE.match(name):
            errors.append(f"{where}: bad method name {name!r} (lower-case dotted, at most 64 chars)")
            continue
        if not callable(fn):
            errors.append(f"{where}: method {name!r} is not callable")
            continue
        if name in out:
            errors.append(f"{where}: method {name!r} is already defined in {out[name].module}; the first one is kept")
            continue
        out[name] = Method(name, fn, where, bool(getattr(fn, "readonly", False)), bool(getattr(fn, "checkpoint", False)))


def _absent(e: ImportError, name: str) -> bool:
    """``name`` (or one of its parents) does not exist, as opposed to failing inside."""
    missing = getattr(e, "name", None)
    return isinstance(e, ModuleNotFoundError) and bool(missing) and (name == missing or name.startswith(missing + "."))


def discover_methods(packages: Iterable[str] = PACKAGES,
                     optional: Iterable[str] = OPTIONAL_MODULES) -> tuple[dict[str, Method], list[str]]:
    """``({name: Method}, errors)`` from the plug-in modules (sorted by name).

    ``packages`` must import (their failure is an error); an ``optional`` module that does not
    exist is skipped silently, one that exists but fails to import is reported in ``errors``.
    A module that is a package is scanned together with its submodules (``_private`` ones skipped).
    """
    out: dict[str, Method] = {}
    errors: list[str] = []

    def load(name: str, required: bool) -> None:
        try:
            mod = importlib.import_module(name)
        except ImportError as e:
            if not required and _absent(e, name):
                return
            errors.append(f"{name}: {type(e).__name__}: {e}")
            return
        except Exception as e:  # noqa: BLE001 - a broken plug-in must not stop the session
            errors.append(f"{name}: {type(e).__name__}: {e}")
            return
        methods_from_module(mod, out, errors)
        if hasattr(mod, "__path__"):
            for m in sorted(pkgutil.iter_modules(mod.__path__), key=lambda m: m.name):
                if m.name.startswith("_"):
                    continue
                sub = f"{name}.{m.name}"
                try:
                    smod = importlib.import_module(sub)
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{sub}: {type(e).__name__}: {e}")
                    continue
                methods_from_module(smod, out, errors)

    for p in packages:
        load(p, True)
    for p in optional:
        load(p, False)
    return dict(sorted(out.items())), errors


# --------------------------------------------------------------------------- journal


class Journal:
    """Append-only JSONL log of the steps (one line per call; ``path=None`` keeps nothing)."""

    def __init__(self, path: str | os.PathLike | None):
        self.path = os.fspath(path) if path else None
        self._lock = threading.Lock()

    def append(self, entry: dict) -> None:
        if not self.path:
            return
        line = json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        with self._lock:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(self.path, "a", encoding="utf-8", newline="\n") as f:
                f.write(line + "\n")

    def entries(self) -> list[dict]:
        if not self.path or not os.path.isfile(self.path):
            return []
        out = []
        with open(self.path, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if ln:
                    try:
                        out.append(json.loads(ln))
                    except ValueError:
                        continue
        return out

    def last_n(self) -> int:
        """The highest step number in the file (0 for none): a new session continues after it."""
        n = 0
        for e in self.entries():
            v = e.get("n")
            if isinstance(v, int) and v > n:
                n = v
        return n


# --------------------------------------------------------------------------- checkpoints


class Checkpoints:
    """Numbered scene copies in one folder: ``<n:04d>.<ext>`` or ``<n:04d>-<tag>.<ext>``.

    ``save_fn(path)`` writes the current scene, ``load_fn(path)`` replaces it. Pruning keeps the last
    ``keep`` untagged checkpoints and every tagged one; only files of this naming scheme are removed.
    """

    def __init__(self, folder: str | os.PathLike, ext: str, save_fn: Callable[[str], Any],
                 load_fn: Callable[[str], Any], *, keep: int = KEEP_CHECKPOINTS):
        self.folder = os.path.abspath(os.fspath(folder))
        self.ext = ext.lstrip(".")
        self.save_fn, self.load_fn = save_fn, load_fn
        self.keep = int(keep)
        self._re = re.compile(r"^(\d{4,})(?:-([A-Za-z0-9_]{1,32}))?\." + re.escape(self.ext) + r"$")

    def path(self, n: int, tag: str | None = None) -> str:
        name = f"{int(n):04d}" + (f"-{tag}" if tag else "") + f".{self.ext}"
        return os.path.join(self.folder, name)

    def list(self) -> list[dict]:
        """``[{n, tag?, path, kb}]`` sorted by step number."""
        out = []
        try:
            names = os.listdir(self.folder)
        except OSError:
            return []
        for fn in names:
            m = self._re.match(fn)
            if not m:
                continue
            p = os.path.join(self.folder, fn)
            row: dict[str, Any] = {"n": int(m.group(1)), "path": p.replace("\\", "/")}
            if m.group(2):
                row["tag"] = m.group(2)
            try:
                row["kb"] = round(os.path.getsize(p) / 1024, 1)
            except OSError:
                continue
            out.append(row)
        return sorted(out, key=lambda r: (r["n"], r.get("tag") or ""))

    def save(self, n: int, tag: str | None = None) -> str:
        if tag is not None and not TAG_RE.match(str(tag)):
            raise SatkError("BAD_PARAMS", f"bad checkpoint tag {tag!r} (letters, digits, '_'; at most 32)")
        os.makedirs(self.folder, exist_ok=True)
        p = self.path(n, tag)
        self.save_fn(p)
        if not os.path.isfile(p):
            raise SatkError("INTERNAL", "the checkpoint was not written", data={"path": p.replace("\\", "/")})
        self.prune()
        return p.replace("\\", "/")

    def find(self, ref: Any) -> dict:
        """Checkpoint by step number, tag or ``last``; ``NOT_FOUND`` lists what exists."""
        rows = self.list()
        hit = None
        if ref in (None, "last", ""):
            hit = rows[-1] if rows else None
        elif isinstance(ref, bool):
            hit = None
        elif isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
            n = int(ref)
            same = [r for r in rows if r["n"] == n]
            hit = same[-1] if same else None
        elif isinstance(ref, str):
            same = [r for r in rows if r.get("tag") == ref]
            hit = same[-1] if same else None
        if hit is None:
            have = [r["n"] if not r.get("tag") else f"{r['n']}-{r['tag']}" for r in rows][-12:]
            raise SatkError("NOT_FOUND", f"no checkpoint {ref!r}", data={"checkpoints": have},
                            hint="session.checkpoints lists them")
        return hit

    def load(self, ref: Any) -> dict:
        hit = self.find(ref)
        self.load_fn(hit["path"])
        return hit

    def prune(self, keep: int | None = None) -> list[str]:
        """Remove old untagged checkpoints (keep the last ``keep``); returns the removed paths."""
        k = self.keep if keep is None else max(0, int(keep))
        plain = [r for r in self.list() if not r.get("tag")]
        gone = []
        for r in plain[: max(0, len(plain) - k)]:
            try:
                os.remove(r["path"])
                gone.append(r["path"])
            except OSError:
                continue
        return gone


# --------------------------------------------------------------------------- replies


def jsonable(v: Any, depth: int = 0) -> Any:
    """``v`` as plain JSON data (tuples -> lists, non-finite floats -> None, others -> str)."""
    if depth > 12:
        return str(v)
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, dict):
        return {str(k): jsonable(x, depth + 1) for k, x in v.items()}
    if isinstance(v, (list, tuple, set, frozenset)):
        seq = sorted(v, key=str) if isinstance(v, (set, frozenset)) else v
        return [jsonable(x, depth + 1) for x in seq]
    try:  # mathutils.Vector, numpy scalars and arrays
        if hasattr(v, "tolist"):
            return jsonable(v.tolist(), depth + 1)
        if hasattr(v, "__len__") and hasattr(v, "__getitem__"):
            return [jsonable(x, depth + 1) for x in v]
        if hasattr(v, "__float__"):
            return jsonable(float(v), depth + 1)
    except Exception:  # noqa: BLE001
        pass
    return str(v)


#: Longest string kept by :func:`compact`.
_SHORT_STR = 60


def compact(v: Any, depth: int = 0) -> Any:
    """A step result cut to its short values: numbers, booleans, short strings and small lists stay; long
    strings are clipped, long lists become ``"<n> items"`` and nested objects keep only their short values."""
    if isinstance(v, dict):
        if depth >= 2:
            return f"{len(v)} keys"
        out = {}
        for k, x in v.items():
            c = compact(x, depth + 1)
            if c is not None and c != {} and c != []:
                out[k] = c
        return out
    if isinstance(v, (list, tuple)):
        if not v:
            return []
        if len(v) <= 4 and all(isinstance(x, (int, float, bool)) or (isinstance(x, str) and len(x) <= 24) for x in v):
            return list(v)
        if len(v) <= 3 and all(isinstance(x, (list, tuple)) and len(x) <= 3 and
                               all(isinstance(y, (int, float)) for y in x) for x in v):
            return [list(x) for x in v]
        return f"{len(v)} items"
    if isinstance(v, str):
        return v if len(v) <= _SHORT_STR else v[: _SHORT_STR - 3] + "..."
    return v


def _size(x: Any) -> int:
    return len(json.dumps(x, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8"))


def _cut_list(out: dict, holder: dict, key: str, limit: int, cut: list[str], label: str) -> None:
    v = holder.get(key)
    if isinstance(v, list) and len(v) > 1 and _size(out) > limit:
        n = len(v)
        keep = n
        while keep > 1 and _size(out) > limit:
            keep = max(1, keep // 2)
            holder[key] = v[:keep]
        cut.append(f"{label}: {keep} of {n}")


def fit_reply(reply: dict, limit: int = MAX_REPLY) -> dict:
    """``reply`` cut to ``limit`` bytes: fewer per-object stats (heaviest kept), shorter result
    lists and strings, batch step results, then no result. ``truncated`` names what was cut."""
    if _size(reply) <= limit:
        return reply
    out = copy.deepcopy(reply)
    cut: list[str] = list(out.get("truncated") or [])
    st = out.get("stats")
    objs = st.get("objects") if isinstance(st, dict) else None
    if isinstance(objs, dict) and len(objs) > 1:
        names = sorted(objs, key=lambda k: (-(objs[k].get("tris") or 0) if isinstance(objs[k], dict) else 0, k))
        keep = len(names)
        while keep > 1 and _size(out) > limit:
            keep = max(1, keep // 2)
            st["objects"] = {k: objs[k] for k in names[:keep]}
            st["objects_more"] = len(names) - keep
        cut.append(f"stats.objects: {keep} of {len(names)}")
    steps = out.get("steps")
    if isinstance(steps, list) and _size(out) > limit:
        # step results: their numbers and short words first, then nothing
        for s in steps:
            r = s.get("result") if isinstance(s, dict) else None
            if isinstance(r, dict):
                s["result"] = {k: x for k, x in r.items() if isinstance(x, (int, float, bool))
                               or (isinstance(x, str) and len(x) <= 32)}
                if not s["result"]:
                    s.pop("result")
        if _size(out) > limit:
            for s in steps:
                if isinstance(s, dict):
                    s.pop("result", None)
            cut.append("steps.result")
        else:
            cut.append("steps.result: short values")
        _cut_list(out, out, "steps", limit, cut, "steps")
    if isinstance(out.get("changed"), list):
        _cut_list(out, out, "changed", limit, cut, "changed")
    res = out.get("result")
    if isinstance(res, dict) and _size(out) > limit:
        for key in sorted(res, key=lambda k: -_size(res[k])):
            v = res[key]
            if isinstance(v, list) and len(v) > 1:
                _cut_list(out, res, key, limit, cut, f"result.{key}")
            elif isinstance(v, str) and len(v) > _STR_MAX:
                res[key] = v[:_STR_MAX] + "..."
                cut.append(f"result.{key}: {_STR_MAX} of {len(v)} chars")
            if _size(out) <= limit:
                break
    if _size(out) > limit and "result" in out:
        del out["result"]
        cut.append("result")
    if _size(out) > limit and isinstance(out.get("stats"), dict):
        out["stats"] = {k: v for k, v in out["stats"].items() if k == "scene"}
        cut.append("stats")
    if _size(out) > limit and isinstance(out.get("warn"), list):
        out["warn"] = [w[:120] for w in out["warn"][:3]]
        cut.append("warn")
    out["truncated"] = cut
    return out


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


#: Exceptions of a method that mean "the parameters were wrong" (the rest is INTERNAL).
_BAD_PARAM_EXC = (ValueError, TypeError, KeyError, IndexError, LookupError)


def _method_error(name: str, e: BaseException) -> SatkError:
    tb = traceback.extract_tb(e.__traceback__)
    where = f"{os.path.basename(tb[-1].filename)}:{tb[-1].lineno}" if tb else ""
    code = "BAD_PARAMS" if isinstance(e, _BAD_PARAM_EXC) else "INTERNAL"
    msg = f"{name}: {type(e).__name__}: {e}"
    return SatkError(code, msg[:400], data={"method": name, "where": where} if where else {"method": name})


def _merge_changed(acc: list[str], more: Iterable[str] | None) -> None:
    for x in more or ():
        if x not in acc:
            acc.append(x)


# --------------------------------------------------------------------------- the core


class AuthorCore:
    """``author.call`` / ``author.methods`` over a *host*.

    The host supplies the world (Blender or the mock)::

        make_ctx(n: int, warn: list[str]) -> ctx
        stats(names: list[str] | None) -> dict          # None = the whole scene
        snapshot(spec: dict, n: int) -> str             # path of the image
        check_open(path) / open(path)                   # load a .blend before the step
        check_save(path) / save(path) -> str            # save the scene after the step
        checkpoints: Checkpoints | None                 # absent = no checkpoints (UNSUPPORTED)
    """

    def __init__(self, host: Any, methods: dict[str, Method], errors: Iterable[str] = (), *,
                 journal: Journal | None = None, max_reply: int = MAX_REPLY, checkpoint_every: int = 0,
                 start_n: int | None = None):
        self.host = host
        self.methods = dict(methods)
        for name, m in _builtin_methods(self).items():
            self.methods.setdefault(name, m)
        self.methods = dict(sorted(self.methods.items()))
        self.errors = list(errors)
        self.journal = journal or Journal(None)
        self.max_reply = int(max_reply)
        self.checkpoint_every = max(0, int(checkpoint_every or 0))
        self.n = self.journal.last_n() if start_n is None else int(start_n)  # step numbers continue
        self.calls = 0    # calls of this session
        self.rev = 0      # scene revision: +1 per mutating call / open
        self._since_ckpt = 0
        self._lock = threading.RLock()

    @property
    def checkpoints(self) -> Checkpoints | None:
        return getattr(self.host, "checkpoints", None)

    # -- discovery -------------------------------------------------------------------------

    def method_list(self, query: str | None = None, limit: int = 500) -> dict:
        q = (query or "").strip().lower()
        rows = []
        for m in self.methods.values():
            if q and q not in f"{m.name} {m.doc}".lower():
                continue
            row: dict[str, Any] = {"name": m.name, "doc": m.doc}
            if m.readonly:
                row["readonly"] = True
            rows.append(row)
        out: dict[str, Any] = {"methods": rows[: max(1, int(limit))], "total": len(rows)}
        exact = self.methods.get(q) if q else None
        if exact is not None:  # one method asked by its name: its whole description and parameters
            out["help"] = _clip(inspect.cleandoc(exact.fn.__doc__ or ""), HELP_MAX)
            try:
                from .methodref import params_of, rows as param_rows

                out["params"] = param_rows(params_of(exact.fn))
            except Exception as e:  # noqa: BLE001 - the help still answers
                out["params_error"] = f"{type(e).__name__}: {e}"[:200]
            mod = sys.modules.get(exact.module)
            if mod is not None and mod.__doc__:
                out["module"] = _clip(inspect.cleandoc(mod.__doc__), HELP_MAX)
        if self.errors:
            out["errors"] = self.errors[:10]
        return out

    def lookup(self, name: Any) -> Method:
        m = self.methods.get(name) if isinstance(name, str) else None
        if m is None:
            near = difflib.get_close_matches(str(name), list(self.methods), n=3, cutoff=0.5)
            data: dict[str, Any] = {"method": str(name)}
            if near:
                data["did_you_mean"] = near
            raise SatkError("NOT_FOUND", f"no studio method {name!r}", data=data,
                            hint="author.methods lists them (satk blender methods)")
        return m

    # -- journal helpers -------------------------------------------------------------------

    def journal_event(self, method: str, **fields: Any) -> int:
        """A non-method entry (``session.start``, ``external``): its own step number."""
        with self._lock:
            self.n += 1
            entry = {"n": self.n, "method": method, "ok": True}
            entry.update({k: jsonable(v) for k, v in fields.items() if v is not None})
            self.journal.append(entry)
            return self.n

    def record_external(self, objects: Iterable[str] = ()) -> dict:
        """Edits made outside the session's methods (the GUI): a tagged checkpoint + an ``external`` entry."""
        with self._lock:
            self.n += 1
            n = self.n
            entry: dict[str, Any] = {"n": n, "method": "external", "ok": True}
            names = sorted({str(x) for x in objects})[:50]
            if names:
                entry["changed"] = names
            if self.checkpoints is not None:
                try:
                    entry["checkpoint"] = self.checkpoints.save(n, "ext")
                except Exception as e:  # noqa: BLE001
                    entry["warn"] = f"checkpoint failed: {type(e).__name__}: {e}"[:200]
            self.rev += 1
            self.journal.append(entry)
            return entry

    # -- one step ----------------------------------------------------------------------------

    def call(self, p: dict) -> dict:
        with self._lock:
            return self._call(p)

    def _run(self, name: Any, args: Any, warn: list[str], t0: float, extra: dict | None = None) -> tuple:
        """Run one method and journal it; returns ``(method, n, result, changed, entry)``."""
        m = self.lookup(name)
        if args is None:
            args = {}
        if not isinstance(args, dict):
            raise SatkError("BAD_PARAMS", f"{m.name}: params must be an object")
        self.n += 1
        self.calls += 1
        n = self.n
        entry: dict[str, Any] = {"n": n, "method": m.name, "params": jsonable(args)}
        if extra:
            entry.update(extra)
        if m.readonly:
            entry["ro"] = True
        if isinstance(args.get("code"), str):
            entry["code_sha256"] = _sha(args["code"])
        try:
            ctx = self.host.make_ctx(n, warn)
            for attr, val in (("call", None), ("methods", self.methods)):
                if getattr(ctx, attr, None) is None:
                    try:
                        setattr(ctx, attr, self._inner_call(ctx) if attr == "call" else val)
                    except AttributeError:
                        pass
            try:
                res = m.fn(ctx, args)
            finally:
                self._after(m.name, warn)
        except SatkError as e:
            self._failed(entry, t0, e)
            raise
        except Exception as e:  # noqa: BLE001 - every method error becomes an envelope
            err = _method_error(m.name, e)
            self._failed(entry, t0, err)
            raise err from None
        if res is None:
            res = {}
        res = dict(res) if isinstance(res, dict) else {"value": res}
        changed = res.pop("changed", None)
        extra_warn = res.pop("warn", None)
        journal_extra = res.pop("_journal", None)
        if extra_warn:
            warn.extend(str(w) for w in (extra_warn if isinstance(extra_warn, (list, tuple)) else [extra_warn]))
        changed = [str(x) for x in changed] if isinstance(changed, (list, tuple)) else None
        if isinstance(journal_extra, dict):
            entry.update(jsonable(journal_extra))
        if not m.readonly:
            self.rev += 1
            self._since_ckpt += 1
        return m, n, res, changed, entry

    def _after(self, name: str, warn: list[str]) -> None:
        """The host's ``after_method`` hook (repairs; it never fails the step)."""
        fn = getattr(self.host, "after_method", None)
        if not callable(fn):
            return
        try:
            fn(name, warn)
        except Exception as e:  # noqa: BLE001
            warn.append(f"INTERNAL: after {name}: {type(e).__name__}: {e}"[:200])

    def _inner_call(self, ctx: Any) -> Callable:
        def call(method: str, params: dict | None = None) -> dict:
            m = self.lookup(method)
            out = m.fn(ctx, dict(params or {}))
            return dict(out) if isinstance(out, dict) else {"value": out}
        return call

    def _call(self, p: dict) -> dict:
        t0 = time.perf_counter()
        name = p.get("method")
        snap = p.get("snapshot")
        if snap is not None and not isinstance(snap, dict):
            raise SatkError("BAD_PARAMS", "snapshot must be an object like {\"view\": \"3q\"}")
        batch = name == "batch"
        if batch:
            steps = (p.get("params") or {}).get("steps")
            if not isinstance(steps, list) or not steps or len(steps) > MAX_STEPS:
                raise SatkError("BAD_PARAMS", f"batch: params.steps must be a list of 1-{MAX_STEPS} "
                                "{\"method\", \"params\"} objects")
            for i, s in enumerate(steps):
                if not isinstance(s, dict) or not isinstance(s.get("method"), str) or s["method"] == "batch":
                    raise SatkError("BAD_PARAMS", f"batch: step {i + 1} needs a 'method' (not 'batch')",
                                    data={"step": i + 1})
                self.lookup(s["method"])
        else:
            self.lookup(name)
        if p.get("open"):
            self.host.check_open(p["open"])
        if p.get("save"):
            self.host.check_save(p["save"])
        warn: list[str] = []
        if p.get("open"):
            self.host.open(p["open"])
            self.rev += 1
        open_extra = {"open": str(p["open"])} if p.get("open") else None
        entries: list[dict] = []
        changed_all: list[str] = []
        any_mut = False
        ck = p.get("checkpoint")
        force_ckpt = ck is True
        asked_ckpt = force_ckpt   # asked for: a world without checkpoints then warns
        budget = self.max_reply
        if batch:
            done: list[dict] = []
            first = True
            for i, s in enumerate(steps):
                try:
                    m, n, res, changed, entry = self._run(s["method"], s.get("params"), warn, t0,
                                                          dict(open_extra or {}, batch=True) if first else
                                                          {"batch": True})
                except SatkError as e:
                    data = dict(e.data or {})
                    data.update(step=i + 1, done=len(done))
                    raise SatkError(e.code, f"batch step {i + 1}: {e.msg}", hint=e.hint,
                                    did_you_mean=e.did_you_mean, data=data) from None
                first = False
                if changed:
                    entry["changed"] = changed
                entries.append(entry)
                row: dict[str, Any] = {"n": n, "method": m.name}
                if res:
                    short = compact(jsonable(res))
                    if short:
                        row["result"] = short
                done.append(row)
                _merge_changed(changed_all, changed)
                any_mut = any_mut or not m.readonly
                force_ckpt = force_ckpt or m.checkpoint
                asked_ckpt = asked_ckpt or m.checkpoint
            reply: dict[str, Any] = {"method": "batch", "n": self.n, "steps": done}
            ro_all = not any_mut
            if ck is None and sum(1 for e in entries if not e.get("ro")) >= 2:
                force_ckpt = True  # one checkpoint per multi-step batch: a restart resumes after it
            budget = min(MAX_BATCH_REPLY, self.max_reply + STEP_REPLY * len(done))
        else:
            m, n, res, changed, entry = self._run(name, p.get("params"), warn, t0, open_extra)
            entries.append(entry)
            _merge_changed(changed_all, changed)
            any_mut = not m.readonly
            force_ckpt = force_ckpt or m.checkpoint
            asked_ckpt = asked_ckpt or m.checkpoint
            reply = {"method": m.name, "n": n}
            if res:
                reply["result"] = jsonable(res)
            ro_all = m.readonly
        if changed_all:
            reply["changed"] = changed_all
        mode = p.get("stats") or "auto"
        if mode == "scene" or (mode == "auto" and not ro_all):
            try:
                st = dict(self.host.stats(changed_all if (mode == "auto" and changed_all) else None))
                warn.extend(str(w) for w in (st.pop("warn", None) or []))
                reply["stats"] = st
            except Exception as e:  # noqa: BLE001
                warn.append(f"INTERNAL: stats failed: {type(e).__name__}: {e}")
        if snap is not None:
            try:
                reply["snapshot"] = self.host.snapshot(dict(snap), self.n)
            except SatkError as e:
                warn.append(f"{e.code}: snapshot: {e.msg}")
            except Exception as e:  # noqa: BLE001
                warn.append(f"INTERNAL: snapshot failed: {type(e).__name__}: {e}")
        auto = ck is not False and any_mut and self.checkpoint_every and self._since_ckpt >= self.checkpoint_every
        if force_ckpt or auto:
            cp = self.checkpoints
            if cp is None:
                if asked_ckpt:
                    warn.append("UNSUPPORTED: checkpoint: this world keeps no checkpoints")
            else:
                try:
                    reply["checkpoint"] = cp.save(self.n)
                    entries[-1]["checkpoint"] = reply["checkpoint"]
                    self._since_ckpt = 0
                except SatkError as e:
                    warn.append(f"{e.code}: checkpoint: {e.msg}")
                except Exception as e:  # noqa: BLE001
                    warn.append(f"INTERNAL: checkpoint failed: {type(e).__name__}: {e}")
        if p.get("save"):
            try:
                reply["saved"] = self.host.save(p["save"])
                entries[-1]["save"] = reply["saved"]
            except SatkError as e:
                warn.append(f"{e.code}: save: {e.msg}")
            except Exception as e:  # noqa: BLE001
                warn.append(f"INTERNAL: save failed: {type(e).__name__}: {e}")
        reply["ms"] = round((time.perf_counter() - t0) * 1000, 2)
        if warn:
            reply["warn"] = warn[:10]
        for entry in entries:
            entry["ok"] = True
            entry.setdefault("ms", reply["ms"] if len(entries) == 1 else None)
            if entry.get("ms") is None:
                entry.pop("ms")
        if len(entries) == 1 and changed_all and not batch:
            entries[0]["changed"] = changed_all
        for entry in entries:
            self.journal.append(entry)
        return fit_reply(reply, budget)

    def _failed(self, entry: dict, t0: float, e: SatkError) -> None:
        entry.update(ok=False, ms=round((time.perf_counter() - t0) * 1000, 2), error=f"{e.code}: {e.msg}"[:300])
        try:
            self.journal.append(entry)
        except OSError:
            pass


# --------------------------------------------------------------------------- built-in session methods


def _builtin_methods(core: AuthorCore) -> dict[str, Method]:
    """``session.*``: checkpoints and the journal, the same in every host."""

    def need() -> Checkpoints:
        cp = core.checkpoints
        if cp is None:
            raise SatkError("UNSUPPORTED", "this world keeps no checkpoints")
        return cp

    def checkpoint(ctx: Any, p: dict) -> dict:
        """Save a checkpoint now (tag = a gate name such as G1; tagged checkpoints are never pruned)."""
        tag = p.get("tag")
        if tag is not None and (not isinstance(tag, str) or not TAG_RE.match(tag)):
            raise SatkError("BAD_PARAMS", f"session.checkpoint: bad tag {tag!r} (letters, digits, '_')")
        path = need().save(core.n, tag)
        core._since_ckpt = 0
        out: dict[str, Any] = {"checkpoint": path, "_journal": {"checkpoint": path}}
        if tag:
            out["tag"] = tag
        return out

    def restore(ctx: Any, p: dict) -> dict:
        """Replace the scene by a checkpoint (ref = step number, tag or 'last'); later steps stay in the journal."""
        hit = need().load(p.get("ref", "last"))
        out: dict[str, Any] = {"restored": hit["n"], "path": hit["path"],
                               "_journal": {"restore": hit["n"], "restore_path": hit["path"]}}
        if hit.get("tag"):
            out["tag"] = hit["tag"]
        names = getattr(core.host, "object_names", None)
        if callable(names):
            out["changed"] = list(names())[:200]
        return out

    def checkpoints(ctx: Any, p: dict) -> dict:
        """List the checkpoints of this session (step, tag, size)."""
        cp = core.checkpoints
        rows = cp.list() if cp is not None else []
        limit = int(p.get("limit", 20))
        out = [{k: r[k] for k in ("n", "tag", "kb") if k in r} for r in rows[-max(1, limit):]]
        return {"checkpoints": out, "total": len(rows), "folder": cp.folder.replace("\\", "/") if cp else None}

    def prune(ctx: Any, p: dict) -> dict:
        """Delete old untagged checkpoints, keeping the last 'keep' (default 10) and every tagged one."""
        keep = p.get("keep", KEEP_CHECKPOINTS)
        if isinstance(keep, bool) or not isinstance(keep, int) or keep < 0:
            raise SatkError("BAD_PARAMS", f"session.prune: keep must be an integer >= 0, got {keep!r}")
        gone = need().prune(keep)
        return {"removed": len(gone), "kept": len(need().list())}

    def journal(ctx: Any, p: dict) -> dict:
        """The last steps of the journal: n, method, ok, changed objects, checkpoint (last = how many)."""
        last = int(p.get("last", 10))
        rows = []
        for e in core.journal.entries()[-max(1, min(last, 100)):]:
            row: dict[str, Any] = {"n": e.get("n"), "method": e.get("method")}
            if not e.get("ok", True):
                row["error"] = str(e.get("error") or "")[:80]
            if e.get("changed"):
                row["changed"] = e["changed"][:5]
            if e.get("checkpoint"):
                row["checkpoint"] = True
            rows.append(row)
        return {"steps": rows, "path": (core.journal.path or "").replace("\\", "/") or None}

    for fn in (checkpoint, checkpoints, prune, journal):
        fn.readonly = True  # type: ignore[attr-defined]
    table = {"session.checkpoint": checkpoint, "session.restore": restore, "session.checkpoints": checkpoints,
             "session.prune": prune, "session.journal": journal}
    return {k: Method(k, fn, __name__, bool(getattr(fn, "readonly", False))) for k, fn in table.items()}


class AuthorWorld:
    """A SAAP world with capabilities ``core`` + ``author`` around an :class:`AuthorCore`.

    :class:`satk.saap.server.SaapServer` calls :meth:`handle`; the Blender world overrides it to run
    the request on Blender's main thread.
    """

    role = "blender"
    impl = "satk-studio"
    caps: list[str] = ["core", "author"]

    def __init__(self, core: AuthorCore, *, build: dict | None = None, info: dict | None = None):
        self.core = core
        self.build = dict(build or {})
        self.info = dict(info or {})
        self.t0 = time.monotonic()
        self.quit_requested = False
        self.caps = list(type(self).caps)

    def hello_info(self) -> dict:
        from .. import __version__

        return {"role": self.role, "impl": self.impl, "impl_version": __version__,
                "build": self.build or {"id": f"{self.impl}-{__version__}"}}

    def meta(self) -> dict:
        return {"frame": self.core.n, "rev": {"scene": self.core.rev, "camera": 0}}

    def status_info(self) -> dict:
        """Extra ``status.author`` fields of the host (objects, file, ...)."""
        return {}

    def dispatch(self, method: str, params: dict) -> dict:
        from ..saap import frame as F

        if method == "hello":
            return {"saap": 1, **self.hello_info(), "caps": list(self.caps), "world": {"units": "m", "up": "z"},
                    "limits": {"max_request": F.MAX_REQUEST, "max_response": F.MAX_RESPONSE},
                    "methods": len(self.core.methods), **self.info}
        if method == "ping":
            return {"t_ms": round((time.monotonic() - self.t0) * 1000, 3)}
        if method == "status":
            author: dict[str, Any] = {"calls": self.core.calls, "step": self.core.n,
                                      "methods": len(self.core.methods)}
            if self.core.journal.path:
                author["journal"] = self.core.journal.path.replace("\\", "/")
            author.update(self.status_info())
            return {"frame": self.core.n, "rev": {"scene": self.core.rev, "camera": 0}, "author": author}
        if method == "quit":
            self.quit_requested = True
            return {}
        if method == "author.methods":
            return self.core.method_list(params.get("query"), int(params.get("limit") or 500))
        if method == "author.call":
            return self.core.call(params)
        raise SatkError("UNKNOWN_METHOD", f"unknown method {method!r}")

    def handle(self, method: str, params: dict) -> dict:
        return self.dispatch(method, params or {})
