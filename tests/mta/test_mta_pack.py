"""Model pack input validation and the files delivered to an MTA client (synthetic assets only)."""

from __future__ import annotations

import json
import re
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from satk.mta.pack import collect_models


def _asset(path: Path, payload: bytes = b"test") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    cid = 0x10 if path.suffix.lower() == ".dff" else 0x16
    child = struct.pack("<III", 1, len(payload), 0x1803FFFF) + payload
    path.write_bytes(struct.pack("<III", cid, len(child), 0x1803FFFF) + child)
    return path


def _list(path: Path, rows) -> Path:
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path


def test_folder_companions_prefer_the_models_own_directory(tmp_path):
    _asset(tmp_path / "a" / "alpha.dff")
    own = _asset(tmp_path / "a" / "alpha.txd", b"own")
    _asset(tmp_path / "z" / "alpha.txd", b"unrelated")
    _asset(tmp_path / "b" / "beta.dff")
    shared = _asset(tmp_path / "b" / "common.txd", b"shared")
    models = collect_models(tmp_path, [])
    assert [(m.name, m.txd) for m in models] == [("alpha", own), ("beta", shared)]


def test_nested_pack_keeps_distinct_and_shared_file_contents(satk_home, run_cli, tmp_path):
    rows = []
    textures = {}
    for name, texture in (("a", "c_shared.txd"), ("b", "shared.txd"), ("c", "shared.txd")):
        _asset(tmp_path / name / "model.dff", name.encode())
        textures[name] = _asset(tmp_path / name / texture, name.encode() * 40)
        rows.append({"name": name, "dff": f"{name}/model.dff", "txd": f"{name}/{texture}"})
    _asset(tmp_path / "d" / "model.dff", b"d")
    rows.append({"name": "d", "dff": "d/model.dff", "txd": "b/../b/shared.txd"})
    textures["d"] = textures["b"]
    source = _list(tmp_path / "models.json", rows)
    args = ["mta", "pack", str(source), "--kind", "object", "--new-id", "--name", "nested"]
    result = run_cli(args)
    assert result.code == 0, result.json
    env = result.json
    root = Path(env["dir"])
    model_text = (root / "models.lua").read_text(encoding="utf-8")
    assignments = dict(re.findall(r'\{name = "([^"]+)"[^\n]*?txd = "([^"]+)"', model_text))
    assert set(assignments) == set(textures)
    assert assignments["b"] == assignments["d"]
    assert len(set(assignments.values())) == 3
    for name, rel in assignments.items():
        assert (root / rel).read_bytes() == textures[name].read_bytes()
    meta = ET.parse(root / "meta.xml").getroot()
    files = [e.get("src") for e in meta.findall("file")]
    assert len(files) == len(set(f.lower() for f in files)) == 7
    download_files = files + ["models.lua", "client.lua"]
    assert env["download"]["files"] == len(download_files)
    assert env["download"]["bytes"] == sum((root / rel).stat().st_size for rel in download_files)
    assert env["lint"]["clean"] is True
    before = {rel: (root / rel).read_bytes() for rel in files + ["meta.xml", "models.lua", "client.lua"]}
    again = run_cli(args + ["--force"])
    assert again.code == 0 and again.json["download"] == env["download"]
    assert before == {rel: (root / rel).read_bytes() for rel in before}


def test_json_replace_order_is_model_name_order(satk_home, run_cli, tmp_path):
    for n in ("zulu", "alpha"):
        _asset(tmp_path / f"{n}.dff")
    source = _list(tmp_path / "list.json", [{"dff": "zulu.dff"}, {"dff": "alpha.dff"}])
    env = run_cli(["mta", "pack", str(source), "--kind", "vehicle", "--replace", "411", "--replace", "415"]).json
    assert env["ok"] and env["models"] == [{"name": "alpha", "replace": 411}, {"name": "zulu", "replace": 415}]


@pytest.mark.parametrize("rows,code", [
    ([], "NOT_FOUND"),
    ([{"dff": 123}], "BAD_PARAMS"),
    ([{"dff": "a.dff", "txd": False}], "BAD_PARAMS"),
    ([{"dff": "a.dff", "name": ["a"]}], "BAD_PARAMS"),
    ([{"dff": "a.dff", "col": "a.dff"}], "BAD_PARAMS"),
    ([{"dff": "a.dff"}, {"dff": "missing.dff"}], "NOT_FOUND"),
    ([{"dff": "a.dff", "name": "A B"}, {"dff": "a.dff", "name": "a_b"}], "AMBIGUOUS"),
    ([{"dff": "a.dff", "lod": "bad"}], "BAD_PARAMS"),
    ([{"dff": "a.dff", "lod": float("nan")}], "BAD_PARAMS"),
    ([{"dff": "a.dff", "lod": -1}], "BAD_PARAMS"),
    ([{"dff": "a.dff", "replace": True}], "BAD_PARAMS"),
    ([{"dff": "a.dff", "replace": 411.5}], "BAD_PARAMS"),
    ([{"dff": "a.dff", "replace": -1}], "BAD_PARAMS"),
    ([{"dff": "a.dff", "replace": 65535}], "BAD_PARAMS"),
    ([{"dff": "a.dff", "name": "a", "replace": 411},
      {"dff": "a.dff", "name": "b", "replace": 411}], "BAD_PARAMS"),
])
def test_bad_lists_fail_before_writing_a_resource(satk_home, run_cli, tmp_path, rows, code):
    asset = _asset(tmp_path / "a.dff")
    before = asset.read_bytes()
    source = _list(tmp_path / "bad.json", rows)
    env = run_cli(["mta", "pack", str(source), "--kind", "vehicle", "--name", "bad-pack"]).json
    assert env["error"]["code"] == code, env
    assert not (satk_home / "work/out/mta/bad-pack").exists()
    assert asset.read_bytes() == before


@pytest.mark.parametrize("option,value", [(option, value) for option in ("--lod", "--budget-mb")
                                         for value in ("0", "-1", "nan", "inf")])
def test_pack_requires_finite_positive_limits(satk_home, run_cli, tmp_path, option, value):
    source = _asset(tmp_path / "model.dff")
    env = run_cli(["mta", "pack", str(source), "--kind", "object", "--new-id", option, value]).json
    assert env["error"]["code"] == "BAD_PARAMS", env
    assert not (satk_home / "work/out/mta/model").exists()


def test_pack_rejects_an_incompatible_parent(satk_home, run_cli, tmp_path):
    source = _asset(tmp_path / "model.dff")
    for args in (["--new-id", "--parent", "411"], ["--replace", "7", "--parent", "7"]):
        env = run_cli(["mta", "pack", str(source), "--kind", "skin", *args]).json
        assert env["error"]["code"] == "BAD_PARAMS", env


@pytest.mark.parametrize("kind,mid", [("vehicle", "7"), ("skin", "74"), ("object", "384"), ("object", "397")])
@pytest.mark.parametrize("new_id", [False, True])
def test_pack_rejects_ids_unusable_for_the_kind(satk_home, run_cli, tmp_path, kind, mid, new_id):
    source = _asset(tmp_path / "model.dff")
    args = ["--new-id", "--parent", mid] if new_id else ["--replace", mid]
    env = run_cli(["mta", "pack", str(source), "--kind", kind, *args]).json
    assert env["error"]["code"] == "BAD_PARAMS", env
    assert not (satk_home / "work/out/mta/model").exists()


def test_incompatible_filename_uses_default_parent_without_a_same_name_note(satk_home, run_cli, tmp_path):
    source = _asset(tmp_path / "411.dff")
    env = run_cli(["mta", "pack", str(source), "--kind", "object", "--new-id"]).json
    assert env["ok"] and env["models"] == [{"name": "411", "parent": 1337, "lod": 300.0}], env


def test_non_ascii_asset_names_become_portable_resource_paths(satk_home, run_cli, tmp_path):
    source = _asset(tmp_path / "model-\u00e9.dff")
    env = run_cli(["mta", "pack", str(source), "--kind", "object", "--new-id", "--name", "portable"]).json
    assert env["ok"] and env["lint"]["clean"], env
    root = Path(env["dir"])
    rel = ET.parse(root / "meta.xml").getroot().find("file").get("src")
    assert rel.isascii() and (root / rel).read_bytes() == source.read_bytes()
