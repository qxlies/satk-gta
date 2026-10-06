"""satk.anim against the vanilla game (READ-ONLY; gta-sa-clean). Marked ``game``.

* every IFP of the game (loose and inside every IMG: 287 ANP3 + 149 ANPK, 13.7 million key frames) decodes and
  encodes back byte for byte; ped.ifp, anim.img and three cutscene files (NaN keys, padding garbage) also through
  the JSON form;
* ped.ifp checked against the SA ped skeleton: two warnings on one unused prop sequence, no errors;
* ``data/anim/ped_skeleton.json`` equals the HAnim hierarchy of vanilla peds; the engine bone names are strings
  of gta_sa.exe; the kb's eBoneTag (when built) has the same ids.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from satk.core import config as _config

pytestmark = pytest.mark.game

_REAL = _config.build()                         # captured before satk_home isolates the environment
GAME: Path = _REAL.paths.game


@pytest.fixture(scope="module")
def game():
    if not (GAME / "anim" / "ped.ifp").is_file():
        pytest.skip(f"no vanilla copy at {GAME}")
    return GAME


def test_every_vanilla_ifp_round_trips(game):
    from satk.anim.ifp import read_ifp, write_ifp
    from satk.anim.source import iter_ifps

    n = keys = 0
    fmts: dict[str, int] = {}
    bad = []
    for label, reader in iter_ifps(game):
        data = reader()
        ifp = read_ifp(data)
        n += 1
        fmts[ifp.format] = fmts.get(ifp.format, 0) + 1
        keys += sum(len(s.keys) for a in ifp.anims for s in a.seqs)
        if write_ifp(ifp) != data[:ifp.end] or data[ifp.end:].strip(b"\0"):
            bad.append(label)
    assert bad == []
    assert (n, fmts, keys) == (436, {"ANP3": 287, "ANPK": 149}, 13693940)


def test_json_round_trip_ped_anim_img_and_cutscenes(game):
    from satk.anim.ifp import read_ifp, write_ifp
    from satk.anim.jsonio import dumps, ifp_to_json, json_to_ifp
    from satk.anim.source import iter_ifps
    from satk.formats.img import ImgArchive

    blobs = [("ped.ifp", (game / "anim" / "ped.ifp").read_bytes())]
    blobs += [(lab, rd()) for lab, rd in iter_ifps(game / "anim" / "anim.img")]
    with ImgArchive.open(game / "anim" / "cuts.img") as a:
        blobs += [(n, a.read(a.find(n))) for n in ("bcras2.ifp", "prolog1.ifp")]
    with ImgArchive.open(game / "models" / "gta3.img") as a:
        blobs.append(("las.ifp", a.read(a.find("las.ifp"))))
    assert len(blobs) == 137
    for label, data in blobs:
        ifp = read_ifp(data)
        back = json_to_ifp(json.loads(dumps(ifp_to_json(ifp))))
        assert write_ifp(back) == data[:ifp.end], label


def test_ped_ifp_against_the_ped_skeleton(game):
    from satk.anim.check import check_ifp, root_motion
    from satk.anim.ifp import read_ifp
    from satk.anim.skeleton import ped_skeleton

    ifp = read_ifp((game / "anim" / "ped.ifp").read_bytes())
    assert (ifp.pack, len(ifp.anims)) == ("ped", 294)
    fs = check_ifp(ifp, ped_skeleton())
    warn = [(f.anim, f.check) for f in fs if f.sev != "info"]
    # The unused 'fam3' prop sequence also has a non-unit quaternion; key checks include unbound sequences.
    assert warn == [("CAR_LjackedLHS", "quat_norm"), ("CAR_LjackedLHS", "unbound_name")]
    assert sum(f.check == "root_motion" for f in fs) == 134
    walk = ifp.find("walk_civi")
    assert root_motion(walk) == "move 1.72m" and round(walk.duration, 3) == 1.133


def _skeleton_of(img, name: str):
    from satk.anim.skeleton import read_skeleton

    return read_skeleton(img.read(img.find(name)), name)


def test_ped_skeleton_data_matches_vanilla_peds(game):
    from satk.anim.skeleton import engine_bone_names, ped_skeleton
    from satk.formats.img import ImgArchive

    ref = ped_skeleton()
    with ImgArchive.open(game / "models" / "gta3.img") as img:
        male01 = _skeleton_of(img, "male01.dff")
        others = [_skeleton_of(img, n) for n in ("bfori.dff", "wmomib.dff", "wmyst.dff", "swmyst.dff")]
    assert [(b.id, b.name, b.parent, b.flags) for b in male01.bones] == \
           [(b.id, b.name, b.parent, b.flags) for b in ref.bones]
    for sk in others:
        assert [(b.id, b.parent) for b in sk.bones] == [(b.id, b.parent) for b in ref.bones], sk.name
    exe = (game / "gta_sa.exe").read_bytes()
    for name in engine_bone_names().values():
        assert b"\0" + name.encode() + b"\0" in exe, name


def test_kb_bone_tags_agree():
    kb = Path(_REAL.paths.work) / "kb" / "kb.sqlite"
    if not kb.is_file():
        pytest.skip("kb not built")
    from satk.anim.skeleton import ped_skeleton

    c = sqlite3.connect(f"file:{kb.as_posix()}?mode=ro", uri=True)
    try:
        rows = c.execute("SELECT name, sig FROM sym WHERE kind='enum' AND owner='eBoneTag'").fetchall()
    finally:
        c.close()
    if not rows:
        pytest.skip("kb has no eBoneTag")
    ids = {int(v) for n, v in rows if n not in ("BONE_UNKNOWN", "BONE_MAX_ID")}
    assert ids == {b.id for b in ped_skeleton().bones}
