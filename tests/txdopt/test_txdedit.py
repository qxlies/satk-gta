"""TXD rebuild, level access, the lossless DXT3/DXT5 -> DXT1 transcode and size planning (no game files)."""

from __future__ import annotations

import random
import struct

import numpy as np
import pytest

from satk.formats.dxt import decode_rgba
from satk.formats.txd import mip0_bytes, parse_txd
from satk.texmod.txdwrite import NativeSpec, native_chunk
from satk.txdopt.analyze import chain_bytes, facts, fmt_label, full_levels
from satk.txdopt.optimize import _pot_side, target_dims
from satk.txdopt.txdedit import dxt_to_dxt1, level_list, native_spans, rebuild


def _txd(synth):
    return synth.txd([synth.native("a", synth.smooth(8, 8, 1), "DXT1"),
                      synth.native("b", synth.smooth(16, 8, 2), "A8R8G8B8", mips=True),
                      synth.native("c", synth.smooth(4, 4, 3), "DXT5")])


def test_rebuild_without_changes_is_identity(synth):
    data = _txd(synth)
    assert rebuild(data, {}) == data
    spans = native_spans(data)
    assert len(spans) == 3 and spans[0][0] == 12 + 16 and spans[-1][1] == len(data) - 12


def test_rebuild_drops_replaces_and_counts(synth):
    data = _txd(synth)
    new_c = synth.native("c2", synth.smooth(4, 4, 9), "DXT1")
    out = rebuild(data, {0: None, 2: new_c})
    t = parse_txd(out)
    assert [x.name for x in t.textures] == ["b", "c2"] and t.count == 2
    assert rebuild(out, {}, [synth.native("d", synth.smooth(4, 4, 4), "DXT1")]).count(b"d\0") >= 1
    with pytest.raises(ValueError, match="no TextureNative number 5"):
        rebuild(data, {5: None})


def test_level_list_and_facts(synth):
    data = _txd(synth)
    t = parse_txd(data).textures[1]
    lv = level_list(data, t)
    assert len(lv) == full_levels(16, 8) == 5 and len(lv[0]) == 16 * 8 * 4 and len(lv[-1]) == 4
    s, e = native_spans(data)[1]
    f = facts(data, t, e - s)
    assert f.data == sum(len(x) for x in lv) and f.alpha == "opaque" and len(f.hash) == 24
    assert f.label == "A8R8G8B8 16x8 m5" == fmt_label("A8R8G8B8", True, 16, 8, 5)
    assert chain_bytes("DXT1", 16, 16, 1) == 4 + 128 and chain_bytes("DXT1", 4, 4, 3) == 3 * (4 + 8)


def _decode(fmt: str, w: int, h: int, level: bytes, alpha: bool) -> bytes:
    tx = parse_txd(_one(fmt, w, h, level, alpha))
    t = tx.textures[0]
    return decode_rgba(t, mip0_bytes(_one(fmt, w, h, level, alpha), t))


def _one(fmt, w, h, level, alpha):
    from satk.texmod.txdwrite import txd_chunk

    return txd_chunk([native_chunk(NativeSpec("t", fmt, w, h, (level,), alpha=alpha))])


@pytest.mark.parametrize("fmt", ["DXT3", "DXT5"])
def test_dxt_to_dxt1_is_lossless_for_opaque_blocks(fmt):
    rng = random.Random(7)
    w, h = 16, 16
    blocks = []
    for i in range((w // 4) * (h // 4)):
        c0 = rng.randrange(65536)
        c1 = c0 if i % 5 == 0 else rng.randrange(65536)      # equal endpoints, c0 < c1 and c0 > c1 all occur
        bits = rng.getrandbits(32)
        alpha = b"\xff" * 8 if fmt == "DXT3" else bytes([255, 0]) + b"\0" * 6   # every texel 255
        blocks.append(alpha + struct.pack("<HHI", c0, c1, bits))
    level = b"".join(blocks)
    src = np.frombuffer(_decode(fmt, w, h, level, True), np.uint8).reshape(h, w, 4)
    assert (src[:, :, 3] == 255).all()
    out = dxt_to_dxt1(level)
    assert len(out) == len(level) // 2
    dst = np.frombuffer(_decode("DXT1", w, h, out, False), np.uint8).reshape(h, w, 4)
    assert (dst[:, :, :3] == src[:, :, :3]).all()
    dst_a = np.frombuffer(_decode("DXT1", w, h, out, True), np.uint8).reshape(h, w, 4)
    assert (dst_a[:, :, 3] == 255).all(), "no 3-colour block may use the transparent index"
    assert dxt_to_dxt1(b"") == b""
    with pytest.raises(ValueError):
        dxt_to_dxt1(b"\0" * 15)


@pytest.mark.parametrize("w,h,mx,pot,want", [
    (1024, 1024, 512, True, (512, 512)), (2048, 512, 512, True, (512, 128)), (600, 300, 0, True, (512, 256)),
    (1000, 1000, 0, True, (1024, 1024)), (768, 768, 0, True, (512, 512)), (300, 200, 0, False, (300, 200)),
    (1000, 500, 250, False, (250, 125)), (64, 64, 512, True, (64, 64)), (3, 5, 0, True, (2, 4)),
])
def test_target_dims(w, h, mx, pot, want):
    assert target_dims(w, h, mx, pot) == want


def test_pot_side():
    assert [_pot_side(d) for d in (1, 2, 3, 5, 6, 7, 100, 128, 130)] == [1, 2, 2, 4, 4, 8, 128, 128, 128]
