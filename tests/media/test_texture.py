"""Texture decoding and the content-addressed PNG cache (SPEC §4.4; WP-04 acceptance 1, 2, 6)."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from satk.core.errors import SatkError
from satk.index.api import TexRef, override_index
from satk.index.fake import FakeIndexDB
from satk.media import texture as T


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_texture_png_mip0_in_cache_and_repeatable(fake_db, satk_home, mh):
    p = T.texture_png("tex:bistro/vent_64")
    ref = fake_db.texture_ref("tex:bistro/vent_64")
    hexkey = ref.pix.split(":", 1)[1]
    assert p == satk_home / "work" / "cache" / "tex" / hexkey[:2] / f"{hexkey}.png"
    w, h, rgba = mh.png_pixels(p)
    assert (w, h) == (64, 64)
    colour = mh.px(rgba, w, 10, 10)
    assert all(mh.px(rgba, w, x, y) == colour for x, y in ((0, 0), (63, 63), (31, 5)))
    sha, mtime = _sha(p), p.stat().st_mtime_ns
    assert T.texture_png("tex:bistro/vent_64") == p
    assert _sha(p) == sha and p.stat().st_mtime_ns == mtime  # served from the cache, not rewritten
    assert T.texture_png(ref.pix) == p  # pix: SID -> same file


def test_dxt1_solid_colour_decodes_exactly(fake_db, mh):
    # sw_wallbrick_01 is DXT1 128x128 in the fake; its colour comes from the payload generator
    rows = mh.texture_rows(fake_db)
    i = next(k for k, r in enumerate(rows) if r[1] == "sw_wallbrick_01")
    r, g, b, _a = mh.colour_for(i)
    p = T.texture_png("tex:bistro/sw_wallbrick_01", size=0)
    w, h, rgba = mh.png_pixels(p)
    assert (w, h) == (128, 128)
    assert mh.px(rgba, w, 5, 77) == (*mh.expand565(mh.c565(r, g, b)), 255)


def test_resized_variant(fake_db, mh):
    p = T.texture_png("tex:vehicle/vehiclegeneric256", size=64)
    assert p.name.endswith("@64.png")
    assert mh.png_pixels(p)[:2] == (64, 64)
    q = T.texture_png("tex:vehicle/vehicletyres128", size=32)  # 64x128 -> 16x32
    assert mh.png_pixels(q)[:2] == (16, 32)
    full = T.texture_png("tex:vehicle/vehiclegeneric256", size=None)
    assert "@" not in full.name and mh.png_pixels(full)[:2] == (256, 256)
    assert T.texture_png("tex:vehicle/vehicledash32", size=256).name.count("@") == 0  # small: no upscale


@pytest.mark.parametrize("bad", [-1, 5000])
def test_bad_size(fake_db, bad):
    with pytest.raises(SatkError) as e:
        T.texture_png("tex:bistro/vent_64", size=bad)
    assert e.value.code == "BAD_PARAMS"


def test_texture_png_wants_tex_or_pix(fake_db):
    with pytest.raises(SatkError) as e:
        T.texture_png("txd:bistro")
    assert e.value.code == "BAD_ID"
    with pytest.raises(SatkError) as e:
        T.texture_png("tex:bistro/nope")
    assert e.value.code == "NOT_FOUND"


def test_resolve_refs_expands_and_dedupes(fake_db):
    refs, warn = T.resolve_refs(fake_db, ["txd:bistro", "tex:bistro/vent_64", "model:411"])
    sids = [str(r.sid) for r in refs]
    assert sids[:2] == ["tex:bistro/vent_64", "tex:bistro/sw_wallbrick_01"]
    assert sids.count("tex:bistro/vent_64") == 1
    assert len(sids) == 2 + 13 and not warn  # infernus: 13 material textures
    refs, warn = T.resolve_refs(fake_db, ["model:300"])  # cutscene placeholder: no textures
    assert refs == [] and warn and warn[0].startswith("EMPTY")
    with pytest.raises(SatkError) as e:
        T.resolve_refs(fake_db, ["inst:lae2_stream0#4"])
    assert e.value.code == "BAD_ID"
    with pytest.raises(SatkError) as e:
        T.resolve_refs(fake_db, ["not a sid"])
    assert e.value.code == "BAD_ID"


def test_unsupported_platform_and_missing_file(satk_home, tmp_path):
    ref = TexRef("tex:ps2/x", "pix:" + "ab" * 12, tmp_path / "none.img", 0, 16, None, "PLATFORM_0x325350", 0,
                 0x325350, 0, 0, 0, False)
    with pytest.raises(SatkError) as e:
        T.decode_ref(ref)
    assert e.value.code == "UNSUPPORTED"
    db = FakeIndexDB()  # no payloads -> archives do not exist
    with override_index(db):
        with pytest.raises(SatkError) as e:
            T.texture_png("tex:bistro/vent_64")
    assert e.value.code == "NOT_FOUND"


def test_short_read_is_not_found(satk_home, tmp_path):
    f = tmp_path / "short.img"
    f.write_bytes(b"\0" * 10)
    ref = TexRef("tex:a/b", "pix:" + "cd" * 12, f, 0, 64, None, "DXT1", 0x200, 9, 4, 4, 1, False)
    with pytest.raises(SatkError) as e:
        T.decode_ref(ref)
    assert e.value.code == "NOT_FOUND" and "short read" in e.value.msg


def test_pal8_uses_palette(satk_home, tmp_path):
    # 4x4 PAL8: indices 0..15, palette entry i = (i*16, 255-i*16, 7, 255)
    pal = b"".join(bytes((i * 16 & 255, (255 - i * 16) & 255, 7, 255)) for i in range(256))
    idx = bytes(range(16))
    f = tmp_path / "pal.bin"
    f.write_bytes(pal + idx)
    ref = TexRef("tex:outro/x", "pix:" + "ef" * 12, f, len(pal), 16, 0, "PAL8", 0x2500, 9, 4, 4, 1, True)
    w, h, rgba = T.decode_ref(ref)
    assert (w, h) == (4, 4)
    assert rgba[5 * 4:5 * 4 + 4] == bytes((80, 175, 7, 255))


def test_cache_paths(satk_home):
    k = "0123456789abcdef01234567"
    assert T.cache_path(f"pix:{k}").as_posix().endswith(f"cache/tex/01/{k}.png")
    assert T.cache_path(k, 128).name == f"{k}@128.png"
    with pytest.raises(SatkError):
        T.cache_path("pix:xyz")


def test_guard_nothing_outside_work(fake_db, satk_home, tmp_path, monkeypatch):
    """Acceptance 6: all writes stay under work/; a work dir inside a protected root is refused."""
    before = {p for p in tmp_path.rglob("*")}
    T.texture_png("tex:bistro/vent_64")
    T.texture_png("tex:vehicle/vehiclegeneric256", size=32)
    new = {p for p in tmp_path.rglob("*")} - before
    work = satk_home / "work"
    assert new and all(work in p.parents for p in new), sorted(map(str, new))
    from satk.core import config

    guarded = satk_home / "src" / "satk-guard-work"  # src\ of the isolated workspace is protected
    monkeypatch.setenv("SATK_PATHS_WORK", str(guarded))
    config.reset()
    try:
        with pytest.raises(SatkError) as e:
            T.texture_png("tex:bistro/sw_wallbrick_01")
        assert e.value.code == "PROTECTED_PATH"
        assert not os.path.exists(guarded)
    finally:
        monkeypatch.delenv("SATK_PATHS_WORK")
        config.reset()
