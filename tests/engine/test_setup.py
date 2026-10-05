"""satk.engine.setup: templates, dependency manifest, fetch/pin logic (synthetic files, no network)."""

from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

from satk.core.errors import SatkError
from satk.engine import setup as S
from satk.engine.common import layout


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# --------------------------------------------------------------------------- templates


def test_templates_present_and_sane():
    names = {p.name for p in S.TEMPLATES.iterdir()}
    assert {"afxres.h", "Directory.Build.targets", "satk-premake.lua", "bootstrap.ps1"} <= names
    afx = (S.TEMPLATES / "afxres.h").read_text(encoding="utf-8")
    assert "#include <winres.h>" in afx and "IDC_STATIC (-1)" in afx and "#pragma once" in afx
    root = ET.parse(S.TEMPLATES / "Directory.Build.targets").getroot()
    ns = "{http://schemas.microsoft.com/developer/msbuild/2003}"
    idg = root.find(f"{ns}ItemDefinitionGroup")
    assert idg is not None and "SatkIsFork" in idg.get("Condition", "")
    inc = idg.find(f"{ns}ResourceCompile/{ns}AdditionalIncludeDirectories").text
    assert inc.startswith("$(SatkShimDir);") and inc.endswith("%(AdditionalIncludeDirectories)")
    lua = (S.TEMPLATES / "satk-premake.lua").read_text(encoding="utf-8")
    assert "SATK_OFFLINE" in lua and "http.download = function" in lua and 'include(path.join(FORK_DIR, "premake5.lua"))' in lua
    ps1 = (S.TEMPLATES / "bootstrap.ps1").read_bytes()
    assert all(b < 128 for b in ps1), "bootstrap.ps1 must stay ASCII (Windows PowerShell 5.1)"


def test_template_line_endings_independent_of_checkout(satk_home, tmp_path, monkeypatch):
    ps1 = S.template_bytes("bootstrap.ps1")
    assert b"\r\n" in ps1 and b"\n" not in ps1.replace(b"\r\n", b"")
    assert b"\r\n" not in S.template_bytes("satk-premake.lua")
    lf = {n: S.template_bytes(n) for n in ("afxres.h", "Directory.Build.targets", "satk-premake.lua", "bootstrap.ps1")}
    crlf_dir = tmp_path / "tpl"
    crlf_dir.mkdir()
    for n in lf:  # what a core.autocrlf=true checkout would produce
        (crlf_dir / n).write_bytes((S.TEMPLATES / n).read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    monkeypatch.setattr(S, "TEMPLATES", crlf_dir)
    assert {n: S.template_bytes(n) for n in lf} == lf


def test_write_templates_idempotent(satk_home):
    L = layout()
    rows = S.write_templates(L)
    assert {r["status"] for r in rows} == {"written"}
    assert L.afxres.is_file() and L.targets.is_file() and L.wrapper.is_file() and L.bootstrap.is_file()
    assert {r["status"] for r in S.write_templates(L)} == {"ok"}
    L.afxres.write_text("broken", encoding="utf-8")
    st = {t["name"]: t["status"] for t in S.template_status(L)}
    assert st["afxres.h"] == "differs" and st["bootstrap.ps1"] == "ok"
    rows = {r["name"]: r["status"] for r in S.write_templates(L)}
    assert rows["afxres.h"] == "updated"


# --------------------------------------------------------------------------- manifest

_CEF = '''local CEF_URL_PREFIX = "https://cef.example/cef_binary_"
local CEF_URL_SUFFIX = "_windows32_minimal.tar.bz2"
local CEF_VERSION = "1.2.3+g0+chromium-1"
local CEF_HASH = "{cef}"
if os.getenv("MTA_MAETRO") == "true" then
	CEF_VERSION = "0.0.1"
	CEF_HASH = "deadbeef"
end
'''
_DIS = '''local DISCORD_URL = "https://gh.example/discord-rpc/archive/refs/tags/"
local DISCORD_EXT = ".zip"
local RAPID_URL = "https://gh.example/rapidjson/archive/refs/tags/"
local RAPID_EXT = ".zip"
local DISCORD_VERSION = "v3.4.3"
local DISCORD_HASH = "{discord}"
local RAPID_VERSION = "v1.1.1"
local RAPID_HASH = "{rapid}"
'''
_UNI = '''local UNIFONT_BASEURL = "https://gh.example/unifont/releases/download/"
local UNIFONT_DOWNLOAD_FILENAME = "unifont-16.0.04.ttf"
local UNIFONT_TAG = "v16.0.04"
local UNIFONT_HASH = "{unifont}"
'''
_DAT = '''local BASE_URL = "https://cdn.example/bdata/"
local NET_PATH_X86_WIN   = BASE_URL .. "net.dll"
local NET_PATH_X64_WIN   = BASE_URL .. "net_64.dll"
local NET_PATH_ARM64_WIN = BASE_URL .. "net_arm64.dll"
local NETC_PATH_WIN      = BASE_URL .. "netc.dll"
'''

CONTENT = {
    "dxfiles": None,  # a real zip, built in the fixture
    "cef": b"fake cef archive",
    "discord-rpc": b"fake discord zip",
    "rapidjson": b"fake rapidjson zip",
    "unifont": b"fake font",
    "net-x86": b"net x86",
    "net-x64": b"net x64",
    "net-arm64": b"net arm64",
    "netc": b"netc",
}
REUSE = {
    "dxfiles": "utils/DXFiles.zip",
    "cef": "vendor/cef3/temp.tar.bz2",
    "discord-rpc": "vendor/discord-rpc/discord-rpc.zip",
    "rapidjson": "vendor/discord-rpc/rapidjson.zip",
    "unifont": "Shared/data/MTA San Andreas/MTA/cgui/unifont.ttf",
    "net-x86": "Bin/server/net.dll",
    "net-x64": "Bin/server/x64/net.dll",
    "net-arm64": "Bin/server/arm64/net.dll",
    "netc": "Bin/MTA/netc.dll",
}


def _dx_zip() -> bytes:
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Include/d3dx9.h", "// d3dx9\n")
        z.writestr("Lib/x86/d3dx9.lib", "lib")
    return buf.getvalue()


@pytest.fixture
def deps_env(satk_home, tmp_path, monkeypatch):
    L = layout()
    content = dict(CONTENT, dxfiles=_dx_zip())
    ba = L.fork / "utils" / "buildactions"
    ba.mkdir(parents=True)
    (ba / "install_cef.lua").write_text(_CEF.format(cef=_sha(content["cef"]).upper()), encoding="utf-8")
    (ba / "install_discord.lua").write_text(_DIS.format(discord=_sha(content["discord-rpc"]),
                                                        rapid=_sha(content["rapidjson"])), encoding="utf-8")
    (ba / "install_unifont.lua").write_text(_UNI.format(unifont=_sha(content["unifont"])), encoding="utf-8")
    (ba / "install_data.lua").write_text(_DAT, encoding="utf-8")
    monkeypatch.setattr(S, "DXFILES_SHA256", _sha(content["dxfiles"]))
    spike = tmp_path / "spike"
    for dep_id, rel in REUSE.items():
        f = spike / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(content[dep_id])
    downloads: list[str] = []
    monkeypatch.setattr(S, "_download", lambda url, dst, timeout=120: downloads.append(url) or 0)
    return L, spike, content, downloads


def test_manifest_reads_upstream_pins(deps_env):
    L, _, content, _ = deps_env
    deps = {d.id: d for d in S.manifest(L)}
    assert list(deps) == ["dxfiles", "cef", "discord-rpc", "rapidjson", "unifont", "net-x86", "net-x64",
                          "net-arm64", "netc"]
    cef = deps["cef"]
    assert cef.url == "https://cef.example/cef_binary_1.2.3+g0+chromium-1_windows32_minimal.tar.bz2"
    assert cef.sha256 == _sha(content["cef"])  # lower-cased; the MAETRO override is ignored
    assert cef.name == "cef_binary_1.2.3+g0+chromium-1_windows32_minimal.tar.bz2" and cef.pin == "upstream"
    assert deps["discord-rpc"].url == "https://gh.example/discord-rpc/archive/refs/tags/v3.4.3.zip"
    assert deps["unifont"].url == "https://gh.example/unifont/releases/download/v16.0.04/unifont-16.0.04.ttf"
    assert deps["net-x64"].url == "https://cdn.example/bdata/net_64.dll" and deps["net-x64"].sha256 is None
    assert deps["net-x64"].pin == "tofu" and deps["net-x64"].store == "net"
    assert deps["dxfiles"].pin == "satk"


def test_manifest_missing_actions(satk_home):
    with pytest.raises(SatkError) as e:
        S.manifest(layout())
    assert e.value.code == "NOT_READY"


def test_fetch_reuses_and_pins(deps_env):
    L, spike, content, downloads = deps_env
    rows = S.fetch_deps(L, allow_download=False, reuse_roots=[spike])
    assert {r["status"] for r in rows} == {"reused"} and downloads == []
    lock = json.loads(L.lock.read_text(encoding="utf-8"))
    items = {it["id"]: it for it in lock["items"]}
    assert len(items) == 9
    assert items["cef"]["file"].startswith("cache/cef_binary_")
    net = items["net-x64"]
    assert net["file"] == f"net/{_sha(content['net-x64'])}/net_64.dll" and net["pin"] == "tofu"
    assert (L.deps / net["file"]).read_bytes() == content["net-x64"]
    assert items["dxfiles"]["sha256"] == _sha(content["dxfiles"])
    # second run: everything already in the store
    rows = S.fetch_deps(L, allow_download=False, reuse_roots=[])
    assert {r["status"] for r in rows} == {"ok"}


def test_fetch_wrong_reuse_copy_is_skipped(deps_env):
    L, spike, _, downloads = deps_env
    (spike / REUSE["cef"]).write_bytes(b"tampered")
    rows = {r["id"]: r for r in S.fetch_deps(L, allow_download=False, reuse_roots=[spike])}
    assert rows["cef"]["status"] == "missing" and downloads == []


def test_fetch_download_checks_hash(deps_env, monkeypatch):
    L, spike, content, _ = deps_env

    def fake_download(url, dst, timeout=120):
        data = b"tampered" if "cef" in url else content["unifont"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        return len(data)

    monkeypatch.setattr(S, "_download", fake_download)
    with pytest.raises(SatkError) as e:
        S.fetch_deps(L, allow_download=True, reuse_roots=[], only=["cef"])
    assert e.value.code == "EXTERNAL_TOOL" and "sha256" in e.value.msg
    assert not list(L.cache.glob("*.download"))
    rows = S.fetch_deps(L, allow_download=True, reuse_roots=[], only=["unifont"])
    assert rows[0]["status"] == "downloaded" and rows[0]["source"] == "download"


def test_tofu_pin_protects_against_cdn_change(deps_env, monkeypatch):
    L, spike, content, _ = deps_env
    S.fetch_deps(L, allow_download=False, reuse_roots=[spike], only=["netc"])
    pinned = json.loads(L.lock.read_text(encoding="utf-8"))["items"][0]["sha256"]
    # the store copy disappears and the CDN now serves something else
    for p in L.net.rglob("netc.dll"):
        p.unlink()

    def cdn(url, dst, timeout=120):
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"new netc from cdn")
        return 1

    monkeypatch.setattr(S, "_download", cdn)
    with pytest.raises(SatkError) as e:
        S.fetch_deps(L, allow_download=True, reuse_roots=[], only=["netc"])
    assert e.value.code == "REVISION"
    rows = S.fetch_deps(L, allow_download=True, reuse_roots=[], only=["netc"], update_pins=True)
    assert rows[0]["sha256"] == _sha(b"new netc from cdn") != pinned


def test_update_pins_refetches_tofu_even_if_stored(deps_env, monkeypatch):
    L, spike, content, _ = deps_env
    S.fetch_deps(L, reuse_roots=[spike], only=["netc", "cef"])
    calls = []

    def cdn(url, dst, timeout=120):
        calls.append(url)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"netc v2")
        return 1

    monkeypatch.setattr(S, "_download", cdn)
    rows = {r["id"]: r for r in S.fetch_deps(L, allow_download=True, reuse_roots=[spike], update_pins=True,
                                             only=["netc", "cef"])}
    assert calls == ["https://cdn.example/bdata/netc.dll"]  # upstream-pinned CEF is not re-downloaded
    assert rows["netc"]["status"] == "downloaded" and rows["netc"]["sha256"] == _sha(b"netc v2")
    assert rows["cef"]["status"] == "ok"


def test_fetch_unknown_id(deps_env):
    L, *_ = deps_env
    with pytest.raises(SatkError) as e:
        S.fetch_deps(L, only=["nope"])
    assert e.value.code == "BAD_PARAMS"


def test_extract_dxfiles(deps_env):
    L, spike, content, _ = deps_env
    S.fetch_deps(L, reuse_roots=[spike], only=["dxfiles"])
    sha = _sha(content["dxfiles"])
    assert S._extract_dxfiles(L, L.cache / "DXFiles.zip", sha) == "extracted"
    assert (L.dxfiles / "Include" / "d3dx9.h").is_file()
    assert S._extract_dxfiles(L, L.cache / "DXFiles.zip", sha) == "ok"


def test_extract_rejects_path_traversal(satk_home, tmp_path):
    L = layout()
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("../evil.txt", "x")
    with pytest.raises(SatkError):
        S._extract_dxfiles(L, bad, "0" * 64)
    assert not (L.deps / "evil.txt").exists()


def test_install_requires_fetch(deps_env):
    L, *_ = deps_env
    with pytest.raises(SatkError) as e:
        S.install_deps(L)
    assert e.value.code == "NOT_READY"


def test_verify_installed_reports(deps_env):
    L, spike, content, _ = deps_env
    S.fetch_deps(L, reuse_roots=[spike])
    v = {x["id"]: x["status"] for x in S.verify_installed(L)}
    assert v["cef"] == "missing" and v["net-x64"] == "missing"
    p = L.bin / "server" / "x64" / "net.dll"
    p.parent.mkdir(parents=True)
    p.write_bytes(b"other")
    assert {x["id"]: x["status"] for x in S.verify_installed(L)}["net-x64"] == "mismatch"
    p.write_bytes(content["net-x64"])
    assert {x["id"]: x["status"] for x in S.verify_installed(L)}["net-x64"] == "ok"


def test_ensure_fork_refuses_non_empty_dir(satk_home):
    L = layout()
    L.fork.mkdir(parents=True)
    (L.fork / "something.txt").write_text("x", encoding="utf-8")
    with pytest.raises(SatkError) as e:
        S.ensure_fork(L)
    assert e.value.code == "EXISTS"


def test_ensure_fork_needs_donor(satk_home):
    with pytest.raises(SatkError) as e:
        S.ensure_fork(layout())
    assert e.value.code == "NOT_READY"


def test_fork_info_without_checkout(satk_home):
    info = S.fork_info(layout())
    assert info["exists"] is False


def test_ensure_fork_offline_from_local_donor(satk_home, tmp_path):
    """End to end on a synthetic donor: blobless file:// clone, remotes, main from upstream ref."""
    import subprocess

    L = layout()
    donor = L.donor
    donor.mkdir(parents=True)

    def g(*a, cwd=donor):
        return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()

    g("init", "-q", "-b", "master")
    g("config", "user.email", "t@example.invalid")
    g("config", "user.name", "t")
    (donor / "premake5.lua").write_text("-- upstream\n", encoding="utf-8")
    g("add", "-A")
    g("commit", "-q", "-m", "upstream")
    up = g("rev-parse", "HEAD")
    g("update-ref", "refs/remotes/upstream/master", up)
    (donor / "neon.txt").write_text("neon\n", encoding="utf-8")
    g("add", "-A")
    g("commit", "-q", "-m", "neon")
    info = S.ensure_fork(L)
    assert info["created"] and info["branch"] == "main" and info["head"] == up[:9]
    assert info["remotes"]["neon"]["push"] == "DISABLED" and info["remotes"]["upstream"]["push"] == "DISABLED"
    assert info["upstream_fetched"] is False and info["rerere"] is True
    assert (L.fork / "premake5.lua").is_file() and not (L.fork / "neon.txt").exists()
    assert g("rev-parse", "--verify", "lab/neon", cwd=L.fork)
    assert g("config", "remote.upstream.skipFetchAll", cwd=L.fork) == "true"
    again = S.ensure_fork(L)
    assert again["created"] is False and again["steps"] == []
    assert g("status", "--porcelain") == ""  # donor untouched


def test_store_paths():
    from satk.engine.common import Layout

    d = S.Dep("netc", "netc.dll", "u", None, "tofu", "net", "runtime")
    lay = Layout(fork=Path("X:/e/mtasa"), root=Path("X:/e"), donor=Path("X:/s"), build_dir=Path("X:/w"))
    p, rel = S._store_path(lay, d, "ab" * 32)
    assert rel == f"net/{'ab' * 32}/netc.dll" and p == Path("X:/e/deps") / rel
    d2 = S.Dep("cef", "cef.tar.bz2", "u", "x", "upstream", "cache", "build")
    assert S._store_path(lay, d2, "x")[1] == "cache/cef.tar.bz2"
