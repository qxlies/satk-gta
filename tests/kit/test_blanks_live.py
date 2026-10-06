"""``kit.blank`` with the real Blender 5.1 (markers blender, slow; the like/band parts also game).

Plans are built first from the vanilla index of the current configuration (names and numbers only); then the
work directory is redirected to a temp dir (``SATK_PATHS_WORK``) and the studio session ``blanklive`` (owner = this
process) runs there, exactly like ``test_live.py``. The acceptance run is the quickstart car: template, blank,
four shaping steps, split + fill, wheel, shade, vlo, damage, export, then ``asset.check`` against the class band
with the real configuration restored.
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path

import pytest

from satk.core import config
from satk.core.errors import SatkError
from satk.core.registry import get_op

pytestmark = [pytest.mark.blender, pytest.mark.slow]

NAME = "blanklive"
CAR = "blkcar"
TIERS = ("sa_plus", "vanilla")
#: blank kind -> the template kind whose slots it fills
FILLED = {"prop_cyl": "prop", "prop_box": "prop", "building_box": "building", "bike": "bike", "boat": "boat",
          "heli": "heli", "plane": "plane"}
#: the shape of the quickstart: loops moved by selectors, one widening about the origin (never move x = 0 vertices)
SHAPING = [
    ("mesh.transform", {"object": CAR + "_body", "select": {"where": ["z>0.45"]}, "translate": [0, 0, 0.03]}),
    ("mesh.transform", {"object": CAR + "_body", "select": {"where": ["y>2.2", "z>-0.15"]},
                        "translate": [0, 0, -0.03]}),
    ("mesh.transform", {"object": CAR + "_body", "select": {"where": ["y<-2.45"]}, "translate": [0, -0.06, 0]}),
    ("mesh.transform", {"object": CAR + "_body", "scale": [1.02, 1, 1], "pivot": "origin"}),
]
#: asset.check rows that must be in band (the acceptance of the blank)
BAND_ROWS = ("shade.normal_bend", "shade.flat_share", "dff.verts_per_tri", "geo.largest_piece_share",
             "geo.sliver_share", "uv.zero_area_share", "geo.median_dihedral")
#: the rows that must not be ``high``/``low`` for the vanilla tier either (its band is the vanilla one)
SIZE_ROWS = ("veh.hd_tris", "dims.L", "dims.W", "dims.H")


def _write(pdir: Path, key: str, plan: dict) -> str:
    f = pdir / f"{key}.json"
    f.write_text(json.dumps(plan), encoding="utf-8")
    return str(f)


def _game_plans(pdir: Path) -> dict:
    """Template and blank plans that need the vanilla index (``{}`` without it)."""
    from satk.kit import blanks as B
    from satk.kit.plan import template_plan

    out: dict = {}
    try:
        for tier in TIERS:
            out[f"car_{tier}"] = {
                "template": _write(pdir, f"tpl_car_{tier}", template_plan(like="model:426", name=CAR, tier=tier,
                                                                         ghost=True)),
                "blank": _write(pdir, f"blank_car_{tier}", B.blank_plan("automobile", like="model:426", name=CAR,
                                                                        tier=tier)),
            }
        for blank, tk in FILLED.items():
            nm = f"blk{blank}"[:17]
            dims = [0.6, 0.6, 1.1] if blank == "prop_cyl" else None
            out[blank] = {
                "template": _write(pdir, f"tpl_{blank}", template_plan(kind=tk, name=nm)),
                "blank": _write(pdir, f"blank_{blank}", B.blank_plan(blank, name=nm, dims=dims)),
            }
    except SatkError:
        return {}
    return out


def _plain_plans(pdir: Path) -> dict:
    """Blank plans of every kind without the index (no ``like``)."""
    from satk.kit import blanks as B

    return {k: _write(pdir, f"plain_{k}", B.blank_plan(k, name=f"pl{k}"[:17]))
            for k in B.kinds()["kinds"]}


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    from satk.blender import runner
    from satk.docs.cleanup import remove_tree
    from satk.studio import api
    from satk.studio import launcher as L

    try:
        runner.blender_exe()
    except SatkError as e:
        pytest.skip(str(e))
    from satk.blender import gamedata
    from satk.index import api as index_api

    work = tmp_path_factory.mktemp("blank") / "work"
    work.mkdir()
    pdir = work / "plans"
    pdir.mkdir()
    game = _game_plans(pdir)
    plain = _plain_plans(pdir)
    index_api.clear_cache()
    old = os.environ.get("SATK_PATHS_WORK")
    os.environ["SATK_PATHS_WORK"] = str(work)
    config.reset()
    try:
        runner.ensure_dragonff()
    except SatkError as e:
        os.environ.pop("SATK_PATHS_WORK", None) if old is None else os.environ.__setitem__("SATK_PATHS_WORK", old)
        config.reset()
        pytest.skip(str(e))
    L.start(NAME, owner_pid=os.getpid(), idle=900)
    try:
        yield {"work": work, "game": game, "plain": plain, "old": old}
    finally:
        api.close_all()
        try:
            L.stop(NAME)
        finally:
            if old is None:
                os.environ.pop("SATK_PATHS_WORK", None)
            else:
                os.environ["SATK_PATHS_WORK"] = old
            config.reset()
            index_api.clear_cache()
            gamedata.clear_cache()
            remove_tree(work.parent)


@contextlib.contextmanager
def _real_work(live):
    """The real configuration (vanilla index and caches) for the check; the session keeps running."""
    from satk.index import api as index_api

    cur = os.environ.get("SATK_PATHS_WORK")
    if live["old"] is None:
        os.environ.pop("SATK_PATHS_WORK", None)
    else:
        os.environ["SATK_PATHS_WORK"] = live["old"]
    config.reset()
    index_api.clear_cache()
    try:
        yield
    finally:
        os.environ["SATK_PATHS_WORK"] = cur or ""
        if not cur:
            os.environ.pop("SATK_PATHS_WORK", None)
        config.reset()
        index_api.clear_cache()


def _call(method: str, params: dict | None = None, **kw) -> dict:
    from satk.studio import api

    return (api.call(method, params, session=NAME, stats="none", **kw).get("result") or {})


def _py(code: str, args: dict | None = None) -> dict:
    return (_call("python", {"code": code, "args": args or {}}).get("value") or {})


def _need(live, key: str) -> dict:
    if key not in live["game"]:
        pytest.skip("no vanilla index for the template and class numbers")
    return live["game"][key]


@pytest.fixture(scope="module")
def cars(live):
    """The quickstart car per tier: ``{tier: {"steps": n, "export": {...}, "check": {...}, "blank": {...}}}``."""
    from satk.style import api as style_api

    out: dict = {}
    for tier in TIERS:
        if f"car_{tier}" not in live["game"]:
            continue
        plans = live["game"][f"car_{tier}"]
        steps = 0
        _call("scene.clear")
        _call("kit.template", {"plan": plans["template"], "replace": True})
        blank = _call("kit.blank", {"plan": plans["blank"], "replace": True})
        steps += 2
        for method, params in SHAPING:
            _call(method, params)
            steps += 1
        split = _call("kit.blank_split", {"model": CAR, "fill": True})
        wheel = _call("kit.wheel", {})
        shade = _call("kit.shade", {})
        _call("kit.vlo", {})
        _call("kit.damage", {})
        steps += 5
        res = get_op("kit.export").call({"session": NAME, "model": CAR, "col": "auto", "lint": False, "check": False})
        steps += 1
        with _real_work(live):
            check = style_api.check(str(Path(res["files"]["dff"]).parent), like="model:426", tier=tier)[0]
        out[tier] = {"steps": steps, "blank": blank, "split": split, "wheel": wheel, "shade": shade, "export": res,
                     "check": check}
    return out


@pytest.mark.game
@pytest.mark.parametrize("tier", TIERS)
def test_sedan_blank_to_g2_shell_is_in_band(live, cars, tier):
    """The acceptance: blank + 10-20 session steps give a G2 shell whose shading and geometry rows are in band."""
    _need(live, f"car_{tier}")
    c = cars[tier]
    assert 10 <= c["steps"] <= 20, c["steps"]
    rows = {r[0]: r for r in c["check"]["rows"]}
    bad = [(k, c["check"]["metrics"].get(k), rows[k][6]) for k in BAND_ROWS + SIZE_ROWS
           if k in rows and rows[k][6] not in ("ok", "info")]
    assert not bad, bad
    assert c["check"]["counts"].get("error", 0) == 0, c["check"]["counts"]
    for k in BAND_ROWS:
        assert k in c["check"]["metrics"], k


@pytest.mark.game
@pytest.mark.parametrize("tier", TIERS)
def test_car_split_fills_the_kit_slots_and_exports_clean(live, cars, tier):
    _need(live, f"car_{tier}")
    c = cars[tier]
    slots = {row[1] for row in c["split"]["parts"]}
    for slot in ("chassis", "bonnet_ok", "boot_ok", "door_lf_ok", "door_rf_ok", "bump_front_ok", "bump_rear_ok",
                 "windscreen_ok"):
        assert slot in slots, (slot, sorted(slots))
    assert not c["split"].get("warn"), c["split"]["warn"]
    assert {row[0] for row in c["split"]["fill"]} >= {"chassis", "door_lf_ok", "door_rf_ok"}
    assert all(row[1] > 0 for row in c["split"]["fill"])
    assert c["blank"]["tris"] > 0 and c["export"]["frames"] >= 50
    from satk.formats.dff import find_embedded_col

    assert find_embedded_col(Path(c["export"]["files"]["dff"]).read_bytes())[1] > 0
    lint = get_op("asset.lint").call({"target": c["export"]["files"]["dff"], "preset": "strict"})
    assert lint["summary"]["error"] == 0, lint["summary"]


@pytest.mark.game
@pytest.mark.parametrize("kind", sorted(FILLED))
def test_every_kind_builds_and_fills_its_slots(live, kind):
    plans = _need(live, kind)
    _call("scene.clear")
    _call("kit.template", {"plan": plans["template"], "replace": True})
    r = _call("kit.blank", {"plan": plans["blank"], "replace": True, "split": True, "fill": True})
    split = r["split"]
    assert r["tris"] > 0 and split["fill"], r
    assert not split.get("warn"), split["warn"]          # every part found its kit slot
    assert all(row[1] > 0 for row in split["fill"]), split["fill"]


@pytest.mark.parametrize("kind", ["automobile", "bike", "boat", "heli", "plane", "prop_box", "prop_cyl", "building_box"])
def test_blank_without_a_template_builds_a_clean_mesh(live, kind):
    """No index, no template: the pieces, the mirror and the part attribute exist; the split cuts without fill."""
    _call("scene.clear")
    plan = live["plain"][kind]
    r = _call("kit.blank", {"plan": plan, "replace": True})
    assert r["tris"] and r["objects"]
    name = json.loads(Path(plan).read_text(encoding="utf-8"))["name"]
    info = _py("o = [o for o in bpy.data.objects if o.name.startswith(args['n'] + '_') and o.type == 'MESH']\n"
               "result['n'] = len(o)\n"
               "result['mirror'] = sum(1 for x in o if any(m.type == 'MIRROR' for m in x.modifiers))\n"
               "result['meta'] = [bool(x.get('satk_blank')) for x in o]\n"
               "result['nonmanifold'] = 0", {"n": name})
    assert info["n"] >= 1 and all(info["meta"])
    if kind in ("automobile", "bike", "boat", "heli", "plane"):
        assert info["mirror"] >= 1
    if kind == "automobile":
        s = _call("kit.blank_split", {"model": name})
        assert {row[1] for row in s["parts"]} >= {"chassis", "bonnet_ok", "door_lf_ok", "door_rf_ok"}
