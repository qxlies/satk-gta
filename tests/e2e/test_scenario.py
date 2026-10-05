"""End-to-end agent scenario (SPEC §5.3 WP-12 acceptance 5).

``index build`` → ``asset_find grove`` → ``asset_get`` → ``texture_image(mode=sheet)`` → ``map_image`` →
``view_capture(target=mock, marks=4)`` → ``view_pick`` → ``asset_get`` of the picked SID; the same with
``target=ariane`` (markers ``viewer`` + ``SATK_TEST_LIVE=1``: it opens the viewer window, D14).

Calls go through the registry exactly as the MCP server dispatches them (``op_by_mcp`` + ``invoke``), so
the JSON envelopes are the ones an agent sees. The work directory is redirected (``SATK_PATHS_WORK``) to a
fresh directory under ``work/tmp/e2e/`` (on D:, removed afterwards): the index, PNGs, captures, notes and
viewer endpoints of the run never touch the shared workspace; the game copy is only read. The clean-up waits
for the viewer process to be gone and retries the removal; a directory that still cannot be removed is a
teardown error, not a silent leftover (``satk.docs.cleanup``).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from satk.core import config
from satk.core import registry as R
from satk.docs.cleanup import remove_tree, wait_process_exit

#: (operation name, MCP name or None) needed by the scenario.
NEEDED = (
    ("index.build", None),
    ("asset.find", "asset_find"),
    ("asset.get", "asset_get"),
    ("texture.image", "texture_image"),
    ("map.image", "map_image"),
    ("view.capture", "view_capture"),
    ("view.pick", "view_pick"),
    ("view.control", "view_control"),
)
#: Grove Street at street level, CJ's house ahead (the E2 eval pose; bookmark grove_center).
GROVE_POSE = {"pos": [2490.0, -1655.0, 16.0], "look": [2500.0, -1700.0, 16.0], "fov_h_deg": 70.0}


def _call(name: str, **args) -> dict:
    mcp = dict(NEEDED).get(name)
    spec = R.op_by_mcp(mcp) if mcp else R.get_op(name)
    env = R.invoke(spec, args)
    assert env.get("ok") is True, f"{name}({args}) -> {env}"
    return env


def _rows(env: dict) -> list[dict]:
    cols = env.get("cols") or []
    return [dict(zip(cols, r)) for r in env.get("rows") or []]


def _reset_caches() -> None:
    config.reset()
    from satk.index import api as index_api

    index_api.clear_cache()
    try:
        from satk.viewer.backends.mock import reset_world
    except ImportError:  # pragma: no cover
        return
    reset_world()


@pytest.fixture(scope="module")
def e2e_work():
    """Isolated work dir with a freshly built vanilla index (the first step of the scenario)."""
    have = {o.name for o in R.all_ops()}
    missing = [n for n, _ in NEEDED if n not in have]
    if missing:
        pytest.skip(f"operations not merged: {', '.join(missing)}")
    base = Path(config.load().paths.work) / "tmp" / "e2e"
    base.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="run-", dir=base))
    old = os.environ.get("SATK_PATHS_WORK")
    os.environ["SATK_PATHS_WORK"] = str(work)
    _reset_caches()
    try:
        b = _call("index.build", profile="vanilla")
        assert b["counts"]["inst"] == 50935 and b["counts"]["texture"] == 32878  # golden A.1
        assert Path(b["path"]).resolve().is_relative_to(work.resolve())
        yield work
    finally:
        if old is None:
            os.environ.pop("SATK_PATHS_WORK", None)
        else:
            os.environ["SATK_PATHS_WORK"] = old
        _reset_caches()
        left = remove_tree(work, seconds=20.0)
        assert not left, f"e2e work dir not removed: {work} (still there: {left[:5]})"


def _scenario(target: str, work: Path, pose: dict | None = None) -> dict:
    out: dict = {}
    found = _rows(_call("asset.find", query="grove"))
    assert found and all(r["name"] == "grove" for r in found)  # exact names first
    wide = _call("asset.find", query="*grove*", limit=50)
    kinds = [r["kind"] for r in _rows(wide)]
    assert kinds.count("tex") == 11 and kinds.count("txd") == 4  # golden A.1: FTS grove → 11 textures + 4 TXDs

    first = found[0]["id"]
    obj = _call("asset.get", id=first)
    assert obj["id"] == first and obj.get("name") == "grove"

    texs = [r["id"] for r in found if str(r["id"]).startswith("tex:")][:16]
    sheet = _call("texture.image", ids=texs, mode="sheet")
    assert Path(sheet["files"][0]).is_file() and Path(sheet["files"][0]).resolve().is_relative_to(work.resolve())
    assert [row[1] for row in sheet["legend"]] == texs
    out["sheet"] = sheet["files"][0]

    m = _call("map.image", x=2495.0, y=-1687.0)
    assert Path(m["file"]).is_file() and m["legend"] and m["m_per_px"] > 0
    out["map"] = m["file"]

    cap = _call("view.capture", target=target, pose=pose, marks=4)
    assert Path(cap["file"]).is_file() and Path(cap["sidecar"]).is_file()
    assert Path(cap["file"]).resolve().is_relative_to(work.resolve())
    legend = cap.get("legend") or []
    assert 1 <= len(legend) <= 4 and cap.get("settled") is True
    out["capture"], out["legend"] = cap["file"], legend

    # the agent picks where a mark is; the legend has no pixels, so probe a 4x3 grid in ONE call
    w, h = cap["w"], cap["h"]
    pts = [[round(w * (i + 0.5) / 4), round(h * (j + 0.5) / 3)] for j in range(3) for i in range(4)]
    picks = _rows(_call("view.pick", target=target, points=pts, capture=cap["id"]))
    assert len(picks) == len(pts)
    # the mock world mixes one real placement (inst:lae2_stream0#4) with synthetic ones (inst:mock*, not in
    # the index: asset_get says NOT_FOUND); take the first real one
    hit = next((p for p in picks if str(p.get("id") or "").startswith("inst:")
                and not str(p["id"]).startswith("inst:mock")), None)
    assert hit, f"no indexed placement under a 4x3 grid of the frame: {picks}"
    got = _call("asset.get", id=hit["id"])
    assert got["id"] == hit["id"] and got["model"] == hit["model"]
    out["picked"], out["picked_name"] = hit["id"], got.get("name")
    return out


@pytest.mark.e2e
@pytest.mark.game
@pytest.mark.slow
def test_agent_scenario_mock(e2e_work):
    out = _scenario("mock", e2e_work)
    assert out["legend"][0][1] == "inst:lae2_stream0#4"  # the mock world's biggest object is real
    assert out["picked"].startswith("inst:")


@pytest.mark.e2e
@pytest.mark.game
@pytest.mark.viewer
@pytest.mark.slow
@pytest.mark.skipif(os.environ.get("SATK_TEST_LIVE") != "1",
                    reason="live Ariane: opens the viewer window; set SATK_TEST_LIVE=1")
def test_agent_scenario_ariane(e2e_work):
    root = config.load().paths.game
    snap = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}
    st = _call("view.control", action="start", target="ariane", window="960x540")
    try:
        assert st["up"] is True and st["proto"] == "ariane-ipc/1"
        out = _scenario("ariane", e2e_work, pose=GROVE_POSE)
        sids = [row[1] for row in out["legend"]]
        assert "inst:lae2_stream0#8" in sids  # CJ's house carlshou1_lae2 is the biggest building ahead
        assert out["picked"].startswith("inst:lae2_stream")
    finally:
        stop = _call("view.control", action="stop", target="ariane")
        # `stop` polls the exit code; the work dir can only go once the process object is signaled
        # (all its handles, viewer-ariane.log included, closed)
        gone = wait_process_exit(st.get("pid"), 15.0)
    assert stop["up"] is False and gone, (stop, st.get("pid"))
    after = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}
    assert after == snap  # gta-sa-clean untouched (V0-4: no IMG writes)
