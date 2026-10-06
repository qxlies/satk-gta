"""Resource metadata, scaffold escaping and archive validation without game assets."""

from __future__ import annotations

import struct
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

from satk.mta import luaparse as L
from satk.mta.resource import _rw_ok


@pytest.mark.parametrize("kind", ["script", "map", "shader", "vehicle-pack", "skin-pack", "object-pack"])
def test_scaffold_preserves_xml_attribute_text(satk_home, run_cli, kind):
    author = 'A & B <studio> "team"\nsecond line'
    env = run_cli(["mta", "resource", "new", "escaped", "--kind", kind, "--author", author]).json
    assert env["ok"] and env["lint"]["clean"], env
    meta = ET.parse(Path(env["dir"]) / "meta.xml").getroot()
    assert meta.find("info").get("author") == author


def test_shader_texture_is_a_literal_and_round_trips(satk_home, run_cli):
    texture = 'name"\\tail\nsecond & <line>'
    env = run_cli(["mta", "resource", "new", "quoted", "--kind", "shader", "--texture", texture]).json
    assert env["ok"] and env["lint"]["clean"], env
    root = Path(env["dir"])
    chunk = L.parse((root / "client.lua").read_text(encoding="utf-8"))
    calls = [n for n in L.walk(chunk.body) if isinstance(n, L.Call) and isinstance(n.fn, L.Name)
             and n.fn.name == "engineApplyShaderToWorldTexture"]
    assert len(calls) == 1 and calls[0].args[1].value == texture
    assert texture in ET.parse(root / "meta.xml").getroot().find("info").get("description")


@pytest.mark.parametrize("args", [["--author", "bad\x01text"], ["--kind", "map", "--pos", "1", "2"],
                                  ["--kind", "map", "--pos", "0", "nan", "0"]])
def test_invalid_scaffold_values_fail_before_writing(satk_home, run_cli, args):
    env = run_cli(["mta", "resource", "new", "bad", *args]).json
    assert env["error"]["code"] == "BAD_PARAMS", env
    assert not (satk_home / "work/out/mta/bad").exists()


@pytest.mark.parametrize("name", ["CON", "lpt1", "bad name", "bad\n"])
def test_resource_names_are_portable(satk_home, run_cli, name):
    env = run_cli(["mta", "resource", "new", name]).json
    assert env["error"]["code"] == "BAD_PARAMS", env


def test_scaffold_output_must_be_a_directory(satk_home, run_cli, tmp_path):
    output = tmp_path / "existing.txt"
    output.write_text("keep", encoding="utf-8")
    env = run_cli(["mta", "resource", "new", "demo", "--out", str(output), "--force"]).json
    assert env["error"]["code"] == "BAD_PARAMS" and output.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize("top_size,child_size,payload", [(12, 0xFFFFFFF4, b""), (12, 100, b""),
                                                         (13, 0, b"x"), (100, 88, b"short")])
def test_bad_rw_lengths_finish_and_are_rejected(tmp_path, top_size, child_size, payload):
    path = tmp_path / "bad.dff"
    path.write_bytes(struct.pack("<III", 0x10, top_size, 0x1803FFFF)
                     + struct.pack("<III", 1, child_size, 0x1803FFFF) + payload)
    assert _rw_ok(path) is False


@pytest.mark.parametrize("entry", ["../escape.lua", "sub/file:stream", "sub/CON.lua", "sub/file. ",
                                   "/absolute.lua", "sub/../../escape.lua"])
def test_zip_rejects_unsafe_names_before_extracting(satk_home, run_cli, tmp_path, entry):
    path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(".done", "")
        archive.writestr("ok.lua", "return 1")
        archive.writestr(entry, "return 2")
    # The first failure must not leave an archive-supplied completion marker that bypasses the next check.
    for _ in range(2):
        env = run_cli(["mta", "lint", str(path), "--ref", "none"]).json
        assert env["error"]["code"] == "BAD_PARAMS" and "unsafe" in env["error"]["msg"], env
    assert not list((satk_home / "work/tmp/mta-lint").rglob("ok.lua"))


@pytest.mark.parametrize("entries", [["Client.lua", "client.lua"], ["sub", "sub/client.lua"],
                                     ["sub/client.lua", "sub"]])
def test_zip_rejects_conflicting_entries(satk_home, run_cli, tmp_path, entries):
    path = tmp_path / "conflict.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for entry in entries:
            archive.writestr(entry, "return 1")
    env = run_cli(["mta", "lint", str(path), "--ref", "none"]).json
    assert env["error"]["code"] == "BAD_PARAMS" and "conflicting" in env["error"]["msg"], env


def test_corrupt_zip_returns_an_input_error(satk_home, run_cli, tmp_path):
    path = tmp_path / "corrupt.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("meta.xml", '<meta><script src="client.lua" type="client"/></meta>')
        archive.writestr("client.lua", "local a = 1")
    path.write_bytes(path.read_bytes().replace(b"local a = 1", b"local a = 2"))
    for _ in range(2):
        env = run_cli(["mta", "lint", str(path), "--ref", "none"]).json
        assert env["error"]["code"] == "BAD_PARAMS" and "CRC" in env["error"]["msg"], env


def test_empty_zip_reports_missing_meta(satk_home, run_cli, tmp_path):
    path = tmp_path / "empty.zip"
    with zipfile.ZipFile(path, "w"):
        pass
    env = run_cli(["mta", "lint", str(path), "--ref", "none"]).json
    assert env["ok"] and [row[2] for row in env["rows"]] == ["META_MISSING"], env
