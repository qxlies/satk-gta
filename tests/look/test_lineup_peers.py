"""Lineup peers from the style peer set, and a session's lineup taken from its project's asset.json
(markers game: the vanilla index and its style cache; the Blender render is replaced by synthetic cells)."""

from __future__ import annotations

import pytest

from satk.core.errors import SatkError
from satk.core.registry import get_op
from satk.look import lineup as LU

pytestmark = [pytest.mark.game]


@pytest.fixture(scope="module")
def ref_bin():
    try:
        r = LU.model_row("model:1300")
    except SatkError as e:
        pytest.skip(f"no vanilla index: {e.msg}")
    if r is None:
        pytest.skip("model:1300 is not in the index")
    return r


def _names(sids: list[str]) -> list[str]:
    return [LU.model_row(s)["name"].lower() for s in sids]


def test_name_words():
    assert LU.name_words("CJ_WASTEBIN") == {"wastebin"} and LU.name_words("bin1") == {"bin"}
    assert LU.name_words("dyn_cabinet") == {"cabinet"}


def test_a_litter_bin_stands_next_to_bins(ref_bin):
    sids, how = LU.class_peers(ref_bin, 2)
    assert how == "style peer set prop@1-2m"
    assert set(_names(sids)) <= {"cj_wastebin", "cj_bin1", "cj_hippo_bin"} and len(sids) == 2
    assert "model:1300" not in sids
    again, _ = LU.class_peers(ref_bin, 2)
    assert again == sids                                     # deterministic


def test_a_sedan_stands_next_to_sedans():
    ref = LU.model_row("model:426")
    sids, how = LU.class_peers(ref, 3)
    assert how == "style peer set car.sedan" and "model:426" not in sids
    sedans = {"admiral", "elegant", "emperor", "glendale", "greenwoo", "intruder", "merit", "nebula", "oceanic",
              "premier", "primo", "sentinel", "stafford", "sunrise", "tahoma", "washing", "willard", "vincent",
              "taxi", "cabbie", "stretch", "romero", "sultan", "glenshit"}
    assert set(_names(sids)) <= sedans


@pytest.fixture
def fake_session(monkeypatch, tmp_path, png):
    """``look.ops`` with a session whose Blender render is synthetic cells; the sheets it writes under the
    work directory are removed afterwards."""
    from satk.core.paths import work
    from satk.docs.cleanup import remove_tree
    from satk.look import ops as O

    seen: dict = {"out": []}
    real = O.out_key

    def out_key(*a, **kw):
        k = "a2test-" + real(*a, **kw)
        seen["out"].append(k)
        return k

    def run_session(name, spec, timeout):
        seen.update(spec=spec, timeout=timeout)
        w, h = spec["size"]
        rows = [f"{s}/{p}" for s in spec["states"] for p in spec["passes"]]
        cells = [[r, c, str(png(tmp_path / f"c{r}{c}.png", w, h, (90, 100, 110)))]
                 for r in range(len(rows)) for c in range(len(spec["views"]))]
        return {"cells": cells, "rows": rows, "cols": spec["views"], "stats": {}, "seconds": {"total": 0.1}}, None

    monkeypatch.setattr(O, "out_key", out_key)
    monkeypatch.setattr(O, "_run_session", run_session)
    yield seen
    for k in seen["out"]:
        d = work("out", "preview", k)
        if d.exists():
            remove_tree(d)


def test_session_lineup_takes_like_from_asset_json(monkeypatch, fake_session, ref_bin):
    from satk.look import ops as O

    monkeypatch.setattr(O, "_session_asset", lambda name: {"kind": "prop", "like": "model:1300",
                                                            "dims": {"target": [0.99, 0.8, 1.3]}})
    r = get_op("blender.preview").call({"subject": "session:binx", "lineup": "class", "views": ["3q"]})
    ents = fake_session["spec"]["entries"]
    assert ents[0]["scene"] is True and "paint" not in ents[0]   # a prop gets no vehicle paint
    labels = [e[1].lower() for e in r["legend"]["entries"]]
    assert labels[:2] == ["session:binx", "bin1"] and set(labels[2:]) <= {"cj_wastebin", "cj_bin1", "cj_hippo_bin"}
    assert "NOTE: like model:1300 from the project's asset.json" in r["warn"]
    assert fake_session["timeout"] == O.SESSION_TIMEOUT_S


def test_session_lineup_without_like_uses_kind_and_size(monkeypatch, fake_session, ref_bin):
    from satk.look import ops as O

    monkeypatch.setattr(O, "_session_asset", lambda name: {"kind": "prop", "dims": {"target": [0.63, 0.63, 1.09]}})
    r = get_op("blender.preview").call({"subject": "session:binx", "lineup": "class", "views": ["3q"],
                                        "timeout": 30})
    assert len(r["legend"]["entries"]) == 3 and fake_session["timeout"] == 30
    assert "NOTE: lineup peers from the style peer set prop@1-2m" in r["warn"]
    monkeypatch.setattr(O, "_session_asset", lambda name: None)
    with pytest.raises(SatkError) as ei:
        get_op("blender.preview").call({"subject": "session:binx", "lineup": "class"})
    assert ei.value.code == "BAD_PARAMS" and "asset.json" in ei.value.hint
