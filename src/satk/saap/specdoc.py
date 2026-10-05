"""Cross-check ``proto/SAAP-v1.md`` against ``proto/schema`` (WP-07 acceptance 1).

Every method schema must have a ``### `method``` section in the document with a
``Capability:`` line naming the schema's capability, a ``Params`` and a ``Result``
paragraph and a ``json`` example whose ``→`` request and ``←`` response validate against
the envelope and method schemas; every documented method must have a schema.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import schema as S

__all__ = ["MethodDoc", "parse", "check"]

_HEAD = re.compile(r"^###\s+`([a-z][a-z0-9_.]*)`\s*$")
_CAP = re.compile(r"^Capability:\s*`([a-z0-9_.]+)`")
_FENCE = re.compile(r"^```(\w*)\s*$")


@dataclass
class MethodDoc:
    method: str
    line: int
    capability: str | None = None
    has_params: bool = False
    has_result: bool = False
    requests: list[tuple[int, str]] = field(default_factory=list)   # (line, json text)
    responses: list[tuple[int, str]] = field(default_factory=list)


def parse(text: str) -> dict[str, MethodDoc]:
    """Method sections of the document (``{method: MethodDoc}``)."""
    out: dict[str, MethodDoc] = {}
    cur: MethodDoc | None = None
    in_fence = False
    fence_lang = ""
    for i, line in enumerate(text.splitlines(), 1):
        m = _FENCE.match(line.strip())
        if m:
            if in_fence:
                in_fence = False
            else:
                in_fence, fence_lang = True, m.group(1)
            continue
        if in_fence:
            if cur is not None and fence_lang == "json":
                s = line.strip()
                if s.startswith("→"):
                    cur.requests.append((i, s[1:].strip()))
                elif s.startswith("←"):
                    cur.responses.append((i, s[1:].strip()))
            continue
        h = _HEAD.match(line)
        if h:
            cur = MethodDoc(h.group(1), i)
            out[cur.method] = cur
            continue
        if line.startswith("## "):
            cur = None
            continue
        if cur is None:
            continue
        c = _CAP.match(line.strip())
        if c and cur.capability is None:
            cur.capability = c.group(1)
        if re.match(r"^Params\b", line.strip()):
            cur.has_params = True
        if re.match(r"^Result\b", line.strip()):
            cur.has_result = True
    return out


def check(path: Path | None = None, schemas: S.Schemas | None = None) -> dict:
    """``{"methods": n, "examples": k, "errors": [...]}`` for the spec document."""
    path = Path(path) if path else S.PROTO_DIR / "SAAP-v1.md"
    schemas = schemas or S.load()
    text = path.read_text(encoding="utf-8")
    docs = parse(text)
    known = schemas.methods()
    errors: list[str] = []
    examples = 0
    rel = path.name
    for m, cap in known.items():
        d = docs.get(m)
        if d is None:
            errors.append(f"{rel}: no section for method {m!r}")
            continue
        where = f"{rel}:{d.line} {m}"
        if d.capability != cap:
            errors.append(f"{where}: capability {d.capability!r}, schema says {cap!r}")
        if not d.has_params:
            errors.append(f"{where}: no Params paragraph")
        if not d.has_result:
            errors.append(f"{where}: no Result paragraph")
        if not d.requests or not d.responses:
            errors.append(f"{where}: no example (→ request and ← response in a json block)")
        ids = {}
        for line, txt in d.requests:
            examples += 1
            try:
                req = json.loads(txt)
            except json.JSONDecodeError as e:
                errors.append(f"{rel}:{line}: example request is not JSON: {e}")
                continue
            if req.get("method") != m:
                errors.append(f"{rel}:{line}: example request calls {req.get('method')!r}, section is {m!r}")
            for e in S.validate_request(req):
                errors.append(f"{rel}:{line}: {e}")
            ids[req.get("id")] = req.get("method")
        for line, txt in d.responses:
            examples += 1
            try:
                resp = json.loads(txt)
            except json.JSONDecodeError as e:
                errors.append(f"{rel}:{line}: example response is not JSON: {e}")
                continue
            method = ids.get(resp.get("id"), m)
            for e in S.validate_response(resp, method):
                errors.append(f"{rel}:{line}: {e}")
    for m, d in docs.items():
        if m not in known:
            errors.append(f"{rel}:{d.line}: section for {m!r} but no schema/{m}.json")
    return {"methods": len(known), "documented": len(docs), "examples": examples, "errors": errors}
