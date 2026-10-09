"""The map-model side of ``kit.export`` without Blender: the primitive-collision rule, the LOD name, the TXD of
``blender.export`` and what ``kit.export`` hands to ``col.gen`` and ``mod.add``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from satk.kit import kinds as K
from satk.kit import plan as P


def _shape(size_xyz, volume=None) -> dict:
    lo = [0.0, 0.0, 0.0]
    d = {"bbox": [lo, [float(v) for v in size_xyz]], "size": float(max(size_xyz))}
    if volume is not None:
        d["volume"] = float(volume)
    return d


def test_lod_name_is_unique_like_the_name():
    assert P.lod_name("sa_bin1") == "lodsa_bin1" and P.lod_name("kitbuilding") == "lodkitbuilding"
    assert P.lod_name("vbld") != P.lod_name("abld") and len(P.lod_name("a" * 30)) == 19
    assert P.lod_name("bin") == "lodbin" and P.lod_name("ab") == "lodab"
    assert P.lod_name("shabbyhouse03_lvs") == "lodshabbyhouse03_lv"
    assert len(P.lod_name("x" * 19)) <= 19 and P.lod_name("ABCdef") == "lodabcdef"


def test_col_rule_data_is_in_the_classes_file():
    rule = K.classes()["col_rule"]
    assert rule["classes"] == ["prop", "interior_prop", "pickup"] and rule["primitive_max_m"] == 8.0
    for c in rule["classes"]:
        assert c in K.classes()["classes"]


def test_a_solid_prop_gets_one_box():
    """The street bin of the dogfood run: 0.63 x 0.63 x 1.09 m, a closed volume at about 60 % of its box."""
    box = 0.63 * 0.63 * 1.09
    mode, extra, why = K.col_choice("prop", _shape((0.63, 0.63, 1.09), box * 0.6))
    assert (mode, extra) == ("box", {}) and "fill 0.60" in why


def test_thin_and_open_props_get_a_few_boxes():
    mode, extra, why = K.col_choice("prop", _shape((0.1, 0.1, 3.0), 0.003))            # a pole: hollow of its box
    assert mode == "boxes" and extra == {"max_prims": 4} and "thin" in why
    mode, extra, why = K.col_choice("prop", _shape((1.0, 1.0, 1.0)))                    # an open mesh: no volume
    assert mode == "boxes" and extra == {"max_prims": 3} and "unmeasured" in why
    assert K.col_choice("prop", _shape((6.0, 1.0, 1.0), 0.5))[1] == {"max_prims": 6}    # the bigger, the more boxes


def test_a_ball_gets_one_sphere_and_a_pickup_too():
    import math

    d = 0.5
    mode, extra, why = K.col_choice("prop", _shape((d, d, d), math.pi / 6 * d ** 3))
    assert (mode, extra) == ("spheres", {"max_prims": 1}) and "ball" in why
    mode, extra, why = K.col_choice("pickup", _shape((0.3, 0.3, 0.4), 0.01))
    assert (mode, extra) == ("spheres", {"max_prims": 1})


def test_big_or_unmeasured_models_keep_the_class_mode():
    assert K.col_choice("prop", _shape((9.0, 2.0, 2.0), 20.0))[0] == "hull"             # above the primitive limit
    assert K.col_choice("prop", None)[0] == "hull" and K.col_choice("prop", {})[0] == "hull"
    assert K.col_choice("building", _shape((10.0, 10.0, 10.0), 900.0))[0] == "mesh"      # not a rule class
    assert K.col_choice("interior_shell", _shape((4.0, 4.0, 3.0), 40.0))[0] == "mesh"
    assert K.col_choice("interior_prop", _shape((1.0, 0.5, 0.5), 0.2))[0] == "box"


# ----------------------------------------------------------------------------- what kit.export hands to col.gen


class _Spec:
    def __init__(self, calls: list, reply=None):
        self.calls, self.reply = calls, reply or {}

    def param(self, name):
        if name in ("max_prims", "shadow", "mode"):
            return object()
        raise KeyError(name)

    def call(self, args):
        self.calls.append(dict(args))
        out = Path(args["out"])
        out.mkdir(parents=True, exist_ok=True)
        (out / "model.col").write_bytes(b"COL3")
        return {"rows": [["model", args["mode"]]]}


@pytest.fixture
def colgen_calls(monkeypatch):
    from satk.kit import export as E

    calls: list = []
    monkeypatch.setattr(E, "_op", lambda name: _Spec(calls) if name == "col.gen" else None)
    return E, calls


def test_colgen_gets_the_rule_mode_for_a_small_prop(colgen_calls, tmp_path):
    E, calls = colgen_calls
    warn: list[str] = []
    shape = _shape((0.63, 0.63, 1.09), 0.63 * 0.63 * 1.09 * 0.6)
    data, info = E._colgen(tmp_path / "x.dff", "prop", "world", "sa_bin1", tmp_path, warn, shape=shape)
    assert data == b"COL3" and calls[0]["mode"] == "box" and "max_prims" not in calls[0]
    assert info["by"] == "col.gen" and info["mode"] == "box" and "solid block" in info["why"]
    E._colgen(tmp_path / "x.dff", "prop", "world", "sa_bin1", tmp_path, warn, shape=_shape((0.1, 0.1, 3.0), 0.003))
    assert calls[1]["mode"] == "boxes" and calls[1]["max_prims"] == 4
    E._colgen(tmp_path / "x.dff", "pickup", "world", "health", tmp_path, warn, shape=_shape((0.3, 0.3, 0.4), 0.01))
    assert calls[2]["mode"] == "spheres" and calls[2]["max_prims"] == 1
    assert not warn


def test_an_explicit_col_mode_wins_and_vehicles_keep_spheres(colgen_calls, tmp_path):
    E, calls = colgen_calls
    warn: list[str] = []
    E._colgen(tmp_path / "x.dff", "prop", "world", "p", tmp_path, warn, shape=_shape((0.5, 0.5, 0.5), 0.1), col="mesh")
    assert calls[0]["mode"] == "mesh"
    E._colgen(tmp_path / "x.dff", "automobile", "vehicle", "c", tmp_path, warn, shape=None)
    assert calls[1]["mode"] == "spheres" and calls[1]["shadow"] is True
    E._colgen(tmp_path / "x.dff", "building", "world", "b", tmp_path, warn, shape=_shape((20.0, 20.0, 9.0), 900.0))
    assert calls[2]["mode"] == "mesh"
    E._colgen(tmp_path / "x.dff", "prop", "world", "q", tmp_path, warn, shape=None)       # no shape: the class default
    assert calls[3]["mode"] == "hull"


def test_kit_export_takes_the_new_options(run_cli, satk_home):
    r = run_cli(["kit", "export", "--add", "--prelight", "bake", "--place", "1,2,3", "--col", "boxes"])
    assert r.code != 0 and r.json["error"]["code"] == "BAD_PARAMS" and "--blend" in r.json["error"]["hint"]
    r = run_cli(["kit", "export", "--prelight", "red"])
    assert r.code != 0 and r.json["error"]["code"] == "BAD_PARAMS"
    r = run_cli(["kit", "template", "-h"])
    assert "--lod" in r.out


# ----------------------------------------------------------------------------- blender.export: TXD through texmod


def _png(path: Path, rgb, w=64, h=64, alpha=False) -> str:
    import numpy as np

    from satk.media import png as _png

    a = np.zeros((h, w, 4), dtype=np.uint8)
    a[..., :3] = rgb
    a[..., 3] = 255
    if alpha:
        a[: h // 2, :, 3] = 90
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_png.encode(w, h, a.tobytes()))
    return str(path).replace("\\", "/")


def test_blender_export_packs_dxt_txds_of_the_own_textures(satk_home):
    from satk.blender import packaging as PK
    from satk.core import paths
    from satk.formats.txd import parse_txd

    d = paths.work("out", "exports", "mix")
    models = [
        {"name": "crate", "sec": "objs", "files": {"dff": str(d / "crate.dff")},
         "textures": [{"name": "crate", "png": _png(d / "_tex" / "crate" / "tex" / "crate.png", (120, 90, 60)),
                       "w": 64, "h": 64, "alpha": False}]},
        {"name": "mycar", "sec": "cars", "files": {},
         "textures": [{"name": "mycar92interior128", "png": _png(d / "_tex" / "mycar" / "tex" / "mycar92interior128.png",
                                                                 (40, 36, 30), alpha=True),
                       "w": 64, "h": 64, "alpha": True}]},
        {"name": "bare", "sec": "objs", "files": {}, "textures": []},
        {"name": "old", "sec": "objs", "files": {"txd": "from-the-old-blender-side"}},
    ]
    (d / "crate.dff").write_bytes(b"dff")
    warn = PK.pack_txds(d, models)
    assert not warn, warn
    crate = parse_txd((d / "crate.txd").read_bytes()).textures
    assert [(t.name, t.d3dfmt) for t in crate] == [("crate", "DXT1")] and crate[0].levels == 1   # 64 px: no mip chain
    car = parse_txd((d / "mycar.txd").read_bytes()).textures
    assert [(t.name, t.d3dfmt, t.levels) for t in car] == [("mycar92interior128", "DXT3", 1)]    # vehicle: never DXT5
    assert parse_txd((d / "bare.txd").read_bytes()).textures == []                               # an empty TXD
    assert models[0]["files"]["txd"].endswith("/crate.txd") and models[0]["sizes"]["txd"] > 0
    assert models[0]["txd"]["textures"] == 1 and "png" not in models[0]["textures"][0]
    assert models[3]["files"]["txd"] == "from-the-old-blender-side"                              # left alone
    assert not (d / "_tex").exists()                                                             # the PNGs are gone
