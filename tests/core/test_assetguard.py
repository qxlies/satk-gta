"""Asset guard: game assets, images and reference code never reach git (SPEC §2.5)."""

from __future__ import annotations

import hashlib
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from satk.core import assetguard as G

DATA = Path(__file__).resolve().parent / "data"
FAKE_RW = DATA / "fake_rw.bin"


def _rw(t: int, libid: int = 0x1803FFFF, body: bytes = b"synthetic") -> bytes:
    return struct.pack("<III", t, len(body), libid) + body


def test_fake_rw_fixture_is_synthetic_rw_header():
    data = FAKE_RW.read_bytes()
    assert data[:4] == b"\x10\x00\x00\x00" and data[8:12] == b"\xff\xff\x03\x18"
    assert b"synthetic" in data and len(data) < 100
    assert G.sniff(data[:64]) == ("rw-chunk", "RenderWare clump chunk, RW 0x36003")


@pytest.mark.parametrize("magic", sorted(G.MAGICS))
def test_magics_rejected(tmp_path, magic):
    f = tmp_path / "blob.bin"
    f.write_bytes(magic + b"\x00" * 60)
    n, found = G.scan_files([f])
    assert n == 1 and [x.rule for x in found] == ["magic"]


@pytest.mark.parametrize("t", sorted(G.RW_TYPES))
def test_rw_chunks_rejected(t):
    assert G.check_blob("x.bin", _rw(t), 21)[0].rule == "rw-chunk"


@pytest.mark.parametrize("libid, ok", [(0x1803FFFF, True), (0x0C02FFFF, True), (0x00000310, True),
                                       (0x1FFFFFFF, True), (0xFFFFFFFF, False), (0x00000001, False),
                                       (0x2803FFFF, False)])
def test_libid_plausibility(libid, ok):
    assert G.plausible_libid(libid) is ok


def test_lookalike_text_is_fine():
    # 0x10 type followed by text: library ID implausible -> not an asset
    assert G.check_blob("notes.txt", b"\x10\x00\x00\x00hello world, plain text", 26) == []
    assert G.check_blob("src/satk/x.py", b'"""Docstring."""\nimport os\n', 30) == []


def test_images_only_in_docs_img():
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
    assert G.check_blob("docs/img/diagram.png", png, 28) == []
    assert G.check_blob("tools/docs/img/a.png", png, 28) == []
    assert [f.rule for f in G.check_blob("out/vent_64.png", png, 28)] == ["image"]
    assert [f.rule for f in G.check_blob("x.bin", b"\xff\xd8\xff\xe0" + b"\x00" * 8, 12)] == ["image"]
    assert [f.rule for f in G.check_blob("empty.jpg", b"", 0)] == ["image"]


def test_extension_path_and_size_rules():
    assert [f.rule for f in G.check_blob("tests/x/infernus.DFF", b"text", 4)] == ["asset-ext"]
    assert [f.rule for f in G.check_blob("vendor/gta-reversed/Game.cpp", b"int x;", 6)] == ["forbidden-path"]
    assert [f.rule for f in G.check_blob("ref/samp-source/a.cpp", b"int x;", 6)] == ["forbidden-path"]
    assert [f.rule for f in G.check_blob("big.bin", b"\x00", G.MAX_BYTES + 1)] == ["large-file"]


def test_allowlist(tmp_path):
    repo = tmp_path
    f = repo / "tests" / "core" / "data" / "fake.bin"
    f.parent.mkdir(parents=True)
    f.write_bytes(_rw(0x16))
    assert G.scan_files([f], repo=repo)[1]
    sha = hashlib.sha256(f.read_bytes()).hexdigest()
    (repo / G.ALLOWLIST_FILE).write_text(f"# comment\n{sha} tests/core/data/fake.bin\n", encoding="utf-8")
    assert G.load_allowlist(repo) == {"tests/core/data/fake.bin": {sha}}
    assert G.scan_files([f], repo=repo, use_allowlist=True)[1] == []
    assert G.scan_files([f], repo=repo, use_allowlist=False)[1]  # strict by default
    f.write_bytes(_rw(0x16, body=b"changed"))  # different content -> not allowed any more
    assert G.scan_files([f], repo=repo, use_allowlist=True)[1]


@pytest.mark.parametrize("candidate", ["unlisted", "matching", "mismatching"])
def test_allowlist_only_streams_matching_paths(tmp_path, monkeypatch, candidate):
    path = tmp_path / "large.bin"
    digest = hashlib.sha256()
    chunk = b"synthetic fixture\n" * 4096
    with path.open("wb") as f:
        for _ in range(G.MAX_BYTES // len(chunk) + 1):
            f.write(chunk)
            digest.update(chunk)
    rel = "elsewhere.bin" if candidate == "unlisted" else path.name
    sha = "0" * 64 if candidate == "mismatching" else digest.hexdigest()
    (tmp_path / G.ALLOWLIST_FILE).write_text(f"{sha} {rel}\n", encoding="utf-8")
    read_bytes = Path.read_bytes

    def no_whole_file_read(p):
        assert p != path, "assetguard must not allocate an entire rejected file"
        return read_bytes(p)

    monkeypatch.setattr(Path, "read_bytes", no_whole_file_read)
    real_open = open
    reads = []

    class Watched:
        def __init__(self, file):
            self.file = file

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.file.__exit__(*args)

        def read(self, n=-1):
            assert 0 < n <= 1 << 20, "reads must be bounded"
            data = self.file.read(n)
            reads.append(len(data))
            return data

    def watched_open(p, *args, **kwargs):
        f = real_open(p, *args, **kwargs)
        return Watched(f) if Path(p) == path else f

    monkeypatch.setattr(G, "open", watched_open, raising=False)
    n, found = G.scan_files([path], repo=tmp_path, use_allowlist=True)
    assert n == 1
    assert [f.rule for f in found] == ([] if candidate == "matching" else ["large-file"])
    if candidate == "unlisted":
        assert sum(reads) <= 64
    else:
        assert path.stat().st_size <= sum(reads) <= path.stat().st_size + 64


def test_repo_allowlist_covers_fixture(repo_root):
    allow = G.load_allowlist(repo_root)
    sha = hashlib.sha256(FAKE_RW.read_bytes()).hexdigest()
    assert sha in allow.get("tests/core/data/fake_rw.bin", set())


def test_directory_scan_skips_venv_and_git(tmp_path):
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "x.dff").write_bytes(b"x")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "ok.py").write_text("x = 1\n", encoding="utf-8")
    n, found = G.scan_files([tmp_path], repo=tmp_path)
    assert n == 1 and found == []
    with pytest.raises(Exception):
        G.scan_files([tmp_path / "missing.bin"])


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_scan_staged_and_all_in_a_temp_repo(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()

    def git(*a: str) -> None:
        subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)

    git("init", "-q")
    (repo / "ok.py").write_text("print('hi')\n", encoding="utf-8")
    (repo / "leak.bin").write_bytes(b"COL3" + b"\x00" * 30)
    (repo / "model.dff").write_bytes(_rw(0x10))
    git("add", "ok.py", "leak.bin", "model.dff")
    n, found = G.scan_staged(repo, use_allowlist=True)
    assert n == 3 and sorted({f.path for f in found}) == ["leak.bin", "model.dff"]
    # the index content is checked, not the working tree
    (repo / "leak.bin").write_bytes(b"fine now")
    assert {f.path for f in G.scan_staged(repo)[1]} == {"leak.bin", "model.dff"}
    git("rm", "-q", "--cached", "model.dff")
    sha = hashlib.sha256(b"COL3" + b"\x00" * 30).hexdigest()
    (repo / G.ALLOWLIST_FILE).write_text(f"{sha} leak.bin\n", encoding="utf-8")
    assert G.scan_staged(repo)[1] == []
    n_all, found_all = G.scan_all(repo)
    assert {f.path for f in found_all} == {"model.dff"} and n_all == 4


def test_cli_acceptance(run_cli, repo_root, monkeypatch):
    monkeypatch.chdir(repo_root)
    bad = run_cli(["dev", "assetguard", "tests/core/data/fake_rw.bin"])
    assert bad.code == 1
    err = bad.json["error"]
    assert err["code"] == "ASSET_GUARD" and err["data"]["rows"][0][:2] == ["tests/core/data/fake_rw.bin", "rw-chunk"]
    good = run_cli(["dev", "assetguard", "src/satk/core/ids.py"])
    assert good.code == 0 and good.json["violations"] == 0
    assert run_cli(["dev", "assetguard", "tests/core/data/fake_rw.bin", "--allowlist"]).code == 0
    assert run_cli(["dev", "assetguard"]).code == 2
    assert run_cli(["dev", "assetguard", "x.py", "--all"]).code == 2
