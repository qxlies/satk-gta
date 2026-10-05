"""Response envelopes, errors and exit codes (SPEC §3.3)."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from satk.core import envelope as E
from satk.core.errors import (
    ERROR_CODES,
    EXIT_ERROR,
    EXIT_NOT_READY,
    EXIT_OK,
    EXIT_USAGE,
    SatkError,
    exit_code_for,
    require_module,
)
from satk.core.ids import Sid

# The three literal examples from SPEC §3.3; our output must match them byte for byte.
SPEC_TABLE = '{"ok":true,"cols":["id","name","w","h","fmt"],"rows":[["tex:bistro/vent_64","vent_64",64,64,"X8R8G8B8"]],"n":1,"total":1,"next":null}'
SPEC_OBJ = '{"ok":true,"id":"inst:lae2_stream0#4","model":"model:17613","name":"lae2_roads89","pos":[2489.3,-1668.5,12.3],"rz":0.0,"area":0,"lod":"inst:lae2#198","layer":"vanilla"}'
SPEC_ERR = '{"ok":false,"error":{"code":"NOT_FOUND","msg":"no model \'infernos\'","hint":"satk asset find infernos --kind model","did_you_mean":["model:411"]}}'


def test_table_matches_spec_example():
    env = E.table(["id", "name", "w", "h", "fmt"], [["tex:bistro/vent_64", "vent_64", 64, 64, "X8R8G8B8"]])
    assert E.dumps(env) == SPEC_TABLE


def test_obj_matches_spec_example():
    env = E.obj(
        Sid.parse("inst:lae2_stream0#4"), model=Sid.parse("model:17613"), name="lae2_roads89",
        pos=E.round_pos([2489.301, -1668.4999, 12.3]), rz=E.round_angle(-0.01), area=0,
        lod="inst:lae2#198", layer="vanilla", q=None, tags=[],
    )
    assert E.dumps(env) == SPEC_OBJ


def test_error_matches_spec_example():
    e = SatkError("NOT_FOUND", "no model 'infernos'", hint="satk asset find infernos --kind model",
                  did_you_mean=["model:411"])
    assert E.dumps(e.to_dict()) == SPEC_ERR
    assert E.dumps(E.error("NOT_FOUND", "no model 'infernos'", hint="satk asset find infernos --kind model",
                           did_you_mean=["model:411"])) == SPEC_ERR


def test_table_paging_and_warn():
    env = E.table(["a"], [[1], [2]], total=10, next="c2", warn=["INDEX_STALE: data/gta.dat changed"])
    assert (env["n"], env["total"], env["next"]) == (2, 10, "c2")
    assert env["warn"] == ["INDEX_STALE: data/gta.dat changed"]
    assert "warn" not in E.table(["a"], [])
    assert E.is_table(env) and not E.is_table(E.obj("x"))


def test_table_rejects_ragged_rows():
    with pytest.raises(ValueError):
        E.table(["a", "b"], [[1]])


def test_obj_omits_empty_fields_recursively():
    env = E.obj("model:1", a=None, b=[], c={}, d=0, e=False, f="", g={"x": None, "y": 1, "z": {"w": []}},
                h=[None, {"k": None}])
    assert env == {"ok": True, "id": "model:1", "d": 0, "e": False, "f": "", "g": {"y": 1}, "h": [None, {}]}


def test_with_warn_and_select_fields():
    env = E.with_warn(E.obj("model:411", name="infernus", txd="txd:infernus", tris=1200), "W1")
    assert env["warn"] == ["W1"]
    small = E.select_fields(env, ["name"])
    assert small == {"ok": True, "id": "model:411", "name": "infernus", "warn": ["W1"]}
    with pytest.raises(SatkError) as ei:
        E.select_fields(env, ["nope"])
    assert ei.value.code == "BAD_PARAMS"
    t = E.select_fields(E.table(["id", "name", "w"], [["a", "b", 1]]), ["w", "id"])
    assert t["cols"] == ["w", "id"] and t["rows"] == [[1, "a"]]
    with pytest.raises(SatkError):
        E.select_fields(E.table(["id"], []), ["zz"])


def test_rounding_rules():
    assert E.round_pos([1.234, -0.004, 2.006]) == [1.23, 0.0, 2.01]
    assert E.round_pos(-0.001) == 0.0 and math.copysign(1, E.round_pos(-0.001)) == 1.0
    assert E.round_angle(359.96) == 360.0 and E.round_angle(12.34) == 12.3
    assert E.round_quat([0.123456, -0.00001, 0.5, 0.86602]) == [0.1235, 0.0, 0.5, 0.866]


def test_dumps_special_values(tmp_path):
    env = E.obj("x", p=tmp_path / "a" / "b.png", n=float("nan"), s={3, 1}, b=b"\x01\xff", sid=Sid("txd", "vehicle"),
                ru="Красная машина")
    d = json.loads(E.dumps(env))
    assert d["p"].endswith("/a/b.png") and "\\" not in d["p"] and d["p"][1] == ":"
    assert d["n"] is None and d["s"] == [1, 3] and d["b"] == "01ff" and d["sid"] == "txd:vehicle"
    assert "Красная" in E.dumps(env)
    assert "\\u041a" in E.dumps(env, ascii=True)
    assert E.dumps({"a": 1}) == '{"a":1}'
    assert "\n" in E.dumps({"a": 1}, pretty=True)


def test_clamp_limit():
    assert E.clamp_limit(None) == E.DEFAULT_LIMIT == 20
    assert E.clamp_limit(500) == E.MAX_LIMIT == 500
    for bad in (0, 501, "x"):
        with pytest.raises(SatkError) as ei:
            E.clamp_limit(bad)
        assert ei.value.code == "BAD_PARAMS"


SPEC_CODES = """BAD_ID BAD_PARAMS NOT_FOUND AMBIGUOUS NOT_READY INDEX_MISSING READ_ONLY PROTECTED_PATH EXISTS
TIMEOUT UNSUPPORTED DEPENDENCY EXTERNAL_TOOL CONSENT_REQUIRED AUTH PROTOCOL UNKNOWN_METHOD BUSY REVISION
INTERNAL""".split()


def test_error_codes_cover_spec():
    assert set(SPEC_CODES) <= ERROR_CODES
    assert ERROR_CODES - set(SPEC_CODES) == {"ASSET_GUARD", "CHECK_FAILED"}  # additive: assetguard, dev gate


@pytest.mark.parametrize(
    "code, exit_",
    [(None, EXIT_OK), ("BAD_PARAMS", EXIT_USAGE), ("NOT_READY", EXIT_NOT_READY), ("INDEX_MISSING", EXIT_NOT_READY),
     ("DEPENDENCY", EXIT_NOT_READY), ("NOT_FOUND", EXIT_ERROR), ("PROTECTED_PATH", EXIT_ERROR), ("BAD_ID", EXIT_ERROR),
     ("INTERNAL", EXIT_ERROR)],
)
def test_exit_codes(code, exit_):
    assert exit_code_for(code) == exit_
    if code:
        assert SatkError(code, "m").exit_code == exit_
    assert (EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_NOT_READY) == (0, 1, 2, 3)


def test_satk_error_shape():
    e = SatkError("NOT_FOUND", "x", data={"k": 1})
    assert e.to_dict() == {"ok": False, "error": {"code": "NOT_FOUND", "msg": "x", "data": {"k": 1}}}
    assert str(e) == "NOT_FOUND: x"
    assert E.error_from_exception(e) == e.to_dict()
    assert E.error_from_exception(ValueError("boom"))["error"] == {"code": "INTERNAL", "msg": "ValueError: boom"}


def test_require_module():
    assert require_module("json").dumps({}) == "{}"
    with pytest.raises(SatkError) as ei:
        require_module("satk_no_such_module_xyz", pip="no-such", purpose="tests")
    assert ei.value.code == "DEPENDENCY" and ei.value.exit_code == 3
    assert "no-such" in ei.value.msg and "bootstrap.ps1" in ei.value.hint


def test_envelope_path_is_jpath(tmp_path: Path):
    from satk.core.paths import jpath

    assert json.loads(E.dumps({"p": tmp_path}))["p"] == jpath(tmp_path)
