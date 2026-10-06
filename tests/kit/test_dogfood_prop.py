"""Acceptance of lane a2-kit: the street-prop task of the wave-A1 dogfood run, redone with satk only.

The A1 agent needed hand edits and helper scripts for a bin with a LOD: it re-scaffolded the prop as a building to get a
LOD slot, wrote a Pillow script for the base image and the mask resizes, ran ``blender game-ready`` for the prelight,
the COL and the LOD DFF, and edited the IDE and copied the LOD file into the add-on folder by hand. Here every step is a
satk operation (the commands are the ones an agent types; ``COMMANDS`` records them), the package comes out of
``kit export --add`` complete, and ``asset.check`` has nothing to say about parts.

Markers: blender, slow, game (the vanilla index and style cache are copied into the test's own work directory).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from satk.core.registry import get_op

pytestmark = [pytest.mark.blender, pytest.mark.slow, pytest.mark.game]

PROFILE = [[0, 0.03], [0.26, 0.03], [0.29, 0.0], [0.29, 0.09], [0.275, 0.12], [0.275, 0.86], [0.315, 0.89],
           [0.315, 0.96], [0.29, 1.03], [0.17, 1.09], [0.15, 1.02], [0, 1.02]]
NAME = "sa_bin1"


@pytest.fixture(scope="module")
def prop(studio_session, tmp_path_factory):
    from satk.core import paths

    if not (paths.cfg().paths.get("game") and Path(str(paths.cfg().paths.game)).is_dir()):
        pytest.skip("no gta-sa-clean copy configured")
    real = Path(os.path.abspath(paths.cfg().paths.work))
    index = real / "index" / "vanilla.sqlite"
    style = sorted((real / "style").glob("vanilla-*")) if (real / "style").is_dir() else []
    if not index.is_file() or not style:
        pytest.skip("no vanilla index or style cache in the real work directory")
    with studio_session("a2prop", tmp_path_factory.mktemp("a2prop")) as s:
        (s.work / "index").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(index, s.work / "index" / "vanilla.sqlite")
        (s.work / "style").mkdir(parents=True, exist_ok=True)
        for f in style:
            shutil.copyfile(f, s.work / "style" / f.name)
        yield s


def _op(op_name: str, **kw) -> dict:
    return get_op(op_name).call(kw)


def test_street_prop_with_a_lod_from_nothing_but_satk(prop):
    from satk.formats.col import iter_col
    from satk.formats.txd import parse_txd
    from satk.media.png import png_size
    from satk.model3d.mesh import build_scene
    from satk.style import texture as T

    s = prop
    COMMANDS: list[str] = []

    def blender(method: str, **params) -> dict:
        COMMANDS.append(f"blender call {method}")
        return s.call(method, params)["result"]

    def satk(op_name: str, **kw) -> dict:
        COMMANDS.append(op_name.replace(".", " "))
        return _op(op_name, **kw)

    # G0/G1: the scaffold with a LOD slot (no re-scaffold as a building), the shape from a profile
    t = satk("kit.template", like="model:1300", name=NAME, dims=[0.63, 0.63, 1.09], tier="sa_plus", lod=True,
             session=s.name)
    assert t["blender"]["lod"] == "lodbin1" and t["blender"]["slots"] == 1 and not t.get("warn")
    lathe = blender("mesh.lathe", name="bin_body", segments=16, axis="z", profile=PROFILE)
    assert lathe["faces"] == 176
    blender("kit.fill", slot=NAME, objects=["bin_body"])
    blender("kit.material_preset", object=NAME, role="map", slot=0)
    # G4: UV regions (the lathe has cylindrical UVs, uv.fit fills the rectangles), density in px/m for 256x128
    blender("uv.fit", object=NAME, select={"where": "z<0.87"}, rect=[0, 0, 1, 0.72])
    blender("uv.fit", object=NAME, select={"where": "z>=0.87"}, rect=[0, 0.74, 1, 0.97])
    texel = blender("uv.texel", object=NAME, px=[256, 128])
    assert "zero_area_tris" not in texel and texel["px_per_m"]["p50"] > 20

    # the base image, the bake masks at the texture's own size, the SA look inside the role band
    base = satk("texture.new", name=NAME, size=[256, 128], color="#7a7c7c", rect=["0:0:1:0.6=#5c7a68"],
                out=str(s.work / "tex"))
    assert base["size"] == "256x128"
    bake = blender("kit.bake", object=NAME, size=[256, 128])
    assert bake["size"] == [256, 128] and all(png_size(f) == (256, 128) for f in bake["files"].values())
    fin = satk("texture.finish", image=base["file"], mask=bake["files"]["ao"], edge=bake["files"]["edge"], role="prop",
               out=str(s.work / "tex" / "f"))
    assert not fin.get("warn") and fin["role"] == "prop" and fin["style"]["rows"][0][3] == "in"
    stats = T.texture_stats(_rgba(fin["dxt_preview"]), 256, 128)
    peer = T.vanilla("vanilla")["roles"]["prop"]["metrics"]
    for metric in T.roles()["check"]:
        p10, _p50, p90 = peer[metric][:3]
        assert p10 <= stats[metric] <= p90, (metric, stats[metric], (p10, p90))
    blender("kit.material_preset", object=NAME, role="map", slot=0, image=fin["file"])
    lod = blender("kit.lod")
    assert lod["object"] == "lodbin1" and 0.15 <= lod["ratio"] <= 0.3 and "slot" not in lod   # the slot was scaffolded

    # G5: one export: prelight, a primitive COL, the LOD and its IDE line, the placement that links them
    res = satk("kit.export", add=True, session=s.name, place=[2495.0, -1687.0, 13.0])
    assert res["package"]["by"] == "mod.add" and res["package"]["lod"]["name"] == "lodbin1", res
    pkg = Path(res["package"]["out"])
    mid = int(res["package"]["id"].split(":")[1])
    lod_id = int(res["package"]["lod"]["id"].split(":")[1])
    assert lod_id > mid and sorted(p.name for p in pkg.iterdir() if p.is_file()) == \
        ["lodbin1.dff", f"{NAME}.col", f"{NAME}.dff", f"{NAME}.txd", f"{NAME}.txt"]
    ide = (pkg / "data" / "maps" / f"{NAME}.ide").read_text(encoding="latin-1").splitlines()
    assert ide[1] == "objs" and ide[2].startswith(f"{mid}, {NAME}, {NAME}, ") and ide[3] == f"{lod_id}, lodbin1, {NAME}, 800, 0"
    ipl = (pkg / "data" / "maps" / f"{NAME}.ipl").read_text(encoding="latin-1").splitlines()
    assert ipl[2].endswith(", 1") and ipl[3].endswith(", -1") and ipl[2].split(", ")[3:6] == ["2495", "-1687", "13"]

    hd = build_scene((pkg / f"{NAME}.dff").read_bytes(), name=NAME)
    assert len(hd.meshes) == 1 and hd.meshes[0].prelit is not None and hd.meshes[0].night is not None
    assert hd.meshes[0].normals is None                                      # map models carry no normals
    assert res["prelight"]["mode"] == "auto" and res["prelight"]["objects"] == 1 and res["prelight"]["night"] is True
    lod_scene = build_scene((pkg / "lodbin1.dff").read_bytes(), name="lodbin1")
    assert lod_scene.meshes[0].prelit is not None and lod_scene.meshes[0].night is not None
    cols = list(iter_col((pkg / f"{NAME}.col").read_bytes()))
    assert [c.name for c in cols] == [NAME] and cols[0].boxes == 1 and cols[0].faces == 0   # one box, no faces
    assert res["col"]["by"] == "col.gen" and res["col"]["mode"] == "box" and "solid block" in res["col"]["why"]
    tex = parse_txd((pkg / f"{NAME}.txd").read_bytes()).textures
    assert len(tex) == 1 and tex[0].d3dfmt == "DXT1" and (tex[0].w, tex[0].h) == (256, 128) and tex[0].levels > 1
    lint = res["lint"]
    assert lint["summary"]["fatal"] == 0 and lint["summary"]["error"] == 0
    assert not any(r.startswith("link.") for r in lint.get("by_rule", {}))        # IDE, DFF, TXD and COL all link

    import json

    slim = {k: v for k, v in res.items() if k not in ("lint", "check")}
    print("kit.export answer without lint and check:", len(json.dumps(slim)), "characters")
    assert len(json.dumps(slim)) <= 1900          # asset.status --record takes 2,000 characters (the evals record it)

    # asset.check: no struct.part_missing for a prop, edge rows are shown, every message names the band
    chk = _op("asset.check", target=str(pkg / f"{NAME}.dff"), like="model:1300", tier="sa_plus")
    assert chk["verdict"] in ("pass", "review") and chk["tier"] == "sa_plus"
    assert not [r for r in chk["rows"] if r[0] == "struct.part_missing"]
    assert all("sa_plus band" in r[7] for r in chk["rows"] if r[6] in ("low", "high", "edge"))
    assert res["check"]["verdict"] in ("pass", "review")

    # the acceptance numbers: no python method, no helper script, one command per step
    assert not [c for c in COMMANDS if c.endswith(" python")]
    assert len(COMMANDS) <= 20, COMMANDS
    print(f"\nA2 acceptance: street prop with LOD in {len(COMMANDS)} satk commands, 0 hand edits, 0 helper scripts")


def _rgba(path: str) -> bytes:
    from satk.media.png import load_rgba

    return bytes(load_rgba(path)[2])
