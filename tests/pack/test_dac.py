"""The DAC file: element encodings, the container, corruption detection, derivation from a synthetic DFF."""

from __future__ import annotations

import hashlib
import json
import math
import struct
import zlib

import pytest

from satk.core.registry import invoke
from satk.pack import dac as D
from satk.pack import layout as L


def angle_deg(a, b) -> float:
    d = max(-1.0, min(1.0, a[0] * b[0] + a[1] * b[1] + a[2] * b[2]))
    return math.degrees(math.acos(d))


def unit(x, y, z):
    n = math.sqrt(x * x + y * y + z * z)
    return x / n, y / n, z / n


def test_octahedral_normals_round_trip():
    vecs = [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1), (1, 1, 1), (-1, -1, -1), (1, -2, 3),
            (-0.3, 0.2, -0.9), (0.001, 0.0, -1.0)]
    vecs += [(math.sin(i) * math.cos(j), math.sin(i) * math.sin(j), math.cos(i))
             for i in (0.1, 0.9, 1.5, 2.2, 3.0) for j in (0.0, 1.0, 2.5, 4.0, 5.5)]
    for v in vecs:
        u = unit(*v)
        ix, iy = D.oct_encode(*u)
        assert -32767 <= ix <= 32767 and -32767 <= iy <= 32767
        assert angle_deg(u, D.oct_decode(ix, iy)) < 0.01, v
    assert D.oct_encode(0, 0, 0) == (0, 0)                               # a zero vector is +Z
    assert D.oct_decode(0, 0) == (0.0, 0.0, 1.0)
    flat = [c for v in vecs for c in unit(*v)]
    back = D.unpack_normals(D.pack_normals(flat))
    assert len(back) == len(vecs) and len(D.pack_normals(flat)) == 4 * len(vecs)


def test_tangents_and_lights():
    t = [(1.0, 0.0, 0.0, 1.0), (0.0, 0.6, 0.8, -1.0), (-0.5, 0.5, 0.7071, 1.0)]
    back = D.unpack_tangents(D.pack_tangents(t))
    for a, b in zip(t, back):
        assert max(abs(x - y) for x, y in zip(a, b)) < 0.01 and a[3] == b[3]
    lights = [(0.5, 0.25, 1.0, 250, 200, 100, 255, 80.0, 12.0, 1.5, 775), (2.0, 0.0, -1.0, 1, 2, 3, 4, 0.0, 1.0, 2.0, 0)]
    blob = D.pack_lights(lights)
    assert len(blob) == 64 and D.unpack_lights(blob) == lights


def sample() -> D.Dac:
    dac = D.Dac(hashlib.sha256(b"source").digest(), L.DERIVE_DFF_V1, L.SRC_DFF, {"normals": "missing", "x": 1})
    dac.sections = [
        D.DacSection(L.DAC_GEOM_STREAM, 0, 3, L.FMT_OCT16X2, D.pack_normals([0, 0, 1, 0, 1, 0, 1, 0, 0]),
                     L.STREAM_NORMAL, 4, L.SF_COMPUTED),
        D.DacSection(L.DAC_GEOM_STREAM, 0, 3, L.FMT_RGBA8, bytes(range(12)), L.STREAM_NIGHT_COLOR, 4, 0),
        D.DacSection(L.DAC_LIGHT_LIST, 1, 1, L.FMT_LIGHT_V1, D.pack_lights([(1, 2, 3, 4, 5, 6, 7, 8.0, 9.0, 10.0, 11)]),
                     0, 32, 0),
        D.DacSection(L.DAC_MIP_CHAIN, 7, 2, L.FMT_BYTES, bytes(range(40)), 0, 0, 0,
                     (int.from_bytes(b"DXT1", "little"), 8, 8, 1)),
    ]
    return dac


def test_container_round_trip_and_layout():
    dac = sample()
    blob = D.encode_dac(dac)
    assert blob[:8] == L.DAC_MAGIC and len(blob) % 16 == 0
    h = L.DAC_HEADER.unpack(blob[:128])
    assert h["section_count"] == 5 and h["section_table_off"] == 128 and h["file_size"] == len(blob)
    assert h["source_sha256"] == dac.source_sha256 and h["cache_key"] == dac.key
    assert h["crc32"] == zlib.crc32(blob[:112])
    for i in range(h["section_count"]):
        r = L.DAC_SECTION.unpack(blob[128 + 64 * i:128 + 64 * (i + 1)])
        assert r["offset"] % 16 == 0 and r["offset"] >= 128 + 64 * 5
        assert zlib.crc32(blob[r["offset"]:r["offset"] + r["size"]]) == r["crc32"]
    back = D.decode_dac(blob)
    assert back.source_sha256 == dac.source_sha256 and back.params == dac.params and back.derive_id == 1
    assert [(s.kind, s.index, s.stream, s.count, s.format, s.data, s.flags, s.aux) for s in back.sections] == \
        sorted([(s.kind, s.index, s.stream, s.count, s.format, s.data, s.flags, s.aux) for s in dac.sections],
               key=lambda t: (t[0], t[1], t[2]))
    assert back.find(L.DAC_GEOM_STREAM, 0, L.STREAM_NIGHT_COLOR).data == bytes(range(12))
    assert D.encode_dac(back) == blob                                      # the encoding is canonical
    assert D.verify_dac(blob) == []
    summary, rows = D.describe_dac(blob)
    assert summary["sections"] == 4 and summary["source_kind"] == "dff" and len(rows) == 4
    assert ["geom_stream", "normal", 0, 3, "oct16x2", 12, "computed"] in rows


def test_cache_key_covers_source_recipe_and_params():
    sha = hashlib.sha256(b"a").digest()
    k = D.cache_key(sha, 1, {"a": 1, "b": 2})
    h = hashlib.sha256()
    h.update(b"DACKEY1" + bytes([10]) + sha + struct.pack("<I", 1) + b'{"a":1,"b":2}')
    assert k == h.digest()                                                 # the documented formula
    assert D.cache_key(sha, 1, {"b": 2, "a": 1}) == k                      # key order does not matter
    assert len({k, D.cache_key(hashlib.sha256(b"b").digest(), 1, {"a": 1, "b": 2}), D.cache_key(sha, 2, {"a": 1, "b": 2}),
                D.cache_key(sha, 1, {"a": 1, "b": 3})}) == 4
    p = D.dac_cache_path("root", k)
    assert p.parts[-2:] == (k.hex()[:2], f"{k.hex()}.dac")


def test_corruption_is_detected():
    blob = bytearray(D.encode_dac(sample()))

    def code(b) -> str:
        fs = D.verify_dac(bytes(b))
        assert fs and fs[0].sev == "error"
        return fs[0].code

    assert code(b"") == "BAD_MAGIC" and code(bytes(300)) == "BAD_MAGIC"
    b = bytearray(blob)
    b[20] ^= 1
    assert code(b) == "DAC_CRC"                                            # header
    b = bytearray(blob)
    b[-20] ^= 1                                                            # last payload bytes (the meta JSON)
    assert code(b) == "DAC_CRC"
    assert code(blob + b"\0") == "SIZE_MISMATCH" and code(blob[:-16]) == "SIZE_MISMATCH"
    # header edits with a repaired CRC
    def fix(mut):
        h = L.DAC_HEADER.unpack(bytes(blob[:128]))
        mut(h)
        head = L.DAC_HEADER.pack(**{**h, "crc32": 0})
        return head[:112] + struct.pack("<I", zlib.crc32(head[:112])) + head[116:] + bytes(blob[128:])

    assert code(fix(lambda h: h.update(major=2))) == "BAD_VERSION"
    assert code(fix(lambda h: h.update(section_count=9999))) == "DAC_FORMAT"
    assert code(fix(lambda h: h.update(source_sha256=bytes(32)))) == "CACHE_KEY"
    # a payload whose CRC is right but whose size does not fit its format
    b = bytearray(blob)
    first = struct.unpack_from("<Q", b, 128 + 16)[0]
    struct.pack_into("<I", b, 128 + 8, 4)                                  # count 4, the payload holds 3 normals
    assert code(b) == "DAC_FORMAT"
    assert first % 16 == 0


def test_derive_dff_streams(synth):
    w: list[str] = []
    dac = D.derive_dff(synth.dff(lights=True), warn=w)
    assert w == []
    n = dac.find(L.DAC_GEOM_STREAM, 0, L.STREAM_NORMAL)
    assert n.count == 4 and n.flags & L.SF_COMPUTED and n.stride == 4 and len(n.data) == 16
    normals = D.unpack_normals(n.data)
    assert all(abs(math.sqrt(sum(c * c for c in v)) - 1) < 1e-3 for v in normals)
    assert angle_deg(normals[0], (0, 0, 1)) < 0.1                            # a corner of the flat triangle: +Z
    assert normals[3][2] > 0 and normals[3][0] < 0                           # the raised corner leans away
    night = dac.find(L.DAC_GEOM_STREAM, 0, L.STREAM_NIGHT_COLOR)
    assert night.data == bytes((40, 40, 60, 255)) * 4 and night.format == L.FMT_RGBA8 and night.flags == 0
    tan = D.unpack_tangents(dac.find(L.DAC_GEOM_STREAM, 0, L.STREAM_TANGENT).data)
    assert len(tan) == 4
    for t, nv in zip(tan, normals):
        assert abs(sum(a * b for a, b in zip(t[:3], nv))) < 0.03            # perpendicular to the normal
        assert abs(math.sqrt(sum(c * c for c in t[:3])) - 1) < 0.02 and t[3] in (1.0, -1.0)
    assert tan[0][0] > 0.95 and tan[0][3] == 1.0                             # U runs along +X on the flat corner
    lights = D.unpack_lights(dac.find(L.DAC_LIGHT_LIST, 0).data)
    assert lights == [(0.5, 0.25, 1.0, 250, 200, 100, 255, 80.0, 12.0, 1.5, 7 | 3 << 8),
                      (2.0, 0.25, 1.0, 1, 2, 3, 4, 80.0, 12.0, 1.5, 7 | 3 << 8)]
    assert dac.source_sha256 == hashlib.sha256(synth.dff(lights=True)).digest()
    assert D.decode_dac(D.encode_dac(dac)).key == dac.key


def test_derive_modes_and_options(synth):
    src = synth.dff(normals=True, lights=True)
    missing = D.derive_dff(src)
    assert missing.find(L.DAC_GEOM_STREAM, 0, L.STREAM_NORMAL) is None       # the file has its normals
    assert missing.find(L.DAC_GEOM_STREAM, 0, L.STREAM_TANGENT) is not None  # tangents use the stored ones
    alln = D.derive_dff(src, normals="all")
    sec = alln.find(L.DAC_GEOM_STREAM, 0, L.STREAM_NORMAL)
    assert sec.flags == 0 and all(angle_deg(v, (0, 0, 1)) < 0.01 for v in D.unpack_normals(sec.data))
    bare = D.derive_dff(synth.dff(night=False, lights=False), normals="none", tangents=False)
    assert bare.sections == [] and bare.params["normals"] == "none"
    only_night = D.derive_dff(src, normals="none", tangents=False, lights=False)
    assert [(s.kind, s.stream) for s in only_night.sections] == [(L.DAC_GEOM_STREAM, L.STREAM_NIGHT_COLOR)]
    no_night = D.derive_dff(src, night=False, tangents=False, lights=False)
    assert no_night.sections == []
    # the cache key follows the options and the source
    keys = {D.derive_dff(src).key, alln.key, only_night.key, D.derive_dff(synth.dff(seed=2)).key,
            D.derive_dff(src, angle=30.0).key}
    assert len(keys) == 5
    assert D.encode_dac(D.derive_dff(src)) == D.encode_dac(D.derive_dff(src))
    with pytest.raises(ValueError):
        D.derive_dff(src, normals="some")


def test_derive_reports_damage(synth):
    w: list[str] = []
    dac = D.derive_dff(synth.dff(night_raw=struct.pack("<I", 1) + b"\x01\x02"), normals="none", tangents=False, warn=w)
    assert dac.sections == [] and w and w[0].startswith("BAD_NIGHT: geometry 0")
    with pytest.raises(ValueError):
        D.derive_dff(b"not a dff at all")


def test_op_dac_cache_and_out_folder(satk_home, synth, tmp_path):
    d = tmp_path / "models"
    d.mkdir()
    (d / "a.dff").write_bytes(synth.dff(lights=True))
    (d / "b.dff").write_bytes(synth.dff(seed=4))
    (d / "broken.dff").write_bytes(b"this is not a model file")
    (d / "c.txd").write_bytes(synth.txd([("t", 1)]))
    env = invoke("pack.dac", {"targets": [str(d)]})
    assert env["ok"] and env["total"] == 2 and [r[0] for r in env["rows"]] == ["a.dff", "b.dff"]
    assert {r[7] for r in env["rows"]} == {"new"}
    assert any(w.startswith("BAD_DFF: broken.dff") for w in env["warn"]) and any("SKIPPED" in w for w in env["warn"])
    row = env["rows"][0]
    assert row[1:5] == [1, 1, 1, 2] and row[5] > 0                           # normals, night, tangents, lights
    files = [r[8] for r in env["rows"]]
    assert all(f.startswith(str(satk_home / "work").replace("\\", "/")) and "/cache/dac/" in f for f in files)
    again = invoke("pack.dac", {"targets": [str(d)]})
    assert {r[7] for r in again["rows"]} == {"cached"} and [r[8] for r in again["rows"]] == files
    out = tmp_path / "dac_out"
    env = invoke("pack.dac", {"targets": [str(d / "a.dff")], "out": str(out), "normals": "all"})
    assert env["ok"] and env["rows"][0][7] == "written" and (out / "a.dac").is_file()
    assert D.read_dac(out / "a.dac").params["normals"] == "all"
    # the files are readable through verify / inspect
    v = invoke("pack.verify", {"target": str(out / "a.dac")})
    assert v["ok"] and v["summary"]["kind"] == "dac" and v["summary"]["verdict"] == "pass"
    ins = invoke("pack.inspect", {"target": str(out / "a.dac")})
    assert ins["ok"] and ins["summary"]["type"] == "dac" and ins["summary"]["sections"] == 4 and ins["n"] == 4
    bad = out / "bad.dac"
    raw = bytearray((out / "a.dac").read_bytes())
    raw[-20] ^= 1
    bad.write_bytes(raw)
    assert invoke("pack.verify", {"target": str(bad)})["error"]["code"] == "CHECK_FAILED"
    assert invoke("pack.inspect", {"target": str(bad)})["error"]["code"] == "UNSUPPORTED"
    assert invoke("pack.dac", {"targets": [str(d)], "angle": 0})["error"]["code"] == "BAD_PARAMS"
    assert invoke("pack.dac", {"targets": [str(d / "c.txd")]})["error"]["code"] == "BAD_PARAMS"
    assert json.dumps(env)             # serialisable


def test_op_dac_from_the_index(ws, synth):
    env = invoke("pack.dac", {"targets": ["dff:box1", "model:1001"]})
    assert env["ok"] and [r[0] for r in env["rows"]] == ["dff:box1", "model:1001"]
    assert env["rows"][0][6] == env["rows"][1][6]                            # the same synthetic bytes: the same key
    assert env["rows"][0][1] == 1 and env["rows"][0][2] == 1
    assert invoke("pack.dac", {"targets": ["dff:nosuchmodel"]})["error"]["code"] == "NOT_FOUND"


def test_random_damage_to_a_dac_gives_findings_not_crashes(synth):
    import random

    rng = random.Random(7)
    blob = D.encode_dac(D.derive_dff(synth.dff(lights=True)))
    bad = 0
    for _ in range(500):
        b = bytearray(blob)
        for _k in range(rng.randint(1, 3)):
            b[rng.randrange(len(b))] = rng.randrange(256)
        if rng.random() < 0.5:
            b = b[:rng.randrange(len(b))]
        fs = D.verify_dac(bytes(b))               # must not raise
        bad += any(f.sev == "error" for f in fs)
        if not any(f.sev == "error" for f in fs):
            D.describe_dac(bytes(b))              # whatever verifies also describes
    assert bad > 400


def test_op_dac_out_folder_keeps_models_with_one_file_name(satk_home, synth, tmp_path):
    for sub, seed in (("x", 1), ("y", 2)):
        (tmp_path / "m" / sub).mkdir(parents=True)
        (tmp_path / "m" / sub / "a.dff").write_bytes(synth.dff(seed=seed))
    out = tmp_path / "o"
    env = invoke("pack.dac", {"targets": [str(tmp_path / "m")], "out": str(out)})
    assert env["ok"] and env["total"] == 2
    names = sorted(p.name for p in out.glob("*.dac"))
    assert len(names) == 2 and "a.dac" in names
    assert any(n.startswith("a.") and n != "a.dac" for n in names)
    assert len({D.read_dac(out / n).key for n in names}) == 2
