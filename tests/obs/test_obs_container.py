"""The .saenet / .saerec container: golden bytes, round trips, every damage the reader must notice (no game files)."""

from __future__ import annotations

import json
import struct
import zlib
from pathlib import Path

import pytest

from satk.core.errors import SatkError
from satk.obs import container as C
from satk.obs import sample
from satk.obs import stream as S

HERE = Path(__file__).resolve().parent
FIX = HERE / "fixtures"
sys_path = str(HERE)


def make_fixtures_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("obs_make_fixtures", HERE / "make_fixtures.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def build(tmp_path, records=None, name="t.saerec", **kw):
    p = tmp_path / name
    C.pack_records(records or sample.client_stream(seconds=1.0), p, **kw)
    return p


def patch(path: Path, offset: int, data: bytes) -> None:
    b = bytearray(path.read_bytes())
    b[offset:offset + len(data)] = data
    path.write_bytes(bytes(b))


def codes(path, **kw):
    cont, rep = C.validate(path, **kw)
    return sorted(rep.by_code), rep, cont


# --------------------------------------------------------------------------- golden files


def test_the_golden_plain_file_is_reproduced_byte_for_byte(tmp_path):
    mod = make_fixtures_module()
    out = tmp_path / "g.saerec"
    C.pack_records(mod.golden_plain_records(), out, compress=False, chunk_records=8)
    assert out.read_bytes() == (FIX / "golden-plain.saerec").read_bytes()
    raw = (FIX / "golden-plain.saerec").read_bytes()
    assert raw[:8] == C.MAGIC and struct.unpack_from("<HH", raw, 8) == (1, 0) and raw[12:16] == b"SREC"
    assert struct.unpack_from("<I", raw, 60)[0] == zlib.crc32(raw[:60])
    hdr = C.open_container(FIX / "golden-plain.saerec")
    assert hdr.header.flags == C.FLAG_FINALIZED | C.FLAG_SORTED and hdr.used_table
    assert [c.offset % 8 for c in hdr.chunks] == [0] * len(hdr.chunks) and hdr.header.index_offset % 8 == 0
    assert raw[hdr.header.index_offset:hdr.header.index_offset + 4] == b"SAEX" and raw[-4:] == b"XEAS"


def test_the_golden_files_validate_and_read_back():
    for name in ("golden-plain.saerec", "golden-zlib.saenet"):
        cont, rep = C.validate(FIX / name)
        assert rep.ok and not rep.problems, (name, rep.problems)
    mod = make_fixtures_module()
    cont = C.open_container(FIX / "golden-plain.saerec")
    assert list(cont.iter_records()) == mod.golden_plain_records()
    z = C.open_container(FIX / "golden-zlib.saenet")
    assert list(z.iter_records()) == mod.golden_zlib_records()
    assert z.header.flags & C.FLAG_COMPRESSED and z.chunks[1].codec == C.CODEC_ZLIB and z.chunks[1].raw_size > z.chunks[1].stored_size
    assert z.header.kind == "SNET" and z.header.dt_s_us == 33333 and z.header.t_first_us == 600008249


# --------------------------------------------------------------------------- round trips


@pytest.mark.parametrize("compress", [False, True])
@pytest.mark.parametrize("chunk_records", [1, 7, 5000])
def test_pack_roundtrip(tmp_path, compress, chunk_records):
    recs = sample.client_stream(seconds=1.0)
    p = build(tmp_path, recs, compress=compress, chunk_records=chunk_records)
    cont, rep = C.validate(p)
    assert rep.ok, rep.problems
    assert list(cont.iter_records()) == recs
    n_obs = len([r for r in recs if r["rec"] not in ("hdr",)])
    assert sum(c.rec_count for c in cont.chunks if c.type == b"OBSJ") == n_obs
    assert len([c for c in cont.chunks if c.type == b"OBSJ"]) == -(-n_obs // chunk_records)
    assert bool(cont.header.flags & C.FLAG_COMPRESSED) == (compress and any(c.codec for c in cont.chunks))
    assert cont.header.t_first_us == min(r["t_us"] for r in recs[1:]) and cont.header.t_last_us == max(r["t_us"] for r in recs[1:])
    assert [c.index for c in cont.chunks] == list(range(len(cont.chunks)))
    ch = [c for c in cont.chunks if c.type == b"OBSJ"][-1]
    assert list(cont.iter_records(ch.index)) == recs[-ch.rec_count:]


def test_merged_streams_make_a_merged_container(tmp_path):
    merged = list(S.merge_streams([("a", sample.client_stream(seconds=1.0)), ("b", sample.server_stream(seconds=1.0))]))
    p = build(tmp_path, merged, name="m.saerec")
    cont, rep = C.validate(p)
    assert rep.ok, rep.problems
    assert cont.header.flags & C.FLAG_MERGED and len([c for c in cont.chunks if c.type == b"META"]) == 2
    assert sorted(h["node"] for h in cont.meta()) == ["client-1", "server"]
    assert list(cont.iter_records()) == merged


def test_blobs_and_kind_from_the_extension(tmp_path):
    p = tmp_path / "b.saenet"
    with C.Writer(p, "SNET", dt_s_us=33333) as w:
        w.meta(sample.net_stream()[0])
        w.add(sample.net_stream()[1])
        w.blob("note.txt", b"hello world" * 20, compress=True)
        w.add(sample.net_stream()[2])
    cont, rep = C.validate(p)
    assert rep.ok, rep.problems
    assert dict(cont.blobs()) == {"note.txt": b"hello world" * 20}
    assert [c.name for c in cont.chunks] == ["META", "OBSJ", "BLOB", "OBSJ"] and not cont.chunks[2].critical
    with pytest.raises(SatkError) as e:
        C.pack_records(sample.client_stream(seconds=0.3), tmp_path / "x.bin")
    assert e.value.code == "BAD_PARAMS" and ".saerec" in e.value.hint
    with pytest.raises(SatkError):
        C.Writer(tmp_path / "y", "NOPE")
    with pytest.raises(SatkError):
        C.pack_records(sample.client_stream(seconds=0.3)[1:], tmp_path / "z.saerec")


def test_writer_order_rules(tmp_path):
    with C.Writer(tmp_path / "o.saerec", "SREC") as w:
        with pytest.raises(SatkError):
            w.add({"rec": "event"})                       # META first
        w.meta(sample.client_stream(seconds=0.2)[0])
        with pytest.raises(SatkError):
            w.meta(sample.client_stream(seconds=0.2)[0])  # a second META needs merged=True
        with pytest.raises(SatkError):
            C.Writer(tmp_path / "p.saerec", "SREC").meta({"rec": "hdr"})   # an invalid header is refused


# --------------------------------------------------------------------------- damage


def test_a_writer_that_died_leaves_a_scannable_file(tmp_path):
    p = tmp_path / "dead.saerec"
    recs = sample.client_stream(seconds=1.0, end=False)
    w = C.Writer(p, "SREC", dt_s_us=33333, chunk_records=20)
    w.meta(recs[0])
    for r in recs[1:]:
        w.add(r)
    w.abandon()
    cont, rep = C.validate(p)
    assert rep.ok and set(rep.by_code) == {"W:NOT_FINALIZED", "W:NO_END"}
    assert not cont.used_table and cont.header.flags == 0 and cont.header.index_offset == 0
    assert list(cont.iter_records()) == recs
    # a file cut in the middle of a chunk
    raw = p.read_bytes()
    cut = tmp_path / "cut.saerec"
    cut.write_bytes(raw[:len(raw) // 2])
    c, rep, cont = codes(cut)
    assert "E:TRUNC" in c and not rep.ok
    assert len(cont.chunks) >= 2                              # the chunks before the cut are still listed
    cut.write_bytes(raw[:C.HEADER_SIZE + 10])
    assert "E:TRUNC" in codes(cut)[0]


def test_flipped_bytes_are_noticed(tmp_path):
    p = build(tmp_path, compress=False, chunk_records=40)
    cont = C.open_container(p)
    ch = cont.chunks[1]
    good = p.read_bytes()
    patch(p, ch.offset + C.CHUNK_HEADER_SIZE + 10, bytes([good[ch.offset + C.CHUNK_HEADER_SIZE + 10] ^ 0xFF]))
    c, rep, _ = codes(p)
    assert "E:CHUNK_CRC" in c and "chunk 1" in rep.problems[0].where
    assert "E:CHUNK_CRC" not in codes(p, deep=False)[0]          # the shallow check does not read payloads
    p.write_bytes(good)
    patch(p, 30, b"\x01")                                       # header byte
    assert "E:HEADER_CRC" in codes(p)[0]
    p.write_bytes(good)
    idx = cont.header.index_offset
    patch(p, idx + 20, bytes([good[idx + 20] ^ 1]))             # table entry
    c = codes(p)[0]
    assert "E:TABLE_CRC" in c
    assert all(x in c for x in ("E:TABLE_CRC",)) and C.open_container(p).used_table is False


def test_header_level_refusals(tmp_path):
    p = build(tmp_path)
    good = p.read_bytes()
    bad = tmp_path / "bad.saerec"
    bad.write_bytes(b"not a container at all" * 10)
    c, rep, cont = codes(bad)
    assert c == ["E:MAGIC"] and cont is None
    bad.write_bytes(good[:20])
    assert codes(bad)[0] == ["E:TRUNC"]
    bump = bytearray(good)
    bump[8:10] = struct.pack("<H", 2)
    bad.write_bytes(bytes(bump))
    assert codes(bad)[0] == ["E:VERSION"]
    newer = bytearray(good)
    newer[10:12] = struct.pack("<H", 3)
    newer[60:64] = struct.pack("<I", zlib.crc32(bytes(newer[:60])))
    bad.write_bytes(bytes(newer))
    c, rep, _ = codes(bad)
    assert c == ["W:VERSION"] and rep.ok
    kind = bytearray(good)
    kind[12:16] = b"ZZZZ"
    kind[60:64] = struct.pack("<I", zlib.crc32(bytes(kind[:60])))
    bad.write_bytes(bytes(kind))
    assert "E:KIND" in codes(bad)[0]
    flags = bytearray(good)
    flags[20:24] = struct.pack("<I", struct.unpack_from("<I", good, 20)[0] | 0x100)
    flags[60:64] = struct.pack("<I", zlib.crc32(bytes(flags[:60])))
    bad.write_bytes(bytes(flags))
    c, rep, _ = codes(bad)
    assert c == ["W:FLAGS"] and rep.ok                          # reserved bits: ignored with a warning


def _rewrite(path: Path, mutate, fix_raw: bool = True) -> None:
    """Rebuild a container from its parts after ``mutate(chunks)`` (chunks = [(Chunk, payload bytes)]) with valid CRCs."""
    cont = C.open_container(path)
    parts = [(c, cont.raw_payload(c)) for c in cont.chunks]
    parts = mutate(parts)
    out = bytearray(C._HEADER.pack(C.MAGIC, 1, 0, cont.header.kind.encode(), 64, cont.header.flags, 0, 0, cont.header.dt_s_us,  # noqa: SLF001
                                   cont.header.t_first_us, cont.header.t_last_us, cont.header.session_id, 0))
    offsets = []
    for c, payload in parts:
        c.offset, c.stored_size, c.crc32 = len(out), len(payload), zlib.crc32(payload) & 0xFFFFFFFF
        if fix_raw and c.codec == C.CODEC_NONE:
            c.raw_size = len(payload)
        offsets.append(c)
        out += c.pack() + payload + b"\0" * (C._align8(len(payload)) - len(payload))   # noqa: SLF001
    if cont.header.flags & C.FLAG_FINALIZED:
        entries = b"".join(struct.pack("<Q", c.offset) + c.pack() for c in offsets)
        head = b"SAEX" + struct.pack("<III", len(offsets), 48, 0)
        index = len(out)
        out += head + entries + struct.pack("<I", zlib.crc32(head + entries)) + b"XEAS"
        out[24:32] = struct.pack("<Q", index)
        out[32:36] = struct.pack("<I", len(offsets))
    out[60:64] = struct.pack("<I", zlib.crc32(bytes(out[:60])))
    path.write_bytes(bytes(out))


def test_structural_rules(tmp_path):
    p = build(tmp_path, compress=False, chunk_records=50)
    # an unknown critical chunk, an unknown ancillary one
    q = tmp_path / "q.saerec"
    q.write_bytes(p.read_bytes())

    def add_unknown(parts):
        c0 = parts[0][0]
        crit = C.Chunk(0, 0, b"WHAT", 0, C.CF_CRITICAL, 0, 3, -1, -1, 0, 0)
        anc = C.Chunk(0, 0, b"info", 0, 0, 0, 3, -1, -1, 0, 0)
        return [parts[0], (crit, b"abc"), (anc, b"abc"), *parts[1:]]

    _rewrite(q, add_unknown)
    c, rep, _ = codes(q)
    assert c.count("E:CRITICAL") == 1 and not any("info" in p.msg for p in rep.problems if p.code == "CRITICAL")
    # META missing / not first
    q.write_bytes(p.read_bytes())
    _rewrite(q, lambda parts: parts[1:])
    assert "E:META" in codes(q)[0]
    q.write_bytes(p.read_bytes())
    _rewrite(q, lambda parts: [parts[1], parts[0], *parts[2:]])
    assert "E:META" in codes(q)[0]
    # two META chunks without the MERGED flag
    q.write_bytes(p.read_bytes())
    _rewrite(q, lambda parts: [parts[0], parts[0], *parts[1:]])
    assert any(x.code == "META" and "MERGED" in x.msg for x in codes(q)[1].problems)
    # a META that is not a header
    q.write_bytes(p.read_bytes())
    _rewrite(q, lambda parts: [(parts[0][0], b'{"rec":"frame"}'), *parts[1:]])
    assert "E:META" in codes(q)[0]
    # a record count that lies
    q.write_bytes(p.read_bytes())

    def lie(parts):
        parts[1][0].rec_count += 3
        return parts

    _rewrite(q, lie)
    assert "E:REC_COUNT" in codes(q)[0]
    q.write_bytes(p.read_bytes())

    def shift(parts):
        parts[1][0].t_last_us += 5
        return parts

    _rewrite(q, shift)
    assert "E:RANGE" in codes(q)[0]
    # a header hdr record inside an OBSJ chunk
    q.write_bytes(p.read_bytes())

    def hdr_in(parts):
        raw = (json.dumps(sample.server_stream()[0], separators=(",", ":")) + "\n").encode() + parts[1][1]
        return [parts[0], (parts[1][0], raw), *parts[2:]]

    _rewrite(q, hdr_in)
    c = codes(q)[0]
    assert "E:META" in c


def test_ordering_and_flags(tmp_path):
    p = build(tmp_path, compress=False, chunk_records=50)
    q = tmp_path / "q.saerec"
    q.write_bytes(p.read_bytes())
    _rewrite(q, lambda parts: [parts[0], parts[2], parts[1], *parts[3:]])
    c = codes(q)[0]
    assert "E:ORDER" in c                                        # SORTED is set but the chunks are out of order
    raw = bytearray(p.read_bytes())
    raw[20:24] = struct.pack("<I", struct.unpack_from("<I", raw, 20)[0] | C.FLAG_COMPRESSED)
    raw[60:64] = struct.pack("<I", zlib.crc32(bytes(raw[:60])))
    q.write_bytes(bytes(raw))
    assert "E:FLAGS" in codes(q)[0]
    z = build(tmp_path, name="z.saerec", compress=True)
    raw = bytearray(z.read_bytes())
    raw[20:24] = struct.pack("<I", struct.unpack_from("<I", raw, 20)[0] & ~C.FLAG_COMPRESSED)
    raw[60:64] = struct.pack("<I", zlib.crc32(bytes(raw[:60])))
    q.write_bytes(bytes(raw))
    assert "E:FLAGS" in codes(q)[0]
    raw = bytearray(p.read_bytes())
    raw[32:36] = struct.pack("<I", 99)
    raw[60:64] = struct.pack("<I", zlib.crc32(bytes(raw[:60])))
    q.write_bytes(bytes(raw))
    assert "E:COUNT" in codes(q)[0]
    raw = bytearray(p.read_bytes())
    raw[40:48] = struct.pack("<q", 1)
    raw[60:64] = struct.pack("<I", zlib.crc32(bytes(raw[:60])))
    q.write_bytes(bytes(raw))
    assert "E:RANGE" in codes(q)[0]


def test_compression_guards(tmp_path):
    p = build(tmp_path, name="orig.saerec", compress=True, chunk_records=60)
    cont = C.open_container(p)
    z = next(c for c in cont.chunks if c.codec == C.CODEC_ZLIB)

    def tamper(field, value):
        q = tmp_path / "t.saerec"
        q.write_bytes(p.read_bytes())

        def mut(parts):
            for c, _ in parts:
                if c.index == z.index:
                    setattr(c, field, value)
            return parts
        _rewrite(q, mut)
        return q

    # raw_size too small: a decompression bomb must not be expanded
    c, rep, _ = codes(tamper("raw_size", 100))
    assert "E:SIZE" in c
    c, rep, _ = codes(tamper("raw_size", z.raw_size + 5))
    assert "E:SIZE" in c
    c, rep, _ = codes(tamper("raw_size", C.MAX_RAW_DEFAULT + 1))
    assert "E:SIZE" in c
    assert "E:SIZE" in codes(p, max_raw=100)[0]                   # a stricter reader limit
    c, rep, _ = codes(tamper("codec", C.CODEC_ZSTD))
    assert c == ["W:CODEC"] or "W:CODEC" in c                      # reserved: warned, not checked
    c, _, _ = codes(tamper("codec", 9))
    assert "E:CODEC" in c
    # a codec-0 chunk whose raw_size differs
    q = build(tmp_path, name="n.saerec", compress=False)

    def mut2(parts):
        parts[1][0].raw_size += 1
        return parts
    _rewrite(q, mut2, fix_raw=False)
    assert "E:SIZE" in codes(q)[0]
    # a payload that is not zlib
    r = tmp_path / "r.saerec"
    r.write_bytes(p.read_bytes())
    _rewrite(r, lambda parts: [(c, b"garbage-garbage" if c.index == z.index else d) for c, d in parts])
    assert "E:ZLIB" in codes(r)[0]
    # a truncated zlib stream
    r.write_bytes(p.read_bytes())
    _rewrite(r, lambda parts: [(c, d[:-6] if c.index == z.index else d) for c, d in parts])
    assert "E:ZLIB" in codes(r)[0] or "E:SIZE" in codes(r)[0]


def test_chunk_payload_content_errors(tmp_path):
    p = build(tmp_path, compress=False, chunk_records=200)
    q = tmp_path / "q.saerec"
    for payload, code in ((b"\xff\xfe\n", "E:UTF8"), (b"{not json}\n", "E:JSON")):
        q.write_bytes(p.read_bytes())
        _rewrite(q, lambda parts, pl=payload: [parts[0], (parts[1][0], pl), *parts[2:]])
        assert code in codes(q)[0], code
    # the stream rules apply inside containers: a frame record that breaks its schema
    recs = sample.client_stream(seconds=1.0)
    recs[5] = {**recs[5], "frame_ms": -1}
    bad = build(tmp_path, recs, name="bad.saerec")
    c, rep, _ = codes(bad)
    assert "E:SCHEMA" in c and "chunk 1 rec" in rep.problems[0].where
    # the META clock must agree with the container header
    q.write_bytes(p.read_bytes())
    raw = bytearray(q.read_bytes())
    raw[36:40] = struct.pack("<I", 20000)
    raw[60:64] = struct.pack("<I", zlib.crc32(bytes(raw[:60])))
    q.write_bytes(bytes(raw))
    assert "E:META" in codes(q)[0]
    # blob names
    b = tmp_path / "b.saenet"
    with C.Writer(b, "SNET") as w:
        w.meta(sample.net_stream()[0])
        w.blob("x", b"1")
    cont = C.open_container(b)
    _rewrite(b, lambda parts: [parts[0], (parts[1][0], b"\xff\xff"), *parts[2:]])
    assert "E:BLOB" in codes(b)[0]


def test_reader_api_errors(tmp_path):
    p = build(tmp_path, compress=False)
    cont = C.open_container(p)
    c = cont.chunks[1]
    patch(p, c.offset + C.CHUNK_HEADER_SIZE, b"\x00")
    with pytest.raises(C.ContainerError) as e:
        cont.raw_payload(c)
    assert e.value.code == "CHUNK_CRC"
    assert C.is_container(p) and not C.is_container(tmp_path / "nope") and not C.is_container(FIX / "satk-bench" / "S2-mta.json")
    assert C.flag_names(0xF | 0x100) == ["FINALIZED", "COMPRESSED", "SORTED", "MERGED", "0x100"]
