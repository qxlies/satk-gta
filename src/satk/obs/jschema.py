"""A small JSON Schema validator (the subset the ``data/obs`` schemas use). Stdlib only.

The schema files are standard JSON Schema 2020-12 documents, so any external validator can check them. satk does not
depend on one (the ``jsonschema`` package is not a satk requirement), so this module implements what the files use:
``$ref`` (``#/$defs/x`` and ``<id>#/$defs/x`` into a registry), ``type``, ``enum``, ``const``, ``minimum``,
``maximum``, ``exclusiveMinimum``, ``exclusiveMaximum``, ``minLength``, ``maxLength``, ``pattern``, ``minItems``,
``maxItems``, ``items``, ``prefixItems``, ``uniqueItems``, ``required``, ``properties``, ``patternProperties``,
``additionalProperties``, ``propertyNames``, ``minProperties``, ``allOf``, ``anyOf``, ``oneOf``, ``not``,
``if``/``then``/``else``. ``format``, ``title``, ``description`` and ``examples`` are ignored.

Strict mode: an object whose schema declares ``properties`` and no ``additionalProperties`` rejects keys that no
applicable subschema (``allOf``, ``$ref``, the matching ``then``) declares. Non-strict mode ignores unknown keys, which
is what keeps a version-1 reader compatible with a later additive minor version.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping

__all__ = ["Issue", "Validator", "MAX_DEPTH"]

MAX_DEPTH = 40


@dataclass(frozen=True)
class Issue:
    """One violation: ``path`` is dotted (``frames.q[3]``, ``$`` = the root), ``keyword`` the schema keyword."""

    path: str
    keyword: str
    msg: str

    def text(self) -> str:
        return f"{self.path}: {self.msg}"


@lru_cache(maxsize=512)
def _rx(pattern: str) -> re.Pattern:
    return re.compile(pattern)


_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and v not in (float("inf"), float("-inf")),
    "integer": lambda v: (isinstance(v, int) and not isinstance(v, bool)) or (isinstance(v, float) and v.is_integer()),
}


def _kind(v: Any) -> str:
    if isinstance(v, bool):
        return "boolean"
    if v is None:
        return "null"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, float):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "array"
    return "object" if isinstance(v, dict) else type(v).__name__


class _Stop(Exception):
    pass


class Validator:
    """Validates instances against schemas of a registry ``{$id: schema}``."""

    def __init__(self, registry: Mapping[str, dict] | None = None, *, strict: bool = False, max_issues: int = 50):
        self.registry = dict(registry or {})
        self.strict = strict
        self.max_issues = max_issues

    # ------------------------------------------------------------------ public

    def validate(self, instance: Any, schema: dict | str, base: str | None = None) -> list[Issue]:
        """Issues of ``instance`` against ``schema`` (a dict, or a ``$id#/$defs/name`` reference string)."""
        out: list[Issue] = []
        if isinstance(schema, str):
            schema = {"$ref": schema}
        ctx = _Ctx(out, base or (schema.get("$id") if isinstance(schema, dict) else "") or "", self.max_issues)
        try:
            self._check(instance, schema, "$", ctx, 0)
            if self.strict:
                self._unknown(instance, schema, "$", ctx, 0)
        except _Stop:
            pass
        return out

    # ------------------------------------------------------------------ references

    def _resolve(self, ref: str, base: str) -> tuple[dict, str]:
        doc_id, _, frag = ref.partition("#")
        if doc_id:
            doc = self.registry.get(doc_id)
            if doc is None:
                raise KeyError(f"unknown schema document {doc_id!r}")
            base = doc_id
        else:
            doc = self.registry.get(base)
            if doc is None:
                raise KeyError(f"no base document for reference {ref!r}")
        node: Any = doc
        for part in [p for p in frag.split("/") if p]:
            part = part.replace("~1", "/").replace("~0", "~")
            if not isinstance(node, dict) or part not in node:
                raise KeyError(f"unresolvable reference {ref!r}")
            node = node[part]
        return node, base

    # ------------------------------------------------------------------ core

    def _check(self, inst: Any, schema: Any, path: str, ctx: "_Ctx", depth: int) -> set[str]:
        """Check ``inst``; returns the property names this schema (with its applicators) declares."""
        if schema is True or schema is None:
            return set()
        if schema is False:
            self._add(ctx, path, "false", "no value is allowed here")
            return set()
        if depth > MAX_DEPTH:
            self._add(ctx, path, "depth", "nesting is too deep")
            return set()
        declared: set[str] = set()
        base = ctx.base
        ref = schema.get("$ref")
        if ref is not None:
            try:
                target, new_base = self._resolve(ref, base)
            except KeyError as e:
                self._add(ctx, path, "$ref", str(e.args[0]))
                return declared
            ctx.base = new_base
            try:
                declared |= self._check(inst, target, path, ctx, depth + 1)
            finally:
                ctx.base = base
        t = schema.get("type")
        if t is not None:
            names = t if isinstance(t, list) else [t]
            if not any(_TYPES[n](inst) for n in names):
                self._add(ctx, path, "type", f"expected {'/'.join(names)}, got {_kind(inst)}")
                return declared
        if "const" in schema and not _same(inst, schema["const"]):
            self._add(ctx, path, "const", f"must be {schema['const']!r}")
        if "enum" in schema and not any(_same(inst, e) for e in schema["enum"]):
            self._add(ctx, path, "enum", f"{_short(inst)} is not one of {', '.join(map(repr, schema['enum']))}")
        if isinstance(inst, (int, float)) and not isinstance(inst, bool):
            self._numbers(inst, schema, path, ctx)
        elif isinstance(inst, str):
            self._strings(inst, schema, path, ctx)
        elif isinstance(inst, list):
            self._arrays(inst, schema, path, ctx, depth)
        elif isinstance(inst, dict):
            declared |= self._objects(inst, schema, path, ctx, depth)
        for sub in schema.get("allOf", ()):
            declared |= self._check(inst, sub, path, ctx, depth + 1)
        if "anyOf" in schema:
            declared |= self._alternatives(inst, schema["anyOf"], path, ctx, depth, "anyOf", exactly_one=False)
        if "oneOf" in schema:
            declared |= self._alternatives(inst, schema["oneOf"], path, ctx, depth, "oneOf", exactly_one=True)
        if "not" in schema and self._probe(inst, schema["not"], ctx, depth):
            self._add(ctx, path, "not", "matches a schema it must not match")
        if "if" in schema:
            if self._probe(inst, schema["if"], ctx, depth):
                if "then" in schema:
                    declared |= self._check(inst, schema["then"], path, ctx, depth + 1)
            elif "else" in schema:
                declared |= self._check(inst, schema["else"], path, ctx, depth + 1)
        return declared

    def _probe(self, inst: Any, schema: Any, ctx: "_Ctx", depth: int) -> bool:
        sub = _Ctx([], ctx.base, 1)
        try:
            self._check(inst, schema, "$", sub, depth + 1)
        except _Stop:
            pass
        return not sub.out

    def _alternatives(self, inst, alts, path, ctx, depth, kw, *, exactly_one) -> set[str]:
        results = []
        for alt in alts:
            sub = _Ctx([], ctx.base, self.max_issues)
            try:
                declared = self._check(inst, alt, path, sub, depth + 1)
            except _Stop:
                declared = set()
            results.append((sub.out, declared))
        ok = [r for r in results if not r[0]]
        if exactly_one and len(ok) == 1 or (not exactly_one and ok):
            return ok[0][1]
        if exactly_one and len(ok) > 1:
            self._add(ctx, path, kw, f"matches {len(ok)} alternatives, exactly one is allowed")
            return set()
        best = min(results, key=lambda r: len(r[0]))
        self._add(ctx, path, kw, f"matches none of the {len(alts)} alternatives; the closest fails with: "
                  + "; ".join(i.text() for i in best[0][:3]))
        return set()

    def _numbers(self, v, schema, path, ctx) -> None:
        if "minimum" in schema and v < schema["minimum"]:
            self._add(ctx, path, "minimum", f"{v} is below the minimum {schema['minimum']}")
        if "maximum" in schema and v > schema["maximum"]:
            self._add(ctx, path, "maximum", f"{v} is above the maximum {schema['maximum']}")
        if "exclusiveMinimum" in schema and v <= schema["exclusiveMinimum"]:
            self._add(ctx, path, "exclusiveMinimum", f"{v} must be above {schema['exclusiveMinimum']}")
        if "exclusiveMaximum" in schema and v >= schema["exclusiveMaximum"]:
            self._add(ctx, path, "exclusiveMaximum", f"{v} must be below {schema['exclusiveMaximum']}")

    def _strings(self, v, schema, path, ctx) -> None:
        if "minLength" in schema and len(v) < schema["minLength"]:
            self._add(ctx, path, "minLength", f"shorter than {schema['minLength']} characters")
        if "maxLength" in schema and len(v) > schema["maxLength"]:
            self._add(ctx, path, "maxLength", f"longer than {schema['maxLength']} characters")
        if "pattern" in schema and not _rx(schema["pattern"]).search(v):
            self._add(ctx, path, "pattern", f"{_short(v)} does not match {schema['pattern']}")

    def _arrays(self, v, schema, path, ctx, depth) -> None:
        if "minItems" in schema and len(v) < schema["minItems"]:
            self._add(ctx, path, "minItems", f"{len(v)} items, at least {schema['minItems']} required")
        if "maxItems" in schema and len(v) > schema["maxItems"]:
            self._add(ctx, path, "maxItems", f"{len(v)} items, at most {schema['maxItems']} allowed")
        prefix = schema.get("prefixItems") or []
        for i, sub in enumerate(prefix[:len(v)]):
            self._check(v[i], sub, f"{path}[{i}]", ctx, depth + 1)
        if "items" in schema:
            for i in range(len(prefix), len(v)):
                self._check(v[i], schema["items"], f"{path}[{i}]", ctx, depth + 1)
        if schema.get("uniqueItems"):
            seen: list[Any] = []
            for i, x in enumerate(v):
                if any(_same(x, y) for y in seen):
                    self._add(ctx, f"{path}[{i}]", "uniqueItems", "duplicate item")
                    break
                seen.append(x)

    def _objects(self, v, schema, path, ctx, depth) -> set[str]:
        declared: set[str] = set()
        props = schema.get("properties") or {}
        for name in schema.get("required", ()):
            if name not in v:
                self._add(ctx, path, "required", f"missing {name!r}")
        if "minProperties" in schema and len(v) < schema["minProperties"]:
            self._add(ctx, path, "minProperties", f"fewer than {schema['minProperties']} properties")
        pnames = schema.get("propertyNames")
        patterns = schema.get("patternProperties") or {}
        extra = schema.get("additionalProperties")
        for key, val in v.items():
            sub = f"{path}.{key}" if path != "$" else key
            if pnames is not None and self._probe(key, pnames, ctx, depth) is False:
                self._add(ctx, sub, "propertyNames", f"the name {key!r} is not allowed")
            matched = False
            if key in props:
                matched = True
                self._check(val, props[key], sub, ctx, depth + 1)
            for pat, psub in patterns.items():
                if _rx(pat).search(key):
                    matched = True
                    declared.add(key)
                    self._check(val, psub, sub, ctx, depth + 1)
            if not matched and extra is not None:
                declared.add(key)
                if extra is False:
                    self._add(ctx, sub, "additionalProperties", "unknown property")
                elif extra is not True:
                    self._check(val, extra, sub, ctx, depth + 1)
        declared |= set(props)
        if extra is not None:
            declared |= set(v)
        return declared

    def _add(self, ctx: "_Ctx", path: str, keyword: str, msg: str) -> None:
        ctx.out.append(Issue(path, keyword, msg))
        if len(ctx.out) >= ctx.limit:
            raise _Stop

    # ------------------------------------------------------------------ strictness

    def _unknown(self, inst: Any, schema: Any, path: str, ctx: "_Ctx", depth: int) -> None:
        """Walk instance and schema together and flag keys that no applicable subschema declares."""
        if not isinstance(schema, dict) or depth > MAX_DEPTH:
            return
        base = ctx.base
        merged = self._flatten(inst, schema, ctx, depth)
        ctx.base = base
        if isinstance(inst, dict):
            props = merged["properties"]
            for key in inst:
                if merged["closed"] and key not in props and not any(_rx(p).search(key) for p in merged["patterns"]):
                    self._add(ctx, f"{path}.{key}" if path != "$" else key, "unknown", "unknown property (strict mode)")
            for key, val in inst.items():
                sub = f"{path}.{key}" if path != "$" else key
                if key in props:
                    self._unknown(val, props[key], sub, ctx, depth + 1)
                else:
                    for pat, psub in merged["patterns"].items():
                        if _rx(pat).search(key):
                            self._unknown(val, psub, sub, ctx, depth + 1)
                    extra = merged["extra"]
                    if isinstance(extra, dict):
                        self._unknown(val, extra, sub, ctx, depth + 1)
        elif isinstance(inst, list):
            items = merged["items"]
            if isinstance(items, dict):
                for i, x in enumerate(inst):
                    self._unknown(x, items, f"{path}[{i}]", ctx, depth + 1)

    def _flatten(self, inst, schema, ctx, depth) -> dict:
        out = {"properties": {}, "patterns": {}, "extra": None, "closed": False, "items": None}
        has_extra = False

        def walk(s: Any, d: int) -> None:
            nonlocal has_extra
            if not isinstance(s, dict) or d > MAX_DEPTH:
                return
            if "$ref" in s:
                try:
                    target, nb = self._resolve(s["$ref"], ctx.base)
                except KeyError:
                    target, nb = None, ctx.base
                old = ctx.base
                ctx.base = nb
                walk(target, d + 1)
                ctx.base = old
            for k, val in (s.get("properties") or {}).items():
                out["properties"].setdefault(k, val)
            out["patterns"].update(s.get("patternProperties") or {})
            if "additionalProperties" in s:
                has_extra = True
                if isinstance(s["additionalProperties"], dict):
                    out["extra"] = s["additionalProperties"]
            if "items" in s and out["items"] is None:
                out["items"] = s["items"]
            for sub in s.get("allOf", ()):
                walk(sub, d + 1)
            if "if" in s:
                if self._probe(inst, s["if"], ctx, d):
                    walk(s.get("then"), d + 1)
                else:
                    walk(s.get("else"), d + 1)
            for kw in ("anyOf", "oneOf"):
                for alt in s.get(kw, ()):
                    if self._probe(inst, alt, ctx, d):
                        walk(alt, d + 1)
                        break

        walk(schema, depth)
        out["closed"] = bool(out["properties"]) and not has_extra
        return out


class _Ctx:
    __slots__ = ("out", "base", "limit")

    def __init__(self, out: list[Issue], base: str, limit: int):
        self.out = out
        self.base = base
        self.limit = limit


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    return a == b


def _short(v: Any, n: int = 60) -> str:
    s = repr(v)
    return s if len(s) <= n else s[:n - 3] + "..."
