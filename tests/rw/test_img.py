"""satk.rw.img: new archives, the INU_Core BUGS.md regressions (2026-09-27) and archive diffs.

INU_Core ``core/BUGS.md`` (GPL; only its findings are used): 1 - the VER2 directory grows over the first
file after 67 new entries in vanilla gta3.img; 2 - rebuild breaks every archive of 64+ entries; 3 - VER1
archives get a VER2 header; 4 - the entry size read as one u32; 6 - 24-character names without NUL.
satk only builds new archives, so each case is a property of :func:`build_img` checked here.
"""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path

import pytest

from satk.formats.img import SECTOR, ImgArchive
from satk.rw.img import (MAX_V2_SECTORS, ImgBuildError, Source, base_sources, build_img, check_name, diff_img,
                         dir_sources, plan_layout)


def _src(name: str, data: bytes) -> Source:
    return Source(name, len(data), lambda d=data: d, f"test:{name}")


def _payload(i: int, size: int | None = None) -> bytes:
    size = size if size is not None else 100 + (i * 977) % 5000
    return (hashlib.sha256(str(i).encode()).digest() * (size // 32 + 1))[:size]


def _check_archive(path: Path, expect: dict[str, bytes], version: int = 2) -> None:
    with ImgArchive.open(path) as a:
        assert a.version == version
        assert [e.name for e in a.entries] == list(expect)
        first = min(e.offset_sectors for e in a.entries)
        if version == 2:
            assert first * SECTOR >= 8 + 32 * len(a.entries)              # BUGS 1/2: directory never overlaps
        spans = sorted((e.offset_sectors, e.offset_sectors + e.stream_sectors) for e in a.entries)
        assert all(spans[i][1] <= spans[i + 1][0] for i in range(len(spans) - 1))
        for e in a.entries:
            assert e.archive_sectors == 0                                 # BUGS 4: <IHH24s>, archive size 0
            data = a.read(e)
            assert data[:len(expect[e.name])] == expect[e.name]
            assert data[len(expect[e.name]):] == b"\0" * (len(data) - len(expect[e.name]))


@pytest.mark.parametrize("n", [1, 63, 64, 65, 67, 100, 3000])
def test_ver2_directory_never_overwrites_data(tmp_path, satk_home, n):
    files = {f"f{i:04d}.dff": _payload(i) for i in range(n)}
    res = build_img(tmp_path / "x.img", [_src(k, v) for k, v in files.items()])
    assert res["first_data_sector"] == (8 + 32 * n + SECTOR - 1) // SECTOR
    _check_archive(tmp_path / "x.img", files)
    raw = (tmp_path / "x.img").read_bytes()
    assert raw[:4] == b"VER2" and struct.unpack_from("<I", raw, 4)[0] == n
    assert struct.unpack_from("<IHH", raw, 8) == (res["first_data_sector"], (len(files["f0000.dff"]) + 2047) // 2048, 0)


@pytest.mark.parametrize("n_base,n_new", [(64, 0), (100, 67), (700, 3), (5, 3000)])
def test_rebuild_with_base_of_64_plus_entries(tmp_path, satk_home, n_base, n_new):
    base_files = {f"b{i:04d}.txd": _payload(i) for i in range(n_base)}
    build_img(tmp_path / "base.img", [_src(k, v) for k, v in base_files.items()])
    before = hashlib.sha256((tmp_path / "base.img").read_bytes()).hexdigest()
    new_files = {f"n{i:04d}.dff": _payload(10_000 + i) for i in range(n_new)}
    with ImgArchive.open(tmp_path / "base.img") as a:
        srcs = base_sources(a) + [_src(k, v) for k, v in new_files.items()]
        build_img(tmp_path / "out.img", srcs)
    assert hashlib.sha256((tmp_path / "base.img").read_bytes()).hexdigest() == before     # base untouched
    _check_archive(tmp_path / "out.img", {**base_files, **new_files})


def test_tight_base_archive_grown_by_67_entries(tmp_path, satk_home):
    """BUGS 1 replayed: a base whose first file starts right after its directory (like vanilla gta3.img with
    2 168 spare bytes) gets 67 more entries; every original file must survive."""
    n = 63                                                      # 8 + 32 * 63 = 2024 bytes: 24 spare bytes
    base_files = {f"b{i:03d}.dff": _payload(i) for i in range(n)}
    build_img(tmp_path / "tight.img", [_src(k, v) for k, v in base_files.items()])
    with ImgArchive.open(tmp_path / "tight.img") as a:
        assert a.entries[0].offset_sectors == 1
        srcs = base_sources(a) + [_src(f"new{i:02d}.dff", _payload(500 + i)) for i in range(67)]
        build_img(tmp_path / "grown.img", srcs)
    expect = {**base_files, **{f"new{i:02d}.dff": _payload(500 + i) for i in range(67)}}
    _check_archive(tmp_path / "grown.img", expect)


def test_ver1_writes_img_and_dir_without_header(tmp_path, satk_home):
    files = {f"v{i}.dff": _payload(i) for i in range(70)}
    res = build_img(tmp_path / "gta3.img", [_src(k, v) for k, v in files.items()], version=1)
    raw = (tmp_path / "gta3.img").read_bytes()
    assert raw[:4] != b"VER2" and raw[:len(files["v0.dff"])] == files["v0.dff"]   # BUGS 3: no VER2 header
    d = (tmp_path / "gta3.dir").read_bytes()
    assert len(d) == 32 * 70 and res["dir"] == tmp_path / "gta3.dir"
    _check_archive(tmp_path / "gta3.img", files, version=1)


def test_size_limit_and_names(tmp_path, satk_home):
    big = Source("huge.dff", (MAX_V2_SECTORS + 1) * SECTOR, lambda: b"", "test")
    with pytest.raises(ImgBuildError, match="VER2 stores at most"):
        build_img(tmp_path / "big.img", [big])
    assert not (tmp_path / "big.img").exists()
    build_img(tmp_path / "big1.img", [Source("huge.dff", 4096, lambda: b"\1" * 4096)], version=1)
    with pytest.raises(ImgBuildError, match="1..23"):
        check_name("a" * 20 + ".dff")                                     # 24 characters: BUGS 6
    assert check_name("a" * 19 + ".dff") == []                            # 23 characters fit with the NUL
    with pytest.raises(ImgBuildError, match="ASCII"):
        check_name("café.dff")
    with pytest.raises(ImgBuildError, match="ASCII"):
        check_name("sub/x.dff")
    with pytest.raises(ImgBuildError, match="duplicate"):
        build_img(tmp_path / "dup.img", [_src("A.dff", b"1"), _src("a.DFF", b"2")])
    res = build_img(tmp_path / "n.img", [_src("a" * 19 + ".dff", b"x")])
    raw = (tmp_path / "n.img").read_bytes()
    assert raw[8 + 8:8 + 32] == ("a" * 19 + ".dff").encode() + b"\0"     # NUL-terminated
    assert "warn" not in res


def test_long_extension_position_warns_for_ver1():
    assert any(w.startswith("LONG_NAME") for w in check_name("abcdefghijklmnopqrstu.d", 1))
    assert not any(w.startswith("LONG_NAME") for w in check_name("abcdefghijklmnopqrstu.d", 2))


def test_replace_and_remove_first_entry(tmp_path, satk_home):
    files = {f"e{i}.dff": _payload(i) for i in range(10)}
    build_img(tmp_path / "base.img", [_src(k, v) for k, v in files.items()])
    with ImgArchive.open(tmp_path / "base.img") as a:
        srcs = [s for s in base_sources(a) if s.name != "e1.dff"]
        srcs[0] = _src("e0.dff", b"replaced" * 1000)
        build_img(tmp_path / "out.img", srcs)
    expect = {k: v for k, v in files.items() if k != "e1.dff"}
    expect["e0.dff"] = b"replaced" * 1000
    _check_archive(tmp_path / "out.img", expect)


def test_dir_sources_and_include(tmp_path, satk_home):
    d = tmp_path / "mod"
    (d / "sub").mkdir(parents=True)
    (d / "B.dff").write_bytes(b"b")
    (d / "a.txd").write_bytes(b"a")
    (d / "sub" / "c.col").write_bytes(b"c")
    (d / "Thumbs.db").write_bytes(b"junk")
    (d / ".gitkeep").write_bytes(b"")
    assert [s.name for s in dir_sources(d)] == ["a.txd", "B.dff"]
    assert [s.name for s in dir_sources(d, recursive=True)] == ["a.txd", "B.dff", "c.col"]
    build_img(tmp_path / "m.img", dir_sources(d))
    with ImgArchive.open(tmp_path / "m.img") as a:
        assert [s.name for s in base_sources(a, ["*.TXD"])] == ["a.txd"]


def test_layout_is_deterministic(tmp_path, satk_home):
    srcs = [_src(f"x{i}.dff", _payload(i)) for i in range(30)]
    r1 = build_img(tmp_path / "1.img", srcs)
    r2 = build_img(tmp_path / "2.img", srcs)
    assert r1["blake2b"] == r2["blake2b"]
    assert (tmp_path / "1.img").read_bytes() == (tmp_path / "2.img").read_bytes()
    assert plan_layout(srcs)[0] == 1


def test_diff(tmp_path, satk_home):
    build_img(tmp_path / "a.img", [_src("same.dff", b"x" * 10), _src("chg.dff", b"1"), _src("gone.dff", b"g")])
    build_img(tmp_path / "b.img", [_src("SAME.dff", b"x" * 10 + b"\0" * 5), _src("chg.dff", b"2"),
                                   _src("new.txd", b"n")])
    with ImgArchive.open(tmp_path / "a.img") as a, ImgArchive.open(tmp_path / "b.img") as b:
        counts, rows = diff_img(a, b)
    assert counts == {"same": 1, "changed": 1, "added": 1, "removed": 1}
    assert [r[:2] for r in rows] == [["chg.dff", "changed"], ["gone.dff", "removed"], ["new.txd", "added"]]
