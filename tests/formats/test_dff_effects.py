"""satk.formats.dff additions of satk.style (A1 L1): Material.effects, Frame.matrix, model_matrices, GeomInfo.bbox.

Synthetic DFFs only (the ``b`` builders of tests/formats/conftest.py).
"""

from __future__ import annotations

import struct

import pytest

from satk.formats import dff as D

MATFX = 0x120          # rpMATFX plugin id
REFLECTION = 0x253F2FC
SPECULAR = 0x253F2F6


def _tex(b, name: str) -> bytes:
    return b.chunk(0x06, b.chunk(0x01, struct.pack("<HH", 0x1106, 0)) + b.string(name) + b.string("")
                   + b.chunk(0x03, b""))


def _env(b, coef=0.5, fb=0, tex="xvehicleenv128") -> bytes:
    """MatFX material stream: type 2 (env) = effect env + effect none."""
    body = struct.pack("<I", 2) + struct.pack("<I", 2) + struct.pack("<fii", coef, fb, 1) + _tex(b, tex)
    body += struct.pack("<I", 0)
    return b.chunk(MATFX, body)


def _reflection(b, scale=(1.0, 1.0), off=(1.0, 1.0), intensity=0.09) -> bytes:
    return b.chunk(REFLECTION, struct.pack("<5fI", *scale, *off, intensity, 0))


def _specular(b, level=0.2, tex="vehiclespecdot64") -> bytes:
    return b.chunk(SPECULAR, struct.pack("<f", level) + tex.encode().ljust(24, b"\0"))


def _car_like(b) -> bytes:
    pos = [(0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (0.0, 1.0, 0.5), (2.0, 1.0, -0.5)]
    mats = [b.material((60, 255, 0, 255), "vehiclegrunge256",
                       ext=_env(b) + _reflection(b) + _specular(b)),
            b.material((255, 255, 255, 128), "vehiclegeneric256", ext=_reflection(b, intensity=0.0)),
            b.material()]
    g = b.geometry(pos, tris=[(0, 1, 2, 0), (2, 1, 3, 1)], mats=mats, uv_sets=2, prelit=False, normals=True)
    frames = ((-1, "car", (0.0, 0.0, 0.0)), (0, "chassis", (0.0, 0.5, 0.0)), (1, "door_lf_dummy", (-1.0, 1.0, 0.25)))
    return b.clump([g], frames=frames, atomics=((1, 0),))


def test_material_effects_values(b):
    info = D.scan_dff(_car_like(b))
    m0, m1, m2 = info.materials
    assert m0.color_slot == 1 and m0.texture == "vehiclegrunge256"
    assert m0.effects["env"] == {"coef": 0.5, "fb_alpha": False, "tex": "xvehicleenv128"}
    assert m0.effects["reflection"] == {"scale": [1.0, 1.0], "offset": [1.0, 1.0], "intensity": 0.09}
    assert m0.effects["specular"] == {"level": 0.2, "tex": "vehiclespecdot64"}
    assert m0.effects["surface"] == [1.0, 1.0, 1.0]
    assert m0.fx & D.FX_ENV and m0.fx & D.FX_REFLECTION and m0.fx & D.FX_SPECULAR
    assert m1.effects["reflection"]["intensity"] == 0.0 and "env" not in m1.effects
    assert set(m2.effects) == {"surface"}
    # effects never take part in equality (frozen dataclass stays hashable)
    assert hash(m2) == hash(D.Material(m2.geom, m2.idx, m2.rgba, m2.texture, m2.mask, m2.fx, m2.color_slot))


def test_frame_matrix_and_model_matrices(b):
    info = D.scan_dff(_car_like(b))
    assert [f.name for f in info.frames] == ["car", "chassis", "door_lf_dummy"]
    assert info.frames[2].matrix == (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, -1.0, 1.0, 0.25)
    mm = D.model_matrices(info.frames)
    assert mm[0] == D.IDENTITY                              # the root is the model origin (engine)
    assert mm[1][9:12] == (0.0, 0.5, 0.0)
    assert mm[2][9:12] == (-1.0, 1.5, 0.25)                 # child = parent * local


def test_model_matrices_rotation():
    rot90 = (0.0, 1.0, 0.0, -1.0, 0.0, 0.0, 0.0, 0.0, 1.0)   # right -> +Y, up -> -X (90 deg about Z)
    frames = [D.Frame(0, -1, "root", False, D.IDENTITY),
              D.Frame(1, 0, "a", False, rot90 + (1.0, 0.0, 0.0)),
              D.Frame(2, 1, "b", False, D.IDENTITY[:9] + (1.0, 0.0, 0.0)),
              D.Frame(3, 5, "bad", False, ())]               # forward parent: model origin, no crash
    mm = D.model_matrices(frames)
    assert mm[2][9:12] == pytest.approx((1.0, 1.0, 0.0))    # local +X of 'b' runs along +Y after 'a'
    assert mm[2][:3] == pytest.approx((0.0, 1.0, 0.0))
    assert mm[3] == D.IDENTITY


def test_geom_bbox_and_uv_sets(b):
    info = D.scan_dff(_car_like(b))
    g = info.geoms[0]
    assert g.bbox == (0.0, 0.0, -0.5, 2.0, 1.0, 0.5)
    assert g.uv_sets == 2 and g.frame == 1


def test_matfx_truncated_keeps_what_was_read(b):
    bad = b.chunk(MATFX, struct.pack("<I", 2) + struct.pack("<I", 2) + struct.pack("<f", 0.5))   # cut short
    g = b.geometry([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)], tris=[(0, 1, 2, 0)],
                   mats=[b.material(tex="x", ext=bad)])
    info = D.scan_dff(b.clump([g]))
    assert info.materials[0].fx & D.FX_ENV and "env" not in info.materials[0].effects
