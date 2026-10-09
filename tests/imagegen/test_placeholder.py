"""The Pillow placeholder set: complete, valid, deterministic, with provenance."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("PIL")
pytest.importorskip("numpy")

from satk.imagegen import keying as K  # noqa: E402
from satk.imagegen.ops import placeholder  # noqa: E402
from satk.imagegen.spec import load_spec  # noqa: E402

EXPECTED = ({f"preset_{n}_{s}.png" for n in ("classic", "balanced", "high", "extreme") for s in (64, 128)}
            | {"lock_24.png", "lock_48.png"} | {f"status_{n}_12.png" for n in ("ok", "warn", "error")})


def test_placeholder_set_is_complete_and_valid(satk_home):
    env = placeholder()
    files = {r[0] for r in env["rows"]}
    assert files == EXPECTED and len(EXPECTED) == 13
    assert all(r[2] is True for r in env["rows"]), env["rows"]
    from pathlib import Path

    out = Path(env["dir"])
    assert out.parts[-3:] == ("imagegen", "sae-v1", "placeholder")
    for name in EXPECTED:
        im = K.load_image(out / name)
        v = K.validate_image(im)
        assert v["ok"], (name, v)
        side = int(name.rsplit("_", 1)[1].split(".")[0])
        assert im.size == (side, side)
        rec = json.loads((out / (name + ".provenance.json")).read_text(encoding="utf-8"))
        assert rec["generator"] == "placeholder" and rec["file"] == name and len(rec["sha256"]) == 64
        assert rec["validation"]["ok"] and "SATK_IMAGEGEN_API_KEY" not in json.dumps(rec)
    md = (out / "PROVENANCE.md").read_text(encoding="utf-8")
    assert "placeholder" in md and all(n in md for n in EXPECTED)


def test_placeholder_is_deterministic(satk_home, tmp_path):
    a = placeholder()
    sums_a = {r[0]: r[3] for r in a["rows"]}
    b = placeholder(out=tmp_path / "ws" / "work" / "out" / "other")
    assert sums_a == {r[0]: r[3] for r in b["rows"]}


def test_placeholder_only_and_unknown(satk_home):
    env = placeholder(only=["lock"])
    assert {r[0] for r in env["rows"]} == {"lock_24.png", "lock_48.png"} and "provenance" not in env
    from satk.core.errors import SatkError

    with pytest.raises(SatkError) as e:
        placeholder(only=["nope"])
    assert e.value.code == "NOT_FOUND"


def test_status_dots_have_a_darker_ring_and_the_spec_colours(satk_home):
    env = placeholder(only=["ok", "warn", "error"])
    from pathlib import Path

    out = Path(env["dir"])
    for name, color in (("ok", (0x34, 0xA8, 0x53)), ("warn", (0xFB, 0xBC, 0x04)), ("error", (0xEA, 0x43, 0x35))):
        im = K.load_image(out / f"status_{name}_12.png")
        center = im.getpixel((6, 6))
        assert center[:3] == color and center[3] == 255
        ring = im.getpixel((6, 0))
        assert ring[3] > 0 and sum(ring[:3]) < sum(color)  # the 1 px ring is darker


def test_spec_style_prompt_and_icons():
    s = load_spec("sae_v1")
    assert [i.name for i in s.icons] == ["classic", "balanced", "high", "extreme", "lock"]
    assert [x.name for x in s.status] == ["ok", "warn", "error"]
    assert s.model == "google/gemini-3.1-flash-image-preview" and s.candidates == 3
    p = s.style_prompt
    assert p.startswith("Flat minimalist user-interface icon for a PC game settings menu.")
    assert p.endswith("Crisp edges, readable at 32x32 pixels.")
    for frag in ("12 percent corner radius filling 80 percent of the canvas", "Badge fill dark slate #1F2428",
                 "thin inner border #3A4148", "off-white #E8EAED plus one accent color {accent}",
                 "no drop shadow outside the badge", "pure magenta #FF00FF background"):
        assert frag in p
    assert {i.accent for i in s.icons} == {"#9AA0A6", "#34A853", "#4285F4", "#FB8C00", "#FBBC04"}
    assert [s2.color for s2 in s.status] == ["#34A853", "#FBBC04", "#EA4335"]
    assert sorted(s.shipped_files()) == sorted(EXPECTED)
