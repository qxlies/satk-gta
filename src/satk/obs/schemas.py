"""The schema files of ``data/obs`` and validation against them (read through :mod:`satk.core.resources`)."""

from __future__ import annotations

import difflib
from typing import Any

from ..core import resources
from ..core.errors import SatkError
from .jschema import Issue, Validator

__all__ = ["FILES", "STREAM_SCHEMA", "BENCH_SCHEMA", "LEGACY_BENCH_SCHEMA", "RECORD_KINDS", "load", "registry", "describe",
           "check_record", "check_bench", "check_document", "layout"]

STREAM_SCHEMA = "sae-obs/1"
BENCH_SCHEMA = "sae-bench/1"
LEGACY_BENCH_SCHEMA = "satk-bench/1"
#: name -> (file in data/obs, $id)
FILES: dict[str, tuple[str, str]] = {
    "common": ("sae-obs-common.schema.json", "sae-obs-common.schema.json"),
    "record": ("sae-obs-record.schema.json", "sae-obs-record.schema.json"),
    "bench": ("sae-bench.schema.json", "sae-bench.schema.json"),
}
RECORD_KINDS = ("hdr", "frame", "tick", "stats", "event", "net", "input", "seed", "snap", "end")
_CACHE: dict[str, Any] = {}


def load(name: str) -> dict[str, Any]:
    """A schema document by short name (``common``, ``record``, ``bench``); the container layout is :func:`layout`."""
    if name not in FILES:
        raise SatkError("NOT_FOUND", f"no schema {name!r}", hint="one of: " + ", ".join([*FILES, "layout"]),
                        did_you_mean=difflib.get_close_matches(name, [*FILES, "layout"], n=3, cutoff=0.5))
    doc = _CACHE.get(name)
    if doc is None:
        doc = resources.read_json("obs", FILES[name][0])
        _CACHE[name] = doc
    return doc


def layout() -> dict[str, Any]:
    """The machine-readable container layout (``data/obs/sae-container-layout.json``)."""
    doc = _CACHE.get("layout")
    if doc is None:
        doc = resources.read_json("obs", "sae-container-layout.json")
        _CACHE["layout"] = doc
    return doc


def registry() -> dict[str, dict[str, Any]]:
    """``{$id: schema}`` of every schema document."""
    return {FILES[n][1]: load(n) for n in FILES}


def describe() -> list[dict[str, Any]]:
    """One row per schema document: name, id, file, title, definitions."""
    rows = []
    for n, (fname, sid) in FILES.items():
        d = load(n)
        rows.append({"name": n, "id": sid, "file": fname, "title": d.get("title", ""), "defs": sorted(d.get("$defs") or {})})
    rows.append({"name": "layout", "id": "sae-container-layout", "file": "sae-container-layout.json",
                 "title": layout().get("title", ""), "defs": []})
    return rows


def _validator(strict: bool) -> Validator:
    key = f"validator-{strict}"
    v = _CACHE.get(key)
    if v is None:
        v = Validator(registry(), strict=strict, max_issues=25)
        _CACHE[key] = v
    return v


def check_record(rec: Any, *, strict: bool = False) -> list[Issue]:
    """Issues of one stream record, validated against the definition of its kind (``rec``)."""
    if not isinstance(rec, dict):
        return [Issue("$", "type", "a record must be a JSON object")]
    kind = rec.get("rec")
    if kind not in RECORD_KINDS:
        return [Issue("rec", "enum", f"{kind!r} is not a record kind (one of {', '.join(RECORD_KINDS)})")]
    return _validator(strict).validate(rec, f"sae-obs-record.schema.json#/$defs/{kind}", "sae-obs-record.schema.json")


def check_bench(doc: Any, *, strict: bool = False) -> list[Issue]:
    """Issues of a ``sae-bench/1`` document against the bench schema."""
    return _validator(strict).validate(doc, load("bench"), "sae-bench.schema.json")


def check_document(doc: Any, name: str, *, strict: bool = False) -> list[Issue]:
    """Validate against the root of schema ``name`` (the full record dispatch for ``record``)."""
    load(name)
    return _validator(strict).validate(doc, load(name), FILES[name][1])
