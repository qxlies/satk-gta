"""satk.formats.rw and satk.formats.img on synthetic data (pitfalls #1, #3, #4, #14)."""

from __future__ import annotations

import builtins
import struct

import pytest

from satk.formats import img
from satk.formats.img import SECTOR, ImgArchive, parse_v1_directory, parse_ver2_directory
from satk.formats.rw import (FormatError, find_child, iter_children, plausible_libid, read_chunk, rw_payload_size,
                             rw_version)


# ----------------------------------------------------------------------------- rw
@pytest.mark.parametrize("libid,ver", [(0x1803FFFF, 0x36003), (0x1400FFFF, 0x35000), (0x0C02FFFF, 0x33002),
                                       (0x1003FFFF, 0x34003), (0x310, 0x31000)])
def test_rw_version(libid, ver):
    assert rw_version(libid) == ver
    assert plausible_libid(libid)


def test_rw_version_implausible():
    assert not plausible_libid(0x12345678 ^ 0xFFFF0000)
    assert not plausible_libid(0)


def test_iter_children_nested(b):
    inner = b.chunk(0x01, b"abcd") + b.chunk(0x02, b"name\0\0\0\0")
    buf = b.chunk(0x10, inner)
    top = read_chunk(buf, 0)
    assert (top.type, top.size, top.data_off, top.end) == (0x10, len(inner), 12, len(buf))
    assert top.version == 0x36003 and top.name == "Clump"
    kids = list(iter_children(buf, top.data_off, top.end))
    assert [k.type for k in kids] == [0x01, 0x02]
    assert bytes(buf[kids[0].data_off:kids[0].end]) == b"abcd"
    assert find_child(buf, top.data_off, top.end, 0x02).size == 8
    assert find_child(buf, top.data_off, top.end, 0x03) is None


def test_child_larger_than_parent_raises(b):
    bad = struct.pack("<III", 0x01, 100, b.SA_LIBID) + b"x" * 8
    buf = b.chunk(0x10, bad)
    with pytest.raises(FormatError) as ei:
        list(iter_children(buf, 12, len(buf)))
    assert ei.value.kind == "rw" and ei.value.offset == 12
    clamped = list(iter_children(buf, 12, len(buf), strict=False))
    assert len(clamped) == 1 and clamped[0].end == len(buf) and clamped[0].size == 8


def test_read_chunk_truncated():
    with pytest.raises(FormatError):
        read_chunk(b"\x10\0\0\0", 0)
    with pytest.raises(FormatError):
        read_chunk(struct.pack("<III", 0x10, 50, 0x1803FFFF), 0)


def test_payload_size_multi_clump_and_padding(b):
    c1, c2 = b.chunk(0x2B, b"uv" * 3), b.chunk(0x10, b"\0" * 20)
    data = b.pad(c1 + c2 + b.chunk(0x10, b"z" * 4))
    assert rw_payload_size(data) == len(c1) + len(c2) + 16
    assert rw_payload_size(b"\0" * 64) is None
    assert rw_payload_size(b"COL3" + b"\0" * 60) is None
    assert rw_payload_size(b"\x10\0\0") is None


def test_format_error_is_value_error_and_picklable():
    import pickle

    e = FormatError("txd", 0x40, "boom")
    assert isinstance(e, ValueError) and "0x40" in str(e)
    e2 = pickle.loads(pickle.dumps(e))
    assert (e2.kind, e2.offset, e2.msg) == ("txd", 0x40, "boom")


# ----------------------------------------------------------------------------- img
def test_ver2_ihh24s_and_size_in_archive(tmp_path, b):
    data = b.ver2([("Infernus.DFF", b.chunk(0x10, b"\0" * 3000)), ("a.txd", b"t" * 10)],
                  archive_sectors={"a.txd": 3})
    p = tmp_path / "x.img"
    p.write_bytes(data)
    with ImgArchive.open(p) as a:
        assert a.version == 2 and len(a) == 2 and a.file_size == len(data)
        e0, e1 = a.entries
        assert (e0.idx, e0.name, e0.stem, e0.ext) == (0, "Infernus.DFF", "Infernus", "dff")
        assert e0.stream_sectors == 2 and e0.archive_sectors == 0 and e0.size == 2 * SECTOR
        assert e0.abs_offset == SECTOR
        # SizeInArchive != 0 -> the engine (and we) take it
        assert e1.stream_sectors == 1 and e1.archive_sectors == 3 and e1.size == 3 * SECTOR
        assert a.find("infernus.dff") is e0 and a.find("INFERNUS.dff") is e0 and a.find("nope") is None
        blob = a.read(e0)
        assert len(blob) == 2 * SECTOR and rw_payload_size(blob) == 3012
        assert a.read_at(SECTOR, 4) == blob[:4]
        with pytest.raises(FormatError):
            a.read(e1)  # 3 sectors from its offset run past the end of this small archive
        with pytest.raises(FormatError):
            a.read_at(len(data) - 2, 4)


def test_ver2_not_ii24s():
    """A DragonFF-style '<II24s>' reading would glue Size/SizeInArchive into one huge number."""
    raw = b"VER2" + struct.pack("<I", 1) + struct.pack("<IHH24s", 1, 5, 7, b"a.dff")
    (e,) = parse_ver2_directory(raw)
    assert (e.stream_sectors, e.archive_sectors) == (5, 7)
    assert struct.unpack_from("<II", raw, 8)[1] == 5 | (7 << 16)


def test_v1_dir_img(tmp_path, b):
    d, img = b.v1([("one.dff", b"1" * 10), ("two.txd", b"2" * 3000)])
    (tmp_path / "old.dir").write_bytes(d)
    (tmp_path / "old.img").write_bytes(img)
    for path in (tmp_path / "old.img", tmp_path / "old.dir"):
        with ImgArchive.open(path) as a:
            assert a.version == 1 and [e.name for e in a.entries] == ["one.dff", "two.txd"]
            assert a.entries[1].abs_offset == SECTOR and a.entries[1].size == 2 * SECTOR
            assert a.read(a.entries[0])[:10] == b"1" * 10
    assert len(parse_v1_directory(d)) == 2
    with pytest.raises(FormatError):
        parse_v1_directory(d[:-1])


@pytest.mark.parametrize("count", [0, 2])
def test_v1_directory_read_is_bounded(tmp_path, monkeypatch, b, count):
    monkeypatch.setattr(img, "MAX_ENTRIES", 2)
    directory, data = b.v1([(f"a{i}.dff", b"x") for i in range(count)])
    p = tmp_path / "old.img"
    d = p.with_suffix(".dir")
    p.write_bytes(data)
    d.write_bytes(directory)
    reads = []
    open_ro = img.open_ro

    def guarded_open(path):
        fh = open_ro(path)
        if path == d:
            read = fh.read

            def bounded_read(size=-1):
                assert 0 <= size <= len(directory) + 1
                reads.append(size)
                return read(size)
            monkeypatch.setattr(fh, "read", bounded_read)
        return fh

    monkeypatch.setattr(img, "open_ro", guarded_open)
    with ImgArchive.open(p) as a:
        assert a.version == 1 and len(a.entries) == count
    assert reads


def test_unknown_magic_is_explicit(tmp_path):
    p = tmp_path / "packed.img"
    p.write_bytes(b"\x78\x9c" + b"\0" * 4094)  # zlib-like, no .dir
    with pytest.raises(FormatError, match="unknown IMG magic"):
        ImgArchive.open(p)


@pytest.mark.parametrize("cut", [4, 7, 8 + 31, 8 + 32 * 1 + 5])
def test_truncated_directory(tmp_path, b, cut):
    data = b.ver2([("a.dff", b"x"), ("b.dff", b"y")])
    p = tmp_path / "t.img"
    p.write_bytes(data[:cut])
    with pytest.raises(FormatError):
        ImgArchive.open(p)


def test_out_of_bounds_check_is_opt_in(b):
    raw = b"VER2" + struct.pack("<I", 1) + struct.pack("<IHH24s", 100, 5, 0, b"far.dff")
    assert parse_ver2_directory(raw)[0].abs_offset == 100 * SECTOR
    with pytest.raises(FormatError, match="beyond archive size"):
        parse_ver2_directory(raw, file_size=4096)


def test_img_opened_read_only(tmp_path, b, monkeypatch):
    p = tmp_path / "ro.img"
    p.write_bytes(b.ver2([("a.dff", b"x")]))
    modes = []
    real_open = builtins.open

    def spy(file, mode="r", *a, **k):
        modes.append(mode)
        return real_open(file, mode, *a, **k)

    monkeypatch.setattr(builtins, "open", spy)
    with ImgArchive.open(p) as a:
        a.read(a.entries[0])
    assert modes and all(m == "rb" for m in modes)
