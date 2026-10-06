"""Checks of IFP animations against a skeleton and the engine's rules (``satk anim check``). Stdlib only.

Severity: ``error`` = the file cannot be written or the engine misreads it, ``warn`` = it loads but part of it
does nothing or looks wrong in game, ``info`` = worth knowing. Codes:

==========================  =======  ================================================================
code                        sev      meaning
==========================  =======  ================================================================
``name_length``             error    ANP2/ANP3 names over 23 bytes, or ANPK bone names over 27
``compression``             error    mixed key types or a mismatched compression flag in ANP3
``non_finite``              error    a key contains NaN or infinity, even on an unbound sequence
``duplicate_anim``          warn     two animations with the same name (the game finds the first)
``empty``                   warn     an animation without key frames
``unknown_bone``            warn     a bone tag the skeleton does not have (the sequence does nothing)
``tag_on_plain``            warn     a tagged sequence on a model without bones (it never binds)
``unbound_name``            warn     an untagged sequence whose name matches no bone (engine names)
``duplicate_bone``          warn     two sequences bind to one bone (the later one wins)
``tag_name_conflict``       warn     a tagged sequence named like another bone (a wrong tag?)
``root_motion_on_pelvis``   warn     the pelvis travels but the root does not (snaps back on loop)
``time_order``              warn     a key time before the previous one (a jump in game)
``quat_norm``               warn     a rotation that is not a unit quaternion (|q| off by > 2 %)
``trans_limit``             warn     a compressed translation at the int16 limit (+-32 m, clipped)
``root_motion``             info     how far the root bone travels (ped movement for walk-type anims)
``no_keys``                 info     sequences without key frames (skipped by the engine)
==========================  =======  ================================================================

``loader``: ``game`` = the game's own loader (``ped.ifp``, ``anim.img``, in single player and in MTA alike);
``mta`` = MTA's ``engineLoadIFP`` (``CClientIFP``), which first turns untagged names into bone ids with its own
table (lower case, spaces removed: ``" Pelvis"`` -> 1, which the game itself would not bind) and keeps only its
64 bone ids (``data/anim/mta_bones.json``); names it does not know stay untagged and bind like in the game.
"""

from __future__ import annotations

import difflib
import math
from dataclasses import dataclass

from ..core import resources
from .ifp import MAX_NAME, Anim, Ifp, Seq, T_SCALE, upper_key
from .skeleton import Skeleton

__all__ = ["Finding", "SEVERITIES", "LOADERS", "check_ifp", "root_motion", "root_seq", "mta_tag", "mta_ids"]

LOADERS = ("game", "mta")
_MTA: tuple[dict[str, int], frozenset[int]] | None = None

SEVERITIES = ("info", "warn", "error")
_PELVIS = 1
_MOVE_EPS = 0.01


@dataclass(frozen=True)
class Finding:
    anim: str
    sev: str
    check: str
    msg: str


def _mta_table() -> tuple[dict[str, int], frozenset[int]]:
    global _MTA
    if _MTA is None:
        d = resources.read_json("anim", "mta_bones.json")
        _MTA = ({str(k): int(v) for k, v in d["names"].items()}, frozenset(int(i) for i in d["ids"]))
    return _MTA


def mta_ids() -> frozenset[int]:
    """The 64 bone ids MTA's IFP loader keeps (32 ped + 32 cutscene bones)."""
    return _mta_table()[1]


def mta_tag(name: str, tag: int, fmt: str) -> int:
    """The bone id MTA's ``engineLoadIFP`` gives a sequence: its tag, else (and always for ANPK) the id of its
    lower-cased, space-free name (ANPK: the part after ``:``); -1 = stays untagged."""
    names = _mta_table()[0]
    if fmt == "ANPK":
        tag = -1
        name = name.split(":", 1)[1] if ":" in name else name
    if tag != -1:
        return int(tag)
    return names.get(name.lower().replace(" ", ""), -1)


def _norm(name: str) -> str:
    return " ".join(name.upper().split())


def root_seq(a: Anim, skel: Skeleton | None = None) -> Seq | None:
    """The last keyed sequence bound to the root; without a skeleton, tag 0 or the untagged engine name Root."""
    if skel is not None and skel.bones:
        root = skel.root()
        for s in reversed(a.seqs):
            if s.keys and skel.bind(s.name, s.tag) == root:
                return s
        return None
    for s in reversed(a.seqs):
        if s.keys and (s.tag == 0 or (s.tag == -1 and upper_key(s.name) == "ROOT")):
            return s
    return None


def _travel(s: Seq | None) -> tuple[float, float, float] | None:
    """Translation of the last key minus the first one (metres), ``None`` without translation keys."""
    if s is None or not s.trans or not s.keys:
        return None
    k0, k1 = s.keys[0], s.keys[-1]
    d = T_SCALE if s.compressed else 1.0
    return ((k1[5] - k0[5]) / d, (k1[6] - k0[6]) / d, (k1[7] - k0[7]) / d)


def root_motion(a: Anim, skel: Skeleton | None = None) -> str:
    """``"move 1.72m"`` (horizontal travel of the root), ``"fixed"`` (translation keys, no travel) or ``"-"``."""
    t = _travel(root_seq(a, skel))
    if t is None:
        return "-"
    h = math.hypot(t[0], t[1])
    return f"move {h:.2f}m" if h >= _MOVE_EPS else "fixed"


def _named(skel: Skeleton) -> dict[str, "object"]:
    """Normalized DFF and engine names -> bone."""
    out = {}
    for b in skel.bones:
        out.setdefault(_norm(b.name), b)
        if b.id in skel.engine_names:
            out.setdefault(_norm(skel.engine_names[b.id]), b)
    return out


def _close(name: str, skel: Skeleton) -> str:
    pool = list(skel.engine_names.values()) if skel.skinned else [b.name for b in skel.bones]
    hit = difflib.get_close_matches(name.strip(), pool, n=1, cutoff=0.6)
    return f" (did you mean {hit[0]!r}?)" if hit else ""


def _check_anim(a: Anim, skel: Skeleton, fmt: str, out: list[Finding], loader: str = "game") -> None:
    def f(sev: str, code: str, msg: str) -> None:
        out.append(Finding(a.name, sev, code, msg))

    if fmt != "ANPK" and len(a.name.encode("latin-1", "replace")) > MAX_NAME:
        f("error", "name_length", f"animation name has {len(a.name)} characters, the field holds {MAX_NAME}")
    bone_limit = 27 if fmt == "ANPK" else MAX_NAME
    for s in a.seqs:
        if len(s.name.encode("latin-1", "replace")) > bone_limit:
            f("error", "name_length", f"bone name {s.name!r} is longer than {bone_limit} characters")
    keyed = [s for s in a.seqs if s.keys]
    if not keyed:
        f("warn", "empty", "no key frames: the animation does nothing")
        return
    if fmt == "ANP3" and len({s.compressed for s in keyed}) > 1:
        f("error", "compression", "compressed and float sequences mixed: the engine reads every sequence with "
                                  "the animation's compressed flag (write it with one kind)")
    elif fmt == "ANP3" and bool(a.flags & 1) != keyed[0].compressed:
        f("error", "compression", "the animation's compressed flag does not match its key frame types")
    if len(keyed) < len(a.seqs):
        f("info", "no_keys", f"{len(a.seqs) - len(keyed)} sequence(s) without key frames (skipped by the engine)")
    bound: dict[int, Seq] = {}
    for s in keyed:
        _check_keys(s, f)
        tag = s.tag
        if loader == "mta":
            tag = mta_tag(s.name, s.tag, fmt)
            if tag != -1 and tag not in mta_ids():
                f("warn", "unknown_bone", f"bone tag {tag} ({s.name!r}) is not one of the 64 bone ids MTA's loader "
                                          "keeps: engineLoadIFP drops the sequence")
                continue
        b = skel.bind(s.name, tag)
        if b is None:
            if tag != -1 and not skel.skinned:
                f("warn", "tag_on_plain", f"{s.name!r} has bone tag {tag}: tagged sequences bind only to "
                                          f"skinned models, {skel.name} has frames only")
            elif tag != -1:
                f("warn", "unknown_bone", f"bone tag {tag} ({s.name!r}) is not a bone of {skel.name}: the "
                                          "sequence does nothing")
            else:
                hint = ""
                if loader == "game" and skel.skinned:
                    mt = mta_tag(s.name, -1, fmt)
                    hint = ": untagged sequences match the engine bone names"
                    if mt != -1:
                        hint += f" (MTA's engineLoadIFP would map it to bone {mt}; set its tag to {mt})"
                f("warn", "unbound_name", f"no bone of {skel.name} answers to {s.name!r}{_close(s.name, skel)}"
                                          + hint)
            continue
        if b.idx in bound:
            f("warn", "duplicate_bone", f"{bound[b.idx].name!r} and {s.name!r} both drive bone {b.name.strip()!r} "
                                           "(the later one wins)")
        bound[b.idx] = s
        if s.tag != -1 and skel.skinned:
            other = _named(skel).get(_norm(s.name))
            if other is not None and other.idx != b.idx:
                f("warn", "tag_name_conflict", f"tag {s.tag} drives {b.name.strip()!r}, but the sequence is named "
                                               f"like bone {other.name.strip()!r} (tag {other.id}): check the tag")
    if skel.skinned and skel.bones:
        rs = next((s for i, s in bound.items() if i == skel.bones[0].idx), None)
        rt = _travel(rs)
        rh = math.hypot(rt[0], rt[1]) if rt else 0.0
        if rh >= _MOVE_EPS:
            f("info", "root_motion", f"the root travels {rh:.2f} m ({rt[0]:+.2f}, {rt[1]:+.2f}, {rt[2]:+.2f}): "
                                     "anims played with the movement flag move the ped by it")
        pel = next((b for b in skel.bones if b.id == _PELVIS), None)
        ps = bound.get(pel.idx) if pel is not None else None
        pt = _travel(ps)
        if pt is not None and rh < _MOVE_EPS and math.hypot(pt[0], pt[1]) > 0.25:
            f("warn", "root_motion_on_pelvis",
              f"the pelvis travels {math.hypot(pt[0], pt[1]):.2f} m but the root does not: the game cannot turn it "
              "into movement and the ped snaps back when the animation loops (move the travel to the root bone)")


def _check_keys(s: Seq, f) -> None:
    keys = s.keys
    invalid = next((i for i, k in enumerate(keys) if not all(math.isfinite(v) for v in k)), None)
    if invalid is not None:
        f("error", "non_finite", f"{s.name!r} key {invalid} contains NaN or infinity")
        return
    d = 4096.0 if s.compressed else 1.0
    bad_t = next((i for i in range(1, len(keys)) if keys[i][4] < keys[i - 1][4]), None)
    if bad_t is not None:
        div = 60.0 if s.compressed else 1.0
        f("warn", "time_order", f"{s.name!r} key {bad_t}: time {keys[bad_t][4] / div:.3f} s is before the "
                                   f"previous key ({keys[bad_t - 1][4] / div:.3f} s)")
    worst = 0.0
    for k in keys:
        n = math.sqrt(k[0] * k[0] + k[1] * k[1] + k[2] * k[2] + k[3] * k[3]) / d
        worst = max(worst, abs(n - 1.0))
    if worst > 0.02:
        f("warn", "quat_norm", f"{s.name!r}: a rotation is not a unit quaternion (|q| off by {worst:.1%})")
    if s.compressed and s.trans and any(abs(c) >= 32767 or c == -32768 for k in keys for c in k[5:8]):
        f("warn", "trans_limit", f"{s.name!r}: a compressed translation sits at the int16 limit (+-32 m): it "
                                    "was probably clipped")


def check_ifp(ifp: Ifp, skel: Skeleton, anims: list[Anim] | None = None, loader: str = "game") -> list[Finding]:
    """Findings for ``anims`` (default: all) of ``ifp`` against ``skel`` for the ``game`` or ``mta`` loader."""
    if loader not in LOADERS:
        raise ValueError(f"unknown loader {loader!r} ({', '.join(LOADERS)})")
    out: list[Finding] = []
    seen: dict[str, str] = {}
    for a in ifp.anims:
        k = upper_key(a.name)
        if k in seen and (anims is None or any(x is a for x in anims)):
            out.append(Finding(a.name, "warn", "duplicate_anim",
                               "another animation of this file has the same name: the game finds the first"))
        seen.setdefault(k, a.name)
    for a in ifp.anims if anims is None else anims:
        _check_anim(a, skel, ifp.format, out, loader)
    return out
