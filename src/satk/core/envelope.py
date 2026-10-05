"""Response envelopes (SPEC §3.3). FROZEN contract.

Three shapes, identical for CLI ``--json``, MCP and SAAP payloads:

* table  ``{"ok":true,"cols":[...],"rows":[[...]],"n":1,"total":1,"next":null}``
  (``ok/cols/rows/n/total/next`` are always present, ``warn`` only when non-empty);
* object ``{"ok":true,"id":"inst:lae2_stream0#4","model":"model:17613",...}``
  (``None`` values and empty lists/dicts are omitted, recursively in nested dicts);
* error  ``{"ok":false,"error":{"code":"NOT_FOUND","msg":"...","hint":"...","did_you_mean":[...]}}``.

Rounding rules: coordinates 0.01, angles 0.1 degree, quaternions 1e-4
(:func:`round_pos`, :func:`round_angle`, :func:`round_quat`).
"""

from __future__ import annotations

import dataclasses
import json
import math
from pathlib import PurePath
from typing import Any, Iterable, Sequence

from .errors import SatkError

__all__ = [
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "table",
    "obj",
    "error",
    "error_from_exception",
    "compact",
    "with_warn",
    "select_fields",
    "clamp_limit",
    "round_pos",
    "round_angle",
    "round_quat",
    "dumps",
    "is_table",
]

DEFAULT_LIMIT = 20
MAX_LIMIT = 500


def table(
    cols: list[str],
    rows: list[list],
    *,
    total: int | None = None,
    next: str | None = None,  # noqa: A002 - frozen name from SPEC §3.3
    warn: Iterable[str] = (),
) -> dict:
    """Tabular list envelope.

    Args:
        cols: column names.
        rows: list of rows; each row has ``len(cols)`` cells (checked).
        total: total number of matches (defaults to ``len(rows)``).
        next: opaque cursor for the next page, ``None`` if this is the last page.
        warn: warnings such as ``"INDEX_STALE: data/gta.dat changed"``.
    """
    cols = [str(c) for c in cols]
    out_rows: list[list] = []
    for i, r in enumerate(rows):
        r = list(r)
        if len(r) != len(cols):
            raise ValueError(f"row {i} has {len(r)} cells, expected {len(cols)}")
        out_rows.append(r)
    env: dict[str, Any] = {
        "ok": True,
        "cols": cols,
        "rows": out_rows,
        "n": len(out_rows),
        "total": len(out_rows) if total is None else int(total),
        "next": next,
    }
    w = [str(x) for x in warn]
    if w:
        env["warn"] = w
    return env


def is_table(env: dict) -> bool:
    """True if ``env`` has the table shape."""
    return isinstance(env, dict) and "cols" in env and "rows" in env


def compact(value: Any) -> Any:
    """Drop ``None`` values and empty lists/tuples/dicts from dicts, recursively.

    List elements are positional and never dropped (a ``None`` cell stays ``None``),
    but dicts inside lists are compacted. ``0``, ``False`` and ``""`` are kept.
    """
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            v = compact(v)
            if v is None:
                continue
            if isinstance(v, (list, tuple, dict, set, frozenset)) and len(v) == 0:
                continue
            out[k] = v
        return out
    if isinstance(value, (list, tuple)):
        return [compact(v) for v in value]
    return value


def obj(id: str | None = None, **fields) -> dict:  # noqa: A002 - frozen name from SPEC §3.3
    """Object envelope: ``{"ok": true, "id": id, **fields}`` with empty fields omitted.

    Values can be SIDs (``str(sid)``), numbers, lists, nested dicts and ``Path`` objects
    (serialized by :func:`dumps` as absolute forward-slash paths).
    """
    env: dict[str, Any] = {"ok": True}
    if id is not None:
        env["id"] = str(id)
    env.update(compact(fields))
    return env


def error(
    code: str,
    msg: str,
    *,
    hint: str | None = None,
    did_you_mean: Iterable[str] = (),
    data: dict | None = None,
) -> dict:
    """Error envelope without raising (same shape as ``SatkError(...).to_dict()``)."""
    return SatkError(code, msg, hint=hint, did_you_mean=did_you_mean, data=data).to_dict()


def error_from_exception(exc: BaseException, *, hint: str | None = None) -> dict:
    """Envelope for any exception: ``SatkError`` keeps its code, others become ``INTERNAL``."""
    if isinstance(exc, SatkError):
        return exc.to_dict()
    return error("INTERNAL", f"{type(exc).__name__}: {exc}", hint=hint)


def with_warn(env: dict, *warnings: str) -> dict:
    """Append warnings to ``env["warn"]`` (created on demand); returns ``env``."""
    ws = [str(w) for w in warnings if w]
    if ws:
        env.setdefault("warn", []).extend(ws)
    return env


def select_fields(env: dict, fields: Sequence[str] | None) -> dict:
    """Reduce an object envelope to ``fields`` (``ok``, ``id``, ``warn`` are always kept).

    For a table envelope the columns are reduced instead. Unknown fields raise
    ``BAD_PARAMS`` listing the available ones.
    """
    if not fields:
        return env
    want = [f.strip() for f in fields if f and f.strip()]
    if is_table(env):
        cols = env["cols"]
        missing = [f for f in want if f not in cols]
        if missing:
            raise SatkError("BAD_PARAMS", f"unknown column(s): {', '.join(missing)}",
                            data={"available": list(cols)})
        idx = [cols.index(f) for f in want]
        out = dict(env)
        out["cols"] = want
        out["rows"] = [[r[i] for i in idx] for r in env["rows"]]
        return out
    keep = {"ok", "id", "warn"}
    available = [k for k in env if k not in keep]
    missing = [f for f in want if f not in env and f not in available]
    # A field may be legitimately absent (omitted because empty), so only reject
    # names that do not look like fields at all when nothing matches.
    if missing and len(missing) == len(want):
        raise SatkError("BAD_PARAMS", f"unknown field(s): {', '.join(missing)}",
                        data={"available": available})
    return {k: v for k, v in env.items() if k in keep or k in want}


def clamp_limit(limit: int | None, *, default: int = DEFAULT_LIMIT, maximum: int = MAX_LIMIT) -> int:
    """Validate a ``limit`` parameter: ``None`` -> default, 1..maximum, else ``BAD_PARAMS``."""
    if limit is None:
        return default
    try:
        n = int(limit)
    except (TypeError, ValueError):
        raise SatkError("BAD_PARAMS", f"limit must be an integer, got {limit!r}") from None
    if n < 1 or n > maximum:
        raise SatkError("BAD_PARAMS", f"limit must be in 1..{maximum}, got {n}")
    return n


def _round(x: float, nd: int) -> float:
    r = round(float(x), nd)
    if r == 0:
        return 0.0  # no "-0.0" in output
    return r


def round_pos(v: Iterable[float] | float) -> list[float] | float:
    """Round coordinates to 0.01 (a scalar or a vector)."""
    if isinstance(v, (int, float)):
        return _round(v, 2)
    return [_round(x, 2) for x in v]


def round_angle(deg: float) -> float:
    """Round an angle in degrees to 0.1."""
    return _round(deg, 1)


def round_quat(q: Iterable[float]) -> list[float]:
    """Round quaternion components ``[x, y, z, w]`` to 1e-4."""
    return [_round(x, 4) for x in q]


def _json_default(o: Any) -> Any:
    if isinstance(o, PurePath):
        from .paths import jpath

        return jpath(o)
    if isinstance(o, (set, frozenset)):
        return sorted(o, key=str)
    if isinstance(o, bytes):
        return o.hex()
    if type(o).__str__ is not object.__str__:
        return str(o)  # e.g. Sid -> "model:411" (checked before the dataclass fallback)
    if dataclasses.is_dataclass(o) and not isinstance(o, type):
        return dataclasses.asdict(o)
    raise TypeError(f"not JSON serializable: {type(o).__name__}")


def _sanitize(o: Any) -> Any:
    # JSON has no NaN/Infinity; emit null instead of invalid JSON.
    if isinstance(o, float) and not math.isfinite(o):
        return None
    if isinstance(o, dict):
        return {k: _sanitize(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_sanitize(v) for v in o]
    return o


def dumps(env: Any, *, pretty: bool = False, ascii: bool = False) -> str:  # noqa: A002
    """Serialize an envelope: compact ``separators=(",",":")`` unless ``pretty``."""
    data = _sanitize(env)
    if pretty:
        return json.dumps(data, ensure_ascii=ascii, indent=2, default=_json_default, allow_nan=False)
    return json.dumps(data, ensure_ascii=ascii, separators=(",", ":"), default=_json_default, allow_nan=False)
