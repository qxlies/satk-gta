"""Fuzz: random truncations and bit flips of synthetic IMG/TXD/DFF/COL/IFP/bnry/RW give only FormatError.

SPEC §5.3 WP-02 acceptance 1: >= 2 000 mutated inputs for EACH of the card formats IMG (VER2 and v1
``.dir``), TXD, DFF and COL, plus the other parsers; no hangs, no ``IndexError``/``struct.error``/
``MemoryError``; the whole module stays well under the 20 s budget. Every binary target must actually hit
its error paths (some mutations fail), and the DFF/COL/IFP targets must also survive some mutations
(the parsers are not just rejecting everything). IMG archives are also fuzzed as files on disk
(``ImgArchive.open`` + reading every entry), and DOS paths of DAT lines against ``resolve_ci``.
"""

from __future__ import annotations

import random
import struct
import time
from pathlib import Path

import pytest

from satk.formats.col import iter_col
from satk.formats.dat import resolve_ci, split_dos_path
from satk.formats.dff import decode_geometries, scan_dff
from satk.formats.dxt import decode_rgba, mean_rgba, preview_rgba
from satk.formats.ide import parse_ide
from satk.formats.ifp import parse_ifp
from satk.formats.img import ImgArchive, parse_v1_directory, parse_ver2_directory
from satk.formats.ipl import parse_ipl_binary, parse_ipl_text
from satk.formats.rw import FormatError, iter_children, rw_payload_size
from satk.formats.txd import mip0_bytes, palette_bytes, parse_txd, texture_hash
from satk.formats.zon import parse_zon

#: Mutated inputs per target: the card formats get >= 2 000 each, the other parsers fewer.
CASES = {"txd": 2000, "img": 2000, "img_v1": 2000, "dff": 2000, "col": 2000}
CASES_OTHER = 400


def _mutate(rng: random.Random, data: bytes) -> bytes:
    d = bytearray(data)
    op = rng.random()
    if op < 0.35 and d:
        return bytes(d[: rng.randrange(len(d))])                   # truncate
    flips = rng.randint(1, 8)
    for _ in range(flips):
        if not d:
            break
        i = rng.randrange(len(d))
        if rng.random() < 0.5:
            d[i] ^= 1 << rng.randrange(8)                            # bit flip
        else:
            d[i] = rng.choice((0, 0xFF, 0x7F, 0x80))                 # extreme byte
    if op > 0.9 and len(d) >= 16:                                    # corrupt a size field
        pos = rng.randrange(0, len(d) - 4)
        struct.pack_into("<I", d, pos, rng.choice((0, 1, 0x7FFFFFFF, 0xFFFFFFFF, len(d))))
    return bytes(d)


def _parse_txd_fully(buf: bytes) -> None:
    t = parse_txd(buf)
    for x in t.textures:
        texture_hash(buf, x)
        if not x.unsupported:
            m0, pal = mip0_bytes(buf, x), palette_bytes(buf, x)
            decode_rgba(x, m0, pal, backend="python")
            preview_rgba(x, m0)
            mean_rgba(x, m0, pal)


def _walk(buf: bytes) -> None:
    def rec(s, e, depth):
        for ch in iter_children(buf, s, e):
            if depth < 6 and ch.type in (0x10, 0x16, 0x15, 0x0E, 0x1A, 0x0F, 0x03):
                rec(ch.data_off, ch.end, depth + 1)

    rw_payload_size(buf)
    if len(buf) >= 12:
        rec(0, len(buf), 0)


def _ver2(buf: bytes) -> None:
    parse_ver2_directory(buf, file_size=len(buf))


def _dff(buf: bytes) -> None:
    info = scan_dff(buf)
    meshes = decode_geometries(buf)
    assert len(meshes) == len(info.geoms)
    for m in meshes:
        assert len(m.tris) == 3 * len(m.mat_ids)
        assert not m.tris or max(m.tris) < len(m.positions) // 3 or not m.positions


def _col(buf: bytes) -> None:
    list(iter_col(buf))
    list(iter_col(buf, strict=True))


def _targets(b):
    tx = b.txd([b.native("a"), b.native("p", platform=8, raster=0x2600, flags=0, depth=8, w=4, h=4,
                                         levels=[bytes(16)], palette=bytes(1024)),
                b.native("m", fmt="DXT3", raster=0x300, levels=[bytes(16), bytes(16)]),
                b.native("r", fmt=21, raster=0x500, w=2, h=2, levels=[bytes(16)], flags=1)])
    img = b.ver2([("a.txd", tx), ("b.dff", b.chunk(0x10, b.chunk(0x01, b"\0" * 12)))])
    bn = b.bnry([(1, 2, 3, 0, 0, 0, 1, 5, 0, -1)] * 4, [(1, 2, 3, 0, 1, 2, 3, 4, 5, 6, 7, 8)])
    d1, _ = b.v1([("a.dff", b"x"), ("b.txd", b"y")])
    ide = b"objs\n1, a, b, 10, 0\nend\ncars\n400, c, c, car, H, G, null, normal, 10, 0, 0\nend\ntxdp\na, b\nend\n"
    ipl = b"inst\n1, a, 0, 1, 2, 3, 0, 0, 0, 1, -1\nend\nenex\n1, 2, 3, 4\nend\n"
    col = b.col_v23("car_col", 3, spheres=[(0, 0, 0, 1, 9)], faces=[(0, 1, 2, 63), (1, 2, 3, 4)],
                    verts=[(0, 0, 0), (128, 0, 0), (0, 128, 0), (0, 0, 128)], shadow=[(0, 1, 2, 1)])
    pos = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (1.0, 1.0, 1.0)]
    g1 = b.geometry(pos, tris=[(0, 1, 2, 0)], strips=[(0, [0, 1, 2, 3, 3, 0])], night=True, normals=True,
                    fx2d=b.fx_entries(b.fx_light()), mats=[b.material(tex="a", mask="m"), b.material((60, 255, 0, 255))])
    g2 = b.geometry(pos, lists=[(1, [0, 1, 2, 2, 1, 3])], uv_sets=2, prelit=False,
                    mats=[b.material(), b.material(tex="b")], mat_refs=[-1, -1, 0])
    dff = b.chunk(0x2B, b.chunk(0x01, b"\0" * 4)) + b.clump(
        [g1, g2], frames=[(-1, "root", (0.0, 0.0, 0.0)), (0, "child", (1.0, 2.0, 3.0))],
        atomics=[(0, 0), (1, 1)], col=col, lights=[1])
    col_file = b.col_v1("w", spheres=[(1, 0, 0, 0, 1)], boxes=[((0, 0, 0), (1, 1, 1), 2)],
                        verts=[(0, 0, 0)] * 3, faces=[(0, 1, 2, 3)]) + col + b.col_v23(
                            "c2", 2, verts=[(0, 0, 0)], faces=[(0, 0, 0, 1)])
    anp3 = b.anp3("ped", [("walk", [("root", 4, 3), ("arm", 3, 2)]), ("idle", [("root", 1, 2)])])
    anpk = b.anpk("cut", [("a", [("root", 2, b"KRT0"), ("x", 1, b"KR00")]), ("b", [("s", 1, b"KRTS")])])
    zon = b"zone\nGAN1, 0, 1, 2, 3, 4, 5, 6, 1, GAN1\nend\n"
    return [
        ("txd", tx, _parse_txd_fully),
        ("img", img, _ver2),
        ("img_v1", d1, parse_v1_directory),
        ("bnry", bn, parse_ipl_binary),
        ("rw", b.chunk(0x10, b.chunk(0x01, b"\1" * 8) + b.chunk(0x0E, b.chunk(0x01, b"\0" * 8))), _walk),
        ("ide", ide, lambda d: parse_ide(d.decode("latin-1"))),
        ("ipl", ipl, lambda d: parse_ipl_text(d.decode("latin-1"))),
        ("dff", dff, _dff),
        ("col", col_file, _col),
        ("anp3", anp3, parse_ifp),
        ("anpk", anpk, parse_ifp),
        ("zon", zon, lambda d: parse_zon(d.decode("latin-1"))),
    ]


def test_targets_parse_cleanly(b):
    """The unmutated inputs are valid (otherwise the fuzz would only test the first error path)."""
    for name, data, fn in _targets(b):
        fn(data)


def test_fuzz_only_format_errors(b):
    rng = random.Random(20261004)
    targets = _targets(b)
    t0 = time.perf_counter()
    stats = {name: [0, 0] for name, _, _ in targets}
    worst = 0.0
    for name, data, fn in targets:
        for _ in range(CASES.get(name, CASES_OTHER)):
            bad = _mutate(rng, data)
            t1 = time.perf_counter()
            try:
                fn(bad)
                stats[name][0] += 1
            except FormatError:
                stats[name][1] += 1
            except Exception as e:  # noqa: BLE001 - this is what the test hunts for
                pytest.fail(f"{name}: {type(e).__name__}: {e} on input {bad[:64].hex()}...")
            worst = max(worst, time.perf_counter() - t1)
    elapsed = time.perf_counter() - t0
    assert elapsed < 20 and worst < 2, (elapsed, worst)
    for name, n in CASES.items():                  # >= 2 000 per card format (IMG v2 and v1 each)
        assert sum(stats[name]) == n >= 2000, (name, stats[name])
    # the mutations must exercise the error paths of the binary parsers ...
    for name in ("txd", "img", "bnry", "rw", "dff", "col", "anp3", "anpk"):
        assert stats[name][1] > 0, (name, stats[name])
    # ... and the tolerant ones must survive some of them
    for name in ("dff", "col", "anp3", "anpk", "txd"):
        assert stats[name][0] > 0, (name, stats[name])


def test_fuzz_img_files_on_disk(b, tmp_path: Path):
    """``ImgArchive.open`` + reading every entry of mutated VER2 / v1 (.dir + .img) files: only FormatError."""
    rng = random.Random(4242)
    tx = b.txd([b.native("a")])
    files = [("a.txd", tx), ("b.dff", b"\x10" * 3000), ("c.col", b"COL3" + bytes(60))]
    ver2 = b.ver2(files)
    d1, img1 = b.v1(files)
    seen = [0, 0]
    for i in range(160):
        p = tmp_path / f"v{i}.img"
        if i % 2:
            p.write_bytes(_mutate(rng, ver2))
        else:
            p.write_bytes(_mutate(rng, img1) if i % 4 == 0 else img1)
            p.with_suffix(".dir").write_bytes(_mutate(rng, d1) if i % 4 else d1)
        try:
            with ImgArchive.open(p) as a:
                for e in a.entries:
                    a.read(e)
            seen[0] += 1
        except FormatError:
            seen[1] += 1
        except Exception as e:  # noqa: BLE001
            pytest.fail(f"{p.name}: {type(e).__name__}: {e}")
    assert seen[0] and seen[1], seen


_PARTS = ["data", "DATA", "maps", "x.ide", "..", ".", "...", ". .", "C:", "c:\\windows", "D:x", "\\\\srv\\s",
          "/", "\\", "NUL", "con.txt", "COM1", "lpt9.img", "a:b", "a*b", "\x00", "\t", "gta3.img", "models", " "]


def test_fuzz_dos_paths_stay_inside_root(tmp_path: Path):
    """Crafted DAT paths: ``resolve_ci`` returns ``None`` or a path made of plain names under the root."""
    root = tmp_path / "root"
    (root / "data" / "maps").mkdir(parents=True)
    (root / "data" / "maps" / "x.ide").write_text("objs\nend\n", encoding="latin-1")
    (root / "models").mkdir()
    (root / "models" / "gta3.img").write_bytes(b"VER2" + bytes(4))
    rng = random.Random(7)
    hits = 0
    for _ in range(3000):
        dos = rng.choice(("\\", "/")).join(rng.choice(_PARTS) for _ in range(rng.randint(1, 4)))
        p = resolve_ci(root, dos)
        parts = split_dos_path(dos)
        if p is None:
            continue
        hits += 1
        assert parts is not None and p.relative_to(root).parts and ".." not in p.relative_to(root).parts, dos
        assert p.resolve().is_relative_to(root.resolve()), (dos, p)
    assert hits > 0
