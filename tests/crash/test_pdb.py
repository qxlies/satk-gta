"""PDB routing and identity checks with synthetic images and a mocked native symbol reader."""

from __future__ import annotations

import os
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest
from crash_helpers import STACK, make_dll, write

from satk.crash import analyze, pdb
from satk.crash.images import Images, PeFile
from satk.crash.minidump import Module
from satk.crash.stack import Frame
from satk.crash.synth import DumpBuilder

GUID = struct.pack("<IHH", 0x12345678, 0x9ABC, 0xDEF0) + bytes(range(8))
PDB_ID = "123456789ABCDEF000010203040506071"


def _image(path: Path, *, symbols: bool = True, age: int = 1) -> PeFile:
    path.parent.mkdir(parents=True, exist_ok=True)
    make_dll(path, 0x10000000, {"ExportOnly": 0x1000}, {})
    if symbols:
        raw = bytearray(path.read_bytes())
        cv = b"RSDS" + GUID + struct.pack("<I", age) + path.with_suffix(".pdb").name.encode() + b"\0"
        struct.pack_into("<II", raw, 0x98 + 96 + 6 * 8, 0x300, 28)
        struct.pack_into("<IIHHIIII", raw, 0x300, 0, 0, 0, 0, 2, len(cv), 0, len(raw))
        raw += cv
        path.write_bytes(raw)
        # Invented placeholder, never passed to DbgHelp: only the reader is mocked.
        path.with_suffix(".pdb").write_bytes(b"mock PDB")
    pe = PeFile.load(path)
    assert pe is not None
    return pe


@pytest.fixture
def native(monkeypatch):
    state = SimpleNamespace(opened=[], closed=[], lookups=[], ids={}, fail=set(), fail_lookup=False)

    class Session:
        def __init__(self, path, size):
            state.opened.append((path, size))
            if path in state.fail:
                raise OSError("cannot read symbols")
            self.path = path
            self.pdb_id = state.ids.get(path, PDB_ID)
            self.is_pdb = True

        def resolve(self, off, *, ret=False):
            state.lookups.append((self.path, off, ret))
            if state.fail_lookup:
                raise OSError("symbol lookup failed")
            return {"fn": f"{self.path.stem}::Tick+0x{off - 0x1000:x}", "src": "Client/Test.cpp:42", "via": "pdb"}

        def close(self):
            state.closed.append(self.path)

    monkeypatch.setattr(pdb, "_Session", Session)
    return state


@pytest.mark.parametrize("base, arch", [(0x62000000, "x86"), (0x180000000, "amd64")])
def test_mixed_dump_routes_pdb_gta_and_exports(synth, tmp_path, monkeypatch, native, base, arch):
    client = _image(tmp_path / "Bin/mods/deathmatch/client.dll")
    core = _image(tmp_path / "Bin/mta/core.dll")
    netc = _image(tmp_path / "Bin/mta/netc.dll", symbols=False)
    b = DumpBuilder(arch)
    b.add_module("missing/client.dll", base, client.size_of_image, timestamp=client.timestamp, pdb="client.pdb")
    b.add_module("missing/core.dll", base + 0x10000, core.size_of_image, timestamp=core.timestamp, pdb="core.pdb")
    b.add_module("missing/netc.dll", base + 0x20000, netc.size_of_image, timestamp=netc.timestamp)
    b.add_module(str(synth.exe), 0x400000, 0x6000, timestamp=0x427101CA)
    ctx = {"eip": base + 0x1014, "esp": STACK} if arch == "x86" else {"rip": base + 0x1014, "rsp": STACK}
    b.add_thread(1, ctx, STACK, bytes(32))
    b.set_exception(1, 0xC0000005, base + 0x1014)
    frames = [Frame(base + 0x1014, "ip"), Frame(0x401325, "ebp"), Frame(base + 0x11010, "scan"),
              Frame(base + 0x21014, "ebp"), Frame(base + 0x1018, "scan")]
    monkeypatch.setattr(analyze, "walk", lambda *a, **k: (frames, {}))
    env = analyze.analyze_file(str(write(tmp_path, "crash.dmp", b.build())), images=[str(tmp_path / "Bin")])
    rows = [dict(zip(env["cols"], row)) for row in env["rows"]]
    assert rows[0]["fn"] == "client::Tick+0x14" and rows[0]["via"] == "ip/pdb"
    assert rows[0]["src"] == "Client/Test.cpp:42" and rows[0]["at"] == "client.dll+0x1014"
    assert rows[1]["fn"] == "sub_401320+0x5" and "pdb" not in rows[1]["via"]
    assert rows[2]["fn"] == "core::Tick+0x10" and rows[2]["via"] == "scan?/pdb"
    assert rows[3]["fn"] == "ExportOnly+0x14" and rows[3]["src"] == "export"
    assert rows[4]["fn"] == "client::Tick+0x18"
    assert native.lookups == [(client.path.with_suffix(".pdb"), 0x1014, False),
                              (core.path.with_suffix(".pdb"), 0x1010, True),
                              (client.path.with_suffix(".pdb"), 0x1018, True)]
    assert len(native.opened) == 2 and len(native.closed) == 2  # cached per module, then released


@pytest.mark.parametrize("text", [
    "Module = client.dll\nOffset = 00001014\nStack Trace:\n  #0 client.dll+0x1014\n  #1 client.dll+0x1018\n",
    "client.dll+0x1014\n",
    "Exception At Address: 0x60001014\nBacktrace:\n  client.dll+0x1014\n",
])
def test_text_frames_use_images_without_a_load_address(satk_home, tmp_path, native, text):
    _image(tmp_path / "images/client.dll")
    env = analyze.analyze_file(str(write(tmp_path, "crash.log", text)), images=[str(tmp_path / "images")])
    assert any(row[3] == "client::Tick+0x14" and row[6].endswith("/pdb") for row in env["rows"])
    assert len(native.opened) == 1 and len(native.closed) == 1


def test_configured_bin_finds_client_and_mta_modules(satk_home, native):
    from satk.engine.common import layout

    for rel in ("mods/deathmatch/client.dll", "mta/game_sa.dll", "mta/multiplayer_sa.dll"):
        pe = _image(layout().bin / rel)
        with Images(None, game_exes=[]) as images:
            mod = images.named_module(pe.path.name)
            assert mod is not None and images.pdb_symbol(mod, 0x1014)["via"] == "pdb"
    assert len(native.opened) == len(native.closed) == 3


@pytest.mark.parametrize("actual_id", [PDB_ID[:-1] + "2", "FFFFFFFF" + PDB_ID[8:]])
def test_wrong_pdb_signature_or_age_warns_and_keeps_export(satk_home, tmp_path, native, actual_id):
    pe = _image(tmp_path / "client.dll")
    native.ids[pe.path.with_suffix(".pdb")] = actual_id
    with Images(None, dirs=[tmp_path], game_exes=[]) as images:
        sym = analyze.Symbols(images)
        got = sym.describe(None, "client.dll", 0x1014)
        assert got["fn"] == "ExportOnly+0x14" and got["src"] == "export" and "via" not in got
        assert any(w.startswith("REVISION:") and "GUID/signature + age" in w for w in sym.warn)
        sym.describe(None, "client.dll", 0x1018)
    assert not native.lookups and len(native.opened) == len(native.closed) == 1


def test_dump_identity_overrules_same_timestamp_image(satk_home, tmp_path, native):
    pe = _image(tmp_path / "client.dll", age=2)
    native.ids[pe.path.with_suffix(".pdb")] = PDB_ID[:-1] + "2"
    mod = Module(0x62000000, pe.size_of_image, str(pe.path), pe.timestamp, pdb_id=PDB_ID)
    with Images(None, game_exes=[]) as images:
        assert images.pdb_symbol(mod, 0x1014) == {}
        assert any("does not match the dump" in w for w in images.warn)
    assert native.lookups == [] and native.closed == [pe.path.with_suffix(".pdb")]


def test_dump_can_verify_an_archived_pdb_without_the_image(satk_home, tmp_path, native):
    symbols = tmp_path / "symbols"
    symbols.mkdir()
    (symbols / "client.pdb").write_bytes(b"mock PDB")
    b = DumpBuilder("x86")
    b.add_module("missing/client.dll", 0x62000000, 0x3000, timestamp=42, pdb="client.pdb")
    b.add_thread(1, {"eip": 0x62001014, "esp": STACK}, STACK, bytes(32))
    b.set_exception(1, 0xC0000005, 0x62001014)
    env = analyze.analyze_file(str(write(tmp_path, "crash.dmp", b.build())), images=[str(symbols)])
    assert env["rows"][0][3] == "client::Tick+0x14" and env["rows"][0][6] == "ip/pdb"
    assert native.opened == [(symbols / "client.pdb", 0x3000)]
    assert native.closed == [symbols / "client.pdb"]


def test_rebuilt_image_and_pdb_warn_against_the_dump(satk_home, tmp_path, native):
    pe = _image(tmp_path / "client.dll", age=2)
    native.ids[pe.path.with_suffix(".pdb")] = PDB_ID[:-1] + "2"
    mod = Module(0x62000000, pe.size_of_image, str(pe.path), pe.timestamp - 1, pdb="client.pdb", pdb_id=PDB_ID)
    with Images(None, game_exes=[]) as images:
        assert images.pdb_symbol(mod, 0x1014) == {}
        assert any(w.startswith("REVISION:") and "expected " + PDB_ID in w for w in images.warn)
    assert not native.lookups and len(native.closed) == 1


def test_dump_identity_selects_matching_image_after_stale_recorded_path(satk_home, tmp_path, native):
    old = _image(tmp_path / "old/client.dll", age=2)
    good = _image(tmp_path / "saved/client.dll")
    mod = Module(0x62000000, old.size_of_image, str(old.path), old.timestamp, pdb_id=PDB_ID)
    with Images(None, dirs=[good.path.parent], game_exes=[]) as images:
        assert images.pdb_symbol(mod, 0x1014)["via"] == "pdb"
        assert images.used["client.dll"] == good.path.as_posix()
        assert any("image ignored" in warning for warning in images.warn)
    assert native.lookups[0][0] == good.path.with_suffix(".pdb")


def test_mismatched_sidecar_can_fall_back_to_matching_images_pdb(satk_home, tmp_path, native):
    pe = _image(tmp_path / "old/client.dll")
    other = tmp_path / "symbols/client.pdb"
    other.parent.mkdir()
    other.write_bytes(b"mock matching PDB")
    native.ids[pe.path.with_suffix(".pdb")] = PDB_ID[:-1] + "2"
    mod = Module(0x62000000, pe.size_of_image, str(pe.path), pe.timestamp)
    with Images(None, dirs=[other.parent], game_exes=[]) as images:
        assert images.pdb_symbol(mod, 0x1014)["via"] == "pdb"
        assert native.lookups[0][0] == other
        assert any(w.startswith("REVISION:") for w in images.warn)
    assert len(native.closed) == 2


def test_missing_image_or_pdb_does_not_start_dbghelp(satk_home, tmp_path, native):
    pe = _image(tmp_path / "netc.dll", symbols=False)
    with Images(None, dirs=[tmp_path], game_exes=[]) as images:
        sym = analyze.Symbols(images)
        assert "fn" not in sym.describe(None, "absent.dll", 0x1014)
        assert sym.describe(None, "netc.dll", 0x1014)["src"] == "export"
        wrong = Module(0x62000000, pe.size_of_image, str(pe.path), pe.timestamp + 1)
        assert images.pdb_symbol(wrong, 0x1014) == {}
    assert not native.opened


def test_unusable_dbghelp_leaves_report_and_warning(satk_home, tmp_path, native):
    pe = _image(tmp_path / "client.dll")
    native.fail.add(pe.path.with_suffix(".pdb"))
    env = analyze.analyze_file(str(write(tmp_path, "log.txt", "client.dll+0x1014")), images=[str(tmp_path)])
    assert env["rows"][0][3] == "ExportOnly+0x14" and env["rows"][0][4] == "export"
    assert any(w.startswith("EXTERNAL_TOOL:") for w in env["warn"])


def test_lookup_error_releases_session_and_preserves_export(satk_home, tmp_path, native):
    pe = _image(tmp_path / "client.dll")
    native.fail_lookup = True
    with Images(None, dirs=[tmp_path], game_exes=[]) as images:
        sym = analyze.Symbols(images)
        assert sym.describe(None, "client.dll", 0x1014)["src"] == "export"
        assert sym.describe(None, "client.dll", 0x1018)["src"] == "export"
        assert any("cannot resolve PDB symbols" in w for w in sym.warn)
    assert native.closed == [pe.path.with_suffix(".pdb")]
    assert len(native.lookups) == 1


def test_report_failure_still_closes_sessions(satk_home, tmp_path, native, monkeypatch):
    pe = _image(tmp_path / "client.dll")

    def fail(*args):
        raise RuntimeError("render failure")

    monkeypatch.setattr(analyze, "_fit", fail)
    with pytest.raises(RuntimeError, match="render failure"):
        analyze.analyze_file(str(write(tmp_path, "log.txt", "client.dll+0x1014")), images=[str(tmp_path)])
    assert native.closed == [pe.path.with_suffix(".pdb")]


def test_dbghelp_discovery_uses_configured_vs_and_system32(satk_home, tmp_path, monkeypatch):
    from satk.engine import common

    msbuild = tmp_path / "VS/MSBuild/Current/Bin/amd64/MSBuild.exe"
    monkeypatch.setattr(common, "find_msbuild", lambda: msbuild)
    monkeypatch.setenv("SystemRoot", str(tmp_path / "Windows"))
    got = pdb.dbghelp_paths()
    arch = "x64" if struct.calcsize("P") == 8 else "x86"
    assert got == [tmp_path / f"VS/Common7/IDE/CommonExtensions/Microsoft/TestWindow/VsTest/{arch}/dbghelp.dll",
                   tmp_path / "Windows/System32/dbghelp.dll"]
    monkeypatch.setattr(common, "find_msbuild", lambda: None)
    assert pdb.dbghelp_paths() == got[1:]


def test_image_codeview_and_legacy_signature_id(tmp_path):
    pe = _image(tmp_path / "client.dll")
    assert pe.codeview() == ("client.pdb", PDB_ID)
    assert pdb._pdb_id(bytes(16), 0x12345678, 10) == "12345678A"


@pytest.mark.engine
def test_real_client_pdb_at_known_export_rva(tmp_path):
    """InitClient's export gives a stable known RVA across builds; PDB must add its source line."""
    from satk.core import config
    from satk.engine.common import layout

    config.reset()
    try:
        path = layout().bin / "mods/deathmatch/client.dll"
        if os.name != "nt" or not path.is_file() or not path.with_suffix(".pdb").is_file():
            pytest.skip("Windows fork client.dll/client.pdb build not found")
        pe = PeFile.load(path)
        assert pe is not None
        rva = next(r for r, name in pe.exports() if name == "InitClient")
        # Only a synthetic dump header/context, no captured game bytes and no running process.
        base = 0x62000000
        b = DumpBuilder("x86")
        b.add_module(str(path), base, pe.size_of_image, timestamp=pe.timestamp)
        b.add_thread(1, {"eip": base + rva + 1, "esp": STACK}, STACK, bytes(32))
        b.set_exception(1, 0xC0000005, base + rva + 1)
        env = analyze.analyze_file(str(write(tmp_path, "fork.dmp", b.build())))
        row = dict(zip(env["cols"], env["rows"][0]))
        assert row["fn"] == "InitClient+0x1", env
        assert row["via"] == "ip/pdb" and "/Client.cpp:" in row["src"]
        assert int(row["src"].rsplit(":", 1)[1]) > 0
        assert not any(w.startswith(("REVISION:", "EXTERNAL_TOOL:")) for w in env.get("warn", []))
    finally:
        config.reset()
