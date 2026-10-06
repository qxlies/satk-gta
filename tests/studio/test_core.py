"""satk.studio.core: method plug-ins (K3), the author.call dispatch, journal and reply budget (no Blender)."""

from __future__ import annotations

import json
import sys
import textwrap

import pytest

from satk.core.errors import SatkError
from satk.studio import core as K
from satk.studio.mock import METHODS, MockHost, MockStudioWorld, primitive_counts, primitive_dims


def _core(tmp_path=None, methods=None) -> K.AuthorCore:
    table: dict = {}
    errs: list[str] = []
    mod = type(sys)("m")
    mod.METHODS = methods if methods is not None else METHODS
    K.methods_from_module(mod, table, errs)
    assert errs == []
    journal = K.Journal(tmp_path / "journal.jsonl") if tmp_path is not None else None
    return K.AuthorCore(MockHost(), table, journal=journal)


# --------------------------------------------------------------------------- plug-in discovery


def _write(root, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(text), encoding="utf-8")


def test_discover_methods_packages_optional_and_errors(tmp_path, monkeypatch):
    _write(tmp_path, "plugA/__init__.py", "")
    _write(tmp_path, "plugA/methods/__init__.py", "")
    _write(tmp_path, "plugA/methods/one.py", """
        def a(ctx, p):
            '''First line of a.

            More text.'''
            return {}
        def b(ctx, p):
            return {}
        b.readonly = True
        METHODS = {"grp.a": a, "grp.b": b, "Bad Name": a, "grp.c": 3}
    """)
    _write(tmp_path, "plugA/methods/two.py", "METHODS = {'grp.a': lambda c, p: {}, 'two.x': lambda c, p: {}}\n")
    _write(tmp_path, "plugA/methods/_private.py", "METHODS = {'hidden.x': lambda c, p: {}}\n")
    _write(tmp_path, "plugB/__init__.py", "")
    _write(tmp_path, "plugB/methods.py", "import no_such_dependency_xyz\nMETHODS = {}\n")
    _write(tmp_path, "plugC/__init__.py", "")
    _write(tmp_path, "plugC/methods.py", "METHODS = {'look.flat': lambda c, p: {}}\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    methods, errors = K.discover_methods(["plugA.methods"], ["plugB.methods", "plugC.methods", "plugZ.methods"])
    assert list(methods) == ["grp.a", "grp.b", "look.flat", "two.x"]  # sorted; hidden and bad ones skipped
    assert methods["grp.a"].module == "plugA.methods.one" and methods["grp.a"].doc == "First line of a."
    assert methods["grp.b"].readonly and not methods["grp.a"].readonly
    text = "\n".join(errors)
    assert "'Bad Name'" in text and "'grp.c' is not callable" in text
    assert "'grp.a' is already defined in plugA.methods.one" in text
    assert "plugB.methods: ModuleNotFoundError" in text  # exists but broken -> reported
    assert "plugZ" not in text  # absent optional module -> silent


def test_discover_required_package_missing_is_an_error():
    methods, errors = K.discover_methods(["no_such_pkg_q.methods"], [])
    assert methods == {} and errors and errors[0].startswith("no_such_pkg_q.methods: ModuleNotFoundError")


# --------------------------------------------------------------------------- author.call


def test_call_reply_stats_and_journal(tmp_path):
    core = _core(tmp_path)
    r = core.call({"method": "mesh.primitive", "params": {"kind": "cylinder", "segments": 12, "radius": 0.3,
                                                           "depth": 1, "name": "post"}})
    assert r["method"] == "mesh.primitive" and r["n"] == 1 and r["changed"] == ["post"]
    assert r["result"] == {"object": "post"}
    assert r["stats"]["objects"]["post"] == {"tris": 44, "verts": 24, "dims": [0.6, 0.6, 1.0]}
    assert r["stats"]["scene"] == {"objects": 1, "tris": 44, "verts": 24, "bbox": [[-0.3, -0.3, -0.5], [0.3, 0.3, 0.5]]}
    assert r["ms"] >= 0 and core.rev == 1
    ro = core.call({"method": "scene.info"})
    assert "stats" not in ro and ro["result"]["total"] == 1 and core.rev == 1  # read-only: no stats, no new revision
    assert "stats" in core.call({"method": "scene.info", "stats": "scene"})
    assert "stats" not in core.call({"method": "mesh.primitive", "params": {"kind": "cube"}, "stats": "none"})
    lines = core.journal.entries()
    assert [e["n"] for e in lines] == [1, 2, 3, 4] and lines[0]["changed"] == ["post"] and lines[1]["ro"] is True
    assert lines[0]["params"]["segments"] == 12 and all(e["ok"] for e in lines)


def test_python_is_journaled_with_its_hash(tmp_path):
    import hashlib

    core = _core(tmp_path)
    code = "result = {'a': 1}"
    r = core.call({"method": "python", "params": {"code": code}})
    assert r["result"] == {"value": {"a": 1}}
    assert any(w.startswith("UNSUPPORTED: checkpoint") for w in r["warn"])  # the mock keeps no checkpoints
    e = core.journal.entries()[-1]
    assert e["params"]["code"] == code and e["code_sha256"] == hashlib.sha256(code.encode()).hexdigest()


def test_errors_keep_the_core_alive_and_are_journaled(tmp_path):
    core = _core(tmp_path)
    with pytest.raises(SatkError) as ei:
        core.call({"method": "mesh.primitiv"})
    assert ei.value.code == "NOT_FOUND" and ei.value.data["did_you_mean"] == ["mesh.primitive"]
    assert core.n == 0  # an unknown method is not a step
    with pytest.raises(SatkError) as ei:
        core.call({"method": "python", "params": {"code": "raise ValueError('boom')"}})
    assert ei.value.code == "BAD_PARAMS" and "boom" in ei.value.msg
    with pytest.raises(SatkError) as ei:
        core.call({"method": "mesh.primitive", "params": {"kind": "uv_sphere", "segments": 8}})
    assert ei.value.code == "BAD_PARAMS" and "rings" in ei.value.msg and ei.value.hint
    assert core.call({"method": "scene.info"})["n"] == 3
    bad = [e for e in core.journal.entries() if not e["ok"]]
    assert [e["n"] for e in bad] == [1, 2] and bad[0]["error"].startswith("BAD_PARAMS: python")


def test_plain_exceptions_map_to_bad_params_or_internal():
    def typo(ctx, p):
        return {}["x"]

    def crash(ctx, p):
        raise RuntimeError("bpy said no")

    core = _core(methods={"t.typo": typo, "t.crash": crash})
    with pytest.raises(SatkError) as ei:
        core.call({"method": "t.typo"})
    assert ei.value.code == "BAD_PARAMS" and ei.value.data["where"].startswith("test_core.py:")
    with pytest.raises(SatkError) as ei:
        core.call({"method": "t.crash"})
    assert ei.value.code == "INTERNAL" and "RuntimeError: bpy said no" in ei.value.msg


def test_reply_fits_the_budget():
    core = _core()
    for i in range(60):
        core.call({"method": "mesh.primitive", "params": {"kind": "grid", "x_segments": i + 1, "y_segments": 1,
                                                          "name": f"g{i:02d}"}, "stats": "none"})
    names = [f"g{i:02d}" for i in range(60)]

    def many(ctx, p):
        return {"changed": names, "rows": [{"name": n, "note": "x" * 40} for n in names], "text": "y" * 900}

    core.methods["t.many"] = K.Method("t.many", many, "t")
    r = core.call({"method": "t.many"})
    size = len(json.dumps(r, separators=(",", ":")).encode())
    assert size <= K.MAX_REPLY, size
    assert r["truncated"] and r["truncated"][0].startswith("stats.objects: ")
    kept = list(r["stats"]["objects"])
    assert kept[0] == "g59" and r["stats"]["objects_more"] == 60 - len(kept)  # the heaviest objects stay
    assert r["stats"]["scene"]["objects"] == 60


def test_fit_reply_leaves_small_replies_alone():
    small = {"method": "x", "n": 1, "ms": 0.1}
    assert K.fit_reply(small) is small


def test_jsonable():
    assert K.jsonable({"a": (1, 2.5, float("nan")), "b": {3, 1}, 4: None}) == {"a": [1, 2.5, None], "b": [1, 3],
                                                                                 "4": None}


# --------------------------------------------------------------------------- primitives


#: (kind, params) -> (verts, tris): measured in Blender 5.1 (bmesh), also checked by the live test.
PRIMS = [
    ("plane", {}, (4, 2)),
    ("grid", {"x_segments": 3, "y_segments": 2}, (12, 12)),
    ("cube", {"size": [1, 2, 3]}, (8, 12)),
    ("cylinder", {"segments": 12}, (24, 44)),
    ("cylinder", {"segments": 8, "cap": "none"}, (16, 16)),
    ("cone", {"segments": 12}, (13, 22)),
    ("cone", {"segments": 6, "radius_top": 0.2}, (12, 20)),
    ("uv_sphere", {"segments": 8, "rings": 6}, (42, 80)),
    ("ico_sphere", {"subdivisions": 1}, (12, 20)),
    ("ico_sphere", {"subdivisions": 2}, (42, 80)),
    ("circle", {"segments": 10}, (10, 8)),
    ("circle", {"segments": 10, "cap": "none"}, (10, 0)),
]


@pytest.mark.parametrize("kind,params,want", PRIMS, ids=[f"{k}-{i}" for i, (k, _, _) in enumerate(PRIMS)])
def test_primitive_counts(kind, params, want):
    assert primitive_counts(kind, dict(params, kind=kind)) == want


@pytest.mark.parametrize("kind,params,needs", [
    ("cylinder", {}, "segments"), ("cone", {}, "segments"), ("circle", {}, "segments"),
    ("uv_sphere", {"segments": 8}, "rings"), ("ico_sphere", {}, "subdivisions"), ("grid", {"x_segments": 2}, "y_segments"),
])
def test_round_primitives_need_explicit_density(kind, params, needs):
    with pytest.raises(SatkError) as ei:
        primitive_counts(kind, dict(params, kind=kind))
    assert ei.value.code == "BAD_PARAMS" and needs in ei.value.msg


def test_primitive_validation():
    for bad in ({"kind": "torus"}, {"kind": "cylinder", "segments": 2}, {"kind": "cylinder", "segments": 8.5},
                {"kind": "cylinder", "segments": 8, "cap": "fan"}, {"kind": "ico_sphere", "subdivisions": True}):
        with pytest.raises(SatkError):
            primitive_counts(bad["kind"], bad)
    assert primitive_dims("cube", {"size": 2}) == [2.0, 2.0, 2.0]
    assert primitive_dims("cylinder", {"radius": 0.3, "depth": 1.5}) == [0.6, 0.6, 1.5]
    with pytest.raises(SatkError):
        primitive_dims("cube", {"size": [1, -1, 1]})


# --------------------------------------------------------------------------- the SAAP world


def test_author_world_dispatch():
    w = MockStudioWorld()
    hello = w.dispatch("hello", {"token": "x" * 32, "client": {"name": "t"}})
    assert hello["role"] == "blender" and hello["caps"] == ["core", "author"] and hello["methods"] == 9
    assert w.dispatch("author.methods", {"query": "prim"})["methods"][0]["name"] == "mesh.primitive"
    rows = w.dispatch("author.methods", {})["methods"]
    assert [m["name"] for m in rows] == ["mesh.primitive", "python", "scene.clear", "scene.info", "session.checkpoint",
                                         "session.checkpoints", "session.journal", "session.prune", "session.restore"]
    assert {m["name"] for m in rows if m.get("readonly")} == {"scene.info", "session.checkpoint",
                                                              "session.checkpoints", "session.journal",
                                                              "session.prune"}
    w.dispatch("author.call", {"method": "mesh.primitive", "params": {"kind": "cube"}})
    st = w.dispatch("status", {})
    assert st["author"] == {"calls": 1, "step": 1, "methods": 9, "objects": 1} and st["rev"]["scene"] == 1
    assert w.dispatch("quit", {}) == {} and w.quit_requested
    with pytest.raises(SatkError) as ei:
        w.dispatch("camera.get", {})
    assert ei.value.code == "UNKNOWN_METHOD"
