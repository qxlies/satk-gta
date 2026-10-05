"""CLI/MCP parity (WP-06 acceptance 2): every tool's ``inputSchema`` is the CLI argument schema of the same op.

Both are generated from one ``OpSpec``; these tests pin that the argparse parser built for the CLI
accepts exactly the schema's properties, with the same required set and the same value coercion.
"""

from __future__ import annotations

import argparse

import pytest

from satk.core import registry as R
from satk.core.cli import build_parser, parse_args
from satk.mcp import adapter as A

_JSON_TYPE = {"str": "string", "path": "string", "int": "integer", "float": "number", "bool": "boolean",
              "dict": "object", "list": "array"}


def _ops():
    return [o for o in R.all_ops()]


def _mcp_ops():
    return A.mcp_ops(None)


def _dests(parser: argparse.ArgumentParser) -> dict[str, argparse.Action]:
    return {a.dest: a for a in parser._actions}  # noqa: SLF001 - introspection in tests


@pytest.mark.parametrize("spec", _mcp_ops(), ids=lambda o: A.tool_name(o))
def test_input_schema_is_registry_schema(spec):
    tool = A.tool_def(spec)
    assert tool["inputSchema"] == spec.input_schema()
    assert tool["inputSchema"]["type"] == "object" and tool["inputSchema"]["additionalProperties"] is False


@pytest.mark.parametrize("spec", _ops(), ids=lambda o: o.name)
def test_cli_parser_matches_schema(spec):
    schema = spec.input_schema()
    props = schema["properties"]
    dests = _dests(build_parser(spec))
    assert set(props) == set(dests), spec.name
    required = set(schema.get("required", []))
    for p in spec.params:
        prop = props[p.name]
        act = dests[p.name]
        if p.kind == "enum":
            assert prop["enum"] == list(p.choices)
        else:
            assert prop["type"] == _JSON_TYPE[p.kind], (spec.name, p.name)
        if p.name in required:
            assert not act.option_strings and act.nargs in (None, "+"), (spec.name, p.name)
        if act.option_strings:
            assert act.option_strings[0] == "--" + p.name.replace("_", "-")
        if p.has_default and p.default is not None and "default" in prop:
            assert prop["default"] == (list(p.default) if isinstance(p.default, tuple) else p.default)


def _sample(p) -> tuple[object, list[str]] | None:
    """(JSON value, CLI tokens) that must coerce to the same Python value."""
    flag = [] if p.positional else [p.flag]
    if p.kind == "enum":
        v = p.choices[-1]
        return v, flag + [str(v)]
    if p.kind == "str":
        return "model:411", flag + ["model:411"]
    if p.kind == "int":
        return 7, flag + ["7"]
    if p.kind == "float":
        return 2.5, flag + ["2.5"]
    if p.kind == "bool":
        return True, [p.flag]
    if p.kind == "list" and p.item == "str":
        return ["a", "b"], flag + ["a,b"] if not p.positional else ["a", "b"]
    if p.kind == "dict":
        return {"k": 1}, flag + ['{"k":1}']
    return None


@pytest.mark.parametrize("spec", _mcp_ops(), ids=lambda o: A.tool_name(o))
def test_same_values_through_cli_and_json(spec):
    """One parameter at a time through argv and through JSON must bind to the same value.

    Positionals fill their slots in signature order, so an optional positional is given together
    with every positional before it (``index diff A B``: ``b`` alone would land in ``a``). The
    parameter's own flag comes first on purpose: a list option before the required positionals
    must not swallow them (``asset get --fields name model:411``).
    """
    order = {q.name: i for i, q in enumerate(spec.params)}
    for p in spec.params:
        s = _sample(p)
        if s is None:
            continue
        given = {p.name} | {q.name for q in spec.params if q.required}
        if p.positional:
            given |= {q.name for q in spec.params if q.positional and order[q.name] < order[p.name]}
        json_args: dict = {}
        own: list[str] = []
        flags: list[str] = []
        positionals: list[str] = []
        for q in spec.params:
            if q.name not in given:
                continue
            qj, qt = _sample(q)
            json_args[q.name] = qj
            (positionals if q.positional else own if q is p else flags).extend(qt)
        cli_args = parse_args(spec, own + positionals + flags)
        assert spec.bind(cli_args)[p.name] == spec.bind(json_args)[p.name], (spec.name, p.name)


@pytest.mark.parametrize("op_name,argv,expected", [
    ("asset.get", ["--fields", "name", "model:411"], {"id": "model:411", "fields": ["name"]}),
    ("asset.get", ["model:411", "--fields", "name", "links"], {"id": "model:411", "fields": ["name", "links"]}),
    ("index.query", ["--params", "411", "SELECT ?"], {"sql": "SELECT ?", "params": ["411"]}),
    ("note.add", ["--tags", "a", "b", "model:411", "x"], {"sid": "model:411", "text": "x", "tags": ["a", "b"]}),
    ("note.add", ["model:411", "--tags", "a", "b", "x", "--lang", "en"],
     {"sid": "model:411", "text": "x", "tags": ["a", "b"], "lang": "en"}),
    ("note.add", ["model:411", "x", "--tags", "a", "b"], {"sid": "model:411", "text": "x", "tags": ["a", "b"]}),
])
def test_list_option_leaves_required_positionals(op_name, argv, expected):
    """A multi-value list flag before the required positionals gives them their tokens back."""
    spec = R.get_op(op_name)
    assert parse_args(spec, argv) == expected


def test_list_option_errors_still_name_the_missing_positional():
    from satk.core.errors import SatkError

    with pytest.raises(SatkError) as e:
        parse_args(R.get_op("note.add"), ["--tags", "a", "b"])  # one value goes to sid, text is still missing
    assert e.value.code == "BAD_PARAMS" and "text" in e.value.msg
    with pytest.raises(SatkError):
        parse_args(R.get_op("asset.get"), ["--fields", "name"])  # a lone value stays with its option
