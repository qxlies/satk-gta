"""The dogfood sequence of a street prop in ONE live session (markers blender, game, slow): asset.init ->
session start --project -> kit.template (building with a LOD slot) -> one batch of primitives + kit.fill +
kit.lod -> blender.preview session:NAME --lineup class. Before the fix the preview after kit.lod crashed or hung
the session (kit.lod stored depsgraph copies of the materials in the LOD mesh).

Runs in the configured work directory (the vanilla index and its style cache are needed) under names of its
own, and removes what it wrote: the project, the session folder, the kit and preview outputs.
"""

from __future__ import annotations

import os
import time

import pytest

from satk.core.errors import SatkError
from satk.core.registry import get_op

pytestmark = [pytest.mark.blender, pytest.mark.game, pytest.mark.slow]

NAME = f"a2dog{os.getpid() % 100000}"
STEPS = [
    {"method": "mesh.primitive", "params": {"kind": "cylinder", "name": "foot", "segments": 16, "radius": 0.29,
                                            "depth": 0.10, "location": [0, 0, 0.05]}},
    {"method": "mesh.primitive", "params": {"kind": "cylinder", "name": "body", "segments": 16, "radius": 0.275,
                                            "depth": 0.76, "location": [0, 0, 0.48]}},
    {"method": "mesh.primitive", "params": {"kind": "cone", "name": "dome", "segments": 16, "radius": 0.30,
                                            "radius_top": 0.17, "depth": 0.13, "location": [0, 0, 0.92]}},
    {"method": "kit.fill", "params": {"slot": "satkdog", "objects": ["foot", "body", "dome"]}},
    {"method": "kit.lod", "params": {}},
]


@pytest.fixture(scope="module")
def dog():
    from satk.blender import runner
    from satk.core import paths
    from satk.docs.cleanup import remove_tree
    from satk.studio import api
    from satk.studio import launcher as L

    try:
        runner.blender_exe()
        from satk.style import cache as SK

        SK.load("vanilla")                    # built once per index: not part of the timed sequence
    except SatkError as e:
        pytest.skip(f"no Blender or no vanilla index: {e.msg}")
    tmp = paths.tmp(NAME)
    made: list = [tmp, L.session_dir(NAME)]
    try:
        yield {"proj": tmp / NAME, "tmp": tmp, "made": made}
    finally:
        api.close_all()
        try:
            L.stop(NAME)
        finally:
            made += sorted(paths.work("out", "preview").glob(f"session_{NAME}-*"))
            for d in made:
                if d.exists():
                    remove_tree(d)


def test_template_fill_lod_preview_in_one_session(dog):
    from satk.studio import api

    op = get_op
    t0 = time.monotonic()
    r = op("asset.init").call({"dir": str(dog["proj"]), "kind": "prop", "like": "model:1300", "tier": "sa_plus",
                               "force": True})
    assert r["like"] == "model:1300"
    st = op("blender.session").call({"action": "start", "project": str(dog["proj"]), "idle": 300})
    assert st["up"] and st["name"] == NAME
    kit_out = dog["tmp"] / "kit"
    r = op("kit.template").call({"kind": "building", "name": "satkdog", "dims": [0.63, 0.63, 1.09],
                                 "tier": "sa_plus", "session": NAME, "out": str(kit_out)})
    assert r["blender"]["lod"]
    r = api.call("batch", {"steps": STEPS}, session=NAME, timeout=60)
    lod = r["steps"][-1]
    assert lod["method"] == "kit.lod" and lod["result"]["tris"] < r["steps"][-2]["result"]["tris"]
    assert r["checkpoint"].endswith(f"{r['n']:04d}.blend")          # one checkpoint for the batch
    hd = r["stats"]["objects"]["satkdog"]
    scene = r["stats"]["scene"]
    flags = scene.get("out", []) + scene.get("edge", [])
    assert hd["tris"] > 100 and not any(f.startswith("geo.tris") for f in flags)  # inside the sa_plus band
    p = op("blender.preview").call({"subject": f"session:{NAME}", "lineup": "class", "passes": ["game", "clay"],
                                    "views": ["3q", "side"], "size": 256})
    seconds = time.monotonic() - t0
    labels = [e[1].lower() for e in p["legend"]["entries"]]
    assert labels[0] == f"session:{NAME}" and labels[1] == "bin1"         # like model:1300 from asset.json
    assert set(labels[2:]) <= {"cj_wastebin", "cj_bin1", "cj_hippo_bin"} and len(labels) == 4
    assert p["stats"]["e0"]["geo.tris"] == hd["tris"]                   # the LOD is not drawn next to the HD
    assert seconds < 60, seconds
    again = api.call("scene.info", {"limit": 0}, session=NAME)           # the session is still usable
    assert again["result"]["total"] >= 2
