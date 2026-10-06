"""Soft-renderer parity with the Blender game look: dirt level, lamp rule, timecyc light, DFF paths."""

from __future__ import annotations

import numpy as np
import pytest

from satk.look import gamelook as G
from satk.model3d import api
from satk.model3d import softrender as SR
from satk.model3d import textures as TX
from satk.model3d.mesh import build_scene


def _box(m, rgb=(120, 100, 80)):
    sc = build_scene(m.box_dff(tex="vehiclegrunge256", size=(2.0, 2.0, 2.0)), name="box",
                     sec="cars")
    ch = TX.TxdChain([("v", m.solid_txd({"vehiclegrunge256": (*rgb, 255)}))])
    return sc, TX.resolve_materials(sc.materials, ch, {1: (10, 20, 30)})


def test_dirt_level_lightens_the_grunge_texture(m):
    sc, mats = _box(m)
    raw = SR.prepare(sc, mats)
    d2 = SR.prepare(sc, mats, dirt=2)
    d16 = SR.prepare(sc, mats, dirt=16)
    assert tuple(raw.textures[0][0][0, 0, :3]) == (120, 100, 80)
    assert tuple(d2.textures[0][0][0, 0, :3]) == G.dirt_rgb((120, 100, 80), 2)
    assert tuple(d16.textures[0][0][0, 0, :3]) == (120, 100, 80)


def test_game_light_differs_and_is_deterministic(m):
    sc, mats = _box(m, (200, 200, 200))
    prep = SR.prepare(sc, mats, dirt=2)
    env = G.default_env("12:00")
    plain = SR.render(prep, SR.view_angles(1), 48)[0][0]
    game = SR.render(prep, SR.view_angles(1), 48, env=env)[0][0]
    again = SR.render(prep, SR.view_angles(1), 48, env=env)[0][0]
    assert game.tobytes() == again.tobytes() and game.tobytes() != plain.tobytes()
    fg = (np.abs(game.astype(int) - np.array(SR.DEFAULT_BG)).sum(axis=2) > 0)
    c = game[fg].mean(axis=0)
    assert c[0] > c[2] + 10                                # the warm noon colour filter
    night = SR.render(prep, SR.view_angles(1), 48, env=G.default_env("23:00"))[0][0]
    assert night[fg].mean() < game[fg].mean()              # less light at night


def test_prelit_uses_prelight_plus_ambient(m):
    sc = build_scene(m.box_dff(tex=None, prelit=(100, 100, 100, 255)), name="b")
    mats = TX.resolve_materials(sc.materials, None, None)
    prep = SR.prepare(sc, mats)
    env = G.default_env("12:00")
    img = SR.render(prep, SR.view_angles(1), 48, env=env)[0][0]
    fg = (np.abs(img.astype(int) - np.array(SR.DEFAULT_BG)).sum(axis=2) > 0)
    want = [G.display_curve(min(1.0, (100 / 255 + a) * f)) * 255 for a, f in zip(env["amb"], G.grade_factors(env))]
    got = img[fg].mean(axis=0)
    assert np.allclose(got, want, atol=3)                  # flat colour: prelight is not shaded by N.L


def test_cache_key_covers_the_look():
    e1, e2 = G.default_env("12:00"), G.default_env("23:00")
    k1 = api.cache_key(b"d", [("t", b"x")], "soft", 4, 256, env=e1)
    assert k1 == api.cache_key(b"d", [("t", b"x")], "soft", 4, 256, env=e1)
    assert k1 != api.cache_key(b"d", [("t", b"x")], "soft", 4, 256, env=e2)
    assert SR.RENDER_VERSION >= 3


def test_model_image_of_a_dff_file(satk_home, tmp_path, m):
    from satk.model3d.resolve import is_dff_path, resolve

    d = tmp_path / "mod"
    d.mkdir()
    (d / "mycar.dff").write_bytes(m.car_dff())
    (d / "mycar.txd").write_bytes(m.solid_txd({"paint": (200, 200, 200, 255)}))
    p = str(d / "mycar.dff")
    assert is_dff_path(p) and not is_dff_path("model:411")
    src = resolve(p)
    assert src.sid == "file:mycar.dff" and src.model_id is None and src.sec == "cars"
    assert src.txd_chain[0].name == "mycar.txd"
    assert any(n.startswith("NO_VEHICLE_TXD") for n in src.notes)
    r = api.image(p, views=1, size=64)
    assert r["ok"] and r["backend"] == "soft" and r["stats"]["tris"] > 0
    with pytest.raises(Exception):
        resolve(str(d / "missing.dff"))
