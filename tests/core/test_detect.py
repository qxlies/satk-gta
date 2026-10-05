"""satk.core.detect: tools, game installs and executable variants (docs/en/install.md)."""

from __future__ import annotations

import json
import os
import struct
from pathlib import Path

import pytest

from satk.core import detect as D

VDF = r'''
"libraryfolders"
{
	"0"
	{
		"path"		"C:\\Program Files (x86)\\Steam"
		"apps"		{ "12120"		"4687" }
	}
	"1"
	{
		"path"		"E:\\SteamLibrary"
	}
}
'''


def test_parse_vdf():
    tree = D.parse_vdf(VDF)
    lf = tree["libraryfolders"]
    assert lf["0"]["path"] == r"C:\Program Files (x86)\Steam" and lf["1"]["path"] == r"E:\SteamLibrary"
    assert lf["0"]["apps"] == {"12120": "4687"}


def _pe(size: int, *, va_u32: dict[int, int] | None = None) -> bytes:
    """A PE image (base 0x400000, one section .text at VA 0x401000, raw 0x400) of ``size`` bytes."""
    b = bytearray(size)
    b[0:2] = b"MZ"
    struct.pack_into("<I", b, 0x3C, 0x80)
    b[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HH", b, 0x84, 0x14C, 1)
    struct.pack_into("<H", b, 0x84 + 16, 0xE0)  # SizeOfOptionalHeader
    opt = 0x98
    struct.pack_into("<HI", b, opt, 0x10B, 0)
    struct.pack_into("<I", b, opt + 28, 0x400000)  # ImageBase
    sec = opt + 0xE0
    b[sec:sec + 8] = b".text\0\0\0"
    struct.pack_into("<IIII", b, sec + 8, 0x500000, 0x1000, size - 0x400, 0x400)
    for va, v in (va_u32 or {}).items():
        struct.pack_into("<I", b, 0x400 + (va - 0x401000), v)
    return bytes(b)


def test_identify_exe_by_signature_size_and_unknown(tmp_path):
    hood = tmp_path / "hoodlum.exe"
    hood.write_bytes(_pe(0x500000, va_u32={0x401000: 0x16197BE9}))
    i = D.identify_exe(hood)
    assert (i["name"], i["layout"], i["supported"], i["match"], i["heuristic"]) == \
        ("hoodlum", "1.0us", True, "signature", True)
    eu = tmp_path / "eu.exe"
    eu.write_bytes(_pe(0x500000, va_u32={0x8245BC: 0x94BF}))
    i = D.identify_exe(eu)
    assert (i["variant"], i["supported"], i["match"]) == ("1.0 EU", False, "signature")
    sized = tmp_path / "compact.exe"
    sized.write_bytes(b"\0" * 5_189_632)  # no PE header: only the size speaks
    i = D.identify_exe(sized)
    assert (i["name"], i["match"], i["supported"]) == ("compact-1.0us", "size", True)
    junk = tmp_path / "junk.exe"
    junk.write_bytes(b"MZ junk")
    i = D.identify_exe(junk)
    assert (i["name"], i["variant"], i["supported"], i["match"]) == ("unknown", "unknown", False, "none")


def test_identify_exe_by_hash(tmp_path, monkeypatch):
    f = tmp_path / "x.exe"
    f.write_bytes(b"MZ exact")
    import hashlib

    sha = hashlib.sha256(b"MZ exact").hexdigest()
    db = {"variants": [{"sha256": sha, "size": 8, "name": "t", "variant": "T", "layout": "1.0us", "supported": True}]}
    monkeypatch.setattr(D, "_EXE_VERSIONS", db)
    i = D.identify_exe(f)
    assert (i["name"], i["match"], i["heuristic"], i["sha256"]) == ("t", "sha256", False, sha)


def test_exe_versions_data_is_verified_and_consistent():
    from satk.game import exe as X

    d = D.exe_versions()
    assert d["format"] == "satk.exe-versions/1"
    shas = [v["sha256"] for v in d["variants"]]
    assert len(shas) == len(set(shas))
    for v in d["variants"]:
        assert {"sha256", "size", "name", "variant", "supported", "source"} <= set(v), v
        assert len(v["sha256"]) == 64 and v["source"]
        # every verified hash is one satk.game.exe knows, under the same name
        assert X.KNOWN_EXE[v["sha256"]].name == v["name"]
    assert {v["name"]: v["size"] for v in d["variants"]}["hoodlum-stock"] == X.STOCK_SIZE
    for s in d["signatures"] + d["sizes"]:
        assert s["source"] and isinstance(s["supported"], bool)


def test_check_game_and_installs(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "_registry_candidates", lambda: [])
    monkeypatch.setattr(D, "steam_libraries", lambda: [])
    monkeypatch.setattr(D, "_TYPICAL", ())
    ws = tmp_path / "ws"
    good = ws / "GTA San Andreas"
    (good / "models").mkdir(parents=True)
    (good / "data").mkdir()
    (good / "gta_sa.exe").write_bytes(b"MZ x")
    (good / "models" / "gta3.img").write_bytes(b"VER2")
    (good / "data" / "gta.dat").write_text("x", encoding="utf-8")
    steam = tmp_path / "steamlike"
    steam.mkdir()
    (steam / "gta-sa.exe").write_bytes(b"MZ y")  # Steam executable name, nothing else
    assert D.check_game(good) == []
    assert D.check_game(steam) == ["no models/gta3.img", "no data/gta.dat"]
    found = D.game_installs(extra=[(good, "config"), (tmp_path / "nope", "config")], workspace=ws, cwd=steam)
    assert [(g.root, g.source, g.exe.name) for g in found] == [(good, "config", "gta_sa.exe"),
                                                                (steam, "cwd", "gta-sa.exe")]
    assert found[0].sources == ["config", "workspace"]  # de-duplicated, every source kept
    assert found[1].problems and found[0].as_dict()["variant"] == "unknown"


def test_tools_cache(tmp_path, monkeypatch):
    monkeypatch.delenv("SATK_DETECT", raising=False)
    exe = tmp_path / "blender.exe"
    exe.write_bytes(b"MZ")
    calls = []

    def finder(name, hit):
        def f():
            calls.append(name)
            return hit
        return f

    monkeypatch.setattr(D, "_FINDERS", {"blender": finder("blender", D.Hit(exe, "program_files")),
                                        "msbuild": finder("msbuild", D.Hit(None, None))})
    cache = tmp_path / "cache"
    a = D.tools(cache)
    assert a["blender"] == D.Hit(exe, "program_files") and a["msbuild"].path is None
    data = json.loads((cache / "detect.json").read_text(encoding="utf-8"))
    assert data["tools"]["blender"]["source"] == "program_files"
    assert D.tools(cache) == a and calls == ["blender", "msbuild"]  # second call: from the cache
    exe.unlink()  # a cached hit whose file vanished is detected again at once
    D.tools(cache)
    assert calls == ["blender", "msbuild", "blender"]
    old = (cache / "detect.json").stat().st_mtime - D.CACHE_TTL - 10
    os.utime(cache / "detect.json", (old, old))
    D.tools(cache)
    assert calls[-2:] == ["blender", "msbuild"]  # expired: everything again
    monkeypatch.setenv("SATK_DETECT", "nocache")
    n = len(calls)
    D.tools(cache, refresh=True)
    D.tools(cache)
    assert len(calls) == n + 2  # per process, nothing read from or written to the cache


@pytest.mark.skipif(os.name != "nt", reason="Windows discovery")
def test_this_machine_discovery_is_sane():
    t = D.tools(refresh=True)
    for name, hit in t.items():
        if hit.path is not None:
            assert hit.path.is_file() and hit.source in ("registry", "vswhere", "program_files", "path"), (name, hit)
    if t["msbuild"].path is not None:
        assert t["msbuild"].path.name.lower() == "msbuild.exe"
