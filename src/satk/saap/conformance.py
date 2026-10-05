"""SAAP/1 conformance: case files, static validation and the runner (SPEC §4.8.9).

Case format: ``proto/SAAP-v1.md`` §9. Two drivers run the same cases:

* :class:`SaapDriver` — a real SAAP endpoint over TCP (raw frames, so transport/auth cases run);
* :class:`BackendDriver` — any object with ``call(method, params) -> result`` that raises
  ``SatkError`` (the ``ARIANE_IPC/1`` adapter, the in-process mock); ``transport:"saap"``
  cases are skipped for it.

Example::

    drv = SaapDriver("127.0.0.1", port, token)
    report = run(drv)            # {"pass": 40, "fail": 0, "skip": 7, "results": [...]}
"""

from __future__ import annotations

import json
import math
import os
import re
import secrets
import socket
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from ..core.errors import SatkError
from . import frame as F
from . import schema as S
from .protocol import CAPABILITIES, ERROR_CODES

__all__ = [
    "CONFORMANCE_DIR",
    "Case",
    "load_cases",
    "validate_cases",
    "match",
    "SaapDriver",
    "BackendDriver",
    "run",
]

CONFORMANCE_DIR: Path = S.PROTO_DIR / "conformance"
_VAR_WHOLE = re.compile(r"^\$\{([a-z_][a-z0-9_]*)\}$")
_VAR_ANY = re.compile(r"\$\{([a-z_][a-z0-9_]*)\}")
_BUILTIN_VARS = ("token", "bad_token", "prefix")
_OPS = ("$approx", "$type", "$gte", "$lte", "$len", "$minlen", "$contains", "$match", "$file")
#: Largest ``raw.fill`` (filler bytes after a hand-made header) a case may ask for.
RAW_FILL_MAX = 2 * F.MAX_REQUEST


@dataclass
class Case:
    id: str
    data: dict
    file: str
    line: int

    @property
    def caps(self) -> list[str]:
        return list(self.data.get("caps") or ["core"])

    @property
    def transport(self) -> str | None:
        return self.data.get("transport")

    @property
    def steps(self) -> list[dict]:
        return list(self.data.get("steps") or [])


# --------------------------------------------------------------------------- loading


def _expand_paths(paths: Iterable[str | Path] | None) -> list[Path]:
    if not paths:
        return sorted(CONFORMANCE_DIR.glob("*.jsonl"))
    out: list[Path] = []
    for p in paths:
        s = str(p)
        if any(ch in s for ch in "*?["):
            base = Path(s).parent
            out.extend(sorted(base.glob(Path(s).name)))
        else:
            out.append(Path(s))
    return out


def load_cases(paths: Iterable[str | Path] | None = None) -> tuple[list[Case], list[str]]:
    """(cases, errors) from JSONL files (default: ``proto/conformance/*.jsonl``)."""
    cases: list[Case] = []
    errors: list[str] = []
    for p in _expand_paths(paths):
        if not p.is_file():
            errors.append(f"{p}: no such file")
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip() or line.lstrip().startswith("//"):
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError as e:
                errors.append(f"{p.name}:{i}: not JSON: {e.msg}")
                continue
            if not isinstance(d, dict) or not isinstance(d.get("id"), str):
                errors.append(f"{p.name}:{i}: a case must be an object with a string 'id'")
                continue
            cases.append(Case(d["id"], d, p.name, i))
    return cases, errors


# --------------------------------------------------------------------------- variables


def _expand(value: Any, vars: dict[str, Any], missing: Callable[[str], Any] | None = None) -> Any:
    """Substitute ``${var}`` in strings (a whole-string reference keeps the JSON type)."""
    if isinstance(value, str):
        m = _VAR_WHOLE.match(value)
        if m:
            k = m.group(1)
            if k in vars:
                return vars[k]
            if missing is not None:
                return missing(k)
            raise KeyError(k)

        def sub(mm: re.Match) -> str:
            k = mm.group(1)
            if k in vars:
                return str(vars[k])
            if missing is not None:
                return str(missing(k))
            raise KeyError(k)

        return _VAR_ANY.sub(sub, value)
    if isinstance(value, list):
        return [_expand(v, vars, missing) for v in value]
    if isinstance(value, dict):
        return {k: _expand(v, vars, missing) for k, v in value.items()}
    return value


def _dig(obj: Any, dotted: str) -> Any:
    cur = obj
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            raise KeyError(dotted)
    return cur


# --------------------------------------------------------------------------- matching


def _is_op(p: Any) -> bool:
    return isinstance(p, dict) and any(k in _OPS for k in p)


def _png_size(path: Path) -> tuple[int, int] | None:
    try:
        with open(path, "rb") as f:
            head = f.read(24)
    except OSError:
        return None
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", head[16:24])


def _inside(path: Path, root: Path) -> bool:
    try:
        a = os.path.normcase(os.path.abspath(path))
        b = os.path.normcase(os.path.abspath(root))
        return os.path.commonpath([a, b]) == b
    except ValueError:
        return False


def _num_eq(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    return False


def match(actual: Any, pattern: Any, path: str = "result", ctx: dict | None = None) -> list[str]:
    """Errors of ``actual`` against a conformance pattern (subset match, SAAP-v1.md §9)."""
    ctx = ctx or {}
    if _is_op(pattern):
        return _match_op(actual, pattern, path, ctx)
    if isinstance(pattern, dict):
        if not isinstance(actual, dict):
            return [f"{path}: expected an object, got {json.dumps(actual)[:80]}"]
        errs: list[str] = []
        for k, p in pattern.items():
            if k not in actual:
                errs.append(f"{path}.{k}: missing")
            else:
                errs.extend(match(actual[k], p, f"{path}.{k}", ctx))
        return errs
    if isinstance(pattern, list):
        if not isinstance(actual, list):
            return [f"{path}: expected a list, got {json.dumps(actual)[:80]}"]
        if len(actual) != len(pattern):
            return [f"{path}: expected {len(pattern)} items, got {len(actual)}"]
        errs = []
        for i, (a, p) in enumerate(zip(actual, pattern)):
            errs.extend(match(a, p, f"{path}[{i}]", ctx))
        return errs
    if isinstance(pattern, (int, float)) and not isinstance(pattern, bool):
        return [] if _num_eq(actual, pattern) else [f"{path}: expected {pattern}, got {json.dumps(actual)[:80]}"]
    if actual == pattern and type(actual) is type(pattern):
        return []
    return [f"{path}: expected {json.dumps(pattern)}, got {json.dumps(actual)[:80]}"]


def _match_op(actual: Any, p: dict, path: str, ctx: dict) -> list[str]:
    errs: list[str] = []
    if "$approx" in p:
        want = p["$approx"]
        tol = float(p.get("tol", 1e-6))
        a_list = actual if isinstance(actual, list) else [actual]
        w_list = want if isinstance(want, list) else [want]
        if isinstance(want, list) != isinstance(actual, list) or len(a_list) != len(w_list):
            return [f"{path}: expected ≈{json.dumps(want)}, got {json.dumps(actual)[:80]}"]
        for a, w in zip(a_list, w_list):
            if not isinstance(a, (int, float)) or isinstance(a, bool) or not math.isfinite(a) or abs(a - w) > tol:
                return [f"{path}: expected ≈{json.dumps(want)} (±{tol}), got {json.dumps(actual)[:80]}"]
    if "$type" in p:
        t = p["$type"]
        ok = {
            "string": isinstance(actual, str),
            "integer": isinstance(actual, int) and not isinstance(actual, bool),
            "number": isinstance(actual, (int, float)) and not isinstance(actual, bool),
            "boolean": isinstance(actual, bool),
            "array": isinstance(actual, list),
            "object": isinstance(actual, dict),
            "null": actual is None,
        }.get(t)
        if not ok:
            errs.append(f"{path}: expected type {t}, got {json.dumps(actual)[:80]}")
    for op, cmp in (("$gte", lambda a, b: a >= b), ("$lte", lambda a, b: a <= b)):
        if op in p:
            if not isinstance(actual, (int, float)) or isinstance(actual, bool) or not cmp(actual, p[op]):
                errs.append(f"{path}: expected {op[1:]} {p[op]}, got {json.dumps(actual)[:80]}")
    if "$len" in p and (not isinstance(actual, (list, str, dict)) or len(actual) != p["$len"]):
        errs.append(f"{path}: expected length {p['$len']}, got {json.dumps(actual)[:80]}")
    if "$minlen" in p and (not isinstance(actual, (list, str, dict)) or len(actual) < p["$minlen"]):
        errs.append(f"{path}: expected length >= {p['$minlen']}, got {json.dumps(actual)[:80]}")
    if "$contains" in p:
        if not isinstance(actual, list) or not any(not match(a, p["$contains"], path, ctx) for a in actual):
            errs.append(f"{path}: no element matches {json.dumps(p['$contains'])}")
    if "$match" in p:
        if not isinstance(actual, str) or not re.search(p["$match"], actual):
            errs.append(f"{path}: does not match /{p['$match']}/: {json.dumps(actual)[:80]}")
    if "$file" in p:
        errs.extend(_match_file(actual, p["$file"] or {}, path, ctx))
    return errs


def _match_file(actual: Any, spec: dict, path: str, ctx: dict) -> list[str]:
    if not isinstance(actual, str):
        return [f"{path}: expected a file path, got {json.dumps(actual)[:80]}"]
    f = Path(actual)
    work = ctx.get("work")
    if work is not None and not _inside(f, Path(work)):
        return [f"{path}: {actual} is outside the work directory {work}"]
    if not f.is_file():
        return [f"{path}: file {actual} does not exist"]
    errs: list[str] = []
    if "png" in spec:
        want = spec["png"]
        if want == "result":
            r = ctx.get("result") or {}
            want = [r.get("w"), r.get("h")]
        size = _png_size(f)
        if size is None:
            errs.append(f"{path}: {actual} is not a PNG")
        elif list(size) != list(want):
            errs.append(f"{path}: PNG is {size[0]}x{size[1]}, expected {want[0]}x{want[1]}")
    if "size" in spec and f.stat().st_size != spec["size"]:
        errs.append(f"{path}: file has {f.stat().st_size} bytes, expected {spec['size']}")
    return errs


# --------------------------------------------------------------------------- static validation


def _known_cap(c: str) -> bool:
    return c in CAPABILITIES


def validate_cases(paths: Iterable[str | Path] | None = None) -> dict:
    """Check case files: structure, unique ids, known caps/methods, params vs schemas.

    ``call`` params must validate against ``schema/<method>.json#/$defs/params`` — except
    steps marked ``"invalid": true``, which must **fail** validation. Variables are treated as
    wildcards (built-ins get realistic placeholder values).
    """
    files = _expand_paths(paths)
    cases, errors = load_cases(files)
    schemas = S.load()
    methods = schemas.methods()
    seen: dict[str, str] = {}
    n_steps = 0
    placeholders = {"token": "0" * 64, "bad_token": "1" * 64,
                    "prefix": "<workspace>/work/run/captures/conformance/placeholder"}
    for c in cases:
        where = f"{c.file}:{c.line} {c.id}"
        if c.id in seen:
            errors.append(f"{where}: duplicate id (also in {seen[c.id]})")
        seen[c.id] = f"{c.file}:{c.line}"
        for cap in c.caps:
            if not _known_cap(cap):
                errors.append(f"{where}: unknown capability {cap!r}")
        if c.transport not in (None, "saap"):
            errors.append(f"{where}: transport must be 'saap' when given")
        if not c.steps:
            errors.append(f"{where}: no steps")
        saved: set[str] = set()
        for si, st in enumerate(c.steps):
            n_steps += 1
            sw = f"{where} step {si + 1}"
            kinds = [k for k in ("call", "send", "raw") if k in st]
            if len(kinds) != 1:
                errors.append(f"{sw}: exactly one of call/send/raw is required")
                continue
            if kinds[0] in ("send", "raw") and c.transport != "saap":
                errors.append(f"{sw}: send/raw steps need transport 'saap'")
            exp = st.get("expect") or {}
            if exp:
                if "ok" in exp and not isinstance(exp["ok"], bool):
                    errors.append(f"{sw}: expect.ok must be boolean")
                if "error" in exp and exp["error"] not in ERROR_CODES:
                    errors.append(f"{sw}: expect.error {exp['error']!r} is not a SAAP error code")
            for var in _VAR_ANY.findall(json.dumps(st.get("params", {})) + json.dumps(exp)):
                if var not in _BUILTIN_VARS and var not in saved:
                    errors.append(f"{sw}: variable ${{{var}}} is used before it is saved")
            for var, src in (st.get("save") or {}).items():
                if not isinstance(src, str) or not src.split(".")[0] in ("result", "error", "meta"):
                    errors.append(f"{sw}: save.{var} must be a dotted path starting with result/error/meta")
                saved.add(var)
            if kinds[0] == "call":
                method = st["call"]
                if method not in methods:
                    if exp.get("error") != "UNKNOWN_METHOD":
                        errors.append(f"{sw}: unknown method {method!r}")
                    continue
                cap = methods[method]
                if cap not in c.caps and not (cap == "core"):
                    errors.append(f"{sw}: method {method} needs capability {cap!r}, not listed in caps")
                params = _expand(st.get("params", {}), placeholders, missing=lambda k: S.ANY)
                perrs = schemas.validate_part(method, "params", params)
                if st.get("invalid"):
                    if not perrs:
                        errors.append(f"{sw}: params are marked invalid but validate against the schema")
                elif perrs:
                    errors.extend(f"{sw}: {e}" for e in perrs)
            elif kinds[0] == "send":
                if not isinstance(st["send"], dict):
                    errors.append(f"{sw}: send must be an object")
            else:
                raw = st["raw"]
                if not isinstance(raw, dict) or not isinstance(raw.get("length"), int) or raw["length"] < 0:
                    errors.append(f"{sw}: raw needs an integer length >= 0")
                elif "fill" in raw and (not isinstance(raw["fill"], int) or not 0 <= raw["fill"] <= RAW_FILL_MAX):
                    errors.append(f"{sw}: raw.fill must be an integer 0..{RAW_FILL_MAX}")
    return {"files": [p.name for p in files], "cases": len(cases), "steps": n_steps, "errors": errors}


# --------------------------------------------------------------------------- drivers


class _Closed(Exception):
    pass


class SaapConn:
    """One raw SAAP connection used by :class:`SaapDriver`."""

    def __init__(self, host: str, port: int, timeout: float):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.timeout = timeout
        self._n = 0

    def _read(self) -> dict:
        try:
            return F.read_json(self.sock, F.MAX_RESPONSE)
        except F.FrameError as e:
            if e.closed or e.code == "PROTOCOL":
                raise _Closed(str(e)) from None
            raise

    def send_obj(self, obj: dict) -> dict:
        try:
            self.sock.sendall(F.encode_json(obj, F.MAX_REQUEST))
        except OSError as e:
            raise _Closed(str(e)) from None
        return self._read()

    def call(self, method: str, params: dict | None) -> dict:
        self._n += 1
        env = {"saap": 1, "id": f"c{self._n}", "method": method}
        if params is not None:
            env["params"] = params
        resp = self.send_obj(env)
        if resp.get("id") != env["id"]:
            raise SatkError("PROTOCOL", f"response id {resp.get('id')!r} != request id {env['id']!r}")
        return resp

    def raw(self, length: int, body: str, fill: int = 0) -> dict:
        """Send a hand-made frame: header ``length`` + ``body`` + ``fill`` filler bytes."""
        try:
            self.sock.sendall(struct.pack("!I", length) + body.encode("utf-8") + b" " * fill)
        except OSError as e:
            raise _Closed(str(e)) from None
        return self._read()

    def is_closed(self, wait: float = 2.0) -> bool:
        self.sock.settimeout(wait)
        try:
            data = self.sock.recv(1)
            return data == b""
        except socket.timeout:
            return False
        except OSError:
            return True
        finally:
            self.sock.settimeout(self.timeout)

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class SaapDriver:
    """Runs cases against a SAAP endpoint (raw frames; transport cases included)."""

    kind = "saap"

    def __init__(self, host: str, port: int, token: str, *, timeout: float = 60.0, client: str = "satk-conformance"):
        self.host, self.port, self.token, self.timeout, self.client = host, port, token, timeout, client
        conn = self.open(True)
        try:
            self.hello = conn.call("hello", {"token": token, "client": {"name": client}}).get("result") or {}
        finally:
            conn.close()
        self.caps = list(self.hello.get("caps") or [])

    def open(self, hello: bool) -> SaapConn:
        conn = SaapConn(self.host, self.port, self.timeout)
        if hello:
            resp = conn.call("hello", {"token": self.token, "client": {"name": self.client}})
            if not resp.get("ok"):
                conn.close()
                err = resp.get("error") or {}
                raise SatkError(err.get("code", "AUTH"), f"hello failed: {err.get('message')}")
        return conn


class _BackendConn:
    def __init__(self, backend: Any):
        self.backend = backend

    def call(self, method: str, params: dict | None) -> dict:
        try:
            return {"ok": True, "result": self.backend.call(method, params or {})}
        except SatkError as e:
            return {"ok": False, "error": {"code": e.code, "message": e.msg, "data": e.data or {}}}

    def is_closed(self, wait: float = 0.0) -> bool:  # pragma: no cover - transport cases are skipped
        return False

    def close(self) -> None:
        pass


class BackendDriver:
    """Runs cases through ``backend.call(method, params)`` (adapters, in-process mock)."""

    kind = "backend"

    def __init__(self, backend: Any):
        self.backend = backend
        self.hello = backend.call("hello", {"token": "0" * 64, "client": {"name": "satk-conformance"}})
        self.caps = list(self.hello.get("caps") or [])

    def open(self, hello: bool) -> _BackendConn:
        return _BackendConn(self.backend)


# --------------------------------------------------------------------------- runner


def _fresh_prefix(case_id: str, n: int) -> str:
    from ..core import paths

    d = paths.work("run", "captures", "conformance")
    safe = re.sub(r"[^a-z0-9_.-]+", "_", case_id.lower())
    return paths.jpath(d / f"{safe}-{n}")


def _run_case(driver: Any, case: Case, token: str) -> tuple[str, str]:
    from ..core import paths

    vars_: dict[str, Any] = {"token": token, "bad_token": secrets.token_hex(32)}
    ctx = {"work": paths.cfg().paths.work}
    conn = None
    n_prefix = 0
    try:
        conn = driver.open(case.data.get("hello", True))
        for si, st in enumerate(case.steps, 1):
            if "${prefix}" in json.dumps(st):
                n_prefix += 1
                vars_["prefix"] = _fresh_prefix(case.id, n_prefix)
            exp = _expand(st.get("expect") or {}, vars_)
            method = st.get("call")
            closed = False
            try:
                if "call" in st:
                    params = _expand(st.get("params"), vars_) if "params" in st else None
                    resp = conn.call(method, params)
                elif "send" in st:
                    resp = conn.send_obj(_expand(st["send"], vars_))
                else:
                    raw = st["raw"]
                    resp = conn.raw(int(raw["length"]), str(raw.get("body", "")), int(raw.get("fill", 0)))
            except _Closed as e:
                if exp.get("disconnect") and exp.get("ok") is False and not exp.get("error"):
                    return "pass", ""
                return "fail", f"step {si}: connection closed without a response ({e})"
            ok = bool(resp.get("ok"))
            if "ok" in exp and ok != exp["ok"]:
                err = resp.get("error") or {}
                got = "ok" if ok else f"{err.get('code')}: {err.get('message')}"
                return "fail", f"step {si} ({method or 'raw'}): expected ok={exp['ok']}, got {got}"
            if "error" in exp:
                code = (resp.get("error") or {}).get("code")
                if code != exp["error"]:
                    return "fail", f"step {si}: expected error {exp['error']}, got {code}"
            if ok and method and driver.kind == "saap":
                verr = S.validate_response(resp, method if method in S.methods() else None)
                if verr:
                    return "fail", f"step {si} ({method}): response violates the schema: {verr[0]}"
            elif ok and method in S.methods():
                verr = S.validate_result(method, resp.get("result"))
                if verr:
                    return "fail", f"step {si} ({method}): result violates the schema: {verr[0]}"
            if "result" in exp:
                ctx["result"] = resp.get("result") or {}
                merr = match(resp.get("result"), exp["result"], "result", ctx)
                if merr:
                    return "fail", f"step {si} ({method}): {merr[0]}"
            if exp.get("disconnect"):
                closed = conn.is_closed()
                if not closed:
                    return "fail", f"step {si}: endpoint did not close the connection"
            for var, src in (st.get("save") or {}).items():
                try:
                    vars_[var] = _dig(resp, src)
                except KeyError:
                    return "fail", f"step {si}: cannot save {var} from {src}"
        return "pass", ""
    except SatkError as e:
        return "fail", f"{e.code}: {e.msg}"
    except (OSError, KeyError, ValueError) as e:
        return "fail", f"{type(e).__name__}: {e}"
    finally:
        if conn is not None:
            conn.close()


def run(driver: Any, cases: list[Case] | None = None, *, only: str | None = None,
        token: str | None = None) -> dict:
    """Run cases against ``driver``; returns counts and per-case results.

    Cases whose ``caps`` are not all offered by the endpoint, or that need the SAAP transport
    when the driver is not a SAAP endpoint, are skipped (with the reason).
    """
    if cases is None:
        cases, errs = load_cases()
        if errs:
            raise SatkError("BAD_PARAMS", f"conformance files are broken: {errs[0]}")
    caps = set(driver.caps)
    tok = token if token is not None else getattr(driver, "token", "0" * 64)
    results: list[list] = []
    counts = {"pass": 0, "fail": 0, "skip": 0}
    for c in cases:
        if only and not re.search(only, c.id):
            continue
        missing = [x for x in c.caps if x not in caps]
        t0 = time.perf_counter()
        if missing:
            status, detail = "skip", f"endpoint lacks {', '.join(missing)}"
        elif c.transport == "saap" and driver.kind != "saap":
            status, detail = "skip", "needs the SAAP transport"
        else:
            status, detail = _run_case(driver, c, tok)
        ms = round((time.perf_counter() - t0) * 1000, 1)
        counts[status] += 1
        results.append([c.id, ",".join(c.caps), status, ms, detail])
    ran = counts["pass"] + counts["fail"]
    return {**counts, "ran": ran, "total": len(results),
            "percent": round(100.0 * counts["pass"] / ran, 1) if ran else 0.0,
            "caps": sorted(caps), "results": results}
