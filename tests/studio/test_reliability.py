"""Session reliability on the mock world (no Blender): batch checkpoints by default, compact step results,
the host's after-method hook, the parameter table of one method (``satk.studio.methodref``), the generated
methods reference, the ``blender.methods`` answer and the client's time budget and crash report."""

from __future__ import annotations

import ast
import json
import sys
import textwrap
from pathlib import Path

import pytest

from satk.core.errors import SatkError
from satk.core.registry import get_op
from satk.studio import api
from satk.studio import core as K
from satk.studio import methodref as R
from satk.studio.mock import METHODS, MockHost


class _CkptHost(MockHost):
    """The mock host with a file-backed checkpoint store."""

    def __init__(self, folder):
        super().__init__()
        self.checkpoints = K.Checkpoints(folder, "ckpt", self._save, lambda _p: None)

    def _save(self, path: str) -> None:
        Path(path).write_text(json.dumps(self.scene.objects), encoding="utf-8")


def _ckpt_core(tmp_path) -> tuple[K.AuthorCore, _CkptHost]:
    host = _CkptHost(tmp_path / "ck")
    return K.AuthorCore(host, _table(), journal=K.Journal(tmp_path / "journal.jsonl")), host


def _batch(core, steps, **kw) -> dict:
    return core.call(dict({"method": "batch", "params": {"steps": steps}}, **kw))


CUBE = {"method": "mesh.primitive", "params": {"kind": "cube", "name": "a"}}
CUBE_B = {"method": "mesh.primitive", "params": {"kind": "cube", "name": "b"}}
INFO = {"method": "scene.info", "params": {"limit": 1}}


# --------------------------------------------------------------------------- checkpoints of a batch


def test_a_multi_step_batch_writes_one_checkpoint_by_default(tmp_path):
    core, host = _ckpt_core(tmp_path)
    r = _batch(core, [CUBE, INFO, CUBE_B])
    assert r["checkpoint"].endswith("0003.ckpt") and len(host.checkpoints.list()) == 1
    assert [e.get("checkpoint", "").rsplit("/", 1)[-1] for e in core.journal.entries()] == ["", "", "0003.ckpt"]
    r = _batch(core, [CUBE, CUBE_B], checkpoint=False)        # asked not to
    assert "checkpoint" not in r and len(host.checkpoints.list()) == 1
    r = _batch(core, [CUBE, INFO])                             # one mutating step: the session's own rule
    assert "checkpoint" not in r
    r = core.call(dict(CUBE, checkpoint=True))                 # a single step on request
    assert r["checkpoint"].endswith(f"{r['n']:04d}.ckpt")


def test_no_checkpoint_store_warns_only_when_asked():
    core = K.AuthorCore(MockHost(), _table())
    r = _batch(core, [CUBE, CUBE_B])
    assert "warn" not in r
    r = _batch(core, [CUBE, CUBE_B], checkpoint=True)
    assert "UNSUPPORTED: checkpoint: this world keeps no checkpoints" in r["warn"]


# --------------------------------------------------------------------------- compact step results


def _table(extra: dict | None = None) -> dict:
    table: dict = {}
    mod = type(sys)("m")
    mod.METHODS = dict(METHODS, **(extra or {}))
    K.methods_from_module(mod, table, [])
    return table


def test_compact_keeps_short_values():
    big = {"object": "lodbin1", "tris": 48, "ratio": 0.2, "names": [f"n{i}" for i in range(30)],
           "pair": [1, 2], "text": "x" * 200, "nested": {"a": 1, "deep": {"b": {"c": 1}}}, "empty": []}
    c = K.compact(big)
    assert c["object"] == "lodbin1" and c["tris"] == 48 and c["pair"] == [1, 2] and c["names"] == "30 items"
    assert len(c["text"]) == 60 and c["nested"] == {"a": 1, "deep": "1 keys"} and "empty" not in c


def test_batch_step_results_are_compact_not_dropped():
    def rows(ctx, p):
        """A method with a long answer."""
        return {"object": p.get("name", "x"), "tris": 12, "rows": [{"name": f"r{i}", "v": i} for i in range(40)],
                "note": "y" * 300}

    core = K.AuthorCore(MockHost(), _table({"t.rows": rows}))
    steps = [{"method": "t.rows", "params": {"name": f"o{i}"}} for i in range(8)]
    r = _batch(core, steps)
    size = len(json.dumps(r, separators=(",", ":")).encode())
    assert size <= min(K.MAX_BATCH_REPLY, K.MAX_REPLY + K.STEP_REPLY * 8), size
    assert [s["result"]["object"] for s in r["steps"]] == [f"o{i}" for i in range(8)]
    assert all(s["result"]["tris"] == 12 and s["result"]["rows"] == "40 items" for s in r["steps"])
    assert "truncated" not in r
    many = [{"method": "t.rows", "params": {"name": f"o{i}"}} for i in range(60)]
    r = _batch(core, many)                                     # far over the budget: numbers stay longest
    assert len(json.dumps(r, separators=(",", ":")).encode()) <= K.MAX_BATCH_REPLY
    assert any(t.startswith("steps") for t in r["truncated"])


# --------------------------------------------------------------------------- the host's hook


def test_after_method_runs_after_success_and_failure():
    seen: list[str] = []

    class Host(MockHost):
        def after_method(self, name, warn):
            seen.append(name)
            if name == "scene.info":
                raise RuntimeError("boom")

    core = K.AuthorCore(Host(), _table())
    core.call(CUBE)
    r = core.call(INFO)
    assert "INTERNAL: after scene.info: RuntimeError: boom" in r["warn"]
    with pytest.raises(SatkError):
        core.call({"method": "mesh.primitive", "params": {"kind": "cylinder"}})  # no segments
    assert seen == ["mesh.primitive", "scene.info", "mesh.primitive"]


# --------------------------------------------------------------------------- parameter tables


_SRC = '''
import x as U

def helper(q):
    return q.get("inner", 3)

def studio_style(ctx, p):
    """Studio helpers: (p, key, method, default)."""
    M = "t.studio"
    U.num(p, "width", M, 0.5, lo=0)
    U.integer(p, "segments", M, lo=3, required=True)
    U.axis(p, "axis", M, "z")
    U.text(p, "mode", M, "smooth", choices=("smooth", "flat"))
    n = p.get("name") or "lathe"
    if p.get("image"):
        use(p["image"])
    o = U.mesh_obj(ctx, p, M)
    helper(p)
    return p["profile"]

def kit_style(ctx, p):
    U.num(p, "ratio", 0.21, "kit.lod", 0.01, 1.0)
    U.need(p, "out", "kit.export")
    objs = U.resolve(ctx, p, "x", key="parts", single="part")
    if "keep" in p:
        pass
'''


def _params(src: str, name: str, tmp_path) -> dict:
    f = tmp_path / "m.py"
    f.write_text(textwrap.dedent(src), encoding="utf-8")
    tree = ast.parse(f.read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    return {p["name"]: p for p in R.params_from_source(str(f), fn)}


def test_params_from_source_studio_helpers(tmp_path):
    ps = _params(_SRC, "studio_style", tmp_path)
    assert list(ps) == ["width", "segments", "axis", "mode", "name", "image", "object", "profile", "inner"]
    assert ps["width"]["default"] == 0.5 and ps["segments"].get("required") is True
    assert ps["axis"] == {"name": "axis", "default": "z", "choices": ["x", "y", "z"]}
    assert ps["mode"]["choices"] == ["smooth", "flat"] and ps["name"]["default"] == "lathe"
    assert "required" not in ps["image"]        # read with get() first: p["image"] is guarded
    assert ps["object"]["required"] and ps["profile"]["required"] and ps["inner"]["default"] == 3


def test_params_from_source_kit_helpers(tmp_path):
    ps = _params(_SRC, "kit_style", tmp_path)
    assert ps["ratio"]["default"] == 0.21 and ps["out"]["required"] and ps["part|parts"]["required"]
    assert "required" not in ps["keep"]
    rows = dict(R.rows(list(ps.values())))
    assert rows["ratio"] == "0.21" and rows["out"] == "required" and rows["keep"] == ""


def test_params_of_a_live_function_and_the_core_answer():
    def lathe(ctx, p):
        """Spin a profile."""
        return {"segments": p.get("segments", 16), "axis": p["axis"]}

    rows = R.rows(R.params_of(lathe))
    assert rows == [["segments", "16"], ["axis", "required"]]
    core = K.AuthorCore(MockHost(), _table({"t.lathe": lathe}))
    out = core.method_list("t.lathe")
    assert out["params"] == rows and out["help"] == "Spin a profile."


def test_the_reference_covers_every_plugin_method():
    ms = R.scan()
    names = {m["name"] for m in ms}
    assert {"mesh.lathe", "kit.lod", "look.preview", "session.checkpoint", "uv.fit"} <= names
    assert {"kit.blank", "kit.blank_split"} <= names  # registered with METHODS.update({...})
    by ={m["name"]: m for m in ms}
    assert dict(R.rows(by["mesh.lathe"]["params"]))["segments"] == "required"
    assert dict(R.rows(by["kit.lod"]["params"]))["ratio"] == "0.21"
    text = R.render()
    assert text == R.render() and text.count("\n| `") == len(ms)
    committed = Path(__file__).resolve().parents[2] / "docs" / "agent" / "studio-methods.md"
    assert committed.read_text(encoding="utf-8").replace("\r\n", "\n") == text, "run: satk dev gen-docs"


# --------------------------------------------------------------------------- blender.methods


def test_methods_exact_name_is_a_parameter_table(monkeypatch):
    seen = {}

    def fake(query=None, *, session=None, timeout=120.0):
        seen["q"] = query
        if query == "mesh.lathe":
            return {"methods": [{"name": "mesh.lathe", "doc": "Spin a profile"}], "total": 1,
                    "help": "Spin a profile [[r, h], ...]", "module": "mesh.*",
                    "params": [["profile", "required"], ["axis", "\"z\" (x|y|z)"]]}
        return {"methods": [{"name": "mesh.lathe", "doc": "Spin"}, {"name": "scene.info", "doc": "List",
                                                                  "readonly": True}], "total": 2}

    monkeypatch.setattr(api, "methods", fake)
    r = get_op("blender.methods").call({"query": "mesh.lathe"})
    assert r["ok"] and r["cols"] == ["param", "default"] and r["rows"][0] == ["profile", "required"]
    assert r["method"] == "mesh.lathe" and r["mode"] == "rw" and r["help"].startswith("Spin")
    r = get_op("blender.methods").call({})
    assert r["cols"] == ["method", "mode", "doc"] and r["rows"][1] == ["scene.info", "ro", "List"]
    assert r["reference"].startswith("docs/agent/studio-methods.md")


# --------------------------------------------------------------------------- client: budget and crash report


def test_call_sends_the_budget_and_waits_longer(monkeypatch):
    got = {}

    def warm(name, method, p, timeout):
        got.update(name=name, p=p, timeout=timeout)
        return {"method": p["method"], "n": 1, "ms": 1.0}

    monkeypatch.setattr(api, "_warm", warm)
    api.call("scene.info", session="x", timeout=30)
    assert got["p"]["timeout"] == 30.0 and got["timeout"] == 30.0 + api.ANSWER_GRACE_S
    assert "checkpoint" not in got["p"]
    api.call("batch", {"steps": [INFO]}, session="x", checkpoint=False)
    assert got["p"]["checkpoint"] is False
    with pytest.raises(SatkError) as ei:
        api.call("scene.info", session="x", timeout=0.5)
    assert ei.value.code == "BAD_PARAMS"


def test_a_dead_session_is_reported_with_its_crash_report(satk_home, monkeypatch):
    from satk.saap import client as C
    from satk.studio import launcher as L

    d = L.session_dir("crashy")
    (d / "tmp").mkdir(parents=True)
    (d / "tmp" / "blender.crash.txt").write_text("stack", encoding="utf-8")
    monkeypatch.setattr(C, "read_endpoint", lambda r: {"pid": 4242, "port": 1})
    monkeypatch.setattr(C, "read_session", lambda r: {"pid": 4242, "log": "x.log", "project": "/w/assets/bin"})
    monkeypatch.setattr(C, "pid_alive", lambda pid: False)
    e = api._died("crashy", "look.preview", SatkError("PROTOCOL", "connection reset"))
    assert e is not None and e.code == "EXTERNAL_TOOL" and "died during look.preview" in e.msg
    assert e.data["crash_report"].endswith("tmp/blender.crash.txt")
    assert "session start --project bin" in e.hint
    monkeypatch.setattr(C, "pid_alive", lambda pid: True)
    assert api._died("crashy", "look.preview", SatkError("PROTOCOL", "x")) is None
