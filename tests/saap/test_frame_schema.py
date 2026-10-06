"""SAAP/1 framing, schema loading, the stdlib validator (cross-checked with jsonschema) and the spec doc."""

from __future__ import annotations

import copy
import json
import socket
import struct

import pytest

from satk.core.errors import SatkError
from satk.saap import frame as F
from satk.saap import schema as S
from satk.saap import specdoc
from satk.saap.conformance import load_cases

# --------------------------------------------------------------------------- frame


def test_encode_decode_roundtrip():
    obj = {"saap": 1, "id": "r1", "method": "ping", "params": {"x": "ü"}}
    data = F.encode_json(obj, F.MAX_REQUEST)
    (n,) = struct.unpack("!I", data[:4])
    assert n == len(data) - 4
    assert F.decode_json(data[4:]) == obj


def test_encode_limits():
    with pytest.raises(F.FrameError) as e:
        F.encode(b"", F.MAX_REQUEST)
    assert e.value.code == "PROTOCOL"
    with pytest.raises(F.FrameError):
        F.encode(b"x" * (F.MAX_REQUEST + 1), F.MAX_REQUEST)
    assert len(F.encode(b"x" * F.MAX_REQUEST, F.MAX_REQUEST)) == F.MAX_REQUEST + 4
    assert F.MAX_REQUEST == 1048576 and F.MAX_RESPONSE == 8388608


@pytest.mark.parametrize("payload", [b"\xff\xfe", b"{bad}", b"[1,2]", b"42"])
def test_decode_rejects(payload):
    with pytest.raises(F.FrameError) as e:
        F.decode_json(payload)
    assert e.value.code == "PROTOCOL"


@pytest.mark.parametrize("number", [b"NaN", b"Infinity", b"-Infinity", b"1e9999", b"-1e9999"])
def test_decode_rejects_nonfinite_numbers(number):
    with pytest.raises(F.FrameError) as e:
        F.decode_json(b'{"x":' + number + b'}')
    assert e.value.code == "PROTOCOL"


@pytest.mark.parametrize("value", [
    b"1" * 5000,
    b"[" * 10000 + b"0" + b"]" * 10000,
    br'"\ud800"',
], ids=["integer", "depth", "surrogate"])
def test_decode_malformed_values_are_frame_errors(value):
    with pytest.raises(F.FrameError) as e:
        F.decode_json(b'{"x":' + value + b'}')
    assert e.value.code == "PROTOCOL"


def test_decode_preserves_valid_unicode_and_quoted_brackets():
    obj = {"text": '[{\\\"' * 100, "unicode": "😀", "number": -12345678901234567890}
    assert F.decode_json(json.dumps(obj).encode("utf-8")) == obj


def test_decode_nesting_limit():
    def payload(depth):
        return b'{"x":' + b'[' * (depth - 1) + b'0' + b']' * (depth - 1) + b'}'

    assert F.decode_json(payload(F.MAX_JSON_DEPTH))["x"]
    with pytest.raises(F.FrameError, match="nesting"):
        F.decode_json(payload(F.MAX_JSON_DEPTH + 1))


@pytest.mark.parametrize("sign", [b"", b"-"])
def test_decode_integer_size_limit(sign):
    at_limit = sign + b"9" * F.MAX_INTEGER_DIGITS
    assert F.decode_json(b'{"x":' + at_limit + b'}')["x"] == int(at_limit)
    with pytest.raises(F.FrameError, match="integer"):
        F.decode_json(b'{"x":' + at_limit + b'9}')


def test_read_frame_socketpair():
    a, b = socket.socketpair()
    try:
        F.write_json(a, {"saap": 1, "id": "x", "method": "ping"}, F.MAX_REQUEST)
        assert F.read_json(b, F.MAX_REQUEST)["id"] == "x"
        a.sendall(struct.pack("!I", F.MAX_REQUEST + 1))
        with pytest.raises(F.FrameError) as e:
            F.read_frame(b, F.MAX_REQUEST)
        assert e.value.length == F.MAX_REQUEST + 1
        a.sendall(struct.pack("!I", 0))
        with pytest.raises(F.FrameError):
            F.read_frame(b, F.MAX_REQUEST)
        a.close()
        with pytest.raises(F.FrameError) as e:
            F.read_frame(b, F.MAX_REQUEST)
        assert e.value.closed
    finally:
        b.close()


def test_tokens():
    assert F.token_ok("a" * 32) and F.token_ok("a" * 256)
    assert not F.token_ok("a" * 31) and not F.token_ok("a" * 257)
    assert not F.token_ok("a" * 40 + "\n") and not F.token_ok(None) and not F.token_ok(123)
    assert F.token_equal("a" * 64, "a" * 64)
    assert not F.token_equal("a" * 64, "a" * 63 + "b")


@pytest.mark.parametrize("token", ["\ud800" * 32, "a" * 32 + "\udfff"])
def test_invalid_unicode_token_is_rejected(token):
    assert not F.token_ok(token)


# --------------------------------------------------------------------------- schemas


def test_methods_and_capabilities():
    m = S.methods()
    assert len(m) == 30  # 23 core methods + 7 scene.*
    assert m["capture"] == "capture" and m["raycast"] == "pick" and m["hello"] == "core"
    assert m["world.settle"] == "world.settle" and m["mem.read"] == "mem.read"
    for method in m:
        d = S.load().method_doc(method)
        assert set(d["$defs"]) == {"params", "result"}, method
        assert d["$id"].endswith(f"/{method}.json")


def test_index_json_matches_schemas():
    idx = json.loads((S.SCHEMA_DIR / "index.json").read_text(encoding="utf-8"))
    assert idx["methods"] == S.methods()


def test_pose_one_of():
    ok = {"pose": {"pos": [0, 0, 10], "look": [0, 10, 0]}}
    assert S.validate_params("camera.set", ok) == []
    assert S.validate_params("camera.set", {"pose": {"pos": [0, 0, 10], "ypr": [0, -10, 0], "fov_h_deg": 70}}) == []
    both = {"pose": {"pos": [0, 0, 10], "look": [0, 1, 0], "ypr": [0, 0, 0]}}
    assert S.validate_params("camera.set", both)
    assert S.validate_params("camera.set", {"pose": {"pos": [0, 0]}})
    assert S.validate_params("camera.set", {"pose": {"pos": [0, 0, 1], "look": [0, 1, 0], "fov_h_deg": 180}})


def test_wildcard_and_types():
    assert S.validate_params("entity.inspect", {"ref": S.ANY}) == []
    assert S.validate_params("mem.read", {"addr": "0x400000", "len": 2}) == []
    assert S.validate_params("mem.read", {"addr": 4194304, "len": 2}) == []
    assert S.validate_params("mem.read", {"addr": "400000", "len": 2})
    assert S.validate_params("mem.read", {"addr": 1, "len": True})  # booleans are not integers
    assert S.validate_params("world.settle", {"max_frames": 2.0}) == []  # 2.0 is an integer in JSON Schema
    with pytest.raises(KeyError):
        S.validate_params("no.such.method", {})


def _jsonschema():
    js = pytest.importorskip("jsonschema")
    referencing = pytest.importorskip("referencing")
    from referencing.jsonschema import DRAFT202012

    docs = S.load().docs
    reg = referencing.Registry().with_resources(
        [(d["$id"], referencing.Resource.from_contents(d, default_specification=DRAFT202012)) for d in docs.values()
         if "$id" in d])
    return js, reg, docs


def _corpus() -> list[tuple[str, str, object]]:
    """(method, part, instance) — valid and invalid instances from the doc, the cases and mutations."""
    out: list[tuple[str, str, object]] = []
    docs = specdoc.parse((S.PROTO_DIR / "SAAP-v1.md").read_text(encoding="utf-8"))
    for m, d in docs.items():
        for _, txt in d.requests:
            out.append((m, "params", json.loads(txt).get("params", {})))
        for _, txt in d.responses:
            r = json.loads(txt)
            if r.get("ok"):
                out.append((m, "result", r["result"]))
    cases, _ = load_cases()
    for c in cases:
        for st in c.steps:
            if "call" in st and st["call"] in S.methods():
                p = json.loads(json.dumps(st.get("params", {})).replace('"${rev}"', "3").replace('"${ref}"', '"r1"')
                               .replace('"${next}"', '"1"').replace('"${token}"', json.dumps("0" * 64))
                               .replace('"${bad_token}"', json.dumps("1" * 64))
                               .replace('"${prefix}"', '"D:/ws/work/run/x"'))
                out.append((st["call"], "params", p))
    # mutations: drop each required field / break types of the first example of each method
    extra = []
    for m, part, inst in out:
        if isinstance(inst, dict):
            for k in list(inst):
                j = copy.deepcopy(inst)
                del j[k]
                extra.append((m, part, j))
                j2 = copy.deepcopy(inst)
                j2[k] = {"bogus": [1, 2]} if not isinstance(inst[k], dict) else "str"
                extra.append((m, part, j2))
    return out + extra


def test_validator_agrees_with_jsonschema():
    js, reg, docs = _jsonschema()
    n = disagree = 0
    for method, part, inst in _corpus():
        doc = docs[f"{method}.json"]
        v = js.Draft202012Validator({"$ref": f"{doc['$id']}#/$defs/{part}"}, registry=reg)
        theirs = v.is_valid(inst)
        ours = not S.load().validate_part(method, part, inst)
        n += 1
        if theirs != ours:
            disagree += 1
            print("DISAGREE", method, part, json.dumps(inst)[:200], "jsonschema:", theirs, "satk:", ours)
    assert n > 300
    assert disagree == 0


def test_schemas_are_valid_2020_12():
    js, _reg, docs = _jsonschema()
    for name, d in docs.items():
        if "$schema" in d:
            js.Draft202012Validator.check_schema(d)


def test_envelopes():
    assert S.validate_request({"saap": 1, "id": "r1", "method": "ping"}) == []
    assert S.validate_request({"saap": 2, "id": "r1", "method": "ping"})
    assert S.validate_request({"saap": 1, "id": "r1", "method": "nope"})
    ok = {"saap": 1, "id": "r1", "ok": True, "result": {"t_ms": 1.5}}
    assert S.validate_response(ok, "ping") == []
    assert S.validate_response({"saap": 1, "id": "r1", "ok": True}, "ping")  # missing result
    err = {"saap": 1, "id": "r1", "ok": False, "error": {"code": "AUTH", "message": "no"}}
    assert S.validate_response(err) == []
    err["error"]["code"] = "BAD_ID"  # satk code, not a SAAP code
    assert S.validate_response(err)


# --------------------------------------------------------------------------- spec document


def test_spec_document_matches_schemas():
    r = specdoc.check()
    assert r["errors"] == []
    assert r["methods"] == r["documented"] == 30
    assert r["examples"] >= 60


def test_spec_document_check_finds_problems(tmp_path):
    text = (S.PROTO_DIR / "SAAP-v1.md").read_text(encoding="utf-8")
    broken = text.replace("Capability: `camera` (`stream:\"settle\"` also needs `world.settle`)", "Capability: `view`")
    broken = broken.replace('"method":"ping","params":{}}', '"method":"ping","params":[]}')
    start = broken.index("### `quit`")
    end = broken.index("### `camera.get`")
    broken = broken[:start] + broken[end:]
    p = tmp_path / "SAAP-v1.md"
    p.write_text(broken, encoding="utf-8")
    errs = specdoc.check(p)["errors"]
    assert any("no section for method 'quit'" in e for e in errs)
    assert any("camera.set" in e and "capability" in e for e in errs)
    assert any("params" in e for e in errs)


def test_schema_errors_report_paths():
    errs = S.validate_params("capture", {"path_prefix": "D:/x/y", "w": 8, "layers": []})
    text = " ".join(errs)
    assert "params.w" in text and "params.layers" in text


def test_protocol_constants_cover_schema_caps():
    from satk.saap.protocol import CAPABILITIES

    assert set(S.methods().values()) <= set(CAPABILITIES)
    with pytest.raises(SatkError):
        raise SatkError("PROTOCOL", "x")
