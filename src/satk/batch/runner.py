"""The batch runner: one registered operation, many inputs, one JSONL record per input.

* inputs come from :func:`satk.batch.sources.expand`; each input fills the operation's input parameter
  (``param``; default: its first required parameter) on top of the shared arguments (``--arg k=v``,
  ``--args JSON``); string arguments may use ``{item} {name} {stem} {ext} {parent} {n}`` per input;
* operations in :data:`THREAD_SAFE` (or marked ``spec.extra["thread_safe"] = True`` by their package) run in
  ``jobs`` threads; every other operation runs sequentially;
* every record is appended to the results file as soon as it is known (an interrupted run keeps them);
  at the end the file is rewritten with one line per input in input order. ``resume`` skips inputs whose
  stable key (sha256 of the operation and its arguments) already has an ``ok`` record;
* the answer is a summary: counts, error codes, summed ``summary`` counters, and the first failures. Any
  failed input makes the whole batch ``CHECK_FAILED`` (exit code 1).
"""

from __future__ import annotations

import concurrent.futures as cf
import contextvars
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from ..core.envelope import clamp_limit, dumps, table
from ..core.errors import SatkError
from ..core.paths import atomic_write, cfg, ensure_writable, jpath
from ..core.registry import OpSpec, ParamSpec, invoke, progress_handler, report_progress
from .common import check_policy, input_param, nested, resolve_op
from .sources import Inputs, expand

__all__ = ["THREAD_SAFE", "run_batch", "parse_kv", "read_results", "report", "default_out", "item_key"]

#: Operations known to be safe in several threads at once (read-only, no shared mutable state).
THREAD_SAFE: frozenset[str] = frozenset({
    "asset.lint", "asset.get", "asset.find", "asset.refs", "world.near", "index.query",
    "formats.dump", "formats.ls", "mod.inspect", "mod.check", "mod.effective",
    "crash.known", "crash.analyze", "crash.info",
})
_PLACEHOLDER = re.compile(r"\{(item|name|stem|ext|parent|n)\}")
_MSG = 200


def _mute(done: float, total: float | None, msg: str | None) -> None:  # inner operations report nothing
    return None


def parse_kv(pairs: Iterable[str] | None, what: str = "--arg") -> dict[str, Any]:
    """``["sev=info", "rule=dff.parse", "txd.pow2"]`` -> ``{"sev": "info", "rule": "dff.parse,txd.pow2"}``.

    The CLI splits list options at commas, so a fragment without ``=`` continues the previous value.
    Values stay strings (the operation's own parameter types convert them; JSON lists work: ``rule=["a","b"]``).
    """
    out: dict[str, Any] = {}
    last: str | None = None
    for raw in pairs or ():
        s = str(raw)
        if "=" not in s:
            if last is None:
                raise SatkError("BAD_PARAMS", f"{what} {s!r}: expected key=value", hint=f"{what} sev=info")
            out[last] = f"{out[last]},{s}"
            continue
        k, v = s.split("=", 1)
        k = k.strip().lstrip("-").replace("-", "_")
        if not k:
            raise SatkError("BAD_PARAMS", f"{what} {s!r}: empty key", hint=f"{what} sev=info")
        out[k] = v
        last = k
    return out


def item_key(op_name: str, args: dict) -> str:
    """Stable key of one call: sha256 of the operation name and its arguments (sorted JSON), 16 hex digits."""
    blob = json.dumps({"op": op_name, "args": args}, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", s).strip("._") or "batch"


def default_out(op_name: str, over: str, base_args: dict, param: str | None, profile: str | None) -> Path:
    """``<work>/out/batch/<op>-<hash>.jsonl``: the same batch always gets the same file (``--resume``)."""
    h = item_key(op_name, {"over": over, "args": base_args, "param": param, "profile": profile})[:8]
    return Path(os.path.abspath(cfg().paths.work)) / "out" / "batch" / f"{_slug(op_name)}-{h}.jsonl"


def _template(v: Any, ctx: dict[str, str]) -> Any:
    if isinstance(v, str) and "{" in v:
        return _PLACEHOLDER.sub(lambda m: ctx[m.group(1)], v)
    if isinstance(v, list):
        return [_template(x, ctx) for x in v]
    return v


def _ctx(label: str, n: int) -> dict[str, str]:
    s = label.replace("\\", "/")
    name = s.rsplit("/", 1)[-1]
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    return {"item": label, "name": name, "stem": stem, "ext": ext, "parent": s.rsplit("/", 1)[0] if "/" in s else "",
            "n": str(n)}


@dataclass
class _Task:
    n: int                    # 1-based input number
    label: str                # the input as given (path, SID, or the input parameter of an argument set)
    args: dict
    key: str
    error: dict | None = None  # binding error envelope (the operation is not called)


@dataclass
class _Run:
    spec: OpSpec
    param: ParamSpec | None
    base: dict
    profile: str | None
    tasks: list[_Task] = field(default_factory=list)
    dup: int = 0


def _label(item: Any, param: ParamSpec | None) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict) and param is not None and param.name in item:
        v = item[param.name]
        label = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        if param.name != "name" and isinstance(item.get("name"), str):
            label += f":{item['name']}"  # argument sets of one kind ("vehicle") differ by name
        return label
    return json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)


def _plan(spec: OpSpec, inputs: Inputs, base: dict, param: ParamSpec | None, profile: str | None, *,
          pass_input: bool = True) -> _Run:
    run = _Run(spec, param, base, profile)
    names = {p.name for p in spec.params}
    seen: set[str] = set()
    for n, item in enumerate(inputs.items, 1):
        label = _label(item, param)
        args = {k: _template(v, _ctx(label, n)) for k, v in base.items()}
        if isinstance(item, dict):
            args.update({str(k).replace("-", "_"): v for k, v in item.items()})
        elif pass_input:
            if param is None:
                raise SatkError("BAD_PARAMS", f"{spec.name} has no positional parameter for the inputs",
                                hint="name the parameter that gets each input: --param <name> (or --param none "
                                     "and use {item} in --arg)", data={"params": sorted(names)})
            args[param.name] = [item] if param.kind == "list" else item
        if profile and "profile" in names and "profile" not in args:
            args["profile"] = profile
        key = item_key(spec.name, args)
        if key in seen:
            run.dup += 1
            continue
        seen.add(key)
        task = _Task(n, label, args, key)
        try:
            spec.bind(args)
        except SatkError as e:
            task.error = e.to_dict()
        run.tasks.append(task)
    return run


def _brief(env: dict) -> dict:
    """The envelope without bulky parts: table rows/cols and long lists are dropped."""
    if not env.get("ok", True):
        err = dict(env.get("error") or {})
        if isinstance(err.get("data"), dict):
            err["data"] = {k: v for k, v in err["data"].items() if k not in ("rows", "cols")}
        return {"ok": False, "error": err}
    return {k: v for k, v in env.items()
            if k not in ("rows", "cols") and not (isinstance(v, list) and len(v) > 10)}


def _record(spec: OpSpec, t: _Task, env: dict, keep: str) -> dict:
    rec: dict[str, Any] = {"key": t.key, "op": spec.name, "item": t.label, "n": t.n}
    if env.get("ok", True):
        rec["status"] = "ok"
    else:
        err = env.get("error") or {}
        rec["status"] = "fail"
        rec["code"] = err.get("code", "INTERNAL")
        rec["msg"] = str(err.get("msg", ""))[:_MSG]
    rec["args"] = t.args
    if keep == "full":
        rec["result"] = env
    elif keep == "brief":
        rec["result"] = _brief(env)
    return rec


def read_results(path: str | os.PathLike) -> list[dict]:
    """Records of a results file (latest record per key wins, in file order); bad lines are skipped."""
    p = Path(path)
    if not p.is_file():
        return []
    by_key: dict[str, dict] = {}
    with open(p, encoding="utf-8", errors="replace") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            try:
                rec = json.loads(s)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict) and isinstance(rec.get("key"), str):
                by_key.pop(rec["key"], None)
                by_key[rec["key"]] = rec
    return list(by_key.values())


def _summary_counts(env: dict) -> dict | None:
    s = env.get("summary")
    if not isinstance(s, dict) and not env.get("ok", True):
        data = (env.get("error") or {}).get("data")
        s = data.get("summary") if isinstance(data, dict) else None
    if isinstance(s, dict):
        return {k: v for k, v in s.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
    return None


def _add(totals: dict, counts: dict | None) -> None:
    for k, v in (counts or {}).items():
        totals[k] = totals.get(k, 0) + v


class _Progress:
    """``[12/50] ok 11 fail 1`` on stderr while a command-line batch runs in a terminal."""

    def __init__(self, total: int, name: str):
        from ..core.cli import surface

        self.on = surface() == "cli" and getattr(sys.stderr, "isatty", lambda: False)()
        self.total, self.name, self.last = total, name, 0.0

    def __call__(self, done: int, ok: int, fail: int, final: bool = False) -> None:
        report_progress(done, self.total, f"{self.name}: {done}/{self.total}, {fail} failed")
        if not self.on:
            return
        now = time.monotonic()
        if final or now - self.last > 0.1:
            self.last = now
            sys.stderr.write(f"\r[{done}/{self.total}] ok {ok} fail {fail}" + ("\n" if final else ""))
            sys.stderr.flush()


def _call(spec: OpSpec, args: dict) -> dict:
    with progress_handler(_mute):
        return invoke(spec, args)


def _display(label: str, base: str | None) -> str:
    if base and label.replace("\\", "/").lower().startswith(base.lower() + "/"):
        return label.replace("\\", "/")[len(base) + 1:]
    return label


def run_batch(op: str, over: str, *, arg: Iterable[str] | None = None, args: dict | None = None,
              param: str | None = None, jobs: int = 1, resume: bool = False, out: str | None = None,
              keep: str = "full", max_fail: int = 0, allow_empty: bool = False, profile: str | None = None,
              dry_run: bool = False, yes: bool = False, limit: int = 20) -> dict:
    """Run ``op`` once per input of ``over``; see the module docstring. Returns the summary envelope."""
    lim = clamp_limit(limit)
    if keep not in ("full", "brief", "none"):
        raise SatkError("BAD_PARAMS", f"keep: {keep!r} is not one of full, brief, none")
    if jobs < 0 or jobs > 64:
        raise SatkError("BAD_PARAMS", "jobs must be in 0..64 (0 = one per CPU, at most 8)")
    if max_fail < 0:
        raise SatkError("BAD_PARAMS", "max_fail must be >= 0 (0 = no limit)")
    spec = resolve_op(op)
    if spec.name in ("batch", "batch.report"):
        raise SatkError("BAD_PARAMS", "a batch of batches: give the inner operation directly",
                        hint="satk batch asset.lint --over \"mods/*.dff\"")
    check_policy(spec, yes=yes)
    base = parse_kv(arg)
    if args:
        base.update({str(k).replace("-", "_"): v for k, v in args.items()})
    names = [p.name for p in spec.params]
    unknown = [k for k in base if k not in names]
    if unknown:
        import difflib

        raise SatkError("BAD_PARAMS", f"{spec.name}: unknown parameter {unknown[0]!r} in --arg",
                        did_you_mean=difflib.get_close_matches(unknown[0], names, n=3, cutoff=0.5),
                        hint=f"satk {spec.cli} -h", data={"params": names})
    no_param = isinstance(param, str) and param.strip().lower() in ("none", "-")
    prm = None if no_param else input_param(spec, param)  # each input overrides a --arg of the same parameter
    inputs = expand(over, profile=profile, params=set(names))
    run = _plan(spec, inputs, base, prm, profile, pass_input=not no_param)
    warn = list(inputs.warn)
    if run.dup:
        warn.append(f"DUPLICATE: {run.dup} input(s) repeat an earlier one and run once")
    if not run.tasks:
        if not allow_empty:
            raise SatkError("NOT_FOUND", f"--over matched nothing: {over}",
                            hint="check the glob/query (a glob needs * or ?), or pass --allow-empty",
                            data={"over": over, "kind": inputs.kind})
        return {**table(["item", "status", "code", "msg"], [], warn=warn), "op": spec.name, "items": 0,
                "counts": {"ok": 0, "fail": 0}}
    first_bad = next((t for t in run.tasks if t.error is not None), None)
    if first_bad is not None and first_bad is run.tasks[0] and not dry_run:
        err = first_bad.error["error"]
        raise SatkError(err.get("code", "BAD_PARAMS"), f"{spec.name} on {first_bad.label}: {err.get('msg', '')}",
                        hint=err.get("hint") or f"satk {spec.cli} -h", did_you_mean=err.get("did_you_mean") or (),
                        data={"args": first_bad.args})
    nj = jobs or min(8, os.cpu_count() or 2)
    threaded = nj > 1 and len(run.tasks) > 1 and (spec.name in THREAD_SAFE or spec.extra.get("thread_safe") is True)
    if nj > 1 and not threaded and len(run.tasks) > 1:
        warn.append(f"JOBS: {spec.name} is not known to be thread-safe; it ran sequentially")
    out_path = Path(os.path.abspath(out)) if out else default_out(spec.name, over, base, param, profile)
    with nested(f"batch {spec.name}"):
        if dry_run:
            return _dry(run, inputs, out_path, resume, threaded, nj, lim, warn)
        return _execute(run, inputs, out_path, resume, threaded, nj, keep, max_fail, lim, warn)


def _dry(run: _Run, inputs: Inputs, out_path: Path, resume: bool, threaded: bool, nj: int, lim: int,
         warn: list[str]) -> dict:
    done = {r["key"] for r in read_results(out_path) if r.get("status") == "ok"} if resume else set()
    rows = []
    for t in run.tasks[:lim]:
        state = "invalid" if t.error else ("done" if t.key in done else "run")
        rows.append([t.n, _display(t.label, inputs.base), state,
                     json.dumps(t.args, ensure_ascii=False, sort_keys=True, default=str)[:160]])
    invalid = sum(1 for t in run.tasks if t.error)
    env = table(["n", "item", "state", "args"], rows, total=len(run.tasks), warn=warn)
    env.update({"op": run.spec.name, "dry_run": True, "items": len(run.tasks), "source": inputs.kind,
                "base": inputs.base, "jobs": nj if threaded else 1, "out": jpath(out_path),
                "would_skip": sum(1 for t in run.tasks if t.key in done) or None, "invalid": invalid or None})
    if invalid:
        bad = next(t for t in run.tasks if t.error)
        env["first_invalid"] = f"{bad.label}: {bad.error['error'].get('msg', '')}"[:_MSG]
    return {k: v for k, v in env.items() if v is not None}


def _execute(run: _Run, inputs: Inputs, out_path: Path, resume: bool, threaded: bool, nj: int, keep: str,
             max_fail: int, lim: int, warn: list[str]) -> dict:
    spec = run.spec
    ensure_writable(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    old = read_results(out_path) if resume else []
    done = {r["key"]: r for r in old if r.get("status") == "ok"}
    todo = [t for t in run.tasks if t.key not in done]
    results: dict[str, dict] = {}
    counts = {"ok": 0, "fail": 0}
    by_code: dict[str, int] = {}
    totals: dict[str, float] = {}
    progress = _Progress(len(todo), spec.name)
    stopped = False

    def take(t: _Task, env: dict, fh) -> None:
        rec = _record(spec, t, env, keep)
        results[t.key] = rec
        counts[rec["status"]] += 1
        if rec["status"] == "fail":
            by_code[rec["code"]] = by_code.get(rec["code"], 0) + 1
        _add(totals, _summary_counts(env))
        fh.write(dumps(rec) + "\n")
        fh.flush()
        progress(counts["ok"] + counts["fail"], counts["ok"], counts["fail"])

    mode = "a" if resume and out_path.is_file() else "w"
    with open(out_path, mode, encoding="utf-8", newline="\n") as fh:
        if not threaded:
            for t in todo:
                if max_fail and counts["fail"] >= max_fail:
                    stopped = True
                    break
                take(t, t.error if t.error else _call(spec, t.args), fh)
        else:
            pool = cf.ThreadPoolExecutor(max_workers=nj, thread_name_prefix="satk-batch")
            try:
                futs = {}
                for t in todo:
                    if t.error:
                        take(t, t.error, fh)
                        continue
                    ctx = contextvars.copy_context()
                    futs[pool.submit(ctx.run, _call, spec, t.args)] = t
                for f in cf.as_completed(futs):
                    t = futs[f]
                    if f.cancelled():
                        continue
                    take(t, f.result(), fh)
                    if max_fail and counts["fail"] >= max_fail and not stopped:
                        stopped = True
                        for g in futs:
                            g.cancel()
            finally:
                pool.shutdown(wait=True, cancel_futures=True)
    progress(counts["ok"] + counts["fail"], counts["ok"], counts["fail"], final=True)
    # one line per key: this run's inputs in input order, then records of other inputs from earlier runs
    keys = [t.key for t in run.tasks]
    merged: list[dict] = []
    keyset = set(keys)
    old_by_key = {r["key"]: r for r in old}
    by_key_n = {t.key: t.n for t in run.tasks}
    for k in keys:
        rec = results.get(k) or old_by_key.get(k)
        if rec is not None:
            merged.append({**rec, "n": by_key_n[k]})
    merged += [r for r in old if r["key"] not in keyset]
    atomic_write(out_path, "".join(dumps(r) + "\n" for r in merged))
    not_run = len(todo) - counts["ok"] - counts["fail"]
    resumed = len(run.tasks) - len(todo)
    fails = [results[t.key] for t in run.tasks if t.key in results and results[t.key]["status"] == "fail"]
    rows = [[_display(r["item"], inputs.base), r["status"], r.get("code"), r.get("msg")] for r in fails[:lim]]
    cnt = {"ok": counts["ok"], "fail": counts["fail"], "resumed": resumed or None, "not_run": not_run or None}
    cnt = {k: v for k, v in cnt.items() if v is not None}
    if stopped:
        warn.append(f"MAX_FAIL: stopped after {counts['fail']} failure(s); {not_run} input(s) not run (--resume)")
    fields: dict[str, Any] = {"op": spec.name, "items": len(run.tasks), "source": inputs.kind, "base": inputs.base,
                              "counts": cnt, "by_code": by_code or None, "totals": totals or None,
                              "jobs": nj if threaded else 1, "out": jpath(out_path)}
    fields = {k: v for k, v in fields.items() if v is not None}
    if counts["fail"] or stopped:
        parts = [f"{counts['fail']} of {len(run.tasks)} input(s) failed", f"{counts['ok']} ok"]
        if resumed:
            parts.append(f"{resumed} done earlier")
        if not_run:
            parts.append(f"{not_run} not run")
        codes = ", ".join(f"{k} {v}" for k, v in sorted(by_code.items()))
        raise SatkError("CHECK_FAILED", f"batch {spec.name}: " + ", ".join(parts) + (f" ({codes})" if codes else ""),
                        hint=f"satk batch report {jpath(out_path)} --status fail",
                        data={"cols": ["item", "status", "code", "msg"], "rows": rows, **fields,
                              **({"warn": warn} if warn else {})})
    env = table(["item", "status", "code", "msg"], rows, total=len(fails), warn=warn)
    env.update(fields)
    return env


# --------------------------------------------------------------------------- report


def _dig(v: Any, path: str) -> Any:
    """A dotted path into an answer; a failed answer also looks inside ``error.data`` (CHECK_FAILED summaries)."""
    got = _dig1(v, path)
    if got is None and isinstance(v, dict) and not v.get("ok", True):
        data = (v.get("error") or {}).get("data")
        if isinstance(data, dict):
            got = _dig1(data, path)
    return got


def _dig1(v: Any, path: str) -> Any:
    cur = v
    for part in path.split("."):
        if isinstance(cur, dict):
            if part not in cur:
                return None
            cur = cur[part]
        elif isinstance(cur, list) and re.fullmatch(r"-?\d+", part):
            i = int(part)
            if not -len(cur) <= i < len(cur):
                return None
            cur = cur[i]
        else:
            return None
    return cur


def report(path: str, *, status: str = "all", fields: list[str] | None = None, limit: int = 20,
           cursor: str | None = None) -> dict:
    """Table of a results file: ``item status code msg`` or the given result fields (dotted paths)."""
    p = Path(path)
    if not p.is_file():
        raise SatkError("NOT_FOUND", f"results file not found: {jpath(p)}",
                        hint="the 'out' value of a 'satk batch' answer (work/out/batch/<op>-<hash>.jsonl)")
    recs = read_results(p)
    if not recs:
        raise SatkError("BAD_PARAMS", f"no batch records in {jpath(p)}", hint="a JSONL file written by satk batch")
    counts: dict[str, int] = {}
    by_code: dict[str, int] = {}
    totals: dict[str, float] = {}
    for r in recs:
        counts[r.get("status", "?")] = counts.get(r.get("status", "?"), 0) + 1
        if r.get("code"):
            by_code[r["code"]] = by_code.get(r["code"], 0) + 1
        if isinstance(r.get("result"), dict):
            _add(totals, _summary_counts(r["result"]))
    sel = [r for r in recs if status == "all" or r.get("status") == status]
    lim = clamp_limit(limit)
    try:
        start = int(cursor) if cursor else 0
    except ValueError:
        raise SatkError("BAD_PARAMS", f"bad cursor {cursor!r}") from None
    page = sel[start:start + lim]
    nxt = str(start + lim) if start + lim < len(sel) else None
    labels = [r.get("item", "") for r in sel]
    base = _common(labels)
    if fields:
        cols = ["item", "status", *fields]
        missing_all = all(_dig(r.get("result"), f) is None for f in fields for r in sel[:50]) if sel else False
        rows = [[_display(r.get("item", ""), base), r.get("status"), *[_dig(r.get("result"), f) for f in fields]]
                for r in page]
    else:
        cols = ["item", "status", "code", "msg"]
        missing_all = False
        rows = [[_display(r.get("item", ""), base), r.get("status"), r.get("code"), r.get("msg")] for r in page]
    env = table(cols, rows, total=len(sel), next=nxt)
    ops = sorted({r.get("op") for r in recs if r.get("op")})
    env.update({k: v for k, v in {"op": ",".join(ops) or None, "base": base, "counts": counts,
                                  "by_code": by_code or None, "totals": totals or None}.items() if v is not None})
    if missing_all:
        env.setdefault("warn", []).append("FIELDS: none of the records has these result fields "
                                          "(results kept with --keep full have them all)")
    return env


def _common(labels: list[str]) -> str | None:
    paths = [s.replace("\\", "/") for s in labels if isinstance(s, str)]
    if len(paths) < 2 or not all("/" in s for s in paths):
        return None
    try:
        return os.path.commonpath([s.rsplit("/", 1)[0] for s in paths]).replace("\\", "/") or None
    except ValueError:
        return None
