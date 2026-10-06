"""MTA texture-name matching and the pattern cover (``satk.shader.wild``)."""

from __future__ import annotations

import random

from satk.shader import wild

UNIVERSE = sorted({
    "sf_road5", "dt_road", "dt_road_stoplinea", "roadnew4_256", "vegasroad1_256", "tar_1line256hv",
    "tar_freewyleft", "tar_freewyright", "roadsign01_128", "cj_road_sign1", "roadnew4_256alod", "lod_road1",
    "broadway92body256a", "rocky road", "sidewalk4_lae", "sf_pave6", "waterclear256", "glass_64", "cj_ped_torso",
    "cjtest", "a[b]c", "dot.name", "#emap",
})


def test_mta_semantics_lower_case_star_question_and_literals():
    assert wild.matches("*ROAD*", "SF_Road5")
    assert wild.matches("sf_road?", "sf_road5") and not wild.matches("sf_road?", "sf_road55")
    assert wild.matches("a[b]c", "a[b]c") and not wild.matches("a[b]c", "abc")   # brackets are literal
    assert wild.matches("dot.name", "dot.name") and not wild.matches("dot.name", "dotxname")
    assert wild.matches("*", "") and not wild.matches("", "x")
    assert wild.matches("rocky*", "rocky road")


def test_cj_means_cj_ped_star():
    assert wild.mta_pattern("CJ") == "cj_ped_*"
    assert wild.matches("cj", "cj_ped_torso") and not wild.matches("cj", "cjtest")


def test_resolve_last_match_wins_and_reapply_moves_to_end():
    names = ["sf_road5", "roadsign01_128", "cj_road_sign1"]
    assert wild.resolve([("apply", "*road*"), ("remove", "*sign*")], names) == {"sf_road5"}
    assert wild.resolve([("remove", "*sign*"), ("apply", "*road*")], names) == set(names)
    # re-adding *road* erases the earlier entry and appends it: it now wins over the remove
    assert wild.resolve([("apply", "*road*"), ("remove", "*sign*"), ("apply", "*road*")], names) == set(names)


def test_cover_exact_uses_remove_patterns_when_shorter():
    want = {n for n in UNIVERSE if "road" in n and "sign" not in n and "lod" not in n}
    plan = wild.cover(want, UNIVERSE)
    assert plan.exact and plan.extra == 0
    assert wild.resolve(plan.ops(), UNIVERSE) == want
    assert len(plan.picks) <= 4
    assert "*road*" in plan.apply and plan.remove


def test_cover_without_extras_never_hits_unwanted_names():
    want = {"tar_1line256hv", "tar_freewyleft", "tar_freewyright"}
    plan = wild.cover(want, UNIVERSE)
    assert plan.apply == ["tar_*"] and not plan.remove
    assert plan.picks[0].covers == 3 and plan.picks[0].extra == 0


def test_cover_max_extra_allows_unwanted_hits_and_no_removes():
    want = {"sf_road5", "dt_road", "dt_road_stoplinea", "roadnew4_256", "vegasroad1_256"}
    loose = wild.cover(want, UNIVERSE, max_extra=10)
    assert not loose.remove and not loose.exact
    assert want <= wild.resolve(loose.ops(), UNIVERSE)
    assert all(p.extra <= 10 for p in loose.picks)
    strict = wild.cover(want, UNIVERSE)
    assert strict.exact and wild.resolve(strict.ops(), UNIVERSE) == want


def test_cover_edge_cases():
    assert wild.cover([], UNIVERSE).picks == []
    one = wild.cover(["rocky road"], UNIVERSE)
    assert one.apply == ["rocky road"] and one.exact
    # a wanted name outside the universe is still covered
    extra = wild.cover(["brand_new_tex"], UNIVERSE)
    assert wild.resolve(extra.ops(), UNIVERSE + ["brand_new_tex"]) == {"brand_new_tex"}


def test_cover_is_exact_and_deterministic_on_random_sets():
    rng = random.Random(7)
    words = ["road", "tar", "pave", "grass", "lod", "wall", "sign", "glass", "sand", "rock"]
    uni = sorted({f"{rng.choice(words)}{rng.choice(['', '_', 'x'])}{rng.choice(words)}{rng.randint(0, 40)}"
                  for _ in range(600)})
    for k in range(5):
        want = set(rng.sample(uni, 40 + 10 * k)) | {n for n in uni if n.startswith("grass")}
        a = wild.cover(want, uni)
        b = wild.cover(set(want), list(reversed(uni)))
        assert a.exact and wild.resolve(a.ops(), uni) == want
        assert a.ops() == b.ops()
        assert len(a.picks) <= len(want)


def test_nameset_lookups():
    ns = wild.NameSet(UNIVERSE)
    assert [ns.names[i] for i in ns.prefix("tar_")] == ["tar_1line256hv", "tar_freewyleft", "tar_freewyright"]
    assert {ns.names[i] for i in ns.suffix("_lae")} == {"sidewalk4_lae"}
    assert {ns.names[i] for i in ns.infix("road")} == {n for n in UNIVERSE if "road" in n}
    assert ns.lookup("exact", "glass_64") == [ns.index["glass_64"]]
