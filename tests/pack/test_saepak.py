"""The .saepak writer and reader: layout, content addressing, IMG compatibility, chunks, determinism, limits."""

from __future__ import annotations

import hashlib
import struct
import sys
import types
from pathlib import Path

import pytest

from satk.core.errors import SatkError
from satk.formats.img import ImgArchive
from satk.pack import layout as L
from satk.pack import saepak as P
from satk.pack.sources import collect, logical_problem


def build(mod_dir: Path, out: Path, **kw) -> dict:
    srcs, _warn = collect([str(mod_dir)])
    return P.build_pack(srcs, out, **kw)


def test_record_sizes_and_offsets():
    for rec, size in ((L.HEADER, 192), (L.FOOTER, 128), (L.ENTRY, 96), (L.NAME, 40), (L.CHUNK, 48),
                      (L.IMG_ENTRY, 32), (L.DAC_HEADER, 128), (L.DAC_SECTION, 64)):
        assert rec.struct.size == size == rec.size, rec.name
        offs = [f.offset for f in rec.fields]
        assert offs == sorted(offs) and offs[0] == 0
        assert all(f.offset % f.size == 0 or f.code.endswith("s") for f in rec.fields), rec.name
    # 8-byte aligned tables: every manifest section offset stays aligned for any count
    assert L.ENTRY.size % 8 == L.NAME.size % 8 == L.CHUNK.size % 8 == L.HEADER.size % 8 == 0
    assert L.FOOTER.by_name["crc32"].offset == L.FOOTER_CRC_SPAN


def test_build_roundtrip(mod_dir, tmp_path):
    out = tmp_path / "out" / "m.saepak"
    r = build(mod_dir, out, namespace="demo", title="Demo", priority=5, mount_name="demo.img")
    assert r["names"] == 5 and r["entries"] == 4 and r["dedup_saved"] == (mod_dir / "car.dff").stat().st_size
    assert out.is_file() and out.with_name("m.saepak.manifest").is_file()
    with P.Pack.open(out) as pk:
        d = pk.describe()
        assert (d["namespace"], d["title"], d["mount_name"], d["priority"], d["mount_mode"]) == (
            "demo", "Demo", "demo.img", 5, "overlay")
        assert d["names"] == 5 and d["entries"] == 4 and d["chunk_size"] == 65536 and d["format"] == "1.0"
        assert [n["logical"] for n in pk.names] == sorted(n["logical"] for n in pk.names)
        for rel in ("car.dff", "car_copy.dff", "car.txd", "col/car.col", "sub/extra.dff"):
            assert pk.read_name(rel) == (mod_dir / rel).read_bytes(), rel
        # content addressing: the two identical DFFs are one entry (one payload), two names, two IMG slots at one offset
        a, b = pk.name_index("car.dff"), pk.name_index("car_copy.dff")
        assert pk.names[a]["entry_index"] == pk.names[b]["entry_index"]
        e = pk.entries[pk.names[a]["entry_index"]]
        assert e["sha256"] == hashlib.sha256((mod_dir / "car.dff").read_bytes()).digest()
        assert e["kind"] == 1 and e["rw_version"] == 0x36003 and e["offset"] % 2048 == 0
        assert pk.header["data_offset"] % 2048 == 0 and pk.header["pack_id"] == P.compute_pack_id(
            "demo", ((n["logical"], pk.entries[n["entry_index"]]["sha256"]) for n in pk.names))
    findings, stats = P.verify_pack(out)
    assert findings == [] and stats["entries"] == 4


def test_the_pack_is_an_img_archive(mod_dir, tmp_path):
    out = tmp_path / "m.saepak"
    build(mod_dir, out)
    with ImgArchive.open(out) as img:
        assert img.version == 2 and len(img.entries) == 5
        names = {e.name for e in img.entries}
        assert {"car.dff", "car_copy.dff", "car.txd", "car.col", "extra.dff"} == names     # the base names
        assert img.find("car.dff").abs_offset == img.find("car_copy.dff").abs_offset       # one payload, two slots
        e = img.find("car.txd")
        assert img.read(e)[:len((mod_dir / "car.txd").read_bytes())] == (mod_dir / "car.txd").read_bytes()
        assert e.archive_sectors == 0 and e.abs_offset % 2048 == 0
    with P.Pack.open(out) as pk:
        for n in pk.names:                      # the manifest and the directory agree
            slot = pk.img_directory()[n["dir_index"]]
            assert slot["offset_sectors"] * 2048 == pk.entries[n["entry_index"]]["offset"]


def test_deterministic_and_order_independent(mod_dir, tmp_path):
    a, b, c = (tmp_path / n for n in ("a.saepak", "b.saepak", "c.saepak"))
    build(mod_dir, a, namespace="x")
    build(mod_dir, b, namespace="x")
    assert a.read_bytes() == b.read_bytes()
    srcs, _ = collect([str(mod_dir)])
    P.build_pack(list(reversed(srcs)), c, namespace="x")
    assert a.read_bytes() == c.read_bytes()
    # the pack id names the mapping, not the layout: another chunk size changes the bytes but not the id
    d = tmp_path / "d.saepak"
    build(mod_dir, d, namespace="x", chunk_size=4096)
    assert d.read_bytes() != a.read_bytes()
    with P.Pack.open(a) as pa, P.Pack.open(d) as pd:
        assert pa.header["pack_id"] == pd.header["pack_id"]
    # ... while another namespace changes it
    e = tmp_path / "e.saepak"
    build(mod_dir, e, namespace="y")
    with P.Pack.open(a) as pa, P.Pack.open(e) as pe:
        assert pa.header["pack_id"] != pe.header["pack_id"]


def test_chunks_and_the_ranged_fetch_plan(tmp_path):
    big = bytes((i * 31 + i // 251) & 0xFF for i in range(300_000))
    d = tmp_path / "in"
    d.mkdir()
    (d / "big.col").write_bytes(b"COL3" + big)
    out = tmp_path / "big.saepak"
    build(d, out, chunk_size=64 * 1024)
    with P.Pack.open(out) as pk:
        e = pk.entries[0]
        assert e["size"] == len(big) + 4 and e["chunk_count"] == 5
        plan = pk.chunk_plan(0)
        assert [p[1] for p in plan] == [65536] * 4 + [e["size"] - 4 * 65536]
        # a fetcher that only has ranges rebuilds the content and checks every chunk
        raw = out.read_bytes()
        body = b""
        for off, ln, sha in plan:
            piece = raw[off:off + ln]
            assert hashlib.sha256(piece).digest() == sha
            body += piece
        assert body == b"COL3" + big
        assert pk.manifest_hash_ok
    assert P.verify_pack(out)[0] == []


def test_manifest_only_file(mod_dir, tmp_path):
    out = tmp_path / "m.saepak"
    build(mod_dir, out, namespace="demo")
    side = P.sidecar_path(out)
    raw = out.read_bytes()
    assert side.read_bytes() == raw[-(len(side.read_bytes())):]       # the manifest block + footer, byte for byte
    with P.Pack.open(side) as pk:
        assert not pk.has_payload and len(pk.names) == 5 and pk.namespace == "demo"
        with pytest.raises(P.PackError):
            pk.read_entry(0)
    fs, _ = P.verify_pack(side)
    assert [f.code for f in fs] == ["MANIFEST_ONLY"] and fs[0].sev == "info"


def test_long_and_clashing_stream_names(tmp_path):
    d = tmp_path / "in"
    long_name = "averyveryverylongnameformodel.dff"            # 33 characters: does not fit the IMG field
    a, b = S_dff(1), S_dff(2)
    (d / "x").mkdir(parents=True)
    (d / "y").mkdir()
    (d / "x" / "wall.dff").write_bytes(a)
    (d / "y" / "wall.dff").write_bytes(b)                       # the same base name, other content
    (d / "y" / long_name).write_bytes(a)
    out = tmp_path / "n.saepak"
    build(d, out)
    with P.Pack.open(out) as pk:
        slots = {s["name"] for s in pk.img_directory()}
        sha_a = hashlib.sha256(a).hexdigest()
        sha_b = hashlib.sha256(b).hexdigest()
        # x/wall.dff (sorted first) keeps the plain name; y/wall.dff gets the content-addressed one
        assert slots == {"wall.dff", f"~{sha_b[:18]}.dff", f"~{sha_a[:18]}.dff"}
        assert all(len(s) <= 23 for s in slots)
        derived = [n for n in pk.names if n["flags"] & L.NAME_DERIVED]
        assert {n["logical"] for n in derived} == {"y/wall.dff", f"y/{long_name}"}
    assert P.verify_pack(out)[0] == []


def S_dff(seed: int) -> bytes:
    import pack_synth as synth

    return synth.dff(seed=seed)


def test_limits_and_bad_inputs(tmp_path, monkeypatch):
    d = tmp_path / "in"
    d.mkdir()
    (d / "empty.dff").write_bytes(b"")
    srcs, _ = collect([str(d)])
    with pytest.raises(SatkError, match="empty"):
        P.build_pack(srcs, tmp_path / "o.saepak")
    (d / "empty.dff").write_bytes(S_dff(0))
    srcs, _ = collect([str(d)])
    monkeypatch.setattr(L, "MAX_ENTRY_BYTES", 100)
    with pytest.raises(SatkError, match="IMG limit"):
        P.build_pack(srcs, tmp_path / "o.saepak")
    monkeypatch.undo()
    for kw, text in (({"chunk_size": 3000}, "chunk size"), ({"chunk_size": 1024}, "chunk size"),
                     ({"mount_mode": "nope"}, "mount mode"), ({"fast": "crc"}, "fast"),
                     ({"namespace": "a\nb"}, "namespace")):
        with pytest.raises(SatkError, match=text):
            P.build_pack(srcs, tmp_path / "o.saepak", **kw)
    with pytest.raises(SatkError, match="nothing to pack"):
        P.build_pack([], tmp_path / "o.saepak")
    assert not (tmp_path / "o.saepak").exists()                  # a refused build leaves no file behind


def test_collect_rules(tmp_path):
    d = tmp_path / "in"
    (d / "A" / "B").mkdir(parents=True)
    (d / ".hidden").mkdir()
    (d / ".hidden" / "x.dff").write_bytes(b"1")
    (d / "A" / "B" / "Car.DFF").write_bytes(b"1")
    (d / "top.txd").write_bytes(b"2")
    (d / "notes.md").write_bytes(b"3")
    srcs, warn = collect([str(d)])
    assert [s.logical for s in srcs] == ["a/b/car.dff", "top.txd"]
    assert warn and warn[0].startswith("SKIPPED: 1 file")
    flat, _ = collect([str(d)], flat=True)
    assert [s.logical for s in flat] == ["car.dff", "top.txd"]
    assert [s.logical for s in collect([str(d / "top.txd"), str(d / "top.txd")])[0]] == ["top.txd"]   # twice = once
    other = tmp_path / "other"
    other.mkdir()
    (other / "top.txd").write_bytes(b"4")
    with pytest.raises(SatkError, match="two inputs"):
        collect([str(d / "top.txd"), str(other / "top.txd")])
    with pytest.raises(SatkError, match="not a streamable"):
        collect([str(d / "notes.md")])
    with pytest.raises(SatkError) as e:
        collect([str(tmp_path / "missing")])
    assert e.value.code == "NOT_FOUND"
    only_notes = tmp_path / "only_notes"
    only_notes.mkdir()
    (only_notes / "a.txt").write_bytes(b"1")
    with pytest.raises(SatkError, match="no DFF"):
        collect([str(only_notes)])
    for bad in ("", "A.dff", "a\\b.dff", "a//b.dff", "../a.dff", "a/./b.dff", "a:b.dff", "a.exe", "x" * 300 + ".dff"):
        assert logical_problem(bad), bad
    assert logical_problem("models/car.dff") is None


def test_header_checks_warn_but_do_not_block(tmp_path):
    d = tmp_path / "in"
    d.mkdir()
    (d / "bad.dff").write_bytes(b"not a clump at all, no")           # >= 12 bytes, wrong first chunk
    (d / "bad.col").write_bytes(b"XXXX" + bytes(40))
    (d / "bad.ifp").write_bytes(b"YYYY" + bytes(40))
    (d / "tiny.txd").write_bytes(b"abc")
    r = build(d, tmp_path / "w.saepak")
    text = "\n".join(r["warn"])
    assert text.count("BAD_HEADER") == 5 and "bad.dff" in text and "COLL" in text and "ANP3" in text
    assert P.verify_pack(tmp_path / "w.saepak")[0] == []             # a container does not judge its contents


def test_rw_version_stamps():
    assert P.rw_version_of(0x1803FFFF) == 0x36003          # San Andreas
    assert P.rw_version_of(0x0C02FFFF) == 0x33002          # Vice City
    assert P.rw_version_of(0x0401FFFF) == 0x31001          # III


def test_fast_hash_fields(mod_dir, tmp_path, monkeypatch):
    out = tmp_path / "f.saepak"
    # without the xxhash package the request is a DEPENDENCY error, before anything is written
    monkeypatch.setitem(sys.modules, "xxhash", None)
    srcs, _ = collect([str(mod_dir)])
    with pytest.raises(SatkError) as e:
        P.build_pack(srcs, out, fast="xxh3")
    assert e.value.code == "DEPENDENCY" and not out.exists()
    plain = tmp_path / "plain.saepak"
    P.build_pack(srcs, plain)
    with P.Pack.open(plain) as pk:
        assert pk.header["fast_algo"] == 0 and all(e["fast"] == bytes(16) for e in pk.entries)
        assert all(f == bytes(16) for _s, f in pk.chunks) and pk.footer["manifest_fast"] == bytes(16)
    # with a (fake) module the fields are filled, verified, and a flipped byte is caught
    fake = types.SimpleNamespace(xxh3_128_digest=lambda b: hashlib.blake2b(b, digest_size=16).digest())
    monkeypatch.setitem(sys.modules, "xxhash", fake)
    P.build_pack(srcs, out, fast="xxh3")
    with P.Pack.open(out) as pk:
        assert pk.header["fast_algo"] == 1
        assert all(e["fast"] == hashlib.blake2b(pk.read_entry(i), digest_size=16).digest()
                   for i, e in enumerate(pk.entries))
        assert pk.footer["manifest_fast"] == hashlib.blake2b(pk.manifest, digest_size=16).digest()
    assert P.verify_pack(out)[0] == []
    # without the module a fast-hash pack still verifies on SHA-256 and says what it skipped
    monkeypatch.setitem(sys.modules, "xxhash", None)
    fs = P.verify_pack(out)[0]
    assert [f.code for f in fs] == ["FAST_SKIPPED"] and fs[0].sev == "info"


def test_pack_error_types(tmp_path):
    p = tmp_path / "junk.saepak"
    p.write_bytes(bytes(1000))
    with pytest.raises(P.PackError) as e:
        P.Pack.open(p)
    assert e.value.code == "BAD_MAGIC"
    p.write_bytes(b"x")
    with pytest.raises(P.PackError):
        P.Pack.open(p)
    assert struct.calcsize("<I") == 4


def test_a_pack_stays_below_4_gib(mod_dir, tmp_path, monkeypatch):
    srcs, _ = collect([str(mod_dir)])
    ok = tmp_path / "ok.saepak"
    P.build_pack(srcs, ok)
    size = ok.stat().st_size
    monkeypatch.setattr(L, "MAX_PACK_BYTES", size - 1)
    with pytest.raises(SatkError, match="4 GiB") as e:
        P.build_pack(srcs, tmp_path / "big.saepak")
    assert e.value.code == "BAD_PARAMS" and "split" in e.value.hint and not (tmp_path / "big.saepak").exists()
    fs = P.verify_pack(ok)[0]                          # a pack that grew past the limit after the fact
    assert [(f.code, f.sev) for f in fs] == [("TOO_LARGE", "warn")]
    monkeypatch.undo()
    assert L.MAX_PACK_BYTES == 4 * 1024 ** 3 - 2048 and P.verify_pack(ok)[0] == []
