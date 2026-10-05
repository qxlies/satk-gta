"""SAAP/1 JSON Schemas: loading and a small stdlib-only validator (SPEC §4.8.9).

The schemas live in ``tools/proto/schema/`` (one file per method with ``$defs.params`` and
``$defs.result``, shared types in ``common.json``, envelopes in ``envelope.json``). They are
JSON Schema 2020-12; this module implements the subset they use, so validation also works
from Blender's Python and without ``jsonschema`` (the tests cross-check with it when present):

``type`` (incl. lists), ``properties``, ``required``, ``additionalProperties``, ``items``,
``minItems``/``maxItems``, ``minProperties``, ``enum``, ``const``, ``minimum``/``maximum``,
``exclusiveMinimum``/``exclusiveMaximum``, ``minLength``/``maxLength``, ``pattern``,
``oneOf``/``anyOf``/``allOf``/``not`` and ``$ref`` (``#/...`` and ``<file>.json#/...``).

Example::

    from satk.saap import schema
    errs = schema.validate_params("camera.set", {"pose": {"pos": [0, 0, 0]}})
    # ['params.pose: must match exactly one of 2 alternatives (matched 0)']
"""

from __future__ import annotations

import json
import math
import re
import threading
from pathlib import Path
from typing import Any

from ..core.config import REPO_ROOT

__all__ = [
    "SCHEMA_DIR",
    "PROTO_DIR",
    "Schemas",
    "load",
    "methods",
    "capability_of",
    "validate",
    "validate_params",
    "validate_result",
    "validate_request",
    "validate_response",
    "ANY",
]

PROTO_DIR: Path = REPO_ROOT / "proto"
SCHEMA_DIR: Path = PROTO_DIR / "schema"

_TYPES = ("null", "boolean", "object", "array", "number", "integer", "string")


class _Wildcard:
    """A value that satisfies every schema (stands for an unresolved ``${var}`` in checks)."""

    def __repr__(self) -> str:  # pragma: no cover
        return "ANY"


#: Wildcard value accepted by every schema node (see :class:`_Wildcard`).
ANY = _Wildcard()


def _type_ok(v: Any, t: str) -> bool:
    if t == "null":
        return v is None
    if t == "boolean":
        return isinstance(v, bool)
    if t == "object":
        return isinstance(v, dict)
    if t == "array":
        return isinstance(v, list)
    if t == "string":
        return isinstance(v, str)
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool) and (
            not isinstance(v, float) or math.isfinite(v))
    if t == "integer":
        if isinstance(v, bool):
            return False
        if isinstance(v, int):
            return True
        return isinstance(v, float) and math.isfinite(v) and v.is_integer()
    raise ValueError(f"unknown JSON type {t!r}")


def _type_name(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, dict):
        return "object"
    if isinstance(v, list):
        return "array"
    if isinstance(v, str):
        return "string"
    if isinstance(v, (int, float)):
        return "number"
    return type(v).__name__


def _json_equal(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_json_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_json_equal(a[k], b[k]) for k in a)
    return type(a) is type(b) and a == b


class Schemas:
    """A set of schema documents keyed by file name (``"common.json"``)."""

    def __init__(self, docs: dict[str, dict], directory: Path | None = None):
        self.docs = docs
        self.directory = directory
        self._re_cache: dict[str, re.Pattern] = {}

    # -- loading ---------------------------------------------------------------------------

    @classmethod
    def from_dir(cls, directory: Path) -> "Schemas":
        docs: dict[str, dict] = {}
        for p in sorted(Path(directory).glob("*.json")):
            docs[p.name] = json.loads(p.read_text(encoding="utf-8"))
        if not docs:
            raise FileNotFoundError(f"no SAAP schemas in {directory}")
        return cls(docs, Path(directory))

    def methods(self) -> dict[str, str]:
        """``{method: capability}`` for every method schema file."""
        out = {}
        for name, d in self.docs.items():
            x = d.get("x-saap")
            if isinstance(x, dict) and "method" in x:
                out[x["method"]] = x.get("capability", "core")
        return dict(sorted(out.items()))

    def method_doc(self, method: str) -> dict:
        d = self.docs.get(f"{method}.json")
        if d is None or "x-saap" not in d:
            raise KeyError(method)
        return d

    # -- references ------------------------------------------------------------------------

    def resolve(self, ref: str, base: str) -> tuple[dict, str]:
        """Resolve ``$ref`` relative to document ``base``; returns (schema, its document)."""
        file, _, frag = ref.partition("#")
        doc_name = file.rsplit("/", 1)[-1] if file else base
        doc = self.docs.get(doc_name)
        if doc is None:
            raise KeyError(f"unresolvable $ref {ref!r} (no document {doc_name!r})")
        node: Any = doc
        for part in [p for p in frag.split("/") if p]:
            part = part.replace("~1", "/").replace("~0", "~")
            if not isinstance(node, dict) or part not in node:
                raise KeyError(f"unresolvable $ref {ref!r}")
            node = node[part]
        return node, doc_name

    # -- validation ------------------------------------------------------------------------

    def _pattern(self, p: str) -> re.Pattern:
        r = self._re_cache.get(p)
        if r is None:
            r = self._re_cache[p] = re.compile(p)
        return r

    def errors(self, value: Any, schema: Any, path: str = "$", doc: str = "", depth: int = 0) -> list[str]:
        """All validation errors of ``value`` against ``schema`` (empty list = valid)."""
        if depth > 64:
            return [f"{path}: schema recursion too deep"]
        if value is ANY:
            return []
        if schema is True or schema == {}:
            return []
        if schema is False:
            return [f"{path}: no value allowed here"]
        if not isinstance(schema, dict):
            return [f"{path}: bad schema node {schema!r}"]
        errs: list[str] = []
        if "$ref" in schema:
            target, tdoc = self.resolve(schema["$ref"], doc)
            errs.extend(self.errors(value, target, path, tdoc, depth + 1))
        t = schema.get("type")
        if t is not None:
            ts = t if isinstance(t, list) else [t]
            if not any(_type_ok(value, x) for x in ts):
                return errs + [f"{path}: expected {' or '.join(ts)}, got {_type_name(value)}"]
        if "const" in schema and not _json_equal(value, schema["const"]):
            errs.append(f"{path}: must be {json.dumps(schema['const'])}")
        if "enum" in schema and not any(_json_equal(value, e) for e in schema["enum"]):
            errs.append(f"{path}: {json.dumps(value)} is not one of {json.dumps(schema['enum'])}")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in schema and value < schema["minimum"]:
                errs.append(f"{path}: {value} < minimum {schema['minimum']}")
            if "maximum" in schema and value > schema["maximum"]:
                errs.append(f"{path}: {value} > maximum {schema['maximum']}")
            if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
                errs.append(f"{path}: {value} <= exclusiveMinimum {schema['exclusiveMinimum']}")
            if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
                errs.append(f"{path}: {value} >= exclusiveMaximum {schema['exclusiveMaximum']}")
        if isinstance(value, str):
            n = len(value)
            if "minLength" in schema and n < schema["minLength"]:
                errs.append(f"{path}: shorter than {schema['minLength']} characters")
            if "maxLength" in schema and n > schema["maxLength"]:
                errs.append(f"{path}: longer than {schema['maxLength']} characters")
            if "pattern" in schema and not self._pattern(schema["pattern"]).search(value):
                errs.append(f"{path}: does not match /{schema['pattern']}/")
        if isinstance(value, list):
            if "minItems" in schema and len(value) < schema["minItems"]:
                errs.append(f"{path}: fewer than {schema['minItems']} items")
            if "maxItems" in schema and len(value) > schema["maxItems"]:
                errs.append(f"{path}: more than {schema['maxItems']} items")
            if "items" in schema:
                for i, item in enumerate(value):
                    errs.extend(self.errors(item, schema["items"], f"{path}[{i}]", doc, depth + 1))
        if isinstance(value, dict):
            props = schema.get("properties") or {}
            for k in schema.get("required") or ():
                if k not in value:
                    errs.append(f"{path}: missing required field {k!r}")
            if "minProperties" in schema and len(value) < schema["minProperties"]:
                errs.append(f"{path}: needs at least {schema['minProperties']} field(s)")
            for k, v in value.items():
                if k in props:
                    errs.extend(self.errors(v, props[k], f"{path}.{k}", doc, depth + 1))
                elif "additionalProperties" in schema:
                    errs.extend(self.errors(v, schema["additionalProperties"], f"{path}.{k}", doc, depth + 1))
        for sub in schema.get("allOf") or ():
            errs.extend(self.errors(value, sub, path, doc, depth + 1))
        if "anyOf" in schema:
            if not any(not self.errors(value, s, path, doc, depth + 1) for s in schema["anyOf"]):
                errs.append(f"{path}: must match at least one of {len(schema['anyOf'])} alternatives")
        if "oneOf" in schema:
            results = [self.errors(value, s, path, doc, depth + 1) for s in schema["oneOf"]]
            matched = sum(1 for r in results if not r)
            if matched != 1:
                detail = ""
                if matched == 0 and results:
                    best = min(results, key=len)
                    detail = f"; closest: {best[0]}" if best else ""
                errs.append(f"{path}: must match exactly one of {len(results)} alternatives "
                            f"(matched {matched}){detail}")
        if "not" in schema and not self.errors(value, schema["not"], path, doc, depth + 1):
            errs.append(f"{path}: must not match the 'not' schema")
        return errs

    def validate_part(self, method: str, part: str, value: Any) -> list[str]:
        """Errors of ``value`` against ``<method>.json#/$defs/<part>`` (``params``/``result``)."""
        doc = self.method_doc(method)
        node = doc["$defs"][part]
        return self.errors(value, node, part, f"{method}.json")


_lock = threading.Lock()
_cached: Schemas | None = None


def load(directory: Path | None = None) -> Schemas:
    """The schemas of ``tools/proto/schema`` (cached) or of ``directory``."""
    global _cached
    if directory is not None:
        return Schemas.from_dir(directory)
    with _lock:
        if _cached is None:
            _cached = Schemas.from_dir(SCHEMA_DIR)
        return _cached


def methods() -> dict[str, str]:
    """``{method: capability}`` of SAAP/1."""
    return load().methods()


def capability_of(method: str) -> str | None:
    return methods().get(method)


def validate(value: Any, schema: dict, doc: str = "common.json") -> list[str]:
    """Validate against an inline schema whose ``$ref`` s point into the SAAP documents."""
    return load().errors(value, schema, "$", doc)


def validate_params(method: str, params: Any) -> list[str]:
    """Errors of request params (``{}`` when omitted); ``KeyError`` for unknown methods."""
    return load().validate_part(method, "params", {} if params is None else params)


def validate_result(method: str, result: Any) -> list[str]:
    return load().validate_part(method, "result", result)


def validate_request(env: Any) -> list[str]:
    """Envelope + params errors of a request object."""
    s = load()
    errs = s.errors(env, s.docs["envelope.json"]["$defs"]["request"], "request", "envelope.json")
    if errs or not isinstance(env, dict):
        return errs
    try:
        return s.validate_part(env["method"], "params", env.get("params", {}))
    except KeyError:
        return [f"request.method: unknown method {env.get('method')!r}"]


def validate_response(env: Any, method: str | None = None) -> list[str]:
    """Envelope errors of a response (+ result errors when ``method`` is known)."""
    s = load()
    errs = s.errors(env, s.docs["envelope.json"]["$defs"]["response"], "response", "envelope.json")
    if errs or not method or not isinstance(env, dict) or not env.get("ok"):
        return errs
    return s.validate_part(method, "result", env.get("result"))
