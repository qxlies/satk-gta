"""satk.anim.skeleton and satk.anim.check on synthetic skeletons and animations."""

from __future__ import annotations

import pytest

from satk.anim.check import check_ifp, root_motion
from satk.anim.ifp import Anim, Ifp, Seq
from satk.anim.skeleton import engine_bone_names, ped_skeleton, read_skeleton
from satk.formats.rw import FormatError

from .conftest import SKIN_BONES, plain_dff, skinned_dff, walk_ifp

#: eBoneTag of the engine (gta-reversed / plugin-sdk enum values; the kb holds them as BONE_*).
BONE_TAGS = {0, 1, 2, 3, 4, 5, 6, 7, 8, 21, 22, 23, 24, 25, 26, 31, 32, 33, 34, 35, 36, 41, 42, 43, 44, 51, 52, 53, 54,
             201, 301, 302}


def test_ped_skeleton_data():
    sk = ped_skeleton()
    assert sk.skinned and len(sk.bones) == 32 and {b.id for b in sk.bones} == BONE_TAGS
    assert sk.bones[0].name == "Root" and sk.bones[0].parent == -1
    by = {b.id: b for b in sk.bones}
    assert sk.bones[by[1].parent].id == 0 and sk.bones[by[5].parent].id == 4       # Pelvis <- Root, Head <- Neck
    assert sk.bones[by[22].parent].id == 21 and sk.bones[by[52].parent].id == 51   # arm/leg chains
    names = engine_bone_names()
    assert set(names) == BONE_TAGS and names[23] == "R Forearm" and names[35] == "L Fingers" and names[44] == "L Toe"


def test_read_skeleton_skinned_push_pop_parents():
    sk = read_skeleton(skinned_dff(), "toy")
    assert sk.skinned and [b.id for b in sk.bones] == [b[0] for b in SKIN_BONES]
    assert [b.name for b in sk.bones] == [b[1] for b in SKIN_BONES]
    assert [b.parent for b in sk.bones] == [-1, 0, 1, 2, 1]          # Head pops back to Pelvis for L Thigh


def test_read_skeleton_plain_and_errors():
    sk = read_skeleton(plain_dff(), "door")
    assert not sk.skinned and [b.name for b in sk.bones] == ["DOOR", "handle"] and [b.parent for b in sk.bones] == [-1, 0]
    with pytest.raises(FormatError):
        read_skeleton(b"\x16\0\0\0\x04\0\0\0\xff\xff\x03\x18\0\0\0\0")


def test_bind_rules():
    sk = read_skeleton(skinned_dff())
    assert sk.bind("anything", 41).name == " L Thigh"                # tag wins over the name
    assert sk.bind("PELVIS", -1).id == 1                              # untagged: engine bone name, any case
    assert sk.bind(" Pelvis", -1) is None                            # the DFF frame name is not an engine name
    assert sk.bind("x", 99) is None
    door = read_skeleton(plain_dff())
    assert door.bind("door", -1).name == "DOOR" and door.bind("DOOR", 1) is None


def _codes(findings):
    return sorted({(f.sev, f.check) for f in findings})


def test_check_clean_walk_and_root_motion():
    ifp = walk_ifp()
    fs = check_ifp(ifp, ped_skeleton())
    assert [(f.anim, f.check) for f in fs] == [("walk", "root_motion")]
    assert root_motion(ifp.anims[0]) == "move 1.50m" and root_motion(ifp.anims[1]) == "fixed"
    assert root_motion(Anim("x", [Seq("Root", 0, False, True, [(0, 0, 0, 4096, 0)])])) == "-"


def test_check_findings():
    k = [(0, 0, 0, 4096, 0), (0, 0, 0, 4096, 2)]
    bad = Anim("bad", [
        Seq("Root", 0, True, True, [(0, 0, 0, 4096, 0, 0, 0, 0), (0, 0, 0, 4096, 2, 0, 0, 0)]),
        Seq(" Pelvis", 1, True, True, [(0, 0, 0, 4096, 0, 0, 0, 0), (0, 0, 0, 4096, 2, 0, 1024, 0)]),
        Seq("Bone09", 9, False, True, k),                            # not a ped bone
        Seq("Pelvs", -1, False, True, k),                            # typo of an engine name
        Seq("Spine", -1, False, True, k), Seq(" Spine", 2, False, True, k),       # both bind to bone 2
        Seq(" L UpperArm", 22, False, True, k),                      # tag of the right arm, named like the left
        Seq(" Head", 5, False, True, [(0, 0, 0, 4096, 4), (0, 0, 0, 4096, 2)]),   # time goes back
        Seq(" Neck", 4, False, True, [(0, 0, 0, 3000, 0)]),          # |q| = 0.73
        Seq("Jaw", 8, False, False, [(0.0, 0.0, 0.0, 1.0, 0.0)]),     # float in a compressed animation
        Seq("R Calf", 52, False, True, []),
    ], flags=1)
    long = Anim("x" * 30, [Seq("Root", 0, False, True, k)], flags=1)
    ifp = Ifp("ANP3", "t", [bad, Anim("BAD", [Seq("Root", 0, False, True, k)], flags=1), long,
                            Anim("empty", [Seq("Root", 0, False, True, [])])])
    fs = check_ifp(ifp, ped_skeleton())
    got = _codes(fs)
    for code in [("error", "compression"), ("error", "name_length"), ("info", "no_keys"),
                 ("warn", "duplicate_anim"), ("warn", "duplicate_bone"), ("warn", "empty"),
                 ("warn", "quat_norm"), ("warn", "root_motion_on_pelvis"), ("warn", "tag_name_conflict"),
                 ("warn", "time_order"), ("warn", "unbound_name"), ("warn", "unknown_bone")]:
        assert code in got, code
    msg = next(f.msg for f in fs if f.check == "unbound_name")
    assert "did you mean 'Pelvis'" in msg
    assert next(f.anim for f in fs if f.check == "duplicate_anim") == "BAD"
    # only the named animations
    assert {f.anim for f in check_ifp(ifp, ped_skeleton(), [ifp.anims[3]])} == {"empty"}


def test_check_plain_target():
    door = read_skeleton(plain_dff(), "door")
    a = Anim("open", [Seq("DOOR", -1, False, False, [(0.0, 0.0, 0.0, 1.0, 0.0)]),
                      Seq("DOOR", 1, False, False, [(0.0, 0.0, 0.0, 1.0, 0.0)]),
                      Seq("hinge", -1, False, False, [(0.0, 0.0, 0.0, 1.0, 0.0)])])
    fs = check_ifp(Ifp("ANP3", "d", [a]), door)
    assert _codes(fs) == [("warn", "tag_on_plain"), ("warn", "unbound_name")]


def test_root_motion_does_not_mistake_pelvis_for_root_and_last_binding_wins():
    moving = Seq("Pelvis", 1, True, True, [(0, 0, 0, 4096, 0, 0, 0, 0), (0, 0, 0, 4096, 60, 0, 1024, 0)])
    fixed = Seq("Root", 0, True, True, [(0, 0, 0, 4096, 0, 0, 0, 0), (0, 0, 0, 4096, 60, 0, 0, 0)])
    a = Anim("a", [moving], 1)
    assert root_motion(a) == "-"
    assert "root_motion_on_pelvis" in {f.check for f in check_ifp(Ifp("ANP3", "p", [a]), ped_skeleton())}
    moving.name, moving.tag = "Root", 0
    a.seqs.append(fixed)
    assert root_motion(a) == root_motion(a, ped_skeleton()) == "fixed"


def test_nonfinite_keys_on_unbound_bones_and_wrong_compression_flags():
    bad = Seq("prop", -1, False, False, [(float("nan"), 0, 0, 1, 0)])
    a = Anim("a", [bad])
    codes = _codes(check_ifp(Ifp("ANP3", "p", [a]), ped_skeleton()))
    assert ("error", "non_finite") in codes and ("warn", "unbound_name") in codes
    a = walk_ifp().anims[0]
    a.flags = 0
    assert ("error", "compression") in _codes(check_ifp(Ifp("ANP3", "p", [a]), ped_skeleton()))
