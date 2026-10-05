"""Operation registry and argparse/JSON-schema generation from signatures (SPEC §3.1)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Literal

import pytest

import satk
from satk.core import registry as R
from satk.core.cli import build_parser, parse_args
from satk.core.errors import SatkError


def _sample_ops():
    """Register a few operations covering every supported annotation."""

    @R.op("world.near", summary="Instances near a point.", summary_ru="Объекты рядом с точкой.",
          examples=("satk world near 2495 -1687",))
    def world_near(x: float, y: float, z: float | None = None, r: float = 50.0,
                   box: list[float] | None = None, match: Literal["aabb", "center"] = "aabb",
                   kinds: list[str] = ["inst"], area: int = 0, lod: Literal["hd", "lod", "all"] = "hd",  # noqa: B006
                   limit: int = 50) -> dict:
        """Find instances.

        Args:
            x: world X.
            y: world Y.
            z: optional Z; enables 3-D distance
                (continuation line of the description).
            r: radius in metres.
            kinds: kinds to return.
        """
        return {"x": x, "y": y, "z": z, "r": r, "box": box, "match": match, "kinds": kinds, "area": area,
                "lod": lod, "limit": limit}

    @R.op("view.capture", summary="Capture a frame.", summary_ru="Снять кадр.", long_running=True)
    def view_capture(pose: list[list[float]] | None = None, w: int = 960, inline: bool = False,
                     env: dict[str, Any] | None = None, weather: Literal[0, 8, 19] = 0, out: Path | None = None,
                     ids: list[int] | None = None) -> dict:
        """Capture.

        Args:
            pose: [[x,y,z],[yaw,pitch,roll]].
            inline: embed the image.
        """
        return {"pose": pose, "w": w, "inline": inline, "env": env, "weather": weather,
                "out": str(out) if out else None, "ids": ids}

    @R.op("re.addr", summary="Resolve addresses.", summary_ru="Разрешить адреса.", mcp="re_addr")
    def re_addr(text: list[str], module: str = "gta_sa.exe") -> dict:
        return {"text": text, "module": module}

    @R.op("dev.guard_test", summary="CLI only.", summary_ru="Только CLI.", mcp=False)
    def guard(path: str) -> dict:
        return {"path": path}

    return world_near, view_capture, re_addr, guard


def test_param_specs_from_signature(isolated_ops):
    _sample_ops()
    s = R.get_op("world.near")
    kinds = {p.name: (p.kind, p.item, p.choices, p.optional, p.positional) for p in s.params}
    assert kinds["x"] == ("float", None, None, False, True)
    assert kinds["z"] == ("float", None, None, True, False)
    assert kinds["box"] == ("list", "float", None, True, False)
    assert kinds["match"] == ("enum", None, ("aabb", "center"), False, False)
    assert kinds["kinds"] == ("list", "str", None, False, False)
    assert s.param("z").help == "optional Z; enables 3-D distance (continuation line of the description)."
    assert s.param("r").help == "radius in metres."
    assert s.doc == "Find instances."
    c = R.get_op("view.capture")
    assert c.param("pose").item == "list[float]"
    assert c.param("weather").choices == (0, 8, 19)
    assert c.param("out").kind == "path" and c.param("env").kind == "dict" and c.param("ids").item == "int"
    assert c.long_running


def test_names_cli_and_mcp(isolated_ops):
    _sample_ops()
    assert R.get_op("dev.guard_test").cli_path == ("dev", "guard-test")
    assert R.get_op("dev.guard_test").mcp_name is None
    assert R.get_op("world.near").mcp_name == "world_near"
    assert R.get_op("re.addr").mcp_name == "re_addr"
    assert R.op_by_mcp("world_near").name == "world.near"
    assert R.get_op("world.near").group == "world" and R.get_op("world.near").mcp_group == "index"
    assert R.get_op("view.capture").mcp_group == "view"
    with pytest.raises(SatkError) as ei:
        R.get_op("world.nera")
    assert ei.value.code == "NOT_FOUND" and "world.near" in ei.value.did_you_mean
    with pytest.raises(SatkError) as ei:
        R.op_by_mcp("nope")
    assert ei.value.code == "UNKNOWN_METHOD"
    assert [o.name for o in R.all_ops()] == ["dev.guard_test", "re.addr", "view.capture", "world.near"]


def test_argparse_types_literals_lists_negative_numbers(isolated_ops):
    _sample_ops()
    s = R.get_op("world.near")
    args = parse_args(s, ["2495", "-1687.5", "--z", "-10", "--box", "0,-5,10,10", "--match", "CENTER",
                          "--kinds", "inst,item", "zone", "--area", "0x3", "--lod", "all"])
    assert args == {"x": 2495.0, "y": -1687.5, "z": -10.0, "box": [0.0, -5.0, 10.0, 10.0], "match": "center",
                    "kinds": ["inst", "item", "zone"], "area": 3, "lod": "all"}
    kw = s.bind(args)
    assert kw["r"] == 50.0 and kw["limit"] == 50
    c = R.get_op("view.capture")
    a2 = parse_args(c, ["--pose", "1,2,3;90,0,0", "--inline", "--env", '{"time":"12:00"}', "--weather", "8",
                        "--ids", "1,2", "--out", "D:/x"])
    assert a2 == {"pose": [[1.0, 2.0, 3.0], [90.0, 0.0, 0.0]], "inline": True, "env": {"time": "12:00"},
                  "weather": 8, "ids": [1, 2], "out": Path("D:/x")}
    assert parse_args(c, ["--no-inline"]) == {"inline": False}
    a3 = parse_args(R.get_op("re.addr"), ["0x53bf09", "gta_sa.exe+0x1000,x"])
    assert a3 == {"text": ["0x53bf09", "gta_sa.exe+0x1000,x"]}  # positional list keeps commas


@pytest.mark.parametrize(
    "argv, frag",
    [
        (["1"], "y"),                               # missing positional
        (["1", "2", "--match", "box"], "match"),    # bad Literal
        (["1", "2", "--area", "x"], "area"),        # bad int
        (["1", "2", "--nope", "1"], "nope"),        # unknown flag
        (["a", "2"], "x"),                          # bad float
    ],
)
def test_argparse_errors_are_bad_params(isolated_ops, argv, frag):
    _sample_ops()
    with pytest.raises(SatkError) as ei:
        R.get_op("world.near").bind(parse_args(R.get_op("world.near"), argv))
    assert ei.value.code == "BAD_PARAMS" and frag in ei.value.msg


def test_bad_literal_suggests(isolated_ops):
    _sample_ops()
    with pytest.raises(SatkError) as ei:
        R.get_op("world.near").bind({"x": 1, "y": 2, "match": "centre"})
    assert ei.value.did_you_mean == ["center"]


def test_bind_json_args(isolated_ops):
    _sample_ops()
    s = R.get_op("world.near")
    kw = s.bind({"x": "1.5", "y": 2, "kinds": "inst", "box": [1, 2, 3, 4], "z": None})
    assert kw["x"] == 1.5 and kw["kinds"] == ["inst"] and kw["box"] == [1.0, 2.0, 3.0, 4.0] and kw["z"] is None
    with pytest.raises(SatkError) as ei:
        s.bind({"x": 1, "y": 2, "radius": 3})
    assert ei.value.code == "BAD_PARAMS" and ei.value.data["params"][0] == "x"
    with pytest.raises(SatkError):
        s.bind({"x": True, "y": 2})  # bool is not a number
    with pytest.raises(SatkError):
        s.bind({"y": 2})
    # defaults are copied: mutating one call's list does not leak into the next
    kw2 = s.bind({"x": 1, "y": 2})
    kw2["kinds"].append("zzz")
    assert s.bind({"x": 1, "y": 2})["kinds"] == ["inst"]


def test_input_schema(isolated_ops):
    _sample_ops()
    sch = R.get_op("world.near").input_schema()
    json.dumps(sch)
    assert sch["required"] == ["x", "y"] and sch["additionalProperties"] is False
    p = sch["properties"]
    assert p["match"] == {"type": "string", "enum": ["aabb", "center"], "default": "aabb"}
    assert p["box"]["type"] == "array" and p["box"]["items"] == {"type": "number"}
    assert p["kinds"]["default"] == ["inst"] and p["r"]["description"] == "radius in metres."
    c = R.get_op("view.capture").input_schema()["properties"]
    assert c["weather"]["type"] == "integer" and c["pose"]["items"]["type"] == "array"
    assert c["env"]["type"] == "object" and c["inline"]["type"] == "boolean"


def test_summary_limit_300(isolated_ops):
    ok = "x" * R.MAX_SUMMARY
    R.op("t.ok", summary=ok, summary_ru="ок")(lambda: {})
    with pytest.raises(ValueError, match="max 300"):
        R.op("t.long", summary="x" * 301, summary_ru="длинно")
    with pytest.raises(ValueError):
        R.op("t.nosum", summary=" ", summary_ru="x")
    with pytest.raises(ValueError):
        R.op("t.noru", summary="x", summary_ru="")


@pytest.mark.parametrize("name", ["Index.build", "index..build", "1x", "index-build", ""])
def test_bad_op_names(isolated_ops, name):
    with pytest.raises(ValueError):
        R.op(name, summary="s", summary_ru="s")


def test_bad_signatures(isolated_ops):
    def deco(fn):
        return R.op("t.x", summary="s", summary_ru="s")(fn)

    def no_ann(a):  # noqa: ANN001
        return {}

    def var(*a: int) -> dict:
        return {}

    def tup(a: tuple[int, int]) -> dict:
        return {}

    def union(a: int | str) -> dict:
        return {}

    def reserved(json: bool = False) -> dict:
        return {}

    def bool_no_default(flag: bool) -> dict:
        return {}

    for fn in (no_ann, var, tup, union, reserved, bool_no_default):
        with pytest.raises(TypeError):
            deco(fn)


def test_duplicate_registration(isolated_ops):
    def a() -> dict:
        return {}

    def b() -> dict:
        return {}

    R.op("t.a", summary="s", summary_ru="s")(a)
    R.op("t.a", summary="s", summary_ru="s")(a)  # same function again (re-import): fine
    with pytest.raises(ValueError, match="already registered"):
        R.op("t.a", summary="s", summary_ru="s")(b)
    with pytest.raises(ValueError, match="MCP tool name"):
        R.op("t.b", summary="s", summary_ru="s", mcp="t_a")(b)


def test_invoke_envelopes(isolated_ops, satk_home):
    @R.op("t.ok", summary="s", summary_ru="s")
    def ok(n: int = 1) -> dict:
        return {"n": n}

    @R.op("t.none", summary="s", summary_ru="s")
    def none() -> None:
        return None

    @R.op("t.err", summary="s", summary_ru="s")
    def err() -> dict:
        raise SatkError("NOT_READY", "no index", hint="satk index build")

    @R.op("t.crash", summary="s", summary_ru="s")
    def crash() -> dict:
        raise RuntimeError("boom")

    assert R.invoke("t.ok", {"n": "5"}) == {"ok": True, "n": 5}
    assert R.invoke("t.none") == {"ok": True}
    assert R.invoke("t.err")["error"]["code"] == "NOT_READY"
    env = R.invoke("t.crash")
    assert env["error"]["code"] == "INTERNAL" and "boom" in env["error"]["msg"]
    log = satk_home / "work" / "logs" / "errors.log"
    assert log.is_file() and "RuntimeError: boom" in log.read_text(encoding="utf-8")
    assert R.invoke("t.nope")["error"]["code"] == "NOT_FOUND"
    assert R.invoke("t.ok", {"zz": 1})["error"]["code"] == "BAD_PARAMS"


def test_doctor_and_status_registration(isolated_ops):
    @R.doctor_check("t_check")
    def chk() -> dict:
        return {"status": "ok", "msg": "fine", "fix": None}

    @R.status_provider("t_section")
    def st(deep: bool) -> dict:
        return {"deep": deep}

    assert R.doctor_checks()["t_check"]() == {"status": "ok", "msg": "fine", "fix": None}
    assert R.status_providers()["t_section"](True) == {"deep": True}
    with pytest.raises(ValueError):
        R.doctor_check("t_check")(lambda: {})


def test_progress_handler():
    seen = []
    R.report_progress(1, 2, "no handler: ignored")
    with R.progress_handler(lambda d, t, m: seen.append((d, t, m))):
        R.report_progress(1, 10, "a")
    R.report_progress(2, 10, "after")
    assert seen == [(1, 10, "a")]


def test_discover_survives_broken_package(tmp_path, monkeypatch):
    pkg = tmp_path / "zz_broken"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "ops.py").write_text("import satk_definitely_missing_module\n", encoding="utf-8")
    good = tmp_path / "zz_good"
    good.mkdir()
    (good / "__init__.py").write_text("", encoding="utf-8")
    (good / "ops.py").write_text(
        "from satk.core.registry import op\n"
        "@op('zz.hello', summary='hello', summary_ru='привет')\n"
        "def hello(name: str = 'x') -> dict:\n    return {'hello': name}\n", encoding="utf-8")
    monkeypatch.setattr(satk, "__path__", list(satk.__path__) + [str(tmp_path)])
    try:
        with R.isolated_registry(discovered=False):
            R.discover()
            errs = R.import_errors()
            assert "satk.zz_broken.ops" in errs and "ModuleNotFoundError" in errs["satk.zz_broken.ops"]
            assert R.invoke("zz.hello", {"name": "y"}) == {"ok": True, "hello": "y"}
            assert "satk.core.ops" not in errs
            from satk.core.ops import _check_ops_import  # already imported: not re-registered here

            chk = _check_ops_import()
            assert chk["status"] == "fail" and "zz_broken" in chk["msg"]
    finally:
        for m in [m for m in sys.modules if m.startswith(("satk.zz_broken", "satk.zz_good"))]:
            del sys.modules[m]


def test_parse_docstring_sections():
    desc, params = R.parse_docstring("""Summary line.

    More text.

    Args:
        a (int): first
            continues here.
        b: second.

    Returns:
        dict: ignored.
    """)
    assert desc == "Summary line.\n\nMore text."
    assert params == {"a": "first continues here.", "b": "second."}


def test_help_from_parser(isolated_ops):
    _sample_ops()
    text = " ".join(build_parser(R.get_op("world.near")).format_help().split())
    assert "satk world near" in text and "--box" in text and "{aabb,center}" in text and "radius in metres." in text
