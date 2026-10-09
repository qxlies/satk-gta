"""The JSON Schema subset validator of satk.obs (no game, stdlib only; the ``jsonschema`` package is used as an oracle when installed)."""

from __future__ import annotations

import json

import pytest

from satk.obs import schemas
from satk.obs.jschema import Validator


def check(schema, inst, registry=None, **kw):
    reg = dict(registry or {})
    if isinstance(schema, dict):
        schema = dict(schema)
        schema.setdefault("$id", "t.json")
        reg[schema["$id"]] = schema
    return [i.text() for i in Validator(reg, **kw).validate(inst, schema)]


@pytest.mark.parametrize("schema,good,bad", [
    ({"type": "integer"}, [1, 0, -5, 3.0], [1.5, "1", None, True, [1]]),
    ({"type": "number"}, [1, 2.5, 0], ["1", None, True]),
    ({"type": ["string", "null"]}, ["a", None], [1, True]),
    ({"type": "boolean"}, [True, False], [0, 1, "true"]),
    ({"enum": ["a", 1]}, ["a", 1], ["b", True, 2]),
    ({"enum": [0, 1]}, [0, 1], [False, True]),
    ({"const": 5}, [5, 5.0], [6, "5"]),
    ({"minimum": 0, "maximum": 10}, [0, 10, 5.5], [-1, 11]),
    ({"exclusiveMinimum": 0}, [0.1], [0]),
    ({"exclusiveMaximum": 1}, [0.9], [1]),
    ({"type": "string", "minLength": 2, "maxLength": 3}, ["ab", "abc"], ["a", "abcd"]),
    ({"type": "string", "pattern": "^a+$"}, ["aa"], ["ab", ""]),
    ({"type": "array", "minItems": 1, "maxItems": 2}, [[1], [1, 2]], [[], [1, 2, 3]]),
    ({"type": "array", "items": {"type": "integer"}}, [[], [1, 2]], [[1, "x"]]),
    ({"type": "array", "uniqueItems": True}, [[1, 2], [1, True]], [[1, 1], [{"a": 1}, {"a": 1}]]),
    ({"type": "array", "prefixItems": [{"type": "string"}, {"type": "integer"}]}, [["a", 1], ["a"]], [[1, 1]]),
    ({"type": "object", "required": ["a"]}, [{"a": 1}], [{}, {"b": 1}]),
    ({"type": "object", "properties": {"a": {"type": "integer"}}}, [{"a": 1}, {"b": "x"}], [{"a": "x"}]),
    ({"type": "object", "additionalProperties": False, "properties": {"a": {}}}, [{"a": 1}, {}], [{"b": 1}]),
    ({"type": "object", "additionalProperties": {"type": "integer"}}, [{"x": 1}], [{"x": "s"}]),
    ({"type": "object", "patternProperties": {"^x_": {"type": "integer"}}}, [{"x_a": 1, "y": "s"}], [{"x_a": "s"}]),
    ({"type": "object", "propertyNames": {"pattern": "^[a-z]+$"}}, [{"ab": 1}], [{"AB": 1}]),
    ({"type": "object", "minProperties": 1}, [{"a": 1}], [{}]),
    ({"allOf": [{"type": "integer"}, {"minimum": 3}]}, [3, 9], [2, "x"]),
    ({"anyOf": [{"type": "integer"}, {"type": "string"}]}, [1, "a"], [None, 1.5]),
    ({"oneOf": [{"type": "integer"}, {"minimum": 0}]}, [-1, 1.5, "x"], [1, 0]),
    ({"not": {"type": "string"}}, [1], ["a"]),
    ({"if": {"properties": {"k": {"const": 1}}, "required": ["k"]}, "then": {"required": ["a"]}, "else": {"required": ["b"]}},
     [{"k": 1, "a": 0}, {"k": 2, "b": 0}], [{"k": 1}, {"k": 2}, {}]),
    (True, [1, None], []),
    (False, [], [1]),
])
def test_keywords(schema, good, bad):
    for g in good:
        assert check(schema, g) == [], (schema, g)
    for b in bad:
        assert check(schema, b), (schema, b)


def test_messages_name_the_path():
    sch = {"type": "object", "properties": {"a": {"type": "object", "properties": {"b": {"type": "array", "items": {"type": "integer"}}}}}}
    assert check(sch, {"a": {"b": [1, "x"]}}) == ["a.b[1]: expected integer, got string"]
    assert check({"type": "integer"}, "x") == ["$: expected integer, got string"]
    assert check({"type": "object", "required": ["z"]}, {}) == ["$: missing 'z'"]


def test_refs_and_registry():
    common = {"$id": "c.json", "$defs": {"pos": {"type": "integer", "minimum": 1}}}
    sch = {"$id": "m.json", "type": "object", "properties": {"a": {"$ref": "c.json#/$defs/pos"}, "b": {"$ref": "#/$defs/local"}},
           "$defs": {"local": {"type": "string"}}}
    reg = {"c.json": common}
    assert check(sch, {"a": 1, "b": "x"}, reg) == []
    assert check(sch, {"a": 0, "b": 5}, reg) == ["a: 0 is below the minimum 1", "b: expected string, got integer"]
    assert any("unknown schema document" in m for m in check({"$ref": "nope.json#/x"}, 1))
    assert any("unresolvable" in m for m in check({"$ref": "#/$defs/missing"}, 1))
    # a $ref with siblings keeps both
    assert check({"$defs": {"i": {"type": "integer"}}, "$ref": "#/$defs/i", "minimum": 5}, 3) == ["$: 3 is below the minimum 5"]


def test_alternatives_report_the_closest_branch():
    sch = {"oneOf": [{"type": "object", "required": ["a"], "properties": {"a": {"type": "integer"}}},
                     {"type": "object", "required": ["b", "c"]}]}
    msgs = check(sch, {"a": "x"})
    assert len(msgs) == 1 and "closest" in msgs[0] and "a: expected integer" in msgs[0]
    assert "exactly one" in check({"oneOf": [{"type": "integer"}, {"type": "number"}]}, 3)[0]


def test_issue_limit_and_depth():
    v = Validator({}, max_issues=3)
    issues = v.validate(list(range(20)), {"type": "array", "items": {"type": "string"}})
    assert len(issues) == 3
    deep: dict = {"type": "integer"}
    for _ in range(60):
        deep = {"allOf": [deep]}
    assert any("deep" in m for m in check(deep, 1))


def test_strict_mode_rejects_unknown_members_only_where_the_schema_is_closed():
    sch = {"type": "object", "properties": {"a": {"type": "object", "properties": {"x": {}}}, "m": {"type": "object", "additionalProperties": True}}}
    inst = {"a": {"x": 1, "y": 2}, "b": 3, "m": {"anything": 1}}
    assert check(sch, inst) == []
    msgs = check(sch, inst, strict=True)
    assert sorted(msgs) == ["a.y: unknown property (strict mode)", "b: unknown property (strict mode)"]
    # allOf contributes declared members; the matching 'then' too
    sch2 = {"type": "object", "allOf": [{"properties": {"a": {}}}, {"if": {"required": ["k"]}, "then": {"properties": {"k": {}, "z": {}}}}],
            "properties": {"b": {}}}
    assert check(sch2, {"a": 1, "b": 2, "k": 1, "z": 3}, strict=True) == []
    assert check(sch2, {"a": 1, "q": 2}, strict=True) == ["q: unknown property (strict mode)"]
    sch3 = {"type": "object", "properties": {"l": {"type": "array", "items": {"type": "object", "properties": {"i": {}}}}}}
    assert check(sch3, {"l": [{"i": 1}, {"j": 2}]}, strict=True) == ["l[1].j: unknown property (strict mode)"]


def test_the_repository_schemas_are_valid_json_schema_documents():
    js = pytest.importorskip("jsonschema")
    for name in schemas.FILES:
        doc = schemas.load(name)
        js.Draft202012Validator.check_schema(doc)


def _oracle(name, ref):
    js = pytest.importorskip("jsonschema")
    pytest.importorskip("referencing")
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012

    reg = Registry().with_resources([(sid, Resource.from_contents(doc, default_specification=DRAFT202012)) for sid, doc in schemas.registry().items()])
    return js.Draft202012Validator({"$ref": ref}, registry=reg)


def test_agrees_with_the_jsonschema_package_on_a_corpus():
    from satk.obs import sample

    oracle = _oracle("record", "sae-obs-record.schema.json")
    good = [r for r in sample.client_stream(seconds=1.2) + sample.server_stream(seconds=0.3) + sample.net_stream(rows=12)]
    bad = []
    for r in good[:40]:
        for field, value in (("t_us", -1), ("frame", 2 ** 32), ("rec", "zzz"), ("node", "bad node"), ("frame_ms", "x"), ("ticks", 99),
                             ("fence_ms", {"Q": 1}), ("counts", 3), ("dir", "up"), ("tick_ms", -1)):
            if field in r:
                bad.append({**r, field: value})
        bad.append({k: v for k, v in r.items() if k != "t_us"} if r["rec"] != "hdr" else {k: v for k, v in r.items() if k != "node"})
    for rec in good:
        assert not list(oracle.iter_errors(rec)), rec
        assert schemas.check_record(rec) == [], rec
    for rec in bad:
        mine = bool(schemas.check_document(rec, "record"))
        theirs = bool(list(oracle.iter_errors(rec)))
        assert mine == theirs == True, rec  # noqa: E712 - both must reject
    # the bench schema as well
    from satk.obs import bench

    doc = bench.doc_from_records(sample.client_stream(seconds=2.5), run="r")
    boracle = _oracle("bench", "sae-bench.schema.json")
    assert not list(boracle.iter_errors(doc)) and schemas.check_bench(doc) == []
    broken = json.loads(json.dumps(doc))
    broken["stages"][0]["frames"]["q"] = [1]
    broken["schema"] = "x"
    assert list(boracle.iter_errors(broken)) and schemas.check_bench(broken)
