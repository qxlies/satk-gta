"""``texture new`` and the lane-a2-kit changes of ``texture finish``: edge mask, mask resize, role bands.

Dogfood friction of wave A1: an agent needed a Pillow script to make a flat base image and to resize the bake masks,
``texture finish`` ignored the edge mask and landed exactly on the edge of the vanilla band.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from satk.media.png import load_rgba


def _ok(res):
    assert res.code == 0, res.out + res.err
    assert res.json["ok"], res.json
    return res.json


def _px(path) -> np.ndarray:
    w, h, rgba = load_rgba(path)
    return np.frombuffer(bytes(rgba), dtype=np.uint8).reshape(h, w, 4)


# ----------------------------------------------------------------------------- texture new


def test_new_paints_a_flat_base_with_uv_rectangles(run_cli, satk_home, tmp_path):
    env = _ok(run_cli(["texture", "new", "bin_base", "--size", "256", "128", "--color", "#7a7c7c", "--rect",
                       "0:0:1:0.6=#5c7a68", "0.25:0.8:0.75:1=#808080", "--out", str(tmp_path / "o"), "--json"]))
    assert env["size"] == "256x128" and env["alpha"] is False and Path(env["file"]) == tmp_path / "o" / "bin_base.png"
    im = _px(env["file"])
    assert im.shape == (128, 256, 4)
    assert tuple(im[0, 0, :3]) == (0x7A, 0x7C, 0x7C)                    # the base colour
    assert tuple(im[127, 0, :3]) == (0x5C, 0x7A, 0x68)                  # v = 0 is the BOTTOM row, as in uv.fit rectangles
    assert tuple(im[127 - 76, 5, :3]) == (0x5C, 0x7A, 0x68) and tuple(im[127 - 77, 5, :3]) == (0x7A, 0x7C, 0x7C)
    assert tuple(im[5, 128, :3]) == (0x80, 0x80, 0x80) and tuple(im[5, 10, :3]) == (0x7A, 0x7C, 0x7C)
    assert [r[1] for r in env["rects"]["rows"]] == ["0,51..256,128", "64,0..192,26"]


def test_new_is_deterministic_and_defaults_to_the_texmod_folder(run_cli, satk_home):
    a = _ok(run_cli(["texture", "new", "wall", "--size", "64", "--color", "100,110,120", "--json"]))
    first = Path(a["file"]).read_bytes()
    assert Path(a["file"]) == satk_home / "work" / "out" / "texmod" / "new" / "wall.png"
    assert a["size"] == "64x64"
    _ok(run_cli(["texture", "new", "wall", "--size", "64", "--color", "100,110,120", "--json"]))
    assert Path(a["file"]).read_bytes() == first
    # the PNG goes straight into texture finish by its relative path under work/out/texmod
    _ok(run_cli(["texture", "finish", "new/wall.png", "--preset", "wall", "--out", str(satk_home / "work" / "f"),
                 "--json"]))


def test_new_gradients_run_bottom_to_top_and_left_to_right(run_cli, satk_home, tmp_path):
    v = _px(_ok(run_cli(["texture", "new", "gv", "--size", "32", "64", "--color", "#000000", "--color2", "#ff0000",
                         "--gradient", "v", "--out", str(tmp_path), "--json"]))["file"])
    col = v[:, 3, 0].astype(int)
    assert col[-1] < 10 and col[0] > 245 and (np.diff(col) <= 0).all()   # bottom row = color, top row = color2
    u = _px(_ok(run_cli(["texture", "new", "gu", "--size", "64", "16", "--color", "#00ff00", "--color2", "#0000ff",
                         "--gradient", "u", "--out", str(tmp_path), "--json"]))["file"])
    assert u[3, 0, 1] > 245 and u[3, -1, 2] > 245 and u[3, -1, 1] < 10


def test_new_alpha_and_rgba_colour(run_cli, satk_home, tmp_path):
    env = _ok(run_cli(["texture", "new", "glass", "--size", "16", "--color", "#ffffff", "--alpha", "128",
                       "--out", str(tmp_path), "--json"]))
    assert env["alpha"] is True and _px(env["file"])[0, 0, 3] == 128
    env = _ok(run_cli(["texture", "new", "rgba", "--size", "16", "--color", "10,20,30,40", "--out", str(tmp_path),
                       "--json"]))
    assert tuple(_px(env["file"])[0, 0]) == (10, 20, 30, 40)


@pytest.mark.parametrize("args", [
    ["bad", "--size", "10"], ["bad", "--size", "256", "6"], ["bad", "--size", "8192"], ["Bad Name"],
    ["bad", "--color", "#12"], ["bad", "--color", "1,2,300"], ["bad", "--gradient", "v"],
    ["bad", "--rect", "0:0:2:1=#fff"], ["bad", "--rect", "nonsense"], ["bad", "--alpha", "300"],
])
def test_new_refuses_bad_arguments(run_cli, satk_home, tmp_path, args):
    r = run_cli(["texture", "new", *args, "--out", str(tmp_path), "--json"])
    assert r.code != 0 and r.json["error"]["code"] == "BAD_PARAMS", r.out
    assert not list(tmp_path.glob("*.png"))


def test_new_warns_about_non_power_of_two(run_cli, satk_home, tmp_path):
    env = _ok(run_cli(["texture", "new", "odd", "--size", "100", "60", "--out", str(tmp_path), "--json"]))
    assert any(w.startswith("NOT_POT") for w in env["warn"])


# ----------------------------------------------------------------------------- texture finish


def _flat(w: int, h: int, rgb=(150, 140, 130)) -> np.ndarray:
    img = np.zeros((h, w, 4), dtype=np.uint8)
    img[..., :3] = rgb
    img[..., 3] = 255
    return img


def test_edge_wear_lightens_the_marked_edges_only():
    from satk.texmod import finish as F

    img = _flat(64, 64, (110, 100, 90))
    edge = np.zeros((64, 64))
    edge[:, 28:36] = 255.0
    p = dict(F.PRESETS["photo_like"])
    plain = F._apply(img, None, p, 7, 1.0)
    worn = F._apply(img, None, p, 7, 1.0, edge)                          # same seed: only the wear differs
    lum = lambda a: a[..., :3].astype(float).mean(-1)                    # noqa: E731
    d = lum(worn) - lum(plain)
    assert d[:, 30:34].mean() > 8 and abs(d[:, :20].mean()) < 0.5 and abs(d[:, 44:].mean()) < 0.5
    # the worn edge is greyer, not just brighter
    sat = lambda a: (a[..., :3].max(-1).astype(float) - a[..., :3].min(-1)) / np.maximum(a[..., :3].max(-1), 1)  # noqa: E731
    assert sat(worn)[:, 30:34].mean() < sat(plain)[:, 30:34].mean()


def test_finish_takes_edge_and_resizes_masks_of_another_size(run_cli, satk_home, tmp_path, tm):
    src = tm.write_png(tmp_path / "bin.png", _flat(64, 32))
    ao = tm.write_png(tmp_path / "ao.png", np.full((16, 16, 4), 255, dtype=np.uint8))
    edge_img = np.zeros((16, 16, 4), dtype=np.uint8)
    edge_img[..., 3] = 255
    edge_img[:, 6:10, :3] = 255
    edge = tm.write_png(tmp_path / "edge.png", edge_img)
    env = _ok(run_cli(["texture", "finish", str(src), "--mask", str(ao), "--edge", str(edge), "--out",
                       str(tmp_path / "f"), "--json"]))
    assert env["size"] == "64x32" and env["role"] == "prop"
    warns = [w for w in env["warn"] if w.startswith("RESIZED_MASK")]
    assert len(warns) == 2 and "16x16 -> 64x32" in warns[0] and "edge mask" in warns[1]
    plain = _ok(run_cli(["texture", "finish", str(src), "--out", str(tmp_path / "g"), "--json"]))
    assert "warn" not in plain
    # the same inputs give the same file
    again = _ok(run_cli(["texture", "finish", str(src), "--mask", str(ao), "--edge", str(edge), "--out",
                         str(tmp_path / "f"), "--json"]))
    assert Path(again["file"]).read_bytes() == Path(env["file"]).read_bytes()


def test_finish_role_defaults_and_errors(run_cli, satk_home, tmp_path, tm):
    src = tm.write_png(tmp_path / "seat.png", _flat(64, 64, (90, 84, 76)))
    assert _ok(run_cli(["texture", "finish", str(src), "--preset", "interior", "--out", str(tmp_path / "a"),
                        "--json"]))["role"] == "interior"
    assert _ok(run_cli(["texture", "finish", str(src), "--out", str(tmp_path / "b"), "--json"]))["role"] == "interior"
    plain = tm.write_png(tmp_path / "thing.png", _flat(64, 64))
    assert _ok(run_cli(["texture", "finish", str(plain), "--out", str(tmp_path / "c"), "--json"]))["role"] == "prop"
    assert _ok(run_cli(["texture", "finish", str(plain), "--role", "ground", "--out", str(tmp_path / "d"),
                        "--json"]))["role"] == "ground"
    r = run_cli(["texture", "finish", str(plain), "--role", "metal", "--json"])
    assert r.code != 0 and r.json["error"]["code"] == "BAD_PARAMS" and "prop" in r.json["error"]["hint"]


def test_finish_lands_inside_the_role_band(monkeypatch, tmp_path, tm):
    """A flat bright image is pulled into the inner part of the band on value, saturation, detail and colours."""
    from satk.style import texture as T
    from satk.texmod import finish as F

    band = {"tex.val_mean": (0.25, 0.40, 0.55), "tex.sat_mean": (0.05, 0.12, 0.20),
            "tex.hf_energy": (8.0, 16.0, 28.0), "tex.colours": (100.0, 400.0, 1500.0)}
    monkeypatch.setattr(F, "_role_band", lambda role, profile: (band, "test band"))
    src = tm.write_png(tmp_path / "flat.png", _flat(128, 128, (230, 215, 200)))     # value 0.90
    env = F.finish(str(src), preset="photo_like", out=str(tmp_path / "f"))
    assert env["landing"]["source"] == "test band" and env["landing"]["rows"]
    prev = _px(env["dxt_preview"])
    st = T.texture_stats(prev.tobytes(), 128, 128)
    for metric, (p10, _p50, p90) in band.items():
        assert p10 <= st[metric] <= p90, (metric, st[metric], band[metric])
    # and the answer without a band stays as before: no landing rows
    monkeypatch.setattr(F, "_role_band", lambda role, profile: ({}, ""))
    plain = F.finish(str(src), preset="photo_like", out=str(tmp_path / "g"))
    assert "landing" not in plain
    assert _px(plain["dxt_preview"]).astype(float).mean() > prev.astype(float).mean()      # not pulled down


def test_finish_in_band_images_are_left_alone(monkeypatch, tmp_path, tm):
    """A band that holds the finished image's own numbers in its inner part changes nothing."""
    from satk.style import texture as T
    from satk.texmod import finish as F

    src = tm.write_png(tmp_path / "n.png", tm.image("noise", 64, 64))
    monkeypatch.setattr(F, "_role_band", lambda role, profile: ({}, ""))
    first = F.finish(str(src), preset="photo_like", out=str(tmp_path / "f"))
    st = T.texture_stats(_px(first["dxt_preview"]).tobytes(), 64, 64)
    band = {m: (st[m] * 0.6, st[m], st[m] * 1.6) for m in ("tex.val_mean", "tex.sat_mean", "tex.hf_energy")}
    band["tex.colours"] = (st["tex.colours"] * 0.3, float(st["tex.colours"]), st["tex.colours"] * 3.0)
    monkeypatch.setattr(F, "_role_band", lambda role, profile: (band, "own numbers"))
    env = F.finish(str(src), preset="photo_like", out=str(tmp_path / "g"))
    assert "landing" not in env
    assert Path(env["file"]).read_bytes() == Path(first["file"]).read_bytes()          # nothing was re-finished


# ----------------------------------------------------------------------------- game: the real prop band


@pytest.mark.game
def test_finish_lands_in_the_real_prop_band(tmp_path, tm):
    """With the vanilla texture distribution: a flat grey-green base gets all four style metrics inside p10..p90."""
    from satk.core.errors import SatkError
    from satk.style import texture as T
    from satk.texmod import finish as F

    try:
        dist = T.vanilla("vanilla")
    except (SatkError, OSError) as e:
        pytest.skip(f"no vanilla texture distribution: {e}")
    if dist["roles"].get("prop", {}).get("n", 0) < 12:
        pytest.skip("no vanilla prop textures")
    img = _flat(256, 128, (122, 124, 124))
    img[51:, :, :3] = (92, 122, 104)
    src = tm.write_png(tmp_path / "bin_base.png", img)
    env = F.finish(str(src), preset="photo_like", role="prop", out=str(tmp_path / "f"))
    assert env["role"] == "prop"
    if "landing" in env:
        assert "style.texture" in env["landing"]["source"]
    prev = _px(env["dxt_preview"])
    st = T.texture_stats(prev.tobytes(), 256, 128)
    peer = dist["roles"]["prop"]["metrics"]
    for metric in T.roles()["check"]:
        p10, _p50, p90 = peer[metric][:3]
        assert p10 <= st[metric] <= p90, (metric, st[metric], (p10, p90))
    assert env["style"]["rows"][0][3] == "in"
