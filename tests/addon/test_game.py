"""satk.addon on the real clean copy (read-only): exact round trips of handling.cfg and weapon.dat, the lines
``mod add`` makes for every vanilla donor, and one add-on folder checked by ``mod inspect`` and ``asset lint``."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from satk.addon import handling as H
from satk.addon import weapon as W
from satk.addon.tokline import f32, fmt_number, parse_typed
from satk.core.paths import open_ro
from satk.formats.handling import KINDS, parse_handling

pytestmark = pytest.mark.game


def _read(root: Path, rel: str) -> str:
    with open_ro(root.joinpath(*rel.split("/"))) as f:
        return f.read().decode("latin-1")


def _same(a, b) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        return f32(float(a)) == f32(float(b))
    return a == b


def test_every_vanilla_handling_line_round_trips(clean_root):
    text = _read(clean_root, "data/handling.cfg")
    hf = H.parse(text)
    assert hf.errors == [] and hf.render() == text
    raw = text.splitlines()
    ref = parse_handling(text)
    assert len(hf.recs) == len(ref) >= 250
    counts = {k: 0 for k in KINDS}
    for r in ref:
        mine = hf.get(r.name, r.kind)
        counts[r.kind] += 1
        assert mine.text == raw[mine.line - 1] and mine.values == r.values, r.name
        # write every value in canonical form (no style to copy) and read the line again: same 32-bit values
        canon = mine.patched({f["key"]: (mine.values[f["key"]], None) for f in H.field_table(r.kind)})
        again = H.parse(canon.text + "\r\n").get(r.name, r.kind)
        assert all(_same(again.values[k], v) for k, v in mine.values.items()), r.name
        # and in the file's own style: identical text
        styled = mine.patched({f["key"]: (mine.values[f["key"]], mine.token(f["key"])) for f in H.field_table(r.kind)})
        if r.name != "RCRAIDER" or r.kind != "flying":                   # '0.1s' is written back as '0.1'
            assert styled.text == mine.text, r.name
    assert counts["car"] >= 200 and counts["bike"] >= 13 and counts["boat"] >= 12 and counts["flying"] >= 24


def test_every_vanilla_weapon_line_round_trips(clean_root):
    text = _read(clean_root, "data/weapon.dat")
    wf = W.parse(text)
    assert wf.errors == [] and wf.render() == text and wf.end is not None
    kinds = [r.kind for r in wf.recs.values()]
    assert kinds.count("melee") >= 16 and kinds.count("gun") >= 50 and kinds.count("aim") >= 18
    raw = text.splitlines()
    for r in wf.recs.values():
        assert r.text == raw[r.line - 1]
        styled = r.patched({k: (v, r.tl.get(r.first + i)) for i, (k, v) in enumerate(r.values.items())})
        assert styled.text == r.text
        for k, v in r.values.items():
            f = next(x for x in W._fields(r.kind) if x["key"] == k)
            assert _same(parse_typed(fmt_number(v, f["type"]), f["type"]), v), (r.key, k)
    assert [r.skill for r in wf.by_type("PISTOL")] == [0, 1, 2, 3]
    assert all(W.type_id(t) is not None for t in wf.types())


def test_every_vanilla_donor_gives_lines_mod_loader_reads(clean_root):
    """vehicles (all 212), peds and weapons: the copied lines pass Mod Loader's readme patterns."""
    from satk.addon.add import _ide_copy, _vehicle, _weapon
    from satk.addon.game import GameData, tok
    from satk.modinspect.base import BaseGame
    from satk.modinspect.traits import IdeTrait, trim_config_line

    gd = GameData("vanilla", base=BaseGame("vanilla", root=clean_root))
    _rel, hf = gd.handling
    cars = [m for m in gd.base.models.values() if m.sec == "cars"]
    assert len(cars) == 212
    for m in cars:
        ml = gd.model_line(m)
        warn: list[str] = []
        readme, files, _rows = _vehicle(gd, m, ml, 15000, "zzaddon", "own", None, "Add-on", warn)
        assert files.keys() == {"zzaddon.fxt"}, m.name               # no fallback file was needed
        ide = next(t for f, t in readme if f == "vehicles.ide")
        got, want = tok(ide).toks, tok(ml.text).toks
        assert [g for i, g in enumerate(got) if i not in (0, 1, 2, 4, 5)] == \
            [w for i, w in enumerate(want) if i not in (0, 1, 2, 4, 5)], m.name
        hl = [t for f, t in readme if f == "handling.cfg"]
        assert len(hl) == len(hf.kinds_of(m.handling)) >= 1, m.name
        for new, old in zip(hl, hf.kinds_of(m.handling)):
            assert new == old.patched({}, new_id="ZZADDON").text
    peds = [m for m in gd.base.models.values() if m.sec == "peds"]
    assert len(peds) > 250
    bad = [m.name for m in peds
           if IdeTrait().readme_section(trim_config_line(_ide_copy(gd.model_line(m).text, 15000, "zzped", "zzped")[1]))
           != "peds"]
    assert bad and all(n.startswith("special") for n in bad), bad    # SPECIAL01-10 get an IDE file instead
    for m in [m for m in gd.base.models.values() if m.sec == "weap"]:
        warn = []
        _weapon(gd, m, 15000, "zzgun", None, warn)                    # raises if a line would not be read


@pytest.fixture
def out_name(request):
    """A unique output folder under <work>/out/addon, removed afterwards."""
    from satk.core.paths import cfg
    from satk.docs.cleanup import remove_tree

    name = f"test-{request.node.name}"[:60]
    yield name
    p = Path(cfg().paths.work) / "out" / "addon" / name
    assert remove_tree(p) == []


def test_add_on_like_infernus(clean_root, run_cli, out_name):
    r = run_cli(["mod", "add", "vehicle", "--dff", "dff:infernus", "--txd", "txd:infernus", "--like", "411",
                 "--name", "zzinfern", "--game-name", "ZZ Infernus", "--profile", "vanilla", "--out", out_name,
                 "--cargrp", "donor"])
    assert r.code == 0, r.out + r.err
    env = r.json
    out = Path(env["out"])
    mid = int(env["id"].split(":")[1])
    assert mid >= 612
    text = (out / "zzinfern.txt").read_bytes().decode("latin-1")
    data = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
    vehicles = _read(clean_root, "data/vehicles.ide").splitlines()
    donor = next(ln for ln in vehicles if ln.startswith("411,"))
    assert data[0] == donor.replace("411,", f"{mid},", 1).replace("infernus,", "zzinfern,").replace(
        "INFERNUS,", "ZZINFERN,").replace("INFERNU,", "ZZINFER,").rstrip()
    handling = next(ln for ln in _read(clean_root, "data/handling.cfg").splitlines() if ln.startswith("INFERNUS "))
    assert data[1] == handling.replace("INFERNUS", "ZZINFERN", 1).rstrip()
    carcols = next(ln for ln in _read(clean_root, "data/carcols.dat").splitlines() if ln.startswith("infernus,"))
    assert data[2] == carcols.replace("infernus", "zzinfern", 1).rstrip()
    carmods = next(ln for ln in _read(clean_root, "data/carmods.dat").splitlines() if ln.startswith("infernus,"))
    assert data[3] == carmods.replace("infernus", "zzinfern", 1).rstrip()
    grp = (out / "data" / "cargrp.dat").read_bytes().decode("latin-1")
    van = _read(clean_root, "data/cargrp.dat")
    assert grp.count("zzinfern") == van.count("infernus") >= 1
    assert any(w.startswith("CARGRP_FULL: car group 6 ") for w in env["warn"])   # 26 models: only 23 are read
    assert (out / "zzinfern.dff").read_bytes()[:4] == b"\x10\0\0\0"
    ins = run_cli(["mod", "inspect", str(out), "--profile", "vanilla"])
    assert ins.code == 0 and not ins.json.get("warn"), ins.out
    got = {(x[0], x[1], x[2]) for x in ins.json["rows"]}
    assert ("ide", f"model:{mid}", "new-id") in got and ("data", "handling:ZZINFERN", "add") in got
    lint = run_cli(["asset", "lint", str(out), "--profile", "vanilla"])
    assert lint.code == 0 and lint.json["summary"]["fatal"] == 0 and lint.json["summary"]["error"] == 0, lint.out


def test_data_get_and_patch_on_vanilla(clean_root, run_cli, out_name):
    env = run_cli(["data", "get", "handling", "infernus", "--profile", "vanilla"]).json
    rows = {r[0]: r[1] for r in env["rows"]}
    assert rows["fMass"] == 1400.0 and rows["fMaxVelocity"] == 240.0 and env["models"] == ["model:411 infernus"]
    w = run_cli(["data", "get", "weapon", "PISTOL", "--profile", "vanilla"]).json
    assert w["weapon_id"] == 22 and w["cols"][1:5] == ["poor", "std", "pro", "cop"]
    r = run_cli(["data", "patch", "handling", "infernus", "fMass=1500", "--target", "full", "--profile", "vanilla",
                 "--name", out_name])
    assert r.code == 0, r.out
    new = (Path(r.json["out"]) / "data" / "handling.cfg").read_bytes().decode("latin-1")
    van = _read(clean_root, "data/handling.cfg")
    assert new != van and new.replace(r.json["new"][0], r.json["old"][0]) == van
    assert not math.isnan(rows["fTurnMass"])
