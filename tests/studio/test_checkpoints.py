"""satk.studio.core: checkpoints (save, tags, prune, restore), batches and the session.* methods - on the mock world
with a file-backed checkpoint store (no Blender)."""

from __future__ import annotations

import json
import sys

import pytest

from satk.core.errors import SatkError
from satk.studio import core as K
from satk.studio.mock import METHODS, MockHost


class CkptHost(MockHost):
    """The mock host whose scene is saved to / loaded from small JSON checkpoint files."""

    def __init__(self, folder):
        super().__init__()
        self.checkpoints = K.Checkpoints(folder, "ckpt", self._save, self._load)

    def _save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.scene.objects, f)

    def _load(self, path: str) -> None:
        with open(path, encoding="utf-8") as f:
            self.scene.objects = json.load(f)

    def object_names(self):
        return sorted(self.scene.objects)


def _core(tmp_path, every: int = 0) -> tuple[K.AuthorCore, CkptHost]:
    table: dict = {}
    mod = type(sys)("m")
    mod.METHODS = METHODS
    K.methods_from_module(mod, table, [])
    host = CkptHost(tmp_path / "ck")
    return K.AuthorCore(host, table, journal=K.Journal(tmp_path / "journal.jsonl"), checkpoint_every=every), host


def _cube(core, name: str) -> dict:
    return core.call({"method": "mesh.primitive", "params": {"kind": "cube", "name": name}})


def test_checkpoints_save_tag_find_prune(tmp_path):
    saved = []
    cp = K.Checkpoints(tmp_path, "blend", lambda p: (open(p, "wb").write(b"x"), saved.append(p)), lambda p: None,
                       keep=3)
    for n in range(1, 7):
        cp.save(n)
    cp.save(4, "G1")
    (tmp_path / "notes.txt").write_text("not ours", encoding="utf-8")
    rows = cp.list()
    assert [(r["n"], r.get("tag")) for r in rows] == [(4, None), (4, "G1"), (5, None), (6, None)]
    assert (tmp_path / "notes.txt").is_file()  # only our naming scheme is ever removed
    assert cp.find("G1")["path"].endswith("0004-G1.blend") and cp.find("last")["n"] == 6
    assert cp.find(5)["n"] == 5 and cp.find("5")["n"] == 5
    with pytest.raises(SatkError) as ei:
        cp.find(2)
    assert ei.value.code == "NOT_FOUND" and ei.value.data["checkpoints"] == [4, "4-G1", 5, 6]
    with pytest.raises(SatkError):
        cp.save(7, "bad tag")
    assert len(cp.prune(0)) == 3 and [r.get("tag") for r in cp.list()] == ["G1"]


def test_auto_checkpoint_every_n_mutating_steps(tmp_path):
    core, host = _core(tmp_path, every=2)
    r1 = _cube(core, "a")
    r2 = _cube(core, "b")
    core.call({"method": "scene.info"})  # read-only: not counted
    r3 = _cube(core, "c")
    assert "checkpoint" not in r1 and r2["checkpoint"].endswith("0002.ckpt") and "checkpoint" not in r3
    r4 = _cube(core, "d")
    assert r4["checkpoint"].endswith("0005.ckpt")
    entries = core.journal.entries()
    assert [e.get("checkpoint", "").rsplit("/", 1)[-1] for e in entries if e.get("checkpoint")] == \
        ["0002.ckpt", "0005.ckpt"]


def test_session_methods_restore_tags_and_journal(tmp_path):
    core, host = _core(tmp_path)
    _cube(core, "a")
    r = core.call({"method": "session.checkpoint", "params": {"tag": "G1"}})
    assert r["result"]["tag"] == "G1" and r["result"]["checkpoint"].endswith("0002-G1.ckpt")
    _cube(core, "b")
    _cube(core, "c")
    assert sorted(host.scene.objects) == ["a", "b", "c"]
    r = core.call({"method": "session.restore", "params": {"ref": "G1"}})
    assert r["result"]["restored"] == 2 and r["result"]["tag"] == "G1" and r["changed"] == ["a"]
    assert sorted(host.scene.objects) == ["a"]
    rows = core.call({"method": "session.checkpoints"})["result"]
    assert rows["total"] == 1 and rows["checkpoints"] == [{"n": 2, "tag": "G1", "kb": rows["checkpoints"][0]["kb"]}]
    j = core.call({"method": "session.journal", "params": {"last": 3}})["result"]["steps"]
    assert [s["method"] for s in j] == ["mesh.primitive", "session.restore", "session.checkpoints"]
    last = [e for e in core.journal.entries() if e["method"] == "session.restore"][-1]
    assert last["method"] == "session.restore" and last["restore"] == 2 and last["restore_path"].endswith("G1.ckpt")
    with pytest.raises(SatkError) as ei:
        core.call({"method": "session.restore", "params": {"ref": 99}})
    assert ei.value.code == "NOT_FOUND"
    with pytest.raises(SatkError) as ei:
        core.call({"method": "session.checkpoint", "params": {"tag": "no spaces"}})
    assert ei.value.code == "BAD_PARAMS"
    core.call({"method": "session.prune", "params": {"keep": 0}})
    assert [r.get("tag") for r in host.checkpoints.list()] == ["G1"]


def test_session_methods_without_checkpoints_are_unsupported(tmp_path):
    table: dict = {}
    mod = type(sys)("m")
    mod.METHODS = METHODS
    K.methods_from_module(mod, table, [])
    core = K.AuthorCore(MockHost(), table)
    with pytest.raises(SatkError) as ei:
        core.call({"method": "session.restore"})
    assert ei.value.code == "UNSUPPORTED"
    r = core.call({"method": "python", "params": {"code": "result = 1"}})
    assert "UNSUPPORTED: checkpoint: this world keeps no checkpoints" in r["warn"]


def test_batch_runs_journals_and_stops_at_the_failing_step(tmp_path):
    core, host = _core(tmp_path)
    r = core.call({"method": "batch", "params": {"steps": [
        {"method": "mesh.primitive", "params": {"kind": "cube", "name": "a"}},
        {"method": "scene.info", "params": {"limit": 1}},
        {"method": "mesh.primitive", "params": {"kind": "cylinder", "segments": 8, "name": "b"}}]}})
    assert r["method"] == "batch" and r["n"] == 3 and [s["n"] for s in r["steps"]] == [1, 2, 3]
    assert r["changed"] == ["a", "b"] and r["stats"]["objects"]["b"]["tris"] == 28
    assert [e["method"] for e in core.journal.entries()] == ["mesh.primitive", "scene.info", "mesh.primitive"]
    assert all(e.get("batch") for e in core.journal.entries())
    with pytest.raises(SatkError) as ei:
        core.call({"method": "batch", "params": {"steps": [
            {"method": "mesh.primitive", "params": {"kind": "cube", "name": "c"}},
            {"method": "mesh.primitive", "params": {"kind": "cylinder", "name": "d"}},  # no segments
            {"method": "mesh.primitive", "params": {"kind": "cube", "name": "e"}}]}})
    assert ei.value.code == "BAD_PARAMS" and ei.value.data["step"] == 2 and ei.value.data["done"] == 1
    assert "c" in host.scene.objects and "e" not in host.scene.objects  # the steps before stay applied
    for bad in ({"steps": []}, {"steps": [{"params": {}}]}, {"steps": [{"method": "batch"}]}, {}):
        with pytest.raises(SatkError) as ei:
            core.call({"method": "batch", "params": bad})
        assert ei.value.code == "BAD_PARAMS"
    with pytest.raises(SatkError) as ei:
        core.call({"method": "batch", "params": {"steps": [{"method": "mesh.primitve"}]}})
    assert ei.value.code == "NOT_FOUND" and ei.value.data["did_you_mean"] == ["mesh.primitive"]


def test_numbering_continues_across_sessions_sharing_a_journal(tmp_path):
    core, _ = _core(tmp_path)
    _cube(core, "a")
    _cube(core, "b")
    again, _ = _core(tmp_path)
    assert again.n == 2 and _cube(again, "c")["n"] == 3
    n = again.journal_event("session.start", name="x")
    assert n == 4 and again.journal.entries()[-1] == {"n": 4, "method": "session.start", "ok": True, "name": "x"}


def test_external_edits_get_a_tagged_checkpoint(tmp_path):
    core, host = _core(tmp_path)
    _cube(core, "a")
    e = core.record_external(["a", "z"])
    assert e["method"] == "external" and e["changed"] == ["a", "z"] and e["checkpoint"].endswith("0002-ext.ckpt")
    assert core.journal.entries()[-1]["method"] == "external"


def test_method_help_by_exact_name():
    from satk.studio.mock import MockStudioWorld

    w = MockStudioWorld()
    one = w.dispatch("author.methods", {"query": "mesh.primitive"})
    assert one["total"] == 1 and one["help"].startswith("Add a mesh primitive") and "module" in one
    assert "help" not in w.dispatch("author.methods", {"query": "mesh"})
