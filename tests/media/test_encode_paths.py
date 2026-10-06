"""JPEG/WebP output with pixel and byte limits; texture files (.txd/.png) as texture_image inputs."""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from satk.core.errors import SatkError
from satk.media import encode as E


def _noise(w: int, h: int, seed: int = 1) -> bytes:
    rnd = random.Random(seed)
    return bytes(b for _ in range(w * h) for b in (rnd.randrange(256), rnd.randrange(256), rnd.randrange(256), 255))


def test_fit_never_upscales():
    assert E.fit(2000, 1000, 1024) == (1024, 512)
    assert E.fit(300, 200, 1024) == (300, 200) and E.fit(300, 200, None) == (300, 200)


@pytest.mark.parametrize("fmt,magic", [("jpg", b"\xff\xd8\xff"), ("webp", b"RIFF"), ("png", b"\x89PNG")])
def test_encode_formats_and_max_px(fmt, magic):
    data, info = E.encode_image(400, 200, _noise(400, 200), fmt, max_px=100)
    assert data[:len(magic)] == magic and (info["w"], info["h"]) == (100, 50) and info["format"] == fmt


def test_max_bytes_steps_quality_then_size():
    rgba = _noise(512, 512, 7)
    data, info = E.encode_image(512, 512, rgba, "jpg", max_bytes=40_000)
    assert len(data) <= 40_000 and info["bytes"] == len(data)
    assert info["quality"] < 85 or info["w"] < 512
    again, _ = E.encode_image(512, 512, rgba, "jpg", max_bytes=40_000)
    assert again == data                                   # deterministic


def test_alpha_is_flattened_and_bad_input(satk_home):
    rgba = bytes((10, 20, 30, 0)) * 16
    data, info = E.encode_image(4, 4, rgba, "jpg")
    assert info["w"] == 4
    with pytest.raises(SatkError):
        E.encode_image(4, 4, b"\0" * 3, "jpg")
    with pytest.raises(SatkError):
        E.encode_image(4, 4, rgba, "gif")
    p = satk_home / "work" / "out" / "x.webp"
    info = E.save_image(p, 4, 4, rgba)
    assert Path(info["path"]).is_file() and info["format"] == "webp"


def test_texture_files_as_inputs(satk_home, tmp_path):
    from satk.media.png import encode_png
    from satk.media.texture import decode_ref, is_texture_path, refs_from_path, resolve_refs

    import importlib.util

    spec = importlib.util.spec_from_file_location("_m3d", Path(__file__).parents[1] / "model3d" / "conftest.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    txd = tmp_path / "own.txd"
    txd.write_bytes(mod.M.solid_txd({"a": (255, 0, 0, 255), "b": (0, 0, 255, 255)}, size=4))
    png = tmp_path / "pic.png"
    png.write_bytes(encode_png(2, 2, bytes((1, 2, 3)) * 4, 3))
    assert is_texture_path(str(txd)) and is_texture_path(str(png)) and not is_texture_path("txd:own")
    refs = refs_from_path(str(txd))
    assert [r.sid for r in refs] == ["file:own.txd/a", "file:own.txd/b"] and refs[0].w == 4
    w, h, rgba = decode_ref(refs[0])
    assert (w, h) == (4, 4) and rgba[:4] == bytes((255, 0, 0, 255))
    pr = refs_from_path(str(png))[0]
    assert pr.d3dfmt == "PNG" and decode_ref(pr)[2][:3] == bytes((1, 2, 3))
    got, warn = resolve_refs(None, [str(txd), str(png), str(txd)])
    assert len(got) == 3 and not warn                      # duplicates dropped, no index needed
