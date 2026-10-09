"""The pack verifier: every finding code fires on a deliberately damaged pack, and a good pack stays clean."""

from __future__ import annotations

import hashlib
import struct
import sys
import types
import zlib
from pathlib import Path

import pytest

from satk.core.registry import invoke
from satk.pack import layout as L
from satk.pack import saepak as P
from satk.pack.sources import collect


@pytest.fixture
def good(mod_dir, tmp_path) -> Path:
    out = tmp_path / "good.saepak"
    srcs, _ = collect([str(mod_dir)])
    P.build_pack(srcs, out, namespace="demo", chunk_size=2048)      # small chunks: the col spans several
    return out


def codes(path, **kw) -> dict[str, str]:
    """``{code: severity}`` of the findings (the last severity wins)."""
    return {f.code: f.sev for f in P.verify_pack(path, **kw)[0]}


def edit_manifest(src: Path, dst: Path, edits, *, fix: bool = True) -> Path:
    """Copy ``src`` to ``dst`` with manifest-relative edits ``(offset, bytes)``; ``fix`` repairs the footer hash."""
    raw = bytearray(src.read_bytes())
    with P.Pack.open(src) as pk:
        mo, ms, algo = pk.footer["manifest_offset"], pk.footer["manifest_size"], pk.header["fast_algo"]
    for off, data in edits:
        raw[mo + off:mo + off + len(data)] = data
    if fix:
        raw[-L.FOOTER.size:] = P.encode_footer(manifest=bytes(raw[mo:mo + ms]), manifest_offset=mo,
                                               pack_size=len(raw), fast_algo=algo)
    dst.write_bytes(bytes(raw))
    return dst


def field(pk: P.Pack, table: str, i: int, name: str) -> int:
    rec, key = {"entry": (L.ENTRY, "entries_off"), "name": (L.NAME, "names_off"),
                "header": (L.HEADER, None)}[table]
    base = 0 if key is None else pk.header[key] + i * rec.size
    return base + rec.by_name[name].offset


def pk_of(path: Path) -> P.Pack:
    return P.Pack.open(path)


def test_a_good_pack_is_clean(good):
    assert P.verify_pack(good)[0] == []
    assert P.verify_pack(good, deep=False)[0] == []


def test_payload_damage_is_found_by_entry_and_chunk_hashes(good, tmp_path):
    with pk_of(good) as pk:
        i = pk.names[pk.name_index("col/car.col")]["entry_index"]
        off = pk.entries[i]["offset"] + 5000                          # in the third 2048-byte chunk
    raw = bytearray(good.read_bytes())
    raw[off] ^= 1
    bad = tmp_path / "bad.saepak"
    bad.write_bytes(raw)
    fs = P.verify_pack(bad)[0]
    assert {f.code for f in fs} == {"ENTRY_HASH", "CHUNK_HASH"}
    assert "chunk 2" in next(f.msg for f in fs if f.code == "CHUNK_HASH") and "car.col" in fs[0].msg
    assert codes(bad, deep=False) == {}                               # structure only: not seen


def test_manifest_and_footer_damage(good, tmp_path):
    with pk_of(good) as pk:
        mo = pk.footer["manifest_offset"]
    raw = bytearray(good.read_bytes())
    raw[mo + 300] ^= 0xFF
    p = tmp_path / "m.saepak"
    p.write_bytes(raw)
    assert codes(p)["MANIFEST_HASH"] == "error"
    raw = bytearray(good.read_bytes())
    raw[-100] ^= 0xFF                                                 # inside the footer's hash field
    p.write_bytes(raw)
    assert codes(p) == {"FOOTER_CRC": "error"}
    raw = bytearray(good.read_bytes())
    raw[-3] ^= 0xFF                                                   # inside the reserved tail: the CRC does not cover it
    p.write_bytes(raw)
    assert codes(p) == {"RESERVED": "warn"}                          # outside the CRC span, but checked for zero


def test_truncation_and_garbage(good, tmp_path):
    raw = good.read_bytes()
    p = tmp_path / "t.saepak"
    p.write_bytes(raw[:-1])
    assert codes(p) == {"BAD_MAGIC": "error"}                        # the footer is no longer last
    p.write_bytes(raw + b"extra")
    assert codes(p) == {"BAD_MAGIC": "error"}
    p.write_bytes(bytes(5000))
    assert codes(p) == {"BAD_MAGIC": "error"}
    p.write_bytes(b"tiny")
    assert codes(p) == {"BAD_MAGIC": "error"}
    # a footer that claims another pack size
    with pk_of(good) as pk:
        ft = dict(pk.footer)
    ft["pack_size"] += 8
    ft["crc32"] = 0
    raw = bytearray(raw)
    head = L.FOOTER.pack(**ft)
    raw[-128:] = L.FOOTER.pack(**{**ft, "crc32": zlib.crc32(head[:88])})
    p.write_bytes(raw)
    assert codes(p) == {"SIZE_MISMATCH": "error"}


def test_unsupported_version(good, tmp_path):
    with pk_of(good) as pk:
        ft = dict(pk.footer)
    ft["major"] = 2
    head = L.FOOTER.pack(**{**ft, "crc32": 0})
    raw = bytearray(good.read_bytes())
    raw[-128:] = L.FOOTER.pack(**{**ft, "crc32": zlib.crc32(head[:88])})
    p = tmp_path / "v2.saepak"
    p.write_bytes(raw)
    assert codes(p) == {"BAD_VERSION": "error"}


def test_img_directory_mismatch(good, tmp_path):
    raw = bytearray(good.read_bytes())
    struct.pack_into("<I", raw, 8, 99)                                # slot 0: another offset
    p = tmp_path / "d.saepak"
    p.write_bytes(raw)
    assert codes(p)["DIR_MISMATCH"] == "error"
    raw = bytearray(good.read_bytes())
    struct.pack_into("<I", raw, 4, 3)                                 # the count disagrees with the manifest
    p.write_bytes(raw)
    assert codes(p)["DIR_MISMATCH"] == "error"
    raw = bytearray(good.read_bytes())
    raw[8 + 8:8 + 8 + 5] = b"zzzzz"                                   # a stream name that no logical name explains
    p.write_bytes(raw)
    assert codes(p)["STREAM_NAME"] == "error"
    raw = bytearray(good.read_bytes())
    raw[8 + 32 + 8:8 + 32 + 8 + 24] = raw[8 + 8:8 + 8 + 24]            # two slots with one name
    p.write_bytes(raw)
    assert codes(p)["STREAM_NAME_DUP"] == "error"


def test_manifest_semantics(good, tmp_path):
    out = tmp_path / "x.saepak"
    with pk_of(good) as pk:
        pid = field(pk, "header", 0, "pack_id")
        e0 = {n: field(pk, "entry", 0, n) for n in ("offset", "size", "stream_sectors", "first_chunk", "chunk_count",
                                                    "flags", "sha256")}
        e1_off = field(pk, "entry", 1, "offset")
        n0 = {n: field(pk, "name", 0, n) for n in ("dir_index", "entry_index", "logical_off", "logical_len")}
        n1 = {n: field(pk, "name", 1, n) for n in ("logical_off", "logical_len")}
        res = field(pk, "header", 0, "reserved1")
        eoff0 = pk.entries[0]["offset"]
        e0size = pk.entries[0]["size"]
        names = [dict(n) for n in pk.names]
    one = lambda v: struct.pack("<I", v)          # noqa: E731
    cases = {
        "PACK_ID": ([(pid, bytes(32))], "error"),
        "ENTRY_SIZE": ([(e0["stream_sectors"], one(9))], "error"),
        "ALIGN": ([(e0["offset"], struct.pack("<Q", eoff0 + 8))], "error"),
        "OVERLAP": ([(e1_off, struct.pack("<Q", eoff0))], "error"),
        "CHUNK_RANGE": ([(e0["first_chunk"], one(1))], "error"),
        "NAME_DIR": ([(n0["dir_index"], one(77))], "error"),
        "NAME_ENTRY": ([(n0["entry_index"], one(77))], "error"),
        "NAME_DUP": ([(n1["logical_off"], one(names[0]["logical_off"])), (n1["logical_len"], one(names[0]["logical_len"]))],
                     "error"),
        "RESERVED": ([(res, b"\x01")], "warn"),
        "ENTRY_DUP": ([(e0["sha256"], hashlib.sha256(b"x").digest())], None),   # placeholder: see below
    }
    cases.pop("ENTRY_DUP")
    for code, (edits, sev) in cases.items():
        p = edit_manifest(good, out, edits)
        found = codes(p)
        assert found.get(code) == sev, (code, found)
    # two entries with one hash
    with pk_of(good) as pk:
        sha1 = pk.entries[1]["sha256"]
    p = edit_manifest(good, out, [(e0["sha256"], sha1)])
    assert codes(p)["ENTRY_DUP"] == "error"
    # an entry flag set: reserved/unknown
    p = edit_manifest(good, out, [(e0["flags"], b"\x01")])
    assert codes(p).get("RESERVED") == "warn"
    assert e0size > 0


def test_padding_and_gaps(good, tmp_path):
    with pk_of(good) as pk:
        e = pk.entries[0]
        off = e["offset"] + e["size"]                                  # the first padding byte after the entry
        assert e["size"] % 2048
    raw = bytearray(good.read_bytes())
    raw[off] = 7
    p = tmp_path / "pad.saepak"
    p.write_bytes(raw)
    found = codes(p)
    assert found == {"PADDING": "warn"}


def test_footer_manifest_size_rules(good, tmp_path):
    # a manifest size that is not a multiple of 8 is refused before anything is read
    with pk_of(good) as pk:
        ft = dict(pk.footer)
    ft["manifest_size"] -= 3
    head = L.FOOTER.pack(**{**ft, "crc32": 0})
    raw = bytearray(good.read_bytes())
    raw[-128:] = L.FOOTER.pack(**{**ft, "crc32": zlib.crc32(head[:88])})
    p = tmp_path / "ms.saepak"
    p.write_bytes(raw)
    assert codes(p) == {"MANIFEST_RANGE": "error"}


def test_fast_hash_mismatch_is_an_error(mod_dir, tmp_path, monkeypatch):
    fake = types.SimpleNamespace(xxh3_128_digest=lambda b: hashlib.blake2b(b, digest_size=16).digest())
    monkeypatch.setitem(sys.modules, "xxhash", fake)
    out = tmp_path / "f.saepak"
    srcs, _ = collect([str(mod_dir)])
    P.build_pack(srcs, out, fast="xxh3", chunk_size=2048)
    assert codes(out) == {}
    with pk_of(out) as pk:
        ef = field(pk, "entry", 0, "fast")
    p = edit_manifest(out, tmp_path / "f2.saepak", [(ef, bytes(range(16)))])
    assert codes(p).get("FAST_HASH") == "error"


def test_op_verify_reports_check_failed(good, tmp_path):
    ok = invoke("pack.verify", {"target": str(good)})
    assert ok["ok"] and ok["summary"]["verdict"] == "pass" and ok["summary"]["entries"] == 4
    raw = bytearray(good.read_bytes())
    with pk_of(good) as pk:
        raw[pk.entries[0]["offset"]] ^= 1
    bad = tmp_path / "bad.saepak"
    bad.write_bytes(raw)
    env = invoke("pack.verify", {"target": str(bad)})
    assert env["ok"] is False and env["error"]["code"] == "CHECK_FAILED"
    assert {r[1] for r in env["error"]["data"]["rows"]} == {"ENTRY_HASH", "CHUNK_HASH"}
    assert invoke("pack.verify", {"target": str(bad), "deep": False})["ok"]
    missing = invoke("pack.verify", {"target": str(tmp_path / "nope.saepak")})
    assert missing["error"]["code"] == "NOT_FOUND"
    assert invoke("pack.verify", {"target": str(tmp_path)})["error"]["code"] == "NOT_FOUND"     # a folder is no pack


def test_random_damage_never_crashes_the_reader(satk_home, good, tmp_path):
    """400 damaged copies (directory, manifest with a repaired footer, footer, anywhere): findings, never a crash."""
    import random

    rng = random.Random(20261008)
    raw = good.read_bytes()
    with pk_of(good) as pk:
        mo, ms, algo = pk.footer["manifest_offset"], pk.footer["manifest_size"], pk.header["fast_algo"]
    p = tmp_path / "fz.saepak"
    errors = oks = 0
    for _ in range(400):
        b = bytearray(raw)
        repair = rng.random() < 0.7
        for _k in range(rng.randint(1, 4)):
            zone = rng.choice(("dir", "manifest", "manifest", "footer", "any"))
            lo, hi = {"dir": (0, 400), "manifest": (mo, mo + ms), "footer": (len(b) - 128, len(b)),
                      "any": (0, len(b))}[zone]
            b[rng.randrange(lo, hi)] = rng.randrange(256)
        if repair:      # push the damage past the manifest hash and the footer CRC
            b[-L.FOOTER.size:] = P.encode_footer(manifest=bytes(b[mo:mo + ms]), manifest_offset=mo,
                                                 pack_size=len(b), fast_algo=algo)
        p.write_bytes(b)
        findings, _stats = P.verify_pack(p)
        assert all(f.sev in ("error", "warn", "info") and f.code for f in findings)
        errors += any(f.sev == "error" for f in findings)
        oks += not findings
        for op, args in (("pack.inspect", {"target": str(p)}), ("pack.inspect", {"target": str(p), "show": "entries"}),
                         ("pack.verify", {"target": str(p), "deep": False})):
            env = invoke(op, args)
            assert env.get("error", {}).get("code") != "INTERNAL", (op, env)
    assert errors > 100 and oks < 100        # the damage is found, not ignored
