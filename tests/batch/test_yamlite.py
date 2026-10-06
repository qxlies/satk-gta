"""The YAML subset of recipe files (satk.batch.yamlite)."""

from __future__ import annotations

import json

import pytest

from satk.batch.yamlite import YamlError, loads
from satk.core.errors import SatkError

DOC = """\
---
# a recipe-like document
name: demo   # trailing comment
summary: "Inspect a mod: lint # not a comment"
vars:
  mod: {required: true, help: 'mod folder, .zip or .img'}
  profile: vanilla
  n: 0x1F
  f: 1.5
  neg: -3
  e: ~
  t: true
  s: yes
steps:
  - id: inspect
    op: mod.inspect
    args:
      path: ${mod}
      limit: 20
  - id: lint
    op: [texture.audit, txd.audit]
    on_error: continue
    args: {target: ${mod}, rule: [dff.parse, "txd.*"],
           sev: error}
  -
    id: bare
  - - a
    - b
text: |
  line one
    indented
  line three

folded: >-
  a
  b

  c
list:
- 1
- two
esc: "tab\\t A=\\u0041 back\\\\slash"
single: 'it''s'
url: http://x.y/z
win: C:/Games/x
empty:
"""


def test_document():
    d = loads(DOC)
    assert d["name"] == "demo"
    assert d["summary"] == "Inspect a mod: lint # not a comment"
    assert d["vars"] == {"mod": {"required": True, "help": "mod folder, .zip or .img"}, "profile": "vanilla",
                         "n": 31, "f": 1.5, "neg": -3, "e": None, "t": True, "s": "yes"}
    assert d["steps"][0] == {"id": "inspect", "op": "mod.inspect", "args": {"path": "${mod}", "limit": 20}}
    assert d["steps"][1] == {"id": "lint", "op": ["texture.audit", "txd.audit"], "on_error": "continue",
                             "args": {"target": "${mod}", "rule": ["dff.parse", "txd.*"], "sev": "error"}}
    assert d["steps"][2] == {"id": "bare"} and d["steps"][3] == ["a", "b"]
    assert d["text"] == "line one\n  indented\nline three\n"
    assert d["folded"] == "a b\nc"
    assert d["list"] == [1, "two"]
    assert d["esc"] == "tab\t A=A back\\slash"
    assert d["single"] == "it's" and d["url"] == "http://x.y/z" and d["win"] == "C:/Games/x"
    assert d["empty"] is None


@pytest.mark.parametrize("text,value", [
    ("[1, 2, {a: b}]", [1, 2, {"a": "b"}]),
    ("- a\n- b: 1\n  c: 2\n- [x]\n", ["a", {"b": 1, "c": 2}, ["x"]]),
    ("a:\n- 1\n- 2\nb: 3\n", {"a": [1, 2], "b": 3}),
    ("\ufeffa: 1\r\nb: 2\r\n", {"a": 1, "b": 2}),
    ("a: {k: [1, [2, 3]], 'q x': \"v\"}", {"a": {"k": [1, [2, 3]], "q x": "v"}}),
    ("a: [\n  1,\n  2\n]\n", {"a": [1, 2]}),
    ("a: |-\n  x\n  y\n", {"a": "x\ny"}),
    ("a: |+\n  x\n\nb: 1\n", {"a": "x\n\n", "b": 1}),
    ("- |\n  block in a list\n- z\n", ["block in a list\n", "z"]),
    ("", None),
    ("# only a comment\n", None),
    ("a: 1_000\nb: .5\nc: 1e3\nd: 0o17\ne: '007'\n", {"a": 1000, "b": 0.5, "c": 1000.0, "d": 15, "e": "007"}),
    ("a: x#y\nb: \"#\"\n", {"a": "x#y", "b": "#"}),
])
def test_values(text, value):
    assert loads(text) == value


def test_matches_json_for_the_same_data():
    data = {"name": "x", "vars": {"a": {"default": None, "required": False}}, "steps": [
        {"id": "s1", "op": "formats.dump", "args": {"level": "stats", "limit": 5}},
        {"id": "s2", "op": ["a.b", "c.d"], "when": "${steps.s1.ok}"}]}
    text = """
name: x
vars:
  a: {default: null, required: false}
steps:
  - {id: s1, op: formats.dump, args: {level: stats, limit: 5}}
  - id: s2
    op: [a.b, c.d]
    when: "${steps.s1.ok}"
"""
    assert loads(text) == json.loads(json.dumps(data))


@pytest.mark.parametrize("text,line,words", [
    ("a: 1\na: 2\n", 2, "duplicate key"),
    ("a:\n\tb: 1\n", 2, "tab"),
    ("a: &x 1\n", 1, "anchors"),
    ("a: *x\n", 1, "anchors"),
    ("a: !!str 1\n", 1, "tags"),
    ("a: b\n  c\n", 2, "continues"),
    ("a: [1, 2\n", 1, "unclosed"),
    ("a: \"\\q\"\n", 1, "unknown escape"),
    ("a: 'x\n", 1, "single-quoted"),
    ("a: 1\n---\nb: 2\n", 2, "several documents"),
    ("a:\n  b: 1\n c: 2\n", 3, "indentation"),
    ("- a\nb: 1\n", 2, "after the document"),
    ("a: {b: 1, b: 2}\n", 1, "duplicate key"),
    ("? complex\n", 1, ""),
])
def test_errors_name_the_line(text, line, words):
    with pytest.raises(SatkError) as ei:
        loads(text, source="r.yaml")
    e = ei.value
    assert isinstance(e, YamlError) and e.code == "BAD_PARAMS"
    assert e.msg.startswith(f"r.yaml:{line}:"), e.msg
    assert words in e.msg
    assert "YAML subset" in (e.hint or "")
