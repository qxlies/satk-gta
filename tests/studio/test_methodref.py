"""Parameter tables that know branches (``satk.studio.methodref``): keys needed only for some kinds or only with
another key are ``required_if``/``when``, never a plain ``required``; helpers and constants are followed into
other modules. Static source only (no Blender)."""

from __future__ import annotations

import ast
import textwrap

from satk.studio import methodref as R

_MAIN = '''
from .shapes import KINDS, ROUND, count, build
from . import util as U

def make(ctx, p):
    """Kind-dependent density."""
    kind = p.get("kind")
    if kind not in KINDS:
        raise ValueError(kind)
    count(kind, p)
    if kind in ROUND:
        n = p["segments"]
    elif kind == "grid":
        n = U.whole(p, "cells", "t.make")
    else:
        n = 1
    if p.get("points") is not None:
        d = U.number(p, "distance", "t.make", required=True)
    w = U.number(p, "width", "t.make", 0.5)
    return build(kind, p)

def guarded(ctx, p):
    mode = U.choose(p, "mode", "t.g", "fast")
    if mode == "fast":
        return {}
    if "span" in p:
        return {"span": p["span"]}
    return {"steps": p["steps"]}

def vague(ctx, p):
    if ctx.ready():
        return p["late"]
    return p["early"]
'''

_SHAPES = '''
KINDS = ("box", "grid", "tube", "ball")
ROUND = frozenset({"tube", "ball"})

def _int(p, key, lo, hi, default=None):
    v = p.get(key, default)
    if v is None:
        raise ValueError(key)
    return int(v)

def _vec(p, key, n, default):
    return p.get(key, default)

def count(kind, p):
    if kind == "box":
        return _vec(p, "size", 3, 1.0)
    if kind == "ball":
        return _int(p, "rings", 2, 64)
    return 0

def build(kind, p):
    if kind in ("tube", "ball"):
        return p.get("radius", 0.5)
    return None
'''

_UTIL = '''
def number(p, key, method, default=None, *, required=False):
    v = p.get(key, default)
    if v is None:
        if required:
            raise ValueError(key)
        return None
    return float(v)

def whole(p, key, method, default=None):
    v = p.get(key, default)
    if v is None:
        raise ValueError(key)
    return int(v)

def choose(p, key, method, default=None, *, choices=None):
    return p.get(key, default)
'''


def _pkg(tmp_path) -> dict:
    pkg = tmp_path / "plug"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    for name, src in (("main", _MAIN), ("shapes", _SHAPES), ("util", _UTIL)):
        (pkg / f"{name}.py").write_text(textwrap.dedent(src), encoding="utf-8")
    path = pkg / "main.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = {}
    for fn in (n for n in tree.body if isinstance(n, ast.FunctionDef)):
        out[fn.name] = {p["name"]: p for p in R.params_from_source(str(path), fn)}
    return out


def test_kind_branches_are_required_if(tmp_path):
    ps = _pkg(tmp_path)["make"]
    assert ps["kind"]["choices"] == ["box", "grid", "tube", "ball"]      # from 'if kind not in KINDS: raise'
    assert ps["segments"] == {"name": "segments", "required_if": "kind=tube|ball"}   # ROUND imported from shapes
    assert ps["cells"]["required_if"] == "kind=grid"                   # helper raises on None: required
    assert ps["rings"]["required_if"] == "kind=ball"                   # followed into count() with kind aliased
    assert ps["size"] == {"name": "size", "default": 1.0, "when": "kind=box"}     # 'default' param of _vec
    assert ps["radius"]["default"] == 0.5 and ps["radius"]["when"] == "kind=tube|ball"
    assert ps["distance"]["required_if"] == "with points"
    assert "required" not in ps["points"] and "when" not in ps["points"]
    assert ps["width"] == {"name": "width", "default": 0.5}
    rows = dict(R.rows(list(ps.values())))
    assert rows["segments"] == "required if kind=tube|ball" and rows["size"] == "1.0 if kind=box"
    assert rows["kind"] == "(box|grid|tube|ball)" and rows["width"] == "0.5"


def test_early_return_guards_and_vague_tests(tmp_path):
    out = _pkg(tmp_path)
    g = out["guarded"]
    assert g["mode"]["default"] == "fast"
    assert g["span"].get("required") is None and g["span"].get("required_if") is None   # tested with 'in' first
    assert g["steps"]["required_if"] == "mode not fast and without span"
    v = out["vague"]
    assert v["late"]["required_if"] == "some cases"      # a test that is not on a parameter: "some cases"
    assert v["early"]["required"] is True                # such a guard narrows nothing: still required


def test_param_cell_and_render_say_conditions(tmp_path):
    ps = list(_pkg(tmp_path)["make"].values())
    cell = R._param_cell(ps)
    assert "segments (required if kind=tube|ball)" in cell and "size=1.0 (if kind=box)" in cell
    assert "**" not in cell.split("segments")[1].split(",")[0]


def test_the_real_tables_of_primitive_and_ref_plane():
    by = {m["name"]: {p["name"]: p for p in m.get("params") or []} for m in R.scan()}
    prim = by["mesh.primitive"]
    assert "rounded_box" in prim["kind"]["choices"] and "capsule" in prim["kind"]["choices"]
    for key in ("segments", "rings", "subdivisions"):
        assert not prim[key].get("required") and prim[key].get("required_if"), key
    assert prim["subdivisions"]["required_if"] == "kind=ico_sphere"
    assert prim["rings"]["required_if"] == "kind=uv_sphere|capsule"
    assert "size" in prim and "rounded_box" in prim["size"]["when"]          # read in satk.studio.mock
    plane = by["ref.plane"]
    assert plane["view"].get("required") and plane["image"].get("required")
    assert not plane["points"].get("required") and plane["distance"]["required_if"] == "with points"
    assert by["mesh.lathe"]["segments"].get("required") is True             # unconditional stays required
